"""Canonical observable-time replay event and state contracts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import StrEnum


class ReplaySource(StrEnum):
    SIG = "sig"
    POLYMARKET = "polymarket"


class ReplayEventType(StrEnum):
    BOOK_OBSERVATION = "book_observation"
    BOOK_CHANGE = "book_change"
    DEPTH_SNAPSHOT = "depth_snapshot"
    TRADE = "trade"
    TRUST = "trust"
    HEALTH = "health"


class InvalidReason(StrEnum):
    NO_EXECUTABLE_START = "no_executable_start"
    NO_VALID_FUTURE_QUOTE = "no_valid_future_quote"
    SIG_UNTRUSTED = "sig_untrusted"
    SIG_STALE = "sig_stale"
    EXTERNAL_STALE = "external_stale"
    DATA_GAP = "data_gap"
    MISSING_PAIR = "missing_pair"
    DATASET_END = "dataset_end"


@dataclass(frozen=True, slots=True)
class BookLevel:
    price: Decimal
    quantity: Decimal


@dataclass(frozen=True, slots=True)
class QuotePayload:
    best_bid: Decimal | None
    best_ask: Decimal | None
    quote_observed_at: datetime
    bids: tuple[BookLevel, ...] = ()
    asks: tuple[BookLevel, ...] = ()
    last_trade: Decimal | None = None
    book_valid: bool = True

    def __post_init__(self) -> None:
        _require_aware(self.quote_observed_at, "quote_observed_at")
        object.__setattr__(self, "quote_observed_at", self.quote_observed_at.astimezone(UTC))


@dataclass(frozen=True, slots=True)
class TradePayload:
    price: Decimal
    quantity: Decimal | None
    side: str | None = None


@dataclass(frozen=True, slots=True)
class TrustPayload:
    trusted: bool
    transition: str


@dataclass(frozen=True, slots=True)
class HealthPayload:
    available: bool
    detail: str | None = None


ReplayPayload = QuotePayload | TradePayload | TrustPayload | HealthPayload


@dataclass(frozen=True, slots=True)
class ReplayEvent:
    """One event ordered only by the time it became observable locally."""

    observed_at: datetime
    source_at: datetime | None
    source: ReplaySource
    event_type: ReplayEventType
    instrument_id: str
    market_id: str | None
    sequence: int
    payload: ReplayPayload

    def __post_init__(self) -> None:
        _require_aware(self.observed_at, "observed_at")
        object.__setattr__(self, "observed_at", self.observed_at.astimezone(UTC))
        if self.source_at is not None:
            _require_aware(self.source_at, "source_at")
            object.__setattr__(self, "source_at", self.source_at.astimezone(UTC))
        if (
            isinstance(self.payload, QuotePayload)
            and self.payload.quote_observed_at > self.observed_at
        ):
            raise ValueError("quote_observed_at cannot be later than observed_at")
        if not self.instrument_id:
            raise ValueError("instrument_id must not be blank")
        if self.sequence < 0:
            raise ValueError("sequence must be non-negative")

    @property
    def sort_key(self) -> tuple[datetime, str, str, str, int]:
        """Stable tie break after observable time; source timestamps never participate."""
        return (
            self.observed_at.astimezone(UTC),
            self.source.value,
            self.instrument_id,
            self.event_type.value,
            self.sequence,
        )


@dataclass(frozen=True, slots=True)
class InstrumentView:
    source: ReplaySource
    instrument_id: str
    market_id: str | None
    best_bid: Decimal | None
    best_ask: Decimal | None
    bids: tuple[BookLevel, ...]
    asks: tuple[BookLevel, ...]
    last_trade: Decimal | None
    quote_observed_at: datetime | None
    last_event_observed_at: datetime | None
    trusted: bool
    book_valid: bool
    source_available: bool

    @property
    def midpoint(self) -> Decimal | None:
        if self.best_bid is None or self.best_ask is None:
            return None
        return (self.best_bid + self.best_ask) / Decimal(2)


@dataclass(slots=True)
class _InstrumentState:
    source: ReplaySource
    instrument_id: str
    market_id: str | None = None
    best_bid: Decimal | None = None
    best_ask: Decimal | None = None
    bids: tuple[BookLevel, ...] = ()
    asks: tuple[BookLevel, ...] = ()
    last_trade: Decimal | None = None
    quote_observed_at: datetime | None = None
    last_event_observed_at: datetime | None = None
    trusted: bool = True
    book_valid: bool = True


class ReplayState:
    """Mutable state advanced only by events whose observed_at has been reached."""

    def __init__(self) -> None:
        self._instruments: dict[tuple[ReplaySource, str], _InstrumentState] = {}
        self._source_available: dict[ReplaySource, bool] = {
            ReplaySource.SIG: True,
            ReplaySource.POLYMARKET: True,
        }
        self._default_sig_trusted = False

    def apply(self, event: ReplayEvent) -> None:
        payload = event.payload
        if isinstance(payload, HealthPayload):
            self._source_available[event.source] = payload.available
            return
        if isinstance(payload, TrustPayload) and event.instrument_id == "*":
            if event.source is ReplaySource.SIG:
                self._default_sig_trusted = payload.trusted
            for (source, _), state in self._instruments.items():
                if source is event.source:
                    state.trusted = payload.trusted
                    state.last_event_observed_at = event.observed_at
            return

        state = self._state_for(event.source, event.instrument_id)
        state.market_id = event.market_id or state.market_id
        state.last_event_observed_at = event.observed_at

        if isinstance(payload, QuotePayload):
            state.best_bid = payload.best_bid
            state.best_ask = payload.best_ask
            state.bids = payload.bids
            state.asks = payload.asks
            if payload.last_trade is not None:
                state.last_trade = payload.last_trade
            state.quote_observed_at = payload.quote_observed_at
            state.book_valid = payload.book_valid
        elif isinstance(payload, TradePayload):
            state.last_trade = payload.price
        elif isinstance(payload, TrustPayload):
            state.trusted = payload.trusted

    def view(self, source: ReplaySource, instrument_id: str) -> InstrumentView | None:
        state = self._instruments.get((source, instrument_id))
        if state is None:
            return None
        return InstrumentView(
            source=source,
            instrument_id=instrument_id,
            market_id=state.market_id,
            best_bid=state.best_bid,
            best_ask=state.best_ask,
            bids=state.bids,
            asks=state.asks,
            last_trade=state.last_trade,
            quote_observed_at=state.quote_observed_at,
            last_event_observed_at=state.last_event_observed_at,
            trusted=state.trusted,
            book_valid=state.book_valid,
            source_available=self._source_available[source],
        )

    def quote_status(
        self,
        *,
        source: ReplaySource,
        instrument_id: str,
        at: datetime,
        max_age: timedelta | None,
    ) -> tuple[InstrumentView | None, InvalidReason | None]:
        _require_aware(at, "at")
        view = self.view(source, instrument_id)
        if view is None or view.quote_observed_at is None:
            return view, InvalidReason.NO_EXECUTABLE_START
        if source is ReplaySource.SIG and not view.trusted:
            return view, InvalidReason.SIG_UNTRUSTED
        if not view.source_available or not view.book_valid:
            return view, InvalidReason.DATA_GAP
        if max_age is not None and at - view.quote_observed_at > max_age:
            reason = (
                InvalidReason.SIG_STALE
                if source is ReplaySource.SIG
                else InvalidReason.EXTERNAL_STALE
            )
            return view, reason
        if view.best_bid is None or view.best_ask is None:
            return view, InvalidReason.NO_EXECUTABLE_START
        return view, None

    def _state_for(self, source: ReplaySource, instrument_id: str) -> _InstrumentState:
        key = (source, instrument_id)
        state = self._instruments.get(key)
        if state is None:
            state = _InstrumentState(
                source=source,
                instrument_id=instrument_id,
                trusted=self._default_sig_trusted if source is ReplaySource.SIG else True,
            )
            self._instruments[key] = state
        return state


def _require_aware(value: datetime, field: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")
