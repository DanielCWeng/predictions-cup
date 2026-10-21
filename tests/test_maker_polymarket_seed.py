"""Maker/recorder compatibility: a REST seed is mandatory before WS subscribe."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import cast

import pytest

from predictions_cup.external.polymarket.client import (
    ClobMarketDataClient,
    ObservedBookBatch,
)
from predictions_cup.external.polymarket.health import IngestionHealth
from predictions_cup.external.polymarket.orderbook import OrderBookStore
from predictions_cup.external.polymarket.websocket import MarketWebSocket
from predictions_cup.maker.service import MakerService

_OBSERVED = datetime(2026, 10, 21, tzinfo=UTC)


class FakeClob:
    def __init__(self, seeded: tuple[str, ...]) -> None:
        self.seeded = seeded

    async def fetch_book_batches(
        self, token_ids: tuple[str, ...]
    ) -> tuple[ObservedBookBatch, ...]:
        return (
            ObservedBookBatch(
                books=tuple(
                    {
                        "asset_id": token,
                        "market": "market",
                        "bids": [{"price": "0.45", "size": "12"}],
                        "asks": [{"price": "0.46", "size": "13"}],
                    }
                    for token in self.seeded
                ),
                observed_at=_OBSERVED,
            ),
        )


def _maker(
    requested: tuple[str, ...], seeded: tuple[str, ...]
) -> MakerService:
    maker = MakerService.__new__(MakerService)
    maker._pm_token_ids = requested
    maker.pm_health = IngestionHealth()
    maker.pm_books = OrderBookStore()
    maker.pm_ws = MarketWebSocket("wss://example.invalid", maker.pm_health)
    maker.pm_clob = cast(ClobMarketDataClient, FakeClob(seeded))
    return maker


def test_maker_certifies_complete_exact_seed_on_start_and_reconnect() -> None:
    maker = _maker(("A", "B"), ("A", "B"))
    asyncio.run(maker._seed_polymarket_books())
    asyncio.run(maker.pm_ws.set_tokens(("A", "B")))
    maker.pm_ws._require_current_seed(frozenset({"A", "B"}))
    assert maker.pm_health.seed_status == "COMPLETE"
    assert maker.pm_health.seed_attempt_epoch == 1
    assert maker.pm_health.seed_complete_epoch == 1
    assert maker.pm_health.seed_expected_tokens == 2
    assert maker.pm_health.seed_initialized_tokens == 2
    assert maker.pm_health.seed_completed_at is not None
    assert maker.pm_ws._seeded_tokens == frozenset({"A", "B"})

    # Reconnect repeats the authoritative seed, advancing the attempt epoch.
    asyncio.run(maker._seed_polymarket_books())
    maker.pm_ws._require_current_seed(frozenset({"A", "B"}))
    assert maker.pm_health.seed_attempt_epoch == 2
    assert maker.pm_health.seed_complete_epoch == 2


def test_maker_partial_seed_never_authorizes_subscription() -> None:
    maker = _maker(("A", "B"), ("A",))
    asyncio.run(maker.pm_ws.set_tokens(("A", "B")))
    with pytest.raises(RuntimeError, match="missing 1 mapped tokens"):
        asyncio.run(maker._seed_polymarket_books())
    assert maker.pm_health.seed_status == "FAILED"
    assert maker.pm_health.seed_complete_epoch == 0
    assert maker.pm_health.seed_initialized_tokens == 1
    assert maker.pm_ws._seeded_tokens == frozenset()
    with pytest.raises(RuntimeError, match="changed after REST seed"):
        maker.pm_ws._require_current_seed(frozenset({"A", "B"}))


def test_maker_rejects_change_of_websocket_token_identity() -> None:
    maker = _maker(("A", "B"), ("A", "B"))
    asyncio.run(maker.pm_ws.set_tokens(("X", "Y")))
    with pytest.raises(RuntimeError, match="desired universe changed"):
        asyncio.run(maker._seed_polymarket_books())
    assert maker.pm_health.seed_status == "FAILED"
    assert maker.pm_ws._seeded_tokens == frozenset()
