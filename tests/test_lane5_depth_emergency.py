from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

from predictions_cup.maker.sources import MakerSourceBridge
from predictions_cup.sig.realtime_state import DepthState
from test_sig_realtime import FakeRest, _engine, _live_market_batch


def _fresh_batch(
    now: datetime, *, revision: int = 1, previous: int = 0,
) -> dict[str, Any]:
    b = cast(dict[str, Any], _live_market_batch(revision=revision, previous=previous))
    stamp = (now - timedelta(milliseconds=200)).isoformat().replace("+00:00", "Z")
    b["books"][0]["asOf"]["at"] = stamp
    # Fixed historical fixture nextExpiryAt would already be in the past.
    b["books"][0]["nextExpiryAt"] = (now + timedelta(hours=1)).isoformat()
    b["bookDirty"][0]["at"] = stamp
    b["trades"][0]["executedAt"] = stamp
    return b


def test_streamed_full_depth_immediately_trusted_without_rest(tmp_path: Path) -> None:
    async def scenario() -> None:
        rest=FakeRest()
        engine, recorder=_engine(tmp_path,rest,tracked={"36"})
        engine._prefer_streamed_depth=True
        try:
            await engine.initialize()
            assert rest.calls == []
            engine.mark_connected()
            now=datetime.now(UTC)
            await engine.handle_raw_batch(
                engine.market_topic("26"), _fresh_batch(now), now)
            state=engine.states["36"]
            assert state.depth_state == DepthState.TRACKED_TRUSTED
            assert state.orderbook is not None
            assert state.orderbook.source == "sig-realtime"
            assert state.orderbook.timestamp == now
            assert rest.calls == []
            bridge=object.__new__(MakerSourceBridge)
            result=bridge._runtime_book(state,wall_now=now+timedelta(milliseconds=150),
                                        monotonic_now_ns=10_000_000_000)
            book,bbo_at,bbo_ok,depth_at,depth_ok=result
            assert book is not None and book.trusted_depth and depth_ok and bbo_ok
            assert depth_at is not None and 0<=10_000_000_000-depth_at<=150_000_000
        finally:
            await engine.aclose()
            recorder.close()
    asyncio.run(scenario())


def test_stream_rejects_old_book_version_and_uncovered_dirty(tmp_path: Path) -> None:
    async def scenario() -> None:
        rest=FakeRest()
        engine, recorder=_engine(tmp_path,rest,tracked={"36"})
        engine._prefer_streamed_depth=True
        rest.block_exchange = "36"
        rest.block_release.clear()
        try:
            await engine.initialize()
            engine.mark_connected()
            now=datetime.now(UTC)
            old=_fresh_batch(now)
            old["books"][0]["asOf"]["sequence"]=old["delivery"]["sourceSequenceThrough"]+1
            await engine.handle_raw_batch(engine.market_topic("26"),old,now)
            assert engine.states["36"].trusted
            b=_fresh_batch(now+timedelta(milliseconds=50),revision=2,previous=1)
            b["books"][0]["asOf"]["sequence"]=old["books"][0]["asOf"]["sequence"]+1
            b["bookDirty"][0]["at"]=(now+timedelta(seconds=2)).isoformat()
            await engine.handle_raw_batch(
                engine.market_topic("26"), b, now + timedelta(milliseconds=50)
            )
            assert not engine.states["36"].trusted
        finally:
            await engine.aclose()
            recorder.close()
    asyncio.run(scenario())


def test_stream_past_invalidation_not_promoted_on_revision_gap(tmp_path: Path) -> None:
    async def scenario() -> None:
        rest=FakeRest()
        engine, recorder=_engine(tmp_path,rest,tracked={"36"})
        engine._prefer_streamed_depth=True
        rest.block_exchange = "36"
        rest.block_release.clear()
        try:
            await engine.initialize()
            engine.mark_connected()
            now=datetime.now(UTC)
            await engine.handle_raw_batch(engine.market_topic("26"), _fresh_batch(now),now)
            assert engine.states["36"].trusted
            await engine.handle_raw_batch(engine.market_topic("26"),
                 _fresh_batch(now+timedelta(milliseconds=80),revision=6,previous=4),
                 now+timedelta(milliseconds=80))
            assert not engine.states["36"].trusted
        finally:
            await engine.aclose()
            recorder.close()
    asyncio.run(scenario())


def test_targeted_rest_recovers_missing_streamed_depth(tmp_path: Path) -> None:
    async def scenario() -> None:
        rest=FakeRest()
        engine, recorder=_engine(tmp_path,rest,tracked={"36"})
        engine._prefer_streamed_depth=True
        try:
            await engine.initialize()
            engine.mark_connected()
            assert not engine.states["36"].trusted
            assert await engine.refresh_candidate_depth("36")
            assert rest.calls == ["36"]
            assert engine.states["36"].trusted
        finally:
            await engine.aclose()
            recorder.close()
    asyncio.run(scenario())


