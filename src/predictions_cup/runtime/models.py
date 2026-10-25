"""Compact immutable live-state representations used only on the hot path."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum
from types import MappingProxyType

SIG_TICK = Decimal("0.005")
MIN_LIMIT_TICKS = 1
MAX_LIMIT_TICKS = 199


class OutcomeSide(StrEnum):
    YES = "yes"
    NO = "no"


class OrderAction(StrEnum):
    BUY = "buy"
    SELL = "sell"


class AccountTrustGrade(StrEnum):
    TRUSTED = "TRUSTED"
    PROXY = "PROXY"
    UNTRUSTED = "UNTRUSTED"


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
    # Signed YES inventory change if this order fills (NO BUY and YES SELL are
    # negative). Zero means direction is unknown and must be bounded both ways.
    signed_quantity: float = 0.0
    # SIG order identity when an authoritative journal ACK ties this intent to
    # an exchange order.
    exchange_order_id: str | None = None


@dataclass(frozen=True, slots=True)
class RuntimePortfolio:
    positions: tuple[RuntimePosition, ...] = ()
    orders: tuple[RuntimeOrderState, ...] = ()
    account_trusted: bool = False
    account_trust_grade: AccountTrustGrade | None = None
    account_proxy_age_ns: int | None = None
    account_proxy_uncertainty: float = 0.0
    account_proxy_cash_balance: Decimal | None = None
    # Immutable, per-snapshot inventory index. Built on first demand: the
    # account/risk path that merely constructs a portfolio pays no extra cost.
    # Excluded from equality/hash so caching does not alter portfolio identity.
    _inventory_lookup: Mapping[tuple[str, str], tuple[float, float, float]] | None = field(
        default=None, init=False, repr=False, compare=False, hash=False
    )

    def _indexed_inventory(self) -> Mapping[tuple[str, str], tuple[float, float, float]]:
        lookup = self._inventory_lookup
        if lookup is not None:
            return lookup
        # position, positive pending, negative pending, unknown pending.
        totals: dict[tuple[str, str], list[float]] = {}
        for position in self.positions:
            key = (position.exchange_id, position.tournament_id)
            totals.setdefault(key, [0.0, 0.0, 0.0, 0.0])[0] += position.signed_quantity
        for order in self.orders:
            if not (order.open or order.uncertain):
                continue
            key = (order.exchange_id, order.tournament_id)
            values = totals.setdefault(key, [0.0, 0.0, 0.0, 0.0])
            if order.signed_quantity > 0.0:
                values[1] += order.signed_quantity
            elif order.signed_quantity < 0.0:
                values[2] += order.signed_quantity
            else:
                values[3] += order.reserved_exposure
        lookup = MappingProxyType({
            key: (row[0], row[0] + row[2] - row[3], row[0] + row[1] + row[3])
            for key, row in totals.items()
        })
        # A frozen portfolio only gains a deterministic cache derived from its
        # immutable positions and orders. Never cache across account snapshots.
        object.__setattr__(self, "_inventory_lookup", lookup)
        return lookup

    @property
    def trust_grade(self) -> AccountTrustGrade:
        if self.account_trust_grade is not None:
            return self.account_trust_grade
        return AccountTrustGrade.TRUSTED if self.account_trusted else AccountTrustGrade.UNTRUSTED

    @property
    def proxy_active(self) -> bool:
        return self.trust_grade is AccountTrustGrade.PROXY

    @property
    def gross_exposure(self) -> float:
        return sum(abs(position.gross_exposure) for position in self.positions)

    @property
    def open_order_exposure(self) -> float:
        return sum(
            order.reserved_exposure for order in self.orders if order.open or order.uncertain
        )

    def signed_inventory(self, exchange_id: str, tournament_id: str) -> float:
        value = self._indexed_inventory().get((exchange_id, tournament_id))
        return 0.0 if value is None else value[0]

    def worst_case_inventory_bounds(
        self,
        exchange_id: str,
        tournament_id: str,
    ) -> tuple[float, float]:
        """Signed inventory bounds if every open/uncertain order fills.

        Unknown order directions count against both sides. Every result
        uses the same arithmetic as the original portfolio scan.
        """
        value = self._indexed_inventory().get((exchange_id, tournament_id))
        return (0.0, 0.0) if value is None else (value[1], value[2])

    def is_inventory_reducing(
        self,
        *,
        exchange_id: str,
        tournament_id: str,
        signed_delta: float,
    ) -> bool:
        if signed_delta == 0.0:
            return False
        low, high = self.worst_case_inventory_bounds(exchange_id, tournament_id)
        # The action must reduce absolute exposure under every exposure state
        # represented by the proxy's worst-case interval.
        if signed_delta > 0.0:
            return high < 0.0 and signed_delta <= -high
        return low > 0.0 and -signed_delta <= low


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
