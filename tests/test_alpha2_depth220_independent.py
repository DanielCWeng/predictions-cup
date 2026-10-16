"""Ex-Alpha2 independent #220 offline maker-depth recorder safety probes.

Frozen combined source only; fake transports, no real exchange or service.
"""

import asyncio
import json
import os
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from threading import Event

import pytest

from predictions_cup.models.market import OrderBook, OrderBookLevel
from predictions_cup.sig.depth_mirror import MakerDepthMirror
from predictions_cup.sig.realtime_state import (
    SigRealtimeStateEngine,
    TrustTransition,
)
from predictions_cup.sig.realtime_storage import SigRealtimeRecorder
from predictions_cup.sig.rest_governor import RestPriority

from test_sig_realtime import FakeRest


def _book() -> OrderBook:
    return OrderBook(
        exchange_id="A",
        bids=(OrderBookLevel(price=Decimal("0.4"), quantity=Decimal("10")),),
        asks=(OrderBookLevel(price=Decimal("0.6"), quantity=Decimal("11")),),
        timestamp=datetime(2026, 10, 16, tzinfo=UTC),
        source="fake",
    )


def _record(mirror: MakerDepthMirror) -> None:
    mirror.record(_book(), "market-A", "test", datetime(2026, 10, 16, tzinfo=UTC), 9)


def test_async_stalled_writer_teardown_never_blocks_loop_or_certifies_early(
    tmp_path: Path,
) -> None:
    mirror = MakerDepthMirror(tmp_path / "stalled.jsonl")
    entered = Event()
    release = Event()
    real_row = mirror._book_row

    def stalled(item: object) -> dict[str, object]:
        entered.set()
        if not release.wait(timeout=4):
            raise RuntimeError("test writer release timed out")
        return real_row(item)  # type: ignore[arg-type]

    mirror._book_row = stalled  # type: ignore[method-assign]
    mirror.start()

    async def run() -> None:
        try:
            _record(mirror)
            assert await asyncio.to_thread(entered.wait, 2)
            timer_fired = asyncio.Event()

            async def heartbeat() -> None:
                await asyncio.sleep(0.025)
                timer_fired.set()

            tick = asyncio.create_task(heartbeat())
            teardown = asyncio.create_task(mirror.aclose(timeout=0.16))
            await asyncio.wait_for(timer_fired.wait(), timeout=0.11)
            assert teardown.done() is False
            assert mirror.snapshot()["terminal_complete"] is False
            await teardown
            await tick
            stats = mirror.snapshot()
            assert stats["writer_alive"] is True
            assert stats["file_synced"] is False
            assert stats["terminal_complete"] is False
            before = int(stats["producer_rejected"])
            _record(mirror)
            assert mirror.snapshot()["producer_rejected"] == before + 1
        finally:
            release.set()
            await mirror.aclose(timeout=2)

    asyncio.run(run())
    final = mirror.snapshot()
    assert final["writer_alive"] is False
    assert final["accepted"] == 1
    assert final["written"] == 1
    assert final["queue_pending"] == 0
    assert final["terminal_complete"] is False  # rejected post-stop entry


def test_fsync_failure_requires_incomplete_even_when_row_was_written(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    mirror = MakerDepthMirror(tmp_path / "failed-fsync.jsonl")

    def fail_fsync(_fd: int) -> None:
        raise OSError("offline fsync failure")

    with monkeypatch.context() as patch:
        patch.setattr(os, "fsync", fail_fsync)
        mirror.start()
        _record(mirror)
        mirror.close(timeout=2)

    stats = mirror.snapshot()
    assert stats["writer_alive"] is False
    assert stats["written"] == 1
    assert stats["errors"] == 1
    assert stats["file_synced"] is False
    assert stats["terminal_complete"] is False


def test_terminal_complete_only_for_closed_lossless_synced_file(tmp_path: Path) -> None:
    mirror = MakerDepthMirror(tmp_path / "lossless.jsonl", queue_size=128)
    mirror.start()
    for _ in range(16):
        _record(mirror)
    mirror.close(timeout=2)
    stats = mirror.snapshot()
    assert stats["accepted"] == stats["written"] == 16
    assert stats["dropped"] == stats["writer_lost"] == 0
    assert stats["queue_pending"] == 0
    assert stats["writer_alive"] is False
    assert stats["file_synced"] is True
    assert stats["terminal_complete"] is True
    data = [json.loads(x) for x in (tmp_path / "lossless.jsonl").read_text().splitlines()]
    assert len(data) == 16
    assert all(
        row["trusted_for_paired_paper"] is False
        and row["venue_timestamp"] is None
        and row["venue_revision"] is None
        for row in data
    )


def test_sig_realtime_on_off_fake_rest_transcripts_match_and_never_infer_trust(
    tmp_path: Path,
) -> None:
    """Exercise real realtime reconciliation with fake REST, not mirrored polls."""

    async def run(enabled: bool) -> tuple[list[str], list[str], int]:
        name = "on" if enabled else "off"
        rest = FakeRest()
        recorder = SigRealtimeRecorder(tmp_path / f"{name}.sqlite3")
        mirror = (
            MakerDepthMirror(tmp_path / f"{name}.jsonl")
            if enabled
            else None
        )
        if mirror is not None:
            mirror.start()
        engine = SigRealtimeStateEngine(
            rest=rest, recorder=recorder, tournament_id="fake-cup",
            tracked_depth_exchange_ids={"36", "37"},
            depth_capture=mirror.record if mirror else None,
        )
        try:
            await engine.initialize()
            await engine._queue_reconciliation(
                "36",
                reason="offline-independent",
                final_transition=TrustTransition.TRUSTED_AFTER_RECONCILIATION,
                triggering_revision=9,
                priority=RestPriority.HIGH,
                wait=True,
            )
            calls = list(rest.calls)
            markets = list(rest.market_calls)
            return calls, markets, int(mirror.snapshot()["accepted"]) if mirror else 0
        finally:
            await engine.aclose()
            recorder.close()
            if mirror is not None:
                await mirror.aclose(timeout=2)

    off = asyncio.run(run(False))
    on = asyncio.run(run(True))
    assert off[0] == on[0] == ["36", "37", "36"]
    assert off[1] == on[1]
    assert off[2] == 0
    assert on[2] == 3

    data = [json.loads(x) for x in (tmp_path / "on.jsonl").read_text().splitlines()]
    assert len(data) == 3
    assert {row["exchange_id"] for row in data} == {"36", "37"}
    assert all(
        row["source_clock_verified"] is False
        and row["source_sequence_verified"] is False
        and row["trusted_for_paired_paper"] is False
        for row in data
    )
