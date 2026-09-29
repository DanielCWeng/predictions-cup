"""Thin sink adapters: maker logic is identical in SHADOW and LIVE."""

from __future__ import annotations

from predictions_cup.execution.journal import ExecutionJournal
from predictions_cup.execution.live import SigLiveSink
from predictions_cup.execution.models import (
    ExecutionEnvelope,
    ExecutionEvent,
    ExecutionMode,
    LifecycleState,
    OperationKind,
)
from predictions_cup.execution.sinks import ExecutionPlan, ShadowSink
from predictions_cup.maker.contracts import MakerMarketSnapshot, QuoteSide
from predictions_cup.maker.lifecycle import ActiveQuote, QuoteRegistry
from predictions_cup.runtime.models import OrderAction


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
    """LIVE adapter delegates writes to BUILD-009 and resolves ACK identity."""

    def __init__(
        self,
        sink: SigLiveSink,
        *,
        journal: ExecutionJournal,
        quotes: QuoteRegistry,
    ) -> None:
        self._sink = sink
        self._journal = journal
        self._quotes = quotes

    async def place(
        self,
        plan: ExecutionPlan,
        snapshot: MakerMarketSnapshot,
    ) -> ExecutionEvent:
        del snapshot
        event = await self._sink.dispatch(plan)
        self._sync_quote_registry(plan, event)
        return event

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

    def _sync_quote_registry(
        self,
        plan: ExecutionPlan,
        event: ExecutionEvent,
    ) -> None:
        journal_events = self._journal.events(plan.envelope.logical_operation_id)
        for intent in plan.intents:
            side = (
                QuoteSide.BID
                if intent.action is OrderAction.BUY
                else QuoteSide.ASK
            )
            related = tuple(
                item
                for item in journal_events
                if item.logical_intent_id == intent.intent_id
                and item.event_type in {"ACK", "REJECTED"}
            )
            if not related:
                # No conclusive per-intent identity: keep the coordinator's
                # local UNCERTAIN reservation and wait for reconciliation.
                continue
            latest = related[-1]
            if latest.event_type == "REJECTED":
                self._quotes.clear_side(
                    exchange_id=intent.exchange_id,
                    side=side,
                    observed_monotonic_ns=event.observed_monotonic_ns,
                )
                continue
            order_id = self._positive_int(latest.exchange_order_id)
            if order_id is None:
                continue
            state = self._event_state(latest.terminal_status, event.state)
            if state in {
                LifecycleState.FILLED,
                LifecycleState.REJECTED,
                LifecycleState.CANCELLED,
            }:
                self._quotes.clear_side(
                    exchange_id=intent.exchange_id,
                    side=side,
                    observed_monotonic_ns=event.observed_monotonic_ns,
                )
                continue
            if intent.limit_price_ticks is None:
                # MAKE only emits passive limit quotes. A market sentinel here is
                # an invariant violation, so keep the pre-dispatch uncertain state.
                continue
            self._quotes.apply_authoritative(
                exchange_id=intent.exchange_id,
                side=side,
                price_ticks=intent.limit_price_ticks,
                size=intent.quantity,
                remaining_size=intent.quantity,
                logical_operation_id=plan.envelope.logical_operation_id,
                exchange_order_id=order_id,
                lifecycle_state=state,
                observed_monotonic_ns=event.observed_monotonic_ns,
            )

    @staticmethod
    def _positive_int(value: str | None) -> int | None:
        if value is None:
            return None
        try:
            parsed = int(value)
        except ValueError:
            return None
        return parsed if parsed > 0 else None

    @staticmethod
    def _event_state(
        terminal_status: str | None,
        fallback: LifecycleState,
    ) -> LifecycleState:
        if terminal_status is None:
            return fallback
        try:
            return LifecycleState(terminal_status)
        except ValueError:
            return fallback
