from __future__ import annotations

import numpy as np
import pytest

from predictions_cup.learning.participant_ecology import (
    ChronologicalCuts,
    bh_adjust,
    canonical_yes_price,
    chronological_cuts,
    effective_number,
    participant_yes_pressure,
    prior_count_from_sorted_groups,
    prior_cumsum_from_sorted_groups,
    ridge_fit,
    ridge_predict,
    shrunk_historical_score,
    split_labels,
)


def test_canonical_yes_axis() -> None:
    assert canonical_yes_price("YES", 0.7) == pytest.approx(0.7)
    assert canonical_yes_price("NO", 0.7) == pytest.approx(0.3)
    assert np.isnan(canonical_yes_price("OTHER", 0.5))
    assert np.isnan(canonical_yes_price("YES", 1.0))


def test_owner_pressure_is_not_aggressor_semantics() -> None:
    assert participant_yes_pressure("YES", "BUY") == 1.0
    assert participant_yes_pressure("YES", "SELL") == -1.0
    assert participant_yes_pressure("NO", "BUY") == -1.0
    assert participant_yes_pressure("NO", "SELL") == 1.0
    assert np.isnan(participant_yes_pressure("OTHER", "BUY"))


def test_chronological_cut_points_are_timestamp_only() -> None:
    cuts = chronological_cuts(np.arange(100, dtype=np.int64), train_fraction=0.7, dev_fraction=0.15)
    assert cuts.train_end_ns == 70
    assert cuts.dev_end_ns == 85


def test_split_purges_crossing_labels_and_embargo() -> None:
    cuts = ChronologicalCuts(train_end_ns=1_000, dev_end_ns=2_000)
    ts = np.array([100, 800, 950, 1_100, 1_800, 1_950, 2_100], dtype=np.int64)
    end = np.array([200, 900, 1_100, 1_200, 1_900, 2_100, 2_200], dtype=np.int64)
    labels = split_labels(ts, end, cuts, embargo_seconds=0)
    assert labels.tolist() == ["TRAIN", "TRAIN", "PURGED", "DEV", "DEV", "PURGED", "HOLDOUT"]


def test_prior_group_helpers_never_use_current_row() -> None:
    groups = np.array([1, 1, 1, 2, 2], dtype=np.int64)
    values = np.array([2.0, 3.0, 5.0, 7.0, 11.0])
    assert prior_count_from_sorted_groups(groups).tolist() == [0, 1, 2, 0, 1]
    assert prior_cumsum_from_sorted_groups(groups, values).tolist() == [0.0, 2.0, 5.0, 0.0, 7.0]


def test_shrinkage_is_neutral_for_unseen_and_count_adjusted() -> None:
    score = shrunk_historical_score(
        np.array([0.0, 10.0, 10.0]), np.array([0, 1, 100]), prior_strength=20.0
    )
    assert score[0] == 0.0
    assert score[1] == pytest.approx(10.0 / 21.0)
    assert score[2] == pytest.approx(10.0 / 120.0)


def test_ridge_recovers_signal_without_penalising_intercept() -> None:
    x = np.arange(20, dtype=float)[:, None]
    y = 2.0 + 3.0 * x[:, 0]
    beta = ridge_fit(x, y, alpha=1e-9)
    pred = ridge_predict(beta, x)
    assert np.max(np.abs(pred - y)) < 1e-6


def test_bh_adjust_is_monotone_and_deterministic() -> None:
    result = bh_adjust({"b": 0.02, "a": 0.01, "c": 0.9}, alpha=0.05)
    assert result["a"]["rank"] == 1
    assert result["b"]["rank"] == 2
    assert result["a"]["q"] <= result["b"]["q"] <= result["c"]["q"]
    assert result["a"]["reject"] is True


def test_effective_number() -> None:
    assert effective_number([]) == 0.0
    assert effective_number([1.0, 1.0, 1.0, 1.0]) == pytest.approx(4.0)
    assert effective_number([4.0, 0.0, 0.0, 0.0]) == pytest.approx(1.0)
