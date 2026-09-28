"""Synchronous in-memory strategy -> risk -> execution-plan hot path."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from time import monotonic_ns

from predictions_cup.execution.models import ExecutionMode
from predictions_cup.execution.planner import build_execution_plan
from predictions_cup.execution.sinks import ExecutionPlan
from predictions_cup.risk.core import RiskContext, RiskDecision, evaluate_risk
from predictions_cup.runtime.models import RuntimeSnapshot
from predictions_cup.runtime.telemetry import HotPathTelemetry
from predictions_cup.strategy.core import StrategyRegistry, StrategyResult
from predictions_cup.strategy.kernels import KernelRegistry

ClockNs = Callable[[], int]


@dataclass(frozen=True, slots=True)
class DecisionOutcome:
    strategy_result: StrategyResult
    risk_decision: RiskDecision
    execution_plan: ExecutionPlan | None


class DecisionRuntime:
    """Pure calculation orchestrator. No await, I/O, logging or hidden market state."""

    def __init__(
        self,
        *,
        strategies: StrategyRegistry,
        kernels: KernelRegistry,
        telemetry: HotPathTelemetry | None = None,
        clock_ns: ClockNs = monotonic_ns,
    ) -> None:
        self._strategies = strategies
        self._kernels = kernels
        self._telemetry = telemetry
        self._clock_ns = clock_ns

    def decide(
        self,
        *,
        strategy_id: str,
        snapshot: RuntimeSnapshot,
        strategy_config: Mapping[str, float],
        risk_context: RiskContext,
        logical_operation_id: str,
        mode: ExecutionMode,
        created_monotonic_ns: int,
    ) -> DecisionOutcome:
        decision_started = self._clock_ns()

        strategy_started = self._clock_ns()
        proposal = self._strategies.evaluate(
            strategy_id,
            snapshot,
            self._kernels,
            strategy_config,
        )
        strategy_finished = self._clock_ns()
        self._observe("strategy", strategy_finished - strategy_started)

        risk_started = self._clock_ns()
        decision = evaluate_risk(proposal, snapshot, risk_context)
        risk_finished = self._clock_ns()
        self._observe("risk", risk_finished - risk_started)

        plan: ExecutionPlan | None = None
        if decision.approved:
            plan_started = self._clock_ns()
            plan = build_execution_plan(
                decision,
                logical_operation_id=logical_operation_id,
                mode=mode,
                created_monotonic_ns=created_monotonic_ns,
            )
            plan_finished = self._clock_ns()
            self._observe("execution_plan", plan_finished - plan_started)
            self._increment("approved")
        else:
            self._increment("no_trade")

        decision_finished = self._clock_ns()
        self._observe("decision_total", decision_finished - decision_started)
        return DecisionOutcome(
            strategy_result=proposal,
            risk_decision=decision,
            execution_plan=plan,
        )

    def _observe(self, name: str, duration_ns: int) -> None:
        if self._telemetry is not None:
            self._telemetry.observe(name, duration_ns)

    def _increment(self, name: str) -> None:
        if self._telemetry is not None:
            self._telemetry.increment(name)
