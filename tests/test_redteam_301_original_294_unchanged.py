"""Independent frozen #294 original Capital strict function-body replay (invented only).
Original function bodies/assertions below copied verbatim; #297 remains untouched.
No source/admission/live action.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

from predictions_cup.research.locked_maker_economics import estimate
from test_locked_maker_economics import at, bundle


def test_capital_exit_issuer_time_must_follow_both_fill_times() -> None:
    """An exit before future entry fills must never establish closed-lot P&L."""
    b = bundle()
    # Maker fill-a1 is Oct 2 10:01 and fill-a2 is Oct 2 10:02;
    # counterfeit cash exit occurs at 10:00:30, after order intent only.
    exit_time = at(2, 10) + timedelta(seconds=30)
    cash = tuple(
        replace(c, issuer_at=exit_time, received_at=exit_time + timedelta(seconds=1))
        if c.flow_id == "exit-a" else c
        for c in b.cashflows
    )
    verdict = estimate(replace(b, cashflows=cash))
    assert verdict.verdict == "NOT_ESTIMABLE"
    assert verdict.realised_net is None


def test_capital_settlement_issuer_time_must_follow_entry_fill() -> None:
    """Issuer settlement cash before its fill cannot close that inventory."""
    b = bundle()
    # Maker fill-b1 is Oct 5 10:01, settlement fabricated at 10:00:30.
    fake_time = at(5, 10) + timedelta(seconds=30)
    cash = tuple(
        replace(c, issuer_at=fake_time, received_at=fake_time + timedelta(seconds=1))
        if c.flow_id == "settlement-b" else c
        for c in b.cashflows
    )
    verdict = estimate(replace(b, cashflows=cash))
    assert verdict.verdict == "NOT_ESTIMABLE"
    assert verdict.realised_net is None
