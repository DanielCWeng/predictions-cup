"""Small atomic process-boundary status surface for OBSERVE-001 health."""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

from predictions_cup.observe.health import ObservationHealthSnapshot, ObservationHealthState

STATUS_SCHEMA_VERSION = "observe-001-health-v1"


class ObservationStatusState(StrEnum):
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    BLOCKED = "BLOCKED"
    MISSING = "MISSING"
    STALE = "STALE"
    OWNER_MISMATCH = "OWNER_MISMATCH"
    PROCESS_MISMATCH = "PROCESS_MISMATCH"
    INVALID = "INVALID"


@dataclass(frozen=True, slots=True)
class ObservationHealthStatusRead:
    state: ObservationStatusState
    reason: str | None
    observed_at: datetime | None
    process_instance_id: str | None
    owner: str | None
    health: Mapping[str, object] | None

    @property
    def healthy(self) -> bool:
        return self.state is ObservationStatusState.HEALTHY


def default_observation_health_status_path(research_root: Path) -> Path:
    return research_root.parent / "runtime" / "status" / "observe.json"


class ObservationHealthStatusPublisher:
    """Atomically publish bounded-cadence health without becoming a Risk dependency."""

    def __init__(
        self,
        path: Path,
        *,
        process_instance_id: str,
        owner: str,
        min_interval_seconds: float = 1.0,
        wall_clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        monotonic_clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not process_instance_id.strip() or not owner.strip():
            raise ValueError("status process/owner identity must not be blank")
        if min_interval_seconds <= 0:
            raise ValueError("min_interval_seconds must be positive")
        self.path = path
        self.process_instance_id = process_instance_id
        self.owner = owner
        self.min_interval_seconds = min_interval_seconds
        self._wall_clock = wall_clock
        self._monotonic_clock = monotonic_clock
        self._last_attempt_monotonic: float | None = None
        self._last_attempt_signature: tuple[object, ...] | None = None

    def publish(
        self,
        snapshot: ObservationHealthSnapshot,
        *,
        force: bool = False,
    ) -> bool:
        now_mono = self._monotonic_clock()
        signature = (
            snapshot.state.value,
            snapshot.reasons,
            snapshot.emitter.dropped,
            snapshot.emitter.sink_failures,
            snapshot.emitter.worker_alive,
            snapshot.capture.dropped_rows,
            snapshot.capture.storage_failures,
            snapshot.capture.writer_alive,
        )
        changed = signature != self._last_attempt_signature
        due = (
            self._last_attempt_monotonic is None
            or now_mono - self._last_attempt_monotonic >= self.min_interval_seconds
        )
        if not force and not changed and not due:
            return False

        self._last_attempt_monotonic = now_mono
        self._last_attempt_signature = signature
        observed_at = self._wall_clock().astimezone(UTC)
        payload = {
            "schema_version": STATUS_SCHEMA_VERSION,
            "observed_at": observed_at.isoformat(),
            "process_instance_id": self.process_instance_id,
            "owner": self.owner,
            "health": snapshot.to_dict(),
        }
        _atomic_json(self.path, payload)
        return True


def read_observation_health_status(
    path: Path,
    *,
    expected_owner: str,
    max_age_seconds: float,
    expected_process_instance_id: str | None = None,
    now: datetime | None = None,
) -> ObservationHealthStatusRead:
    if max_age_seconds <= 0:
        raise ValueError("max_age_seconds must be positive")
    if not path.exists():
        return ObservationHealthStatusRead(
            ObservationStatusState.MISSING,
            "status_file_missing",
            None,
            None,
            None,
            None,
        )
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise TypeError("status document must be an object")
        if raw.get("schema_version") != STATUS_SCHEMA_VERSION:
            raise ValueError("unsupported status schema")
        owner = _required_str(raw, "owner")
        process_instance_id = _required_str(raw, "process_instance_id")
        observed_at = datetime.fromisoformat(_required_str(raw, "observed_at"))
        if observed_at.tzinfo is None or observed_at.utcoffset() is None:
            raise ValueError("status observed_at must be timezone aware")
        health = raw.get("health")
        if not isinstance(health, dict):
            raise TypeError("status health must be an object")
        health_state = ObservationHealthState(_required_str(health, "state"))
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        return ObservationHealthStatusRead(
            ObservationStatusState.INVALID,
            f"invalid_status:{type(exc).__name__}",
            None,
            None,
            None,
            None,
        )

    if owner != expected_owner:
        return ObservationHealthStatusRead(
            ObservationStatusState.OWNER_MISMATCH,
            "owner_mismatch",
            observed_at,
            process_instance_id,
            owner,
            health,
        )
    if (
        expected_process_instance_id is not None
        and process_instance_id != expected_process_instance_id
    ):
        return ObservationHealthStatusRead(
            ObservationStatusState.PROCESS_MISMATCH,
            "process_instance_mismatch",
            observed_at,
            process_instance_id,
            owner,
            health,
        )

    current = (now or datetime.now(UTC)).astimezone(UTC)
    age_seconds = (current - observed_at.astimezone(UTC)).total_seconds()
    if age_seconds < -1.0 or age_seconds > max_age_seconds:
        return ObservationHealthStatusRead(
            ObservationStatusState.STALE,
            "status_snapshot_stale",
            observed_at,
            process_instance_id,
            owner,
            health,
        )

    return ObservationHealthStatusRead(
        ObservationStatusState(health_state.value),
        None,
        observed_at,
        process_instance_id,
        owner,
        health,
    )


def _atomic_json(path: Path, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    data = json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n"
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def _fsync_directory(path: Path) -> None:
    flags = getattr(os, "O_DIRECTORY", 0)
    descriptor = os.open(path, os.O_RDONLY | flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _required_str(value: Mapping[str, object], key: str) -> str:
    candidate = value.get(key)
    if not isinstance(candidate, str) or not candidate.strip():
        raise TypeError(f"{key} must be a non-blank string")
    return candidate
