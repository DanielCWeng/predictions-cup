"""Invented-only successor negatives; original Red Team #299 tests stay unchanged."""
from __future__ import annotations

import hashlib
import json
from dataclasses import replace

import pytest

from predictions_cup.research.journal_maker_adapter import (
    EnvelopeRow,
    EventRow,
    FillDedupeRow,
    normalise_journal,
)


def envelope(operation: str, *, missing: str | None = None) -> EnvelopeRow:
    fields = {"market_id": "invented-market", "action": "BUY",
              "outcome_side": "NO", "quantity": "2"}
    if missing is not None:
        del fields[missing]
    raw = json.dumps(fields, sort_keys=True)
    return EnvelopeRow("fake-tournament", operation, "single_placement", "LIVE",
                       raw, hashlib.sha256(raw.encode()).hexdigest(), '["local-i"]')


def event(identifier: int, operation: str, kind: str, *,
          order_id: str | None = None, fill_id: str | None = None) -> EventRow:
    return EventRow(
        identifier, "fake-tournament", operation, "local-i", kind,
        "2026-10-05T10:00:00Z", 20, order_id, "exchange", fill_id,
        "2", "0.4", None,
        json.dumps({"market_id": "invented-market", "action": "BUY",
                    "outcome_side": "NO"}), "MAKE",
    )


def test_retries_preserve_single_composite_native_order_and_fill() -> None:
    events = (
        event(1, "op-a", "SUBMISSION"),
        event(2, "op-a", "ACK", order_id="native-a"),
        event(3, "op-a", "ACK", order_id="native-a"),
        event(4, "op-a", "REALTIME_FILL", order_id="native-a", fill_id="fill-a"),
        event(5, "op-a", "AUTHORITATIVE_FILL", order_id="native-a", fill_id="fill-a"),
    )
    aliases = (FillDedupeRow("sig:a", 4), FillDedupeRow("content:a", 4))
    result = normalise_journal((envelope("op-a"),), events, aliases)
    assert len(result.candidates) == 1
    assert result.candidates[0].native_fill_ids_observed == ("fill-a",)
    assert result.eligible_maker_denominator is None
    assert result.realised_net is None and result.locked_bundle is None
    assert result.source_admitted is False and result.paper_admitted is False


def test_same_native_fill_id_cannot_join_distinct_composite_intents() -> None:
    events = (
        event(1, "op-a", "SUBMISSION"),
        event(2, "op-a", "ACK", order_id="native-a"),
        event(3, "op-a", "REALTIME_FILL", order_id="native-a", fill_id="shared"),
        event(4, "op-b", "SUBMISSION"),
        event(5, "op-b", "ACK", order_id="native-b"),
        event(6, "op-b", "REALTIME_FILL", order_id="native-b", fill_id="shared"),
    )
    result = normalise_journal((envelope("op-a"), envelope("op-b")), events, ())
    assert result.candidates == ()
    assert any("CONFLICTING_NATIVE_FILL_ALIAS" in reason
               for reason in result.rejected_rows)


@pytest.mark.parametrize("missing", ["market_id", "action", "outcome_side", "quantity"])
def test_mutable_event_detail_cannot_repair_missing_immutable_order_field(
    missing: str,
) -> None:
    result = normalise_journal(
        (envelope("op-a", missing=missing),),
        (event(1, "op-a", "SUBMISSION"),),
        (),
    )
    assert result.candidates == ()
    assert any("MISSING_IMMUTABLE_ENVELOPE_ORDER_FIELDS" in reason
               for reason in result.rejected_rows)
    assert result.realised_net is None


def test_fill_without_explicit_original_native_ack_id_denied() -> None:
    events = (
        event(1, "op-a", "SUBMISSION"),
        event(2, "op-a", "ACK", order_id="native-a"),
        event(3, "op-a", "AUTHORITATIVE_FILL", fill_id="fill-a"),
    )
    result = normalise_journal((envelope("op-a"),), events, ())
    assert result.candidates == ()
    assert any("FILL_NATIVE_ORDER_NOT_MATCHED_TO_ACK" in reason
               for reason in result.rejected_rows)


def test_nonfill_alias_rejected_even_with_spoofed_fill_id() -> None:
    events = (
        event(1, "op-a", "SUBMISSION"),
        event(2, "op-a", "ACK", order_id="native-a"),
        event(3, "op-a", "CANCEL_ACK", order_id="native-a", fill_id="fake"),
    )
    result = normalise_journal(
        (envelope("op-a"),), events, (FillDedupeRow("fake-alias", 3),)
    )
    assert result.candidates == ()
    assert result.rejected_rows == ("INVALID_ALIAS_REFERENCE",)


def test_local_confirmed_fee_role_claim_remains_missing_native_proof() -> None:
    ev = replace(
        event(3, "op-a", "CANCEL_ACK", order_id="native-a"),
        detail_json='{"native_role":"MAKER","paid_fee_susq":"0","complete":true}',
        terminal_status="CONFIRMED",
    )
    result = normalise_journal(
        (envelope("op-a"),),
        (event(1, "op-a", "SUBMISSION"),
         event(2, "op-a", "ACK", order_id="native-a"), ev),
        (),
    )
    assert len(result.candidates) == 1
    candidate = result.candidates[0]
    assert candidate.verified_zero_fill is None
    assert candidate.evidence_holes.native_liquidity_role is None
    assert candidate.evidence_holes.paid_fee_susq is None
    assert candidate.evidence_holes.original_fill_universe_complete is None
    assert result.locked_bundle is None and result.realised_net is None
    assert result.source_admitted is False
