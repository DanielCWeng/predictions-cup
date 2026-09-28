"""Execution-plan contracts shared by SHADOW and LIVE sinks."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from enum import StrEnum

from predictions_cup.runtime.models import OrderAction, OutcomeSide, ticks_to_limit_price


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
class ExecutionAudit:
    """Decision metadata required for later signal/latency/PnL attribution."""

    strategy_family: str
    strategy_id: str
    signal_value: float
    fair_value: float | None
    decision_observation_ns: int
    decision_monotonic_ns: int

    def __post_init__(self) -> None:
        if not self.strategy_family.strip():
            raise ValueError("strategy_family must not be blank")
        if not self.strategy_id.strip():
            raise ValueError("strategy_id must not be blank")
        if not math.isfinite(self.signal_value):
            raise ValueError("signal_value must be finite")
        if self.fair_value is not None:
            if not math.isfinite(self.fair_value):
                raise ValueError("fair_value must be finite")
            if not 0.0 <= self.fair_value <= 1.0:
                raise ValueError("fair_value must be within probability support")
        if self.decision_observation_ns < 0:
            raise ValueError("decision_observation_ns must be non-negative")
        if self.decision_monotonic_ns < 0:
            raise ValueError("decision_monotonic_ns must be non-negative")


@dataclass(frozen=True, slots=True)
class ExecutionEnvelope:
    logical_operation_id: str
    tournament_id: str
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
        tournament_ids = {intent.tournament_id for intent in intents}
        if len(tournament_ids) != 1:
            raise ValueError("placement intents must share one tournament_id")
        tournament_id = next(iter(tournament_ids))
        if not tournament_id.strip():
            raise ValueError("placement requires a non-blank tournament_id")
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
            tournament_id=tournament_id,
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
    def cancellation(
        cls,
        *,
        logical_operation_id: str,
        operation_kind: OperationKind,
        sink_mode: ExecutionMode,
        created_monotonic_ns: int,
        order_id: int | None = None,
        tournament_id: str | None = None,
        exchange_id: str | None = None,
        market_id: str | None = None,
    ) -> ExecutionEnvelope:
        if tournament_id is None or not tournament_id.strip():
            raise ValueError("cancellation requires explicit tournament_id")
        if operation_kind is OperationKind.SINGLE_CANCELLATION:
            if order_id is None or order_id <= 0:
                raise ValueError("single cancellation requires a positive order_id")
            payload: dict[str, object] = {"orderId": order_id}
        elif operation_kind is OperationKind.CANCEL_ALL:
            if exchange_id is not None and market_id is not None:
                raise ValueError("exchange_id and market_id are mutually exclusive")
            payload = {}
            if tournament_id is not None:
                payload["tournamentId"] = tournament_id
            if exchange_id is not None:
                payload["exchangeId"] = exchange_id
            if market_id is not None:
                payload["marketId"] = market_id
        else:
            raise ValueError("cancellation factory requires a cancellation operation kind")
        payload_json = json.dumps(payload, separators=(",", ":"), ensure_ascii=True)
        return cls(
            logical_operation_id=logical_operation_id,
            tournament_id=tournament_id,
            operation_kind=operation_kind,
            sink_mode=sink_mode,
            idempotency_key=None,
            payload_json=payload_json,
            payload_sha256=hashlib.sha256(payload_json.encode("utf-8")).hexdigest(),
            intent_ids=(),
            lifecycle_state=LifecycleState.CANCEL_PENDING,
            created_monotonic_ns=created_monotonic_ns,
        )

    @classmethod
    def persisted(
        cls,
        *,
        logical_operation_id: str,
        tournament_id: str,
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
        if not tournament_id.strip():
            raise ValueError("persisted execution requires tournament_id")
        return cls(
            logical_operation_id=logical_operation_id,
            tournament_id=tournament_id,
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


_ALLOWED_LIFECYCLE_TRANSITIONS: dict[LifecycleState, frozenset[LifecycleState]] = {
    LifecycleState.PENDING: frozenset({
        LifecycleState.ACKED,
        LifecycleState.OPEN,
        LifecycleState.PARTIALLY_FILLED,
        LifecycleState.FILLED,
        LifecycleState.UNCERTAIN,
        LifecycleState.REJECTED,
        LifecycleState.RECONCILING,
    }),
    LifecycleState.ACKED: frozenset({
        LifecycleState.OPEN,
        LifecycleState.PARTIALLY_FILLED,
        LifecycleState.FILLED,
        LifecycleState.UNCERTAIN,
        LifecycleState.RECONCILING,
        LifecycleState.REJECTED,
    }),
    LifecycleState.OPEN: frozenset({
        LifecycleState.PARTIALLY_FILLED,
        LifecycleState.FILLED,
        LifecycleState.CANCEL_PENDING,
        LifecycleState.CANCELLED,
        LifecycleState.UNCERTAIN,
        LifecycleState.RECONCILING,
    }),
    LifecycleState.PARTIALLY_FILLED: frozenset({
        LifecycleState.FILLED,
        LifecycleState.CANCEL_PENDING,
        LifecycleState.CANCELLED,
        LifecycleState.UNCERTAIN,
        LifecycleState.RECONCILING,
    }),
    LifecycleState.CANCEL_PENDING: frozenset({
        LifecycleState.CANCELLED,
        LifecycleState.UNCERTAIN,
        LifecycleState.RECONCILING,
        LifecycleState.REJECTED,
    }),
    LifecycleState.UNCERTAIN: frozenset({
        LifecycleState.RECONCILING,
        LifecycleState.RECONCILED,
        LifecycleState.OPEN,
        LifecycleState.PARTIALLY_FILLED,
        LifecycleState.FILLED,
        LifecycleState.CANCELLED,
        LifecycleState.REJECTED,
    }),
    LifecycleState.RECONCILING: frozenset({
        LifecycleState.RECONCILED,
        LifecycleState.OPEN,
        LifecycleState.PARTIALLY_FILLED,
        LifecycleState.FILLED,
        LifecycleState.CANCELLED,
        LifecycleState.UNCERTAIN,
        LifecycleState.REJECTED,
    }),
    LifecycleState.RECONCILED: frozenset(),
    LifecycleState.FILLED: frozenset(),
    LifecycleState.CANCELLED: frozenset(),
    LifecycleState.REJECTED: frozenset(),
}


def lifecycle_transition_allowed(
    current: LifecycleState,
    target: LifecycleState,
) -> bool:
    return target == current or target in _ALLOWED_LIFECYCLE_TRANSITIONS[current]


@dataclass(frozen=True, slots=True)
class ExecutionEvent:
    logical_operation_id: str
    state: LifecycleState
    observed_monotonic_ns: int
    simulated: bool
    detail: str | None = None
