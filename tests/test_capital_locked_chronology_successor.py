"""Successor Capital only: invented chronology and markout replay adversaries.

Frozen #285 source remains on its original branch; original reviewer #297
regression file is inherited byte-identically and is not modified here.
No issuer/network/host/source admission.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

from predictions_cup.research.locked_maker_economics import QuoteMarkout, estimate
from test_locked_maker_economics import at, bundle, proof

D = Decimal


def test_original_294_cash_exit_precedes_both_original_fills() -> None:
    b = bundle()
    exit_at = at(2, 10) + timedelta(seconds=30)
    cash = tuple(
        replace(c, issuer_at=exit_at, received_at=exit_at + timedelta(seconds=1))
        if c.flow_id == "exit-a" else c for c in b.cashflows
    )
    r = estimate(replace(b, cashflows=cash))
    assert r.reason == "DISPOSAL_PRECEDES_AVAILABLE_NATIVE_FILLED_LOT"
    assert r.verdict == "NOT_ESTIMABLE" and r.realised_net is None


def test_original_294_cash_settlement_precedes_original_yes_fill() -> None:
    b = bundle()
    settle_at = at(5, 10) + timedelta(seconds=30)
    cash = tuple(
        replace(c, issuer_at=settle_at, received_at=settle_at + timedelta(seconds=1))
        if c.flow_id == "settlement-b" else c for c in b.cashflows
    )
    r = estimate(replace(b, cashflows=cash))
    assert r.reason == "DISPOSAL_PRECEDES_AVAILABLE_NATIVE_FILLED_LOT"
    assert r.verdict == "NOT_ESTIMABLE" and r.realised_net is None


def test_original_native_entry_cash_cannot_precede_filled_share() -> None:
    b = bundle()
    before_fill = at(2, 10) + timedelta(seconds=30)
    cash = tuple(
        replace(c, issuer_at=before_fill, received_at=before_fill + timedelta(seconds=1))
        if c.flow_id == "entry-a1" else c for c in b.cashflows
    )
    r = estimate(replace(b, cashflows=cash))
    assert r.reason == "ENTRY_BEFORE_NATIVE_FILL_OR_AMBIGUOUS_LOT"
    assert r.realised_net is None


def _split_no_exit(early_qty: Decimal) -> object:
    b = bundle()
    original_exit = next(c for c in b.cashflows if c.flow_id == "exit-a")
    half_minute_after_first_fill = at(2, 10, 1) + timedelta(seconds=30)
    early = replace(original_exit, flow_id="exit-early", position_delta=-early_qty,
                    amount_susq=early_qty, issuer_at=half_minute_after_first_fill,
                    received_at=half_minute_after_first_fill + timedelta(seconds=1))
    later_qty = D("1") - early_qty
    later = replace(original_exit, flow_id="exit-late", position_delta=-later_qty,
                    amount_susq=later_qty)
    return replace(b, cashflows=tuple(
        c for c in b.cashflows if c.flow_id != "exit-a"
    ) + (early, later))


def test_valid_partial_no_disposal_before_second_fill_keeps_cash_conservation() -> None:
    r = estimate(_split_no_exit(D("0.25")))
    assert r.realised_net == D("0.89")
    assert r.verdict == "SYNTHETIC_OR_STRUCTURAL_ONLY_NOT_SOURCE_ADMISSION"
    assert r.source_admitted is False and r.verified_eligible_maker_denominator is None


def test_over_disposal_before_second_fill_denied_despite_final_qty_balance() -> None:
    r = estimate(_split_no_exit(D("0.5")))
    assert r.reason == "DISPOSAL_PRECEDES_AVAILABLE_NATIVE_FILLED_LOT"
    assert r.realised_net is None and r.holdout_cluster_ci95 is None


def test_same_clock_fill_and_disposal_without_venue_sequence_denied() -> None:
    b = bundle()
    cash = tuple(
        replace(c, issuer_at=at(2, 10, 2), received_at=at(2, 10, 2) + timedelta(seconds=1))
        if c.flow_id == "exit-a" else c for c in b.cashflows
    )
    r = estimate(replace(b, cashflows=cash))
    assert r.reason == "DISPOSAL_PRECEDES_AVAILABLE_NATIVE_FILLED_LOT"
    assert r.realised_net is None


def _markout(seconds: int, bid: str = "0.60") -> QuoteMarkout:
    filled_at = at(2, 10, 1)
    later = filled_at + timedelta(seconds=seconds)
    return QuoteMarkout("fill-a1", seconds, later, later, filled_at,
                        D(bid), D("0.70"), "INVENTED-BOOK",
                        D("2"), D("2"))


def test_same_horizon_identical_markout_replay_refused_without_double_count() -> None:
    b = bundle()
    mark = _markout(5)
    r = estimate(replace(b, markouts=(mark, mark),
                         proofs=(*b.proofs, proof("quote_markouts"))))
    assert r.reason == "DUPLICATE_FILL_HORIZON_MARKOUT"
    assert r.verdict == "NOT_ESTIMABLE"
    assert r.realised_net is None and r.descriptive_markout_count == 0


def test_same_horizon_conflicting_price_replay_refused() -> None:
    b = bundle()
    r = estimate(replace(b, markouts=(_markout(5), _markout(5, "0.55")),
                         proofs=(*b.proofs, proof("quote_markouts"))))
    assert r.reason == "DUPLICATE_FILL_HORIZON_MARKOUT"
    assert r.realised_net is None and r.descriptive_markout_count == 0


def test_distinct_five_and_thirty_second_horizons_remain_descriptive_only() -> None:
    b = bundle()
    r = estimate(replace(b, markouts=(_markout(5), _markout(30)),
                         proofs=(*b.proofs, proof("quote_markouts"))))
    assert r.descriptive_markout_count == 2
    assert r.realised_net == D("0.89")
    assert r.source_admitted is False
    assert r.paper_admitted is False
    assert r.verified_conservative_alpha_lower_bound is None
