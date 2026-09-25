from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from predictions_cup.models import OrderBook, OrderBookLevel
from predictions_cup.sig.dto import MarketDto, OrderBookSnapshotDto
from predictions_cup.sig.realtime_models import (
    BookDirtyDto,
    MarketBatchDto,
    MarketSettledDto,
    RealtimeDeliveryDto,
    RealtimeTokenDto,
    RealtimeTradeDto,
)
from predictions_cup.sig.realtime_state import SigRealtimeStateEngine, SubscriptionReason
from predictions_cup.sig.realtime_storage import SigRealtimeRecorder


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
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.market_calls: list[str] = []
        self.fail: set[str] = set()
        self.market_status = {"26": "open", "27": "open"}
        self.settled_with: dict[str, str | None] = {"26": None, "27": None}

    def _market(self, market_id: str) -> MarketDto:
        exchange_id = "36" if market_id == "26" else "37"
        return _market(
            market_id,
            exchange_id,
            status=self.market_status[market_id],
            settled_with=self.settled_with[market_id],
        )

    async def iter_markets(
        self,
        *,
        limit: int = 100,
        tournament_id: str | None = None,
    ) -> AsyncIterator[MarketDto]:
        del limit, tournament_id
        for market_id in ("26", "27"):
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

    async def get_orderbook(
        self,
        exchange_id: str,
        *,
        depth: int = 20,
        tournament_id: str | None = None,
    ) -> OrderBookSnapshotDto:
        del depth, tournament_id
        self.calls.append(exchange_id)
        if exchange_id in self.fail:
            raise RuntimeError("synthetic REST failure")
        market_id = "26" if exchange_id == "36" else "27"
        return OrderBookSnapshotDto.model_validate(
            {
                "exchangeId": exchange_id,
                "marketId": market_id,
                "depth": 20,
                "bids": [{"price": 0.4, "quantity": 10}],
                "asks": [{"price": 0.6, "quantity": 12}],
                "bestBid": 0.4,
                "bestAsk": 0.6,
                "spread": 0.2,
            }
        )


