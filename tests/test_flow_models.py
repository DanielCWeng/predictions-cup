import numpy as np

from predictions_cup.learning.flow_models import (
    benjamini_hochberg,
    chronological_split,
    empirical_two_sided_p,
    fit_weighted_ridge,
    hierarchical_equal_weights,
    predict_ridge,
    weighted_mse,
)


def test_hierarchical_weights_equalize_top_level_families() -> None:
    family = np.array(["A", "A", "A", "B"], object)
    event = np.array(["a1", "a1", "a2", "b1"], object)
    block = np.array(["x", "y", "z", "q"], object)
    entity = np.array(["m", "m", "m", "m"], object)
    w = hierarchical_equal_weights([family, event, block, entity])
    assert np.isclose(w[family == "A"].sum(), 0.5)
    assert np.isclose(w[family == "B"].sum(), 0.5)
    assert np.isclose(w.sum(), 1.0)


def test_weighted_ridge_recovers_simple_signal() -> None:
    x = np.arange(10, dtype=float)[:, None]
    y = 2.0 + 3.0 * x[:, 0]
    w = np.ones(10) / 10
    model = fit_weighted_ridge(x, y, w, feature_names=("x",), alpha=0.0)
    pred = predict_ridge(model, x)
    assert weighted_mse(y, pred, w) < 1e-20
    assert np.allclose(pred, y)


def test_bh_is_monotone_in_rank_and_bounded() -> None:
    p = np.array([0.01, 0.04, 0.03, 0.5])
    q = benjamini_hochberg(p)
    assert np.all((q >= 0) & (q <= 1))
    ordered = q[np.argsort(p)]
    assert np.all(np.diff(ordered) >= -1e-15)


def test_empirical_p_has_plus_one_correction() -> None:
    null = np.array([0.0, 0.1, 0.2, 0.3])
    assert empirical_two_sided_p(1.0, null) == 0.2


def test_chronological_split_applies_embargo() -> None:
    times = np.arange(0, 100, 10, dtype=np.int64)
    train, validation = chronological_split(times, train_fraction=0.5, embargo_ns=10)
    assert np.all(times[train] <= 35)
    assert np.all(times[validation] >= 55)
    assert not np.any(train & validation)
