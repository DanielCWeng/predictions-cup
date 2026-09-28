"""Bounded in-memory latency/counter telemetry for the live decision loop."""

from __future__ import annotations

from collections import Counter, deque
from dataclasses import dataclass
from typing import Final

DEFAULT_TELEMETRY_CAPACITY: Final = 8192


@dataclass(frozen=True, slots=True)
class LatencyObservation:
    name: str
    duration_ns: int

    def __post_init__(self) -> None:
        if self.duration_ns < 0:
            raise ValueError("duration_ns must be non-negative")


@dataclass(frozen=True, slots=True)
class TelemetrySnapshot:
    counters: dict[str, int]
    observations: tuple[LatencyObservation, ...]
    dropped_observations: int


class HotPathTelemetry:
    """Never performs I/O and never blocks on downstream telemetry consumers."""

    def __init__(self, capacity: int = DEFAULT_TELEMETRY_CAPACITY) -> None:
        if capacity <= 0:
            raise ValueError("capacity must be positive")
        self._capacity = capacity
        self._observations: deque[LatencyObservation] = deque(maxlen=capacity)
        self._counters: Counter[str] = Counter()
        self._dropped = 0

    def increment(self, name: str, value: int = 1) -> None:
        if not name.strip():
            raise ValueError("counter name must not be blank")
        self._counters[name] += value

    def observe(self, name: str, duration_ns: int) -> None:
        if len(self._observations) == self._capacity:
            self._dropped += 1
        self._observations.append(LatencyObservation(name=name, duration_ns=duration_ns))

    def snapshot(self) -> TelemetrySnapshot:
        return TelemetrySnapshot(
            counters=dict(self._counters),
            observations=tuple(self._observations),
            dropped_observations=self._dropped,
        )
