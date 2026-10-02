"""Frozen residual-taker signal used by RESIDUAL-TAKER-001.

The research implementation scans minute states in timestamp order, gates on
PM spread and the smaller of PM best bid/ask sizes, then cools down by market
and direction. This module preserves those rules; execution is deliberately
kept in the caller so orders still pass through RISK-002 and BUILD-009.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from predictions_cup.maker.contracts import MakerMarketSnapshot
from predictions_cup.mapping.models import (
    MappingClass,
    MappingDirection,
    MappingDocument,
    MappingStatus,
    MarketMapping,
)
from predictions_cup.runtime.models import OrderAction, OutcomeSide

STRATEGY_ID = "residual-taker-001"
THRESHOLD = 0.02
PM_SPREAD_CAP = 0.02
MIN_PM_DEPTH = 50.0
COOLDOWN_NS = 60_000_000_000
SIG_TICK_PROBABILITY = 0.005


def resolve_residual_universe(
    mapping: MappingDocument,
    configured: str,
    fallback: str,
) -> frozenset[str]:
    exact_ids = frozenset(
        record.sig_exchange_id
        for record in mapping.records
        if record.mapping_class is MappingClass.EXACT
        and record.mapping_direction is MappingDirection.SAME
        and record.status is MappingStatus.VERIFIED
        and record.direct_polymarket is not None
    )
    requested = configured.strip() or fallback.strip()
    if requested == "ALL_EXACT":
        return exact_ids
    ids = frozenset(value.strip() for value in requested.split(",") if value.strip())
    if not ids or not ids.issubset(exact_ids):
        raise ValueError("residual-taker universe must contain verified EXACT exchange IDs")
    return ids


@dataclass(frozen=True, slots=True)
class ResidualInput:
    exchange_id: str
    sig_bid: float
    sig_ask: float
    pm_mid: float
    pm_spread: float
    pm_bid_size: float | None
    pm_ask_size: float | None
    observed_monotonic_ns: int
    sig_touch_depth: float | None = None


@dataclass(frozen=True, slots=True)
class ResidualSignal:
    exchange_id: str
    direction: str
    outcome_side: OutcomeSide
    action: OrderAction
    entry_price: float
    quantity: int
    residual: float
    pm_mid: float
    pm_spread: float
    observed_monotonic_ns: int


class ResidualTakerSignal:
    """Stateful exact market/direction cooldown around the frozen signal."""

    def __init__(
        self,
        *,
        size: int = 50,
        tracked_exchange_ids: frozenset[str] = frozenset(),
    ) -> None:
        if size <= 0:
            raise ValueError("residual-taker size must be positive")
        self.size = size
        self.tracked_exchange_ids = tracked_exchange_ids
        self._last_signal: dict[tuple[str, str], int] = {}

    def on_state(self, state: ResidualInput) -> ResidualSignal | None:
        if self.tracked_exchange_ids and state.exchange_id not in self.tracked_exchange_ids:
            return None
        if not state.exchange_id or state.observed_monotonic_ns < 0:
            return None
        values = (state.sig_bid, state.sig_ask, state.pm_mid, state.pm_spread)
        if any(not math.isfinite(value) for value in values):
            return None
        if (
            state.pm_bid_size is None
            or state.pm_ask_size is None
            or not math.isfinite(state.pm_bid_size)
            or not math.isfinite(state.pm_ask_size)
        ):
            return None
        if not (0.0 <= state.sig_bid <= state.sig_ask <= 1.0):
            return None
        if not (0.0 <= state.pm_mid <= 1.0) or state.pm_spread < 0.0:
            return None
        pm_mid = state.pm_mid
        pm_spread = state.pm_spread
        direction: str | None = None
        entry: float
        residual: float
        if state.sig_ask <= pm_mid - THRESHOLD:
            direction, entry, residual = "BUY", state.sig_ask, pm_mid - state.sig_ask
            side, action = OutcomeSide.YES, OrderAction.BUY
        elif state.sig_bid >= pm_mid + THRESHOLD:
            direction, entry, residual = "SELL", state.sig_bid, state.sig_bid - pm_mid
            # SIG accepts flat YES sells but canonicalizes them to a NO buy.
            side, action = OutcomeSide.NO, OrderAction.BUY
        else:
            return None
        depth = min(state.pm_bid_size, state.pm_ask_size)
        if pm_spread > PM_SPREAD_CAP or depth < MIN_PM_DEPTH:
            return None
        quantity = (
            self.size
            if state.sig_touch_depth is None
            else min(self.size, max(0, int(state.sig_touch_depth)))
        )
        if quantity <= 0:
            return None
        key = (state.exchange_id, direction)
        last = self._last_signal.get(key)
        if last is not None and state.observed_monotonic_ns - last < COOLDOWN_NS:
            return None
        self._last_signal[key] = state.observed_monotonic_ns
        return ResidualSignal(
            exchange_id=state.exchange_id,
            direction=direction,
            outcome_side=side,
            action=action,
            entry_price=entry,
            quantity=quantity,
            residual=residual,
            pm_mid=pm_mid,
            pm_spread=pm_spread,
            observed_monotonic_ns=state.observed_monotonic_ns,
        )


def residual_input_from_snapshot(
    maker: MakerMarketSnapshot,
    record: MarketMapping | None,
    *,
    max_pm_book_age_ns: int,
    observed_monotonic_ns: int,
    allow_bbo_proxy: bool = False,
    max_sig_bbo_age_ns: int | None = None,
) -> ResidualInput | None:
    """Build the frozen signal input, or None when any input is untrusted/stale."""

    sig_bbo_age_ns = maker.now_monotonic_ns - maker.sig_bbo_observed_ns
    sig_bbo_fresh = maker.sig_bbo_trusted and 0 <= sig_bbo_age_ns <= (
        max_pm_book_age_ns if max_sig_bbo_age_ns is None else max_sig_bbo_age_ns
    )
    if (
        record is None
        or record.mapping_class is not MappingClass.EXACT
        or record.mapping_direction is not MappingDirection.SAME
        or record.status is not MappingStatus.VERIFIED
        or record.sig_market_id != maker.market_id
        or record.direct_polymarket is None
    ):
        return None
    quote = maker.external_quotes.get(record.direct_polymarket.mapped_token_id)
    book = maker.runtime.book(maker.exchange_id)
    if (
        quote is None
        or not quote.trusted
        or quote.best_bid is None
        or quote.best_ask is None
        or quote.best_bid_size is None
        or quote.best_ask_size is None
        or not 0 <= maker.now_monotonic_ns - quote.observed_monotonic_ns <= max_pm_book_age_ns
        or (not sig_bbo_fresh and not allow_bbo_proxy)
        or book is None
        or book.market_id != maker.market_id
        or book.tournament_id != maker.tournament_id
        or book.observed_monotonic_ns != maker.sig_bbo_observed_ns
        or not book.bids
        or not book.asks
        or not maker.runtime.portfolio.account_trusted
    ):
        return None
    pm_mid = (quote.best_bid + quote.best_ask) / 2.0
    sig_ask = book.asks[0].price_ticks * SIG_TICK_PROBABILITY
    return ResidualInput(
        exchange_id=maker.exchange_id,
        sig_bid=book.bids[0].price_ticks * SIG_TICK_PROBABILITY,
        sig_ask=sig_ask,
        pm_mid=pm_mid,
        pm_spread=quote.best_ask - quote.best_bid,
        pm_bid_size=quote.best_bid_size,
        pm_ask_size=quote.best_ask_size,
        observed_monotonic_ns=observed_monotonic_ns,
        sig_touch_depth=(
            book.asks[0].quantity if sig_ask <= pm_mid - THRESHOLD else book.bids[0].quantity
        ),
    )
