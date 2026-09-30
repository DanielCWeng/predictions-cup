from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

from predictions_cup.analysis.live_005f import analyze_state_transfer_from_paths
from predictions_cup.mapping.crosswalk import load_document
from predictions_cup.shadow.live_005f import Live005FStateProvider


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Replay exact observable 005F state and stratify future MAKE economics. "
            "This does not score or refit the frozen 005F model."
        )
    )
    parser.add_argument("--mapping", required=True, type=Path)
    parser.add_argument("--grid-origin", required=True)
    parser.add_argument("--shadow-journal", required=True, type=Path)
    parser.add_argument("--live-learn-outcomes", required=True, type=Path)
    parser.add_argument("--output-root", required=True, type=Path)
    return parser


def main() -> None:
    args = _parser().parse_args()
    origin = datetime.fromisoformat(args.grid_origin)
    if origin.tzinfo is None or origin.utcoffset() is None:
        raise SystemExit("--grid-origin must include an explicit timezone offset")

    provider = Live005FStateProvider(
        mapping=load_document(args.mapping),
        grid_origin=origin,
    )
    result = analyze_state_transfer_from_paths(
        provider=provider,
        shadow_journal_path=args.shadow_journal,
        live_learn_outcome_path=args.live_learn_outcomes,
        output_root=args.output_root,
    )
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
