"""Independent Red Team negatives against frozen research candidates #285/#291.

All records and quote books here are invented. Tests do not access a host,
network, private archive, account, SQLite or a real venue. No LIVE actions.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

from predictions_cup.research.locked_maker_economics import estimate
from test_005i_after_cost_admission_oracle import (
    Level,
    clustered_holdout_gate,
    evaluate_invented_trial,
    invented_trial,
)
from test_locked_maker_economics import at, bundle

D = Decimal


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


def test_capital_null_paid_fee_is_not_zero_and_no_realised_alpha() -> None:
    b = bundle()
    fake = tuple(
        replace(c, amount_susq=None) if c.flow_id == "fee-b" else c
        for c in b.cashflows
    )
    verdict = estimate(replace(b, cashflows=fake))
    assert verdict.verdict == "NOT_ESTIMABLE"
    assert verdict.realised_net is None
    assert verdict.verified_conservative_alpha_lower_bound is None


def test_capital_cherry_picked_attempt_denominator_cannot_be_source_admitted() -> None:
    """A coherent smaller manifest is not evidence of complete maker attempts."""
    b = bundle()
    narrowed = replace(
        b,
        attempts=b.attempts[:2],
        events=tuple(e for e in b.events if e.attempt_id in {"a", "b"}),
    )
    verdict = estimate(narrowed)
    # Positive arithmetic is possible from incomplete, self-reported manifests.
    assert verdict.realised_net == D("0.89")
    assert verdict.attempts == 2
    assert verdict.source_admitted is False
    assert verdict.paper_admitted is False
    assert verdict.verified_eligible_maker_denominator is None
    assert verdict.verified_conservative_alpha_lower_bound is None


def test_capital_spoofed_native_maker_reference_is_only_caller_claim() -> None:
    b = bundle()
    f = replace(b.fills[0], native_role_reference="FAKE-ISSUER-PASSIVE-ROLE")
    verdict = estimate(replace(b, fills=(f, *b.fills[1:])))
    assert verdict.realised_net == D("0.89")
    assert verdict.source_admitted is False
    assert verdict.verified_conservative_alpha_lower_bound is None


def test_capital_held_no_after_cost_uses_actual_closed_cash_not_markout() -> None:
    b = bundle()
    # All base records are fake; a positive NO lot does not prove venue proceeds.
    verdict = estimate(b)
    assert verdict.realised_net == D("0.89")
    assert verdict.realised_gross == D("0.90")
    assert verdict.paid_fees == D("-0.02")
    assert verdict.source_admitted is False
    assert verdict.verified_conservative_alpha_lower_bound is None


def test_005i_naive_issuer_publisher_clock_must_fail_closed() -> None:
    """Malformed source timestamps should reject, not raise or score profit."""
    t = invented_trial()
    publisher_without_timezone = t.arrival.published_at.replace(tzinfo=None)
    arr = replace(t.arrival, published_at=publisher_without_timezone)
    result = evaluate_invented_trial(replace(t, arrival=arr))
    assert result.status == "NET_NOT_ESTIMABLE"
    assert result.realised_net_pnl is None


def test_005i_inverted_publisher_and_receipt_clock_rejected() -> None:
    t = invented_trial()
    arr = replace(
        t.arrival, published_at=t.arrival.received_at + timedelta(seconds=1),
    )
    result = evaluate_invented_trial(replace(t, arrival=arr))
    assert result.status == "NET_NOT_ESTIMABLE"
    assert result.realised_net_pnl is None


def test_005i_same_price_depth_zero_cannot_be_monetised() -> None:
    t = invented_trial()
    arrival = replace(
        t.arrival,
        bids=(Level(D("0.40"), 0),),
        asks=(Level(D("0.42"), 0),),
    )
    verdict = evaluate_invented_trial(replace(t, arrival=arrival))
    assert verdict.status == "NET_NOT_ESTIMABLE"
    assert verdict.realised_net_pnl is None


def test_005i_all_fake_proof_hashes_still_never_report_realised_profit() -> None:
    t = invented_trial()
    verdict = evaluate_invented_trial(t)
    assert verdict.status == "SYNTHETIC_SCENARIO_ONLY"
    assert verdict.after_cost_quote_scenario is not None
    assert verdict.after_cost_quote_scenario > 0
    assert verdict.realised_net_pnl is None


def test_005i_repeated_market_minute_does_not_become_independent_holdout() -> None:
    t = invented_trial()
    times = tuple(
        (f"event_{n}", "market_1", t.decision_at + timedelta(seconds=n))
        for n in range(100)
    )
    all_controls = frozenset({"NO_TRADE", "REVERSAL", "MOMENTUM", "RANDOM"})
    assert clustered_holdout_gate(times, all_controls) == "NET_NOT_ESTIMABLE"
