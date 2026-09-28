"""Shared post-risk execution-plan construction for SHADOW and LIVE."""

from __future__ import annotations

from time import monotonic_ns
from typing import Callable

from predictions_cup.execution.models import ExecutionEnvelope, ExecutionMode
from predictions_cup.execution.sinks import ExecutionPlan
from predictions_cup.risk.core import RiskDecision

ClockNs = Callable[[], int]


def build_execution_plan(
    decision: RiskDecision,
    *,
    mode: ExecutionMode,
    logical_operation_id: str,
    idempotency_key: str,
    clock_ns: ClockNs = monotonic_ns,
) -> ExecutionPlan:
    """Convert one approved central-Risk decision into the shared sink boundary."""
    if not logical_operation_id.strip():
        raise ValueError("logical_operation_id must not be blank")
    if not idempotency_key.strip():
        raise ValueError("idempotency_key must not be blank")
    if not decision.approved:
        raise ValueError("cannot build an execution plan from a denied RiskDecision")
    if decision.operation_kind is None or not decision.intents:
        raise ValueError("approved RiskDecision is missing executable intents")

    envelope = ExecutionEnvelope.placement(
        logical_operation_id=logical_operation_id,
        operation_kind=decision.operation_kind,
        sink_mode=mode,
        idempotency_key=idempotency_key,
        intents=decision.intents,
        created_monotonic_ns=clock_ns(),
        relationship_constraint=decision.relationship_constraint,
    )
    return ExecutionPlan(envelope=envelope, intents=decision.intents)
