"""Strict observed-time 005I feature producer from accepted PM replay quote+depth.

The source experiment's minute-state formulas (ret_5m, top-of-book
depth-normalised OFI and frozen numeric model inputs) are carried forward.
Sparse or non-contiguous minutes are rejected, never filled by future quotes.
"""
from __future__ import annotations

import statistics
from collections import defaultdict, deque
from collections.abc import Iterable
from datetime import datetime, timedelta
from decimal import Decimal

from predictions_cup.replay.economic_005i import MinuteFeature
from predictions_cup.replay.model import InstrumentView, ReplayEvent, ReplaySource, QuotePayload
from predictions_cup.replay.runner import ReplayFrame, ReplayRunner

D = Decimal


def causal_minute_features(
    events: Iterable[ReplayEvent],
    *,
    pm_token_ids: set[str],
    max_last_quote_age_ms: int = 2000,
) -> tuple[MinuteFeature, ...]:
    """Reconstruct completed minute states with no target/forward columns.

    A quote must be observable in its own minute and current within 2 seconds
    of minute end; a 6-minute continuous history is required. Conservative
    stricter coverage than original retrospective 005I worker.
    """
    if max_last_quote_age_ms < 0:
        raise ValueError("negative quote freshness")
    ongoing: dict[str, tuple[datetime, InstrumentView, int]] = {}
    history: dict[str, deque[tuple[datetime, float, float, float, float, float, float]]] = defaultdict(
        lambda: deque(maxlen=6)
    )
    output: list[MinuteFeature] = []

    def finish(token: str, at: datetime) -> None:
        minute, book, count = ongoing.pop(token)
        minute_end = minute + timedelta(minutes=1)
        if book.quote_observed_at is None:
            history[token].clear()
            return
        if not (minute <= book.quote_observed_at < minute_end):
            history[token].clear()
            return
        if minute_end - book.quote_observed_at > timedelta(
            milliseconds=max_last_quote_age_ms
        ):
            history[token].clear()
            return
        if not (book.book_valid and book.source_available
                and book.best_bid is not None and book.best_ask is not None
                and book.bids and book.asks
                and D(0) <= book.best_bid < book.best_ask <= D(1)):
            history[token].clear()
            return
        bids = sorted(book.bids, key=lambda x: x.price, reverse=True)
        asks = sorted(book.asks, key=lambda x: x.price)
        if any(l.quantity <= 0 for l in (*bids[:5], *asks[:5])):
            history[token].clear()
            return
        depth = sum((float(l.quantity) for l in (*bids[:5], *asks[:5])), 0.0)
        bsz, asz = float(bids[0].quantity), float(asks[0].quantity)
        if depth <= 0 or bsz + asz <= 0:
            history[token].clear()
            return
        bid, ask = float(book.best_bid), float(book.best_ask)
        mid = (bid + ask) / 2.0
        h = history[token]
        if h and minute - h[-1][0] != timedelta(minutes=1):
            h.clear()
        h.append((minute, mid, bid, ask, bsz, asz, depth))
        if len(h) < 6:
            return
        prev = h[-2]
        returns = [h[j][1] - h[j - 1][1] for j in range(1, 6)]
        rv = statistics.stdev(returns)
        prev_bid, prev_ask, prev_bsz, prev_asz = prev[2:6]
        bid_term = (bsz if bid >= prev_bid else 0.0) - (
            prev_bsz if bid <= prev_bid else 0.0
        )
        ask_term = -(asz if ask <= prev_ask else 0.0) + (
            prev_asz if ask >= prev_ask else 0.0
        )
        market = book.market_id
        if market is None:
            return
        output.append(MinuteFeature(
            market_id=market, pm_yes=token, minute_end=minute_end,
            available_at=max(minute_end, at),
            values={
                "mid": mid, "relative_spread": (ask - bid) / max(mid, 0.001),
                "depth_total5": depth, "quote_events": float(count),
                "rv_5m": rv, "imbalance1": (bsz - asz) / (bsz + asz),
                "ret_1m": mid - prev[1], "ret_5m": mid - h[0][1],
                "ofi_depth_norm": (bid_term + ask_term) / depth,
            },
            provenance="005I_V3_CAUSAL_COMPLETED_MINUTE",
        ))

    def before_frame(at: datetime, _state: object) -> None:
        for token in tuple(ongoing):
            minute, _, _ = ongoing[token]
            if minute + timedelta(minutes=1) <= at:
                finish(token, at)

    def on_frame(frame: ReplayFrame) -> None:
        for event in frame.events:
            if event.source is not ReplaySource.POLYMARKET:
                continue
            if event.instrument_id not in pm_token_ids:
                continue
            if not isinstance(event.payload, QuotePayload):
                continue
            minute = event.observed_at.replace(second=0, microsecond=0)
            book = frame.state.view(ReplaySource.POLYMARKET, event.instrument_id)
            if book is None:
                continue
            prior = ongoing.get(event.instrument_id)
            if prior is not None and prior[0] == minute:
                ongoing[event.instrument_id] = (minute, book, prior[2] + 1)
            else:
                ongoing[event.instrument_id] = (minute, book, 1)

    ReplayRunner(events).run(on_frame, before_frame=before_frame)
    # Intentionally never finalise an uncompleted terminal minute.
    return tuple(output)
