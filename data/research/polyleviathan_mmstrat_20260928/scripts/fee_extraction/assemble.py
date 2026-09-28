"""Stage 2: attribute custody fee/refund legs to scoped fills; write one Parquet per family + rebates + unattributed legs."""
import json, sys
from pathlib import Path
import polars as pl
sys.path.insert(0, str(Path(__file__).parent))
from stage import tokens, FEE_ENDPOINTS, ST, OUT
EXCH = {"0x4bfb41d5b3570defd03c39a9a4d8de6bd8b8982e": "V1_CTF", "0xc5d563a36ae78145c45a50134d48a1215220f80a": "V1_NegRisk",
        "0xe111180000d2663c0091e4f400237545b87b996b": "V2_CTF", "0xe2222d279d744050d28e00520010520000310f59": "V2_NegRisk",
        "0xe3333700ca9d93003f00f0f71f8515005f6c00aa": "ComboV3"}
ENDNAME = {"0x115f48dc2a731aa16251c6d6e1befc42f92accc9": "v2_fee_receiver", "0xe3f18acc55091e2c48d883fc8c8413319d4ab7b0": "v1_ctf_fee_module",
           "0xb768891e3130f6df18214ac804d4db76c2c37730": "negrisk_fee_module"}
ends = pl.Series(FEE_ENDPOINTS).implode(); exch = pl.Series(list(EXCH)).implode()

tm = pl.DataFrame(list(tokens().values()), schema=["token_id", "family", "event_id", "market_id", "outcome_side"], orient="row")
done = [json.loads(p.read_text()) for p in (ST / "done").glob("*.json")]
dayinfo = pl.DataFrame(done).select("day", pl.col("custody").cast(pl.String).alias("custody_scan"))
legs = pl.read_parquet(str(ST / "legs/*.parquet"))
fills_all = pl.scan_parquet(str(ST / "fills/*.parquet")).with_columns(pl.from_epoch("timestamp", "s").dt.strftime("%Y-%m-%d").alias("day"))
fills = (fills_all.join(legs.select(pl.col("tx_hash").unique()).lazy(), on="tx_hash", how="semi")
         .unique(["tx_hash", "log_index", "token_id"]).collect())

legs = legs.with_columns(
    pl.when(pl.col("to_address").is_in(ends)).then(pl.lit("charge")).otherwise(pl.lit("refund")).alias("leg_kind"),
    pl.when(pl.col("to_address").is_in(ends)).then(pl.col("to_address")).otherwise(pl.col("from_address")).alias("fee_contract"),
    pl.when(pl.col("token_id").is_null()).then(pl.lit("USDC_collateral")).otherwise(pl.lit("outcome_token")).alias("fee_asset"),
    (pl.col("tx_hash") + ":" + pl.col("log_index").cast(pl.String)).alias("fee_leg_ref"),
).with_row_index("leg_id")
F = fills.select("tx_hash", pl.col("log_index").alias("fill_log_index"), pl.col("token_id").alias("fill_token_id"),
                 pl.col("maker_address").alias("fill_owner"))
# charge: exchange -> fee endpoint. Receipt-measured carriers: V1 leg precedes its OrderFilled by 2 logs; V2 leg follows the
# taker-order OrderFilled by 2 logs. Candidates = same tx, |log distance| == 2, token must match for token-denominated fees.
F = F.join(fills.select("tx_hash", pl.col("log_index").alias("fill_log_index"), pl.col("token_id").alias("fill_token_id"),
                        pl.col("taker_address").is_in(exch).alias("fill_is_taker_order")), on=["tx_hash", "fill_log_index", "fill_token_id"])
