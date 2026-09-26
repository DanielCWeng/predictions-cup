"""Priority-aware pacing and shared cooldown for SIG REST traffic."""

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
    """One per-client request budget shared by every SIG REST caller.

    Requests are FIFO within each priority class. The worker waits for the next
    pacing/cooldown slot before choosing work, so a HIGH request that arrives
    while a BACKGROUND request is waiting for capacity can overtake it.
    """

    def __init__(
        self,
        *,
        rate_per_second: float,
        max_shared_cooldown_seconds: float = 8.0,
        sleep: SleepFn = asyncio.sleep,
        monotonic: MonotonicFn = time.monotonic,
        random_fn: RandomFn = random.random,
    ) -> None:
        if rate_per_second <= 0:
            raise ValueError("rate_per_second must be positive")
        if max_shared_cooldown_seconds <= 0:
            raise ValueError("max_shared_cooldown_seconds must be positive")
        self.rate_per_second = rate_per_second
        self._interval_seconds = 1.0 / rate_per_second
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
        self._next_request_at = self._monotonic()
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

    async def _run(self) -> None:
        try:
            while not self._closed:
                await self._queue_event.wait()
                self._queue_event.clear()
                while not self._queue.empty() and not self._closed:
                    while True:
                        now = self._monotonic()
                        delay = max(
                            0.0,
                            self._next_request_at - now,
                            self._cooldown_until - now,
                        )
                        if delay <= 0:
                            break
                        await self._sleep(delay)

                    try:
                        priority_value, _, future = self._queue.get_nowait()
                    except asyncio.QueueEmpty:
                        break

                    priority = RestPriority(priority_value)
                    self._pending[priority] = max(0, self._pending[priority] - 1)
                    if future.cancelled():
                        continue

                    now = self._monotonic()
                    self._next_request_at = now + self._interval_seconds
                    self._requests_total += 1
                    future.set_result(None)
        finally:
            if not self._closed and not self._queue.empty():
                self._queue_event.set()
