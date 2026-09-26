"""DATA-001 normalization, determinism and window/no-lookahead guarantees (synthetic)."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from predictions_cup.historical import pmxt
from predictions_cup.historical.acquire import plan_hours
from predictions_cup.historical.corpus import (
    build_corpus,
    splice_primary_with_supplement,
    validate_corpus,
)
from predictions_cup.historical.fills import normalize_fills
from predictions_cup.historical.regimes import (
    Regime,
    pmxt_supplement_for_hour,
    pmxt_version_for_hour,
)
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


def _v2_row(
    offset_ms: int,
    event_type: str,
    token: str = YES,
    *,
    base: datetime = T0,
    market: str = CID,
    **values: object,
) -> dict[str, object]:
    received = base + timedelta(milliseconds=offset_ms)
    row: dict[str, object] = {name: None for name in _V2.names}
    row.update(
        timestamp_received=received,
        timestamp=received - timedelta(milliseconds=40),
        market=market,
        event_type=event_type,
        asset_id=token,
    )
    for key, value in values.items():
        decimal_fields = {"price", "size", "best_bid", "best_ask", "old_tick_size",
                          "new_tick_size"}
        row[key] = Decimal(str(value)) if key in decimal_fields else value
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
    # 19:00 is V1-primary (the raw V2 19:00 file only starts at 19:42:26.6), supplemented by
    # V2; every other hour has exactly one source.
    assert pmxt_version_for_hour(datetime(2026, 4, 13, 19, tzinfo=UTC)) == "PMXT_V1"
    assert pmxt_supplement_for_hour(datetime(2026, 4, 13, 19, tzinfo=UTC)) == "PMXT_V2"
    assert pmxt_version_for_hour(datetime(2026, 4, 13, 20, tzinfo=UTC)) == "PMXT_V2"
    assert pmxt_version_for_hour(datetime(2026, 4, 14, 0, tzinfo=UTC)) == "PMXT_V2"
    for hour in (18, 20, 21):
        assert pmxt_supplement_for_hour(datetime(2026, 4, 13, hour, tzinfo=UTC)) is None
    assert pmxt_supplement_for_hour(datetime(2026, 4, 12, 19, tzinfo=UTC)) is None


def test_acquire_plan_can_be_narrowed_to_one_hour() -> None:
    assert plan_hours(frozenset({"2026-04-13T19"})) == [
        {"hour": "2026-04-13T19", "source_version": "PMXT_V1", "extract": "events.parquet",
         "families": ["HUN_2026", "PER_2026"]},
        {"hour": "2026-04-13T19", "source_version": "PMXT_V2", "extract": "events_v2.parquet",
         "families": ["HUN_2026", "PER_2026"]},
    ]
    assert plan_hours(frozenset({"2026-04-13T20"})) == [
        {"hour": "2026-04-13T20", "source_version": "PMXT_V2", "extract": "events.parquet",
         "families": ["HUN_2026", "PER_2026"]}
    ]
    assert len(plan_hours()) > len(plan_hours(frozenset({"2026-04-13T19", "2026-06-01T00"})))
    with pytest.raises(ValueError, match="outside every regime window"):
        plan_hours(frozenset({"2025-01-01T00"}))


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
    from predictions_cup.historical import regimes

    monkeypatch.setitem(regimes.FAMILY_MARKET_FAMILIES, "TST_2026", frozenset({"TST"}))
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
    from predictions_cup.historical import regimes

    monkeypatch.setitem(regimes.FAMILY_MARKET_FAMILIES, "TST_2026", frozenset({"TST"}))
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


def test_quality_reports_gaps_intervals_and_anomalies_without_repair() -> None:
    from predictions_cup.historical.quality import RegimeQuality

    quality = RegimeQuality(regime_id="q", material_gap=timedelta(minutes=5))
    first = pmxt.normalize_extract(
        _v2_sample(), source_version="PMXT_V2", token_ids=TOKENS,
        window_start=START, window_end=END,
    )
    later = _v2_table(
        [_v2_row(10 * 60_000, "price_change", side="BUY", price="0.43", size="2",
                 best_bid="0.43", best_ask="0.55")]
    )
    second = pmxt.normalize_extract(
        later, source_version="PMXT_V2", token_ids=TOKENS, window_start=START, window_end=END
    )
    for out in (first, second):
        tables = {s: pmxt.sort_and_dedupe(s, t)[0] for s, t in out.tables.items()}
        quality.add_book_hour(tables, {})
    report = quality.token_report(YES)
    assert report["book_evidence_rows"] == 5
    # instants: 0 ms, 5 ms (two rows), 20 ms, 10 min
    assert report["unique_timestamps"] == 4
    assert report["median_observation_interval_ms"] == 15
    assert report["material_gap_count"] == 1
    assert report["max_observation_gap_seconds"] == 599.98
    assert report["crossed_bbo_changes"] == 1
    assert report["ambiguous_same_timestamp_bbo_groups"] == 1
    assert report["rejected_malformed_rows"] == {"invalid_price": 1, "malformed_bid_levels": 1}
    assert report["median_bid_levels"] == 2.0
    assert report["evidence_grades"] == ["BOOK_SNAPSHOT", "PRICE_ONLY", "TRADE_FILL"]
    assert quality.regime_report()["regime_wide_material_silences"] == 1


# --- 2026-04-13T19 splice: V1-preferred / V2-only supplementation ----------------------------

SPLICE_HOUR = datetime(2026, 4, 13, 19, tzinfo=UTC)
V2_ONLY_CID = "0x" + "c" * 64
V2_ONLY_YES = "333"
V2_ONLY_NO = "444"
SPLICE_TOKENS = frozenset({YES, NO, V2_ONLY_YES, V2_ONLY_NO})
V2_FIRST_MS = 42 * 60_000 + 26_600  # 19:42:26.6, where the raw V2 19:00 file starts
BOOK = ("depth_snapshots", "book_changes")


def _v1_splice_table(bid: str = "0.40") -> pa.Table:
    def row(offset_ms: int, kind: str, **extra: object) -> dict[str, object]:
        at = SPLICE_HOUR + timedelta(milliseconds=offset_ms)
        data = {"update_type": kind, "token_id": YES, "market_id": CID,
                "timestamp": at.timestamp(), **extra}
        return {"timestamp_received": at, "timestamp_created_at": at + timedelta(seconds=2),
                "market_id": CID, "update_type": kind, "data": json.dumps(data)}

    # V1 records only YES of the shared condition; NO is covered through its condition.
    return pa.Table.from_pylist(
        [
            row(500, "book_snapshot", best_bid=bid, best_ask="0.60",
                bids=[[bid, "5"]], asks=[["0.60", "5"]]),
            row(600_000, "price_change", best_bid=bid, best_ask="0.59",
                change_price="0.59", change_size="2", change_side="SELL"),
            row(V2_FIRST_MS + 1_000, "price_change", best_bid=bid, best_ask="0.58",
                change_price="0.58", change_size="1", change_side="SELL"),
        ],
        schema=_V1,
    )


def _v2_splice_table(price: str = "0.30") -> pa.Table:
    def row(offset_ms: int, event_type: str, token: str, market: str,
            **values: object) -> dict[str, object]:
        return _v2_row(V2_FIRST_MS + offset_ms, event_type, token, base=SPLICE_HOUR,
                       market=market, **values)

    levels = '[["' + price + '","4"]]'
    return _v2_table(
        [
            # Shared condition: V1 is preferred for both of its tokens.
            row(0, "book", YES, CID, bids='[["0.40","9"]]', asks='[["0.60","9"]]'),
            row(0, "book", NO, CID, bids='[["0.40","9"]]', asks='[["0.60","9"]]'),
            row(1_000, "price_change", YES, CID, side="SELL", price="0.58", size="1",
                best_bid="0.40", best_ask="0.58"),
            # Evidence types V1 cannot carry: kept for a shared token too.
            row(2_000, "last_trade_price", YES, CID, price="0.58", size="1", side="BUY",
                fee_rate_bps=0, transaction_hash="0xshared"),
            # V2-only condition: subscription snapshots, a change, a print, a tick change.
            row(0, "book", V2_ONLY_YES, V2_ONLY_CID, bids=levels, asks='[["0.70","4"]]'),
            row(0, "book", V2_ONLY_NO, V2_ONLY_CID, bids='[["0.30","4"]]',
                asks='[["0.70","4"]]'),
            row(3_000, "price_change", V2_ONLY_YES, V2_ONLY_CID, side="BUY", price="0.31",
                size="2", best_bid="0.31", best_ask="0.70"),
            row(4_000, "last_trade_price", V2_ONLY_YES, V2_ONLY_CID, price="0.31", size="2",
                side="SELL", fee_rate_bps=0, transaction_hash="0xonly"),
            row(5_000, "tick_size_change", V2_ONLY_YES, V2_ONLY_CID, old_tick_size="0.01",
                new_tick_size="0.001"),
        ]
    )


def _splice(v1: pa.Table, v2: pa.Table) -> tuple[dict[str, pa.Table], dict[str, Any]]:
    end = SPLICE_HOUR + timedelta(hours=1)
    primary = pmxt.normalize_extract(v1, source_version="PMXT_V1", token_ids=SPLICE_TOKENS,
                                     window_start=SPLICE_HOUR, window_end=end)
    extra = pmxt.normalize_extract(v2, source_version="PMXT_V2", token_ids=SPLICE_TOKENS,
                                   window_start=SPLICE_HOUR, window_end=end)
    tables, report = splice_primary_with_supplement(primary.tables, extra.tables)
    return {s: pmxt.sort_and_dedupe(s, t)[0] for s, t in tables.items()}, report


def _keys(tables: dict[str, pa.Table]) -> dict[str, list[tuple[object, ...]]]:
    return {
        s: [(r["token_id"], r[pmxt.STREAM_TIME_FIELD[s]], r["source_version"])
            for r in t.to_pylist()]
        for s, t in tables.items() if s != "rejects"
    }


def test_splice_prefers_v1_book_evidence_for_shared_tokens_and_conditions() -> None:
    tables, report = _splice(_v1_splice_table(), _v2_splice_table())
    for stream in BOOK:
        rows = tables[stream].to_pylist()
        shared = [r for r in rows if r["market_id"] == CID]
        assert shared and all(r["source_version"] == "PMXT_V1" for r in shared)
        # NO never appears in V1, but its condition does: its V2 book rows are superseded.
        assert all(r["token_id"] != NO for r in rows)
    # No double counting: every token's book evidence comes from exactly one version.
    for token in SPLICE_TOKENS:
        versions = {r["source_version"] for s in BOOK for r in tables[s].to_pylist()
                    if r["token_id"] == token}
        assert len(versions) <= 1
    assert tables["book_changes"].num_rows == 3  # 2 V1 changes + 1 V2-only change
    assert report["supplement_rows_superseded"] == {
        "depth_snapshots": 2, "book_changes": 1, "rejects": 0
    }
    assert report["primary_book_tokens"] == 1
    assert report["primary_book_conditions"] == 1
    assert report["covered_tokens"] == 2


def test_splice_keeps_v2_only_state_from_its_real_first_observation() -> None:
    tables, report = _splice(_v1_splice_table(), _v2_splice_table())
    first_v2 = SPLICE_HOUR + timedelta(milliseconds=V2_FIRST_MS)
    snaps = [r for r in tables["depth_snapshots"].to_pylist() if r["market_id"] == V2_ONLY_CID]
    # Both subscription snapshots survive, at their own receive time, with V2 provenance.
    assert sorted(r["token_id"] for r in snaps) == [V2_ONLY_YES, V2_ONLY_NO]
    assert all(r["recorded_at"] == first_v2 for r in snaps)
    assert all(r["source_version"] == "PMXT_V2" for r in snaps)
    only = [
        r[pmxt.STREAM_TIME_FIELD[s]] for s in pmxt.EVIDENCE_STREAMS
        for r in tables[s].to_pylist() if r["market_id"] == V2_ONLY_CID
    ]
    # Nothing is backfilled before the first V2 observation.
    assert min(only) == first_v2
    assert report["supplement_only_tokens"] == 2
    assert report["supplement_only_conditions"] == 1
    assert report["supplement_only_first_observed_at"] == first_v2.isoformat()


def test_splice_keeps_v2_trades_and_tick_sizes_with_v2_provenance() -> None:
    tables, report = _splice(_v1_splice_table(), _v2_splice_table())
    trades = tables["trades"].to_pylist()
    assert sorted(r["token_id"] for r in trades) == [YES, V2_ONLY_YES]
    assert all(r["source_version"] == "PMXT_V2" for r in trades)
    ticks = tables["tick_size_changes"].to_pylist()
    assert [(r["token_id"], r["source_version"]) for r in ticks] == [(V2_ONLY_YES, "PMXT_V2")]
    assert report["supplement_rows_kept_for_covered_tokens"] == {
        "trades": 1, "tick_size_changes": 0
    }
    assert report["primary_version"] == "PMXT_V1"
    assert report["supplement_version"] == "PMXT_V2"


def test_splice_selection_is_independent_of_values() -> None:
    base, _ = _splice(_v1_splice_table(), _v2_splice_table())
    other, _ = _splice(_v1_splice_table(bid="0.10"), _v2_splice_table(price="0.05"))
    assert _keys(base) == _keys(other)


def test_splice_is_deterministic_under_input_reordering() -> None:
    v1, v2 = _v1_splice_table(), _v2_splice_table()
    forward, report = _splice(v1, v2)
    backward, report_back = _splice(
        v1.take(list(range(v1.num_rows - 1, -1, -1))),
        v2.take(list(range(v2.num_rows - 1, -1, -1))),
    )
    assert report == report_back
    for stream, table in forward.items():
        assert table.equals(backward[stream])


def test_splice_rejects_mixed_source_versions_in_one_input() -> None:
    end = SPLICE_HOUR + timedelta(hours=1)
    primary = pmxt.normalize_extract(_v1_splice_table(), source_version="PMXT_V1",
                                     token_ids=SPLICE_TOKENS, window_start=SPLICE_HOUR,
                                     window_end=end).tables
    extra = pmxt.normalize_extract(_v2_splice_table(), source_version="PMXT_V2",
                                   token_ids=SPLICE_TOKENS, window_start=SPLICE_HOUR,
                                   window_end=end).tables
    mixed = {**primary, "trades": extra["trades"]}
    with pytest.raises(ValueError, match="mixes source versions"):
        splice_primary_with_supplement(mixed, extra)


def _one_fill(path: Path) -> None:
    at = int((SPLICE_HOUR + timedelta(minutes=50)).timestamp())
    pq.write_table(
        pa.table(
            {
                "timestamp": pa.array([at], pa.int64()), "side": ["buy"], "price": [0.31],
                "size_shares": [2.0], "value_usd": [0.62], "token_id": [V2_ONLY_YES],
                "condition_id": [V2_ONLY_CID], "maker_address": ["0xm"],
                "taker_address": ["0xt"], "tx_hash": ["0xonly"],
                "log_index": pa.array([1], pa.int64()), "is_exchange_taker": [False],
                "event_id": ["e1"], "market_id": ["m2"], "outcome_side": ["YES"],
            }
        ),
        path,
    )


def test_build_splices_the_hour_with_provenance_and_build005_loads_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from predictions_cup.historical import regimes

    monkeypatch.setitem(regimes.FAMILY_MARKET_FAMILIES, "TST_2026", frozenset({"TST"}))
    hour_dir = tmp_path / "orderbooks" / "TST_2026" / "date=2026-04-13" / "hour=19"
    hour_dir.mkdir(parents=True)
    pq.write_table(_v1_splice_table(), hour_dir / "events.parquet")
    pq.write_table(_v2_splice_table(), hour_dir / "events_v2.parquet")
    fills = tmp_path / "fills"
    fills.mkdir()
    pq.write_table(
        pa.table(
            {
                "market_family": ["TST", "TST"], "condition_id": [CID, V2_ONLY_CID],
                "market_id": ["m1", "m2"], "yes_token_id": [YES, V2_ONLY_YES],
                "no_token_id": [NO, V2_ONLY_NO], "event_id": ["e1", "e1"],
                "question": ["Will X?", "Will Y?"], "outcome_labels": ['["Yes", "No"]'] * 2,
            }
        ),
        fills / "markets_TST_2026.parquet",
    )
    _one_fill(fills / "fills_TST_2026.parquet")
    regime = Regime(
        regime_id="splice_regime", country="Testland", round="first_round", family="TST_2026",
        election_date=date(2026, 4, 12), window_start=SPLICE_HOUR,
        window_end=SPLICE_HOUR + timedelta(hours=1),
    )
    manifest = build_corpus(orderbooks_root=tmp_path / "orderbooks", fills_root=fills,
                            output_root=tmp_path / "out", regimes=(regime,))
    assert validate_corpus(tmp_path / "out")["ok"]
    entry = manifest["regimes"][0]
    assert entry["source_versions"] == ["PMXT_V1", "PMXT_V2"]
    assert entry["missing_source_hours"] == []
    (splice,) = entry["source_splices"]
    assert splice["hour"] == "2026-04-13T19"
    assert splice["supplement_only_tokens"] == 2
    roles = {f["path"].rsplit("/", 1)[-1]: (f["splice_role"], f["source_version"])
             for f in manifest["source_files"] if f["kind"] == "pmxt_extract"}
    assert roles == {"events.parquet": ("primary", "PMXT_V1"),
                     "events_v2.parquet": ("supplement", "PMXT_V2")}
    counts = entry["pipeline_counts"]["books"]
    assert counts["splice_superseded_by_primary:depth_snapshots"] == 2
    assert counts["depth_snapshots_rows"] == 3  # 1 V1 + 2 V2-only subscription snapshots

    books = tmp_path / "out" / "schema_version=1" / "splice_regime" / "books"
    events = load_polymarket_capture(books)
    only = [e for e in events if e.instrument_id == V2_ONLY_YES]
    assert min(e.observed_at for e in only) == SPLICE_HOUR + timedelta(
        milliseconds=V2_FIRST_MS
    )
    assert only[0].event_type == ReplayEventType.DEPTH_SNAPSHOT

    # Without the supplement the hour is V1 only and the missing supplement is reported.
    (hour_dir / "events_v2.parquet").unlink()
    degraded = build_corpus(orderbooks_root=tmp_path / "orderbooks", fills_root=fills,
                            output_root=tmp_path / "degraded", regimes=(regime,))
    assert degraded["regimes"][0]["missing_source_hours"] == ["2026-04-13T19:PMXT_V2"]
    assert degraded["regimes"][0]["source_splices"] == []
