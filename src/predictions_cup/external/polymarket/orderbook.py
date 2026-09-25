"""In-memory Polymarket order books with snapshot-before-delta safety."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from predictions_cup.external.polymarket.models import (
    BookLevel,
    BookSnapshot,
    JsonObject,
    PayloadError,
    optional_text,
    parse_decimal,
    parse_source_timestamp,
    require_text,
)


@dataclass(slots=True)
class _MutableBook:
    market_id: str
    token_id: str
    bids: dict[Decimal, Decimal]
    asks: dict[Decimal, Decimal]
    source_timestamp: datetime | None
    observed_at: datetime
    book_hash: str | None
    min_order_size: Decimal | None
    tick_size: Decimal | None
    neg_risk: bool | None
    last_trade_price: Decimal | None


@dataclass(frozen=True, slots=True)
class DeltaResult:
    changed_tokens: frozenset[str]
    uninitialized_deltas: int


class OrderBookStore:
    """Maintains authoritative full snapshots plus subsequent deltas."""

    def __init__(self) -> None:
        self._books: dict[str, _MutableBook] = {}

    @property
    def initialized_tokens(self) -> frozenset[str]:
        return frozenset(self._books)

    def invalidate_all(self) -> None:
        self._books.clear()

    def invalidate(self, token_ids: set[str] | frozenset[str]) -> None:
        for token_id in token_ids:
            self._books.pop(token_id, None)

    def apply_full_snapshot(self, payload: JsonObject, observed_at: datetime) -> str:
        event = _unwrap_market_event(payload)
        token_id = require_text(event.get("asset_id") or event.get("tokenId"), "asset_id")
        market_id = require_text(event.get("market"), "market")
        bids = _levels(event.get("bids"), side="bids")
        asks = _levels(event.get("asks"), side="asks")
        observed = observed_at.astimezone(UTC)
        neg_risk_raw = event.get("neg_risk")
        if neg_risk_raw is None:
            neg_risk_raw = event.get("negRisk")
        neg_risk = neg_risk_raw if isinstance(neg_risk_raw, bool) else None

        self._books[token_id] = _MutableBook(
            market_id=market_id,
            token_id=token_id,
            bids=bids,
            asks=asks,
            source_timestamp=parse_source_timestamp(event.get("timestamp")),
            observed_at=observed,
            book_hash=optional_text(event.get("hash")),
            min_order_size=parse_decimal(
                event.get("min_order_size") or event.get("minOrderSize"),
                "min_order_size",
                optional=True,
            ),
            tick_size=parse_decimal(
                event.get("tick_size") or event.get("tickSize"), "tick_size", optional=True
            ),
            neg_risk=neg_risk,
            last_trade_price=parse_decimal(
                event.get("last_trade_price") or event.get("lastTradePrice"),
                "last_trade_price",
                optional=True,
            ),
        )
        return token_id

    def apply_price_change(self, payload: JsonObject, observed_at: datetime) -> DeltaResult:
        event = _unwrap_market_event(payload)
        changes_raw = event.get("price_changes") or event.get("priceChanges")
        if not isinstance(changes_raw, list):
            raise PayloadError("price_change event must contain price_changes")
        source_ts = parse_source_timestamp(event.get("timestamp"))
        observed = observed_at.astimezone(UTC)
        changed: set[str] = set()
        missing = 0

        for change_raw in changes_raw:
            if not isinstance(change_raw, dict):
                raise PayloadError("price change entry must be an object")
            change: dict[str, Any] = change_raw
            token_id = require_text(
                change.get("asset_id") or change.get("tokenId"), "price_change asset_id"
            )
            book = self._books.get(token_id)
            if book is None:
                missing += 1
                continue
            side_raw = require_text(change.get("side"), "price_change side").upper()
            if side_raw == "BUY":
                side = book.bids
            elif side_raw == "SELL":
                side = book.asks
            else:
                raise PayloadError(f"unsupported price_change side: {side_raw}")
            price = parse_decimal(change.get("price"), "price")
            size = parse_decimal(change.get("size"), "size")
            assert price is not None and size is not None
            if price < 0 or price > 1 or size < 0:
                raise PayloadError("price_change price/size outside valid range")
            if size == 0:
                side.pop(price, None)
            else:
                side[price] = size
            book.source_timestamp = source_ts
            book.observed_at = observed
            book.book_hash = optional_text(change.get("hash")) or book.book_hash
            changed.add(token_id)

        return DeltaResult(frozenset(changed), missing)

    def apply_tick_size_change(self, payload: JsonObject, observed_at: datetime) -> bool:
        event = _unwrap_market_event(payload)
        token_id = require_text(event.get("asset_id") or event.get("tokenId"), "asset_id")
        book = self._books.get(token_id)
        if book is None:
            return False
        new_tick = parse_decimal(
            event.get("new_tick_size") or event.get("newTickSize"), "new_tick_size"
        )
        assert new_tick is not None
        book.tick_size = new_tick
        book.source_timestamp = parse_source_timestamp(event.get("timestamp"))
        book.observed_at = observed_at.astimezone(UTC)
        return True

    def snapshot(self, token_id: str, depth: int) -> BookSnapshot | None:
        if depth <= 0:
            raise ValueError("depth must be positive")
        book = self._books.get(token_id)
        if book is None:
            return None
        bids = tuple(
            BookLevel(price=price, size=size)
            for price, size in sorted(book.bids.items(), reverse=True)[:depth]
        )
        asks = tuple(
            BookLevel(price=price, size=size)
            for price, size in sorted(book.asks.items())[:depth]
        )
        return BookSnapshot(
            market_id=book.market_id,
            token_id=book.token_id,
            source_timestamp=book.source_timestamp,
            observed_at=book.observed_at,
            bids=bids,
            asks=asks,
            book_hash=book.book_hash,
            min_order_size=book.min_order_size,
            tick_size=book.tick_size,
            neg_risk=book.neg_risk,
            last_trade_price=book.last_trade_price,
        )

    def snapshots(self, depth: int) -> tuple[BookSnapshot, ...]:
        result: list[BookSnapshot] = []
        for token_id in sorted(self._books):
            snapshot = self.snapshot(token_id, depth)
            if snapshot is not None:
                result.append(snapshot)
        return tuple(result)


def _levels(raw: object, *, side: str) -> dict[Decimal, Decimal]:
    if raw is None:
        return {}
    if not isinstance(raw, list):
        raise PayloadError(f"{side} must be a list")
    result: dict[Decimal, Decimal] = {}
    for item in raw:
        level = BookLevel.from_payload(item)
        if level.size > 0:
            result[level.price] = level.size
    return result


def _unwrap_market_event(payload: JsonObject) -> JsonObject:
    """Accept raw CLOB frames and the current SDK-style market envelope."""
    if payload.get("topic") == "market" and isinstance(payload.get("payload"), dict):
        inner = dict(payload["payload"])
        event_type = payload.get("type")
        if isinstance(event_type, str):
            inner.setdefault("event_type", event_type)
        return inner
    return payload


def event_type(payload: JsonObject) -> str | None:
    raw = payload.get("event_type")
    if isinstance(raw, str):
        return raw
    if payload.get("topic") == "market" and isinstance(payload.get("type"), str):
        return str(payload["type"])
    return None
