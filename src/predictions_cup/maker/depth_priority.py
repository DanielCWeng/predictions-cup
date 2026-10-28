from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class DepthCandidate:
    exchange_id: str
    score: float
    observed_ns: int

class CandidateDepthQueue:
    """Bounded coalesced priority queue; 1.2s TTL; 2s global and 5s per-market pacing."""
    def __init__(self, max_pending: int = 12):
        if max_pending < 1:
            raise ValueError("max_pending")
        self.max_pending = max_pending
        self.pending: dict[str, DepthCandidate] = {}
        self.last_market_ns: dict[str, int] = {}
        self.last_global_ns: int | None = None
        self.dropped_expired = 0
        self.dropped_low_edge = 0
        self.requests_started = 0

    def _expire(self, now_ns: int) -> None:
        for key, item in tuple(self.pending.items()):
            if now_ns < item.observed_ns or now_ns - item.observed_ns > 1_200_000_000:
                self.pending.pop(key, None)
                self.dropped_expired += 1
        for key, value in tuple(self.last_market_ns.items()):
            if now_ns - value >= 20_000_000_000:
                self.last_market_ns.pop(key, None)

    def offer(self, exchange_id: str, score: float, now_ns: int) -> bool:
        if not exchange_id or not math.isfinite(score) or score <= 0 or now_ns < 0:
            return False
        self._expire(now_ns)
        if exchange_id in self.pending:
            self.pending[exchange_id] = DepthCandidate(exchange_id, score, now_ns)
            return True
        if len(self.pending) >= self.max_pending:
            worst = min(self.pending.values(), key=lambda item: (item.score, item.observed_ns))
            if worst.score >= score:
                self.dropped_low_edge += 1
                return False
            self.pending.pop(worst.exchange_id)
            self.dropped_low_edge += 1
        self.pending[exchange_id] = DepthCandidate(exchange_id, score, now_ns)
        return True

    def pick(self, now_ns: int) -> DepthCandidate | None:
        self._expire(now_ns)
        if self.last_global_ns is not None and now_ns - self.last_global_ns < 2_000_000_000:
            return None
        eligible = [item for item in self.pending.values()
                    if item.exchange_id not in self.last_market_ns
                    or now_ns - self.last_market_ns[item.exchange_id] >= 5_000_000_000]
        if not eligible:
            return None
        best = max(eligible, key=lambda item: (item.score, item.observed_ns))
        self.pending.pop(best.exchange_id)
        self.last_global_ns = now_ns
        self.last_market_ns[best.exchange_id] = now_ns
        self.requests_started += 1
        return best
