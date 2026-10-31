"""Incremental, bounded liquidity-weighted portfolio rotation proposals.

One market update is O(depth), and expensive cross-market ranking runs only
on heartbeat/request. No I/O, orders, cash reservations or risk bypass.
"""
from __future__ import annotations

import math
from collections import Counter
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
    advisory_requires_clear: bool = False


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
        advisory_include_resting_orders: bool = False,
        shadow_evaluate_during_capital_block: bool = False,
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
        self.advisory_include_resting_orders = advisory_include_resting_orders
        # ONLY the separately allocated advisory screen may keep non-actionable
        # economic evidence while central capital is temporarily not ready.
        self.shadow_evaluate_during_capital_block = shadow_evaluate_during_capital_block
        self.shadow_capital_blocked = False
        self.blocked_by_orders: set[str] = set()
        self.entries: dict[str, RankedEntry] = {}
        self.exits: dict[str, RankedExit] = {}
        self._positions_fingerprint: tuple[object, ...] | None = None
        self._open_orders_fingerprint: dict[str, tuple[tuple[object, ...], ...]] = {}
        self.observed = 0
        # Bounded in-memory evidence of why candidates vanish. No historical
        # storage, network I/O or trading permission is introduced here.
        self.diagnostic_counts: Counter[str] = Counter()
        self.latest_eligibility: dict[str, tuple[int, str]] = {}
        self.last_rank_diagnostic: dict[str, float | int | str | None] = {}

    def _diagnose(self, eid: str, observed_ns: int, reason: str) -> None:
        self.diagnostic_counts[reason] += 1
        self.latest_eligibility[eid] = (observed_ns, reason)

    def eligibility_report(self, now_ns: int) -> dict[str, object]:
        """Recent per-market eligibility, not tradeability or execution authority."""
        current = Counter(
            reason for observed_ns, reason in self.latest_eligibility.values()
            if 0 <= now_ns - observed_ns <= 60_000_000_000
        )
        return {
            "recent_markets": sum(current.values()),
            "recent_reasons": dict(current.most_common(12)),
            "total_observations": self.observed,
            "total_reasons": dict(self.diagnostic_counts.most_common(12)),
            "rank": dict(self.last_rank_diagnostic),
            "shadow_capital_blocked": self.shadow_capital_blocked,
            "shadow_only": self.shadow_evaluate_during_capital_block,
        }

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
        self.blocked_by_orders.discard(eid)
        self.observed += 1
        if mapped_token is None:
            self._diagnose(eid, snapshot.now_monotonic_ns, "mapping_not_exact_same")
            return
        portfolio = snapshot.runtime.portfolio
        # RISK-002 must separately certify complete issuer reconciliation.
        # Never retain any apparently actionable proposals through a halt.
        if not risk_state_trusted:
            if not self.shadow_evaluate_during_capital_block:
                self.entries.clear()
                self.exits.clear()
                self.blocked_by_orders.clear()
                self._diagnose(eid, snapshot.now_monotonic_ns, "capital_risk_not_ready")
                return
            if not self.shadow_capital_blocked:
                self.entries.clear()
                self.exits.clear()
                self.blocked_by_orders.clear()
                self._positions_fingerprint = None
                self._open_orders_fingerprint.clear()
            self.shadow_capital_blocked = True
            self._diagnose(eid, snapshot.now_monotonic_ns, "capital_risk_not_ready")
            # May collect local quote economics below for diagnostics, but
            # NEVER submit an order or clear maker quotes from this state.
        elif self.shadow_capital_blocked:
            # Never reuse even young shadow-only candidates after recovery:
            # wait for fresh, independent market observations of both legs.
            self.entries.clear()
            self.exits.clear()
            self.blocked_by_orders.clear()
            self._positions_fingerprint = None
            self._open_orders_fingerprint.clear()
            self.shadow_capital_blocked = False
        # Explicitly disallow the PROXY state and ambiguous outstanding fills.
        if not portfolio.account_trusted or portfolio.trust_grade is not AccountTrustGrade.TRUSTED:
            self.entries.clear()
            self.exits.clear()
            self.blocked_by_orders.clear()
            self._diagnose(eid, snapshot.now_monotonic_ns, "account_not_trusted")
            return
        if portfolio.account_proxy_uncertainty != 0 or any(o.uncertain for o in portfolio.orders):
            self.entries.clear()
            self.exits.clear()
            self.blocked_by_orders.clear()
            self._diagnose(eid, snapshot.now_monotonic_ns, "uncertain_orders_or_cash")
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
        if any(
            o.open and o.exchange_id == eid
            and o.tournament_id == snapshot.tournament_id
            for o in portfolio.orders
        ):
            if not self.advisory_include_resting_orders:
                self._diagnose(eid, snapshot.now_monotonic_ns, "resting_order_on_market")
                return
            # Hypothetical economics only: the regular allocator still
            # requires a clean book and a new trusted risk check to execute.
            self.blocked_by_orders.add(eid)
        if not snapshot.sig_depth_trusted:
            self._diagnose(eid, snapshot.now_monotonic_ns, "sig_depth_untrusted")
            return
        book = snapshot.runtime.book(eid)
        if book is None or not book.trusted_depth:
            self._diagnose(eid, snapshot.now_monotonic_ns, "sig_book_missing_or_untrusted")
            return
        depth_ns = snapshot.sig_depth_observed_ns
        if depth_ns is None:
            self._diagnose(eid, snapshot.now_monotonic_ns, "sig_depth_timestamp_missing")
            return
        age = snapshot.now_monotonic_ns - depth_ns
        # The 15s extended window only applies to independently guarded WS depth.
        allowed_age = (
            max(self.max_exit_book_age_ns, 15_000_000_000)
            if snapshot.sig_stream_depth_corroborated else self.max_exit_book_age_ns
        )
        if age < 0 or age > allowed_age or not snapshot.sig_bbo_trusted:
            reason = "sig_depth_stale" if age < 0 or age > allowed_age else "sig_bbo_untrusted"
            self._diagnose(eid, snapshot.now_monotonic_ns, reason)
            return
        quote = snapshot.external_quotes.get(mapped_token)
        if quote is None or not quote.trusted:
            self._diagnose(eid, snapshot.now_monotonic_ns, "pm_quote_missing_or_untrusted")
            return
        ref_age = snapshot.now_monotonic_ns - quote.observed_monotonic_ns
        if ref_age < 0 or ref_age > self.max_age_ns:
            self._diagnose(eid, snapshot.now_monotonic_ns, "pm_quote_stale")
            return
        if quote.best_bid is None or quote.best_ask is None:
            self._diagnose(eid, snapshot.now_monotonic_ns, "pm_two_sided_book_missing")
            return
        if quote.best_bid > quote.best_ask:
            self._diagnose(eid, snapshot.now_monotonic_ns, "pm_book_crossed")
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
            self._diagnose(eid, snapshot.now_monotonic_ns, "sig_depth_invalid")
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
        # A valid quote can provide an exit, an entry or neither. Distinguish
        # genuinely weak economics from missing or untrusted source data.
        if eid in self.entries and eid in self.exits:
            reason = "eligible_entry_and_exit"
        elif eid in self.entries:
            reason = "eligible_entry_only"
        elif eid in self.exits:
            reason = "eligible_exit_only"
        elif not bids and not asks:
            reason = "sig_executable_depth_empty"
        elif quote.best_bid_size is None and quote.best_ask_size is None:
            reason = "pm_depth_size_missing"
        elif quote.best_bid_size is not None and quote.best_bid_size < 1 and (
            quote.best_ask_size is None or quote.best_ask_size < 1
        ):
            reason = "pm_depth_size_below_one"
        else:
            reason = "no_marginal_entry_edge_or_reducing_exit"
        self._diagnose(eid, now, reason)
        if self.observed % 4096 == 0:
            # Bound memory independently of heartbeat frequency.
            self._expire(now)

    def stale_depth_entry_probe(
        self, snapshot: MakerMarketSnapshot, mapped_token: str | None, *,
        max_sig_bbo_age_ns: int,
    ) -> float | None:
        """Conservative BBO-only score for a *depth refresh*, never a trade.

        Called only after the real allocator rejected this market for stale
        or untrusted SIG depth. Uses fresh independently trusted SIG BBO and
        PM reference quotes, the unchanged marginal net-edge floor, and
        directional capacity. The fetched depth and risk are rechecked by the
        normal execution path; this score confers no trading permission.
        """
        if mapped_token is None or max_sig_bbo_age_ns <= 0:
            return None
        now = snapshot.now_monotonic_ns
        portfolio = snapshot.runtime.portfolio
        if (
            not portfolio.account_trusted
            or portfolio.trust_grade is not AccountTrustGrade.TRUSTED
            or portfolio.account_proxy_uncertainty != 0
            or any(order.uncertain for order in portfolio.orders)
            or any(order.open and order.exchange_id == snapshot.exchange_id
                   for order in portfolio.orders)
            or not snapshot.sig_bbo_trusted
        ):
            return None
        bbo_age = now - snapshot.sig_bbo_observed_ns
        if bbo_age < 0 or bbo_age >= max_sig_bbo_age_ns:
            return None
        book = snapshot.runtime.book(snapshot.exchange_id)
        if book is None or not book.asks or not book.bids:
            return None
        quote = snapshot.external_quotes.get(mapped_token)
        if quote is None or not quote.trusted:
            return None
        quote_age = now - quote.observed_monotonic_ns
        if not 0 <= quote_age <= self.max_age_ns:
            return None
        if (
            quote.best_bid is None or quote.best_ask is None
            or not math.isfinite(quote.best_bid)
            or not math.isfinite(quote.best_ask)
            or quote.best_bid > quote.best_ask
        ):
            return None
        bid = book.bids[0].price_ticks * SIG_TICK
        ask = book.asks[0].price_ticks * SIG_TICK
        if not 0 <= bid <= ask <= 1:
            return None
        low, high = portfolio.worst_case_inventory_bounds(
            snapshot.exchange_id, snapshot.tournament_id,
        )
        scores: list[float] = []
        for direction, fair, touch, reference_size in (
            ("BUY", quote.best_bid, ask, quote.best_bid_size),
            ("SELL", quote.best_ask, bid, quote.best_ask_size),
        ):
            if reference_size is None or not math.isfinite(reference_size):
                continue
            cap = min(self.max_trade_shares, math.floor(reference_size))
            if self.max_position is not None:
                room = (
                    self.max_position - high if direction == "BUY"
                    else self.max_position + low
                )
                cap = min(cap, math.floor(room))
            if cap < 1:
                continue
            edge = (
                (fair - touch) if direction == "BUY" else (touch - fair)
            ) - self.entry_cushion - self.min_marginal_net_edge
            if edge > 0:
                scores.append(edge * cap)
        return max(scores) if scores else None

    def _expire(self, now_ns: int) -> None:
        for eid, item in tuple(self.entries.items()):
            if now_ns < item.observed_ns or now_ns - item.observed_ns > self.max_age_ns:
                del self.entries[eid]
        for eid, exit_state in tuple(self.exits.items()):
            if (
                now_ns < exit_state.observed_ns
                or now_ns - exit_state.observed_ns > self.max_age_ns
            ):
                del self.exits[eid]

    def passive_recycle_targets(
        self, *, now_ns: int, limit: int = 8,
    ) -> frozenset[str]:
        """Select held YES or NO inventory for normal reduce-only maker quotes.

        A BUY YES against an existing NO position redeems the paired holdings:
        the one-SUSQie redemption covers the YES purchase and releases the
        remaining (1 - YES price) cash. We nominate the NO market only;
        maker cash checks and central risk must independently prove that the
        proposed order cannot overshoot the signed NO inventory.
        """
        if limit < 1 or limit > 8:
            raise ValueError("passive recycling must be bounded to 1..8")
        self._expire(now_ns)
        candidates: list[tuple[float, int, str]] = []
        for eid, held in self.exits.items():
            is_yes = held.signed_shares > 0
            levels = held.yes_bids if is_yes else held.yes_asks
            if not levels:
                continue
            best = levels[0]
            if best.shares < 1:
                continue
            executable = min(abs(held.signed_shares), best.shares, self.max_trade_shares)
            # Sale proceeds in the *held outcome* currency. For NO, covering
            # at YES ask returns 1 - ask after pair redemption.
            proceeds = best.yes_price if is_yes else 1 - best.yes_price
            fair_hold = (
                held.conservative_fair_yes
                if is_yes else 1 - held.conservative_fair_yes
            )
            concession = max(0.0, fair_hold - proceeds)
            score = executable * max(0.0, proceeds - concession)
            if score > 0:
                candidates.append((score, executable, eid))
        candidates.sort(key=lambda item: (-item[0], -item[1], item[2]))
        return frozenset(eid for _, _, eid in candidates[:limit])

    def rank(self, *, now_ns: int | None = None, limit: int = 3) -> tuple[RankedRotation, ...]:
        if limit < 1:
            return ()
        current = monotonic_ns() if now_ns is None else now_ns
        self._expire(current)
        if not self.entries or not self.exits:
            self.last_rank_diagnostic = {
                "entries": len(self.entries), "exits": len(self.exits),
                "economically_priced_pairs": 0, "best_gain": None,
                "gain_hurdle": self.min_rotation_gain,
            }
            return ()
        # Keep this ranking strictly OUT of the per-market hot path.
        # Compare at most 12 entries with 256 fresh exits on the slower rank
        # cadence. The verified live mapping currently has <=140 contracts;
        # limiting exits to 24 by the FIRST 100 shares can hide an otherwise
        # profitable 500-share capital switch behind 24 shallow books.
        # Beyond 256 exits this remains a bounded heuristic shortlist.
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

        exits = sorted(self.exits.values(), key=exit_depth_cost)[:256]
        # Size at real depth breakpoints, not arbitrary round lots only.
        # Most election books have sparse liquidity: the best executable slice
        # is often 17 or 83 shares rather than 50/100/250/500. Bound the work.
        sizes = (1, 10, 25, 50, 100, 250, 500)

        def breakpoints(level_sizes: tuple[int, ...], capacity: int) -> tuple[int, ...]:
            total = 0
            points: list[int] = []
            for amount in level_sizes[:8]:
                total += amount
                if 0 < total <= capacity:
                    points.append(total)
                if total >= capacity:
                    break
            return tuple(points)

        entry_breakpoints = {
            item.exchange_id: breakpoints(
                tuple(t.shares for t in item.tiers), self.max_trade_shares
            ) for item in entries
        }
        exit_breakpoints = {
            item.exchange_id: breakpoints(
                tuple(level.shares for level in (
                    item.yes_bids if item.signed_shares > 0 else item.yes_asks
                )), self.max_trade_shares
            ) for item in exits
        }
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
        best_gain = -math.inf
        priced_pairs = 0
        for entry in entries:
            for old in exits:
                # Market ID identifies the SIG contract, not a proof of
                # independent or mutually exclusive election outcomes.
                if old.exchange_id == entry.exchange_id or old.market_id == entry.market_id:
                    continue
                max_qty = min(entry.capacity, abs(old.signed_shares), self.max_trade_shares)
                quantities = {
                    max_qty,
                    *(q for q in sizes if q <= max_qty),
                    *(q for q in entry_breakpoints[entry.exchange_id] if q <= max_qty),
                    *(q for q in exit_breakpoints[old.exchange_id] if q <= max_qty),
                }
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
                    priced_pairs += 1
                    best_gain = max(best_gain, gain)
                    if gain >= self.min_rotation_gain:
                        ranked.append((gain, gain / qty, entry_ev, old, entry, qty))
        self.last_rank_diagnostic = {
            "entries": len(self.entries), "exits": len(self.exits),
            "economically_priced_pairs": priced_pairs,
            "best_gain": round(best_gain, 4) if math.isfinite(best_gain) else None,
            "gain_hurdle": self.min_rotation_gain,
        }
        ranked.sort(key=lambda row: (
            -row[0], -row[1], row[3].exchange_id, row[4].exchange_id
        ))
        unique: list[RankedRotation] = []
        seen_exits: set[str] = set()
        seen_entries: set[str] = set()
        for _gain, _density, entry_ev, old, entry, qty in ranked:
            # Diversify the bounded live shortlist; otherwise three slightly
            # different replacements for the SAME holding crowd out other
            # capital-release opportunities.
            if old.exchange_id in seen_exits or entry.exchange_id in seen_entries:
                continue
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
            seen_exits.add(old.exchange_id)
            seen_entries.add(entry.exchange_id)
            unique.append(RankedRotation(
                assessment.gain_after_buffer,
                assessment.gain_after_buffer / qty,
                entry_ev, assessment, entry.direction,
                advisory_requires_clear=(
                    self.advisory_include_resting_orders
                    and (
                        old.exchange_id in self.blocked_by_orders
                        or entry.exchange_id in self.blocked_by_orders
                    )
                ),
            ))
            if len(unique) >= limit:
                break
        return tuple(unique)
