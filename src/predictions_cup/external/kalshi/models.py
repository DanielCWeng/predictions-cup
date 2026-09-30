"""Typed read-only Kalshi market-data contracts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any


class KalshiPayloadError(ValueError):
    """Kalshi returned a payload outside the supported public market-data contract."""


def parse_utc(value: object, *, field: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise KalshiPayloadError(f"{field} must be a non-empty timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise KalshiPayloadError(f"{field} is not ISO-8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise KalshiPayloadError(f"{field} must be timezone-aware")
    return parsed.astimezone(UTC)


def optional_utc(value: object, *, field: str) -> datetime | None:
    if value is None:
        return None
    return parse_utc(value, field=field)


def optional_decimal(value: object, *, field: str) -> Decimal | None:
    if value is None or value == "":
        return None
    if isinstance(value, (str, int, Decimal)):
        try:
            return Decimal(str(value))
        except Exception as exc:
            raise KalshiPayloadError(f"{field} is not decimal-compatible") from exc
    raise KalshiPayloadError(f"{field} is not decimal-compatible")


def required_str(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise KalshiPayloadError(f"{key} must be a non-empty string")
    return value


@dataclass(frozen=True, slots=True)
class KalshiMarket:
    ticker: str
    event_ticker: str
    status: str
    title: str | None
    yes_bid: Decimal | None
    yes_ask: Decimal | None
    no_bid: Decimal | None
    no_ask: Decimal | None
    yes_bid_size: Decimal | None
    yes_ask_size: Decimal | None
    last_price: Decimal | None
    volume: Decimal | None
    source_updated_at: datetime | None
    observed_at: datetime
    api_version: str

    @classmethod
    def from_api(
        cls,
        payload: dict[str, Any],
        *,
        observed_at: datetime,
        api_version: str,
    ) -> "KalshiMarket":
        title = payload.get("title")
        if title is not None and not isinstance(title, str):
            raise KalshiPayloadError("title must be a string when present")
        return cls(
            ticker=required_str(payload, "ticker"),
            event_ticker=required_str(payload, "event_ticker"),
            status=required_str(payload, "status"),
            title=title,
            yes_bid=optional_decimal(payload.get("yes_bid_dollars"), field="yes_bid_dollars"),
            yes_ask=optional_decimal(payload.get("yes_ask_dollars"), field="yes_ask_dollars"),
            no_bid=optional_decimal(payload.get("no_bid_dollars"), field="no_bid_dollars"),
            no_ask=optional_decimal(payload.get("no_ask_dollars"), field="no_ask_dollars"),
            yes_bid_size=optional_decimal(
                payload.get("yes_bid_size_fp"), field="yes_bid_size_fp"
            ),
            yes_ask_size=optional_decimal(
                payload.get("yes_ask_size_fp"), field="yes_ask_size_fp"
            ),
            last_price=optional_decimal(
                payload.get("last_price_dollars"), field="last_price_dollars"
            ),
            volume=optional_decimal(payload.get("volume_fp"), field="volume_fp"),
            source_updated_at=optional_utc(
                payload.get("updated_time"), field="updated_time"
            ),
            observed_at=observed_at.astimezone(UTC),
            api_version=api_version,
        )



@dataclass(frozen=True, slots=True)
class KalshiEvent:
    event_ticker: str
    series_ticker: str | None
    title: str | None
    observed_at: datetime
    api_version: str

    @classmethod
    def from_api(
        cls,
        payload: dict[str, Any],
        *,
        observed_at: datetime,
        api_version: str,
    ) -> "KalshiEvent":
        event_ticker = payload.get("event_ticker", payload.get("ticker"))
        if not isinstance(event_ticker, str) or not event_ticker.strip():
            raise KalshiPayloadError("event response requires event_ticker/ticker")
        series_ticker = payload.get("series_ticker")
        title = payload.get("title")
        if series_ticker is not None and not isinstance(series_ticker, str):
            raise KalshiPayloadError("series_ticker must be a string when present")
        if title is not None and not isinstance(title, str):
            raise KalshiPayloadError("event title must be a string when present")
        return cls(
            event_ticker=event_ticker,
            series_ticker=series_ticker,
            title=title,
            observed_at=observed_at.astimezone(UTC),
            api_version=api_version,
        )


@dataclass(frozen=True, slots=True)
class KalshiBookLevel:
    price: Decimal
    quantity: Decimal


@dataclass(frozen=True, slots=True)
class KalshiOrderBook:
    ticker: str
    yes_bids: tuple[KalshiBookLevel, ...]
    no_bids: tuple[KalshiBookLevel, ...]
    observed_at: datetime
    api_version: str

    @staticmethod
    def _levels(value: object, *, field: str) -> tuple[KalshiBookLevel, ...]:
        if not isinstance(value, list):
            raise KalshiPayloadError(f"{field} must be an array")
        levels: list[KalshiBookLevel] = []
        for row in value:
            if not isinstance(row, list) or len(row) != 2:
                raise KalshiPayloadError(f"{field} rows must be [price, quantity]")
            price = optional_decimal(row[0], field=f"{field}.price")
            quantity = optional_decimal(row[1], field=f"{field}.quantity")
            if price is None or quantity is None:
                raise KalshiPayloadError(f"{field} level cannot contain null")
            levels.append(KalshiBookLevel(price=price, quantity=quantity))
        return tuple(levels)


@dataclass(frozen=True, slots=True)
class KalshiTrade:
    trade_id: str
    ticker: str
    quantity: Decimal
    yes_price: Decimal
    no_price: Decimal
    created_at: datetime
    observed_at: datetime
    api_version: str

    @classmethod
    def from_api(
        cls,
        payload: dict[str, Any],
        *,
        observed_at: datetime,
        api_version: str,
    ) -> "KalshiTrade":
        quantity = optional_decimal(payload.get("count_fp"), field="count_fp")
        yes_price = optional_decimal(
            payload.get("yes_price_dollars"), field="yes_price_dollars"
        )
        no_price = optional_decimal(
            payload.get("no_price_dollars"), field="no_price_dollars"
        )
        if quantity is None or yes_price is None or no_price is None:
            raise KalshiPayloadError("trade quantity/prices must be present")
        return cls(
            trade_id=required_str(payload, "trade_id"),
            ticker=required_str(payload, "ticker"),
            quantity=quantity,
            yes_price=yes_price,
            no_price=no_price,
            created_at=parse_utc(payload.get("created_time"), field="created_time"),
            observed_at=observed_at.astimezone(UTC),
            api_version=api_version,
        )


@dataclass(frozen=True, slots=True)
class KalshiPage:
    items: tuple[KalshiMarket, ...]
    cursor: str | None


@dataclass(frozen=True, slots=True)
class KalshiTradePage:
    items: tuple[KalshiTrade, ...]
    cursor: str | None


@dataclass(frozen=True, slots=True)
class KalshiHealth:
    state: str
    reason_codes: tuple[str, ...]
    checked_at: datetime
    last_success_at: datetime | None
    last_source_timestamp: datetime | None
    consecutive_failures: int
    api_version: str
    base_url: str
