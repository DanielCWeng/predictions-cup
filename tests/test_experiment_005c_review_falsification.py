from __future__ import annotations

import importlib.util
import math
from pathlib import Path

import numpy as np

RUNNER_PATH = (
    Path(__file__).resolve().parents[1]
    / "scripts/kaggle/experiment_005c_review_falsification/run.py"
)


def load_runner():
    spec = importlib.util.spec_from_file_location("review_005c_runner_test", RUNNER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def synthetic_state(family: str, panel_id: str, scale: float, shift: float):
    hourly = {
        1_700_000_000: (0.20 * scale - shift, 10),
        1_700_003_600: (-0.05 * scale - shift, 10),
        1_700_007_200: (0.10 * scale - shift, 10),
    }
    raw_sum = sum(value for value, _count in hourly.values())
    return {
        "family": family,
        "panel_id": panel_id,
        "grid_seconds": 15,
        "horizon_seconds": 15,
        "model": "M2",
        "loss_advantage_sum": raw_sum,
        "b2_loss_sum": 10.0,
        "hourly_centered": hourly,
    }


def test_multiplicity_max_stat_adjustment_is_not_less_than_cellwise(monkeypatch):
    runner = load_runner()
    monkeypatch.setattr(runner, "MULTIPLICITY_DRAWS", 2_000)
    states = [
        synthetic_state("US_2024", "us_a", 1.0, 0.0),
        synthetic_state("US_2024", "us_b", 0.8, 0.0),
        synthetic_state("PER_2026", "per_a", 0.5, 0.0),
    ]
    rows, summary = runner.compute_multiplicity(states)
    strongest = max(rows, key=lambda row: row["observed_pooled_improvement_pct_vs_b2"])
    assert strongest["familywise_max_stat_p"] >= strongest["unadjusted_block_wild_p"]
    assert 1.0 <= summary["effective_test_count_participation_ratio"] <= 3.0
    assert summary["draws"] == 2_000


def test_circular_block_bootstrap_reports_insufficient_long_blocks(monkeypatch):
    runner = load_runner()
    monkeypatch.setattr(runner, "BLOCK_DRAWS", 500)
    values = np.linspace(-0.1, 0.2, 20)
    result = runner.circular_block_bootstrap(
        values,
        block_rows=15,
        component="synthetic",
    )
    assert result["status"] == "INSUFFICIENT_EFFECTIVE_BLOCKS"
    assert math.isclose(result["effective_blocks"], 20 / 15)


def test_temporal_concentration_removal_recomputes_remaining_improvement():
    runner = load_runner()
    times = np.array(
        [
            1_700_000_000,
            1_700_003_600,
            1_700_007_200,
            1_700_010_800,
        ],
        dtype=np.int64,
    )
    advantage = np.array([[3.0], [2.0], [1.0], [-1.0]])
    base = np.full_like(advantage, 10.0)
    valid = np.ones_like(advantage, dtype=bool)
    rows, summary = runner.temporal_concentration(
        {
            "loss_advantage_matrix": advantage,
            "b2_loss_matrix": base,
            "valid_loss_matrix": valid,
            "hold_q": times,
        }
    )
    assert len(rows) == 4
    assert summary["positive_blocks"] == 3
    assert math.isclose(summary["share_positive_advantage_top_1"], 0.5)
    assert summary["after_removing_strongest_1_positive_block"][
        "relative_improvement_pct_vs_b2"
    ] > 0
    assert summary["after_removing_strongest_3_positive_blocks"][
        "relative_improvement_pct_vs_b2"
    ] < 0