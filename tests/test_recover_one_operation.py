from __future__ import annotations

import asyncio
import importlib.util
import sys
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, cast

import pytest

from predictions_cup.execution.journal import ExecutionJournal
from predictions_cup.execution.models import (
    ExecutionEnvelope,
    ExecutionMode,
    LifecycleState,
    OperationKind,
    RuntimeOrderIntent,
)
from predictions_cup.execution.recovery import RecoveryRest
from predictions_cup.runtime import OrderAction, OutcomeSide
from predictions_cup.sig.errors import SigUnexpectedServerError
from predictions_cup.sig.trading_dto import (
    OrderFillsResponseDto,
    OrderReadDto,
    OrderStatusFilter,
    PortfolioFillPageDto,
    PositionsResponseDto,
)

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "recover_one_operation",
    ROOT / "scripts" / "ops" / "recover_one_operation.py",
)
assert SPEC is not None and SPEC.loader is not None
MODULE: Any = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)
RecoveryResult = MODULE.RecoveryResult
recover_operation = MODULE.recover_operation


class _ReadOnlyRest:
    def __init__(
        self,
        *,
        open_orders: set[int] | frozenset[int] = frozenset(),
    ) -> None:
        self.open_orders = set(open_orders)
        self.get_order_calls: list[int] = []
        self.get_order_fills_calls: list[int] = []

    async def get_order(self, order_id: int) -> OrderReadDto:
        self.get_order_calls.append(order_id)
        return OrderReadDto.model_validate(
            {
                "id": order_id,
                "exchangeId": "36",
                "side": "yes",
                "action": "buy",
                "quantity": "1",
                "priceLimit": "0.5",
                "open": order_id in self.open_orders,
                "createdAt": "2026-10-02T00:00:00Z",
                "expirationDate": None,
            }
        )

    async def get_order_fills(
        self,
        order_id: int,
        *,
        limit: int = 50,
        cursor: str | None = None,
    ) -> OrderFillsResponseDto:
        del limit, cursor
        self.get_order_fills_calls.append(order_id)
        return OrderFillsResponseDto.model_validate(
            {
                "orderId": order_id,
                "exchangeId": "36",
                "tournamentId": "t1",
                "data": [],
                "pagination": {"limit": 200, "hasMore": False, "nextCursor": None},
                "coverage": {"complete": True, "projectedThroughSequence": 1},
                "totalQuantityFilled": "0",
                "avgFillPrice": None,
            }
        )

    async def iter_orders(
        self,
        *,
        status: OrderStatusFilter = "open",
        exchange_id: str | None = None,
        market_id: str | None = None,
        tournament_id: str | None = None,
        limit: int = 200,
    ) -> AsyncIterator[OrderReadDto]:
        del status, exchange_id, market_id, tournament_id, limit
        for order_id in sorted(self.open_orders):
            yield await self.get_order(order_id)

    async def get_tournament_positions(self, tournament_slug: str) -> PositionsResponseDto:
        del tournament_slug
        raise AssertionError("unexpected positions request")


class _RecordingPlacementSink:
    def __init__(self) -> None:
        self.envelopes: list[ExecutionEnvelope] = []

    async def dispatch_recovery(self, envelope: ExecutionEnvelope) -> object:
        self.envelopes.append(envelope)
        return object()


class _ProjectionFallbackRest(_ReadOnlyRest):
    def __init__(self) -> None:
        super().__init__()
        self.order_list_statuses: list[OrderStatusFilter] = []
        self.portfolio_fill_calls = 0

    async def get_order(self, order_id: int) -> OrderReadDto:
        self.get_order_calls.append(order_id)
        raise SigUnexpectedServerError(
            status_code=503,
            code="SERVICE_UNAVAILABLE",
            safe_message="order projection temporarily unavailable",
        )

    def iter_orders(
        self,
        *,
        status: OrderStatusFilter = "open",
        exchange_id: str | None = None,
        market_id: str | None = None,
        tournament_id: str | None = None,
        limit: int = 200,
    ) -> AsyncIterator[OrderReadDto]:
        del exchange_id, market_id, tournament_id, limit
        self.order_list_statuses.append(status)

        async def rows() -> AsyncIterator[OrderReadDto]:
            if status == "all":
                yield OrderReadDto.model_validate(
                    {
                        "id": 914,
                        "exchangeId": "36",
                        "side": "yes",
                        "action": "buy",
                        "quantity": "1",
                        "priceLimit": "0.5",
                        "open": False,
                        "createdAt": "2026-10-02T00:00:00Z",
                        "expirationDate": None,
                    }
                )

        return rows()

    async def get_order_fills(
        self,
        order_id: int,
        *,
        limit: int = 50,
        cursor: str | None = None,
    ) -> OrderFillsResponseDto:
        del limit, cursor
        self.get_order_fills_calls.append(order_id)
        raise SigUnexpectedServerError(
            status_code=503,
            code="SERVICE_UNAVAILABLE",
            safe_message="order fills projection temporarily unavailable",
        )

    async def list_portfolio_fills(
        self,
        *,
        exchange_id: str | None = None,
        market_id: str | None = None,
        tournament_id: str | None = None,
        limit: int = 50,
        cursor: str | None = None,
    ) -> PortfolioFillPageDto:
        del exchange_id, market_id, tournament_id, limit, cursor
        self.portfolio_fill_calls += 1
        return PortfolioFillPageDto.model_validate(
            {
                "data": [
                    {
                        "id": 601,
                        "orderId": 914,
                        "exchangeId": "36",
                        "marketId": "market-1",
                        "price": "0.4",
                        "quantity": "1",
                        "side": "yes",
                        "filledAt": "2026-10-02T00:00:01Z",
                    }
                ],
                "pagination": {"limit": 200, "hasMore": False, "nextCursor": None},
                "coverage": {"complete": True, "projectedThroughSequence": 1},
            }
        )


