"""Independent Alpha-2 red-team cases for PR #242. No venue or host access."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from predictions_cup.external.polymarket.models import PayloadError
from predictions_cup.external.polymarket.orderbook import OrderBookStore


def _seed(store: OrderBookStore, token: str, observed: datetime) -> None:
    store.apply_full_snapshot(
        {
            "market": f"market-{token}",
            "asset_id": token,
            "timestamp": "1782753357257",
            "hash": f"seed-{token}",
            "bids": [{"price": "0.45", "size": "10"}],
            "asks": [{"price": "0.46", "size": "10"}],
        },
        observed,
    )


def test_two_initialized_tokens_preserve_per_change_identity_and_hash() -> None:
    store = OrderBookStore()
    now = datetime(2026, 10, 16, 15, tzinfo=UTC)
    for token in ("A", "B"):
        _seed(store, token, now)
    result = store.apply_price_change(
        {
            "timestamp": "1782753358257",
            "price_changes": [
                {"asset_id": "A", "side": "BUY", "price": "0.45", "size": "9", "hash": "a-new"},
                {"asset_id": "B", "side": "SELL", "price": "0.46", "size": "8", "hash": "b-new"},
            ],
        },
        now + timedelta(seconds=1),
    )
    assert result.uninitialized_deltas == 0
    assert result.changed_tokens == frozenset({"A", "B"})
    assert [(x.token_id, x.market_id, x.book_hash) for x in result.changes] == [
        ("A", "market-A", "a-new"),
        ("B", "market-B", "b-new"),
    ]
    assert store.snapshot("A", depth=1).bids[0].size == Decimal("9")
    assert store.snapshot("B", depth=1).asks[0].size == Decimal("8")


def test_initialized_a_and_unknown_c_not_mislabelled_as_b() -> None:
    store = OrderBookStore()
    now = datetime(2026, 10, 16, 15, tzinfo=UTC)
    _seed(store, "A", now)
    _seed(store, "B", now)
    before_b = store.snapshot("B", depth=2)
    result = store.apply_price_change(
        {"price_changes": [
            {"asset_id": "A", "side": "BUY", "price": "0.45", "size": "9"},
            {"asset_id": "C", "side": "SELL", "price": "0.46", "size": "8"},
        ]},
        now,
    )
    assert [(x.token_id, x.market_id) for x in result.changes] == [("A", "market-A")]
    assert result.uninitialized_deltas == 1
    assert result.uninitialized_token_ids == frozenset({"C"})
    assert store.snapshot("B", depth=2) == before_b


def test_malformed_initialized_second_delta_preserves_all_state() -> None:
    store = OrderBookStore()
    now = datetime(2026, 10, 16, 15, tzinfo=UTC)
    for token in ("A", "B"):
        _seed(store, token, now)
    old_a, old_b = store.snapshot("A", 2), store.snapshot("B", 2)
    with pytest.raises(PayloadError):
        store.apply_price_change(
            {"price_changes": [
                {"asset_id": "A", "side": "BUY", "price": "0.45", "size": "0"},
                {"asset_id": "B", "side": "SELL", "price": "1.01", "size": "9"},
            ]},
            now + timedelta(seconds=1),
        )
    assert (store.snapshot("A", 2), store.snapshot("B", 2)) == (old_a, old_b)


@pytest.mark.xfail(
    strict=True,
    reason="PR #242 skips validation of uninitialized entries; malformed second entry is accepted",
)
def test_invalid_unknown_second_delta_must_reject_entire_frame() -> None:
    store = OrderBookStore()
    now = datetime(2026, 10, 16, 15, tzinfo=UTC)
    _seed(store, "A", now)
    old_a = store.snapshot("A", 2)
    with pytest.raises(PayloadError):
        store.apply_price_change(
            {"price_changes": [
                {"asset_id": "A", "side": "BUY", "price": "0.45", "size": "0"},
                {"asset_id": "C", "side": "SELL", "price": "1.50", "size": "-3"},
            ]},
            now + timedelta(seconds=1),
        )
    assert store.snapshot("A", 2) == old_a
