"""Invented-only adversarial journal projection and synthetic bridge tests."""
from __future__ import annotations

import hashlib
import json
import socket
from dataclasses import replace
from decimal import Decimal

import pytest

from predictions_cup.research.journal_maker_adapter import (
    EnvelopeRow,
    EventRow,
    FillDedupeRow,
    invented_fixture_locked_bundle,
    normalise_journal,
)
from predictions_cup.research.locked_maker_economics import estimate


def envelope(kind: str = "single_placement", ids: tuple[str, ...] = ("a",),
             mode: str = "LIVE") -> EnvelopeRow:
    order = {"market_id": "m", "action": "BUY",
             "outcome_side": "NO", "quantity": "2"}
    payload: dict[str, object] = {}
    if kind == "single_placement":
        payload.update(order)
    else:
        field = "orders" if kind == "best_effort_batch" else "legs"
        payload[field] = [order] * len(ids)
    raw = json.dumps(payload)
    return EnvelopeRow("t", "op", kind, mode, raw,
                       hashlib.sha256(raw.encode()).hexdigest(), json.dumps(ids))


def event(i: int, kind: str, intent: str = "a", order: str | None = None,
          fill: str | None = None, qty: str | None = "2",
          price: str | None = "0.4") -> EventRow:
    details = json.dumps({"market_id": "m", "outcome_side": "NO", "action": "BUY"})
    return EventRow(i, "t", "op", intent, kind, "2026-10-05T10:00:00Z",
                    123, order, "exchange", fill, qty, price, None, details, "MAKE")


def rows() -> tuple[tuple[EnvelopeRow, ...], tuple[EventRow, ...],
                    tuple[FillDedupeRow, ...]]:
    return ((envelope(),),
            (event(1, "SUBMISSION"), event(2, "ACK", order="order1"),
             event(3, "REALTIME_FILL", order="order1", fill="fill1"),
             event(4, "AUTHORITATIVE_FILL", order="order1", fill="fill1"),
             event(5, "CANCEL_ACK", order="order1")),
            (FillDedupeRow("sig:key", 3), FillDedupeRow("content:key", 3)))


def test_local_make_ack_cancel_and_aliases_never_create_native_proof() -> None:
    r = normalise_journal(*rows())
    assert r.verdict == "MISSING_PROOF" and len(r.candidates) == 1
    a = r.candidates[0]
    assert a.native_fill_ids_observed == ("fill1",)
    assert a.possible_native_order_id == "order1"
    assert a.local_strategy_label == "MAKE"
    assert a.evidence_holes.native_liquidity_role is None
    assert a.evidence_holes.paid_fee_susq is None
    assert a.evidence_holes.later_lot_exit_cash_susq is None
    assert a.verified_zero_fill is None and a.receipt_at is None
    assert r.eligible_maker_denominator is None and r.realised_net is None
    assert r.conservative_ci is None and r.locked_bundle is None
    assert r.source_admitted is False and r.paper_admitted is False


def test_uncertain_no_fill_summary_and_no_ack_are_not_zero_fills() -> None:
    es, evs, _ = rows()
    minimal = (evs[0], event(6, "UNCERTAIN"), event(7, "FILL_SUMMARY", fill="fake"))
    a = normalise_journal(es, minimal, ()).candidates[0]
    assert a.native_fill_ids_observed == ()
    assert a.verified_zero_fill is None
    assert a.censored_reason == "UNCERTAIN"


def test_multi_intent_batch_and_atomic_are_two_distinct_candidates() -> None:
    es, evs, _ = rows()
    for kind in ("best_effort_batch", "atomic_multi_leg"):
        env = envelope(kind, ("a", "b"))
        good = (evs[0], event(8, "SUBMISSION", intent="b"))
        r = normalise_journal((env,), good, ())
        assert len(r.candidates) == 2
        assert r.candidates[0].attempt_key != r.candidates[1].attempt_key
        assert r.eligible_maker_denominator is None
        bad = normalise_journal((env,), good[:1], ())
        assert bad.candidates == () and bad.rejected_rows


