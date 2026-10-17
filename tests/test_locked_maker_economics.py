"""Pure synthetic adversarial tests; no real fills, account data or network."""
from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Literal, cast

import pytest

from predictions_cup.research.locked_maker_economics import (
    InventoryCashflow,
    LockedBundle,
    MakerAttempt,
    OrderEvent,
    QuoteMarkout,
    SourceProof,
    TakerControl,
    VenueFill,
    estimate,
)

D = Decimal


def at(day: int, hour: int = 10, minute: int = 0) -> datetime:
    return datetime(2026, 10, day, hour, minute, tzinfo=UTC)


def clock(day: int, hour: int = 10, minute: int = 0) -> tuple[datetime, datetime]:
    x = at(day, hour, minute)
    return x, x + timedelta(seconds=1)


def proof(table: str) -> SourceProof:
    raw = ("INVENTED-SYNTHETIC-" + table).encode()
    return SourceProof(table, raw, hashlib.sha256(raw).hexdigest(),
                       "SYNTHETIC-NOT-ISSUER-ATTESTED",
                       "SYNTHETIC-NOT-LEGAL-CLEARANCE",
                       "SYNTHETIC-NOT-CUSTODIAN-ATTESTED", True)


def bundle() -> LockedBundle:
    attempts = (
        MakerAttempt("a", "native-a", "market-a", "intent-a", *clock(2), "NO",
                     "BUY", D("1"), True, "SYNTHETIC"),
        MakerAttempt("b", "native-b", "market-b", "intent-b", *clock(5), "YES",
                     "BUY", D("2"), True, "SYNTHETIC"),
        MakerAttempt("c", "native-c", "market-c", "intent-c", *clock(3), "YES",
                     "BUY", D("1"), True, "SYNTHETIC"),
        MakerAttempt("d", None, "market-d", "intent-d", *clock(3, 11), "YES",
                     "BUY", D("1"), True, "SYNTHETIC"),
    )
    events = (
        OrderEvent("ack-a", "a", "ACK", *clock(2)),
        OrderEvent("terminal-a", "a", "TERMINAL", *clock(2, 11), True),
        OrderEvent("ack-b", "b", "ACK", *clock(5)),
        OrderEvent("terminal-b", "b", "TERMINAL", *clock(5, 11), True),
        OrderEvent("ack-c", "c", "ACK", *clock(3)),
        OrderEvent("cancel-c", "c", "CANCEL_ACK", *clock(3, 11), True),
        OrderEvent("reject-d", "d", "REJECTED", *clock(3, 11), True),
    )
    fills = (
        VenueFill("fill-a1", "a", "native-a", "market-a", *clock(2, 10, 1),
                  D("0.25"), D("0.65"), "MAKER", "NATIVE-SYNTHETIC"),
        VenueFill("fill-a2", "a", "native-a", "market-a", *clock(2, 10, 2),
                  D("0.75"), D("0.65"), "MAKER", "NATIVE-SYNTHETIC"),
        VenueFill("fill-b1", "b", "native-b", "market-b", *clock(5, 10, 1),
                  D("1"), D("0.45"), "MAKER", "NATIVE-SYNTHETIC"),
    )
    flows = (
        InventoryCashflow("entry-a1", "a", "fill-a1", "ENTRY", D("-0.1625"),
                          D("0.25"), *clock(2, 10, 1), "SYNTHETIC"),
        InventoryCashflow("entry-a2", "a", "fill-a2", "ENTRY", D("-0.4875"),
                          D("0.75"), *clock(2, 10, 2), "SYNTHETIC"),
        InventoryCashflow("fee-a", "a", None, "FEE", D("-0.02"), D("0"),
                          *clock(2, 10, 2), "SYNTHETIC"),
        InventoryCashflow("rebate-a", "a", None, "REBATE", D("0.01"), D("0"),
                          *clock(2, 10, 3), "SYNTHETIC"),
        InventoryCashflow("exit-a", "a", None, "EXIT", D("1"), D("-1"),
                          *clock(3, 12), "SYNTHETIC"),
        InventoryCashflow("entry-b", "b", "fill-b1", "ENTRY", D("-0.45"),
                          D("1"), *clock(5, 10, 1), "SYNTHETIC"),
        # Explicit actual FEE=0; absent fee must never silently mean zero.
        InventoryCashflow("fee-b", "b", None, "FEE", D("0"), D("0"),
                          *clock(5, 10, 2), "SYNTHETIC"),
        # Exit/settlement AFTER the entry cohort Oct-7 cutoff is valid when joined.
        InventoryCashflow("settlement-b", "b", None, "SETTLEMENT", D("1"),
                          D("-1"), *clock(8), "SYNTHETIC"),
    )
    return LockedBundle(tuple(proof(t) for t in (
        "venue_fills", "order_lifecycle", "inventory_cashflows",
    )), attempts, events, fills, tuple(
        replace(c, source_class="PAID_ACTUAL",
                resolution_rules_reference="SYNTHETIC-RULES"
                if c.kind == "SETTLEMENT" else None) for c in flows
    ))


