from __future__ import annotations

import importlib.util
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import Any, cast


def _load_module(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"unable to load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MODULE = _load_module(
    "finalize_experiment_005b",
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "finalize_experiment_005b.py",
)
BUILD_REGISTRY = cast(
    Callable[
        [dict[str, Any], dict[str, Any]],
        list[dict[str, Any]],
    ],
    MODULE.__dict__["build_registry"],
)

BUILD_DEV_VALIDATION = cast(
    Callable[[dict[str, Any]], list[dict[str, Any]]],
    MODULE.__dict__["build_dev_validation_table"],
)
BUILD_HOLDOUT_VALIDATION = cast(
    Callable[[dict[str, Any]], list[dict[str, Any]]],
    MODULE.__dict__["build_holdout_validation_table"],
)
BUILD_MODEL_COMPARISON = cast(
    Callable[[dict[str, Any]], list[dict[str, Any]]],
    MODULE.__dict__["build_model_comparison_table"],
)


def test_registry_preserves_targets_without_candidates() -> None:
    freeze = {
        "shortlist": {
            "target_a": [
                {
                    "feature": "price_change_30",
                    "selection_label": "WITHIN_FAMILY_STABLE",
                    "spearman": 0.1,
                    "dev_spearman": 0.05,
                    "stable_train_dev": True,
                    "promoted_candidate": True,
                }
            ],
            "target_b": [],
        }
    }
    holdout = {
        "results": [
            {
                "target": "target_a",
                "holdout_rows": 100,
                "holdout_market_count": 3,
                "scalar_candidate": {
                    "holdout": {
                        "spearman": 0.03,
                        "pearson": 0.02,
                        "support": 100,
                    }
                },
                "model": None,
            }
        ]
    }
    rows = BUILD_REGISTRY(freeze, holdout)
    assert [row["target"] for row in rows] == ["target_a", "target_b"]
    assert rows[0]["scalar_feature"] == "price_change_30"
    assert rows[1]["candidate_label"] == "DISCOVERY_ONLY"

def test_registry_preserves_all_promoted_candidates() -> None:
    freeze = {
        "shortlist": {
            "target_a": [
                {
                    "feature": "price_change_30",
                    "selection_label": "WITHIN_FAMILY_STABLE",
                    "spearman": 0.10,
                    "dev_spearman": 0.06,
                    "dev_pearson": 0.05,
                    "promoted_candidate": True,
                },
                {
                    "feature": "trade_count_30",
                    "selection_label": "CROSS_FAMILY_CANDIDATE",
                    "spearman": 0.08,
                    "dev_spearman": 0.04,
                    "dev_pearson": 0.03,
                    "promoted_candidate": True,
                },
            ]
        }
    }
    holdout = {
        "results": [
            {
                "target": "target_a",
                "holdout_rows": 200,
                "holdout_market_count": 4,
                "scalar_candidates": [
                    {
                        "feature": "price_change_30",
                        "holdout": {
                            "spearman": 0.02,
                            "pearson": 0.01,
                            "support": 190,
                        },
                    },
                    {
                        "feature": "trade_count_30",
                        "holdout": {
                            "spearman": 0.03,
                            "pearson": 0.02,
                            "support": 180,
                        },
                    },
                ],
                "model": None,
            }
        ]
    }
    rows = BUILD_REGISTRY(freeze, holdout)
    assert [row["scalar_feature"] for row in rows] == [
        "price_change_30",
        "trade_count_30",
    ]
    assert [row["candidate_rank"] for row in rows] == [1, 2]



def test_validation_tables_flatten_frozen_evidence() -> None:
    freeze = {
        "shortlist": {
            "target_a": [
                {
                    "feature": "price_change_30",
                    "feature_group": "price_history",
                    "selection_label": "WITHIN_FAMILY_STABLE",
                    "promoted_candidate": True,
                    "stable_train_dev": True,
                    "spearman": 0.10,
                    "dev_spearman": 0.06,
                    "dev_pearson": 0.05,
                    "model_improvement_ok": True,
                }
            ]
        }
    }
    holdout = {
        "results": [
            {
                "target": "target_a",
                "holdout_rows": 200,
                "holdout_market_count": 4,
                "scalar_candidates": [
                    {
                        "feature": "price_change_30",
                        "selection_label": "WITHIN_FAMILY_STABLE",
                        "holdout": {
                            "support": 190,
                            "pearson": 0.01,
                            "spearman": 0.02,
                            "directional_response": 0.003,
                        },
                    }
                ],
                "model": {
                    "dev_selected": {"name": "ridge"},
                    "holdout_metrics": {
                        "mae": 0.02,
                        "mse": 0.001,
                        "mae_improvement_vs_persistence": 0.05,
                    },
                    "baseline_metrics": {
                        "persistence": {"mae": 0.021, "mse": 0.0011}
                    },
                    "hierarchical_market_bootstrap": {
                        "lower_2_5": -0.001,
                        "upper_97_5": 0.002,
                    },
                },
            }
        ]
    }
    dev_rows = BUILD_DEV_VALIDATION(freeze)
    holdout_rows = BUILD_HOLDOUT_VALIDATION(holdout)
    model_rows = BUILD_MODEL_COMPARISON(holdout)
    assert dev_rows[0]["promoted_candidate"] is True
    assert holdout_rows[0]["holdout_spearman"] == 0.02
    assert [row["kind"] for row in model_rows] == [
        "dev_selected_model",
        "baseline",
    ]


def test_holdout_validation_keeps_model_only_target() -> None:
    holdout = {
        "results": [
            {
                "target": "target_clock_price_change_60",
                "holdout_rows": 50,
                "holdout_market_count": 2,
                "scalar_candidates": [],
                "model": {
                    "dev_selected": {"name": "ridge"},
                    "holdout_metrics": {
                        "mae_improvement_vs_persistence": 0.02,
                    },
                    "hierarchical_market_bootstrap": {},
                },
            }
        ]
    }
    rows = BUILD_HOLDOUT_VALIDATION(holdout)
    assert len(rows) == 1
    assert rows[0]["feature"] is None
    assert rows[0]["dev_selected_model"] == "ridge"
