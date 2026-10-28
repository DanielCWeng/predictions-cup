"""Capital-rotation proposals never turn stale books into live execution authority."""
from __future__ import annotations

import pytest

from predictions_cup.maker.capital_rotation import (
    BookLevel,
    HeldPosition,
    Replacement,
    RotationConditions,
    assess_rotation,
)


def cond(
    *,
    account_trusted: bool = True,
    reconciliation_complete: bool = True,
    depth_trusted: bool = True,
    book_age_ms: float = 25.0,
    no_conflicting_orders: bool = True,
    max_depth_age_ms: float = 1500.0,
    max_tranche_shares: int = 500,
    exit_cost_per_share: float = 0.005,
    extra_buffer_per_share: float = 0.01,
    min_total_gain: float = 5.0,
) -> RotationConditions:
    return RotationConditions(
        account_trusted=account_trusted,
        reconciliation_complete=reconciliation_complete,
        depth_trusted=depth_trusted,
        book_age_ms=book_age_ms,
        no_conflicting_orders=no_conflicting_orders,
        max_depth_age_ms=max_depth_age_ms,
        max_tranche_shares=max_tranche_shares,
        exit_cost_per_share=exit_cost_per_share,
        extra_buffer_per_share=extra_buffer_per_share,
        min_total_gain=min_total_gain,
    )


def replacement(
    *,
    exchange_id: str = "222",
    capacity_shares: int = 500,
    conservative_net_edge_per_share: float = 0.06,
) -> Replacement:
    return Replacement(
        exchange_id=exchange_id,
        capacity_shares=capacity_shares,
        conservative_net_edge_per_share=conservative_net_edge_per_share,
    )


def test_yes_exit_then_better_replacement_after_costs() -> None:
    result = assess_rotation(
        HeldPosition("111", 700, 0.62),
        replacement(),
        yes_bids=(BookLevel(0.61, 300), BookLevel(0.60, 300)),
        yes_asks=(BookLevel(0.63, 1),),
        conditions=cond(),
    )
    assert result.status == "CANDIDATE_ONLY"
    assert result.shares == 500
    assert result.exit_proceeds == pytest.approx(303.0)
    assert result.hold_fair_value == pytest.approx(310.0)
    assert result.exit_concession == pytest.approx(7.0)
    assert result.incremental_new_edge == pytest.approx(30.0)
    assert result.gain_after_buffer == pytest.approx(15.5)


def test_no_exit_buys_yes_ask_and_converts_to_no_proceeds() -> None:
    result = assess_rotation(
        HeldPosition("111", -300, 0.80),
        replacement(capacity_shares=300),
        yes_bids=(BookLevel(0.78, 400),),
        yes_asks=(BookLevel(0.81, 100), BookLevel(0.82, 200)),
        conditions=cond(min_total_gain=0),
    )
    assert result.status == "CANDIDATE_ONLY"
    assert result.shares == 300
    assert result.exit_proceeds == pytest.approx(55)
    assert result.hold_fair_value == pytest.approx(60)
    assert result.exit_concession == pytest.approx(5)


@pytest.mark.parametrize("conditions,reason", [
    (cond(account_trusted=False), "account_or_reservation_not_reconciled"),
    (cond(reconciliation_complete=False), "account_or_reservation_not_reconciled"),
    (cond(no_conflicting_orders=False), "account_or_reservation_not_reconciled"),
    (cond(depth_trusted=False), "exit_book_untrusted_or_stale"),
    (cond(book_age_ms=1500.0), "exit_book_untrusted_or_stale"),
    (cond(book_age_ms=-1.0), "exit_book_untrusted_or_stale"),
])
def test_fail_closed_on_untrusted_or_stale(
    conditions: RotationConditions, reason: str,
) -> None:
    decision = assess_rotation(
        HeldPosition("111", 500, 0.6),
        replacement(),
        yes_bids=(BookLevel(0.59, 500),),
        yes_asks=(),
        conditions=conditions,
    )
    assert decision.status == "BLOCKED"
    assert decision.reason == reason


def test_insufficient_book_does_not_propose_partial_unchecked_trade() -> None:
    decision = assess_rotation(
        HeldPosition("111", 500, 0.6),
        replacement(),
        yes_bids=(BookLevel(0.61, 100),),
        yes_asks=(),
        conditions=cond(),
    )
    assert decision.reason == "insufficient_executable_exit_depth"


def test_unprofitable_rotation_blocked_even_when_cash_could_be_freed() -> None:
    decision = assess_rotation(
        HeldPosition("111", 500, 0.65),
        replacement(conservative_net_edge_per_share=0.02),
        yes_bids=(BookLevel(0.60, 500),),
        yes_asks=(),
        conditions=cond(),
    )
    assert decision.reason == "rotation_not_worth_exit_cost"


def test_invalid_ladder_and_same_market_rejected() -> None:
    held = HeldPosition("111", 500, 0.6)
    assert assess_rotation(
        held, replacement(exchange_id="111"),
        yes_bids=(BookLevel(0.61, 500),), yes_asks=(), conditions=cond(),
    ).reason == "not_a_distinct_rotation"
    assert assess_rotation(
        held, replacement(),
        yes_bids=(BookLevel(0.60, 250), BookLevel(0.61, 250)),
        yes_asks=(), conditions=cond(),
    ).reason == "invalid_exit_ladder_order"


def test_slice_never_exceeds_held_or_replacement_room() -> None:
    result = assess_rotation(
        HeldPosition("111", 100, 0.6),
        replacement(capacity_shares=40, conservative_net_edge_per_share=0.20),
        yes_bids=(BookLevel(0.60, 40),),
        yes_asks=(),
        conditions=cond(max_tranche_shares=500, min_total_gain=0),
    )
    assert result.status == "CANDIDATE_ONLY"
    assert result.shares == 40
