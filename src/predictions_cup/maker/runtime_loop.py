"""Single-worker event shell for MAKE-001.

Feed handlers only enqueue affected identities. One asyncio worker coalesces bursts,
builds immutable snapshots from existing in-memory state, and invokes the maker.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Iterable
from datetime import UTC, datetime
from time import monotonic_ns
from uuid import uuid4

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
        fail_closed_on_observer_error: bool = False,
        wall_clock: WallClock = lambda: datetime.now(UTC),
        mono_clock: MonoClock = monotonic_ns,
        runtime_session_id: str | None = None,
    ) -> None:
        self._bridge = bridge
        self._coordinator = coordinator
        self._pm_trusted = polymarket_feed_trusted
        self._telemetry = telemetry
        self._observer = cycle_observer
        self._observer_fail_closed = fail_closed_on_observer_error
        self._wall_clock = wall_clock
        self._mono_clock = mono_clock
        session_id = uuid4().hex if runtime_session_id is None else runtime_session_id
        if not session_id.strip():
            raise ValueError("runtime_session_id must not be blank")
        self._runtime_session_id = session_id
        self._wake = asyncio.Event()
        self._pending_exchanges: set[str] = set()
        self._global_recheck = False
        self._oldest_observed_ns: int | None = None
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
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=0.25)
            except TimeoutError:
                continue
            if stop_event.is_set():
                break
            await self._drain_once()

    async def drain_once(self) -> MakerCycleResult | None:
        """Deterministic test/operational hook: process current pending work once."""
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
        started = self._mono_clock()
        result = await self._coordinator.on_state_change(
            MakerStateChange(
                event_id=(
                    f"make-runtime-{self._runtime_session_id}-{self._sequence}"
                ),
                observed_monotonic_ns=now_ns,
                exchange_ids=frozenset(snapshots),
                global_recheck=global_recheck,
            ),
            snapshots,
        )
        finished = self._mono_clock()
        if finished >= started:
            self._observe("maker_cycle", finished - started)
        self._increment("maker_cycles")
        self._increment("maker_exchange_evaluations", len(snapshots))

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
