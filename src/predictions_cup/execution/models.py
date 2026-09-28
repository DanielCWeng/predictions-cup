"""Execution-plan contracts shared by SHADOW and LIVE sinks."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import StrEnum

from predictions_cup.runtime import OrderAction, OutcomeSide, ticks_to_limit_price


class ExecutionMode(StrEnum):
    SHADOW = "SHADOW"
    LIVE = "LIVE"


class OperationKind(StrEnum):
    SINGLE_PLACEMENT = "single_placement"
    BEST_EFFORT_BATCH = "best_effort_batch"
    ATOMIC_MULTI_LEG = "atomic_multi_leg"
    SINGLE_CANCELLATION = "single_cancellation"
    CANCEL_ALL = "cancel_all"


class LifecycleState(StrEnum):
    PENDING = "PENDING"
    ACKED = "ACKED"
    OPEN = "OPEN"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCEL_PENDING = "CANCEL_PENDING"
    CANCELLED = "CANCELLED"
    UNCERTAIN = "UNCERTAIN"
    RECONCILING = "RECONCILING"
    RECONCILED = "RECONCILED"
    REJECTED = "REJECTED"


@dataclass(frozen=True, slots=True)
class RuntimeOrderIntent:
    intent_id: str
    exchange_id: str
    market_id: str
    tournament_id: str
    outcome_side: OutcomeSide
    action: OrderAction
    quantity: int
    limit_price_ticks: int | None
    strategy_id: str
    decision_observation_ns: int

    @property
    def is_market(self) -> bool:
        return self.limit_price_ticks is None

    def wire_order(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "exchangeId": self.exchange_id,
            "side": self.outcome_side.value,
            "action": self.action.value,
            "quantity": self.quantity,
        }
        if self.limit_price_ticks is not None:
            payload["price"] = float(ticks_to_limit_price(self.limit_price_ticks))
        payload["tournamentId"] = self.tournament_id
        return payload


@dataclass(frozen=True, slots=True)
class ExecutionEnvelope:
    logical_operation_id: str
    operation_kind: OperationKind
    sink_mode: ExecutionMode
    idempotency_key: str | None
    payload_json: str
    payload_sha256: str
    intent_ids: tuple[str, ...]
    lifecycle_state: LifecycleState
    created_monotonic_ns: int
    relationship_constraint: str | None = None

    @classmethod
    def placement(
        cls,
        *,
        logical_operation_id: str,
        operation_kind: OperationKind,
        sink_mode: ExecutionMode,
        idempotency_key: str,
        intents: tuple[RuntimeOrderIntent, ...],
        created_monotonic_ns: int,
        relationship_constraint: str | None = None,
    ) -> ExecutionEnvelope:
        if not idempotency_key.strip():
            raise ValueError("placement requires a non-blank idempotency key")
        if operation_kind is OperationKind.SINGLE_PLACEMENT:
            if len(intents) != 1:
                raise ValueError("single placement requires exactly one intent")
            payload = {"idempotencyKey": idempotency_key, **intents[0].wire_order()}
        elif operation_kind is OperationKind.BEST_EFFORT_BATCH:
            if not 1 <= len(intents) <= 50:
                raise ValueError("best-effort batch requires 1..50 intents")
            payload = {
                "idempotencyKey": idempotency_key,
                "orders": [intent.wire_order() for intent in intents],
            }
        elif operation_kind is OperationKind.ATOMIC_MULTI_LEG:
            if not 1 <= len(intents) <= 10:
                raise ValueError("atomic multi-leg requires 1..10 intents")
            payload = {
                "legs": [intent.wire_order() for intent in intents],
                "idempotencyKey": idempotency_key,
            }
            if relationship_constraint is not None:
                payload["relationshipConstraint"] = relationship_constraint
        else:
            raise ValueError("placement factory only accepts placement operation kinds")
        payload_json = json.dumps(payload, separators=(",", ":"), ensure_ascii=True)
        return cls(
            logical_operation_id=logical_operation_id,
            operation_kind=operation_kind,
            sink_mode=sink_mode,
            idempotency_key=idempotency_key,
            payload_json=payload_json,
            payload_sha256=hashlib.sha256(payload_json.encode("utf-8")).hexdigest(),
            intent_ids=tuple(intent.intent_id for intent in intents),
            lifecycle_state=LifecycleState.PENDING,
            created_monotonic_ns=created_monotonic_ns,
            relationship_constraint=relationship_constraint,
        )

    @classmethod
    def persisted(
        cls,
        *,
        logical_operation_id: str,
        operation_kind: OperationKind,
        sink_mode: ExecutionMode,
        idempotency_key: str | None,
        payload_json: str,
        payload_sha256: str,
        intent_ids: tuple[str, ...],
        lifecycle_state: LifecycleState,
        created_monotonic_ns: int,
        relationship_constraint: str | None,
    ) -> ExecutionEnvelope:
        return cls(
            logical_operation_id=logical_operation_id,
            operation_kind=operation_kind,
            sink_mode=sink_mode,
            idempotency_key=idempotency_key,
            payload_json=payload_json,
            payload_sha256=payload_sha256,
            intent_ids=intent_ids,
            lifecycle_state=lifecycle_state,
            created_monotonic_ns=created_monotonic_ns,
            relationship_constraint=relationship_constraint,
        )


@dataclass(frozen=True, slots=True)
class ExecutionEvent:
    logical_operation_id: str
    state: LifecycleState
    observed_monotonic_ns: int
    simulated: bool
    detail: str | None = None
