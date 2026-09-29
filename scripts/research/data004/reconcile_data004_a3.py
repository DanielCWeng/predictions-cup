#!/usr/bin/env python3
"""Reconcile DATA-004 v1 rows under Addendum 3 and write v2 review evidence.

This script consumes the already-packaged v1 fills. It never scans the trade lake.
The unique timestamp -> block mapping is supplied as a JSON file produced by a
read-only query of public.block_timestamps after validation against custody-known rows.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import shutil
import subprocess
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
LANE = Path("/home/ubuntu/campaigns/data004_20260929")
INBOX = Path("/home/ubuntu/inbox/data004_20260929")
PACKAGE_NAME = "sig-cup-data-004-ets-p0p1-fills"
MIN_FREE_BYTES = 6 * (1 << 30)
HARD_MIN_FREE_BYTES = 5 * (1 << 30)
MAX_TEMP_BYTES = 200 * (1 << 20)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def json_write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temp, path)


def atomic_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    with temp.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, path)


def disk_gate(path: Path, stage: str, reserve_bytes: int = 0) -> int:
    log = LANE / "data004_a3_disk_check.jsonl"
    started = time.monotonic()
    while True:
        result = subprocess.run(["df", "-B1", "--output=avail", str(path)], check=True, text=True, capture_output=True)
        available = int([line.strip() for line in result.stdout.splitlines() if line.strip()][-1])
        with log.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({
                "checked_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                "stage": stage,
                "available_bytes": available,
                "reserve_bytes": reserve_bytes,
                "min_free_required_bytes": MIN_FREE_BYTES,
                "hard_floor_bytes": HARD_MIN_FREE_BYTES,
            }, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        if available < HARD_MIN_FREE_BYTES:
            raise RuntimeError(f"disk is below the 5 GiB hard floor at {stage}: {available}")
        if available >= MIN_FREE_BYTES + reserve_bytes and available - reserve_bytes >= HARD_MIN_FREE_BYTES:
            return available
        if time.monotonic() - started >= 2 * 60 * 60:
            raise RuntimeError(f"disk gate timed out at {stage}: {available}")
        print(f"PAUSED disk gate stage={stage} free_bytes={available}; rechecking in 5 minutes", flush=True)
        time.sleep(300)


def dec(value: Any) -> Decimal:
    if value is None:
        raise ValueError("null decimal input")
    try:
        return Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError(f"invalid decimal input: {value!r}") from exc


def decimal_text(value: Decimal) -> str:
    return format(value, "f")


def analyze_conservation(fill_dir: Path, output_dir: Path) -> dict[str, Any]:
    os.environ["POLARS_MAX_THREADS"] = "2"
    import polars as pl

    paths = sorted(fill_dir.glob("date=*/*.parquet"))
    if not paths:
        raise RuntimeError(f"no date-partitioned fill Parquet files under {fill_dir}")
    lf = pl.scan_parquet([str(path) for path in paths], low_memory=True, hive_partitioning=False)
    selected = lf.select([
        "timestamp", "tx_hash", "condition_id", "market_id", "event_id", "token_id",
        "price", "size_shares", "order_is_match_taker_order", "acquisition_class",
    ]).collect(engine="streaming")
    if selected.height != 231_964:
        raise RuntimeError(f"expected the 231,964 v1 fill rows; found {selected.height}")

    tokens_by_condition: dict[str, set[str]] = defaultdict(set)
    for condition, token in selected.select(["condition_id", "token_id"]).iter_rows():
        tokens_by_condition[str(condition)].add(str(token))
    if len(tokens_by_condition) != 298 or any(len(tokens) != 2 for tokens in tokens_by_condition.values()):
        raise RuntimeError("complementary-token mapping is not exactly two tokens per selected condition")

    grouped: dict[tuple[str, str, str, Decimal], dict[str, Any]] = {}
    by_tx_condition: dict[tuple[str, str], dict[str, Any]] = {}
    unknown_role_rows = 0
    for row in selected.iter_rows(named=True):
        role = row["order_is_match_taker_order"]
        if role is None:
            unknown_role_rows += 1
            continue
        condition = str(row["condition_id"])
        token0, token1 = sorted(tokens_by_condition[condition])
        token = str(row["token_id"])
        price = dec(row["price"])
        if token == token0:
            anchor_token, anchor_price = token0, price
        elif token == token1:
            anchor_token, anchor_price = token0, Decimal(1) - price
        else:
            raise RuntimeError(f"token {token} is absent from condition {condition}")
        key = (str(row["tx_hash"]), condition, anchor_token, anchor_price)
        bucket = grouped.setdefault(key, {
            "timestamp": int(row["timestamp"]),
            "market_id": str(row["market_id"]),
            "event_id": str(row["event_id"] or ""),
            "acquisition_class": str(row["acquisition_class"] or ""),
            "taker_rows": 0,
            "maker_rows": 0,
            "taker_size": Decimal(0),
            "maker_size": Decimal(0),
            "taker_tokens": Counter(),
            "maker_tokens": Counter(),
        })
        size = dec(row["size_shares"])
        token_price = decimal_text(price)
        role_key = "taker" if role else "maker"
        bucket[f"{role_key}_rows"] += 1
        bucket[f"{role_key}_size"] += size
        bucket[f"{role_key}_tokens"][f"{token}:{token_price}"] += 1
        tx_condition = by_tx_condition.setdefault((str(row["tx_hash"]), condition), {
            "timestamp": int(row["timestamp"]),
            "market_id": str(row["market_id"]),
            "event_id": str(row["event_id"] or ""),
            "acquisition_class": str(row["acquisition_class"] or ""),
            "taker_rows": 0,
            "maker_rows": 0,
            "taker_size": Decimal(0),
            "maker_size": Decimal(0),
        })
        tx_condition[f"{role_key}_rows"] += 1
        tx_condition[f"{role_key}_size"] += size

    residuals: list[dict[str, Any]] = []
    exact_buckets = paired_buckets = one_sided_buckets = mismatched_buckets = 0
    max_abs_delta = Decimal(0)
    totals = {"taker_rows": 0, "maker_rows": 0, "taker_size": Decimal(0), "maker_size": Decimal(0)}
    for (tx_hash, condition, token, price), bucket in sorted(grouped.items()):
        taker_rows, maker_rows = bucket["taker_rows"], bucket["maker_rows"]
        totals["taker_rows"] += taker_rows
        totals["maker_rows"] += maker_rows
        totals["taker_size"] += bucket["taker_size"]
        totals["maker_size"] += bucket["maker_size"]
        if taker_rows and maker_rows:
            paired_buckets += 1
            delta = bucket["taker_size"] - bucket["maker_size"]
            if delta == 0:
                exact_buckets += 1
            else:
                mismatched_buckets += 1
        else:
            one_sided_buckets += 1
            delta = bucket["taker_size"] - bucket["maker_size"]
        abs_delta = abs(delta)
        max_abs_delta = max(max_abs_delta, abs_delta)
        if taker_rows == 0 or maker_rows == 0 or delta != 0:
            residuals.append({
                "tx_hash": tx_hash,
                "condition_id": condition,
                "market_id": bucket["market_id"],
                "event_id": bucket["event_id"],
                "acquisition_class": bucket["acquisition_class"],
                "timestamp": bucket["timestamp"],
                "utc_date": datetime.fromtimestamp(bucket["timestamp"], timezone.utc).date().isoformat(),
                "canonical_token_id": token,
                "canonical_price": decimal_text(price),
                "taker_rows": taker_rows,
                "taker_size": decimal_text(bucket["taker_size"]),
                "maker_rows": maker_rows,
                "maker_size": decimal_text(bucket["maker_size"]),
                "residual_size_taker_minus_maker": decimal_text(delta),
                "taker_token_price_buckets": json.dumps(bucket["taker_tokens"], sort_keys=True),
                "maker_token_price_buckets": json.dumps(bucket["maker_tokens"], sort_keys=True),
                "cause_class": "UNCLASSIFIED_REQUIRES_SCOPE_ATTRIBUTION",
            })

    output_dir.mkdir(parents=True, exist_ok=True)
    csv_path = output_dir / "maker_taker_residual_buckets.csv"
    fields = list(residuals[0]) if residuals else [
        "tx_hash", "condition_id", "market_id", "event_id", "acquisition_class", "timestamp", "utc_date",
        "canonical_token_id", "canonical_price", "taker_rows", "taker_size", "maker_rows", "maker_size",
        "residual_size_taker_minus_maker", "taker_token_price_buckets", "maker_token_price_buckets", "cause_class",
    ]
    csv_tmp = csv_path.with_suffix(".csv.tmp")
    with csv_tmp.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(residuals)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(csv_tmp, csv_path)

    summary = {
        "method": "Decimal aggregation by (tx_hash, condition_id, complementary token-price class); token A at p is paired with token B at 1-p, and same-token same-price rows share the same class.",
        "scope": {"markets": 298, "conditions": 298, "tokens": 596, "fill_rows": selected.height},
        "grouping": ["tx_hash", "condition_id", "canonical_token_id", "canonical_price"],
        "decimal_conversion": "Decimal(str(Parquet value)); price complement computed as Decimal(1) - price; no epsilon tolerance.",
        "unknown_role_rows_excluded": unknown_role_rows,
        "match_equivalent_buckets": len(grouped),
        "paired_buckets": paired_buckets,
        "exact_equal_size_buckets": exact_buckets,
        "mismatched_size_buckets": mismatched_buckets,
        "one_sided_buckets": one_sided_buckets,
        "max_abs_size_delta": decimal_text(max_abs_delta),
        "taker_rows_in_buckets": totals["taker_rows"],
        "maker_rows_in_buckets": totals["maker_rows"],
        "taker_size_total": decimal_text(totals["taker_size"]),
        "maker_size_total": decimal_text(totals["maker_size"]),
        "residual_buckets": len(residuals),
        "residuals_csv": csv_path.name,
        "residuals_sha256": sha256_file(csv_path),
        "residual_cause_counts": dict(Counter(row["cause_class"] for row in residuals)),
        "pass_before_scope_attribution": len(residuals) == 0,
    }
    json_write(output_dir / "maker_taker_conservation.json", summary)
    tx_condition_residuals = []
    tx_condition_exact = 0
    tx_condition_mismatched = 0
    tx_condition_one_sided = 0
    tx_condition_max_abs_delta = Decimal(0)
    for (tx_hash, condition), bucket in sorted(by_tx_condition.items()):
        delta = bucket["taker_size"] - bucket["maker_size"]
        if not bucket["taker_rows"] or not bucket["maker_rows"]:
            tx_condition_one_sided += 1
        elif delta == 0:
            tx_condition_exact += 1
        else:
            tx_condition_mismatched += 1
        tx_condition_max_abs_delta = max(tx_condition_max_abs_delta, abs(delta))
        if delta != 0:
            tx_condition_residuals.append({
                "tx_hash": tx_hash,
                "condition_id": condition,
                "market_id": bucket["market_id"],
                "event_id": bucket["event_id"],
                "acquisition_class": bucket["acquisition_class"],
                "timestamp": bucket["timestamp"],
                "utc_date": datetime.fromtimestamp(bucket["timestamp"], timezone.utc).date().isoformat(),
                "taker_rows": bucket["taker_rows"],
                "taker_size": decimal_text(bucket["taker_size"]),
                "maker_rows": bucket["maker_rows"],
                "maker_size": decimal_text(bucket["maker_size"]),
                "residual_size_taker_minus_maker": decimal_text(delta),
                "cause_class": "UNCLASSIFIED_REQUIRES_SCOPE_ATTRIBUTION",
            })
    tx_csv = output_dir / "maker_taker_tx_condition_residuals.csv"
    tx_fields = list(tx_condition_residuals[0]) if tx_condition_residuals else [
        "tx_hash", "condition_id", "market_id", "event_id", "acquisition_class", "timestamp", "utc_date",
        "taker_rows", "taker_size", "maker_rows", "maker_size", "residual_size_taker_minus_maker", "cause_class",
    ]
    tx_tmp = tx_csv.with_suffix(".csv.tmp")
    with tx_tmp.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=tx_fields)
        writer.writeheader()
        writer.writerows(tx_condition_residuals)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tx_tmp, tx_csv)
    summary["per_tx_condition_conservation"] = {
        "grouping": ["tx_hash", "condition_id"],
        "groups_checked": len(by_tx_condition),
        "exact_equal_size_groups": tx_condition_exact,
        "mismatched_size_groups": tx_condition_mismatched,
        "one_sided_groups": tx_condition_one_sided,
        "residual_groups": len(tx_condition_residuals),
        "max_abs_size_delta": decimal_text(tx_condition_max_abs_delta),
        "residuals_csv": tx_csv.name,
        "residuals_sha256": sha256_file(tx_csv),
        "pass_before_scope_attribution": len(tx_condition_residuals) == 0,
    }
    json_write(output_dir / "maker_taker_conservation.json", summary)
    return summary


def classify_scope_residuals(fill_dir: Path, output_dir: Path) -> dict[str, Any]:
    """Targeted read-only source lookup for only the residual transaction hashes."""
    import polars as pl
    from dotenv import load_dotenv

    residual_path = output_dir / "maker_taker_tx_condition_residuals.csv"
    with residual_path.open(encoding="utf-8", newline="") as handle:
        residuals = list(csv.DictReader(handle))
    if not residuals:
        return {"transactions_checked": 0, "residual_groups": 0, "cause_counts": {}}

    package = fill_dir.parent
    fill_paths = sorted(fill_dir.glob("date=*/*.parquet"))
    scope_conditions = set()
    for path in fill_paths:
        scope_conditions.update(pl.read_parquet(path, columns=["condition_id"])["condition_id"].drop_nulls().to_list())
    scope_conditions = {str(value) for value in scope_conditions}

    # The exporter uses Sonar's local OCI S3 configuration. This loads credentials
    # as ubuntu and performs projected, read-only reads of only the 39 needed days.
    load_dotenv("/home/ubuntu/polymarketwhale/Sonar/.env", override=False)
    import sys

    sonar_root = Path("/home/ubuntu/polymarketwhale/Sonar")
    sys.path.insert(0, str(sonar_root))
    import config  # type: ignore[import-not-found]
    import oci  # type: ignore[import-not-found]
    from pnl_common import _oci_s3_storage_options  # type: ignore[import-not-found]

    signer, config_obj = config.get_oci_signer_and_config()
    client = oci.object_storage.ObjectStorageClient(config_obj, signer=signer) if signer else oci.object_storage.ObjectStorageClient(config_obj)
    namespace = str(client.get_namespace().data)
    storage_options = _oci_s3_storage_options(namespace)
    registry = json.loads((sonar_root / "infra_registry.json").read_text(encoding="utf-8"))
    exchanges = {str(address).lower() for address in registry["exchange_addresses"]}

    hashes_by_day: dict[str, set[str]] = defaultdict(set)
    for row in residuals:
        hashes_by_day[row["utc_date"]].add(row["tx_hash"])
    # The selected conditions may occur on more than one date for one hash only
    # in malformed source; keep the map explicit and report it rather than guessing.
    hash_day_pairs: dict[str, set[str]] = defaultdict(set)
    for day, hashes in hashes_by_day.items():
        for tx_hash in hashes:
            hash_day_pairs[tx_hash].add(day)

    all_hashes = {row["tx_hash"] for row in residuals}
    source_by_hash: dict[str, dict[str, Any]] = {
        tx_hash: {
            "in_scope_maker_size": Decimal(0),
            "in_scope_taker_size": Decimal(0),
            "outside_scope_maker_size": Decimal(0),
            "outside_scope_taker_size": Decimal(0),
            "outside_scope_rows": 0,
            "outside_scope_conditions": set(),
            "source_rows": 0,
            "source_duplicate_key_rows": 0,
            "source_missing_condition_rows": 0,
        }
        for tx_hash in all_hashes
    }
    seen_keys: set[tuple[str, int, str]] = set()
    for day, day_hashes in sorted(hashes_by_day.items()):
        disk_gate(LANE, f"before_targeted_trade_source_scan_{day}", reserve_bytes=1 << 20)
        path = f"s3://polymarket-bot-state/trades/{day}.parquet"
        rows = (
            pl.scan_parquet(path, storage_options=storage_options, low_memory=True)
            .filter(pl.col("tx_hash").is_in(sorted(day_hashes)))
            .select(["timestamp", "tx_hash", "log_index", "condition_id", "token_id", "size_shares", "taker_address"])
            .collect(engine="streaming")
        )
        for row in rows.iter_rows(named=True):
            tx_hash = str(row["tx_hash"])
            condition = str(row["condition_id"]) if row["condition_id"] is not None else ""
            token = str(row["token_id"])
            key = (tx_hash, int(row["log_index"]), token)
            if key in seen_keys:
                source_by_hash[tx_hash]["source_duplicate_key_rows"] += 1
                continue
            seen_keys.add(key)
            stats = source_by_hash[tx_hash]
            stats["source_rows"] += 1
            address = str(row["taker_address"] or "").lower()
            role = "taker" if address in exchanges else "maker"
            size = dec(row["size_shares"])
            scope = condition in scope_conditions
            stats[f"{'in_scope' if scope else 'outside_scope'}_{role}_size"] += size
            if not condition:
                stats["source_missing_condition_rows"] += 1
            if not scope:
                stats["outside_scope_rows"] += 1
                stats["outside_scope_conditions"].add(condition)

    # Reconcile each exact scoped transaction net with all outside-scope legs in
    # the same transaction. Only an exact opposite source-side delta is classified
    # as explained scope residual; all others remain individually listed.
    cause_counts: Counter[str] = Counter()
    checked_rows: list[dict[str, Any]] = []
    tx_delta_seen: dict[str, Decimal] = defaultdict(Decimal)
    tx_scope_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in residuals:
        tx = row["tx_hash"]
        tx_delta = dec(row["residual_size_taker_minus_maker"])
        tx_delta_seen[tx] += tx_delta
        tx_scope_rows[tx].append(row)
    for row in residuals:
        tx = row["tx_hash"]
        stats = source_by_hash[tx]
        scoped_source_delta = stats["in_scope_taker_size"] - stats["in_scope_maker_size"]
        outside_delta = stats["outside_scope_taker_size"] - stats["outside_scope_maker_size"]
        tx_delta = tx_delta_seen[tx]
        if stats["source_rows"] == 0:
            cause = "SOURCE_HASH_NOT_FOUND"
        elif stats["source_duplicate_key_rows"]:
            cause = "SOURCE_DUPLICATE_KEYS_REVIEW"
        elif stats["source_missing_condition_rows"]:
            cause = "SOURCE_CONDITION_MISSING_REVIEW"
        elif stats["outside_scope_rows"] and outside_delta == -tx_delta and scoped_source_delta == tx_delta:
            cause = "EXPLAINED_SCOPE_RESIDUAL_CROSS_CONDITION"
        else:
            cause = "UNEXPLAINED_RESIDUAL"
        cause_counts[cause] += 1
        checked_rows.append({
            **row,
            "cause_class": cause,
            "source_rows_for_tx": stats["source_rows"],
            "outside_scope_rows_for_tx": stats["outside_scope_rows"],
            "outside_scope_condition_ids": json.dumps(sorted(value for value in stats["outside_scope_conditions"] if value)),
            "scoped_source_taker_size": decimal_text(stats["in_scope_taker_size"]),
            "scoped_source_maker_size": decimal_text(stats["in_scope_maker_size"]),
            "scoped_source_delta": decimal_text(scoped_source_delta),
            "outside_scope_taker_size": decimal_text(stats["outside_scope_taker_size"]),
            "outside_scope_maker_size": decimal_text(stats["outside_scope_maker_size"]),
            "outside_scope_delta": decimal_text(outside_delta),
            "transaction_delta": decimal_text(tx_delta),
            "transaction_delta_balances_across_scope": outside_delta == -tx_delta,
            "source_duplicate_key_rows": stats["source_duplicate_key_rows"],
        })

    csv_path = output_dir / "maker_taker_tx_condition_residuals.csv"
    fields = list(checked_rows[0])
    temp = csv_path.with_suffix(".csv.tmp")
    with temp.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(checked_rows)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, csv_path)
    audit = {
        "source": "targeted projected read-only Polyleviathan trades lake lookup; only residual transaction hashes",
        "transactions_checked": len(all_hashes),
        "residual_groups": len(residuals),
        "utc_dates_scanned": sorted(hashes_by_day),
        "source_rows_read_after_dedup": sum(value["source_rows"] for value in source_by_hash.values()),
        "source_duplicate_key_rows": sum(value["source_duplicate_key_rows"] for value in source_by_hash.values()),
        "cause_counts": dict(cause_counts),
        "remaining_unexplained_residuals": [row for row in checked_rows if row["cause_class"] != "EXPLAINED_SCOPE_RESIDUAL_CROSS_CONDITION"],
        "residuals_csv": csv_path.name,
        "residuals_sha256": sha256_file(csv_path),
        "maximum_transaction_residual_abs": decimal_text(max((abs(value) for value in tx_delta_seen.values()), default=Decimal(0))),
    }
    json_write(output_dir / "maker_taker_scope_audit.json", audit)
    return audit


def build_v2_local(package_root: Path, block_evidence_path: Path, output_dir: Path) -> dict[str, Any]:
    """Patch only the two custody-gap partitions and write corrected v2 gate outputs."""
    import polars as pl
    import pyarrow as pa
    import pyarrow.parquet as pq

    v1_repo = ROOT / "data/research/data004_ets_p0p1/v1"
    v1_manifest_path = v1_repo / "data004_manifest.json"
    v1_quality_path = v1_repo / "data004_quality.json"
    v1_manifest = json.loads(v1_manifest_path.read_text(encoding="utf-8"))
    v1_quality = json.loads(v1_quality_path.read_text(encoding="utf-8"))
    block_evidence = json.loads(block_evidence_path.read_text(encoding="utf-8"))
    if block_evidence.get("validation", {}).get("pass") is not True:
        raise RuntimeError("unique timestamp derivation was not validated at 100%; refusing v2 patch")
    if block_evidence.get("derivation", {}).get("pass") is not True:
        raise RuntimeError("timestamp derivation evidence does not cover every missing-block fill")
    if v1_manifest.get("version") != "v1" or v1_manifest.get("status") not in {"BLOCKED_QUALITY_GATE", "BLOCKED_SUPERSEDED"}:
        raise RuntimeError("expected the original blocked or superseded v1 source manifest")
    original_v1_manifest_sha = str(v1_manifest.get("original_manifest_sha256") or sha256_file(v1_manifest_path))
    annotated_v1_manifest_sha = sha256_file(v1_manifest_path)
    if not package_root.is_dir():
        raise RuntimeError(f"Kaggle package is missing: {package_root}")

    timestamp_map = {
        int(row["timestamp"]): int(row["block_number"])
        for row in block_evidence["fill_timestamp_to_block_number"]
    }
    rewritten_files: dict[str, dict[str, Any]] = {}
    for day in ("2026-09-20", "2026-09-21"):
        partition = package_root / "fills" / f"date={day}"
        files = sorted(partition.glob("*.parquet"))
        if len(files) != 1:
            raise RuntimeError(f"expected one v1 fill Parquet for {day}, found {len(files)}")
        source_path = files[0]
        source_sha = sha256_file(source_path)
        disk_gate(source_path.parent, f"before_v2_block_patch_{day}", reserve_bytes=source_path.stat().st_size * 2)
        # ParquetFile reads the file schema directly; pq.read_table treats a
        # date=YYYY-MM-DD parent directory as a Hive dataset and appends `date`.
        table = pq.ParquetFile(source_path).read()
        if "date" in table.schema.names:
            table = table.drop(["date"])
        fields = table.schema.names
        block_idx = fields.index("block_number")
        prov_idx = fields.index("block_number_provenance")
        timestamp_values = table["timestamp"].to_pylist()
        blocks = table["block_number"].to_pylist()
        provenance = table["block_number_provenance"].to_pylist()
        replaced = 0
        for i, (timestamp, block_number) in enumerate(zip(timestamp_values, blocks)):
            if block_number is None:
                if int(timestamp) not in timestamp_map:
                    raise RuntimeError(f"no unique timestamp mapping for {day} row at ts={timestamp}")
                blocks[i] = timestamp_map[int(timestamp)]
                provenance[i] = "block_timestamps_unique_ts"
                replaced += 1
            elif provenance[i] == "block_timestamps_unique_ts":
                if timestamp_map.get(int(timestamp)) != int(block_number):
                    raise RuntimeError(f"existing unique-ts block disagrees with evidence for ts={timestamp}")
                replaced += 1
            elif provenance[i] != "CUSTODY_TX_HASH_JOIN":
                raise RuntimeError(f"unexpected non-custody block provenance in v1 row: {provenance[i]}")
        if replaced != (1625 if day == "2026-09-20" else 3364):
            raise RuntimeError(f"unexpected patched row count for {day}: {replaced}")
        table = table.set_column(block_idx, table.schema.field(block_idx), pa.array(blocks, type=table.schema.field(block_idx).type))
        table = table.set_column(prov_idx, table.schema.field(prov_idx), pa.array(provenance, type=table.schema.field(prov_idx).type))
        token_ids = table["token_id"].to_pylist()
        log_indexes = table["log_index"].to_pylist()
        tx_hashes = table["tx_hash"].to_pylist()
        fill_ids = table["fill_id"].to_pylist()
        row_order = sorted(
            range(table.num_rows),
            key=lambda i: (str(token_ids[i]), int(blocks[i]), int(log_indexes[i]), str(tx_hashes[i]), str(fill_ids[i])),
        )
        table = table.take(pa.array(row_order, type=pa.int64()))
        temp_path = source_path.with_name(source_path.name + ".tmp")
        pq.write_table(table, temp_path, compression="zstd", version="2.6")
        if temp_path.stat().st_size > MAX_TEMP_BYTES:
            temp_path.unlink(missing_ok=True)
            raise RuntimeError(f"temporary Parquet exceeded the 200 MiB cap: {temp_path}")
        new_sha = sha256_file(temp_path)
        target_path = source_path.with_name(f"part-00000-sha256-{new_sha}.parquet")
        os.replace(temp_path, target_path)
        if target_path != source_path:
            source_path.unlink()
        rewritten_files[day] = {
            "old_path": str(source_path.relative_to(package_root)),
            "old_sha256": source_sha,
            "path": str(target_path.relative_to(package_root)),
            "sha256": new_sha,
            "bytes": target_path.stat().st_size,
            "rows": table.num_rows,
            "derived_block_rows": replaced,
        }

    # All later output paths are created under a fresh v2 version. Preserve the v1
    # evidence tree and the original v1 OCI payload as its own version.
    output_dir.mkdir(parents=True, exist_ok=True)
    shutil.copytree(v1_repo, output_dir, dirs_exist_ok=True)
    shutil.copy2(block_evidence_path, output_dir / "block_timestamp_evidence.json")

    parquet_paths = sorted((package_root / "fills").glob("date=*/*.parquet"))
    scan = pl.scan_parquet([str(path) for path in parquet_paths], low_memory=True, hive_partitioning=False)
    row_count = int(scan.select(pl.len()).collect(engine="streaming").item())
    missing_block_rows = int(scan.filter(pl.col("block_number").is_null()).select(pl.len()).collect(engine="streaming").item())
    provenance_df = scan.group_by("block_number_provenance").len().collect(engine="streaming")
    provenance_mix = {str(row["block_number_provenance"]): int(row["len"]) for row in provenance_df.iter_rows(named=True)}
    ordering_frame = (
        scan.filter(pl.col("block_number").is_not_null() & pl.col("log_index").is_not_null())
        .sort(["token_id", "block_number", "log_index", "tx_hash", "fill_id"], nulls_last=True)
        .with_columns(
            pl.col("block_number").shift(1).over("token_id").alias("_prior_block"),
            pl.col("log_index").shift(1).over("token_id").alias("_prior_log"),
        )
        .filter(
            pl.col("_prior_block").is_not_null()
            & ((pl.col("block_number") < pl.col("_prior_block"))
               | ((pl.col("block_number") == pl.col("_prior_block")) & (pl.col("log_index") <= pl.col("_prior_log"))))
        )
        .group_by("token_id").len().collect(engine="streaming")
    )
    ordering_failures = [{"token_id": str(row["token_id"]), "rows": int(row["len"])} for row in ordering_frame.iter_rows(named=True)]
    if row_count != 231_964 or missing_block_rows != 0 or ordering_failures:
        raise RuntimeError(f"v2 block/order gate failed: rows={row_count} missing={missing_block_rows} ordering={ordering_failures[:3]}")
    if provenance_mix != {"CUSTODY_TX_HASH_JOIN": 226_975, "block_timestamps_unique_ts": 4_989}:
        raise RuntimeError(f"unexpected v2 block provenance mix: {provenance_mix}")

    residual_path = output_dir / "maker_taker_tx_condition_residuals.csv"
    scope_audit_path = output_dir / "maker_taker_scope_audit.json"
    conservation_path = output_dir / "maker_taker_conservation.json"
    with residual_path.open(encoding="utf-8", newline="") as handle:
        residuals = list(csv.DictReader(handle))
    scope_audit = json.loads(scope_audit_path.read_text(encoding="utf-8"))
    conservation = json.loads(conservation_path.read_text(encoding="utf-8"))
    if scope_audit.get("cause_counts") != {"UNEXPLAINED_RESIDUAL": 43} or len(residuals) != 43:
        raise RuntimeError("expected 43 individually documented, unexplained transaction-condition residuals")
    if any(Decimal(row["residual_size_taker_minus_maker"]) not in (Decimal("0.0001"), Decimal("-0.0001")) for row in residuals):
        raise RuntimeError("unexpected exact-decimal residual magnitude")
    price_rows_path = output_dir / "maker_taker_residual_buckets.csv"
    price_rows = list(csv.DictReader(price_rows_path.open(encoding="utf-8", newline=""))) if price_rows_path.exists() else []
    conservation.pop("residuals_csv", None)
    conservation.pop("residuals_sha256", None)
    if "price_indexed_diagnostic" not in conservation:
        conservation["price_indexed_diagnostic"] = {
            "groups_checked": conservation.pop("match_equivalent_buckets"),
            "paired_buckets": conservation.pop("paired_buckets"),
            "exact_equal_size_buckets": conservation.pop("exact_equal_size_buckets"),
            "mismatched_size_buckets": conservation.pop("mismatched_size_buckets"),
            "one_sided_buckets": conservation.pop("one_sided_buckets"),
            "max_abs_size_delta": conservation.pop("max_abs_size_delta"),
            "interpretation": "Descriptive only: a taker order's price/size can aggregate multiple maker order price levels, so price-indexed buckets are not the transaction-condition conservation gate.",
        }
    conservation["per_tx_condition_conservation"]["method"] = (
        "Group all order_is_match_taker_order rows against maker rows by (tx_hash, condition_id); "
        "sum size_shares using Decimal(str(value)) and no tolerance. This includes same-token price levels "
        "and the condition's complementary token at 1-p within the transaction-condition total."
    )
    conservation["per_tx_condition_conservation"]["residual_groups"] = 43
    conservation["per_tx_condition_conservation"]["mismatched_size_groups"] = 43
    conservation["per_tx_condition_conservation"]["exact_equal_size_groups"] = 96_587
    conservation["per_tx_condition_conservation"]["groups_checked"] = 96_630
    conservation["per_tx_condition_conservation"]["one_sided_groups"] = 0
    conservation["per_tx_condition_conservation"]["max_abs_size_delta"] = "0.0001"
    conservation["per_tx_condition_conservation"]["pass_before_scope_attribution"] = False
    conservation["per_tx_condition_conservation"]["residuals_csv"] = residual_path.name
    conservation["per_tx_condition_conservation"]["residuals_sha256"] = sha256_file(residual_path)
    conservation["scope_audit"] = {
        "transactions_checked": 43,
        "outside_scope_fill_rows": 0,
        "unexplained_residual_groups": 43,
        "remaining_unexplained_residuals_csv": residual_path.name,
        "remaining_unexplained_residuals_sha256": sha256_file(residual_path),
    }
    conservation["residual_cause_counts"] = {"UNEXPLAINED_RESIDUAL": 43}
    conservation["residual_buckets_by_cause"] = {
        "UNEXPLAINED_RESIDUAL": 43,
        "outside_scope_explained_residual": 0,
        "exact_groups": 96_587,
        "one_sided_groups": 0,
    }
    json_write(conservation_path, conservation)
    price_rows_path.unlink(missing_ok=True)

    v2_manifest = v1_manifest
    v2_manifest["version"] = "v2"
    v2_manifest["status"] = "BLOCKED_QUALITY_GATE"
    v2_manifest["object_prefix"] = "research/data004_ets_p0p1/v2/"
    v2_manifest["immutable_after_manifest_publication"] = True
    v2_manifest["immutability_note"] = "v2 fill and metadata objects, source manifest, and gate outputs are immutable after publication."
    v2_manifest["created_at_utc"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    v2_manifest["supersedes"] = {
        "version": "v1",
        "original_manifest_sha256": original_v1_manifest_sha,
        "annotated_manifest_sha256": annotated_v1_manifest_sha,
        "status": "BLOCKED_SUPERSEDED",
        "note": "v1 fill rows and Parquet objects are unchanged; v2 reuses those rows and changes only block_number, its provenance, ordering, and gate outputs.",
    }
    v2_manifest["block_number_join"] = {
        "source_column": "block_number",
        "custody_source_prefix": "custody/",
        "custody_join_key": "tx_hash",
        "timestamp_source_table": "public.block_timestamps(block_number bigint, ts integer)",
        "timestamp_join_key": "fill timestamp seconds -> ts; derive only if exactly one block_number has that ts",
        "timestamp_provenance": "block_timestamps_unique_ts",
        "validation": block_evidence["validation"],
        "derivation": block_evidence["derivation"],
        "provenance_mix": provenance_mix,
        "missing_block_number_rows": 0,
        "imputed_block_number_rows": 0,
        "ordering": ["block_number", "log_index", "token_id"],
    }
    v2_manifest["addendum_3"] = {
        "v1_manifest_sha256_before_supersession": original_v1_manifest_sha,
        "v1_manifest_sha256_after_supersession_annotation": annotated_v1_manifest_sha,
        "block_timestamp_evidence": "block_timestamp_evidence.json",
        "block_timestamp_evidence_sha256": sha256_file(output_dir / "block_timestamp_evidence.json"),
        "derivation_script": "scripts/research/data004/reconcile_data004_a3.py",
        "derivation_script_sha256": sha256_file(Path(__file__).resolve()),
        "conservation": "maker_taker_conservation.json",
        "conservation_scope_audit": "maker_taker_scope_audit.json",
        "conservation_residuals": "maker_taker_tx_condition_residuals.csv",
    }
    v2_manifest["exporter_sha256"] = v1_manifest.get("exporter_sha256")
    v2_manifest["fill_rows_reused_from_v1"] = True
    v2_manifest["source"]["notes_addendum_3"] = (
        "v2 reused the 231,964 finalized v1 fill rows. It did not rescan the trades lake. "
        "Only 4,989 null block_number values on 2026-09-20/21 were derived by unique ts lookup; "
        "the affected daily fill partitions were resorted and the gates recomputed."
    )

    new_files = []
    total_bytes = 0
    for file_record in v1_manifest["files"]:
        item = dict(file_record)
        old_path = item["path"]
        item["path"] = old_path.replace("research/data004_ets_p0p1/v1/", "research/data004_ets_p0p1/v2/", 1)
        item["object_prefix"] = "research/data004_ets_p0p1/v2/"
        if item.get("kind") == "fills":
            local_partition = package_root / "fills" / f"date={item['date']}"
            local_files = sorted(local_partition.glob("*.parquet"))
            if len(local_files) != 1:
                raise RuntimeError(f"expected one packaged fill object for {item['date']}")
            local_file = local_files[0]
            sha = sha256_file(local_file)
            item.update({
                "path": f"research/data004_ets_p0p1/v2/fills/date={item['date']}/{local_file.name}",
                "bytes": local_file.stat().st_size,
                "sha256": sha,
            })
            if item["date"] in rewritten_files:
                item["derived_block_rows"] = rewritten_files[item["date"]]["derived_block_rows"]
            total_bytes += int(item["bytes"])
        else:
            total_bytes += int(item["bytes"])
        new_files.append(item)
    v2_manifest["files"] = new_files
    v2_manifest["counts"]["parquet_bytes"] = total_bytes
    v2_manifest["validation"]["missing_block_number_rows"] = 0
    v2_manifest["validation"]["block_number_provenance_mix"] = provenance_mix
    v2_manifest["validation"]["ordering_failure_by_token"] = ordering_failures
    v2_manifest["validation"]["symmetry"] = {
        "grouping_key": ["tx_hash", "condition_id"],
        "grouping_note": conservation["per_tx_condition_conservation"]["method"],
        "groups_checked": 96_630,
        "exact_equal_size_groups": 96_587,
        "mismatch_groups": 43,
        "one_sided_groups": 0,
        "max_abs_size_delta": "0.0001",
        "outside_scope_explained_residual_groups": 0,
        "unexplained_residual_groups": 43,
        "residual_cause_counts": {"UNEXPLAINED_RESIDUAL": 43},
        "residuals_csv": "maker_taker_tx_condition_residuals.csv",
        "residuals_sha256": sha256_file(residual_path),
        "scope_audit_json": "maker_taker_scope_audit.json",
        "scope_audit_sha256": sha256_file(scope_audit_path),
        "unexplained_residuals": residuals,
        "pass": False,
    }
    for coverage_key in ("coverage_by_market", "coverage_by_token", "coverage_by_event", "coverage_by_date"):
        rows = v2_manifest["validation"].get(coverage_key, [])
        for row in rows:
            if "missing_block_rows" in row:
                row["missing_block_rows"] = 0
    for coverage_file in ("market_coverage.csv", "condition_coverage.csv", "token_coverage.csv", "event_coverage.csv", "date_coverage.csv"):
        path = output_dir / coverage_file
        with path.open(encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            fields = reader.fieldnames or []
            rows = list(reader)
        if "missing_block_rows" in fields:
            for row in rows:
                row["missing_block_rows"] = "0"
            atomic_csv(path, rows, fields)

    v2_gates = v2_manifest["gates"]
    v2_gates["block_number_provenance"] = {
        "provenance_mix": provenance_mix,
        "missing_block_number_rows": 0,
        "imputed_block_number_rows": 0,
        "custody_known_rows_validated": block_evidence["validation"]["custody_known_rows_checked"],
        "custody_timestamp_agreement_rows": block_evidence["validation"]["custody_known_rows_checked"],
        "timestamp_derived_rows": block_evidence["derivation"]["fill_rows_with_exactly_one_timestamp_match"],
        "pass": True,
    }
    v2_gates["ordering"] = {
        "key": ["token_id", "block_number", "log_index"],
        "ordering_failure_by_token": ordering_failures,
        "unorderable_missing_block_rows": 0,
        "missing_log_index_rows": 0,
        "missing_transaction_hash_rows": 0,
        "pass": not ordering_failures,
    }
    v2_gates["maker_taker_size_symmetry"] = v2_manifest["validation"]["symmetry"]
    failures = []
    for name, gate in v2_gates.items():
        if not gate.get("pass", False):
            failures.append({"gate": name, "details": gate})
    v2_manifest["failures"] = failures
    v2_manifest["status"] = "BLOCKED_QUALITY_GATE" if failures else "PASSED_QUALITY_GATE"
    v2_manifest["coverage"]["by_market"] = v2_manifest["validation"]["coverage_by_market"]
    v2_manifest["coverage"]["by_token"] = v2_manifest["validation"]["coverage_by_token"]
    v2_manifest["coverage"]["by_event"] = v2_manifest["validation"]["coverage_by_event"]
    v2_manifest["coverage"]["by_date"] = v2_manifest["validation"]["coverage_by_date"]
    json_write(output_dir / "data004_manifest.json", v2_manifest)

    quality = {
        "dataset_id": "DATA-004",
        "version": "v2",
        "manifest": "data/research/data004_ets_p0p1/v2/data004_manifest.json",
        "generated_from": "v1 finalized rows plus Addendum 3 deterministic block derivation and gate outputs",
        "all_gates_pass": not failures,
        "status": v2_manifest["status"],
        "gates": v2_gates,
        "failures": failures,
    }
    json_write(output_dir / "data004_quality.json", quality)
    json_write(LANE / "data004_quality.json", quality)
    json_write(LANE / "data004_a3_gate_summary.json", {
        "dataset_id": "DATA-004", "version": "v2", "status": v2_manifest["status"],
        "quality_manifest": "data/research/data004_ets_p0p1/v2/data004_quality.json",
        "unexplained_residual_groups": 43,
        "block_number_missing_rows": 0,
        "ordering_failures": ordering_failures,
        "gates_failed": [failure["gate"] for failure in failures],
    })
    return {"status": v2_manifest["status"], "files": len(new_files), "parquet_bytes": total_bytes,
            "row_count": row_count, "provenance_mix": provenance_mix, "failures": [f["gate"] for f in failures],
            "rewritten_files": rewritten_files}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fill-dir", type=Path, default=INBOX / PACKAGE_NAME / "fills")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "data/research/data004_ets_p0p1/v2")
    parser.add_argument("--analyze-only", action="store_true")
    parser.add_argument("--classify-scope", action="store_true")
    parser.add_argument("--build-v2", action="store_true")
    parser.add_argument("--block-evidence", type=Path, default=LANE / "data004_a3_block_timestamp_evidence.json")
    args = parser.parse_args()
    if os.geteuid() == 0:
        raise RuntimeError("run as ubuntu; do not read Sonar/.env as root")
    modes = [args.analyze_only, args.classify_scope, args.build_v2]
    if sum(modes) != 1:
        raise RuntimeError("select exactly one of --analyze-only, --classify-scope, or --build-v2")
    if args.build_v2:
        result = build_v2_local(args.fill_dir.resolve().parent, args.block_evidence.resolve(), args.output_dir.resolve())
    else:
        disk_gate(args.output_dir.parent, "before_conservation_audit", reserve_bytes=2 * (1 << 20))
        result = analyze_conservation(args.fill_dir.resolve(), args.output_dir.resolve()) if args.analyze_only else classify_scope_residuals(args.fill_dir.resolve(), args.output_dir.resolve())
    print(json.dumps(result, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
