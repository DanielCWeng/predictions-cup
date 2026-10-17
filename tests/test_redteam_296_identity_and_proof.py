"""Independent Red Team strict invented journal-adapter identity and proof falsifiers.

Reviewer-only negative fixtures against immutable #296. No original competition
records, private rows, filesystem, sockets, or production code are required.
Expected strict failures MUST NOT be waived/xfail'd to green the branch.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import replace

from predictions_cup.research.journal_maker_adapter import (
    EnvelopeRow,
    EventRow,
    FillDedupeRow,
    normalise_journal,
)


def _env(op: str, *, tournament: str = "test-tourney") -> EnvelopeRow:
    raw = json.dumps({
        "market_id": "invented-market", "action": "BUY",
        "outcome_side": "NO", "quantity": "2",
    })
    return EnvelopeRow(
        tournament_id=tournament,
        logical_operation_id=op,
        operation_kind="single_placement",
        sink_mode="LIVE",
        payload_json=raw,
        payload_sha256=hashlib.sha256(raw.encode()).hexdigest(),
        intent_ids_json='["same-local-intent"]',
    )


def _ev(i: int, op: str, kind: str, *,
        native_order: str | None = None,
        native_fill: str | None = None) -> EventRow:
    return EventRow(
        event_id=i,
        tournament_id="test-tourney",
        logical_operation_id=op,
        logical_intent_id="same-local-intent",
        event_type=kind,
        source_timestamp="2026-10-05T10:00:00Z",
        exchange_order_id=native_order,
        fill_id=native_fill,
        quantity="2",
        price="0.4",
        detail_json=json.dumps({
            "market_id": "invented-market",
            "action": "BUY", "outcome_side": "NO",
        }),
        strategy_family="MAKE",
    )


def _no_economic_promotion(result: object) -> None:
    # Deliberately use a loosely typed helper only to avoid changing production
    # interfaces; these properties are read directly on every concrete result.
    from predictions_cup.research.journal_maker_adapter import AdapterResult
    assert isinstance(result, AdapterResult)
    assert result.verdict == "MISSING_PROOF"
    assert result.eligible_maker_denominator is None
    assert result.locked_bundle is None
    assert result.realised_net is None
    assert result.conservative_ci is None
    assert result.source_admitted is False
    assert result.paper_admitted is False


def test_local_strategy_and_faked_fee_terminal_event_never_certify_cash() -> None:
    env = _env("op-one")
    sub = _ev(1, "op-one", "SUBMISSION")
    ack = _ev(2, "op-one", "ACK", native_order="native-A")
    terminal = replace(
        _ev(3, "op-one", "CANCEL_ACK", native_order="native-A"),
        terminal_status="CONFIRMED",
        detail_json='{"paid_fee_susq":"0","issuer_certified":true}',
    )
    r = normalise_journal((env,), (sub, ack, terminal), ())
    assert len(r.candidates) == 1
    assert r.candidates[0].verified_zero_fill is None
    assert r.candidates[0].evidence_holes.native_liquidity_role is None
    assert r.candidates[0].evidence_holes.paid_fee_susq is None
    _no_economic_promotion(r)


def test_distinct_operations_reusing_local_intent_must_not_share_native_order() -> None:
    # Distinct original (tournament, operation, intent) keys; no valid issuer
    # evidence may collapse both into one purported exchange-native order.
    envs = (_env("op-one"), _env("op-two"))
    events = (
        _ev(1, "op-one", "SUBMISSION"),
        _ev(2, "op-one", "ACK", native_order="SAME-NATIVE-ORDER"),
        _ev(3, "op-two", "SUBMISSION"),
        _ev(4, "op-two", "ACK", native_order="SAME-NATIVE-ORDER"),
    )
    r = normalise_journal(envs, events, ())
    _no_economic_promotion(r)
    assert r.candidates == (), "COMPOSITE_ORIGINAL_INTENT_COLLISION_ACCEPTED"
    assert any("NATIVE_ORDER_SHARED" in x for x in r.rejected_rows)


def test_fill_native_order_mismatch_must_not_attach_to_ack_intent() -> None:
    env = _env("op-one")
    events = (
        _ev(1, "op-one", "SUBMISSION"),
        _ev(2, "op-one", "ACK", native_order="native-A"),
        _ev(3, "op-one", "AUTHORITATIVE_FILL",
            native_order="native-B", native_fill="fill-B"),
    )
    r = normalise_journal((env,), events, ())
    _no_economic_promotion(r)
    assert r.candidates == (), "NATIVE_FILL_B_ORDER_JOIN_ACCEPTED"
    assert r.rejected_rows


def test_fill_dedupe_alias_to_submission_is_not_native_fill() -> None:
    env = _env("op-one")
    events = (
        _ev(1, "op-one", "SUBMISSION"),
        _ev(2, "op-one", "ACK", native_order="native-A"),
    )
    alias = FillDedupeRow(dedupe_key="fake-native-fill-alias", event_id=1)
    r = normalise_journal((env,), events, (alias,))
    _no_economic_promotion(r)
    assert r.candidates == (), "NON_FILL_ALIAS_VALIDATED_AS_FILL_DEDUPE"
    assert "INVALID_ALIAS_REFERENCE" in r.rejected_rows


def test_empty_envelope_payload_cannot_be_repaired_by_local_event_detail() -> None:
    env = _env("op-one")
    empty = "{}"
    env = replace(
        env, payload_json=empty,
        payload_sha256=hashlib.sha256(empty.encode()).hexdigest(),
    )
    r = normalise_journal((env,), (_ev(1, "op-one", "SUBMISSION"),), ())
    _no_economic_promotion(r)
    assert r.candidates == (), "MISSING_IMMUTABLE_ENVELOPE_ORDER_FIELDS_ACCEPTED"
    assert r.rejected_rows


def test_bad_claimed_publisher_clock_does_not_promote_receipt_utc() -> None:
    env = _env("op-one")
    sub = replace(
        _ev(1, "op-one", "SUBMISSION"),
        source_timestamp="2026-10-05T10:00:00", observed_monotonic_ns=42,
    )
    r = normalise_journal((env,), (sub,), ())
    _no_economic_promotion(r)
    assert len(r.candidates) == 1
    a = r.candidates[0]
    assert a.purported_publisher_at is None
    assert a.receipt_at is None
    assert a.evidence_holes.issuer_receipt_clock_verified is None
