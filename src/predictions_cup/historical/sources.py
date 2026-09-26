"""DATA-001 source inventory: what each input physically is, verified from its bytes."""

from __future__ import annotations

import json
import urllib.request
from pathlib import Path
from typing import Any

import pyarrow.compute as pc
import pyarrow.parquet as pq

from predictions_cup.historical.corpus import _file_record, _hours
from predictions_cup.historical.regimes import (
    PMXT_ARCHIVE_BASE_URL,
    REGIMES,
    pmxt_version_for_hour,
)

_FOOTER_TAIL = 8


def pmxt_archive_url(hour_key: str, version: str) -> str:
    era = "v2" if version == "PMXT_V2" else "v1"
    return f"{PMXT_ARCHIVE_BASE_URL}/{era}/polymarket_orderbook_{hour_key}.parquet"


def fetch_remote_footer(url: str, *, timeout: float = 60) -> dict[str, Any]:
    """Row count / row groups / size / ETag of a remote Parquet file via HTTP range reads."""
    head = urllib.request.Request(url, method="HEAD")
    with urllib.request.urlopen(head, timeout=timeout) as response:
        size = int(response.headers["Content-Length"])
        etag = response.headers.get("ETag")
        modified = response.headers.get("Last-Modified")
    tail = _range(url, size - _FOOTER_TAIL, size - 1, timeout)
    if tail[4:] != b"PAR1":
        raise ValueError(f"{url}: not a Parquet file")
    footer_len = int.from_bytes(tail[:4], "little")
    footer = _range(url, size - _FOOTER_TAIL - footer_len, size - 1, timeout)
    metadata = pq.read_metadata(pq.BufferReader(_pad_footer(footer)))
    return {
        "url": url,
        "bytes": size,
        "etag": etag,
        "last_modified": modified,
        "rows": metadata.num_rows,
        "row_groups": metadata.num_row_groups,
        "columns": [metadata.schema.column(i).name for i in range(metadata.num_columns)],
    }


def _pad_footer(footer: bytes) -> bytes:
    # pq.read_metadata needs a buffer shaped like a whole file: magic + footer.
    return b"PAR1" + footer


def _range(url: str, start: int, end: int, timeout: float) -> bytes:
    request = urllib.request.Request(url, headers={"Range": f"bytes={start}-{end}"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        data: bytes = response.read()
    return data


def build_source_manifest(
    *,
    orderbooks_root: Path,
    fills_root: Path,
    remote_footers: bool,
) -> dict[str, Any]:
    archive_hours: list[dict[str, Any]] = []
    for family in sorted({r.family for r in REGIMES}):
        hours = sorted(
            {h for r in REGIMES if r.family == family for h in _hours(r.window_start, r.window_end)}
        )
        for hour in hours:
            key = f"{hour:%Y-%m-%dT%H}"
            version = pmxt_version_for_hour(hour)
            record: dict[str, Any] = {"family": family, "hour": key, "source_version": version}
            extract = (
                orderbooks_root / family / f"date={hour:%Y-%m-%d}" / f"hour={hour:%H}"
                / "events.parquet"
            )
            if extract.is_file():
                record["extract"] = _file_record(
                    extract, extract.relative_to(orderbooks_root).as_posix(), kind="pmxt_extract"
                )
            else:
                record["extract"] = None
            archive_hours.append(record)

    footers: dict[tuple[str, str], dict[str, Any]] = {}
    if remote_footers:
        for record in archive_hours:
            footer_key = (record["hour"], record["source_version"])
            if footer_key not in footers:
                footers[footer_key] = fetch_remote_footer(pmxt_archive_url(*footer_key))
            record["archive"] = footers[footer_key]

    fills: list[dict[str, Any]] = []
    for path in sorted(fills_root.glob("*.parquet")):
        record = _file_record(path, path.name, kind="polyleviathan")
        schema = pq.read_schema(path)
        record["schema"] = {f.name: str(f.type) for f in schema}
        if path.name.startswith("fills_"):
            ts = pq.read_table(path, columns=["timestamp"])["timestamp"]
            record["first_timestamp_unix"] = pc.min(ts).as_py()
            record["last_timestamp_unix"] = pc.max(ts).as_py()
        fills.append(record)

    return {
        "dataset_id": "DATA-001-sources",
        "sources": [
            {
                "source_id": "pmxt_orderbook_archive",
                "provider": "PendulumFlow PMXT archive (V1: mirror of a third-party capture; "
                "V2: PendulumFlow capture)",
                "physical_location": PMXT_ARCHIVE_BASE_URL + "/{v1,v2}/polymarket_orderbook_"
                "YYYY-MM-DDTHH.parquet (public HTTPS, no credentials)",
                "format": "Parquet, one file per UTC hour",
                "compression": "Parquet internal (ZSTD/Snappy per column chunk)",
                "schemas": {
                    "PMXT_V1": {
                        "timestamp_received": "timestamp[ms, UTC] recorder receive time",
                        "timestamp_created_at": "timestamp[ms, UTC] archive write time (later)",
                        "market_id": "string condition id (0x...)",
                        "update_type": "string: book_snapshot | price_change",
                        "data": "JSON text: token_id, side (YES/NO label), best_bid, best_ask, "
                        "timestamp (float s, == receive time), bids/asks [[price,size]] "
                        "(snapshots), change_price/change_size/change_side (changes)",
                    },
                    "PMXT_V2": {
                        "timestamp_received": "timestamp[ms, UTC] archive receive time",
                        "timestamp": "timestamp[ms, UTC] venue event time",
                        "market": "fixed_size_binary[66] condition id",
                        "event_type": "book | price_change | last_trade_price | "
                        "tick_size_change",
                        "asset_id": "string token id",
                        "bids/asks": "JSON text [[price,size]] on book rows (asks listed "
                        "high-to-low by the source)",
                        "price/size/best_bid/best_ask": "decimal128 exact",
                        "side": "BUY | SELL",
                        "transaction_hash/fee_rate_bps": "last_trade_price rows",
                        "old_tick_size/new_tick_size": "tick_size_change rows",
                    },
                },
                "order_book_representation": "full L2 book messages (snapshots) interleaved "
                "with per-level price_change deltas carrying post-change best bid/ask",
                "timestamp_resolution": "milliseconds; ties unordered",
                "coverage_note": "V1 2026-02-21T18..2026-04-16T05, V2 from 2026-04-13T19",
                "hours": archive_hours,
            },
            {
                "source_id": "pmxt_exact_token_extracts",
                "provider": "DATA-001 acquire stage (this repository)",
                "physical_location": "local/Kaggle directory <FAMILY>/date=/hour=/events.parquet",
                "format": "Parquet (ZSTD); raw archive rows for candidate tokens only, raw "
                "columns preserved plus research_family/source_version",
            },
            {
                "source_id": "polyleviathan_fills",
                "provider": "PolyLeviathan on-chain fill export (sisterreq_fills_20260926)",
                "physical_location": "local/Kaggle directory fills_<FAMILY>.parquet, "
                "markets_<FAMILY>.parquet",
                "format": "Parquet",
                "timestamp_resolution": "seconds (block time)",
                "fill_representation": "one row per (tx_hash, log_index, token_id); price/size "
                "binary float64; side/maker/taker as exported",
                "files": fills,
            },
        ],
    }


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
