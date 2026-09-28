"""Prepare immutable EXPERIMENT-005E Kaggle code bundles."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PREREG = ROOT / "data/experiments/experiment_005e/preregistration.json"
RUNNER = ROOT / "scripts/kaggle/experiment_005e/run.py"
DATA002 = ROOT / "data/manifests/fees/data_002_manifest.json"
INFRA = ROOT / "data/reference/polymarket_infrastructure/infra_registry.json"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", choices=("TRAIN_DEV", "HOLDOUT"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--freeze", type=Path)
    args = parser.parse_args()
    if args.phase == "HOLDOUT" and args.freeze is None:
        raise SystemExit("HOLDOUT requires --freeze")
    out = args.output
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    head = subprocess.check_output(["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True).strip()

    bundle = out / "predictions_cup_005e.bundle"
    with zipfile.ZipFile(bundle, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted((ROOT / "src").rglob("*.py")):
            archive.write(path, path.relative_to(ROOT / "src"))

    files: list[Path] = [bundle]
    for source, name in (
        (PREREG, "preregistration.json"),
        (DATA002, "data_002_manifest_expected.json"),
        (INFRA, "infra_registry.json"),
    ):
        target = out / name
        shutil.copy2(source, target)
        files.append(target)
    if args.freeze is not None:
        freeze_target = out / "pre_holdout_freeze.json"
        shutil.copy2(args.freeze, freeze_target)
        files.append(freeze_target)

    config = out / "run_config.json"
    config.write_text(json.dumps({"phase": args.phase}, indent=2, sort_keys=True) + "\n")
    files.append(config)
    manifest = {
        "schema_version": 1,
        "experiment_id": "EXPERIMENT-005E",
        "phase": args.phase,
        "canonical_start": "ba938bedcf63f562be8b26c9502e828391123867",
        "implementation_commit": head,
        "preregistration_sha256": sha256(PREREG),
        "runner_sha256": sha256(RUNNER),
        "files": {path.name: sha256(path) for path in files},
    }
    (out / "005e_code_manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    )
    slug = (
        "polyleviathan/sig-cup-005e-train-dev-code"
        if args.phase == "TRAIN_DEV"
        else "polyleviathan/sig-cup-005e-holdout-code"
    )
    (out / "dataset-metadata.json").write_text(
        json.dumps(
            {
                "title": f"SIG Cup EXP005E {args.phase} Code",
                "id": slug,
                "licenses": [{"name": "CC0-1.0"}],
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    print(json.dumps(manifest, sort_keys=True))


if __name__ == "__main__":
    main()
