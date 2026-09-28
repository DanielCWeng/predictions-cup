"""Prepare the lane-specific Kaggle code dataset for EXPERIMENT-004C-A."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FREEZE_SHA = "4aafab4bd77d58fd82a510fa2b95842689f49ba2"
OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("/tmp/004c_a_code_dataset")
OUT.mkdir(parents=True, exist_ok=True)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def copy(rel: str, name: str | None = None) -> Path:
    src = ROOT / rel
    dst = OUT / (name or src.name)
    shutil.copy2(src, dst)
    return dst


head = subprocess.check_output(
    ["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True
).strip()
zip_path = OUT / "predictions_cup_004c_a.bundle"
with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
    for path in sorted((ROOT / "src").rglob("*.py")):
        archive.write(path, path.relative_to(ROOT / "src"))

files = [
    zip_path,
    copy("data/experiments/experiment_004c/a/preregistration.json"),
    copy("data/experiments/experiment_004c/a/pair_registry.csv"),
    copy("data/experiments/experiment_004c/a/universe.csv"),
    copy("data/experiments/experiment_004c/a/prior_exposure_manifest.json"),
    copy("data/experiments/experiment_004c/a/FREEZE_V3.json"),
    copy("data/experiments/experiment_004a/regime_definitions.json"),
]
manifest = {
    "experiment_id": "EXPERIMENT-004C-A",
    "freeze_commit": FREEZE_SHA,
    "implementation_commit": head,
    "files": {path.name: sha256(path) for path in files},
}
(OUT / "code_manifest.json").write_text(
    json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
)
(OUT / "dataset-metadata.json").write_text(
    json.dumps(
        {
            "title": "SIG Cup EXP004C-A Freshness Code",
            "id": "polyleviathan/sig-cup-004c-a-code",
            "licenses": [{"name": "CC0-1.0"}],
        },
        indent=2,
        sort_keys=True,
    )
    + "\n",
    encoding="utf-8",
)
print(json.dumps(manifest, sort_keys=True))