ch = (legs.filter(pl.col("leg_kind") == "charge").join(F, on="tx_hash", how="inner")
      .filter(((pl.col("fill_log_index") - pl.col("log_index")).abs() == 2)
              & (pl.col("token_id").is_null() | (pl.col("token_id") == pl.col("fill_token_id"))))
      .sort("leg_id", pl.col("fill_is_taker_order"), descending=[False, True])
      .group_by("leg_id", maintain_order=True).agg(pl.all().first(), pl.len().alias("n_cand"), pl.col("fill_is_taker_order").sum().alias("n_taker"))
      .with_columns(pl.when(pl.col("n_cand") == 1).then(pl.lit("adjacent_log_2_unique"))
                    .when(pl.col("n_taker") == 1).then(pl.lit("adjacent_log_2_taker_order_selected"))
                    .otherwise(pl.lit("adjacent_log_2_AMBIGUOUS")).alias("attribution_rule")).drop("n_cand", "n_taker"))
# wallet-sent charges (from = the payer): fallback to the nearest fill in the tx owned by the sender.
rest = legs.filter((pl.col("leg_kind") == "charge") & ~pl.col("leg_id").is_in(ch["leg_id"].implode()))
fb = (rest.join(F, on="tx_hash").filter(pl.col("from_address") == pl.col("fill_owner"))
      .with_columns((pl.col("fill_log_index") - pl.col("log_index")).abs().alias("dist"))
      .sort("dist").group_by("leg_id").agg(pl.all().first(), pl.len().alias("n_cand"), pl.col("dist").min().alias("d0")))
ch2 = fb.with_columns(pl.lit("sender_owner_nearest").alias("attribution_rule")).drop("dist", "d0", "n_cand")
# refund: fee endpoint -> wallet. Fill = same tx, owner == recipient, same token for token refunds, nearest preceding fill.
rf = (legs.filter(pl.col("leg_kind") == "refund").join(F, on="tx_hash")
      .filter((pl.col("to_address") == pl.col("fill_owner")) & (pl.col("token_id").is_null() | (pl.col("token_id") == pl.col("fill_token_id"))))
      .with_columns((pl.col("log_index") - pl.col("fill_log_index")).alias("dist")).filter(pl.col("dist") > 0)
      .sort("dist").group_by("leg_id").agg(pl.all().first(), pl.len().alias("n_cand"))
      .with_columns(pl.when(pl.col("n_cand") > 1).then(pl.lit("owner_token_nearest_preceding_AMBIGUOUS"))
                    .otherwise(pl.lit("owner_token_unique")).alias("attribution_rule")).drop("dist", "n_cand"))
att = pl.concat([ch.select(rf.columns), ch2.select(rf.columns), rf], how="vertical_relaxed")
dup = att.group_by("leg_id").len().filter(pl.col("len") > 1)
assert dup.height == 0, f"legs attributed twice: {dup.height}"
unatt = legs.filter(~pl.col("leg_id").is_in(att["leg_id"].implode()))
agg = (att.group_by("tx_hash", "fill_log_index", "fill_token_id").agg(
    pl.col("value_usd").filter((pl.col("leg_kind") == "charge") & pl.col("token_id").is_null()).sum().alias("fee_charged_usdc"),
    pl.col("size_shares").filter((pl.col("leg_kind") == "charge") & pl.col("token_id").is_not_null()).sum().alias("fee_charged_shares"),
    pl.col("value_usd").filter((pl.col("leg_kind") == "refund") & pl.col("token_id").is_null()).sum().alias("fee_refunded_usdc"),
    pl.col("size_shares").filter((pl.col("leg_kind") == "refund") & pl.col("token_id").is_not_null()).sum().alias("fee_refunded_shares"),
    (pl.col("leg_kind") == "charge").sum().alias("n_charge_legs"), (pl.col("leg_kind") == "refund").sum().alias("n_refund_legs"),
    pl.col("fee_contract").unique().sort().str.join(";").alias("fee_contract"),
    pl.col("fee_asset").unique().sort().str.join(";").alias("fee_asset"),
    pl.col("fee_leg_ref").sort().str.join(";").alias("fee_leg_refs"),
    pl.col("attribution_rule").unique().sort().str.join(";").alias("attribution_rules"),
    (pl.col("from_address").is_in(exch)).any().alias("fee_sent_by_exchange"),
).rename({"fill_log_index": "log_index", "fill_token_id": "token_id"}))
_cids = pl.scan_parquet(str(ST / "fills/*.parquet")).select(pl.col("condition_id").str.to_lowercase().unique()).collect()
snap = (pl.read_parquet("/home/ubuntu/polymarketwhale/campaigns/feejoin_20260927/market_fee_snapshot.parquet").with_columns(
    pl.col("condition_id").str.to_lowercase()).unique("condition_id").join(_cids, on="condition_id", how="semi"))
