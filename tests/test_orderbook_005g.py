"""EXPERIMENT-005G source normalization and continuity invariants."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pyarrow as pa

from predictions_cup.historical.orderbook_005g import (
    SourceHour,
    assign_continuity_segments,
    normalize_005g_extract,
)

T0 = datetime(2026, 8, 24, 0, tzinfo=UTC)
TOKEN = "111"
FOREIGN = "999"
MARKET = "0x" + "a" * 64
TOKENS = frozenset({TOKEN})


V3_LEVELS = pa.list_(
    pa.struct(
        [
            pa.field("price", pa.string()),
            pa.field("size", pa.string()),
        ]
    )
)
V3_SCHEMA = pa.schema(
    [
        ("event_type", pa.string()),
        ("timestamp_received", pa.timestamp("us", tz="UTC")),
        ("timestamp", pa.timestamp("us", tz="UTC")),
        ("market", pa.string()),
        ("asset_id", pa.string()),
        ("bids", V3_LEVELS),
        ("asks", V3_LEVELS),
        ("price", pa.string()),
        ("size", pa.string()),
        ("side", pa.string()),
        ("best_bid", pa.string()),
        ("best_ask", pa.string()),
        ("spread", pa.string()),
        ("fee_rate_bps", pa.int64()),
        ("transaction_hash", pa.string()),
        ("old_tick_size", pa.string()),
        ("new_tick_size", pa.string()),
        ("sequence", pa.uint64()),
        ("source_witness", pa.string()),
        ("witness_set", pa.string()),
        ("arrival_skew", pa.int64()),
    ]
)

def _v3_table() -> pa.Table:
    return pa.Table.from_pylist(
        [
            {
                "event_type": "book",
                "timestamp_received": T0 + timedelta(microseconds=100),
                "timestamp": T0,
                "market": MARKET,
                "asset_id": TOKEN,
                "bids": [
                    {"price": "0.40", "size": "5"},
                    {"price": "0.41", "size": "3"},
                ],
                "asks": [
                    {"price": "0.60", "size": "7"},
                    {"price": "0.55", "size": "2"},
                ],
                "sequence": 1,
                "source_witness": "w1",
                "witness_set": "w1,w2",
                "arrival_skew": 17,
            },
            {
                "event_type": "price_change",
                "timestamp_received": T0 + timedelta(microseconds=200),
                "timestamp": T0 + timedelta(microseconds=50),
                "market": MARKET,
                "asset_id": TOKEN,
                "price": "0.42",
                "size": "4",
                "side": "BUY",
                "best_bid": "0.42",
                "best_ask": "0.55",
                "sequence": 2,
                "source_witness": "w1",
                "witness_set": "w1",
                "arrival_skew": 0,
            },
            {
                "event_type": "best_bid_ask",
                "timestamp_received": T0 + timedelta(microseconds=250),
                "timestamp": T0 + timedelta(microseconds=60),
                "market": MARKET,
                "asset_id": TOKEN,
                "best_bid": "0.42",
                "best_ask": "0.55",
                "spread": "0.13",
                "sequence": 3,
                "source_witness": "w1",
                "witness_set": "w1",
                "arrival_skew": 0,
            },
            {
                "event_type": "last_trade_price",
                "timestamp_received": T0 + timedelta(microseconds=300),
                "timestamp": T0 + timedelta(microseconds=70),
                "market": MARKET,
                "asset_id": TOKEN,
                "price": "0.54",
                "size": "2",
                "side": "BUY",
                "fee_rate_bps": 0,
                "transaction_hash": "0xabc",
                "sequence": 4,
                "source_witness": "w2",
                "witness_set": "w1,w2",
                "arrival_skew": 9,
            },
            {
                "event_type": "tick_size_change",
                "timestamp_received": T0 + timedelta(microseconds=350),
                "timestamp": T0 + timedelta(microseconds=80),
                "market": MARKET,
                "asset_id": TOKEN,
                "old_tick_size": "0.01",
                "new_tick_size": "0.001",
                "sequence": 5,
                "source_witness": "w1",
                "witness_set": "w1",
                "arrival_skew": 0,
            },
            {
                "event_type": "best_bid_ask",
                "timestamp_received": T0 + timedelta(microseconds=400),
                "timestamp": T0 + timedelta(microseconds=90),
                "market": MARKET,
                "asset_id": FOREIGN,
                "best_bid": "0.10",
                "best_ask": "0.20",
                "spread": "0.10",
                "sequence": 6,
            },
        ]
    )


def test_v3_uses_receive_time_and_preserves_capture_provenance() -> None:
    out = normalize_005g_extract(
        _v3_table(),
        source_generation="v3",
        token_ids=TOKENS,
        window_start=T0,
        window_end=T0 + timedelta(hours=1),
    )

    assert out.counts["raw_rows"] == 6
    assert out.counts["rows_matching_tokens"] == 5

    snap = out.tables["depth_snapshots"].to_pylist()[0]
    assert snap["best_bid"] == "0.41"
    assert snap["best_ask"] == "0.55"
    assert snap["recorded_at"] == T0 + timedelta(microseconds=100)
    assert snap["source_timestamp"] == T0
    assert snap["source_generation"] == "v3"
    assert snap["source_version"] == "PENDULUM_V3"
    assert snap["sequence"] == 1
    assert snap["source_witness"] == "w1"
    assert snap["witness_set"] == "w1,w2"
    assert snap["arrival_skew_us"] == 17

    change = out.tables["book_changes"].to_pylist()[0]
    assert change["observed_at"] == T0 + timedelta(microseconds=200)
    assert change["source_timestamp"] == T0 + timedelta(microseconds=50)

    bbo = out.tables["bbo_updates"].to_pylist()[0]
    assert (bbo["best_bid"], bbo["best_ask"], bbo["spread"]) == ("0.42", "0.55", "0.13")
    assert bbo["observed_at"] == T0 + timedelta(microseconds=250)

    trade = out.tables["trades"].to_pylist()[0]
    assert trade["event_id"].startswith("polymarket-trade:")
    assert trade["observed_at"] == T0 + timedelta(microseconds=300)
    assert trade["arrival_skew_us"] == 9

    tick = out.tables["tick_size_changes"].to_pylist()[0]
    assert tick["observed_at"] == T0 + timedelta(microseconds=350)
    assert tick["source_generation"] == "v3"


def test_v3_early_schema_can_omit_witness_columns() -> None:
    early_schema = pa.schema(
        [
            field
            for field in V3_SCHEMA
            if field.name not in {"source_witness", "witness_set", "arrival_skew"}
        ]
    )
    table = pa.Table.from_pylist(
        [
            {
                "event_type": "best_bid_ask",
                "timestamp_received": T0,
                "timestamp": T0 - timedelta(microseconds=5),
                "market": MARKET,
                "asset_id": TOKEN,
                "best_bid": "0.45",
                "best_ask": "0.46",
                "spread": "0.01",
                "sequence": 7,
            }
        ],
        schema=early_schema,
    )
    out = normalize_005g_extract(
        table,
        source_generation="v3",
        token_ids=TOKENS,
        window_start=T0,
        window_end=T0 + timedelta(hours=1),
    )
    row = out.tables["bbo_updates"].to_pylist()[0]
    assert row["sequence"] == 7
    assert row["source_witness"] is None
    assert row["witness_set"] is None
    assert row["arrival_skew_us"] is None


def test_continuity_breaks_on_source_change_missing_and_zero_row_hours() -> None:
    hours = [
        SourceHour("ev16", T0, "v2", rows=10),
        SourceHour("ev16", T0 + timedelta(hours=1), "v2", rows=10),
        SourceHour("ev16", T0 + timedelta(hours=2), "ag6", rows=10),
        SourceHour("ev16", T0 + timedelta(hours=3), "ag6", status="SOURCE_MISSING", rows=0),
        SourceHour("ev16", T0 + timedelta(hours=4), "ag6", rows=10),
        SourceHour("ev16", T0 + timedelta(hours=5), "ag6", rows=0),
        SourceHour("ev16", T0 + timedelta(hours=6), "ag6", rows=10),
    ]

    result = assign_continuity_segments(hours)

    assert [item.segment_id for item in result] == [1, 1, 2, None, 3, None, 4]
    assert [item.reason for item in result] == [
        "INITIAL",
        "CONTIGUOUS",
        "SOURCE_GENERATION_BOUNDARY",
        "INVALID_STATUS:SOURCE_MISSING",
        "AFTER_INVALID_BARRIER",
        "ZERO_ROW_HOUR",
        "AFTER_INVALID_BARRIER",
    ]


def test_continuity_breaks_on_unlisted_hour_gap_and_resets_per_window() -> None:
    hours = [
        SourceHour("a", T0, "v3"),
        SourceHour("a", T0 + timedelta(hours=2), "v3"),
        SourceHour("b", T0, "v3"),
        SourceHour("b", T0 + timedelta(hours=1), "v3"),
    ]

    result = assign_continuity_segments(hours)

    assert [(item.window_id, item.segment_id, item.reason) for item in result] == [
        ("a", 1, "INITIAL"),
        ("a", 2, "TIME_GAP"),
        ("b", 1, "INITIAL"),
        ("b", 1, "CONTIGUOUS"),
    ]