def test_synthetic_realised_gross_net_fees_split_and_late_settlement() -> None:
    result = estimate(bundle())
    assert result.verdict == "SYNTHETIC_OR_STRUCTURAL_ONLY_NOT_SOURCE_ADMISSION"
    assert result.reason == "REAL_WORLD_PROVENANCE_EXTERNAL"
    assert (result.attempts, result.fully_filled, result.partially_filled,
            result.zero_filled, result.rejected, result.censored) == (4, 1, 1, 1, 1, 0)
    assert result.fill_count == 3 and result.fill_quantity == D("2")
    assert result.realised_gross == D("0.90")
    assert result.paid_fees == D("-0.02") and result.rebates == D("0.01")
    assert result.realised_net == D("0.89")
    assert result.training_net == D("0.34")
    assert result.holdout_net == D("0.55")
    assert result.outstanding_quantity == D("0")
    assert result.holdout_cluster_ci95 is None
    assert result.taker_reference_difference is None
    assert result.observed_position_share_days is not None
    assert result.observed_cash_turnover_susq == D("3.10")
    assert result.capital_reservation_susq_days is None
    assert result.no_quote_incremental_new_order_cash_susq == D('0')


def test_zero_fill_versus_never_filled_and_censored_terminal() -> None:
    b = bundle()
    c = next(e for e in b.events if e.event_id == "cancel-c")
    b2 = replace(b, events=tuple(
        replace(e, native_terminal_confirmed=False) if e == c else e for e in b.events
    ))
    result = estimate(b2)
    assert result.verdict == "NOT_ESTIMABLE"
    assert result.realised_net is None
    assert result.reason == "CENSORED_MAKER_ATTEMPTS_NOT_RESOLVED"


def test_uncertain_request_is_not_cancel_ack() -> None:
    b = bundle()
    events = tuple(replace(e, status="UNCERTAIN", native_terminal_confirmed=False)
                   if e.event_id == "cancel-c" else e for e in b.events)
    result = estimate(replace(b, events=events))
    assert result.reason == "CENSORED_MAKER_ATTEMPTS_NOT_RESOLVED"
    assert result.realised_net is None


@pytest.mark.parametrize("fee", [None, "DROP"])
def test_missing_fee_not_zero(fee: str | None) -> None:
    b = bundle()
    cash = tuple(
        replace(c, amount_susq=None) if c.flow_id == "fee-b" and fee is None else c
        for c in b.cashflows
        if not (c.flow_id == "fee-b" and fee == "DROP")
    )
    expected = (
        "MISSING_ACTUAL_FEE_OR_EXIT_CASHFLOW" if fee is None
        else "MISSING_REAL_FEE_ENTRY_OR_DISPOSAL"
    )
    assert estimate(replace(b, cashflows=cash)).reason == expected


def test_unsettled_inventory_is_censored_not_realised_profit() -> None:
    b = bundle()
    cash = tuple(c for c in b.cashflows if c.flow_id != "settlement-b")
    r = estimate(replace(b, cashflows=cash))
    assert r.realised_net is None and r.reason == "MISSING_REAL_FEE_ENTRY_OR_DISPOSAL"


def test_held_no_signed_cash_and_share_conservation() -> None:
    b = bundle()
    cash = tuple(replace(c, amount_susq=D("0.1625")) if c.flow_id == "entry-a1" else c
                 for c in b.cashflows)
    r = estimate(replace(b, cashflows=cash))
    assert r.reason == "POSITION_OR_PARTIAL_FILL_CASH_CONSERVATION_FAIL"
    assert r.realised_net is None


