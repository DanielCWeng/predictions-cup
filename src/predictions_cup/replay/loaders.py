"""Read-only adapters from accepted SIG and Polymarket SQLite capture schemas."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from itertools import groupby
from pathlib import Path
from typing import Any

from predictions_cup.replay.model import (
    BookLevel,
    HealthPayload,
    QuotePayload,
    ReplayEvent,
    ReplayEventType,
    ReplaySource,
    ReplayState,
    TradePayload,
    TrustPayload,
)


class CaptureSchemaError(ValueError):
    """Raised when an offline capture does not match the accepted schema we consume."""


_SIG_SCHEMA = {
    "book_observations": {
        "id", "exchange_id", "market_id", "book_json", "best_bid", "best_ask",
        "rest_observed_at",
    },
    "realtime_trades": {
        "id", "exchange_id", "market_id", "price", "quantity", "executed_at",
        "observed_at",
    },
    "trust_transitions": {"id", "exchange_id", "transition", "observed_at"},
}
_POLY_SCHEMA = {
    "polymarket_book_observations": {
        "id", "token_id", "market_id", "source_timestamp", "state_observed_at",
        "observed_at", "best_bid", "best_ask", "last_trade_price", "book_valid",
    },
    "polymarket_book_changes": {
        "id", "token_id", "market_id", "source_timestamp", "observed_at",
        "best_bid", "best_ask",
    },
    "polymarket_book_snapshots": {
        "id", "token_id", "market_id", "source_timestamp", "observed_at",
        "best_bid", "best_ask", "bids_json", "asks_json", "last_trade_price",
    },
    "polymarket_trades": {
        "id", "token_id", "market_id", "price", "size", "side",
        "source_timestamp", "observed_at",
    },
    "ingestion_health": {"id", "recorded_at", "payload_json"},
}


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


def load_sig_capture(path: Path) -> tuple[ReplayEvent, ...]:
    events: list[ReplayEvent] = []
    with closing(_connect(path)) as db:
        _validate_schema(db, _SIG_SCHEMA, "SIG")
        for row in db.execute(
            "SELECT id, exchange_id, market_id, book_json, best_bid, best_ask, "
            "rest_observed_at FROM book_observations ORDER BY id"
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
        for row in db.execute(
            "SELECT id, exchange_id, market_id, price, quantity, executed_at, "
            "observed_at FROM realtime_trades ORDER BY id"
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
        for row in db.execute(
            "SELECT id, exchange_id, transition, observed_at "
            "FROM trust_transitions ORDER BY id"
        ):
            transition = _text(row[2], "SIG transition")
            trusted = _transition_trust(transition)
            if trusted is None:
                continue
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


def load_polymarket_capture(path: Path) -> tuple[ReplayEvent, ...]:
    events: list[ReplayEvent] = []
    with closing(_connect(path)) as db:
        _validate_schema(db, _POLY_SCHEMA, "Polymarket")
        for row in db.execute(
            "SELECT id, token_id, market_id, source_timestamp, state_observed_at, "
            "observed_at, best_bid, best_ask, last_trade_price, book_valid "
            "FROM polymarket_book_observations ORDER BY id"
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
        for row in db.execute(
            "SELECT id, token_id, market_id, source_timestamp, observed_at, "
            "best_bid, best_ask FROM polymarket_book_changes ORDER BY id"
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
        for row in db.execute(
            "SELECT id, token_id, market_id, source_timestamp, observed_at, "
            "best_bid, best_ask, bids_json, asks_json, last_trade_price "
            "FROM polymarket_book_snapshots ORDER BY id"
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
        for row in db.execute(
            "SELECT id, token_id, market_id, price, size, side, source_timestamp, "
            "observed_at FROM polymarket_trades ORDER BY id"
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
        for row in db.execute(
            "SELECT id, recorded_at, payload_json FROM ingestion_health ORDER BY id"
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
    *, sig_path: Path | None = None, polymarket_path: Path | None = None
) -> CaptureSummary:
    events = list(load_sig_capture(sig_path)) if sig_path is not None else []
    if polymarket_path is not None:
        events.extend(load_polymarket_capture(polymarket_path))
    ordered = list(_ordered(events))
    instruments = tuple(
        sorted(
            {
                f"{event.source.value}:{event.instrument_id}"
                for event in ordered
                if event.instrument_id != "*"
            }
        )
    )
    return CaptureSummary(
        records_loaded=len(ordered),
        start_at=None if not ordered else ordered[0].observed_at,
        end_at=None if not ordered else ordered[-1].observed_at,
        instruments=instruments,
        trusted_sig_observations=_count_trusted_sig_books(ordered),
        external_observations=sum(
            event.source is ReplaySource.POLYMARKET
            and event.event_type in _QUOTE_TYPES
            for event in ordered
        ),
        data_gaps=sum(_is_gap(event) for event in ordered),
    )


_QUOTE_TYPES = {
    ReplayEventType.BOOK_OBSERVATION,
    ReplayEventType.BOOK_CHANGE,
    ReplayEventType.DEPTH_SNAPSHOT,
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


def _count_trusted_sig_books(events: list[ReplayEvent]) -> int:
    state = ReplayState()
    count = 0
    for _, group_iter in groupby(events, key=lambda event: event.observed_at):
        group = tuple(group_iter)
        for event in group:
            state.apply(event)
        for event in group:
            if event.source is ReplaySource.SIG and event.event_type in _QUOTE_TYPES:
                view = state.view(ReplaySource.SIG, event.instrument_id)
                count += int(view is not None and view.trusted)
    return count


def _is_gap(event: ReplayEvent) -> bool:
    payload = event.payload
    return (
        isinstance(payload, TrustPayload)
        and not payload.trusted
        or isinstance(payload, HealthPayload)
        and not payload.available
        or isinstance(payload, QuotePayload)
        and not payload.book_valid
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


def _ordered(events: list[ReplayEvent]) -> tuple[ReplayEvent, ...]:
    return tuple(sorted(events, key=lambda event: event.sort_key))
