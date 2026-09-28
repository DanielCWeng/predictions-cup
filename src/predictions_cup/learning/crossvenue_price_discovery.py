"""As-of-safe primitives for EXPERIMENT-004C-C cross-venue price discovery."""

from __future__ import annotations

import bisect
import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

import numpy as np
from numpy.typing import NDArray

from predictions_cup.replay.model import BookLevel


@dataclass(frozen=True, slots=True)
class QuotePoint:
    observed_at: datetime
    best_bid: Decimal | None
    best_ask: Decimal | None
    source_at: datetime | None = None
    bids: tuple[BookLevel, ...] = ()
    asks: tuple[BookLevel, ...] = ()
    valid: bool = True

    def __post_init__(self) -> None:
        if self.observed_at.tzinfo is None or self.observed_at.utcoffset() is None:
            raise ValueError("observed_at must be timezone-aware")
        if self.source_at is not None and (
            self.source_at.tzinfo is None or self.source_at.utcoffset() is None
        ):
            raise ValueError("source_at must be timezone-aware")
        if (
            self.best_bid is not None
            and self.best_ask is not None
            and self.best_bid > self.best_ask
        ):
            raise ValueError("crossed quote is invalid")

    @property
    def midpoint(self) -> Decimal | None:
        if not self.valid or self.best_bid is None or self.best_ask is None:
            return None
        return (self.best_bid + self.best_ask) / Decimal(2)

    @property
    def spread(self) -> Decimal | None:
        if not self.valid or self.best_bid is None or self.best_ask is None:
            return None
        return self.best_ask - self.best_bid


class QuoteSeries:
    """Immutable quote history with explicit observable-time as-of lookup."""

    def __init__(self, points: Iterable[QuotePoint]) -> None:
        ordered = tuple(sorted(points, key=lambda point: point.observed_at))
        if any(
            ordered[index].observed_at > ordered[index + 1].observed_at
            for index in range(len(ordered) - 1)
        ):
            raise ValueError("quote points must be time ordered")
        self.points = ordered
        self._times = tuple(point.observed_at for point in ordered)

    def asof(self, at: datetime) -> QuotePoint | None:
        _require_aware(at)
        index = bisect.bisect_right(self._times, at) - 1
        return None if index < 0 else self.points[index]

    def before(self, at: datetime) -> QuotePoint | None:
        _require_aware(at)
        index = bisect.bisect_left(self._times, at) - 1
        return None if index < 0 else self.points[index]

    def first_after(self, at: datetime, *, inclusive: bool = False) -> QuotePoint | None:
        _require_aware(at)
        index = (
            bisect.bisect_left(self._times, at)
            if inclusive
            else bisect.bisect_right(self._times, at)
        )
        return None if index >= len(self.points) else self.points[index]

    def count_updates(self, start: datetime, end: datetime) -> int:
        _require_aware(start)
        _require_aware(end)
        if end < start:
            raise ValueError("end must not precede start")
        left = bisect.bisect_right(self._times, start)
        right = bisect.bisect_right(self._times, end)
        return max(0, right - left)

    def midpoint_change(self, at: datetime, lookback: timedelta) -> Decimal | None:
        current = self.asof(at)
        previous = self.asof(at - lookback)
        if current is None or previous is None:
            return None
        current_mid = current.midpoint
        previous_mid = previous.midpoint
        if current_mid is None or previous_mid is None:
            return None
        return current_mid - previous_mid


@dataclass(frozen=True, slots=True)
class SyntheticExecutableQuote:
    observed_at: datetime
    best_bid: Decimal
    best_ask: Decimal
    executable_size: Decimal

    @property
    def midpoint(self) -> Decimal:
        return (self.best_bid + self.best_ask) / Decimal(2)