def test_stream_mode_dirty_hints_fail_closed_without_bulk_rest(tmp_path: Path) -> None:
    """Dirty-only pushes should not replenish a 76-book routine REST queue."""
    from test_sig_realtime import _batch

    async def scenario() -> None:
        rest = FakeRest()
        engine, recorder = _engine(tmp_path, rest, tracked={"36"})
        engine._prefer_streamed_depth = True
        try:
            await engine.initialize()
            engine.mark_connected()
            now = datetime.now(UTC)
            await engine.handle_raw_batch(
                engine.market_topic("26"),
                _batch(
                    revision=1, previous=0, source_from=1,
                    source_through=1, dirty=True,
                ),
                now,
            )
            assert engine.states["36"].depth_state == DepthState.TRACKED_UNTRUSTED
            assert rest.calls == []
            fresh = _fresh_batch(now + timedelta(milliseconds=75), revision=2, previous=1)
            await engine.handle_raw_batch(
                engine.market_topic("26"), fresh, now + timedelta(milliseconds=75)
            )
            assert cast(DepthState, engine.states["36"].depth_state) == DepthState.TRACKED_TRUSTED
            assert rest.calls == []
        finally:
            await engine.aclose()
            recorder.close()

    asyncio.run(scenario())


def test_stream_mode_unbatched_dirty_does_not_schedule_rest(tmp_path: Path) -> None:
    async def scenario() -> None:
        rest = FakeRest()
        engine, recorder = _engine(tmp_path, rest, tracked={"36"})
        engine._prefer_streamed_depth = True
        try:
            await engine.initialize()
            engine.mark_connected()
            now = datetime.now(UTC)
            await engine.handle_tournament_event(
                "book_dirty",
                {
                    "exchangeId": "36",
                    "marketId": "26",
                    "tournamentId": "cup",
                    "at": now.isoformat().replace("+00:00", "Z"),
                },
                now,
            )
            assert engine.states["36"].depth_state == DepthState.TRACKED_UNTRUSTED
            assert rest.calls == []
        finally:
            await engine.aclose()
            recorder.close()

    asyncio.run(scenario())


def test_stream_maintenance_does_not_enqueue_tracked_untrusted_bulk_rest(tmp_path: Path) -> None:
    async def scenario() -> None:
        rest = FakeRest(market_count=76)
        tracked = {f"e-{i}" for i in range(76)}
        engine, recorder = _engine(tmp_path, rest, tracked=tracked, max_age=180.0)
        engine._prefer_streamed_depth = True
        try:
            await engine.initialize()
            engine.mark_connected()
            assert rest.calls == []
            now = datetime.now(UTC)
            await engine.maintenance(now)
            await asyncio.sleep(0.01)
            assert rest.calls == []
            assert not engine._reconcile_tasks
            assert all(not engine.states[k].trusted for k in tracked)
        finally:
            await engine.aclose()
            recorder.close()
    asyncio.run(scenario())


def test_stream_maintenance_expires_book_trust_without_rest(tmp_path: Path) -> None:
    async def scenario() -> None:
        rest = FakeRest()
        engine, recorder = _engine(tmp_path, rest, tracked={"36"}, max_age=180.0)
        engine._prefer_streamed_depth = True
        try:
            await engine.initialize()
            engine.mark_connected()
            now = datetime.now(UTC)
            await engine.handle_raw_batch(engine.market_topic("26"), _fresh_batch(now), now)
            assert engine.states["36"].trusted
            assert rest.calls == []
            await engine.refresh_stale_open_books(now + timedelta(seconds=181), wait=False)
            await asyncio.sleep(0.01)
            assert not engine.states["36"].trusted
            assert rest.calls == []
        finally:
            await engine.aclose()
            recorder.close()
    asyncio.run(scenario())


def test_scalar_heartbeat_preserves_matching_depth_but_not_original_age(tmp_path: Path) -> None:
    async def scenario() -> None:
        rest = FakeRest()
        engine, recorder = _engine(tmp_path, rest, tracked={"36"})
        engine._prefer_streamed_depth = True
        try:
            await engine.initialize()
            engine.mark_connected()
            now = datetime.now(UTC)
            await engine.handle_raw_batch(engine.market_topic("26"), _fresh_batch(now), now)
            state = engine.states["36"]
            assert state.trusted
            assert state.orderbook is not None
            later = now + timedelta(milliseconds=200)
            state.last_realtime_observed_at = now
            state.last_scalar_observed_at = later
            state.scalar_best_bid = state.orderbook.bids[0].price
            state.scalar_best_ask = state.orderbook.asks[0].price
            bridge = object.__new__(MakerSourceBridge)
            book, bbo_time, bbo_ok, depth_time, depth_ok = bridge._runtime_book(
                state, wall_now=later, monotonic_now_ns=11_000_000_000)
            assert book is not None and book.trusted_depth and depth_ok and bbo_ok
            assert bbo_time is not None and depth_time is not None
            assert bbo_time > depth_time  # newer scalar does not refresh depth age
            state.scalar_best_ask = state.scalar_best_ask + type(state.scalar_best_ask)("0.005")
            book2, _, _, _, trust2 = bridge._runtime_book(
                state, wall_now=later, monotonic_now_ns=11_000_000_000)
            assert book2 is not None and not book2.trusted_depth and not trust2
        finally:
            await engine.aclose()
            recorder.close()
    asyncio.run(scenario())


