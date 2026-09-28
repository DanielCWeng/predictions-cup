import numpy as np

from predictions_cup.learning.participant_role import (
    deterministic_participant_seed,
    expanding_cross_fitted_scores,
    fit_participant_role_scores,
    permute_participant_identity_within_strata,
    score_participants,
)


def test_role_specific_scores_do_not_mix_maker_and_taker() -> None:
    scores = fit_participant_role_scores(
        np.array(["A", "A", "A", "A"], object),
        np.array(["TAKER", "TAKER", "MAKER", "MAKER"], object),
        np.array([1.0, 1.0, -1.0, -1.0]),
        minimum_history=2,
        prior_count=0,
    )
    assert scores[("a", "TAKER")].raw_mean == 1.0
    assert scores[("a", "MAKER")].raw_mean == -1.0


def test_sparse_and_unseen_participants_get_neutral_fallback() -> None:
    scores = fit_participant_role_scores(
        np.array(["A", "A", "B"], object),
        np.array(["TAKER", "TAKER", "TAKER"], object),
        np.array([1.0, 1.0, 5.0]),
        minimum_history=2,
        prior_count=10,
    )
    encoded = score_participants(
        np.array(["A", "B", "C"], object),
        np.array(["TAKER", "TAKER", "TAKER"], object),
        scores,
    )
    assert encoded[0] > 0
    assert encoded[1] == 0
    assert encoded[2] == 0


def test_infrastructure_participant_is_excluded() -> None:
    scores = fit_participant_role_scores(
        np.array(["0xinfra", "0xinfra", "0xperson", "0xperson"], object),
        np.array(["TAKER"] * 4, object),
        np.array([10.0, 10.0, 1.0, 1.0]),
        minimum_history=2,
        prior_count=0,
        excluded_participants=frozenset({"0xinfra"}),
    )
    assert ("0xinfra", "TAKER") not in scores
    assert ("0xperson", "TAKER") in scores


def test_expanding_cross_fit_never_uses_current_or_future_outcomes() -> None:
    times = np.arange(8, dtype=np.int64) * 10
    people = np.array(["A"] * 8, object)
    roles = np.array(["TAKER"] * 8, object)
    outcomes = np.array([1.0, 1.0, 100.0, 100.0, -100.0, -100.0, 7.0, 7.0])
    encoded, folds = expanding_cross_fitted_scores(
        times,
        people,
        roles,
        outcomes,
        fold_boundaries_ns=np.array([20, 40, 60], np.int64),
        embargo_ns=1,
        minimum_history=2,
        prior_count=0,
    )
    assert encoded[2] == 1.0 and encoded[3] == 1.0
    assert encoded[4] == 50.5 and encoded[5] == 50.5
    assert np.isclose(encoded[6], 1 / 3) and np.isclose(encoded[7], 1 / 3)
    assert folds.tolist() == [-1, -1, 0, 0, 1, 1, 2, 2]


def test_embargo_removes_near_boundary_history() -> None:
    times = np.array([0, 10, 20, 30], np.int64)
    people = np.array(["A"] * 4, object)
    roles = np.array(["TAKER"] * 4, object)
    outcomes = np.array([1.0, 9.0, 0.0, 0.0])
    encoded, _ = expanding_cross_fitted_scores(
        times,
        people,
        roles,
        outcomes,
        fold_boundaries_ns=np.array([20], np.int64),
        embargo_ns=15,
        minimum_history=1,
        prior_count=0,
    )
    assert encoded[2] == 1.0


def test_identity_permutation_preserves_each_stratum_multiset() -> None:
    people = np.array(["A", "B", "C", "D", "E"], object)
    strata = np.array(["x", "x", "x", "y", "y"], object)
    shuffled = permute_participant_identity_within_strata(people, strata, seed=123)
    assert sorted(shuffled[:3]) == ["A", "B", "C"]
    assert sorted(shuffled[3:]) == ["D", "E"]


def test_participant_seed_is_deterministic() -> None:
    assert deterministic_participant_seed(7, "a") == deterministic_participant_seed(7, "a")
    assert deterministic_participant_seed(7, "a") != deterministic_participant_seed(7, "b")


def test_available_score_waits_for_label_horizon_and_embargo() -> None:
    from predictions_cup.learning.participant_role import (
        expanding_available_participant_role_scores,
    )

    times = np.array([0, 100, 200, 400], np.int64)
    available = np.array([30, 130, 230, 430], np.int64)
    participants = np.array(["a", "a", "a", "a"], object)
    roles = np.array(["TAKER"] * 4, object)
    outcomes = np.array([2.0, 4.0, 8.0, 16.0])
    score, count = expanding_available_participant_role_scores(
        times,
        available,
        participants,
        roles,
        outcomes,
        embargo_ns=50,
        minimum_history=1,
        prior_count=0,
    )
    assert score.tolist() == [0.0, 2.0, 3.0, 14.0 / 3.0]
    assert count.tolist() == [0, 1, 2, 3]


def test_available_score_same_timestamp_rows_cannot_leak() -> None:
    from predictions_cup.learning.participant_role import (
        expanding_available_participant_role_scores,
    )

    times = np.array([100, 100, 200], np.int64)
    available = np.array([100, 100, 200], np.int64)
    participants = np.array(["a", "a", "a"], object)
    roles = np.array(["TAKER", "TAKER", "TAKER"], object)
    outcomes = np.array([1.0, 3.0, 5.0])
    score, count = expanding_available_participant_role_scores(
        times,
        available,
        participants,
        roles,
        outcomes,
        embargo_ns=0,
        minimum_history=1,
        prior_count=0,
    )
    assert score.tolist() == [0.0, 0.0, 2.0]
    assert count.tolist() == [0, 0, 2]


def test_available_score_excludes_infrastructure_identity() -> None:
    from predictions_cup.learning.participant_role import (
        expanding_available_participant_role_scores,
    )

    score, count = expanding_available_participant_role_scores(
        np.array([0, 100], np.int64),
        np.array([10, 110], np.int64),
        np.array(["0xinfra", "0xinfra"], object),
        np.array(["TAKER", "TAKER"], object),
        np.array([9.0, 9.0]),
        embargo_ns=0,
        minimum_history=1,
        prior_count=0,
        excluded_participants=frozenset({"0xinfra"}),
    )
    assert score.tolist() == [0.0, 0.0]
    assert count.tolist() == [0, 0]