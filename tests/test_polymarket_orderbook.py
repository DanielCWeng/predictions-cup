from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from predictions_cup.external.polymarket.models import PayloadError
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
    assert result.uninitialized_token_ids == frozenset()
    assert len(result.changes) == 3
    assert result.changes[0].token_id == "token-1"
    assert result.changes[0].market_id == "0xmarket"
    assert result.changes[0].side == "BUY"
    assert result.changes[0].price == Decimal("0.44")
    assert result.changes[0].size == Decimal("0")
    assert result.changes[0].observed_at == observed + timedelta(seconds=1)
    assert result.changes[-1].best_bid == Decimal("0.450000000000000001")
    assert result.changes[-1].best_ask == Decimal("0.46")
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
    assert result.uninitialized_token_ids == frozenset({"missing-token"})
    assert result.changed_tokens == frozenset()
    assert result.changes == ()
    assert store.snapshot("missing-token", depth=10) is None


def test_repeated_book_decimals_are_shared_across_tokens() -> None:
    store = OrderBookStore()
    observed = datetime(2026, 9, 25, 12, 0, tzinfo=UTC)
    for token_id in ("token-1", "token-2"):
        store.apply_full_snapshot(
            {
                "asset_id": token_id,
                "market": "market-1",
                "bids": [{"price": "0.45", "size": "10"}],
                "asks": [{"price": "0.46", "size": "12"}],
            },
            observed,
        )

    first = store._books["token-1"]
    second = store._books["token-2"]
    assert next(iter(first.bids)) is next(iter(second.bids))
    assert next(iter(first.bids.values())) is next(iter(second.bids.values()))
    assert next(iter(first.asks)) is next(iter(second.asks))
    assert next(iter(first.asks.values())) is next(iter(second.asks.values()))


def test_last_trade_price_updates_current_book_state() -> None:
    store = OrderBookStore()
    observed = datetime(2026, 9, 25, tzinfo=UTC)
    store.apply_full_snapshot(
        {
            "market": "0xmarket",
            "asset_id": "token-1",
            "timestamp": "1782753357257",
            "bids": [{"price": "0.45", "size": "10"}],
            "asks": [{"price": "0.46", "size": "10"}],
            "last_trade_price": "0.44",
        },
        observed,
    )

    assert store.apply_last_trade_price(
        {
            "event_type": "last_trade_price",
            "market": "0xmarket",
            "asset_id": "token-1",
            "price": "0.455",
            "timestamp": "1782753358257",
        },
        observed + timedelta(seconds=1),
    )

    snapshot = store.snapshot("token-1", depth=10)
    assert snapshot is not None
    assert snapshot.last_trade_price == Decimal("0.455")


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


def test_invalid_later_price_change_does_not_partially_mutate_book() -> None:
    store = OrderBookStore()
    observed = datetime(2026, 10, 16, 12, 0, tzinfo=UTC)
    store.apply_full_snapshot(
        {
            "market": "0xmarket",
            "asset_id": "token-1",
            "timestamp": "1782753357257",
            "hash": "original",
            "bids": [{"price": "0.45", "size": "10"}],
            "asks": [{"price": "0.46", "size": "10"}],
        },
        observed,
    )
    before = store.snapshot("token-1", depth=10)
    with pytest.raises(PayloadError, match="outside valid range"):
        store.apply_price_change(
            {
                "market": "0xmarket",
                "timestamp": "1782753358257",
                "price_changes": [
                    {
                        "asset_id": "token-1", "side": "BUY",
                        "price": "0.45", "size": "0", "hash": "changed",
                    },
                    {
                        "asset_id": "token-1", "side": "SELL",
                        "price": "1.01", "size": "1",
                    },
                ],
            },
            observed + timedelta(seconds=1),
        )
    assert store.snapshot("token-1", depth=10) == before

