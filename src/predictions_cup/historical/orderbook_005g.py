"""Source-aware normalization for EXPERIMENT-005G fresh order-book research.

V1/V2 reuse the accepted DATA-001 PMXT normalizer. AG6 is schema-compatible with V2 but
keeps distinct provenance. V3 is adapted separately because its depth encoding, receive-time
precision and multi-witness provenance differ materially.

Observable-time rules are frozen in data/experiments/experiment_005g/OBSERVABILITY_MODEL.json.
Exchange time and physical Parquet row order never control replay ordering.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

import pyarrow as pa
import pyarrow.compute as pc

from predictions_cup.external.polymarket.models import hashed_trade_event_id
from predictions_cup.historical import pmxt

SOURCE_LABEL = {
    "v1": "PMXT_V1",
    "v2": "PMXT_V2",
    "ag6": "PENDULUM_AG6",
    "v3": "PENDULUM_V3",
}

BBO_UPDATE_SCHEMA = pa.schema(
    [
        ("token_id", pa.string()),
        ("market_id", pa.string()),
        ("source_timestamp", pmxt.UTC_US),
        ("observed_at", pmxt.UTC_US),
        ("best_bid", pa.string()),
        ("best_ask", pa.string()),
        ("spread", pa.string()),
        ("source_version", pa.string()),
        ("source_generation", pa.string()),
        ("sequence", pa.uint64()),
        ("source_witness", pa.string()),
        ("witness_set", pa.string()),
        ("arrival_skew_us", pa.int64()),
    ]
)

_PROVENANCE_TYPES: dict[str, pa.DataType] = {
    "source_generation": pa.string(),
    "sequence": pa.uint64(),
    "source_witness": pa.string(),
    "witness_set": pa.string(),
    "arrival_skew_us": pa.int64(),
}


@dataclass(frozen=True, slots=True)
class SourceHour:
    """One planned acquisition hour used to define feature continuity."""

    window_id: str
    hour: datetime
    source_generation: str
    status: str = "DONE"
    rows: int = 1


@dataclass(frozen=True, slots=True)
class ContinuityAssignment:
    """Continuity segment for one source hour.

    A null segment is an explicit barrier: no lagged feature or forward target may cross it.
    Source-generation changes are also barriers because recorder semantics changed materially.
    """

    window_id: str
    hour: datetime
    source_generation: str
    segment_id: int | None
    valid: bool
    reason: str


def assign_continuity_segments(
    hours: list[SourceHour],
) -> tuple[ContinuityAssignment, ...]:
    """Assign source-aware continuity segments without bridging gaps or bad hours."""
    ordered = sorted(hours, key=lambda item: (item.window_id, item.hour))
    output: list[ContinuityAssignment] = []
    current_window: str | None = None
    previous_valid: SourceHour | None = None
    segment = 0
    barrier_since_valid = False

    for item in ordered:
        if item.window_id != current_window:
            current_window = item.window_id
            previous_valid = None
            barrier_since_valid = False
            segment = 0

        if item.status != "DONE":
            output.append(
                ContinuityAssignment(
                    window_id=item.window_id,
                    hour=item.hour,
                    source_generation=item.source_generation,
                    segment_id=None,
                    valid=False,
                    reason=f"INVALID_STATUS:{item.status}",
                )
            )
            previous_valid = None
            barrier_since_valid = True
            continue

        if item.rows <= 0:
            output.append(
                ContinuityAssignment(
                    window_id=item.window_id,
                    hour=item.hour,
                    source_generation=item.source_generation,
                    segment_id=None,
                    valid=False,
                    reason="ZERO_ROW_HOUR",
                )
            )
            previous_valid = None
            barrier_since_valid = True
            continue

        if previous_valid is None:
            segment += 1
            reason = "AFTER_INVALID_BARRIER" if barrier_since_valid else "INITIAL"
        elif item.source_generation != previous_valid.source_generation:
            segment += 1
            reason = "SOURCE_GENERATION_BOUNDARY"
        elif item.hour - previous_valid.hour != timedelta(hours=1):
            segment += 1
            reason = "TIME_GAP"
        else:
            reason = "CONTIGUOUS"

        output.append(
            ContinuityAssignment(
                window_id=item.window_id,
                hour=item.hour,
                source_generation=item.source_generation,
                segment_id=segment,
                valid=True,
                reason=reason,
            )
        )
        previous_valid = item
        barrier_since_valid = False

    return tuple(output)


def normalize_005g_extract(
    table: pa.Table,
    *,
    source_generation: str,
    token_ids: frozenset[str],
    window_start: datetime,
    window_end: datetime,
) -> pmxt.NormalizedExtract:
    """Normalize one raw source-hour into observable-time compatibility streams."""
    generation = source_generation.lower()
    if generation not in SOURCE_LABEL:
        raise ValueError(f"unsupported 005G source generation {source_generation!r}")

    if generation in {"v1", "v2", "ag6"}:
        pmxt_source = "PMXT_V1" if generation == "v1" else "PMXT_V2"
        out = pmxt.normalize_extract(
            table,
            source_version=pmxt_source,
            token_ids=token_ids,
            window_start=window_start,
            window_end=window_end,
        )
        if generation == "ag6":
            out = _relabel_source(out, SOURCE_LABEL[generation])
        return _append_static_provenance(out, generation)

    return _normalize_v3(
        table,
        token_ids=token_ids,
        window_start=window_start,
        window_end=window_end,
    )


def _append_static_provenance(
    out: pmxt.NormalizedExtract,
    generation: str,
) -> pmxt.NormalizedExtract:
    tables: dict[str, pa.Table] = {}
    for stream, table in out.tables.items():
        if stream == "rejects":
            tables[stream] = table
            continue
        columns: dict[str, Any] = {name: table[name] for name in table.schema.names}
        columns.update(
            {
                "source_generation": _const(generation, table.num_rows),
                "sequence": pa.nulls(table.num_rows, pa.uint64()),
                "source_witness": pa.nulls(table.num_rows, pa.string()),
                "witness_set": pa.nulls(table.num_rows, pa.string()),
                "arrival_skew_us": pa.nulls(table.num_rows, pa.int64()),
            }
        )
        tables[stream] = pa.table(columns)
    tables["bbo_updates"] = BBO_UPDATE_SCHEMA.empty_table()
    return pmxt.NormalizedExtract(tables=tables, counts=dict(out.counts))


def _relabel_source(
    out: pmxt.NormalizedExtract,
    label: str,
) -> pmxt.NormalizedExtract:
    tables: dict[str, pa.Table] = {}
    for stream, table in out.tables.items():
        index = table.schema.get_field_index("source_version")
        if index < 0:
            tables[stream] = table
        else:
            tables[stream] = table.set_column(
                index,
                "source_version",
                _const(label, table.num_rows),
            )
    return pmxt.NormalizedExtract(tables=tables, counts=dict(out.counts))


def _normalize_v3(
    table: pa.Table,
    *,
    token_ids: frozenset[str],
    window_start: datetime,
    window_end: datetime,
) -> pmxt.NormalizedExtract:
    _require_columns(
        table,
        {"event_type", "timestamp_received", "timestamp", "market", "asset_id"},
    )
    out = pmxt.NormalizedExtract(tables={})
    out.add("raw_rows", table.num_rows)

    token = _text_array(table["asset_id"])
    observed = pc.cast(table["timestamp_received"], pmxt.UTC_US)
    token_match = pc.fill_null(
        pc.is_in(token, value_set=pa.array(sorted(token_ids), pa.string())),
        False,
    )
    in_window = pc.and_(
        pc.greater_equal(observed, pa.scalar(window_start, pmxt.UTC_US)),
        pc.less(observed, pa.scalar(window_end, pmxt.UTC_US)),
    )
    matching = int(pc.sum(pc.cast(token_match, pa.int64())).as_py() or 0)
    outside = pc.and_(token_match, pc.invert(in_window))
    outside_n = int(pc.sum(pc.cast(outside, pa.int64())).as_py() or 0)
    selected = table.filter(pc.and_(token_match, in_window))
    out.add("rows_matching_tokens", matching)
    out.add("rows_outside_window", outside_n)
    out.add("rows_inside_window", selected.num_rows)

    event_type = pc.cast(selected["event_type"], pa.string())
    known = {"book", "price_change", "best_bid_ask", "last_trade_price", "tick_size_change"}
    known_mask = pc.is_in(event_type, value_set=pa.array(sorted(known)))
    known_n = int(pc.sum(pc.cast(known_mask, pa.int64())).as_py() or 0)
    out.add("ignored_token_rows", selected.num_rows - known_n)
    selected = selected.filter(known_mask)
    event_type = pc.cast(selected["event_type"], pa.string())

    books = selected.filter(pc.equal(event_type, "book"))
    out.tables["depth_snapshots"], book_rejects = _v3_books(books)

    changes = selected.filter(pc.equal(event_type, "price_change"))
    out.tables["book_changes"], change_rejects = _v3_changes(changes)

    bbo = selected.filter(pc.equal(event_type, "best_bid_ask"))
    out.tables["bbo_updates"] = _v3_bbo(bbo)

    trades = selected.filter(pc.equal(event_type, "last_trade_price"))
    out.tables["trades"], trade_rejects = _v3_trades(trades)

    ticks = selected.filter(pc.equal(event_type, "tick_size_change"))
    out.tables["tick_size_changes"] = _v3_ticks(ticks)

    rejects = book_rejects + change_rejects + trade_rejects
    out.tables["rejects"] = (
        pa.concat_tables(rejects) if rejects else pmxt.empty_table("rejects")
    )
    for stream, normalized in out.tables.items():
        out.add(f"{stream}_rows", normalized.num_rows)
    return out


def _v3_books(books: pa.Table) -> tuple[pa.Table, list[pa.Table]]:
    if books.num_rows == 0:
        return _empty_with_provenance("depth_snapshots"), []
    _require_columns(books, {"bids", "asks"})

    bid_levels, best_bid, bid_bad = _native_levels(books["bids"], descending=True)
    ask_levels, best_ask, ask_bad = _native_levels(books["asks"], descending=False)
    bad = pc.or_(bid_bad, ask_bad)

    midpoint: list[str | None] = []
    spread: list[str | None] = []
    for bid, ask in zip(best_bid.to_pylist(), best_ask.to_pylist(), strict=True):
        if bid is None or ask is None:
            midpoint.append(None)
            spread.append(None)
        else:
            bid_d = Decimal(bid)
            ask_d = Decimal(ask)
            midpoint.append(_decimal_text((bid_d + ask_d) / Decimal(2)))
            spread.append(_decimal_text(ask_d - bid_d))

    normalized = pa.table(
        {
            "token_id": _text_array(books["asset_id"]),
            "market_id": _text_array(books["market"]),
            "source_timestamp": pc.cast(books["timestamp"], pmxt.UTC_US),
            "state_observed_at": pc.cast(books["timestamp_received"], pmxt.UTC_US),
            "recorded_at": pc.cast(books["timestamp_received"], pmxt.UTC_US),
            "best_bid": best_bid,
            "best_ask": best_ask,
            "midpoint": pa.array(midpoint, pa.string()),
            "spread": pa.array(spread, pa.string()),
            "bids": bid_levels,
            "asks": ask_levels,
            "book_hash": pa.nulls(books.num_rows, pa.string()),
            "min_order_size": pa.nulls(books.num_rows, pa.string()),
            "tick_size": pa.nulls(books.num_rows, pa.string()),
            "neg_risk": pa.nulls(books.num_rows, pa.bool_()),
            "last_trade_price": pa.nulls(books.num_rows, pa.string()),
            "source_version": _const(SOURCE_LABEL["v3"], books.num_rows),
            "evidence_grade": _const(pmxt.GRADE_BOOK_SNAPSHOT, books.num_rows),
            **_v3_provenance(books),
        }
    )

    rejects: list[pa.Table] = []
    if pc.any(bad).as_py():
        rejected = normalized.filter(bad)
        rejects.append(
            pa.table(
                {
                    "stream": _const("depth_snapshots", rejected.num_rows),
                    "reason": _const("malformed_native_levels", rejected.num_rows),
                    "token_id": rejected["token_id"],
                    "observed_at": rejected["recorded_at"],
                    "source_version": rejected["source_version"],
                },
                schema=pmxt.SCHEMAS["rejects"],
            )
        )
        normalized = normalized.filter(pc.invert(bad))
    return normalized, rejects


def _v3_changes(changes: pa.Table) -> tuple[pa.Table, list[pa.Table]]:
    if changes.num_rows == 0:
        return _empty_with_provenance("book_changes"), []
    _require_columns(changes, {"price", "size", "side", "best_bid", "best_ask"})
    normalized = pa.table(
        {
            "token_id": _text_array(changes["asset_id"]),
            "market_id": _text_array(changes["market"]),
            "side": pc.cast(changes["side"], pa.string()),
            "price": _decimal_array(changes["price"]),
            "size": _decimal_array(changes["size"]),
            "source_timestamp": pc.cast(changes["timestamp"], pmxt.UTC_US),
            "observed_at": pc.cast(changes["timestamp_received"], pmxt.UTC_US),
            "best_bid": _decimal_array(changes["best_bid"]),
            "best_ask": _decimal_array(changes["best_ask"]),
            "book_hash": pa.nulls(changes.num_rows, pa.string()),
            "source_version": _const(SOURCE_LABEL["v3"], changes.num_rows),
            "evidence_grade": _const(pmxt.GRADE_PRICE_ONLY, changes.num_rows),
            **_v3_provenance(changes),
        }
    )
    return pmxt._validate_changes(normalized)


def _v3_bbo(rows: pa.Table) -> pa.Table:
    if rows.num_rows == 0:
        return BBO_UPDATE_SCHEMA.empty_table()
    _require_columns(rows, {"best_bid", "best_ask", "spread"})
    return pa.table(
        {
            "token_id": _text_array(rows["asset_id"]),
            "market_id": _text_array(rows["market"]),
            "source_timestamp": pc.cast(rows["timestamp"], pmxt.UTC_US),
            "observed_at": pc.cast(rows["timestamp_received"], pmxt.UTC_US),
            "best_bid": _decimal_array(rows["best_bid"]),
            "best_ask": _decimal_array(rows["best_ask"]),
            "spread": _decimal_array(rows["spread"]),
            "source_version": _const(SOURCE_LABEL["v3"], rows.num_rows),
            **_v3_provenance(rows),
        },
        schema=BBO_UPDATE_SCHEMA,
    )


def _v3_trades(trades: pa.Table) -> tuple[pa.Table, list[pa.Table]]:
    if trades.num_rows == 0:
        return _empty_with_provenance("trades"), []
    _require_columns(
        trades,
        {"price", "size", "side", "fee_rate_bps", "transaction_hash"},
    )
    token = _text_array(trades["asset_id"])
    tx_hash = _text_array(trades["transaction_hash"])
    event_ids = [
        hashed_trade_event_id(t, h)
        for t, h in zip(token.to_pylist(), tx_hash.to_pylist(), strict=True)
    ]
    normalized = pa.table(
        {
            "event_id": pa.array(event_ids, pa.string()),
            "token_id": token,
            "market_id": _text_array(trades["market"]),
            "price": _decimal_array(trades["price"]),
            "size": _decimal_array(trades["size"]),
            "side": pc.cast(trades["side"], pa.string()),
            "source_timestamp": pc.cast(trades["timestamp"], pmxt.UTC_US),
            "observed_at": pc.cast(trades["timestamp_received"], pmxt.UTC_US),
            "transaction_hash": tx_hash,
            "fee_rate_bps": pc.cast(trades["fee_rate_bps"], pa.string()),
            "source_version": _const(SOURCE_LABEL["v3"], trades.num_rows),
            "evidence_grade": _const(pmxt.GRADE_TRADE_FILL, trades.num_rows),
            **_v3_provenance(trades),
        }
    )
    bad = pc.or_(
        pc.fill_null(pmxt._out_of_unit_range(normalized["price"]), True),
        pc.fill_null(
            pc.invert(
                pc.match_substring_regex(
                    normalized["size"],
                    pmxt._UNSIGNED_DECIMAL,
                )
            ),
            False,
        ),
    )
    rejects: list[pa.Table] = []
    if pc.any(bad).as_py():
        rejected = normalized.filter(bad)
        rejects.append(
            pa.table(
                {
                    "stream": _const("trades", rejected.num_rows),
                    "reason": _const("invalid_price_or_size", rejected.num_rows),
                    "token_id": rejected["token_id"],
                    "observed_at": rejected["observed_at"],
                    "source_version": rejected["source_version"],
                },
                schema=pmxt.SCHEMAS["rejects"],
            )
        )
        normalized = normalized.filter(pc.invert(bad))
    return normalized, rejects


def _v3_ticks(rows: pa.Table) -> pa.Table:
    if rows.num_rows == 0:
        return _empty_with_provenance("tick_size_changes")
    _require_columns(rows, {"old_tick_size", "new_tick_size"})
    return pa.table(
        {
            "token_id": _text_array(rows["asset_id"]),
            "market_id": _text_array(rows["market"]),
            "source_timestamp": pc.cast(rows["timestamp"], pmxt.UTC_US),
            "observed_at": pc.cast(rows["timestamp_received"], pmxt.UTC_US),
            "old_tick_size": _decimal_array(rows["old_tick_size"]),
            "new_tick_size": _decimal_array(rows["new_tick_size"]),
            "source_version": _const(SOURCE_LABEL["v3"], rows.num_rows),
            **_v3_provenance(rows),
        }
    )


def _empty_with_provenance(stream: str) -> pa.Table:
    schema = pmxt.SCHEMAS[stream]
    columns: dict[str, Any] = {
        field.name: pa.array([], type=field.type) for field in schema
    }
    for name, dtype in _PROVENANCE_TYPES.items():
        columns[name] = pa.array([], type=dtype)
    return pa.table(columns)


def _v3_provenance(table: pa.Table) -> dict[str, Any]:
    return {
        "source_generation": _const("v3", table.num_rows),
        "sequence": _optional_cast(table, "sequence", pa.uint64()),
        "source_witness": _optional_text(table, "source_witness"),
        "witness_set": _optional_text(table, "witness_set"),
        "arrival_skew_us": _optional_cast(table, "arrival_skew", pa.int64()),
    }


def _native_levels(
    array: pa.Array | pa.ChunkedArray,
    *,
    descending: bool,
) -> tuple[pa.Array, pa.Array, pa.Array]:
    normalized: list[list[dict[str, str]]] = []
    best: list[str | None] = []
    malformed: list[bool] = []

    for raw in array.to_pylist():
        levels: list[tuple[Decimal, Decimal]] = []
        seen: set[Decimal] = set()
        bad = raw is None
        if raw is not None:
            for item in raw:
                try:
                    price = Decimal(str(item["price"]))
                    size = Decimal(str(item["size"]))
                except (InvalidOperation, KeyError, TypeError, ValueError):
                    bad = True
                    continue
                invalid = (
                    not Decimal(0) <= price <= Decimal(1)
                    or size < 0
                    or price in seen
                )
                if invalid:
                    bad = True
                    continue
                seen.add(price)
                levels.append((price, size))

        levels.sort(key=lambda value: value[0], reverse=descending)
        normalized.append(
            [
                {"price": _decimal_text(price), "size": _decimal_text(size)}
                for price, size in levels
            ]
        )
        best.append(_decimal_text(levels[0][0]) if levels else None)
        malformed.append(bad or not levels)

    return (
        pa.array(normalized, type=pmxt.LEVELS),
        pa.array(best, pa.string()),
        pa.array(malformed, pa.bool_()),
    )


def _decimal_array(array: pa.Array | pa.ChunkedArray) -> pa.Array:
    return pa.array(
        [
            None if value is None else _decimal_text(Decimal(str(value)))
            for value in array.to_pylist()
        ],
        pa.string(),
    )


def _text_array(array: pa.Array | pa.ChunkedArray) -> pa.Array:
    values: list[str | None] = []
    for value in array.to_pylist():
        if value is None:
            values.append(None)
        elif isinstance(value, bytes):
            try:
                values.append(value.decode("utf-8"))
            except UnicodeDecodeError:
                values.append("0x" + value.hex())
        else:
            values.append(str(value))
    return pa.array(values, pa.string())


def _optional_text(table: pa.Table, name: str) -> pa.Array:
    if table.schema.get_field_index(name) < 0:
        return pa.nulls(table.num_rows, pa.string())
    return _text_array(table[name])


def _optional_cast(
    table: pa.Table,
    name: str,
    dtype: pa.DataType,
) -> pa.Array | pa.ChunkedArray:
    if table.schema.get_field_index(name) < 0:
        return pa.nulls(table.num_rows, dtype)
    return pc.cast(table[name], dtype)


def _decimal_text(value: Decimal) -> str:
    if not value.is_finite():
        raise ValueError(f"non-finite decimal {value}")
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _const(value: str, n: int) -> pa.Array:
    return pa.array([value] * n, pa.string())


def _require_columns(table: pa.Table, names: set[str]) -> None:
    missing = sorted(names - set(table.schema.names))
    if missing:
        raise ValueError(f"005G source table missing required columns: {missing}")
