"""Append-only event persistence for SHADOW-002 snapshots and decisions."""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from predictions_cup.shadow.contracts import (
    CandidateDecision,
    CanonicalShadowSnapshot,
    decision_semantic_record,
    maker_snapshot_record,
)


@dataclass(frozen=True, slots=True)
class PersistenceHealth:
    healthy: bool
    persisted_events: int
    failures: int
    last_error: str | None
    queue_depth: int
    queue_high_water: int


class ShadowEventStore(Protocol):
    async def start(self) -> None: ...

    async def persist_snapshot(self, snapshot: CanonicalShadowSnapshot) -> None: ...

    async def persist_decision(self, decision: CandidateDecision) -> None: ...

    async def flush(self) -> None: ...

    async def close(self) -> None: ...

    @property
    def health(self) -> PersistenceHealth: ...


class InMemoryEventStore:
    def __init__(self) -> None:
        self.snapshots: list[CanonicalShadowSnapshot] = []
        self.decisions: list[CandidateDecision] = []
        self._started = False

    async def start(self) -> None:
        self._started = True

    async def persist_snapshot(self, snapshot: CanonicalShadowSnapshot) -> None:
        self._require_started()
        self.snapshots.append(snapshot)

    async def persist_decision(self, decision: CandidateDecision) -> None:
        self._require_started()
        self.decisions.append(decision)

    async def flush(self) -> None:
        self._require_started()

    async def close(self) -> None:
        self._started = False

    @property
    def health(self) -> PersistenceHealth:
        return PersistenceHealth(
            healthy=True,
            persisted_events=len(self.snapshots) + len(self.decisions),
            failures=0,
            last_error=None,
            queue_depth=0,
            queue_high_water=0,
        )

    def _require_started(self) -> None:
        if not self._started:
            raise RuntimeError("event store is not started")


@dataclass(frozen=True, slots=True)
class _Stop:
    pass


PersistItem = CanonicalShadowSnapshot | CandidateDecision | _Stop


class JsonlEventStore:
    """Crash-conscious immutable event log written off the SHADOW hot path."""

    def __init__(self, path: Path, *, queue_capacity: int = 4096) -> None:
        if queue_capacity <= 0:
            raise ValueError("persistence queue capacity must be positive")
        self._path = path
        self._queue: asyncio.Queue[PersistItem] = asyncio.Queue(maxsize=queue_capacity)
        self._writer: asyncio.Task[None] | None = None
        self._started = False
        self._closed = False
        self._persisted_events = 0
        self._failures = 0
        self._last_error: str | None = None
        self._queue_high_water = 0

    async def start(self) -> None:
        if self._closed:
            raise RuntimeError("event store is closed")
        if self._started:
            return
        await asyncio.to_thread(self._path.parent.mkdir, parents=True, exist_ok=True)
        self._started = True
        self._writer = asyncio.create_task(self._writer_loop())

    async def persist_snapshot(self, snapshot: CanonicalShadowSnapshot) -> None:
        await self._enqueue(snapshot)

    async def persist_decision(self, decision: CandidateDecision) -> None:
        await self._enqueue(decision)

    async def flush(self) -> None:
        self._require_started()
        await self._queue.join()
        if self._last_error is not None:
            raise RuntimeError(f"SHADOW persistence unhealthy: {self._last_error}")

    async def close(self) -> None:
        if not self._started:
            self._closed = True
            return
        error: Exception | None = None
        try:
            await self.flush()
        except Exception as exc:  # pragma: no cover - defensive shutdown path
            error = exc
        await self._queue.put(_Stop())
        self._queue_high_water = max(self._queue_high_water, self._queue.qsize())
        if self._writer is not None:
            await self._writer
        self._writer = None
        self._started = False
        self._closed = True
        if error is not None:
            raise error

    @property
    def health(self) -> PersistenceHealth:
        return PersistenceHealth(
            healthy=self._last_error is None,
            persisted_events=self._persisted_events,
            failures=self._failures,
            last_error=self._last_error,
            queue_depth=self._queue.qsize(),
            queue_high_water=self._queue_high_water,
        )

    async def _enqueue(self, item: CanonicalShadowSnapshot | CandidateDecision) -> None:
        self._require_started()
        if self._last_error is not None:
            raise RuntimeError(f"SHADOW persistence unhealthy: {self._last_error}")
        await self._queue.put(item)
        self._queue_high_water = max(self._queue_high_water, self._queue.qsize())

    async def _writer_loop(self) -> None:
        while True:
            item = await self._queue.get()
            try:
                if isinstance(item, _Stop):
                    return
                try:
                    await asyncio.to_thread(self._append_event, item)
                except Exception as exc:  # pragma: no cover - filesystem failure path
                    self._failures += 1
                    self._last_error = f"{type(exc).__name__}:{exc}"
                else:
                    self._persisted_events += 1
            finally:
                self._queue.task_done()

    def _append_event(self, item: CanonicalShadowSnapshot | CandidateDecision) -> None:
        record = event_record(item)
        line = json.dumps(
            record,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ) + "\n"
        with self._path.open("a", encoding="utf-8") as handle:
            handle.write(line)
            handle.flush()
            os.fsync(handle.fileno())

    def _require_started(self) -> None:
        if not self._started:
            raise RuntimeError("event store is not started")


def event_record(
    item: CanonicalShadowSnapshot | CandidateDecision,
) -> dict[str, object]:
    if isinstance(item, CanonicalShadowSnapshot):
        return {
            "event_type": "snapshot",
            "schema_version": item.schema_version,
            "snapshot_id": item.snapshot_id,
            "observed_at": item.observed_at.isoformat(),
            "observed_monotonic_ns": item.observed_monotonic_ns,
            "mapping_version": item.mapping_version,
            "source_revision": item.source_revision,
            "source_provenance": [list(pair) for pair in item.source_provenance],
            "maker": maker_snapshot_record(item.maker),
        }
    record = decision_semantic_record(item)
    record.update(
        {
            "event_type": "decision",
            "compute_started_at": item.compute_started_at,
            "compute_finished_at": item.compute_finished_at,
            "compute_latency_ns": item.compute_latency_ns,
        }
    )
    return record


def read_jsonl_records(path: Path) -> Sequence[dict[str, object]]:
    records: list[dict[str, object]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, raw in enumerate(handle, start=1):
            stripped = raw.strip()
            if not stripped:
                continue
            value = json.loads(stripped)
            if not isinstance(value, dict):
                raise ValueError(f"invalid SHADOW event at line {line_number}")
            records.append(value)
    return records
