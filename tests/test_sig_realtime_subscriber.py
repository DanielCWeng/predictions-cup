from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any, Self

import pytest
from realtime import RealtimeSubscribeStates
from realtime.types import RealtimeChannelOptions

import predictions_cup.sig.realtime_subscriber as subscriber_module
from predictions_cup.sig.realtime_models import RealtimeTokenDto
from predictions_cup.sig.realtime_subscriber import (
    SubscriberExit,
    SupabaseTournamentSubscriber,
)

StatusCallback = Callable[[RealtimeSubscribeStates, Exception | None], None]
BroadcastCallback = Callable[[dict[str, Any]], None]


class FakeChannel:
    def __init__(self, follow_up_status: RealtimeSubscribeStates | None) -> None:
        self.follow_up_status = follow_up_status
        self.is_errored = False
        self.is_closed = False
        self.broadcast_callback: BroadcastCallback | None = None

    def on_broadcast(self, event: str, callback: BroadcastCallback) -> Self:
        assert event == "market_batch"
        self.broadcast_callback = callback
        return self

    async def subscribe(self, callback: StatusCallback) -> Self:
        callback(RealtimeSubscribeStates.SUBSCRIBED, None)
        if self.follow_up_status is not None:
            asyncio.get_running_loop().call_soon(
                callback, self.follow_up_status, None
            )
        return self


class FakeRealtime:
    def __init__(self) -> None:
        self.is_connected = True
        self.auth_token: str | None = None
        self.removed = False
        self.closed = False

    async def set_auth(self, token: str | None) -> None:
        self.auth_token = token

    async def remove_channel(self, channel: object) -> None:
        del channel
        self.removed = True

    async def close(self) -> None:
        self.closed = True


class FakeClient:
    def __init__(self, follow_up_status: RealtimeSubscribeStates | None) -> None:
        self.realtime = FakeRealtime()
        self.fake_channel = FakeChannel(follow_up_status)
        self.topic: str | None = None
        self.options: RealtimeChannelOptions | None = None

    def channel(
        self, topic: str, options: RealtimeChannelOptions | None = None
    ) -> FakeChannel:
        self.topic = topic
        self.options = options
        return self.fake_channel


def _token(*, expires_at: datetime) -> RealtimeTokenDto:
    return RealtimeTokenDto.model_validate(
        {
            "token": "jwt-secret",
            "expiresAt": expires_at.isoformat(),
            "supabaseUrl": "https://example.supabase.co",
            "anonKey": "anon-secret",
            "channels": {"user": "user:test"},
        }
    )


async def _never_batch(topic: str, payload: object, observed_at: datetime) -> None:
    del topic, payload, observed_at
    raise AssertionError("no batch expected in subscriber lifecycle test")


def _install_fake_client(
    monkeypatch: pytest.MonkeyPatch,
    *,
    follow_up_status: RealtimeSubscribeStates | None,
) -> FakeClient:
    client = FakeClient(follow_up_status)

    async def fake_acreate_client(url: str, key: str) -> FakeClient:
        assert url.startswith("https://example.supabase.co")
        assert key == "anon-secret"
        return client

    monkeypatch.setattr(subscriber_module, "acreate_client", fake_acreate_client)
    return client


def test_subscriber_rejoin_ack_exits_for_authoritative_resync(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        client = _install_fake_client(
            monkeypatch, follow_up_status=RealtimeSubscribeStates.SUBSCRIBED
        )
        connected: list[bool] = []
        subscriber = SupabaseTournamentSubscriber(
            topic="tournament:cup",
            token=_token(expires_at=datetime.now(UTC) + timedelta(hours=2)),
        )

        outcome = await subscriber.run(
            on_batch=_never_batch,
            on_connected=lambda: connected.append(True),
            stop_event=asyncio.Event(),
        )

        assert outcome == SubscriberExit.DISCONNECTED
        assert connected == [True]
        assert client.realtime.auth_token == "jwt-secret"
        assert client.topic == "tournament:cup"
        assert client.options == {
            "config": {"broadcast": None, "presence": None, "private": True}
        }
        assert client.realtime.removed is True

    asyncio.run(scenario())


def test_subscriber_follow_up_error_exits_as_socket_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        _install_fake_client(
            monkeypatch, follow_up_status=RealtimeSubscribeStates.CHANNEL_ERROR
        )
        subscriber = SupabaseTournamentSubscriber(
            topic="tournament:cup",
            token=_token(expires_at=datetime.now(UTC) + timedelta(hours=2)),
        )

        outcome = await subscriber.run(
            on_batch=_never_batch,
            on_connected=lambda: None,
            stop_event=asyncio.Event(),
        )

        assert outcome == SubscriberExit.SOCKET_ERROR

    asyncio.run(scenario())


def test_quiet_subscriber_runs_authoritative_book_maintenance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        _install_fake_client(monkeypatch, follow_up_status=None)
        stop_event = asyncio.Event()
        maintenance_calls: list[datetime] = []

        async def on_maintenance(observed_at: datetime) -> None:
            maintenance_calls.append(observed_at)
            stop_event.set()

        subscriber = SupabaseTournamentSubscriber(
            topic="tournament:cup",
            token=_token(expires_at=datetime.now(UTC) + timedelta(hours=2)),
            maintenance_interval_seconds=1.0,
        )

        outcome = await subscriber.run(
            on_batch=_never_batch,
            on_connected=lambda: None,
            stop_event=stop_event,
            on_maintenance=on_maintenance,
        )

        assert outcome == SubscriberExit.STOPPED
        assert len(maintenance_calls) == 1
        assert maintenance_calls[0].tzinfo is not None

    asyncio.run(scenario())


def test_subscriber_token_refresh_and_graceful_stop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def refresh_scenario() -> None:
        _install_fake_client(monkeypatch, follow_up_status=None)
        subscriber = SupabaseTournamentSubscriber(
            topic="tournament:cup",
            token=_token(expires_at=datetime.now(UTC) + timedelta(seconds=60)),
            token_refresh_margin_seconds=300,
        )
        outcome = await subscriber.run(
            on_batch=_never_batch,
            on_connected=lambda: None,
            stop_event=asyncio.Event(),
        )
        assert outcome == SubscriberExit.TOKEN_REFRESH

    async def stop_scenario() -> None:
        _install_fake_client(monkeypatch, follow_up_status=None)
        stop_event = asyncio.Event()
        stop_event.set()
        subscriber = SupabaseTournamentSubscriber(
            topic="tournament:cup",
            token=_token(expires_at=datetime.now(UTC) + timedelta(hours=2)),
        )
        outcome = await subscriber.run(
            on_batch=_never_batch,
            on_connected=lambda: None,
            stop_event=stop_event,
        )
        assert outcome == SubscriberExit.STOPPED

    asyncio.run(refresh_scenario())
    asyncio.run(stop_scenario())
