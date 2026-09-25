"""Offline capture compatibility smoke for BUILD-005."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

from predictions_cup.replay.loaders import (
    CaptureSchemaError,
    CaptureSelection,
    summarize_captures,
)


def _aware_datetime(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise argparse.ArgumentTypeError("timestamp must be ISO-8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise argparse.ArgumentTypeError("timestamp must include a UTC offset")
    return parsed


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Inspect accepted SIG/Polymarket SQLite captures without network access."
    )
    parser.add_argument("--sig-db", type=Path)
    parser.add_argument("--polymarket-db", type=Path)
    parser.add_argument("--start-at", type=_aware_datetime)
    parser.add_argument("--end-at", type=_aware_datetime)
    parser.add_argument("--sig-exchange-id", action="append")
    parser.add_argument("--polymarket-token-id", action="append")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.sig_db is None and args.polymarket_db is None:
        print("at least one of --sig-db or --polymarket-db is required", file=sys.stderr)
        return 2
    try:
        selection = CaptureSelection(
            start_at=args.start_at,
            end_at=args.end_at,
            sig_exchange_ids=(
                None if args.sig_exchange_id is None else tuple(args.sig_exchange_id)
            ),
            polymarket_token_ids=(
                None
                if args.polymarket_token_id is None
                else tuple(args.polymarket_token_id)
            ),
        )
        summary = summarize_captures(
            sig_path=args.sig_db,
            polymarket_path=args.polymarket_db,
            selection=selection,
        )
    except (CaptureSchemaError, ValueError) as exc:
        print(f"capture incompatible: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(summary.as_record(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
