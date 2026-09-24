"""Canonical proposed-order, order, fill, and position representations."""

from __future__ import annotations

from pydantic import model_validator

from predictions_cup.models.common import (
    AwareDateTime,
    CanonicalModel,
    Identifier,
    NonBlankStr,
    NonNegativeDecimal,
    OrderKind,
    PositiveQuantity,
    Probability,
    Side,
)


def _validate_order_price(kind: OrderKind, limit_price: Probability | None) -> None:
    if kind is OrderKind.LIMIT and limit_price is None:
        raise ValueError("limit orders require limit_price")
    if kind is OrderKind.MARKET and limit_price is not None:
        raise ValueError("market orders must not include limit_price")


class OrderIntent(CanonicalModel):
    intent_id: Identifier
    exchange_id: Identifier
    side: Side
    quantity: PositiveQuantity
    order_kind: OrderKind
    limit_price: Probability | None = None
    created_at: AwareDateTime
    strategy_id: Identifier

    @model_validator(mode="after")
    def validate_order_semantics(self) -> OrderIntent:
        _validate_order_price(self.order_kind, self.limit_price)
        return self


class Order(CanonicalModel):
    order_id: Identifier | None = None
    intent_id: Identifier
    client_order_id: Identifier | None = None
    exchange_id: Identifier
    side: Side
    quantity: PositiveQuantity
    order_kind: OrderKind
    limit_price: Probability | None = None
    status: NonBlankStr | None = None
    created_at: AwareDateTime
    updated_at: AwareDateTime | None = None

    @model_validator(mode="after")
    def validate_order_semantics(self) -> Order:
        _validate_order_price(self.order_kind, self.limit_price)
        if self.updated_at is not None and self.updated_at < self.created_at:
            raise ValueError("updated_at must not be earlier than created_at")
        return self


class Fill(CanonicalModel):
    fill_id: Identifier | None = None
    order_id: Identifier
    exchange_id: Identifier
    side: Side
    price: Probability
    quantity: PositiveQuantity
    timestamp: AwareDateTime


class Position(CanonicalModel):
    exchange_id: Identifier
    quantity: NonNegativeDecimal
    average_entry_price: Probability | None = None
    as_of: AwareDateTime
