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
            "gamma_last_refresh_at": iso(self.gamma_last_refresh_at),
            "gamma_last_status": self.gamma_last_status,
            "snapshot_last_at": iso(self.snapshot_last_at),
            "snapshot_last_status": self.snapshot_last_status,
            "storage_failures": self.storage_failures,
        }
