"""Independent combined PM #266 source-only fake-frame acceptance tests."""

import asyncio
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from predictions_cup.config import AppSettings
from predictions_cup.external.polymarket.models import BookChangeEvent, PayloadError
from predictions_cup.external.polymarket.recorder import PolymarketRecorder


def _snap(token: str, market: str, tag: str) -> dict[str, object]:
    return {
        "asset_id": token,
        "market": market,
        "hash": tag,
        "timestamp": "1782753357257",
        "bids": [{"price": "0.45", "size": "10"}],
        "asks": [{"price": "0.46", "size": "10"}],
    }


def test_combined_atomicity_attribution_reason_counters_and_persistence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The frame atomic fix and reason telemetry must compose without loss."""
    recorder = PolymarketRecorder(
        AppSettings.model_validate({
            "polymarket_storage_path": tmp_path / "capture.sqlite3",
            "polymarket_research_path": tmp_path / "research",
        })
    )
    recorder._token_ids = ("A", "B", "C")
    recorder._subscribed_tokens = frozenset(recorder._token_ids)
    observed = datetime(2026, 10, 16, 20, tzinfo=UTC)
    recorder.books.apply_full_snapshot(_snap("A", "market-A", "A0"), observed)
    recorder.books.apply_full_snapshot(_snap("B", "market-B", "B0"), observed)
    recorded: list[tuple[BookChangeEvent, ...]] = []

    def fake_persist(changes: Iterable[BookChangeEvent]) -> int:
        batch = tuple(changes)
        recorded.append(batch)
        return len(batch)

    monkeypatch.setattr(recorder.research_storage, "append_book_changes", fake_persist)

    async def scenario() -> None:
        # A valid and B valid changes must be preserved independently while
        # valid unknown C occurs twice and out-of-universe X occurs once.
        await recorder.handle_message({
            "event_type": "price_change",
            "timestamp": "1782753358257",
            "price_changes": [
                {"asset_id": "A", "side": "BUY", "price": "0.45", "size": "8",
                 "hash": "A1"},
                {"asset_id": "C", "side": "BUY", "price": "0.43", "size": "1"},
                {"asset_id": "B", "side": "SELL", "price": "0.46", "size": "7",
                 "hash": "B1"},
                {"asset_id": "C", "side": "SELL", "price": "0.47", "size": "2"},
                {"asset_id": "X", "side": "SELL", "price": "0.44", "size": "3"},
            ],
        }, observed + timedelta(seconds=1))
        assert len(recorded) == 1
        assert [
            (event.token_id, event.market_id, event.book_hash)
            for event in recorded[0]
        ] == [
            ("A", "market-A", "A1"),
            ("B", "market-B", "B1"),
        ]
        a = recorder.books.snapshot("A", depth=2)
        b = recorder.books.snapshot("B", depth=2)
        assert a is not None and b is not None
        assert str(a.bids[0].size) == "8"
        assert str(b.asks[0].size) == "7"

        for token in ("C", "X"):
            await recorder.handle_message({
                "event_type": "tick_size_change",
                "asset_id": token,
                "new_tick_size": "0.01",
            }, observed)

        health = recorder.health
        assert health.book_uninitialized_delta_count == 5
        assert health.price_change_missing_subscribed == 2
        assert health.price_change_missing_other == 1
        assert health.price_change_missing_unclassified == 0
        assert health.tick_size_missing_subscribed == 1
        assert health.tick_size_missing_other == 1
        assert health.research_book_state_accepted is False
        assert recorder.books.snapshot("C", 1) is None
        assert recorder.books.snapshot("X", 1) is None

        before_a = recorder.books.snapshot("A", 2)
        before_b = recorder.books.snapshot("B", 2)
        before_health = health.as_record()
        persisted_count = len(recorded)
        # Malformed *uninitialized* C following valid A and B must reject the
        # complete frame before mutations, counters or persistence.
        with pytest.raises(PayloadError):
            await recorder.handle_message({
                "event_type": "price_change",
                "price_changes": [
                    {"asset_id": "A", "side": "BUY", "price": "0.45",
                     "size": "0", "hash": "A2"},
                    {"asset_id": "B", "side": "SELL", "price": "0.46",
                     "size": "0", "hash": "B2"},
                    {"asset_id": "C", "side": "SELL", "price": "1.50",
                     "size": "-3"},
                ],
            }, observed + timedelta(seconds=2))
        assert recorder.books.snapshot("A", 2) == before_a
        assert recorder.books.snapshot("B", 2) == before_b
        assert health.as_record() == before_health
        assert len(recorded) == persisted_count

    asyncio.run(scenario())
