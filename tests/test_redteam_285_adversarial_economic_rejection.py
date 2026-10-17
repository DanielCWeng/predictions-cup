"""Independent Red Team tests-only falsifiers for frozen Capital #285.

Every row in this file is invented. Intentionally strict failing assertions
are regression evidence, NOT an instruction to weaken or waive a source gate.
No network, host, exchange, journal, or original competition data.
"""
from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from predictions_cup.research.locked_maker_economics import (
    InventoryCashflow,
    LockedBundle,
    MakerAttempt,
    OrderEvent,
    QuoteMarkout,
    SourceProof,
    VenueFill,
    estimate,
)

D = Decimal
T = datetime(2026, 10, 5, 10, 0, tzinfo=UTC)


def _clock(t: datetime) -> tuple[datetime, datetime]:
    return t, t + timedelta(milliseconds=200)


def _proof(table: str) -> SourceProof:
    raw = ("FAKE-ORIGINAL-BYTES:" + table).encode()
    return SourceProof(
        table, raw, hashlib.sha256(raw).hexdigest(),
        "SIGNED-LOOKING-ISSUER-NOT-ATTESTED",
        "RIGHTS-LOOKING-STRING-NOT-ATTESTED",
        "CUSTODIAN-LOOKING-STRING-NOT-ATTESTED", True,
    )


def _bundle() -> LockedBundle:
    win = MakerAttempt(
        "win", "native-win", "m-no", "event-win", *_clock(T),
        "NO", "BUY", D("1"), True, "FAKE-NATIVE-MAKER-ELIGIBILITY",
    )
    empty_at = T + timedelta(hours=1)
    empty = MakerAttempt(
        "empty", "native-empty", "m-yes", "event-empty", *_clock(empty_at),
        "YES", "BUY", D("1"), True, "FAKE-NATIVE-MAKER-ELIGIBILITY",
    )
    events = (
        OrderEvent("ack-win", "win", "ACK", *_clock(T + timedelta(seconds=1))),
        OrderEvent(
            "term-win", "win", "TERMINAL",
            *_clock(T + timedelta(minutes=20)), True,
        ),
        OrderEvent(
            "ack-empty", "empty", "ACK",
            *_clock(empty_at + timedelta(seconds=1)),
        ),
        OrderEvent(
            "term-empty", "empty", "CANCEL_ACK",
            *_clock(empty_at + timedelta(minutes=20)), True,
        ),
    )
    filled_at = T + timedelta(minutes=5)
    fills = (
        VenueFill(
            "fill-win", "win", "native-win", "m-no", *_clock(filled_at),
            D("1"), D("0.40"), "MAKER", "SIGNED-LOOKING-ROLE",
        ),
    )
    cashflows = (
        InventoryCashflow(
            "entry", "win", "fill-win", "ENTRY", D("-0.40"), D("1"),
            *_clock(filled_at), "FAKE-CASH", "PAID_ACTUAL",
        ),
        InventoryCashflow(
            "fee", "win", "fill-win", "FEE", D("-0.01"), D("0"),
            *_clock(filled_at), "FAKE-FEE", "PAID_ACTUAL",
        ),
        InventoryCashflow(
            "settle", "win", None, "SETTLEMENT", D("1"), D("-1"),
            *_clock(datetime(2026, 10, 8, tzinfo=UTC)),
            "FAKE-SETTLEMENT", "PAID_ACTUAL", "FAKE-NO-RULES",
        ),
    )
    return LockedBundle(
        tuple(_proof(x) for x in (
            "venue_fills", "order_lifecycle", "inventory_cashflows",
        )),
        (win, empty), events, fills, cashflows,
    )


def _assert_never_certified(result: object) -> None:
    # Local structural output is intentionally not an issuer admission.
    assert getattr(result, "source_admitted") is False
    assert getattr(result, "paper_admitted") is False
    assert getattr(result, "verified_eligible_maker_denominator") is None
    assert getattr(result, "verified_conservative_alpha_lower_bound") is None


def test_forged_source_hash_issuer_rights_role_and_cash_are_only_structural() -> None:
    b = _bundle()
    r = estimate(b)
    assert r.verdict == "SYNTHETIC_OR_STRUCTURAL_ONLY_NOT_SOURCE_ADMISSION"
    assert r.realised_net == D("0.59")
    assert r.zero_filled == 1
    _assert_never_certified(r)


