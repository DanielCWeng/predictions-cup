from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from predictions_cup.models import OrderBook, OrderBookLevel
from predictions_cup.sig.dto import ExchangeListItemDto, OrderBookSnapshotDto
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


class FakeRest:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.fail: set[str] = set()
        self.exchanges = (
            ExchangeListItemDto.model_validate(
                {
                    "id": "36",
                    "marketId": "26",
                    "option": "YES",
                    "latestPrice": 0.5,
                    "initialPrice": 0.5,
                    "contexts": [],
                }
            ),
            ExchangeListItemDto.model_validate(
                {
                    "id": "37",
                    "marketId": "27",
                    "option": "YES",
                    "latestPrice": 0.5,
                    "initialPrice": 0.5,
                    "contexts": [],
                }
            ),
        )

    async def iter_exchanges(
        self,
        *,
        market_id: str | None = None,
        tournament_id: str | None = None,
        limit: int = 200,
    ) -> AsyncIterator[ExchangeListItemDto]:
        del market_id, tournament_id, limit
        for exchange in self.exchanges:
            yield exchange

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
        "marketSettled": [],
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


def test_revision_continuity_uses_delivery_not_engine_source_sequence(tmp_path: Path) -> None:
    async def scenario() -> None:
        rest = FakeRest()
        recorder = SigRealtimeRecorder(tmp_path / "sig.sqlite3")
        engine = SigRealtimeStateEngine(rest=rest, recorder=recorder, tournament_id="cup")
        await engine.initialize()
        await engine.prepare_subscription(SubscriptionReason.INITIAL_SUBSCRIBE)
        engine.mark_connected()
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
        assert all(state.trusted for state in engine.states.values())
        recorder.close()

        connection = sqlite3.connect(db)
        transitions = {
            row[0] for row in connection.execute("SELECT transition FROM trust_transitions")
        }
        connection.close()
        assert "UNTRUSTED_RECONNECT" in transitions
        assert "UNTRUSTED_TOKEN_REFRESH" in transitions
        assert "UNTRUSTED_SOCKET_ERROR" in transitions
        assert "TRUSTED_AFTER_RECONCILIATION" in transitions

    asyncio.run(scenario())


def test_malformed_batch_resynchronizes_and_never_advances_revision(
    tmp_path: Path,
) -> None:
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
        "book_observations",
        "trust_transitions",
    ):
        assert connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone() == (0,)
    assert connection.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
    connection.close()
