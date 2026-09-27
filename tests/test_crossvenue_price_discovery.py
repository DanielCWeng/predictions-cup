from datetime import UTC, datetime, timedelta
from decimal import Decimal

import numpy as np

from predictions_cup.learning.crossvenue_price_discovery import (
    QuotePoint,
    QuoteSeries,
    fit_ridge,
    paired_absolute_error_improvement,
    synthetic_union_quote,
)
from predictions_cup.replay.model import BookLevel


def _quote(at: datetime, bid: str, ask: str, size: str = "5") -> QuotePoint:
    return QuotePoint(
        observed_at=at,
        best_bid=Decimal(bid),
        best_ask=Decimal(ask),
        bids=(BookLevel(Decimal(bid), Decimal(size)),),
        asks=(BookLevel(Decimal(ask), Decimal(size)),),
    )


def test_quote_series_asof_never_uses_future_state() -> None:
    base = datetime(2026, 9, 27, 12, tzinfo=UTC)
    series = QuoteSeries(
        (
            _quote(base, "0.40", "0.42"),
            _quote(base + timedelta(seconds=10), "0.44", "0.46"),
        )
    )

    asof = series.asof(base + timedelta(seconds=9))
    after = series.first_after(base + timedelta(seconds=9))
    assert asof is not None
    assert after is not None
    assert asof.best_bid == Decimal("0.40")
    assert after.best_bid == Decimal("0.44")
    assert series.count_updates(base, base + timedelta(seconds=10)) == 1


def test_synthetic_union_quote_is_size_aware_and_fails_closed() -> None:
    at = datetime(2026, 9, 27, 12, tzinfo=UTC)
    first = _quote(at, "0.20", "0.22", "3")
    second = _quote(at - timedelta(seconds=1), "0.30", "0.33", "2")

    synthetic = synthetic_union_quote(
        (first, second),
        at=at,
        trade_size=Decimal("1"),
        max_quote_age=timedelta(seconds=5),
    )
    assert synthetic is not None
    assert synthetic.best_bid == Decimal("0.50")
    assert synthetic.best_ask == Decimal("0.55")
    assert synthetic.executable_size == Decimal("2")

    assert (
        synthetic_union_quote(
            (first, second),
            at=at,
            trade_size=Decimal("3"),
            max_quote_age=timedelta(seconds=5),
        )
        is None
    )
    assert (
        synthetic_union_quote(
            (first, second),
            at=at + timedelta(seconds=10),
            trade_size=Decimal("1"),
            max_quote_age=timedelta(seconds=5),
        )
        is None
    )


def test_ridge_scaling_is_fit_only_on_training_matrix() -> None:
    x_train = np.array([[0.0], [1.0], [2.0], [3.0]])
    y_train = np.array([0.0, 1.0, 2.0, 3.0])
    model = fit_ridge(x_train, y_train, alpha=0.001)

    assert model.scaler.median[0] == 1.5
    prediction = model.predict(np.array([[100.0]]))
    assert prediction.shape == (1,)


def test_paired_error_improvement_positive_when_challenger_is_better() -> None:
    actual = np.array([1.0, 2.0])
    baseline = np.array([0.0, 0.0])
    challenger = np.array([0.9, 1.9])
    improvement = paired_absolute_error_improvement(actual, baseline, challenger)
    assert np.all(improvement > 0)