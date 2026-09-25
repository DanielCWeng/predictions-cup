#!/usr/bin/env python3
"""Extract selected canonical PolyLeviathan OrderFilled rows on its OCI host."""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import pathlib
import sys

CANONICAL_COLUMNS = [
    "timestamp", "side", "value_usd", "price", "size_shares", "token_id",
    "maker_address", "taker_address", "tx_hash", "condition_id", "log_index",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--polymarketwhale-root", type=pathlib.Path, required=True)
    parser.add_argument("--start", required=True, help="inclusive YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="inclusive YYYY-MM-DD")
    parser.add_argument("--token-id", action="append", default=[])
    parser.add_argument("--condition-id", action="append", default=[])
    parser.add_argument("--output", type=pathlib.Path, required=True)
    parser.add_argument("--bucket", default="polymarket-bot-state")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    sonar = args.polymarketwhale_root / "Sonar"
    if not sonar.exists():
        raise SystemExit(f"missing Sonar directory: {sonar}")
    sys.path.insert(0, str(sonar))

    import polars as pl  # type: ignore[import-not-found]
    from lake_reader_service import DataLakeReader  # type: ignore[import-not-found]

    start = datetime.datetime.fromisoformat(args.start).replace(tzinfo=datetime.UTC)
    end = datetime.datetime.fromisoformat(args.end).replace(tzinfo=datetime.UTC)
    if end < start:
        raise SystemExit("--end must be on or after --start")
    if not args.token_id and not args.condition_id:
        raise SystemExit("supply at least one --token-id or --condition-id")

    reader = DataLakeReader(bucket_name=args.bucket)
    files = reader.list_trade_files_in_range(start, end)
    selected: list[pl.DataFrame] = []
    for object_name in files:
        frame = reader.read_parquet_file(object_name)
        if frame is None or frame.is_empty():
            continue
        clauses: list[pl.Expr] = []
        if args.token_id and "token_id" in frame.columns:
            clauses.append(pl.col("token_id").cast(pl.Utf8).is_in(args.token_id))
        if args.condition_id and "condition_id" in frame.columns:
            clauses.append(pl.col("condition_id").cast(pl.Utf8).is_in(args.condition_id))
        if not clauses:
            continue
        predicate = clauses[0]
        for clause in clauses[1:]:
            predicate = predicate | clause
        filtered = frame.filter(predicate)
        if filtered.is_empty():
            continue
        for column in CANONICAL_COLUMNS:
            if column not in filtered.columns:
                filtered = filtered.with_columns(pl.lit(None).alias(column))
        selected.append(filtered.select(CANONICAL_COLUMNS))

    args.output.parent.mkdir(parents=True, exist_ok=True)
    if selected:
        out = (
            pl.concat(selected, how="diagonal")
            .unique(subset=["tx_hash", "log_index"], keep="last")
            .sort(["timestamp", "tx_hash", "log_index"])
        )
    else:
        out = pl.DataFrame({column: [] for column in CANONICAL_COLUMNS})
    out.write_parquet(args.output)

    digest = hashlib.sha256(args.output.read_bytes()).hexdigest()
    manifest = {
        "dataset_id": args.output.stem,
        "source": "PolyLeviathan OCI canonical trades/ RPC OrderFilled lake",
        "source_url_or_origin": f"oci://{args.bucket}/trades/",
        "downloaded_at": datetime.datetime.now(datetime.UTC).isoformat(),
        "source_version_if_known": "canonical trades/ schema",
        "sha256": digest,
        "file_size": args.output.stat().st_size,
        "format": "parquet",
        "compression": "parquet default",
        "markets": [],
        "conditions": sorted(set(args.condition_id)),
        "tokens": sorted(set(args.token_id)),
        "time_start": None if len(out) == 0 else int(out["timestamp"].min()),
        "time_end": None if len(out) == 0 else int(out["timestamp"].max()),
        "record_count": len(out),
        "timestamp_semantics": "OrderFilled execution/block timestamp; second precision",
        "data_grade": "TRADE_ONLY",
        "known_gaps": "Depends on selected canonical day-file inventory.",
        "known_limitations": (
            "Matching roles are not custody roles; no queue/depth/cancellation state."
        ),
    }
    args.output.with_suffix(".manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps({"rows": len(out), "files_scanned": len(files), "sha256": digest}))


if __name__ == "__main__":
    main()
