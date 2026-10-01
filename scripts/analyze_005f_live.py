from __future__ import annotations

import argparse
import json
from pathlib import Path

from predictions_cup.analysis.live_005f import analyze_state_transfer_from_paths


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Analyze persisted exact decision-time 005F state against future MAKE economics. "
            "This does not score or refit the frozen 005F model."
        )
    )
    parser.add_argument("--shadow-journal", required=True, type=Path)
    parser.add_argument("--live-learn-outcomes", required=True, type=Path)
    parser.add_argument("--output-root", required=True, type=Path)
    return parser


def main() -> None:
    args = _parser().parse_args()
    result = analyze_state_transfer_from_paths(
        shadow_journal_path=args.shadow_journal,
        live_learn_outcome_path=args.live_learn_outcomes,
        output_root=args.output_root,
    )
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
