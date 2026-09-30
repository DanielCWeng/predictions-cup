from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest

from predictions_cup.external.kalshi import KalshiPublicClient, KalshiReadError


NOW = datetime(2026, 9, 30, 22, 0, tzinfo=UTC)


def market_payload() -> dict[str, Any]:
    return {
        "ticker": "KXTEST-YES",
        "event_ticker": "KXTEST",
        "status": "open",
        "title": "Test market",
        "updated_time": "2026-09-30T21:59:59Z",
        "yes_bid_dollars": "0.5600",
        "yes_bid_size_fp": "10.00",
        "yes_ask_dollars": "0.5700",
        "yes_ask_size_fp": "8.00",
        "no_bid_dollars": "0.4300",
        "no_ask_dollars": "0.4400",
        "last_price_dollars": "0.5650",
        "volume_fp": "123.50",
    }


@pytest.mark.asyncio
async def test_market_parses_fixed_point_decimals_and_provenance() -> None:
    client = KalshiPublicClient(wall_clock=lambda: NOW)

    async def fake(path: str, *, params: dict[str, str] | None = None):
        assert path == "/markets/KXTEST-YES"
        assert params is None
        return {"market": market_payload()}, NOW

    client._request_json = fake  # type: ignore[method-assign]
    market = await client.get_market("KXTEST-YES")
    assert market.yes_bid == Decimal("0.5600")
    assert market.yes_bid_size == Decimal("10.00")
    assert market.observed_at == NOW
    assert market.source_updated_at != market.observed_at
    assert market.api_version == "kalshi-trade-api-v2"


@pytest.mark.asyncio
async def test_orderbook_is_bid_only_and_exact_decimal() -> None:
    client = KalshiPublicClient(wall_clock=lambda: NOW)

    async def fake(path: str, *, params: dict[str, str] | None = None):
        assert path == "/markets/KXTEST-YES/orderbook"
        assert params == {"depth": "2"}
        return {
            "orderbook_fp": {
                "yes_dollars": [["0.5500", "10.00"]],
                "no_dollars": [["0.4400", "4.25"]],
            }
        }, NOW

    client._request_json = fake  # type: ignore[method-assign]
    book = await client.get_orderbook("KXTEST-YES", depth=2)
    assert book.yes_bids[0].price == Decimal("0.5500")
    assert book.no_bids[0].quantity == Decimal("4.25")


@pytest.mark.asyncio
async def test_markets_and_trades_preserve_cursor() -> None:
    client = KalshiPublicClient(wall_clock=lambda: NOW)

    async def fake(path: str, *, params: dict[str, str] | None = None):
        if path == "/markets":
            return {"markets": [market_payload()], "cursor": "next"}, NOW
        assert path == "/markets/trades"
        return {
            "trades": [{
                "trade_id": "t1",
                "ticker": "KXTEST-YES",
                "count_fp": "2.50",
                "yes_price_dollars": "0.5600",
                "no_price_dollars": "0.4400",
                "created_time": "2026-09-30T21:59:58Z",
            }],
            "cursor": "trade-next",
        }, NOW

    client._request_json = fake  # type: ignore[method-assign]
    markets = await client.list_markets(cursor="c", status="open")
    trades = await client.list_trades(ticker="KXTEST-YES")
    assert markets.cursor == "next"
    assert trades.cursor == "trade-next"
    assert trades.items[0].quantity == Decimal("2.50")


@pytest.mark.asyncio
async def test_ticker_cannot_escape_public_routes() -> None:
    client = KalshiPublicClient()
    with pytest.raises(ValueError):
        await client.get_market("../portfolio/orders")


def test_health_is_unknown_before_real_read_and_never_fake_healthy() -> None:
    client = KalshiPublicClient(wall_clock=lambda: NOW)
    health = client.health()
    assert health.state == "UNKNOWN"
    assert "NO_SUCCESSFUL_REQUEST" in health.reason_codes


def test_client_has_no_order_write_surface() -> None:
    prohibited = {
        "create_order",
        "cancel_order",
        "amend_order",
        "withdraw",
        "transfer",
        "post",
        "put",
        "delete",
    }
    public = set(dir(KalshiPublicClient))
    assert not prohibited & public
