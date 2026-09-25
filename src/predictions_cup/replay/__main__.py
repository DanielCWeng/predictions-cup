"""Offline capture compatibility smoke for BUILD-005."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from predictions_cup.replay.loaders import CaptureSchemaError, summarize_captures


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Inspect accepted SIG/Polymarket SQLite captures without network access."
    )
    parser.add_argument("--sig-db", type=Path)
    parser.add_argument("--polymarket-db", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.sig_db is None and args.polymarket_db is None:
        print("at least one of --sig-db or --polymarket-db is required", file=sys.stderr)
        return 2
    try:
        summary = summarize_captures(
            sig_path=args.sig_db,
            polymarket_path=args.polymarket_db,
        )
    except CaptureSchemaError as exc:
        print(f"capture incompatible: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(summary.as_record(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
