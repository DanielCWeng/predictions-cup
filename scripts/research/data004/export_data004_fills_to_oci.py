#!/usr/bin/env python3
"""Stream immutable DATA-004 P0/P1 fills from the Polyleviathan OCI trades lake."""

from __future__ import annotations

import argparse
import contextlib
import csv
import ctypes
import gc
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from collections import Counter
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

SONAR_ROOT = Path("/home/ubuntu/polymarketwhale/Sonar")
DEFAULT_LANE = Path("/home/ubuntu/campaigns/data004_20260929")
ROOT = Path(__file__).resolve().parents[3]
BUCKET = "polymarket-bot-state"
TRADE_PREFIX = "trades/"
CUSTODY_PREFIX = "custody/"
DATA_PREFIX = "research/data004_ets_p0p1/v1/"
FINAL_MANIFEST = DATA_PREFIX + "MANIFEST.json"
MIN_FREE_BYTES = 6 * (1 << 30)
HARD_MIN_FREE_BYTES = 5 * (1 << 30)
MAX_TEMP_FILE_BYTES = 200 * (1 << 20)
MAX_ESTIMATED_CHUNK_BYTES = 180 * (1 << 20)
STREAMING_CHUNK_ROWS = 4096
RESUME_COMPATIBLE_EXPORTER_SHA256 = {
    "9fcf0860f2fc70567ee9533068e7b02f21ca484a38df05cbb7bdae3dd7a01b32",
    "8a8eef811ccded488668fec40dd7a31df2f2eef17a6f233bca26fdfb5eb561c8",
}
DEDUP_KEY = ["tx_hash", "log_index", "token_id"]
CONTENT_COLUMNS = [
    "timestamp",
    "side",
    "price",
    "size_shares",
    "value_usd",
    "condition_id",
    "maker_address",
    "taker_address",
]
TRADE_COLUMNS = [
    "timestamp",
    "side",
    "price",
    "size_shares",
    "value_usd",
    "token_id",
    "condition_id",
    "maker_address",
    "taker_address",
    "tx_hash",
    "log_index",
]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def json_bytes(value: Any) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    payload = json_bytes(value)
    with tmp.open("wb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)
    directory_fd = os.open(str(path.parent), os.O_DIRECTORY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def atomic_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


def df_free_bytes() -> int:
    result = subprocess.run(
        ["df", "-B1", "--output=avail", "/home/ubuntu"],
        check=True,
        text=True,
        capture_output=True,
    )
    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    return int(lines[-1])


def trim_process_memory() -> None:
    """Release freed per-day Arrow/Polars arenas before opening the next large source object."""
    gc.collect()
    with contextlib.suppress(AttributeError, OSError):
        ctypes.CDLL("libc.so.6").malloc_trim(0)


def disk_gate(lane: Path, stage: str, reserve_bytes: int = 0) -> int:
    log_path = lane / "disk_check_log.jsonl"
    started = time.monotonic()
    while True:
        available = df_free_bytes()
        entry = {
            "checked_at_utc": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "stage": stage,
            "available_bytes": available,
            "reserve_bytes": reserve_bytes,
            "min_free_required_bytes": MIN_FREE_BYTES,
            "hard_floor_bytes": HARD_MIN_FREE_BYTES,
        }
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        if available < HARD_MIN_FREE_BYTES:
            raise RuntimeError(
                f"disk is already below the 5 GiB hard floor at {stage}: {available} bytes"
            )
        if available >= MIN_FREE_BYTES and available - reserve_bytes >= HARD_MIN_FREE_BYTES:
            return available
        elapsed = time.monotonic() - started
        if elapsed >= 2 * 60 * 60:
            raise RuntimeError(
                f"disk gate timed out after two hours at {stage}: {available} bytes free"
            )
        print(
            f"PAUSED disk gate stage={stage} free_bytes={available}; recheck in 5 minutes "
            f"(elapsed={int(elapsed)}s, max=7200s)",
            flush=True,
        )
        time.sleep(300)


def source_objects(client: Any, namespace: str, prefix: str) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    start = None
    while True:
        args: dict[str, Any] = {
            "namespace_name": namespace,
            "bucket_name": BUCKET,
            "prefix": prefix,
            "limit": 1000,
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


def refresh_object_metadata(client: Any, namespace: str, source: dict[str, Any]) -> None:
    if source.get("etag") and source.get("size"):
        return
    response = client.head_object(
        namespace_name=namespace,
        bucket_name=BUCKET,
        object_name=source["object_name"],
    )
    headers = response.headers
    source["etag"] = headers.get("etag") or source.get("etag")
    size = headers.get("content-length")
    source["size"] = int(size) if size is not None else source.get("size")
    source["last_modified"] = headers.get("last-modified") or source.get("last_modified")
    source["source_sha256"] = headers.get("opc-meta-sha256") or source.get("source_sha256")


def parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    text = str(value).strip().replace("Z", "+00:00")
    # Gamma emits valid UTC timestamps with 1–5 fractional digits. Python's
    # fromisoformat accepts only select fractional widths, so normalize the
    # explicitly supplied fraction to microseconds without inventing timezone data.
    fraction = re.search(r"\.(\d+)([+-]\d{2}:\d{2})$", text)
    if fraction:
        digits = fraction.group(1)
        normalized = digits[:6].ljust(6, "0")
        text = text[: fraction.start(1)] + normalized + fraction.group(2)
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(UTC)


def utc_string(epoch: int | float | None) -> str | None:
    if epoch is None:
        return None
    return datetime.fromtimestamp(float(epoch), UTC).isoformat().replace("+00:00", "Z")


def utc_day(epoch: int | float | None) -> str | None:
    value = utc_string(epoch)
    return value[:10] if value else None


def is_404(exc: Exception) -> bool:
    return int(getattr(exc, "status", 0) or 0) == 404


def object_exists(client: Any, namespace: str, object_name: str) -> dict[str, Any] | None:
    try:
        result = client.head_object(
            namespace_name=namespace,
            bucket_name=BUCKET,
            object_name=object_name,
        )
        size = result.headers.get("content-length")
        return {"size": int(size) if size is not None else None, "etag": result.headers.get("etag")}
    except Exception as exc:
        if is_404(exc):
            return None
        raise


def save_checkpoint(path: Path, checkpoint: dict[str, Any]) -> None:
    checkpoint["updated_at_utc"] = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    write_json(path, checkpoint)


def build_dimensions(universe: dict[str, Any]) -> tuple[Any, Any, dict[str, dict[str, Any]]]:
    import polars as pl

    markets = universe["markets"]
    token_rows = universe["tokens"]
    token_metadata: dict[str, dict[str, Any]] = {}
    market_by_id = {str(row["market_id"]): row for row in markets}
    for token in token_rows:
        market = market_by_id[str(token["market_id"])]
        gamma = market["gamma_market"]
        event = market.get("gamma_event") or {}
        token_metadata[str(token["token_id"])] = {
            "market_id": str(market["market_id"]),
            "gamma_condition_id": str(market["condition_id"]),
            "outcome_label": str(token["outcome_label"]),
            "event_id": str(market.get("event_id") or ""),
            "event_slug": str(market.get("event_slug") or event.get("slug") or ""),
            "event_title": str(market.get("event_title") or event.get("title") or ""),
            "market_slug": str(gamma.get("slug") or ""),
            "market_question": str(gamma.get("question") or market.get("question") or ""),
            "market_created_at": str(gamma.get("createdAt") or ""),
            "market_start_date": str(gamma.get("startDate") or market.get("start_date") or ""),
            "market_end_date": str(gamma.get("endDate") or market.get("end_date") or ""),
            "market_closed": bool(gamma.get("closed")) if gamma.get("closed") is not None else None,
            "market_active": bool(gamma.get("active")) if gamma.get("active") is not None else None,
            "market_neg_risk": bool(gamma.get("negRisk"))
            if gamma.get("negRisk") is not None
            else None,
            "acquisition_class": str(market.get("acquisition_class") or ""),
            "acquisition_tier": str(market.get("priority") or ""),
            "contract_archetype": str(market.get("contract_archetype") or ""),
            "mathematical_class": str(market.get("mathematical_class") or ""),
            "relationship_class_set_json": str(market.get("relationship_class_set_json") or "[]"),
            "sig_market_ids_json": str(market.get("sig_market_ids_json") or "[]"),
            "sig_exchange_ids_json": str(market.get("sig_exchange_ids_json") or "[]"),
            "gamma_description_sha256": str(market.get("gamma_description_sha256") or ""),
            "graph_link_ids_json": json.dumps(
                sorted(
                    {edge.get("relationship_id", "") for edge in market.get("graph_links") or []}
                ),
                separators=(",", ":"),
            ),
            "graph_relationship_classes_json": json.dumps(
                sorted(
                    {edge.get("relationship_class", "") for edge in market.get("graph_links") or []}
                ),
                separators=(",", ":"),
            ),
            "gamma_market_sha256": hashlib.sha256(
                json.dumps(gamma, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest(),
        }
    market_rows: list[dict[str, Any]] = []
    for market in markets:
        gamma = market["gamma_market"]
        event = market.get("gamma_event") or {}
        market_rows.append(
            {
                "market_id": str(market["market_id"]),
                "condition_id": str(market["condition_id"]),
                "event_id": str(market.get("event_id") or ""),
                "event_slug": str(market.get("event_slug") or event.get("slug") or ""),
                "event_title": str(market.get("event_title") or event.get("title") or ""),
                "market_slug": str(gamma.get("slug") or ""),
                "market_question": str(gamma.get("question") or market.get("question") or ""),
                "market_description": str(gamma.get("description") or ""),
                "created_at": str(gamma.get("createdAt") or ""),
                "start_date": str(gamma.get("startDate") or market.get("start_date") or ""),
                "end_date": str(gamma.get("endDate") or market.get("end_date") or ""),
                "closed": bool(gamma.get("closed")) if gamma.get("closed") is not None else None,
                "active": bool(gamma.get("active")) if gamma.get("active") is not None else None,
                "neg_risk": bool(gamma.get("negRisk"))
                if gamma.get("negRisk") is not None
                else None,
                "outcomes_json": json.dumps(
                    market.get("gamma_outcomes") or [], separators=(",", ":")
                ),
                "token_alignment_json": json.dumps(
                    market.get("gamma_outcome_token_alignment") or [], separators=(",", ":")
                ),
                "acquisition_class": str(market.get("acquisition_class") or ""),
                "acquisition_tier": str(market.get("priority") or ""),
                "contract_archetype": str(market.get("contract_archetype") or ""),
                "mathematical_class": str(market.get("mathematical_class") or ""),
                "relationship_class_set_json": str(
                    market.get("relationship_class_set_json") or "[]"
                ),
                "sig_market_ids_json": str(market.get("sig_market_ids_json") or "[]"),
                "sig_exchange_ids_json": str(market.get("sig_exchange_ids_json") or "[]"),
                "expected_empirical_use": str(market.get("expected_empirical_use") or ""),
                "reason": str(market.get("reason") or ""),
                "gamma_description_sha256": str(market.get("gamma_description_sha256") or ""),
                "graph_links_json": json.dumps(
                    market.get("graph_links") or [], sort_keys=True, separators=(",", ":")
                ),
                "sig_anchor_rows_json": json.dumps(
                    market.get("sig_anchor_rows") or [], sort_keys=True, separators=(",", ":")
                ),
                "gamma_market_sha256": hashlib.sha256(
                    json.dumps(gamma, sort_keys=True, separators=(",", ":")).encode()
                ).hexdigest(),
                "gamma_event_fetch_error": str(market.get("gamma_event_fetch_error") or ""),
            }
        )
    token_dimension_rows = [
        {
            "market_id": str(row["market_id"]),
            "condition_id": str(row["condition_id"]),
            "token_id": str(row["token_id"]),
            "outcome_label": str(row["outcome_label"]),
            "market_question": str(row.get("market_question") or ""),
            "event_id": str(row.get("event_id") or ""),
            "acquisition_class": str(row.get("acquisition_class") or ""),
            "acquisition_tier": str(row.get("priority") or ""),
        }
        for row in token_rows
    ]
    return (
        pl.DataFrame(market_rows, infer_schema_length=None),
        pl.DataFrame(token_dimension_rows, infer_schema_length=None),
        token_metadata,
    )


def active_role_expr(exchange_addresses: list[str]) -> Any:
    import polars as pl

    maker = pl.col("maker_address").cast(pl.String, strict=False)
    taker = pl.col("taker_address").cast(pl.String, strict=False).str.to_lowercase()
    role_known = (
        maker.is_not_null()
        & (maker.str.len_chars() > 0)
        & pl.col("taker_address").is_not_null()
        & (pl.col("taker_address").cast(pl.String).str.len_chars() > 0)
    )
    active = taker.is_in(exchange_addresses) & maker.is_not_null() & (maker.str.len_chars() > 0)
    return pl.when(role_known).then(active).otherwise(None)


def transform_fills(
    fills: Any,
    token_metadata: dict[str, dict[str, Any]],
    *,
    trade: dict[str, Any],
    custody: dict[str, Any] | None,
    block_map: Any,
    conflict_tx_hashes: list[str],
    exchange_addresses: list[str],
) -> Any:
    import polars as pl

    token_rows = [
        {"token_id": token_id, **metadata} for token_id, metadata in token_metadata.items()
    ]
    token_df = pl.DataFrame(token_rows, infer_schema_length=None)
    enriched = fills.join(token_df, on="token_id", how="left")
    if enriched.filter(pl.col("market_id").is_null()).height:
        raise ValueError(
            "selected trade rows contain tokens outside the frozen Gamma P0/P1 token set"
        )
    enriched = enriched.with_columns(
        (pl.col("condition_id").cast(pl.String) == pl.col("gamma_condition_id")).alias(
            "condition_id_matches_gamma"
        ),
    )
    # The canonical trade row's CID remains the preserved raw source value. Gamma's frozen CID
    # is kept separately and a mismatch is visible in both the row and the quality gate.
    if block_map.height:
        enriched = enriched.join(block_map, on="tx_hash", how="left")
    else:
        enriched = enriched.with_columns(pl.lit(None, dtype=pl.Int64).alias("block_number"))

    provenance = pl.when(pl.col("block_number").is_not_null()).then(pl.lit("CUSTODY_TX_HASH_JOIN"))
    if conflict_tx_hashes:
        provenance = (
            pl.when(pl.col("tx_hash").is_in(conflict_tx_hashes))
            .then(pl.lit("CONFLICTING_CUSTODY_BLOCK_NUMBER"))
            .otherwise(provenance)
        )
    provenance = (
        pl.when(
            pl.col("tx_hash").is_null() | (pl.col("tx_hash").cast(pl.String).str.len_chars() == 0)
        )
        .then(pl.lit("MISSING_TRANSACTION_HASH"))
        .otherwise(provenance)
    )
    if custody is None:
        provenance = (
            pl.when(pl.col("block_number").is_null())
            .then(pl.lit("MISSING_CUSTODY_OBJECT"))
            .otherwise(provenance)
        )
    else:
        provenance = (
            pl.when(
                pl.col("block_number").is_null()
                & ~pl.col("tx_hash").is_in(conflict_tx_hashes)
                & pl.col("tx_hash").is_not_null()
            )
            .then(pl.lit("MISSING_CUSTODY_BLOCK_MATCH"))
            .otherwise(provenance)
        )
    role_expr = active_role_expr(exchange_addresses)
    side = pl.col("side").cast(pl.String, strict=False).str.to_lowercase()
    enriched = enriched.with_columns(
        provenance.alias("block_number_provenance"),
        pl.when(side.is_in(["buy", "sell"]))
        .then(side.str.to_uppercase())
        .otherwise(None)
        .alias("economic_direction"),
        pl.when(side.is_in(["buy", "sell"]))
        .then(pl.lit("RECONSTRUCTED_FROM_SIGNED_ORDER_SIDE"))
        .otherwise(pl.lit("UNKNOWN_UNRECONSTRUCTABLE_SIDE"))
        .alias("economic_direction_status"),
        role_expr.alias("order_is_match_taker_order"),
        pl.when(role_expr.is_null())
        .then(pl.lit("UNKNOWN"))
        .when(role_expr)
        .then(pl.lit("TAKER"))
        .otherwise(pl.lit("MAKER"))
        .alias("order_role"),
        pl.col("maker_address").alias("participant_address"),
        pl.col("taker_address").alias("counterparty_address"),
        pl.concat_str(
            [
                pl.col("tx_hash").cast(pl.String),
                pl.col("log_index").cast(pl.String),
                pl.col("token_id"),
            ],
            separator=":",
        ).alias("fill_id"),
        pl.from_epoch(pl.col("timestamp"), time_unit="s")
        .dt.replace_time_zone("UTC")
        .dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        .alias("timestamp_utc"),
        pl.lit(trade["object_name"]).alias("source_trade_object"),
        pl.lit(trade.get("etag")).cast(pl.String).alias("source_trade_etag"),
        pl.lit(trade.get("source_sha256")).cast(pl.String).alias("source_trade_sha256"),
        pl.lit(trade.get("size")).cast(pl.Int64).alias("source_trade_bytes"),
        pl.lit(custody["object_name"] if custody else None).alias("source_custody_object"),
        pl.lit(custody.get("etag") if custody else None)
        .cast(pl.String)
        .alias("source_custody_etag"),
        pl.lit(custody.get("source_sha256") if custody else None)
        .cast(pl.String)
        .alias("source_custody_sha256"),
        pl.lit(custody.get("size") if custody else None)
        .cast(pl.Int64)
        .alias("source_custody_bytes"),
        pl.lit("POLYLEVIATHAN_TRADES_LAKE_CANONICAL").alias("source_version"),
        pl.lit("POLYLEVIATHAN_OCI_TRADES").alias("source"),
        pl.lit("TRADE_FILL").alias("evidence_grade"),
        pl.lit("maker_address owns the signed order; counterparty is taker_address").alias(
            "participant_address_semantics"
        ),
    )
    return enriched.select(
        [
            "fill_id",
            "timestamp",
            "timestamp_utc",
            "block_number",
            "log_index",
            "tx_hash",
            "condition_id",
            "gamma_condition_id",
            "condition_id_matches_gamma",
            "market_id",
            "event_id",
            "event_slug",
            "event_title",
            "market_slug",
            "market_question",
            "market_created_at",
            "market_start_date",
            "market_end_date",
            "market_closed",
            "market_active",
            "market_neg_risk",
            "token_id",
            "outcome_label",
            "side",
            "economic_direction",
            "economic_direction_status",
            "order_is_match_taker_order",
            "order_role",
            "participant_address",
            "counterparty_address",
            "maker_address",
            "taker_address",
            "price",
            "size_shares",
            "value_usd",
            "acquisition_class",
            "acquisition_tier",
            "contract_archetype",
            "mathematical_class",
            "relationship_class_set_json",
            "sig_market_ids_json",
            "sig_exchange_ids_json",
            "gamma_description_sha256",
            "graph_link_ids_json",
            "graph_relationship_classes_json",
            "block_number_provenance",
            "participant_address_semantics",
            "source",
            "source_version",
            "evidence_grade",
            "source_trade_object",
            "source_trade_etag",
            "source_trade_sha256",
            "source_trade_bytes",
            "source_custody_object",
            "source_custody_etag",
            "source_custody_sha256",
            "source_custody_bytes",
        ]
    ).sort(["token_id", "block_number", "log_index", "tx_hash", "fill_id"], nulls_last=True)


def parquet_parts(
    frame: Any,
    *,
    object_prefix: str,
    namespace: str,
    client: Any,
    lane: Path,
    checkpoint: dict[str, Any],
    checkpoint_path: Path,
    path_prefix: str,
    chunk_label: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Write/upload bounded parts. Each part is capped by estimated in-memory size."""
    output_records: list[dict[str, Any]] = []
    upload_records: list[dict[str, Any]] = []
    if frame.height == 0:
        return output_records, upload_records
    total_estimate = max(int(frame.estimated_size()), 1)
    row_limit = (
        frame.height
        if total_estimate <= MAX_ESTIMATED_CHUNK_BYTES
        else max(
            1,
            int(frame.height * MAX_ESTIMATED_CHUNK_BYTES / total_estimate * 0.90),
        )
    )
    start = 0
    part = 0
    while start < frame.height:
        current_limit = min(row_limit, frame.height - start)
        while True:
            chunk = frame.slice(start, current_limit)
            estimate = max(int(chunk.estimated_size()), 1)
            if estimate <= MAX_ESTIMATED_CHUNK_BYTES:
                break
            current_limit = max(1, int(current_limit * MAX_ESTIMATED_CHUNK_BYTES / estimate * 0.85))
        disk_gate(lane, f"before_output_chunk:{chunk_label}:part={part}", reserve_bytes=estimate)
        with tempfile.NamedTemporaryFile(
            prefix="data004-fill-", suffix=".parquet", dir="/tmp", delete=False
        ) as handle:
            temp_path = Path(handle.name)
        try:
            chunk.write_parquet(temp_path, compression="zstd", statistics=True)
            file_bytes = temp_path.stat().st_size
            if file_bytes > MAX_TEMP_FILE_BYTES:
                raise RuntimeError(
                    f"temporary Parquet exceeded 200 MB safety cap at {chunk_label}:part={part}: {file_bytes} bytes"
                )
            digest = sha256_file(temp_path)
            object_name = f"{object_prefix}{path_prefix}/part-{part:05d}-sha256-{digest}.parquet"
            existing = object_exists(client, namespace, object_name)
            if existing is not None:
                if existing.get("size") not in (None, file_bytes):
                    raise RuntimeError(
                        f"existing immutable object has a different size: {object_name}"
                    )
            else:
                with temp_path.open("rb") as body:
                    client.put_object(
                        namespace_name=namespace,
                        bucket_name=BUCKET,
                        object_name=object_name,
                        put_object_body=body,
                        content_type="application/vnd.apache.parquet",
                    )

            def optional_min_max(
                column: str, frame=chunk
            ) -> tuple[int | None, int | None]:
                if column not in frame.columns:
                    return None, None
                values = frame.get_column(column)
                if values.null_count() >= frame.height:
                    return None, None
                return int(values.min()), int(values.max())

            first_timestamp, last_timestamp = optional_min_max("timestamp")
            first_block_number, last_block_number = optional_min_max("block_number")
            record = {
                "path": object_name,
                "rows": chunk.height,
                "bytes": file_bytes,
                "sha256": digest,
                "first_timestamp": first_timestamp,
                "last_timestamp": last_timestamp,
                "first_block_number": first_block_number,
                "last_block_number": last_block_number,
                "token_count": chunk["token_id"].n_unique() if "token_id" in chunk.columns else 0,
                "condition_count": chunk["condition_id"].n_unique()
                if "condition_id" in chunk.columns
                else 0,
            }
            output_records.append(record)
            upload_record = {**record, "source_chunk": chunk_label, "object_prefix": object_prefix}
            upload_records.append(upload_record)
            previous = checkpoint["outputs"].get(object_name)
            if previous is not None and (
                previous.get("sha256") != digest or previous.get("rows") != chunk.height
            ):
                raise RuntimeError(
                    f"resume checkpoint differs from regenerated chunk {object_name}"
                )
            checkpoint["outputs"][object_name] = upload_record
            save_checkpoint(checkpoint_path, checkpoint)
            part += 1
            start += current_limit
        finally:
            temp_path.unlink(missing_ok=True)
    return output_records, upload_records


def validate_uploaded_corpus(
    *,
    pl: Any,
    files: list[dict[str, Any]],
    storage_options: dict[str, Any],
    universe: dict[str, Any],
    missing_trade_days: list[str],
    source_last_day: str,
) -> dict[str, Any]:
    fill_files = [row for row in files if row["kind"] == "fills"]
    markets = universe["markets"]
    tokens = universe["tokens"]
    expected_markets = {str(row["market_id"]): row for row in markets}
    expected_tokens = {str(row["token_id"]): row for row in tokens}
    if not fill_files:
        zero_market_rows = []
        last_source_date = date.fromisoformat(source_last_day)
        for market in markets:
            created = parse_date(market.get("gamma_created_at"))
            reason = (
                "new_market_after_latest_available_trade_day"
                if created and created.date() > last_source_date
                else "source_gap_during_market_lifetime"
                if created and any(day >= created.date().isoformat() for day in missing_trade_days)
                else "no_trading_observed_in_available_source"
            )
            zero_market_rows.append(
                {
                    "market_id": str(market["market_id"]),
                    "condition_id": str(market["condition_id"]),
                    "event_id": str(market.get("event_id") or ""),
                    "acquisition_class": market.get("acquisition_class"),
                    "acquisition_tier": market.get("priority"),
                    "created_at_utc": market.get("gamma_created_at"),
                    "end_date_utc": market.get("gamma_end_date"),
                    "fill_rows": 0,
                    "tokens_expected": 2,
                    "tokens_with_rows": 0,
                    "first_fill_utc": None,
                    "last_fill_utc": None,
                    "first_fill_minus_created_seconds": None,
                    "last_fill_minus_end_seconds": None,
                    "missing_block_rows": 0,
                    "condition_id_mismatch_rows": 0,
                    "zero_fill_reason": reason,
                }
            )
        event_metadata = {}
        for market in markets:
            event_id = str(market.get("event_id") or "")
            event_metadata[event_id] = {
                "event_id": event_id,
                "event_slug": str(market.get("event_slug") or ""),
                "event_title": str(market.get("event_title") or ""),
                "fill_rows": 0,
                "markets_with_rows": 0,
                "conditions_with_rows": 0,
                "tokens_with_rows": 0,
                "first_fill_utc": None,
                "last_fill_utc": None,
                "missing_block_rows": 0,
            }
        return {
            "raw_fill_rows": 0,
            "deduped_fill_rows": 0,
            "duplicate_key_groups": 0,
            "conflicting_duplicate_groups": 0,
            "missing_block_number_rows": 0,
            "missing_tx_hash_rows": 0,
            "missing_log_index_rows": 0,
            "missing_timestamp_rows": 0,
            "missing_size_rows": 0,
            "missing_value_usd_rows": 0,
            "unknown_order_role_rows": 0,
            "unknown_economic_direction_rows": 0,
            "block_number_provenance_mix": {},
            "ordering_failure_by_token": [],
            "price_failure_rows": 0,
            "condition_id_mismatch_rows": 0,
            "symmetry": {
                "groups_checked": 0,
                "one_sided_groups": 0,
                "mismatch_groups": 0,
                "max_abs_size_delta": 0.0,
            },
            "coverage_by_market": zero_market_rows,
            "zero_fill_markets": zero_market_rows,
            "coverage_by_token": [
                {
                    "token_id": str(token["token_id"]),
                    "market_id": str(token["market_id"]),
                    "condition_id": str(token["condition_id"]),
                    "outcome_label": str(token["outcome_label"]),
                    "fill_rows": 0,
                    "first_fill_utc": None,
                    "last_fill_utc": None,
                    "missing_block_rows": 0,
                    "zero_fill": True,
                }
                for token in tokens
            ],
            "coverage_by_event": list(event_metadata.values()),
            "coverage_by_date": [],
            "market_lifecycle_anomalies": [],
        }

    paths = [f"s3://{BUCKET}/{row['path']}" for row in fill_files]
    scan = pl.scan_parquet(paths, storage_options=storage_options, low_memory=True)
    raw_fill_rows = int(scan.select(pl.len()).collect(engine="streaming").item())
    dedup_groups = (
        scan.group_by(DEDUP_KEY)
        .agg(
            pl.len().alias("rows"), pl.struct(CONTENT_COLUMNS).n_unique().alias("content_versions")
        )
        .filter(pl.col("rows") > 1)
        .collect(engine="streaming")
    )
    conflicting = dedup_groups.filter(pl.col("content_versions") > 1)
    provenance_rows = (
        scan.group_by("block_number_provenance")
        .len()
        .sort("block_number_provenance")
        .collect(engine="streaming")
    )
    provenance_mix = {
        str(row["block_number_provenance"]): int(row["len"])
        for row in provenance_rows.iter_rows(named=True)
    }
    missing_block_rows = int(
        scan.filter(pl.col("block_number").is_null())
        .select(pl.len())
        .collect(engine="streaming")
        .item()
    )
    missing_tx_hash_rows = int(
        scan.filter(
            pl.col("tx_hash").is_null() | (pl.col("tx_hash").cast(pl.String).str.len_chars() == 0)
        )
        .select(pl.len())
        .collect(engine="streaming")
        .item()
    )
    missing_log_index_rows = int(
        scan.filter(pl.col("log_index").is_null())
        .select(pl.len())
        .collect(engine="streaming")
        .item()
    )
    missing_timestamp_rows = int(
        scan.filter(pl.col("timestamp").is_null())
        .select(pl.len())
        .collect(engine="streaming")
        .item()
    )
    missing_size_rows = int(
        scan.filter(pl.col("size_shares").is_null())
        .select(pl.len())
        .collect(engine="streaming")
        .item()
    )
    missing_value_rows = int(
        scan.filter(pl.col("value_usd").is_null())
        .select(pl.len())
        .collect(engine="streaming")
        .item()
    )
    unknown_order_role_rows = int(
        scan.filter(pl.col("order_is_match_taker_order").is_null())
        .select(pl.len())
        .collect(engine="streaming")
        .item()
    )
    unknown_direction_rows = int(
        scan.filter(pl.col("economic_direction").is_null())
        .select(pl.len())
        .collect(engine="streaming")
        .item()
    )
    price_failure_rows = int(
        scan.filter(pl.col("price").is_null() | (pl.col("price") < 0) | (pl.col("price") > 1))
        .select(pl.len())
        .collect(engine="streaming")
        .item()
    )
    condition_mismatch_rows = int(
        scan.filter(~pl.col("condition_id_matches_gamma").fill_null(False))
        .select(pl.len())
        .collect(engine="streaming")
        .item()
    )
    ordering = (
        scan.filter(pl.col("block_number").is_not_null() & pl.col("log_index").is_not_null())
        .sort(["token_id", "block_number", "log_index", "tx_hash"], nulls_last=True)
        .with_columns(
            pl.col("block_number").shift(1).over("token_id").alias("_prior_block"),
            pl.col("log_index").shift(1).over("token_id").alias("_prior_log"),
        )
        .filter(
            pl.col("_prior_block").is_not_null()
            & (
                (pl.col("block_number") < pl.col("_prior_block"))
                | (
                    (pl.col("block_number") == pl.col("_prior_block"))
                    & (pl.col("log_index") <= pl.col("_prior_log"))
                )
            )
        )
        .group_by("token_id")
        .len()
        .sort("token_id")
        .collect(engine="streaming")
    )
    ordering_failures = [
        {"token_id": str(row["token_id"]), "rows": int(row["len"])}
        for row in ordering.iter_rows(named=True)
    ]
    symmetry = (
        scan.filter(
            pl.col("tx_hash").is_not_null() & pl.col("order_is_match_taker_order").is_not_null()
        )
        .group_by(["tx_hash", "token_id", "timestamp", "price"])
        .agg(
            pl.col("size_shares")
            .filter(pl.col("order_is_match_taker_order"))
            .sum()
            .alias("taker_size"),
            pl.col("size_shares")
            .filter(~pl.col("order_is_match_taker_order"))
            .sum()
            .alias("maker_size"),
            pl.col("order_is_match_taker_order").sum().alias("taker_rows"),
            (~pl.col("order_is_match_taker_order")).sum().alias("maker_rows"),
        )
        .collect(engine="streaming")
    )
    paired = symmetry.filter((pl.col("taker_rows") > 0) & (pl.col("maker_rows") > 0))
    one_sided = symmetry.filter((pl.col("taker_rows") == 0) | (pl.col("maker_rows") == 0))
    mismatches = paired.filter((pl.col("taker_size") - pl.col("maker_size")).abs() > 0.00001)
    max_delta = (
        float(
            paired.select((pl.col("taker_size") - pl.col("maker_size")).abs().max()).item() or 0.0
        )
        if paired.height
        else 0.0
    )

    coverage_market_df = (
        scan.group_by(
            [
                "market_id",
                "condition_id",
                "gamma_condition_id",
                "event_id",
                "acquisition_class",
                "acquisition_tier",
            ]
        )
        .agg(
            pl.len().alias("fill_rows"),
            pl.col("token_id").n_unique().alias("tokens_with_rows"),
            pl.col("timestamp").min().alias("first_timestamp"),
            pl.col("timestamp").max().alias("last_timestamp"),
            pl.col("block_number").null_count().alias("missing_block_rows"),
            (~pl.col("condition_id_matches_gamma").fill_null(False))
            .sum()
            .alias("condition_id_mismatch_rows"),
        )
        .collect(engine="streaming")
    )
    market_stats = {str(row["market_id"]): row for row in coverage_market_df.iter_rows(named=True)}
    coverage_by_market: list[dict[str, Any]] = []
    zero_fill_markets: list[dict[str, Any]] = []
    last_source_date = date.fromisoformat(source_last_day)
    for market_id, market in sorted(expected_markets.items(), key=lambda pair: int(pair[0])):
        stats = market_stats.get(market_id)
        created = parse_date(market.get("gamma_created_at"))
        end = parse_date(
            (market.get("gamma_market") or {}).get("endDate") or market.get("end_date")
        )
        if stats:
            first = int(stats["first_timestamp"]) if stats["first_timestamp"] is not None else None
            last = int(stats["last_timestamp"]) if stats["last_timestamp"] is not None else None
            first_dt = datetime.fromtimestamp(first, UTC) if first is not None else None
            last_dt = datetime.fromtimestamp(last, UTC) if last is not None else None
            created_delta = (first_dt - created).total_seconds() if first_dt and created else None
            end_delta = (last_dt - end).total_seconds() if last_dt and end else None
            row = {
                "market_id": market_id,
                "condition_id": str(market["condition_id"]),
                "event_id": str(market.get("event_id") or ""),
                "acquisition_class": market.get("acquisition_class"),
                "acquisition_tier": market.get("priority"),
                "created_at_utc": market.get("gamma_created_at"),
                "end_date_utc": market.get("gamma_end_date"),
                "fill_rows": int(stats["fill_rows"]),
                "tokens_expected": 2,
                "tokens_with_rows": int(stats["tokens_with_rows"]),
                "first_fill_utc": utc_string(first),
                "last_fill_utc": utc_string(last),
                "first_fill_minus_created_seconds": created_delta,
                "last_fill_minus_end_seconds": end_delta,
                "missing_block_rows": int(stats["missing_block_rows"]),
                "condition_id_mismatch_rows": int(stats["condition_id_mismatch_rows"]),
                "zero_fill_reason": "",
            }
        else:
            created_day = created.date().isoformat() if created else ""
            if created and created.date() > last_source_date:
                reason = "new_market_after_latest_available_trade_day"
            else:
                gaps = [day for day in missing_trade_days if not created_day or day >= created_day]
                reason = (
                    "source_gap_during_market_lifetime"
                    if gaps
                    else "no_trading_observed_in_available_source"
                )
            row = {
                "market_id": market_id,
                "condition_id": str(market["condition_id"]),
                "event_id": str(market.get("event_id") or ""),
                "acquisition_class": market.get("acquisition_class"),
                "acquisition_tier": market.get("priority"),
                "created_at_utc": market.get("gamma_created_at"),
                "end_date_utc": market.get("gamma_end_date"),
                "fill_rows": 0,
                "tokens_expected": 2,
                "tokens_with_rows": 0,
                "first_fill_utc": None,
                "last_fill_utc": None,
                "first_fill_minus_created_seconds": None,
                "last_fill_minus_end_seconds": None,
                "missing_block_rows": 0,
                "condition_id_mismatch_rows": 0,
                "zero_fill_reason": reason,
            }
            zero_fill_markets.append(row)
        coverage_by_market.append(row)

    token_df = (
        scan.group_by(["market_id", "condition_id", "event_id", "token_id", "outcome_label"])
        .agg(
            pl.len().alias("fill_rows"),
            pl.col("timestamp").min().alias("first_timestamp"),
            pl.col("timestamp").max().alias("last_timestamp"),
            pl.col("block_number").null_count().alias("missing_block_rows"),
            pl.col("block_number_provenance").value_counts().alias("block_provenance_counts"),
        )
        .collect(engine="streaming")
    )
    token_stats = {str(row["token_id"]): row for row in token_df.iter_rows(named=True)}
    coverage_by_token = []
    for token_id, token in sorted(expected_tokens.items()):
        stats = token_stats.get(token_id)
        coverage_by_token.append(
            {
                "token_id": token_id,
                "market_id": str(token["market_id"]),
                "condition_id": str(token["condition_id"]),
                "outcome_label": str(token["outcome_label"]),
                "fill_rows": int(stats["fill_rows"]) if stats else 0,
                "first_fill_utc": utc_string(stats["first_timestamp"]) if stats else None,
                "last_fill_utc": utc_string(stats["last_timestamp"]) if stats else None,
                "missing_block_rows": int(stats["missing_block_rows"]) if stats else 0,
                "zero_fill": stats is None,
            }
        )
    event_df = (
        scan.group_by(["event_id", "event_slug", "event_title"])
        .agg(
            pl.len().alias("fill_rows"),
            pl.col("market_id").n_unique().alias("markets_with_rows"),
            pl.col("condition_id").n_unique().alias("conditions_with_rows"),
            pl.col("token_id").n_unique().alias("tokens_with_rows"),
            pl.col("timestamp").min().alias("first_timestamp"),
            pl.col("timestamp").max().alias("last_timestamp"),
            pl.col("block_number").null_count().alias("missing_block_rows"),
        )
        .sort("event_id")
        .collect(engine="streaming")
    )
    coverage_by_event_from_fills = [
        {
            **{key: row[key] for key in ("event_id", "event_slug", "event_title")},
            "fill_rows": int(row["fill_rows"]),
            "markets_with_rows": int(row["markets_with_rows"]),
            "conditions_with_rows": int(row["conditions_with_rows"]),
            "tokens_with_rows": int(row["tokens_with_rows"]),
            "first_fill_utc": utc_string(row["first_timestamp"]),
            "last_fill_utc": utc_string(row["last_timestamp"]),
            "missing_block_rows": int(row["missing_block_rows"]),
        }
        for row in event_df.iter_rows(named=True)
    ]
    event_by_id = {str(row["event_id"]): row for row in coverage_by_event_from_fills}
    expected_events: dict[str, dict[str, Any]] = {}
    for market in markets:
        event_id = str(market.get("event_id") or "")
        event = market.get("gamma_event") or {}
        expected_events[event_id] = {
            "event_id": event_id,
            "event_slug": str(event.get("slug") or market.get("event_slug") or ""),
            "event_title": str(event.get("title") or market.get("event_title") or ""),
        }
    coverage_by_event = []
    for event_id, identity in sorted(expected_events.items()):
        coverage_by_event.append(
            event_by_id.get(
                event_id,
                {
                    **identity,
                    "fill_rows": 0,
                    "markets_with_rows": 0,
                    "conditions_with_rows": 0,
                    "tokens_with_rows": 0,
                    "first_fill_utc": None,
                    "last_fill_utc": None,
                    "missing_block_rows": 0,
                },
            )
        )
    date_df = (
        scan.with_columns(
            pl.from_epoch(pl.col("timestamp"), time_unit="s")
            .dt.strftime("%Y-%m-%d")
            .alias("utc_date")
        )
        .group_by("utc_date")
        .agg(
            pl.len().alias("fill_rows"),
            pl.col("market_id").n_unique().alias("markets_with_rows"),
            pl.col("condition_id").n_unique().alias("conditions_with_rows"),
            pl.col("token_id").n_unique().alias("tokens_with_rows"),
            pl.col("event_id").n_unique().alias("events_with_rows"),
            pl.col("block_number").null_count().alias("missing_block_rows"),
        )
        .sort("utc_date")
        .collect(engine="streaming")
    )
    coverage_by_date = [
        {
            "date": str(row["utc_date"]),
            "fill_rows": int(row["fill_rows"]),
            "markets_with_rows": int(row["markets_with_rows"]),
            "conditions_with_rows": int(row["conditions_with_rows"]),
            "tokens_with_rows": int(row["tokens_with_rows"]),
            "events_with_rows": int(row["events_with_rows"]),
            "missing_block_rows": int(row["missing_block_rows"]),
        }
        for row in date_df.iter_rows(named=True)
    ]
    lifecycle_anomalies = [
        row
        for row in coverage_by_market
        if (
            row.get("first_fill_minus_created_seconds") is not None
            and row["first_fill_minus_created_seconds"] < -1
        )
        or (
            row.get("last_fill_minus_end_seconds") is not None
            and row["last_fill_minus_end_seconds"] > 0
        )
    ]
    return {
        "raw_fill_rows": raw_fill_rows,
        "deduped_fill_rows": raw_fill_rows,
        "duplicate_key_groups": dedup_groups.height,
        "conflicting_duplicate_groups": conflicting.height,
        "duplicate_groups": dedup_groups.to_dicts(),
        "missing_block_number_rows": missing_block_rows,
        "missing_tx_hash_rows": missing_tx_hash_rows,
        "missing_log_index_rows": missing_log_index_rows,
        "missing_timestamp_rows": missing_timestamp_rows,
        "missing_size_rows": missing_size_rows,
        "missing_value_usd_rows": missing_value_rows,
        "unknown_order_role_rows": unknown_order_role_rows,
        "unknown_economic_direction_rows": unknown_direction_rows,
        "block_number_provenance_mix": provenance_mix,
        "ordering_failure_by_token": ordering_failures,
        "price_failure_rows": price_failure_rows,
        "condition_id_mismatch_rows": condition_mismatch_rows,
        "symmetry": {
            "grouping_key": ["tx_hash", "token_id", "timestamp", "price"],
            "grouping_note": "Match-equivalent conservation bucket; the lake schema has no common participant-order match ID.",
            "groups_checked": paired.height,
            "one_sided_groups": one_sided.height,
            "mismatch_groups": mismatches.height,
            "max_abs_size_delta": max_delta,
            "mismatch_examples": mismatches.head(25).to_dicts(),
            "one_sided_examples": one_sided.head(25).to_dicts(),
        },
        "coverage_by_market": coverage_by_market,
        "zero_fill_markets": zero_fill_markets,
        "coverage_by_token": coverage_by_token,
        "coverage_by_event": coverage_by_event,
        "coverage_by_date": coverage_by_date,
        "market_lifecycle_anomalies": lifecycle_anomalies,
    }


def make_gate_summary(
    *,
    universe: dict[str, Any],
    validation: dict[str, Any],
    scan_start_day: str,
    source_last_day: str,
    missing_trade_days: list[str],
    day_coverage: list[dict[str, Any]],
    raw_fill_rows: int,
    deduped_fill_rows: int,
    duplicate_rows_removed: int,
    duplicate_key_groups: int,
    conflicting_duplicate_groups: int,
    metadata_alignment_passed: bool,
    market_graph_link_count: int,
) -> dict[str, Any]:
    market_count = len(universe["markets"])
    condition_count = len({str(row["condition_id"]) for row in universe["markets"]})
    token_count = len(universe["tokens"])
    p0_count = sum(row.get("acquisition_class") == "FILLS_P0" for row in universe["markets"])
    p1_count = sum(row.get("acquisition_class") == "FILLS_P1" for row in universe["markets"])
    exact_scope = (
        market_count == 298
        and condition_count == 298
        and token_count == 596
        and p0_count == 210
        and p1_count == 88
    )
    block_mix = validation["block_number_provenance_mix"]
    only_custody_join = set(block_mix).issubset({"CUSTODY_TX_HASH_JOIN"})
    block_complete = validation["missing_block_number_rows"] == 0 and only_custody_join
    dedup_pass = (
        validation["duplicate_key_groups"] == 0
        and validation["conflicting_duplicate_groups"] == 0
        and validation["missing_tx_hash_rows"] == 0
        and validation["missing_log_index_rows"] == 0
    )
    ordering_pass = (
        validation["missing_block_number_rows"] == 0
        and validation["missing_log_index_rows"] == 0
        and validation["missing_tx_hash_rows"] == 0
        and not validation["ordering_failure_by_token"]
    )
    price_pass = validation["price_failure_rows"] == 0
    identity_pass = validation["condition_id_mismatch_rows"] == 0
    symmetry = validation["symmetry"]
    symmetry_pass = symmetry["mismatch_groups"] == 0 and symmetry["one_sided_groups"] == 0
    full_source_days_pass = not missing_trade_days
    gates: dict[str, Any] = {
        "universe_scope": {
            "markets": market_count,
            "conditions": condition_count,
            "tokens": token_count,
            "p0_markets": sum(
                row.get("acquisition_class") == "FILLS_P0" for row in universe["markets"]
            ),
            "p1_markets": sum(
                row.get("acquisition_class") == "FILLS_P1" for row in universe["markets"]
            ),
            "pass": exact_scope,
        },
        "gamma_outcome_token_alignment": {
            "markets_checked": market_count,
            "tokens_checked": token_count,
            "pass": metadata_alignment_passed,
        },
        "graph_and_anchor_links": {
            "market_graph_links": market_graph_link_count,
            "selected_sig_exchange_ids": universe["counts"]["selected_sig_exchange_ids"],
            "pass": market_graph_link_count > 0
            and universe["counts"]["selected_sig_exchange_ids"] > 0,
        },
        "dedup": {
            "raw_fill_rows": raw_fill_rows,
            "deduped_fill_rows": deduped_fill_rows,
            "duplicate_rows_removed": duplicate_rows_removed,
            "source_duplicate_key_groups_before_dedup": duplicate_key_groups,
            "output_duplicate_key_groups_after_dedup": validation["duplicate_key_groups"],
            "conflicting_duplicate_groups": max(
                conflicting_duplicate_groups, validation["conflicting_duplicate_groups"]
            ),
            "pass": dedup_pass,
        },
        "fill_key_completeness": {
            "dedup_key": DEDUP_KEY,
            "missing_transaction_hash_rows": validation["missing_tx_hash_rows"],
            "missing_log_index_rows": validation["missing_log_index_rows"],
            "pass": validation["missing_tx_hash_rows"] == 0
            and validation["missing_log_index_rows"] == 0,
        },
        "block_number_provenance": {
            "provenance_mix": block_mix,
            "missing_block_number_rows": validation["missing_block_number_rows"],
            "imputed_block_number_rows": 0,
            "pass": block_complete,
        },
        "ordering": {
            "key": ["token_id", "block_number", "log_index"],
            "ordering_failure_by_token": validation["ordering_failure_by_token"],
            "unorderable_missing_block_rows": validation["missing_block_number_rows"],
            "missing_log_index_rows": validation["missing_log_index_rows"],
            "missing_transaction_hash_rows": validation["missing_tx_hash_rows"],
            "pass": ordering_pass,
        },
        "fill_core_missingness": {
            "missing_timestamp_rows": validation["missing_timestamp_rows"],
            "missing_size_shares_rows": validation["missing_size_rows"],
            "missing_value_usd_rows": validation["missing_value_usd_rows"],
            "unknown_order_role_rows": validation["unknown_order_role_rows"],
            "unknown_economic_direction_rows": validation["unknown_economic_direction_rows"],
            "pass": validation["missing_timestamp_rows"] == 0
            and validation["missing_size_rows"] == 0
            and validation["missing_value_usd_rows"] == 0,
            "unknown_semantics": "unknown role/direction remain explicit; they are never imputed",
        },
        "price_bounds": {
            "range": [0, 1],
            "invalid_or_missing_rows": validation["price_failure_rows"],
            "pass": price_pass,
        },
        "fill_condition_identity": {
            "token_to_gamma_condition_mismatch_rows": validation["condition_id_mismatch_rows"],
            "pass": identity_pass,
        },
        "maker_taker_size_symmetry": {**symmetry, "pass": symmetry_pass},
        "market_lifecycle_bounds": {
            "markets_checked": len(validation["coverage_by_market"]),
            "fills_before_gamma_created_at": sum(
                row.get("first_fill_minus_created_seconds") is not None
                and row["first_fill_minus_created_seconds"] < -1
                for row in validation["coverage_by_market"]
            ),
            "fills_after_gamma_end_date": sum(
                row.get("last_fill_minus_end_seconds") is not None
                and row["last_fill_minus_end_seconds"] > 0
                for row in validation["coverage_by_market"]
            ),
            "anomalies": validation["market_lifecycle_anomalies"],
            "pass": not validation["market_lifecycle_anomalies"],
        },
        "source_date_coverage": {
            "scan_start_day": scan_start_day,
            "latest_available_trade_day": source_last_day,
            "missing_trade_object_days_in_scan_range": missing_trade_days,
            "day_rows_scanned": len(day_coverage),
            "pass": full_source_days_pass,
        },
        "per_market_coverage": {
            "markets_with_fills": sum(
                row["fill_rows"] > 0 for row in validation["coverage_by_market"]
            ),
            "markets_zero_fill": len(validation["zero_fill_markets"]),
            "zero_fill_reason_counts": dict(
                Counter(row["zero_fill_reason"] for row in validation["zero_fill_markets"])
            ),
            "pass": True,
        },
    }
    failures: list[dict[str, Any]] = []
    for gate_name, gate in gates.items():
        if not gate.get("pass", False):
            failures.append(
                {
                    "gate": gate_name,
                    "details": {key: value for key, value in gate.items() if key != "pass"},
                }
            )
    zero_fill_source_gaps = [
        row
        for row in validation["zero_fill_markets"]
        if row.get("zero_fill_reason") == "source_gap_during_market_lifetime"
    ]
    if zero_fill_source_gaps:
        failures.append(
            {"gate": "zero_fill_markets_with_source_gaps", "markets": zero_fill_source_gaps}
        )
    if missing_trade_days:
        affected_market_ids = [
            row["market_id"]
            for row in validation["coverage_by_market"]
            if any(
                day >= str(row.get("created_at_utc") or "9999")[:10] for day in missing_trade_days
            )
        ]
        failures.append(
            {
                "gate": "missing_trade_source_days",
                "dates": missing_trade_days,
                "markets_exposed_to_possible_source_gaps": affected_market_ids,
            }
        )
    for day in day_coverage:
        if day.get("block_number_missing_rows", 0):
            failures.append(
                {
                    "gate": "block_number_missing_rows_by_day",
                    "date": day["date"],
                    "rows": day["block_number_missing_rows"],
                    "markets": day.get("markets_with_missing_blocks", []),
                    "reason": day.get("block_number_missing_reason"),
                }
            )
    status = "PASS" if not failures else "BLOCKED_QUALITY_GATE"
    return {
        "dataset_id": "DATA-004",
        "version": "v1",
        "status": status,
        "all_gates_pass": not failures,
        "gates": gates,
        "failures": failures,
    }


def write_repo_artifacts(
    *,
    repo_output: Path,
    lane: Path,
    manifest: dict[str, Any],
    quality: dict[str, Any],
    validation: dict[str, Any],
    sample_rows: list[dict[str, Any]],
) -> None:
    repo_output.mkdir(parents=True, exist_ok=True)
    write_json(repo_output / "data004_manifest.json", manifest)
    write_json(repo_output / "data004_quality.json", quality)
    baseline = lane / "data004_baseline_pairing.json"
    if baseline.exists():
        (repo_output / "data004_baseline_pairing.json").write_bytes(baseline.read_bytes())
    market_fields = [
        "market_id",
        "condition_id",
        "event_id",
        "acquisition_class",
        "acquisition_tier",
        "created_at_utc",
        "end_date_utc",
        "fill_rows",
        "tokens_expected",
        "tokens_with_rows",
        "first_fill_utc",
        "last_fill_utc",
        "first_fill_minus_created_seconds",
        "last_fill_minus_end_seconds",
        "missing_block_rows",
        "condition_id_mismatch_rows",
        "zero_fill_reason",
    ]
    atomic_csv(repo_output / "market_coverage.csv", validation["coverage_by_market"], market_fields)
    token_fields = [
        "token_id",
        "market_id",
        "condition_id",
        "outcome_label",
        "fill_rows",
        "first_fill_utc",
        "last_fill_utc",
        "missing_block_rows",
        "zero_fill",
    ]
    atomic_csv(repo_output / "token_coverage.csv", validation["coverage_by_token"], token_fields)
    event_fields = [
        "event_id",
        "event_slug",
        "event_title",
        "fill_rows",
        "markets_with_rows",
        "conditions_with_rows",
        "tokens_with_rows",
        "first_fill_utc",
        "last_fill_utc",
        "missing_block_rows",
    ]
    atomic_csv(repo_output / "event_coverage.csv", validation["coverage_by_event"], event_fields)
    date_fields = [
        "date",
        "fill_rows",
        "markets_with_rows",
        "conditions_with_rows",
        "tokens_with_rows",
        "events_with_rows",
        "missing_block_rows",
    ]
    atomic_csv(repo_output / "date_coverage.csv", validation["coverage_by_date"], date_fields)
    sample_fields = (
        list(sample_rows[0])
        if sample_rows
        else [
            "fill_id",
            "timestamp_utc",
            "block_number",
            "log_index",
            "tx_hash",
            "market_id",
            "condition_id",
            "event_id",
            "token_id",
            "outcome_label",
            "side",
            "economic_direction",
            "order_role",
            "price",
            "size_shares",
            "value_usd",
            "block_number_provenance",
        ]
    )
    atomic_csv(repo_output / "sample_fills.csv", sample_rows, sample_fields)
    zero_fields = market_fields
    atomic_csv(repo_output / "zero_fill_markets.csv", validation["zero_fill_markets"], zero_fields)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lane", type=Path, default=DEFAULT_LANE)
    parser.add_argument("--universe", type=Path, default=DEFAULT_LANE / "data004_universe.json")
    parser.add_argument(
        "--repo-output", type=Path, default=ROOT / "data/research/data004_ets_p0p1/v1"
    )
    parser.add_argument("--bucket", default=BUCKET)
    parser.add_argument("--object-prefix", default=DATA_PREFIX)
    args = parser.parse_args()
    if args.bucket != BUCKET:
        raise ValueError(f"writes are restricted to OCI bucket {BUCKET!r}")
    if args.object_prefix != DATA_PREFIX:
        raise ValueError(f"writes are restricted to new DATA-004 prefix {DATA_PREFIX!r}")
    if os.geteuid() == 0:
        raise RuntimeError("run as ubuntu; root must not read Sonar/.env")
    os.environ["POLARS_MAX_THREADS"] = "2"
    lane = args.lane.resolve()
    lane.mkdir(parents=True, exist_ok=True)
    repo_output = args.repo_output.resolve()
    universe_path = args.universe.resolve()
    universe = json.loads(universe_path.read_text(encoding="utf-8"))
    if universe["counts"] != {
        "markets": 298,
        "conditions": 298,
        "tokens": 596,
        "p0_markets": 210,
        "p1_markets": 88,
        "selected_sig_exchange_ids": universe["counts"]["selected_sig_exchange_ids"],
        "market_graph_links": universe["counts"]["market_graph_links"],
    }:
        expected = {
            "markets": 298,
            "conditions": 298,
            "tokens": 596,
            "p0_markets": 210,
            "p1_markets": 88,
        }
        for key, value in expected.items():
            if universe["counts"].get(key) != value:
                raise ValueError(
                    f"universe count {key} expected {value}, got {universe['counts'].get(key)}"
                )
    if len(universe["markets"]) != 298 or len(universe["tokens"]) != 596:
        raise ValueError(
            "DATA-004 exporter input must be exactly the prepared 298 markets / 596 tokens"
        )
    prepared_created = [parse_date(row.get("gamma_created_at")) for row in universe["markets"]]
    if not all(prepared_created):
        raise ValueError(
            "all scoped markets must have a Gamma createdAt timestamp for full-history scanning"
        )
    first_expected_day = (
        min(value for value in prepared_created if value is not None).date().isoformat()
    )
    universe_sha = sha256_file(universe_path)
    script_sha = sha256_file(Path(__file__).resolve())
    if args.object_prefix.startswith("research/r25_ets/"):
        raise ValueError("the prior R25 ETS PARTIAL prefix is forbidden")

    os.environ.setdefault("TMPDIR", "/tmp")
    from dotenv import load_dotenv

    load_dotenv(SONAR_ROOT / ".env", override=False)
    sys.path.insert(0, str(SONAR_ROOT))
    import config
    import oci
    import polars as pl
    from pnl_common import _oci_s3_storage_options

    # Polars' default streaming batches can be too large for custody objects with wide string
    # columns. A small fixed batch keeps per-row-group decoding below this lane's 6 GiB cgroup.
    pl.Config.set_streaming_chunk_size(STREAMING_CHUNK_ROWS)

    signer, cfg = config.get_oci_signer_and_config()
    client = (
        oci.object_storage.ObjectStorageClient(cfg, signer=signer)
        if signer
        else oci.object_storage.ObjectStorageClient(cfg)
    )
    namespace = client.get_namespace().data
    storage_options = _oci_s3_storage_options(namespace)
    initial_disk = disk_gate(lane, "before_export_start")
    final_marker = object_exists(client, namespace, FINAL_MANIFEST)
    if final_marker is not None:
        raise RuntimeError(
            f"DATA-004 v1 is already finalized; immutable prefix cannot be overwritten: {FINAL_MANIFEST}"
        )

    checkpoint_path = lane / "data004_checkpoint.json"
    if checkpoint_path.exists():
        checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
        if (
            checkpoint.get("universe_sha256") != universe_sha
            or checkpoint.get("object_prefix") != DATA_PREFIX
        ):
            raise RuntimeError(
                "checkpoint universe/prefix differs; refusing to resume inconsistent DATA-004 output"
            )
        if checkpoint.get("exporter_sha256") != script_sha:
            previous_script_sha = str(checkpoint.get("exporter_sha256") or "")
            if previous_script_sha not in RESUME_COMPATIBLE_EXPORTER_SHA256:
                raise RuntimeError(
                    "exporter changed incompatibly since checkpoint; use a new immutable dataset version"
                )
            history = checkpoint.setdefault("exporter_resume_history", [])
            history.append(
                {
                    "from_sha256": previous_script_sha,
                    "to_sha256": script_sha,
                    "change": "Reduced Polars streaming batch size, filtered custody directly to selected transaction hashes, trimmed freed per-day memory, and made Parquet chunk statistics tolerate metadata tables without fill-only columns; fill output schema and row semantics unchanged.",
                }
            )
            checkpoint["exporter_sha256"] = script_sha
            save_checkpoint(checkpoint_path, checkpoint)
    else:
        checkpoint = {
            "dataset_id": "DATA-004",
            "version": "v1",
            "object_prefix": DATA_PREFIX,
            "bucket": BUCKET,
            "universe_sha256": universe_sha,
            "exporter_sha256": script_sha,
            "outputs": {},
            "source_objects": {},
            "completed_source_days": [],
            "day_coverage": [],
            "sample_rows": [],
            "raw_fill_rows": 0,
            "deduped_fill_rows": 0,
            "duplicate_rows_removed": 0,
            "duplicate_key_groups": 0,
            "conflicting_duplicate_groups": 0,
            "custody_identity_failures_by_day": [],
            "condition_id_mismatch_rows": 0,
            "created_at_utc": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        }
        save_checkpoint(checkpoint_path, checkpoint)

    trade_objects = source_objects(client, namespace, TRADE_PREFIX)
    custody_objects = source_objects(client, namespace, CUSTODY_PREFIX)
    if not trade_objects:
        raise RuntimeError("no daily OCI trades objects found")
    all_trade_days = sorted(trade_objects)
    source_last_day = all_trade_days[-1]
    days = [day for day in all_trade_days if day >= first_expected_day]
    if not days:
        raise RuntimeError(
            f"no trades objects exist on/after earliest scoped market creation date {first_expected_day}"
        )
    scan_start_day = days[0]
    last_dt = date.fromisoformat(source_last_day)
    cursor = date.fromisoformat(first_expected_day)
    listed_days = set(trade_objects)
    missing_trade_days: list[str] = []
    while cursor <= last_dt:
        if cursor.isoformat() not in listed_days:
            missing_trade_days.append(cursor.isoformat())
        cursor += timedelta(days=1)

    completed = set(checkpoint.get("completed_source_days", []))
    for day in sorted(checkpoint.get("source_objects", {})):
        current = trade_objects.get(day)
        stored = checkpoint.get("source_objects", {}).get(day, {}).get("trade")
        if current is None or stored is None:
            raise RuntimeError(f"checkpoint refers to absent trade object {day}")
        refresh_object_metadata(client, namespace, current)
        if current.get("etag") != stored.get("etag") or current.get("size") != stored.get("size"):
            raise RuntimeError(f"source trade object changed while resuming {day}")
        current_custody = custody_objects.get(day)
        stored_custody = checkpoint.get("source_objects", {}).get(day, {}).get("custody")
        if (current_custody is None) != (stored_custody is None):
            raise RuntimeError(f"source custody object availability changed while resuming {day}")
        if current_custody is not None and stored_custody is not None:
            refresh_object_metadata(client, namespace, current_custody)
            if current_custody.get("etag") != stored_custody.get("etag") or current_custody.get(
                "size"
            ) != stored_custody.get("size"):
                raise RuntimeError(f"source custody object changed while resuming {day}")

    token_values = pl.Series(
        sorted(row["token_id"] for row in universe["tokens"]), dtype=pl.String
    ).implode()
    market_df, token_dimension_df, token_metadata = build_dimensions(universe)
    del market_df
    registry = json.loads((SONAR_ROOT / "infra_registry.json").read_text(encoding="utf-8"))
    exchange_addresses = sorted(
        {str(address).lower() for address in registry["exchange_addresses"]}
    )
    file_records: list[dict[str, Any]] = list(checkpoint.get("file_records", []))
    day_coverage_by_date = {row["date"]: row for row in checkpoint.get("day_coverage", [])}
    market_graph_link_count = int(universe["counts"]["market_graph_links"])

    for day in days:
        if day in completed:
            continue
        disk_gate(lane, f"before_source_day:{day}")
        trade = trade_objects[day]
        refresh_object_metadata(client, namespace, trade)
        custody = custody_objects.get(day)
        if custody is not None:
            refresh_object_metadata(client, namespace, custody)
        previous_source = checkpoint.get("source_objects", {}).get(day)
        if previous_source is not None:
            previous_trade = previous_source.get("trade") or {}
            previous_custody = previous_source.get("custody")
            if trade.get("etag") != previous_trade.get("etag") or trade.get(
                "size"
            ) != previous_trade.get("size"):
                raise RuntimeError(f"source trade object changed during partial-day resume {day}")
            if (custody is None) != (previous_custody is None):
                raise RuntimeError(
                    f"source custody object availability changed during partial-day resume {day}"
                )
            if (
                custody is not None
                and previous_custody is not None
                and (
                    custody.get("etag") != previous_custody.get("etag")
                    or custody.get("size") != previous_custody.get("size")
                )
            ):
                raise RuntimeError(
                    f"source custody object changed during partial-day resume {day}"
                )
        checkpoint["source_objects"][day] = {"trade": trade, "custody": custody}
        save_checkpoint(checkpoint_path, checkpoint)
        trade_uri = f"s3://{BUCKET}/{trade['object_name']}"
        source_schema = pl.scan_parquet(trade_uri, storage_options=storage_options).collect_schema()
        absent = sorted(set(TRADE_COLUMNS) - set(source_schema.names()))
        if absent:
            raise ValueError(
                f"source trade object {trade['object_name']} lacks required columns {absent}"
            )
        source_fills = (
            pl.scan_parquet(trade_uri, storage_options=storage_options, low_memory=True)
            .select(TRADE_COLUMNS)
            .filter(pl.col("token_id").is_in(token_values))
            .collect(engine="streaming")
        )
        raw_count = source_fills.height
        day_conflicts: list[dict[str, Any]] = []
        dropped = 0
        if source_fills.height:
            outside_cid = source_fills.join(
                token_dimension_df.select(
                    "token_id", pl.col("condition_id").alias("_gamma_condition_id")
                ),
                on="token_id",
                how="left",
            ).filter(pl.col("condition_id") != pl.col("_gamma_condition_id"))
            if outside_cid.height:
                checkpoint["condition_id_mismatch_rows"] += outside_cid.height

            valid_keys = source_fills.filter(
                pl.all_horizontal([pl.col(key).is_not_null() for key in DEDUP_KEY])
            )
            invalid_keys = source_fills.filter(
                ~pl.all_horizontal([pl.col(key).is_not_null() for key in DEDUP_KEY])
            )
            duplicate_stats = (
                valid_keys.group_by(DEDUP_KEY)
                .agg(
                    pl.len().alias("rows"),
                    pl.struct(CONTENT_COLUMNS).n_unique().alias("content_versions"),
                )
                .filter(pl.col("rows") > 1)
            )
            duplicate_key_groups = duplicate_stats.height
            conflict_df = duplicate_stats.filter(pl.col("content_versions") > 1)
            if conflict_df.height:
                day_conflicts = conflict_df.select(
                    DEDUP_KEY + ["rows", "content_versions"]
                ).to_dicts()
            safe_base = valid_keys.sort(DEDUP_KEY + CONTENT_COLUMNS, nulls_last=True).unique(
                subset=DEDUP_KEY,
                keep="first",
                maintain_order=True,
            )
            safe_conflicts = (
                valid_keys.join(conflict_df.select(DEDUP_KEY), on=DEDUP_KEY, how="semi")
                if conflict_df.height
                else valid_keys.head(0)
            )
            safe_unique = (
                safe_base.join(conflict_df.select(DEDUP_KEY), on=DEDUP_KEY, how="anti")
                if conflict_df.height
                else safe_base
            )
            deduped_valid = pl.concat([safe_unique, safe_conflicts], how="vertical_relaxed")
            deduped_fills = pl.concat([deduped_valid, invalid_keys], how="vertical_relaxed")
            dropped = valid_keys.height - deduped_valid.height
            checkpoint["duplicate_rows_removed"] += dropped
            checkpoint["duplicate_key_groups"] += duplicate_key_groups
            checkpoint["conflicting_duplicate_groups"] += conflict_df.height
            if day_conflicts:
                checkpoint.setdefault("dedup_conflicts", []).extend(
                    [{"date": day, **row} for row in day_conflicts]
                )

            txs = deduped_fills.select(pl.col("tx_hash").drop_nulls().unique().alias("tx_hash"))
            conflict_tx_hashes: list[str] = []
            block_map = pl.DataFrame(schema={"tx_hash": pl.String, "block_number": pl.Int64})
            if custody is not None and txs.height:
                custody_uri = f"s3://{BUCKET}/{custody['object_name']}"
                custody_schema = pl.scan_parquet(
                    custody_uri, storage_options=storage_options
                ).collect_schema()
                if not {"tx_hash", "block_number"}.issubset(custody_schema.names()):
                    checkpoint["custody_identity_failures_by_day"].append(
                        {
                            "date": day,
                            "reason": "custody_source_missing_tx_hash_or_block_number_columns",
                            "rows": deduped_fills.height,
                        }
                    )
                else:
                    selected_tx_hashes = txs["tx_hash"].drop_nulls().unique().to_list()
                    custody_matches = (
                        pl.scan_parquet(
                            custody_uri, storage_options=storage_options, low_memory=True
                        )
                        .select("tx_hash", "block_number")
                        .filter(pl.col("tx_hash").is_in(selected_tx_hashes))
                        .collect(engine="streaming")
                    )
                    block_stats = custody_matches.group_by("tx_hash").agg(
                        pl.col("block_number").drop_nulls().n_unique().alias("block_count"),
                        pl.col("block_number").drop_nulls().first().alias("block_number"),
                    )
                    conflict_df2 = block_stats.filter(pl.col("block_count") > 1)
                    conflict_tx_hashes = (
                        sorted(set(conflict_df2["tx_hash"].to_list()))
                        if conflict_df2.height
                        else []
                    )
                    if conflict_df2.height:
                        checkpoint["custody_identity_failures_by_day"].append(
                            {
                                "date": day,
                                "reason": "conflicting_block_numbers_for_tx_hash",
                                "transaction_hashes": conflict_tx_hashes,
                            }
                        )
                    block_map = block_stats.filter(pl.col("block_count") == 1).select(
                        "tx_hash", "block_number"
                    )
            enriched = transform_fills(
                deduped_fills,
                token_metadata,
                trade=trade,
                custody=custody,
                block_map=block_map,
                conflict_tx_hashes=conflict_tx_hashes,
                exchange_addresses=exchange_addresses,
            )
            mismatch_rows = int((~enriched["condition_id_matches_gamma"].fill_null(False)).sum())
            block_missing = int(enriched["block_number"].null_count())
            missing_markets = sorted(
                set(enriched.filter(pl.col("block_number").is_null())["market_id"].to_list()),
                key=int,
            )
            provenance_reasons = Counter(
                enriched.filter(pl.col("block_number").is_null())[
                    "block_number_provenance"
                ].to_list()
            )
            chunk_files, _ = parquet_parts(
                enriched,
                object_prefix=DATA_PREFIX,
                namespace=namespace,
                client=client,
                lane=lane,
                checkpoint=checkpoint,
                checkpoint_path=checkpoint_path,
                path_prefix=f"fills/date={day}",
                chunk_label=f"fills/date={day}",
            )
            file_records.extend({**record, "kind": "fills", "date": day} for record in chunk_files)
            if not checkpoint["sample_rows"]:
                sample = enriched.head(25).to_dicts()
                checkpoint["sample_rows"] = sample
            checkpoint["raw_fill_rows"] += raw_count
            checkpoint["deduped_fill_rows"] += enriched.height
            day_coverage = {
                "date": day,
                "source_trade_object": trade,
                "source_custody_object": custody,
                "raw_matched_fill_rows": raw_count,
                "deduped_fill_rows": enriched.height,
                "duplicate_rows_removed": dropped,
                "duplicate_key_groups": duplicate_key_groups,
                "conflicting_duplicate_groups": len(day_conflicts),
                "condition_id_mismatch_rows": mismatch_rows,
                "block_number_missing_rows": block_missing,
                "markets_with_missing_blocks": missing_markets,
                "block_number_missing_reason": dict(provenance_reasons),
                "files": [row["path"] for row in chunk_files],
                "status": "EXPORTED_WITH_PROVENANCE_GAPS" if block_missing else "EXPORTED",
            }
        else:
            checkpoint["raw_fill_rows"] += 0
            day_coverage = {
                "date": day,
                "source_trade_object": trade,
                "source_custody_object": custody,
                "raw_matched_fill_rows": 0,
                "deduped_fill_rows": 0,
                "duplicate_rows_removed": 0,
                "duplicate_key_groups": 0,
                "conflicting_duplicate_groups": 0,
                "condition_id_mismatch_rows": 0,
                "block_number_missing_rows": 0,
                "markets_with_missing_blocks": [],
                "block_number_missing_reason": {},
                "files": [],
                "status": "NO_MATCHED_FILLS",
            }
        day_coverage_by_date[day] = day_coverage
        checkpoint["day_coverage"] = [
            day_coverage_by_date[key] for key in sorted(day_coverage_by_date)
        ]
        checkpoint["file_records"] = file_records
        checkpoint["completed_source_days"] = sorted(completed | {day})
        completed.add(day)
        save_checkpoint(checkpoint_path, checkpoint)
        print(
            f"date={day} raw={day_coverage['raw_matched_fill_rows']} deduped={day_coverage['deduped_fill_rows']} "
            f"files={len(day_coverage['files'])} missing_blocks={day_coverage['block_number_missing_rows']} "
            f"raw_total={checkpoint['raw_fill_rows']}",
            flush=True,
        )
        trim_process_memory()

    # Persist compact market and token dimensions in the same immutable OCI version.
    market_dimension, token_dimension, _ = build_dimensions(universe)
    meta_files = []
    for kind, frame in (
        ("market_universe", market_dimension),
        ("token_alignment", token_dimension),
    ):
        frame = (
            frame.sort("market_id")
            if kind == "market_universe"
            else frame.sort(["market_id", "token_id"])
        )
        record, _ = parquet_parts(
            frame,
            object_prefix=DATA_PREFIX,
            namespace=namespace,
            client=client,
            lane=lane,
            checkpoint=checkpoint,
            checkpoint_path=checkpoint_path,
            path_prefix=f"metadata/{kind}",
            chunk_label=f"metadata/{kind}",
        )
        meta_files.extend({**item, "kind": "metadata", "dataset": kind} for item in record)
    file_records = [
        row for row in checkpoint.get("file_records", []) if row.get("kind") == "fills"
    ] + meta_files
    # Rebuild the canonical file list from checkpoints, so a resumed export has no duplicate entries.
    fill_by_path = {
        str(row["path"]): {
            **row,
            "kind": "fills",
            "date": row.get("date") or row.get("source_chunk", "").split("=")[-1],
        }
        for row in checkpoint.get("file_records", [])
        if row.get("kind") == "fills"
    }
    # Current-run day records may not exist in older checkpoints written before file_records was saved.
    for day in checkpoint.get("day_coverage", []):
        for path in day.get("files", []):
            record = checkpoint["outputs"].get(path)
            if record:
                fill_by_path[path] = {**record, "kind": "fills", "date": day["date"]}
    meta_by_path = {str(row["path"]): row for row in meta_files}
    file_records = sorted(
        [*fill_by_path.values(), *meta_by_path.values()], key=lambda row: row["path"]
    )
    checkpoint["file_records"] = file_records
    save_checkpoint(checkpoint_path, checkpoint)

    validation = validate_uploaded_corpus(
        pl=pl,
        files=file_records,
        storage_options=storage_options,
        universe=universe,
        missing_trade_days=missing_trade_days,
        source_last_day=source_last_day,
    )
    raw_fill_rows = int(checkpoint["raw_fill_rows"])
    deduped_fill_rows = int(checkpoint["deduped_fill_rows"])
    duplicate_rows_removed = int(checkpoint["duplicate_rows_removed"])
    duplicate_key_groups = int(checkpoint["duplicate_key_groups"])
    conflicting_duplicate_groups = int(checkpoint["conflicting_duplicate_groups"])
    validation["raw_source_fill_rows"] = raw_fill_rows
    validation["raw_fill_rows"] = raw_fill_rows
    validation["deduped_fill_rows_written"] = deduped_fill_rows
    validation["deduped_fill_rows"] = deduped_fill_rows
    validation["duplicate_rows_removed_within_source_days"] = duplicate_rows_removed
    validation["duplicate_key_groups_within_source_days"] = duplicate_key_groups
    validation["conflicting_duplicate_groups_within_source_days"] = conflicting_duplicate_groups
    validation["condition_id_mismatch_rows_during_source_scan"] = int(
        checkpoint["condition_id_mismatch_rows"]
    )
    validation["custody_identity_failures_by_day"] = checkpoint["custody_identity_failures_by_day"]

    # Input preparation already validated all 298 Gamma market IDs, CIDs, and token/outcome pairs.
    metadata_alignment_passed = (
        universe["counts"]["markets"] == 298
        and universe["counts"]["conditions"] == 298
        and universe["counts"]["tokens"] == 596
        and universe["gamma"]["market_snapshot_rows"] == 298
    )
    day_coverage = [day_coverage_by_date[key] for key in sorted(day_coverage_by_date)]
    gate_summary = make_gate_summary(
        universe=universe,
        validation=validation,
        scan_start_day=scan_start_day,
        source_last_day=source_last_day,
        missing_trade_days=missing_trade_days,
        day_coverage=day_coverage,
        raw_fill_rows=raw_fill_rows,
        deduped_fill_rows=deduped_fill_rows,
        duplicate_rows_removed=duplicate_rows_removed,
        duplicate_key_groups=duplicate_key_groups,
        conflicting_duplicate_groups=conflicting_duplicate_groups,
        metadata_alignment_passed=metadata_alignment_passed,
        market_graph_link_count=market_graph_link_count,
    )
    latest_fill_timestamp = max(
        (
            row["last_timestamp"]
            for row in file_records
            if row["kind"] == "fills" and row.get("last_timestamp") is not None
        ),
        default=None,
    )
    earliest_fill_timestamp = min(
        (
            row["first_timestamp"]
            for row in file_records
            if row["kind"] == "fills" and row.get("first_timestamp") is not None
        ),
        default=None,
    )
    file_payload_bytes = sum(int(row["bytes"]) for row in file_records)
    disk_lines = [
        json.loads(line)
        for line in (lane / "disk_check_log.jsonl").read_text(encoding="utf-8").splitlines()
        if line
    ]
    disk_summary = {
        "checks": len(disk_lines),
        "initial_free_bytes": initial_disk,
        "minimum_free_bytes_observed": min(
            (row["available_bytes"] for row in disk_lines), default=initial_disk
        ),
        "max_temp_file_bytes": MAX_TEMP_FILE_BYTES,
        "disk_check_log": str(lane / "disk_check_log.jsonl"),
    }
    now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    manifest = {
        "schema_version": 1,
        "dataset_id": "DATA-004",
        "version": "v1",
        "status": gate_summary["status"],
        "immutable_after_manifest_publication": True,
        "created_at_utc": now,
        "repository_source_commit": checkpoint.get("repository_source_commit")
        or subprocess.check_output(
            ["git", "-C", str(ROOT), "rev-parse", "HEAD"],
            text=True,
        ).strip(),
        "exporter_sha256": script_sha,
        "exporter_resume_history": checkpoint.get("exporter_resume_history", []),
        "runtime_controls": {
            "polars_max_threads": int(os.environ.get("POLARS_MAX_THREADS", "2")),
            "polars_streaming_chunk_rows": STREAMING_CHUNK_ROWS,
        },
        "universe_input_sha256": universe_sha,
        "bucket": BUCKET,
        "object_prefix": DATA_PREFIX,
        "source": {
            "trade_lake": "Polyleviathan canonical daily OCI trades Parquet",
            "trade_bucket": BUCKET,
            "trade_prefix": TRADE_PREFIX,
            "custody_bucket": BUCKET,
            "custody_prefix": CUSTODY_PREFIX,
            "latest_available_trade_object_day": source_last_day,
            "first_available_trade_object_day": all_trade_days[0],
            "listed_trade_object_count": len(all_trade_days),
            "latest_available_custody_object_day": max(custody_objects)
            if custody_objects
            else None,
            "first_available_custody_object_day": min(custody_objects) if custody_objects else None,
            "listed_custody_object_count": len(custody_objects),
            "scan_start_day": scan_start_day,
            "scan_last_day": source_last_day,
            "scan_days_with_trade_objects": len(days),
            "missing_trade_object_days": missing_trade_days,
            "source_trade_objects_scanned": [
                day_coverage_by_date[day]["source_trade_object"]
                for day in sorted(day_coverage_by_date)
            ],
            "source_day_coverage": [
                day_coverage_by_date[day] for day in sorted(day_coverage_by_date)
            ],
            "source_custody_objects_used": [
                day_coverage_by_date[day]["source_custody_object"]
                for day in sorted(day_coverage_by_date)
                if day_coverage_by_date[day].get("source_custody_object") is not None
            ],
            "dates_missing_custody_object_in_trade_scan": [
                day
                for day in sorted(day_coverage_by_date)
                if day_coverage_by_date[day].get("source_custody_object") is None
            ],
            "extraction_cutoff_utc": now,
        },
        "universe": {
            "acquisition_list_path": universe["source_files"]["acquisition"]["path"],
            "acquisition_list_sha256": universe["source_files"]["acquisition"]["sha256"],
            "market_graph_path": universe["source_files"]["market_graph"]["path"],
            "market_graph_sha256": universe["source_files"]["market_graph"]["sha256"],
            "sig_anchor_graph_path": universe["source_files"]["sig_anchor_graph"]["path"],
            "sig_anchor_graph_sha256": universe["source_files"]["sig_anchor_graph"]["sha256"],
            "counts": universe["counts"],
            "markets": [
                {
                    "market_id": str(row["market_id"]),
                    "condition_id": str(row["condition_id"]),
                    "event_id": str(row.get("event_id") or ""),
                    "event_slug": str(row.get("event_slug") or ""),
                    "event_title": str(row.get("event_title") or ""),
                    "question": str(
                        (row.get("gamma_market") or {}).get("question") or row.get("question") or ""
                    ),
                    "created_at": row.get("gamma_created_at"),
                    "start_date": row.get("gamma_start_date"),
                    "end_date": row.get("gamma_end_date"),
                    "outcome_token_alignment": row.get("gamma_outcome_token_alignment"),
                    "acquisition_class": row.get("acquisition_class"),
                    "tier": row.get("priority"),
                    "relationship_class_set": row.get("relationship_class_set_json"),
                    "contract_archetype": row.get("contract_archetype"),
                    "mathematical_class": row.get("mathematical_class"),
                    "expected_empirical_use": row.get("expected_empirical_use"),
                    "sig_market_ids": row.get("sig_market_ids_json"),
                    "sig_exchange_ids": row.get("sig_exchange_ids_json"),
                    "graph_link_ids": sorted(
                        {edge.get("relationship_id", "") for edge in (row.get("graph_links") or [])}
                    ),
                    "graph_relationship_classes": sorted(
                        {
                            edge.get("relationship_class", "")
                            for edge in (row.get("graph_links") or [])
                        }
                    ),
                }
                for row in sorted(universe["markets"], key=lambda item: int(item["market_id"]))
            ],
            "gamma_market_snapshot": {
                "api_base": universe["gamma"]["api_base"],
                "cache_path": universe["gamma"]["market_snapshot_path"],
                "cache_sha256": universe["gamma"]["market_snapshot_sha256"],
                "markets_cached": universe["gamma"]["market_snapshot_rows"],
                "event_cache_path": universe["gamma"]["event_snapshot_path"],
                "event_cache_sha256": universe["gamma"]["event_snapshot_sha256"],
                "events_cached": universe["gamma"]["event_snapshot_rows"],
                "event_fetch_errors": universe["gamma"]["event_fetch_errors"],
            },
        },
        "baseline_pairing_manifest": {
            "path": "data/research/data004_ets_p0p1/v1/data004_baseline_pairing.json",
            "sha256": sha256_file(lane / "data004_baseline_pairing.json"),
            "baseline_dataset": "DATA-003",
            "pairing_rule": "Join by accepted SIG exchange IDs; preserve crosswalk direction and each market graph's relation/equation; DATA-003 source histories were not reacquired.",
        },
        "counts": {
            "markets": len(universe["markets"]),
            "conditions": len({str(row["condition_id"]) for row in universe["markets"]}),
            "tokens": len(universe["tokens"]),
            "raw_source_fill_rows": raw_fill_rows,
            "deduped_fill_rows": deduped_fill_rows,
            "duplicate_rows_removed": duplicate_rows_removed,
            "source_duplicate_key_groups_before_dedup": duplicate_key_groups,
            "duplicate_key_groups_after_dedup": validation["duplicate_key_groups"],
            "conflicting_duplicate_groups": max(
                conflicting_duplicate_groups, validation["conflicting_duplicate_groups"]
            ),
            "files": len(file_records),
            "fill_files": sum(row["kind"] == "fills" for row in file_records),
            "parquet_bytes": file_payload_bytes,
            "earliest_fill_utc": utc_string(earliest_fill_timestamp),
            "latest_fill_utc": utc_string(latest_fill_timestamp),
        },
        "coverage": {
            "by_market": validation["coverage_by_market"],
            "by_token": validation["coverage_by_token"],
            "by_event": validation["coverage_by_event"],
            "by_date": validation["coverage_by_date"],
            "zero_fill_markets": validation["zero_fill_markets"],
        },
        "block_number_join": {
            "source_prefix": CUSTODY_PREFIX,
            "join_key": "tx_hash",
            "source_column": "block_number",
            "validation": "one unique non-null block_number per transaction hash; retain source rows with null block_number and explicit provenance on absent/conflicting joins",
            "ordering": ["block_number", "log_index", "token_id"],
            "provenance_mix": validation["block_number_provenance_mix"],
            "missing_block_number_rows": validation["missing_block_number_rows"],
            "imputed_block_number_rows": 0,
            "custody_identity_failures_by_day": validation["custody_identity_failures_by_day"],
        },
        "timestamp_semantics": "UTC block timestamp from the upstream trades lake at one-second resolution; not a receive-time claim",
        "side_and_role_semantics": {
            "side": "source signed-order side retained verbatim",
            "economic_direction": "BUY/SELL only when reconstructed from source signed-order side; otherwise UNKNOWN",
            "participant": "maker_address owns the signed order per DATA-002 dictionary",
            "order_role": "TAKER only when taker_address is a registered exchange contract; passive rows are MAKER when address fields are present",
            "exchange_registry": "Sonar/infra_registry.json exchange_addresses",
        },
        "files": file_records,
        "validation": validation,
        "gates": gate_summary["gates"],
        "failures": gate_summary["failures"],
        "disk_safety": disk_summary,
        "schema": [
            "fill_id",
            "timestamp",
            "timestamp_utc",
            "block_number",
            "log_index",
            "tx_hash",
            "condition_id",
            "gamma_condition_id",
            "condition_id_matches_gamma",
            "market_id",
            "event_id",
            "event_slug",
            "event_title",
            "market_slug",
            "market_question",
            "market_created_at",
            "market_start_date",
            "market_end_date",
            "market_closed",
            "market_active",
            "market_neg_risk",
            "token_id",
            "outcome_label",
            "side",
            "economic_direction",
            "economic_direction_status",
            "order_is_match_taker_order",
            "order_role",
            "participant_address",
            "counterparty_address",
            "maker_address",
            "taker_address",
            "price",
            "size_shares",
            "value_usd",
            "acquisition_class",
            "acquisition_tier",
            "contract_archetype",
            "mathematical_class",
            "relationship_class_set_json",
            "sig_market_ids_json",
            "sig_exchange_ids_json",
            "gamma_description_sha256",
            "graph_link_ids_json",
            "graph_relationship_classes_json",
            "block_number_provenance",
            "participant_address_semantics",
            "source",
            "source_version",
            "evidence_grade",
            "source_trade_object",
            "source_trade_etag",
            "source_trade_sha256",
            "source_trade_bytes",
            "source_custody_object",
            "source_custody_etag",
            "source_custody_sha256",
            "source_custody_bytes",
        ],
    }
    manifest_payload = json_bytes(manifest)
    manifest_sha = hashlib.sha256(manifest_payload).hexdigest()
    # The manifest is the finalization marker. Publish only after all gates and summaries are complete.
    if object_exists(client, namespace, FINAL_MANIFEST) is not None:
        raise RuntimeError(
            f"manifest path is already occupied, refusing to finalize over it: {FINAL_MANIFEST}"
        )
    write_json(lane / "data004_manifest.json", manifest)
    write_json(lane / "data004_quality.json", gate_summary)
    write_json(
        lane / "data004_export_summary.json",
        {
            "status": manifest["status"],
            "manifest_object": FINAL_MANIFEST,
            "manifest_sha256": manifest_sha,
            "markets": manifest["counts"]["markets"],
            "conditions": manifest["counts"]["conditions"],
            "tokens": manifest["counts"]["tokens"],
            "raw_fill_rows": raw_fill_rows,
            "deduped_fill_rows": deduped_fill_rows,
            "files": len(file_records),
            "parquet_bytes": file_payload_bytes,
            "source_trade_latest_available_day": source_last_day,
            "selected_fill_latest_timestamp": utc_string(latest_fill_timestamp),
        },
    )
    write_repo_artifacts(
        repo_output=repo_output,
        lane=lane,
        manifest=manifest,
        quality=gate_summary,
        validation=validation,
        sample_rows=checkpoint.get("sample_rows", []),
    )
    client.put_object(
        namespace_name=namespace,
        bucket_name=BUCKET,
        object_name=FINAL_MANIFEST,
        put_object_body=__import__("io").BytesIO(manifest_payload),
        content_type="application/json",
    )
    print(
        json.dumps(
            {
                "status": manifest["status"],
                "manifest_object": FINAL_MANIFEST,
                "manifest_sha256": manifest_sha,
                "markets": manifest["counts"]["markets"],
                "conditions": manifest["counts"]["conditions"],
                "tokens": manifest["counts"]["tokens"],
                "raw_fill_rows": raw_fill_rows,
                "deduped_fill_rows": deduped_fill_rows,
                "parquet_bytes": file_payload_bytes,
                "source_trade_latest_available_day": source_last_day,
                "selected_fill_latest_timestamp": utc_string(latest_fill_timestamp),
                "gate_failures": len(gate_summary["failures"]),
                "gate_failure_names": [row["gate"] for row in gate_summary["failures"]],
            },
            sort_keys=True,
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
