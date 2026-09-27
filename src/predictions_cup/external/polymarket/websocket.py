"""Persistent public Polymarket market WebSocket transport."""

from __future__ import annotations

import asyncio
import json
import logging
import random
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any

import aiohttp

from predictions_cup.external.polymarket.health import IngestionHealth
from predictions_cup.external.polymarket.models import JsonObject, utc_now

_LOG = logging.getLogger(__name__)
MessageHandler = Callable[[JsonObject, datetime], Awaitable[None]]
BeforeConnect = Callable[[], Awaitable[None]]


class MarketWebSocket:
    """Persistent market stream with bounded heartbeat sends and safe reconnects."""

    def __init__(
        self,
        url: str,
        health: IngestionHealth,
        *,
        heartbeat_seconds: float = 10.0,
        ping_send_timeout_seconds: float = 3.0,
        receive_liveness_timeout_seconds: float = 30.0,
        max_backoff_seconds: float = 30.0,
    ) -> None:
        self.url = url
        self.health = health
        self.heartbeat_seconds = heartbeat_seconds
        if receive_liveness_timeout_seconds <= 0:
            raise ValueError("receive_liveness_timeout_seconds must be positive")
        self.ping_send_timeout_seconds = ping_send_timeout_seconds
        self.receive_liveness_timeout_seconds = receive_liveness_timeout_seconds
        self.max_backoff_seconds = max_backoff_seconds
        self._desired_tokens: frozenset[str] = frozenset()
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._subscription_lock = asyncio.Lock()
        self._stopping = False
        self._last_receive_monotonic: float | None = None
        self._forced_reconnect_reason: str | None = None

    async def set_tokens(self, token_ids: tuple[str, ...]) -> None:
        new_tokens = frozenset(token_ids)
        async with self._subscription_lock:
            old_tokens = self._desired_tokens
            self._desired_tokens = new_tokens
            ws = self._ws
            if ws is None or ws.closed:
                return
            additions = sorted(new_tokens - old_tokens)
            removals = sorted(old_tokens - new_tokens)
            if additions:
                await ws.send_json({"assets_ids": additions, "operation": "subscribe"})
            if removals:
                await ws.send_json({"assets_ids": removals, "operation": "unsubscribe"})

    def stop(self) -> None:
        self._stopping = True

    async def run(self, handler: MessageHandler, before_connect: BeforeConnect) -> None:
        self._stopping = False
        attempt = 0
        has_connected = False
        timeout = aiohttp.ClientTimeout(total=None, sock_connect=30)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            while not self._stopping:
                if not self._desired_tokens:
                    await asyncio.sleep(1)
                    continue
                ping_task: asyncio.Task[None] | None = None
                try:
                    await before_connect()
                    async with session.ws_connect(self.url, heartbeat=None) as ws:
                        self._ws = ws
                        async with self._subscription_lock:
                            tokens = sorted(self._desired_tokens)
                            await ws.send_json({"assets_ids": tokens, "type": "market"})
                        self.health.websocket_connected = True
                        has_connected = True
                        attempt = 0
                        self._last_receive_monotonic = asyncio.get_running_loop().time()
                        self._forced_reconnect_reason = None
                        ping_task = asyncio.create_task(self._ping_loop(ws))
                        async for message in ws:
                            if self._stopping:
                                break
                            self._last_receive_monotonic = asyncio.get_running_loop().time()
                            if message.type == aiohttp.WSMsgType.TEXT:
                                observed_at = utc_now()
                                self.health.last_message_at = observed_at
                                if message.data == "PONG":
                                    self.health.last_pong_at = observed_at
                                    continue
                                await self._dispatch_text(message.data, observed_at, handler)
                            elif message.type in {
                                aiohttp.WSMsgType.CLOSE,
                                aiohttp.WSMsgType.CLOSED,
                                aiohttp.WSMsgType.ERROR,
                            }:
                                raise ConnectionError(f"websocket closed: {message.type.name}")
                        if not self._stopping:
                            reason = self._forced_reconnect_reason or "websocket stream ended"
                            self._forced_reconnect_reason = None
                            raise ConnectionError(reason)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    self.health.websocket_connected = False
                    self.health.last_reconnect_reason = f"{type(exc).__name__}: {exc}"
                    if has_connected or attempt > 0:
                        self.health.reconnect_count += 1
                    attempt += 1
                    _LOG.warning("Polymarket WebSocket reconnect: %s", exc)
                    delay = min(self.max_backoff_seconds, 2 ** min(attempt - 1, 5))
                    delay += random.uniform(0, min(1.0, delay * 0.25))
                    await asyncio.sleep(delay)
                finally:
                    self.health.websocket_connected = False
                    self._ws = None
                    self._last_receive_monotonic = None
                    if ping_task is not None:
                        ping_task.cancel()
                        await asyncio.gather(ping_task, return_exceptions=True)

    async def _ping_loop(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        loop = asyncio.get_running_loop()
        while not ws.closed and not self._stopping:
            await asyncio.sleep(self.heartbeat_seconds)
            last_receive = self._last_receive_monotonic
            if (
                last_receive is not None
                and loop.time() - last_receive > self.receive_liveness_timeout_seconds
            ):
                self._forced_reconnect_reason = "heartbeat receive/PONG liveness timed out"
                await ws.close()
                return
            try:
                await asyncio.wait_for(
                    ws.send_str("PING"), timeout=self.ping_send_timeout_seconds
                )
            except TimeoutError:
                self._forced_reconnect_reason = "heartbeat send timed out"
                await ws.close()
                return

    async def _dispatch_text(
        self,
        text: str,
        observed_at: datetime,
        handler: MessageHandler,
    ) -> None:
        try:
            raw: Any = json.loads(text)
        except json.JSONDecodeError:
            self.health.parse_failures += 1
            _LOG.warning("Malformed Polymarket WS JSON: %r", text[:500])
            return
        events = raw if isinstance(raw, list) else [raw]
        for event in events:
            if not isinstance(event, dict):
                self.health.parse_failures += 1
                _LOG.warning("Non-object Polymarket WS payload: %r", event)
                continue
            try:
                await handler(event, observed_at)
            except (ValueError, KeyError, TypeError) as exc:
                self.health.parse_failures += 1
                _LOG.warning("Rejected Polymarket WS event: %s payload=%r", exc, event)
