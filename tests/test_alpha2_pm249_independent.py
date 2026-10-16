"""Independent Alpha-2 red-team fake-only telemetry tests for PR #249."""

import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest

from predictions_cup.config import AppSettings
from predictions_cup.external.polymarket.recorder import PolymarketRecorder


def _snapshot(token: str) -> dict[str, object]:
    return {
        "market": f"market-{token}",
        "asset_id": token,
        "timestamp": "1782753357257",
        "bids": [{"price": "0.45", "size": "10"}],
        "asks": [{"price": "0.46", "size": "10"}],
    }


def _recorder(tmp_path: Path) -> PolymarketRecorder:
    recorder = PolymarketRecorder(AppSettings.model_validate({
        "polymarket_storage_path": tmp_path / "capture.sqlite3",
        "polymarket_research_path": tmp_path / "research",
    }))
    recorder._token_ids = ("A", "B")
    recorder._subscribed_tokens = frozenset({"A", "B"})
    return recorder


def test_reason_counters_repeated_missing_price_changes_and_tick_events(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder = _recorder(tmp_path)
    now = datetime(2026, 10, 16, tzinfo=UTC)
    recorder.books.apply_full_snapshot(_snapshot("A"), now)
    monkeypatch.setattr(
        recorder.research_storage,
        "append_book_changes",
        lambda changes: len(tuple(changes)),
    )

    async def scenario() -> None:
        await recorder.handle_message(
            {"event_type": "price_change", "price_changes": [
                {"asset_id": "A", "side": "BUY", "price": "0.45", "size": "8"},
                {"asset_id": "B", "side": "BUY", "price": "0.45", "size": "8"},
                {"asset_id": "C", "side": "SELL", "price": "0.46", "size": "8"},
                {"asset_id": "B", "side": "BUY", "price": "0.45", "size": "7"},
            ]},
            now,
        )
        for token in ("B", "C"):
            await recorder.handle_message(
                {"event_type": "tick_size_change", "asset_id": token,
                 "new_tick_size": "0.01"}, now,
            )

    asyncio.run(scenario())
    h = recorder.health
    assert h.book_uninitialized_delta_count == 5
    assert h.price_change_missing_subscribed == 2
    assert h.price_change_missing_other == 1
    assert h.price_change_missing_unclassified == 0
    assert h.tick_size_missing_subscribed == 1
    assert h.tick_size_missing_other == 1
    assert h.research_book_state_accepted is False


@pytest.mark.xfail(
    strict=True,
    reason="PR #249 marks seed COMPLETE and associates new subscription epoch even if selection changes while awaiting the old REST seed",
)
def test_subscription_mutation_during_seed_cannot_mark_new_selection_complete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder = _recorder(tmp_path)
    recorder._token_ids = ("A",)
    recorder._subscribed_tokens = frozenset({"A"})
    recorder.health.subscription_epoch = 1
    started = asyncio.Event()
    resume = asyncio.Event()

    async def fake_seed(tokens: tuple[str, ...], *, invalidate: bool) -> None:
        assert tokens == ("A",)
        assert invalidate is False
        started.set()
        await resume.wait()
        recorder.books.apply_full_snapshot(
            _snapshot("A"), datetime(2026, 10, 16, tzinfo=UTC)
        )

    monkeypatch.setattr(recorder, "_seed_books", fake_seed)

    async def scenario() -> None:
        task = asyncio.create_task(recorder._before_websocket_connect())
        await started.wait()
        # Model the async periodic universe refresh changing the desired
        # subscriptions while an already-running seed awaits its old response.
        recorder._token_ids = ("B",)
        recorder._subscribed_tokens = frozenset({"B"})
        recorder.health.subscription_epoch = 2
        resume.set()
        await task

    asyncio.run(scenario())
    h = recorder.health
    assert recorder.books.initialized_tokens == frozenset({"A"})
    assert h.seed_expected_tokens == 1
    assert h.seed_initialized_tokens == 0
    # Fail closed: no epoch for a desired token set not actually initialized.
    assert h.seed_status != "COMPLETE"
    assert h.research_book_state_accepted is False
