"""Bounded, failure-isolated champion/challenger fan-out for SHADOW-002."""

from __future__ import annotations

import asyncio
import math
from collections import deque
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from time import monotonic_ns

from predictions_cup.maker.contracts import MakerMarketSnapshot
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
    quarantined: bool
    in_flight: bool
    queue_depth: int
    queue_high_water: int
    skipped_states: int
    failure_count: int
    timeout_count: int
    quarantine_count: int
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
    maker_snapshots_coalesced: int
    ingress_queue_depth: int
    ingress_queue_high_water: int
    ingress_rejected: int
    ingress_error: str | None
    candidates: tuple[CandidateHealth, ...]
    persistence: PersistenceHealth


@dataclass(frozen=True, slots=True)
class _QueuedSnapshot:
    snapshot: CanonicalShadowSnapshot
    enqueued_ns: int


@dataclass(frozen=True, slots=True)
class _MakerIngress:
    maker: MakerMarketSnapshot
    observed_at: datetime
    mapping_version: str
    source_revision: str
    source_provenance: Mapping[str, str]


IngressItem = CanonicalShadowSnapshot | _MakerIngress


@dataclass(slots=True)
class _CandidateRuntime:
    candidate: ShadowCandidate
    queue: asyncio.Queue[_QueuedSnapshot]
    timeout_seconds: float
    enabled: bool = True
    quarantined: bool = False
    task: asyncio.Task[None] | None = None
    in_flight_task: asyncio.Task[CandidateOutput] | None = None
    reaper_task: asyncio.Task[None] | None = None
    queue_high_water: int = 0
    skipped_states: int = 0
    failure_count: int = 0
    timeout_count: int = 0
    quarantine_count: int = 0
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
        queue_capacity: int = 512,
        ingress_capacity: int = 4096,
        candidate_timeout_seconds: float = 0.050,
        minimum_maker_snapshot_interval_seconds: float = 1.0,
        trading_enabled: bool = False,
        clock_ns: ClockNs = monotonic_ns,
    ) -> None:
        if trading_enabled:
            raise ValueError("SHADOW-002 requires trading_enabled=false")
        if queue_capacity <= 0:
            raise ValueError("candidate queue capacity must be positive")
        if ingress_capacity <= 0:
            raise ValueError("ingress queue capacity must be positive")
        if candidate_timeout_seconds <= 0:
            raise ValueError("candidate timeout must be positive")
        if (
            not math.isfinite(minimum_maker_snapshot_interval_seconds)
            or minimum_maker_snapshot_interval_seconds < 0.0
        ):
            raise ValueError("minimum maker snapshot interval must be non-negative")

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

        self._ingress: asyncio.Queue[IngressItem] = asyncio.Queue(maxsize=ingress_capacity)
        self._ingress_task: asyncio.Task[None] | None = None
        self._ingress_high_water = 0
        self._ingress_rejected = 0
        self._ingress_error: str | None = None
        self._running = False
        self._paused = False
        self._snapshots_processed = 0
        self._dropped_or_coalesced = 0
        self._maker_snapshots_coalesced = 0
        self._last_snapshot_monotonic_ns: int | None = None
        self._last_accepted_observed_ns: int | None = None
        self._maker_interval_ns = int(
            minimum_maker_snapshot_interval_seconds * 1_000_000_000
        )
        self._last_maker_fanout_ns: dict[str, int] = {}
        self._coalesced_maker: dict[str, _MakerIngress] = {}
        self._coalesced_maker_handles: dict[str, asyncio.TimerHandle] = {}

    async def start(self) -> None:
        if self._running:
            return
        await self._store.start()
        self._running = True
        self._ingress_task = asyncio.create_task(
            self._ingress_worker(),
            name="shadow-ingress",
        )
        for runtime in self._runtimes.values():
            runtime.task = asyncio.create_task(
                self._worker(runtime),
                name=f"shadow-{runtime.candidate.candidate_id}",
            )

    def submit(self, snapshot: CanonicalShadowSnapshot) -> bool:
        """Non-blocking launch-path ingress for an already frozen canonical state."""
        self._require_running()
        self._validate_submission_order(snapshot.observed_monotonic_ns)
        return self._submit_ingress(snapshot)

    def submit_maker(
        self,
        maker: MakerMarketSnapshot,
        *,
        observed_at: datetime,
        mapping_version: str,
        source_revision: str,
        source_provenance: Mapping[str, str] | None = None,
    ) -> bool:
        """Non-blocking launch ingress from the exact immutable MAKE decision state."""
        self._require_running()
        self._validate_submission_order(maker.now_monotonic_ns)
        item = _MakerIngress(
            maker=maker,
            observed_at=observed_at,
            mapping_version=mapping_version,
            source_revision=source_revision,
            source_provenance=dict(source_provenance or {}),
        )
        exchange_id = maker.exchange_id
        if self._maker_interval_ns == 0:
            accepted = self._submit_ingress(item)
            if accepted:
                self._last_maker_fanout_ns[exchange_id] = self._clock_ns()
            return accepted

        now_ns = self._clock_ns()
        last_fanout_ns = self._last_maker_fanout_ns.get(exchange_id)
        if last_fanout_ns is None or now_ns - last_fanout_ns >= self._maker_interval_ns:
            pending = self._coalesced_maker.pop(exchange_id, None)
            handle = self._coalesced_maker_handles.pop(exchange_id, None)
            if handle is not None:
                handle.cancel()
            if pending is not None:
                self._count_coalesced_maker()
            accepted = self._submit_ingress(item)
            if accepted:
                self._last_maker_fanout_ns[exchange_id] = now_ns
            return accepted

        if exchange_id in self._coalesced_maker:
            self._count_coalesced_maker()
        self._coalesced_maker[exchange_id] = item
        if exchange_id not in self._coalesced_maker_handles:
            remaining_ns = max(
                0,
                last_fanout_ns + self._maker_interval_ns - now_ns,
            )
            self._coalesced_maker_handles[exchange_id] = (
                asyncio.get_running_loop().call_later(
                    remaining_ns / 1_000_000_000,
                    self._release_coalesced_maker,
                    exchange_id,
                )
            )
        return True

    async def publish(self, snapshot: CanonicalShadowSnapshot) -> None:
        """Awaitable direct path retained for tests/replay-oriented callers."""
        self._require_running()
        self._validate_submission_order(snapshot.observed_monotonic_ns)
        await self._process_snapshot(snapshot)

    async def flush(self) -> None:
        if not self._running:
            return
        await self._ingress.join()
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
        try:
            await self._flush_coalesced_maker()
            await self.flush()
        except Exception as exc:  # pragma: no cover - defensive persistence shutdown
            flush_error = flush_error or exc

        if self._ingress_task is not None:
            self._ingress_task.cancel()
        for runtime in self._runtimes.values():
            if runtime.task is not None:
                runtime.task.cancel()
            if runtime.reaper_task is not None:
                runtime.reaper_task.cancel()
        await asyncio.gather(
            *(
                task
                for task in (
                    self._ingress_task,
                    *(
                        runtime.task
                        for runtime in self._runtimes.values()
                        if runtime.task is not None
                    ),
                    *(
                        runtime.reaper_task
                        for runtime in self._runtimes.values()
                        if runtime.reaper_task is not None
                    ),
                )
                if task is not None
            ),
            return_exceptions=True,
        )
        self._ingress_task = None
        for runtime in self._runtimes.values():
            runtime.task = None
            runtime.reaper_task = None
        self._running = False
        try:
            await self._store.close()
        finally:
            if flush_error is not None:
                raise flush_error

    def enable_candidate(self, candidate_id: str) -> None:
        runtime = self._candidate(candidate_id)
        runtime.enabled = True
        if not runtime.quarantined:
            runtime.last_error = None

    def disable_candidate(self, candidate_id: str) -> None:
        runtime = self._candidate(candidate_id)
        runtime.enabled = False
        self._drain_candidate_queue(runtime)

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
            maker_snapshots_coalesced=self._maker_snapshots_coalesced,
            ingress_queue_depth=self._ingress.qsize(),
            ingress_queue_high_water=self._ingress_high_water,
            ingress_rejected=self._ingress_rejected,
            ingress_error=self._ingress_error,
            candidates=candidates,
            persistence=self._store.health,
        )

    def _submit_ingress(self, item: IngressItem) -> bool:
        if self._ingress_error is not None:
            self._ingress_rejected += 1
            return False
        try:
            self._ingress.put_nowait(item)
        except asyncio.QueueFull:
            self._ingress_rejected += 1
            return False
        self._ingress_high_water = max(self._ingress_high_water, self._ingress.qsize())
        return True

    def _count_coalesced_maker(self) -> None:
        self._maker_snapshots_coalesced += 1
        self._dropped_or_coalesced += 1

    def _release_coalesced_maker(self, exchange_id: str) -> None:
        self._coalesced_maker_handles.pop(exchange_id, None)
        item = self._coalesced_maker.pop(exchange_id, None)
        if item is None or not self._running:
            return
        if self._submit_ingress(item):
            self._last_maker_fanout_ns[exchange_id] = self._clock_ns()

    async def _flush_coalesced_maker(self) -> None:
        for handle in self._coalesced_maker_handles.values():
            handle.cancel()
        self._coalesced_maker_handles.clear()
        pending = sorted(
            self._coalesced_maker.values(),
            key=lambda item: item.maker.now_monotonic_ns,
        )
        self._coalesced_maker.clear()
        for item in pending:
            if self._submit_ingress(item):
                self._last_maker_fanout_ns[item.maker.exchange_id] = self._clock_ns()

    async def _ingress_worker(self) -> None:
        while True:
            item = await self._ingress.get()
            try:
                if isinstance(item, CanonicalShadowSnapshot):
                    snapshot = item
                else:
                    snapshot = CanonicalShadowSnapshot.freeze(
                        item.maker,
                        observed_at=item.observed_at,
                        mapping_version=item.mapping_version,
                        source_revision=item.source_revision,
                        source_provenance=item.source_provenance,
                    )
                await self._process_snapshot(snapshot)
            except Exception as exc:
                self._ingress_error = f"{type(exc).__name__}:{exc}"
                self._paused = True
            finally:
                self._ingress.task_done()

    async def _process_snapshot(self, snapshot: CanonicalShadowSnapshot) -> None:
        self._last_snapshot_monotonic_ns = snapshot.observed_monotonic_ns
        self._snapshots_processed += 1

        await self._store.persist_snapshot(snapshot)
        if self._paused:
            self._dropped_or_coalesced += 1
            return

        enqueued_ns = self._clock_ns()
        for runtime in self._runtimes.values():
            if not runtime.enabled or runtime.quarantined:
                if runtime.quarantined:
                    runtime.skipped_states += 1
                    self._dropped_or_coalesced += 1
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

    async def _worker(self, runtime: _CandidateRuntime) -> None:
        while True:
            item = await runtime.queue.get()
            try:
                if not runtime.enabled or runtime.quarantined:
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
        if runtime.in_flight_task is not None:
            raise RuntimeError("candidate already has an in-flight evaluation")

        started = self._clock_ns()
        runtime.queue_delays_ns.append(max(0, started - item.enqueued_ns))
        status_failure = False
        task = asyncio.create_task(
            asyncio.to_thread(runtime.candidate.evaluate, item.snapshot),
            name=f"shadow-eval-{runtime.candidate.candidate_id}",
        )
        runtime.in_flight_task = task
        done, _ = await asyncio.wait({task}, timeout=runtime.timeout_seconds)

        if not done:
            finished = self._clock_ns()
            runtime.timeout_count += 1
            runtime.failure_count += 1
            runtime.quarantine_count += 1
            runtime.quarantined = True
            runtime.last_error = "timeout_inflight_quarantined"
            status_failure = True
            output = failure_output(
                DecisionStatus.TIMEOUT,
                "candidate_timeout_inflight_quarantined",
            )
            self._drain_candidate_queue(runtime)
            runtime.reaper_task = asyncio.create_task(
                self._reap_timed_out(runtime, task),
                name=f"shadow-reap-{runtime.candidate.candidate_id}",
            )
        else:
            runtime.in_flight_task = None
            try:
                output = task.result()
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

    async def _reap_timed_out(
        self,
        runtime: _CandidateRuntime,
        task: asyncio.Task[CandidateOutput],
    ) -> None:
        try:
            await asyncio.shield(task)
        except Exception:
            pass
        finally:
            if runtime.in_flight_task is task:
                runtime.in_flight_task = None
            runtime.quarantined = False
            runtime.reaper_task = None
            if runtime.enabled:
                runtime.last_error = "timeout_completed_provider_recovered"

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
            quarantined=runtime.quarantined,
            in_flight=runtime.in_flight_task is not None,
            queue_depth=runtime.queue.qsize(),
            queue_high_water=runtime.queue_high_water,
            skipped_states=runtime.skipped_states,
            failure_count=runtime.failure_count,
            timeout_count=runtime.timeout_count,
            quarantine_count=runtime.quarantine_count,
            last_success_monotonic_ns=runtime.last_success_monotonic_ns,
            last_error=runtime.last_error,
            evaluation_latency_p50_ns=_percentile(values, 0.50),
            evaluation_latency_p95_ns=_percentile(values, 0.95),
            evaluation_latency_p99_ns=_percentile(values, 0.99),
            queue_delay_p95_ns=_percentile(queue_delays, 0.95),
            last_snapshot_age_ns=_age(now, runtime.last_snapshot_monotonic_ns),
        )

    def _drain_candidate_queue(self, runtime: _CandidateRuntime) -> None:
        while True:
            try:
                runtime.queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            else:
                runtime.queue.task_done()
                runtime.skipped_states += 1
                self._dropped_or_coalesced += 1

    def _validate_submission_order(self, observed_monotonic_ns: int) -> None:
        previous = self._last_accepted_observed_ns
        if previous is not None and observed_monotonic_ns < previous:
            raise ValueError("out-of-order observable state rejected")
        self._last_accepted_observed_ns = observed_monotonic_ns

    def _require_running(self) -> None:
        if not self._running:
            raise RuntimeError("SHADOW bus is not started")

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
