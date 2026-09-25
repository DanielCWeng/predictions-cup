from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from predictions_cup.external.polymarket.models import BookLevel, BookSnapshot
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

    second = PolymarketStorage(path)
    second.initialize()
    later = observed.replace(second=1)
    assert second.append_snapshots((snapshot(later),), later.isoformat()) == 1

    with sqlite3.connect(path) as connection:
        count = connection.execute("SELECT COUNT(*) FROM polymarket_book_snapshots").fetchone()[0]
    assert count == 2


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
