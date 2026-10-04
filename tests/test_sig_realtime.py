from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import cast

import pytest

from predictions_cup.models import OrderBook, OrderBookLevel
from predictions_cup.sig.dto import (
    BulkPricesDto,
    MarketDto,
    OrderBookSnapshotDto,
    PriceSnapshotDto,
)
from predictions_cup.sig.realtime_models import (
    BookDirtyDto,
    MarketBatchDto,
    MarketSettledDto,
    RealtimeDeliveryDto,
    RealtimeTokenDto,
    RealtimeTradeDto,
)
from predictions_cup.sig.realtime_state import (
    DepthState,
    SigRealtimeStateEngine,
    SubscriptionReason,
)
from predictions_cup.sig.realtime_storage import SigRealtimeRecorder
from predictions_cup.sig.rest_governor import RestPriority


def _market(
    market_id: str,
    exchange_id: str,
    *,
    status: str = "open",
    settled_with: str | None = None,
) -> MarketDto:
    settled_on = "2026-09-25T14:00:00Z" if status == "settled" else None
    return MarketDto.model_validate(
        {
            "id": market_id,
            "title": f"Market {market_id}",
            "thumbnailUrl": None,
            "status": status,
            "createdAt": None,
            "settlementDate": None,
            "settledWith": settled_with,
            "settledOn": settled_on,
            "categories": [],
            "isComposite": False,
            "isMultiOutcome": False,
            "exchanges": [
                {
                    "id": exchange_id,
                    "option": "YES",
                    "latestPrice": 0.5,
                    "initialPrice": 0.5,
                }
            ],
            "contexts": [],
            "creator": None,
        }
    )


class FakeRest:
    def __init__(self, market_count: int = 2) -> None:
        if market_count == 2:
            pairs = [("26", "36"), ("27", "37")]
        else:
            pairs = [(f"m-{index}", f"e-{index}") for index in range(market_count)]
        self.pairs = pairs
        self.exchange_to_market = {exchange_id: market_id for market_id, exchange_id in pairs}
        self.market_to_exchange = {market_id: exchange_id for market_id, exchange_id in pairs}

        self.calls: list[str] = []
        self.orderbook_priorities: list[RestPriority] = []
        self.market_calls: list[str] = []
        self.bulk_calls: list[tuple[str, ...]] = []
        self.bulk_priorities: list[RestPriority] = []
        self.bulk_missing: set[str] = set()
        self.fail: set[str] = set()
        self.empty_books: set[str] = set()
        self.market_status = {market_id: "open" for market_id, _ in pairs}
        self.settled_with: dict[str, str | None] = {market_id: None for market_id, _ in pairs}
        self._priority = RestPriority.NORMAL
        self.block_exchange: str | None = None
        self.block_started = asyncio.Event()
        self.block_release = asyncio.Event()

    def _market(self, market_id: str) -> MarketDto:
        return _market(
            market_id,
            self.market_to_exchange[market_id],
            status=self.market_status[market_id],
            settled_with=self.settled_with[market_id],
        )

    @asynccontextmanager
    async def priority(self, priority: RestPriority) -> AsyncIterator[None]:
        previous = self._priority
        self._priority = priority
        try:
            yield
        finally:
            self._priority = previous

    async def iter_markets(
        self,
        *,
        limit: int = 100,
        tournament_id: str | None = None,
    ) -> AsyncIterator[MarketDto]:
        del limit, tournament_id
        for market_id, _ in self.pairs:
            yield self._market(market_id)

    async def get_market(
        self,
        market_id: str,
        *,
        tournament_id: str | None = None,
    ) -> MarketDto:
        del tournament_id
        self.market_calls.append(market_id)
        return self._market(market_id)

    async def get_bulk_prices(
        self,
        exchange_ids: Sequence[str],
        *,
        tournament_id: str | None = None,
    ) -> BulkPricesDto:
        assert tournament_id == "cup"
        requested = tuple(exchange_ids)
        self.bulk_calls.append(requested)
        self.bulk_priorities.append(self._priority)
        return BulkPricesDto.model_validate(
            {
                "data": [
                    {
                        "exchangeId": exchange_id,
                        "marketId": self.exchange_to_market[exchange_id],
                        "option": "YES",
                        "latestPrice": 0.5,
                        "bestBid": 0.4,
                        "bestAsk": 0.6,
                        "spread": 0.2,
                    }
                    for exchange_id in requested
                    if exchange_id not in self.bulk_missing
                ],
                "missingIds": [
                    exchange_id
                    for exchange_id in requested
                    if exchange_id in self.bulk_missing
                ],
            }
        )

    async def get_orderbook(
        self,
        exchange_id: str,
        *,
        depth: int = 20,
        tournament_id: str | None = None,
    ) -> OrderBookSnapshotDto:
        assert tournament_id == "cup"
        self.calls.append(exchange_id)
        self.orderbook_priorities.append(self._priority)
        if exchange_id == self.block_exchange and not self.block_release.is_set():
            self.block_started.set()
            await self.block_release.wait()
        if exchange_id in self.fail:
            raise RuntimeError("synthetic REST failure")
        market_id = self.exchange_to_market[exchange_id]
        empty = exchange_id in self.empty_books
        return OrderBookSnapshotDto.model_validate(
            {
                "exchangeId": exchange_id,
                "marketId": market_id,
                "depth": depth,
                "bids": [] if empty else [{"price": 0.4, "quantity": 10}],
                "asks": [] if empty else [{"price": 0.6, "quantity": 12}],
                "bestBid": None if empty else 0.4,
                "bestAsk": None if empty else 0.6,
                "spread": None if empty else 0.2,
            }
        )


