"""Strict SIG trading request/response DTOs from api-1.json."""

from __future__ import annotations

from decimal import Decimal
from typing import Literal, Self
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from predictions_cup.sig.dto import (
    CoverageDto,
    CursorPaginationDto,
    TransportModel,
    WireDateTime,
    WireDecimal,
    WireProbability,
)

OrderOutcomeSide = Literal["yes", "no"]
OrderAction = Literal["buy", "sell"]
OrderStatusFilter = Literal["open", "closed", "expired", "all"]
SIG_TICK = Decimal("0.005")
SIG_MAX_QUANTITY = 2_147_483_647


class OrderInputDto(TransportModel):
    exchange_id: str = Field(alias="exchangeId", pattern=r"^\d+$")
    side: OrderOutcomeSide
    action: OrderAction
    quantity: int = Field(gt=0, le=SIG_MAX_QUANTITY)
    price: WireProbability | None = None
    expiration_date: WireDateTime | None = Field(default=None, alias="expirationDate")
    tournament_id: str | None = Field(default=None, alias="tournamentId")

    @field_validator("tournament_id")
    @classmethod
    def reject_blank_tournament(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("tournamentId must not be blank")
        return value

    @model_validator(mode="after")
    def validate_price_semantics(self) -> Self:
        if self.price is None:
            if self.expiration_date is not None:
                raise ValueError("market orders cannot have expirationDate")
            return self

        market_sentinel = (
            (self.action == "buy" and self.price == Decimal("1"))
            or (self.action == "sell" and self.price == Decimal("0"))
        )
        if market_sentinel:
            if self.expiration_date is not None:
                raise ValueError("market orders cannot have expirationDate")
            return self

        if self.price < Decimal("0.005") or self.price > Decimal("0.995"):
            raise ValueError("fresh limit price must be between 0.005 and 0.995")
        if self.price % SIG_TICK != 0:
            raise ValueError("fresh limit price must lie on the 0.005 tick")
        return self


class SingleOrderRequestDto(OrderInputDto):
    idempotency_key: str = Field(
        alias="idempotencyKey",
        min_length=1,
        max_length=255,
    )


class BatchOrderRequestDto(TransportModel):
    idempotency_key: str = Field(alias="idempotencyKey", min_length=1, max_length=255)
    orders: tuple[OrderInputDto, ...] = Field(min_length=1, max_length=50)


class MultiLegOrderRequestDto(TransportModel):
    legs: tuple[OrderInputDto, ...] = Field(min_length=1, max_length=10)
    idempotency_key: str = Field(alias="idempotencyKey", min_length=1, max_length=255)
    relationship_constraint: str | None = Field(default=None, alias="relationshipConstraint")

    @field_validator("relationship_constraint")
    @classmethod
    def validate_relationship_uuid(cls, value: str | None) -> str | None:
        if value is None:
            return None
        UUID(value)
        return value

    @model_validator(mode="after")
    def reject_duplicate_legs(self) -> Self:
        identities = tuple((leg.tournament_id, leg.exchange_id) for leg in self.legs)
        if len(set(identities)) != len(identities):
            raise ValueError("duplicate exchange leg in the same tournament scope")
        return self


class SingleOrderResponseDto(TransportModel):
    order_id: int | None = Field(alias="orderId")
    exchange_id: str = Field(alias="exchangeId")
    open: bool
    remaining_quantity: WireDecimal | None = Field(default=None, alias="remainingQuantity")
    action: OrderAction | None = None
    side: OrderOutcomeSide | None = None
    price: WireDecimal | None = None
    quantity: int | None = None
    terminal_reason_code: str | None = Field(default=None, alias="terminalReasonCode")
    quantity_traded: WireDecimal = Field(alias="quantityTraded")
    total_cost: WireDecimal = Field(alias="totalCost")
    fill_price: WireProbability | None = Field(alias="fillPrice")
    all: dict[str, object] | None


class BatchOrderResultDto(TransportModel):
    index: int
    ok: bool
    status: int
    data: dict[str, object]


class BatchOrderResponseDto(TransportModel):
    results: tuple[BatchOrderResultDto, ...]


class MultiLegResultDto(TransportModel):
    index: int
    ok: bool
    data: dict[str, object]


class MultiLegResponseDto(TransportModel):
    results: tuple[MultiLegResultDto, ...]


class CancelAllErrorDto(TransportModel):
    order_id: int = Field(alias="orderId")
    error: str


class CancelAllResponseDto(TransportModel):
    cancelled: int
    errors: tuple[CancelAllErrorDto, ...]


class OrderReadDto(TransportModel):
    id: int
    exchange_id: str = Field(alias="exchangeId")
    side: OrderOutcomeSide
    action: OrderAction
    quantity: WireDecimal
    price_limit: WireProbability | None = Field(alias="priceLimit")
    open: bool
    created_at: WireDateTime = Field(alias="createdAt")
    expiration_date: WireDateTime | None = Field(alias="expirationDate")


class OrderPageDto(TransportModel):
    data: tuple[OrderReadDto, ...]
    pagination: CursorPaginationDto
    coverage: CoverageDto | None = None


class PositionLotDto(TransportModel):
    lot_id: str = Field(alias="lotId")
    side: str
    quantity: WireDecimal
    entry_price: WireDecimal = Field(alias="entryPrice")
    opened_at: WireDateTime = Field(alias="openedAt")


class PositionReadDto(TransportModel):
    exchange_id: str = Field(alias="exchangeId")
    market_id: str = Field(alias="marketId")
    market_title: str = Field(alias="marketTitle")
    option: str | None
    settled: bool
    quantity: WireDecimal
    avg_cost: WireDecimal = Field(alias="avgCost")
    current_price: WireDecimal | None = Field(alias="currentPrice")
    market_value: WireDecimal = Field(alias="marketValue")
    cost_basis: WireDecimal = Field(alias="costBasis")
    unrealized_pnl: WireDecimal = Field(alias="unrealizedPnl")
    unrealized_pnl_pct: WireDecimal = Field(alias="unrealizedPnlPct")
    money_earned: WireDecimal = Field(alias="moneyEarned")
    lots: tuple[PositionLotDto, ...]


class PositionSummaryDto(TransportModel):
    total_market_value: WireDecimal = Field(alias="totalMarketValue")
    total_cost_basis: WireDecimal = Field(alias="totalCostBasis")
    total_unrealized_pnl: WireDecimal = Field(alias="totalUnrealizedPnl")


class PositionsResponseDto(TransportModel):
    positions: tuple[PositionReadDto, ...]
    summary: PositionSummaryDto
