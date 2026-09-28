from __future__ import annotations

import json

import pytest
from scripts.aggregate_experiment_005b_features import main


def _report(family: str) -> dict[str, object]:
    return {
        "family": family,
        "feature_target_spec_sha256": "a" * 64,
        "feature_target_spec_repo_path": "spec.json",
        "feature_count": 2,
        "target_count": 1,
        "feature_columns": ["a", "b"],
        "target_columns": ["target"],
        "rows": 10,
        "output_bytes": 100,
        "split_counts": {"TRAIN": 6, "DEV": 2, "HOLDOUT": 2},
    }


def test_aggregate_reports(tmp_path, monkeypatch) -> None:
    families = ("US_2024", "CAN_2025", "COL_2026", "HUN_2026", "PER_2026")
    for family in families:
        path = tmp_path / f"feature_target_build_report_{family}.json"
        path.write_text(json.dumps(_report(family)))
    monkeypatch.setattr(
        "sys.argv",
        ["aggregate", "--root", str(tmp_path)],
    )
    main()
    payload = json.loads(
        (tmp_path / "feature_target_build_report.json").read_text()
    )
    assert payload["totals"]["rows"] == 50
    assert payload["totals"]["split_counts"]["TRAIN"] == 30


def test_aggregate_refuses_schema_drift(tmp_path, monkeypatch) -> None:
    families = ("US_2024", "CAN_2025", "COL_2026", "HUN_2026", "PER_2026")
    for family in families:
        report = _report(family)
        if family == "PER_2026":
            report["feature_count"] = 3
        path = tmp_path / f"feature_target_build_report_{family}.json"
        path.write_text(json.dumps(report))
    monkeypatch.setattr(
        "sys.argv",
        ["aggregate", "--root", str(tmp_path)],
    )
    with pytest.raises(RuntimeError, match="schema/spec drift"):
        main()
