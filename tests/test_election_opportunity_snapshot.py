"""Pure tests for GET-only election snapshot helpers; no external API access."""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from typing import cast

import pytest

from predictions_cup.analysis.election_opportunity_snapshot import (
    _cash_ask_from_book,
    _full_holdings,
)
from predictions_cup.sig.dto import OrderBookSnapshotDto
from predictions_cup.sig.trading_dto import PositionReadDto


def _book(prices: tuple[tuple[str, str], ...]) -> OrderBookSnapshotDto:
    return cast(OrderBookSnapshotDto, SimpleNamespace(
        asks=tuple(
            SimpleNamespace(price=Decimal(price), quantity=Decimal(shares))
            for price, shares in prices
        ),
    ))


def _position(
    market: str, exchange: str, amount: str, *, settled: bool = False,
) -> PositionReadDto:
    return cast(PositionReadDto, SimpleNamespace(
        market_id=market, exchange_id=exchange,
        quantity=Decimal(amount), settled=settled,
    ))


def test_snapshot_buy_yes_vwap_uses_real_depth_and_never_extrapolates() -> None:
    book = _book(((".40", "100"), (".45", "200")))
    filled, price = _cash_ask_from_book(book, 250)
    assert filled == 250
    assert price == Decimal(".43")
    filled, price = _cash_ask_from_book(book, 500)
    assert filled == 300
    assert price == Decimal("130") / 300
    assert _cash_ask_from_book(_book(()), 100) == (0, Decimal(0))


def test_snapshot_holdings_keep_both_outcome_sides_and_skip_settled() -> None:
    holdings, ids = _full_holdings((
        _position("m1", "11", "80"), _position("m1", "11", "-40"),
        _position("m2", "22", "-200"),
        _position("settled", "33", "500", settled=True),
    ))
    assert holdings == [
        {"market_id": "m1", "yes_shares": 80, "no_shares": 40},
        {"market_id": "m2", "yes_shares": 0, "no_shares": 200},
    ]
    assert ids == frozenset({"m1", "m2"})


def test_snapshot_rejects_fractional_lots_and_out_of_range_prices() -> None:
    with pytest.raises(ValueError, match="fractional"):
        _full_holdings((_position("m1", "11", "3.5"),))
    with pytest.raises(ValueError, match="invalid executable"):
        _cash_ask_from_book(_book((("1.01", "20"),)), 10)
    with pytest.raises(ValueError, match="bounded"):
        _cash_ask_from_book(_book(((".45", "10"),)), 10001)
