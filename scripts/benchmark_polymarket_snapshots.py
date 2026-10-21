"""Reproducible synthetic PM book snapshot baseline; never reads venue data.

Run after installing the project:
    python scripts/benchmark_polymarket_snapshots.py

Outputs per-call wall time and peak temporary allocations, NOT process RSS.
No production collector or exchange connectivity.
"""

from __future__ import annotations

import gc
import random
import statistics
import time
import tracemalloc
from datetime import UTC, datetime
from decimal import Decimal
from functools import partial
from typing import Callable

from predictions_cup.external.polymarket.models import BookLevel, BookSnapshot
from predictions_cup.external.polymarket.orderbook import OrderBookStore

OBSERVED_AT = datetime(2026, 10, 21, tzinfo=UTC)
TOKEN = "synthetic-token"
MARKET = "synthetic-market"


def _book(levels_per_side: int) -> OrderBookStore:
    values = [Decimal(i) / Decimal(2 * levels_per_side + 1) for i in range(1, levels_per_side + 1)]
    bids = list(values)
    asks = list(reversed(values))
    random.Random(1337).shuffle(bids)
    random.Random(7331).shuffle(asks)
    store = OrderBookStore()
    store.apply_full_snapshot(
        {
            "asset_id": TOKEN,
            "market": MARKET,
            "hash": "synthetic",
            "bids": [
                {"price": str(price), "size": str(index + 1)}
                for index, price in enumerate(bids)
            ],
            "asks": [
                {"price": str(price), "size": str(index + 1)}
                for index, price in enumerate(asks)
            ],
        },
        OBSERVED_AT,
    )
    return store


def _original_snapshot(store: OrderBookStore, depth: int) -> BookSnapshot:
    """Replicate the old source algorithm for a fair A/B comparison."""
    book = store._books[TOKEN]
    return BookSnapshot(
        market_id=book.market_id,
        token_id=book.token_id,
        source_timestamp=book.source_timestamp,
        observed_at=book.observed_at,
        bids=tuple(
            BookLevel(price=price, size=size)
            for price, size in sorted(book.bids.items(), reverse=True)[:depth]
        ),
        asks=tuple(
            BookLevel(price=price, size=size)
            for price, size in sorted(book.asks.items())[:depth]
        ),
        book_hash=book.book_hash,
        min_order_size=book.min_order_size,
        tick_size=book.tick_size,
        neg_risk=book.neg_risk,
        last_trade_price=book.last_trade_price,
    )


def _bench(fn: Callable[[], BookSnapshot], *, repetitions: int) -> tuple[float, float]:
    # Warm once outside the measurement window.
    fn()
    trials: list[float] = []
    for _ in range(3):
        start = time.perf_counter_ns()
        for _ in range(repetitions):
            fn()
        trials.append((time.perf_counter_ns() - start) / repetitions / 1_000_000)
    gc.collect()
    tracemalloc.start()
    try:
        fn()
        _, peak_bytes = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    return statistics.median(trials), peak_bytes / 1024


def main() -> None:
    print("Synthetic only; milliseconds and KiB peak temporary allocation per call")
    print("levels depth baseline_ms optimized_ms baseline_KiB optimized_KiB")
    for count, depth in (
        (128, 1),
        (128, 20),
        (1_000, 1),
        (1_000, 20),
        (9_000, 1),
        (9_000, 20),
    ):
        store = _book(count)
        old = partial(_original_snapshot, store, depth)
        new = partial(store.snapshot, TOKEN, depth)
        # The book is always seeded, so snapshot(...) is never None.
        assert old() == new()
        n = 100 if count <= 1_000 else 25
        old_ms, old_peak = _bench(old, repetitions=n)
        new_ms, new_peak = _bench(new, repetitions=n)
        print(
            f"{count} {depth} {old_ms:.3f} {new_ms:.3f} "
            f"{old_peak:.1f} {new_peak:.1f}"
        )


if __name__ == "__main__":
    main()
