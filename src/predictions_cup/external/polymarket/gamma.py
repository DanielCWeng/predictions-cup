"""Read-only Gamma market discovery."""

from __future__ import annotations

import asyncio
import json
import logging
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from email.utils import parsedate_to_datetime
from typing import Any

import aiohttp

from predictions_cup.external.polymarket.models import PayloadError, PolymarketMarket

_LOG = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class GammaDiscovery:
    markets: tuple[PolymarketMarket, ...]
    parse_failures: int


class GammaRateLimitError(RuntimeError):
    """Gamma remained rate limited after the bounded retry budget."""


class GammaClient:
    """Minimal keyset-paginated Gamma client for public market metadata."""

    def __init__(
        self,
        base_url: str,
        *,
        page_limit: int = 100,
        timeout_seconds: float = 30.0,
        max_rate_limit_attempts: int = 4,
        rate_limit_backoff_base_seconds: float = 2.0,
        rate_limit_backoff_max_seconds: float = 30.0,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        random_fraction: Callable[[], float] = random.random,
    ):
        if page_limit <= 0:
            raise ValueError("page_limit must be positive")
        if max_rate_limit_attempts <= 0:
            raise ValueError("max_rate_limit_attempts must be positive")
        if rate_limit_backoff_base_seconds <= 0:
            raise ValueError("rate_limit_backoff_base_seconds must be positive")
        if rate_limit_backoff_max_seconds < rate_limit_backoff_base_seconds:
            raise ValueError(
                "rate_limit_backoff_max_seconds must be >= rate_limit_backoff_base_seconds"
            )
        self.base_url = base_url.rstrip("/")
        self.page_limit = page_limit
        self.timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        self.max_rate_limit_attempts = max_rate_limit_attempts
        self.rate_limit_backoff_base_seconds = rate_limit_backoff_base_seconds
        self.rate_limit_backoff_max_seconds = rate_limit_backoff_max_seconds
        self._sleep = sleep
        self._random_fraction = random_fraction

    async def discover_active_markets(self) -> GammaDiscovery:
        markets: list[PolymarketMarket] = []
        parse_failures = 0
        cursor: str | None = None
        seen_cursors: set[str] = set()
        seen_market_ids: set[str] = set()

        async with aiohttp.ClientSession(timeout=self.timeout) as session:
            while True:
                params: dict[str, str] = {
                    "closed": "false",
                    "limit": str(self.page_limit),
                }
                if cursor is not None:
                    params["after_cursor"] = cursor

                raw = await self._fetch_keyset_page(session, params)
                if not isinstance(raw, dict):
                    raise PayloadError("Gamma keyset response must be an object")
                page = raw.get("markets")
                if not isinstance(page, list):
                    raise PayloadError("Gamma keyset response missing markets list")

                for item in page:
                    if not isinstance(item, dict):
                        parse_failures += 1
                        _LOG.warning("Skipping non-object Gamma market payload")
                        continue
                    try:
                        market = PolymarketMarket.from_gamma(item)
                    except PayloadError as exc:
                        parse_failures += 1
                        _LOG.warning("Skipping malformed Gamma market: %s", exc)
                        continue
                    if market.market_id not in seen_market_ids:
                        seen_market_ids.add(market.market_id)
                        markets.append(market)

                next_cursor = raw.get("next_cursor")
                if not isinstance(next_cursor, str) or not next_cursor:
                    break
                if next_cursor in seen_cursors:
                    raise PayloadError("Gamma keyset cursor repeated; refusing pagination loop")
                seen_cursors.add(next_cursor)
                cursor = next_cursor

        return GammaDiscovery(tuple(markets), parse_failures)

    async def _fetch_keyset_page(
        self,
        session: aiohttp.ClientSession,
        params: dict[str, str],
    ) -> Any:
        for attempt in range(1, self.max_rate_limit_attempts + 1):
            async with session.get(
                f"{self.base_url}/markets/keyset",
                params=params,
            ) as response:
                if response.status != 429:
                    response.raise_for_status()
                    return json.loads(await response.text(), parse_float=Decimal)

                if attempt >= self.max_rate_limit_attempts:
                    _LOG.error(
                        "Gamma discovery exhausted rate-limit retries cursor=%s attempts=%s",
                        params.get("after_cursor"),
                        attempt,
                    )
                    raise GammaRateLimitError(
                        "Gamma discovery remained rate limited after "
                        f"{attempt} attempts at cursor={params.get('after_cursor')!r}"
                    )

                delay_seconds = self._rate_limit_delay_seconds(
                    response.headers.get("Retry-After"),
                    attempt,
                )
                _LOG.warning(
                    "Gamma discovery rate limited cursor=%s attempt=%s/%s "
                    "retry_in_seconds=%.3f",
                    params.get("after_cursor"),
                    attempt,
                    self.max_rate_limit_attempts,
                    delay_seconds,
                )
            await self._sleep(delay_seconds)

        raise RuntimeError("unreachable Gamma retry state")

    def _rate_limit_delay_seconds(
        self,
        retry_after: str | None,
        attempt: int,
    ) -> float:
        parsed_retry_after = _parse_retry_after_seconds(retry_after)
        if parsed_retry_after is not None:
            return parsed_retry_after

        backoff = min(
            self.rate_limit_backoff_base_seconds * (2 ** (attempt - 1)),
            self.rate_limit_backoff_max_seconds,
        )
        jitter = self._random_fraction() * min(
            self.rate_limit_backoff_base_seconds,
            backoff,
        )
        return backoff + jitter


def _parse_retry_after_seconds(value: str | None) -> float | None:
    if value is None:
        return None

    candidate = value.strip()
    if not candidate:
        return None

    try:
        seconds = float(candidate)
    except ValueError:
        seconds = -1.0
    if seconds >= 0:
        return seconds

    try:
        parsed_retry_at = parsedate_to_datetime(candidate)
    except (TypeError, ValueError, OverflowError):
        return None
    if not isinstance(parsed_retry_at, datetime):
        return None
    retry_at = parsed_retry_at
    if retry_at.tzinfo is None:
        retry_at = retry_at.replace(tzinfo=UTC)
    return max(0.0, (retry_at - datetime.now(UTC)).total_seconds())
