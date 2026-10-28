"""Reduce-only executable depth maths for both SIG position orientations."""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from typing import cast

import pytest

from predictions_cup.analysis.election_capital import estimate_reducing_exit
from predictions_cup.sig.dto import OrderBookSnapshotDto
from predictions_cup.sig.trading_dto import PositionReadDto


def _position(quantity: str, mark: str = "55") -> PositionReadDto:
    return cast(PositionReadDto, SimpleNamespace(
        exchange_id="959", market_id="270",
        market_title="Maine Democratic Senate", settled=False,
        quantity=Decimal(quantity), market_value=Decimal(mark),
    ))


def _book() -> OrderBookSnapshotDto:
    return OrderBookSnapshotDto.model_validate({
        "exchangeId": "959", "marketId": "270", "depth": 2,
        "bids": [
            {"price": "0.50", "quantity": "20"},
            {"price": "0.49", "quantity": "80"},
        ],
        "asks": [
            {"price": "0.55", "quantity": "20"},
            {"price": "0.56", "quantity": "80"},
        ],
        "bestBid": "0.50", "bestAsk": "0.55", "spread": "0.05",
    })


def test_yes_sale_honours_depth_and_fee_reserve() -> None:
    result = estimate_reducing_exit(_position("100"), _book(), size_limit=100)
    assert result.held_side == "YES"
    assert result.executable_shares == 100
    assert result.gross_proceeds == "49.20"
    assert result.fee_reserve == "2.00"
    assert result.net_proceeds == "47.20"
    assert result.net_minus_mark == "-7.80"
    assert result.last_fill_price == "0.49"
    assert result.full_evaluated_size_fillable and not result.authority


def test_no_sale_uses_complement_of_yes_ask_not_bid() -> None:
    result = estimate_reducing_exit(_position("-100", "44"), _book(), size_limit=100)
    assert result.held_side == "NO"
    assert result.gross_proceeds == "44.20"
    assert result.net_proceeds == "42.20"
    assert result.last_fill_price == "0.44"
    assert result.net_minus_mark == "-1.80"


def test_visible_depth_caps_sale_without_claiming_full_liquidity() -> None:
    result = estimate_reducing_exit(_position("250"), _book(), size_limit=200)
    assert result.evaluated_shares == 200
    assert result.executable_shares == 100
    assert not result.full_evaluated_size_fillable


def test_fail_closed_identity_settled_and_invalid_size() -> None:
    book = _book()
    with pytest.raises(ValueError, match="risk"):
        estimate_reducing_exit(_position("10"), book, size_limit=0)
    with pytest.raises(ValueError, match="exchange identity"):
        estimate_reducing_exit(
            cast(PositionReadDto, SimpleNamespace(
                exchange_id="950", market_id="261", settled=False,
                quantity=Decimal("10"),
            )), book,
        )
    with pytest.raises(ValueError, match="fractional"):
        estimate_reducing_exit(_position("0.5"), book)
