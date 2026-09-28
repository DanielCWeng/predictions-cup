from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parents[1]


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


TRAIN_DEV = _load(
    ROOT / "scripts/kaggle/experiment_005b_train_dev/run.py",
    "experiment_005b_train_dev_run",
)
HOLDOUT = _load(
    ROOT / "scripts/kaggle/experiment_005b_holdout/run.py",
    "experiment_005b_holdout_run",
)


def test_directional_effect_persistence_helper() -> None:
    x = np.array([0.0, 0.1, 0.2, 0.8, 0.9, 1.0] * 10)
    y = np.array([0.0, 0.0, 0.1, 1.0, 1.1, 1.2] * 10)
    response = TRAIN_DEV.directional_response_at_threshold(x, y, 0.5)
    assert response is not None
    assert response > 0


def test_group_sign_diagnostics_require_supported_groups() -> None:
    x = np.linspace(0.0, 1.0, 400)
    y = x.copy()
    groups = np.array(["A"] * 200 + ["B"] * 200, dtype=object)
    result = TRAIN_DEV.group_sign_diagnostics(
        x,
        y,
        groups,
        0.5,
        minimum_rows=100,
    )
    assert result["support_count"] == 2
    assert result["sign_consistency"] == 1.0
    assert set(result["associations"]) == {"A", "B"}


def test_model_improvement_gate_rejects_baselines() -> None:
    regression = {
        "status": "OK",
        "best": {
            "name": "persistence_baseline",
            "mae_improvement": 0.0,
        },
        "all": [],
    }
    assert not TRAIN_DEV.model_improvement_passes(
        "target_clock_price_change_30",
        regression,
    )

    classification = {
        "status": "OK",
        "best": {
            "name": "logistic_l2",
            "brier": 0.49,
        },
        "all": [
            {
                "name": "majority_baseline",
                "brier": 0.50,
            }
        ],
    }
    assert TRAIN_DEV.model_improvement_passes(
        "target_clock_sign_30",
        classification,
    )


def test_holdout_uses_only_promoted_scalar_candidate() -> None:
    freeze = {
        "shortlist": {
            "target_clock_price_change_30": [
                {
                    "feature": "price_change_30",
                    "stable_train_dev": True,
                    "promoted_candidate": False,
                },
                {
                    "feature": "trade_count_30",
                    "stable_train_dev": True,
                    "promoted_candidate": True,
                },
            ]
        },
        "model_selection": {
            "target_clock_price_change_30": {
                "status": "OK",
                "promotion_eligible": True,
                "features": ["trade_count_30"],
            }
        },
    }
    selected = HOLDOUT.selected_columns(freeze)
    cols = selected["target_clock_price_change_30"]
    assert "trade_count_30" in cols
    assert "price_change_30" in cols
