"""Single-worker event shell for MAKE-001.

Feed handlers only enqueue affected identities. One asyncio worker coalesces bursts,
builds immutable snapshots from existing in-memory state, and invokes the maker.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Iterable, Mapping
from datetime import UTC, datetime
from time import monotonic_ns
from uuid import uuid4

from predictions_cup.maker.contracts import MakerMarketSnapshot
from predictions_cup.maker.coordinator import (
    MakerCoordinator,
    MakerCycleResult,
    MakerStateChange,
)
from predictions_cup.maker.sources import MakerSourceBridge
from predictions_cup.runtime.telemetry import HotPathTelemetry

WallClock = Callable[[], datetime]
MonoClock = Callable[[], int]
FeedTrust = Callable[[], bool]
CycleObserver = Callable[[MakerCycleResult], Awaitable[None]]
SnapshotObserver = Callable[[MakerStateChange, datetime, Mapping[str, MakerMarketSnapshot]], None]
ExecutionObserver = Callable[
    [MakerStateChange, datetime, Mapping[str, MakerMarketSnapshot]],
    Awaitable[None],
]


class MakerRuntimeLoop:
    """Coalescing event-driven runtime with no task-per-market polling."""

    def __init__(
        self,
        *,
        bridge: MakerSourceBridge,
        coordinator: MakerCoordinator,
        polymarket_feed_trusted: FeedTrust,
        telemetry: HotPathTelemetry | None = None,
        cycle_observer: CycleObserver | None = None,
        snapshot_observer: SnapshotObserver | None = None,
        execution_observer: ExecutionObserver | None = None,
        fail_closed_on_observer_error: bool = False,
        wall_clock: WallClock = lambda: datetime.now(UTC),
        mono_clock: MonoClock = monotonic_ns,
        runtime_session_id: str | None = None,
        max_exchanges_per_cycle: int | None = None,
    ) -> None:
        self._bridge = bridge
        self._coordinator = coordinator
        self._pm_trusted = polymarket_feed_trusted
        self._telemetry = telemetry
        self._observer = cycle_observer
        self._snapshot_observer = snapshot_observer
        self._execution_observer = execution_observer
        self._observer_fail_closed = fail_closed_on_observer_error
        self._wall_clock = wall_clock
        self._mono_clock = mono_clock
        session_id = uuid4().hex if runtime_session_id is None else runtime_session_id
        if not session_id.strip():
            raise ValueError("runtime_session_id must not be blank")
        self._runtime_session_id = session_id
        if max_exchanges_per_cycle is not None and max_exchanges_per_cycle <= 0:
            raise ValueError("max_exchanges_per_cycle must be positive")
        self._max_exchanges_per_cycle = max_exchanges_per_cycle
        self._wake = asyncio.Event()
        self._pending_exchanges: set[str] = set()
        self._global_recheck = False
        self._oldest_observed_ns: int | None = None
        self._freshness_deadlines: dict[str, int] = {}
        self._sequence = 0

    def notify_sig(
        self,
        exchange_ids: Iterable[str],
        *,
        observed_monotonic_ns: int | None = None,
    ) -> None:
        self._notify_exchanges(exchange_ids, observed_monotonic_ns)

    def notify_polymarket(
        self,
        token_ids: Iterable[str],
        *,
        observed_monotonic_ns: int | None = None,
    ) -> None:
        affected: set[str] = set()
        for token_id in token_ids:
            affected.update(self._bridge.sig_exchanges_for_polymarket_token(token_id))
        self._notify_exchanges(affected, observed_monotonic_ns)

    def notify_account(
        self,
        *,
        observed_monotonic_ns: int | None = None,
    ) -> None:
        # Account/inventory changes can alter reservation price and central risk
        # across every held/open-order market. They are comparatively infrequent.
        self._notify_global(observed_monotonic_ns)

    def notify_global(
        self,
        *,
        observed_monotonic_ns: int | None = None,
    ) -> None:
        self._notify_global(observed_monotonic_ns)

    def activate_kill_switch(self, reason: str) -> None:
        self._coordinator.activate_kill_switch(reason)
        self._notify_global(self._mono_clock())

    async def run(self, *, stop_event: asyncio.Event) -> None:
        while not stop_event.is_set():
            self._enqueue_due_deadlines(self._mono_clock())
            if self._wake.is_set():
                await self._drain_once()
                continue

            timeout = self._seconds_until_next_deadline(self._mono_clock())
            wake_wait = asyncio.create_task(self._wake.wait())
            stop_wait = asyncio.create_task(stop_event.wait())
            done, pending = await asyncio.wait(
                {wake_wait, stop_wait},
                timeout=timeout,
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)

            if stop_wait in done and stop_event.is_set():
                return
            if not done:
                self._enqueue_due_deadlines(self._mono_clock())
            if self._wake.is_set():
                await self._drain_once()

    async def drain_once(self) -> MakerCycleResult | None:
        """Deterministic test/operational hook: process pending or expired work once."""
        self._enqueue_due_deadlines(self._mono_clock())
        if not self._wake.is_set():
            return None
        return await self._drain_once()

    async def _drain_once(self) -> MakerCycleResult | None:
        exchange_ids = frozenset(self._pending_exchanges)
        global_recheck = self._global_recheck
        oldest_observed_ns = self._oldest_observed_ns
        self._pending_exchanges.clear()
        self._global_recheck = False
        self._oldest_observed_ns = None
        self._wake.clear()

        if global_recheck:
            exchange_ids = self._bridge.tradeable_exchange_ids
        if not exchange_ids:
            return None

        ordered_exchange_ids = tuple(sorted(exchange_ids))
        limit = self._max_exchanges_per_cycle
        if limit is not None and len(ordered_exchange_ids) > limit:
            selected = ordered_exchange_ids[:limit]
            deferred = ordered_exchange_ids[limit:]
            exchange_ids = frozenset(selected)
            self._pending_exchanges.update(deferred)
            self._wake.set()
        else:
            exchange_ids = frozenset(ordered_exchange_ids)

        now_ns = self._mono_clock()
        if oldest_observed_ns is not None and now_ns >= oldest_observed_ns:
            self._observe("source_update_to_make_trigger", now_ns - oldest_observed_ns)

        wall_now = self._wall_clock()
        snapshots = self._bridge.build_many(
            exchange_ids,
            wall_now=wall_now,
            monotonic_now_ns=now_ns,
            polymarket_feed_trusted=self._pm_trusted(),
        )
        if not snapshots:
            return None

        self._sequence += 1
        change = MakerStateChange(
            event_id=f"make-runtime-{self._runtime_session_id}-{self._sequence}",
            observed_monotonic_ns=now_ns,
            exchange_ids=frozenset(snapshots),
            global_recheck=global_recheck,
        )
        if self._snapshot_observer is not None:
            try:
                self._snapshot_observer(change, wall_now, snapshots)
            except Exception:
                # SHADOW/research observation must never block or kill MAKE.
                self._increment("maker_snapshot_observer_failures")
        if self._execution_observer is not None:
            execution_started = self._mono_clock()
            # This observer is an economic execution path, not analytics.
            # Infrastructure failure propagates so the service fails closed.
            # Individual model failures are isolated inside MODEL-RUNTIME-001.
            await self._execution_observer(change, wall_now, snapshots)
            execution_finished = self._mono_clock()
            if execution_finished >= execution_started:
                self._observe(
                    "model_runtime_execution",
                    execution_finished - execution_started,
                )
        started = self._mono_clock()
        result = await self._coordinator.on_state_change(
            change,
            snapshots,
        )
        finished = self._mono_clock()
        if finished >= started:
            self._observe("maker_cycle", finished - started)
        self._increment("maker_cycles")
        self._increment("maker_exchange_evaluations", len(snapshots))
        evaluated_exchange_ids = tuple(sorted(snapshots))
        if len(result.decisions) != len(evaluated_exchange_ids):
            raise RuntimeError("maker decision count does not match evaluated exchanges")
        for exchange_id, decision in zip(
            evaluated_exchange_ids,
            result.decisions,
            strict=True,
        ):
            deadline = decision.next_recheck_monotonic_ns
            if decision.desired is None or deadline is None:
                self._freshness_deadlines.pop(exchange_id, None)
            else:
                self._freshness_deadlines[exchange_id] = deadline

        if self._observer is not None:
            try:
                await self._observer(result)
            except Exception:
                self._increment("maker_observer_failures")
                if self._observer_fail_closed:
                    self._coordinator.activate_kill_switch("observer_failure")
                    all_snapshots = self._bridge.build_many(
                        self._bridge.tradeable_exchange_ids,
                        wall_now=self._wall_clock(),
                        monotonic_now_ns=self._mono_clock(),
                        polymarket_feed_trusted=self._pm_trusted(),
                    )
                    if all_snapshots:
                        await self._coordinator.halt_all(
                            event_id=(
                                f"make-observer-failure-"
                                f"{self._runtime_session_id}-{self._sequence}"
                            ),
                            observed_monotonic_ns=self._mono_clock(),
                            snapshots=all_snapshots,
                            reason="observer_failure",
                        )
                # Analytics is optional by default. Execution safety never depends
                # on successful downstream persistence.
        return result

    def _enqueue_due_deadlines(self, now_ns: int) -> None:
        due = {
            exchange_id
            for exchange_id, deadline_ns in self._freshness_deadlines.items()
            if deadline_ns <= now_ns
        }
        if not due:
            return
        for exchange_id in due:
            self._freshness_deadlines.pop(exchange_id, None)
        self._pending_exchanges.update(due)
        self._record_observed(now_ns)
        self._wake.set()
        self._increment("maker_freshness_deadline_triggers", len(due))

    def _seconds_until_next_deadline(self, now_ns: int) -> float | None:
        if not self._freshness_deadlines:
            return None
        nearest = min(self._freshness_deadlines.values())
        return max(0.0, (nearest - now_ns) / 1_000_000_000.0)

    def _notify_exchanges(
        self,
        exchange_ids: Iterable[str],
        observed_monotonic_ns: int | None,
    ) -> None:
        additions = {
            exchange_id
            for exchange_id in exchange_ids
            if exchange_id in self._bridge.tradeable_exchange_ids
        }
        if not additions:
            return
        self._pending_exchanges.update(additions)
        self._record_observed(observed_monotonic_ns)
        self._wake.set()

    def _notify_global(self, observed_monotonic_ns: int | None) -> None:
        self._global_recheck = True
        self._record_observed(observed_monotonic_ns)
        self._wake.set()

    def _record_observed(self, observed_monotonic_ns: int | None) -> None:
        observed = self._mono_clock() if observed_monotonic_ns is None else observed_monotonic_ns
        if observed < 0:
            raise ValueError("observed_monotonic_ns must be non-negative")
        if self._oldest_observed_ns is None:
            self._oldest_observed_ns = observed
        else:
            self._oldest_observed_ns = min(self._oldest_observed_ns, observed)

    def _observe(self, name: str, duration_ns: int) -> None:
        if self._telemetry is not None:
            self._telemetry.observe(name, duration_ns)

    def _increment(self, name: str, value: int = 1) -> None:
        if self._telemetry is not None:
            self._telemetry.increment(name, value)
