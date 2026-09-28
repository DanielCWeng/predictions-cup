"""Build the post-HOLDOUT EXPERIMENT-005D independent-review Kaggle runner.

This does not reopen discovery. It regenerates the accepted corrected HOLDOUT runner,
removes its execution entry point, and appends review-only falsification diagnostics.
"""
from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

import prepare_experiment_005d_kaggle as base_prepare

ROOT = Path(__file__).resolve().parents[1]
EXP = ROOT / "data/experiments/experiment_005d"
KERNEL = ROOT / "scripts/kaggle/experiment_005d_review_followup"
BASE_KERNEL = ROOT / "scripts/kaggle/experiment_005d"
REGIMES = ROOT / "data/experiments/experiment_004a/regime_definitions.json"
ORIGINAL_HOLDOUT = EXP / "holdout_candidate_results.csv"
DESIGN = EXP / "REVIEW_FOLLOWUP_DESIGN.json"

ACCEPTED_HOLDOUT_RUNNER = "e65c961b73c56e26b322c8336944ea2fbc048ccf53bdd645cdaf77c53caef7ce"
ACCEPTED_HOLDOUT_RESULTS = "610e4b6bd0ed199c63cc2543206ce70c4900e7cb1b84a24bf0e572f4f975920b"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    base_prepare.main()
    base_run = BASE_KERNEL / "run.py"
    if sha(base_run) != ACCEPTED_HOLDOUT_RUNNER:
        raise SystemExit("accepted HOLDOUT runner drift")
    if sha(ORIGINAL_HOLDOUT) != ACCEPTED_HOLDOUT_RESULTS:
        raise SystemExit("accepted HOLDOUT result drift")
    source = base_run.read_text(encoding="utf-8")
    trailer = '\nif __name__ == "__main__":\n    main()\n'
    if trailer not in source:
        raise SystemExit("base runner entry point not found")
    source = source.rsplit(trailer, 1)[0] + "\n"
    with ORIGINAL_HOLDOUT.open(newline="", encoding="utf-8") as handle:
        original_rows = list(csv.DictReader(handle))
    embedded_review = {
        "design": json.loads(DESIGN.read_text(encoding="utf-8")),
        "design_sha256": sha(DESIGN),
        "regime_definitions": json.loads(REGIMES.read_text(encoding="utf-8")),
        "regime_definitions_sha256": sha(REGIMES),
        "original_holdout_rows": original_rows,
        "original_holdout_results_sha256": sha(ORIGINAL_HOLDOUT),
        "accepted_holdout_runner_sha256": sha(base_run),
    }
    body = (KERNEL / "run_body.py.txt").read_text(encoding="utf-8")
    generated = source + "\nREVIEW_EMBEDDED=" + repr(embedded_review) + "\n" + body
    run_path = KERNEL / "run.py"
    run_path.write_text(generated, encoding="utf-8")
    print(json.dumps({
        "phase": "POST_HOC_INDEPENDENT_REVIEW_FALSIFICATION",
        "run_sha256": sha(run_path),
        "run_bytes": run_path.stat().st_size,
        "fixed_predictive_cells": len(
            json.loads((EXP / "PRE_HOLDOUT_FREEZE.json").read_text())[
                "selected_predictive_candidate_keys"
            ]
        ),
        "design_sha256": sha(DESIGN),
    }, sort_keys=True))


if __name__ == "__main__":
    main()
