"""Stage 1: per day, scoped fills (5 election families) + custody fee/refund legs in those txs + all rebate-distributor payouts."""
import csv, json, os, sys, time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
os.environ.setdefault("POLARS_MAX_THREADS", "2")
ROOT = Path("/home/ubuntu/polymarketwhale")
OUT = Path("/home/ubuntu/sisterreq_fees_20260927")
ST = OUT / "_work/stage"
FAMILIES = ["US_2024", "COL_2026", "PER_2026", "HUN_2026", "CAN_2025"]
FEE_ENDPOINTS = ["0x115f48dc2a731aa16251c6d6e1befc42f92accc9", "0xe3f18acc55091e2c48d883fc8c8413319d4ab7b0",
                 "0xb768891e3130f6df18214ac804d4db76c2c37730"]
REBATE_DISTRIBUTOR = "0x3a9418b2651c8164db5ebc56f12008137865e0f7"

def tokens():
    inv = [r for r in csv.DictReader(open(ROOT / "campaigns/sisterreq_20260925/polyleviathan_election_market_inventory.csv"))
           if r["research_family"] in FAMILIES]
    assert len(inv) == 1747, len(inv)
    t = {}
    for r in inv:
        pairs = [(r["yes_token_id"], "YES"), (r["no_token_id"], "NO")]
        if r["other_token_ids"]:
            pairs += [(x, "OTHER") for x in json.loads(r["other_token_ids"])]
        for tok, side in pairs:
            if tok and tok not in t:
                t[tok] = (tok, r["research_family"], r["event_id"] or None, r["market_id"] or None, side)
    return t

_so = _bucket = None
def _init():
    global _so, _bucket
    sys.path.insert(0, str(ROOT / "Sonar"))
    from dotenv import load_dotenv; load_dotenv(ROOT / "Sonar/.env", override=False)
    import config, oci
    from pnl_common import _oci_s3_storage_options
    s, c = config.get_oci_signer_and_config()
    cl = oci.object_storage.ObjectStorageClient(c, signer=s) if s else oci.object_storage.ObjectStorageClient(c)
    _so = _oci_s3_storage_options(cl.get_namespace().data); _bucket = "polymarket-bot-state"

def day(d, toks=None):
    import polars as pl
    done = ST / "done" / f"{d}.json"
    if done.exists():
        return json.loads(done.read_text())
    t0 = time.time()
    tok = pl.Series(toks, dtype=pl.String).implode()
    f = (pl.scan_parquet(f"s3://{_bucket}/trades/{d}.parquet", storage_options=_so)
         .filter(pl.col("token_id").is_in(tok)).collect(engine="streaming"))
    rec = {"day": d, "fills": f.height, "legs": 0, "rebates": 0}
    cust = f"s3://{_bucket}/custody/{d}.parquet"
    cols = ["event_type", "from_address", "to_address", "token_addr", "token_id", "size_shares", "value_usd",
            "tx_hash", "log_index", "block_number", "event_timestamp"]
    probe = d.endswith("-15")  # pre-era probe: one custody scan per month
    if d < "2025-12-01" and not probe:
        rec["custody"] = "skipped_pre_fee_era"; rec["secs"] = round(time.time() - t0, 1)
        p = ST / "fills" / f"{d}.parquet"
        if f.height:
            f.write_parquet(p.with_suffix(".tmp"), compression="zstd"); os.replace(p.with_suffix(".tmp"), p)
        done.write_text(json.dumps(rec)); return rec
    if not f.height and not ("2026-04-15" <= d <= "2026-07-31"):
        rec["custody"] = "skipped_no_scoped_fills"; rec["secs"] = round(time.time() - t0, 1)
        done.write_text(json.dumps(rec)); return rec
    try:
        c = pl.scan_parquet(cust, storage_options=_so).select(cols)
        ends = pl.Series(FEE_ENDPOINTS).implode()
        reb = c.filter(pl.col("from_address") == REBATE_DISTRIBUTOR).collect(engine="streaming")
        if f.height:
            txs = f.select(pl.col("tx_hash").unique()).lazy()
            fee = (c.filter(pl.col("to_address").is_in(ends) | pl.col("from_address").is_in(ends))
                   .join(txs, on="tx_hash", how="semi").collect(engine="streaming"))
            g = pl.concat([fee, reb])
        else:
            g = reb
        rec["custody"] = True
    except Exception as e:  # no custody object for this day
        g = None; rec["custody"] = False; rec["custody_error"] = str(e)[:200]
    for name, df in (("fills", f), ("legs", None if g is None else g.filter(pl.col("from_address") != REBATE_DISTRIBUTOR)),
                     ("rebates", None if g is None else g.filter(pl.col("from_address") == REBATE_DISTRIBUTOR))):
        if df is not None and df.height:
            p = ST / name / f"{d}.parquet"; df.write_parquet(p.with_suffix(".tmp"), compression="zstd"); os.replace(p.with_suffix(".tmp"), p)
            rec[name] = df.height
    rec["secs"] = round(time.time() - t0, 1)
    done.write_text(json.dumps(rec))
    return rec

if __name__ == "__main__":
    for s in ("fills", "legs", "rebates", "done"):
        (ST / s).mkdir(parents=True, exist_ok=True)
    toks = sorted(tokens())
    names = json.load(open(ROOT / "campaigns/sisterreq_20260925/oci_trade_object_inventory.json"))["canonical_daily_objects"]
    days = [Path(n).stem for n in names]
    days = [d for d in days if "2023-01-14" <= d <= "2026-07-31"]
    days = sorted([d for d in days if d >= "2025-12-01"], reverse=True) + sorted([d for d in days if d < "2025-12-01"], reverse=True)
    print(f"tokens={len(toks)} days={len(days)}", flush=True)
    t0 = time.time(); n = 0; tf = 0
    import multiprocessing as mp
    from functools import partial
    todo = [d for d in days if not (ST / "done" / f"{d}.json").exists()]
    with mp.get_context("spawn").Pool(int(os.environ.get("WORKERS", "2")), initializer=_init, maxtasksperchild=1) as pool:
        for r in pool.imap_unordered(partial(day, toks=toks), todo):
            n += 1; tf += r["fills"]
            print(f"{n}/{len(todo)} {r['day']} fills={r['fills']} legs={r['legs']} reb={r['rebates']} {r.get('secs')}s el={int(time.time()-t0)}s cumfills={tf}", flush=True)
    print("STAGE DONE", flush=True)
