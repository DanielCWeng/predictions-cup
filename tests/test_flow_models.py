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


def test_chronological_split_by_group_does_not_split_on_global_calendar() -> None:
    from predictions_cup.learning.flow_models import chronological_split_by_group

    times = np.array([0, 10, 20, 1000, 1010, 1020], dtype=np.int64)
    groups = np.array(["A", "A", "A", "B", "B", "B"], object)
    train, validation = chronological_split_by_group(
        times,
        groups,
        train_fraction=2 / 3,
        embargo_ns=0,
    )
    assert train.tolist() == [True, True, False, True, True, False]
    assert validation.tolist() == [False, False, True, False, False, True]


def test_joint_circular_shift_preserves_rows_and_feature_pairing() -> None:
    from predictions_cup.learning.flow_models import circular_shift_feature_columns

    values = np.column_stack([np.arange(20, dtype=float), 10 * np.arange(20, dtype=float)])
    groups = np.array(["A"] * 10 + ["B"] * 10, object)
    shifted = circular_shift_feature_columns(
        values,
        groups,
        minimum_shift_rows=2,
        seed=123,
    )
    for start, end in ((0, 10), (10, 20)):
        assert sorted(map(tuple, shifted[start:end].tolist())) == sorted(
            map(tuple, values[start:end].tolist())
        )
        assert np.allclose(shifted[start:end, 1], 10 * shifted[start:end, 0])


def test_signed_row_permutation_preserves_group_multiset() -> None:
    from predictions_cup.learning.flow_models import permute_signed_feature_rows

    values = np.array([[1.0, 2.0], [-3.0, -4.0], [5.0, 6.0], [-7.0, -8.0]])
    groups = np.array(["A", "A", "B", "B"], object)
    permuted = permute_signed_feature_rows(values, groups, seed=9)
    assert sorted(map(tuple, permuted[:2].tolist())) == sorted(map(tuple, values[:2].tolist()))
    assert sorted(map(tuple, permuted[2:].tolist())) == sorted(map(tuple, values[2:].tolist()))


def test_empirical_upper_p_uses_plus_one_correction() -> None:
    from predictions_cup.learning.flow_models import empirical_upper_p

    null = np.array([-1.0, 0.0, 0.1, 0.2])
    assert empirical_upper_p(1.0, null) == 0.2
    assert empirical_upper_p(0.1, null) == 0.6

def test_ridge_prediction_delta_matches_full_replacement() -> None:
    rng = np.random.default_rng(20260928)
    x = rng.normal(size=(500, 6))
    y = rng.normal(size=500)
    w = rng.uniform(0.1, 1.0, size=500)
    w /= w.sum()
    names = tuple(f"x{i}" for i in range(x.shape[1]))
    model = fit_weighted_ridge(x, y, w, feature_names=names, alpha=1.0)

    observed = predict_ridge(model, x)
    positions = np.array([1, 4])
    transformed = x[:, positions].copy()
    transformed = np.roll(transformed, 37, axis=0)

    full = x.copy()
    full[:, positions] = transformed
    full_prediction = predict_ridge(model, full)

    slopes = model.coefficient[positions] / model.scale[positions]
    delta_prediction = observed + (transformed - x[:, positions]) @ slopes

    assert np.allclose(full_prediction, delta_prediction, rtol=0.0, atol=2e-15)
    assert weighted_mse(y, full_prediction, w) == weighted_mse(y, delta_prediction, w)
