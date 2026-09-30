"""Thin sink adapters: maker logic is identical in SHADOW and LIVE."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from uuid import uuid4

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
from predictions_cup.observe.contracts import ObservationEmitter, ObservationKind, VenueObservation
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
        observation_emitter: ObservationEmitter | None = None,
        observation_process_instance_id: str | None = None,
        wall_clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._sink = sink
        self._journal = journal
        self._quotes = quotes
        self._observation_emitter = observation_emitter
        self._observation_process_instance_id = (
            uuid4().hex
            if observation_process_instance_id is None
            else observation_process_instance_id
        )
        if not self._observation_process_instance_id.strip():
            raise ValueError("observation_process_instance_id must not be blank")
        self._wall_clock = wall_clock

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
            self._observe_published_quote(
                plan=plan,
                intent=intent,
                side=side,
                order_id=order_id,
                state=state,
                observed_monotonic_ns=event.observed_monotonic_ns,
            )

    def _observe_published_quote(
        self,
        *,
        plan: ExecutionPlan,
        intent: RuntimeOrderIntent,
        side: QuoteSide,
        order_id: int,
        state: LifecycleState,
        observed_monotonic_ns: int,
    ) -> None:
        emitter = self._observation_emitter
        if emitter is None or intent.limit_price_ticks is None:
            return
        try:
            emitter.emit(
                VenueObservation(
                    kind=ObservationKind.QUOTE_PUBLISHED,
                    observed_at=self._wall_clock(),
                    monotonic_ns=observed_monotonic_ns,
                    process_instance_id=self._observation_process_instance_id,
                    source="MAKE_001_AUTHORITATIVE_QUOTE_REGISTRY",
                    source_version="observe-001",
                    provenance="AUTHORITATIVE_PER_INTENT_ACK",
                    tournament_id=intent.tournament_id,
                    market_id=intent.market_id,
                    exchange_id=intent.exchange_id,
                    strategy_family="MAKE",
                    strategy_id=intent.strategy_id,
                    logical_operation_id=plan.envelope.logical_operation_id,
                    logical_intent_id=intent.intent_id,
                    idempotency_key=plan.envelope.idempotency_key,
                    exchange_order_id=str(order_id),
                    detail=(
                        (
                            "quote_key",
                            (
                                f"{plan.envelope.logical_operation_id}|"
                                f"{intent.exchange_id}|{side.value}"
                            ),
                        ),
                        ("side", side.value),
                        ("price_ticks", str(intent.limit_price_ticks)),
                        ("size", str(intent.quantity)),
                        ("execution_state", state.value),
                    ),
                )
            )
        except Exception:
            return

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
