from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any, cast

from predictions_cup.external.polymarket.health import IngestionHealth
from predictions_cup.external.polymarket.models import JsonObject
from predictions_cup.external.polymarket.websocket import MarketWebSocket


class FakeWebSocket:
    def __init__(self) -> None:
        self.closed = False
        self.sent: list[dict[str, object]] = []
        self.sent_text: list[str] = []

    async def send_json(self, payload: dict[str, object]) -> None:
        self.sent.append(payload)

    async def send_str(self, payload: str) -> None:
        self.sent_text.append(payload)

    async def close(self) -> None:
        self.closed = True


def test_incremental_subscription_updates_do_not_require_reconnect() -> None:
    health = IngestionHealth()
    transport = MarketWebSocket("wss://example.invalid/ws", health)
    fake = FakeWebSocket()
    transport._desired_tokens = frozenset({"a"})
    transport._ws = cast(Any, fake)

    asyncio.run(transport.set_tokens(("a", "b")))
    asyncio.run(transport.set_tokens(("b",)))

    assert fake.sent == [
        {"assets_ids": ["b"], "operation": "subscribe"},
        {"assets_ids": ["a"], "operation": "unsubscribe"},
    ]


def test_missing_receive_or_pong_liveness_forces_socket_close() -> None:
    health = IngestionHealth()
    transport = MarketWebSocket(
        "wss://example.invalid/ws",
        health,
        heartbeat_seconds=0.001,
        receive_liveness_timeout_seconds=0.001,
    )
    fake = FakeWebSocket()

    async def scenario() -> None:
        transport._last_receive_monotonic = asyncio.get_running_loop().time() - 1
        await transport._ping_loop(cast(Any, fake))

    asyncio.run(scenario())

    assert fake.closed is True
    assert transport._forced_reconnect_reason == "heartbeat receive/PONG liveness timed out"


def test_malformed_message_is_counted_without_calling_handler() -> None:
    health = IngestionHealth()
    transport = MarketWebSocket("wss://example.invalid/ws", health)
    called = False

    async def handler(payload: JsonObject, observed_at: datetime) -> None:
        nonlocal called
        del payload, observed_at
        called = True

    asyncio.run(
        transport._dispatch_text(
            "not-json", datetime(2026, 9, 25, tzinfo=UTC), handler
        )
    )

    assert health.parse_failures == 1
    assert called is False
