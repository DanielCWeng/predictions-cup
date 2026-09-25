"""Transport-facing Polymarket data types for EXPERIMENT-001A."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

JsonObject = dict[str, Any]


class PayloadError(ValueError):
    """Raised when a remote payload cannot be normalized safely."""


def utc_now() -> datetime:
    return datetime.now(UTC)


def require_text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PayloadError(f"{field} must be a non-blank string")
    return value.strip()


def optional_text(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        return str(value)
    value = value.strip()
    return value or None


def parse_decimal(value: object, field: str, *, optional: bool = False) -> Decimal | None:
    if value is None and optional:
        return None
    if isinstance(value, float):
        raise PayloadError(f"{field} must not be a binary float")
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise PayloadError(f"{field} is not a valid decimal") from exc
    if not parsed.is_finite():
        raise PayloadError(f"{field} must be finite")
    return parsed


def parse_source_timestamp(value: object) -> datetime | None:
    """Parse exchange wire timestamps while retaining observed_at separately."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise PayloadError("source timestamp must be timezone-aware")
        return value.astimezone(UTC)
    if isinstance(value, bool):
        raise PayloadError("boolean is not a valid timestamp")
    if isinstance(value, (int, Decimal)) or (isinstance(value, str) and value.strip().isdigit()):
        raw = Decimal(str(value).strip())
        seconds = raw / Decimal(1000) if raw >= Decimal("100000000000") else raw
        return datetime.fromtimestamp(float(seconds), tz=UTC)
    if isinstance(value, str):
        text = value.strip().replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError as exc:
            raise PayloadError("source timestamp is not ISO-8601 or epoch time") from exc
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise PayloadError("source timestamp must be timezone-aware")
        return parsed.astimezone(UTC)
    raise PayloadError("unsupported source timestamp type")


def _string_tuple(value: object, field: str) -> tuple[str, ...]:
    if value is None:
        return ()
    parsed: object = value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as exc:
            raise PayloadError(f"{field} must contain a JSON string array") from exc
    if not isinstance(parsed, list):
        raise PayloadError(f"{field} must be a list")
    result: list[str] = []
    for item in parsed:
        result.append(require_text(item, field))
    return tuple(result)


