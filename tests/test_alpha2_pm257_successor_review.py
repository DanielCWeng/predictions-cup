"""Independent Alpha-2 replay of frozen #254 tests against corrected #257 source."""

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from predictions_cup.config import AppSettings
from predictions_cup.external.polymarket.models import PayloadError
from predictions_cup.external.polymarket.orderbook import OrderBookStore
from predictions_cup.external.polymarket.recorder import PolymarketRecorder


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
    snapshot_a = store.snapshot("A", depth=1)
    snapshot_b = store.snapshot("B", depth=1)
    assert snapshot_a is not None and snapshot_b is not None
    assert snapshot_a.bids[0].size == Decimal("9")
    assert snapshot_b.asks[0].size == Decimal("8")


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


def test_repeated_valid_unknown_entries_are_counted_not_applied() -> None:
    store = OrderBookStore()
    now = datetime(2026, 10, 16, 15, tzinfo=UTC)
    _seed(store, "A", now)
    res = store.apply_price_change(
        {"price_changes": [
            {"asset_id": "A", "side": "BUY", "price": "0.45", "size": "7"},
            {"asset_id": "C", "side": "BUY", "price": "0.42", "size": "2"},
            {"asset_id": "C", "side": "SELL", "price": "0.50", "size": "3"},
        ]},
        now,
    )
    assert len(res.changes) == 1
    assert res.changes[0].token_id == "A"
    assert res.uninitialized_deltas == 2
    assert res.uninitialized_token_ids == frozenset({"C"})
    assert store.snapshot("C", 1) is None


def test_invalid_second_nonobject_rejects_first_without_mutation() -> None:
    store = OrderBookStore()
    now = datetime(2026, 10, 16, 15, tzinfo=UTC)
    _seed(store, "A", now)
    before = store.snapshot("A", 2)
    with pytest.raises(PayloadError):
        store.apply_price_change(
            {"price_changes": [
                {"asset_id": "A", "side": "BUY", "price": "0.45", "size": "0"},
                "bad-second-entry",
            ]},
            now + timedelta(seconds=1),
        )
    assert store.snapshot("A", 2) == before


def test_malformed_unknown_c_does_not_call_persistence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder = PolymarketRecorder(AppSettings.model_validate({
        "polymarket_storage_path": tmp_path / "capture.sqlite3",
        "polymarket_research_path": tmp_path / "research",
    }))
    now = datetime(2026, 10, 16, 15, tzinfo=UTC)
    recorder.books.apply_full_snapshot({
        "market": "market-A", "asset_id": "A",
        "bids": [{"price": "0.45", "size": "10"}],
        "asks": [{"price": "0.46", "size": "10"}],
    }, now)
    before = recorder.books.snapshot("A", 2)
    persisted: list[object] = []

    def record(changes: object) -> int:
        persisted.append(changes)
        return 1

    monkeypatch.setattr(recorder.research_storage, "append_book_changes", record)

    async def scenario() -> None:
        with pytest.raises(PayloadError):
            await recorder.handle_message(
                {"event_type": "price_change", "price_changes": [
                    {"asset_id": "A", "side": "BUY", "price": "0.45", "size": "0"},
                    {"asset_id": "C", "side": "SELL", "price": "1.50", "size": "-3"},
                ]},
                now + timedelta(seconds=1),
            )

    asyncio.run(scenario())
    assert recorder.books.snapshot("A", 2) == before
    assert persisted == []
    assert recorder.health.research_book_state_accepted is False
