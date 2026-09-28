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
    getattr(MODULE, "build_registry"),
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
