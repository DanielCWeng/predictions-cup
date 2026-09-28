from datetime import UTC, datetime, timedelta

import numpy as np
import pyarrow as pa
import pytest

from predictions_cup.learning.flow_response import NS, reconstruct_genuine_bbo
from predictions_cup.learning.role_markouts import (
    ConditionTarget,
    aggregate_participant_conditioned_flow,
    before_chain_second_ns,
    build_role_markouts,
    end_of_chain_second_ns,
)

BASE = datetime(2026, 5, 1, tzinfo=UTC)


def _row(second: float, bid: str, ask: str) -> dict[str, object]:
    return {
        "observed_at": BASE + timedelta(seconds=second),
        "best_bid": bid,
        "best_ask": ask,
    }


def _role_table() -> pa.Table:
    timestamp = int(BASE.timestamp())
    return pa.table(
        {
            "maker_address": ["0xa", "0xb"],
            "role_condition_id": ["c1", "c1"],
            "role_timestamp": [timestamp, timestamp],
            "role_class": ["TAKER_HIGH_CONFIDENCE", "MAKER_HIGH_CONFIDENCE"],
            "role_outcome_side": ["YES", "NO"],
            "role_participant_side": ["buy", "buy"],
            "role_value_usd": [10.0, 5.0],
            "role_size_shares": [20.0, 10.0],
        }
    )


def test_chain_second_boundaries_are_fail_closed() -> None:
    timestamp = int(BASE.timestamp())
    assert before_chain_second_ns(timestamp) == timestamp * NS - 1
    assert end_of_chain_second_ns(timestamp) == (timestamp + 1) * NS - 1


def test_fill_markout_uses_canonical_yes_and_confirmation_time() -> None:
    series = reconstruct_genuine_bbo(
        [
            _row(-1.0, "0.39", "0.41"),
            _row(0.2, "0.40", "0.42"),
            _row(1.2, "0.40", "0.42"),
            _row(5.2, "0.42", "0.44"),
            _row(6.2, "0.42", "0.44"),
        ]
    )
    collector = series.times_ns.copy()
    rows = build_role_markouts(
        _role_table(),
        {"c1": ConditionTarget("yes", "F")},
        {"yes": series},
        collector,
        horizon_seconds=5,
        allowed_role_classes=frozenset(
            {"TAKER_HIGH_CONFIDENCE", "MAKER_HIGH_CONFIDENCE"}
        ),
    )
    assert len(rows) == 2
    taker, maker = rows
    assert taker["same_second_move"] == pytest.approx(0.01)
    assert taker["future_move"] == pytest.approx(0.02)
    assert taker["owner_markout"] == pytest.approx(0.02)
    # NO buyer has -YES exposure, so the same future YES rise is adverse.
    assert maker["owner_markout"] == pytest.approx(-0.02)
    assert maker["adverse_markout"] == pytest.approx(0.02)
    assert taker["label_available_ns"] == int(
        (BASE + timedelta(seconds=6.2)).timestamp() * NS
    )


def test_fill_markout_is_missing_across_capture_gap() -> None:
    series = reconstruct_genuine_bbo(
        [_row(0.2, "0.40", "0.42"), _row(40.2, "0.42", "0.44")]
    )
    start = int(BASE.timestamp() * NS)
    collector = np.array([start, start + 40 * NS], np.int64)
    rows = build_role_markouts(
        _role_table().slice(0, 1),
        {"c1": ConditionTarget("yes", "F")},
        {"yes": series},
        collector,
        horizon_seconds=5,
        allowed_role_classes=frozenset({"TAKER_HIGH_CONFIDENCE"}),
    )
    assert len(rows) == 1
    assert np.isnan(rows[0]["owner_markout"])
    assert rows[0]["label_available_ns"] == -1


def test_participant_conditioned_flow_excludes_fill_at_grid_second() -> None:
    timestamp = int(BASE.timestamp())
    rows = [
        {
            "role": "TAKER",
            "condition_id": "c",
            "timestamp_seconds": timestamp,
            "owner_yes_sign": 1,
            "value_usd": 10.0,
            "size_shares": 20.0,
        },
        {
            "role": "TAKER",
            "condition_id": "c",
            "timestamp_seconds": timestamp + 10,
            "owner_yes_sign": -1,
            "value_usd": 3.0,
            "size_shares": 5.0,
        },
        {
            "role": "TAKER",
            "condition_id": "c",
            "timestamp_seconds": timestamp + 30,
            "owner_yes_sign": 1,
            "value_usd": 100.0,
            "size_shares": 100.0,
        },
    ]
    grid = np.array([(timestamp + 30) * NS], np.int64)
    result = aggregate_participant_conditioned_flow(
        rows,
        np.array([2.0, 4.0, 99.0]),
        grid,
    )["c"]
    assert result["signed_value"].tolist() == [-3.0]
    assert result["participant_conditioned_value"].tolist() == [-12.0]
