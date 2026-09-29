from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import cast

import pyarrow as pa
import pyarrow.dataset as ds
import pyarrow.parquet as pq

from predictions_cup.analysis.cross_venue import analyze_direct_cross_venue
from predictions_cup.analysis.first_hours import run
from predictions_cup.mapping.crosswalk import write_document
from predictions_cup.mapping.models import (
    MappingClass,
    MappingDirection,
    MappingDocument,
    MappingStatus,
    MarketMapping,
    PolymarketContractIdentity,
)
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
    recorder.record_connection_boundary(
        observed_at=at,
        reason="initial_subscribe",
    )
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
    recorder.record_health(
        observed_at=at + timedelta(seconds=1),
        payload={"connected": True},
    )
    recorder.close()

    raw = _rows(root, "raw_events")
    assert raw[0]["session_id"] == "test-session"
    assert raw[0]["connection_epoch"] == 1
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

    journal_path = tmp_path / "execution.sqlite3"
    execution = sqlite3.connect(journal_path)
    execution.execute(
        """
        CREATE TABLE execution_events (
            event_id INTEGER PRIMARY KEY,
            logical_operation_id TEXT NOT NULL,
            event_type TEXT NOT NULL,
            observed_monotonic_ns INTEGER NOT NULL,
            decision_observation_ns INTEGER,
            decision_monotonic_ns INTEGER
        )
        """
    )
    execution.executemany(
        """
        INSERT INTO execution_events (
            event_id, logical_operation_id, event_type, observed_monotonic_ns,
            decision_observation_ns, decision_monotonic_ns
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        [
            (1, "op-1", "SUBMISSION", 300_000_000, 100_000_000, 200_000_000),
            (2, "op-1", "NETWORK_DISPATCH", 400_000_000, None, None),
            (3, "op-1", "ACK", 550_000_000, None, None),
            (4, "op-1", "REALTIME_FILL", 800_000_000, None, None),
        ],
    )
    execution.commit()
    execution.close()

    report_root = tmp_path / "first_hours"
    summary = run(
        input_root=root,
        output_root=report_root,
        polymarket_root=None,
        execution_journal=journal_path,
        mapping_path=None,
    )
    assert summary["sig"]["raw_batches"] == 1
    assert summary["sig"]["event_counts"]["TRADE"] == 1
    assert summary["sig"]["bbo_rows"] == 1
    assert summary["execution"]["observation_to_decision_ms"]["p50"] == 100.0
    assert summary["execution"]["submission_to_dispatch_ms"]["p50"] == 100.0
    assert summary["execution"]["dispatch_to_ack_ms"]["p50"] == 150.0
    assert summary["execution"]["dispatch_to_fill_ms"]["p50"] == 400.0
    assert (report_root / "summary.json").exists()
    assert (report_root / "report.md").exists()
    assert (report_root / "market_activity.csv").exists()
    assert (report_root / "market_microstructure.csv").exists()
    assert (report_root / "markouts.csv").exists()
    assert (report_root / "depth_summary.csv").exists()
    assert (report_root / "activity_15m.csv").exists()
    assert (report_root / "execution_latency.csv").exists()

    connection = sqlite3.connect(tmp_path / "sig.sqlite3")
    health_row = connection.execute(
        "SELECT payload_json FROM capture_health ORDER BY id DESC LIMIT 1"
    ).fetchone()
    connection.close()
    assert health_row is not None
    health = json.loads(str(health_row[0]))
    assert health["connected"] is True
    assert health["research_storage"]["dropped_rows"] == 0
    assert health["research_storage"]["storage_failures"] == 0


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


def _write_rows_parquet(root: Path, stream: str, rows: list[dict[str, object]]) -> None:
    directory = root / stream
    directory.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist(rows), directory / "part.parquet")


def test_cross_venue_complement_alignment_and_response_lags(tmp_path: Path) -> None:
    at = datetime(2026, 10, 1, 16, 0, tzinfo=UTC)
    sig_root = tmp_path / "sig"
    pm_root = tmp_path / "pm"
    mapping_path = tmp_path / "mapping.json"

    _write_rows_parquet(
        sig_root,
        "normalized_events",
        [
            {
                "event_type": "BBO_SNAPSHOT",
                "exchange_id": "sig-1",
                "observed_at": at,
                "best_bid": "0.49",
                "best_ask": "0.51",
                "spread": "0.02",
            },
            {
                "event_type": "BBO_SNAPSHOT",
                "exchange_id": "sig-1",
                "observed_at": at + timedelta(seconds=1),
                "best_bid": "0.59",
                "best_ask": "0.61",
                "spread": "0.02",
            },
            {
                "event_type": "BBO_SNAPSHOT",
                "exchange_id": "sig-1",
                "observed_at": at + timedelta(seconds=2),
                "best_bid": "0.69",
                "best_ask": "0.71",
                "spread": "0.02",
            },
        ],
    )
    _write_rows_parquet(
        pm_root,
        "observations",
        [
            {
                "token_id": "pm-no",
                "observed_at": at,
                "best_bid": "0.49",
                "best_ask": "0.51",
                "spread": "0.02",
                "book_valid": True,
            },
            {
                "token_id": "pm-no",
                "observed_at": at + timedelta(milliseconds=500),
                "best_bid": "0.39",
                "best_ask": "0.41",
                "spread": "0.02",
                "book_valid": True,
            },
            {
                "token_id": "pm-no",
                "observed_at": at + timedelta(milliseconds=2200),
                "best_bid": "0.29",
                "best_ask": "0.31",
                "spread": "0.02",
                "book_valid": True,
            },
        ],
    )
    document = MappingDocument(
        tournament_id="cup",
        records=(
            MarketMapping(
                sig_tournament_id="cup",
                sig_market_id="sig-market",
                sig_market_title="Test",
                sig_exchange_id="sig-1",
                sig_outcome_label="YES",
                mapping_class=MappingClass.EXACT,
                mapping_direction=MappingDirection.COMPLEMENT,
                mapping_confidence=Decimal("1"),
                status=MappingStatus.VERIFIED,
                direct_polymarket=PolymarketContractIdentity(
                    market_id="pm-market",
                    condition_id="pm-condition",
                    question="Test complement",
                    outcomes=("YES", "NO"),
                    token_ids=("pm-yes", "pm-no"),
                    mapped_outcome="NO",
                    mapped_token_id="pm-no",
                ),
            ),
        ),
    )
    write_document(mapping_path, document)

    cross_summary, rows = analyze_direct_cross_venue(
        sig_root=sig_root,
        polymarket_root=pm_root,
        mapping_path=mapping_path,
        max_lag_seconds=10.0,
    )
    assert cross_summary["available"] is True
    assert len(rows) == 1
    row = rows[0]
    assert row["mapping_direction"] == "COMPLEMENT"
    assert abs(float(str(row["latest_pm_aligned_mid"])) - 0.7) < 1e-9
    assert abs(float(str(row["latest_discrepancy"]))) < 1e-9
    assert row["pm_to_sig_matches"] == 1
    assert row["sig_to_pm_matches"] == 2
    assert abs(float(str(row["pm_to_sig_lag_seconds_p50"])) - 0.5) < 1e-9

    report_root = tmp_path / "first_hours_cross"
    summary = run(
        input_root=sig_root,
        output_root=report_root,
        polymarket_root=pm_root,
        execution_journal=None,
        mapping_path=mapping_path,
    )
    latest = summary["cross_venue_latest"][0]
    assert latest["mapping_direction"] == "COMPLEMENT"
    assert abs(float(str(latest["polymarket_aligned_mid"])) - 0.7) < 1e-9
    assert (report_root / "cross_venue_diagnostics.csv").exists()
    assert (report_root / "cross_venue_latest.csv").exists()
