from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from predictions_cup.external.polymarket.gamma import GammaClient


class FakeResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload

    async def __aenter__(self) -> FakeResponse:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: object,
    ) -> None:
        del exc_type, exc, tb

    def raise_for_status(self) -> None:
        return None

    async def text(self) -> str:
        return json.dumps(self.payload)


class FakeSession:
    pages: list[dict[str, object]] = []
    params_seen: list[dict[str, str]] = []

    def __init__(self, *args: object, **kwargs: object) -> None:
        del args, kwargs

    async def __aenter__(self) -> FakeSession:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: object,
    ) -> None:
        del exc_type, exc, tb

    def get(self, url: str, *, params: dict[str, str]) -> FakeResponse:
        del url
        self.params_seen.append(dict(params))
        return FakeResponse(self.pages.pop(0))


def payload(market_id: str) -> dict[str, Any]:
    return {
        "id": market_id,
        "conditionId": f"condition-{market_id}",
        "question": f"2026 U.S. Senate market {market_id}",
        "outcomes": '["Yes", "No"]',
        "clobTokenIds": f'["{market_id}-yes", "{market_id}-no"]',
        "active": True,
        "closed": False,
    }


def test_gamma_keyset_pagination_uses_next_cursor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    FakeSession.pages = [
        {"markets": [payload("1")], "next_cursor": "cursor-2"},
        {"markets": [payload("2")], "next_cursor": ""},
    ]
    FakeSession.params_seen = []
    monkeypatch.setattr(
        "predictions_cup.external.polymarket.gamma.aiohttp.ClientSession", FakeSession
    )

    result = asyncio.run(
        GammaClient("https://gamma.example", page_limit=20).discover_active_markets()
    )

    assert [market.market_id for market in result.markets] == ["1", "2"]
    assert "after_cursor" not in FakeSession.params_seen[0]
    assert FakeSession.params_seen[1]["after_cursor"] == "cursor-2"
