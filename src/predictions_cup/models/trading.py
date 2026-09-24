"""Canonical proposed-order, order, fill, and position representations."""

from __future__ import annotations

from pydantic import model_validator

from predictions_cup.models.common import (
    AwareDateTime,
    CanonicalModel,
    FiniteDecimal,
    Identifier,
    NonBlankStr,
    PositiveQuantity,
    Probability,
    Side,
)


class OrderIntent(CanonicalModel):
    intent_id: Identifier
    exchange_id: Identifier
    side: Side
    quantity: PositiveQuantity
    order_kind: NonBlankStr
    limit_price: Probability | None = None
    created_at: AwareDateTime
    strategy_id: Identifier


class Order(CanonicalModel):
    order_id: Identifier | None = None
    intent_id: Identifier
    client_order_id: Identifier | None = None
    exchange_id: Identifier
    side: Side
    quantity: PositiveQuantity
    order_kind: NonBlankStr
    limit_price: Probability | None = None
    status: NonBlankStr | None = None
    created_at: AwareDateTime
    updated_at: AwareDateTime | None = None

    @model_validator(mode="after")
    def validate_timestamp_order(self) -> Order:
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
    quantity: FiniteDecimal
    average_entry_price: Probability | None = None
    as_of: AwareDateTime