def test_censor_original_zero_fill_attempt_cannot_establish_native_denominator() -> None:
    b = _bundle()
    # Drop the whole native zero-fill order, including its real lifecycle.
    trimmed = replace(
        b,
        attempts=(b.attempts[0],),
        events=tuple(e for e in b.events if e.attempt_id == "win"),
    )
    r = estimate(trimmed)
    assert r.verdict == "SYNTHETIC_OR_STRUCTURAL_ONLY_NOT_SOURCE_ADMISSION"
    assert r.realised_net == D("0.59")
    assert r.zero_filled == 0
    assert r.attempts == 1
    _assert_never_certified(r)


def test_fake_uncertain_then_fake_native_cancel_ack_remains_untrusted() -> None:
    b = _bundle()
    claimed_uncertain = OrderEvent(
        "unknown-empty", "empty", "UNCERTAIN",
        *_clock(T + timedelta(hours=1, minutes=10)),
    )
    r = estimate(replace(b, events=(*b.events, claimed_uncertain)))
    # Boolean native-terminal flag is an unverified caller assertion.
    assert r.verdict == "SYNTHETIC_OR_STRUCTURAL_ONLY_NOT_SOURCE_ADMISSION"
    assert r.censored == 0
    _assert_never_certified(r)


def test_fee_null_is_not_zero_and_fake_zero_fee_is_not_issuer_proof() -> None:
    b = _bundle()
    missing = tuple(
        replace(c, amount_susq=None) if c.flow_id == "fee" else c
        for c in b.cashflows
    )
    r_missing = estimate(replace(b, cashflows=missing))
    assert r_missing.verdict == "NOT_ESTIMABLE"
    assert r_missing.realised_net is None
    zero = tuple(
        replace(c, amount_susq=D("0")) if c.flow_id == "fee" else c
        for c in b.cashflows
    )
    r_zero = estimate(replace(b, cashflows=zero))
    assert r_zero.realised_net == D("0.60")
    _assert_never_certified(r_zero)


def test_same_price_zero_liquidity_does_not_inflate_descriptive_markout() -> None:
    b = _bundle()
    at_five = T + timedelta(minutes=5, seconds=5)
    zero = QuoteMarkout(
        "fill-win", 5, at_five, at_five, T,
        D("0.50"), D("0.60"), "FAKE-BOOK",
        D("0"), D("0"),
    )
    r = estimate(replace(
        b, markouts=(zero,), proofs=(*b.proofs, _proof("quote_markouts")),
    ))
    assert r.reason == "UNVERIFIED_CAUSAL_EXECUTABLE_MARKOUT"
    assert r.realised_net is None


def test_descriptive_markout_cannot_replace_missing_native_settlement() -> None:
    b = _bundle()
    at_five = T + timedelta(minutes=5, seconds=5)
    mark = QuoteMarkout(
        "fill-win", 5, at_five, at_five, T,
        D("0.50"), D("0.60"), "FAKE-BOOK", D("1"), D("1"),
    )
    no_settlement = replace(
        b,
        cashflows=tuple(c for c in b.cashflows if c.kind != "SETTLEMENT"),
        proofs=(*b.proofs, _proof("quote_markouts")),
        markouts=(mark,),
    )
    r = estimate(no_settlement)
    assert r.realised_net is None
    assert r.reason == "MISSING_REAL_FEE_ENTRY_OR_DISPOSAL"


def test_reject_native_settlement_before_its_entry_fill_strict_regression() -> None:
    b = _bundle()
    # Impossible chronology: settlement purportedly paid 3 minutes before fill.
    earlier = tuple(
        replace(c, issuer_at=T + timedelta(minutes=2),
                received_at=T + timedelta(minutes=2, milliseconds=200))
        if c.flow_id == "settle" else c for c in b.cashflows
    )
    r = estimate(replace(b, cashflows=earlier))
    assert r.verdict == "NOT_ESTIMABLE", (
        "PRE_FILL_SETTLEMENT_ACCEPTED_AS_STRUCTURAL_PROFIT"
    )
    assert r.realised_net is None


def test_reject_duplicate_same_fill_same_horizon_markout_strict_regression() -> None:
    b = _bundle()
    at_five = T + timedelta(minutes=5, seconds=5)
    mark = QuoteMarkout(
        "fill-win", 5, at_five, at_five, T,
        D("0.50"), D("0.60"), "FAKE-BOOK", D("1"), D("1"),
    )
    r = estimate(replace(
        b, markouts=(mark, mark),
        proofs=(*b.proofs, _proof("quote_markouts")),
    ))
    assert r.verdict == "NOT_ESTIMABLE", (
        "DUPLICATE_MARKOUT_HORIZON_CAN_DOUBLE_COUNT_DESCRIPTIVE_RETURNS"
    )
    assert r.descriptive_markout_count == 0
