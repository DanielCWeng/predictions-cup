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

