"""Event-driven in-memory decision core.

Networking and persistence live outside this module. The core stops at the common
ExecutionPlan boundary so SHADOW and LIVE differ only at the sink.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from time import monotonic_ns
from typing import Callable

from predictions_cup.execution.models import ExecutionMode
from predictions_cup.execution.planning import build_execution_plan
from predictions_cup.execution.sinks import ExecutionPlan
from predictions_cup.risk.core import RiskContext, RiskDecision, evaluate_risk
from predictions_cup.runtime.models import RuntimeSnapshot
from predictions_cup.runtime.telemetry import HotPathTelemetry
from predictions_cup.strategy.core import StrategyRegistry, StrategyResult
from predictions_cup.strategy.kernels import KernelRegistry

ClockNs = Callable[[], int]


@dataclass(frozen=True, slots=True)
class PreparedDecision:
    proposal: StrategyResult
    risk: RiskDecision
    plan: ExecutionPlan | None
    decision_started_ns: int
    decision_finished_ns: int


class DecisionCore:
    """Synchronous calculation path: strategy -> Risk -> shared execution plan."""

    def __init__(
        self,
        *,
        strategies: StrategyRegistry,
        kernels: KernelRegistry,
        risk_context: RiskContext,
        telemetry: HotPathTelemetry | None = None,
        clock_ns: ClockNs = monotonic_ns,
    ) -> None:
        self._strategies = strategies
        self._kernels = kernels
        self._risk_context = risk_context
        self._telemetry = telemetry or HotPathTelemetry()
        self._clock_ns = clock_ns

    @property
    def telemetry(self) -> HotPathTelemetry:
        return self._telemetry

    def prepare(
        self,
        *,
        strategy_id: str,
        snapshot: RuntimeSnapshot,
        strategy_config: Mapping[str, float],
        mode: ExecutionMode,
        logical_operation_id: str,
        idempotency_key: str,
    ) -> PreparedDecision:
        started = self._clock_ns()

        strategy_started = self._clock_ns()
        proposal = self._strategies.evaluate(
            strategy_id,
            snapshot,
            self._kernels,
            strategy_config,
        )
        strategy_finished = self._clock_ns()
        self._telemetry.observe(
            "strategy",
            strategy_finished - strategy_started,
        )

        risk_started = self._clock_ns()
        decision = evaluate_risk(proposal, snapshot, self._risk_context)
        risk_finished = self._clock_ns()
        self._telemetry.observe("risk", risk_finished - risk_started)

        plan: ExecutionPlan | None = None
        if decision.approved:
            plan_started = self._clock_ns()
            plan = build_execution_plan(
                decision,
                mode=mode,
                logical_operation_id=logical_operation_id,
                idempotency_key=idempotency_key,
                clock_ns=self._clock_ns,
            )
            plan_finished = self._clock_ns()
            self._telemetry.observe("execution_plan", plan_finished - plan_started)
            self._telemetry.increment("approved")
        else:
            self._telemetry.increment("no_trade")

        finished = self._clock_ns()
        self._telemetry.observe("decision_total", finished - started)
        return PreparedDecision(
            proposal=proposal,
            risk=decision,
            plan=plan,
            decision_started_ns=started,
            decision_finished_ns=finished,
        )
