"""Typed OBSERVE-001 contracts with no persistence or strategy semantics."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol


class ObservationKind(StrEnum):
    DECISION_OBSERVED = "DECISION_OBSERVED"
    PLAN_CREATED = "PLAN_CREATED"
    REQUEST_ENQUEUED = "REQUEST_ENQUEUED"
    REQUEST_DISPATCHED = "REQUEST_DISPATCHED"
    RESPONSE_RECEIVED = "RESPONSE_RECEIVED"
    RESPONSE_PARSED = "RESPONSE_PARSED"
    ACK = "ACK"
    PARTIAL_FILL = "PARTIAL_FILL"
    FILL = "FILL"
    CANCEL_REQUESTED = "CANCEL_REQUESTED"
    CANCEL_ACK = "CANCEL_ACK"
    REJECTED = "REJECTED"
    UNCERTAIN = "UNCERTAIN"
    RECONCILIATION_STARTED = "RECONCILIATION_STARTED"
    RECONCILIATION_RESOLVED = "RECONCILIATION_RESOLVED"
    RATE_LIMIT = "RATE_LIMIT"
    SERVER_ERROR = "SERVER_ERROR"
    TRANSPORT_EXCEPTION = "TRANSPORT_EXCEPTION"
    RECONNECT_STARTED = "RECONNECT_STARTED"
    RECONNECT_RESOLVED = "RECONNECT_RESOLVED"
    REALTIME_REVISION_GAP = "REALTIME_REVISION_GAP"
    CONTEXT_SNAPSHOT = "CONTEXT_SNAPSHOT"
    QUOTE_PUBLISHED = "QUOTE_PUBLISHED"
    QUOTE_REPLENISHED = "QUOTE_REPLENISHED"
    QUOTE_WITHDRAWN = "QUOTE_WITHDRAWN"


class FieldClassification(StrEnum):
    OBSERVED = "observed"
    NORMALIZED = "normalized"
    DERIVED = "derived"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class VenueObservation:
    """One immutable observation linked to canonical execution identities.

    observed_at is UTC wall time from the emitting process. monotonic_ns is
    meaningful only inside process_instance_id and must not be compared across
    processes or hosts.
    """

    kind: ObservationKind
    observed_at: datetime
    monotonic_ns: int
    process_instance_id: str
    source: str
    source_version: str
    provenance: str
    tournament_id: str | None = None
    market_id: str | None = None
    exchange_id: str | None = None
    strategy_family: str | None = None
    strategy_id: str | None = None
    logical_operation_id: str | None = None
    logical_intent_id: str | None = None
    idempotency_key: str | None = None
    exchange_order_id: str | None = None
    fill_id: str | None = None
    revision: int | None = None
    source_timestamp: datetime | None = None
    status_code: int | None = None
    detail: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        if self.observed_at.tzinfo is None or self.observed_at.utcoffset() is None:
            raise ValueError("observed_at must be timezone aware")
        if self.monotonic_ns < 0:
            raise ValueError("monotonic_ns must be non-negative")
        for name, value in (
            ("process_instance_id", self.process_instance_id),
            ("source", self.source),
            ("source_version", self.source_version),
            ("provenance", self.provenance),
        ):
            if not value.strip():
                raise ValueError(f"{name} must not be blank")
        if self.source_timestamp is not None and (
            self.source_timestamp.tzinfo is None
            or self.source_timestamp.utcoffset() is None
        ):
            raise ValueError("source_timestamp must be timezone aware")

    @property
    def observed_at_utc(self) -> datetime:
        return self.observed_at.astimezone(UTC)


class ObservationEmitter(Protocol):
    """Minimal hot-path dependency. Implementations must not block."""

    def emit(self, observation: VenueObservation) -> bool: ...


class ObservationSink(Protocol):
    """Slow-path sink consumed by a bounded emitter worker."""

    def write(self, observation: VenueObservation) -> None: ...
