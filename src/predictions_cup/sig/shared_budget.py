"""Host-shared SIG REST budget across processes (CAPTURE, MAKE, tools).

SIG documents its rate limits per account across all API keys, while each
``SigRestGovernor`` paces only its own process. This optional token bucket
lives in a small state file guarded by ``fcntl.flock`` so every process on the
host draws from one account-wide budget. HIGH priority (orders, cancels and
execution reconciliation) may spend the last ``high_priority_reserve`` tokens;
NORMAL/BACKGROUND reads must leave them, so CAPTURE cannot starve MAKE.

A 429/503 Retry-After cooldown observed by any process is also shared.
Unreadable state fails safe: the bucket restarts empty, which only delays.
"""

from __future__ import annotations

import fcntl
import json
import math
import os
import time
from collections.abc import Callable
from pathlib import Path

from predictions_cup.sig.rest_governor import RestPriority

WallFn = Callable[[], float]


class HostSharedRestBudget:
    def __init__(
        self,
        path: Path,
        *,
        rate_per_second: float,
        burst_capacity: float = 2.0,
        high_priority_reserve: float = 1.0,
        wall: WallFn = time.time,
    ) -> None:
        if rate_per_second <= 0:
            raise ValueError("shared rate_per_second must be positive")
        if high_priority_reserve < 0 or high_priority_reserve + 1.0 > burst_capacity:
            raise ValueError("shared burst must leave one request above the reserve")
        self.path = path
        self.rate_per_second = rate_per_second
        self._burst_capacity = burst_capacity
        self._high_priority_reserve = high_priority_reserve
        self._wall = wall
        path.parent.mkdir(parents=True, exist_ok=True)

    def try_take(self, priority: RestPriority) -> float:
        """Take one token now and return 0.0, or return seconds to wait."""

        required = (
            1.0 if priority is RestPriority.HIGH else 1.0 + self._high_priority_reserve
        )
        with self._locked() as (fd, state):
            now = self._wall()
            tokens = self._refilled(state, now)
            cooldown_until = state["cooldown_until"]
            if now < cooldown_until:
                self._write(fd, tokens, now, cooldown_until)
                return cooldown_until - now
            if tokens + 1e-12 < required:
                self._write(fd, tokens, now, cooldown_until)
                return (required - tokens) / self.rate_per_second
            self._write(fd, tokens - 1.0, now, cooldown_until)
            return 0.0

    def extend_cooldown(self, delay_seconds: float) -> None:
        if not math.isfinite(delay_seconds) or delay_seconds <= 0:
            return
        with self._locked() as (fd, state):
            now = self._wall()
            tokens = self._refilled(state, now)
            until = max(state["cooldown_until"], now + delay_seconds)
            self._write(fd, tokens, now, until)

    def _refilled(self, state: dict[str, float], now: float) -> float:
        elapsed = max(0.0, now - state["at"])
        return min(self._burst_capacity, state["tokens"] + elapsed * self.rate_per_second)

    def _locked(self) -> _LockedState:
        return _LockedState(self.path, self._wall)

    @staticmethod
    def _write(fd: int, tokens: float, at: float, cooldown_until: float) -> None:
        payload = json.dumps(
            {"tokens": tokens, "at": at, "cooldown_until": cooldown_until}
        ).encode()
        os.lseek(fd, 0, os.SEEK_SET)
        os.ftruncate(fd, 0)
        os.write(fd, payload)


class _LockedState:
    def __init__(self, path: Path, wall: WallFn) -> None:
        self._path = path
        self._wall = wall
        self._fd = -1

    def __enter__(self) -> tuple[int, dict[str, float]]:
        self._fd = os.open(self._path, os.O_RDWR | os.O_CREAT, 0o600)
        fcntl.flock(self._fd, fcntl.LOCK_EX)
        return self._fd, self._read()

    def __exit__(self, *_: object) -> None:
        try:
            fcntl.flock(self._fd, fcntl.LOCK_UN)
        finally:
            os.close(self._fd)

    def _read(self) -> dict[str, float]:
        now = self._wall()
        empty = {"tokens": 0.0, "at": now, "cooldown_until": 0.0}
        os.lseek(self._fd, 0, os.SEEK_SET)
        raw = os.read(self._fd, 4096)
        if not raw:
            return empty
        try:
            data = json.loads(raw)
            state = {
                key: float(data[key]) for key in ("tokens", "at", "cooldown_until")
            }
        except (ValueError, KeyError, TypeError):
            return empty
        if not all(math.isfinite(value) for value in state.values()):
            return empty
        # A clock step backwards must not mint tokens or erase a cooldown.
        state["at"] = min(state["at"], now)
        state["tokens"] = max(0.0, state["tokens"])
        return state
