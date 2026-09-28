from __future__ import annotations

import numpy as np
import pytest

from predictions_cup.learning.review_falsification import (
    bootstrap_p_value,
    moving_block_mean_sensitivity,
    per_time_loss_advantage,
    synchronized_hour_wild_max_t,
    temporal_hour_concentration,
    utc_hour_cluster_series,
)


def test_cluster_series_and_wild_bootstrap_are_deterministic() -> None:
    times = np.arange(12, dtype=np.int64) * 1800
    a = np.array([1.0, 0.5, 0.2, 0.7, -0.1, 0.4, 0.6, 0.3, 0.2, 0.5, 0.1, 0.9])
    b = a * 0.5 + np.array([0.0, 0.1] * 6)
    sa = utc_hour_cluster_series(times, a)
    sb = utc_hour_cluster_series(times, b)
    assert sa.n_blocks == 6
    assert sa.mean == pytest.approx(float(a.mean()))
    one = synchronized_hour_wild_max_t([sa, sb], draws=200, seed=7)
    two = synchronized_hour_wild_max_t([sa, sb], draws=200, seed=7)
    assert np.array_equal(one.max_statistics, two.max_statistics)
    assert one.draw_statistics.shape == (200, 2)
    assert 1.0 <= one.effective_tests <= 2.0
    p = bootstrap_p_value(one.max_statistics, sa.t_stat)
    assert 0.0 < p <= 1.0


def test_moving_block_stability_threshold() -> None:
    values = np.linspace(-1.0, 1.0, 100)
    stable = moving_block_mean_sensitivity(
        values,
        block_rows=20,
        draws=200,
        seed=9,
    )
    assert stable.status == "STABLE_ENOUGH_FOR_CI"
    assert stable.effective_block_equivalents == pytest.approx(5.0)
    assert stable.ci_low is not None
    assert stable.ci_high is not None

    unstable = moving_block_mean_sensitivity(
        values,
        block_rows=30,
        draws=200,
        seed=9,
    )
    assert unstable.status == "UNSTABLE_TOO_FEW_BLOCK_EQUIVALENTS"
    assert unstable.ci_low is None
    assert unstable.ci_high is None


def test_per_time_loss_advantage() -> None:
    y = np.array([[1.0, 2.0], [1.0, np.nan]])
    challenger = np.array([[1.0, 2.0], [1.1, 2.0]])
    baseline = np.array([[1.2, 1.8], [1.4, 2.0]])
    advantage = per_time_loss_advantage(y, challenger, baseline)
    assert advantage[0] == pytest.approx(0.04)
    assert advantage[1] == pytest.approx(0.15)


def test_temporal_concentration_and_removal() -> None:
    times = np.array([0, 900, 3600, 4500, 7200, 8100], dtype=np.int64)
    y = np.zeros((6, 2))
    challenger = np.array(
        [[0.1, 0.1], [0.1, 0.1], [0.2, 0.2], [0.2, 0.2], [0.3, 0.3], [0.3, 0.3]]
    )
    baseline = np.full((6, 2), 0.5)
    rows, summary = temporal_hour_concentration(times, y, challenger, baseline)
    assert len(rows) == 3
    assert summary["active_blocks"] == 3
    assert summary["positive_blocks"] == 3
    assert summary["top1_positive_share"] is not None
    top1 = summary["improvement_after_remove_top1"]
    top3 = summary["improvement_after_remove_top3"]
    assert isinstance(top1, float)
    assert top1 > 0
    assert isinstance(top3, float)
    assert np.isnan(top3)