def test_more_partial_fills_than_original_order_size() -> None:
    b = bundle()
    fills = tuple(replace(f, quantity=D("1.25")) if f.fill_id == "fill-a1" else f
                  for f in b.fills)
    assert estimate(replace(b, fills=fills)).reason == "PARTIAL_FILL_EXCEEDS_ORDER"


def test_duplicate_native_fill_ids() -> None:
    b = bundle()
    assert estimate(replace(b, fills=(*b.fills, b.fills[0]))).reason == "DUPLICATE_NATIVE_FILL"


@pytest.mark.parametrize("role,reference", [("TAKER", "NATIVE-SYNTHETIC"), ("MAKER", None)])
def test_maker_label_cannot_substitute_native_role(role: str, reference: str | None) -> None:
    b = bundle()
    f = replace(b.fills[0], native_role=cast(Literal["MAKER", "TAKER", "UNKNOWN"], role),
                native_role_reference=reference)
    assert estimate(replace(b, fills=(f, *b.fills[1:]))).reason == (
        "NATIVE_MAKER_ROLE_OR_FILL_IDENTITY_NOT_PROVEN"
    )


def test_hash_mismatch_and_rights_denial() -> None:
    b = bundle()
    for p in (replace(b.proofs[0], original_sha256="0" * 64),
              replace(b.proofs[0], use_rights_reference=None),
              replace(b.proofs[0], complete=False)):
        r = estimate(replace(b, proofs=(p, *b.proofs[1:])))
        assert r.verdict == "NOT_ESTIMABLE"
        assert r.reason == "SOURCE_HASH_COMPLETENESS_RIGHTS_OR_ISSUER_NOT_PROVEN"


def test_noncausal_fill_receipt_rejected() -> None:
    b = bundle()
    bad = replace(b.fills[0], received_at=at(2, 9))
    assert estimate(replace(b, fills=(bad, *b.fills[1:]))).reason == (
        "NATIVE_MAKER_ROLE_OR_FILL_IDENTITY_NOT_PROVEN"
    )


def test_training_outcome_cannot_leak_across_frozen_holdout_boundary() -> None:
    b = bundle()
    replacement = replace(next(c for c in b.cashflows if c.flow_id == "exit-a"),
                          issuer_at=at(5), received_at=at(5) + timedelta(seconds=1))
    flows = tuple(replacement if c.flow_id == "exit-a" else c for c in b.cashflows)
    assert estimate(replace(b, cashflows=flows)).reason == (
        "CHRONOLOGICAL_HOLDOUT_OUTCOME_LEAKAGE"
    )


def test_control_references_need_original_market_day_and_quantity_match() -> None:
    b = bundle()
    valid = TakerControl("b", "market-b", "2026-10-05", D("0.20"), D("1"),
                         "SYNTHETIC-MATCHED-CONTROL")
    r = estimate(replace(b, taker_controls=(valid,)))
    assert r.taker_reference_difference == D("0.35")
    assert r.realised_net == D("0.89")
    bad = replace(valid, market_id="other")
    assert estimate(replace(b, taker_controls=(bad,))).reason == "UNMATCHED_TAKER_CONTROL"


def test_markouts_are_separate_from_actual_cash_and_require_original_source() -> None:
    b = bundle()
    horizon = at(2, 10, 1) + timedelta(seconds=5)
    m = QuoteMarkout("fill-a1", 5, horizon, horizon, at(2, 10, 1),
                     D("0.60"), D("0.70"), "SYNTHETIC", D("2"), D("2"))
    b2 = replace(b, markouts=(m,))
    assert estimate(b2).reason == "UNATTESTED_MARKOUT_SOURCE"
    b3 = replace(b2, proofs=(*b.proofs, proof("quote_markouts")))
    r = estimate(b3)
    assert r.descriptive_markout_count == 1
    assert r.descriptive_executable_markouts == ((5, D("-0.0125")),)
    assert r.realised_net == estimate(b).realised_net
    assert estimate(replace(b3, markouts=(replace(m, issuer_at=at(2, 10, 2)),))).reason == (
        "UNVERIFIED_CAUSAL_EXECUTABLE_MARKOUT"
    )


