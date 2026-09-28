"""Event-driven I/O shell around the synchronous BUILD-009 decision runtime."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass

from predictions_cup.execution.models import ExecutionEvent, ExecutionMode
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
    """Evaluate only strategies affected by an actual state change or schedule trigger."""

    def __init__(
        self,
        *,
        runtime: DecisionRuntime,
        bindings: tuple[StrategyBinding, ...],
        risk_context: RiskContext,
        mode: ExecutionMode,
        dispatch: PlanDispatcher,
    ) -> None:
        self._runtime = runtime
        self._bindings = bindings
        self._risk_context = risk_context
        self._mode = mode
        self._dispatch = dispatch

    async def on_state_change(
        self,
        change: StateChange,
        snapshot: RuntimeSnapshot,
    ) -> tuple[ExecutionEvent, ...]:
        events: list[ExecutionEvent] = []
        for binding in self._bindings:
            if not binding.affected_by(change):
                continue

            outcome = self._runtime.decide(
                strategy_id=binding.strategy_id,
                snapshot=snapshot,
                strategy_config=binding.config,
                risk_context=self._risk_context,
                logical_operation_id=f"{change.event_id}:{binding.strategy_id}",
                mode=self._mode,
            )
            if outcome.execution_plan is None:
                continue
            events.append(await self._dispatch(outcome.execution_plan, snapshot))
        return tuple(events)