def _batch_envelope() -> ExecutionEnvelope:
    intent = RuntimeOrderIntent(
        intent_id="intent-1",
        exchange_id="36",
        market_id="market-1",
        tournament_id="t1",
        outcome_side=OutcomeSide.YES,
        action=OrderAction.BUY,
        quantity=1,
        limit_price_ticks=100,
        strategy_id="fixture",
        decision_observation_ns=10,
    )
    return ExecutionEnvelope.placement(
        logical_operation_id="batch-1",
        operation_kind=OperationKind.BEST_EFFORT_BATCH,
        sink_mode=ExecutionMode.LIVE,
        idempotency_key="batch-idempotency-1",
        intents=(intent,),
        created_monotonic_ns=100,
    )


def _single_cancel_envelope() -> ExecutionEnvelope:
    return ExecutionEnvelope.cancellation(
        logical_operation_id="cancel-1",
        operation_kind=OperationKind.SINGLE_CANCELLATION,
        sink_mode=ExecutionMode.LIVE,
        created_monotonic_ns=100,
        order_id=914,
        tournament_id="t1",
    )


def _journal_with_state(
    path: Path,
    envelope: ExecutionEnvelope,
    state: LifecycleState,
) -> ExecutionJournal:
    journal = ExecutionJournal(path)
    journal.record_before_dispatch(envelope, submitted_monotonic_ns=101)
    if envelope.lifecycle_state is state:
        return journal
    if (
        state is LifecycleState.CANCEL_PENDING
        and envelope.lifecycle_state is LifecycleState.PENDING
    ):
        journal.mark_state(envelope.logical_operation_id, LifecycleState.OPEN, 102)
    journal.mark_state(envelope.logical_operation_id, state, 103)
    return journal


@pytest.mark.parametrize(
    "state",
    [LifecycleState.RECONCILING, LifecycleState.CANCEL_PENDING, LifecycleState.UNCERTAIN],
)
def test_single_cancellation_never_uses_placement_replay_and_needs_authoritative_evidence(
    tmp_path: Path,
    state: LifecycleState,
) -> None:
    envelope = _single_cancel_envelope()
    journal = _journal_with_state(
        tmp_path / f"single-cancel-{state.value}.sqlite3",
        envelope,
        state,
    )
    rest = _ReadOnlyRest(open_orders={914})
    sink = _RecordingPlacementSink()
    try:
        result = asyncio.run(
            recover_operation(
                envelope=journal.unresolved()[0],
                journal=journal,
                rest=cast(RecoveryRest, rest),
                placement_sink=sink,
            )
        )

        assert result == RecoveryResult(
            resolved=False,
            detail="authoritative order 914 is still open; cancellation unresolved",
        )
        assert rest.get_order_calls == [914]
        assert rest.get_order_fills_calls == []
        assert sink.envelopes == []
        assert journal.unresolved()[0].lifecycle_state is LifecycleState.RECONCILING
    finally:
        journal.close()


@pytest.mark.parametrize("state", [LifecycleState.RECONCILING, LifecycleState.UNCERTAIN])
def test_best_effort_batch_placement_replay_keeps_the_durable_idempotency_key(
    tmp_path: Path,
    state: LifecycleState,
) -> None:
    envelope = _batch_envelope()
    journal = _journal_with_state(tmp_path / f"batch-{state.value}.sqlite3", envelope, state)
    rest = _ReadOnlyRest()
    sink = _RecordingPlacementSink()
    try:
        result = asyncio.run(
            recover_operation(
                envelope=journal.unresolved()[0],
                journal=journal,
                rest=cast(RecoveryRest, rest),
                placement_sink=sink,
            )
        )

        assert result.resolved is False
        assert len(sink.envelopes) == 1
        assert sink.envelopes[0].logical_operation_id == envelope.logical_operation_id
        assert sink.envelopes[0].idempotency_key == "batch-idempotency-1"
        assert rest.get_order_calls == []
    finally:
        journal.close()