print("snap rows", snap.height, flush=True)
def build(fills):
    return (fills.join(agg, on=["tx_hash", "log_index", "token_id"], how="left")
         .join(tm, on="token_id", how="left")
         .join(dayinfo, on="day", how="left")
         .join(snap, on="condition_id", how="left")
         .with_columns(
             pl.col("maker_address").alias("participant_address"),
             pl.col("taker_address").alias("counterparty_address"),
             pl.col("taker_address").is_in(exch).alias("order_is_match_taker_order"),
             pl.col("taker_address").replace_strict(EXCH, default=None).alias("exchange_if_taker_is_exchange"),
             pl.col("side").alias("participant_side"),
             (pl.col("fee_charged_usdc").fill_null(0) + pl.col("fee_charged_shares").fill_null(0) * pl.col("price")).alias("fee_charged_usd_equiv"),
             (pl.col("fee_refunded_usdc").fill_null(0) + pl.col("fee_refunded_shares").fill_null(0) * pl.col("price")).alias("fee_refunded_usd_equiv"),
         ).with_columns(
             (pl.col("fee_charged_usd_equiv") - pl.col("fee_refunded_usd_equiv")).alias("fee_net_usd_equiv"),
             pl.when(pl.col("custody_scan").str.to_lowercase() != "true").then(pl.lit("custody_not_scanned_pre_fee_era"))
               .when(pl.col("n_charge_legs").is_null()).then(pl.lit("no_fee_leg_observed"))
               .when(pl.col("fee_refunded_usd_equiv") >= pl.col("fee_charged_usd_equiv") - 1e-9).then(pl.lit("fee_charged_fully_refunded"))
               .when(pl.col("n_refund_legs") > 0).then(pl.lit("fee_charged_partly_refunded"))
               .otherwise(pl.lit("fee_charged")).alias("fee_evidence"),
             (pl.col("n_charge_legs").fill_null(0) > 1).alias("flag_multiple_fee_records"),
             (pl.col("attribution_rules").str.contains("AMBIGUOUS").fill_null(False)).alias("flag_attribution_ambiguous"),
             ((pl.col("n_charge_legs").fill_null(0) > 0) & (pl.col("fees_enabled") == False)).alias("flag_fee_on_fee_disabled_market"),
             ((pl.col("n_charge_legs").fill_null(0) > 0) & ~pl.col("order_is_match_taker_order")).alias("flag_fee_on_non_taker_order"),
         ))
cols = ["family", "event_id", "market_id", "condition_id", "token_id", "outcome_side", "day", "timestamp", "tx_hash", "log_index",
        "participant_address", "counterparty_address", "order_is_match_taker_order", "exchange_if_taker_is_exchange",
        "participant_side", "price", "size_shares", "value_usd", "maker_address", "taker_address",
        "fee_evidence", "fee_charged_usdc", "fee_charged_shares", "fee_refunded_usdc", "fee_refunded_shares",
        "fee_charged_usd_equiv", "fee_refunded_usd_equiv", "fee_net_usd_equiv", "n_charge_legs", "n_refund_legs",
        "fee_asset", "fee_contract", "fee_sent_by_exchange", "fee_leg_refs", "attribution_rules",
        "flag_multiple_fee_records", "flag_attribution_ambiguous", "flag_fee_on_fee_disabled_market", "flag_fee_on_non_taker_order",
        "fee_type", "fee_rate", "fee_taker_only", "fee_rebate_rate", "fees_enabled", "fee_source", "fee_category", "custody_scan"]
