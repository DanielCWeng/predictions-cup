"""Operational health state for Polymarket capture."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(slots=True)
class IngestionHealth:
    websocket_connected: bool = False
    last_message_at: datetime | None = None
    last_pong_at: datetime | None = None
    last_valid_book_update_at: datetime | None = None
    last_book_change_at: datetime | None = None
    last_trade_at: datetime | None = None
    markets_subscribed: int = 0
    tokens_subscribed: int = 0
    reconnect_count: int = 0
    last_reconnect_reason: str | None = None
    parse_failures: int = 0
    unknown_event_count: int = 0
    book_uninitialized_delta_count: int = 0
    # Bounded per-reason counters; no per-token identifiers or hot-path logs.
    price_change_missing_subscribed: int = 0
    price_change_missing_other: int = 0
    price_change_missing_unclassified: int = 0
    tick_size_missing_subscribed: int = 0
    tick_size_missing_other: int = 0
    # Epochs are local to this recorder process, never external venue clocks.
    subscription_epoch: int = 0
    seed_attempt_epoch: int = 0
    seed_complete_epoch: int = 0
    seed_subscription_epoch: int = 0
    seed_expected_tokens: int = 0
    seed_initialized_tokens: int = 0
    seed_status: str = "NOT_STARTED"
    seed_completed_at: datetime | None = None
    connection_epoch: int = 0
    connection_seed_epoch: int = 0
    connection_subscription_epoch: int = 0
    subscribed_books_initialized: int = 0
    research_book_state_accepted: bool = False
    gamma_last_refresh_at: datetime | None = None
    gamma_last_status: str | None = None
    snapshot_last_at: datetime | None = None
    snapshot_last_status: str | None = None
    storage_failures: int = 0

    def as_record(self) -> dict[str, str | int | bool | None]:
        def iso(value: datetime | None) -> str | None:
            return value.isoformat() if value is not None else None

        return {
            "websocket_connected": self.websocket_connected,
            "last_message_at": iso(self.last_message_at),
            "last_pong_at": iso(self.last_pong_at),
            "last_valid_book_update_at": iso(self.last_valid_book_update_at),
            "last_book_change_at": iso(self.last_book_change_at),
            "last_trade_at": iso(self.last_trade_at),
            "markets_subscribed": self.markets_subscribed,
            "tokens_subscribed": self.tokens_subscribed,
            "reconnect_count": self.reconnect_count,
            "last_reconnect_reason": self.last_reconnect_reason,
            "parse_failures": self.parse_failures,
            "unknown_event_count": self.unknown_event_count,
            "book_uninitialized_delta_count": self.book_uninitialized_delta_count,
            "price_change_missing_subscribed": self.price_change_missing_subscribed,
            "price_change_missing_other": self.price_change_missing_other,
            "price_change_missing_unclassified": self.price_change_missing_unclassified,
            "tick_size_missing_subscribed": self.tick_size_missing_subscribed,
            "tick_size_missing_other": self.tick_size_missing_other,
            "subscription_epoch": self.subscription_epoch,
            "seed_attempt_epoch": self.seed_attempt_epoch,
            "seed_complete_epoch": self.seed_complete_epoch,
            "seed_subscription_epoch": self.seed_subscription_epoch,
            "seed_expected_tokens": self.seed_expected_tokens,
            "seed_initialized_tokens": self.seed_initialized_tokens,
            "seed_status": self.seed_status,
            "seed_completed_at": iso(self.seed_completed_at),
            "connection_epoch": self.connection_epoch,
            "connection_seed_epoch": self.connection_seed_epoch,
            "connection_subscription_epoch": self.connection_subscription_epoch,
            "subscribed_books_initialized": self.subscribed_books_initialized,
            "research_book_state_accepted": self.research_book_state_accepted,
            "gamma_last_refresh_at": iso(self.gamma_last_refresh_at),
            "gamma_last_status": self.gamma_last_status,
            "snapshot_last_at": iso(self.snapshot_last_at),
            "snapshot_last_status": self.snapshot_last_status,
            "storage_failures": self.storage_failures,
        }
