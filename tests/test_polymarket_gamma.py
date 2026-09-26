from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from predictions_cup.external.polymarket.gamma import GammaClient, GammaRateLimitError


class FakeResponse:
    def __init__(
        self,
        payload: dict[str, object] | None = None,
        *,
        status: int = 200,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.payload = payload or {}
        self.status = status
        self.headers = headers or {}

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
        if self.status >= 400:
            raise RuntimeError(f"HTTP {self.status}")

    async def text(self) -> str:
        return json.dumps(self.payload)


class FakeSession:
    responses: list[FakeResponse] = []
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
        return self.responses.pop(0)


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
    FakeSession.responses = [
        FakeResponse({"markets": [payload("1")], "next_cursor": "cursor-2"}),
        FakeResponse({"markets": [payload("2")], "next_cursor": ""}),
    ]
    FakeSession.params_seen = []
    monkeypatch.setattr(
        "predictions_cup.external.polymarket.gamma.aiohttp.ClientSession",
        FakeSession,
    )

    result = asyncio.run(
        GammaClient("https://gamma.example", page_limit=20).discover_active_markets()
    )

    assert [market.market_id for market in result.markets] == ["1", "2"]
    assert "after_cursor" not in FakeSession.params_seen[0]
    assert FakeSession.params_seen[1]["after_cursor"] == "cursor-2"


def test_gamma_mid_pagination_429_retries_same_cursor_and_honors_retry_after(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    FakeSession.responses = [
        FakeResponse({"markets": [payload("1")], "next_cursor": "cursor-2"}),
        FakeResponse(status=429, headers={"Retry-After": "7"}),
        FakeResponse({"markets": [payload("2")], "next_cursor": ""}),
    ]
    FakeSession.params_seen = []
    delays: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        delays.append(seconds)

    monkeypatch.setattr(
        "predictions_cup.external.polymarket.gamma.aiohttp.ClientSession",
        FakeSession,
    )

    result = asyncio.run(
        GammaClient(
            "https://gamma.example",
            page_limit=20,
            sleep=fake_sleep,
            random_fraction=lambda: 0.99,
        ).discover_active_markets()
    )

    assert [market.market_id for market in result.markets] == ["1", "2"]
    assert delays == [7.0]
    assert len(FakeSession.params_seen) == 3
    assert "after_cursor" not in FakeSession.params_seen[0]
    assert FakeSession.params_seen[1]["after_cursor"] == "cursor-2"
    assert FakeSession.params_seen[2]["after_cursor"] == "cursor-2"


def test_gamma_zero_retry_after_uses_positive_floor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    FakeSession.responses = [
        FakeResponse(status=429, headers={"Retry-After": "0"}),
        FakeResponse({"markets": [payload("1")], "next_cursor": ""}),
    ]
    FakeSession.params_seen = []
    delays: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        delays.append(seconds)

    monkeypatch.setattr(
        "predictions_cup.external.polymarket.gamma.aiohttp.ClientSession",
        FakeSession,
    )

    result = asyncio.run(
        GammaClient(
            "https://gamma.example",
            sleep=fake_sleep,
            random_fraction=lambda: 0.0,
            rate_limit_min_delay_seconds=1.0,
        ).discover_active_markets()
    )

    assert [market.market_id for market in result.markets] == ["1"]
    assert delays == [1.0]


def test_gamma_persistent_429_exhausts_bounded_retry_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    FakeSession.responses = [
        FakeResponse(status=429),
        FakeResponse(status=429),
        FakeResponse(status=429),
    ]
    FakeSession.params_seen = []
    delays: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        delays.append(seconds)

    monkeypatch.setattr(
        "predictions_cup.external.polymarket.gamma.aiohttp.ClientSession",
        FakeSession,
    )
    client = GammaClient(
        "https://gamma.example",
        max_rate_limit_attempts=3,
        rate_limit_backoff_base_seconds=2.0,
        rate_limit_backoff_max_seconds=8.0,
        sleep=fake_sleep,
        random_fraction=lambda: 0.5,
    )

    with pytest.raises(GammaRateLimitError, match="3 attempts"):
        asyncio.run(client.discover_active_markets())

    assert delays == [3.0, 5.0]
    assert FakeSession.params_seen == [
        {"closed": "false", "limit": "100"},
        {"closed": "false", "limit": "100"},
        {"closed": "false", "limit": "100"},
    ]
