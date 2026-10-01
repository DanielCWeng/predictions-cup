"""Supabase private-broadcast subscriber for the SIG tournament Realtime topic."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any, cast

from realtime import RealtimeSubscribeStates
from realtime.types import BroadcastPayload, RealtimeChannelOptions
from supabase import acreate_client

from predictions_cup.sig.realtime_models import RealtimeTokenDto

Clock = Callable[[], datetime]
BatchHandler = Callable[[str, object, datetime], Awaitable[None]]
MaintenanceHandler = Callable[[datetime], Awaitable[None]]
ConnectedHandler = Callable[[], None]
logger = logging.getLogger(__name__)
_MAX_PENDING_BROADCASTS = 4_096
_MAX_PENDING_STATUSES = 1_024


class SubscriberExit(StrEnum):
    STOPPED = "stopped"
    TOKEN_REFRESH = "token_refresh"
    DISCONNECTED = "disconnected"
    SOCKET_ERROR = "socket_error"


# Supabase Realtime rejects joins beyond 100 channels on one socket
# ("ChannelRateLimitReached: Too many channels", observed live 2026-10-01), so
# per-market topics are sharded across sockets with headroom below that cap.
DEFAULT_MAX_CHANNELS_PER_CONNECTION = 90


class SupabaseTournamentSubscriber:
    """Private SIG Realtime subscription(s); REST remains authoritative.

    Accepts one ``topic`` (e.g. the user account channel) or many ``topics``
    (one per tournament market). All topics share one token and one exit
    lifecycle: any channel error, reconnect or token refresh ends the run so
    the caller can REST-resynchronize before trusting Realtime again.
    """

    def __init__(
        self,
        *,
        topic: str | None = None,
        topics: Sequence[str] | None = None,
        token: RealtimeTokenDto,
        token_refresh_margin_seconds: float = 300.0,
        subscribe_timeout_seconds: float = 15.0,
        maintenance_interval_seconds: float = 1.0,
        event_name: str = "market_batch",
        max_channels_per_connection: int = DEFAULT_MAX_CHANNELS_PER_CONNECTION,
        clock: Clock = lambda: datetime.now(UTC),
    ) -> None:
        if (topic is None) == (topics is None):
            raise ValueError("exactly one of topic or topics is required")
        resolved = (topic,) if topic is not None else tuple(dict.fromkeys(topics or ()))
        if not resolved or any(not item.strip() for item in resolved):
            raise ValueError("topics must be non-empty and non-blank")
        if token_refresh_margin_seconds <= 0:
            raise ValueError("token refresh margin must be positive")
        if maintenance_interval_seconds <= 0:
            raise ValueError("maintenance interval must be positive")
        if max_channels_per_connection < 1:
            raise ValueError("max_channels_per_connection must be positive")
        self._topics = resolved
        self._token = token
        self._refresh_margin = timedelta(seconds=token_refresh_margin_seconds)
        self._subscribe_timeout_seconds = subscribe_timeout_seconds
        self._maintenance_interval = timedelta(seconds=maintenance_interval_seconds)
        if not event_name.strip():
            raise ValueError("event_name must not be blank")
        self._event_name = event_name
        self._max_channels_per_connection = max_channels_per_connection
        self._clock = clock

    @property
    def topics(self) -> tuple[str, ...]:
        return self._topics

    def _shards(self) -> list[tuple[str, ...]]:
        size = self._max_channels_per_connection
        return [
            self._topics[index : index + size]
            for index in range(0, len(self._topics), size)
        ]

    async def run(
        self,
        *,
        on_batch: BatchHandler,
        on_connected: ConnectedHandler,
        stop_event: asyncio.Event,
        on_maintenance: MaintenanceHandler | None = None,
    ) -> SubscriberExit:
        payload_queue: asyncio.Queue[tuple[str, object, datetime]] = asyncio.Queue(
            maxsize=_MAX_PENDING_BROADCASTS
        )
        status_queue: asyncio.Queue[
            tuple[str, RealtimeSubscribeStates, Exception | None]
        ] = asyncio.Queue(
            maxsize=min(_MAX_PENDING_STATUSES, max(32, len(self._topics) * 2))
        )
        queue_overflow = asyncio.Event()
        connections: list[tuple[Any, list[Any]]] = []

        def broadcast_handler(topic: str) -> Callable[[BroadcastPayload], None]:
            def handle_broadcast(message: BroadcastPayload) -> None:
                try:
                    payload_queue.put_nowait(
                        (topic, message.get("payload"), self._clock())
                    )
                except asyncio.QueueFull:
                    queue_overflow.set()

            return handle_broadcast

        def status_handler(
            topic: str,
        ) -> Callable[[RealtimeSubscribeStates, Exception | None], None]:
            def handle_status(
                status: RealtimeSubscribeStates, error: Exception | None
            ) -> None:
                try:
                    status_queue.put_nowait((topic, status, error))
                except asyncio.QueueFull:
                    queue_overflow.set()

            return handle_status

        try:
            try:
                for shard in self._shards():
                    client = await acreate_client(
                        str(self._token.supabase_url),
                        self._token.anon_key.get_secret_value(),
                    )
                    shard_channels: list[Any] = []
                    connections.append((client, shard_channels))
                    await client.realtime.set_auth(self._token.token.get_secret_value())
                    for topic in shard:
                        channel_options: RealtimeChannelOptions = {
                            "config": {"broadcast": None, "presence": None, "private": True}
                        }
                        channel = client.channel(topic, channel_options)
                        shard_channels.append(channel)
                        await channel.on_broadcast(
                            self._event_name, broadcast_handler(topic)
                        ).subscribe(status_handler(topic))

                pending = set(self._topics)
                loop = asyncio.get_running_loop()
                deadline = loop.time() + self._subscribe_timeout_seconds
                while pending:
                    if queue_overflow.is_set():
                        logger.warning(
                            "SIG Realtime status queue overflow; reconnecting for "
                            "authoritative resync"
                        )
                        return SubscriberExit.DISCONNECTED
                    topic, status, _ = await asyncio.wait_for(
                        status_queue.get(), timeout=max(0.0, deadline - loop.time())
                    )
                    if status != RealtimeSubscribeStates.SUBSCRIBED:
                        logger.warning(
                            "SIG Realtime subscription rejected status=%s "
                            "pending_topics=%s total_topics=%s",
                            status,
                            len(pending),
                            len(self._topics),
                        )
                        return SubscriberExit.SOCKET_ERROR
                    pending.discard(topic)
            except Exception as exc:
                logger.warning(
                    "SIG Realtime subscription setup failed error=%s",
                    type(exc).__name__,
                )
                return SubscriberExit.SOCKET_ERROR
            logger.info(
                "SIG Realtime subscribed topics=%s connections=%s",
                len(self._topics),
                len(connections),
            )
            on_connected()

            refresh_at = self._token.expires_at - self._refresh_margin
            next_maintenance_at = self._clock()
            while True:
                if queue_overflow.is_set():
                    logger.warning(
                        "SIG Realtime payload/status queue overflow; reconnecting "
                        "for authoritative resync payload_capacity=%s status_capacity=%s",
                        payload_queue.maxsize,
                        status_queue.maxsize,
                    )
                    return SubscriberExit.DISCONNECTED
                if stop_event.is_set():
                    return SubscriberExit.STOPPED
                now = self._clock()
                if now >= refresh_at:
                    return SubscriberExit.TOKEN_REFRESH
                if on_maintenance is not None and now >= next_maintenance_at:
                    await on_maintenance(now)
                    next_maintenance_at = now + self._maintenance_interval
                    if stop_event.is_set():
                        return SubscriberExit.STOPPED

                # Supabase reuses the original subscribe callback when its channel
                # auto-rejoins after a socket reconnect. A second SUBSCRIBED status
                # therefore means this connection crossed a recovery boundary and
                # must exit so the outer loop can REST-resynchronize before trusting
                # any subsequent Realtime data.
                while True:
                    try:
                        _, reconnect_status, _ = status_queue.get_nowait()
                    except asyncio.QueueEmpty:
                        break
                    if reconnect_status == RealtimeSubscribeStates.SUBSCRIBED:
                        return SubscriberExit.DISCONNECTED
                    if reconnect_status in {
                        RealtimeSubscribeStates.CHANNEL_ERROR,
                        RealtimeSubscribeStates.TIMED_OUT,
                        RealtimeSubscribeStates.CLOSED,
                    }:
                        return SubscriberExit.SOCKET_ERROR

                for client, shard_channels in connections:
                    if any(channel.is_errored for channel in shard_channels):
                        return SubscriberExit.SOCKET_ERROR
                    if not client.realtime.is_connected or any(
                        channel.is_closed for channel in shard_channels
                    ):
                        return SubscriberExit.DISCONNECTED

                try:
                    topic, payload, observed_at = await asyncio.wait_for(
                        payload_queue.get(), timeout=0.25
                    )
                except TimeoutError:
                    continue
                await on_batch(topic, payload, observed_at)
        finally:
            for client, shard_channels in connections:
                try:
                    for channel in shard_channels:
                        await client.realtime.remove_channel(cast(Any, channel))
                except Exception:
                    await client.realtime.close()