def _batch(
    *,
    revision: int,
    previous: int,
    source_from: int,
    source_through: int,
    dirty: bool = False,
    settled_market_id: str | None = None,
) -> dict[str, object]:
    return {
        "trades": [
            {
                "exchangeId": "36",
                "marketId": "26",
                "price": 0.45,
                "quantity": 3,
                "executedAt": "2026-09-25T14:00:00Z",
                "tournamentId": "cup",
            }
        ],
        "bookDirty": (
            [
                {
                    "exchangeId": "36",
                    "marketId": "26",
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


def test_initial_seed_runs_once_and_source_sequence_is_not_gap_counter(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        rest = FakeRest()
        recorder = SigRealtimeRecorder(tmp_path / "sig.sqlite3")
        engine = SigRealtimeStateEngine(rest=rest, recorder=recorder, tournament_id="cup")
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
        assert calls_after_first == 2

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
        assert engine.health_snapshot()["market_count"] == 2
        assert engine.health_snapshot()["trusted_exchange_count"] == 2
        recorder.close()

    asyncio.run(scenario())


def test_duplicate_gap_and_failed_reconciliation_semantics(tmp_path: Path) -> None:
    async def scenario() -> None:
        rest = FakeRest()
        recorder = SigRealtimeRecorder(tmp_path / "sig.sqlite3")
        engine = SigRealtimeStateEngine(rest=rest, recorder=recorder, tournament_id="cup")
        await engine.initialize()
        observed = datetime(2026, 9, 25, 14, 0, tzinfo=UTC)
        await engine.handle_raw_batch(
            engine.topic,
            _batch(revision=10, previous=9, source_from=1, source_through=1),
            observed,
        )
        calls_after_first = len(rest.calls)
        assert calls_after_first == 2

        await engine.handle_raw_batch(
            engine.topic,
            _batch(revision=10, previous=9, source_from=1, source_through=1),
            observed,
        )
        assert len(rest.calls) == calls_after_first

        await engine.handle_raw_batch(
            engine.topic,
            _batch(revision=12, previous=11, source_from=2, source_through=2),
            observed + timedelta(seconds=1),
        )
        assert engine.health.revision_gap_count == 1
        assert len(rest.calls) == calls_after_first + 2

        rest.fail.add("36")
        await engine.handle_raw_batch(
            engine.topic,
            _batch(
                revision=13,
                previous=12,
                source_from=3,
                source_through=3,
                dirty=True,
            ),
            observed + timedelta(seconds=2),
        )
        assert engine.states["36"].trusted is False
        assert engine.health.reconciliation_failure_count == 1
        recorder.close()

    asyncio.run(scenario())


def test_lifecycle_reasons_resync_and_persist_transitions(tmp_path: Path) -> None:
    async def scenario() -> None:
        db = tmp_path / "sig.sqlite3"
        rest = FakeRest()
        recorder = SigRealtimeRecorder(db)
        engine = SigRealtimeStateEngine(rest=rest, recorder=recorder, tournament_id="cup")
        await engine.initialize()
        for reason in (
            SubscriptionReason.RECONNECT,
            SubscriptionReason.TOKEN_REFRESH,
            SubscriptionReason.SOCKET_ERROR,
        ):
            await engine.prepare_subscription(reason)
        assert engine.health.reconnect_count == 1
        assert len(rest.calls) == 8
        assert all(state.trusted for state in engine.states.values())
        recorder.close()

        connection = sqlite3.connect(db)
        transitions = {
            row[0] for row in connection.execute("SELECT transition FROM trust_transitions")
        }
        market_observations = connection.execute(
            "SELECT COUNT(*) FROM market_observations"
        ).fetchone()
        connection.close()
        assert "UNTRUSTED_RECONNECT" in transitions
        assert "UNTRUSTED_TOKEN_REFRESH" in transitions
        assert "UNTRUSTED_SOCKET_ERROR" in transitions
        assert "TRUSTED_AFTER_RECONCILIATION" in transitions
        assert market_observations == (8,)

    asyncio.run(scenario())


def test_malformed_batch_full_resync_never_advances_revision(tmp_path: Path) -> None:
    async def scenario() -> None:
        rest = FakeRest()
        recorder = SigRealtimeRecorder(tmp_path / "sig.sqlite3")
        engine = SigRealtimeStateEngine(rest=rest, recorder=recorder, tournament_id="cup")
        await engine.initialize()
        calls_before = len(rest.calls)
        observed = datetime(2026, 9, 25, 14, 0, tzinfo=UTC)

        await engine.handle_raw_batch(
            engine.topic,
            {"trades": [], "bookDirty": [], "marketSettled": []},
            observed,
        )

        assert engine.last_accepted_revision is None
        assert len(rest.calls) == calls_before + 2
        assert all(state.trusted for state in engine.states.values())
        recorder.close()

    asyncio.run(scenario())


def test_market_settlement_refetches_market_and_clears_stale_book(tmp_path: Path) -> None:
    async def scenario() -> None:
        db = tmp_path / "sig.sqlite3"
        rest = FakeRest()
        recorder = SigRealtimeRecorder(db)
        engine = SigRealtimeStateEngine(rest=rest, recorder=recorder, tournament_id="cup")
        await engine.initialize()
        initial_book_calls = len(rest.calls)

        rest.market_status["26"] = "settled"
        rest.settled_with["26"] = "YES"
        observed = datetime(2026, 9, 25, 14, 0, tzinfo=UTC)
        await engine.handle_raw_batch(
            engine.topic,
            _batch(
                revision=1,
                previous=0,
                source_from=1,
                source_through=1,
                settled_market_id="26",
            ),
            observed,
        )

        assert rest.market_calls == ["26"]
        assert len(rest.calls) == initial_book_calls
        assert engine.market_states["26"].status == "settled"
        assert engine.market_states["26"].settled_with == "YES"
        assert engine.states["36"].trusted is True
        assert engine.states["36"].orderbook is None
        recorder.close()

        connection = sqlite3.connect(db)
        row = connection.execute(
            """
            SELECT settled_with, source_at, observed_at
            FROM market_settled_events
            WHERE market_id = '26'
            """
        ).fetchone()
        market_row = connection.execute(
            """
            SELECT status, settled_with, reason
            FROM market_observations
            WHERE market_id = '26'
            ORDER BY id DESC LIMIT 1
            """
        ).fetchone()
        connection.close()
        assert row is not None
        assert row[0] == "YES"
        assert row[1] != row[2] or row[1].startswith("2026-09-25T14:00:00")
        assert market_row == ("settled", "YES", "market_settled")

    asyncio.run(scenario())


def test_recorder_normalization_wal_and_retention(tmp_path: Path) -> None:
    path = tmp_path / "sig.sqlite3"
    recorder = SigRealtimeRecorder(path)
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
    recorder.record_delivery(topic="tournament:cup", delivery=delivery, observed_at=old)
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
        "book_observations",
        "trust_transitions",
    ):
        assert connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone() == (0,)
    assert connection.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
    connection.close()
