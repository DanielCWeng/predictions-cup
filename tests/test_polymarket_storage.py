from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from predictions_cup.external.polymarket.models import BookChangeEvent, BookLevel, BookSnapshot
from predictions_cup.external.polymarket.storage import PolymarketStorage


def snapshot(observed_at: datetime) -> BookSnapshot:
    return BookSnapshot(
        market_id="0xmarket",
        token_id="token-1",
        source_timestamp=observed_at,
        observed_at=observed_at,
        bids=(BookLevel(Decimal("0.45"), Decimal("10")),),
        asks=(BookLevel(Decimal("0.46"), Decimal("12")),),
    )


def test_sqlite_append_survives_restart(tmp_path: Path) -> None:
    path = tmp_path / "capture.sqlite3"
    first = PolymarketStorage(path)
    first.initialize()
    observed = datetime(2026, 9, 25, tzinfo=UTC)
    assert first.append_snapshots((snapshot(observed),), observed.isoformat()) == 1
    assert first.append_observations((snapshot(observed),), observed.isoformat()) == 1

    second = PolymarketStorage(path)
    second.initialize()
    later = observed.replace(second=1)
    assert second.append_snapshots((snapshot(later),), later.isoformat()) == 1
    assert second.append_observations((snapshot(later),), later.isoformat()) == 1

    with sqlite3.connect(path) as connection:
        depth_count = connection.execute(
            "SELECT COUNT(*) FROM polymarket_book_snapshots"
        ).fetchone()[0]
        panel_count = connection.execute(
            "SELECT COUNT(*) FROM polymarket_book_observations"
        ).fetchone()[0]
    assert depth_count == 2
    assert panel_count == 2


def test_lean_panel_and_event_changes_preserve_distinct_timestamps(tmp_path: Path) -> None:
    path = tmp_path / "capture.sqlite3"
    storage = PolymarketStorage(path)
    storage.initialize()
    source_at = datetime(2026, 9, 25, 0, 0, 0, tzinfo=UTC)
    state_observed_at = datetime(2026, 9, 25, 0, 0, 0, 250000, tzinfo=UTC)
    sampled_at = datetime(2026, 9, 25, 0, 0, 1, tzinfo=UTC)
    book = BookSnapshot(
        market_id="0xmarket",
        token_id="token-1",
        source_timestamp=source_at,
        observed_at=state_observed_at,
        bids=(BookLevel(Decimal("0.45"), Decimal("10")),),
        asks=(BookLevel(Decimal("0.46"), Decimal("12")),),
        last_trade_price=Decimal("0.455"),
    )
    change = BookChangeEvent(
        market_id="0xmarket",
        token_id="token-1",
        side="BUY",
        price=Decimal("0.45"),
        size=Decimal("10"),
        source_timestamp=source_at,
        observed_at=state_observed_at,
        best_bid=Decimal("0.45"),
        best_ask=Decimal("0.46"),
        book_hash="hash-1",
    )

    assert storage.append_observations((book,), sampled_at.isoformat()) == 1
    assert storage.append_book_changes((change,)) == 1

    with sqlite3.connect(path) as connection:
        panel = connection.execute(
            """
            SELECT source_timestamp, state_observed_at, observed_at, best_bid, best_ask,
                   midpoint, spread, last_trade_price, book_valid
            FROM polymarket_book_observations
            """
        ).fetchone()
        delta = connection.execute(
            """
            SELECT source_timestamp, observed_at, side, price, size, best_bid, best_ask, book_hash
            FROM polymarket_book_changes
            """
        ).fetchone()

    assert panel == (
        source_at.isoformat(),
        state_observed_at.isoformat(),
        sampled_at.isoformat(),
        "0.45",
        "0.46",
        "0.455",
        "0.01",
        "0.455",
        1,
    )
    assert delta == (
        source_at.isoformat(),
        state_observed_at.isoformat(),
        "BUY",
        "0.45",
        "10",
        "0.45",
        "0.46",
        "hash-1",
    )


def test_snapshot_rows_keep_source_and_observed_timestamps(tmp_path: Path) -> None:
    path = tmp_path / "capture.sqlite3"
    storage = PolymarketStorage(path)
    storage.initialize()
    observed = datetime(2026, 9, 25, 0, 0, 1, tzinfo=UTC)
    storage.append_snapshots((snapshot(observed),), observed.isoformat())

    with sqlite3.connect(path) as connection:
        row = connection.execute(
            "SELECT source_timestamp, observed_at FROM polymarket_book_snapshots"
        ).fetchone()
    assert row == (observed.isoformat(), observed.isoformat())