def test_trade_without_dirty_or_covering_book_invalidates_streamed_depth(tmp_path: Path) -> None:
    async def scenario() -> None:
        rest = FakeRest()
        engine, recorder = _engine(tmp_path, rest, tracked={"36"})
        engine._prefer_streamed_depth = True
        try:
            await engine.initialize()
            engine.mark_connected()
            now = datetime.now(UTC)
            await engine.handle_raw_batch(engine.market_topic("26"), _fresh_batch(now), now)
            assert engine.states["36"].trusted
            trade_only = _fresh_batch(now + timedelta(milliseconds=50), revision=2, previous=1)
            trade_only["books"] = []
            trade_only["bookDirty"] = []
            await engine.handle_raw_batch(
                engine.market_topic("26"), trade_only, now + timedelta(milliseconds=50))
            assert not engine.states["36"].trusted
            assert rest.calls == []
        finally:
            await engine.aclose()
            recorder.close()
    asyncio.run(scenario())


def test_unbatched_duplicate_dirty_after_full_book_preserves_stream_trust(tmp_path: Path) -> None:
    """Out-of-order duplicate dirty notification cannot erase covering depth."""
    async def scenario() -> None:
        rest = FakeRest()
        engine, recorder = _engine(tmp_path, rest, tracked={"36"})
        engine._prefer_streamed_depth = True
        try:
            await engine.initialize()
            engine.mark_connected()
            now = datetime.now(UTC)
            batch = _fresh_batch(now)
            await engine.handle_raw_batch(engine.market_topic("26"), batch, now)
            state = engine.states["36"]
            assert state.trusted
            assert state.orderbook is not None
            timestamp = state.orderbook.timestamp
            dirty = {
                "exchangeId": "36", "marketId": "26", "tournamentId": "cup",
                "at": batch["bookDirty"][0]["at"],
            }
            changed = await engine.handle_tournament_event(
                "book_dirty", dirty, now + timedelta(milliseconds=250)
            )
            assert changed == {"36"}
            assert state.trusted
            assert state.orderbook is not None
            assert state.orderbook.timestamp == timestamp
            assert engine._stream_depth_trust_counts["duplicate_dirty_covered"] == 1
            assert rest.calls == []
            dirty["at"] = (now + timedelta(milliseconds=400)).isoformat()
            await engine.handle_tournament_event(
                "book_dirty", dirty, now + timedelta(milliseconds=450)
            )
            assert not state.trusted
            assert rest.calls == []
        finally:
            await engine.aclose()
            recorder.close()
    asyncio.run(scenario())


def test_unbatched_dirty_first_then_new_full_book_restores_trust(tmp_path: Path) -> None:
    """Fresh dirty must revoke depth; next complete market book may restore it."""
    async def scenario() -> None:
        rest = FakeRest()
        engine, recorder = _engine(tmp_path, rest, tracked={"36"})
        engine._prefer_streamed_depth = True
        try:
            await engine.initialize()
            engine.mark_connected()
            now = datetime.now(UTC)
            first = _fresh_batch(now)
            await engine.handle_raw_batch(engine.market_topic("26"), first, now)
            dirty_at = now + timedelta(milliseconds=300)
            await engine.handle_tournament_event("book_dirty", {
                "exchangeId": "36", "marketId": "26", "tournamentId": "cup",
                "at": dirty_at.isoformat(),
            }, dirty_at)
            assert not engine.states["36"].trusted
            next_batch = _fresh_batch(now + timedelta(milliseconds=450), revision=2, previous=1)
            await engine.handle_raw_batch(
                engine.market_topic("26"), next_batch,
                now + timedelta(milliseconds=450),
            )
            assert engine.states["36"].trusted
            assert rest.calls == []
        finally:
            await engine.aclose()
            recorder.close()
    asyncio.run(scenario())


