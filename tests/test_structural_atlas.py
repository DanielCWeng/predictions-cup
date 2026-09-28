from __future__ import annotations

import numpy as np
import pytest

from predictions_cup.learning.structural_atlas import (
    fit_affine,
    fit_huber_affine,
    implied_pmf_from_thresholds,
    kl_partition_projection,
    leave_one_out_mean,
    logit,
    project_capped_simplex,
    project_simplex,
    weighted_isotonic_nonincreasing,
    weighted_partition_projection,
)


def test_simplex_projection_is_coherent() -> None:
    projected = project_simplex([0.55, 0.40, 0.30])
    assert np.all(projected >= 0.0)
    assert projected.sum() == pytest.approx(1.0)
    assert np.allclose(projected, [0.4666666667, 0.3166666667, 0.2166666667])


def test_capped_simplex_leaves_feasible_vector_unchanged() -> None:
    raw = np.array([0.2, 0.3, 0.1])
    assert np.allclose(project_capped_simplex(raw), raw)


def test_weighted_quadratic_partition_projection() -> None:
    projected = weighted_partition_projection([0.8, 0.4, 0.2], [4.0, 1.0, 1.0])
    assert projected.sum() == pytest.approx(1.0, abs=1e-9)
    assert projected[0] > project_simplex([0.8, 0.4, 0.2])[0]


def test_kl_partition_projection_is_probability_coherent() -> None:
    projected = kl_partition_projection([0.8, 0.4, 0.2], [1.0, 1.0, 1.0])
    assert np.all((projected > 0.0) & (projected < 1.0))
    assert projected.sum() == pytest.approx(1.0, abs=1e-9)


def test_weighted_isotonic_repairs_threshold_order() -> None:
    repaired = weighted_isotonic_nonincreasing([0.8, 0.5, 0.6, 0.2])
    assert np.all(np.diff(repaired) <= 1e-12)
    assert np.allclose(repaired, [0.8, 0.55, 0.55, 0.2])


def test_implied_pmf_from_thresholds() -> None:
    pmf = implied_pmf_from_thresholds([0.9, 0.7, 0.4, 0.1])
    assert np.allclose(pmf, [0.2, 0.3, 0.3])
    with pytest.raises(ValueError):
        implied_pmf_from_thresholds([0.5, 0.6])


def test_leave_one_out_mean_respects_weights() -> None:
    assert leave_one_out_mean([0.1, 0.2, 0.9], 2) == pytest.approx(0.15)
    assert leave_one_out_mean([0.1, 0.2, 0.9], 2, [1.0, 3.0, 1.0]) == pytest.approx(0.175)


def test_affine_and_huber_are_train_only_deterministic_estimators() -> None:
    x = np.arange(10, dtype=float)
    y = 1.0 + 2.0 * x
    ordinary = fit_affine(x, y)
    robust = fit_huber_affine(x, y)
    assert ordinary.intercept == pytest.approx(1.0)
    assert ordinary.slope == pytest.approx(2.0)
    assert robust.intercept == pytest.approx(1.0)
    assert robust.slope == pytest.approx(2.0)


def test_logit_clips_probability_boundaries() -> None:
    values = logit(np.array([0.0, 0.5, 1.0]))
    assert np.isfinite(values).all()
    assert values[0] < 0 < values[2]
