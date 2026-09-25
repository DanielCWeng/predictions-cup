#!/usr/bin/env python3
"""Extract compact Joseph3222 1-minute L2 snapshots with DuckDB range reads."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import shutil
import subprocess
import tempfile
from datetime import UTC, date, datetime, timedelta
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", required=True, help="UTC inclusive ISO timestamp")
    parser.add_argument("--end", required=True, help="UTC exclusive ISO timestamp")
    parser.add_argument("--asset-id", action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--manifest", type=Path)
    return parser.parse_args()


def aware(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise SystemExit("timestamps must include a UTC offset")
    return parsed.astimezone(UTC)


def dates(start: datetime, end: datetime) -> list[date]:
    out: list[date] = []
    current = start.date()
    last = (end - timedelta(microseconds=1)).date()
    while current <= last:
        out.append(current)
        current += timedelta(days=1)
    return out


def quote_sql(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def normalize_levels(raw: str | None) -> list[list[str]]:
    if not raw:
        return []
    data = json.loads(raw, parse_float=str, parse_int=str)
    if not isinstance(data, list):
        raise ValueError("depth JSON must be a list")
    out: list[list[str]] = []
    for item in data:
        if not isinstance(item, list) or len(item) != 2:
            raise ValueError("depth entry must be [price,size]")
        out.append([str(item[0]), str(item[1])])
    return out


def main() -> None:
    args = parse_args()
    start = aware(args.start)
    end = aware(args.end)
    if end <= start:
        raise SystemExit("--end must be after --start")
    duckdb = shutil.which("duckdb")
    if duckdb is None:
        raise SystemExit("duckdb CLI is required; install DuckDB before running this extractor")

    paths = [
        "hf://datasets/Joseph3222/polymarket-orderbook/"
        f"orderbook_1min/date={day.isoformat()}/data_0.parquet"
        for day in dates(start, end)
    ]
    source_list = ", ".join(quote_sql(path) for path in paths)
    assets = ", ".join(quote_sql(str(asset)) for asset in sorted(set(args.asset_id)))
    start_ts = int(start.timestamp())
    end_ts = int(end.timestamp())

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        csv_path = Path(tmp) / "extract.csv"
        sql = f"""
INSTALL httpfs;
LOAD httpfs;
COPY (
    SELECT minute_ts, CAST(asset_id AS VARCHAR) AS asset_id, condition_id,
           bids_json, asks_json, best_bid, best_ask
    FROM read_parquet([{source_list}])
    WHERE CAST(asset_id AS VARCHAR) IN ({assets})
      AND minute_ts >= {start_ts}
      AND minute_ts < {end_ts}
    ORDER BY minute_ts, asset_id
) TO {quote_sql(str(csv_path))} (HEADER, FORMAT CSV);
"""
        subprocess.run([duckdb, "-c", sql], check=True)

        count = 0
        first_ts: int | None = None
        last_ts: int | None = None
        conditions: set[str] = set()
        with csv_path.open("r", encoding="utf-8", newline="") as src, args.output.open(
            "w", encoding="utf-8"
        ) as dst:
            for row in csv.DictReader(src):
                minute_ts = int(row["minute_ts"])
                condition_id = row["condition_id"]
                record = {
                    "minute_ts": minute_ts,
                    "asset_id": row["asset_id"],
                    "condition_id": condition_id,
                    "bids": normalize_levels(row.get("bids_json")),
                    "asks": normalize_levels(row.get("asks_json")),
                }
                dst.write(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n")
                count += 1
                first_ts = minute_ts if first_ts is None else min(first_ts, minute_ts)
                last_ts = minute_ts if last_ts is None else max(last_ts, minute_ts)
                conditions.add(condition_id)

    digest = hashlib.sha256(args.output.read_bytes()).hexdigest()
    manifest = {
        "dataset_id": args.output.stem,
        "source": "Joseph3222/polymarket-orderbook:orderbook_1min",
        "source_url_or_origin": "hf://datasets/Joseph3222/polymarket-orderbook/orderbook_1min/",
        "downloaded_at": datetime.now(UTC).isoformat(),
        "source_version_if_known": "Hugging Face orderbook_1min",
        "sha256": digest,
        "file_size": args.output.stat().st_size,
        "format": "jsonl",
        "compression": None,
        "markets": [],
        "conditions": sorted(conditions),
        "tokens": sorted(set(args.asset_id)),
        "time_start": (
            None if first_ts is None else datetime.fromtimestamp(first_ts, UTC).isoformat()
        ),
        "time_end": None if last_ts is None else datetime.fromtimestamp(last_ts, UTC).isoformat(),
        "record_count": count,
        "timestamp_semantics": "minute-end source-time state; BUILD-005 uses SOURCE_TIME_PROXY",
        "data_grade": "SNAPSHOT_ONLY",
        "known_gaps": (
            "Minutes without activity have no row; PMXT source omissions remain inherited."
        ),
        "known_limitations": "Not exact event replay; no historical local observed_at or latency.",
    }
    manifest_path = args.manifest or args.output.with_suffix(".manifest.json")
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps({"rows": count, "sha256": digest, "output": str(args.output)}))


if __name__ == "__main__":
    main()