@pytest.mark.parametrize(
    ("second_token_id", "expected_change_token_ids", "expected_missing"),
    [
        ("token-2", ("token-1", "token-2"), 0),
        ("outside-universe", ("token-1",), 1),
    ],
)
def test_atomic_multitoken_batch_preserves_each_persisted_token_id(
    second_token_id: str,
    expected_change_token_ids: tuple[str, ...],
    expected_missing: int,
) -> None:
    store = OrderBookStore()
    observed = datetime(2026, 10, 16, 12, 0, tzinfo=UTC)
    for token_id in ("token-1", "token-2"):
        store.apply_full_snapshot(
            {
                "market": f"market-{token_id}",
                "asset_id": token_id,
                "bids": [{"price": "0.45", "size": "10"}],
                "asks": [{"price": "0.46", "size": "10"}],
            },
            observed,
        )

    result = store.apply_price_change(
        {
            "event_type": "price_change",
            "timestamp": "1782753358257",
            "price_changes": [
                {"asset_id": "token-1", "price": "0.45", "size": "8", "side": "BUY"},
                {
                    "asset_id": second_token_id,
                    "price": "0.46",
                    "size": "8",
                    "side": "SELL",
                },
            ],
        },
        observed + timedelta(seconds=1),
    )

    assert result.uninitialized_deltas == expected_missing
    assert result.uninitialized_token_ids == (
        frozenset({"outside-universe"}) if expected_missing else frozenset()
    )
    assert result.changed_tokens == frozenset(expected_change_token_ids)
    assert tuple(change.token_id for change in result.changes) == (
        expected_change_token_ids
    )
    assert tuple(change.market_id for change in result.changes) == tuple(
        f"market-{token_id}" for token_id in expected_change_token_ids
    )
    assert store.snapshot("outside-universe", depth=1) is None


@pytest.mark.parametrize(
    ("unknown_side", "unknown_price", "unknown_size"),
    [
        ("SELL", "1.50", "-3"),
        ("NOT_A_SIDE", "0.50", "3"),
        ("SELL", "0.50", "-3"),
    ],
)
def test_malformed_uninitialized_second_delta_cannot_mutate_seeded_book(
    unknown_side: str, unknown_price: str, unknown_size: str,
) -> None:
    store = OrderBookStore()
    observed = datetime(2026, 10, 16, 15, tzinfo=UTC)
    store.apply_full_snapshot(
        {
            "market": "market-A",
            "asset_id": "A",
            "hash": "seed-A",
            "bids": [{"price": "0.45", "size": "10"}],
            "asks": [{"price": "0.46", "size": "10"}],
        },
        observed,
    )
    before = store.snapshot("A", depth=2)
    with pytest.raises(PayloadError):
        store.apply_price_change(
            {
                "price_changes": [
                    {"asset_id": "A", "side": "BUY", "price": "0.45", "size": "0"},
                    {
                        "asset_id": "C",
                        "side": unknown_side,
                        "price": unknown_price,
                        "size": unknown_size,
                    },
                ]
            },
            observed + timedelta(seconds=1),
        )
    assert store.snapshot("A", depth=2) == before
    assert store.snapshot("C", depth=2) is None


def test_valid_uninitialized_tokens_still_count_every_entry_after_validation() -> None:
    store = OrderBookStore()
    observed = datetime(2026, 10, 16, tzinfo=UTC)
    store.apply_full_snapshot(
        {
            "market": "market-A",
            "asset_id": "A",
            "bids": [{"price": "0.45", "size": "10"}],
            "asks": [{"price": "0.46", "size": "10"}],
        },
        observed,
    )
    result = store.apply_price_change(
        {
            "price_changes": [
                {"asset_id": "A", "side": "BUY", "price": "0.45", "size": "8"},
                {"asset_id": "C", "side": "SELL", "price": "0.46", "size": "8"},
                {"asset_id": "C", "side": "BUY", "price": "0.45", "size": "7"},
            ]
        },
        observed,
    )
    assert [event.token_id for event in result.changes] == ["A"]
    assert result.uninitialized_deltas == 2
    assert result.uninitialized_token_ids == frozenset({"C"})
    assert store.snapshot("C", depth=2) is None



