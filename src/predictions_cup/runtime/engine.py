"""Synchronous in-memory strategy -> risk -> execution-plan hot path."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from predictions_cup.execution.models import ExecutionMode
from predictions_cup.execution.planner import build_execution_plan
from predictions_cup.execution.sinks import ExecutionPlan
from predictions_cup.risk.core import RiskContext, RiskDecision, evaluate_risk
from predictions_cup.runtime.models import RuntimeSnapshot
from predictions_cup.strategy.core import StrategyRegistry, StrategyResult
from predictions_cup.strategy.kernels import KernelRegistry


@dataclass(frozen=True, slots=True)
class DecisionOutcome:
    strategy_result: StrategyResult
    risk_decision: RiskDecision
    execution_plan: ExecutionPlan | None


class DecisionRuntime:
    """Pure calculation orchestrator. No await, I/O, logging or hidden state."""

    def __init__(
        self,
        *,
        strategies: StrategyRegistry,
        kernels: KernelRegistry,
    ) -> None:
        self._strategies = strategies
        self._kernels = kernels

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
        proposal = self._strategies.evaluate(
            strategy_id,
            snapshot,
            self._kernels,
            strategy_config,
        )
        decision = evaluate_risk(proposal, snapshot, risk_context)
        plan = (
            build_execution_plan(
                decision,
                logical_operation_id=logical_operation_id,
                mode=mode,
                created_monotonic_ns=created_monotonic_ns,
            )
            if decision.approved
            else None
        )
        return DecisionOutcome(
            strategy_result=proposal,
            risk_decision=decision,
            execution_plan=plan,
        )
