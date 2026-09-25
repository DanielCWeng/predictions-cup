"""Supabase private-broadcast subscriber for the SIG tournament Realtime topic."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any, cast

from realtime import RealtimeSubscribeStates
from realtime.types import BroadcastPayload, RealtimeChannelOptions
from supabase import acreate_client

from predictions_cup.sig.realtime_models import RealtimeTokenDto

Clock = Callable[[], datetime]
BatchHandler = Callable[[str, object, datetime], Awaitable[None]]
ConnectedHandler = Callable[[], None]


class SubscriberExit(StrEnum):
    STOPPED = "stopped"
    TOKEN_REFRESH = "token_refresh"
    DISCONNECTED = "disconnected"
    SOCKET_ERROR = "socket_error"


class SupabaseTournamentSubscriber:
    """One private tournament subscription; REST remains authoritative."""

    def __init__(
        self,
        *,
        topic: str,
        token: RealtimeTokenDto,
        token_refresh_margin_seconds: float = 300.0,
        subscribe_timeout_seconds: float = 15.0,
        clock: Clock = lambda: datetime.now(UTC),
    ) -> None:
        if token_refresh_margin_seconds <= 0:
            raise ValueError("token refresh margin must be positive")
        self._topic = topic
        self._token = token
        self._refresh_margin = timedelta(seconds=token_refresh_margin_seconds)
        self._subscribe_timeout_seconds = subscribe_timeout_seconds
        self._clock = clock

    async def run(
        self,
        *,
        on_batch: BatchHandler,
        on_connected: ConnectedHandler,
        stop_event: asyncio.Event,
    ) -> SubscriberExit:
        client = await acreate_client(
            str(self._token.supabase_url), self._token.anon_key.get_secret_value()
        )
        await client.realtime.set_auth(self._token.token.get_secret_value())
        channel_options: RealtimeChannelOptions = {
            "config": {"broadcast": None, "presence": None, "private": True}
        }
        channel = client.channel(self._topic, channel_options)
        payload_queue: asyncio.Queue[tuple[object, datetime]] = asyncio.Queue()
        status_queue: asyncio.Queue[tuple[RealtimeSubscribeStates, Exception | None]] = (
            asyncio.Queue()
        )

        def handle_broadcast(message: BroadcastPayload) -> None:
            payload_queue.put_nowait((message.get("payload"), self._clock()))

        def handle_status(status: RealtimeSubscribeStates, error: Exception | None) -> None:
            status_queue.put_nowait((status, error))

        try:
            await channel.on_broadcast("market_batch", handle_broadcast).subscribe(handle_status)
            status, _ = await asyncio.wait_for(
                status_queue.get(), timeout=self._subscribe_timeout_seconds
            )
            if status != RealtimeSubscribeStates.SUBSCRIBED:
                return SubscriberExit.SOCKET_ERROR
            on_connected()

            refresh_at = self._token.expires_at - self._refresh_margin
            while True:
                if stop_event.is_set():
                    return SubscriberExit.STOPPED
                if self._clock() >= refresh_at:
                    return SubscriberExit.TOKEN_REFRESH

                # Supabase reuses the original subscribe callback when its channel
                # auto-rejoins after a socket reconnect. A second SUBSCRIBED status
                # therefore means this connection crossed a recovery boundary and
                # must exit so the outer loop can REST-resynchronize before trusting
                # any subsequent Realtime data.
                try:
                    reconnect_status, _ = status_queue.get_nowait()
                except asyncio.QueueEmpty:
                    reconnect_status = None
                if reconnect_status == RealtimeSubscribeStates.SUBSCRIBED:
                    return SubscriberExit.DISCONNECTED
                if reconnect_status in {
                    RealtimeSubscribeStates.CHANNEL_ERROR,
                    RealtimeSubscribeStates.TIMED_OUT,
                    RealtimeSubscribeStates.CLOSED,
                }:
                    return SubscriberExit.SOCKET_ERROR

                if channel.is_errored:
                    return SubscriberExit.SOCKET_ERROR
                if not client.realtime.is_connected or channel.is_closed:
                    return SubscriberExit.DISCONNECTED

                try:
                    payload, observed_at = await asyncio.wait_for(
                        payload_queue.get(), timeout=0.25
                    )
                except TimeoutError:
                    continue
                await on_batch(self._topic, payload, observed_at)
        finally:
            try:
                await client.realtime.remove_channel(cast(Any, channel))
            except Exception:
                await client.realtime.close()