def test_missing_denominator_or_unclear_eligibility_fails_closed() -> None:
    b = bundle()
    a = replace(b.attempts[0], native_eligibility_reference=None)
    assert estimate(replace(b, attempts=(a, *b.attempts[1:]))).reason == (
        "INCOMPLETE_MAKER_DENOMINATOR_OR_CLOCK"
    )


def test_redteam_midpoint_exit_cannot_be_realised_cash() -> None:
    b = bundle()
    cash = tuple(replace(c, source_class="MIDPOINT_MARKOUT")
                 if c.flow_id == "exit-a" else c for c in b.cashflows)
    r = estimate(replace(b, cashflows=cash))
    assert r.reason == "INVALID_CASHFLOW_OR_ORPHAN_FILL"
    assert r.realised_net is None
    assert r.verified_eligible_maker_denominator is None
    assert r.verified_conservative_alpha_lower_bound is None


def test_redteam_missing_settlement_rules_must_deny() -> None:
    b = bundle()
    cash = tuple(replace(c, resolution_rules_reference=None)
                 if c.flow_id == "settlement-b" else c for c in b.cashflows)
    r = estimate(replace(b, cashflows=cash))
    assert r.reason == "INVALID_CASHFLOW_OR_ORPHAN_FILL"
    assert r.realised_net is None


@pytest.mark.parametrize("bid_depth,ask_depth", [
    ("0", "2"), ("2", "0"), ("0.10", "2"), ("2", "0.10"),
])
def test_redteam_same_price_zero_or_insufficient_depth_not_executable(
    bid_depth: str, ask_depth: str
) -> None:
    b = bundle()
    horizon = at(2, 10, 1) + timedelta(seconds=5)
    m = QuoteMarkout("fill-a1", 5, horizon, horizon, at(2, 10, 1),
                     D("0.60"), D("0.70"), "SYNTHETIC", D(bid_depth), D(ask_depth))
    r = estimate(replace(b, markouts=(m,), proofs=(*b.proofs, proof("quote_markouts"))))
    assert r.reason == "UNVERIFIED_CAUSAL_EXECUTABLE_MARKOUT"
    assert r.realised_net is None


def test_redteam_forged_source_claim_is_not_data_or_alpha_admission() -> None:
    b = bundle()
    # Even a hash-consistent self-asserted issuer/custodian string is not an
    # independently verified issuer signature, legal licence, or native role.
    fake = replace(b.proofs[0], issuer_reference="FORGED-TRUSTED-ISSUER")
    r = estimate(replace(b, proofs=(fake, *b.proofs[1:])))
    assert r.verdict == "SYNTHETIC_OR_STRUCTURAL_ONLY_NOT_SOURCE_ADMISSION"
    assert r.source_admitted is False and r.paper_admitted is False
    assert r.verified_eligible_maker_denominator is None
    assert r.verified_conservative_alpha_lower_bound is None
    assert r.network_requests == 0


def test_redteam_wrong_market_or_order_mapping_rejected() -> None:
    b = bundle()
    first = b.fills[0]
    for replacement in (replace(first, market_id="WRONG-MARKET"),
                        replace(first, order_id="WRONG-ORDER")):
        r = estimate(replace(b, fills=(replacement, *b.fills[1:])))
        assert r.reason == "NATIVE_MAKER_ROLE_OR_FILL_IDENTITY_NOT_PROVEN"
        assert r.realised_net is None


def test_redteam_cash_price_not_invented_from_fill_fails() -> None:
    b = bundle()
    cash = tuple(replace(c, amount_susq=D("-0.10"))
                 if c.flow_id == "entry-a1" else c for c in b.cashflows)
    r = estimate(replace(b, cashflows=cash))
    assert r.reason == "POSITION_OR_PARTIAL_FILL_CASH_CONSERVATION_FAIL"
    assert r.realised_net is None


def test_redteam_no_sockets_even_on_structural_success(monkeypatch: pytest.MonkeyPatch) -> None:
    import socket

    def deny(*args: object, **kwargs: object) -> None:
        raise AssertionError("network access forbidden")

    monkeypatch.setattr(socket.socket, "connect", deny)
    monkeypatch.setattr(socket, "create_connection", deny)
    result = estimate(bundle())
    assert result.network_requests == 0
    assert result.source_admitted is False
    assert result.paper_admitted is False