def _bool(value: object, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1"}:
            return True
        if lowered in {"false", "0"}:
            return False
    if isinstance(value, int) and value in {0, 1}:
        return bool(value)
    raise PayloadError("invalid boolean field")


@dataclass(frozen=True, slots=True)
class PolymarketToken:
    market_id: str
    condition_id: str
    token_id: str
    outcome: str
    outcome_index: int


@dataclass(frozen=True, slots=True)
class PolymarketMarket:
    market_id: str
    condition_id: str
    question: str
    slug: str | None
    outcomes: tuple[str, ...]
    token_ids: tuple[str, ...]
    active: bool
    closed: bool
    accepting_orders: bool | None
    start_at: datetime | None
    end_at: datetime | None
    resolution_source: str | None
    event_id: str | None
    event_slug: str | None
    event_title: str | None
    neg_risk: bool
    neg_risk_market_id: str | None
    market_group: str | None
    group_item_title: str | None
    group_item_threshold: Decimal | None
    parent_event_slug: str | None
    min_tick_size: Decimal | None
    min_order_size: Decimal | None
    liquidity: Decimal | None
    volume: Decimal | None

    @classmethod
    def from_gamma(cls, payload: JsonObject) -> PolymarketMarket:
        events = payload.get("events")
        event: JsonObject = {}
        if isinstance(events, list) and events and isinstance(events[0], dict):
            event = events[0]

        outcomes = _string_tuple(payload.get("outcomes"), "outcomes")
        token_ids = _string_tuple(payload.get("clobTokenIds"), "clobTokenIds")
        if not outcomes or not token_ids or len(outcomes) != len(token_ids):
            raise PayloadError("Gamma outcomes and clobTokenIds must be non-empty aligned arrays")

        start_raw = payload.get("startDate") or payload.get("startDateIso")
        end_raw = payload.get("endDate") or payload.get("endDateIso")
        market_id = require_text(payload.get("id"), "id")
        condition_id = require_text(payload.get("conditionId"), "conditionId")

        accepting = payload.get("acceptingOrders")
        accepting_orders = None if accepting is None else _bool(accepting)
        neg_risk_value = payload.get("negRisk")
        if neg_risk_value is None:
            neg_risk_value = event.get("negRisk") or event.get("enableNegRisk")

        return cls(
            market_id=market_id,
            condition_id=condition_id,
            question=require_text(payload.get("question"), "question"),
            slug=optional_text(payload.get("slug")),
            outcomes=outcomes,
            token_ids=token_ids,
            active=_bool(payload.get("active"), default=True),
            closed=_bool(payload.get("closed"), default=False),
            accepting_orders=accepting_orders,
            start_at=parse_source_timestamp(start_raw),
            end_at=parse_source_timestamp(end_raw),
            resolution_source=optional_text(payload.get("resolutionSource")),
            event_id=optional_text(event.get("id")),
            event_slug=optional_text(event.get("slug")),
            event_title=optional_text(event.get("title")),
            neg_risk=_bool(neg_risk_value, default=False),
            neg_risk_market_id=optional_text(
                event.get("negRiskMarketID") or payload.get("negRiskMarketID")
            ),
            market_group=optional_text(payload.get("marketGroup")),
            group_item_title=optional_text(payload.get("groupItemTitle")),
            group_item_threshold=parse_decimal(
                payload.get("groupItemThreshold"), "groupItemThreshold", optional=True
            ),
            parent_event_slug=optional_text(
                payload.get("parentEventSlug") or payload.get("parent_event_slug")
            ),
            min_tick_size=parse_decimal(
                payload.get("orderPriceMinTickSize"), "orderPriceMinTickSize", optional=True
            ),
            min_order_size=parse_decimal(
                payload.get("orderMinSize"), "orderMinSize", optional=True
            ),
            liquidity=parse_decimal(
                payload.get("liquidityNum") or payload.get("liquidity"), "liquidity", optional=True
            ),
            volume=parse_decimal(
                payload.get("volumeNum") or payload.get("volume"), "volume", optional=True
            ),
        )

    def tokens(self) -> tuple[PolymarketToken, ...]:
        return tuple(
            PolymarketToken(
                market_id=self.market_id,
                condition_id=self.condition_id,
                token_id=token_id,
                outcome=self.outcomes[index],
                outcome_index=index,
            )
            for index, token_id in enumerate(self.token_ids)
        )


@dataclass(frozen=True, slots=True)
class BookLevel:
    price: Decimal
    size: Decimal

    @classmethod
    def from_payload(cls, payload: object) -> BookLevel:
        if not isinstance(payload, dict):
            raise PayloadError("book level must be an object")
        price = parse_decimal(payload.get("price"), "price")
        size = parse_decimal(payload.get("size"), "size")
        assert price is not None and size is not None
        if price < 0 or price > 1 or size < 0:
            raise PayloadError("book level price/size outside valid range")
        return cls(price=price, size=size)


@dataclass(frozen=True, slots=True)
class BookSnapshot:
    market_id: str
    token_id: str
    source_timestamp: datetime | None
    observed_at: datetime
    bids: tuple[BookLevel, ...]
    asks: tuple[BookLevel, ...]
    book_hash: str | None = None
    min_order_size: Decimal | None = None
    tick_size: Decimal | None = None
    neg_risk: bool | None = None
    last_trade_price: Decimal | None = None

    @property
    def best_bid(self) -> Decimal | None:
        return self.bids[0].price if self.bids else None

    @property
    def best_ask(self) -> Decimal | None:
        return self.asks[0].price if self.asks else None

    @property
    def midpoint(self) -> Decimal | None:
        if self.best_bid is None or self.best_ask is None:
            return None
        return (self.best_bid + self.best_ask) / Decimal(2)

    @property
    def spread(self) -> Decimal | None:
        if self.best_bid is None or self.best_ask is None:
            return None
        return self.best_ask - self.best_bid


@dataclass(frozen=True, slots=True)
class TradeEvent:
    market_id: str
    token_id: str
    price: Decimal
    size: Decimal | None
    side: str | None
    source_timestamp: datetime | None
    observed_at: datetime
    transaction_hash: str | None
    fee_rate_bps: Decimal | None

    @classmethod
    def from_ws(cls, payload: JsonObject, observed_at: datetime) -> TradeEvent:
        price = parse_decimal(payload.get("price"), "price")
        size = parse_decimal(payload.get("size"), "size", optional=True)
        fee = parse_decimal(
            payload.get("fee_rate_bps") or payload.get("feeRateBps"),
            "fee_rate_bps",
            optional=True,
        )
        assert price is not None
        return cls(
            market_id=require_text(payload.get("market"), "market"),
            token_id=require_text(payload.get("asset_id") or payload.get("tokenId"), "asset_id"),
            price=price,
            size=size,
            side=optional_text(payload.get("side")),
            source_timestamp=parse_source_timestamp(payload.get("timestamp")),
            observed_at=observed_at.astimezone(UTC),
            transaction_hash=optional_text(
                payload.get("transaction_hash") or payload.get("transactionHash")
            ),
            fee_rate_bps=fee,
        )
