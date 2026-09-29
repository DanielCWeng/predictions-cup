"""Strict transport models for SIG tournament discovery and Realtime batches."""

from __future__ import annotations

from decimal import Decimal
from typing import Literal, Self

from pydantic import AnyHttpUrl, Field, SecretStr, field_validator, model_validator

from predictions_cup.sig.dto import (
    TransportModel,
    WireDateTime,
    WireDecimal,
    WireProbability,
)

TournamentListStatus = Literal["draft", "active", "ended", "any"]


class RealtimeTokenChannelsDto(TransportModel):
    user: str

    @field_validator("user")
    @classmethod
    def reject_blank_user_channel(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("realtime user channel must not be blank")
        return value


class RealtimeTokenDto(TransportModel):
    token: SecretStr
    expires_at: WireDateTime = Field(alias="expiresAt")
    supabase_url: AnyHttpUrl = Field(alias="supabaseUrl")
    anon_key: SecretStr = Field(alias="anonKey")
    channels: RealtimeTokenChannelsDto

    @field_validator("token", "anon_key")
    @classmethod
    def reject_blank_secret(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value().strip():
            raise ValueError("realtime secret must not be blank")
        return value


class TournamentSummaryDto(TransportModel):
    id: str
    slug: str
    name: str
    description: str | None
    status: str
    start_date: WireDateTime | None = Field(alias="startDate")
    end_date: WireDateTime | None = Field(alias="endDate")
    initial_balance: WireDecimal = Field(alias="initialBalance")
    currency_name: str = Field(alias="currencyName")
    my_balance: WireDecimal = Field(alias="myBalance")
    joined_at: WireDateTime | None = Field(alias="joinedAt")
    is_pending_enrolment: bool = Field(alias="isPendingEnrolment")


class TournamentOffsetPaginationDto(TransportModel):
    limit: int
    offset: int
    has_more: bool = Field(alias="hasMore")
    total: int


class TournamentPageDto(TransportModel):
    data: tuple[TournamentSummaryDto, ...]
    pagination: TournamentOffsetPaginationDto


class RealtimeDeliveryDto(TransportModel):
    """Topic-local delivery continuity plus engine provenance."""

    model: str | None = None
    revision: int = Field(ge=0)
    previous_revision: int = Field(alias="previousRevision", ge=0)
    correlation_id: str = Field(alias="correlationId")
    source_sequence_from: int = Field(alias="sourceSequenceFrom", ge=0)
    source_sequence_through: int = Field(alias="sourceSequenceThrough", ge=0)

    @field_validator("correlation_id")
    @classmethod
    def reject_blank_correlation_id(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("correlationId must not be blank")
        return value

    @model_validator(mode="after")
    def validate_source_range(self) -> Self:
        if self.source_sequence_through < self.source_sequence_from:
            raise ValueError("source sequence range is inverted")
        return self


class RealtimeTradeDto(TransportModel):
    exchange_id: str = Field(alias="exchangeId")
    market_id: str = Field(alias="marketId")
    price: WireProbability
    quantity: WireDecimal
    executed_at: WireDateTime = Field(alias="executedAt")
    tournament_id: str | None = Field(default=None, alias="tournamentId")

    @model_validator(mode="after")
    def validate_quantity(self) -> Self:
        if self.quantity <= Decimal("0"):
            raise ValueError("realtime trade quantity must be positive")
        return self


class BookDirtyDto(TransportModel):
    exchange_id: str = Field(alias="exchangeId")
    market_id: str = Field(alias="marketId")
    tournament_id: str = Field(alias="tournamentId")
    at: WireDateTime


class MarketSettledDto(TransportModel):
    market_id: str = Field(alias="marketId")
    tournament_id: str = Field(alias="tournamentId")
    settled_with: str = Field(alias="settledWith")
    at: WireDateTime


class MarketBatchDto(TransportModel):
    trades: tuple[RealtimeTradeDto, ...]
    book_dirty: tuple[BookDirtyDto, ...] = Field(alias="bookDirty")
    market_settled: tuple[MarketSettledDto, ...] = Field(alias="marketSettled")
    delivery: RealtimeDeliveryDto


class AccountFillDto(TransportModel):
    order_id: int = Field(alias="orderId")
    exchange_id: str = Field(alias="exchangeId")
    market_id: str = Field(alias="marketId")
    price: WireProbability
    quantity: WireDecimal
    executed_at: WireDateTime = Field(alias="executedAt")
    tournament_id: str | None = Field(alias="tournamentId")


class AccountOrderUpdateDto(TransportModel):
    order_id: int = Field(alias="orderId")
    exchange_id: str = Field(alias="exchangeId")
    market_id: str = Field(alias="marketId")
    open: bool
    quantity_traded: WireDecimal = Field(alias="quantityTraded")
    total_cost: WireDecimal = Field(alias="totalCost")
    latest_trade_price: WireProbability | None = Field(alias="latestTradePrice")
    tournament_id: str | None = Field(alias="tournamentId")
    at: WireDateTime


class AccountSettlementDto(TransportModel):
    market_id: str = Field(alias="marketId")
    exchange_id: str = Field(alias="exchangeId")
    outcome_side: str = Field(alias="outcomeSide")
    shares: WireDecimal
    cost_basis: WireDecimal = Field(alias="costBasis")
    payout: WireDecimal
    realized_pnl: WireDecimal = Field(alias="realizedPnl")
    settlement_outcome: str = Field(alias="settlementOutcome")
    tournament_id: str | None = Field(alias="tournamentId")
    at: WireDateTime


class AccountRefundDto(TransportModel):
    market_id: str = Field(alias="marketId")
    exchange_id: str = Field(alias="exchangeId")
    shares: WireDecimal
    refund_amount: WireDecimal = Field(alias="refundAmount")
    tournament_id: str | None = Field(alias="tournamentId")
    at: WireDateTime


class AccountCollateralChangeDto(TransportModel):
    delta: WireDecimal
    outstanding_advance_after: WireDecimal = Field(alias="outstandingAdvanceAfter")
    component_id: str = Field(alias="componentId")
    tournament_id: str | None = Field(alias="tournamentId")


class AccountBatchDto(TransportModel):
    fills: tuple[AccountFillDto, ...]
    order_updates: tuple[AccountOrderUpdateDto, ...] = Field(alias="orderUpdates")
    settlements: tuple[AccountSettlementDto, ...]
    refunds: tuple[AccountRefundDto, ...]
    collateral_changes: tuple[AccountCollateralChangeDto, ...] = Field(
        alias="collateralChanges"
    )
    delivery: RealtimeDeliveryDto