@pytest.mark.parametrize("depth", [1, 20, 256, 2_000])
def test_large_snapshot_sorted_price_keys_match_full_tuple_sort(
    depth: int,
) -> None:
    """Exact top-level price/size ordering, including partial and full depth."""
    observed = datetime(2026, 10, 21, 8, tzinfo=UTC)
    store = OrderBookStore()
    # An intentionally mixed insertion order reproduces the slow case of
    # the old full-item sort without introducing any lossy float conversion.
    values = [Decimal(index) / Decimal(10_000) for index in range(1, 1_025)]
    bid_prices = values[::2] + values[1::2]
    ask_prices = list(reversed(values))
    store.apply_full_snapshot(
        {
            "market": "deep-market",
            "asset_id": "deep-token",
            "hash": "initial",
            "bids": [
                {"price": str(price), "size": str(index + 1)}
                for index, price in enumerate(bid_prices)
            ],
            "asks": [
                {"price": str(price), "size": str(index + 2)}
                for index, price in enumerate(ask_prices)
            ],
        },
        observed,
    )
    book = store._books["deep-token"]
    snapshot = store.snapshot("deep-token", depth)
    assert snapshot is not None
    assert snapshot.book_hash == "initial"
    assert tuple((level.price, level.size) for level in snapshot.bids) == tuple(
        sorted(book.bids.items(), reverse=True)[:depth]
    )
    assert tuple((level.price, level.size) for level in snapshot.asks) == tuple(
        sorted(book.asks.items())[:depth]
    )

    # Delta insertion, existing-level replacement and deletion must remain
    # visible at the precise next snapshot, with the same original provenance.
    result = store.apply_price_change(
        {
            "timestamp": "1792573200000",
            "price_changes": [
                {"asset_id": "deep-token", "side": "BUY",
                 "price": "0.999999999999999999", "size": "1.2345", "hash": "next"},
                {"asset_id": "deep-token", "side": "SELL",
                 "price": "0.000001", "size": "7"},
                {"asset_id": "deep-token", "side": "SELL",
                 "price": str(ask_prices[-1]), "size": "0"},
            ],
        },
        observed + timedelta(milliseconds=1),
    )
    assert result.uninitialized_deltas == 0
    current = store.snapshot("deep-token", depth)
    assert current is not None
    assert current.book_hash == "next"
    assert current.observed_at == observed + timedelta(milliseconds=1)
    assert tuple((level.price, level.size) for level in current.bids) == tuple(
        sorted(book.bids.items(), reverse=True)[:depth]
    )
    assert tuple((level.price, level.size) for level in current.asks) == tuple(
        sorted(book.asks.items())[:depth]
    )
    assert current.bids[0].price == Decimal("0.999999999999999999")
    assert current.asks[0].price == Decimal("0.000001")



def test_resident_level_counts_track_delta_delete_and_invalidation() -> None:
    store = OrderBookStore()
    observed = datetime(2026, 10, 21, 9, tzinfo=UTC)
    assert store.resident_level_counts() == (0, 0)
    store.apply_full_snapshot(
        {
            "market": "m",
            "asset_id": "A",
            "bids": [{"price": "0.42", "size": "10"},
                     {"price": "0.41", "size": "5"}],
            "asks": [{"price": "0.45", "size": "3"}],
        },
        observed,
    )
    store.apply_full_snapshot(
        {
            "market": "m",
            "asset_id": "B",
            "bids": [{"price": "0.35", "size": "10"}],
            "asks": [{"price": "0.37", "size": "4"}],
        },
        observed,
    )
    assert store.resident_level_counts() == (3, 2)
    store.apply_price_change(
        {
            "price_changes": [
                {"asset_id": "A", "side": "BUY", "price": "0.42", "size": "0"},
                {"asset_id": "B", "side": "SELL", "price": "0.38", "size": "1"},
            ]
        },
        observed + timedelta(seconds=1),
    )
    assert store.resident_level_counts() == (2, 3)
    store.invalidate({"A"})
    assert store.resident_level_counts() == (1, 2)
    store.invalidate_all()
    assert store.resident_level_counts() == (0, 0)

def test_unseeded_price_deltas_validate_without_polluting_price_caches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reject malformed unknown deltas but never intern valid unused levels."""
    from predictions_cup.external.polymarket import orderbook as orderbook_module

    store = OrderBookStore()
    observed = datetime(2026, 10, 21, 12, tzinfo=UTC)

    def forbidden(value: Decimal) -> Decimal:
        raise AssertionError(f"unseeded delta must not intern {value}")

    monkeypatch.setattr(orderbook_module, "_shared_price", forbidden)
    monkeypatch.setattr(orderbook_module, "_shared_size", forbidden)
    valid = store.apply_price_change(
        {
            "price_changes": [
                {"asset_id": "unknown-A", "side": "BUY",
                 "price": "0.450000000000001", "size": "1234.56789"},
                {"asset_id": "unknown-B", "side": "SELL",
                 "price": "0.50", "size": "0"},
            ]
        },
        observed,
    )
    assert valid.uninitialized_deltas == 2
    assert valid.uninitialized_token_ids == frozenset({"unknown-A", "unknown-B"})
    assert not valid.changes
    with pytest.raises(PayloadError, match="outside valid range"):
        store.apply_price_change(
            {
                "price_changes": [
                    {"asset_id": "unknown-A", "side": "BUY",
                     "price": "0.45", "size": "1"},
                    {"asset_id": "unknown-B", "side": "SELL",
                     "price": "1.01", "size": "1"},
                ]
            },
            observed,
        )
    assert store.resident_level_counts() == (0, 0)
