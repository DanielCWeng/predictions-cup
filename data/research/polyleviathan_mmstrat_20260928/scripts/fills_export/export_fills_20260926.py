#!/usr/bin/env python3
"""Export the raw canonical fill corpus for five election families (sisterreq, 2026-09-26)."""
from __future__ import annotations

import csv, hashlib, json, os, sys
from pathlib import Path

os.environ.setdefault("POLARS_MAX_THREADS", "2")
import polars as pl
import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

ROOT = Path("/home/ubuntu/polymarketwhale")
CAMPAIGN = ROOT / "campaigns/sisterreq_20260925"
OUT = Path("/home/ubuntu/sisterreq_fills_20260926")
STAGE = OUT / "_work/staging"
FAMILIES = ["US_2024", "COL_2026", "PER_2026", "HUN_2026", "CAN_2025"]
CHUNK_ROWS = 400_000
sys.path.insert(0, str(ROOT / "Sonar"))
from backfills.core.backfill.schema import canonicalize_dataframe, TRADE_SCHEMA  # noqa: E402

# ---- inventory -> token map -------------------------------------------------
inv = [r for r in csv.DictReader(open(CAMPAIGN / "polyleviathan_election_market_inventory.csv"))
       if r["research_family"] in FAMILIES]
assert len(inv) == 1747, len(inv)
tmap: dict[str, tuple] = {}
for r in inv:
    pairs = [(r["yes_token_id"], "YES"), (r["no_token_id"], "NO")]
    if r["other_token_ids"]:
        pairs += [(t, "OTHER") for t in json.loads(r["other_token_ids"])]
    for tok, side in pairs:
        if not tok:
            continue
        row = (tok, r["research_family"], r["event_id"] or None, r["market_id"] or None, side)
        if tok in tmap and tmap[tok][1:4] != row[1:4]:
            raise RuntimeError(f"token {tok} maps to two markets: {tmap[tok]} vs {row}")
        if tok in tmap and tmap[tok][4] != side:
            row = (tok, row[1], row[2], row[3], tmap[tok][4] if tmap[tok][4] != "OTHER" else side)
        tmap[tok] = row
tokens = sorted(tmap)
print(f"tokens={len(tokens)}", flush=True)

registry = json.loads((ROOT / "Sonar/infra_registry.json").read_text())
exchanges = sorted({a.lower() for a in registry["exchange_addresses"]})
assert len(exchanges) == 5

# ---- pass 1: per-day filtered staging from the OCI lake ----------------------
objects = json.loads((CAMPAIGN / "oci_trade_object_inventory.json").read_text())
names = objects["canonical_daily_objects"]
assert len(names) == 1392
from dotenv import load_dotenv
load_dotenv(ROOT / "Sonar/.env", override=False)
import config, oci
from pnl_common import _oci_s3_storage_options
signer, cfg = config.get_oci_signer_and_config()
client = oci.object_storage.ObjectStorageClient(cfg, signer=signer) if signer else oci.object_storage.ObjectStorageClient(cfg)
ns = client.get_namespace().data
assert ns == objects["namespace"]
so = _oci_s3_storage_options(ns)
STAGE.mkdir(parents=True, exist_ok=True)
tok_series = pl.Series(tokens, dtype=pl.String)
for i, name in enumerate(names, 1):
    dst = STAGE / (Path(name).stem + ".parquet")
    if dst.exists():
        continue
    df = (pl.scan_parquet(f"s3://{objects['bucket']}/{name}", storage_options=so)
          .filter(pl.col("token_id").is_in(tok_series.implode())).collect(engine="streaming"))
    df = canonicalize_dataframe(df).with_columns(pl.lit(Path(name).stem).alias("_src_day"))
    tmp = dst.with_suffix(".tmp")
    df.write_parquet(tmp, compression="zstd")
    os.replace(tmp, dst)
    if i % 50 == 0 or i == len(names):
        print(f"stage {i}/{len(names)} {name} rows={df.height}", flush=True)

# ---- pass 2: per-family dedup + sort + write (polars, market-ordered chunks) -
STAGE_GLOB = str(STAGE / "*.parquet")
FILL_SCHEMA = pa.schema([
    ("timestamp", pa.int64()), ("ts_utc", pa.timestamp("us", tz="UTC")), ("side", pa.string()),
    ("price", pa.float64()), ("size_shares", pa.float64()), ("value_usd", pa.float64()),
    ("token_id", pa.string()), ("condition_id", pa.string()), ("maker_address", pa.string()),
    ("taker_address", pa.string()), ("tx_hash", pa.string()), ("log_index", pa.int64()),
    ("is_exchange_taker", pa.bool_()), ("family", pa.string()), ("event_id", pa.string()),
    ("market_id", pa.string()), ("outcome_side", pa.string()),
])
KEYS = ["tx_hash", "log_index", "token_id"]
CONTENT = ["timestamp", "side", "price", "size_shares", "value_usd", "condition_id", "maker_address", "taker_address"]
tmap_df = pl.DataFrame(list(tmap.values()), schema=["token_id", "family", "event_id", "market_id", "outcome_side"], orient="row")
tok_counts = (pl.scan_parquet(STAGE_GLOB).group_by("token_id").agg(pl.len().alias("n"),
              (pl.col("tx_hash").is_null() | pl.col("log_index").is_null() | pl.col("timestamp").is_null()).sum().alias("nullkeys"))
              .collect(engine="streaming"))
if tok_counts["nullkeys"].sum():
    raise RuntimeError("staged rows have null dedup keys")
