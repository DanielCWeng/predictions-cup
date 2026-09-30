"""Structurally read-only Kalshi public REST transport.

Only documented public market-data GET routes are exposed. There is deliberately
no authentication, order, portfolio, cancel, amend, transfer, or generic method
surface in this module.
"""

from __future__ import annotations

import asyncio
import json
import random
import re
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import quote

import aiohttp

from predictions_cup.external.kalshi.models import (
    KalshiEvent,
    KalshiHealth,
    KalshiMarket,
    KalshiOrderBook,
    KalshiPage,
    KalshiPayloadError,
    KalshiTrade,
    KalshiTradePage,
)

DEFAULT_BASE_URL = "https://external-api.kalshi.com/trade-api/v2"
API_VERSION = "kalshi-trade-api-v2"
_TICKER = re.compile(r"^[A-Za-z0-9._:-]+$")


class KalshiReadError(RuntimeError):
    """Public market-data request exhausted its bounded retry budget."""


class KalshiPublicClient:
    """Small public market-data transport with no mutation capability."""

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        *,
        timeout_seconds: float = 10.0,
        max_attempts: int = 4,
        backoff_base_seconds: float = 0.25,
        backoff_max_seconds: float = 4.0,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        random_fraction: Callable[[], float] = random.random,
        wall_clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        if not base_url.startswith(("https://", "http://")):
            raise ValueError("Kalshi base_url must be http(s)")
        if timeout_seconds <= 0 or max_attempts <= 0:
            raise ValueError("timeout_seconds and max_attempts must be positive")
        if backoff_base_seconds <= 0 or backoff_max_seconds < backoff_base_seconds:
            raise ValueError("invalid retry backoff")
        self.base_url = base_url.rstrip("/")
        self.timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        self.max_attempts = max_attempts
        self.backoff_base_seconds = backoff_base_seconds
        self.backoff_max_seconds = backoff_max_seconds
        self._sleep = sleep
        self._random_fraction = random_fraction
        self._wall_clock = wall_clock
        self._last_success_at: datetime | None = None
        self._last_source_timestamp: datetime | None = None
        self._consecutive_failures = 0

    async def get_market(self, ticker: str) -> KalshiMarket:
        safe = self._ticker(ticker)
        raw, observed = await self._request_json(f"/markets/{safe}")
        payload = raw.get("market")
        if not isinstance(payload, dict):
            raise KalshiPayloadError("market response missing market object")
        market = KalshiMarket.from_api(payload, observed_at=observed, api_version=API_VERSION)
        self._record_source_time(market.source_updated_at)
        return market

    async def list_markets(
        self,
        *,
        cursor: str | None = None,
        limit: int = 100,
        status: str | None = None,
        event_ticker: str | None = None,
        series_ticker: str | None = None,
    ) -> KalshiPage:
        if not 1 <= limit <= 1000:
            raise ValueError("limit must be between 1 and 1000")
        params: dict[str, str] = {"limit": str(limit)}
        if cursor:
            params["cursor"] = cursor
        if status:
            params["status"] = status
        if event_ticker:
            params["event_ticker"] = self._ticker(event_ticker)
        if series_ticker:
            params["series_ticker"] = self._ticker(series_ticker)
        raw, observed = await self._request_json("/markets", params=params)
        rows = raw.get("markets")
        if not isinstance(rows, list):
            raise KalshiPayloadError("markets response missing markets array")
        markets: list[KalshiMarket] = []
        for row in rows:
            if not isinstance(row, dict):
                raise KalshiPayloadError("market row must be an object")
            market = KalshiMarket.from_api(row, observed_at=observed, api_version=API_VERSION)
            markets.append(market)
            self._record_source_time(market.source_updated_at)
        next_cursor = raw.get("cursor")
        if next_cursor is not None and not isinstance(next_cursor, str):
            raise KalshiPayloadError("cursor must be a string when present")
        return KalshiPage(tuple(markets), next_cursor or None)

    async def get_event(self, event_ticker: str) -> KalshiEvent:
        safe = self._ticker(event_ticker)
        raw, observed = await self._request_json(f"/events/{safe}")
        event = raw.get("event")
        if not isinstance(event, dict):
            raise KalshiPayloadError("event response missing event object")
        return KalshiEvent.from_api(event, observed_at=observed, api_version=API_VERSION)

    async def get_orderbook(self, ticker: str, *, depth: int | None = None) -> KalshiOrderBook:
        safe = self._ticker(ticker)
        params: dict[str, str] | None = None
        if depth is not None:
            if depth <= 0:
                raise ValueError("depth must be positive")
            params = {"depth": str(depth)}
        raw, observed = await self._request_json(
            f"/markets/{safe}/orderbook", params=params
        )
        book = raw.get("orderbook_fp")
        if not isinstance(book, dict):
            raise KalshiPayloadError("orderbook response missing orderbook_fp")
        return KalshiOrderBook(
            ticker=ticker,
            yes_bids=KalshiOrderBook._levels(
                book.get("yes_dollars", []), field="yes_dollars"
            ),
            no_bids=KalshiOrderBook._levels(
                book.get("no_dollars", []), field="no_dollars"
            ),
            observed_at=observed,
            api_version=API_VERSION,
        )

    async def list_trades(
        self,
        *,
        ticker: str | None = None,
        cursor: str | None = None,
        limit: int = 100,
    ) -> KalshiTradePage:
        if not 1 <= limit <= 1000:
            raise ValueError("limit must be between 1 and 1000")
        params = {"limit": str(limit)}
        if ticker:
            params["ticker"] = self._ticker(ticker)
        if cursor:
            params["cursor"] = cursor
        raw, observed = await self._request_json("/markets/trades", params=params)
        rows = raw.get("trades")
        if not isinstance(rows, list):
            raise KalshiPayloadError("trades response missing trades array")
        trades: list[KalshiTrade] = []
        for row in rows:
            if not isinstance(row, dict):
                raise KalshiPayloadError("trade row must be an object")
            trade = KalshiTrade.from_api(row, observed_at=observed, api_version=API_VERSION)
            trades.append(trade)
            self._record_source_time(trade.created_at)
        next_cursor = raw.get("cursor")
        if next_cursor is not None and not isinstance(next_cursor, str):
            raise KalshiPayloadError("cursor must be a string when present")
        return KalshiTradePage(tuple(trades), next_cursor or None)

    def health(self, *, max_freshness_seconds: float = 60.0) -> KalshiHealth:
        checked_at = self._wall_clock().astimezone(UTC)
        reasons: list[str] = []
        state = "HEALTHY"
        if self._last_success_at is None:
            state = "UNKNOWN"
            reasons.append("NO_SUCCESSFUL_REQUEST")
        elif self._consecutive_failures:
            state = "DEGRADED"
            reasons.append("RECENT_REQUEST_FAILURE")
        if self._last_source_timestamp is not None:
            age = (checked_at - self._last_source_timestamp).total_seconds()
            if age < -1:
                state = "DEGRADED"
                reasons.append("SOURCE_TIME_IN_FUTURE")
            elif age > max_freshness_seconds:
                state = "DEGRADED"
                reasons.append("SOURCE_STALE")
        return KalshiHealth(
            state=state,
            reason_codes=tuple(reasons),
            checked_at=checked_at,
            last_success_at=self._last_success_at,
            last_source_timestamp=self._last_source_timestamp,
            consecutive_failures=self._consecutive_failures,
            api_version=API_VERSION,
            base_url=self.base_url,
        )

    async def _request_json(
        self,
        path: str,
        *,
        params: dict[str, str] | None = None,
    ) -> tuple[dict[str, Any], datetime]:
        # All callers above pass compile-time GET-only public market-data paths.
        for attempt in range(1, self.max_attempts + 1):
            try:
                async with (
                    aiohttp.ClientSession(timeout=self.timeout) as session,
                    session.get(
                        f"{self.base_url}{path}",
                        params=params or {},
                        headers={"Accept": "application/json"},
                    ) as response,
                ):
                        if response.status in {429, 500, 502, 503, 504}:
                            if attempt == self.max_attempts:
                                self._consecutive_failures += 1
                                raise KalshiReadError(
                                    f"Kalshi GET exhausted retries status={response.status}"
                                )
                            delay = self._retry_delay(
                                response.headers.get("Retry-After"), attempt
                            )
                        else:
                            response.raise_for_status()
                            decoded = json.loads(await response.text())
                            if not isinstance(decoded, dict):
                                raise KalshiPayloadError("Kalshi response must be an object")
                            observed = self._wall_clock().astimezone(UTC)
                            self._last_success_at = observed
                            self._consecutive_failures = 0
                            return decoded, observed
            except (aiohttp.ClientError, TimeoutError) as exc:
                if attempt == self.max_attempts:
                    self._consecutive_failures += 1
                    raise KalshiReadError("Kalshi public GET failed") from exc
                delay = self._retry_delay(None, attempt)
            await self._sleep(delay)
        raise RuntimeError("unreachable Kalshi retry state")

    def _record_source_time(self, source_time: datetime | None) -> None:
        if source_time is None:
            return
        if self._last_source_timestamp is None or source_time > self._last_source_timestamp:
            self._last_source_timestamp = source_time

    def _retry_delay(self, retry_after: str | None, attempt: int) -> float:
        parsed = _retry_after_seconds(retry_after, now=self._wall_clock())
        if parsed is not None:
            return max(0.05, parsed)
        backoff = min(
            self.backoff_base_seconds * (2 ** (attempt - 1)),
            self.backoff_max_seconds,
        )
        return backoff + self._random_fraction() * min(self.backoff_base_seconds, backoff)

    @staticmethod
    def _ticker(value: str) -> str:
        candidate = value.strip()
        if not candidate or _TICKER.fullmatch(candidate) is None:
            raise ValueError("ticker contains unsupported characters")
        return quote(candidate, safe="._:-")


def _retry_after_seconds(value: str | None, *, now: datetime) -> float | None:
    if value is None or not value.strip():
        return None
    candidate = value.strip()
    try:
        seconds = float(candidate)
    except ValueError:
        seconds = -1
    if seconds >= 0:
        return seconds
    try:
        parsed = parsedate_to_datetime(candidate)
    except (TypeError, ValueError, OverflowError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return max(0.0, (parsed.astimezone(UTC) - now.astimezone(UTC)).total_seconds())
