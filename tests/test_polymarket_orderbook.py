from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from predictions_cup.external.polymarket.orderbook import OrderBookStore


def test_full_snapshot_delta_zero_delete_and_decimal_precision() -> None:
    store = OrderBookStore()
    observed = datetime(2026, 9, 25, 0, 0, tzinfo=UTC)
    store.apply_full_snapshot(
        {
            "event_type": "book",
            "market": "0xmarket",
            "asset_id": "token-1",
            "timestamp": "1782753357257",
            "bids": [
                {"price": "0.450000000000000001", "size": "10.25"},
                {"price": "0.44", "size": "20"},
            ],
            "asks": [{"price": "0.46", "size": "15"}],
        },
        observed,
    )
    result = store.apply_price_change(
        {
            "event_type": "price_change",
            "market": "0xmarket",
            "timestamp": "1782753358257",
            "price_changes": [
                {"asset_id": "token-1", "price": "0.44", "size": "0", "side": "BUY"},
                {"asset_id": "token-1", "price": "0.43", "size": "7.5", "side": "BUY"},
                {"asset_id": "token-1", "price": "0.47", "size": "3", "side": "SELL"},
            ],
        },
        observed + timedelta(seconds=1),
    )

    snapshot = store.snapshot("token-1", depth=10)
    assert snapshot is not None
    assert result.uninitialized_deltas == 0
    assert snapshot.bids == (
        snapshot.bids[0],
        snapshot.bids[1],
    )
    assert [level.price for level in snapshot.bids] == [
        Decimal("0.450000000000000001"),
        Decimal("0.43"),
    ]
    assert [level.price for level in snapshot.asks] == [Decimal("0.46"), Decimal("0.47")]
    assert snapshot.midpoint == Decimal("0.4550000000000000005")


def test_delta_before_snapshot_is_counted_and_not_applied() -> None:
    store = OrderBookStore()
    result = store.apply_price_change(
        {
            "event_type": "price_change",
            "market": "0xmarket",
            "timestamp": "1782753358257",
            "price_changes": [
                {"asset_id": "missing-token", "price": "0.50", "size": "1", "side": "BUY"}
            ],
        },
        datetime(2026, 9, 25, tzinfo=UTC),
    )

    assert result.uninitialized_deltas == 1
    assert result.changed_tokens == frozenset()
    assert store.snapshot("missing-token", depth=10) is None


def test_reconnect_invalidation_discards_old_books() -> None:
    store = OrderBookStore()
    store.apply_full_snapshot(
        {
            "market": "0xmarket",
            "asset_id": "token-1",
            "timestamp": "1782753357257",
            "bids": [{"price": "0.45", "size": "10"}],
            "asks": [{"price": "0.46", "size": "10"}],
        },
        datetime(2026, 9, 25, tzinfo=UTC),
    )
    store.invalidate_all()

    assert store.initialized_tokens == frozenset()
