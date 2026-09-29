from __future__ import annotations

from decimal import Decimal

import numpy as np
import pytest

from predictions_cup.learning.historical_predictive_atlas import (
    ReconstructionError,
    assert_identity_blind_predictors,
    canonical_spec_hash,
    canonical_yes_price,
    clock_target_indices,
    event_trade_target_indices,
    family_time_boundaries,
    leave_one_out_mean,
    reconstruct_transaction_group,
)


def _rows() -> list[dict[str, object]]:
    common = {
        "family": "TEST",
        "event_id": "event",
        "market_id": "market",
        "condition_id": "condition",
        "timestamp": 100,
        "tx_hash": "0xabc",
        "outcome_side": "YES",
        "price": "0.60",
    }
    return [
        {
            **common,
            "log_index": 3,
            "size_shares": "10",
            "value_usd": "6",
            "participant_address": "active-secret",
            "order_is_match_taker_order": True,
        },
        {
            **common,
            "log_index": 1,
            "size_shares": "4",
            "value_usd": "2.4",
            "participant_address": "maker-a",
            "order_is_match_taker_order": False,
        },
        {
            **common,
            "log_index": 2,
            "size_shares": "6",
            "value_usd": "3.6",
            "participant_address": "maker-b",
            "order_is_match_taker_order": False,
        },
    ]


def test_yes_axis_and_non_binary_failure() -> None:
    assert canonical_yes_price("YES", "0.3") == Decimal("0.3")
    assert canonical_yes_price("NO", "0.3") == Decimal("0.7")
    with pytest.raises(ReconstructionError):
        canonical_yes_price("OTHER", "0.3")


def test_reconstruction_preserves_multi_maker_without_double_count() -> None:
    trades = reconstruct_transaction_group(_rows())
    assert len(trades) == 2
    assert [trade.log_index for trade in trades] == [1, 2]
    assert sum(trade.size_shares for trade in trades) == Decimal("10")
    assert all("address" not in key for key in trades[0].as_dict())


def test_reconstruction_fails_closed_on_conservation() -> None:
    rows = _rows()
    rows[0]["size_shares"] = "11"
    with pytest.raises(ReconstructionError, match="size conservation"):
        reconstruct_transaction_group(rows)


def test_no_future_observation_is_not_zero_movement() -> None:
    times = np.array([10, 10, 15, 30], dtype=np.int64)
    target = clock_target_indices(times, 5)
    assert target.tolist() == [2, 2, -1, -1]


def test_event_time_targets_preserve_same_timestamp_sequence() -> None:
    assert event_trade_target_indices(4, 2).tolist() == [2, 3, -1, -1]


def test_family_boundaries_use_time_span_not_row_quantiles() -> None:
    assert family_time_boundaries(0, 100) == (60, 80)


def test_leave_one_out_mean_excludes_target() -> None:
    result = leave_one_out_mean(
        np.array([9.0, 2.0]),
        np.array([3.0, 1.0]),
        np.array([3.0, 2.0]),
    )
    assert result[0] == 3.0
    assert np.isnan(result[1])


def test_identity_blind_guard() -> None:
    assert_identity_blind_predictors(["price_yes", "trade_count_30"])
    with pytest.raises(ValueError, match="forbidden"):
        assert_identity_blind_predictors(["price_yes", "maker_address"])


def test_spec_hash_is_order_invariant() -> None:
    assert canonical_spec_hash({"b": 2, "a": 1}) == canonical_spec_hash({"a": 1, "b": 2})
