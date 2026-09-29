"""Bounded, failure-isolated champion/challenger fan-out for SHADOW-002."""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass, field
from time import monotonic_ns
from typing import Callable

from predictions_cup.shadow.contracts import (
    CandidateOutput,
    CanonicalShadowSnapshot,
    DecisionStatus,
    ShadowCandidate,
    decision_from_output,
    failure_output,
)
from predictions_cup.shadow.persistence import (
    InMemoryEventStore,
    PersistenceHealth,
    ShadowEventStore,
)

ClockNs = Callable[[], int]


@dataclass(frozen=True, slots=True)
class CandidateHealth:
    candidate_id: str
    candidate_version: str
    enabled: bool
    queue_depth: int
    queue_high_water: int
    skipped_states: int
    failure_count: int
    timeout_count: int
    last_success_monotonic_ns: int | None
    last_error: str | None
    evaluation_latency_p50_ns: int | None
    evaluation_latency_p95_ns: int | None
    evaluation_latency_p99_ns: int | None
    queue_delay_p95_ns: int | None
    last_snapshot_age_ns: int | None


@dataclass(frozen=True, slots=True)
class ShadowHealth:
    running: bool
    paused: bool
    last_snapshot_age_ns: int | None
    snapshots_processed: int
    snapshots_dropped_or_coalesced: int
    candidates: tuple[CandidateHealth, ...]
    persistence: PersistenceHealth


@dataclass(frozen=True, slots=True)
class _QueuedSnapshot:
    snapshot: CanonicalShadowSnapshot
    enqueued_ns: int


@dataclass(slots=True)
class _CandidateRuntime:
    candidate: ShadowCandidate
    queue: asyncio.Queue[_QueuedSnapshot]
    timeout_seconds: float
    enabled: bool = True
    task: asyncio.Task[None] | None = None
    queue_high_water: int = 0
    skipped_states: int = 0
    failure_count: int = 0
    timeout_count: int = 0
    last_success_monotonic_ns: int | None = None
    last_error: str | None = None
    last_snapshot_monotonic_ns: int | None = None
    latencies_ns: deque[int] = field(default_factory=lambda: deque(maxlen=2048))
    queue_delays_ns: deque[int] = field(default_factory=lambda: deque(maxlen=2048))


