from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import cast

import pyarrow.dataset as ds

from predictions_cup.analysis.first_hours import run
from predictions_cup.models import OrderBook, OrderBookLevel
from predictions_cup.sig.dto import PriceSnapshotDto
from predictions_cup.sig.launch_storage import LaunchSigRecorder
from predictions_cup.sig.realtime_models import RealtimeDeliveryDto, RealtimeTradeDto


def _book(at: datetime, *, bid_quantity: str, ask_quantity: str) -> OrderBook:
    return OrderBook(
        exchange_id="sig-exchange-1",
        bids=(
            OrderBookLevel(
                price=Decimal("0.40"),
                quantity=Decimal(bid_quantity),
            ),
        ),
        asks=(
            OrderBookLevel(
                price=Decimal("0.60"),
                quantity=Decimal(ask_quantity),
            ),
        ),
        timestamp=at,
        source="sig-rest",
        revision=None,
    )


def _rows(root: Path, stream: str) -> list[dict[str, object]]:
    files = sorted((root / stream).rglob("*.parquet"))
    assert files
    rows = ds.dataset(
        [str(path) for path in files],
        format="parquet",
    ).to_table().to_pylist()
    return cast(list[dict[str, object]], rows)


def test_launch_recorder_persists_replayable_evidence_and_first_hours_report(
    tmp_path: Path,
) -> None:
    root = tmp_path / "sig_research"
    recorder = LaunchSigRecorder(
        tmp_path / "sig.sqlite3",
        research_root=root,
        shard_seconds=1,
        max_rows_per_shard=1_000,
        queue_max=1_000,
        session_id="test-session",
    )
    at = datetime(2026, 10, 1, 16, 0, tzinfo=UTC)
    trade_payload: dict[str, object] = {
        "exchangeId": "sig-exchange-1",
        "marketId": "sig-market-1",
        "price": "0.50",
        "quantity": "3",
        "executedAt": at.isoformat(),
        "tournamentId": "cup",
    }
    delivery_payload: dict[str, object] = {
        "revision": 1,
        "previousRevision": 0,
        "correlationId": "corr-1",
        "sourceSequenceFrom": 10,
        "sourceSequenceThrough": 10,
    }
    payload: dict[str, object] = {
        "trades": [trade_payload],
        "bookDirty": [],
        "marketSettled": [],
        "delivery": delivery_payload,
    }
    recorder.record_raw_batch(
        topic="tournament:cup",
        payload=payload,
        observed_at=at,
        monotonic_receive_ns=123,
        parsed_at=at + timedelta(microseconds=50),
        validation_error=None,
    )
    recorder.record_delivery(
        topic="tournament:cup",
        delivery=RealtimeDeliveryDto.model_validate(delivery_payload),
        observed_at=at,
    )
    recorder.record_trade(
        topic="tournament:cup",
        revision=1,
        trade=RealtimeTradeDto.model_validate(trade_payload),
        observed_at=at,
    )
    recorder.record_prices(
        tournament_id="cup",
        prices=(
            PriceSnapshotDto.model_validate(
                {
                    "exchangeId": "sig-exchange-1",
                    "marketId": "sig-market-1",
                    "option": "YES",
                    "latestPrice": "0.50",
                    "bestBid": "0.40",
                    "bestAsk": "0.60",
                    "spread": "0.20",
                }
            ),
        ),
        observed_at=at,
        reason="test",
    )
    recorder.record_book(
        market_id="sig-market-1",
        tournament_id="cup",
        book=_book(at, bid_quantity="10", ask_quantity="8"),
        observed_at=at,
        reason="seed",
        triggering_revision=1,
    )
    recorder.record_book(
        market_id="sig-market-1",
        tournament_id="cup",
        book=_book(at + timedelta(seconds=1), bid_quantity="7", ask_quantity="12"),
        observed_at=at + timedelta(seconds=1),
        reason="book_dirty",
        triggering_revision=2,
    )
    recorder.record_transition(
        topic="tournament:cup",
        transition="TRUSTED_AFTER_RECONCILIATION",
        observed_at=at + timedelta(seconds=1),
        exchange_id="sig-exchange-1",
        revision=2,
    )
    recorder.close()

    raw = _rows(root, "raw_events")
    assert raw[0]["session_id"] == "test-session"
    assert raw[0]["monotonic_receive_ns"] == 123
    assert json.loads(str(raw[0]["raw_json"]))["delivery"]["revision"] == 1

    normalized = _rows(root, "normalized_events")
    event_types = {str(row["event_type"]) for row in normalized}
    expected = {
        "DELIVERY",
        "TRADE",
        "BBO_SNAPSHOT",
        "DEPTH_SNAPSHOT",
        "TRUST_TRANSITION",
    }
    assert expected <= event_types

    liquidity = _rows(root, "liquidity_events")
    decreased = [
        row
        for row in liquidity
        if row["change_type"] == "LEVEL_DECREASED"
    ]
    assert decreased
    assert all(row["evidence_label"] == "AMBIGUOUS_DEPTH_DECREASE" for row in decreased)

    report_root = tmp_path / "first_hours"
    summary = run(
        input_root=root,
        output_root=report_root,
        polymarket_root=None,
        execution_journal=None,
        mapping_path=None,
    )
    assert summary["sig"]["raw_batches"] == 1
    assert summary["sig"]["event_counts"]["TRADE"] == 1
    assert summary["sig"]["bbo_rows"] == 1
    assert (report_root / "summary.json").exists()
    assert (report_root / "report.md").exists()
    assert (report_root / "market_activity.csv").exists()


def test_launch_recorder_restart_never_corrupts_published_shards(tmp_path: Path) -> None:
    root = tmp_path / "sig_research"
    at = datetime(2026, 10, 1, 16, 0, tzinfo=UTC)

    for index in range(2):
        recorder = LaunchSigRecorder(
            tmp_path / f"sig-{index}.sqlite3",
            research_root=root,
            shard_seconds=1,
            max_rows_per_shard=1_000,
            queue_max=1_000,
            session_id=f"session-{index}",
        )
        payload = {
            "trades": [],
            "bookDirty": [],
            "marketSettled": [],
            "delivery": {
                "revision": index + 1,
                "previousRevision": index,
                "correlationId": f"corr-{index}",
                "sourceSequenceFrom": index,
                "sourceSequenceThrough": index,
            },
        }
        recorder.record_raw_batch(
            topic="tournament:cup",
            payload=payload,
            observed_at=at + timedelta(seconds=index),
            monotonic_receive_ns=index + 1,
            parsed_at=at + timedelta(seconds=index),
            validation_error=None,
        )
        recorder.close()

    rows = _rows(root, "raw_events")
    assert {str(row["session_id"]) for row in rows} == {"session-0", "session-1"}
    assert not tuple(root.rglob("*.tmp"))
