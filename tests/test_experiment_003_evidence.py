from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def test_experiment_003_canonical_evidence_hashes() -> None:
    evidence_path = (
        ROOT / "data/experiments/experiment_003/results/reproduction_evidence.json"
    )
    evidence = json.loads(evidence_path.read_text())
    result_root = evidence_path.parent
    for relative, expected in evidence["report_sha256"].items():
        assert _sha256(result_root / relative) == expected

    prereg = ROOT / "data/experiments/experiment_003/preregistration.json"
    relationships = ROOT / "data/experiments/experiment_003/relationship_inventory.csv"
    assert _sha256(prereg) == evidence["preregistration_sha256"]
    assert _sha256(relationships) == evidence["relationship_inventory_sha256"]

    for key in ("canonical_runner", "canonical_kernel_metadata"):
        record = evidence[key]
        assert _sha256(ROOT / record["path"]) == record["sha256"]

    package = evidence["frozen_code_package"]
    commit_file = ROOT / package["commit_file_path"]
    dataset_metadata = ROOT / package["dataset_metadata_path"]
    assert _sha256(commit_file) == package["commit_file_sha256"]
    assert _sha256(dataset_metadata) == package["dataset_metadata_sha256"]


def test_experiment_003_posthoc_runner_manifest_hashes() -> None:
    manifest_path = (
        ROOT / "data/experiments/experiment_003/posthoc/runner_manifest.json"
    )
    manifest = json.loads(manifest_path.read_text())
    assert manifest["status"] == "POST_HOC_EXPLORATORY_ONLY"
    for run in manifest["runs"]:
        assert _sha256(ROOT / run["runner_path"]) == run["runner_sha256"]
        assert _sha256(ROOT / run["metadata_path"]) == run["metadata_sha256"]
        assert _sha256(ROOT / run["output"]) == run["output_sha256"]


def test_experiment_003_protocol_deviations_are_explicit() -> None:
    participant_path = (
        ROOT / "data/experiments/experiment_003/protocol_deviation_001_participant.json"
    )
    structural_path = (
        ROOT
        / "data/experiments/experiment_003/protocol_deviation_002_structural_stability.json"
    )
    participant = json.loads(participant_path.read_text())
    structural = json.loads(structural_path.read_text())
    assert participant["disposition"] == "CONFIRMATORY_VALIDITY_INVALIDATED"
    assert participant["detected_after_outcome_inspection"] is True
    assert structural["scope"] == "STABILITY_DIAGNOSTIC_ONLY"
