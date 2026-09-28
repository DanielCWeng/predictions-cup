"""Build the deterministic EXPERIMENT-005A Kaggle code dataset."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
KERNEL = ROOT / "scripts/kaggle/experiment_005a"
OUT = KERNEL / "code_dataset"
BUNDLE = OUT / "predictions_cup_005a.bundle"

MODULES = (
    "fee_role_structure.py",
    "fee_role_data.py",
    "fee_role_registry.py",
    "flow_response.py",
    "flow_panel.py",
    "flow_models.py",
    "participant_role.py",
)
METADATA = {
    "regime_definitions.json": ROOT / "data/experiments/experiment_004a/regime_definitions.json",
    "execution_spec.json": ROOT / "data/experiments/experiment_005a/execution_spec.json",
    "infra_registry.json": ROOT / "data/reference/polymarket_infrastructure/infra_registry.json",
    "conditions.csv": ROOT / "data/experiments/experiment_005a/registry/conditions.csv",
    "same_family_pairs.csv": (
        ROOT / "data/experiments/experiment_005a/registry/same_family_pairs.csv"
    ),
    "semantic_pairs.csv": ROOT / "data/experiments/experiment_005a/registry/semantic_pairs.csv",
    "data_002_manifest_expected.json": ROOT / "data/manifests/fees/data_002_manifest.json",
    "preregistration.json": ROOT / "data/experiments/experiment_005a/preregistration.json",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def git_head() -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()


def main() -> None:
    if OUT.exists():
        shutil.rmtree(OUT)
    (BUNDLE / "predictions_cup/learning").mkdir(parents=True)
    shutil.copy2(ROOT / "src/predictions_cup/__init__.py", BUNDLE / "predictions_cup/__init__.py")
    (BUNDLE / "predictions_cup/learning/__init__.py").write_text(
        '"""Minimal EXPERIMENT-005A Kaggle learning package."""\n'
    )
    for name in MODULES:
        shutil.copy2(
            ROOT / "src/predictions_cup/learning" / name,
            BUNDLE / "predictions_cup/learning" / name,
        )
    for name, source in METADATA.items():
        shutil.copy2(source, OUT / name)

    gate = ROOT / "data/experiments/experiment_005a/empirical_gate.json"
    if gate.exists():
        shutil.copy2(gate, OUT / "empirical_gate.json")

    files: dict[str, str] = {}
    for path in sorted(OUT.rglob("*")):
        if not path.is_file() or path.name in {
            "005a_code_manifest.json",
            "dataset-metadata.json",
        }:
            continue
        files[str(path.relative_to(OUT))] = sha256(path)

    manifest = {
        "experiment_id": "EXPERIMENT-005A",
        "implementation_commit": git_head(),
        "runner_sha256": sha256(KERNEL / "run.py"),
        "preregistration_sha256": sha256(METADATA["preregistration.json"]),
        "execution_spec_sha256": sha256(METADATA["execution_spec.json"]),
        "gate_present": gate.exists(),
        "files": files,
    }
    (OUT / "005a_code_manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    )
    dataset_metadata = {
        "title": "SIG Cup EXP005A Code",
        "id": "polyleviathan/sig-cup-exp005a-code",
        "licenses": [{"name": "CC0-1.0"}],
    }
    (OUT / "dataset-metadata.json").write_text(
        json.dumps(dataset_metadata, indent=2, sort_keys=True) + "\n"
    )
    print(json.dumps({
        "output": str(OUT),
        "implementation_commit": manifest["implementation_commit"],
        "gate_present": manifest["gate_present"],
        "files": len(files),
    }, sort_keys=True))


if __name__ == "__main__":
    main()