"""Conservative, pure executable-depth economics for the existing residual taker.

This never submits orders. A decision is only a pre-risk screen; RISK-002 and
BUILD-009 remain the sole admission and execution authorities.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from predictions_cup.runtime.models import RuntimeLevel

Direction = Literal["BUY", "SELL"]
SIG_TICK = 0.005


@dataclass(frozen=True, slots=True)
class TakerEconomics:
    reason: str
    direction: Direction
    quantity: int = 0
    limit_price_ticks: int | None = None
    vwap: float | None = None
    conservative_fair: float | None = None
    net_edge_per_share: float = 0.0
    net_expected_value: float = 0.0
    total_slippage: float = 0.0
    # Passive price is conditional on a fill; queue probability is unknown.
    maker_conditional_edge_per_share: float | None = None

    @property
    def admissible(self) -> bool:
        return self.reason == "take" and self.quantity > 0


def evaluate_taker_economics(
    *,
    direction: Direction,
    levels: Sequence[RuntimeLevel],
    conservative_fair: float,
    desired_quantity: int,
    trusted_depth: bool,
    book_age_ns: int,
    reference_age_ns: int,
    max_book_age_ns: int = 1_500_000_000,
    max_reference_age_ns: int = 5_000_000_000,
    fee_per_share: float = 0.0,
    latency_haircut: float = 0.005,
    adverse_selection_haircut: float = 0.005,
    min_net_edge_per_share: float = 0.005,
    available_cash: float | None = None,
    maker_price_ticks: int | None = None,
) -> TakerEconomics:
    """Evaluate marginal depth, rather than assuming every share fills at touch.

    For BUY YES, the fair bound must be a pessimistic YES value (e.g. verified
    PM bid); for SELL YES, an upper fair bound (e.g. verified PM ask).
    The caller applies risk inventory limits and separately validates mapping.
    Cash, when available, is capped at the *worst* swept limit price.
    """
    if direction not in ("BUY", "SELL"):
        raise ValueError("direction")

    def reject(why: str) -> TakerEconomics:
        return TakerEconomics(reason=why, direction=direction)

    if not trusted_depth:
        return reject("depth_untrusted")
    if (
        min(book_age_ns, reference_age_ns) < 0
        or book_age_ns > max_book_age_ns
        or reference_age_ns > max_reference_age_ns
    ):
        return reject("stale_data")
    params = (
        conservative_fair,
        fee_per_share,
        latency_haircut,
        adverse_selection_haircut,
        min_net_edge_per_share,
    )
    if (
        any(not math.isfinite(v) for v in params)
        or not 0.0 <= conservative_fair <= 1.0
        or any(v < 0.0 for v in params[1:])
    ):
        return reject("invalid_economics")
    if desired_quantity < 1 or not levels:
        return reject("no_quantity_or_depth")
    if available_cash is not None and (not math.isfinite(available_cash) or available_cash < 0.0):
        return reject("cash_untrusted")
    cushion = fee_per_share + latency_haircut + adverse_selection_haircut
    touch = levels[0].price_ticks * SIG_TICK
    previous = None
    qty = 0
    price_sum = 0.0
    best: TakerEconomics | None = None
    for level in levels:
        ticks = level.price_ticks
        if (
            not isinstance(ticks, int)
            or ticks < 0
            or ticks > 200
            or not math.isfinite(level.quantity)
            or level.quantity <= 0.0
            or (
                previous is not None
                and (
                    (direction == "BUY" and ticks < previous)
                    or (direction == "SELL" and ticks > previous)
                )
            )
        ):
            return reject("invalid_book")
        previous = ticks
        price = ticks * SIG_TICK
        marginal = (
            conservative_fair - price if direction == "BUY" else price - conservative_fair
        ) - cushion
        if marginal < min_net_edge_per_share - 1e-12:
            break
        take = min(max(0, int(math.floor(level.quantity))), desired_quantity - qty)
        if direction == "BUY" and available_cash is not None and price > 0:
            # Entire sweep must fit the *limit*, not an optimistic VWAP.
            take = min(take, max(0, int(available_cash // price) - qty))
        if take <= 0:
            break
        qty += take
        price_sum += take * price
        vwap = price_sum / qty
        net_per = (
            conservative_fair - vwap if direction == "BUY" else vwap - conservative_fair
        ) - cushion
        slippage = (vwap - touch if direction == "BUY" else touch - vwap) * qty
        maker_edge = None
        if maker_price_ticks is not None and 0 <= maker_price_ticks <= 200:
            passive = maker_price_ticks * SIG_TICK
            maker_edge = (
                conservative_fair - passive if direction == "BUY" else passive - conservative_fair
            ) - fee_per_share
        best = TakerEconomics(
            reason="take",
            direction=direction,
            quantity=qty,
            limit_price_ticks=ticks,
            vwap=vwap,
            conservative_fair=conservative_fair,
            net_edge_per_share=net_per,
            net_expected_value=qty * net_per,
            total_slippage=max(0.0, slippage),
            maker_conditional_edge_per_share=maker_edge,
        )
        if qty >= desired_quantity:
            break
    return best if best is not None else reject("edge_or_depth_insufficient")
