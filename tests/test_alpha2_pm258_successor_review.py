"""Independent Alpha-2 frozen #255 regression replay against successor #258."""

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from predictions_cup.config import AppSettings
from predictions_cup.external.polymarket.recorder import PolymarketRecorder
from predictions_cup.external.polymarket.health import IngestionHealth
from predictions_cup.external.polymarket.websocket import MarketWebSocket


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
        with pytest.raises(RuntimeError, match="changed during REST seed"):
            await task
        assert recorder.books.initialized_tokens == frozenset()
        assert recorder.health.seed_status == "STALE"
        assert recorder.health.seed_complete_epoch == 0
        assert recorder.health.connection_epoch == 0
        assert recorder.websocket._seeded_tokens == frozenset()
        # Genuine current-generation B re-seed after failed A attempt.
        async def current_b_seed(tokens: tuple[str, ...], *, invalidate: bool) -> None:
            assert tokens == ("B",)
            assert invalidate is False
            recorder.books.apply_full_snapshot(
                _snapshot("B"), datetime(2026, 10, 16, tzinfo=UTC)
            )

        monkeypatch.setattr(recorder, "_seed_books", current_b_seed)
        await recorder._before_websocket_connect()
        assert recorder.books.initialized_tokens == frozenset({"B"})
        recorder.websocket._desired_tokens = frozenset({"B"})
        recorder.websocket._require_current_seed(frozenset({"B"}))
        recorder.websocket._mark_connection_established()

    asyncio.run(scenario())
    h = recorder.health
    assert h.seed_expected_tokens == 1
    assert h.seed_initialized_tokens == 1
    assert h.seed_complete_epoch == 2
    assert h.seed_subscription_epoch == 2
    assert h.seed_status == "COMPLETE"
    assert h.connection_epoch == 1
    assert h.connection_seed_epoch == 2
    assert h.connection_subscription_epoch == 2
    assert recorder.websocket._seeded_tokens == frozenset({"B"})
    assert h.research_book_state_accepted is False


def test_failed_reseed_invalidates_previous_ws_seed_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder = _recorder(tmp_path)
    recorder._token_ids = ("A",)
    recorder._subscribed_tokens = frozenset({"A"})
    recorder.health.subscription_epoch = 1
    now = datetime(2026, 10, 16, tzinfo=UTC)

    async def full_a(tokens: tuple[str, ...], *, invalidate: bool) -> None:
        assert tokens == ("A",)
        assert invalidate is False
        recorder.books.apply_full_snapshot(_snapshot("A"), now)

    monkeypatch.setattr(recorder, "_seed_books", full_a)
    asyncio.run(recorder._before_websocket_connect())
    assert recorder.websocket._seeded_tokens == frozenset({"A"})
    assert recorder.health.seed_status == "COMPLETE"

    async def partial_b(tokens: tuple[str, ...], *, invalidate: bool) -> None:
        assert tokens == ("B",)
        assert invalidate is False
        raise RuntimeError("offline injected partial seed")

    recorder._token_ids = ("B",)
    recorder._subscribed_tokens = frozenset({"B"})
    recorder.health.subscription_epoch = 2
    monkeypatch.setattr(recorder, "_seed_books", partial_b)
    with pytest.raises(RuntimeError, match="injected partial"):
        asyncio.run(recorder._before_websocket_connect())
    assert recorder.websocket._seeded_tokens == frozenset()
    assert recorder.books.initialized_tokens == frozenset()
    assert recorder.health.seed_status == "FAILED"
    assert recorder.health.seed_attempt_epoch == 2
    assert recorder.health.seed_complete_epoch == 1
    assert recorder.health.seed_completed_at is None
    assert recorder.health.research_book_state_accepted is False
    recorder.websocket._desired_tokens = frozenset({"B"})
    with pytest.raises(RuntimeError, match="changed after REST seed"):
        recorder.websocket._require_current_seed(frozenset({"B"}))


def test_subscription_change_while_ws_send_awaited_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Run the actual subscribe coroutine with an entirely fake session/socket."""
    health = IngestionHealth()
    transport = MarketWebSocket("wss://no-network.invalid/ws", health)
    health.seed_attempt_epoch = 1
    health.seed_complete_epoch = 1
    health.seed_subscription_epoch = 1
    health.subscription_epoch = 1
    health.seed_status = "COMPLETE"
    health.seed_expected_tokens = 1
    health.seed_initialized_tokens = 1
    transport._seeded_tokens = frozenset({"A"})
    transport._desired_tokens = frozenset({"A"})

    class FakeWebSocket:
        def __init__(self) -> None:
            self.started = asyncio.Event()
            self.resume = asyncio.Event()
            self.exited = asyncio.Event()
            self.sent: list[dict[str, object]] = []

        async def __aenter__(self) -> "FakeWebSocket":
            return self

        async def __aexit__(self, *args: object) -> None:
            self.exited.set()

        async def send_json(self, packet: dict[str, object]) -> None:
            self.sent.append(packet)
            self.started.set()
            await self.resume.wait()

    fake_ws = FakeWebSocket()

    class FakeSession:
        def __init__(self, **kwargs: object) -> None:
            del kwargs

        async def __aenter__(self) -> "FakeSession":
            return self

        async def __aexit__(self, *args: object) -> None:
            del args

        def ws_connect(self, *args: object, **kwargs: object) -> FakeWebSocket:
            del args, kwargs
            return fake_ws

    monkeypatch.setattr(
        "predictions_cup.external.polymarket.websocket.aiohttp.ClientSession",
        FakeSession,
    )

    async def before_connect() -> None:
        return None

    async def handler(payload: dict[str, Any], observed: datetime) -> None:
        del payload, observed

    async def scenario() -> None:
        task = asyncio.create_task(transport.run(handler, before_connect))
        try:
            await asyncio.wait_for(fake_ws.started.wait(), timeout=2)
            # Like refresh_universe: increments epoch before it can acquire
            # the subscription lock held during the awaited WS send.
            health.subscription_epoch = 2
            fake_ws.resume.set()
            await asyncio.wait_for(fake_ws.exited.wait(), timeout=2)
            assert transport.health.connection_epoch == 0
            assert transport.health.websocket_connected is False
            assert "changed after REST seed" in (
                transport.health.last_reconnect_reason or ""
            )
            assert fake_ws.sent == [
                {"assets_ids": ["A"], "type": "market"}
            ]
        finally:
            transport.stop()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())
    assert health.research_book_state_accepted is False
