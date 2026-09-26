"""DATA-001 normalization, determinism and window/no-lookahead guarantees (synthetic)."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from predictions_cup.historical import pmxt
from predictions_cup.historical.corpus import build_corpus, validate_corpus
from predictions_cup.historical.fills import normalize_fills
from predictions_cup.historical.regimes import Regime, pmxt_version_for_hour
from predictions_cup.replay import (
    CaptureSchemaError,
    QuotePayload,
    ReplayEventType,
    load_polymarket_capture,
)

T0 = datetime(2026, 6, 1, 12, tzinfo=UTC)
START = T0
END = T0 + timedelta(hours=1)
CID = "0x" + "a" * 64
OTHER_CID = "0x" + "b" * 64
YES = "111"
NO = "222"
FOREIGN = "999"
TOKENS = frozenset({YES, NO})

_V2 = pa.schema(
    [
        ("timestamp_received", pa.timestamp("ms", tz="UTC")),
        ("timestamp", pa.timestamp("ms", tz="UTC")),
        ("market", pa.string()),
        ("event_type", pa.string()),
        ("asset_id", pa.string()),
        ("bids", pa.string()),
        ("asks", pa.string()),
        ("price", pa.decimal128(9, 4)),
        ("size", pa.decimal128(18, 6)),
        ("side", pa.string()),
        ("best_bid", pa.decimal128(9, 4)),
        ("best_ask", pa.decimal128(9, 4)),
        ("fee_rate_bps", pa.uint16()),
        ("transaction_hash", pa.string()),
        ("old_tick_size", pa.decimal128(9, 4)),
        ("new_tick_size", pa.decimal128(9, 4)),
    ]
)
_V1 = pa.schema(
    [
        ("timestamp_received", pa.timestamp("ms", tz="UTC")),
        ("timestamp_created_at", pa.timestamp("ms", tz="UTC")),
        ("market_id", pa.string()),
        ("update_type", pa.string()),
        ("data", pa.string()),
    ]
)


def _v2_row(offset_ms: int, event_type: str, token: str = YES, **values: object) -> dict[str, object]:
    received = T0 + timedelta(milliseconds=offset_ms)
    row: dict[str, object] = {name: None for name in _V2.names}
    row.update(
        timestamp_received=received,
        timestamp=received - timedelta(milliseconds=40),
        market=CID,
        event_type=event_type,
        asset_id=token,
    )
    for key, value in values.items():
        row[key] = Decimal(value) if key in {"price", "size", "best_bid", "best_ask",
                                              "old_tick_size", "new_tick_size"} else value
    return row


def _v2_table(rows: list[dict[str, object]]) -> pa.Table:
    return pa.Table.from_pylist(rows, schema=_V2)


def _v2_sample() -> pa.Table:
    return _v2_table(
        [
            # Source lists asks high-to-low; normalization must reorder.
            _v2_row(0, "book", bids='[["0.40","10"],["0.41","5"]]',
                    asks='[["0.60","7"],["0.55","3"]]'),
            _v2_row(5, "price_change", side="BUY", price="0.42", size="12",
                    best_bid="0.42", best_ask="0.55"),
            _v2_row(5, "price_change", side="SELL", price="0.55", size="0",
                    best_bid="0.42", best_ask="0.60"),
            _v2_row(9, "last_trade_price", price="0.55", size="3", side="BUY",
                    fee_rate_bps=0, transaction_hash="0xabc"),
            _v2_row(11, "tick_size_change", old_tick_size="0.01", new_tick_size="0.001"),
            # Crossed BBO is kept and surfaced, not repaired.
            _v2_row(20, "price_change", side="BUY", price="0.70", size="1",
                    best_bid="0.70", best_ask="0.60"),
            # Duplicate price level: malformed book, rejected visibly.
            _v2_row(30, "book", bids='[["0.40","1"],["0.40","2"]]', asks='[["0.60","1"]]'),
            # Out-of-range price in a change row.
            _v2_row(31, "price_change", side="BUY", price="1.20", size="1",
                    best_bid="0.40", best_ask="0.60"),
            # Foreign token and rows outside the window never enter the corpus.
            _v2_row(40, "price_change", token=FOREIGN, side="BUY", price="0.1", size="1",
                    best_bid="0.1", best_ask="0.2"),
            _v2_row(3_600_000, "price_change", side="BUY", price="0.1", size="1",
                    best_bid="0.1", best_ask="0.2"),
        ]
    )


def test_v2_books_normalize_to_build007_streams_with_exact_decimals() -> None:
    out = pmxt.normalize_extract(
        _v2_sample(), source_version="PMXT_V2", token_ids=TOKENS,
        window_start=START, window_end=END,
    )
    assert out.counts["raw_rows"] == 10
    assert out.counts["rows_matching_tokens"] == 9
    assert out.counts["rows_outside_window"] == 1

    snaps = out.tables["depth_snapshots"].to_pylist()
    assert len(snaps) == 1
    snap = snaps[0]
    assert [lvl["price"] for lvl in snap["bids"]] == ["0.41", "0.40"]
    assert [lvl["price"] for lvl in snap["asks"]] == ["0.55", "0.60"]
    assert (snap["best_bid"], snap["best_ask"]) == ("0.41", "0.55")
    assert (snap["midpoint"], snap["spread"]) == ("0.48", "0.14")
    assert snap["recorded_at"] == snap["state_observed_at"] == T0
    assert snap["source_timestamp"] == T0 - timedelta(milliseconds=40)
    assert snap["evidence_grade"] == "BOOK_SNAPSHOT"
    assert snap["market_id"] == CID

    changes = out.tables["book_changes"].to_pylist()
    assert [c["price"] for c in changes] == ["0.42", "0.55", "0.7"]
    assert all(c["evidence_grade"] == "PRICE_ONLY" for c in changes)
    trade = out.tables["trades"].to_pylist()[0]
    assert trade["event_id"].startswith("polymarket-trade:")
    assert (trade["price"], trade["size"], trade["evidence_grade"]) == ("0.55", "3", "TRADE_FILL")
    assert out.tables["tick_size_changes"].num_rows == 1

    reasons = sorted(r["reason"] for r in out.tables["rejects"].to_pylist())
    assert reasons == ["invalid_price", "malformed_bid_levels"]


def test_v1_has_no_venue_event_time_and_filters_tokens_exactly() -> None:
    def data(kind: str, token: str, **extra: object) -> str:
        return json.dumps({"update_type": kind, "token_id": token, "market_id": CID,
                           "timestamp": 1.0, **extra})

    table = pa.Table.from_pylist(
        [
            {"timestamp_received": T0, "timestamp_created_at": T0 + timedelta(seconds=2),
             "market_id": CID, "update_type": "book_snapshot",
             "data": data("book_snapshot", NO, best_bid="0.3", best_ask="0.4",
                          bids=[["0.3", "5"], ["0.2", "1"]], asks=[["0.5", "1"], ["0.4", "2"]])},
            {"timestamp_received": T0 + timedelta(milliseconds=3),
             "timestamp_created_at": T0 + timedelta(seconds=2),
             "market_id": CID, "update_type": "price_change",
             "data": data("price_change", NO, best_bid="0.31", best_ask="0.4",
                          change_price="0.31", change_size="4", change_side="BUY")},
            {"timestamp_received": T0 + timedelta(milliseconds=4),
             "timestamp_created_at": T0 + timedelta(seconds=2),
             "market_id": CID, "update_type": "price_change",
             "data": data("price_change", FOREIGN, best_bid="0.1", best_ask="0.2",
                          change_price="0.1", change_size="1", change_side="BUY")},
        ],
        schema=_V1,
    )
    out = pmxt.normalize_extract(
        table, source_version="PMXT_V1", token_ids=TOKENS, window_start=START, window_end=END
    )
    snap = out.tables["depth_snapshots"].to_pylist()[0]
    assert snap["source_timestamp"] is None
    assert [lvl["price"] for lvl in snap["asks"]] == ["0.4", "0.5"]
    change = out.tables["book_changes"].to_pylist()
    assert len(change) == 1
    assert change[0]["source_timestamp"] is None
    assert change[0]["observed_at"] == T0 + timedelta(milliseconds=3)
    assert change[0]["source_version"] == "PMXT_V1"


def test_routing_rule_matches_ticket() -> None:
    assert pmxt_version_for_hour(datetime(2026, 4, 13, 18, tzinfo=UTC)) == "PMXT_V1"
    assert pmxt_version_for_hour(datetime(2026, 4, 13, 19, tzinfo=UTC)) == "PMXT_V2"


def test_ordering_is_content_deterministic_and_exact_duplicates_drop() -> None:
    out = pmxt.normalize_extract(
        _v2_sample(), source_version="PMXT_V2", token_ids=TOKENS,
        window_start=START, window_end=END,
    )
    changes = out.tables["book_changes"]
    doubled = pa.concat_tables([changes, changes])
    forward, dropped = pmxt.sort_and_dedupe("book_changes", doubled)
    backward, _ = pmxt.sort_and_dedupe("book_changes", doubled.take(list(range(5, -1, -1))))
    assert dropped == changes.num_rows
    assert forward.equals(backward)
    times = forward["observed_at"].to_pylist()
    assert times == sorted(times)


def test_fills_stay_separate_trade_fill_evidence() -> None:
    raw = pa.table(
        {
            "timestamp": pa.array([1780315200, 1780315201, 1780315201, 1780400000], pa.int64()),
            "side": ["buy", "sell", "sell", "buy"],
            "price": [0.84, 0.3, 0.3, 0.5],
            "size_shares": [7.0, 1.5, 1.5, 1.0],
            "value_usd": [5.88, 0.45, 0.45, 0.5],
            "token_id": [YES, NO, NO, YES],
            "condition_id": [CID] * 4,
            "maker_address": ["0xm"] * 4,
            "taker_address": ["0xt"] * 4,
            "tx_hash": ["0x1", "0x2", "0x2", "0x3"],
            "log_index": pa.array([1, 2, 2, 3], pa.int64()),
            "is_exchange_taker": [True, False, False, True],
            "event_id": ["e"] * 4,
            "market_id": ["m"] * 4,
            "outcome_side": ["YES", "NO", "NO", "YES"],
        }
    )
    start = datetime(2026, 6, 1, 12, tzinfo=UTC)
    fills, counts = normalize_fills(
        raw, token_ids=TOKENS, window_start=start, window_end=start + timedelta(hours=1)
    )
    assert counts["rows_outside_window"] == 1
    assert counts["duplicates_removed"] == 1
    rows = fills.to_pylist()
    assert [r["fill_id"] for r in rows] == ["0x1:1:111", "0x2:2:222"]
    assert rows[0]["price"] == "0.84" and rows[0]["source_side"] == "buy"
    assert rows[0]["evidence_grade"] == "TRADE_FILL"
    assert "best_bid" not in fills.schema.names and "bids" not in fills.schema.names


def _write_inputs(tmp_path: Path) -> tuple[Path, Path, Regime]:
    orderbooks = tmp_path / "orderbooks"
    extract = orderbooks / "TST_2026" / "date=2026-06-01" / "hour=12" / "events.parquet"
    extract.parent.mkdir(parents=True)
    pq.write_table(_v2_sample(), extract)
    fills = tmp_path / "fills"
    fills.mkdir()
    pq.write_table(
        pa.table(
            {
                "market_family": ["TST"], "condition_id": [CID], "market_id": ["m1"],
                "yes_token_id": [YES], "no_token_id": [NO], "event_id": ["e1"],
                "question": ["Will X?"], "outcome_labels": ['["Yes", "No"]'],
            }
        ),
        fills / "markets_TST_2026.parquet",
    )
    pq.write_table(
        pa.table(
            {
                "timestamp": pa.array([1780315230], pa.int64()), "side": ["buy"],
                "price": [0.55], "size_shares": [3.0], "value_usd": [1.65], "token_id": [YES],
                "condition_id": [CID], "maker_address": ["0xm"], "taker_address": ["0xt"],
                "tx_hash": ["0xabc"], "log_index": pa.array([4], pa.int64()),
                "is_exchange_taker": [False], "event_id": ["e1"], "market_id": ["m1"],
                "outcome_side": ["YES"],
            }
        ),
        fills / "fills_TST_2026.parquet",
    )
    regime = Regime(
        regime_id="test_regime", country="Testland", round="first_round", family="TST_2026",
        election_date=date(2026, 6, 1), window_start=START, window_end=END,
    )
    return orderbooks, fills, regime


def test_build_is_deterministic_validated_and_loadable_by_build005(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from predictions_cup.historical import corpus as corpus_module

    monkeypatch.setitem(corpus_module.FAMILY_MARKET_FAMILIES, "TST_2026", frozenset({"TST"}))
    orderbooks, fills, regime = _write_inputs(tmp_path)
    first = build_corpus(orderbooks_root=orderbooks, fills_root=fills,
                         output_root=tmp_path / "a", regimes=(regime,))
    second = build_corpus(orderbooks_root=orderbooks, fills_root=fills,
                          output_root=tmp_path / "b", regimes=(regime,))
    assert first == second
    assert validate_corpus(tmp_path / "a")["ok"]

    books = tmp_path / "a" / "schema_version=1" / "test_regime" / "books"
    events = load_polymarket_capture(books)
    assert events == load_polymarket_capture(books)
    assert [e.observed_at for e in events] == sorted(e.observed_at for e in events)
    assert all(START <= e.observed_at < END for e in events)
    for event in events:
        if isinstance(event.payload, QuotePayload):
            assert event.payload.quote_observed_at <= event.observed_at
    types = {e.event_type for e in events}
    assert types == {ReplayEventType.DEPTH_SNAPSHOT, ReplayEventType.BOOK_CHANGE,
                     ReplayEventType.TRADE}

    # Fills are a separate evidence stream: not visible through the book loader.
    fills_dir = tmp_path / "a" / "schema_version=1" / "test_regime" / "fills"
    assert load_polymarket_capture(fills_dir) == ()
    fill_rows = pq.ParquetFile(next(fills_dir.rglob("*.parquet"))).read().to_pylist()
    assert fill_rows[0]["evidence_grade"] == "TRADE_FILL"

    identity = (tmp_path / "a" / "schema_version=1" / "market_identity.csv").read_text()
    assert "test_regime,Testland,first_round,m1," + CID + ",111,Yes" in identity


def test_validation_detects_tampering(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from predictions_cup.historical import corpus as corpus_module

    monkeypatch.setitem(corpus_module.FAMILY_MARKET_FAMILIES, "TST_2026", frozenset({"TST"}))
    orderbooks, fills, regime = _write_inputs(tmp_path)
    build_corpus(orderbooks_root=orderbooks, fills_root=fills, output_root=tmp_path / "c",
                 regimes=(regime,))
    target = next((tmp_path / "c").rglob("book_changes/*/part-0.parquet"))
    table = pq.ParquetFile(target).read()
    pq.write_table(table.slice(1), target)
    report = validate_corpus(tmp_path / "c")
    assert not report["ok"]
    assert any("sha256 mismatch" in p for p in report["problems"])


def test_float_prices_in_book_stream_fail_visibly(tmp_path: Path) -> None:
    stream = tmp_path / "books" / "book_changes" / "date=2026-06-01"
    stream.mkdir(parents=True)
    bad = pa.table(
        {
            "token_id": [YES], "market_id": [CID], "side": ["BUY"], "price": [0.5],
            "size": ["1"], "source_timestamp": pa.array([T0], pa.timestamp("us", tz="UTC")),
            "observed_at": pa.array([T0], pa.timestamp("us", tz="UTC")),
            "best_bid": [0.5], "best_ask": ["0.6"], "book_hash": [None],
        }
    )
    pq.write_table(bad, stream / "part-0.parquet")
    with pytest.raises(CaptureSchemaError):
        load_polymarket_capture(tmp_path / "books")
