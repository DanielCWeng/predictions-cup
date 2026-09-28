"""SHADOW/null sink implementations; LIVE sink is layered on the SIG adapter."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from time import monotonic_ns

from predictions_cup.execution.models import (
    ExecutionEnvelope,
    ExecutionEvent,
    LifecycleState,
    RuntimeOrderIntent,
)
from predictions_cup.runtime.models import OrderAction, OutcomeSide, RuntimeSnapshot

ClockNs = Callable[[], int]


@dataclass(frozen=True, slots=True)
class ExecutionPlan:
    envelope: ExecutionEnvelope
    intents: tuple[RuntimeOrderIntent, ...]


class NullSink:
    """Benchmark sink: no I/O and no fill model."""

    def __init__(self, clock_ns: ClockNs = monotonic_ns) -> None:
        self._clock_ns = clock_ns

    def dispatch(self, plan: ExecutionPlan, snapshot: RuntimeSnapshot) -> ExecutionEvent:
        del snapshot
        return ExecutionEvent(
            logical_operation_id=plan.envelope.logical_operation_id,
            state=LifecycleState.ACKED,
            observed_monotonic_ns=self._clock_ns(),
            simulated=True,
            detail="null_sink",
        )


class ShadowSink:
    """Conservative deterministic simulator; passive queue position is never invented."""

    def __init__(self, clock_ns: ClockNs = monotonic_ns) -> None:
        self._clock_ns = clock_ns

    def dispatch(self, plan: ExecutionPlan, snapshot: RuntimeSnapshot) -> ExecutionEvent:
        states = tuple(self._intent_state(intent, snapshot) for intent in plan.intents)
        if states and all(state is LifecycleState.FILLED for state in states):
            state = LifecycleState.FILLED
        elif any(state is LifecycleState.FILLED for state in states):
            state = LifecycleState.PARTIALLY_FILLED
        else:
            state = LifecycleState.OPEN
        return ExecutionEvent(
            logical_operation_id=plan.envelope.logical_operation_id,
            state=state,
            observed_monotonic_ns=self._clock_ns(),
            simulated=True,
            detail="shadow_conservative_depth",
        )

    @staticmethod
    def _intent_state(
        intent: RuntimeOrderIntent,
        snapshot: RuntimeSnapshot,
    ) -> LifecycleState:
        book = snapshot.book(intent.exchange_id)
        if book is None or not book.trusted_depth:
            return LifecycleState.OPEN
        if intent.is_market:
            return LifecycleState.FILLED if (book.bids or book.asks) else LifecycleState.OPEN

        assert intent.limit_price_ticks is not None
        ticks = intent.limit_price_ticks
        if intent.outcome_side is OutcomeSide.YES:
            if intent.action is OrderAction.BUY:
                return (
                    LifecycleState.FILLED
                    if book.asks and ticks >= book.asks[0].price_ticks
                    else LifecycleState.OPEN
                )
            return (
                LifecycleState.FILLED
                if book.bids and ticks <= book.bids[0].price_ticks
                else LifecycleState.OPEN
            )

        yes_complement_ticks = 200 - ticks
        if intent.action is OrderAction.BUY:
            return (
                LifecycleState.FILLED
                if book.bids and book.bids[0].price_ticks >= yes_complement_ticks
                else LifecycleState.OPEN
            )
        return (
            LifecycleState.FILLED
            if book.asks and book.asks[0].price_ticks <= yes_complement_ticks
            else LifecycleState.OPEN
        )