def test_best_effort_batch_cancel_pending_never_replays_placement(
    tmp_path: Path,
) -> None:
    envelope = _batch_envelope()
    journal = _journal_with_state(
        tmp_path / "batch-cancel-pending.sqlite3",
        envelope,
        LifecycleState.CANCEL_PENDING,
    )
    journal.record_event(
        logical_operation_id=envelope.logical_operation_id,
        logical_intent_id="intent-1",
        event_type="ACK",
        observed_monotonic_ns=104,
        exchange_order_id="914",
    )
    rest = _ReadOnlyRest(open_orders={914})
    sink = _RecordingPlacementSink()
    try:
        result = asyncio.run(
            recover_operation(
                envelope=journal.unresolved()[0],
                journal=journal,
                rest=cast(RecoveryRest, rest),
                placement_sink=sink,
            )
        )

        assert result.resolved is False
        assert "order 914 is still open" in result.detail
        assert sink.envelopes == []
        assert rest.get_order_calls == [914]
        assert journal.unresolved()[0].lifecycle_state is LifecycleState.RECONCILING
    finally:
        journal.close()


@pytest.mark.parametrize("operation_kind", ["single_cancellation", "best_effort_batch"])
def test_cancel_recovery_resolves_only_after_closed_order_and_complete_fill_evidence(
    tmp_path: Path,
    operation_kind: str,
) -> None:
    if operation_kind == "single_cancellation":
        envelope = _single_cancel_envelope()
    else:
        envelope = _batch_envelope()
    journal = _journal_with_state(
        tmp_path / f"{operation_kind}-closed.sqlite3",
        envelope,
        LifecycleState.CANCEL_PENDING,
    )
    if operation_kind == "best_effort_batch":
        journal.record_event(
            logical_operation_id=envelope.logical_operation_id,
            logical_intent_id="intent-1",
            event_type="ACK",
            observed_monotonic_ns=104,
            exchange_order_id="914",
        )
    rest = _ReadOnlyRest()
    try:
        result = asyncio.run(
            recover_operation(
                envelope=journal.unresolved()[0],
                journal=journal,
                rest=cast(RecoveryRest, rest),
            )
        )

        assert result.resolved is True
        assert rest.get_order_calls == [914]
        assert rest.get_order_fills_calls == [914]
        assert journal.unresolved() == ()
        terminal = journal.events(envelope.logical_operation_id)[-1]
        assert terminal.event_type == "RECONCILED_TERMINAL"
        assert terminal.terminal_status == LifecycleState.CANCELLED.value
    finally:
        journal.close()


def test_acknowledged_open_placement_uses_all_orders_and_portfolio_fills_on_503(
    tmp_path: Path,
) -> None:
    envelope = _batch_envelope()
    journal = ExecutionJournal(tmp_path / "placement-projection-fallback.sqlite3")
    journal.record_before_dispatch(envelope, submitted_monotonic_ns=101)
    journal.record_event(
        logical_operation_id=envelope.logical_operation_id,
        tournament_id=envelope.tournament_id,
        logical_intent_id="intent-1",
        event_type="ACK",
        observed_monotonic_ns=102,
        exchange_id="36",
        exchange_order_id="914",
        terminal_status=LifecycleState.OPEN.value,
    )
    journal.mark_state(envelope.logical_operation_id, LifecycleState.OPEN, 102)
    rest = _ProjectionFallbackRest()
    try:
        result = asyncio.run(
            recover_operation(
                envelope=journal.unresolved()[0],
                journal=journal,
                rest=cast(RecoveryRest, rest),
            )
        )

        assert result.resolved is True
        assert "authoritatively FILLED" in result.detail
        assert rest.get_order_calls == [914]
        assert rest.order_list_statuses == ["all"]
        assert rest.get_order_fills_calls == [914]
        assert rest.portfolio_fill_calls == 1
        assert journal.unresolved() == ()
        assert any(
            event.event_type == "AUTHORITATIVE_FILL" and event.fill_id == "601"
            for event in journal.events(envelope.logical_operation_id)
        )
        assert any(
            event.event_type == "RECONCILED_TERMINAL"
            and event.terminal_status == LifecycleState.FILLED.value
            for event in journal.events(envelope.logical_operation_id)
        )
    finally:
        journal.close()
