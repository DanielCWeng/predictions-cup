"""Executable crossing economics for forward markouts."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from predictions_cup.replay.model import InstrumentView


class Direction(StrEnum):
    BUY_YES = "BUY_YES"
    SELL_YES = "SELL_YES"


@dataclass(frozen=True, slots=True)
class Markout:
    entry_price: Decimal
    future_price: Decimal
    gross: Decimal
    midpoint: Decimal | None


def executable_entry(quote: InstrumentView, direction: Direction) -> Decimal | None:
    if direction is Direction.BUY_YES:
        return quote.best_ask
    return quote.best_bid


def evaluate_markout(
    *,
    entry_quote: InstrumentView,
    future_quote: InstrumentView,
    direction: Direction,
) -> Markout | None:
    entry = executable_entry(entry_quote, direction)
    if entry is None:
        return None

    entry_mid = entry_quote.midpoint
    future_mid = future_quote.midpoint
    if direction is Direction.BUY_YES:
        future = future_quote.best_bid
        if future is None:
            return None
        midpoint = None if entry_mid is None or future_mid is None else future_mid - entry_mid
        return Markout(
            entry_price=entry,
            future_price=future,
            gross=future - entry,
            midpoint=midpoint,
        )

    future = future_quote.best_ask
    if future is None:
        return None
    midpoint = None if entry_mid is None or future_mid is None else entry_mid - future_mid
    return Markout(
        entry_price=entry,
        future_price=future,
        gross=entry - future,
        midpoint=midpoint,
    )
