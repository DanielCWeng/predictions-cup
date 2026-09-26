"""DATA-001 real-data fixture through unmodified BUILD-005 replay and EXPERIMENT-002."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path

import pyarrow.parquet as pq

from predictions_cup.historical.fills import FILL_SCHEMA
from predictions_cup.historical.pmxt import SCHEMAS
from predictions_cup.historical.smoke import run_smoke
from predictions_cup.replay import (
    CaptureSelection,
    QuotePayload,
    ReplayEvent,
    ReplayEventType,
    TradePayload,
    load_polymarket_capture,
)

FIXTURE = Path(__file__).parent / "fixtures" / "data_001_corpus"
MANIFEST = json.loads((FIXTURE / "fixture_manifest.json").read_text(encoding="utf-8"))
REGIME = FIXTURE / MANIFEST["regime_id"]
BOOKS = REGIME / "books"
START = datetime.fromisoformat(MANIFEST["start_at"])
END = datetime.fromisoformat(MANIFEST["end_at_exclusive"])
TOKENS = tuple(MANIFEST["tokens"])


def test_fixture_bytes_match_their_manifest() -> None:
    for rel, record in MANIFEST["files"].items():
        data = (FIXTURE / rel).read_bytes()
        assert hashlib.sha256(data).hexdigest() == record["sha256"]
        assert pq.ParquetFile(FIXTURE / rel).metadata.num_rows == record["rows"]


def test_real_corpus_rows_load_through_build005_deterministically() -> None:
    events = load_polymarket_capture(BOOKS)
    assert len(events) > 0
    assert events == load_polymarket_capture(BOOKS)
    assert [e.sort_key for e in events] == sorted(e.sort_key for e in events)
    assert {e.instrument_id for e in events} <= set(TOKENS)
    assert {ReplayEventType.BOOK_CHANGE} <= {e.event_type for e in events}


def test_observable_time_constraints_hold_on_real_rows() -> None:
    events = load_polymarket_capture(BOOKS)
    for event in events:
        assert START <= event.observed_at < END
        if isinstance(event.payload, QuotePayload):
            assert event.payload.quote_observed_at <= event.observed_at
    # A bounded slice sees nothing from after its end and inherits nothing from before its start.
    mid = START + (END - START) / 2
    early = load_polymarket_capture(BOOKS, selection=CaptureSelection(end_at=mid))
    assert all(e.observed_at < mid for e in early)

    def content(evts: tuple[ReplayEvent, ...]) -> list[tuple[object, ...]]:
        # BUILD-005 numbers `sequence` by load position, so compare everything else.
        return [
            (e.observed_at, e.source_at, e.event_type, e.instrument_id, e.market_id, e.payload)
            for e in evts
        ]

    assert content(early) == content(tuple(e for e in events if e.observed_at < mid))


def test_book_observations_are_not_fills_and_provenance_is_retained() -> None:
    for stream, grade in (
        ("depth_snapshots", "BOOK_SNAPSHOT"),
        ("book_changes", "PRICE_ONLY"),
        ("trades", "TRADE_FILL"),
    ):
        table = pq.ParquetFile(BOOKS / stream / "part-0.parquet").read()
        assert table.schema.equals(SCHEMAS[stream])
        assert set(table["evidence_grade"].to_pylist()) <= {grade}
        assert set(table["source_version"].to_pylist()) <= {"PMXT_V1", "PMXT_V2"}

    fills = pq.ParquetFile(REGIME / "fills" / "part-0.parquet").read()
    assert fills.schema.equals(FILL_SCHEMA)
    assert set(fills["evidence_grade"].to_pylist()) <= {"TRADE_FILL"}
    # The book loader never sees fills, and fills carry no quote state.
    assert load_polymarket_capture(REGIME / "fills") == ()
    for event in load_polymarket_capture(BOOKS):
        if event.event_type is ReplayEventType.TRADE:
            assert isinstance(event.payload, TradePayload)


def test_experiment002_consumes_real_rows_without_rewriting() -> None:
    first = run_smoke(
        BOOKS,
        dataset_id=f"DATA-001:{MANIFEST['regime_id']}:fixture",
        target_token=TOKENS[0],
        reference_token=TOKENS[1],
        start_at=START,
        end_at=END,
    )
    second = run_smoke(
        BOOKS,
        dataset_id=f"DATA-001:{MANIFEST['regime_id']}:fixture",
        target_token=TOKENS[0],
        reference_token=TOKENS[1],
        start_at=START,
        end_at=END,
    )
    assert first == second
    assert first["replay_events_loaded"] > 0
    assert first["observations"] > 0
