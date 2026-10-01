"""Append-only event persistence for SHADOW-002 snapshots and decisions."""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from collections import deque
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter_ns
from typing import Protocol

import predictions_cup.sig.launch_storage as capture_storage
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
    write_batches: int
    write_latency_p95_ns: int | None
    dropped_decisions: int = 0
    retained_decisions: int = 0


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
            write_batches=0,
            write_latency_p95_ns=None,
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

    def __init__(
        self,
        path: Path,
        *,
        queue_capacity: int = 65_536,
        batch_size: int = 256,
    ) -> None:
        if queue_capacity <= 0:
            raise ValueError("persistence queue capacity must be positive")
        if batch_size <= 0:
            raise ValueError("persistence batch size must be positive")
        self._path = path
        self._queue: asyncio.Queue[PersistItem] = asyncio.Queue(maxsize=queue_capacity)
        self._batch_size = batch_size
        self._writer: asyncio.Task[None] | None = None
        self._started = False
        self._closed = False
        self._persisted_events = 0
        self._failures = 0
        self._last_error: str | None = None
        self._queue_high_water = 0
        self._write_batches = 0
        self._write_latencies_ns: deque[int] = deque(maxlen=2048)

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
            write_batches=self._write_batches,
            write_latency_p95_ns=_percentile(
                tuple(self._write_latencies_ns),
                0.95,
            ),
        )

    async def _enqueue(self, item: CanonicalShadowSnapshot | CandidateDecision) -> None:
        self._require_started()
        if self._last_error is not None:
            raise RuntimeError(f"SHADOW persistence unhealthy: {self._last_error}")
        await self._queue.put(item)
        self._queue_high_water = max(self._queue_high_water, self._queue.qsize())

    async def _writer_loop(self) -> None:
        while True:
            first = await self._queue.get()
            if isinstance(first, _Stop):
                self._queue.task_done()
                return

            batch: list[CanonicalShadowSnapshot | CandidateDecision] = [first]
            while len(batch) < self._batch_size:
                try:
                    candidate = self._queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                if isinstance(candidate, _Stop):
                    self._queue.task_done()
                    raise RuntimeError("stop marker reached before persistence queue drained")
                batch.append(candidate)

            started = perf_counter_ns()
            try:
                await asyncio.to_thread(self._append_events, batch)
            except Exception as exc:  # pragma: no cover - filesystem failure path
                self._failures += 1
                self._last_error = f"{type(exc).__name__}:{exc}"
            else:
                self._persisted_events += len(batch)
                self._write_batches += 1
                self._write_latencies_ns.append(perf_counter_ns() - started)
            finally:
                for _ in batch:
                    self._queue.task_done()

    def _append_events(
        self,
        items: Sequence[CanonicalShadowSnapshot | CandidateDecision],
    ) -> None:
        lines = "".join(
            json.dumps(
                event_record(item),
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
            + "\n"
            for item in items
        )
        with self._path.open("a", encoding="utf-8") as handle:
            handle.write(lines)
            handle.flush()
            os.fsync(handle.fileno())

    def _require_started(self) -> None:
        if not self._started:
            raise RuntimeError("event store is not started")


class CaptureStrategyEventStore:
    """Mirror CandidateDecision into CAPTURE-001's canonical strategy_events stream."""

    def __init__(
        self,
        research_root: Path,
        *,
        queue_capacity: int = 200_000,
        shard_seconds: int = 60,
        max_rows_per_shard: int = 5_000,
        session_id: str | None = None,
    ) -> None:
        self._research_root = research_root
        self._queue_capacity = queue_capacity
        self._shard_seconds = shard_seconds
        self._max_rows_per_shard = max_rows_per_shard
        self._session_id = session_id or uuid.uuid4().hex
        self._sink: capture_storage.ImmutableCaptureSink | None = None
        self._accepted_events = 0
        self._failures = 0
        self._last_error: str | None = None

    async def start(self) -> None:
        if self._sink is not None:
            return
        self._sink = capture_storage.ImmutableCaptureSink(
            self._research_root,
            shard_seconds=self._shard_seconds,
            max_rows_per_shard=self._max_rows_per_shard,
            queue_max=self._queue_capacity,
        )

    async def persist_snapshot(self, snapshot: CanonicalShadowSnapshot) -> None:
        del snapshot

    async def persist_decision(self, decision: CandidateDecision) -> None:
        sink = self._require_sink()
        payload = decision_semantic_record(decision)
        candidate_payload = dict(decision.candidate_payload)
        try:
            sink.emit(
                "strategy_events",
                {
                    "session_id": self._session_id,
                    "connection_epoch": 0,
                    "schema_version": capture_storage.SCHEMA_VERSION,
                    "event_type": "SHADOW_DECISION",
                    "observed_at": decision.observed_at,
                    "monotonic_ns": decision.monotonic_time,
                    "tournament_id": decision.tournament_id,
                    "exchange_id": decision.exchange_id,
                    "market_id": decision.market_id,
                    "strategy_id": decision.candidate_id,
                    "strategy_version": decision.candidate_version,
                    "fair_value_provider": str(
                        candidate_payload.get("fv_source", decision.candidate_id)
                    ),
                    "fair_value_version": str(
                        candidate_payload.get("fv_version", decision.candidate_version)
                    ),
                    "signal_provider": decision.strategy_family,
                    "signal_version": decision.candidate_version,
                    "payload_json": json.dumps(
                        payload,
                        sort_keys=True,
                        separators=(",", ":"),
                        allow_nan=False,
                    ),
                },
            )
        except Exception as exc:
            self._failures += 1
            self._last_error = f"{type(exc).__name__}:{exc}"
            raise
        self._accepted_events += 1

    async def flush(self) -> None:
        if self._last_error is not None:
            raise RuntimeError(f"CAPTURE strategy mirror unhealthy: {self._last_error}")

    async def close(self) -> None:
        if self._sink is None:
            return
        sink = self._sink
        self._sink = None
        await asyncio.to_thread(sink.close)

    @property
    def health(self) -> PersistenceHealth:
        if self._sink is None:
            return PersistenceHealth(
                healthy=self._last_error is None,
                persisted_events=self._accepted_events,
                failures=self._failures,
                last_error=self._last_error,
                queue_depth=0,
                queue_high_water=0,
                write_batches=0,
                write_latency_p95_ns=None,
            )
        raw = self._sink.health_snapshot()
        storage_failures = _health_int(raw, "storage_failures")
        return PersistenceHealth(
            healthy=self._last_error is None and storage_failures == 0,
            persisted_events=self._accepted_events,
            failures=self._failures + storage_failures,
            last_error=self._last_error,
            queue_depth=_health_int(raw, "queue_depth"),
            queue_high_water=_health_int(raw, "queue_high_water"),
            write_batches=_health_int(raw, "written_shards"),
            write_latency_p95_ns=None,
        )

    def _require_sink(self) -> capture_storage.ImmutableCaptureSink:
        if self._sink is None:
            raise RuntimeError("CAPTURE strategy mirror is not started")
        return self._sink


class CompositeShadowEventStore:
    """Primary durable journal plus one or more canonical evidence mirrors."""

    def __init__(
        self,
        primary: ShadowEventStore,
        mirrors: Sequence[ShadowEventStore] = (),
    ) -> None:
        self._primary = primary
        self._mirrors = tuple(mirrors)

    async def start(self) -> None:
        await self._primary.start()
        for mirror in self._mirrors:
            await mirror.start()

    async def persist_snapshot(self, snapshot: CanonicalShadowSnapshot) -> None:
        await self._primary.persist_snapshot(snapshot)
        for mirror in self._mirrors:
            await mirror.persist_snapshot(snapshot)

    async def persist_decision(self, decision: CandidateDecision) -> None:
        await self._primary.persist_decision(decision)
        for mirror in self._mirrors:
            await mirror.persist_decision(decision)

    async def flush(self) -> None:
        await self._primary.flush()
        for mirror in self._mirrors:
            await mirror.flush()

    async def close(self) -> None:
        errors: list[Exception] = []
        for store in (*reversed(self._mirrors), self._primary):
            try:
                await store.close()
            except Exception as exc:  # pragma: no cover - defensive shutdown
                errors.append(exc)
        if errors:
            raise errors[0]

    @property
    def health(self) -> PersistenceHealth:
        primary = self._primary.health
        mirror_health = tuple(mirror.health for mirror in self._mirrors)
        return PersistenceHealth(
            healthy=primary.healthy and all(item.healthy for item in mirror_health),
            persisted_events=primary.persisted_events,
            failures=primary.failures + sum(item.failures for item in mirror_health),
            last_error=primary.last_error
            or next(
                (item.last_error for item in mirror_health if item.last_error is not None),
                None,
            ),
            queue_depth=max(
                (primary.queue_depth, *(item.queue_depth for item in mirror_health)),
            ),
            queue_high_water=max(
                (
                    primary.queue_high_water,
                    *(item.queue_high_water for item in mirror_health),
                ),
            ),
            write_batches=primary.write_batches,
            write_latency_p95_ns=primary.write_latency_p95_ns,
        )


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


def _percentile(values: tuple[int, ...], fraction: float) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int((len(ordered) - 1) * fraction)))
    return ordered[index]


def _health_int(raw: Mapping[str, object], key: str) -> int:
    value = raw[key]
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"CAPTURE health field {key} must be int")
    return value