import pyarrow.parquet as pq, shutil
scoped_cids = pl.DataFrame({"condition_id": []}, schema={"condition_id": pl.String})
PARTS = ST.parent / "parts"; shutil.rmtree(PARTS, ignore_errors=True); PARTS.mkdir()
stats = {"legs_total": legs.height, "legs_attributed": att.height, "legs_unattributed": unatt.height, "fills_total": 0}
days = sorted(p.stem for p in (ST / "fills").glob("*.parquet"))
cnt = {x["day"]: x["fills"] for x in done}
batches, cur, n = [], [], 0
for d in days:
    if cur and n + cnt.get(d, 0) > 600_000:
        batches.append(cur); cur, n = [], 0
    cur.append(d); n += cnt.get(d, 0)
batches.append(cur)
for bi, bd in enumerate(batches):
    m = f"{bi:04d}"
    fm = (pl.scan_parquet([str(ST / "fills" / f"{d}.parquet") for d in bd])
          .with_columns(pl.from_epoch("timestamp", "s").dt.strftime("%Y-%m-%d").alias("day"))
          .unique(["tx_hash", "log_index", "token_id"]).collect())
    o = build(fm).select(cols)
    stats["fills_total"] += o.height
    for (fam,), g in o.group_by("family"):
        g.sort("timestamp", "tx_hash", "log_index").write_parquet(PARTS / f"{fam}__{m}.parquet", compression="zstd")
    print("month", m, o.height, flush=True)
for fam in sorted({p.name.split("__")[0] for p in PARTS.glob("*.parquet")}):
    parts = sorted(PARTS.glob(f"{fam}__*.parquet"))
    w = None
    for p in parts:
        t = pq.read_table(p)
        if w is None:
            w = pq.ParquetWriter(OUT / f"fees_{fam}.parquet", t.schema, compression="zstd")
        w.write_table(t, row_group_size=500_000)
    w.close()
    g = pl.scan_parquet(OUT / f"fees_{fam}.parquet")
    st = g.select(pl.len().alias("rows"), pl.col("day").min().alias("first"), pl.col("day").max().alias("last"),
                  pl.col("participant_address").n_unique().alias("unique_participants"),
                  pl.col("fee_charged_usd_equiv").sum().alias("fee_charged_usd_equiv"),
                  pl.col("fee_refunded_usd_equiv").sum().alias("fee_refunded_usd_equiv")).collect().to_dicts()[0]
    st["evidence"] = dict(g.group_by("fee_evidence").len().collect().iter_rows())
    stats[fam] = st
shutil.rmtree(PARTS)
unatt.drop("leg_id").write_parquet(OUT / "unattributed_fee_legs.parquet")
wallets = (pl.scan_parquet(str(OUT / "fees_*.parquet")).select(pl.concat_list("participant_address", "counterparty_address").alias("w"))
           .explode("w").unique().collect()["w"].implode())
rb = pl.read_parquet(str(ST / "rebates/*.parquet")) if any((ST / "rebates").iterdir()) else pl.DataFrame()
if rb.height:
    rb.rename({"to_address": "wallet"}).filter(pl.col("wallet").is_in(wallets)).select(
        "wallet", pl.from_epoch("event_timestamp", "s").dt.date().alias("utc_date"), pl.col("value_usd").alias("rebate_usdc"),
        "tx_hash", "log_index", "block_number").sort("utc_date", "wallet").write_parquet(OUT / "maker_rebate_payouts_scoped_wallets.parquet")
    stats["rebate_payouts_all"] = rb.height; stats["rebate_usd_all"] = round(rb["value_usd"].sum(), 2)
(OUT / "STATS.json").write_text(json.dumps(stats, indent=1, default=str))
print(json.dumps(stats, indent=1, default=str))
