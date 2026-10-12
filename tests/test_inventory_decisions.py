"""Adversarial synthetic inventory decision sensitivities, not historical alpha."""
from __future__ import annotations

from decimal import Decimal as D
from typing import Any

import pytest

from predictions_cup.research.inventory_decisions import (
    action_economics,
    passive_exit_sensitivity,
    rank_by_incremental_final_value,
)

NOW = "2026-10-12T21:00:00Z"


def fixture_position(q: str = "-100") -> dict[str, Any]:
    return {
        "exchange_id": "10", "quantity": q,
        "market_value": "60", "cost_basis": "55",
    }


def fixture_book(*, status: str = "open", when: str = NOW) -> dict[str, Any]:
    return {
        "exchange_id": "10", "status": status,
        "request_started_at": when, "received_at": when,
        "bids": [{"price": ".60", "quantity": "100"}],
        "asks": [{"price": ".40", "quantity": "100"}],
    }


def fv() -> dict[str, Any]:
    return {
        "published_at": "2026-10-12T20:59:00Z",
        "valid_until": "2026-10-12T21:05:00Z",
        "independent_of_sig_prices": True, "source": "synthetic-offline",
        "yes_lower": ".30", "yes_upper": ".50",
    }


def proof() -> dict[str, Any]:
    return {"review_status": "VERIFIED", "exchange_id": "10",
            "rule_signature": "SYNTHETIC-TEST-ONLY"}


def redeploy() -> dict[str, Any]:
    return {
        "prospectively_validated": True, "net_of_all_costs": True,
        "evidence_id": "SYNTHETIC", "heldout_opportunities": 1,
        "net_expected_profit": "12", "net_profit_lower": "5",
        "net_profit_upper": "17", "gross_shares_used": "80",
        "worst_loss": "40", "valid_until": "2026-10-12T21:05:00Z",
    }


def test_no_fv_cannot_rank_without_inventing_positive_ev() -> None:
    a = action_economics(
        fixture_position(), fixture_book(), forecast=None,
        settlement=proof(), redeployment=None, evaluated_at=NOW
    )
    assert a["status"] == "NOT_RANKABLE"
    assert a["incremental_final_ev_base"] is None
    assert "NO_INDEPENDENT_FAIR_VALUE" in a["blockers"]


def test_independent_yes_interval_inverts_for_long_no() -> None:
    a = action_economics(
        fixture_position(), fixture_book(), forecast=fv(),
        settlement=proof(), redeployment=None, evaluated_at=NOW
    )
    assert a["hold_expected_payout_low"] == "50.00"
    assert a["hold_expected_payout_high"] == "70.00"
    assert a["status"] == "ECONOMIC_SENSITIVITY_ONLY_INDEPENDENT_REVIEW_REQUIRED"
    assert D(a["cash_recovered_if_filled"]) < D("60")
    assert D(a["incremental_final_ev_base"]) < 0


def test_rotation_profit_and_capital_seconds_conditional_on_fill() -> None:
    a = action_economics(
        fixture_position(), fixture_book(), forecast=fv(),
        settlement=proof(), redeployment=redeploy(), evaluated_at=NOW
    )
    assert D(a["incremental_final_ev_base"]) > 0
    assert a["net_capacity_after_rotation"] == "20"
    assert D(a["capital_seconds_released_if_filled"]) > 0
    assert D(a["worst_incremental_settlement_loss"]) < 0
    assert not a["execution_is_guaranteed"]


def test_stale_depth_blocks_ev_even_with_fair_value() -> None:
    a = action_economics(
        fixture_position(), fixture_book(when="2026-10-12T20:00:00Z"),
        forecast=fv(), settlement=proof(), redeployment=redeploy(),
        evaluated_at=NOW,
    )
    assert a["incremental_final_ev_base"] is None
    assert "NO_FRESH_COMPLETE_EXECUTABLE_DEPTH" in a["blockers"]


def test_wrong_settlement_contract_blocks_ev() -> None:
    invalid = proof()
    invalid["exchange_id"] = "other"
    a = action_economics(
        fixture_position(), fixture_book(), forecast=fv(),
        settlement=invalid, redeployment=None, evaluated_at=NOW
    )
    assert a["status"] == "NOT_RANKABLE"


def test_future_forecast_is_rejected() -> None:
    f = fv()
    f["published_at"] = "2026-10-12T21:00:01Z"
    a = action_economics(
        fixture_position(), fixture_book(), forecast=f,
        settlement=proof(), redeployment=None, evaluated_at=NOW
    )
    assert a["incremental_final_ev_base"] is None


def test_unvalidated_redeployment_is_rejected() -> None:
    bad = redeploy()
    bad["heldout_opportunities"] = 0
    a = action_economics(
        fixture_position(), fixture_book(), forecast=fv(),
        settlement=proof(), redeployment=bad, evaluated_at=NOW
    )
    assert "UNVERIFIED_REDEPLOYMENT_ECONOMICS" in a["blockers"]


def test_passive_has_zero_gross_released_until_fill() -> None:
    a = passive_exit_sensitivity(
        D("-100"), D("100"), target_yes_price=D(".25"),
        independent_fill_probability=None,
        conditional_yes_probability_if_filled=None,
    )
    assert a["gross_release_now"] == "0"
    assert a["conditional_expected_gain"] is None


def test_passive_adverse_selection_flips_apparent_profit() -> None:
    a = passive_exit_sensitivity(
        D("100"), D("100"), target_yes_price=D(".70"),
        independent_fill_probability=D(".50"),
        conditional_yes_probability_if_filled=D(".95"),
    )
    assert D(a["conditional_expected_gain"]) < 0
    assert a["status"] == "UNFILLED_PASSIVE_SCENARIO_ONLY"


def test_post_lock_blocks_sensitivity_even_with_proof() -> None:
    a = action_economics(
        fixture_position(), fixture_book(), forecast=fv(),
        settlement=proof(), redeployment=None,
        evaluated_at="2026-11-04T17:00:00Z",
    )
    assert a["status"] == "NOT_RANKABLE"
    assert a["capital_seconds_released_if_filled"] == "0"


def test_rank_orders_only_complete_economic_sensitivities() -> None:
    blocked = {"status": "NOT_RANKABLE", "incremental_final_ev_base": None}
    low = {"status": "ECONOMIC_SENSITIVITY_ONLY_INDEPENDENT_REVIEW_REQUIRED",
           "incremental_final_ev_base": "1"}
    high = {"status": "ECONOMIC_SENSITIVITY_ONLY_INDEPENDENT_REVIEW_REQUIRED",
            "incremental_final_ev_base": "2"}
    result = rank_by_incremental_final_value([low, blocked, high])
    assert result["top_ten_ev_review_only"] == [high, low]
    assert result["unrankable"] == [blocked]


@pytest.mark.parametrize("quantity", ["0", "-2", "101"])
def test_invalid_slice_rejected(quantity: str) -> None:
    with pytest.raises(ValueError):
        action_economics(
            fixture_position(), fixture_book(), quantity=D(quantity),
            forecast=fv(), settlement=proof(), redeployment=None,
            evaluated_at=NOW,
        )
