#!/usr/bin/env python3
"""Checkpointed streaming scan of election token fills in canonical trades/."""
from __future__ import annotations

import csv
import hashlib
import json
import os
import resource
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

os.environ.setdefault("POLARS_MAX_THREADS", "2")

ROOT = Path("/home/ubuntu/polymarketwhale")
CAMPAIGN = ROOT / "campaigns/sisterreq_20260925"
INVENTORY = CAMPAIGN / "polyleviathan_election_family_manifest.json"
OBJECTS = CAMPAIGN / "oci_trade_object_inventory.json"
REGISTRY = ROOT / "Sonar/infra_registry.json"
CHECKPOINT = CAMPAIGN / "trade_scan_checkpoint.json"
DAY_DIR = CAMPAIGN / "token_day_aggregates"
TOKEN_CSV = CAMPAIGN / "per_token_day_fills.csv"
MARKET_CSV = CAMPAIGN / "polyleviathan_election_market_inventory.csv"
REPORT = CAMPAIGN / "POLYLEVIATHAN_ELECTION_INVENTORY_REPORT.md"
MEMORY_LIMIT_KIB = 2_800_000
CAMPAIGN_LIMIT_BYTES = 1_800 * 1024 * 1024


def atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n")
    os.replace(temp, path)


def atomic_text(path: Path, text: str) -> None:
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(text)
    os.replace(temp, path)


def family_markets(manifest: dict) -> list[dict]:
    result = []
    for election in manifest["elections"].values():
        for market_family in election["market_families"].values():
            result.extend(market_family["markets"])
    return result


def exact_token_ids(manifest: dict) -> list[str]:
    tokens = set()
    for market in family_markets(manifest):
        for key in ("yes_token_id", "no_token_id"):
            value = market.get(key)
            if value is not None and str(value):
                tokens.add(str(value))
        for token in market.get("outcome_tokens", []):
            value = token.get("token_id")
            if value is not None and str(value):
                tokens.add(str(value))
        raw = market.get("other_token_ids")
        if raw:
            for value in json.loads(raw) if isinstance(raw, str) else raw:
                tokens.add(str(value))
    for token in tokens:
        if not token.isdecimal():
            raise RuntimeError(f"non-decimal token identity is not eligible for a scan: {token!r}")
    return sorted(tokens)


def update_report_progress(done: int, total: int, last: str, status: str) -> None:
    report = REPORT.read_text()
    begin = "<!-- FILL_SCAN_PROGRESS_BEGIN -->"
    end = "<!-- FILL_SCAN_PROGRESS_END -->"
    progress = (
        f"{begin}\n## Fill scan progress\n\n"
        f"- [M] Canonical daily objects complete: {done:,} of {total:,}.\n"
        f"- [M] Last completed object: `{last or 'none'}`.\n"
        f"- [M] Fill scan state: `{status}`.\n"
        f"{end}"
    )
    if begin in report and end in report:
        left = report.index(begin)
        right = report.index(end, left) + len(end)
        report = report[:left] + progress + report[right:]
    elif "## What I did NOT do" in report:
        point = report.index("## What I did NOT do")
        report = report[:point] + progress + "\n\n" + report[point:]
    else:
        report = report.rstrip() + "\n\n" + progress + "\n"
    atomic_text(REPORT, report)


def campaign_bytes() -> int:
    total = 0
    for path in CAMPAIGN.rglob("*"):
        if path.is_file():
            total += path.stat().st_size
    return total


def rss_kib() -> int:
    return int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)


