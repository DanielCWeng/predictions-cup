"""Regression checks for issuer open-order fields and non-cancelling estimates."""

from __future__ import annotations

from decimal import Decimal

import pytest

from predictions_cup.analysis.election_order_capacity import summarize_open_orders
from predictions_cup.sig.trading_dto import OrderReadDto


def order(
    *,
    oid: int,
    quantity: str,
    filled: str | None = "0",
    price: str | None = "0.5",
    action: str = "buy",
    side: str = "yes",
    open_order: bool = True,
) -> OrderReadDto:
    doc = {
        "id": oid, "exchangeId": "983", "side": side, "action": action,
        "quantity": quantity, "priceLimit": price, "open": open_order,
        "createdAt": "2026-10-28T22:00:00Z",
        "expirationDate": None,
    }
    if filled is not None:
        doc["quantityFilled"] = filled
    return OrderReadDto.model_validate(doc)


def test_partially_filled_buy_only_uses_remaining_shares() -> None:
    rows = summarize_open_orders((
        order(oid=1, quantity="300", filled="100", price="0.5"),
        order(oid=2, quantity="100", filled="0", price="0.4"),
    ))
    assert len(rows) == 1
    assert rows[0].unfilled_shares == "300"
    assert Decimal(rows[0].indicative_buy_notional) == Decimal("140")
    assert rows[0].open_orders == 2


def test_missing_fill_count_is_not_assumed_zero() -> None:
    rows = summarize_open_orders((order(oid=1, quantity="300", filled=None),))
    assert rows[0].missing_fill_counts == 1
    assert rows[0].unfilled_shares == "0"
    assert rows[0].indicative_buy_notional == "0"


def test_missing_price_marked_incomplete_and_sells_not_fake_cash() -> None:
    rows = summarize_open_orders((
        order(oid=1, quantity="50", price=None),
        order(oid=2, quantity="80", action="sell", filled="0"),
        order(oid=3, quantity="100", open_order=False),
    ))
    assert sum(x.open_orders for x in rows) == 2
    assert sum(Decimal(x.indicative_buy_notional) for x in rows) == 0
    assert sum(x.missing_price_limits for x in rows) == 1


def test_bad_fill_exceeding_quantity_refused() -> None:
    with pytest.raises(ValueError, match="filled quantity"):
        summarize_open_orders((order(oid=1, quantity="100", filled="101"),))
