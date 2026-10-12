"""Causal feature-tape regression against original 005I one-minute equations."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from predictions_cup.replay.features_005i import causal_minute_features
from predictions_cup.replay.model import (
    BookLevel,
    QuotePayload,
    ReplayEvent,
    ReplayEventType,
    ReplaySource,
)


def _pm(at: datetime, mid: str, seq: int) -> ReplayEvent:
    price = Decimal(mid)
    return ReplayEvent(
        observed_at=at,
        source_at=at + timedelta(days=10),  # cannot influence receive-time ordering
        source=ReplaySource.POLYMARKET,
        event_type=ReplayEventType.BOOK_OBSERVATION,
        instrument_id="pm-yes",
        market_id="market-1",
        sequence=seq,
        payload=QuotePayload(
            best_bid=price - Decimal("0.01"),
            best_ask=price + Decimal("0.01"),
            quote_observed_at=at,
            bids=(BookLevel(price - Decimal("0.01"), Decimal("100")),),
            asks=(BookLevel(price + Decimal("0.01"), Decimal("100")),),
        ),
    )


def test_six_consecutive_closed_minutes_only_past_returns_and_ofi() -> None:
    start = datetime(2026, 10, 13, 12, 0, tzinfo=UTC)
    prices = ("0.60", "0.59", "0.58", "0.57", "0.56", "0.55")
    events = [
        _pm(start + timedelta(minutes=i, seconds=59), price, i + 1)
        for i, price in enumerate(prices)
    ]
    # First observation AFTER the sixth minute closes, which is availability.
    events.append(_pm(start + timedelta(minutes=6, milliseconds=100), "0.99", 7))
    rows = causal_minute_features(events, pm_token_ids={"pm-yes"})
    assert len(rows) == 1
    x = rows[0]
    assert x.minute_end == start + timedelta(minutes=6)
    assert x.available_at == start + timedelta(minutes=6, milliseconds=100)
    assert abs(x.values["ret_5m"] + 0.05) < 1e-12
    assert abs(x.values["ret_1m"] + 0.01) < 1e-12
    assert x.values["quote_events"] == 1
    assert x.values["depth_total5"] == 200
    assert x.values["ofi_depth_norm"] < 0
    assert "future_ret_5m" not in x.values


def test_sparse_previous_minutes_are_never_interpolated() -> None:
    start = datetime(2026, 10, 13, 12, 0, tzinfo=UTC)
    events = [
        _pm(
            start + timedelta(minutes=i, seconds=30),
            str(Decimal("0.7") - i * Decimal("0.01")),
            i + 1,
        )
        for i in range(7)
    ]
    assert not causal_minute_features(events, pm_token_ids={"pm-yes"})


def test_out_of_order_receive_time_determinism() -> None:
    start = datetime(2026, 10, 13, 12, 0, tzinfo=UTC)
    events = [
        _pm(start + timedelta(minutes=i, seconds=59), "0.60", i + 1)
        for i in range(7)
    ]
    a = causal_minute_features(events, pm_token_ids={"pm-yes"})
    b = causal_minute_features(reversed(events), pm_token_ids={"pm-yes"})
    assert a == b
