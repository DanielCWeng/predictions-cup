from __future__ import annotations

from datetime import UTC, datetime

from predictions_cup.external.polymarket.health import IngestionHealth


def test_feed_activity_and_book_change_health_are_separate() -> None:
    health = IngestionHealth()
    now = datetime(2026, 9, 25, tzinfo=UTC)
    health.last_message_at = now

    assert health.last_message_at == now
    assert health.last_book_change_at is None
    assert health.last_trade_at is None
