from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pyarrow.parquet as pq
import pytest

from predictions_cup.config import AppSettings
from predictions_cup.external.polymarket.client import ObservedBookBatch
from predictions_cup.external.polymarket.recorder import (
    PolymarketRecorder,
    _run_recorder_until_stopped,
    main,
)


def _book_payload(token_id: str, market_id: str = "0xmarket") -> dict[str, object]:
    return {
        "market": market_id,
        "asset_id": token_id,
        "timestamp": "1782753357257",
        "bids": [{"price": "0.45", "size": "10"}],
        "asks": [{"price": "0.46", "size": "10"}],
    }


def test_supervised_recorder_fails_closed_without_explicit_universe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PREDICTIONS_CUP_POLYMARKET_CAPTURE_ENABLED", "true")
    monkeypatch.delenv("PREDICTIONS_CUP_POLYMARKET_SUPERVISED_IDS", raising=False)

    assert main(["--runtime-env-only", "--require-explicit-universe"]) == 2


def test_rest_seed_preserves_batch_level_observation_times(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = AppSettings.model_validate(
        {
            "polymarket_storage_path": tmp_path / "capture.sqlite3",
            "polymarket_research_path": tmp_path / "research",
        }
    )
    recorder = PolymarketRecorder(settings)
    first_at = datetime(2026, 9, 25, 0, 0, 0, tzinfo=UTC)
    second_at = first_at + timedelta(milliseconds=750)

    async def fake_batches(token_ids: tuple[str, ...]) -> tuple[ObservedBookBatch, ...]:
        assert token_ids == ("token-1", "token-2")
        return (
            ObservedBookBatch((_book_payload("token-1"),), first_at),
            ObservedBookBatch((_book_payload("token-2"),), second_at),
        )

    monkeypatch.setattr(recorder.clob, "fetch_book_batches", fake_batches)
    asyncio.run(recorder._seed_books(("token-1", "token-2"), invalidate=False))

    first = recorder.books.snapshot("token-1", depth=1)
    second = recorder.books.snapshot("token-2", depth=1)
    assert first is not None and second is not None
    assert first.observed_at == first_at
    assert second.observed_at == second_at
    assert recorder.health.last_valid_book_update_at == second_at


def test_one_second_panel_is_lean_and_depth_uses_slower_cadence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = AppSettings.model_validate(
        {
            "polymarket_storage_path": tmp_path / "capture.sqlite3",
            "polymarket_research_path": tmp_path / "research",
            "polymarket_depth_snapshot_interval_seconds": 60,
        }
    )
    recorder = PolymarketRecorder(settings)
    recorder.storage.initialize()
    recorder.research_storage.initialize()
    start = datetime(2026, 9, 25, 0, 0, 0, tzinfo=UTC)
    recorder.books.apply_full_snapshot(_book_payload("token-1"), start)
    times = iter((start, start + timedelta(seconds=1)))
    monkeypatch.setattr(
        "predictions_cup.external.polymarket.recorder.utc_now",
        lambda: next(times),
    )

    assert asyncio.run(recorder.record_snapshot_once()) == 1
    assert asyncio.run(recorder.record_snapshot_once()) == 1
    recorder.research_storage.flush_all()

    panel_files = list((settings.polymarket_research_path / "observations").rglob("*.parquet"))
    depth_files = list(
        (settings.polymarket_research_path / "depth_snapshots").rglob("*.parquet")
    )
    assert sum(pq.ParquetFile(path).metadata.num_rows for path in panel_files) == 2
    assert sum(pq.ParquetFile(path).metadata.num_rows for path in depth_files) == 1


def test_price_changes_are_durably_persisted_with_event_and_observation_time(
    tmp_path: Path,
) -> None:
    settings = AppSettings.model_validate(
        {
            "polymarket_storage_path": tmp_path / "capture.sqlite3",
            "polymarket_research_path": tmp_path / "research",
        }
    )
    recorder = PolymarketRecorder(settings)
    recorder.storage.initialize()
    recorder.research_storage.initialize()
    seeded_at = datetime(2026, 9, 25, 0, 0, 0, tzinfo=UTC)
    observed_at = seeded_at + timedelta(milliseconds=500)
    recorder.books.apply_full_snapshot(_book_payload("token-1"), seeded_at)

    asyncio.run(
        recorder.handle_message(
            {
                "event_type": "price_change",
                "market": "0xmarket",
                "timestamp": "1782753358257",
                "price_changes": [
                    {
                        "asset_id": "token-1",
                        "side": "BUY",
                        "price": "0.44",
                        "size": "7",
                        "best_bid": "0.45",
                        "best_ask": "0.46",
                        "hash": "hash-after-change",
                    }
                ],
            },
            observed_at,
        )
    )

    recorder.research_storage.flush_all()
    files = list(
        (settings.polymarket_research_path / "book_changes").rglob("*.parquet")
    )
    row = pq.read_table(files[0]).to_pylist()[0]

    assert (
        row["token_id"],
        row["market_id"],
        row["side"],
        row["price"],
        row["size"],
    ) == ("token-1", "0xmarket", "BUY", "0.44", "7")
    assert row["source_timestamp"] != row["observed_at"]
    assert row["observed_at"] == observed_at
    assert (row["best_bid"], row["best_ask"], row["book_hash"]) == (
        "0.45",
        "0.46",
        "hash-after-change",
    )


def test_reconnect_hook_invalidates_old_state_and_reseeds_from_rest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = AppSettings.model_validate(
        {
            "polymarket_storage_path": tmp_path / "capture.sqlite3",
            "polymarket_research_path": tmp_path / "research",
        }
    )
    recorder = PolymarketRecorder(settings)
    old_at = datetime(2026, 9, 25, 0, 0, 0, tzinfo=UTC)
    new_at = old_at + timedelta(seconds=5)
    recorder.books.apply_full_snapshot(_book_payload("token-1", "old-market"), old_at)
    recorder._token_ids = ("token-1",)

    async def fake_batches(token_ids: tuple[str, ...]) -> tuple[ObservedBookBatch, ...]:
        assert token_ids == ("token-1",)
        return (ObservedBookBatch((_book_payload("token-1", "new-market"),), new_at),)

    monkeypatch.setattr(recorder.clob, "fetch_book_batches", fake_batches)
    asyncio.run(recorder._before_websocket_connect())

    snapshot = recorder.books.snapshot("token-1", depth=1)
    assert snapshot is not None
    assert snapshot.market_id == "new-market"
    assert snapshot.observed_at == new_at


def test_last_trade_event_updates_snapshot_state_and_trade_storage(tmp_path: Path) -> None:
    settings = AppSettings.model_validate(
        {
            "polymarket_storage_path": tmp_path / "capture.sqlite3",
            "polymarket_research_path": tmp_path / "research",
        }
    )
    recorder = PolymarketRecorder(settings)
    recorder.storage.initialize()
    seeded_at = datetime(2026, 9, 25, 0, 0, 0, tzinfo=UTC)
    observed_at = seeded_at + timedelta(seconds=1)
    recorder.books.apply_full_snapshot(_book_payload("token-1"), seeded_at)

    asyncio.run(
        recorder.handle_message(
            {
                "event_type": "last_trade_price",
                "market": "0xmarket",
                "asset_id": "token-1",
                "price": "0.455",
                "size": "2",
                "side": "BUY",
                "timestamp": "1782753358257",
            },
            observed_at,
        )
    )

    snapshot = recorder.books.snapshot("token-1", depth=1)
    assert snapshot is not None
    assert str(snapshot.last_trade_price) == "0.455"
    recorder.research_storage.flush_all()
    files = list((settings.polymarket_research_path / "trades").rglob("*.parquet"))
    assert sum(pq.ParquetFile(path).metadata.num_rows for path in files) == 1


def test_snapshot_storage_failure_is_surfaced(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = AppSettings.model_validate(
        {
            "polymarket_storage_path": tmp_path / "capture.sqlite3",
            "polymarket_research_path": tmp_path / "research",
        }
    )
    recorder = PolymarketRecorder(settings)
    recorder.storage.initialize()
    recorder.books.apply_full_snapshot(
        {
            "market": "0xmarket",
            "asset_id": "token-1",
            "timestamp": "1782753357257",
            "bids": [{"price": "0.45", "size": "10"}],
            "asks": [{"price": "0.46", "size": "10"}],
        },
        datetime(2026, 9, 25, tzinfo=UTC),
    )

    def fail(*args: object, **kwargs: object) -> int:
        del args, kwargs
        raise RuntimeError("disk full")

    monkeypatch.setattr(recorder.research_storage, "append_snapshots", fail)

    with pytest.raises(RuntimeError, match="disk full"):
        asyncio.run(recorder.record_snapshot_once())
    assert recorder.health.storage_failures == 1
    assert recorder.health.snapshot_last_status is not None
    assert recorder.health.snapshot_last_status.startswith("ERROR:")


def test_initial_gamma_failure_remains_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = AppSettings.model_validate(
        {
            "polymarket_storage_path": tmp_path / "capture.sqlite3",
            "polymarket_research_path": tmp_path / "research",
        }
    )
    recorder = PolymarketRecorder(settings)

    async def fail_discovery() -> object:
        raise RuntimeError("startup Gamma unavailable")

    monkeypatch.setattr(recorder.gamma, "discover_active_markets", fail_discovery)

    with pytest.raises(RuntimeError, match="startup Gamma unavailable"):
        asyncio.run(recorder.initialize())


def test_periodic_gamma_refresh_fails_soft_and_later_recovers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = AppSettings.model_validate(
        {
            "polymarket_storage_path": tmp_path / "capture.sqlite3",
            "polymarket_research_path": tmp_path / "research",
        }
    )
    recorder = PolymarketRecorder(settings)
    recorder._token_ids = ("token-existing",)
    recorder._market_count = 1
    calls = 0
    selected_market = object()

    async def fake_discovery() -> object:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("rate limited")
        return SimpleNamespace(markets=(selected_market,), parse_failures=0)

    def fake_select(markets: object) -> object:
        del markets
        return SimpleNamespace(
            markets=(selected_market,),
            token_ids=("token-existing",),
        )

    def fake_upsert(*args: object) -> None:
        del args

    async def fake_set_tokens(token_ids: tuple[str, ...]) -> None:
        assert token_ids == ("token-existing",)

    monkeypatch.setattr(recorder.gamma, "discover_active_markets", fake_discovery)
    monkeypatch.setattr(recorder.selector, "select", fake_select)
    monkeypatch.setattr(recorder.storage, "upsert_markets", fake_upsert)
    monkeypatch.setattr(recorder.websocket, "set_tokens", fake_set_tokens)

    async def scenario() -> None:
        first = await recorder.refresh_universe(fail_soft_if_initialized=True)
        assert first is False
        assert recorder._token_ids == ("token-existing",)
        assert recorder._market_count == 1
        assert recorder.health.gamma_last_status is not None
        assert recorder.health.gamma_last_status.startswith("ERROR:")

        second = await recorder.refresh_universe(fail_soft_if_initialized=True)
        assert second is True
        assert recorder.health.gamma_last_status == "OK"
        assert calls == 2

    asyncio.run(scenario())


def test_periodic_refresh_does_not_hide_local_storage_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = AppSettings.model_validate(
        {
            "polymarket_storage_path": tmp_path / "capture.sqlite3",
            "polymarket_research_path": tmp_path / "research",
        }
    )
    recorder = PolymarketRecorder(settings)
    recorder._token_ids = ("token-existing",)
    selected_market = object()

    async def fake_discovery() -> object:
        return SimpleNamespace(markets=(selected_market,), parse_failures=0)

    def fake_select(markets: object) -> object:
        del markets
        return SimpleNamespace(
            markets=(selected_market,),
            token_ids=("token-existing",),
        )

    def fail_upsert(*args: object) -> None:
        del args
        raise RuntimeError("disk unavailable")

    monkeypatch.setattr(recorder.gamma, "discover_active_markets", fake_discovery)
    monkeypatch.setattr(recorder.selector, "select", fake_select)
    monkeypatch.setattr(recorder.storage, "upsert_markets", fail_upsert)

    with pytest.raises(RuntimeError, match="disk unavailable"):
        asyncio.run(recorder.refresh_universe(fail_soft_if_initialized=True))


def test_runtime_stop_event_stops_websocket_and_cancels_recorder(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = AppSettings.model_validate(
        {
            "polymarket_storage_path": tmp_path / "capture.sqlite3",
            "polymarket_research_path": tmp_path / "research",
        }
    )
    recorder = PolymarketRecorder(settings)

    async def scenario() -> None:
        started = asyncio.Event()
        flags: set[str] = set()

        async def fake_run() -> None:
            started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                flags.add("cancelled")
                raise

        def fake_stop() -> None:
            flags.add("websocket_stopped")

        monkeypatch.setattr(recorder, "run", fake_run)
        monkeypatch.setattr(recorder.websocket, "stop", fake_stop)

        stop_event = asyncio.Event()
        task = asyncio.create_task(_run_recorder_until_stopped(recorder, stop_event))
        await started.wait()
        stop_event.set()
        await task

        assert flags == {"websocket_stopped", "cancelled"}

    asyncio.run(scenario())

