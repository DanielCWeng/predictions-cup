"""Atomic process-boundary status publisher for runtime capabilities."""

from __future__ import annotations

import json
import os
import time
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path


class RuntimeStatusPublisher:
    """Publish a small JSON health/status envelope at bounded cadence."""

    def __init__(
        self,
        path: Path,
        *,
        min_interval_seconds: float = 1.0,
    ) -> None:
        if min_interval_seconds <= 0:
            raise ValueError("min_interval_seconds must be positive")
        self.path = path
        self.min_interval_seconds = min_interval_seconds
        self._last_publish_monotonic: float | None = None
        self._last_signature: str | None = None

    def publish(
        self,
        payload: Mapping[str, object],
        *,
        force: bool = False,
    ) -> bool:
        normalized = dict(payload)
        signature = json.dumps(
            normalized,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        now_mono = time.monotonic()
        changed = signature != self._last_signature
        due = (
            self._last_publish_monotonic is None
            or now_mono - self._last_publish_monotonic >= self.min_interval_seconds
        )
        if not force and not changed and not due:
            return False

        envelope = {
            "schema_version": "runtime-capability-status-v1",
            "observed_at": datetime.now(UTC).isoformat(),
            **normalized,
        }
        _atomic_json(self.path, envelope)
        self._last_signature = signature
        self._last_publish_monotonic = now_mono
        return True


def _atomic_json(path: Path, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    data = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str) + "\n"
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
