"""Versioned LIVE-LEARN outcome and evidence contracts."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from types import MappingProxyType


class OutcomeStatus(StrEnum):
    MATURED_SCORED = "MATURED_SCORED"
    HORIZON_NOT_MATURED = "HORIZON_NOT_MATURED"
    SOURCE_UNAVAILABLE = "SOURCE_UNAVAILABLE"
    STALE_UNTRUSTED_EVIDENCE = "STALE_UNTRUSTED_EVIDENCE"
    EXECUTION_EVIDENCE_UNAVAILABLE = "EXECUTION_EVIDENCE_UNAVAILABLE"
    UNSUPPORTED_SCORE_SEMANTICS = "UNSUPPORTED_SCORE_SEMANTICS"
    DECISION_ABSTAINED = "DECISION_ABSTAINED"
    INSUFFICIENT_HISTORY = "INSUFFICIENT_HISTORY"


@dataclass(frozen=True, slots=True)
class MarketEvidence:
    snapshot_id: str
    exchange_id: str
    market_id: str
    observed_at: datetime
    source_id: str
    source_observed_at: datetime
    source_monotonic_ns: int
    best_bid: float | None
    best_ask: float | None
    midpoint: float | None
    trusted: bool
    freshness_seconds: float
    price_convention: str = "YES_PROBABILITY"

    def __post_init__(self) -> None:
        for name in ("snapshot_id", "exchange_id", "market_id", "source_id"):
            if not str(getattr(self, name)).strip():
                raise ValueError(f"{name} must not be blank")
        for timestamp in (self.observed_at, self.source_observed_at):
            if timestamp.tzinfo is None or timestamp.utcoffset() is None:
                raise ValueError("evidence timestamps must be timezone-aware")
        if self.source_monotonic_ns < 0 or self.freshness_seconds < 0.0:
            raise ValueError("evidence timing values must be non-negative")
        for price in (self.best_bid, self.best_ask, self.midpoint):
            if price is not None and (
                not math.isfinite(price) or not 0.0 <= price <= 1.0
            ):
                raise ValueError("evidence prices must be finite within [0,1]")


@dataclass(frozen=True, slots=True)
class FillEvidence:
    evidence_id: str
    logical_operation_id: str
    exchange_order_id: str | None
    exchange_id: str
    action: str
    quantity: float
    price: float | None
    filled_at: datetime | None
    observed_monotonic_ns: int

    def __post_init__(self) -> None:
        if not self.evidence_id.strip() or not self.logical_operation_id.strip():
            raise ValueError("fill evidence identity must not be blank")
        if self.action not in {"buy", "sell"}:
            raise ValueError("fill action must be buy or sell")
        if not math.isfinite(self.quantity) or self.quantity <= 0.0:
            raise ValueError("fill quantity must be positive and finite")
        if self.price is not None and (
            not math.isfinite(self.price) or not 0.0 <= self.price <= 1.0
        ):
            raise ValueError("fill price must be finite within [0,1]")
        if self.filled_at is not None and (
            self.filled_at.tzinfo is None or self.filled_at.utcoffset() is None
        ):
            raise ValueError("filled_at must be timezone-aware")
        if self.observed_monotonic_ns < 0:
            raise ValueError("fill observation time must be non-negative")


@dataclass(frozen=True, slots=True)
class ExecutionEvidence:
    supported: bool
    reason: str | None
    planned_quantity: float
    fills: tuple[FillEvidence, ...] = ()
    evidence_source_ids: tuple[str, ...] = ()
    execution_mode: str | None = None

    def __post_init__(self) -> None:
        if not math.isfinite(self.planned_quantity) or self.planned_quantity < 0.0:
            raise ValueError("planned_quantity must be finite and non-negative")
        if self.supported and self.planned_quantity <= 0.0:
            raise ValueError("supported execution evidence requires planned quantity")


@dataclass(frozen=True, slots=True)
class DecisionOutcome:
    outcome_id: str
    decision_id: str
    candidate_id: str
    candidate_version: str
    strategy_family: str
    input_snapshot_id: str
    scoring_spec_id: str
    scoring_spec_version: str
    horizon_seconds: int
    maturity_at: datetime
    evidence_observed_at: datetime | None
    evidence_source_ids: tuple[str, ...]
    outcome_status: OutcomeStatus
    component_status: Mapping[str, str]
    metric_values: Mapping[str, float]
    dimensions: Mapping[str, str]
    initial_price: float | None
    future_price: float | None
    price_source: str | None
    source_timestamp: datetime | None
    source_freshness_seconds: float | None
    source_trusted: bool | None
    price_convention: str
    missing_reason: str | None = None
    schema_version: int = 1

    def __post_init__(self) -> None:
        identities = (
            self.outcome_id,
            self.decision_id,
            self.candidate_id,
            self.candidate_version,
            self.strategy_family,
            self.input_snapshot_id,
            self.scoring_spec_id,
            self.scoring_spec_version,
            self.price_convention,
        )
        if any(not value.strip() for value in identities):
            raise ValueError("outcome identity/version fields must not be blank")
        if self.horizon_seconds <= 0:
            raise ValueError("outcome horizon must be positive")
        for timestamp in (
            self.maturity_at,
            self.evidence_observed_at,
            self.source_timestamp,
        ):
            if timestamp is not None and (
                timestamp.tzinfo is None or timestamp.utcoffset() is None
            ):
                raise ValueError("outcome timestamps must be timezone-aware")
        for price in (self.initial_price, self.future_price):
            if price is not None and (
                not math.isfinite(price) or not 0.0 <= price <= 1.0
            ):
                raise ValueError("outcome prices must be finite within [0,1]")
        if self.source_freshness_seconds is not None and (
            not math.isfinite(self.source_freshness_seconds)
            or self.source_freshness_seconds < 0.0
        ):
            raise ValueError("source freshness must be finite and non-negative")
        metrics = dict(self.metric_values)
        if any(not math.isfinite(value) for value in metrics.values()):
            raise ValueError("outcome metrics must be finite")
        statuses = dict(self.component_status)
        dimensions = dict(self.dimensions)
        json.dumps(statuses, sort_keys=True, allow_nan=False)
        json.dumps(dimensions, sort_keys=True, allow_nan=False)
        object.__setattr__(self, "metric_values", MappingProxyType(metrics))
        object.__setattr__(self, "component_status", MappingProxyType(statuses))
        object.__setattr__(self, "dimensions", MappingProxyType(dimensions))


def outcome_id_for(
    decision_id: str,
    scoring_spec_id: str,
    scoring_spec_version: str,
    horizon_seconds: int,
) -> str:
    raw = (
        f"{decision_id}|{scoring_spec_id}|{scoring_spec_version}|{horizon_seconds}"
    ).encode()
    return "llo_" + hashlib.sha256(raw).hexdigest()[:32]


def outcome_record(outcome: DecisionOutcome) -> dict[str, object]:
    return {
        "event_type": "decision_outcome",
        "schema_version": outcome.schema_version,
        "outcome_id": outcome.outcome_id,
        "decision_id": outcome.decision_id,
        "candidate_id": outcome.candidate_id,
        "candidate_version": outcome.candidate_version,
        "strategy_family": outcome.strategy_family,
        "input_snapshot_id": outcome.input_snapshot_id,
        "scoring_spec_id": outcome.scoring_spec_id,
        "scoring_spec_version": outcome.scoring_spec_version,
        "horizon_seconds": outcome.horizon_seconds,
        "maturity_at": outcome.maturity_at.astimezone(UTC).isoformat(),
        "evidence_observed_at": (
            None
            if outcome.evidence_observed_at is None
            else outcome.evidence_observed_at.astimezone(UTC).isoformat()
        ),
        "evidence_source_ids": list(outcome.evidence_source_ids),
        "outcome_status": outcome.outcome_status.value,
        "component_status": dict(outcome.component_status),
        "metric_values": dict(outcome.metric_values),
        "dimensions": dict(outcome.dimensions),
        "initial_price": outcome.initial_price,
        "future_price": outcome.future_price,
        "price_source": outcome.price_source,
        "source_timestamp": (
            None
            if outcome.source_timestamp is None
            else outcome.source_timestamp.astimezone(UTC).isoformat()
        ),
        "source_freshness_seconds": outcome.source_freshness_seconds,
        "source_trusted": outcome.source_trusted,
        "price_convention": outcome.price_convention,
        "missing_reason": outcome.missing_reason,
    }


def outcome_from_record(record: Mapping[str, object]) -> DecisionOutcome:
    evidence_at = record.get("evidence_observed_at")
    source_at = record.get("source_timestamp")
    evidence_source_ids = _record_sequence(
        record.get("evidence_source_ids", ())
    )
    component_status = _record_mapping(record.get("component_status", {}))
    metric_values = _record_mapping(record.get("metric_values", {}))
    dimensions = _record_mapping(record.get("dimensions", {}))
    return DecisionOutcome(
        outcome_id=str(record["outcome_id"]),
        decision_id=str(record["decision_id"]),
        candidate_id=str(record["candidate_id"]),
        candidate_version=str(record["candidate_version"]),
        strategy_family=str(record["strategy_family"]),
        input_snapshot_id=str(record["input_snapshot_id"]),
        scoring_spec_id=str(record["scoring_spec_id"]),
        scoring_spec_version=str(record["scoring_spec_version"]),
        horizon_seconds=_record_int(record["horizon_seconds"]),
        maturity_at=datetime.fromisoformat(str(record["maturity_at"])),
        evidence_observed_at=(
            None if evidence_at is None else datetime.fromisoformat(str(evidence_at))
        ),
        evidence_source_ids=tuple(str(value) for value in evidence_source_ids),
        outcome_status=OutcomeStatus(str(record["outcome_status"])),
        component_status={
            str(key): str(value) for key, value in component_status.items()
        },
        metric_values={
            str(key): _record_float(value) for key, value in metric_values.items()
        },
        dimensions={
            str(key): str(value) for key, value in dimensions.items()
        },
        initial_price=_optional_record_float(record.get("initial_price")),
        future_price=_optional_record_float(record.get("future_price")),
        price_source=(
            None if record.get("price_source") is None else str(record["price_source"])
        ),
        source_timestamp=(
            None if source_at is None else datetime.fromisoformat(str(source_at))
        ),
        source_freshness_seconds=_optional_record_float(
            record.get("source_freshness_seconds")
        ),
        source_trusted=_optional_record_bool(record.get("source_trusted")),
        price_convention=str(record.get("price_convention", "YES_PROBABILITY")),
        missing_reason=(
            None if record.get("missing_reason") is None else str(record["missing_reason"])
        ),
        schema_version=_record_int(record.get("schema_version", 1)),
    )


def _record_sequence(value: object) -> Sequence[object]:
    if not isinstance(value, (list, tuple)):
        raise ValueError("persisted sequence field has invalid type")
    return value


def _record_mapping(value: object) -> Mapping[object, object]:
    if not isinstance(value, Mapping):
        raise ValueError("persisted mapping field has invalid type")
    return value


def _record_int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise ValueError("persisted integer field has invalid type")
    return int(value)


def _record_float(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise ValueError("persisted numeric field has invalid type")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("persisted numeric field must be finite")
    return result


def _optional_record_float(value: object) -> float | None:
    return None if value is None else _record_float(value)


def _optional_record_bool(value: object) -> bool | None:
    if value is None:
        return None
    if not isinstance(value, bool):
        raise ValueError("persisted boolean field has invalid type")
    return value
