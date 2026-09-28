from __future__ import annotations

import importlib.util
import json
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import cast

import pytest


def _load_module(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"unable to load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MODULE = _load_module(
    "aggregate_experiment_005b_features",
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "aggregate_experiment_005b_features.py",
)
MAIN = cast(Callable[[], None], MODULE.__dict__["main"])


def _report(family: str) -> dict[str, object]:
    return {
        "family": family,
        "feature_target_spec_sha256": "a" * 64,
        "feature_target_spec_repo_path": "data/experiments/experiment_005b/feature_target_spec.json",
        "feature_count": 2,
        "target_count": 1,
        "feature_columns": ["a", "b"],
        "target_columns": ["target"],
        "rows": 10,
        "output_bytes": 100,
        "split_counts": {"TRAIN": 6, "DEV": 2, "HOLDOUT": 2},
    }


def test_aggregate_reports(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    families = (
        "US_2024",
        "CAN_2025",
        "COL_2026",
        "HUN_2026",
        "PER_2026",
    )
    for family in families:
        path = tmp_path / f"feature_target_build_report_{family}.json"
        path.write_text(json.dumps(_report(family)))
    monkeypatch.setattr(
        "sys.argv",
        ["aggregate", "--root", str(tmp_path)],
    )
    MAIN()
    payload = json.loads(
        (tmp_path / "feature_target_build_report.json").read_text()
    )
    assert payload["totals"]["rows"] == 50
    assert payload["totals"]["split_counts"]["TRAIN"] == 30




def test_aggregate_accepts_missing_spec_repo_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    families = (
        "US_2024",
        "CAN_2025",
        "COL_2026",
        "HUN_2026",
        "PER_2026",
    )
    for family in families:
        report = _report(family)
        if family == "US_2024":
            report.pop("feature_target_spec_repo_path")
        path = tmp_path / f"feature_target_build_report_{family}.json"
        path.write_text(json.dumps(report))
    monkeypatch.setattr(
        "sys.argv",
        ["aggregate", "--root", str(tmp_path)],
    )
    MAIN()
    payload = json.loads(
        (tmp_path / "feature_target_build_report.json").read_text()
    )
    assert payload["feature_target_spec_repo_path"] == (
        "data/experiments/experiment_005b/feature_target_spec.json"
    )


def test_aggregate_accepts_feature_order_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    families = (
        "US_2024",
        "CAN_2025",
        "COL_2026",
        "HUN_2026",
        "PER_2026",
    )
    for family in families:
        report = _report(family)
        if family == "CAN_2025":
            report["feature_columns"] = ["b", "a"]
        path = tmp_path / f"feature_target_build_report_{family}.json"
        path.write_text(json.dumps(report))
    monkeypatch.setattr(
        "sys.argv",
        ["aggregate", "--root", str(tmp_path)],
    )
    MAIN()
    payload = json.loads(
        (tmp_path / "feature_target_build_report.json").read_text()
    )
    assert payload["feature_columns"] == ["a", "b"]


def test_aggregate_refuses_schema_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    families = (
        "US_2024",
        "CAN_2025",
        "COL_2026",
        "HUN_2026",
        "PER_2026",
    )
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
        MAIN()
