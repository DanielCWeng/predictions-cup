"""SQLite persistence for normalized Polymarket research capture."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from decimal import Decimal
from collections.abc import Iterable, Sequence
from pathlib import Path

from predictions_cup.external.polymarket.health import IngestionHealth
from predictions_cup.external.polymarket.models import BookSnapshot, PolymarketMarket, TradeEvent

_SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
CREATE TABLE IF NOT EXISTS polymarket_markets (
    market_id TEXT PRIMARY KEY,
    condition_id TEXT NOT NULL,
    question TEXT NOT NULL,
    slug TEXT,
    active INTEGER NOT NULL,
    closed INTEGER NOT NULL,
    accepting_orders INTEGER,
    start_at TEXT,
    end_at TEXT,
    resolution_source TEXT,
    event_id TEXT,
    event_slug TEXT,
    event_title TEXT,
    neg_risk INTEGER NOT NULL,
    neg_risk_market_id TEXT,
    market_group TEXT,
    group_item_title TEXT,
    group_item_threshold TEXT,
    parent_event_slug TEXT,
    min_tick_size TEXT,
    min_order_size TEXT,
    liquidity TEXT,
    volume TEXT,
    refreshed_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_polymarket_markets_condition
    ON polymarket_markets(condition_id);
CREATE TABLE IF NOT EXISTS polymarket_tokens (
    token_id TEXT PRIMARY KEY,
    market_id TEXT NOT NULL,
    condition_id TEXT NOT NULL,
    outcome TEXT NOT NULL,
    outcome_index INTEGER NOT NULL,
    FOREIGN KEY(market_id) REFERENCES polymarket_markets(market_id)
);
CREATE INDEX IF NOT EXISTS idx_polymarket_tokens_condition
    ON polymarket_tokens(condition_id);
CREATE TABLE IF NOT EXISTS polymarket_book_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    token_id TEXT NOT NULL,
    market_id TEXT NOT NULL,
    source_timestamp TEXT,
    observed_at TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    best_bid TEXT,
    best_ask TEXT,
    midpoint TEXT,
    spread TEXT,
    bids_json TEXT NOT NULL,
    asks_json TEXT NOT NULL,
    book_hash TEXT,
    min_order_size TEXT,
    tick_size TEXT,
    neg_risk INTEGER,
    last_trade_price TEXT
);
CREATE INDEX IF NOT EXISTS idx_polymarket_books_token_observed
    ON polymarket_book_snapshots(token_id, observed_at);
CREATE INDEX IF NOT EXISTS idx_polymarket_books_market_observed
    ON polymarket_book_snapshots(market_id, observed_at);
CREATE TABLE IF NOT EXISTS polymarket_trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    token_id TEXT NOT NULL,
    market_id TEXT NOT NULL,
    price TEXT NOT NULL,
    size TEXT,
    side TEXT,
    source_timestamp TEXT,
    observed_at TEXT NOT NULL,
    transaction_hash TEXT,
    fee_rate_bps TEXT
);
CREATE INDEX IF NOT EXISTS idx_polymarket_trades_token_observed
    ON polymarket_trades(token_id, observed_at);
CREATE UNIQUE INDEX IF NOT EXISTS idx_polymarket_trades_hash
    ON polymarket_trades(token_id, transaction_hash) WHERE transaction_hash IS NOT NULL;
CREATE TABLE IF NOT EXISTS ingestion_health (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    recorded_at TEXT NOT NULL,
    payload_json TEXT NOT NULL
);
"""


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _decimal_text(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


class PolymarketStorage:
    """Restart-safe local storage using the stdlib SQLite driver."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def upsert_markets(self, markets: Sequence[PolymarketMarket], refreshed_at: str) -> None:
        with self._connect() as connection:
            for market in markets:
                connection.execute(
                    """
                    INSERT INTO polymarket_markets (
                        market_id, condition_id, question, slug, active, closed,
                        accepting_orders, start_at, end_at, resolution_source,
                        event_id, event_slug, event_title, neg_risk, neg_risk_market_id,
                        market_group, group_item_title, group_item_threshold,
                        parent_event_slug, min_tick_size, min_order_size, liquidity,
                        volume, refreshed_at
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(market_id) DO UPDATE SET
                        condition_id=excluded.condition_id,
                        question=excluded.question,
                        slug=excluded.slug,
                        active=excluded.active,
                        closed=excluded.closed,
                        accepting_orders=excluded.accepting_orders,
                        start_at=excluded.start_at,
                        end_at=excluded.end_at,
                        resolution_source=excluded.resolution_source,
                        event_id=excluded.event_id,
                        event_slug=excluded.event_slug,
                        event_title=excluded.event_title,
                        neg_risk=excluded.neg_risk,
                        neg_risk_market_id=excluded.neg_risk_market_id,
                        market_group=excluded.market_group,
                        group_item_title=excluded.group_item_title,
                        group_item_threshold=excluded.group_item_threshold,
                        parent_event_slug=excluded.parent_event_slug,
                        min_tick_size=excluded.min_tick_size,
                        min_order_size=excluded.min_order_size,
                        liquidity=excluded.liquidity,
                        volume=excluded.volume,
                        refreshed_at=excluded.refreshed_at
                    """,
                    (
                        market.market_id,
                        market.condition_id,
                        market.question,
                        market.slug,
                        int(market.active),
                        int(market.closed),
                        None if market.accepting_orders is None else int(market.accepting_orders),
                        _iso(market.start_at),
                        _iso(market.end_at),
                        market.resolution_source,
                        market.event_id,
                        market.event_slug,
                        market.event_title,
                        int(market.neg_risk),
                        market.neg_risk_market_id,
                        market.market_group,
                        market.group_item_title,
                        _decimal_text(market.group_item_threshold),
                        market.parent_event_slug,
                        _decimal_text(market.min_tick_size),
                        _decimal_text(market.min_order_size),
                        _decimal_text(market.liquidity),
                        _decimal_text(market.volume),
                        refreshed_at,
                    ),
                )
                for token in market.tokens():
                    connection.execute(
                        """
                        INSERT INTO polymarket_tokens (
                            token_id, market_id, condition_id, outcome, outcome_index
                        ) VALUES (?,?,?,?,?)
                        ON CONFLICT(token_id) DO UPDATE SET
                            market_id=excluded.market_id,
                            condition_id=excluded.condition_id,
                            outcome=excluded.outcome,
                            outcome_index=excluded.outcome_index
                        """,
                        (
                            token.token_id,
                            token.market_id,
                            token.condition_id,
                            token.outcome,
                            token.outcome_index,
                        ),
                    )

    def append_snapshots(self, snapshots: Iterable[BookSnapshot], recorded_at: str) -> int:
        rows = []
        for snapshot in snapshots:
            rows.append(
                (
                    snapshot.token_id,
                    snapshot.market_id,
                    _iso(snapshot.source_timestamp),
                    snapshot.observed_at.isoformat(),
                    recorded_at,
                    _decimal_text(snapshot.best_bid),
                    _decimal_text(snapshot.best_ask),
                    _decimal_text(snapshot.midpoint),
                    _decimal_text(snapshot.spread),
                    json.dumps([[str(level.price), str(level.size)] for level in snapshot.bids]),
                    json.dumps([[str(level.price), str(level.size)] for level in snapshot.asks]),
                    snapshot.book_hash,
                    _decimal_text(snapshot.min_order_size),
                    _decimal_text(snapshot.tick_size),
                    None if snapshot.neg_risk is None else int(snapshot.neg_risk),
                    _decimal_text(snapshot.last_trade_price),
                )
            )
        if not rows:
            return 0
        with self._connect() as connection:
            connection.executemany(
                """
                INSERT INTO polymarket_book_snapshots (
                    token_id, market_id, source_timestamp, observed_at, recorded_at,
                    best_bid, best_ask, midpoint, spread, bids_json, asks_json,
                    book_hash, min_order_size, tick_size, neg_risk, last_trade_price
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                rows,
            )
        return len(rows)

    def append_trade(self, trade: TradeEvent) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO polymarket_trades (
                    token_id, market_id, price, size, side, source_timestamp,
                    observed_at, transaction_hash, fee_rate_bps
                ) VALUES (?,?,?,?,?,?,?,?,?)
                """,
                (
                    trade.token_id,
                    trade.market_id,
                    str(trade.price),
                    _decimal_text(trade.size),
                    trade.side,
                    _iso(trade.source_timestamp),
                    trade.observed_at.isoformat(),
                    trade.transaction_hash,
                    _decimal_text(trade.fee_rate_bps),
                ),
            )

    def append_health(self, health: IngestionHealth, recorded_at: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO ingestion_health (recorded_at, payload_json) VALUES (?,?)",
                (recorded_at, json.dumps(health.as_record(), sort_keys=True)),
            )
