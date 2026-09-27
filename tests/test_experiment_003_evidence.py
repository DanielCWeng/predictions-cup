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


def test_polymarket_infrastructure_snapshot_is_bound() -> None:
    root = ROOT / "data/reference/polymarket_infrastructure"
    provenance = json.loads((root / "provenance.json").read_text())
    assert provenance["source_repository"] == "DanielCWeng/polymarketwhale"
    assert provenance["source_commit"] == "a38df098809032eae6f770ccf579a5fb56b7b36a"
    for name, record in provenance["files"].items():
        assert _sha256(root / name) == record["snapshot_sha256"]

    registry = json.loads((root / "infra_registry.json").read_text())
    assert len(registry["infra_addresses"]) == 30
    assert len(registry["exchange_addresses"]) == 5
    assert "0xc5d563a36ae78145c45a50134d48a1215220f80a" in registry["exchange_addresses"]
    assert "0xe2222d279d744050d28e00520010520000310f59" in registry["exchange_addresses"]


def test_experiment_003_review_limitations_are_explicit() -> None:
    limitations = json.loads(
        (ROOT / "data/experiments/experiment_003/inference_limitations.json").read_text()
    )
    regime = limitations["limitations"]["regime_window_dependence"]
    holdout = limitations["limitations"]["holdout_geometry"]
    assert regime["regime_windows"] == 5
    assert len(regime["event_families"]) == 3
    assert holdout["total_holdout_day_folds"] == 15
    assert holdout["election_day_holdout_folds"] == 1

    participant = json.loads(
        (
            ROOT
            / "data/experiments/experiment_003/posthoc/participant_protocol_identity_audit.json"
        ).read_text()
    )
    matches = {
        row["sha256_prefix_12"]: row["classification"]
        for row in participant["confirmed_protocol_contract_matches"]
    }
    assert matches["6dd717a425ce"] == "NegRisk CTF Exchange V1"
    assert matches["229cefd48266"] == "NegRisk CTF Exchange V2"
    assert participant["null_calibration"]["EXP004C"]["draws_raw_p_below_0_05"] == 29
    assert participant["null_calibration"]["EXP004D"]["draws_raw_p_below_0_05"] == 30


def test_experiment_003_wheel_provenance_is_honest() -> None:
    evidence = json.loads(
        (
            ROOT
            / "data/experiments/experiment_003/results/reproduction_evidence.json"
        ).read_text()
    )
    package = evidence["frozen_code_package"]
    assert package["kaggle_dataset_id"] == 12219381
    assert package["kaggle_dataset_version"] == 2
    assert package["launcher_runtime_asserted_wheel_sha256"] is False
    assert package["wheel_sha256"] == (
        "b2895b1237ef4331f5ec381037983bf9db251e99f64cff4118f5f7a15a3ac39a"
    )
