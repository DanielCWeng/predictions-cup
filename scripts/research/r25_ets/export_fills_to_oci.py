#!/usr/bin/env python3
"""Stream the frozen ETS fill universe from OCI trades/ into research/r25_ets/.

This host-only exporter reads the accepted ETS token inventory, reads source trade and
custody Parquet from OCI, joins block_number on the transaction hash, and writes one small
Parquet object per populated condition/day. Local chunks are deleted immediately after upload.
It must run as the ubuntu user; it loads Sonar/.env with python-dotenv and never prints values.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SONAR_ROOT = Path("/home/ubuntu/polymarketwhale/Sonar")
POLY_ROOT = Path("/home/ubuntu/polymarketwhale")
DEFAULT_INVENTORY = Path(__file__).resolve().parents[3] / "data/research/ets_universe/ETS_TOKEN_INVENTORY.csv"
DEFAULT_LANE = Path("/home/ubuntu/campaigns/r25ets_20260928")
PREFIX = "research/r25_ets/"
BUCKET = "polymarket-bot-state"
MIN_FREE_BYTES = 6 * (1 << 30)
HARD_MIN_FREE_BYTES = 5 * (1 << 30)
MAX_CHUNK_ESTIMATE_BYTES = 512 * (1 << 20)
TRADE_COLUMNS = [
    "timestamp", "side", "price", "size_shares", "value_usd", "token_id", "condition_id",
    "maker_address", "taker_address", "tx_hash", "log_index",
]
DEDUP_KEY = ["tx_hash", "log_index", "token_id"]
CONTENT_COLUMNS = [
    "timestamp", "side", "price", "size_shares", "value_usd", "condition_id", "maker_address", "taker_address",
]


def free_bytes(path: Path) -> int:
    return shutil.disk_usage(path).free


def pause_for_disk(lane: Path, stage: str, available: int) -> None:
    lane.mkdir(parents=True, exist_ok=True)
    message = (
        "# PAUSED: disk safety gate\n\n"
        f"Stage: `{stage}`\n\n"
        f"Observed free space: `{available}` bytes\n\n"
        "The host hard stop is 6 GiB free and the absolute floor is 5 GiB. Resume only after "
        "free space is at least 6 GiB and the production system owner has not reported pressure.\n"
    )
    (lane / "PAUSED_DISK.md").write_text(message, encoding="utf-8")


def disk_gate(lane: Path, stage: str, reserve_bytes: int = 0) -> None:
    available = free_bytes(Path("/home/ubuntu"))
    if available < MIN_FREE_BYTES or available - reserve_bytes < HARD_MIN_FREE_BYTES:
        pause_for_disk(lane, stage, available)
        raise RuntimeError(f"disk safety gate paused {stage}: free={available} bytes")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def json_bytes(value: Any) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def source_objects(client: Any, namespace: str, bucket: str, prefix: str) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    start = None
    while True:
        args: dict[str, Any] = {
            "namespace_name": namespace, "bucket_name": bucket, "prefix": prefix, "limit": 1000,
        }
        if start:
            args["start"] = start
        page = client.list_objects(**args)
        for obj in page.data.objects:
            name = str(obj.name)
            if re.fullmatch(re.escape(prefix) + r"\d{4}-\d{2}-\d{2}\.parquet", name):
                day = name.rsplit("/", 1)[-1][:-8]
                result[day] = {
                    "object_name": name,
                    "etag": getattr(obj, "etag", None),
                    "size": getattr(obj, "size", None),
                    "last_modified": str(getattr(obj, "time_modified", "") or ""),
                }
        start = page.data.next_start_with
        if not start:
            return result


def load_inventory(path: Path) -> tuple[dict[str, dict[str, str]], dict[str, set[str]]]:
    by_token: dict[str, dict[str, str]] = {}
    by_condition: dict[str, set[str]] = defaultdict(set)
    with path.open(encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            if not row.get("condition_id") or not row.get("token_id"):
                raise ValueError("ETS token inventory contains a blank condition or token ID")
            token = row["token_id"].strip()
            condition = row["condition_id"].strip()
            if token in by_token and by_token[token].get("condition_id") != condition:
                raise ValueError(f"ETS token maps to multiple conditions: {token}")
            by_token[token] = {k: (v or "") for k, v in row.items()}
            by_condition[condition].add(token)
    if not by_token:
        raise ValueError("ETS token inventory is empty")
    return by_token, by_condition


def put_bytes(client: Any, namespace: str, bucket: str, object_name: str, payload: bytes) -> None:
    from io import BytesIO
    client.put_object(
        namespace_name=namespace, bucket_name=bucket, object_name=object_name,
        put_object_body=BytesIO(payload), content_type="application/json",
    )


def refresh_object_metadata(client: Any, namespace: str, bucket: str, source: dict[str, Any]) -> None:
    """Read OCI object identity metadata without downloading source bytes."""
    if source.get("etag") and source.get("size"):
        return
    response = client.head_object(
        namespace_name=namespace, bucket_name=bucket, object_name=source["object_name"],
    )
    headers = response.headers
    source["etag"] = headers.get("etag") or source.get("etag")
    size = headers.get("content-length")
    source["size"] = int(size) if size is not None else source.get("size")
    source["last_modified"] = headers.get("last-modified") or source.get("last_modified")
    # Preserve a service-supplied content digest if the object was uploaded with one.
    source["source_sha256"] = headers.get("opc-meta-sha256") or source.get("source_sha256")


def assert_tx_block_map(custody: Any, day: str) -> Any:
    import polars as pl
    conflicts = (
        custody.group_by("tx_hash")
        .agg(pl.col("block_number").n_unique().alias("block_count"))
        .filter(pl.col("block_count") != 1)
    )
    if conflicts.height:
        examples = conflicts.head(5).to_dicts()
        raise ValueError(f"custody has transaction hashes with conflicting block_number on {day}: {examples}")
    return custody.select("tx_hash", "block_number").unique(subset=["tx_hash"], keep="first")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--inventory", type=Path, default=DEFAULT_INVENTORY)
    parser.add_argument("--lane", type=Path, default=DEFAULT_LANE)
    parser.add_argument("--bucket", default=BUCKET)
    parser.add_argument("--object-prefix", default=PREFIX)
    parser.add_argument("--max-dates", type=int, default=0, help="diagnostic cap; leave zero for complete export")
    args = parser.parse_args()
    if args.object_prefix != PREFIX:
        raise ValueError(f"writes are limited to the authorized prefix {PREFIX!r}")
    if os.geteuid() == 0:
        raise RuntimeError("run as ubuntu; root must not read Sonar/.env")
    disk_gate(args.lane, "before_fill_export")
    os.environ.setdefault("POLARS_MAX_THREADS", "2")

    from dotenv import load_dotenv
    load_dotenv(SONAR_ROOT / ".env", override=False)
    sys.path.insert(0, str(SONAR_ROOT))
    import oci
    import polars as pl
    import config
    from pnl_common import _oci_s3_storage_options

    signer, cfg = config.get_oci_signer_and_config()
    client = oci.object_storage.ObjectStorageClient(cfg, signer=signer) if signer else oci.object_storage.ObjectStorageClient(cfg)
    namespace = client.get_namespace().data
    storage_options = _oci_s3_storage_options(namespace)
    by_token, by_condition = load_inventory(args.inventory)
    trade_objects = source_objects(client, namespace, args.bucket, "trades/")
    custody_objects = source_objects(client, namespace, args.bucket, "custody/")
    creation_times = [parse_date(row.get("created_at")) for row in by_token.values()]
    if creation_times and all(value is not None for value in creation_times):
        first_expected_day = min(value for value in creation_times if value).date().isoformat()
        days = sorted(day for day in trade_objects if day >= first_expected_day)
    else:
        # Missing creation timestamps disable source pruning; no inferred time window is used.
        first_expected_day = min(trade_objects)
        days = sorted(trade_objects)
    if not days:
        raise RuntimeError("no dates have both trades and custody source objects")
    if args.max_dates:
        days = days[: args.max_dates]

    output_files: list[dict[str, Any]] = []
    day_coverage: list[dict[str, Any]] = []
    total_rows = 0
    total_duplicates = 0
    min_ts = None
    max_ts = None
    known_gaps: list[dict[str, Any]] = []
    gaps_by_condition: dict[str, list[dict[str, Any]]] = defaultdict(list)
    token_values = pl.Series(sorted(by_token), dtype=pl.String).implode()

    def record_block_gap(day: str, reason: str, fills: Any) -> None:
        counts = fills.group_by("condition_id").len().sort("condition_id").to_dicts()
        gap = {
            "date": day,
            "reason": reason,
            "unexported_fill_rows": fills.height,
            "affected_condition_ids": [str(row["condition_id"]) for row in counts],
        }
        known_gaps.append(gap)
        for row in counts:
            gaps_by_condition[str(row["condition_id"])].append({
                "date": day, "reason": reason, "unexported_fill_rows": int(row["len"]),
            })

    for day in days:
        disk_gate(args.lane, f"before_source_day_{day}")
        trade = trade_objects[day]
        custody_meta = custody_objects.get(day)
        trade_uri = f"s3://{args.bucket}/{trade['object_name']}"
        schema = pl.scan_parquet(trade_uri, storage_options=storage_options).collect_schema()
        absent = set(TRADE_COLUMNS) - set(schema.names())
        if absent:
            raise ValueError(f"trade source {trade['object_name']} lacks required columns {sorted(absent)}")
        fills = (
            pl.scan_parquet(trade_uri, storage_options=storage_options)
            .select(TRADE_COLUMNS)
            .filter(pl.col("token_id").is_in(token_values))
            .collect(engine="streaming")
        )
        if not fills.height:
            day_coverage.append({
                "date": day, "fill_rows": 0, "block_joined_rows": 0, "status": "NO_MATCHED_FILLS",
                "trade_object": trade, "custody_object": custody_meta,
            })
            continue
        refresh_object_metadata(client, namespace, args.bucket, trade)
        if day not in custody_objects:
            record_block_gap(day, "fills exist but no same-day custody object is available for a block_number join", fills)
            day_coverage.append({
                "date": day, "fill_rows": fills.height, "block_joined_rows": 0,
                "status": "BLOCKED_BLOCK_NUMBER_PROVENANCE", "trade_object": trade,
                "custody_object": None,
            })
            continue
        custody_meta = custody_objects[day]
        refresh_object_metadata(client, namespace, args.bucket, custody_meta)
        custody_uri = f"s3://{args.bucket}/{custody_meta['object_name']}"
        if fills.filter(~pl.col("condition_id").is_in(pl.Series(sorted(by_condition), dtype=pl.String).implode())).height:
            raise ValueError(f"trade rows for frozen tokens point to an out-of-universe condition on {day}")

        txs = fills.select(pl.col("tx_hash").drop_nulls().unique().alias("tx_hash"))
        if txs.height == 0:
            record_block_gap(day, "matched fills have no transaction hashes", fills)
            day_coverage.append({
                "date": day, "fill_rows": fills.height, "block_joined_rows": 0,
                "status": "BLOCKED_BLOCK_NUMBER_PROVENANCE", "trade_object": trade,
                "custody_object": custody_meta,
            })
            continue
        custody_schema = pl.scan_parquet(custody_uri, storage_options=storage_options).collect_schema()
        needed_custody = {"tx_hash", "block_number"}
        if not needed_custody.issubset(custody_schema.names()):
            record_block_gap(day, "custody source lacks tx_hash/block_number identity fields", fills)
            day_coverage.append({
                "date": day, "fill_rows": fills.height, "block_joined_rows": 0,
                "status": "BLOCKED_BLOCK_NUMBER_PROVENANCE", "trade_object": trade,
                "custody_object": custody_meta,
            })
            continue
        custody_matches = (
            pl.scan_parquet(custody_uri, storage_options=storage_options)
            .select("tx_hash", "block_number")
            .join(txs.lazy(), on="tx_hash", how="semi")
            .collect(engine="streaming")
        )
        if custody_matches.height == 0:
            record_block_gap(day, "no custody block-number rows match any fill transaction", fills)
            day_coverage.append({
                "date": day, "fill_rows": fills.height, "block_joined_rows": 0,
                "status": "BLOCKED_BLOCK_NUMBER_PROVENANCE", "trade_object": trade,
                "custody_object": custody_meta,
            })
            continue
        try:
            tx_block = assert_tx_block_map(custody_matches, day)
        except ValueError as exc:
            record_block_gap(day, str(exc), fills)
            day_coverage.append({
                "date": day, "fill_rows": fills.height, "block_joined_rows": 0,
                "status": "BLOCKED_BLOCK_NUMBER_PROVENANCE", "trade_object": trade,
                "custody_object": custody_meta,
            })
            continue
        joined = fills.join(tx_block, on="tx_hash", how="left")
        unmatched = joined.filter(pl.col("block_number").is_null())
        null_identity = joined.filter(pl.col("tx_hash").is_null() | pl.col("log_index").is_null() | pl.col("block_number").is_null())
        if unmatched.height or null_identity.height:
            record_block_gap(
                day,
                f"{unmatched.height} fills lack custody block matches and {null_identity.height} fills have null on-chain identity fields",
                fills,
            )
            day_coverage.append({
                "date": day, "fill_rows": fills.height, "block_joined_rows": 0,
                "status": "BLOCKED_BLOCK_NUMBER_PROVENANCE", "trade_object": trade,
                "custody_object": custody_meta,
            })
            continue

        duplicate_stats = (
            joined.group_by(DEDUP_KEY)
            .agg(pl.len().alias("n"), pl.struct(CONTENT_COLUMNS).n_unique().alias("content_n"))
            .filter(pl.col("n") > 1)
        )
        conflicting = duplicate_stats.filter(pl.col("content_n") > 1)
        if conflicting.height:
            record_block_gap(day, f"conflicting canonical fill keys: {conflicting.head(5).to_dicts()}", fills)
            day_coverage.append({
                "date": day, "fill_rows": fills.height, "block_joined_rows": 0,
                "status": "BLOCKED_CONFLICTING_DUPLICATES", "trade_object": trade,
                "custody_object": custody_meta,
            })
            continue
        dropped = int((duplicate_stats["n"].sum() or 0) - duplicate_stats.height)
        total_duplicates += dropped
        joined = joined.unique(subset=DEDUP_KEY, keep="first", maintain_order=True)

        token_frame = pl.DataFrame(list(by_token.values()), infer_schema_length=None)
        joined = joined.join(
            token_frame.select(
                "token_id", "outcome", "event_id", "market_id", "question", "slug", "event_slug"
            ).rename({"market_id": "polymarket_market_id", "slug": "market_slug"}),
            on="token_id", how="left",
        )
        if joined.filter(pl.col("polymarket_market_id").is_null()).height:
            raise ValueError(f"source token is absent from canonical frozen token inventory on {day}")
        joined = joined.with_columns(
            pl.from_epoch(pl.col("timestamp"), time_unit="s").dt.replace_time_zone("UTC").dt.strftime("%Y-%m-%dT%H:%M:%SZ").alias("timestamp_utc"),
            pl.lit("POLYLEVIATHAN_TRADES_LAKE_CANONICAL").alias("source_version"),
            pl.lit("TRADE_FILL").alias("evidence_grade"),
            pl.lit(trade["object_name"]).alias("source_trade_object"),
            pl.lit(str(trade.get("etag") or "")).alias("source_trade_etag"),
            pl.lit(str(trade.get("source_sha256") or "")).alias("source_trade_sha256"),
            pl.lit(int(trade.get("size") or 0)).alias("source_trade_bytes"),
            pl.lit(custody_meta["object_name"]).alias("source_custody_object"),
            pl.lit(str(custody_meta.get("etag") or "")).alias("source_custody_etag"),
            pl.lit(str(custody_meta.get("source_sha256") or "")).alias("source_custody_sha256"),
            pl.lit(int(custody_meta.get("size") or 0)).alias("source_custody_bytes"),
            pl.lit("tx_hash exact join to OCI custody log block_number; transaction block is shared by all logs").alias("block_number_provenance"),
            pl.lit("POLYLEVIATHAN_OCI_TRADES").alias("source"),
            pl.concat_str(["tx_hash", pl.col("log_index").cast(pl.String), "token_id"], separator=":").alias("fill_id"),
        ).sort(["condition_id", "block_number", "log_index", "token_id"])

        day_rows = joined.height
        day_min_ts = int(joined["timestamp"].min())
        day_max_ts = int(joined["timestamp"].max())
        min_ts = day_min_ts if min_ts is None else min(min_ts, day_min_ts)
        max_ts = day_max_ts if max_ts is None else max(max_ts, day_max_ts)

        condition_ids = sorted(set(joined["condition_id"].to_list()))
        day_file_count = 0
        for condition_id in condition_ids:
            chunk = joined.filter(pl.col("condition_id") == condition_id)
            if not chunk.height:
                continue
            estimate = int(chunk.estimated_size() * 1.25)
            if estimate > MAX_CHUNK_ESTIMATE_BYTES:
                raise RuntimeError(f"condition/day chunk estimate too large for safe local staging: {condition_id} {day} {estimate}")
            disk_gate(args.lane, f"before_write_{condition_id}_{day}", reserve_bytes=estimate)
            chunk = chunk.select([
                "fill_id", "timestamp", "timestamp_utc", "block_number", "log_index", "tx_hash",
                "condition_id", "polymarket_market_id", "event_id", "token_id", "outcome", "side",
                "price", "size_shares", "value_usd", "maker_address", "taker_address", "source",
                "source_version", "evidence_grade", "block_number_provenance", "source_trade_object",
                "source_trade_etag", "source_trade_sha256", "source_trade_bytes", "source_custody_object",
                "source_custody_etag", "source_custody_sha256", "source_custody_bytes",
            ])
            with tempfile.NamedTemporaryFile(prefix="r25-ets-fill-", suffix=".parquet", delete=False) as tmp:
                local_path = Path(tmp.name)
            try:
                chunk.write_parquet(local_path, compression="zstd", statistics=True)
                disk_gate(args.lane, f"after_write_{condition_id}_{day}")
                digest = sha256_file(local_path)
                object_name = f"{PREFIX}fills/family=SIG_CUP_ETS/condition_id={condition_id}/date={day}/part-000.parquet"
                with local_path.open("rb") as handle:
                    client.put_object(
                        namespace_name=namespace, bucket_name=args.bucket, object_name=object_name,
                        put_object_body=handle, content_type="application/vnd.apache.parquet",
                    )
                bytes_written = local_path.stat().st_size
                output_files.append({
                    "path": object_name, "rows": chunk.height, "bytes": bytes_written, "sha256": digest,
                    "condition_id": condition_id, "market_id": chunk["polymarket_market_id"][0],
                    "first_timestamp": int(chunk["timestamp"].min()), "last_timestamp": int(chunk["timestamp"].max()),
                    "first_block_number": int(chunk["block_number"].min()), "last_block_number": int(chunk["block_number"].max()),
                    "source_trade_object": trade["object_name"], "source_trade_etag": trade.get("etag"),
                    "source_trade_sha256": trade.get("source_sha256"), "source_trade_bytes": trade.get("size"),
                    "source_custody_object": custody_meta["object_name"], "source_custody_etag": custody_meta.get("etag"),
                    "source_custody_sha256": custody_meta.get("source_sha256"), "source_custody_bytes": custody_meta.get("size"),
                })
                day_file_count += 1
                total_rows += chunk.height
            finally:
                local_path.unlink(missing_ok=True)
        day_coverage.append({
            "date": day, "fill_rows": day_rows, "block_joined_rows": day_rows,
            "duplicate_rows_removed": dropped, "file_count": day_file_count,
            "status": "EXPORTED", "trade_object": trade, "custody_object": custody_meta,
        })
        print(f"day={day} rows={day_rows} files={day_file_count} dedup_dropped={dropped} total={total_rows}", flush=True)

    def utc(ts: int | None) -> str | None:
        return datetime.fromtimestamp(ts, timezone.utc).isoformat().replace("+00:00", "Z") if ts is not None else None

    files_by_condition: dict[str, list[dict[str, Any]]] = defaultdict(list)
    tokens_by_condition: dict[str, list[dict[str, str]]] = defaultdict(list)
    for file_row in output_files:
        files_by_condition[file_row["condition_id"]].append(file_row)
    for token_row in by_token.values():
        tokens_by_condition[token_row["condition_id"]].append(token_row)
    condition_summary: dict[str, dict[str, Any]] = {}
    for condition_id in sorted(by_condition):
        matching = files_by_condition[condition_id]
        token_rows = tokens_by_condition[condition_id]
        dates = [parse_date(r.get("start_date")) for r in token_rows] + [parse_date(r.get("created_at")) for r in token_rows]
        starts = [value for value in dates if value]
        expected_start = min(starts).isoformat().replace("+00:00", "Z") if starts else None
        condition_summary[condition_id] = {
            "market_id": token_rows[0].get("market_id") if token_rows else None,
            "expected_start": expected_start,
            "resolved_at": token_rows[0].get("resolution_time") if token_rows else None,
            "row_count": sum(r["rows"] for r in matching),
            "file_count": len(matching),
            "bytes": sum(r["bytes"] for r in matching),
            "available_start": min((r["first_timestamp"] for r in matching), default=None),
            "available_end": max((r["last_timestamp"] for r in matching), default=None),
            "first_block_number": min((r["first_block_number"] for r in matching), default=None),
            "last_block_number": max((r["last_block_number"] for r in matching), default=None),
            "complete_to_source_bounds": not gaps_by_condition.get(condition_id),
            "known_gaps": gaps_by_condition.get(condition_id, []),
        }
    manifest = {
        "schema_version": 1,
        "dataset_id": "POLYLEVIATHAN_R25_ETS_FILLS",
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "bucket": args.bucket,
        "object_prefix": PREFIX + "fills/",
        "source": "OCI trades/ canonical daily Parquet, using sisterreq_fills_20260926 identity/dedup semantics",
        "block_number_join": {
            "source_prefix": "custody/",
            "join_key": "tx_hash",
            "source_column": "block_number",
            "validation": "one unique block_number per tx_hash; every selected fill tx_hash must match; conflicting or unmatched joins abort",
            "ordering": ["block_number", "log_index", "token_id"],
            "source_code": "Sonar/custody_indexer.py CUSTODY_SCHEMA; block_number retained from source chain log",
        },
        "dedup_key": DEDUP_KEY,
        "row_count": total_rows,
        "duplicate_rows_removed": total_duplicates,
        "file_count": len(output_files),
        "bytes": sum(r["bytes"] for r in output_files),
        "minimum_timestamp": min_ts,
        "maximum_timestamp": max_ts,
        "minimum_timestamp_utc": utc(min_ts),
        "maximum_timestamp_utc": utc(max_ts),
        "condition_count_attempted": len(by_condition),
        "token_count_attempted": len(by_token),
        "source_date_count": len(days),
        "earliest_expected_market_created_at": min(creation_times).isoformat().replace("+00:00", "Z") if creation_times and all(creation_times) else None,
        "source_pruning_basis": "earliest accepted Gamma created_at; if any are unavailable, all source dates are scanned",
        "source_date_first": days[0], "source_date_last": days[-1],
        "source_trade_objects": {d: trade_objects[d] for d in days},
        "source_custody_objects": {d: custody_objects[d] for d in days if d in custody_objects},
        "dates_missing_custody_object": [d for d in days if d not in custody_objects],
        "coverage_by_day": day_coverage,
        "coverage_by_condition_id": condition_summary,
        "files": output_files,
        "schema": [
            "fill_id", "timestamp", "timestamp_utc", "block_number", "log_index", "tx_hash",
            "condition_id", "polymarket_market_id", "event_id", "token_id", "outcome", "side",
            "price", "size_shares", "value_usd", "maker_address", "taker_address", "source",
            "source_version", "evidence_grade", "block_number_provenance", "source_trade_object",
            "source_trade_etag", "source_trade_sha256", "source_trade_bytes", "source_custody_object",
            "source_custody_etag", "source_custody_sha256", "source_custody_bytes",
        ],
        "status": "PARTIAL_BLOCKED" if known_gaps else "COMPLETE",
        "known_gaps": known_gaps,
    }
    manifest_payload = json_bytes(manifest)
    manifest_sha = hashlib.sha256(manifest_payload).hexdigest()
    put_bytes(client, namespace, args.bucket, PREFIX + "fills/MANIFEST.json", manifest_payload)
    args.lane.mkdir(parents=True, exist_ok=True)
    summary = {
        "status": manifest["status"], "manifest_object": PREFIX + "fills/MANIFEST.json",
        "manifest_sha256": manifest_sha,
        "fill_rows": total_rows, "files": len(output_files), "bytes": manifest["bytes"],
        "markets_attempted": len(by_condition), "tokens_attempted": len(by_token),
        "dedup_key": DEDUP_KEY, "ordering": ["block_number", "log_index", "token_id"],
        "coverage": [days[0], days[-1]], "min_timestamp_utc": utc(min_ts), "max_timestamp_utc": utc(max_ts),
    }
    (args.lane / "fill_export_summary.json").write_bytes(json_bytes(summary))
    print(json.dumps(summary, sort_keys=True), flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        DEFAULT_LANE.mkdir(parents=True, exist_ok=True)
        failure = {
            "status": "BLOCKED_OR_FAILED",
            "error_type": type(exc).__name__,
            "error": str(exc),
            "reported_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        }
        (DEFAULT_LANE / "fill_export_summary.json").write_bytes(json_bytes(failure))
        raise
