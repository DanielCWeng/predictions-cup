"""Read-only CLOB REST client used to seed authoritative order books."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

import aiohttp

from predictions_cup.external.polymarket.models import JsonObject, PayloadError, utc_now


@dataclass(frozen=True, slots=True)
class ObservedBookBatch:
    books: tuple[JsonObject, ...]
    observed_at: datetime


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

    async def fetch_book_batches(
        self, token_ids: tuple[str, ...]
    ) -> tuple[ObservedBookBatch, ...]:
        batches: list[ObservedBookBatch] = []
        async with aiohttp.ClientSession(timeout=self.timeout) as session:
            for start in range(0, len(token_ids), self.batch_size):
                batch = token_ids[start : start + self.batch_size]
                body = [{"token_id": token_id} for token_id in batch]
                async with session.post(f"{self.base_url}/books", json=body) as response:
                    response.raise_for_status()
                    response_text = await response.text()
                    observed_at = utc_now()
                raw: Any = json.loads(response_text, parse_float=Decimal)
                if not isinstance(raw, list):
                    raise PayloadError("CLOB /books response must be a list")
                books: list[JsonObject] = []
                for item in raw:
                    if not isinstance(item, dict):
                        raise PayloadError("CLOB /books item must be an object")
                    books.append(item)
                batches.append(ObservedBookBatch(tuple(books), observed_at))
        return tuple(batches)

    async def fetch_books(self, token_ids: tuple[str, ...]) -> tuple[JsonObject, ...]:
        batches = await self.fetch_book_batches(token_ids)
        return tuple(book for batch in batches for book in batch.books)
