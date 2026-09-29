"""Thin sink adapters: maker logic is identical in SHADOW and LIVE."""

from __future__ import annotations

from predictions_cup.execution.live import SigLiveSink
from predictions_cup.execution.models import (
    ExecutionEnvelope,
    ExecutionEvent,
    ExecutionMode,
    LifecycleState,
    OperationKind,
)
from predictions_cup.execution.sinks import ExecutionPlan, ShadowSink
from predictions_cup.maker.contracts import MakerMarketSnapshot
from predictions_cup.maker.lifecycle import ActiveQuote


class ShadowMakerExecutionAdapter:
    """Conservative shadow execution with no invented passive queue priority."""

    def __init__(self, sink: ShadowSink | None = None) -> None:
        self._sink = sink or ShadowSink()

    async def place(
        self,
        plan: ExecutionPlan,
        snapshot: MakerMarketSnapshot,
    ) -> ExecutionEvent:
        return self._sink.dispatch(plan, snapshot.runtime)

    async def cancel(
        self,
        active: ActiveQuote,
        logical_operation_id: str,
        tournament_id: str,
    ) -> ExecutionEvent:
        del active, tournament_id
        return ExecutionEvent(
            logical_operation_id=logical_operation_id,
            state=LifecycleState.CANCELLED,
            observed_monotonic_ns=0,
            simulated=True,
            detail="shadow_cancel",
        )


class LiveMakerExecutionAdapter:
    """LIVE adapter delegates entirely to BUILD-009 interlocked/journaled sink."""

    def __init__(self, sink: SigLiveSink) -> None:
        self._sink = sink

    async def place(
        self,
        plan: ExecutionPlan,
        snapshot: MakerMarketSnapshot,
    ) -> ExecutionEvent:
        del snapshot
        return await self._sink.dispatch(plan)

    async def cancel(
        self,
        active: ActiveQuote,
        logical_operation_id: str,
        tournament_id: str,
    ) -> ExecutionEvent:
        order_id = active.exchange_order_id
        if order_id is None or order_id <= 0:
            raise ValueError("LIVE maker cannot cancel without authoritative positive order id")
        envelope = ExecutionEnvelope.cancellation(
            logical_operation_id=logical_operation_id,
            operation_kind=OperationKind.SINGLE_CANCELLATION,
            sink_mode=ExecutionMode.LIVE,
            created_monotonic_ns=active.observed_monotonic_ns,
            order_id=order_id,
            tournament_id=tournament_id,
        )
        return await self._sink.cancel(envelope)
