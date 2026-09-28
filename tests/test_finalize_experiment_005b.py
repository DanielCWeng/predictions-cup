from __future__ import annotations

from scripts.finalize_experiment_005b import build_registry


def test_registry_preserves_targets_without_candidates() -> None:
    freeze = {
        "shortlist": {
            "target_a": [
                {
                    "feature": "price_change_30",
                    "selection_label": "WITHIN_FAMILY_STABLE",
                    "spearman": 0.1,
                    "dev_pearson": 0.05,
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
                    "holdout": {"spearman": 0.03, "pearson": 0.02, "support": 100}
                },
                "model": None,
            }
        ]
    }
    rows = build_registry(freeze, holdout)
    assert [row["target"] for row in rows] == ["target_a", "target_b"]
    assert rows[0]["scalar_feature"] == "price_change_30"
    assert rows[1]["candidate_label"] == "DISCOVERY_ONLY"