def _batch(
    *,
    revision: int,
    previous: int,
    source_from: int,
    source_through: int,
    exchange_id: str = "36",
    market_id: str = "26",
    dirty: bool = False,
    settled_market_id: str | None = None,
) -> dict[str, object]:
    return {
        "trades": [
            {
                "exchangeId": exchange_id,
                "marketId": market_id,
                "price": 0.45,
                "quantity": 3,
                "executedAt": "2026-09-25T14:00:00Z",
                "tournamentId": "cup",
            }
        ],
        "bookDirty": (
            [
                {
                    "exchangeId": exchange_id,
                    "marketId": market_id,
                    "tournamentId": "cup",
                    "at": "2026-09-25T14:00:00Z",
                }
            ]
            if dirty
            else []
        ),
        "marketSettled": (
            [
                {
                    "marketId": settled_market_id,
                    "tournamentId": "cup",
                    "settledWith": "YES",
                    "at": "2026-09-25T14:00:00Z",
                }
            ]
            if settled_market_id is not None
            else []
        ),
        "delivery": {
            "revision": revision,
            "previousRevision": previous,
            "correlationId": f"corr-{revision}",
            "sourceSequenceFrom": source_from,
            "sourceSequenceThrough": source_through,
        },
    }


def _engine(
    tmp_path: Path,
    rest: FakeRest,
    *,
    tracked: set[str] | None = None,
    max_age: float = 30.0,
) -> tuple[SigRealtimeStateEngine, SigRealtimeRecorder]:
    recorder = SigRealtimeRecorder(tmp_path / "sig.sqlite3")
    engine = SigRealtimeStateEngine(
        rest=rest,
        recorder=recorder,
        tournament_id="cup",
        tracked_depth_exchange_ids=tracked or set(),
        open_book_max_trusted_age_seconds=max_age,
    )
    return engine, recorder


def test_realtime_models_validate_documented_shapes() -> None:
    token = RealtimeTokenDto.model_validate(
        {
            "token": "jwt-secret",
            "expiresAt": "2026-09-25T18:00:00Z",
            "supabaseUrl": "https://example.supabase.co",
            "anonKey": "sb_publishable_secretish",
            "channels": {"user": "user:abc"},
        }
    )
    assert "jwt-secret" not in repr(token)
    batch = MarketBatchDto.model_validate(
        _batch(revision=7, previous=6, source_from=100, source_through=103)
    )
    assert batch.delivery.revision == 7
    assert batch.trades[0].exchange_id == "36"


def test_safe_default_tracks_no_resident_depth(tmp_path: Path) -> None:
    async def scenario() -> None:
        rest = FakeRest()
        engine, recorder = _engine(tmp_path, rest)
        await engine.initialize()

        assert rest.calls == []
        assert len(rest.bulk_calls) == 1
        assert all(
            state.depth_state == DepthState.UNTRACKED_DEPTH
            for state in engine.states.values()
        )
        health = engine.health_snapshot()
        assert health["known_exchange_count"] == 2
        assert health["tracked_depth_exchange_count"] == 0
        assert health["untracked_depth_exchange_count"] == 2
        await engine.aclose()
        recorder.close()

    asyncio.run(scenario())