def test_stream_book_preceding_market_batch_global_sequence_is_trusted(tmp_path: Path) -> None:
    """Other engine events increase sourceThrough without mutating this book."""
    async def scenario() -> None:
        rest = FakeRest()
        engine, recorder = _engine(tmp_path, rest, tracked={"36"})
        engine._prefer_streamed_depth = True
        try:
            await engine.initialize()
            engine.mark_connected()
            now = datetime.now(UTC)
            batch = _fresh_batch(now)
            batch["delivery"]["sourceSequenceFrom"] = 1854000
            batch["delivery"]["sourceSequenceThrough"] += 1000
            await engine.handle_raw_batch(engine.market_topic("26"), batch, now)
            assert engine.states["36"].trusted
            assert engine._stream_depth_trust_counts["accepted"] == 1
            assert rest.calls == []
        finally:
            await engine.aclose()
            recorder.close()
    asyncio.run(scenario())


def test_book_sequence_ahead_of_delivery_is_valid_but_uncovered_trade_rejected(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        rest = FakeRest()
        engine, recorder = _engine(tmp_path, rest, tracked={"36"})
        engine._prefer_streamed_depth = True
        try:
            await engine.initialize()
            engine.mark_connected()
            now = datetime.now(UTC)
            future = _fresh_batch(now)
            future["delivery"]["sourceSequenceThrough"] -= 1
            await engine.handle_raw_batch(engine.market_topic("26"), future, now)
            assert engine.states["36"].trusted
            assert engine._stream_depth_trust_counts["accepted_book_sequence_ahead"] == 1
            # Fresh book timestamp alone cannot cover a trade with a later
            # source engine sequence even if the delivery range extends past it.
            later = _fresh_batch(now + timedelta(milliseconds=500), revision=2, previous=1)
            later["delivery"]["sourceSequenceThrough"] += 1000
            later["trades"][0]["sequence"] = later["books"][0]["asOf"]["sequence"] + 1
            await engine.handle_raw_batch(
                engine.market_topic("26"), later, now + timedelta(milliseconds=500)
            )
            assert not engine.states["36"].trusted
            assert rest.calls == []
        finally:
            await engine.aclose()
            recorder.close()
    asyncio.run(scenario())


def test_duplicate_topic_revision_preserves_trusted_depth(tmp_path: Path) -> None:
    async def scenario() -> None:
        rest = FakeRest()
        engine, recorder = _engine(tmp_path, rest, tracked={"36"})
        engine._prefer_streamed_depth = True
        try:
            await engine.initialize()
            engine.mark_connected()
            now = datetime.now(UTC)
            initial = _fresh_batch(now)
            await engine.handle_raw_batch(engine.market_topic("26"), initial, now)
            before = engine.states["36"].orderbook
            duplicate = _fresh_batch(now + timedelta(milliseconds=100))
            duplicate["books"][0]["asOf"]["sequence"] += 100
            await engine.handle_raw_batch(
                engine.market_topic("26"), duplicate, now + timedelta(milliseconds=100)
            )
            assert engine.states["36"].trusted
            assert engine.states["36"].orderbook == before
            assert rest.calls == []
        finally:
            await engine.aclose()
            recorder.close()
    asyncio.run(scenario())


def test_new_stream_book_supersedes_old_order_expiry_deadline(tmp_path: Path) -> None:
    async def scenario() -> None:
        rest = FakeRest()
        engine, recorder = _engine(tmp_path, rest, tracked={"36"}, max_age=180.0)
        engine._prefer_streamed_depth = True
        try:
            await engine.initialize()
            engine.mark_connected()
            now = datetime.now(UTC)
            first = _fresh_batch(now)
            first["books"][0]["nextExpiryAt"] = (
                now + timedelta(seconds=2)).isoformat()
            await engine.handle_raw_batch(engine.market_topic("26"), first, now)
            assert engine.states["36"].trusted
            second = _fresh_batch(
                now + timedelta(milliseconds=100), revision=2, previous=1)
            second["books"][0]["asOf"]["sequence"] += 1
            second["books"][0]["nextExpiryAt"] = (
                now + timedelta(seconds=8)).isoformat()
            await engine.handle_raw_batch(
                engine.market_topic("26"), second, now + timedelta(milliseconds=100))
            assert engine.states["36"].trusted
            assert engine.states["36"].next_expiry_at == now + timedelta(seconds=8)
            await engine.refresh_stale_open_books(
                now + timedelta(seconds=3), wait=False)
            assert engine.states["36"].trusted
            third = _fresh_batch(now + timedelta(seconds=4), revision=3, previous=2)
            third["books"][0]["asOf"]["sequence"] += 2
            third["books"][0]["nextExpiryAt"] = None
            await engine.handle_raw_batch(
                engine.market_topic("26"), third, now + timedelta(seconds=4))
            assert engine.states["36"].next_expiry_at is None
            assert rest.calls == []
        finally:
            await engine.aclose()
            recorder.close()
    asyncio.run(scenario())
