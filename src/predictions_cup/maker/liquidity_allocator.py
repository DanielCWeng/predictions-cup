"""Incremental, bounded liquidity-weighted portfolio rotation proposals.

One market update is O(depth), and expensive cross-market ranking runs only
on heartbeat/request. No I/O, orders, cash reservations or risk bypass.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from time import monotonic_ns

from predictions_cup.maker.capital_rotation import (
    BookLevel,
    HeldPosition,
    Replacement,
    RotationAssessment,
    RotationConditions,
    assess_rotation,
)
from predictions_cup.maker.contracts import MakerMarketSnapshot
from predictions_cup.maker.taker_economics import SIG_TICK
from predictions_cup.runtime.models import AccountTrustGrade, RuntimeLevel


@dataclass(frozen=True, slots=True)
class EntryTier:
    shares: int
    net_edge_per_share: float

    def __post_init__(self) -> None:
        if not isinstance(self.shares, int) or self.shares < 1:
            raise ValueError("entry tier requires whole shares")
        if not math.isfinite(self.net_edge_per_share):
            raise ValueError("nonfinite marginal edge")


@dataclass(frozen=True, slots=True)
class RankedEntry:
    exchange_id: str
    market_id: str
    direction: str
    observed_ns: int
    tiers: tuple[EntryTier, ...]

    @property
    def capacity(self) -> int:
        return sum(t.shares for t in self.tiers)

    def net_ev(self, quantity: int) -> float | None:
        if quantity <= 0:
            return None
        left, edge = quantity, 0.0
        for tier in self.tiers:
            filled = min(left, tier.shares)
            edge += filled * tier.net_edge_per_share
            left -= filled
            if left == 0:
                return edge
        return None


@dataclass(frozen=True, slots=True)
class RankedExit:
    exchange_id: str
    market_id: str
    signed_shares: int
    conservative_fair_yes: float
    observed_ns: int
    yes_bids: tuple[BookLevel, ...]
    yes_asks: tuple[BookLevel, ...]


@dataclass(frozen=True, slots=True)
class RankedRotation:
    gain_susq: float
    gain_per_share: float
    entry_ev_susq: float
    assessment: RotationAssessment
    entry_direction: str


class LiquidityWeightedAllocator:
    """Cache recent mapped books and emit *proposals*, never execution permits."""

    def __init__(
        self,
        *,
        max_trade_shares: int = 500,
        max_position: int | None = None,
        max_age_ns: int = 5_000_000_000,
        max_exit_book_age_ns: int = 1_500_000_000,
        fee_per_share: float = 0.0,
        latency_haircut: float = 0.005,
        adverse_haircut: float = 0.005,
        min_marginal_net_edge: float = 0.005,
        exit_fee_per_share: float = 0.005,
        rotation_buffer_per_share: float = 0.01,
        reprice_slippage_per_leg: float = 0.0025,
        min_rotation_gain: float = 5.0,
    ) -> None:
        if (
            max_trade_shares < 1 or max_age_ns <= 0 or max_exit_book_age_ns <= 0
            or (max_position is not None and max_position < 1)
        ):
            raise ValueError("invalid bounded allocator capacity or TTL")
        costs = (fee_per_share, latency_haircut, adverse_haircut,
                 min_marginal_net_edge, exit_fee_per_share,
                 rotation_buffer_per_share, reprice_slippage_per_leg, min_rotation_gain)
        if any(not math.isfinite(v) or v < 0 for v in costs):
            raise ValueError("invalid allocation costs")
        self.max_trade_shares = max_trade_shares
        self.max_position = max_position
        self.max_age_ns = max_age_ns
        self.max_exit_book_age_ns = max_exit_book_age_ns
        self.entry_cushion = fee_per_share + latency_haircut + adverse_haircut
        self.min_marginal_net_edge = min_marginal_net_edge
        self.exit_fee = exit_fee_per_share
        # Both legs may move between observed books and confirmed exit fills.
        # Model extra 0.25c per leg, over and above baseline taker costs.
        self.rotation_buffer = rotation_buffer_per_share + 2 * reprice_slippage_per_leg
        self.min_rotation_gain = min_rotation_gain
        self.entries: dict[str, RankedEntry] = {}
        self.exits: dict[str, RankedExit] = {}
        self._positions_fingerprint: tuple[object, ...] | None = None
        self._open_orders_fingerprint: dict[str, tuple[tuple[object, ...], ...]] = {}
        self.observed = 0

    def observe(
        self,
        snapshot: MakerMarketSnapshot,
        mapped_token: str | None,
        *,
        risk_state_trusted: bool = True,
    ) -> None:
        """Fresh snapshot only; a new event invalidates old cached proposals."""
        eid = snapshot.exchange_id
        self.entries.pop(eid, None)
        self.exits.pop(eid, None)
        self.observed += 1
        if mapped_token is None:
            return
        portfolio = snapshot.runtime.portfolio
        # RISK-002 must separately certify complete issuer reconciliation.
        # Never retain any apparently actionable proposals through a halt.
        if not risk_state_trusted:
            self.entries.clear()
            self.exits.clear()
            return
        # Explicitly disallow the PROXY state and ambiguous outstanding fills.
        if not portfolio.account_trusted or portfolio.trust_grade is not AccountTrustGrade.TRUSTED:
            self.entries.clear()
            self.exits.clear()
            return
        if portfolio.account_proxy_uncertainty != 0 or any(o.uncertain for o in portfolio.orders):
            self.entries.clear()
            self.exits.clear()
            return
        # Position changes alter global gross headroom; resting quote churn
        # affects only the relevant exchange. Do not erase every independent
        # candidate because a maker quote elsewhere was cancelled.
        holdings: tuple[object, ...] = tuple(sorted(
            (p.exchange_id, p.market_id, p.tournament_id,
             p.signed_quantity, p.gross_exposure) for p in portfolio.positions
        ))
        if holdings != self._positions_fingerprint:
            self.entries.clear()
            self.exits.clear()
            self._positions_fingerprint = holdings
        open_orders: dict[str, list[tuple[object, ...]]] = {}
        for order in portfolio.orders:
            if order.open or order.uncertain:
                open_orders.setdefault(order.exchange_id, []).append(
                    (order.market_id, order.tournament_id, order.logical_intent_id,
                     order.reserved_exposure, order.signed_quantity, order.open,
                     order.uncertain)
                )
        order_state = {
            exchange_id: tuple(sorted(items))
            for exchange_id, items in open_orders.items()
        }
        for changed in self._open_orders_fingerprint.keys() | order_state.keys():
            if self._open_orders_fingerprint.get(changed) != order_state.get(changed):
                self.entries.pop(changed, None)
                self.exits.pop(changed, None)
        self._open_orders_fingerprint = order_state
        if any(o.open and o.market_id == snapshot.market_id and
               o.tournament_id == snapshot.tournament_id for o in portfolio.orders):
            return
        if not snapshot.sig_depth_trusted:
            return
        book = snapshot.runtime.book(eid)
        if book is None or not book.trusted_depth:
            return
        depth_ns = snapshot.sig_depth_observed_ns
        if depth_ns is None:
            return
        age = snapshot.now_monotonic_ns - depth_ns
        # The 15s extended window only applies to independently guarded WS depth.
        allowed_age = (
            max(self.max_exit_book_age_ns, 15_000_000_000)
            if snapshot.sig_stream_depth_corroborated else self.max_exit_book_age_ns
        )
        if age < 0 or age > allowed_age or not snapshot.sig_bbo_trusted:
            return
        quote = snapshot.external_quotes.get(mapped_token)
        if quote is None or not quote.trusted:
            return
        ref_age = snapshot.now_monotonic_ns - quote.observed_monotonic_ns
        if ref_age < 0 or ref_age > self.max_age_ns:
            return
        if quote.best_bid is None or quote.best_ask is None:
            return
        if quote.best_bid > quote.best_ask:
            return
        try:
            # Only the executable prefix for our largest 500-share tranche is
            # needed; never walk/retain an unbounded LIVE order book.
            def prefix(raw: tuple[RuntimeLevel, ...]) -> tuple[BookLevel, ...]:
                result: list[BookLevel] = []
                remaining = self.max_trade_shares
                for level in raw:
                    if remaining <= 0 or len(result) >= 64:
                        break
                    if level.quantity < 1:
                        continue
                    n = min(remaining, int(math.floor(level.quantity)))
                    result.append(BookLevel(level.price_ticks * SIG_TICK, n))
                    remaining -= n
                return tuple(result)

            bids = prefix(book.bids)
            asks = prefix(book.asks)
        except (ValueError, OverflowError):
            return
        held = portfolio.signed_inventory(eid, snapshot.tournament_id)
        low, high = portfolio.worst_case_inventory_bounds(eid, snapshot.tournament_id)
        # Exiting sacrifices the incumbent's future value. For a conservative
        # switch decision, VALUE THE HOLD at the favorable PM side: YES ask,
        # or NO equivalent of PM bid. Otherwise exit costs are understated.
        now = snapshot.now_monotonic_ns
        if math.isfinite(held) and held == math.trunc(held) and held != 0 and abs(held) == sum(
            abs(p.gross_exposure) for p in portfolio.positions
            if p.exchange_id == eid and p.tournament_id == snapshot.tournament_id
        ):
            if held > 0 and low > 0 and bids:
                self.exits[eid] = RankedExit(
                    eid, snapshot.market_id, int(held),
                    quote.best_ask, now, bids, asks,
                )
            elif held < 0 and high < 0 and asks:
                self.exits[eid] = RankedExit(
                    eid, snapshot.market_id, int(held),
                    quote.best_bid, now, bids, asks,
                )
        # A SELL YES that would flip NO inventory still consumes new capacity.
        # These are proposals, so never assume cap headroom before RISK-002.
        alternatives: list[RankedEntry] = []
        for direction, levels, fair, pm_size in (
            ("BUY", asks, quote.best_bid, quote.best_bid_size),
            ("SELL", bids, quote.best_ask, quote.best_ask_size),
        ):
            if pm_size is None or not math.isfinite(pm_size) or pm_size < 1:
                continue
            cap = min(self.max_trade_shares, int(pm_size))
            # Match the existing taker's directional room, including signed
            # outstanding reservations. This is still NOT a gross-risk permit.
            if self.max_position is not None:
                room = (
                    self.max_position - high if direction == "BUY"
                    else self.max_position + low
                )
                cap = max(0, min(cap, math.floor(room)))
            if cap < 1:
                continue
            tiers: list[EntryTier] = []
            remaining = cap
            for level in levels:
                marginal = ((fair - level.yes_price) if direction == "BUY"
                            else (level.yes_price - fair)) - self.entry_cushion
                if marginal < self.min_marginal_net_edge:
                    break
                filled = min(remaining, level.shares)
                if filled:
                    tiers.append(EntryTier(filled, marginal))
                    remaining -= filled
                if not remaining:
                    break
            if tiers:
                alternatives.append(RankedEntry(eid, snapshot.market_id,
                                                direction, now, tuple(tiers)))
        if alternatives:
            self.entries[eid] = max(
                alternatives, key=lambda item: (item.net_ev(item.capacity) or 0, item.capacity)
            )
        if self.observed % 4096 == 0:
            # Bound memory independently of heartbeat frequency.
            self._expire(now)

    def _expire(self, now_ns: int) -> None:
        for eid, item in tuple(self.entries.items()):
            if now_ns < item.observed_ns or now_ns - item.observed_ns > self.max_age_ns:
                del self.entries[eid]
        for eid, item in tuple(self.exits.items()):
            if now_ns < item.observed_ns or now_ns - item.observed_ns > self.max_age_ns:
                del self.exits[eid]

    def rank(self, *, now_ns: int | None = None, limit: int = 3) -> tuple[RankedRotation, ...]:
        if limit < 1:
            return ()
        current = monotonic_ns() if now_ns is None else now_ns
        self._expire(current)
        if not self.entries or not self.exits:
            return ()
        # Keep this ranking strictly OUT of the hot-path full cross-product.
        # Per-market observations already hold full depth in memory; screen
        # all IDs cheaply, then compare at most 12 entries with 24 exits.
        # This is a bounded shortlist, not a proof of global optimality.
        entries = sorted(
            self.entries.values(),
            key=lambda item: item.net_ev(item.capacity) or 0.0,
            reverse=True,
        )[:12]
        def exit_depth_cost(item: RankedExit) -> float:
            """Sort by actual depth-weighted concession, not a 1-share touch."""
            is_yes = item.signed_shares > 0
            need = min(100, abs(item.signed_shares), self.max_trade_shares)
            levels = item.yes_bids if is_yes else item.yes_asks
            remaining, proceeds = need, 0.0
            last = float("inf") if is_yes else float("-inf")
            for level in levels:
                if (is_yes and level.yes_price > last) or (
                    not is_yes and level.yes_price < last
                ):
                    return math.inf
                last = level.yes_price
                size = min(remaining, level.shares)
                proceeds += size * (level.yes_price if is_yes else 1 - level.yes_price)
                remaining -= size
                if not remaining:
                    break
            if remaining:
                return math.inf
            hold_fair = item.conservative_fair_yes if is_yes else 1 - item.conservative_fair_yes
            return hold_fair - proceeds / need

        exits = sorted(self.exits.values(), key=exit_depth_cost)[:24]
        sizes = (50, 100, 250, 500)
        # Compute an exit proceeds curve only ONCE per (holding, size).
        # The inner cross-market loop uses plain arithmetic; only the final
        # winners are rebuilt with the full fail-closed rotation validator.
        exit_curve: dict[tuple[str, int], float | None] = {}

        def executable_proceeds(old: RankedExit, qty: int) -> float | None:
            key = (old.exchange_id, qty)
            if key in exit_curve:
                return exit_curve[key]
            is_yes = old.signed_shares > 0
            levels = old.yes_bids if is_yes else old.yes_asks
            remaining, proceeds = qty, 0.0
            last = float("inf") if is_yes else float("-inf")
            for level in levels:
                if (is_yes and level.yes_price > last) or (
                    not is_yes and level.yes_price < last
                ):
                    exit_curve[key] = None
                    return None
                last = level.yes_price
                n = min(remaining, level.shares)
                proceeds += n * (
                    level.yes_price if is_yes else 1.0 - level.yes_price
                )
                remaining -= n
                if not remaining:
                    break
            exit_curve[key] = None if remaining else proceeds
            return exit_curve[key]

        ranked: list[tuple[float, float, float, RankedExit, RankedEntry, int]] = []
        for entry in entries:
            for old in exits:
                # Market ID identifies the SIG contract, not a proof of
                # independent or mutually exclusive election outcomes.
                if old.exchange_id == entry.exchange_id or old.market_id == entry.market_id:
                    continue
                max_qty = min(entry.capacity, abs(old.signed_shares), self.max_trade_shares)
                quantities = set([max_qty] + [q for q in sizes if q <= max_qty])
                for qty in quantities:
                    entry_ev = entry.net_ev(qty)
                    if entry_ev is None or entry_ev <= 0:
                        continue
                    proceeds = executable_proceeds(old, qty)
                    if proceeds is None:
                        continue
                    fair_per_share = (
                        old.conservative_fair_yes if old.signed_shares > 0
                        else 1.0 - old.conservative_fair_yes
                    )
                    concession = qty * fair_per_share - proceeds
                    gain = entry_ev - concession - qty * (
                        self.exit_fee + self.rotation_buffer
                    )
                    if gain >= self.min_rotation_gain:
                        ranked.append((gain, gain / qty, entry_ev, old, entry, qty))
        ranked.sort(key=lambda row: (
            -row[0], -row[1], row[3].exchange_id, row[4].exchange_id
        ))
        unique: list[RankedRotation] = []
        seen: set[tuple[str, str]] = set()
        for _gain, _density, entry_ev, old, entry, qty in ranked:
            pair = (old.exchange_id, entry.exchange_id)
            if pair in seen:
                continue
            seen.add(pair)
            # Do not trust preliminary arithmetic as execution authority.
            # The original independent signed-inventory/depth validator must
            # accept the exact same slice before publishing a candidate.
            assessment = assess_rotation(
                HeldPosition(old.exchange_id, old.signed_shares,
                             old.conservative_fair_yes),
                Replacement(entry.exchange_id, qty, entry_ev / qty),
                yes_bids=old.yes_bids,
                yes_asks=old.yes_asks,
                conditions=RotationConditions(
                    account_trusted=True, reconciliation_complete=True,
                    depth_trusted=True, book_age_ms=0.0,
                    no_conflicting_orders=True,
                    max_depth_age_ms=1500,
                    max_tranche_shares=qty,
                    exit_cost_per_share=self.exit_fee,
                    extra_buffer_per_share=self.rotation_buffer,
                    min_total_gain=self.min_rotation_gain,
                ),
            )
            if assessment.status != "CANDIDATE_ONLY":
                continue
            unique.append(RankedRotation(
                assessment.gain_after_buffer,
                assessment.gain_after_buffer / qty,
                entry_ev, assessment, entry.direction,
            ))
            if len(unique) >= limit:
                break
        return tuple(unique)
