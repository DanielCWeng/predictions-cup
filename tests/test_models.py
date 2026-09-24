from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import pytest
from pydantic import ValidationError

from predictions_cup.models import (
    DecisionRecord,
    Exchange,
    Fill,
    Market,
    Order,
    OrderBook,
    OrderBookLevel,
    OrderIntent,
    Position,
    Price,
    PriceKind,
    Side,
    TournamentContext,
    Trade,
)

NOW = datetime(2026, 9, 24, 20, 0, tzinfo=timezone.utc)


def test_price_boundaries_and_decimal_precision_are_preserved() -> None:
    lower = Price(
        exchange_id="exchange:0001",
        value=Decimal("0"),
        timestamp=NOW,
        source="sig-rest",
        kind=PriceKind.BEST_BID,
    )
    upper = Price(
        exchange_id="exchange:0001",
        value=Decimal("1"),
        timestamp=NOW,
        source="sig-rest",
        kind=PriceKind.BEST_ASK,
    )
    precise = Price(
        exchange_id="exchange:0001",
        value=Decimal("0.1234567890123456789012345678"),
        timestamp=NOW,
        source="sig-rest",
        kind=PriceKind.LAST_TRADE,
    )

    assert lower.value == Decimal("0")
    assert upper.value == Decimal("1")
    assert precise.value == Decimal("0.1234567890123456789012345678")
    assert "0.1234567890123456789012345678" in precise.model_dump_json()


@pytest.mark.parametrize("value", [Decimal("-0.0001"), Decimal("1.0001")])
def test_prices_outside_probability_range_are_rejected(value: Decimal) -> None:
    with pytest.raises(ValidationError):
        Price(
            exchange_id="exchange",
            value=value,
            timestamp=NOW,
            source="sig-rest",
            kind=PriceKind.MIDPOINT,
        )


def test_binary_float_input_is_rejected() -> None:
    payload: dict[str, Any] = {
        "exchange_id": "exchange",
        "value": 0.5,
        "timestamp": NOW,
        "source": "sig-rest",
        "kind": "midpoint",
    }

    with pytest.raises(ValidationError):
        Price.model_validate(payload)


@pytest.mark.parametrize("quantity", [Decimal("0"), Decimal("-1")])
def test_non_positive_orderbook_quantities_are_rejected(quantity: Decimal) -> None:
    with pytest.raises(ValidationError):
        OrderBookLevel(price=Decimal("0.5"), quantity=quantity)


def test_naive_timestamp_is_rejected_and_aware_timestamp_normalises_to_utc() -> None:
    naive = datetime(2026, 9, 24, 20, 0)
    with pytest.raises(ValidationError):
        Trade(
            trade_id="trade",
            exchange_id="exchange",
            price=Decimal("0.4"),
            quantity=Decimal("2"),
            timestamp=naive,
        )

    plus_two = timezone(timedelta(hours=2))
    trade = Trade(
        trade_id="trade",
        exchange_id="exchange",
        price=Decimal("0.4"),
        quantity=Decimal("2"),
        timestamp=datetime(2026, 9, 24, 22, 0, tzinfo=plus_two),
    )
    assert trade.timestamp == NOW
    assert trade.timestamp.tzinfo == timezone.utc


def test_market_and_exchange_are_distinct_and_context_is_explicit() -> None:
    exchange = Exchange(
        exchange_id="0007",
        market_id="market-alpha",
        outcome_label="Yes",
    )
    market = Market(
        market_id="market-alpha",
        title="Will the event occur?",
        exchanges=(exchange,),
        tournament=TournamentContext(
            tournament_id="001",
            slug="cup-2026",
            name="Predictions Cup",
        ),
    )

    assert isinstance(market, Market)
    assert isinstance(exchange, Exchange)
    assert market.exchanges[0] == exchange
    assert market.market_id != exchange.exchange_id
    assert exchange.exchange_id == "0007"
    assert market.tournament is not None
    assert market.tournament.tournament_id == "001"


def test_market_rejects_exchange_from_another_market() -> None:
    exchange = Exchange(
        exchange_id="exchange",
        market_id="other-market",
        outcome_label="No",
    )
    with pytest.raises(ValidationError):
        Market(
            market_id="market",
            title="Question",
            exchanges=(exchange,),
        )


def test_order_intent_and_order_are_distinct_contracts() -> None:
    intent = OrderIntent(
        intent_id="intent:001",
        exchange_id="exchange:001",
        side=Side.BUY,
        quantity=Decimal("3"),
        order_kind="limit",
        limit_price=Decimal("0.44"),
        created_at=NOW,
        strategy_id="strategy:test",
    )
    order = Order(
        order_id=None,
        intent_id=intent.intent_id,
        client_order_id="client:001",
        exchange_id=intent.exchange_id,
        side=intent.side,
        quantity=intent.quantity,
        order_kind=intent.order_kind,
        limit_price=intent.limit_price,
        status="created",
        created_at=NOW,
    )

    assert isinstance(intent, OrderIntent)
    assert isinstance(order, Order)
    assert type(intent) is not type(order)
    assert order.intent_id == intent.intent_id


def test_orderbook_represents_multiple_explicit_levels() -> None:
    book = OrderBook(
        exchange_id="exchange",
        bids=(
            OrderBookLevel(price=Decimal("0.40"), quantity=Decimal("10")),
            OrderBookLevel(price=Decimal("0.39"), quantity=Decimal("20")),
        ),
        asks=(
            OrderBookLevel(price=Decimal("0.41"), quantity=Decimal("5")),
            OrderBookLevel(price=Decimal("0.42"), quantity=Decimal("7")),
        ),
        timestamp=NOW,
        source="sig-rest",
        revision="rev:0001",
    )

    assert [level.price for level in book.bids] == [Decimal("0.40"), Decimal("0.39")]
    assert [level.price for level in book.asks] == [Decimal("0.41"), Decimal("0.42")]


def test_fill_retains_financially_material_fields() -> None:
    fill = Fill(
        fill_id=None,
        order_id="order:0001",
        exchange_id="exchange:0001",
        side=Side.SELL,
        price=Decimal("0.625"),
        quantity=Decimal("4.25"),
        timestamp=NOW,
    )

    assert fill.order_id == "order:0001"
    assert fill.exchange_id == "exchange:0001"
    assert fill.side is Side.SELL
    assert fill.price == Decimal("0.625")
    assert fill.quantity == Decimal("4.25")
    assert fill.timestamp == NOW


def test_position_allows_signed_decimal_exposure_without_portfolio_logic() -> None:
    position = Position(
        exchange_id="exchange:opaque:0001",
        quantity=Decimal("-2.500"),
        average_entry_price=Decimal("0.51"),
        as_of=NOW,
    )

    assert position.exchange_id == "exchange:opaque:0001"
    assert position.quantity == Decimal("-2.500")


def test_decision_record_is_immutable_audit_primitive() -> None:
    record = DecisionRecord(
        decision_id="decision:0001",
        timestamp=NOW,
        component="strategy:test",
        exchange_id="exchange:001",
        market_id="market:001",
        action="propose_order",
        reason_code="signal_threshold",
    )

    with pytest.raises(ValidationError):
        record.action = "mutated"
