"""Read-only adapters from accepted SIG and Polymarket SQLite capture schemas."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from predictions_cup.replay.model import (
    BookLevel,
    HealthPayload,
    QuotePayload,
    ReplayEvent,
    ReplayEventType,
    ReplaySource,
    TradePayload,
    TrustPayload,
)


class CaptureSchemaError(ValueError):
    """Raised when an offline capture does not match the accepted schema we consume."""


_SIG_SCHEMA = {
    "book_observations": {
        "id",
        "exchange_id",
        "market_id",
        "book_json",
        "best_bid",
        "best_ask",
        "rest_observed_at",
    },
    "realtime_trades": {
        "id",
        "exchange_id",
        "market_id",
        "price",
        "quantity",
        "executed_at",
        "observed_at",
    },
    "trust_transitions": {"id", "exchange_id", "transition", "observed_at"},
}
_POLY_SCHEMA = {
    "polymarket_book_observations": {
        "id",
        "token_id",
        "market_id",
        "source_timestamp",
        "state_observed_at",
        "observed_at",
        "best_bid",
        "best_ask",
        "last_trade_price",
        "book_valid",
    },
    "polymarket_book_changes": {
        "id",
        "token_id",
        "market_id",
        "source_timestamp",
        "observed_at",
        "best_bid",
        "best_ask",
    },
    "polymarket_book_snapshots": {
        "id",
        "token_id",
        "market_id",
        "source_timestamp",
        "observed_at",
        "best_bid",
        "best_ask",
        "bids_json",
        "asks_json",
        "last_trade_price",
    },
    "polymarket_trades": {
        "id",
        "token_id",
        "market_id",
        "price",
        "size",
        "side",
        "source_timestamp",
        "observed_at",
    },
    "ingestion_health": {"id", "recorded_at", "payload_json"},
}
_RECOGNIZED_TRUST_SQL = (
    "(transition LIKE 'TRUSTED%' OR transition LIKE 'UNTRUSTED%' "
    "OR transition = 'RECONCILING')"
)
_UNTRUSTED_SQL = "(transition LIKE 'UNTRUSTED%' OR transition = 'RECONCILING')"


@dataclass(frozen=True, slots=True)
class CaptureSelection:
    """SQL-pushed observable-time and instrument selection for a replay slice.

    Bounds are [start_at, end_at). No state before start_at is seeded implicitly.
    """

    start_at: datetime | None = None
    end_at: datetime | None = None
    sig_exchange_ids: tuple[str, ...] | None = None
    polymarket_token_ids: tuple[str, ...] | None = None

    def __post_init__(self) -> None:
        start = _normalize_bound(self.start_at, "start_at")
        end = _normalize_bound(self.end_at, "end_at")
        if start is not None and end is not None and end <= start:
            raise ValueError("end_at must be after start_at")
        object.__setattr__(self, "start_at", start)
        object.__setattr__(self, "end_at", end)
        object.__setattr__(
            self,
            "sig_exchange_ids",
            _normalize_ids(self.sig_exchange_ids, "sig_exchange_ids"),
        )
        object.__setattr__(
            self,
            "polymarket_token_ids",
            _normalize_ids(self.polymarket_token_ids, "polymarket_token_ids"),
        )


@dataclass(frozen=True, slots=True)
class CaptureSummary:
    records_loaded: int
    start_at: datetime | None
    end_at: datetime | None
    instruments: tuple[str, ...]
    trusted_sig_observations: int
    external_observations: int
    data_gaps: int

    def as_record(self) -> dict[str, object]:
        return {
            "records_loaded": self.records_loaded,
            "time_span": {
                "start": None if self.start_at is None else self.start_at.isoformat(),
                "end": None if self.end_at is None else self.end_at.isoformat(),
            },
            "instruments": list(self.instruments),
            "trusted_sig_observations": self.trusted_sig_observations,
            "external_observations": self.external_observations,
            "data_gaps": self.data_gaps,
        }


def load_sig_capture(
    path: Path,
    *,
    selection: CaptureSelection | None = None,
) -> tuple[ReplayEvent, ...]:
    """Materialize only the selected SIG slice; filtering is performed by SQLite."""
    selected = selection or CaptureSelection()
    events: list[ReplayEvent] = []
    with closing(_connect(path)) as db:
        _validate_schema(db, _SIG_SCHEMA, "SIG")

        where, params = _where(
            selected,
            "rest_observed_at",
            "exchange_id",
            selected.sig_exchange_ids,
        )
        for row in db.execute(
            "SELECT id, exchange_id, market_id, book_json, best_bid, best_ask, "
            f"rest_observed_at FROM book_observations{where} "
            "ORDER BY rest_observed_at, id",
            params,
        ):
            book = _json_object(row[3], "SIG book_json")
            observed = _dt(row[6], "SIG rest_observed_at")
            events.append(
                _quote_event(
                    source=ReplaySource.SIG,
                    event_type=ReplayEventType.BOOK_OBSERVATION,
                    sequence=_integer(row[0], "SIG book id"),
                    instrument=_text(row[1], "SIG exchange_id"),
                    market=_text(row[2], "SIG market_id"),
                    observed=observed,
                    source_at=_optional_dt(book.get("timestamp"), "SIG book timestamp"),
                    quote_observed=observed,
                    bid=_optional_decimal(row[4], "SIG best_bid"),
                    ask=_optional_decimal(row[5], "SIG best_ask"),
                    bids=_sig_levels(book.get("bids"), "bids"),
                    asks=_sig_levels(book.get("asks"), "asks"),
                )
            )

        where, params = _where(
            selected,
            "observed_at",
            "exchange_id",
            selected.sig_exchange_ids,
        )
        for row in db.execute(
            "SELECT id, exchange_id, market_id, price, quantity, executed_at, "
            f"observed_at FROM realtime_trades{where} ORDER BY observed_at, id",
            params,
        ):
            events.append(
                ReplayEvent(
                    observed_at=_dt(row[6], "SIG trade observed_at"),
                    source_at=_dt(row[5], "SIG executed_at"),
                    source=ReplaySource.SIG,
                    event_type=ReplayEventType.TRADE,
                    instrument_id=_text(row[1], "SIG exchange_id"),
                    market_id=_text(row[2], "SIG market_id"),
                    sequence=_integer(row[0], "SIG trade id"),
                    payload=TradePayload(
                        price=_decimal(row[3], "SIG trade price"),
                        quantity=_decimal(row[4], "SIG trade quantity"),
                    ),
                )
            )

        where, params = _where(
            selected,
            "observed_at",
            "exchange_id",
            selected.sig_exchange_ids,
            include_null_instrument=True,
            extra_clause=_RECOGNIZED_TRUST_SQL,
        )
        for row in db.execute(
            "SELECT id, exchange_id, transition, observed_at "
            f"FROM trust_transitions{where} ORDER BY observed_at, id",
            params,
        ):
            transition = _text(row[2], "SIG transition")
            trusted = _transition_trust(transition)
            if trusted is None:
                raise CaptureSchemaError("recognized SIG trust transition was not understood")
            events.append(
                ReplayEvent(
                    observed_at=_dt(row[3], "SIG transition observed_at"),
                    source_at=None,
                    source=ReplaySource.SIG,
                    event_type=ReplayEventType.TRUST,
                    instrument_id=(
                        "*" if row[1] is None else _text(row[1], "SIG exchange_id")
                    ),
                    market_id=None,
                    sequence=_integer(row[0], "SIG transition id"),
                    payload=TrustPayload(trusted=trusted, transition=transition),
                )
            )
    return _ordered(events)


def load_polymarket_capture(
    path: Path,
    *,
    selection: CaptureSelection | None = None,
) -> tuple[ReplayEvent, ...]:
    """Materialize only the selected Polymarket slice; filtering is performed by SQLite."""
    selected = selection or CaptureSelection()
    events: list[ReplayEvent] = []
    with closing(_connect(path)) as db:
        _validate_schema(db, _POLY_SCHEMA, "Polymarket")

        where, params = _where(
            selected,
            "observed_at",
            "token_id",
            selected.polymarket_token_ids,
        )
        for row in db.execute(
            "SELECT id, token_id, market_id, source_timestamp, state_observed_at, "
            "observed_at, best_bid, best_ask, last_trade_price, book_valid "
            f"FROM polymarket_book_observations{where} ORDER BY observed_at, id",
            params,
        ):
            events.append(
                _quote_event(
                    source=ReplaySource.POLYMARKET,
                    event_type=ReplayEventType.BOOK_OBSERVATION,
                    sequence=_integer(row[0], "Polymarket observation id"),
                    instrument=_text(row[1], "Polymarket token_id"),
                    market=_text(row[2], "Polymarket market_id"),
                    observed=_dt(row[5], "Polymarket observation observed_at"),
                    source_at=_optional_dt(
                        row[3], "Polymarket observation source_timestamp"
                    ),
                    quote_observed=_dt(row[4], "Polymarket state_observed_at"),
                    bid=_optional_decimal(row[6], "Polymarket best_bid"),
                    ask=_optional_decimal(row[7], "Polymarket best_ask"),
                    last_trade=_optional_decimal(
                        row[8], "Polymarket last_trade_price"
                    ),
                    valid=_book_valid(row[9]),
                )
            )

        where, params = _where(
            selected,
            "observed_at",
            "token_id",
            selected.polymarket_token_ids,
        )
        for row in db.execute(
            "SELECT id, token_id, market_id, source_timestamp, observed_at, "
            "best_bid, best_ask "
            f"FROM polymarket_book_changes{where} ORDER BY observed_at, id",
            params,
        ):
            observed = _dt(row[4], "Polymarket change observed_at")
            events.append(
                _quote_event(
                    source=ReplaySource.POLYMARKET,
                    event_type=ReplayEventType.BOOK_CHANGE,
                    sequence=_integer(row[0], "Polymarket change id"),
                    instrument=_text(row[1], "Polymarket token_id"),
                    market=_text(row[2], "Polymarket market_id"),
                    observed=observed,
                    source_at=_optional_dt(row[3], "Polymarket change source_timestamp"),
                    quote_observed=observed,
                    bid=_optional_decimal(row[5], "Polymarket change best_bid"),
                    ask=_optional_decimal(row[6], "Polymarket change best_ask"),
                )
            )

        where, params = _where(
            selected,
            "observed_at",
            "token_id",
            selected.polymarket_token_ids,
        )
        for row in db.execute(
            "SELECT id, token_id, market_id, source_timestamp, observed_at, "
            "best_bid, best_ask, bids_json, asks_json, last_trade_price "
            f"FROM polymarket_book_snapshots{where} ORDER BY observed_at, id",
            params,
        ):
            observed = _dt(row[4], "Polymarket snapshot observed_at")
            events.append(
                _quote_event(
                    source=ReplaySource.POLYMARKET,
                    event_type=ReplayEventType.DEPTH_SNAPSHOT,
                    sequence=_integer(row[0], "Polymarket snapshot id"),
                    instrument=_text(row[1], "Polymarket token_id"),
                    market=_text(row[2], "Polymarket market_id"),
                    observed=observed,
                    source_at=_optional_dt(
                        row[3], "Polymarket snapshot source_timestamp"
                    ),
                    quote_observed=observed,
                    bid=_optional_decimal(row[5], "Polymarket snapshot best_bid"),
                    ask=_optional_decimal(row[6], "Polymarket snapshot best_ask"),
                    bids=_poly_levels(row[7], "Polymarket bids_json"),
                    asks=_poly_levels(row[8], "Polymarket asks_json"),
                    last_trade=_optional_decimal(
                        row[9], "Polymarket snapshot last_trade_price"
                    ),
                )
            )

        where, params = _where(
            selected,
            "observed_at",
            "token_id",
            selected.polymarket_token_ids,
        )
        for row in db.execute(
            "SELECT id, token_id, market_id, price, size, side, source_timestamp, "
            f"observed_at FROM polymarket_trades{where} ORDER BY observed_at, id",
            params,
        ):
            events.append(
                ReplayEvent(
                    observed_at=_dt(row[7], "Polymarket trade observed_at"),
                    source_at=_optional_dt(row[6], "Polymarket trade source_timestamp"),
                    source=ReplaySource.POLYMARKET,
                    event_type=ReplayEventType.TRADE,
                    instrument_id=_text(row[1], "Polymarket token_id"),
                    market_id=_text(row[2], "Polymarket market_id"),
                    sequence=_integer(row[0], "Polymarket trade id"),
                    payload=TradePayload(
                        price=_decimal(row[3], "Polymarket trade price"),
                        quantity=_optional_decimal(row[4], "Polymarket trade size"),
                        side=(
                            None
                            if row[5] is None
                            else _text(row[5], "Polymarket trade side")
                        ),
                    ),
                )
            )

        where, params = _where(selected, "recorded_at")
        for row in db.execute(
            "SELECT id, recorded_at, payload_json "
            f"FROM ingestion_health{where} ORDER BY recorded_at, id",
            params,
        ):
            payload = _json_object(row[2], "Polymarket health payload_json")
            connected = payload.get("websocket_connected")
            if not isinstance(connected, bool):
                raise CaptureSchemaError(
                    "Polymarket health payload websocket_connected must be boolean"
                )
            detail = payload.get("last_reconnect_reason")
            events.append(
                ReplayEvent(
                    observed_at=_dt(row[1], "Polymarket health recorded_at"),
                    source_at=None,
                    source=ReplaySource.POLYMARKET,
                    event_type=ReplayEventType.HEALTH,
                    instrument_id="*",
                    market_id=None,
                    sequence=_integer(row[0], "Polymarket health id"),
                    payload=HealthPayload(
                        available=connected,
                        detail=None if detail is None else str(detail),
                    ),
                )
            )
    return _ordered(events)


def summarize_captures(
    *,
    sig_path: Path | None = None,
    polymarket_path: Path | None = None,
    selection: CaptureSelection | None = None,
) -> CaptureSummary:
    """Inspect selected capture rows using SQL aggregates, not ReplayEvent materialization."""
    selected = selection or CaptureSelection()
    records = 0
    external_observations = 0
    data_gaps = 0
    trusted_sig_observations = 0
    instruments: set[str] = set()
    times: list[datetime] = []

    if sig_path is not None:
        with closing(_connect(sig_path)) as db:
            _validate_schema(db, _SIG_SCHEMA, "SIG")
            sig_records, sig_gaps, sig_trusted, sig_instruments, sig_times = (
                _summarize_sig(db, selected)
            )
            records += sig_records
            data_gaps += sig_gaps
            trusted_sig_observations += sig_trusted
            instruments.update(f"sig:{value}" for value in sig_instruments)
            times.extend(sig_times)

    if polymarket_path is not None:
        with closing(_connect(polymarket_path)) as db:
            _validate_schema(db, _POLY_SCHEMA, "Polymarket")
            poly_records, poly_quotes, poly_gaps, poly_instruments, poly_times = (
                _summarize_polymarket(db, selected)
            )
            records += poly_records
            external_observations += poly_quotes
            data_gaps += poly_gaps
            instruments.update(f"polymarket:{value}" for value in poly_instruments)
            times.extend(poly_times)

    return CaptureSummary(
        records_loaded=records,
        start_at=min(times) if times else None,
        end_at=max(times) if times else None,
        instruments=tuple(sorted(instruments)),
        trusted_sig_observations=trusted_sig_observations,
        external_observations=external_observations,
        data_gaps=data_gaps,
    )


def _summarize_sig(
    db: sqlite3.Connection,
    selection: CaptureSelection,
) -> tuple[int, int, int, set[str], list[datetime]]:
    records = 0
    gaps = 0
    times: list[datetime] = []
    instruments: set[str] = set()

    table_specs = (
        ("book_observations", "rest_observed_at", "exchange_id", None),
        ("realtime_trades", "observed_at", "exchange_id", None),
        (
            "trust_transitions",
            "observed_at",
            "exchange_id",
            _RECOGNIZED_TRUST_SQL,
        ),
    )
    for table, time_column, instrument_column, extra_clause in table_specs:
        where, params = _where(
            selection,
            time_column,
            instrument_column,
            selection.sig_exchange_ids,
            include_null_instrument=table == "trust_transitions",
            extra_clause=extra_clause,
        )
        records += _count(db, table, where, params)
        times.extend(_minmax(db, table, time_column, where, params))

    for table, time_column in (
        ("book_observations", "rest_observed_at"),
        ("realtime_trades", "observed_at"),
        ("trust_transitions", "observed_at"),
    ):
        extra = "exchange_id IS NOT NULL"
        if table == "trust_transitions":
            extra = f"{extra} AND {_RECOGNIZED_TRUST_SQL}"
        where, params = _where(
            selection,
            time_column,
            "exchange_id",
            selection.sig_exchange_ids,
            extra_clause=extra,
        )
        instruments.update(
            _distinct_text(db, table, "exchange_id", where, params, "SIG exchange_id")
        )

    where, params = _where(
        selection,
        "observed_at",
        "exchange_id",
        selection.sig_exchange_ids,
        include_null_instrument=True,
        extra_clause=_UNTRUSTED_SQL,
    )
    gaps += _count(db, "trust_transitions", where, params)
    trusted = _count_trusted_sig_books_sql(db, selection)
    return records, gaps, trusted, instruments, times


def _summarize_polymarket(
    db: sqlite3.Connection,
    selection: CaptureSelection,
) -> tuple[int, int, int, set[str], list[datetime]]:
    records = 0
    external_observations = 0
    gaps = 0
    times: list[datetime] = []
    instruments: set[str] = set()

    token_tables = (
        ("polymarket_book_observations", "observed_at", True),
        ("polymarket_book_changes", "observed_at", True),
        ("polymarket_book_snapshots", "observed_at", True),
        ("polymarket_trades", "observed_at", False),
    )
    for table, time_column, is_quote in token_tables:
        where, params = _where(
            selection,
            time_column,
            "token_id",
            selection.polymarket_token_ids,
        )
        count = _count(db, table, where, params)
        records += count
        if is_quote:
            external_observations += count
        times.extend(_minmax(db, table, time_column, where, params))
        instruments.update(
            _distinct_text(db, table, "token_id", where, params, "Polymarket token_id")
        )

    where, params = _where(selection, "recorded_at")
    health_count = _count(db, "ingestion_health", where, params)
    records += health_count
    times.extend(_minmax(db, "ingestion_health", "recorded_at", where, params))

    where, params = _where(
        selection,
        "observed_at",
        "token_id",
        selection.polymarket_token_ids,
        extra_clause="book_valid = 0",
    )
    gaps += _count(db, "polymarket_book_observations", where, params)
    gaps += _count_disconnected_health(db, selection)
    return records, external_observations, gaps, instruments, times


def _count_trusted_sig_books_sql(
    db: sqlite3.Connection,
    selection: CaptureSelection,
) -> int:
    book_where, params = _where(
        selection,
        "b.rest_observed_at",
        "b.exchange_id",
        selection.sig_exchange_ids,
    )
    trust_floor = ""
    trust_params: list[object] = []
    if selection.start_at is not None:
        trust_floor = " AND t.observed_at >= ?"
        trust_params.append(selection.start_at.isoformat())
    sql = (
        "SELECT COUNT(*) FROM book_observations b"
        f"{book_where}"
        + (" AND " if book_where else " WHERE ")
        + "(SELECT t.transition FROM trust_transitions t "
        "WHERE t.observed_at <= b.rest_observed_at "
        "AND (t.exchange_id IS NULL OR t.exchange_id = b.exchange_id) "
        f"AND {_RECOGNIZED_TRUST_SQL}"
        f"{trust_floor} "
        "ORDER BY t.observed_at DESC, "
        "CASE WHEN t.exchange_id = b.exchange_id THEN 1 ELSE 0 END DESC, "
        "t.id DESC LIMIT 1) LIKE 'TRUSTED%'"
    )
    row = db.execute(sql, (*params, *trust_params)).fetchone()
    return _integer(row[0], "trusted SIG observation count") if row is not None else 0


def _count_disconnected_health(
    db: sqlite3.Connection,
    selection: CaptureSelection,
) -> int:
    where, params = _where(selection, "recorded_at")
    count = 0
    for row in db.execute(
        f"SELECT payload_json FROM ingestion_health{where}",
        params,
    ):
        payload = _json_object(row[0], "Polymarket health payload_json")
        connected = payload.get("websocket_connected")
        if not isinstance(connected, bool):
            raise CaptureSchemaError(
                "Polymarket health payload websocket_connected must be boolean"
            )
        count += int(not connected)
    return count


def _where(
    selection: CaptureSelection,
    time_column: str,
    instrument_column: str | None = None,
    instrument_ids: tuple[str, ...] | None = None,
    *,
    include_null_instrument: bool = False,
    extra_clause: str | None = None,
) -> tuple[str, tuple[object, ...]]:
    clauses: list[str] = []
    params: list[object] = []
    if selection.start_at is not None:
        clauses.append(f"{time_column} >= ?")
        params.append(selection.start_at.isoformat())
    if selection.end_at is not None:
        clauses.append(f"{time_column} < ?")
        params.append(selection.end_at.isoformat())
    if instrument_column is not None and instrument_ids is not None:
        if instrument_ids:
            placeholders = ", ".join("?" for _ in instrument_ids)
            instrument_clause = f"{instrument_column} IN ({placeholders})"
            params.extend(instrument_ids)
            if include_null_instrument:
                instrument_clause = (
                    f"({instrument_column} IS NULL OR {instrument_clause})"
                )
            clauses.append(instrument_clause)
        elif include_null_instrument:
            clauses.append(f"{instrument_column} IS NULL")
        else:
            clauses.append("0")
    if extra_clause is not None:
        clauses.append(extra_clause)
    if not clauses:
        return "", ()
    return " WHERE " + " AND ".join(clauses), tuple(params)


def _count(
    db: sqlite3.Connection,
    table: str,
    where: str,
    params: tuple[object, ...],
) -> int:
    row = db.execute(f"SELECT COUNT(*) FROM {table}{where}", params).fetchone()
    return _integer(row[0], f"{table} count") if row is not None else 0


def _minmax(
    db: sqlite3.Connection,
    table: str,
    time_column: str,
    where: str,
    params: tuple[object, ...],
) -> list[datetime]:
    row = db.execute(
        f"SELECT MIN({time_column}), MAX({time_column}) FROM {table}{where}",
        params,
    ).fetchone()
    if row is None:
        return []
    result: list[datetime] = []
    if row[0] is not None:
        result.append(_dt(row[0], f"{table} min time"))
    if row[1] is not None:
        result.append(_dt(row[1], f"{table} max time"))
    return result


def _distinct_text(
    db: sqlite3.Connection,
    table: str,
    column: str,
    where: str,
    params: tuple[object, ...],
    label: str,
) -> set[str]:
    return {
        _text(row[0], label)
        for row in db.execute(f"SELECT DISTINCT {column} FROM {table}{where}", params)
    }


def _quote_event(
    *,
    source: ReplaySource,
    event_type: ReplayEventType,
    sequence: int,
    instrument: str,
    market: str,
    observed: datetime,
    source_at: datetime | None,
    quote_observed: datetime,
    bid: Decimal | None,
    ask: Decimal | None,
    bids: tuple[BookLevel, ...] = (),
    asks: tuple[BookLevel, ...] = (),
    last_trade: Decimal | None = None,
    valid: bool = True,
) -> ReplayEvent:
    return ReplayEvent(
        observed_at=observed,
        source_at=source_at,
        source=source,
        event_type=event_type,
        instrument_id=instrument,
        market_id=market,
        sequence=sequence,
        payload=QuotePayload(
            best_bid=bid,
            best_ask=ask,
            quote_observed_at=quote_observed,
            bids=bids,
            asks=asks,
            last_trade=last_trade,
            book_valid=valid,
        ),
    )


def _connect(path: Path) -> sqlite3.Connection:
    if not path.is_file():
        raise CaptureSchemaError(f"capture database does not exist: {path}")
    return sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)


def _validate_schema(
    db: sqlite3.Connection,
    required: dict[str, set[str]],
    label: str,
) -> None:
    tables = {
        str(row[0])
        for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    missing_tables = sorted(set(required) - tables)
    if missing_tables:
        raise CaptureSchemaError(
            f"{label} capture is missing required tables: {', '.join(missing_tables)}"
        )
    for table, columns in required.items():
        actual = {str(row[1]) for row in db.execute(f"PRAGMA table_info({table})")}
        missing = sorted(columns - actual)
        if missing:
            raise CaptureSchemaError(
                f"{label} table {table} is missing columns: {', '.join(missing)}"
            )


def _transition_trust(transition: str) -> bool | None:
    if transition.startswith("TRUSTED"):
        return True
    if transition.startswith("UNTRUSTED") or transition == "RECONCILING":
        return False
    return None


def _sig_levels(value: object, label: str) -> tuple[BookLevel, ...]:
    if not isinstance(value, list):
        raise CaptureSchemaError(f"SIG {label} must be a JSON list")
    levels: list[BookLevel] = []
    for raw in value:
        if not isinstance(raw, dict):
            raise CaptureSchemaError(f"SIG {label} level must be an object")
        levels.append(
            BookLevel(
                _decimal(raw.get("price"), f"SIG {label} price"),
                _decimal(raw.get("quantity"), f"SIG {label} quantity"),
            )
        )
    return tuple(levels)


def _poly_levels(value: object, label: str) -> tuple[BookLevel, ...]:
    raw_levels = _json_array(value, label)
    levels: list[BookLevel] = []
    for raw in raw_levels:
        if not isinstance(raw, list) or len(raw) != 2:
            raise CaptureSchemaError(f"{label} level must be [price, quantity]")
        levels.append(
            BookLevel(
                _decimal(raw[0], f"{label} price"),
                _decimal(raw[1], f"{label} quantity"),
            )
        )
    return tuple(levels)


def _json_object(value: object, label: str) -> dict[str, Any]:
    parsed = _json(value, label)
    if not isinstance(parsed, dict) or not all(isinstance(key, str) for key in parsed):
        raise CaptureSchemaError(f"{label} must contain a JSON object with string keys")
    return {str(key): item for key, item in parsed.items()}


def _json_array(value: object, label: str) -> list[object]:
    parsed = _json(value, label)
    if not isinstance(parsed, list):
        raise CaptureSchemaError(f"{label} must contain a JSON array")
    return list(parsed)


def _json(value: object, label: str) -> Any:
    if not isinstance(value, str):
        raise CaptureSchemaError(f"{label} must be text")
    try:
        return json.loads(value)
    except json.JSONDecodeError as exc:
        raise CaptureSchemaError(f"{label} is not valid JSON") from exc


def _dt(value: object, label: str) -> datetime:
    if not isinstance(value, str):
        raise CaptureSchemaError(f"{label} must be an ISO-8601 string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise CaptureSchemaError(f"{label} is not valid ISO-8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise CaptureSchemaError(f"{label} must be timezone-aware")
    return parsed.astimezone(UTC)


def _optional_dt(value: object, label: str) -> datetime | None:
    return None if value is None else _dt(value, label)


def _decimal(value: object, label: str) -> Decimal:
    if isinstance(value, float):
        raise CaptureSchemaError(f"{label} must not be a binary float")
    if value is None:
        raise CaptureSchemaError(f"{label} must not be null")
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise CaptureSchemaError(f"{label} is not a decimal") from exc
    if not parsed.is_finite():
        raise CaptureSchemaError(f"{label} must be finite")
    return parsed


def _optional_decimal(value: object, label: str) -> Decimal | None:
    return None if value is None else _decimal(value, label)


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise CaptureSchemaError(f"{label} must be non-blank text")
    return value


def _integer(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise CaptureSchemaError(f"{label} must be an integer")
    return value


def _book_valid(value: object) -> bool:
    integer = _integer(value, "Polymarket book_valid")
    if integer not in {0, 1}:
        raise CaptureSchemaError("Polymarket book_valid must be 0 or 1")
    return bool(integer)


def _normalize_bound(value: datetime | None, label: str) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware")
    return value.astimezone(UTC)


def _normalize_ids(
    values: tuple[str, ...] | None,
    label: str,
) -> tuple[str, ...] | None:
    if values is None:
        return None
    if any(not value.strip() for value in values):
        raise ValueError(f"{label} must not contain blank identifiers")
    return tuple(sorted(set(values)))


def _ordered(events: list[ReplayEvent]) -> tuple[ReplayEvent, ...]:
    return tuple(sorted(events, key=lambda event: event.sort_key))
