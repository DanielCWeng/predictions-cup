"""No network: mock the SIG read-only account and orderbook surfaces."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
from scripts.ops.sig_election_snapshot import (
    capture_election_sig_snapshot,
    select_watchlist,
)

from predictions_cup.sig.dto import AccountDto, OrderBookSnapshotDto
from predictions_cup.sig.trading_dto import OrderReadDto, PositionsResponseDto

MAPPING = json.loads(Path("data/mappings/sig_polymarket_2026.json").read_text())


def test_default_watchlist_restricts_to_verified_exact_same_senate_maps() -> None:
    chosen, excluded = select_watchlist(MAPPING)
    assert {row["exchange_id"] for row in chosen} == {"959", "960", "1045"}
    assert {"949", "950", "982", "983", "1046"} <= {
        row["exchange_id"] for row in excluded
    }
    selected, excluded = select_watchlist(MAPPING, races=("Alaska", "Maine"))
    assert {x["exchange_id"] for x in selected} == {"959", "960"}
    assert {x["exchange_id"] for x in excluded} == {"1066", "1067"}
    assert all(x["mapping_class"] == "NO_TRADE" for x in excluded)


def test_derived_is_opt_in_and_no_trade_always_stays_excluded() -> None:
    selected, excluded = select_watchlist(
        MAPPING, races=("Alaska", "Iowa", "Texas"), include_derived=True,
    )
    assert {x["exchange_id"] for x in selected} == {"949", "950", "982", "983"}
    assert {x["exchange_id"] for x in excluded} == {"1066", "1067"}


class _Reader:
    def __init__(self) -> None:
        self.read_books: list[str] = []

    async def get_account(self) -> AccountDto:
        return cast(AccountDto, SimpleNamespace(balance=Decimal("102.50")))

    async def get_tournament_positions(self, tournament_slug: str) -> PositionsResponseDto:
        assert tournament_slug == "the-cup"
        return cast(
            PositionsResponseDto,
            SimpleNamespace(positions=(
                SimpleNamespace(
                    exchange_id="959", market_id="270",
                    quantity=Decimal("-12"), settled=False,
                ),
                SimpleNamespace(
                    exchange_id="960", market_id="271",
                    quantity=Decimal("8"), settled=False,
                ),
            )),
        )

    async def iter_orders(
        self, *,
        status: str = "open", exchange_id: str | None = None,
        market_id: str | None = None, tournament_id: str | None = None,
        limit: int = 200,
    ) -> AsyncIterator[OrderReadDto]:
        assert status == "open" and tournament_id == "cup-test"
        if False:
            yield cast(OrderReadDto, None)

    async def get_orderbook(
        self, exchange_id: str, *,
        depth: int = 20, tournament_id: str | None = None,
    ) -> OrderBookSnapshotDto:
        assert depth == 20 and tournament_id == "cup-test"
        self.read_books.append(exchange_id)
        market_id = {"959": "270", "960": "271"}[exchange_id]
        return OrderBookSnapshotDto.model_validate({
            "exchangeId": exchange_id, "marketId": market_id, "depth": depth,
            "bids": [{"price": "0.55", "quantity": "8"}],
            "asks": [{"price": "0.60", "quantity": "20"}],
            "bestBid": "0.55", "bestAsk": "0.60", "spread": "0.05",
        })


def test_readonly_capture_preserves_signed_position_and_raw_cash() -> None:
    reader = _Reader()
    pauses: list[float] = []

    async def no_wait(seconds: float) -> None:
        pauses.append(seconds)

    # The true mapping ID must match. Use an isolated copy to keep the
    # synthetic tournament fixture independent of live competition IDs.
    mapping = dict(MAPPING)
    mapping["tournament_id"] = "cup-test"
    result = asyncio.run(capture_election_sig_snapshot(
        reader, mapping=mapping, races=("Maine",),
        tournament_id="cup-test", tournament_slug="the-cup",
        pause=no_wait,
    ))
    assert result["mode"] == "READ_ONLY_NON_ATOMIC_SIG_SURVEY"
    assert result["execution_authority"] is False
    assert result["cash_balance_raw"] == "102.50"
    assert result["cash_is_verified_spendable"] is False
    assert reader.read_books == ["959", "960"]
    assert pauses == [0.75, 0.75]
    books = result["selected_books"]
    assert isinstance(books, list)
    assert books[0]["held_no_shares"] == "12"
    assert books[0]["held_yes_shares"] == "0"
    assert books[1]["held_yes_shares"] == "8"
    assert books[0]["asks"] == [{"price": "0.60", "quantity": "20"}]
    assert "non-atomic" in str(result["warning"]).lower()


def test_snapshot_refuses_wrong_tournament_before_any_account_call() -> None:
    reader = _Reader()
    with pytest.raises(ValueError, match="does not match"):
        asyncio.run(capture_election_sig_snapshot(
            reader, mapping=MAPPING, races=("Maine",),
            tournament_id="wrong", tournament_slug="the-cup",
        ))
    assert reader.read_books == []


def test_bad_mapping_exhaustion_fails_closed() -> None:
    with pytest.raises(ValueError, match="no eligible"):
        select_watchlist(MAPPING, races=("Alaska",), include_derived=True)
    with pytest.raises(ValueError, match="no eligible"):
        select_watchlist(MAPPING, races=("Ohio",))
