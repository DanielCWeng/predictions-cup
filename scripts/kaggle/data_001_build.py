"""Kaggle kernel entrypoint for DATA-001: rebuild and validate the corpus from mounted inputs.

Attached datasets (private, owner polyleviathan):
  sig-cup-pmxt-orderbook-extracts   <FAMILY>/date=/hour=/events.parquet (zipped per family)
  sig-cup-polyleviathan-fills       fills_<FAMILY>.parquet, markets_<FAMILY>.parquet
  sig-cup-predictions-cup-code      predictions_cup wheel + COMMIT.txt

Transformation logic lives in the wheel (predictions_cup.historical); this script only locates
inputs and invokes the same CLI used locally.
"""

from __future__ import annotations

import json
import subprocess
import sys
import zipfile
from pathlib import Path

INPUT = Path("/kaggle/input")
WORK = Path("/kaggle/working")
TMP = Path("/kaggle/tmp")
FAMILIES = ("COL_2026", "PER_2026", "HUN_2026")


def _one(pattern: str) -> Path:
    matches = sorted(INPUT.rglob(pattern))
    if len(matches) != 1:
        raise SystemExit(f"expected exactly one {pattern} under {INPUT}, found {matches}")
    return matches[0]


def _orderbooks_root() -> Path:
    for family in FAMILIES:
        dirs = [p for p in INPUT.rglob(family) if p.is_dir() and any(p.glob("date=*"))]
        if dirs:
            return dirs[0].parent
    # Directories uploaded with --dir-mode zip may arrive as archives.
    TMP.mkdir(parents=True, exist_ok=True)
    root = TMP / "orderbooks"
    for family in FAMILIES:
        with zipfile.ZipFile(_one(f"{family}.zip")) as archive:
            archive.extractall(root / family)
    return root


def main() -> None:
    wheel = _one("predictions_cup-*.whl")
    commit = (wheel.parent / "COMMIT.txt").read_text().strip()
    subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", str(wheel)])
    fills = _one("markets_COL_2026.parquet").parent
    orderbooks = _orderbooks_root()
    corpus = WORK / "historical_replay_corpus"
    cli = [sys.executable, "-m", "predictions_cup.historical"]
    subprocess.check_call(
        [*cli, "build", "--orderbooks", str(orderbooks), "--fills", str(fills),
         "--output", str(corpus), "--pipeline-commit", commit]
    )
    validation = subprocess.run(
        [*cli, "validate", "--corpus", str(corpus)], capture_output=True, text=True, check=False
    )
    print(validation.stdout)
    manifest = json.loads((corpus / "schema_version=1" / "corpus_manifest.json").read_text())
    summary = {
        "pipeline_commit": commit,
        "orderbooks_root": str(orderbooks),
        "fills_root": str(fills),
        "validate_exit_code": validation.returncode,
        "totals": manifest["totals"],
        "output_sha256": {f["path"]: f["sha256"] for f in manifest["output_files"]},
    }
    (WORK / "kaggle_run_summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True))
    if validation.returncode != 0:
        raise SystemExit("corpus validation failed")


if __name__ == "__main__":
    main()