def load_existing_checkpoint(objects: list[str], tokens_hash: str, token_count: int, exchanges: list[str]) -> dict:
    if CHECKPOINT.exists():
        data = json.loads(CHECKPOINT.read_text())
        if data.get("object_names") != objects:
            raise RuntimeError("checkpoint object list differs from the inspected OCI inventory")
        if data.get("token_set_sha256") != tokens_hash:
            raise RuntimeError("checkpoint token set differs from the completed identity inventory")
        if data.get("exchange_addresses") != exchanges:
            raise RuntimeError("checkpoint exchange set differs from Sonar/infra_registry.json")
        data.setdefault("completed", [])
        return data
    return {
        "status": "running",
        "object_names": objects,
        "objects_total": len(objects),
        "completed": [],
        "token_set_sha256": tokens_hash,
        "token_count": token_count,
        "exchange_addresses": exchanges,
        "dedup_key": ["tx_hash", "log_index"],
        "scan_columns": ["token_id", "tx_hash", "log_index", "timestamp", "value_usd", "taker_address"],
        "aggregate_columns": ["fill_count", "first_fill_timestamp", "last_fill_timestamp", "traded_notional_usd"],
    }


def timestamp_iso(value: int | None) -> str | None:
    if value is None:
        return None
    return datetime.fromtimestamp(int(value), timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def write_token_day_csv(rows: list[dict]) -> None:
    fields = [
        "trade_object_day_utc", "token_id", "fill_count", "first_fill_timestamp",
        "last_fill_timestamp", "traded_notional_usd",
    ]
    temp = TOKEN_CSV.with_suffix(".csv.tmp")
    with temp.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    os.replace(temp, TOKEN_CSV)


def finalize_inventory(token_stats: dict[str, dict], all_tokens: list[str]) -> None:
    with MARKET_CSV.open(newline="") as handle:
        reader = csv.DictReader(handle)
        fields = list(reader.fieldnames or [])
        markets = list(reader)

    def market_token_ids(row: dict) -> list[str]:
        values = []
        for key in ("yes_token_id", "no_token_id"):
            if row.get(key):
                values.append(str(row[key]))
        if row.get("other_token_ids"):
            values.extend(str(x) for x in json.loads(row["other_token_ids"]))
        if row.get("token_fill_stats_json"):
            values.extend(str(x["token_id"]) for x in json.loads(row["token_fill_stats_json"]) if x.get("token_id"))
        return list(dict.fromkeys(values))

    by_token = {token: token_stats.get(token, {
        "fill_available": False,
        "first_fill_timestamp": None,
        "last_fill_timestamp": None,
        "fill_count": 0,
        "traded_notional_usd": 0.0,
        "distinct_fill_days": 0,
        "fill_source": "canonical",
    }) for token in all_tokens}

    for row in markets:
        ids = market_token_ids(row)
        if not ids:
            for key in ("fill_available", "first_fill_timestamp", "last_fill_timestamp", "fill_count", "traded_notional_usd", "distinct_fill_days", "fill_source"):
                row[key] = ""
            if row.get("token_fill_stats_json"):
                row["token_fill_stats_json"] = "[]"
            continue
        stats = [by_token[token] for token in ids]
        row["fill_available"] = str(any(item["fill_available"] for item in stats)).lower()
        row["first_fill_timestamp"] = min((item["first_fill_timestamp"] for item in stats if item["first_fill_timestamp"]), default="")
        row["last_fill_timestamp"] = max((item["last_fill_timestamp"] for item in stats if item["last_fill_timestamp"]), default="")
        row["fill_count"] = sum(int(item["fill_count"]) for item in stats)
        row["traded_notional_usd"] = format(sum(float(item["traded_notional_usd"]) for item in stats), ".15g")
        row["distinct_fill_days"] = len({day for token in ids for day in token_stats.get(token, {}).get("fill_days", [])})
        row["fill_source"] = "canonical"
        if row.get("token_fill_stats_json"):
            token_rows = json.loads(row["token_fill_stats_json"])
            for token_row in token_rows:
                exact_id = str(token_row["token_id"])
                token_row.update({k: v for k, v in by_token[exact_id].items() if k != "fill_days"})
            row["token_fill_stats_json"] = json.dumps(token_rows, ensure_ascii=False, separators=(",", ":"))

    temp = MARKET_CSV.with_suffix(".csv.tmp")
    with temp.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(markets)
    os.replace(temp, MARKET_CSV)

    manifest_path = CAMPAIGN / "polyleviathan_election_family_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    for market in family_markets(manifest):
        ids = market_token_ids(market)
        if not ids:
            for key in ("fill_available", "first_fill_timestamp", "last_fill_timestamp", "fill_count", "traded_notional_usd", "distinct_fill_days", "fill_source"):
                market[key] = None
            continue
        stats = [by_token[token] for token in ids]
        market["fill_available"] = any(item["fill_available"] for item in stats)
        market["first_fill_timestamp"] = min((item["first_fill_timestamp"] for item in stats if item["first_fill_timestamp"]), default=None)
        market["last_fill_timestamp"] = max((item["last_fill_timestamp"] for item in stats if item["last_fill_timestamp"]), default=None)
        market["fill_count"] = sum(int(item["fill_count"]) for item in stats)
        market["traded_notional_usd"] = sum(float(item["traded_notional_usd"]) for item in stats)
        market["distinct_fill_days"] = len({day for token in ids for day in token_stats.get(token, {}).get("fill_days", [])})
        market["fill_source"] = "canonical"
        for token_row in market.get("outcome_tokens", []):
            exact_id = str(token_row["token_id"])
            token_row.update({k: v for k, v in by_token[exact_id].items() if k != "fill_days"})

    summary = manifest["summary"]
    summary["status"] = "identity_and_canonical_fill_scan_complete"
    summary["fill_scan"] = {
        "status": "complete",
        "canonical_daily_objects": len(json.loads(CHECKPOINT.read_text())["completed"]),
        "token_ids_scanned": len(all_tokens),
        "token_ids_with_fills": sum(bool(by_token[token]["fill_available"]) for token in all_tokens),
        "fill_rows": sum(int(by_token[token]["fill_count"]) for token in all_tokens),
        "exchange_taker_notional_usd": sum(float(by_token[token]["traded_notional_usd"]) for token in all_tokens),
        "source": "canonical trades/YYYY-MM-DD.parquet",
        "dedup_key": ["tx_hash", "log_index"],
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")

    old_summary = json.loads((CAMPAIGN / "inventory_build_summary.json").read_text())
    old_summary.update(summary)
    (CAMPAIGN / "inventory_build_summary.json").write_text(json.dumps(old_summary, ensure_ascii=False, indent=2) + "\n")


def main() -> None:
    if os.environ.get("POLARS_MAX_THREADS") != "2":
        raise RuntimeError("POLARS_MAX_THREADS must be set to 2")
    import polars as pl

    manifest = json.loads(INVENTORY.read_text())
    tokens = exact_token_ids(manifest)
    if not tokens:
        raise RuntimeError("no exact token IDs found in the identity inventory")
    token_hash = hashlib.sha256("\n".join(tokens).encode()).hexdigest()

    object_inventory = json.loads(OBJECTS.read_text())
    objects = object_inventory["canonical_daily_objects"]
    if len(objects) != 1392 or any("test_upload_" in name for name in objects):
        raise RuntimeError(f"expected 1,392 canonical date files without test uploads; got {len(objects)}")
    if object_inventory.get("legacy_root_daily_objects"):
        raise RuntimeError("legacy root daily objects appeared after the inventory was recorded")

    registry = json.loads(REGISTRY.read_text())
    exchanges = sorted({str(value).lower() for value in registry["exchange_addresses"]})
    required_combo = "0xe3333700ca9d93003f00f0f71f8515005f6c00aa"
    if required_combo not in exchanges or len(exchanges) != 5:
        raise RuntimeError("Sonar/infra_registry.json exchange set does not match the inspected five-contract set")

    checkpoint = load_existing_checkpoint(objects, token_hash, len(tokens), exchanges)
    DAY_DIR.mkdir(exist_ok=True)
    completed = set(checkpoint["completed"])
    # Credentials are loaded by name from Sonar/.env and remain in process memory.
    from dotenv import load_dotenv

    load_dotenv(ROOT / "Sonar/.env", override=False)
    if str(ROOT / "Sonar") not in sys.path:
        sys.path.insert(0, str(ROOT / "Sonar"))
    import config
    import oci
    from pnl_common import _oci_s3_storage_options

    signer, cfg = config.get_oci_signer_and_config()
    client = oci.object_storage.ObjectStorageClient(cfg, signer=signer, timeout=(10, 120)) if signer else oci.object_storage.ObjectStorageClient(cfg, timeout=(10, 120))
    namespace = client.get_namespace().data
    if namespace != object_inventory["namespace"]:
        raise RuntimeError("OCI namespace changed from the inspected trade inventory")
    bucket = object_inventory["bucket"]
    storage_options = _oci_s3_storage_options(namespace)
    checkpoint["status"] = "running"
    atomic_json(CHECKPOINT, checkpoint)
    update_report_progress(len(completed), len(objects), checkpoint.get("last_completed", ""), "running")

    for index, name in enumerate(objects, 1):
        out = DAY_DIR / (Path(name).stem + ".json")
        if name in completed and out.is_file():
            continue
        if rss_kib() >= MEMORY_LIMIT_KIB:
            raise MemoryError(f"resident high-water reached {rss_kib()} KiB; task limit is below 3 GiB")
        if campaign_bytes() >= CAMPAIGN_LIMIT_BYTES:
            raise OSError("campaign scratch/output reached 1.8 GiB safety ceiling")

        uri = f"s3://{bucket}/{name}"
        lazy = pl.scan_parquet(uri, storage_options=storage_options)
        schema = lazy.collect_schema()
        required = {"token_id", "tx_hash", "log_index", "timestamp", "value_usd", "taker_address"}
        missing = required.difference(schema.names())
        if missing:
            raise RuntimeError(f"required trade columns missing in {name}: {sorted(missing)}")
        if schema["token_id"] != pl.String:
            raise RuntimeError(f"token_id is not String in {name}: {schema['token_id']}")
        if schema["tx_hash"] != pl.String:
            raise RuntimeError(f"tx_hash is not String in {name}: {schema['tx_hash']}")
        if schema["log_index"] not in (pl.Int64, pl.Int32, pl.UInt64, pl.UInt32):
            raise RuntimeError(f"log_index has an unexpected type in {name}: {schema['log_index']}")

        selected = lazy.filter(pl.col("token_id").is_in(tokens)).select(
            "token_id", "tx_hash", "log_index", "timestamp", "value_usd", "taker_address"
        )
        deduped = selected.unique(subset=["tx_hash", "log_index"])
        exchange_taker = pl.col("taker_address").str.to_lowercase().is_in(exchanges).fill_null(False)
        aggregate = deduped.group_by("token_id").agg(
            pl.len().alias("fill_count"),
            pl.col("timestamp").min().alias("first_timestamp"),
            pl.col("timestamp").max().alias("last_timestamp"),
            pl.when(exchange_taker).then(pl.col("value_usd")).otherwise(None).sum().fill_null(0.0).alias("traded_notional_usd"),
            (pl.col("tx_hash").is_null() | pl.col("log_index").is_null()).sum().alias("null_dedup_key_rows"),
        ).collect(engine="streaming")

        day = Path(name).stem
        rows = []
        for record in aggregate.iter_rows(named=True):
            if int(record["null_dedup_key_rows"] or 0):
                raise RuntimeError(f"{name} has matching fills with null tx_hash/log_index keys")
            rows.append({
                "token_id": str(record["token_id"]),
                "fill_count": int(record["fill_count"]),
                "first_fill_timestamp": timestamp_iso(record["first_timestamp"]),
                "last_fill_timestamp": timestamp_iso(record["last_timestamp"]),
                "traded_notional_usd": float(record["traded_notional_usd"] or 0.0),
            })
        atomic_json(out, {"object": name, "trade_object_day_utc": day, "rows": rows})
        completed.add(name)
        checkpoint.update({
            "status": "running",
            "completed": sorted(completed),
            "last_completed": name,
            "daily_objects_complete": len(completed),
            "last_object_token_rows": len(rows),
            "resident_high_water_kib": rss_kib(),
            "campaign_bytes": campaign_bytes(),
        })
        atomic_json(CHECKPOINT, checkpoint)
        if len(completed) % 10 == 0 or len(completed) == len(objects):
            update_report_progress(len(completed), len(objects), name, "running")
            print(f"checkpoint {len(completed)}/{len(objects)} {name} token_rows={len(rows)} rss_kib={rss_kib()} campaign_bytes={campaign_bytes()}", flush=True)

    if len(completed) != len(objects):
        raise RuntimeError(f"completed {len(completed)} of {len(objects)} canonical objects")

    per_token_days: dict[str, dict] = {}
    daily_rows = []
    for name in objects:
        day = Path(name).stem
        artifact = json.loads((DAY_DIR / (day + ".json")).read_text())
        if artifact.get("object") != name or artifact.get("trade_object_day_utc") != day:
            raise RuntimeError(f"daily aggregate identity mismatch for {name}")
        for row in artifact["rows"]:
            token = str(row["token_id"])
            target = per_token_days.setdefault(token, {
                "fill_count": 0,
                "traded_notional_usd": 0.0,
                "first_fill_timestamp": None,
                "last_fill_timestamp": None,
                "fill_days": [],
            })
            target["fill_count"] += int(row["fill_count"])
            target["traded_notional_usd"] += float(row["traded_notional_usd"])
            if row["first_fill_timestamp"] and (target["first_fill_timestamp"] is None or row["first_fill_timestamp"] < target["first_fill_timestamp"]):
                target["first_fill_timestamp"] = row["first_fill_timestamp"]
            if row["last_fill_timestamp"] and (target["last_fill_timestamp"] is None or row["last_fill_timestamp"] > target["last_fill_timestamp"]):
                target["last_fill_timestamp"] = row["last_fill_timestamp"]
            target["fill_days"].append(day)
            daily_rows.append({
                "trade_object_day_utc": day,
                "token_id": token,
                "fill_count": int(row["fill_count"]),
                "first_fill_timestamp": row["first_fill_timestamp"],
                "last_fill_timestamp": row["last_fill_timestamp"],
                "traded_notional_usd": format(float(row["traded_notional_usd"]), ".15g"),
            })

    token_stats = {}
    for token in tokens:
        aggregate = per_token_days.get(token)
        if aggregate:
            token_stats[token] = {
                **aggregate,
                "fill_available": aggregate["fill_count"] > 0,
                "distinct_fill_days": len(set(aggregate["fill_days"])),
                "fill_source": "canonical",
            }
        else:
            token_stats[token] = {
                "fill_available": False,
                "first_fill_timestamp": None,
                "last_fill_timestamp": None,
                "fill_count": 0,
                "traded_notional_usd": 0.0,
                "distinct_fill_days": 0,
                "fill_days": [],
                "fill_source": "canonical",
            }
    write_token_day_csv(daily_rows)
    checkpoint.update({
        "status": "complete",
        "completed": sorted(completed),
        "daily_objects_complete": len(completed),
        "token_ids_scanned": len(tokens),
        "token_ids_with_fills": sum(bool(token_stats[token]["fill_available"]) for token in tokens),
        "per_token_day_rows": len(daily_rows),
        "fill_rows": sum(int(token_stats[token]["fill_count"]) for token in tokens),
        "traded_notional_usd": sum(float(token_stats[token]["traded_notional_usd"]) for token in tokens),
        "resident_high_water_kib": rss_kib(),
        "campaign_bytes": campaign_bytes(),
    })
    atomic_json(CHECKPOINT, checkpoint)
    finalize_inventory(token_stats, tokens)
    update_report_progress(len(completed), len(objects), checkpoint.get("last_completed", ""), "complete")
    print(json.dumps({k: v for k, v in checkpoint.items() if k not in ("completed", "object_names", "exchange_addresses")}, indent=2))


if __name__ == "__main__":
    main()
