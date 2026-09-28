"""Event-driven I/O shell around the synchronous BUILD-009 decision runtime."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass

from predictions_cup.execution.models import ExecutionEvent, ExecutionMode
from predictions_cup.execution.reservations import ExecutionReservationBook
from predictions_cup.execution.sinks import ExecutionPlan
from predictions_cup.risk.core import RiskContext
from predictions_cup.runtime.engine import DecisionRuntime
from predictions_cup.runtime.models import RuntimeSnapshot

PlanDispatcher = Callable[
    [ExecutionPlan, RuntimeSnapshot],
    Awaitable[ExecutionEvent],
]


@dataclass(frozen=True, slots=True)
class StateChange:
    """One observable state mutation that may invalidate strategy decisions."""

    event_id: str
    observed_monotonic_ns: int
    exchange_ids: frozenset[str] = frozenset()
    market_ids: frozenset[str] = frozenset()
    scheduled_strategy_ids: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        if not self.event_id.strip():
            raise ValueError("event_id must not be blank")
        if self.observed_monotonic_ns < 0:
            raise ValueError("observed_monotonic_ns must be non-negative")


@dataclass(frozen=True, slots=True)
class StrategyBinding:
    strategy_id: str
    config: Mapping[str, float]
    exchange_ids: frozenset[str] = frozenset()
    market_ids: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        if not self.strategy_id.strip():
            raise ValueError("strategy_id must not be blank")

    def affected_by(self, change: StateChange) -> bool:
        if self.strategy_id in change.scheduled_strategy_ids:
            return True
        return bool(
            self.exchange_ids.intersection(change.exchange_ids)
            or self.market_ids.intersection(change.market_ids)
        )


class EventDrivenCoordinator:
    """Evaluate affected strategies against a synchronously reserved risk state."""

    def __init__(
        self,
        *,
        runtime: DecisionRuntime,
        bindings: tuple[StrategyBinding, ...],
        risk_context: RiskContext,
        dispatch: PlanDispatcher,
        reservations: ExecutionReservationBook | None = None,
    ) -> None:
        if risk_context.mode is ExecutionMode.LIVE and reservations is None:
            raise ValueError("LIVE coordinator requires an execution reservation book")
        self._runtime = runtime
        self._bindings = bindings
        self._risk_context = risk_context
        self._dispatch = dispatch
        self._reservations = reservations

    async def on_state_change(
        self,
        change: StateChange,
        snapshot: RuntimeSnapshot,
    ) -> tuple[ExecutionEvent, ...]:
        approved: list[tuple[ExecutionPlan, RuntimeSnapshot]] = []

        # Decision order is deterministic. Each approval is reserved before the
        # next strategy is evaluated, so all bindings see the worst-case effect
        # of earlier approvals even though the exchange has not reported them yet.
        for binding in self._bindings:
            if not binding.affected_by(change):
                continue

            decision_snapshot = (
                snapshot
                if self._reservations is None
                else self._reservations.overlay_snapshot(snapshot)
            )
            outcome = self._runtime.decide(
                strategy_id=binding.strategy_id,
                snapshot=decision_snapshot,
                strategy_config=binding.config,
                risk_context=self._risk_context,
                logical_operation_id=f"{change.event_id}:{binding.strategy_id}",
            )
            plan = outcome.execution_plan
            if plan is None:
                continue
            if self._reservations is not None:
                self._reservations.reserve(
                    plan.envelope.logical_operation_id,
                    plan.intents,
                )
            approved.append((plan, decision_snapshot))

        if not approved:
            return ()

        # Once every approval is synchronously reserved, network dispatches no
        # longer need to serialize behind one another. Wait for every dispatch so
        # a failed sibling cannot silently orphan another in-flight operation.
        results = await asyncio.gather(
            *(self._dispatch(plan, state) for plan, state in approved),
            return_exceptions=True,
        )
        failures = [result for result in results if isinstance(result, BaseException)]
        if failures:
            raise BaseExceptionGroup(
                "one or more execution dispatches failed",
                failures,
            )
        return tuple(result for result in results if isinstance(result, ExecutionEvent))
