"""Durable normalized SQLite/WAL capture for SIG Realtime state."""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

from predictions_cup.models import OrderBook
from predictions_cup.sig.realtime_models import (
    BookDirtyDto,
    MarketSettledDto,
    RealtimeDeliveryDto,
    RealtimeTradeDto,
)


class SigRealtimeRecorder:
    """Small normalized recorder; bounded by configured time-based retention."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(path)
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA synchronous=NORMAL")
        self._connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS realtime_deliveries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                topic TEXT NOT NULL,
                revision INTEGER NOT NULL,
                previous_revision INTEGER NOT NULL,
                correlation_id TEXT NOT NULL,
                source_sequence_from INTEGER NOT NULL,
                source_sequence_through INTEGER NOT NULL,
                observed_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS realtime_trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                topic TEXT NOT NULL,
                revision INTEGER NOT NULL,
                exchange_id TEXT NOT NULL,
                market_id TEXT NOT NULL,
                tournament_id TEXT,
                price TEXT NOT NULL,
                quantity TEXT NOT NULL,
                executed_at TEXT NOT NULL,
                observed_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS ix_realtime_trades_exchange_time
                ON realtime_trades(exchange_id, observed_at);

            CREATE TABLE IF NOT EXISTS book_dirty_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                topic TEXT NOT NULL,
                revision INTEGER NOT NULL,
                exchange_id TEXT NOT NULL,
                market_id TEXT NOT NULL,
                tournament_id TEXT NOT NULL,
                source_at TEXT NOT NULL,
                observed_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS ix_book_dirty_events_exchange_time
                ON book_dirty_events(exchange_id, observed_at);

            CREATE TABLE IF NOT EXISTS market_settled_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                topic TEXT NOT NULL,
                revision INTEGER NOT NULL,
                market_id TEXT NOT NULL,
                tournament_id TEXT NOT NULL,
                settled_with TEXT NOT NULL,
                source_at TEXT NOT NULL,
                observed_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS ix_market_settled_events_market_time
                ON market_settled_events(market_id, observed_at);

            CREATE TABLE IF NOT EXISTS book_observations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                exchange_id TEXT NOT NULL,
                market_id TEXT NOT NULL,
                tournament_id TEXT NOT NULL,
                book_json TEXT NOT NULL,
                best_bid TEXT,
                best_ask TEXT,
                rest_observed_at TEXT NOT NULL,
                reason TEXT NOT NULL,
                triggering_revision INTEGER
            );
            CREATE INDEX IF NOT EXISTS ix_book_observations_exchange_time
                ON book_observations(exchange_id, rest_observed_at);

            CREATE TABLE IF NOT EXISTS trust_transitions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                topic TEXT NOT NULL,
                exchange_id TEXT,
                transition TEXT NOT NULL,
                observed_at TEXT NOT NULL,
                revision INTEGER,
                detail TEXT
            );
            CREATE INDEX IF NOT EXISTS ix_trust_transitions_time
                ON trust_transitions(observed_at);
            """
        )
        self._connection.commit()

    def close(self) -> None:
        self._connection.close()

    def record_delivery(
        self,
        *,
        topic: str,
        delivery: RealtimeDeliveryDto,
        observed_at: datetime,
    ) -> None:
        self._connection.execute(
            """
            INSERT INTO realtime_deliveries (
                topic, revision, previous_revision, correlation_id,
                source_sequence_from, source_sequence_through, observed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                topic,
                delivery.revision,
                delivery.previous_revision,
                delivery.correlation_id,
                delivery.source_sequence_from,
                delivery.source_sequence_through,
                _iso(observed_at),
            ),
        )
        self._connection.commit()

    def record_trade(
        self,
        *,
        topic: str,
        revision: int,
        trade: RealtimeTradeDto,
        observed_at: datetime,
    ) -> None:
        self._connection.execute(
            """
            INSERT INTO realtime_trades (
                topic, revision, exchange_id, market_id, tournament_id,
                price, quantity, executed_at, observed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                topic,
                revision,
                trade.exchange_id,
                trade.market_id,
                trade.tournament_id,
                str(trade.price),
                str(trade.quantity),
                _iso(trade.executed_at),
                _iso(observed_at),
            ),
        )
        self._connection.commit()

    def record_book_dirty(
        self,
        *,
        topic: str,
        revision: int,
        event: BookDirtyDto,
        observed_at: datetime,
    ) -> None:
        self._connection.execute(
            """
            INSERT INTO book_dirty_events (
                topic, revision, exchange_id, market_id, tournament_id,
                source_at, observed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                topic,
                revision,
                event.exchange_id,
                event.market_id,
                event.tournament_id,
                _iso(event.at),
                _iso(observed_at),
            ),
        )
        self._connection.commit()

    def record_market_settled(
        self,
        *,
        topic: str,
        revision: int,
        event: MarketSettledDto,
        observed_at: datetime,
    ) -> None:
        self._connection.execute(
            """
            INSERT INTO market_settled_events (
                topic, revision, market_id, tournament_id, settled_with,
                source_at, observed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                topic,
                revision,
                event.market_id,
                event.tournament_id,
                event.settled_with,
                _iso(event.at),
                _iso(observed_at),
            ),
        )
        self._connection.commit()

    def record_book(
        self,
        *,
        market_id: str,
        tournament_id: str,
        book: OrderBook,
        observed_at: datetime,
        reason: str,
        triggering_revision: int | None,
    ) -> None:
        best_bid = str(book.bids[0].price) if book.bids else None
        best_ask = str(book.asks[0].price) if book.asks else None
        self._connection.execute(
            """
            INSERT INTO book_observations (
                exchange_id, market_id, tournament_id, book_json,
                best_bid, best_ask, rest_observed_at, reason, triggering_revision
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                book.exchange_id,
                market_id,
                tournament_id,
                book.model_dump_json(),
                best_bid,
                best_ask,
                _iso(observed_at),
                reason,
                triggering_revision,
            ),
        )
        self._connection.commit()

    def record_transition(
        self,
        *,
        topic: str,
        transition: str,
        observed_at: datetime,
        exchange_id: str | None = None,
        revision: int | None = None,
        detail: str | None = None,
    ) -> None:
        self._connection.execute(
            """
            INSERT INTO trust_transitions (
                topic, exchange_id, transition, observed_at, revision, detail
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (topic, exchange_id, transition, _iso(observed_at), revision, detail),
        )
        self._connection.commit()

    def prune_before(self, cutoff: datetime) -> None:
        cutoff_iso = _iso(cutoff)
        with self._connection:
            self._connection.execute(
                "DELETE FROM realtime_deliveries WHERE observed_at < ?", (cutoff_iso,)
            )
            self._connection.execute(
                "DELETE FROM realtime_trades WHERE observed_at < ?", (cutoff_iso,)
            )
            self._connection.execute(
                "DELETE FROM book_dirty_events WHERE observed_at < ?", (cutoff_iso,)
            )
            self._connection.execute(
                "DELETE FROM market_settled_events WHERE observed_at < ?", (cutoff_iso,)
            )
            self._connection.execute(
                "DELETE FROM book_observations WHERE rest_observed_at < ?", (cutoff_iso,)
            )
            self._connection.execute(
                "DELETE FROM trust_transitions WHERE observed_at < ?", (cutoff_iso,)
            )


def _iso(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("recorder timestamps must be timezone aware")
    return value.isoformat()
