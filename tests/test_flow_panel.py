from datetime import UTC, datetime, timedelta

import numpy as np
import pyarrow as pa

from predictions_cup.learning.flow_panel import (
    aggregate_flow_grid,
    build_quote_grid,
    build_role_flow_series,
    common_event_move,
    future_moves,
)
from predictions_cup.learning.flow_response import NS, reconstruct_genuine_bbo


def test_flow_aggregation_excludes_fill_at_decision_second() -> None:
    table = pa.table(
        {
            "role_condition_id": ["c", "c", "c"],
            "role_timestamp": pa.array([100, 110, 120], pa.int64()),
            "role_class": ["TAKER_HIGH_CONFIDENCE"] * 3,
            "role_outcome_side": ["YES", "NO", "YES"],
            "role_participant_side": ["buy", "buy", "sell"],
            "role_value_usd": [10.0, 5.0, 2.0],
            "role_size_shares": [20.0, 10.0, 4.0],
        }
    )
    series = build_role_flow_series(table, condition_id="c")
    grid = aggregate_flow_grid(series, np.array([120 * NS]), lookback_seconds=30)
    assert grid.fill_count.tolist() == [2.0]
    assert grid.signed_value.tolist() == [5.0]


def _row(second: int, bid: str, ask: str) -> dict[str, object]:
    return {
        "observed_at": datetime(2026, 1, 1, tzinfo=UTC) + timedelta(seconds=second),
        "best_bid": bid,
        "best_ask": ask,
    }


def test_quote_grid_uses_genuine_changes_not_repeated_records() -> None:
    series = reconstruct_genuine_bbo(
        [
            _row(0, "0.4", "0.5"),
            _row(10, "0.4", "0.5"),
            _row(20, "0.42", "0.5"),
            _row(30, "0.42", "0.5"),
        ]
    )
    start = int(datetime(2026, 1, 1, tzinfo=UTC).timestamp() * NS)
    grid = build_quote_grid(series, np.array([start + 30 * NS]))
    assert grid.genuine_changes_30s.tolist() == [1.0]
    assert grid.repeated_unchanged_30s.tolist() == [2.0]
    assert grid.raw_records_30s.tolist() == [3.0]


def test_future_moves_are_canonical_midpoint_differences() -> None:
    series = reconstruct_genuine_bbo(
        [_row(0, "0.4", "0.5"), _row(30, "0.5", "0.6")]
    )
    start = int(datetime(2026, 1, 1, tzinfo=UTC).timestamp() * NS)
    result = future_moves(series, np.array([start]), horizon_seconds=30)
    assert np.isclose(result[0], 0.1)


def test_common_event_move_excludes_requested_token() -> None:
    series_a = reconstruct_genuine_bbo(
        [_row(0, "0.4", "0.5"), _row(30, "0.5", "0.6")]
    )
    series_b = reconstruct_genuine_bbo(
        [_row(0, "0.3", "0.4"), _row(30, "0.32", "0.42")]
    )
    start = int(datetime(2026, 1, 1, tzinfo=UTC).timestamp() * NS)
    grid_ns = np.array([start + 30 * NS])
    grids = {
        "a": build_quote_grid(series_a, grid_ns),
        "b": build_quote_grid(series_b, grid_ns),
    }
    common = common_event_move(grids, exclude_tokens=frozenset({"a"}))
    assert np.isclose(common[0], 0.02)