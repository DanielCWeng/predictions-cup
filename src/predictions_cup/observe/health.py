"""Typed externally consumable OBSERVE-001 health surface."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Mapping, Protocol

from predictions_cup.observe.emitter import EmitterHealth


class ObservationHealthState(StrEnum):
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    BLOCKED = "BLOCKED"


@dataclass(frozen=True, slots=True)
class CaptureWriterHealth:
    writer_alive: bool
    queue_depth: int
    queue_capacity: int
    queue_high_water: int
    written_rows: int
    written_shards: int
    dropped_rows: int
    storage_failures: int
    last_write_at: datetime | None


@dataclass(frozen=True, slots=True)
class ObservationHealthSnapshot:
    state: ObservationHealthState
    reasons: tuple[str, ...]
    emitter: EmitterHealth
    capture: CaptureWriterHealth

    @property
    def degraded(self) -> bool:
        return self.state is not ObservationHealthState.HEALTHY

    @property
    def blocked(self) -> bool:
        return self.state is ObservationHealthState.BLOCKED

    def to_dict(self) -> dict[str, object]:
        return {
            "state": self.state.value,
            "reasons": list(self.reasons),
            "emitter": {
                "queue_depth": self.emitter.queue_depth,
                "queue_capacity": self.emitter.queue_capacity,
                "queue_high_water": self.emitter.queue_high_water,
                "accepted": self.emitter.accepted,
                "dropped": self.emitter.dropped,
                "sink_failures": self.emitter.sink_failures,
                "worker_alive": self.emitter.worker_alive,
            },
            "capture": {
                "writer_alive": self.capture.writer_alive,
                "queue_depth": self.capture.queue_depth,
                "queue_capacity": self.capture.queue_capacity,
                "queue_high_water": self.capture.queue_high_water,
                "written_rows": self.capture.written_rows,
                "written_shards": self.capture.written_shards,
                "dropped_rows": self.capture.dropped_rows,
                "storage_failures": self.capture.storage_failures,
                "last_write_at": (
                    None
                    if self.capture.last_write_at is None
                    else self.capture.last_write_at.isoformat()
                ),
            },
        }


class EmitterHealthSource(Protocol):
    def health(self) -> EmitterHealth: ...


class CaptureHealthSource(Protocol):
    def capture_health_snapshot(self) -> Mapping[str, object]: ...


class ObservationHealthProvider:
    """Small read-only health adapter for FULLSTACK/operations composition."""

    def __init__(
        self,
        emitter: EmitterHealthSource,
        capture: CaptureHealthSource,
    ) -> None:
        self._emitter = emitter
        self._capture = capture

    def health(self) -> ObservationHealthSnapshot:
        emitter = self._emitter.health()
        capture = _capture_health(self._capture.capture_health_snapshot())

        degraded: list[str] = []
        blocked: list[str] = []
        if emitter.dropped > 0:
            degraded.append("EMITTER_DROPPED_OBSERVATIONS")
        if emitter.sink_failures > 0:
            degraded.append("EMITTER_SINK_FAILURES")
        if capture.dropped_rows > 0:
            degraded.append("CAPTURE_DROPPED_ROWS")
        if capture.storage_failures > 0:
            blocked.append("CAPTURE_STORAGE_FAILURE")
        if not emitter.worker_alive:
            blocked.append("EMITTER_WORKER_NOT_ALIVE")
        if not capture.writer_alive:
            blocked.append("CAPTURE_WRITER_NOT_ALIVE")

        reasons = tuple((*blocked, *degraded))
        if blocked:
            state = ObservationHealthState.BLOCKED
        elif degraded:
            state = ObservationHealthState.DEGRADED
        else:
            state = ObservationHealthState.HEALTHY
        return ObservationHealthSnapshot(state, reasons, emitter, capture)


def _capture_health(snapshot: Mapping[str, object]) -> CaptureWriterHealth:
    last_write_at = snapshot.get("last_write_at")
    return CaptureWriterHealth(
        writer_alive=_bool(snapshot, "writer_alive"),
        queue_depth=_int(snapshot, "queue_depth"),
        queue_capacity=_int(snapshot, "queue_capacity"),
        queue_high_water=_int(snapshot, "queue_high_water"),
        written_rows=_int(snapshot, "written_rows"),
        written_shards=_int(snapshot, "written_shards"),
        dropped_rows=_int(snapshot, "dropped_rows"),
        storage_failures=_int(snapshot, "storage_failures"),
        last_write_at=last_write_at if isinstance(last_write_at, datetime) else None,
    )


def _int(snapshot: Mapping[str, object], key: str) -> int:
    value = snapshot.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"capture health {key} must be int")
    return value


def _bool(snapshot: Mapping[str, object], key: str) -> bool:
    value = snapshot.get(key)
    if not isinstance(value, bool):
        raise TypeError(f"capture health {key} must be bool")
    return value