def test_237_exchange_startup_uses_three_bulk_calls_and_only_tracked_books(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        rest = FakeRest(market_count=237)
        engine, recorder = _engine(tmp_path, rest, tracked={"e-0", "e-100", "e-236"})
        await engine.initialize()

        assert rest.calls == ["e-0", "e-100", "e-236"]
        assert [len(batch) for batch in rest.bulk_calls] == [100, 100, 37]
        assert sum(len(batch) for batch in rest.bulk_calls) == 237
        assert all(priority == RestPriority.BACKGROUND for priority in rest.bulk_priorities)
        assert engine.states["e-1"].depth_state == DepthState.UNTRACKED_DEPTH
        assert engine.states["e-0"].depth_state == DepthState.TRACKED_TRUSTED
        await engine.aclose()
        recorder.close()

    asyncio.run(scenario())


def test_maker_bulk_price_sweep_can_be_limited_to_tracked_exchanges(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        rest = FakeRest(market_count=237)
        recorder = SigRealtimeRecorder(tmp_path / "sig.sqlite3")
        engine = SigRealtimeStateEngine(
            rest=rest,
            recorder=recorder,
            tournament_id="cup",
            tracked_depth_exchange_ids={"e-0", "e-100", "e-236"},
            bulk_prices_tracked_only=True,
            extra_bulk_price_exchange_ids=("e-5",),
        )
        await engine.initialize()

        assert rest.bulk_calls == [("e-0", "e-100", "e-236", "e-5")]
        assert engine.states["e-0"].latest_price is not None
        assert engine.states["e-1"].latest_price is None
        await engine.aclose()
        recorder.close()

    asyncio.run(scenario())


def test_bulk_missing_id_clears_stale_scalar_bbo_fail_closed(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        rest = FakeRest()
        engine, recorder = _engine(tmp_path, rest)
        await engine.initialize()

        state = engine.states["36"]
        assert state.latest_price == Decimal("0.5")
        assert state.scalar_best_bid == Decimal("0.4")
        assert state.scalar_best_ask == Decimal("0.6")
        assert state.scalar_spread == Decimal("0.2")
        assert state.last_scalar_observed_at is not None
        assert state.best_bid == Decimal("0.4")
        assert state.best_ask == Decimal("0.6")

        rest.bulk_missing.add("36")
        await engine.refresh_bulk_prices(reason="test_missing_id")

        assert state.latest_price is None
        assert state.scalar_best_bid is None
        assert state.scalar_best_ask is None
        assert state.scalar_spread is None
        assert state.last_scalar_observed_at is None
        assert state.best_bid is None
        assert state.best_ask is None
        assert engine.health.bulk_price_missing_count == 1

        await engine.aclose()
        recorder.close()

        connection = sqlite3.connect(tmp_path / "sig.sqlite3")
        row = connection.execute(
            """
            SELECT latest_price, best_bid, best_ask, spread, reason
            FROM price_observations
            WHERE exchange_id = '36'
            ORDER BY id DESC LIMIT 1
            """
        ).fetchone()
        connection.close()
        assert row == (None, None, None, None, "test_missing_id:missing")

    asyncio.run(scenario())


def test_initial_seed_source_sequence_is_not_gap_counter(tmp_path: Path) -> None:
    async def scenario() -> None:
        rest = FakeRest()
        engine, recorder = _engine(tmp_path, rest, tracked={"36", "37"})
        await engine.initialize()
        engine.mark_connected()
        assert rest.calls == ["36", "37"]
        observed = datetime(2026, 9, 25, 14, 0, tzinfo=UTC)

        await engine.handle_raw_batch(
            engine.topic,
            _batch(revision=5, previous=4, source_from=100, source_through=100),
            observed,
        )
        calls_after_first = len(rest.calls)

        await engine.handle_raw_batch(
            engine.topic,
            _batch(
                revision=6,
                previous=5,
                source_from=9999,
                source_through=10001,
                dirty=True,
            ),
            observed + timedelta(milliseconds=250),
        )

        assert engine.health.revision_gap_count == 0
        assert engine.last_accepted_revision == 6
        assert len(rest.calls) == calls_after_first + 1
        assert engine.states["36"].trusted is True
        assert engine.states["36"].last_trade is not None
        assert engine.states["36"].latest_price == Decimal("0.45")
        assert engine.states["36"].last_trade_observed_at == observed + timedelta(
            milliseconds=250
        )
        await engine.aclose()
        recorder.close()

    asyncio.run(scenario())


def test_tracked_silent_expiry_marks_untrusted_before_refresh_and_recovers(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        rest = FakeRest()
        engine, recorder = _engine(tmp_path, rest, tracked={"36"}, max_age=30.0)
        await engine.initialize()

        state = engine.states["36"]
        assert state.trusted
        assert state.last_rest_observed_at is not None
        initial_calls = len(rest.calls)

        await engine.refresh_stale_open_books(
            state.last_rest_observed_at + timedelta(seconds=29)
        )
        assert len(rest.calls) == initial_calls

        rest.empty_books.add("36")
        await engine.refresh_stale_open_books(
            state.last_rest_observed_at + timedelta(seconds=31)
        )

        assert len(rest.calls) == initial_calls + 1
        assert state.trusted
        assert state.orderbook is not None
        assert state.orderbook.bids == ()
        assert state.orderbook.asks == ()
        assert state.scalar_best_bid == Decimal("0.4")
        assert state.scalar_best_ask == Decimal("0.6")
        assert state.best_bid is None
        assert state.best_ask is None
        assert engine.health.bounded_book_refresh_count == 1
        await engine.aclose()
        recorder.close()

    asyncio.run(scenario())


def test_failed_tracked_refresh_remains_fail_closed(tmp_path: Path) -> None:
    async def scenario() -> None:
        rest = FakeRest()
        engine, recorder = _engine(tmp_path, rest, tracked={"36"})
        await engine.initialize()
        state = engine.states["36"]
        assert state.last_rest_observed_at is not None
        rest.fail.add("36")

        await engine.refresh_stale_open_books(
            state.last_rest_observed_at + timedelta(seconds=31)
        )

        assert state.depth_state == DepthState.TRACKED_UNTRUSTED
        assert not state.trusted
        await engine.aclose()
        recorder.close()

    asyncio.run(scenario())


def test_untracked_book_dirty_is_persisted_without_full_book_fetch(tmp_path: Path) -> None:
    async def scenario() -> None:
        db = tmp_path / "sig.sqlite3"
        rest = FakeRest()
        recorder = SigRealtimeRecorder(db)
        engine = SigRealtimeStateEngine(
            rest=rest,
            recorder=recorder,
            tournament_id="cup",
        )
        await engine.initialize()
        initial_calls = len(rest.calls)

        await engine.handle_raw_batch(
            engine.topic,
            _batch(
                revision=1,
                previous=0,
                source_from=1,
                source_through=1,
                dirty=True,
            ),
            datetime(2026, 9, 25, 14, 0, tzinfo=UTC),
        )

        assert len(rest.calls) == initial_calls
        assert engine.states["36"].depth_state == DepthState.UNTRACKED_DEPTH
        await engine.aclose()
        recorder.close()

        connection = sqlite3.connect(db)
        assert connection.execute(
            "SELECT COUNT(*) FROM book_dirty_events WHERE exchange_id = '36'"
        ).fetchone() == (1,)
        connection.close()

    asyncio.run(scenario())


def test_tracked_book_dirty_uses_high_priority_reconciliation(tmp_path: Path) -> None:
    async def scenario() -> None:
        rest = FakeRest()
        engine, recorder = _engine(tmp_path, rest, tracked={"36"})
        await engine.initialize()
        initial_calls = len(rest.calls)

        await engine.handle_raw_batch(
            engine.topic,
            _batch(
                revision=1,
                previous=0,
                source_from=1,
                source_through=1,
                dirty=True,
            ),
            datetime(2026, 9, 25, 14, 0, tzinfo=UTC),
        )

        assert len(rest.calls) == initial_calls + 1
        assert rest.orderbook_priorities[-1] == RestPriority.HIGH
        assert engine.states["36"].trusted
        await engine.aclose()
        recorder.close()

    asyncio.run(scenario())


def test_reconciliation_coalesces_dirty_signal_arriving_in_flight(tmp_path: Path) -> None:
    async def scenario() -> None:
        rest = FakeRest()
        engine, recorder = _engine(tmp_path, rest, tracked={"36"})
        await engine.initialize()
        initial_calls = len(rest.calls)
        rest.block_exchange = "36"
        rest.block_release.clear()
        rest.block_started.clear()
        observed = datetime(2026, 9, 25, 14, 0, tzinfo=UTC)

        first = asyncio.create_task(
            engine.handle_raw_batch(
                engine.topic,
                _batch(
                    revision=1,
                    previous=0,
                    source_from=1,
                    source_through=1,
                    dirty=True,
                ),
                observed,
            )
        )
        await rest.block_started.wait()
        second = asyncio.create_task(
            engine.handle_raw_batch(
                engine.topic,
                _batch(
                    revision=2,
                    previous=1,
                    source_from=2,
                    source_through=2,
                    dirty=True,
                ),
                observed + timedelta(milliseconds=250),
            )
        )
        await asyncio.sleep(0)
        rest.block_release.set()
        await asyncio.gather(first, second)

        assert len(rest.calls) == initial_calls + 2
        assert engine.states["36"].trusted
        assert engine.states["36"].last_accepted_revision == 2
        await engine.aclose()
        recorder.close()

    asyncio.run(scenario())


def test_revision_gap_recovery_on_237_markets_does_not_create_book_storm(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        rest = FakeRest(market_count=237)
        engine, recorder = _engine(tmp_path, rest, tracked={"e-0", "e-1"})
        await engine.initialize()
        initial_books = len(rest.calls)
        initial_bulk = len(rest.bulk_calls)
        observed = datetime(2026, 9, 25, 14, 0, tzinfo=UTC)

        await engine.handle_raw_batch(
            engine.topic,
            _batch(
                revision=10,
                previous=9,
                source_from=1,
                source_through=1,
                exchange_id="e-0",
                market_id="m-0",
            ),
            observed,
        )
        await engine.handle_raw_batch(
            engine.topic,
            _batch(
                revision=12,
                previous=11,
                source_from=2,
                source_through=2,
                exchange_id="e-0",
                market_id="m-0",
            ),
            observed + timedelta(seconds=1),
        )

        assert engine.health.revision_gap_count == 1
        assert len(rest.calls) == initial_books + 2
        assert len(rest.bulk_calls) == initial_bulk + 3
        assert engine.states["e-2"].depth_state == DepthState.UNTRACKED_DEPTH
        await engine.aclose()
        recorder.close()

    asyncio.run(scenario())


def test_lifecycle_recovery_only_reseeds_tracked_depth(tmp_path: Path) -> None:
    async def scenario() -> None:
        rest = FakeRest()
        engine, recorder = _engine(tmp_path, rest, tracked={"36"})
        await engine.initialize()
        initial_calls = len(rest.calls)

        for reason in (
            SubscriptionReason.RECONNECT,
            SubscriptionReason.TOKEN_REFRESH,
            SubscriptionReason.SOCKET_ERROR,
        ):
            await engine.prepare_subscription(reason)

        assert engine.health.reconnect_count == 1
        assert len(rest.calls) == initial_calls + 3
        assert engine.states["36"].trusted
        assert engine.states["37"].depth_state == DepthState.UNTRACKED_DEPTH
        await engine.aclose()
        recorder.close()

    asyncio.run(scenario())


def test_market_settlement_refetches_market_and_avoids_closed_book_read(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        db = tmp_path / "sig.sqlite3"
        rest = FakeRest()
        recorder = SigRealtimeRecorder(db)
        engine = SigRealtimeStateEngine(
            rest=rest,
            recorder=recorder,
            tournament_id="cup",
            tracked_depth_exchange_ids={"36"},
        )
        await engine.initialize()
        initial_book_calls = len(rest.calls)

        rest.market_status["26"] = "settled"
        rest.settled_with["26"] = "YES"
        await engine.handle_raw_batch(
            engine.topic,
            _batch(
                revision=1,
                previous=0,
                source_from=1,
                source_through=1,
                settled_market_id="26",
            ),
            datetime(2026, 9, 25, 14, 0, tzinfo=UTC),
        )

        assert rest.market_calls == ["26"]
        assert len(rest.calls) == initial_book_calls
        assert engine.market_states["26"].status == "settled"
        state = engine.states["36"]
        assert state.trusted
        assert state.orderbook is None
        assert state.scalar_best_bid == Decimal("0.4")
        assert state.scalar_best_ask == Decimal("0.6")
        assert state.best_bid is None
        assert state.best_ask is None
        await engine.aclose()
        recorder.close()

        connection = sqlite3.connect(db)
        assert connection.execute(
            "SELECT COUNT(*) FROM market_settled_events WHERE market_id = '26'"
        ).fetchone() == (1,)
        connection.close()

    asyncio.run(scenario())


def test_periodic_bulk_refresh_can_be_disabled_for_capture(tmp_path: Path) -> None:
    async def scenario() -> None:
        rest = FakeRest()
        recorder = SigRealtimeRecorder(tmp_path / "sig.sqlite3")
        engine = SigRealtimeStateEngine(
            rest=rest,
            recorder=recorder,
            tournament_id="cup",
            periodic_bulk_refresh_enabled=False,
        )
        await engine.initialize()
        initial_bulk_calls = len(rest.bulk_calls)

        await engine.maintenance(datetime.now(UTC) + timedelta(seconds=60))
        await asyncio.sleep(0)

        assert len(rest.bulk_calls) == initial_bulk_calls
        await engine.aclose()
        recorder.close()

    asyncio.run(scenario())


def test_periodic_bulk_refresh_uses_configured_priority(tmp_path: Path) -> None:
    async def scenario() -> None:
        rest = FakeRest()
        recorder = SigRealtimeRecorder(tmp_path / "sig.sqlite3")
        engine = SigRealtimeStateEngine(
            rest=rest,
            recorder=recorder,
            tournament_id="cup",
            periodic_bulk_price_priority=RestPriority.NORMAL,
        )
        await engine.initialize()
        rest.bulk_priorities.clear()

        await engine.maintenance(datetime.now(UTC) + timedelta(seconds=60))
        await asyncio.sleep(0)

        assert rest.bulk_priorities == [RestPriority.NORMAL]
        await engine.aclose()
        recorder.close()

    asyncio.run(scenario())


def test_bulk_price_response_started_before_realtime_touch_is_ignored(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        rest = FakeRest()
        engine, recorder = _engine(tmp_path, rest)
        await engine.initialize()
        state = engine.states["36"]
        previous_scalar_observed = state.last_scalar_observed_at
        state.last_realtime_observed_at = datetime.now(UTC) + timedelta(seconds=1)

        await engine.refresh_bulk_prices(reason="test_overlapping_touch")

        assert state.last_scalar_observed_at == previous_scalar_observed
        await engine.aclose()
        recorder.close()

    asyncio.run(scenario())


def test_capacity_check_rejects_impossible_tracked_freshness(tmp_path: Path) -> None:
    async def scenario() -> None:
        rest = FakeRest(market_count=10)
        recorder = SigRealtimeRecorder(tmp_path / "sig.sqlite3")
        engine = SigRealtimeStateEngine(
            rest=rest,
            recorder=recorder,
            tournament_id="cup",
            tracked_depth_exchange_ids={f"e-{index}" for index in range(10)},
            open_book_max_trusted_age_seconds=2.0,
            governed_rate_per_second=3.0,
        )
        with pytest.raises(ValueError, match="not sustainable"):
            await engine.initialize()
        await engine.aclose()
        recorder.close()

    asyncio.run(scenario())


def test_recorder_normalization_wal_bulk_prices_and_retention(tmp_path: Path) -> None:
    path = tmp_path / "sig.sqlite3"
    recorder = SigRealtimeRecorder(path)
    assert recorder._connection.execute("PRAGMA journal_size_limit").fetchone() == (
        64 * 1024 * 1024,
    )
    old = datetime(2026, 9, 1, tzinfo=UTC)
    delivery = RealtimeDeliveryDto.model_validate(
        {
            "revision": 1,
            "previousRevision": 0,
            "correlationId": "corr",
            "sourceSequenceFrom": 11,
            "sourceSequenceThrough": 12,
        }
    )
    trade = RealtimeTradeDto.model_validate(
        {
            "exchangeId": "36",
            "marketId": "26",
            "price": 0.5,
            "quantity": 2,
            "executedAt": "2026-09-01T00:00:00Z",
            "tournamentId": "cup",
        }
    )
    book = OrderBook(
        exchange_id="36",
        bids=(OrderBookLevel(price=Decimal("0.4"), quantity=Decimal("10")),),
        asks=(OrderBookLevel(price=Decimal("0.6"), quantity=Decimal("10")),),
        timestamp=old,
        source="sig-rest",
        revision=None,
    )
    price = PriceSnapshotDto.model_validate(
        {
            "exchangeId": "36",
            "marketId": "26",
            "option": "YES",
            "latestPrice": 0.5,
            "bestBid": 0.4,
            "bestAsk": 0.6,
            "spread": 0.2,
        }
    )

    recorder.record_delivery(topic="tournament:cup", delivery=delivery, observed_at=old)
    recorder.checkpoint_wal_if_due(interval_seconds=0)
    recorder.record_trade(topic="tournament:cup", revision=1, trade=trade, observed_at=old)
    recorder.record_book_dirty(
        topic="tournament:cup",
        revision=1,
        event=BookDirtyDto.model_validate(
            {
                "exchangeId": "36",
                "marketId": "26",
                "tournamentId": "cup",
                "at": "2026-09-01T00:00:00Z",
            }
        ),
        observed_at=old,
    )
    recorder.record_market_settled(
        topic="tournament:cup",
        revision=1,
        event=MarketSettledDto.model_validate(
            {
                "marketId": "26",
                "tournamentId": "cup",
                "settledWith": "YES",
                "at": "2026-09-01T00:00:01Z",
            }
        ),
        observed_at=old,
    )
    recorder.record_market(
        tournament_id="cup",
        market=_market("26", "36"),
        observed_at=old,
        reason="test",
        triggering_revision=1,
    )
    recorder.record_prices(
        tournament_id="cup",
        prices=(price,),
        observed_at=old,
        reason="test",
    )
    recorder.record_book(
        market_id="26",
        tournament_id="cup",
        book=book,
        observed_at=old,
        reason="test",
        triggering_revision=1,
    )
    recorder.record_transition(
        topic="tournament:cup",
        exchange_id="36",
        transition="TRUSTED",
        observed_at=old,
        revision=1,
    )
    recorder.prune_before(old + timedelta(days=1))
    recorder.close()

    connection = sqlite3.connect(path)
    for table in (
        "realtime_deliveries",
        "realtime_trades",
        "book_dirty_events",
        "market_settled_events",
        "market_observations",
        "price_observations",
        "book_observations",
        "trust_transitions",
    ):
        assert connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone() == (0,)
    assert connection.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
    connection.close()


def test_recorder_retention_uses_shorter_book_window(tmp_path: Path) -> None:
    recorder = SigRealtimeRecorder(tmp_path / "sig.sqlite3")
    timestamps = (
        "2026-10-01T00:00:00+00:00",
        "2026-10-02T00:00:00+00:00",
        "2026-10-03T00:00:00+00:00",
    )
    for stamp in timestamps:
        recorder._connection.execute(
            """INSERT INTO realtime_deliveries (
                topic, revision, previous_revision, correlation_id,
                source_sequence_from, source_sequence_through, observed_at
            ) VALUES ('t', 1, 0, 'c', 1, 1, ?)""",
            (stamp,),
        )
        recorder._connection.execute(
            """INSERT INTO book_dirty_events (
                topic, revision, exchange_id, market_id, tournament_id,
                source_at, observed_at
            ) VALUES ('t', 1, 'x', 'm', 'cup', ?, ?)""",
            (stamp, stamp),
        )
    recorder._connection.commit()

    recorder.prune_before(
        datetime.fromisoformat("2026-10-02T00:00:00+00:00"),
        book_cutoff=datetime.fromisoformat("2026-10-03T00:00:00+00:00"),
    )

    delivery_stamps = recorder._connection.execute(
        "SELECT observed_at FROM realtime_deliveries ORDER BY id"
    ).fetchall()
    book_stamps = recorder._connection.execute(
        "SELECT observed_at FROM book_dirty_events ORDER BY id"
    ).fetchall()
    assert delivery_stamps == [(timestamps[1],), (timestamps[2],)]
    assert book_stamps == [(timestamps[2],)]
    recorder.close()


def test_bounded_dirty_price_refresh_uses_high_priority_subset(tmp_path: Path) -> None:
    async def scenario() -> None:
        rest = FakeRest()
        engine, recorder = _engine(tmp_path, rest)
        await engine.initialize()
        rest.bulk_calls.clear()
        rest.bulk_priorities.clear()

        await engine.refresh_exchange_prices(
            {"37"},
            reason="maker_book_dirty",
            priority=RestPriority.HIGH,
        )

        assert rest.bulk_calls == [("37",)]
        assert rest.bulk_priorities == [RestPriority.HIGH]
        assert engine.states["37"].scalar_best_bid == Decimal("0.4")
        assert engine.states["37"].scalar_best_ask == Decimal("0.6")
        await engine.aclose()
        recorder.close()

    asyncio.run(scenario())



def _live_market_batch(
    *,
    market_id: str = "26",
    exchange_id: str = "36",
    revision: int,
    previous: int,
    with_trade: bool = True,
) -> dict[str, object]:
    """Shape observed live on tournament:{id}:market:{id} on 2026-10-01."""
    return {
        "trades": (
            [
                {
                    "id": "1854300",
                    "sequence": 1854300,
                    "exchangeId": exchange_id,
                    "marketId": market_id,
                    "price": 0.07,
                    "quantity": 880,
                    "executedAt": "2026-10-01T16:10:28.500Z",
                    "tournamentId": "cup",
                }
            ]
            if with_trade
            else []
        ),
        "bookDirty": [
            {
                "exchangeId": exchange_id,
                "marketId": market_id,
                "tournamentId": "cup",
                "at": "2026-10-01T16:10:28.500Z",
            }
        ],
        "marketSettled": [],
        "books": [
            {
                "exchangeId": int(exchange_id),
                "asOf": {"sequence": 1854301, "at": "2026-10-01T16:10:28.600Z"},
                "nextExpiryAt": "2026-10-01T17:00:00Z",
                "bids": [{"price": 0.06, "quantity": 100}],
                "asks": [{"price": 0.08, "quantity": 120}],
            }
        ],
        "delivery": {
            "model": "best-effort-authoritative-resync",
            "correlationId": f"realtime:tournament:cup:market:{market_id}:{revision}",
            "revision": revision,
            "previousRevision": previous,
            "sourceSequenceFrom": 1854201,
            "sourceSequenceThrough": 1854301,
        },
    }


def test_live_market_topic_batch_shape_validates() -> None:
    batch = MarketBatchDto.model_validate(_live_market_batch(revision=141, previous=140))
    assert batch.trades[0].id == "1854300"
    assert batch.trades[0].sequence == 1854300
    assert batch.books[0].exchange_id == "36"
    assert batch.books[0].as_of.sequence == 1854301
    assert batch.resync_required is False

    compact = MarketBatchDto.model_validate(
        {
            "resyncRequired": True,
            "delivery": _live_market_batch(revision=2, previous=1)["delivery"],
        }
    )
    assert compact.resync_required is True
    assert compact.trades == () and compact.books == ()


def test_engine_subscribes_per_market_topics_not_tournament_topic(
    tmp_path: Path,
) -> None:
    """Regression: SIG no longer publishes market_batch on tournament:{id}."""

    async def scenario() -> None:
        rest = FakeRest()
        rest.market_status["27"] = "settled"
        rest.settled_with["27"] = "YES"
        engine, recorder = _engine(tmp_path, rest)
        await engine.initialize()
        assert engine.subscription_topics() == ("tournament:cup:market:26",)
        assert engine.subscription_topics(exchange_ids={"36"}) == (
            "tournament:cup:market:26",
        )
        assert engine.subscription_topics(exchange_ids={"not-mapped"}) == ()
        assert engine.topic not in engine.subscription_topics()
        await engine.aclose()
        recorder.close()

    asyncio.run(scenario())


def test_market_topic_batches_record_trades_with_topic_local_revisions(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        rest = FakeRest()
        engine, recorder = _engine(tmp_path, rest)
        await engine.initialize()
        engine.mark_connected()
        observed = datetime(2026, 10, 1, 16, 10, 28, tzinfo=UTC)
        topic_a = engine.market_topic("26")
        topic_b = engine.market_topic("27")

        await engine.handle_raw_batch(
            topic_a, _live_market_batch(revision=141, previous=140), observed
        )
        # Independent revision sequence on another market topic is not a gap.
        await engine.handle_raw_batch(
            topic_b,
            _live_market_batch(
                market_id="27", exchange_id="37", revision=7, previous=6
            ),
            observed,
        )
        await engine.handle_raw_batch(
            topic_a, _live_market_batch(revision=142, previous=141), observed
        )
        # Duplicate (retried send) is ignored, not treated as a gap.
        await engine.handle_raw_batch(
            topic_a, _live_market_batch(revision=141, previous=140), observed
        )

        assert engine.health.revision_gap_count == 0
        assert engine.topic_revisions == {topic_a: 142, topic_b: 7}
        assert engine.health.last_realtime_receive == observed
        assert rest.market_calls == []
        connection = sqlite3.connect(tmp_path / "sig.sqlite3")
        trade_topics = connection.execute(
            "SELECT topic FROM realtime_trades ORDER BY id"
        ).fetchall()
        delivery_count = connection.execute(
            "SELECT COUNT(*) FROM realtime_deliveries"
        ).fetchone()
        connection.close()
        assert trade_topics == [(topic_a,), (topic_b,), (topic_a,)]
        assert delivery_count == (4,)
        await engine.aclose()
        recorder.close()

    asyncio.run(scenario())


def test_market_topic_gap_and_resync_required_recover_only_that_market(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        rest = FakeRest(market_count=237)
        engine, recorder = _engine(tmp_path, rest, tracked={"e-0", "e-1"})
        await engine.initialize()
        initial_bulk = len(rest.bulk_calls)
        observed = datetime(2026, 10, 1, 16, 10, 28, tzinfo=UTC)
        topic = engine.market_topic("m-0")

        def batch(revision: int, previous: int) -> dict[str, object]:
            payload = _live_market_batch(
                market_id="m-0", exchange_id="0", revision=revision, previous=previous
            )
            payload["trades"] = [
                {**trade, "exchangeId": "e-0"}
                for trade in cast(list[dict[str, object]], payload["trades"])
            ]
            payload["bookDirty"] = [
                {**item, "exchangeId": "e-0"}
                for item in cast(list[dict[str, object]], payload["bookDirty"])
            ]
            payload["books"] = []
            return payload

        await engine.handle_raw_batch(topic, batch(10, 9), observed)
        books_before = len(rest.calls)
        await engine.handle_raw_batch(topic, batch(12, 11), observed)

        assert engine.health.revision_gap_count == 1
        assert rest.market_calls == ["m-0"]
        assert rest.calls[books_before:].count("e-1") == 0
        # Market-scoped recovery: no full-universe bulk sweep or market listing.
        assert rest.bulk_calls[initial_bulk:] == [("e-0",)]

        await engine.handle_raw_batch(
            topic,
            {"resyncRequired": True, "delivery": batch(13, 12)["delivery"]},
            observed,
        )
        assert engine.topic_revisions[topic] == 13
        assert rest.market_calls == ["m-0", "m-0"]
        assert engine.health.revision_gap_count == 1

        with pytest.raises(ValueError):
            await engine.handle_raw_batch(
                "tournament:other:market:m-0", batch(14, 13), observed
            )
        await engine.aclose()
        recorder.close()


def _engine_recorder_method_names() -> set[str]:
    import ast

    import predictions_cup.sig.realtime_state as realtime_state

    tree = ast.parse(Path(realtime_state.__file__).read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Attribute)
            and node.value.attr == "_recorder"
            and isinstance(node.value.value, ast.Name)
            and node.value.value.id == "self"
        ):
            names.add(node.attr)
    return names


def test_make_noop_recorder_covers_every_engine_recorder_call() -> None:
    from predictions_cup.maker.noop_recorder import NoopSigRealtimeRecorder

    called = _engine_recorder_method_names()
    assert {"record_health", "record_raw_batch", "record_transition"} <= called
    noop = NoopSigRealtimeRecorder()
    missing = sorted(name for name in called if not callable(getattr(noop, name, None)))
    assert missing == []


def test_make_noop_recorder_survives_connect_batches_and_disconnect() -> None:
    from typing import cast

    from predictions_cup.maker.noop_recorder import NoopSigRealtimeRecorder

    async def scenario() -> None:
        rest = FakeRest()
        engine = SigRealtimeStateEngine(
            rest=rest,
            recorder=cast(SigRealtimeRecorder, NoopSigRealtimeRecorder()),
            tournament_id="cup",
            tracked_depth_exchange_ids={"36"},
        )
        await engine.initialize()
        engine.mark_connected()
        assert engine.health.connected is True
        observed = datetime(2026, 9, 25, 14, 0, tzinfo=UTC)
        await engine.handle_raw_batch(
            engine.topic,
            _batch(revision=5, previous=4, source_from=100, source_through=100),
            observed,
        )
        assert engine.last_accepted_revision == 5
        # Malformed payload exercises the validation-error record_raw_batch path.
        await engine.handle_raw_batch(engine.topic, {"not": "a batch"}, observed)
        await engine.maintenance(observed + timedelta(seconds=1))
        engine.mark_disconnected()
        assert engine.health.connected is False

    asyncio.run(scenario())


def test_book_next_expiry_triggers_one_targeted_refetch_without_dropping_trust(
    tmp_path: Path,
) -> None:
    """SIG emits no event on order expiry; nextExpiryAt is the only signal."""

    async def scenario() -> None:
        rest = FakeRest()
        engine, recorder = _engine(tmp_path, rest, tracked={"36"}, max_age=30.0)
        await engine.initialize()
        state = engine.states["36"]
        assert state.last_rest_observed_at is not None

        await engine.handle_raw_batch(
            engine.topic,
            _live_market_batch(revision=1, previous=0, with_trade=False),
            state.last_rest_observed_at,
        )
        assert state.next_expiry_at == datetime(2026, 10, 1, 17, 0, tzinfo=UTC)

        base = state.last_rest_observed_at
        state.next_expiry_at = base + timedelta(seconds=5)
        calls = len(rest.calls)

        await engine.refresh_stale_open_books(base + timedelta(seconds=5))
        assert len(rest.calls) == calls  # inside the grace window

        await engine.refresh_stale_open_books(base + timedelta(seconds=6))
        assert len(rest.calls) == calls + 1
        assert "36" in str(rest.calls[-1])
        assert state.trusted
        assert state.next_expiry_at is None

        await engine.refresh_stale_open_books(base + timedelta(seconds=7))
        assert len(rest.calls) == calls + 1
        await engine.aclose()
        recorder.close()

    asyncio.run(scenario())
