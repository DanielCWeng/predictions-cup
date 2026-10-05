"""Supabase private-broadcast subscriber for the SIG tournament Realtime topic."""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
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
EventHandler = Callable[[str, str, object, datetime], Awaitable[None]]
MaintenanceHandler = Callable[[datetime], Awaitable[None]]
ConnectedHandler = Callable[[], None]
logger = logging.getLogger(__name__)
_MAX_PENDING_BROADCASTS = 4_096
_MAX_PENDING_STATUSES = 1_024
_METRICS_INTERVAL_SECONDS = 60.0
_PROCESSING_SAMPLE_LIMIT = 10_000


def _percentile(sorted_samples: list[float], quantile: float) -> float:
    if not sorted_samples:
        return 0.0
    index = min(len(sorted_samples) - 1, int((len(sorted_samples) - 1) * quantile))
    return sorted_samples[index]


class SubscriberExit(StrEnum):
    STOPPED = "stopped"
    TOKEN_REFRESH = "token_refresh"
    DISCONNECTED = "disconnected"
    SOCKET_ERROR = "socket_error"


# Supabase Realtime rejects joins beyond 100 channels on one socket
# ("ChannelRateLimitReached: Too many channels", observed live 2026-10-01), so
# per-market topics are sharded across sockets with headroom below that cap.
DEFAULT_MAX_CHANNELS_PER_CONNECTION = 90
# Bound joins to ten per socket in each wave, then wait for their acknowledgements.
DEFAULT_JOIN_BATCH_SIZE = 10


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
        event_names: Sequence[str] | None = None,
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
        self._event_names = tuple(dict.fromkeys((event_name, *(event_names or ()))))
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
        on_event: EventHandler | None = None,
        refetch_metrics: Callable[[], tuple[int, int]] | None = None,
    ) -> SubscriberExit:
        payload_queue: asyncio.Queue[tuple[str, str, object, datetime]] = asyncio.Queue(
            maxsize=_MAX_PENDING_BROADCASTS
        )
        queue_depth_max = 0
        processing_seconds: deque[float] = deque(maxlen=_PROCESSING_SAMPLE_LIMIT)
        status_queue: asyncio.Queue[
            tuple[str, RealtimeSubscribeStates, Exception | None]
        ] = asyncio.Queue(
            maxsize=min(_MAX_PENDING_STATUSES, max(32, len(self._topics) * 2))
        )
        queue_overflow = asyncio.Event()
        connections: list[tuple[Any, list[Any]]] = []
        metrics_task: asyncio.Task[None] | None = None
        metrics_at = time.monotonic() + _METRICS_INTERVAL_SECONDS

        def emit_metrics_if_due(now_monotonic: float) -> None:
            nonlocal queue_depth_max, metrics_at
            if now_monotonic < metrics_at:
                return
            samples = sorted(processing_seconds)
            p50 = _percentile(samples, 0.50)
            p99 = _percentile(samples, 0.99)
            in_flight, coalesced = (
                (0, 0) if refetch_metrics is None else refetch_metrics()
            )
            logger.info(
                "SIG Realtime consumer metrics queue_depth_max=%s "
                "payload_processing_p50_ms=%.3f payload_processing_p99_ms=%.3f "
                "refetch_in_flight=%s refetch_coalesced=%s samples=%s",
                queue_depth_max,
                p50 * 1_000,
                p99 * 1_000,
                in_flight,
                coalesced,
                len(samples),
            )
            queue_depth_max = payload_queue.qsize()
            processing_seconds.clear()
            metrics_at = now_monotonic + _METRICS_INTERVAL_SECONDS

        def broadcast_handler(
            topic: str, event_name: str
        ) -> Callable[[BroadcastPayload], None]:
            def handle_broadcast(message: BroadcastPayload) -> None:
                nonlocal queue_depth_max
                try:
                    payload_queue.put_nowait(
                        (topic, event_name, message.get("payload"), self._clock())
                    )
                    queue_depth_max = max(queue_depth_max, payload_queue.qsize())
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
                shards = self._shards()
                for _ in shards:
                    client = await acreate_client(
                        str(self._token.supabase_url),
                        self._token.anon_key.get_secret_value(),
                    )
                    shard_channels: list[Any] = []
                    connections.append((client, shard_channels))
                    await client.realtime.set_auth(self._token.token.get_secret_value())

                pending = set(self._topics)
                loop = asyncio.get_running_loop()
                batch_count = max(
                    (
                        (len(shard) + DEFAULT_JOIN_BATCH_SIZE - 1)
                        // DEFAULT_JOIN_BATCH_SIZE
                        for shard in shards
                    ),
                    default=0,
                )
                for batch_index in range(batch_count):
                    if queue_overflow.is_set():
                        logger.warning(
                            "SIG Realtime status queue overflow; reconnecting "
                            "for authoritative resync"
                        )
                        return SubscriberExit.DISCONNECTED
                    batch_pending: set[str] = set()
                    for shard, (client, shard_channels) in zip(
                        shards, connections, strict=True
                    ):
                        start = batch_index * DEFAULT_JOIN_BATCH_SIZE
                        topics = shard[start : start + DEFAULT_JOIN_BATCH_SIZE]
                        for topic in topics:
                            channel_options: RealtimeChannelOptions = {
                                "config": {
                                    "broadcast": None,
                                    "presence": None,
                                    "private": True,
                                }
                            }
                            channel = client.channel(topic, channel_options)
                            shard_channels.append(channel)
                            batch_pending.add(topic)
                            for event_name in self._event_names:
                                channel = channel.on_broadcast(
                                    event_name, broadcast_handler(topic, event_name)
                                )
                            await channel.subscribe(status_handler(topic))

                    deadline = loop.time() + self._subscribe_timeout_seconds
                    while batch_pending:
                        if queue_overflow.is_set():
                            logger.warning(
                                "SIG Realtime status queue overflow; reconnecting "
                                "for authoritative resync"
                            )
                            return SubscriberExit.DISCONNECTED
                        topic, status, _ = await asyncio.wait_for(
                            status_queue.get(),
                            timeout=max(0.0, deadline - loop.time()),
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
                        batch_pending.discard(topic)
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

            async def metrics_monitor() -> None:
                while True:
                    await asyncio.sleep(_METRICS_INTERVAL_SECONDS)
                    emit_metrics_if_due(time.monotonic())

            metrics_task = asyncio.create_task(
                metrics_monitor(), name="sig-realtime-consumer-metrics"
            )

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
                    topic, event_name, payload, observed_at = await asyncio.wait_for(
                        payload_queue.get(), timeout=0.25
                    )
                except TimeoutError:
                    emit_metrics_if_due(time.monotonic())
                    continue
                processing_started = time.monotonic()
                try:
                    if event_name == self._event_name:
                        await on_batch(topic, payload, observed_at)
                    elif on_event is not None:
                        await on_event(topic, event_name, payload, observed_at)
                finally:
                    processing_seconds.append(time.monotonic() - processing_started)
                emit_metrics_if_due(time.monotonic())
        finally:
            if metrics_task is not None:
                metrics_task.cancel()
                await asyncio.gather(metrics_task, return_exceptions=True)
            for client, shard_channels in connections:
                try:
                    for channel in shard_channels:
                        await client.realtime.remove_channel(cast(Any, channel))
                except Exception:
                    await client.realtime.close()