def test_shadow_and_cancellation_operations_not_maker_trials() -> None:
    es, evs, aliases = rows()
    for env in (envelope(mode="SHADOW"), envelope(kind="single_cancellation")):
        assert normalise_journal((env,), evs, aliases).candidates == ()


@pytest.mark.parametrize("problem", [
    "hash", "dup_intent", "short_batch", "bad_qty", "dup_event",
    "foreign_op", "foreign_tournament", "wrong_intent", "dup_submission",
    "bad_json", "extra_ack_id", "conflicting_fill",
])
def test_fabricated_or_ambiguous_journal_denied(problem: str) -> None:
    es, evs, aliases = rows()
    e = es[0]
    if problem == "hash":
        es = (replace(e, payload_sha256="x"),)
    elif problem == "dup_intent":
        es = (replace(e, intent_ids_json='["a","a"]'),)
    elif problem == "short_batch":
        es = (replace(e, operation_kind="best_effort_batch"),)
    elif problem == "bad_qty":
        evs = (replace(evs[0], quantity=None), *evs[1:])
    elif problem == "dup_event":
        evs = (*evs, evs[0])
    elif problem == "foreign_op":
        evs = (*evs, replace(evs[0], event_id=10, logical_operation_id="missing"))
    elif problem == "foreign_tournament":
        evs = (replace(evs[0], tournament_id="other"), *evs[1:])
    elif problem == "wrong_intent":
        evs = (*evs, replace(evs[1], event_id=10, logical_intent_id="ghost"))
    elif problem == "dup_submission":
        evs = (*evs, replace(evs[0], event_id=10))
    elif problem == "bad_json":
        raw = '{"market_id":"m","action":"BUY","outcome_side":"NO","quantity":"2","bad":NaN}'
        es = (replace(e, payload_json=raw,
                      payload_sha256=hashlib.sha256(raw.encode()).hexdigest()),)
    elif problem == "extra_ack_id":
        evs = (*evs, event(10, "ACK", order="different"))
    else:
        evs = (*evs[:3], replace(evs[3], price="0.99"), *evs[4:])
    r = normalise_journal(es, evs, aliases)
    assert r.candidates == () and r.rejected_rows
    assert r.locked_bundle is None and r.realised_net is None


def test_duplicate_alias_keys_and_orphan_aliases_rejected() -> None:
    es, evs, aliases = rows()
    for bad in ((*aliases, aliases[0]), (*aliases, FillDedupeRow("x", 999))):
        r = normalise_journal(es, evs, bad)
        assert r.rejected_rows == ("INVALID_ALIAS_REFERENCE",)


def test_native_order_must_not_join_two_distinct_intents() -> None:
    env = envelope("best_effort_batch", ("a", "b"))
    evs = (event(1, "SUBMISSION"), event(2, "ACK", order="one"),
           event(3, "SUBMISSION", intent="b"),
           event(4, "ACK", intent="b", order="one"))
    r = normalise_journal((env,), evs, ())
    assert "NATIVE_ORDER_SHARED_BY_DISTINCT_INTENTS" in r.rejected_rows[0]


def test_frozen_estimator_positive_invented_arithmetic_not_alpha() -> None:
    r = estimate(invented_fixture_locked_bundle())
    assert r.realised_net == Decimal("0.18")
    assert r.verdict == "SYNTHETIC_OR_STRUCTURAL_ONLY_NOT_SOURCE_ADMISSION"
    assert r.verified_eligible_maker_denominator is None
    assert r.verified_conservative_alpha_lower_bound is None
    assert r.source_admitted is False and r.paper_admitted is False
    assert r.network_requests == 0


def test_no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("network forbidden")
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    normalise_journal(*rows())
    estimate(invented_fixture_locked_bundle())
