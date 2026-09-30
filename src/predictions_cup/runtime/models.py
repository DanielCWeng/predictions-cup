"""Compact immutable live-state representations used only on the hot path."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

SIG_TICK = Decimal("0.005")
MIN_LIMIT_TICKS = 1
MAX_LIMIT_TICKS = 199


class OutcomeSide(StrEnum):
    YES = "yes"
    NO = "no"


class OrderAction(StrEnum):
    BUY = "buy"
    SELL = "sell"


@dataclass(frozen=True, slots=True)
class RuntimeLevel:
    price_ticks: int
    quantity: float


@dataclass(frozen=True, slots=True)
class RuntimeBook:
    exchange_id: str
    market_id: str
    tournament_id: str
    bids: tuple[RuntimeLevel, ...]
    asks: tuple[RuntimeLevel, ...]
    trusted_depth: bool
    observed_monotonic_ns: int


@dataclass(frozen=True, slots=True)
class RuntimeMarket:
    market_id: str
    status: str
    exchange_ids: tuple[str, ...]
    tournament_id: str
    mapping_accepted: bool
    tradeable: bool


@dataclass(frozen=True, slots=True)
class RuntimePosition:
    exchange_id: str
    market_id: str
    tournament_id: str
    gross_exposure: float
    # Signed share inventory is additive maker state. Central Risk continues to
    # consume gross_exposure conservatively; MAKE uses signed_quantity only for
    # reservation-price skew and hard inventory boundaries.
    signed_quantity: float = 0.0


@dataclass(frozen=True, slots=True)
class RuntimeOrderState:
    logical_intent_id: str
    exchange_id: str
    market_id: str
    tournament_id: str
    reserved_exposure: float
    open: bool
    uncertain: bool
    # Set for local BUILD-009 reservations before authoritative account
    # reconciliation. Authoritative SIG rows intentionally leave this unset;
    # RISK-002 obtains their strategy attribution from journal + fills.
    strategy_id: str | None = None


@dataclass(frozen=True, slots=True)
class RuntimePortfolio:
    positions: tuple[RuntimePosition, ...] = ()
    orders: tuple[RuntimeOrderState, ...] = ()
    account_trusted: bool = False

    @property
    def gross_exposure(self) -> float:
        return sum(abs(position.gross_exposure) for position in self.positions)

    @property
    def open_order_exposure(self) -> float:
        return sum(
            order.reserved_exposure
            for order in self.orders
            if order.open or order.uncertain
        )


@dataclass(frozen=True, slots=True)
class RuntimeSnapshot:
    markets: tuple[RuntimeMarket, ...]
    books: tuple[RuntimeBook, ...]
    portfolio: RuntimePortfolio
    observation_monotonic_ns: int

    def market(self, market_id: str) -> RuntimeMarket | None:
        for market in self.markets:
            if market.market_id == market_id:
                return market
        return None

    def book(self, exchange_id: str) -> RuntimeBook | None:
        for book in self.books:
            if book.exchange_id == exchange_id:
                return book
        return None


def limit_price_to_ticks(price: Decimal) -> int:
    """Convert a legal SIG limit price to exact integer ticks."""
    if not price.is_finite():
        raise ValueError("price must be finite")
    ticks = price / SIG_TICK
    integral = ticks.to_integral_value()
    if ticks != integral:
        raise ValueError("SIG limit price must lie on the 0.005 tick")
    value = int(integral)
    if value < MIN_LIMIT_TICKS or value > MAX_LIMIT_TICKS:
        raise ValueError("SIG limit price must be between 0.005 and 0.995")
    return value


def ticks_to_limit_price(ticks: int) -> Decimal:
    """Convert exact runtime ticks back to the canonical Decimal boundary."""
    if ticks < MIN_LIMIT_TICKS or ticks > MAX_LIMIT_TICKS:
        raise ValueError("SIG limit ticks must be between 1 and 199")
    return Decimal(ticks) * SIG_TICK
