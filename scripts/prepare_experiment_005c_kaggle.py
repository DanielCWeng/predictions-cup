"""Prepare the frozen EXPERIMENT-005C code dataset for Kaggle."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FREEZE_COMMIT = "ea7ed06dd47cb8240cc8a1e5dedabf00d009843f"
PREREG_SHA = "b6505f8b07dd064cb1e4133a660c5d1f43864823b696539aa60e474eb9b8b6f5"
OUT = (
    Path(sys.argv[1])
    if len(sys.argv) > 1
    else Path("/tmp/005c_joint_panel_code")
)
OUT.mkdir(parents=True, exist_ok=True)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def copy(rel: str, name: str | None = None) -> Path:
    source = ROOT / rel
    target = OUT / (name or source.name)
    shutil.copy2(source, target)
    return target


head = subprocess.check_output(
    ["git", "-C", str(ROOT), "rev-parse", "HEAD"],
    text=True,
).strip()

prereg = ROOT / "data/experiments/experiment_005c/preregistration.json"
if sha256(prereg) != PREREG_SHA:
    raise SystemExit("005C preregistration hash changed after freeze")

bundle = OUT / "predictions_cup_005c.bundle"
with zipfile.ZipFile(
    bundle,
    "w",
    compression=zipfile.ZIP_DEFLATED,
) as archive:
    for path in sorted((ROOT / "src").rglob("*.py")):
        archive.write(
            path,
            path.relative_to(ROOT / "src"),
        )

prereg_copy = copy(
    "data/experiments/experiment_005c/preregistration.json"
)
provenance_copy = copy(
    "data/experiments/experiment_005c/provenance.json"
)
amendment_copy = copy(
    "data/experiments/experiment_005c/amendment_001_data_source_completeness.json"
)
runner = ROOT / "scripts/kaggle/experiment_005c/run.py"

files = [bundle, prereg_copy, provenance_copy, amendment_copy]
manifest = {
    "experiment_id": "EXPERIMENT-005C",
    "freeze_commit": FREEZE_COMMIT,
    "preregistration_sha256": PREREG_SHA,
    "implementation_commit": head,
    "runner_sha256": sha256(runner),
    "files": {
        path.name: sha256(path)
        for path in files
    },
}
(OUT / "code_manifest.json").write_text(
    json.dumps(
        manifest,
        indent=2,
        sort_keys=True,
    )
    + "\n",
    encoding="utf-8",
)
(OUT / "dataset-metadata.json").write_text(
    json.dumps(
        {
            "title": "SIG Cup EXP005C Joint Panel Code",
            "id": "polyleviathan/sig-cup-005c-code",
            "licenses": [{"name": "CC0-1.0"}],
        },
        indent=2,
        sort_keys=True,
    )
    + "\n",
    encoding="utf-8",
)
print(json.dumps(manifest, sort_keys=True))
