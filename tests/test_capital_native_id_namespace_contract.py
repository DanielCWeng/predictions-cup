"""Preregistered invented-only native-ID namespace witness for a FUTURE bridge.

Pinned (unmerged) source contracts reviewed without combining branches:
- journal adapter #304: 3c3ba48db5d8a0074bab1a6e775af18275cb7ad3
  adapter blob: c2675a7da453f0daf1c4cc011d00d4f5b8040d29
- corrected estimator #301: ec630bc045d68bffe67a2c41f6fdf9de90275d95
  estimator blob: e0c3db306bb8683356395ec79d3f71de8eb31118

The adapter indexes raw native orders/fills by tournament; the estimator's
LockedBundle currently checks raw order_id and fill_id uniqueness without
a tournament namespace. This witness is NOT real venue evidence, and makes
NO claim about the issuer's actual ID scope. Do not pass journal candidates
to an estimator or mint fake SourceProof, fees, native clocks or cash.

Two conditional FUTURE resolutions, neither authorised by this test:
1. Original issuer/custodian independently certifies globally unique native
   IDs and reconciles their original order/fill/fee/lot/source coverage.
2. Independently reviewed, reversible (issuer, exchange, tournament, raw-ID)
   typed namespace with an explicit version, original-ID traceability,
   and strict cross-version rejection is defined BEFORE a new bridge.

Absent (1) or (2), FUTURE_COMPOSITION_BLOCKED and real maker alpha NULL.
No data reads, network, venue, active SQLite/WAL, trade or merger.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import fields

from predictions_cup.research.journal_maker_adapter import (
    EnvelopeRow,
    EventRow,
    FillDedupeRow,
    normalise_journal,
)
from predictions_cup.research.locked_maker_economics import (
    MakerAttempt,
    VenueFill,
)

RAW_ORDER = "raw-order-7"
RAW_FILL = "raw-fill-9"
LOCAL_INTENT = "reused-local-intent"
UTC_ISSUER_CLAIM = "2026-10-05T10:00:00Z"
DETAIL = '{"market_id":"fake-market","action":"BUY","outcome_side":"NO"}'


def _rows(
    tournament: str, operation: str, first_event_id: int,
) -> tuple[EnvelopeRow, tuple[EventRow, ...], FillDedupeRow]:
    # Original *local* payload/intent join is complete; it is NOT venue proof.
    payload = json.dumps(
        {"market_id": "fake-market", "action": "BUY",
         "outcome_side": "NO", "quantity": "2"},
        separators=(",", ":"),
    )
    env = EnvelopeRow(
        tournament, operation, "single_placement", "LIVE", payload,
        hashlib.sha256(payload.encode("utf-8")).hexdigest(),
        json.dumps([LOCAL_INTENT]),
    )

    def event(offset: int, kind: str, native_order: str | None,
              native_fill: str | None = None) -> EventRow:
        return EventRow(
            event_id=first_event_id + offset,
            tournament_id=tournament,
            logical_operation_id=operation,
            logical_intent_id=LOCAL_INTENT,
            event_type=kind,
            source_timestamp=UTC_ISSUER_CLAIM,  # purported, not attested
            observed_monotonic_ns=100 + offset,
            exchange_order_id=native_order,
            exchange_id="fake-exchange",
            fill_id=native_fill,
            quantity="2",
            price="0.40",
            detail_json=DETAIL,
            strategy_family="MAKE",  # never venue maker-liquidity proof
        )

    events = (
        event(0, "SUBMISSION", None),
        event(1, "ACK", RAW_ORDER),
        event(2, "AUTHORITATIVE_FILL", RAW_ORDER, RAW_FILL),
    )
    # All aliases point to actual native-FILL-shaped journal rows, not ACKs.
    alias = FillDedupeRow(f"alias:{tournament}:{operation}", first_event_id + 2)
    return env, events, alias


def _fixture(
    tournament_a: str, tournament_b: str,
) -> tuple[tuple[EnvelopeRow, ...], tuple[EventRow, ...],
           tuple[FillDedupeRow, ...]]:
    a_env, a_events, a_alias = _rows(tournament_a, "operation-a", 1)
    b_env, b_events, b_alias = _rows(tournament_b, "operation-b", 4)
    return (a_env, b_env), (*a_events, *b_events), (a_alias, b_alias)


def test_cross_tournament_raw_id_reuse_is_local_only_not_composable() -> None:
    result = normalise_journal(*_fixture("FAKE-A", "FAKE-B"))
    # Current #304 LOCAL projection uses (tournament, raw-ID), so these are
    # two distinct original intent candidates; this is NOT a global ID proof.
    assert result.rejected_rows == ()
    assert len(result.candidates) == 2
    assert len({c.attempt_key for c in result.candidates}) == 2
    assert {c.tournament_id for c in result.candidates} == {"FAKE-A", "FAKE-B"}
    assert all(c.possible_native_order_id == RAW_ORDER for c in result.candidates)
    assert all(c.native_fill_ids_observed == (RAW_FILL,) for c in result.candidates)
    assert len({(c.tournament_id, c.possible_native_order_id)
                for c in result.candidates}) == 2
    assert len({c.possible_native_order_id for c in result.candidates}) == 1
    assert len({fill for c in result.candidates
                for fill in c.native_fill_ids_observed}) == 1

    # #301's LockedBundle typed order/fill rows have raw unscoped ID strings.
    # Both public model types lack a separate issuer/tournament namespace,
    # so retaining both identical raw values would violate its unique-ID gate.
    assert "order_id" in {f.name for f in fields(MakerAttempt)}
    assert "fill_id" in {f.name for f in fields(VenueFill)}
    assert "tournament_id" not in {f.name for f in fields(MakerAttempt)}
    assert "tournament_id" not in {f.name for f in fields(VenueFill)}

    # Never construct/infer a source-admitted LockedBundle from this fixture.
    assert result.verdict == "MISSING_PROOF"
    assert result.locked_bundle is None
    assert result.eligible_maker_denominator is None
    assert result.realised_net is None
    assert result.conservative_ci is None
    assert result.source_admitted is False
    assert result.paper_admitted is False
    assert all(c.verified_zero_fill is None and c.receipt_at is None
               and c.evidence_holes.native_liquidity_role is None
               and c.evidence_holes.paid_fee_susq is None
               and c.evidence_holes.later_lot_exit_cash_susq is None
               for c in result.candidates)


def test_same_tournament_duplicate_native_order_remains_atomic_reject() -> None:
    result = normalise_journal(*_fixture("FAKE-A", "FAKE-A"))
    assert result.candidates == ()
    assert any("NATIVE_ORDER_SHARED_BY_DISTINCT_INTENTS" in x
               for x in result.rejected_rows)
    assert result.verdict == "MISSING_PROOF"
    assert result.locked_bundle is None
    assert result.realised_net is None
    assert result.source_admitted is False
