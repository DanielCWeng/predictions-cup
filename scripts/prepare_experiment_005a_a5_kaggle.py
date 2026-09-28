"""Build deterministic EXPERIMENT-005A A5 Kaggle code dataset."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "scripts/kaggle/experiment_005a_a5/code_dataset"
BUNDLE = OUT / "predictions_cup_005a.bundle"

MODULES = (
    "fee_role_structure.py",
    "fee_role_data.py",
    "fee_role_registry.py",
    "flow_response.py",
    "flow_panel.py",
    "flow_models.py",
    "participant_role.py",
    "role_markouts.py",
    "liquidity_response.py",
)
METADATA = {
    "regime_definitions.json": ROOT / "data/experiments/experiment_004a/regime_definitions.json",
    "execution_spec.json": ROOT / "data/experiments/experiment_005a/execution_spec.json",
    "a5_operationalization.json": (
        ROOT / "data/experiments/experiment_005a/a5/operationalization.json"
    ),
    "infra_registry.json": ROOT / "data/reference/polymarket_infrastructure/infra_registry.json",
    "conditions.csv": ROOT / "data/experiments/experiment_005a/registry/conditions.csv",
    "data_002_manifest_expected.json": ROOT / "data/manifests/fees/data_002_manifest.json",
    "preregistration.json": ROOT / "data/experiments/experiment_005a/preregistration.json",
    "empirical_gate.json": ROOT / "data/experiments/experiment_005a/empirical_gate.json",
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
        '"""Minimal EXPERIMENT-005A A5 Kaggle learning package."""\n'
    )
    for name in MODULES:
        shutil.copy2(
            ROOT / "src/predictions_cup/learning" / name,
            BUNDLE / "predictions_cup/learning" / name,
        )
    for name, source in METADATA.items():
        shutil.copy2(source, OUT / name)

    files: dict[str, str] = {}
    excluded = {"005a_a5_code_manifest.json", "dataset-metadata.json"}
    for path in sorted(OUT.rglob("*")):
        if not path.is_file() or path.name in excluded:
            continue
        files[str(path.relative_to(OUT))] = sha256(path)

    manifest = {
        "experiment_id": "EXPERIMENT-005A",
        "stage": "A5_CODE",
        "implementation_commit": git_head(),
        "execution_spec_sha256": sha256(METADATA["execution_spec.json"]),
        "a5_operationalization_sha256": sha256(METADATA["a5_operationalization.json"]),
        "preregistration_sha256": sha256(METADATA["preregistration.json"]),
        "empirical_gate_sha256": sha256(METADATA["empirical_gate.json"]),
        "files": files,
    }
    (OUT / "005a_a5_code_manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    )
    (OUT / "dataset-metadata.json").write_text(
        json.dumps(
            {
                "title": "SIG Cup EXP005A A5 Code v1",
                "id": "polyleviathan/sig-cup-exp005a-a5-code-v1",
                "licenses": [{"name": "CC0-1.0"}],
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    print(
        json.dumps(
            {"files": len(files), "implementation_commit": manifest["implementation_commit"]},
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
