"""Prepare the frozen EXPERIMENT-005C review-falsification code dataset."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = (
    Path(sys.argv[1])
    if len(sys.argv) > 1
    else Path("/tmp/005c_review_code")
)

REVIEW_PREREG_SHA = "0567e3b5114d527dc8b67079c35e0dcb04ebdecc4889fc8367a24e9caa8f3181"
ORIGINAL_RUNNER_SHA = "ae4468a18d1a980c5db7bb8c683f4580ba91aad903cd49ee3e7dccc2756df40f"
FREEZE_SHA = "20f977ffb64a56ecfe230ba45d9d0021dcc97e5218b13eb307db896d14c87933"
HOLDOUT_SHA = "69ded820bdb336aa4cda16d432c79d51aae017c89fcdb71ed5afe344a60114a9"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def copy(source: Path, target_name: str) -> Path:
    target = OUT / target_name
    shutil.copy2(source, target)
    return target


OUT.mkdir(parents=True, exist_ok=True)

review_prereg = (
    ROOT
    / "data/experiments/experiment_005c/review_followup/review_preregistration.json"
)
original_runner = ROOT / "scripts/kaggle/experiment_005c/run.py"
freeze = ROOT / "data/experiments/experiment_005c/results/PRE_HOLDOUT_FREEZE.json"
holdout = ROOT / "data/experiments/experiment_005c/results/holdout_results.csv"
followup = ROOT / "scripts/kaggle/experiment_005c_review_falsification/run.py"

checks = [
    (review_prereg, REVIEW_PREREG_SHA, "review preregistration"),
    (original_runner, ORIGINAL_RUNNER_SHA, "original runner"),
    (freeze, FREEZE_SHA, "pre-HOLDOUT freeze"),
    (holdout, HOLDOUT_SHA, "original HOLDOUT results"),
]
for path, expected, label in checks:
    actual = sha256(path)
    if actual != expected:
        raise SystemExit(f"{label} hash mismatch: {actual} != {expected}")

branch_head = subprocess.check_output(
    ["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True
).strip()

files = [
    copy(review_prereg, "review_preregistration.json"),
    copy(original_runner, "original_runner.py"),
    copy(freeze, "PRE_HOLDOUT_FREEZE.json"),
    copy(holdout, "holdout_results.csv"),
    copy(followup, "followup_runner.py"),
]
manifest = {
    "experiment_id": "EXPERIMENT-005C",
    "review_id": "005C_REVIEW_FALSIFICATION_001",
    "classification": "POST_HOC_INDEPENDENT_REVIEW_FALSIFICATION",
    "review_preregistration_sha256": REVIEW_PREREG_SHA,
    "review_branch_head": branch_head,
    "followup_runner_sha256": sha256(followup),
    "files": {path.name: sha256(path) for path in files},
}
(OUT / "review_code_manifest.json").write_text(
    json.dumps(manifest, indent=2, sort_keys=True) + "\n",
    encoding="utf-8",
)
(OUT / "dataset-metadata.json").write_text(
    json.dumps(
        {
            "title": "SIG Cup EXP005C Review Falsification Code",
            "id": "polyleviathan/sig-cup-005c-review-code",
            "licenses": [{"name": "CC0-1.0"}],
        },
        indent=2,
        sort_keys=True,
    )
    + "\n",
    encoding="utf-8",
)
print(json.dumps(manifest, sort_keys=True))