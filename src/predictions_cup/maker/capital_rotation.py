"""Pure, read-only capital-rotation assessment (no order placement).

All prices on SIG's YES axis. A negative signed lot owns NO; closing
that lot is BUY YES, valued as (1 - YES ask) per NO share.
The replacement edge must already be net of its own execution costs.
"""
from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class BookLevel:
    yes_price: float
    shares: int

    def __post_init__(self) -> None:
        if not math.isfinite(self.yes_price) or not 0 < self.yes_price < 1:
            raise ValueError("YES level price must be a valid probability")
        if not isinstance(self.shares, int) or isinstance(self.shares, bool) or self.shares < 1:
            raise ValueError("level shares must be positive integers")


@dataclass(frozen=True, slots=True)
class HeldPosition:
    exchange_id: str
    signed_yes_shares: int
    # To avoid overstating rotation EV, pass an UPPER bound for owned YES
    # (PM ask) and a LOWER YES bound for owned NO (PM bid => upper NO).
    conservative_hold_fair_yes: float

    def __post_init__(self) -> None:
        if not self.exchange_id.strip() or not isinstance(self.signed_yes_shares, int):
            raise ValueError("exact exchange ID and signed integer quantity required")
        if isinstance(self.signed_yes_shares, bool) or not self.signed_yes_shares:
            raise ValueError("held position cannot be flat")
        if not math.isfinite(self.conservative_hold_fair_yes) or not (
            0 <= self.conservative_hold_fair_yes <= 1
        ):
            raise ValueError("hold fair must be a finite probability")


@dataclass(frozen=True, slots=True)
class Replacement:
    exchange_id: str
    capacity_shares: int
    conservative_net_edge_per_share: float

    def __post_init__(self) -> None:
        if (
            not self.exchange_id.strip()
            or not isinstance(self.capacity_shares, int)
            or isinstance(self.capacity_shares, bool)
            or self.capacity_shares < 1
        ):
            raise ValueError("valid replacement market and integer capacity required")
        if not math.isfinite(self.conservative_net_edge_per_share):
            raise ValueError("replacement edge must be finite")


@dataclass(frozen=True, slots=True)
class RotationConditions:
    account_trusted: bool
    reconciliation_complete: bool
    depth_trusted: bool
    book_age_ms: float
    no_conflicting_orders: bool
    max_depth_age_ms: float = 1500.0
    max_tranche_shares: int = 500
    exit_cost_per_share: float = 0.005
    extra_buffer_per_share: float = 0.01
    min_total_gain: float = 5.0

    def __post_init__(self) -> None:
        if (
            not isinstance(self.max_tranche_shares, int)
            or isinstance(self.max_tranche_shares, bool)
            or self.max_tranche_shares < 1
            or not math.isfinite(self.max_depth_age_ms)
            or self.max_depth_age_ms <= 0
            or any(
                not math.isfinite(value) or value < 0
                for value in (
                    self.exit_cost_per_share,
                    self.extra_buffer_per_share,
                    self.min_total_gain,
                )
            )
        ):
            raise ValueError("invalid rotation safeguards")


@dataclass(frozen=True, slots=True)
class RotationAssessment:
    status: str
    reason: str
    exit_exchange_id: str
    replacement_exchange_id: str
    shares: int = 0
    exit_proceeds: float = 0.0
    hold_fair_value: float = 0.0
    exit_concession: float = 0.0
    incremental_new_edge: float = 0.0
    gain_after_buffer: float = 0.0


def assess_rotation(
    held: HeldPosition,
    replacement: Replacement,
    *,
    yes_bids: tuple[BookLevel, ...],
    yes_asks: tuple[BookLevel, ...],
    conditions: RotationConditions,
) -> RotationAssessment:
    """Return a proposal, NEVER a live execution permit.

    An EXIT must fill and reconcile before its shares count as capacity.
    Replacement liquidity, cash and central risk are rechecked separately.
    """
    def deny(reason: str) -> RotationAssessment:
        return RotationAssessment(
            "BLOCKED", reason, held.exchange_id, replacement.exchange_id
        )

    if (
        not conditions.account_trusted
        or not conditions.reconciliation_complete
        or not conditions.no_conflicting_orders
    ):
        return deny("account_or_reservation_not_reconciled")
    if (
        not conditions.depth_trusted
        or not math.isfinite(conditions.book_age_ms)
        or not 0 <= conditions.book_age_ms < conditions.max_depth_age_ms
    ):
        return deny("exit_book_untrusted_or_stale")
    if held.exchange_id == replacement.exchange_id:
        return deny("not_a_distinct_rotation")
    if replacement.conservative_net_edge_per_share <= 0:
        return deny("replacement_has_no_positive_edge")

    qty = min(
        abs(held.signed_yes_shares),
        replacement.capacity_shares,
        conditions.max_tranche_shares,
    )
    # SIG stores both YES and NO holdings as signed YES inventory.
    is_yes = held.signed_yes_shares > 0
    levels = yes_bids if is_yes else yes_asks
    remaining, proceeds = qty, 0.0
    last_price = float("inf") if is_yes else float("-inf")
    for level in levels:
        # Do not silently trust a crossed or unsorted ladder.
        if (
            (is_yes and level.yes_price > last_price)
            or (not is_yes and level.yes_price < last_price)
        ):
            return deny("invalid_exit_ladder_order")
        last_price = level.yes_price
        available = min(remaining, level.shares)
        proceeds += available * (
            level.yes_price if is_yes else 1 - level.yes_price
        )
        remaining -= available
        if not remaining:
            break
    if remaining:
        return deny("insufficient_executable_exit_depth")

    fair_outcome = (
        held.conservative_hold_fair_yes
        if is_yes else 1 - held.conservative_hold_fair_yes
    )
    fair = qty * fair_outcome
    concession = fair - proceeds  # Negative => exiting above fair.
    new_edge = qty * replacement.conservative_net_edge_per_share
    net_gain = new_edge - concession - qty * (
        conditions.exit_cost_per_share + conditions.extra_buffer_per_share
    )
    if net_gain < conditions.min_total_gain:
        return deny("rotation_not_worth_exit_cost")
    return RotationAssessment(
        status="CANDIDATE_ONLY",
        reason="must_exit_and_reconcile_before_replacement",
        exit_exchange_id=held.exchange_id,
        replacement_exchange_id=replacement.exchange_id,
        shares=qty,
        exit_proceeds=round(proceeds, 6),
        hold_fair_value=round(fair, 6),
        exit_concession=round(concession, 6),
        incremental_new_edge=round(new_edge, 6),
        gain_after_buffer=round(net_gain, 6),
    )
