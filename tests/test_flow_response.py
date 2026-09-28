from datetime import UTC, datetime, timedelta

import numpy as np

from predictions_cup.learning.flow_response import (
    NS,
    asof_index,
    block_permutation,
    circular_shift_nonmissing,
    count_events,
    future_mid_move,
    reconstruct_genuine_bbo,
)


def _row(seconds: int, bid: str, ask: str) -> dict[str, object]:
    return {
        "observed_at": datetime(2026, 1, 1, tzinfo=UTC) + timedelta(seconds=seconds),
        "best_bid": bid,
        "best_ask": ask,
    }


def test_repeated_identical_records_are_not_genuine_changes() -> None:
    series = reconstruct_genuine_bbo(
        [_row(0, "0.4", "0.6"), _row(5, "0.4", "0.6"), _row(10, "0.4", "0.6")]
    )
    assert series.genuine_change.tolist() == [False, False, False]
    assert series.repeated_unchanged.tolist() == [False, True, True]
    start = int(datetime(2026, 1, 1, tzinfo=UTC).timestamp() * NS)
    assert count_events(series, start, start + 10 * NS, kind="raw") == 2


def test_real_bbo_change_and_midpoint_change_are_distinct() -> None:
    series = reconstruct_genuine_bbo(
        [
            _row(0, "0.4", "0.6"),
            _row(5, "0.39", "0.61"),
            _row(10, "0.41", "0.61"),
        ]
    )
    assert series.genuine_change.tolist() == [False, True, True]
    assert series.midpoint_change.tolist() == [False, False, True]


def test_same_timestamp_conflict_fails_closed_and_resets_continuity() -> None:
    series = reconstruct_genuine_bbo(
        [
            _row(0, "0.4", "0.6"),
            _row(5, "0.4", "0.6"),
            _row(5, "0.41", "0.6"),
            _row(10, "0.42", "0.6"),
            _row(15, "0.43", "0.6"),
        ]
    )
    assert series.valid.tolist() == [True, False, True, True]
    assert series.genuine_change.tolist() == [False, False, False, True]


def test_long_gap_breaks_continuity() -> None:
    series = reconstruct_genuine_bbo(
        [_row(0, "0.4", "0.6"), _row(301, "0.41", "0.6"), _row(302, "0.42", "0.6")]
    )
    assert series.genuine_change.tolist() == [False, False, True]


def test_asof_and_future_move_fail_closed_on_staleness() -> None:
    series = reconstruct_genuine_bbo(
        [_row(0, "0.4", "0.6"), _row(10, "0.5", "0.6"), _row(20, "0.6", "0.7")]
    )
    start = int(datetime(2026, 1, 1, tzinfo=UTC).timestamp() * NS)
    assert asof_index(series, start + 10 * NS, freshness_seconds=30) == 1
    assert np.isclose(
        future_mid_move(series, start + 10 * NS, 10, freshness_seconds=30),
        0.1,
    )
    assert asof_index(series, start + 400 * NS, freshness_seconds=30) is None


def test_circular_shift_preserves_missingness_and_multiset() -> None:
    values = np.array([1.0, np.nan, -2.0, 3.0, np.nan])
    shifted = circular_shift_nonmissing(values, shift=1)
    assert np.isnan(shifted[[1, 4]]).all()
    assert sorted(shifted[np.isfinite(shifted)].tolist()) == [-2.0, 1.0, 3.0]


def test_block_permutation_keeps_rows_within_contiguous_blocks() -> None:
    times = np.array([0, 10, 20, 70, 80, 140], dtype=np.int64) * NS
    order = block_permutation(times, block_seconds=60, seed=123)
    assert sorted(order.tolist()) == list(range(len(times)))
    positions = {row: i for i, row in enumerate(order.tolist())}
    assert abs(positions[0] - positions[1]) == 1
    assert abs(positions[1] - positions[2]) == 1
    assert abs(positions[3] - positions[4]) == 1