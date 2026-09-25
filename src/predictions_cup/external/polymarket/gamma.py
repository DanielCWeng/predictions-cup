"""Read-only Gamma market discovery."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import aiohttp

from predictions_cup.external.polymarket.models import PayloadError, PolymarketMarket

_LOG = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class GammaDiscovery:
    markets: tuple[PolymarketMarket, ...]
    parse_failures: int


class GammaClient:
    """Minimal keyset-paginated Gamma client for public market metadata."""

    def __init__(self, base_url: str, *, page_limit: int = 100, timeout_seconds: float = 30.0):
        if page_limit <= 0:
            raise ValueError("page_limit must be positive")
        self.base_url = base_url.rstrip("/")
        self.page_limit = page_limit
        self.timeout = aiohttp.ClientTimeout(total=timeout_seconds)

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
                async with session.get(
                    f"{self.base_url}/markets/keyset", params=params
                ) as response:
                    response.raise_for_status()
                    raw: Any = json.loads(await response.text(), parse_float=Decimal)
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
