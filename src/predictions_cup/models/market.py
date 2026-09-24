"""Canonical market, observation, and external-reference models."""

from __future__ import annotations

from pydantic import AnyHttpUrl, Field, model_validator

from predictions_cup.models.common import (
    AwareDateTime,
    CanonicalModel,
    Identifier,
    NonBlankStr,
    PositiveQuantity,
    PriceKind,
    Probability,
    Side,
)


class TournamentContext(CanonicalModel):
    tournament_id: Identifier
    slug: NonBlankStr | None = None
    name: NonBlankStr | None = None


class Exchange(CanonicalModel):
    exchange_id: Identifier
    market_id: Identifier
    outcome_label: NonBlankStr


class Market(CanonicalModel):
    market_id: Identifier
    title: NonBlankStr
    status: NonBlankStr | None = None
    exchanges: tuple[Exchange, ...] = Field(min_length=1)
    tournament: TournamentContext | None = None

    @model_validator(mode="after")
    def validate_exchange_membership(self) -> Market:
        if any(exchange.market_id != self.market_id for exchange in self.exchanges):
            raise ValueError("all exchanges must reference this market_id")
        exchange_ids = [exchange.exchange_id for exchange in self.exchanges]
        if len(exchange_ids) != len(set(exchange_ids)):
            raise ValueError("exchange_id values must be unique within a market")
        return self


class Price(CanonicalModel):
    exchange_id: Identifier
    value: Probability
    timestamp: AwareDateTime
    source: NonBlankStr
    kind: PriceKind


class OrderBookLevel(CanonicalModel):
    price: Probability
    quantity: PositiveQuantity


class OrderBook(CanonicalModel):
    exchange_id: Identifier
    bids: tuple[OrderBookLevel, ...]
    asks: tuple[OrderBookLevel, ...]
    timestamp: AwareDateTime
    source: NonBlankStr
    revision: Identifier | None = None


class Trade(CanonicalModel):
    trade_id: Identifier | None = None
    exchange_id: Identifier
    price: Probability
    quantity: PositiveQuantity
    timestamp: AwareDateTime
    side: Side | None = None


class ExternalReference(CanonicalModel):
    venue: NonBlankStr
    external_contract_id: Identifier
    external_outcome_id: Identifier | None = None
    url: AnyHttpUrl | None = None
    slug: NonBlankStr | None = None
