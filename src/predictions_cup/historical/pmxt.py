"""Normalize PendulumFlow PMXT V1/V2 order-book extracts into BUILD-007 replay streams.

Output streams reuse the accepted BUILD-007 Polymarket research Parquet schemas that BUILD-005
already loads (``depth_snapshots``, ``book_changes``, ``trades``) plus DATA-001 provenance
columns. Everything is vectorized with PyArrow compute so one archive hour never needs Python
row loops over its levels.

Timestamp semantics (verified against the bytes, see docs/data):

* PMXT V2 ``timestamp_received`` -- archive recorder receive time (ms). Used as the
  historical observable-time proxy ``observed_at``.
* PMXT V2 ``timestamp`` -- venue event time (ms) from the Polymarket market channel. Kept as
  ``source_timestamp``; it never controls replay order.
* PMXT V1 ``timestamp_received`` equals the payload ``timestamp`` truncated to ms: both are the
  third-party recorder's receive time. V1 therefore has **no venue event time** and
  ``source_timestamp`` is null. ``timestamp_created_at`` is a later archive write time and is
  not used.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.json as pajson

from predictions_cup.external.polymarket.models import hashed_trade_event_id

UTC_US = pa.timestamp("us", tz="UTC")
LEVELS = pa.list_(
    pa.struct(
        [
            pa.field("price", pa.string(), nullable=False),
            pa.field("size", pa.string(), nullable=False),
        ]
    )
)
_PRICE_MATH = pa.decimal128(24, 10)
_RAW_LEVELS = pa.list_(pa.list_(pa.string()))
_UNSIGNED_DECIMAL = r"^[0-9]+(\.[0-9]+)?$"

GRADE_BOOK_SNAPSHOT = "BOOK_SNAPSHOT"
GRADE_PRICE_ONLY = "PRICE_ONLY"
GRADE_TRADE_FILL = "TRADE_FILL"

# BUILD-007 accepted schemas + DATA-001 provenance columns (appended, never reordered).
_PROVENANCE = [("source_version", pa.string()), ("evidence_grade", pa.string())]
SCHEMAS: dict[str, pa.Schema] = {
    "depth_snapshots": pa.schema(
        [
            ("token_id", pa.string()),
            ("market_id", pa.string()),
            ("source_timestamp", UTC_US),
            ("state_observed_at", UTC_US),
            ("recorded_at", UTC_US),
            ("best_bid", pa.string()),
            ("best_ask", pa.string()),
            ("midpoint", pa.string()),
            ("spread", pa.string()),
            ("bids", LEVELS),
            ("asks", LEVELS),
            ("book_hash", pa.string()),
            ("min_order_size", pa.string()),
            ("tick_size", pa.string()),
            ("neg_risk", pa.bool_()),
            ("last_trade_price", pa.string()),
            *_PROVENANCE,
        ]
    ),
    "book_changes": pa.schema(
        [
            ("token_id", pa.string()),
            ("market_id", pa.string()),
            ("side", pa.string()),
            ("price", pa.string()),
            ("size", pa.string()),
            ("source_timestamp", UTC_US),
            ("observed_at", UTC_US),
            ("best_bid", pa.string()),
            ("best_ask", pa.string()),
            ("book_hash", pa.string()),
            *_PROVENANCE,
        ]
    ),
    "trades": pa.schema(
        [
            ("event_id", pa.string()),
            ("token_id", pa.string()),
            ("market_id", pa.string()),
            ("price", pa.string()),
            ("size", pa.string()),
            ("side", pa.string()),
            ("source_timestamp", UTC_US),
            ("observed_at", UTC_US),
            ("transaction_hash", pa.string()),
            ("fee_rate_bps", pa.string()),
            *_PROVENANCE,
        ]
    ),
    "tick_size_changes": pa.schema(
        [
            ("token_id", pa.string()),
            ("market_id", pa.string()),
            ("source_timestamp", UTC_US),
            ("observed_at", UTC_US),
            ("old_tick_size", pa.string()),
            ("new_tick_size", pa.string()),
            ("source_version", pa.string()),
        ]
    ),
    "rejects": pa.schema(
        [
            ("stream", pa.string()),
            ("reason", pa.string()),
            ("token_id", pa.string()),
            ("observed_at", UTC_US),
            ("source_version", pa.string()),
        ]
    ),
}
STREAM_TIME_FIELD = {
    "depth_snapshots": "recorded_at",
    "book_changes": "observed_at",
    "trades": "observed_at",
    "tick_size_changes": "observed_at",
    "rejects": "observed_at",
}
# Deterministic tie-break after (time, token): full row content, never archive export order.
_SORT_KEYS = {
    "depth_snapshots": ["recorded_at", "token_id", "source_timestamp", "best_bid", "best_ask"],
    "book_changes": [
        "observed_at",
        "token_id",
        "source_timestamp",
        "side",
        "price",
        "size",
        "best_bid",
        "best_ask",
    ],
    "trades": ["observed_at", "token_id", "source_timestamp", "transaction_hash", "price", "size"],
    "tick_size_changes": ["observed_at", "token_id", "old_tick_size", "new_tick_size"],
    "rejects": ["observed_at", "stream", "reason", "token_id"],
}
EVIDENCE_STREAMS = ("depth_snapshots", "book_changes", "trades", "tick_size_changes")


@dataclass(slots=True)
class NormalizedExtract:
    """Normalized rows for one source extract plus audit counters."""

    tables: dict[str, pa.Table]
    counts: dict[str, int] = field(default_factory=dict)

    def add(self, key: str, value: int) -> None:
        self.counts[key] = self.counts.get(key, 0) + value


def normalize_extract(
    table: pa.Table,
    *,
    source_version: str,
    token_ids: frozenset[str],
    window_start: datetime,
    window_end: datetime,
) -> NormalizedExtract:
    """Filter to exact tokens and window, then normalize one PMXT extract table."""
    if source_version == "PMXT_V2":
        return _normalize_v2(table, token_ids, window_start, window_end)
    if source_version == "PMXT_V1":
        return _normalize_v1(table, token_ids, window_start, window_end)
    raise ValueError(f"unsupported PMXT source version {source_version!r}")


def empty_table(stream: str) -> pa.Table:
    return SCHEMAS[stream].empty_table()


def sort_and_dedupe(stream: str, table: pa.Table) -> tuple[pa.Table, int]:
    """Deterministic content ordering; drop only rows identical in every column.

    Exact duplicates arise when redundant recorders deliver the same message. They are
    idempotent for book state (level sizes are absolute), so dropping them loses nothing.
    """
    if table.num_rows == 0:
        return table, 0
    keys = [(name, "ascending") for name in _SORT_KEYS[stream]]
    table = table.take(pc.sort_indices(table, sort_keys=keys))
    comparable = [
        name for name in table.schema.names
        if not pa.types.is_list(table.schema.field(name).type)
    ]
    if table.num_rows < 2:
        return table, 0
    head = table.slice(0, table.num_rows - 1)
    tail = table.slice(1)
    same = pa.array([True] * (table.num_rows - 1))
    for name in comparable:
        a, b = head[name], tail[name]
        eq = pc.fill_null(pc.equal(a, b), False)
        both_null = pc.and_(pc.is_null(a), pc.is_null(b))
        same = pc.and_(same, pc.or_(eq, both_null))
    for name in table.schema.names:
        if pa.types.is_list(table.schema.field(name).type):
            # Levels compared through their canonical JSON-free text rendering.
            same = pc.and_(same, pc.equal(_levels_text(head[name]), _levels_text(tail[name])))
    keep = pc.invert(pa.concat_arrays([pa.array([False]), same.combine_chunks()]))
    dropped = table.num_rows - pc.sum(keep).as_py()
    return table.filter(keep), dropped


# ---------------------------------------------------------------------------------------------
# PMXT V2


def _normalize_v2(
    table: pa.Table,
    token_ids: frozenset[str],
    window_start: datetime,
    window_end: datetime,
) -> NormalizedExtract:
    out = NormalizedExtract(tables={})
    out.add("raw_rows", table.num_rows)
    table = table.filter(pc.is_in(table["asset_id"], value_set=pa.array(sorted(token_ids))))
    out.add("rows_matching_tokens", table.num_rows)
    observed = pc.cast(table["timestamp_received"], UTC_US)
    in_window = pc.and_(
        pc.greater_equal(observed, pa.scalar(window_start, UTC_US)),
        pc.less(observed, pa.scalar(window_end, UTC_US)),
    )
    out.add("rows_outside_window", table.num_rows - pc.sum(in_window).as_py())
    table = table.filter(in_window)
    out.add("rows_inside_window", table.num_rows)

    event_type = table["event_type"]
    known = {"book", "price_change", "last_trade_price", "tick_size_change"}
    unknown = table.filter(pc.invert(pc.is_in(event_type, value_set=pa.array(sorted(known)))))
    rejects = [_rejects("unknown", "unknown_event_type", unknown["asset_id"],
                        unknown["timestamp_received"], "PMXT_V2")] if unknown.num_rows else []

    books = table.filter(pc.equal(event_type, "book"))
    snaps, bad = _v2_books(books)
    rejects.extend(bad)
    out.tables["depth_snapshots"] = snaps

    changes = table.filter(pc.equal(event_type, "price_change"))
    out.tables["book_changes"], bad_changes = _v2_changes(changes)
    rejects.extend(bad_changes)

    trades = table.filter(pc.equal(event_type, "last_trade_price"))
    out.tables["trades"], bad_trades = _v2_trades(trades)
    rejects.extend(bad_trades)

    ticks = table.filter(pc.equal(event_type, "tick_size_change"))
    out.tables["tick_size_changes"] = pa.table(
        {
            "token_id": ticks["asset_id"],
            "market_id": _v2_market(ticks),
            "source_timestamp": pc.cast(ticks["timestamp"], UTC_US),
            "observed_at": pc.cast(ticks["timestamp_received"], UTC_US),
            "old_tick_size": _decimal_text(ticks["old_tick_size"]),
            "new_tick_size": _decimal_text(ticks["new_tick_size"]),
            "source_version": _const("PMXT_V2", ticks.num_rows),
        },
        schema=SCHEMAS["tick_size_changes"],
    )
    out.tables["rejects"] = _concat("rejects", rejects)
    for stream, tbl in out.tables.items():
        out.add(f"{stream}_rows", tbl.num_rows)
    return out


def _v2_market(table: pa.Table) -> pa.ChunkedArray:
    return pc.cast(table["market"], pa.string())


def _v2_books(books: pa.Table) -> tuple[pa.Table, list[pa.Table]]:
    rejects: list[pa.Table] = []
    missing = pc.or_(pc.is_null(books["bids"]), pc.is_null(books["asks"]))
    if pc.any(missing).as_py():
        bad = books.filter(missing)
        rejects.append(_rejects("depth_snapshots", "missing_levels", bad["asset_id"],
                                bad["timestamp_received"], "PMXT_V2"))
        books = books.filter(pc.invert(missing))
    bids_raw, asks_raw = _parse_level_json(books["bids"], books["asks"])
    return _snapshots(
        token=books["asset_id"],
        market=_v2_market(books),
        source_ts=pc.cast(books["timestamp"], UTC_US),
        observed=pc.cast(books["timestamp_received"], UTC_US),
        bids_raw=bids_raw,
        asks_raw=asks_raw,
        source_version="PMXT_V2",
        rejects=rejects,
    )


def _v2_changes(changes: pa.Table) -> tuple[pa.Table, list[pa.Table]]:
    tbl = pa.table(
        {
            "token_id": changes["asset_id"],
            "market_id": _v2_market(changes),
            "side": changes["side"],
            "price": _decimal_text(changes["price"]),
            "size": _decimal_text(changes["size"]),
            "source_timestamp": pc.cast(changes["timestamp"], UTC_US),
            "observed_at": pc.cast(changes["timestamp_received"], UTC_US),
            "best_bid": _decimal_text(changes["best_bid"]),
            "best_ask": _decimal_text(changes["best_ask"]),
            "book_hash": pa.nulls(changes.num_rows, pa.string()),
            "source_version": _const("PMXT_V2", changes.num_rows),
            "evidence_grade": _const(GRADE_PRICE_ONLY, changes.num_rows),
        },
        schema=SCHEMAS["book_changes"],
    )
    return _validate_changes(tbl)


def _v2_trades(trades: pa.Table) -> tuple[pa.Table, list[pa.Table]]:
    tokens = trades["asset_id"].to_pylist()
    hashes = trades["transaction_hash"].to_pylist()
    event_ids = [hashed_trade_event_id(t, h) for t, h in zip(tokens, hashes, strict=True)]
    tbl = pa.table(
        {
            "event_id": pa.array(event_ids, pa.string()),
            "token_id": trades["asset_id"],
            "market_id": _v2_market(trades),
            "price": _decimal_text(trades["price"]),
            "size": _decimal_text(trades["size"]),
            "side": trades["side"],
            "source_timestamp": pc.cast(trades["timestamp"], UTC_US),
            "observed_at": pc.cast(trades["timestamp_received"], UTC_US),
            "transaction_hash": trades["transaction_hash"],
            "fee_rate_bps": pc.cast(trades["fee_rate_bps"], pa.string()),
            "source_version": _const("PMXT_V2", trades.num_rows),
            "evidence_grade": _const(GRADE_TRADE_FILL, trades.num_rows),
        },
        schema=SCHEMAS["trades"],
    )
    bad = pc.or_(
        pc.fill_null(_out_of_unit_range(tbl["price"]), True),
        pc.fill_null(pc.invert(pc.match_substring_regex(tbl["size"], _UNSIGNED_DECIMAL)), False),
    )
    rejects = []
    if pc.any(bad).as_py():
        b = tbl.filter(bad)
        rejects.append(_rejects("trades", "invalid_price_or_size", b["token_id"],
                                b["observed_at"], "PMXT_V2"))
        tbl = tbl.filter(pc.invert(bad))
    return tbl, rejects


# ---------------------------------------------------------------------------------------------
# PMXT V1

_V1_PAYLOAD = pa.schema(
    [
        ("token_id", pa.string()),
        ("update_type", pa.string()),
        ("best_bid", pa.string()),
        ("best_ask", pa.string()),
        ("bids", _RAW_LEVELS),
        ("asks", _RAW_LEVELS),
        ("change_price", pa.string()),
        ("change_size", pa.string()),
        ("change_side", pa.string()),
    ]
)


def _normalize_v1(
    table: pa.Table,
    token_ids: frozenset[str],
    window_start: datetime,
    window_end: datetime,
) -> NormalizedExtract:
    out = NormalizedExtract(tables={})
    out.add("raw_rows", table.num_rows)
    payload = _read_ndjson(table["data"], _V1_PAYLOAD)
    matching = pc.is_in(payload["token_id"], value_set=pa.array(sorted(token_ids)))
    table, payload = table.filter(matching), payload.filter(matching)
    out.add("rows_matching_tokens", table.num_rows)
    observed = pc.cast(table["timestamp_received"], UTC_US)
    in_window = pc.and_(
        pc.greater_equal(observed, pa.scalar(window_start, UTC_US)),
        pc.less(observed, pa.scalar(window_end, UTC_US)),
    )
    out.add("rows_outside_window", table.num_rows - pc.sum(in_window).as_py())
    table, payload, observed = (
        table.filter(in_window),
        payload.filter(in_window),
        observed.filter(in_window),
    )
    out.add("rows_inside_window", table.num_rows)

    update = table["update_type"]
    rejects: list[pa.Table] = []
    known = pc.is_in(update, value_set=pa.array(["book_snapshot", "price_change"]))
    if not pc.all(known).as_py():
        unknown = pc.invert(known)
        rejects.append(_rejects("unknown", "unknown_update_type",
                                payload["token_id"].filter(unknown),
                                observed.filter(unknown), "PMXT_V1"))

    is_book = pc.equal(update, "book_snapshot")
    books, book_payload = table.filter(is_book), payload.filter(is_book)
    snaps, bad = _snapshots(
        token=book_payload["token_id"],
        market=books["market_id"],
        source_ts=pa.nulls(books.num_rows, UTC_US),
        observed=pc.cast(books["timestamp_received"], UTC_US),
        bids_raw=book_payload["bids"],
        asks_raw=book_payload["asks"],
        source_version="PMXT_V1",
        rejects=[],
    )
    rejects.extend(bad)
    out.tables["depth_snapshots"] = snaps

    is_change = pc.equal(update, "price_change")
    changes, change_payload = table.filter(is_change), payload.filter(is_change)
    tbl = pa.table(
        {
            "token_id": change_payload["token_id"],
            "market_id": changes["market_id"],
            "side": change_payload["change_side"],
            "price": change_payload["change_price"],
            "size": change_payload["change_size"],
            "source_timestamp": pa.nulls(changes.num_rows, UTC_US),
            "observed_at": pc.cast(changes["timestamp_received"], UTC_US),
            "best_bid": change_payload["best_bid"],
            "best_ask": change_payload["best_ask"],
            "book_hash": pa.nulls(changes.num_rows, pa.string()),
            "source_version": _const("PMXT_V1", changes.num_rows),
            "evidence_grade": _const(GRADE_PRICE_ONLY, changes.num_rows),
        },
        schema=SCHEMAS["book_changes"],
    )
    out.tables["book_changes"], bad = _validate_changes(tbl)
    rejects.extend(bad)
    out.tables["trades"] = empty_table("trades")
    out.tables["tick_size_changes"] = empty_table("tick_size_changes")
    out.tables["rejects"] = _concat("rejects", rejects)
    for stream, tbl in out.tables.items():
        out.add(f"{stream}_rows", tbl.num_rows)
    return out


# ---------------------------------------------------------------------------------------------
# Shared helpers


def _snapshots(
    *,
    token: Any,
    market: Any,
    source_ts: Any,
    observed: Any,
    bids_raw: Any,
    asks_raw: Any,
    source_version: str,
    rejects: list[pa.Table],
) -> tuple[pa.Table, list[pa.Table]]:
    n = len(token)
    bids, best_bid, bid_bad = normalize_levels(_combine(bids_raw), descending=True)
    asks, best_ask, ask_bad = normalize_levels(_combine(asks_raw), descending=False)
    bid_num = pc.cast(best_bid, _PRICE_MATH)
    ask_num = pc.cast(best_ask, _PRICE_MATH)
    spread = pc.subtract(ask_num, bid_num)
    midpoint = pc.divide(pc.add(ask_num, bid_num), pa.scalar(2, pa.decimal128(1, 0)))
    tbl = pa.table(
        {
            "token_id": _combine(token),
            "market_id": pc.cast(_combine(market), pa.string()),
            "source_timestamp": pc.cast(_combine(source_ts), UTC_US),
            "state_observed_at": _combine(observed),
            "recorded_at": _combine(observed),
            "best_bid": best_bid,
            "best_ask": best_ask,
            "midpoint": _decimal_text(midpoint),
            "spread": _decimal_text(spread),
            "bids": bids,
            "asks": asks,
            "book_hash": pa.nulls(n, pa.string()),
            "min_order_size": pa.nulls(n, pa.string()),
            "tick_size": pa.nulls(n, pa.string()),
            "neg_risk": pa.nulls(n, pa.bool_()),
            "last_trade_price": pa.nulls(n, pa.string()),
            "source_version": _const(source_version, n),
            "evidence_grade": _const(GRADE_BOOK_SNAPSHOT, n),
        },
        schema=SCHEMAS["depth_snapshots"],
    )
    bad = pc.fill_null(pc.or_(bid_bad, ask_bad), True)
    if pc.any(bad).as_py():
        reason = pc.if_else(pc.fill_null(bid_bad, True), pa.scalar("malformed_bid_levels"),
                            pa.scalar("malformed_ask_levels"))
        b = tbl.filter(bad)
        rejects.append(
            pa.table(
                {
                    "stream": _const("depth_snapshots", b.num_rows),
                    "reason": reason.filter(bad),
                    "token_id": b["token_id"],
                    "observed_at": b["recorded_at"],
                    "source_version": _const(source_version, b.num_rows),
                },
                schema=SCHEMAS["rejects"],
            )
        )
        tbl = tbl.filter(pc.invert(bad))
    return tbl, rejects


def normalize_levels(
    raw: pa.Array,
    *,
    descending: bool,
) -> tuple[pa.Array, pa.Array, pa.Array]:
    """Return (levels, best price text, malformed flag) for a list<list<string>> array.

    Levels are ordered bids-descending / asks-ascending by exact decimal price. A row is
    malformed if any level is not a ``[price, size]`` pair of unsigned decimal text, a price
    lies outside ``[0, 1]``, or a price level is duplicated. Malformed rows are flagged, not
    repaired.
    """
    raw = _combine(raw)
    if isinstance(raw, pa.ChunkedArray):
        raw = raw.combine_chunks()
    n = len(raw)
    if n == 0:
        return pa.array([], LEVELS), pa.array([], pa.string()), pa.array([], pa.bool_())
    raw = pc.if_else(pc.is_null(raw), pa.scalar([], _RAW_LEVELS), raw)
    raw = raw.combine_chunks() if isinstance(raw, pa.ChunkedArray) else raw
    pairs = raw.flatten()
    parents = pc.list_parent_indices(raw)
    pair_len = pc.fill_null(pc.list_value_length(pairs), 0)
    good_shape = pc.equal(pair_len, 2)
    safe_pairs = pc.if_else(good_shape, pairs, pa.scalar(["0", "0"], pa.list_(pa.string())))
    price_txt = pc.list_element(safe_pairs, 0)
    size_txt = pc.list_element(safe_pairs, 1)
    good_text = pc.and_(
        pc.fill_null(pc.match_substring_regex(price_txt, _UNSIGNED_DECIMAL), False),
        pc.fill_null(pc.match_substring_regex(size_txt, _UNSIGNED_DECIMAL), False),
    )
    price_num = pc.cast(pc.if_else(good_text, price_txt, pa.scalar("0")), _PRICE_MATH)
    in_range = pc.less_equal(price_num, pa.scalar(1, _PRICE_MATH))
    entry_ok = pc.and_(pc.and_(good_shape, good_text), in_range)

    order = pc.sort_indices(
        pa.table({"parent": parents, "price": price_num}),
        sort_keys=[("parent", "ascending"), ("price", "descending" if descending else "ascending")],
    )
    parents_sorted = parents.take(order)
    price_sorted = price_num.take(order)
    dup = pa.array([], pa.bool_())
    if len(parents_sorted) > 1:
        dup = pc.and_(
            pc.equal(parents_sorted.slice(1), parents_sorted.slice(0, len(parents_sorted) - 1)),
            pc.equal(price_sorted.slice(1), price_sorted.slice(0, len(price_sorted) - 1)),
        )
    bad_parent = _any_by_parent(pc.invert(entry_ok), parents, n)
    if len(dup):
        bad_parent = pc.or_(bad_parent, _any_by_parent(dup, parents_sorted.slice(1), n))

    values = pa.StructArray.from_arrays(
        [price_txt.take(order), size_txt.take(order)],
        fields=list(LEVELS.value_type),
    )
    levels = pa.ListArray.from_arrays(_offsets(raw), values, type=LEVELS)
    lengths = pc.list_value_length(levels)
    nonempty = pc.greater(lengths, 0)
    firsts = pc.list_element(levels.filter(nonempty), 0).field("price")
    best = pc.replace_with_mask(pa.nulls(n, pa.string()), nonempty, firsts)
    return levels, best, bad_parent


def _any_by_parent(flags: pa.Array, parents: pa.Array, n: int) -> pa.Array:
    hit = parents.filter(flags)
    return pc.is_in(pa.array(range(n), pa.int64()), value_set=pc.cast(hit, pa.int64()))


def _offsets(raw: pa.ListArray) -> pa.Array:
    lengths = pc.cast(pc.list_value_length(raw), pa.int32())
    acc = pc.cumulative_sum(lengths)
    return pa.concat_arrays([pa.array([0], pa.int32()), pc.cast(acc, pa.int32())])


def _validate_changes(tbl: pa.Table) -> tuple[pa.Table, list[pa.Table]]:
    reason = pa.nulls(tbl.num_rows, pa.string())
    checks = [
        ("invalid_side", pc.invert(pc.fill_null(pc.is_in(tbl["side"],
                                                        value_set=pa.array(["BUY", "SELL"])),
                                                False))),
        ("invalid_price", pc.fill_null(_out_of_unit_range(tbl["price"]), True)),
        ("invalid_size", pc.invert(pc.fill_null(
            pc.match_substring_regex(tbl["size"], _UNSIGNED_DECIMAL), False))),
        ("invalid_best_bid", pc.fill_null(_out_of_unit_range(tbl["best_bid"]), False)),
        ("invalid_best_ask", pc.fill_null(_out_of_unit_range(tbl["best_ask"]), False)),
    ]
    for label, mask in reversed(checks):
        reason = pc.if_else(mask, pa.scalar(label), reason)
    bad = pc.is_valid(reason)
    if not pc.any(bad).as_py():
        return tbl, []
    b = tbl.filter(bad)
    rejected = pa.table(
        {
            "stream": _const("book_changes", b.num_rows),
            "reason": reason.filter(bad),
            "token_id": b["token_id"],
            "observed_at": b["observed_at"],
            "source_version": b["source_version"],
        },
        schema=SCHEMAS["rejects"],
    )
    return tbl.filter(pc.invert(bad)), [rejected]


def _out_of_unit_range(text: Any) -> Any:
    """True when a decimal text is malformed or outside [0, 1]; null stays null."""
    text = _combine(text)
    well_formed = pc.match_substring_regex(text, _UNSIGNED_DECIMAL)
    safe = pc.if_else(pc.fill_null(well_formed, False), text, pa.scalar("0"))
    value = pc.cast(safe, _PRICE_MATH)
    too_big = pc.greater(value, pa.scalar(1, _PRICE_MATH))
    result = pc.or_(pc.invert(well_formed), too_big)
    return pc.if_else(pc.is_null(text), pa.scalar(None, pa.bool_()), result)


def _decimal_text(values: Any) -> Any:
    """Exact decimal -> canonical text without binary float (trailing zeros removed)."""
    text = pc.cast(_combine(values), pa.string())
    text = pc.replace_substring_regex(text, r"^(-?[0-9]+\.[0-9]*?)0+$", r"\1")
    return pc.replace_substring_regex(text, r"\.$", "")


def _parse_level_json(bids: Any, asks: Any) -> tuple[pa.Array, pa.Array]:
    lines = pc.binary_join_element_wise(
        pa.scalar('{"b":'), _combine(bids), pa.scalar(',"a":'), _combine(asks), pa.scalar("}"),
        "",
    )
    schema = pa.schema([("b", _RAW_LEVELS), ("a", _RAW_LEVELS)])
    parsed = _read_ndjson(lines, schema)
    return parsed["b"].combine_chunks(), parsed["a"].combine_chunks()


def _read_ndjson(lines: Any, schema: pa.Schema) -> pa.Table:
    lines = _combine(lines)
    if len(lines) == 0:
        return schema.empty_table()
    buffer = "\n".join(lines.to_pylist()).encode()
    parsed = pajson.read_json(
        io.BytesIO(buffer),
        parse_options=pajson.ParseOptions(
            explicit_schema=schema, unexpected_field_behavior="ignore"
        ),
        read_options=pajson.ReadOptions(block_size=1 << 26, use_threads=False),
    )
    if parsed.num_rows != len(lines):
        raise ValueError("NDJSON row count mismatch while parsing PMXT payloads")
    return parsed.select(schema.names)


def _rejects(stream: str, reason: str, token: Any, observed: Any, version: str) -> pa.Table:
    n = len(token)
    return pa.table(
        {
            "stream": _const(stream, n),
            "reason": _const(reason, n),
            "token_id": _combine(token),
            "observed_at": pc.cast(_combine(observed), UTC_US),
            "source_version": _const(version, n),
        },
        schema=SCHEMAS["rejects"],
    )


def _concat(stream: str, tables: list[pa.Table]) -> pa.Table:
    return pa.concat_tables(tables) if tables else empty_table(stream)


def _const(value: str, n: int) -> pa.Array:
    return pa.array([value] * n, pa.string())


def _combine(values: Any) -> Any:
    if isinstance(values, pa.ChunkedArray):
        return values.combine_chunks() if values.num_chunks != 1 else values.chunk(0)
    return values


def _levels_text(levels: Any) -> Any:
    levels = _combine(levels)
    flat = levels.flatten()
    joined = pc.binary_join_element_wise(flat.field("price"), flat.field("size"), ":")
    rebuilt = pa.ListArray.from_arrays(_offsets(levels), joined)
    return pc.binary_join(rebuilt, ",")
