"""Read-only CLOB REST client used to seed authoritative order books."""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

import aiohttp

from predictions_cup.external.polymarket.models import JsonObject, PayloadError


class ClobMarketDataClient:
    """Public CLOB market-data calls only; this client has no auth or order methods."""

    def __init__(
        self,
        base_url: str,
        *,
        batch_size: int = 100,
        timeout_seconds: float = 30.0,
    ) -> None:
        if batch_size <= 0:
            raise ValueError("batch_size must be positive")
        self.base_url = base_url.rstrip("/")
        self.batch_size = batch_size
        self.timeout = aiohttp.ClientTimeout(total=timeout_seconds)

    async def fetch_books(self, token_ids: tuple[str, ...]) -> tuple[JsonObject, ...]:
        results: list[JsonObject] = []
        async with aiohttp.ClientSession(timeout=self.timeout) as session:
            for start in range(0, len(token_ids), self.batch_size):
                batch = token_ids[start : start + self.batch_size]
                body = [{"token_id": token_id} for token_id in batch]
                async with session.post(f"{self.base_url}/books", json=body) as response:
                    response.raise_for_status()
                    raw: Any = json.loads(await response.text(), parse_float=Decimal)
                if not isinstance(raw, list):
                    raise PayloadError("CLOB /books response must be a list")
                for item in raw:
                    if not isinstance(item, dict):
                        raise PayloadError("CLOB /books item must be an object")
                    results.append(item)
        return tuple(results)
