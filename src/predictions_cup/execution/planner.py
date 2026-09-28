"""Pure conversion from central Risk approval to an executable plan."""

from __future__ import annotations

import hashlib

from predictions_cup.execution.models import ExecutionAudit, ExecutionEnvelope, ExecutionMode
from predictions_cup.execution.sinks import ExecutionPlan
from predictions_cup.risk.core import RiskDecision


def deterministic_idempotency_key(
    logical_operation_id: str,
    operation_kind: str,
) -> str:
    if not logical_operation_id.strip():
        raise ValueError("logical_operation_id must not be blank")
    identity = f"{logical_operation_id}|{operation_kind}".encode()
    return f"pc-{hashlib.sha256(identity).hexdigest()}"


def build_execution_plan(
    decision: RiskDecision,
    *,
    logical_operation_id: str,
    mode: ExecutionMode,
    created_monotonic_ns: int,
) -> ExecutionPlan:
    if not decision.approved or decision.operation_kind is None or not decision.intents:
        raise ValueError("only approved risk decisions can become execution plans")
    if (
        decision.strategy_family is None
        or decision.strategy_id is None
        or decision.signal_value is None
        or decision.decision_observation_ns is None
    ):
        raise ValueError("approved RiskDecision is missing decision audit metadata")
    audit = ExecutionAudit(
        strategy_family=decision.strategy_family,
        strategy_id=decision.strategy_id,
        signal_value=decision.signal_value,
        fair_value=decision.fair_value,
        decision_observation_ns=decision.decision_observation_ns,
        decision_monotonic_ns=created_monotonic_ns,
    )
    idempotency_key = deterministic_idempotency_key(
        logical_operation_id,
        decision.operation_kind.value,
    )
    envelope = ExecutionEnvelope.placement(
        logical_operation_id=logical_operation_id,
        operation_kind=decision.operation_kind,
        sink_mode=mode,
        idempotency_key=idempotency_key,
        intents=decision.intents,
        created_monotonic_ns=created_monotonic_ns,
        relationship_constraint=decision.relationship_constraint,
    )
    return ExecutionPlan(
        envelope=envelope,
        intents=decision.intents,
        audit=audit,
    )