def synthetic_union_quote(
    components: Sequence[QuotePoint],
    *,
    at: datetime,
    trade_size: Decimal,
    max_quote_age: timedelta,
) -> SyntheticExecutableQuote | None:
    """Build the reviewed union/sum synthetic from actually executable component books."""
    _require_aware(at)
    if trade_size <= 0:
        raise ValueError("trade_size must be positive")
    if max_quote_age <= timedelta(0):
        raise ValueError("max_quote_age must be positive")
    if not components:
        return None

    bids: list[Decimal] = []
    asks: list[Decimal] = []
    capacities: list[Decimal] = []
    observed_times: list[datetime] = []
    for quote in components:
        if not quote.valid or quote.observed_at > at:
            return None
        if at - quote.observed_at > max_quote_age:
            return None
        if quote.best_bid is None or quote.best_ask is None:
            return None
        bid_capacity = _top_capacity(quote.bids, quote.best_bid)
        ask_capacity = _top_capacity(quote.asks, quote.best_ask)
        capacity = min(bid_capacity, ask_capacity)
        if capacity < trade_size:
            return None
        bids.append(quote.best_bid)
        asks.append(quote.best_ask)
        capacities.append(capacity)
        observed_times.append(quote.observed_at)

    return SyntheticExecutableQuote(
        observed_at=min(observed_times),
        best_bid=sum(bids, Decimal("0")),
        best_ask=sum(asks, Decimal("0")),
        executable_size=min(capacities),
    )


def logit_probability(value: Decimal | float, *, epsilon: float = 1e-6) -> float:
    probability = float(value)
    clipped = min(1.0 - epsilon, max(epsilon, probability))
    return math.log(clipped / (1.0 - clipped))


@dataclass(frozen=True, slots=True)
class RobustScaler:
    median: NDArray[np.float64]
    scale: NDArray[np.float64]

    @classmethod
    def fit(cls, values: NDArray[np.float64]) -> RobustScaler:
        if values.ndim != 2 or values.shape[0] == 0:
            raise ValueError("scaler requires a non-empty 2D matrix")
        median = np.median(values, axis=0)
        q25 = np.quantile(values, 0.25, axis=0)
        q75 = np.quantile(values, 0.75, axis=0)
        iqr = q75 - q25
        scale = np.where(iqr > 0, iqr, 1.0)
        return cls(median=median, scale=scale)

    def transform(self, values: NDArray[np.float64]) -> NDArray[np.float64]:
        return np.asarray((values - self.median) / self.scale, dtype=np.float64)


@dataclass(frozen=True, slots=True)
class RidgeModel:
    coefficients: NDArray[np.float64]
    scaler: RobustScaler
    alpha: float

    def predict(self, values: NDArray[np.float64]) -> NDArray[np.float64]:
        transformed = self.scaler.transform(values)
        design = np.column_stack((np.ones(transformed.shape[0]), transformed))
        return np.asarray(design @ self.coefficients, dtype=np.float64)


def fit_ridge(
    values: NDArray[np.float64],
    target: NDArray[np.float64],
    *,
    alpha: float,
) -> RidgeModel:
    if values.ndim != 2 or target.ndim != 1 or len(values) != len(target):
        raise ValueError("ridge inputs have incompatible shapes")
    if len(target) == 0:
        raise ValueError("ridge requires observations")
    if alpha < 0:
        raise ValueError("alpha must be non-negative")

    scaler = RobustScaler.fit(values)
    transformed = scaler.transform(values)
    design = np.column_stack((np.ones(transformed.shape[0]), transformed))
    penalty = np.eye(design.shape[1]) * alpha
    penalty[0, 0] = 0.0
    coefficients = np.asarray(
        np.linalg.pinv(design.T @ design + penalty) @ design.T @ target,
        dtype=np.float64,
    )
    return RidgeModel(coefficients=coefficients, scaler=scaler, alpha=alpha)


def paired_absolute_error_improvement(
    actual: NDArray[np.float64],
    baseline: NDArray[np.float64],
    challenger: NDArray[np.float64],
) -> NDArray[np.float64]:
    if not (actual.shape == baseline.shape == challenger.shape):
        raise ValueError("paired prediction arrays must have identical shapes")
    return np.asarray(
        np.abs(actual - baseline) - np.abs(actual - challenger),
        dtype=np.float64,
    )


def _top_capacity(levels: Sequence[BookLevel], price: Decimal) -> Decimal:
    capacity = sum(
        (level.quantity for level in levels if level.price == price),
        Decimal("0"),
    )
    return capacity


def _require_aware(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")