tok_counts = tok_counts.join(tmap_df, on="token_id", how="inner")

stats: dict[str, dict] = {}
for fam in FAMILIES:
    fam_tokens = tok_counts.filter(pl.col("family") == fam)
    # Work units follow the output order (market -> token -> time). A token larger than
    # CHUNK_ROWS is split on UTC-day boundaries; duplicate keys share a tx, hence a timestamp.
    order = fam_tokens.sort(["market_id", "token_id"], nulls_last=True)
    units, cur, n = [], [], 0
    for tok, c in order.select("token_id", "n").iter_rows():
        if c <= CHUNK_ROWS:
            if cur and n + c > CHUNK_ROWS:
                units.append(("tokens", cur)); cur, n = [], 0
            cur.append(tok); n += c
            continue
        if cur:
            units.append(("tokens", cur)); cur, n = [], 0
        days = (pl.scan_parquet(STAGE_GLOB).filter(pl.col("token_id") == tok)
                .group_by((pl.col("timestamp") // 86400).alias("d")).len().sort("d").collect(engine="streaming"))
        lo, m = None, 0
        for d, c2 in days.iter_rows():
            if lo is None:
                lo = d
            if m and m + c2 > CHUNK_ROWS:
                units.append(("range", tok, lo, d)); lo, m = d, 0
            m += c2
        units.append(("range", tok, lo, None))
    if cur:
        units.append(("tokens", cur))
    chunks = units
    dst = OUT / f"fills_{fam}.parquet"
    tmp = dst.with_suffix(".tmp")
    writer = pq.ParquetWriter(tmp, FILL_SCHEMA, compression="zstd")
    raw = written = dup_groups = conflicting = 0
    for unit in chunks:
        if unit[0] == "tokens":
            flt = pl.col("token_id").is_in(pl.Series(unit[1], dtype=pl.String).implode())
        else:
            _, tok, lo, hi = unit
            flt = (pl.col("token_id") == tok) & ((pl.col("timestamp") // 86400) >= lo)
            if hi is not None:
                flt = flt & ((pl.col("timestamp") // 86400) < hi)
        df = pl.scan_parquet(STAGE_GLOB).filter(flt).collect(engine="streaming")
        raw += df.height
        g = (df.with_columns(pl.struct(CONTENT).hash().alias("_h")).group_by(KEYS)
             .agg(pl.len().alias("n"), pl.col("_h").n_unique().alias("h")).filter(pl.col("n") > 1))
        dup_groups += g.height
        conflicting += g.filter(pl.col("h") > 1).height
        df = (df.sort(["tx_hash", "log_index", "token_id", "_src_day", "side", "price", "size_shares", "maker_address", "taker_address"], nulls_last=True)
              .unique(subset=KEYS, keep="first", maintain_order=True)
              .join(tmap_df, on="token_id", how="inner")
              .with_columns(
                  pl.from_epoch("timestamp", time_unit="s").dt.replace_time_zone("UTC").dt.cast_time_unit("us").alias("ts_utc"),
                  pl.col("taker_address").str.to_lowercase().is_in(pl.Series(exchanges).implode()).fill_null(False).alias("is_exchange_taker"))
              .sort(["market_id", "token_id", "timestamp", "tx_hash", "log_index"], nulls_last=True)
              .select(FILL_SCHEMA.names))
        writer.write_table(df.to_arrow().cast(FILL_SCHEMA), row_group_size=250_000)
        print(f"chunk {fam} rows={df.height} rss_kib={int(open('/proc/self/status').read().split('VmRSS:')[1].split()[0])}", flush=True)
        written += df.height
        del df
    writer.close()
    os.replace(tmp, dst)
    stats[fam] = {"raw_matched_rows": raw, "dedup_dropped": raw - written,
                  "duplicate_key_groups": dup_groups, "duplicate_groups_with_differing_content": conflicting}
    print(f"fills {fam} rows={written} raw={raw} dup_groups={dup_groups} conflicting={conflicting} chunks={len(chunks)}", flush=True)

    mrows = [r for r in inv if r["research_family"] == fam]
    mdf = pl.DataFrame(mrows, schema={k: pl.String for k in mrows[0].keys()})
    mdf.write_parquet(OUT / f"markets_{fam}.parquet", compression="zstd")

# ---- manifest ---------------------------------------------------------------
def sha(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()

manifest = []
for fam in FAMILIES:
    for kind in ("fills", "markets"):
        p = OUT / f"{kind}_{fam}.parquet"
        e = {"file": p.name, "rows": pq.ParquetFile(p).metadata.num_rows, "bytes": p.stat().st_size, "sha256": sha(p)}
        if kind == "fills":
            r = pl.scan_parquet(p).select(pl.col("timestamp").min().alias("a"), pl.col("timestamp").max().alias("b"),
                                          pl.col("is_exchange_taker").sum().alias("x")).collect(engine="streaming").row(0)
            iso = lambda t: pl.from_epoch(pl.Series([t]), time_unit="s").dt.strftime("%Y-%m-%dT%H:%M:%SZ")[0]
            e.update(first_timestamp=r[0], last_timestamp=r[1], first_ts_utc=iso(r[0]), last_ts_utc=iso(r[1]), exchange_taker_rows=int(r[2]), **stats[fam])
        else:
            e.update(first_timestamp=None, last_timestamp=None, exchange_taker_rows=None)
        manifest.append(e)
(OUT / "MANIFEST.json").write_text(json.dumps(manifest, indent=1) + "\n")
print("MANIFEST", json.dumps(manifest), flush=True)
print("DONE", flush=True)