class ShadowBus:
    """Common research/control bus. It has no order-placement capability."""

    def __init__(
        self,
        candidates: Iterable[ShadowCandidate],
        *,
        store: ShadowEventStore | None = None,
        queue_capacity: int = 1,
        candidate_timeout_seconds: float = 0.050,
        trading_enabled: bool = False,
        clock_ns: ClockNs = monotonic_ns,
    ) -> None:
        if trading_enabled:
            raise ValueError("SHADOW-002 requires trading_enabled=false")
        if queue_capacity <= 0:
            raise ValueError("candidate queue capacity must be positive")
        if candidate_timeout_seconds <= 0:
            raise ValueError("candidate timeout must be positive")

        self._clock_ns = clock_ns
        self._store = store or InMemoryEventStore()
        self._runtimes: dict[str, _CandidateRuntime] = {}
        for candidate in candidates:
            if not candidate.candidate_id.strip() or not candidate.candidate_version.strip():
                raise ValueError("candidate identity/version must not be blank")
            if candidate.candidate_id in self._runtimes:
                raise ValueError(f"duplicate candidate_id: {candidate.candidate_id}")
            self._runtimes[candidate.candidate_id] = _CandidateRuntime(
                candidate=candidate,
                queue=asyncio.Queue(maxsize=queue_capacity),
                timeout_seconds=candidate_timeout_seconds,
            )

        self._running = False
        self._paused = False
        self._snapshots_processed = 0
        self._dropped_or_coalesced = 0
        self._last_snapshot_monotonic_ns: int | None = None
        self._last_publish_observed_ns: int | None = None

    async def start(self) -> None:
        if self._running:
            return
        await self._store.start()
        self._running = True
        for runtime in self._runtimes.values():
            runtime.task = asyncio.create_task(
                self._worker(runtime),
                name=f"shadow-{runtime.candidate.candidate_id}",
            )

    async def publish(self, snapshot: CanonicalShadowSnapshot) -> None:
        if not self._running:
            raise RuntimeError("SHADOW bus is not started")
        previous = self._last_publish_observed_ns
        if previous is not None and snapshot.observed_monotonic_ns < previous:
            raise ValueError("out-of-order observable state rejected")
        self._last_publish_observed_ns = snapshot.observed_monotonic_ns
        self._last_snapshot_monotonic_ns = snapshot.observed_monotonic_ns
        self._snapshots_processed += 1

        await self._store.persist_snapshot(snapshot)
        if self._paused:
            self._dropped_or_coalesced += 1
            return

        enqueued_ns = self._clock_ns()
        for runtime in self._runtimes.values():
            if not runtime.enabled:
                continue
            item = _QueuedSnapshot(snapshot=snapshot, enqueued_ns=enqueued_ns)
            if runtime.queue.full():
                try:
                    runtime.queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
                else:
                    runtime.queue.task_done()
                    runtime.skipped_states += 1
                    self._dropped_or_coalesced += 1
            runtime.queue.put_nowait(item)
            runtime.queue_high_water = max(
                runtime.queue_high_water,
                runtime.queue.qsize(),
            )

    async def flush(self) -> None:
        if not self._running:
            return
        await asyncio.gather(
            *(runtime.queue.join() for runtime in self._runtimes.values())
        )
        await self._store.flush()

    async def close(self) -> None:
        if not self._running:
            await self._store.close()
            return
        flush_error: Exception | None = None
        try:
            await self.flush()
        except Exception as exc:  # pragma: no cover - defensive persistence shutdown
            flush_error = exc
        for runtime in self._runtimes.values():
            if runtime.task is not None:
                runtime.task.cancel()
        await asyncio.gather(
            *(
                runtime.task
                for runtime in self._runtimes.values()
                if runtime.task is not None
            ),
            return_exceptions=True,
        )
        for runtime in self._runtimes.values():
            runtime.task = None
        self._running = False
        try:
            await self._store.close()
        finally:
            if flush_error is not None:
                raise flush_error

    def enable_candidate(self, candidate_id: str) -> None:
        runtime = self._candidate(candidate_id)
        runtime.enabled = True
        runtime.last_error = None

    def disable_candidate(self, candidate_id: str) -> None:
        runtime = self._candidate(candidate_id)
        runtime.enabled = False
        while True:
            try:
                runtime.queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            else:
                runtime.queue.task_done()
                runtime.skipped_states += 1
                self._dropped_or_coalesced += 1

    def pause(self) -> None:
        self._paused = True

    def resume(self) -> None:
        self._paused = False

    def health(self) -> ShadowHealth:
        now = self._clock_ns()
        candidates = tuple(
            self._candidate_health(runtime, now)
            for runtime in self._runtimes.values()
        )
        return ShadowHealth(
            running=self._running,
            paused=self._paused,
            last_snapshot_age_ns=_age(
                now,
                self._last_snapshot_monotonic_ns,
            ),
            snapshots_processed=self._snapshots_processed,
            snapshots_dropped_or_coalesced=self._dropped_or_coalesced,
            candidates=candidates,
            persistence=self._store.health,
        )

    async def _worker(self, runtime: _CandidateRuntime) -> None:
        while True:
            item = await runtime.queue.get()
            try:
                if not runtime.enabled:
                    runtime.skipped_states += 1
                    self._dropped_or_coalesced += 1
                    continue
                await self._evaluate(runtime, item)
            finally:
                runtime.queue.task_done()

    async def _evaluate(
        self,
        runtime: _CandidateRuntime,
        item: _QueuedSnapshot,
    ) -> None:
        started = self._clock_ns()
        runtime.queue_delays_ns.append(max(0, started - item.enqueued_ns))
        status_failure = False
        try:
            output = await asyncio.wait_for(
                asyncio.to_thread(runtime.candidate.evaluate, item.snapshot),
                timeout=runtime.timeout_seconds,
            )
        except TimeoutError:
            finished = self._clock_ns()
            runtime.timeout_count += 1
            runtime.failure_count += 1
            runtime.last_error = "timeout"
            status_failure = True
            output = failure_output(
                DecisionStatus.TIMEOUT,
                "candidate_timeout",
            )
        except Exception as exc:
            finished = self._clock_ns()
            runtime.failure_count += 1
            runtime.last_error = f"{type(exc).__name__}:{exc}"
            status_failure = True
            output = failure_output(
                DecisionStatus.EXCEPTION,
                "candidate_exception",
                detail=runtime.last_error,
            )
        else:
            finished = self._clock_ns()
            if not isinstance(output, CandidateOutput):
                output = failure_output(
                    DecisionStatus.INVALID_OUTPUT,
                    "candidate_returned_wrong_type",
                    detail=type(output).__name__,
                )
                runtime.failure_count += 1
                runtime.last_error = "invalid_output_type"
                status_failure = True

        try:
            decision = decision_from_output(
                snapshot=item.snapshot,
                candidate=runtime.candidate,
                output=output,
                compute_started_at=started,
                compute_finished_at=finished,
            )
        except (TypeError, ValueError) as exc:
            runtime.failure_count += 1
            runtime.last_error = f"invalid_output:{type(exc).__name__}:{exc}"
            status_failure = True
            fallback = failure_output(
                DecisionStatus.INVALID_OUTPUT,
                "candidate_output_validation_failed",
                detail=runtime.last_error,
            )
            decision = decision_from_output(
                snapshot=item.snapshot,
                candidate=runtime.candidate,
                output=fallback,
                compute_started_at=started,
                compute_finished_at=finished,
            )

        runtime.latencies_ns.append(max(0, finished - started))
        runtime.last_snapshot_monotonic_ns = item.snapshot.observed_monotonic_ns
        try:
            await self._store.persist_decision(decision)
        except Exception as exc:
            runtime.failure_count += 1
            runtime.last_error = f"persistence:{type(exc).__name__}:{exc}"
            return

        if not status_failure and decision.decision_status not in {
            DecisionStatus.EXCEPTION,
            DecisionStatus.TIMEOUT,
            DecisionStatus.INVALID_OUTPUT,
        }:
            runtime.last_success_monotonic_ns = finished
            runtime.last_error = None

    def _candidate_health(
        self,
        runtime: _CandidateRuntime,
        now: int,
    ) -> CandidateHealth:
        values = tuple(runtime.latencies_ns)
        queue_delays = tuple(runtime.queue_delays_ns)
        return CandidateHealth(
            candidate_id=runtime.candidate.candidate_id,
            candidate_version=runtime.candidate.candidate_version,
            enabled=runtime.enabled,
            queue_depth=runtime.queue.qsize(),
            queue_high_water=runtime.queue_high_water,
            skipped_states=runtime.skipped_states,
            failure_count=runtime.failure_count,
            timeout_count=runtime.timeout_count,
            last_success_monotonic_ns=runtime.last_success_monotonic_ns,
            last_error=runtime.last_error,
            evaluation_latency_p50_ns=_percentile(values, 0.50),
            evaluation_latency_p95_ns=_percentile(values, 0.95),
            evaluation_latency_p99_ns=_percentile(values, 0.99),
            queue_delay_p95_ns=_percentile(queue_delays, 0.95),
            last_snapshot_age_ns=_age(now, runtime.last_snapshot_monotonic_ns),
        )

    def _candidate(self, candidate_id: str) -> _CandidateRuntime:
        try:
            return self._runtimes[candidate_id]
        except KeyError as exc:
            raise KeyError(f"unknown SHADOW candidate: {candidate_id}") from exc


def _percentile(values: tuple[int, ...], fraction: float) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math_ceil(fraction * len(ordered)) - 1))
    return ordered[index]


def math_ceil(value: float) -> int:
    integer = int(value)
    return integer if value == integer else integer + 1


def _age(now: int, observed: int | None) -> int | None:
    if observed is None:
        return None
    return max(0, now - observed)
