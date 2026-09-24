"""Shared canonical value types and validation primitives."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from enum import StrEnum
from typing import Annotated

from pydantic import AfterValidator, BaseModel, BeforeValidator, ConfigDict


class CanonicalModel(BaseModel):
    """Immutable base for canonical facts/value objects."""

    model_config = ConfigDict(frozen=True, extra="forbid")


def _non_blank(value: str) -> str:
    if not value.strip():
        raise ValueError("value must not be blank")
    return value


def _reject_binary_float(value: object) -> object:
    if isinstance(value, (bool, float)):
        raise ValueError("binary floating-point and boolean inputs are not accepted")
    return value


def _finite_decimal(value: Decimal) -> Decimal:
    if not value.is_finite():
        raise ValueError("decimal value must be finite")
    return value


def _probability(value: Decimal) -> Decimal:
    value = _finite_decimal(value)
    if value < Decimal("0") or value > Decimal("1"):
        raise ValueError("probability/price must be between 0 and 1 inclusive")
    return value


def _positive_quantity(value: Decimal) -> Decimal:
    value = _finite_decimal(value)
    if value <= Decimal("0"):
        raise ValueError("quantity must be greater than zero")
    return value


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone aware")
    return value.astimezone(timezone.utc)


Identifier = Annotated[str, AfterValidator(_non_blank)]
NonBlankStr = Annotated[str, AfterValidator(_non_blank)]
FiniteDecimal = Annotated[
    Decimal,
    BeforeValidator(_reject_binary_float),
    AfterValidator(_finite_decimal),
]
Probability = Annotated[
    Decimal,
    BeforeValidator(_reject_binary_float),
    AfterValidator(_probability),
]
PositiveQuantity = Annotated[
    Decimal,
    BeforeValidator(_reject_binary_float),
    AfterValidator(_positive_quantity),
]
AwareDateTime = Annotated[datetime, AfterValidator(_aware_utc)]


class Side(StrEnum):
    BUY = "buy"
    SELL = "sell"


class PriceKind(StrEnum):
    BEST_BID = "best_bid"
    BEST_ASK = "best_ask"
    LAST_TRADE = "last_trade"
    MIDPOINT = "midpoint"
