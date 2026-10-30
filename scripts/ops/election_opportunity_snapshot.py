"""GET-only SIG tournament snapshot feeding the offline election opportunity ranker.

Usage:
  python scripts/ops/election_opportunity_snapshot.py --runtime-env PATH \
      --max-exit-markets 4 --entry-exchange-id 840

Prints a transient JSON snapshot to stdout. It never places an order or writes
a historical dataset. Its probability_views and scenarios are intentionally
EMPTY until independently evidenced joint-election models are supplied.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from dotenv import load_dotenv

from predictions_cup.analysis.election_opportunity_snapshot import (
    snapshot_election_opportunities,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Capture one bounded GET-only SIG portfolio and price snapshot"
    )
    parser.add_argument("--runtime-env", type=Path, required=True)
    parser.add_argument("--max-exit-markets", type=int, default=4)
    parser.add_argument("--size-limit", type=int, default=2500)
    parser.add_argument("--depth", type=int, default=20)
    parser.add_argument("--entry-exchange-id", action="append", default=[])
    args = parser.parse_args()
    if not args.runtime_env.is_file():
        parser.error("runtime environment not found")
    load_dotenv(args.runtime_env, override=True)
    try:
        document = asyncio.run(snapshot_election_opportunities(
            max_exit_markets=args.max_exit_markets,
            size_limit=args.size_limit, depth=args.depth,
            entry_exchange_ids=tuple(args.entry_exchange_id),
        ))
    except (ValueError, RuntimeError, OSError) as exc:
        parser.error(str(exc))
    print(json.dumps(document, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
