from __future__ import annotations

import numpy as np
import pytest

from predictions_cup.learning.joint_panel import (
    canonical_yes_price,
    effective_number,
    fit_feature_scaler,
    fit_masked_multioutput,
    future_target,
    logit_price,
    market_grid_features,
    masked_mse,
    predictive_ic,
    singular_spectrum,
    source_target_concentration,
    strict_asof,
    truncate_model,
)


def test_canonical_yes_price_and_logit_clip() -> None:
    assert canonical_yes_price("YES", 0.7) == pytest.approx(0.7)
    assert canonical_yes_price("no", 0.3) == pytest.approx(0.7)
    with pytest.raises(ValueError):
        canonical_yes_price("DRAW", 0.5)
    values = logit_price(np.array([0.0, 0.5, 1.0]), epsilon=1e-4)
    assert np.isfinite(values).all()
    assert values[1] == pytest.approx(0.0)


def test_strict_asof_excludes_same_second() -> None:
    times = np.array([10, 20, 30], dtype=np.int64)
    values = np.array([1.0, 2.0, 3.0])
    state, observed = strict_asof(times, values, np.array([10, 20, 21, 31]))
    assert np.isnan(state[0])
    assert state[1] == pytest.approx(1.0)
    assert state[2] == pytest.approx(2.0)
    assert state[3] == pytest.approx(3.0)
    assert observed.tolist() == [-1, 10, 20, 30]


def test_market_grid_does_not_turn_quiet_period_into_observed_zero() -> None:
    times = np.array([1, 11, 41], dtype=np.int64)
    values = np.array([0.0, 0.2, 0.5])
    decisions = np.array([20, 30, 50], dtype=np.int64)
    grid = market_grid_features(
        times,
        values,
        decisions,
        grid_seconds=10,
        lag_depth=1,
        age_cap_seconds=100,
    )
    assert grid.observed[:, 0].tolist() == [1.0, 0.0, 1.0]
    assert grid.returns[0, 0] == pytest.approx(0.2)
    assert np.isnan(grid.returns[1, 0])
    assert grid.returns[2, 0] == pytest.approx(0.3)


def test_future_target_requires_new_observation() -> None:
    times = np.array([1, 11, 41], dtype=np.int64)
    values = np.array([0.1, 0.2, 0.6])
    decisions = np.array([20, 30, 40], dtype=np.int64)
    target = future_target(
        times,
        values,
        decisions,
        horizon_seconds=10,
        age_cap_seconds=100,
    )
    assert np.isnan(target[0])
    assert np.isnan(target[1])
    assert target[2] == pytest.approx(0.4)


def test_scaler_is_finite_and_train_only_shape() -> None:
    train = np.array([[1.0, 2.0], [3.0, 2.0], [5.0, 2.0]])
    scaler = fit_feature_scaler(train)
    transformed = scaler.transform(train)
    assert np.allclose(transformed[:, 0].mean(), 0.0)
    assert np.allclose(transformed[:, 1], 0.0)


def test_masked_ridge_ignores_missing_targets() -> None:
    x = np.arange(10, dtype=np.float64).reshape(5, 2)
    y = np.array(
        [
            [0.0, np.nan],
            [1.0, 0.0],
            [2.0, 1.0],
            [3.0, np.nan],
            [4.0, 2.0],
        ]
    )
    model = fit_masked_multioutput(x, y, alpha=1.0, minimum_support=3)
    assert model.support.tolist() == [5, 3]
    pred = model.predict(x)
    assert pred.shape == y.shape
    assert np.isfinite(pred).all()


def test_reduced_rank_truncates_coefficient_matrix() -> None:
    rng = np.random.default_rng(7)
    x = rng.normal(size=(80, 6))
    true = rng.normal(size=(6, 4))
    y = x @ true + rng.normal(scale=0.01, size=(80, 4))
    base = fit_masked_multioutput(x, y, alpha=0.1, minimum_support=20)
    reduced = truncate_model(base, 2)
    assert np.linalg.matrix_rank(reduced.coef, tol=1e-9) <= 2


def test_metrics_and_concentration() -> None:
    y = np.array([[1.0, 2.0, 3.0], [2.0, 3.0, 4.0], [3.0, 4.0, 5.0]])
    pred = y.copy()
    assert masked_mse(y, pred) == pytest.approx(0.0)
    cross, temporal = predictive_ic(y, pred)
    assert cross == pytest.approx(1.0)
    assert temporal == pytest.approx(1.0)
    assert effective_number(np.array([1.0, 1.0])) == pytest.approx(2.0)

    coef = np.array([[1.0, 0.0], [1.0, 0.0], [0.0, 1.0], [0.0, 1.0]])
    concentration = source_target_concentration(
        coef,
        source_groups=np.array([0, 0, 1, 1]),
    )
    assert concentration["effective_source_markets"] == pytest.approx(2.0)
    assert concentration["effective_target_markets"] == pytest.approx(2.0)

    spectrum = singular_spectrum(np.eye(3))
    assert spectrum["effective_rank"] == pytest.approx(3.0)