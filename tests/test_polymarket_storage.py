from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from predictions_cup.external.polymarket.health import IngestionHealth
from predictions_cup.external.polymarket.models import PolymarketMarket
from predictions_cup.external.polymarket.storage import PolymarketStorage


def market() -> PolymarketMarket:
    return PolymarketMarket(
        market_id="market-1",
        condition_id="condition-1",
        question="2026 election market",
        slug="market-1",
        outcomes=("Yes", "No"),
        token_ids=("token-yes", "token-no"),
        active=True,
        closed=False,
        accepting_orders=True,
        start_at=None,
        end_at=datetime(2026, 11, 4, tzinfo=UTC),
        resolution_source=None,
        event_id=None,
        event_slug=None,
        event_title=None,
        neg_risk=False,
        neg_risk_market_id=None,
        market_group=None,
        group_item_title=None,
        group_item_threshold=None,
        parent_event_slug=None,
        min_tick_size=None,
        min_order_size=None,
        liquidity=None,
        volume=None,
    )


def test_sqlite_is_operational_state_only(tmp_path: Path) -> None:
    path = tmp_path / "capture.sqlite3"
    storage = PolymarketStorage(path)
    storage.initialize()
    at = datetime(2026, 9, 25, 12, tzinfo=UTC)
    storage.upsert_markets((market(),), at.isoformat())
    storage.append_health(IngestionHealth(websocket_connected=True), at.isoformat())

    with sqlite3.connect(path) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        market_count = connection.execute(
            "SELECT COUNT(*) FROM polymarket_markets"
        ).fetchone()[0]
        token_count = connection.execute(
            "SELECT COUNT(*) FROM polymarket_tokens"
        ).fetchone()[0]
        health_count = connection.execute(
            "SELECT COUNT(*) FROM ingestion_health"
        ).fetchone()[0]

    assert market_count == 1
    assert token_count == 2
    assert health_count == 1
    assert "polymarket_book_observations" not in tables
    assert "polymarket_book_changes" not in tables
    assert "polymarket_book_snapshots" not in tables
    assert "polymarket_trades" not in tables


def test_operational_sqlite_survives_restart(tmp_path: Path) -> None:
    path = tmp_path / "capture.sqlite3"
    first = PolymarketStorage(path)
    first.initialize()
    at = datetime(2026, 9, 25, 12, tzinfo=UTC)
    first.upsert_markets((market(),), at.isoformat())

    second = PolymarketStorage(path)
    second.initialize()
    second.append_health(
        IngestionHealth(websocket_connected=False),
        at.isoformat(),
    )

    with sqlite3.connect(path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM polymarket_markets"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM ingestion_health"
        ).fetchone()[0] == 1


def test_sqlite_connection_uses_one_mib_page_cache(tmp_path: Path) -> None:
    storage = PolymarketStorage(tmp_path / "capture.sqlite3")
    with storage._connect() as connection:
        assert connection.execute("PRAGMA cache_size").fetchone()[0] == -1024
