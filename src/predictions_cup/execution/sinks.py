"""SHADOW/null sink implementations; LIVE sink is layered on the SIG adapter."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from time import monotonic_ns

from predictions_cup.execution.models import (
    ExecutionAudit,
    ExecutionEnvelope,
    ExecutionEvent,
    LifecycleState,
    RuntimeOrderIntent,
)
from predictions_cup.runtime.models import (
    OrderAction,
    OutcomeSide,
    RuntimeLevel,
    RuntimeSnapshot,
)

ClockNs = Callable[[], int]


@dataclass(frozen=True, slots=True)
class ExecutionPlan:
    envelope: ExecutionEnvelope
    intents: tuple[RuntimeOrderIntent, ...]
    audit: ExecutionAudit | None = None


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
        elif any(
            state in {LifecycleState.FILLED, LifecycleState.PARTIALLY_FILLED}
            for state in states
        ):
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
    def _fill_state(quantity: int, observable_quantity: float) -> LifecycleState:
        if observable_quantity <= 0:
            return LifecycleState.OPEN
        if observable_quantity + 1e-12 >= float(quantity):
            return LifecycleState.FILLED
        return LifecycleState.PARTIALLY_FILLED

    @staticmethod
    def _sum_levels(levels: Iterable[RuntimeLevel]) -> float:
        return sum(max(0.0, level.quantity) for level in levels)

    @classmethod
    def _intent_state(
        cls,
        intent: RuntimeOrderIntent,
        snapshot: RuntimeSnapshot,
    ) -> LifecycleState:
        book = snapshot.book(intent.exchange_id)
        if book is None or not book.trusted_depth:
            return LifecycleState.OPEN

        if intent.outcome_side is OutcomeSide.YES:
            executable_levels = book.asks if intent.action is OrderAction.BUY else book.bids
            if intent.is_market:
                return cls._fill_state(
                    intent.quantity,
                    cls._sum_levels(executable_levels),
                )

            assert intent.limit_price_ticks is not None
            ticks = intent.limit_price_ticks
            if intent.action is OrderAction.BUY:
                matching = (
                    level for level in book.asks if level.price_ticks <= ticks
                )
            else:
                matching = (
                    level for level in book.bids if level.price_ticks >= ticks
                )
            return cls._fill_state(intent.quantity, cls._sum_levels(matching))

        # SIG books are YES-denominated. Buying NO is economically equivalent to
        # selling YES at the complementary threshold; selling NO is buying YES.
        if intent.is_market:
            executable_levels = book.bids if intent.action is OrderAction.BUY else book.asks
            return cls._fill_state(
                intent.quantity,
                cls._sum_levels(executable_levels),
            )

        assert intent.limit_price_ticks is not None
        yes_complement_ticks = 200 - intent.limit_price_ticks
        if intent.action is OrderAction.BUY:
            matching = (
                level
                for level in book.bids
                if level.price_ticks >= yes_complement_ticks
            )
        else:
            matching = (
                level
                for level in book.asks
                if level.price_ticks <= yes_complement_ticks
            )
        return cls._fill_state(intent.quantity, cls._sum_levels(matching))
