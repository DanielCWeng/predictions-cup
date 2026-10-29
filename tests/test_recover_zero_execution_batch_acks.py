"""Recover only issuer-proven zero-execution no-order ACKs from batch placement."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from predictions_cup.execution.journal import ExecutionJournal
from predictions_cup.execution.models import (
    ExecutionEnvelope,
    ExecutionMode,
    LifecycleState,
    OperationKind,
    RuntimeOrderIntent,
)
from predictions_cup.execution.recovery import (
    _PortfolioFillProjection,
    _ack_proves_no_execution,
    _reconcile_bulk_placement,
)
from predictions_cup.runtime import OrderAction, OutcomeSide


def _zero_response() -> dict[str, object]:
    return {
        "orderId": None, "exchangeId": "1001", "open": False,
        "remainingQuantity": 0, "quantityTraded": 0,
        "totalCost": 0, "fillPrice": None,
    }


def _proof(payload: dict[str, object]) -> bool:
    return _ack_proves_no_execution(
        SimpleNamespace(
            detail_json=json.dumps(payload),
            exchange_order_id=None,
            exchange_id="1001",
        )
    )


def test_only_complete_zero_execution_ack_qualifies() -> None:
    assert _proof(_zero_response())
    for field in ("orderId", "open", "remainingQuantity", "quantityTraded", "totalCost", "fillPrice"):
        missing = _zero_response()
        del missing[field]
        if field == "fillPrice":
            # A missing fill price is not evidence either.
            assert not _proof(missing)
        else:
            assert not _proof(missing)
    assert not _proof({**_zero_response(), "quantityTraded": 5})
    assert not _proof({**_zero_response(), "remainingQuantity": 100})
    assert not _proof({**_zero_response(), "totalCost": 2})
    assert not _proof({**_zero_response(), "open": True})
    assert not _proof({**_zero_response(), "orderId": 12345})


def _placement(journal: ExecutionJournal) -> ExecutionEnvelope:
    intents = tuple(
        RuntimeOrderIntent(
            intent_id=f"intent-{i}",
            exchange_id="1001",
            market_id="m1",
            tournament_id="t1",
            outcome_side=OutcomeSide.YES,
            action=OrderAction.SELL,
            quantity=(i + 1) * 100,
            limit_price_ticks=500,
            strategy_id="fixture",
            decision_observation_ns=123,
        )
        for i in range(3)
    )
    envelope = ExecutionEnvelope.placement(
        logical_operation_id="zero-ack-placement",
        operation_kind=OperationKind.BEST_EFFORT_BATCH,
        sink_mode=ExecutionMode.LIVE,
        idempotency_key="fixture-key",
        intents=intents,
        created_monotonic_ns=123,
    )
    journal.record_before_dispatch(envelope, intents, submitted_monotonic_ns=124)
    journal.mark_state(envelope.logical_operation_id, LifecycleState.ACKED, 125)
    return envelope


def _reconcile(journal: ExecutionJournal, envelope: ExecutionEnvelope, *, complete: bool = True) -> None:
    _reconcile_bulk_placement(
        journal=journal,
        envelope=envelope,
        open_orders={},
        successful_cancels=frozenset(),
        fills=_PortfolioFillProjection(
            by_order_id={},
            incomplete_exchange_ids=frozenset() if complete else frozenset({"1001"}),
        ),
        order_quantities={},
        order_exchange_ids={},
        envelopes_by_id={envelope.logical_operation_id: envelope},
        snapshot_available=True,
        clock_ns=lambda: 126,
    )


def test_all_zero_execution_acks_reconcile_without_venue_order_id(tmp_path: Path) -> None:
    journal = ExecutionJournal(tmp_path / "zero.sqlite3")
    try:
        envelope = _placement(journal)
        for intent_id in envelope.intent_ids:
            journal.record_event(
                logical_operation_id=envelope.logical_operation_id,
                logical_intent_id=intent_id,
                event_type="ACK",
                observed_monotonic_ns=125,
                exchange_id="1001",
                detail_json=json.dumps(_zero_response()),
            )
        _reconcile(journal, envelope)
        assert journal.lifecycle_state(envelope.logical_operation_id) is LifecycleState.RECONCILED
        assert any(
            event.event_type == "RECONCILED_TERMINAL"
            for event in journal.events(envelope.logical_operation_id)
        )
    finally:
        journal.close()


def test_missing_or_incomplete_issuer_evidence_stays_blocked(tmp_path: Path) -> None:
    journal = ExecutionJournal(tmp_path / "uncertain.sqlite3")
    try:
        envelope = _placement(journal)
        for intent_id in envelope.intent_ids:
            data = _zero_response()
            if intent_id == envelope.intent_ids[-1]:
                data.pop("quantityTraded")
            journal.record_event(
                logical_operation_id=envelope.logical_operation_id,
                logical_intent_id=intent_id,
                event_type="ACK",
                observed_monotonic_ns=125,
                exchange_id="1001",
                detail_json=json.dumps(data),
            )
        _reconcile(journal, envelope)
        assert journal.lifecycle_state(envelope.logical_operation_id) is LifecycleState.ACKED
    finally:
        journal.close()


def test_incomplete_venue_fill_coverage_stays_blocked(tmp_path: Path) -> None:
    journal = ExecutionJournal(tmp_path / "incomplete.sqlite3")
    try:
        envelope = _placement(journal)
        for intent_id in envelope.intent_ids:
            journal.record_event(
                logical_operation_id=envelope.logical_operation_id,
                logical_intent_id=intent_id,
                event_type="ACK",
                observed_monotonic_ns=125,
                exchange_id="1001",
                detail_json=json.dumps(_zero_response()),
            )
        _reconcile(journal, envelope, complete=False)
        assert journal.lifecycle_state(envelope.logical_operation_id) is LifecycleState.ACKED
    finally:
        journal.close()
