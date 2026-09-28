"""Priority-aware token-bucket pacing and shared cooldown for SIG REST traffic."""

from __future__ import annotations

import asyncio
import random
import time
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass
from enum import IntEnum

SleepFn = Callable[[float], Awaitable[None]]
MonotonicFn = Callable[[], float]
RandomFn = Callable[[], float]


class RestPriority(IntEnum):
    """Lower numeric value means the request is more urgent."""

    HIGH = 0
    NORMAL = 1
    BACKGROUND = 2


@dataclass(frozen=True)
class RestGovernorSnapshot:
    rate_per_second: float
    requests_total: int
    rate_limit_count: int
    shared_cooldown_count: int
    pending_high_priority_reads: int
    pending_normal_reads: int
    pending_background_reads: int
    cooldown_remaining_seconds: float


class SigRestGovernor:
    """Shared average-rate token bucket with one token reserved for execution.

    The default two-token bucket preserves the configured long-run request rate
    while allowing one HIGH request to use reserved capacity immediately after a
    read. NORMAL/BACKGROUND traffic may only consume capacity above the reserve.
    A 429 cooldown always overrides the burst allowance.
    """

    def __init__(
        self,
        *,
        rate_per_second: float,
        max_shared_cooldown_seconds: float = 8.0,
        burst_capacity: float = 2.0,
        high_priority_reserve: float = 1.0,
        sleep: SleepFn = asyncio.sleep,
        monotonic: MonotonicFn = time.monotonic,
        random_fn: RandomFn = random.random,
    ) -> None:
        if rate_per_second <= 0:
            raise ValueError("rate_per_second must be positive")
        if max_shared_cooldown_seconds <= 0:
            raise ValueError("max_shared_cooldown_seconds must be positive")
        if burst_capacity < 1.0:
            raise ValueError("burst_capacity must be at least one request")
        if high_priority_reserve < 0:
            raise ValueError("high_priority_reserve must be non-negative")
        if high_priority_reserve + 1.0 > burst_capacity:
            raise ValueError(
                "burst_capacity must leave one request above high_priority_reserve"
            )
        self.rate_per_second = rate_per_second
        self._burst_capacity = burst_capacity
        self._high_priority_reserve = high_priority_reserve
        self._max_shared_cooldown_seconds = max_shared_cooldown_seconds
        self._sleep = sleep
        self._monotonic = monotonic
        self._random = random_fn

        self._queue: asyncio.PriorityQueue[
            tuple[int, int, asyncio.Future[None]]
        ] = asyncio.PriorityQueue()
        self._queue_event = asyncio.Event()
        self._worker: asyncio.Task[None] | None = None
        self._sequence = 0
        self._closed = False

        now = self._monotonic()
        self._tokens = burst_capacity
        self._last_refill_at = now
        self._cooldown_until = 0.0
        self._consecutive_429 = 0

        self._requests_total = 0
        self._rate_limit_count = 0
        self._shared_cooldown_count = 0
        self._pending = {
            RestPriority.HIGH: 0,
            RestPriority.NORMAL: 0,
            RestPriority.BACKGROUND: 0,
        }

    async def acquire(self, priority: RestPriority = RestPriority.NORMAL) -> None:
        if self._closed:
            raise RuntimeError("SIG REST governor is closed")
        loop = asyncio.get_running_loop()
        future: asyncio.Future[None] = loop.create_future()
        self._sequence += 1
        self._pending[priority] += 1
        self._queue.put_nowait((int(priority), self._sequence, future))
        self._queue_event.set()
        if self._worker is None or self._worker.done():
            self._worker = asyncio.create_task(self._run())
        try:
            await future
        except asyncio.CancelledError:
            future.cancel()
            raise

    def record_response(
        self,
        status_code: int,
        *,
        retry_after_seconds: float | None = None,
    ) -> None:
        if status_code != 429:
            self._consecutive_429 = 0
            return

        self._rate_limit_count += 1
        self._consecutive_429 += 1
        if retry_after_seconds is not None:
            delay = max(0.0, retry_after_seconds)
        else:
            base_delay = min(
                self._max_shared_cooldown_seconds,
                0.5 * (2.0 ** (self._consecutive_429 - 1)),
            )
            delay = base_delay + min(
                self._max_shared_cooldown_seconds - base_delay,
                base_delay * 0.2 * self._random(),
            )
        now = self._monotonic()
        candidate = now + delay
        if candidate > self._cooldown_until:
            self._cooldown_until = candidate
            self._shared_cooldown_count += 1
            self._queue_event.set()

    def snapshot(self) -> RestGovernorSnapshot:
        return RestGovernorSnapshot(
            rate_per_second=self.rate_per_second,
            requests_total=self._requests_total,
            rate_limit_count=self._rate_limit_count,
            shared_cooldown_count=self._shared_cooldown_count,
            pending_high_priority_reads=self._pending[RestPriority.HIGH],
            pending_normal_reads=self._pending[RestPriority.NORMAL],
            pending_background_reads=self._pending[RestPriority.BACKGROUND],
            cooldown_remaining_seconds=max(
                0.0, self._cooldown_until - self._monotonic()
            ),
        )

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._queue_event.set()
        if self._worker is not None:
            self._worker.cancel()
            with suppress(asyncio.CancelledError):
                await self._worker
        while True:
            try:
                priority_value, _, future = self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            priority = RestPriority(priority_value)
            self._pending[priority] = max(0, self._pending[priority] - 1)
            if not future.done():
                future.cancel()

    def _refill(self, now: float) -> None:
        elapsed = max(0.0, now - self._last_refill_at)
        if elapsed:
            self._tokens = min(
                self._burst_capacity,
                self._tokens + elapsed * self.rate_per_second,
            )
            self._last_refill_at = now

    def _best_pending_priority(self) -> RestPriority | None:
        for priority in (
            RestPriority.HIGH,
            RestPriority.NORMAL,
            RestPriority.BACKGROUND,
        ):
            if self._pending[priority] > 0:
                return priority
        return None

    def _capacity_delay(self, priority: RestPriority, now: float) -> float:
        self._refill(now)
        required = (
            1.0
            if priority is RestPriority.HIGH
            else 1.0 + self._high_priority_reserve
        )
        token_delay = max(0.0, required - self._tokens) / self.rate_per_second
        cooldown_delay = max(0.0, self._cooldown_until - now)
        return max(token_delay, cooldown_delay)

    async def _sleep_once(self, delay: float) -> None:
        await self._sleep(delay)

    async def _wait_interruptibly(self, delay: float) -> None:
        if delay <= 0:
            return
        self._queue_event.clear()
        sleeper: asyncio.Task[None] = asyncio.create_task(self._sleep_once(delay))
        wake: asyncio.Task[bool] = asyncio.create_task(self._queue_event.wait())
        done, _ = await asyncio.wait(
            {sleeper, wake},
            return_when=asyncio.FIRST_COMPLETED,
        )
        if wake in done:
            sleeper.cancel()
            with suppress(asyncio.CancelledError):
                await sleeper
        else:
            wake.cancel()
            with suppress(asyncio.CancelledError):
                await wake

    async def _run(self) -> None:
        try:
            while not self._closed:
                if self._queue.empty():
                    self._queue_event.clear()
                    await self._queue_event.wait()
                    continue

                priority = self._best_pending_priority()
                if priority is None:
                    await asyncio.sleep(0)
                    continue

                delay = self._capacity_delay(priority, self._monotonic())
                if delay > 0:
                    await self._wait_interruptibly(delay)
                    continue

                try:
                    priority_value, _, future = self._queue.get_nowait()
                except asyncio.QueueEmpty:
                    continue

                priority = RestPriority(priority_value)
                self._pending[priority] = max(0, self._pending[priority] - 1)
                if future.cancelled():
                    continue

                now = self._monotonic()
                self._refill(now)
                required = (
                    1.0
                    if priority is RestPriority.HIGH
                    else 1.0 + self._high_priority_reserve
                )
                if now < self._cooldown_until or self._tokens + 1e-12 < required:
                    # Capacity changed between peek and dequeue. Requeue without
                    # completing the caller; preserve FIFO sequence ordering.
                    self._pending[priority] += 1
                    self._sequence += 1
                    self._queue.put_nowait((int(priority), self._sequence, future))
                    self._queue_event.set()
                    continue

                self._tokens = max(0.0, self._tokens - 1.0)
                self._requests_total += 1
                future.set_result(None)
        finally:
            if not self._closed and not self._queue.empty():
                self._queue_event.set()
