"""Validated SIG REST transport DTOs and explicit canonical conversions."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Annotated, Literal, Self

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, model_validator

from predictions_cup.models import (
    Exchange,
    Market,
    OrderBook,
    OrderBookLevel,
    Price,
    PriceKind,
    TournamentContext,
    Trade,
)

Resolution = Literal["1m", "5m", "1h", "1d", "1w"]
ContextType = Literal["public", "tournament"]
MarketStatus = Literal["open", "closed", "settled"]
TradeOutcome = Literal["YES", "NO"]


def _wire_decimal(value: object) -> Decimal:
    if isinstance(value, bool):
        raise ValueError("boolean is not a numeric wire value")
    if isinstance(value, Decimal):
        result = value
    elif isinstance(value, int):
        result = Decimal(value)
    elif isinstance(value, float):
        result = Decimal(str(value))
    elif isinstance(value, str):
        try:
            result = Decimal(value)
        except InvalidOperation as exc:
            raise ValueError("invalid decimal wire value") from exc
    else:
        raise ValueError("unsupported numeric wire value")
    if not result.is_finite():
        raise ValueError("numeric wire value must be finite")
    return result


def _wire_probability(value: object) -> Decimal:
    result = _wire_decimal(value)
    if result < Decimal("0") or result > Decimal("1"):
        raise ValueError("wire probability must be between 0 and 1 inclusive")
    return result


def _wire_datetime(value: object) -> datetime:
    if not isinstance(value, str):
        raise ValueError("wire timestamp must be an ISO-8601 string")
    candidate = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError as exc:
        raise ValueError("invalid ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("wire timestamp must be timezone aware")
    return parsed.astimezone(UTC)


WireDecimal = Annotated[Decimal, BeforeValidator(_wire_decimal)]
WireProbability = Annotated[Decimal, BeforeValidator(_wire_probability)]
WireDateTime = Annotated[datetime, BeforeValidator(_wire_datetime)]


class TransportModel(BaseModel):
    """Strict immutable base for validated SIG payloads."""

    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)


class AccountDto(TransportModel):
    id: str
    username: str | None
    email: str | None
    created_at: WireDateTime = Field(alias="createdAt")
    avatar_url: str | None = Field(alias="avatarUrl")
    bio: str | None
    balance: WireDecimal | None


class TournamentDto(TransportModel):
    id: str
    slug: str
    name: str
    currency_name: str = Field(alias="currencyName")
    is_ongoing_play: bool = Field(alias="isOngoingPlay")

    def to_canonical(self) -> TournamentContext:
        return TournamentContext(tournament_id=self.id, slug=self.slug, name=self.name)


class MarketReadContextDescriptorDto(TransportModel):
    context_type: ContextType = Field(alias="type")
    tournament: TournamentDto | None

    @model_validator(mode="after")
    def validate_context_shape(self) -> Self:
        if self.context_type == "public" and self.tournament is not None:
            raise ValueError("public context must not contain tournament metadata")
        if self.context_type == "tournament" and self.tournament is None:
            raise ValueError("tournament context requires tournament metadata")
        return self


class MarketContextExchangeDto(TransportModel):
    id: str
    latest_price: WireProbability | None = Field(alias="latestPrice")


class MarketPricingContextDto(MarketReadContextDescriptorDto):
    exchanges: tuple[MarketContextExchangeDto, ...]
    status: MarketStatus | None = None
    settled_with: str | None = Field(default=None, alias="settledWith")
    settled_on: WireDateTime | None = Field(default=None, alias="settledOn")


class CreatorDto(TransportModel):
    id: str
    username: str | None


class ExchangeDto(TransportModel):
    id: str
    option: str | None
    latest_price: WireProbability | None = Field(alias="latestPrice")
    initial_price: WireProbability | None = Field(alias="initialPrice")

    def to_canonical(self, *, market_id: str) -> Exchange:
        if self.option is None or not self.option.strip():
            raise ValueError(
                "SIG exchange option is null/blank and cannot populate canonical outcome_label"
            )
        return Exchange(exchange_id=self.id, market_id=market_id, outcome_label=self.option)


class MarketDto(TransportModel):
    id: str
    title: str
    thumbnail_url: str | None = Field(alias="thumbnailUrl")
    status: MarketStatus
    created_at: WireDateTime | None = Field(alias="createdAt")
    settlement_date: WireDateTime | None = Field(alias="settlementDate")
    settled_with: str | None = Field(alias="settledWith")
    settled_on: WireDateTime | None = Field(alias="settledOn")
    categories: tuple[str, ...]
    is_composite: bool = Field(alias="isComposite")
    is_multi_outcome: bool = Field(alias="isMultiOutcome")
    exchanges: tuple[ExchangeDto, ...]
    contexts: tuple[MarketPricingContextDto, ...]
    creator: CreatorDto | None

    def to_canonical(self, *, tournament_id: str | None = None) -> Market:
        tournament = self._resolve_canonical_tournament(tournament_id)
        return Market(
            market_id=self.id,
            title=self.title,
            status=self.status,
            exchanges=tuple(
                exchange.to_canonical(market_id=self.id) for exchange in self.exchanges
            ),
            tournament=tournament,
        )

    def _resolve_canonical_tournament(self, tournament_id: str | None) -> TournamentContext | None:
        tournament_contexts = tuple(
            context.tournament
            for context in self.contexts
            if context.context_type == "tournament" and context.tournament is not None
        )
        if tournament_id is None:
            if tournament_contexts:
                raise ValueError(
                    "explicit tournament_id is required before converting "
                    "organization-scoped market data"
                )
            return None

        matches = tuple(context for context in tournament_contexts if context.id == tournament_id)
        if len(matches) != 1:
            raise ValueError(
                "requested tournament_id is absent or ambiguous in SIG market contexts"
            )
        return matches[0].to_canonical()


class PaginationDto(TransportModel):
    total: WireDecimal
    limit: WireDecimal
    has_more: bool = Field(alias="hasMore")
    next_cursor: str | None = Field(alias="nextCursor")


class CursorPaginationDto(TransportModel):
    limit: int
    has_more: bool = Field(alias="hasMore")
    next_cursor: str | None = Field(alias="nextCursor")


class MarketPageDto(TransportModel):
    data: tuple[MarketDto, ...]
    pagination: PaginationDto


class ExchangePricingContextDto(MarketReadContextDescriptorDto):
    latest_price: WireProbability | None = Field(alias="latestPrice")


class ExchangeListItemDto(TransportModel):
    id: str
    market_id: str = Field(alias="marketId")
    option: str | None
    latest_price: WireProbability | None = Field(alias="latestPrice")
    initial_price: WireProbability | None = Field(alias="initialPrice")
    contexts: tuple[ExchangePricingContextDto, ...]

    def to_canonical(self) -> Exchange:
        if self.option is None or not self.option.strip():
            raise ValueError(
                "SIG exchange option is null/blank and cannot populate canonical outcome_label"
            )
        return Exchange(exchange_id=self.id, market_id=self.market_id, outcome_label=self.option)


class ExchangePageDto(TransportModel):
    data: tuple[ExchangeListItemDto, ...]
    pagination: CursorPaginationDto


MarketNodeType = Literal["operator", "contract"]
MarketNodeOperator = Literal["AND", "OR", "NOT", "IF"]
MarketContractType = Literal[
    "Freeform",
    "Election Outcome",
    "Sports Outcome",
    "Economic Indicator",
    "Financial Product",
    "Corporate Earnings",
    "Imported Market",
    "Existing Market",
]


class MarketNodeDto(TransportModel):
    """Recursive SIG market-node transport shape from the supplied OpenAPI contract."""

    node_type: MarketNodeType = Field(alias="nodeType")
    position: int | None = None
    node_id: str | None = Field(default=None, alias="nodeId")
    contract_id: str | None = Field(default=None, alias="contractId")
    operator: MarketNodeOperator | None = None
    title: str | None = None
    settlement_date: WireDateTime | None = Field(default=None, alias="settlementDate")
    settled_with: str | None = Field(default=None, alias="settledWith")
    settled_on: WireDateTime | None = Field(default=None, alias="settledOn")
    contract_type: MarketContractType | None = Field(default=None, alias="contractType")
    contract_details: dict[str, object] | None = Field(default=None, alias="contractDetails")
    settlement_options: tuple[str, ...] | None = Field(default=None, alias="settlementOptions")
    children: tuple[MarketNodeDto, ...] | None = None

    @model_validator(mode="after")
    def validate_operator_shape(self) -> Self:
        if self.node_type == "operator":
            if self.operator is None:
                raise ValueError("SIG operator market node requires operator")
            if self.children is None:
                raise ValueError("SIG operator market node requires children")
        return self


class MarketNodesDto(TransportModel):
    market_id: str
    root: MarketNodeDto
    contexts: tuple[MarketReadContextDescriptorDto, ...]


class PriceSnapshotDto(TransportModel):
    exchange_id: str = Field(alias="exchangeId")
    market_id: str = Field(alias="marketId")
    option: str | None
    latest_price: WireProbability | None = Field(alias="latestPrice")
    best_bid: WireProbability | None = Field(alias="bestBid")
    best_ask: WireProbability | None = Field(alias="bestAsk")
    spread: WireDecimal | None

    def to_canonical_prices(self, *, observed_at: datetime) -> tuple[Price, ...]:
        observations = (
            (self.latest_price, PriceKind.LAST_TRADE),
            (self.best_bid, PriceKind.BEST_BID),
            (self.best_ask, PriceKind.BEST_ASK),
        )
        return tuple(
            Price(
                exchange_id=self.exchange_id,
                value=value,
                timestamp=observed_at,
                source="sig-rest",
                kind=kind,
            )
            for value, kind in observations
            if value is not None
        )


class BulkPricesDto(TransportModel):
    data: tuple[PriceSnapshotDto, ...]
    missing_ids: tuple[str, ...] = Field(alias="missingIds")


class OrderBookLevelDto(TransportModel):
    price: WireProbability
    quantity: WireDecimal


class OrderBookSnapshotDto(TransportModel):
    exchange_id: str = Field(alias="exchangeId")
    market_id: str = Field(alias="marketId")
    depth: int
    bids: tuple[OrderBookLevelDto, ...]
    asks: tuple[OrderBookLevelDto, ...]
    best_bid: WireProbability | None = Field(alias="bestBid")
    best_ask: WireProbability | None = Field(alias="bestAsk")
    spread: WireDecimal | None

    @model_validator(mode="after")
    def validate_book_ordering(self) -> Self:
        bid_prices = tuple(level.price for level in self.bids)
        ask_prices = tuple(level.price for level in self.asks)
        if bid_prices != tuple(sorted(bid_prices, reverse=True)):
            raise ValueError("SIG orderbook bids are not descending")
        if ask_prices != tuple(sorted(ask_prices)):
            raise ValueError("SIG orderbook asks are not ascending")
        return self

    def to_canonical(self, *, observed_at: datetime) -> OrderBook:
        return OrderBook(
            exchange_id=self.exchange_id,
            bids=tuple(
                OrderBookLevel(price=level.price, quantity=level.quantity) for level in self.bids
            ),
            asks=tuple(
                OrderBookLevel(price=level.price, quantity=level.quantity) for level in self.asks
            ),
            timestamp=observed_at,
            source="sig-rest",
            revision=None,
        )


class CoverageDto(TransportModel):
    complete: bool
    projected_through_sequence: int | None = Field(alias="projectedThroughSequence")


class CandleDto(TransportModel):
    time: WireDateTime
    open: WireProbability | None
    high: WireProbability | None
    low: WireProbability | None
    close: WireProbability | None
    vwap: WireProbability | None
    volume: WireDecimal
    trade_count: int = Field(alias="tradeCount")


class PriceHistoryDto(TransportModel):
    exchange_id: str = Field(alias="exchangeId")
    market_id: str = Field(alias="marketId")
    resolution: Resolution
    from_timestamp: WireDateTime = Field(alias="from")
    to_timestamp: WireDateTime = Field(alias="to")
    candles: tuple[CandleDto, ...]
    coverage: CoverageDto


class ExchangeTradeDto(TransportModel):
    id: str
    created_at: WireDateTime = Field(alias="createdAt")
    price: WireProbability | None
    size: int
    side: TradeOutcome
    volume: WireDecimal | None

    def to_canonical(self, *, exchange_id: str) -> Trade:
        if self.price is None:
            raise ValueError("SIG trade has null price and cannot populate canonical Trade.price")
        return Trade(
            trade_id=self.id,
            exchange_id=exchange_id,
            price=self.price,
            quantity=Decimal(self.size),
            timestamp=self.created_at,
            side=None,
        )


class TradePageDto(TransportModel):
    exchange_id: str = Field(alias="exchangeId")
    market_id: str = Field(alias="marketId")
    from_timestamp: WireDateTime = Field(alias="from")
    to_timestamp: WireDateTime = Field(alias="to")
    data: tuple[ExchangeTradeDto, ...]
    pagination: CursorPaginationDto
    coverage: CoverageDto | None = None
