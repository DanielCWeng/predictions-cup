"""Pure election-update -> stale-SIG-quote *research* scorer.

No network, account access, order submission, or strategy registration. Inputs
must come from independently validated feed/mapping/account adapters. Numerical
output is an indicative conservative bound, NOT demonstrated executable alpha.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

SIG_PRICE_TICK = 0.005


class ElectionEvidenceKind(StrEnum):
    OFFICIAL_RESULT = "OFFICIAL_RESULT"
    VERIFIED_MEDIA_CALL = "VERIFIED_MEDIA_CALL"


class LagDirection(StrEnum):
    BUY_YES = "BUY_YES"
    SELL_HELD_YES = "SELL_HELD_YES"


@dataclass(frozen=True, slots=True)
class ElectionProbabilityUpdate:
    event_id: str
    source_id: str
    exchange_id: str
    market_id: str
    published_at: datetime
    received_at: datetime
    received_monotonic_ns: int
    probability_lower: float
    probability_upper: float
    kind: ElectionEvidenceKind
    source_verified: bool
    exact_mapping_verified: bool
    probability_interval_calibrated: bool

    def __post_init__(self) -> None:
        if not all(
            value.strip() for value in
            (self.event_id, self.source_id, self.exchange_id, self.market_id)
        ):
            raise ValueError("election evidence identifiers must be nonempty")
        if any(
            timestamp.tzinfo is None or timestamp.utcoffset() is None
            for timestamp in (self.published_at, self.received_at)
        ):
            raise ValueError("election evidence timestamps must be timezone-aware")
        if not isinstance(self.kind, ElectionEvidenceKind):
            raise ValueError("unsupported election evidence kind")
        if self.received_monotonic_ns < 0:
            raise ValueError("negative event receive timestamp")
        if not (
            math.isfinite(self.probability_lower)
            and math.isfinite(self.probability_upper)
            and 0.0 <= self.probability_lower <= self.probability_upper <= 1.0
        ):
            raise ValueError("invalid event probability interval")


@dataclass(frozen=True, slots=True)
class SigExecutableTop:
    exchange_id: str
    market_id: str
    observed_monotonic_ns: int
    best_bid_ticks: int
    best_ask_ticks: int
    bid_shares: float
    ask_shares: float
    depth_trusted: bool
    bbo_trusted: bool
    tradeable: bool

    def __post_init__(self) -> None:
        if not self.exchange_id or not self.market_id:
            raise ValueError("missing SIG market identity")
        if self.observed_monotonic_ns < 0:
            raise ValueError("negative SIG quote timestamp")
        if not (1 <= self.best_bid_ticks <= self.best_ask_ticks <= 199):
            raise ValueError("invalid SIG YES top-of-book ticks")
        if any(
            not math.isfinite(size) or size < 0
            for size in (self.bid_shares, self.ask_shares)
        ):
            raise ValueError("invalid executable SIG visible shares")


@dataclass(frozen=True, slots=True)
class ShadowCapacity:
    cash_available: float
    free_gross_shares: float
    held_yes_shares: float
    account_trusted: bool

    def __post_init__(self) -> None:
        if any(
            not math.isfinite(value) or value < 0
            for value in (
                self.cash_available, self.free_gross_shares, self.held_yes_shares
            )
        ):
            raise ValueError("invalid capacity")


@dataclass(frozen=True, slots=True)
class ElectionLagPolicy:
    max_event_age_ns: int = 5_000_000_000
    max_quote_age_ns: int = 1_000_000_000
    max_publication_delay_seconds: float = 10.0
    cost_per_share: float = 0.02
    min_net_edge_per_share: float = 0.01
    max_shares: int = 100

    def __post_init__(self) -> None:
        if min(self.max_event_age_ns, self.max_quote_age_ns, self.max_shares) <= 0:
            raise ValueError("positive age and size limits required")
        for value in (
            self.max_publication_delay_seconds,
            self.cost_per_share,
            self.min_net_edge_per_share,
        ):
            if not math.isfinite(value) or value < 0:
                raise ValueError("invalid lag policy cost or delay")


@dataclass(frozen=True, slots=True)
class ShadowLagCandidate:
    event_id: str
    exchange_id: str
    market_id: str
    direction: LagDirection
    price_ticks: int
    visible_size_cap: int
    quantity: int
    conservative_edge_per_share: float
    indicative_total_edge: float
    decision_monotonic_ns: int
    source_receive_age_ns: int
    sig_book_age_ns: int


@dataclass(frozen=True, slots=True)
class Abstain:
    reason: str


ShadowLagDecision = ShadowLagCandidate | Abstain


def evaluate_election_lag(
    event: ElectionProbabilityUpdate,
    quote: SigExecutableTop,
    capacity: ShadowCapacity,
    *,
    now_monotonic_ns: int,
    policy: ElectionLagPolicy | None = None,
) -> ShadowLagDecision:
    """Inspect one indexed market in O(1); emit no actual order.

    The interval is supplied by a separately calibrated, independently reviewed
    model. Do not infer certainty from a raw vote lead or from Polymarket midpoints.
    Flat worst-case cost covers fees, slippage, latency and opportunity cost; an
    execution study must replace it with size/fill-conditional estimates.
    """
    if policy is None:
        policy = ElectionLagPolicy()
    if event.exchange_id != quote.exchange_id or event.market_id != quote.market_id:
        return Abstain("identity_mismatch")
    if not (event.source_verified and event.exact_mapping_verified):
        return Abstain("unverified_source_or_mapping")
    if not event.probability_interval_calibrated:
        return Abstain("uncalibrated_interval")
    if not (quote.tradeable and quote.depth_trusted and quote.bbo_trusted):
        return Abstain("sig_market_untrusted")
    if not capacity.account_trusted:
        return Abstain("account_untrusted")
    if (
        now_monotonic_ns < event.received_monotonic_ns
        or now_monotonic_ns < quote.observed_monotonic_ns
    ):
        return Abstain("future_observation")
    event_age = now_monotonic_ns - event.received_monotonic_ns
    quote_age = now_monotonic_ns - quote.observed_monotonic_ns
    if event_age > policy.max_event_age_ns:
        return Abstain("event_stale")
    if quote_age > policy.max_quote_age_ns:
        return Abstain("sig_book_stale")
    delay = (event.received_at - event.published_at).total_seconds()
    if delay < 0 or delay > policy.max_publication_delay_seconds:
        return Abstain("source_publication_clock_or_delay")
    ask = quote.best_ask_ticks * SIG_PRICE_TICK
    bid = quote.best_bid_ticks * SIG_PRICE_TICK
    buy_edge = event.probability_lower - ask - policy.cost_per_share
    sell_edge = bid - event.probability_upper - policy.cost_per_share
    if buy_edge <= policy.min_net_edge_per_share and sell_edge <= policy.min_net_edge_per_share:
        return Abstain("no_conservative_edge")

    # Direction choice is made from *conditional net edge*, not the largest
    # midpoint gap; then capacity and inventory are checked independently.
    directions = (
        (LagDirection.BUY_YES, buy_edge, quote.best_ask_ticks, quote.ask_shares),
        (LagDirection.SELL_HELD_YES, sell_edge, quote.best_bid_ticks, quote.bid_shares),
    )
    for direction, edge, price_ticks, shares in sorted(
        directions, key=lambda item: item[1], reverse=True
    ):
        if edge <= policy.min_net_edge_per_share:
            continue
        if direction is LagDirection.BUY_YES:
            available = min(
                shares, capacity.free_gross_shares, capacity.cash_available / ask
            )
        else:
            available = min(shares, capacity.held_yes_shares)
        quantity = min(policy.max_shares, math.floor(available))
        if quantity < 1:
            continue
        return ShadowLagCandidate(
            event_id=event.event_id,
            exchange_id=event.exchange_id,
            market_id=event.market_id,
            direction=direction,
            price_ticks=price_ticks,
            visible_size_cap=math.floor(shares),
            quantity=quantity,
            conservative_edge_per_share=edge,
            indicative_total_edge=edge * quantity,
            decision_monotonic_ns=now_monotonic_ns,
            source_receive_age_ns=event_age,
            sig_book_age_ns=quote_age,
        )
    return Abstain("insufficient_executable_capacity")
