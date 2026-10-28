"""LIVE execution shell for RESIDUAL-TAKER-001.

The frozen signal crosses the SIG touch with a single limit order through
RISK-002 admission and BUILD-009 dispatch, then cancels any unfilled remainder
straight away: the strategy takes liquidity and never rests a quote.
"""

from __future__ import annotations

import asyncio
import logging
import math
from collections import Counter
from collections.abc import Awaitable, Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import cast

from predictions_cup.execution.journal import ExecutionJournal
from predictions_cup.execution.models import (
    ExecutionEnvelope,
    ExecutionEvent,
    ExecutionMode,
    LifecycleState,
    OperationKind,
)
from predictions_cup.execution.planner import build_execution_plan
from predictions_cup.execution.reservations import ExecutionReservationBook
from predictions_cup.execution.sinks import ExecutionPlan
from predictions_cup.maker.capital_rotation import (
    HeldPosition,
    Replacement,
    RotationConditions,
    assess_rotation,
)
from predictions_cup.maker.contracts import MakerMarketSnapshot, QuoteSide
from predictions_cup.maker.coordinator import MakerStateChange
from predictions_cup.maker.lifecycle import QuoteRegistry
from predictions_cup.maker.liquidity_allocator import LiquidityWeightedAllocator, RankedRotation
from predictions_cup.maker.residual_taker import (
    COOLDOWN_NS,
    MIN_PM_DEPTH,
    PM_SPREAD_CAP,
    SIG_TICK_PROBABILITY,
    STRATEGY_ID,
    THRESHOLD,
    ResidualSignal,
    ResidualTakerSignal,
    residual_input_from_snapshot,
)
from predictions_cup.maker.rotation_recovery import RotationEvidence, RotationEvidenceCache
from predictions_cup.maker.safety import MakerKillSwitch
from predictions_cup.maker.taker_economics import Direction, evaluate_taker_economics
from predictions_cup.mapping.models import (
    MappingClass,
    MappingDirection,
    MappingDocument,
    MappingStatus,
    MarketMapping,
)
from predictions_cup.risk.core import RiskContext, evaluate_risk
from predictions_cup.runtime.models import (
    AccountTrustGrade,
    OrderAction,
    OutcomeSide,
    RuntimePortfolio,
)
from predictions_cup.sig.errors import SigApiError, SigExecutionUncertainError
from predictions_cup.strategy.core import CandidateLeg, Opportunity, StrategyFamily

_LOG = logging.getLogger(__name__)
_LIVENESS_INTERVAL_SECONDS = 60.0

PlanDispatcher = Callable[[ExecutionPlan], Awaitable[ExecutionEvent]]
CancelDispatcher = Callable[[ExecutionEnvelope], Awaitable[ExecutionEvent]]
RiskContextSource = RiskContext | Callable[[], RiskContext]

_RESTING_STATES = {
    LifecycleState.ACKED,
    LifecycleState.OPEN,
    LifecycleState.PARTIALLY_FILLED,
}


@dataclass(frozen=True, slots=True)
class _PendingRotationEntry:
    """One exit can finance at most one independently priced replacement."""

    exit_operation_id: str
    exit_exchange_id: str
    entry_exchange_id: str
    entry_direction: str
    tournament_id: str
    before_signed: int
    exit_shares: int
    exit_concession_per_share: float
    exit_account_observed_ns: int
    created_ns: int
    entry_before_signed: int = 0


def rotation_mapped_token(record: MarketMapping | None) -> str | None:
    """Only price-aligned direct contracts enter rotation EV or exit targets.

    The allocator consumes Polymarket YES bid/ask as SIG YES fair bounds.
    Complement and NEAR mappings require different or uncertain economics;
    never let those proposals influence inventory-reducing maker quotes.
    """
    if (
        record is None
        or record.status is not MappingStatus.VERIFIED
        or record.mapping_class is not MappingClass.EXACT
        or record.mapping_direction is not MappingDirection.SAME
        or record.direct_polymarket is None
    ):
        return None
    return record.direct_polymarket.mapped_token_id


class ResidualTakerLiveCoordinator:
    def __init__(
        self,
        *,
        mapping: MappingDocument,
        tracked_exchange_ids: frozenset[str],
        size: int,
        max_pm_book_age_ns: int,
        risk_context: RiskContextSource,
        reservations: ExecutionReservationBook,
        journal: ExecutionJournal,
        quotes: QuoteRegistry,
        dispatch: PlanDispatcher,
        cancel: CancelDispatcher,
        kill_switch: MakerKillSwitch,
        min_fair_value: float = 0.0,
        max_fair_value: float = 1.0,
        max_position: int | None = None,
        allow_bbo_proxy: bool = False,
        max_sig_bbo_age_ns: int | None = None,
        threshold: float = THRESHOLD,
        pm_spread_cap: float = PM_SPREAD_CAP,
        min_pm_depth: float = MIN_PM_DEPTH,
        cooldown_ns: int = COOLDOWN_NS,
        economics_gate_enabled: bool = False,
        min_net_edge: float = 0.005,
        fee_per_share: float = 0.0,
        latency_haircut: float = 0.005,
        adverse_haircut: float = 0.005,
        max_sig_depth_age_ns: int = 1_500_000_000,
        max_sig_stream_depth_age_ns: int = 15_000_000_000,
        max_fair_age_ns: int = 5_000_000_000,
        request_fresh_depth: Callable[[str, float], None] | None = None,
        rotation_allocator: LiquidityWeightedAllocator | None = None,
        rotation_reduce_only_targets: Callable[[frozenset[str], int], None] | None = None,
        active_rotation_exits: bool = False,
        request_rotation_recheck: Callable[[frozenset[str]], None] | None = None,
        rotation_capacity_priority: Callable[[bool, int], None] | None = None,
    ) -> None:
        if not tracked_exchange_ids:
            raise ValueError("LIVE residual taker requires an explicit universe")
        self._mapping = {record.sig_exchange_id: record for record in mapping.records}
        self._signal = ResidualTakerSignal(
            size=size,
            tracked_exchange_ids=tracked_exchange_ids,
            threshold=threshold,
            pm_spread_cap=pm_spread_cap,
            min_pm_depth=min_pm_depth,
            cooldown_ns=cooldown_ns,
        )
        self._threshold = threshold
        self._max_pm_book_age_ns = max_pm_book_age_ns
        self._max_sig_bbo_age_ns = (
            max_pm_book_age_ns if max_sig_bbo_age_ns is None else max_sig_bbo_age_ns
        )
        self._risk_context_source = risk_context
        self._reservations = reservations
        self._journal = journal
        self._quotes = quotes
        self._dispatch = dispatch
        self._cancel = cancel
        self._kill_switch = kill_switch
        # Near-certain markets are not taken: a residual there is mostly
        # settlement/bag-holding risk, not mean reversion.
        self._min_fair_value = min_fair_value
        self._max_fair_value = max_fair_value
        # Per-market cap on signed YES inventory the taker may build; trades
        # that reduce inventory are never blocked.
        self._max_position = max_position
        self._economics_gate_enabled = economics_gate_enabled
        self._min_net_edge = min_net_edge
        self._fee_per_share = fee_per_share
        self._latency_haircut = latency_haircut
        self._adverse_haircut = adverse_haircut
        self._max_sig_depth_age_ns = max_sig_depth_age_ns
        self._max_sig_stream_depth_age_ns = max_sig_stream_depth_age_ns
        self._max_fair_age_ns = max_fair_age_ns
        self._request_fresh_depth = request_fresh_depth
        self._rotation_allocator = rotation_allocator
        self._rotation_reduce_only_targets = rotation_reduce_only_targets
        self._active_rotation_exits = active_rotation_exits
        self._request_rotation_recheck = request_rotation_recheck
        self._rotation_pending: dict[str, tuple[RankedRotation, int]] = {}
        self._rotation_last_request_ns: dict[str, int] = {}
        self._rotation_last_exit_ns: dict[str, int] = {}
        self._rotation_pending_entries: dict[str, _PendingRotationEntry] = {}
        self._rotation_last_entry_request_ns: dict[str, int] = {}
        self._rotation_capacity_priority = rotation_capacity_priority
        self._rotation_priority_published = False
        self._rotation_evidence = RotationEvidenceCache()
        self._rotation_last_rank_ns = 0
        self._allow_bbo_proxy = allow_bbo_proxy
        # Remainder cancels that did not conclude; retried every cycle so a
        # taker order is never left resting at a stale price.
        self._pending_cancels: dict[str, ExecutionEnvelope] = {}
        self._evaluation_cycles = 0
        self._evaluated_markets = 0
        self._candidate_ready_markets = 0
        self._no_residual_markets = 0
        self._signals = 0
        self._attempted_orders = 0
        self._skipped_signals: Counter[str] = Counter()
        # Fixed-size diagnostic: distinguish missing WS corroboration from
        # genuinely old ladders without logging per-candidate hot-path events.
        self._depth_window_counts: Counter[str] = Counter()
        self._evaluation_changed = asyncio.Event()

    @property
    def tracked_exchange_ids(self) -> frozenset[str]:
        return self._signal.tracked_exchange_ids

    @property
    def evaluation_cycles(self) -> int:
        return self._evaluation_cycles

    async def wait_for_evaluation_after(self, evaluation_cycles: int) -> None:
        """Wait for a completed coordinator pass after ``evaluation_cycles``."""
        while self._evaluation_cycles <= evaluation_cycles:
            self._evaluation_changed.clear()
            if self._evaluation_cycles > evaluation_cycles:
                return
            await self._evaluation_changed.wait()

    def log_heartbeat(self) -> None:
        _LOG.info(
            "TAKE heartbeat evaluated=%d candidate_ready=%d no_residual=%d "
            "signals=%d attempts=%d cycles=%d skipped=%s depth_windows=%s",
            self._evaluated_markets,
            self._candidate_ready_markets,
            self._no_residual_markets,
            self._signals,
            self._attempted_orders,
            self._evaluation_cycles,
            dict(self._skipped_signals),
            dict(self._depth_window_counts),
        )
        if self._rotation_allocator is not None:
            # Ranking is low-cadence and advisory: no order submission here.
            ranked = self._rotation_allocator.rank(limit=3)
            _LOG.info(
                "ROTATION shadow entries=%d exits=%d shortlisted=%s sightings=%d history=%d",
                len(self._rotation_allocator.entries),
                len(self._rotation_allocator.exits),
                [(r.assessment.exit_exchange_id, r.assessment.replacement_exchange_id,
                  r.assessment.shares, round(r.gain_susq, 3),
                  round(r.entry_ev_susq, 3)) for r in ranked],
                self._rotation_evidence.qualified_observations,
                len(self._rotation_evidence.history),
            )

    def _rotation_alternative_beats_target(
        self, *, net_per_share: float | None, quantity: int, now_ns: int,
    ) -> str | None:
        """Offer released capital to a better live opportunity, not merely first arrival.

        The competing ordinary taker must clear the strongest queued replacement
        by an additional execution buffer. An unpriced/stale target is never
        assumed to have zero value merely to permit unrelated consumption.
        """
        allocator = self._rotation_allocator
        if (
            allocator is None or net_per_share is None
            or not math.isfinite(net_per_share) or net_per_share <= 0
            or quantity < 1
        ):
            return None
        best_id: str | None = None
        best_edge = -math.inf
        for eid, pending in self._rotation_pending_entries.items():
            if now_ns < pending.created_ns or now_ns - pending.created_ns >= 75_000_000_000:
                continue
            entry = allocator.entries.get(eid)
            if (
                entry is None or now_ns < entry.observed_ns
                or now_ns - entry.observed_ns > allocator.max_age_ns
            ):
                return None
            size = min(quantity, entry.capacity, pending.exit_shares)
            if size < 1:
                return None
            expected = entry.net_ev(size)
            if expected is None:
                return None
            if expected / size > best_edge:
                best_edge = expected / size
                best_id = eid
        # Require an economically meaningful improvement instead of bouncing
        # between exchange books due to one-tick noise.
        if (
            best_id is not None
            and net_per_share > best_edge + allocator.rotation_buffer
        ):
            return best_id
        return None

    def _publish_rotation_priority(self, now_ns: int) -> bool:
        """Prefer the planned replacement for a short window after an exit."""
        active = self._active_rotation_exits and any(
            0 <= now_ns - entry.created_ns < 75_000_000_000
            for entry in self._rotation_pending_entries.values()
        )
        if active != self._rotation_priority_published:
            self._rotation_priority_published = active
            if self._rotation_capacity_priority is not None:
                self._rotation_capacity_priority(active, now_ns)
            _LOG.info(
                "ROTATION capital priority active=%s pending=%d",
                active, len(self._rotation_pending_entries),
            )
        return active

    def _capture_rotation(
        self, snapshot: MakerMarketSnapshot, *, risk_ready: bool
    ) -> None:
        """Record bounded shadow evidence; no order submission."""
        allocator = self._rotation_allocator
        if allocator is None:
            return
        portfolio = snapshot.runtime.portfolio
        trusted = bool(
            risk_ready
            and portfolio.account_trusted
            and portfolio.trust_grade is AccountTrustGrade.TRUSTED
            and portfolio.account_proxy_uncertainty == 0
            and not any(order.uncertain for order in portfolio.orders)
        )
        self._rotation_evidence.update_account(
            trusted=trusted,
            positions=tuple(
                (p.exchange_id, p.market_id, p.tournament_id,
                 p.signed_quantity, p.gross_exposure)
                for p in portfolio.positions
            ),
            pending_orders=tuple(
                (order.exchange_id, order.market_id, order.tournament_id,
                 order.logical_intent_id, order.reserved_exposure,
                 order.signed_quantity)
                for order in portfolio.orders if order.open or order.uncertain
            ),
        )
        now_ns = snapshot.now_monotonic_ns
        # Revisit replacements as trusted account and book updates arrive.
        # The original exit signal never grants permission to place an entry.
        for eid, pending in tuple(self._rotation_pending_entries.items()):
            if now_ns < pending.created_ns or now_ns - pending.created_ns > 180_000_000_000:
                self._rotation_pending_entries.pop(eid, None)
                continue
            if (
                trusted
                and self._request_rotation_recheck is not None
                and now_ns - self._rotation_last_entry_request_ns.get(
                    eid, -10_000_000_000
                ) >= 2_000_000_000
            ):
                self._rotation_last_entry_request_ns[eid] = now_ns
                self._request_rotation_recheck(frozenset({eid}))
        self._publish_rotation_priority(now_ns)
        if not trusted or not allocator.entries or not allocator.exits:
            self._rotation_pending.clear()
            if self._rotation_reduce_only_targets is not None:
                self._rotation_reduce_only_targets(frozenset(), now_ns)
            return
        if now_ns - self._rotation_last_rank_ns < 500_000_000:
            return
        self._rotation_last_rank_ns = now_ns
        ranked = allocator.rank(now_ns=now_ns, limit=3)
        if self._active_rotation_exits:
            self._rotation_pending = {
                item.assessment.exit_exchange_id: (item, now_ns)
                for item in ranked
            }
            if self._request_rotation_recheck is not None:
                to_recheck = frozenset(
                    eid for eid in self._rotation_pending
                    if eid != snapshot.exchange_id
                    and now_ns - self._rotation_last_request_ns.get(eid, -10_000_000_000)
                    >= 1_500_000_000
                )
                if to_recheck:
                    for eid in to_recheck:
                        self._rotation_last_request_ns[eid] = now_ns
                    self._request_rotation_recheck(to_recheck)
        if self._rotation_reduce_only_targets is not None:
            # A dynamic target only changes which EXISTING maker side may
            # quote. Never treat the shortlist as permission for a taker exit.
            self._rotation_reduce_only_targets(
                frozenset(item.assessment.exit_exchange_id for item in ranked),
                now_ns,
            )
        for candidate in ranked:
            evidence = RotationEvidence(
                candidate.assessment.exit_exchange_id,
                candidate.assessment.replacement_exchange_id,
                candidate.assessment.shares,
                candidate.gain_susq,
                candidate.entry_ev_susq,
                now_ns,
            )
            self._rotation_evidence.offer(
                evidence,
                now_ns=now_ns,
                publish=lambda item: _LOG.info(
                    "ROTATION sighting exit=%s entry=%s shares=%d "
                    "expected_gain=%.3f entry_ev=%.3f ADVISORY_ONLY",
                    item.exit_exchange_id, item.entry_exchange_id,
                    item.quantity, item.net_improvement, item.entry_ev,
                ),
            )

    async def run_liveness(
        self,
        *,
        stop_event: asyncio.Event,
        recheck: Callable[[], None],
    ) -> None:
        """Periodically recheck the tracked universe and fail if evaluation stalls."""
        while not stop_event.is_set():
            try:
                await asyncio.wait_for(
                    stop_event.wait(),
                    timeout=_LIVENESS_INTERVAL_SECONDS,
                )
                return
            except TimeoutError:
                pass

            # A busy LIVE runtime intentionally evaluates only one exchange per
            # cycle. A temporary backlog must not restart the entire maker on
            # the first missed heartbeat; force one more bounded evaluation.
            # A genuinely stuck coordinator still fails closed on the retry.
            for attempt in range(2):
                if stop_event.is_set():
                    return
                previous_cycles = self._evaluation_cycles
                self.log_heartbeat()
                recheck()
                try:
                    await asyncio.wait_for(
                        self.wait_for_evaluation_after(previous_cycles),
                        timeout=_LIVENESS_INTERVAL_SECONDS,
                    )
                    break
                except TimeoutError as exc:
                    if stop_event.is_set():
                        return
                    if attempt == 0:
                        _LOG.warning("TAKE liveness delayed; forcing one bounded recheck")
                        continue
                    _LOG.critical(
                        "TAKE liveness stalled evaluated=%d candidate_ready=%d "
                        "signals=%d cycles=%d",
                        self._evaluated_markets,
                        self._candidate_ready_markets,
                        self._signals,
                        self._evaluation_cycles,
                    )
                    raise RuntimeError("residual taker coordinator stopped evaluating") from exc

    async def on_state_change(
        self,
        change: MakerStateChange,
        observed_at: datetime,
        snapshots: Mapping[str, MakerMarketSnapshot],
    ) -> tuple[ExecutionEvent, ...]:
        del observed_at
        events: list[ExecutionEvent] = []
        for operation_id, envelope in tuple(self._pending_cancels.items()):
            retried = await self._try_cancel(operation_id, envelope)
            if retried is not None:
                events.append(retried)
        # The LIVE bridge normally delivers one changed market per cycle.
        # Avoid allocating a sorted list in that overwhelmingly common case.
        exchange_ids = snapshots if len(snapshots) < 2 else sorted(snapshots)
        for exchange_id in exchange_ids:
            if self._kill_switch.active:
                break
            if exchange_id not in self._signal.tracked_exchange_ids:
                continue
            self._evaluated_markets += 1
            snapshot = snapshots[exchange_id]
            record = self._mapping.get(exchange_id)
            if self._rotation_allocator is not None:
                risk_context = (
                    self._risk_context_source()
                    if callable(self._risk_context_source)
                    else self._risk_context_source
                )
                capital = risk_context.capital_state
                risk_ready = bool(
                    not risk_context.kill_switch
                    and not self._kill_switch.active
                    and capital is not None
                    and capital.account_trusted
                    and capital.reconciliation_complete
                    and capital.marks_trusted
                    and capital.exposure.trusted
                    and capital.exposure.uncertain_order_exposure == 0
                    and not (capital.global_halt is not None and capital.global_halt.active)
                )
                self._rotation_allocator.observe(
                    snapshot,
                    rotation_mapped_token(record),
                    risk_state_trusted=risk_ready,
                )
                self._capture_rotation(snapshot, risk_ready=risk_ready)
                if self._active_rotation_exits and exchange_id in self._rotation_pending_entries:
                    entered = await self._execute_rotation_entry(change, snapshot)
                    if entered:
                        events.extend(entered)
                        continue
                pending = self._rotation_pending.get(exchange_id)
                if self._active_rotation_exits and pending is not None:
                    proposed, proposed_ns = pending
                    if 0 <= snapshot.now_monotonic_ns - proposed_ns <= 1_500_000_000:
                        rotated = await self._execute_rotation_exit(
                            change, snapshot, proposed
                        )
                        if rotated:
                            events.extend(rotated)
                            continue
            # Most updates have no executable PM/SIG price residual. Reject
            # that common case using only touch prices, before allocating a
            # signal input or checking ancillary feed/portfolio state. All
            # candidates still pass the original full fail-closed validation.
            if record is not None and record.direct_polymarket is not None:
                quote = snapshot.external_quotes.get(
                    record.direct_polymarket.mapped_token_id
                )
                book = snapshot.runtime.book(exchange_id)
                if (
                    quote is not None
                    and quote.best_bid is not None
                    and quote.best_ask is not None
                    and book is not None
                    and book.bids
                    and book.asks
                ):
                    pm_mid = (quote.best_bid + quote.best_ask) / 2.0
                    if (
                        book.asks[0].price_ticks * SIG_TICK_PROBABILITY
                        > pm_mid - self._threshold
                        and book.bids[0].price_ticks * SIG_TICK_PROBABILITY
                        < pm_mid + self._threshold
                    ):
                        self._no_residual_markets += 1
                        continue
            sig_bbo_age = snapshot.now_monotonic_ns - snapshot.sig_bbo_observed_ns
            bbo_proxy = (
                not snapshot.sig_bbo_trusted
                or sig_bbo_age < 0
                or sig_bbo_age >= self._max_sig_bbo_age_ns
            )
            state = residual_input_from_snapshot(
                snapshot,
                record,
                max_pm_book_age_ns=self._max_pm_book_age_ns,
                observed_monotonic_ns=snapshot.now_monotonic_ns,
                allow_bbo_proxy=self._allow_bbo_proxy,
                threshold=self._threshold,
            )
            if state is None or not (self._min_fair_value <= state.pm_mid <= self._max_fair_value):
                continue
            self._candidate_ready_markets += 1
            signal = self._signal.on_state(state, commit_cooldown=False)
            if signal is None:
                continue
            self._signals += 1
            events.extend(await self._take(change, snapshot, signal, bbo_proxy=bbo_proxy))
        self._evaluation_cycles += 1
        self._evaluation_changed.set()
        return tuple(events)

    async def _execute_rotation_exit(
        self,
        change: MakerStateChange,
        snapshot: MakerMarketSnapshot,
        proposed: RankedRotation,
    ) -> tuple[ExecutionEvent, ...]:
        """Take a verified reducing exit; leave reinvestment to fresh admission.

        All observed EV is advisory until this fresh RISK-002/BUILD-009 path
        approves. Any missing journal claim or uncertain order fails closed.
        No new position is opened from the same stale shortlist.
        """
        now = snapshot.now_monotonic_ns
        eid = snapshot.exchange_id
        if (
            self._kill_switch.active
            or now - self._rotation_last_exit_ns.get(eid, -60_000_000_000)
            < 30_000_000_000
            or self._rotation_allocator is None
        ):
            return ()
        # Recompute after the target market's latest observation. The pending
        # signal never authorises a transaction on its own.
        fresh = next((
            item for item in self._rotation_allocator.rank(now_ns=now, limit=3)
            if item.assessment.exit_exchange_id == eid
            and item.assessment.replacement_exchange_id
                == proposed.assessment.replacement_exchange_id
            and item.assessment.gain_after_buffer
                >= self._rotation_allocator.min_rotation_gain
        ), None)
        if fresh is None:
            return ()
        record = self._mapping.get(eid)
        if rotation_mapped_token(record) is None:
            return ()
        portfolio = snapshot.runtime.portfolio
        if (
            not portfolio.account_trusted
            or portfolio.trust_grade is not AccountTrustGrade.TRUSTED
            or portfolio.account_proxy_uncertainty != 0
            or any(
                o.uncertain
                or (
                    o.open
                    and o.market_id == snapshot.market_id
                    and o.tournament_id == snapshot.tournament_id
                )
                for o in portfolio.orders
            )
        ):
            return ()
        book = snapshot.runtime.book(eid)
        depth_ns = snapshot.sig_depth_observed_ns
        if (
            not snapshot.sig_depth_trusted or not snapshot.sig_bbo_trusted
            or book is None or not book.trusted_depth or depth_ns is None
        ):
            return ()
        age = now - depth_ns
        max_age = (
            self._max_sig_stream_depth_age_ns
            if snapshot.sig_stream_depth_corroborated
            else self._max_sig_depth_age_ns
        )
        bbo_age = now - snapshot.sig_bbo_observed_ns
        if (
            age < 0 or age > max_age
            or bbo_age < 0 or bbo_age > self._max_sig_bbo_age_ns
        ):
            return ()
        signed = portfolio.signed_inventory(eid, snapshot.tournament_id)
        if not math.isfinite(signed) or signed == 0 or signed != math.trunc(signed):
            return ()
        action = OrderAction.SELL if signed > 0 else OrderAction.BUY
        if signed * fresh.assessment.shares == 0:
            return ()
        context = (
            self._risk_context_source()
            if callable(self._risk_context_source)
            else self._risk_context_source
        )
        # Respect every configured order/open-order limit in every risk
        # profile. Never assume a reducing order is free to remain OPEN.
        limits = [item for item in (
            context.limits,
            None if context.profile is None else context.profile.limits,
            None if context.profile is None else context.profile.hard_limits,
        ) if item is not None]
        if (
            not limits
            or context.mode is not ExecutionMode.LIVE
            or context.capital_state is None
            or not context.capital_state.reconciliation_complete
            or not context.capital_state.account_trusted
            or not context.capital_state.exposure.trusted
            or context.capital_state.exposure.uncertain_order_exposure != 0
        ):
            return ()
        levels = book.bids if action is OrderAction.SELL else book.asks
        # A marketable slice should use whatever *real* bid/ask depth exists,
        # not reject an otherwise profitable switch when a level disappears.
        if any(not math.isfinite(level.quantity) or level.quantity < 0 for level in levels):
            return ()
        depth_capacity = sum(
            int(math.floor(level.quantity)) for level in levels[:64]
        )
        quantity = min(
            fresh.assessment.shares, abs(int(signed)), depth_capacity,
            *(item.max_order_size for item in limits),
            *(max(0, math.floor(
                item.max_open_order_exposure - portfolio.open_order_exposure
            )) for item in limits),
        )
        if quantity < 1:
            return ()
        # Revalue at the ACTUAL permitted size, never extrapolate a 500-share
        # candidate's net return into a different tranche.
        revised = fresh.assessment
        if quantity != revised.shares:
            old = self._rotation_allocator.exits.get(eid)
            entry = self._rotation_allocator.entries.get(revised.replacement_exchange_id)
            entry_ev = None if entry is None else entry.net_ev(quantity)
            if old is None or entry is None or entry_ev is None or entry_ev <= 0:
                return ()
            revised = assess_rotation(
                HeldPosition(eid, int(signed), old.conservative_fair_yes),
                Replacement(entry.exchange_id, quantity, entry_ev / quantity),
                yes_bids=old.yes_bids,
                yes_asks=old.yes_asks,
                conditions=RotationConditions(
                    account_trusted=True,
                    reconciliation_complete=True,
                    depth_trusted=True,
                    book_age_ms=age / 1_000_000,
                    max_depth_age_ms=max_age / 1_000_000 + 0.001,
                    no_conflicting_orders=True,
                    max_tranche_shares=quantity,
                    exit_cost_per_share=self._rotation_allocator.exit_fee,
                    extra_buffer_per_share=self._rotation_allocator.rotation_buffer,
                    min_total_gain=self._rotation_allocator.min_rotation_gain,
                ),
            )
            if revised.status != "CANDIDATE_ONLY":
                return ()
        levels = book.bids if action is OrderAction.SELL else book.asks
        remaining = quantity
        last = None
        ticks = None
        for level in levels:
            if last is not None and (
                (action is OrderAction.SELL and level.price_ticks > last)
                or (action is OrderAction.BUY and level.price_ticks < last)
            ):
                return ()
            last = level.price_ticks
            if not math.isfinite(level.quantity) or level.quantity < 0:
                return ()
            usable = max(0, int(math.floor(level.quantity)))
            remaining -= min(remaining, usable)
            ticks = level.price_ticks
            if remaining == 0:
                break
        if remaining or ticks is None or not 1 <= ticks <= 199:
            return ()
        if self._crosses_own_quote(eid, action, ticks):
            return ()
        opportunity = Opportunity(
            family=StrategyFamily.FV_TAKE,
            strategy_id="rotation-exit-v1",
            legs=(CandidateLeg(
                exchange_id=eid,
                market_id=snapshot.market_id,
                tournament_id=snapshot.tournament_id,
                outcome_side=OutcomeSide.YES,
                action=action,
                quantity=quantity,
                limit_price_ticks=ticks,
            ),),
            gross_edge=revised.gain_after_buffer / quantity,
            fair_value=revised.hold_fair_value / quantity,
            decision_observation_ns=now,
        )
        risk = evaluate_risk(
            opportunity,
            self._reservations.overlay_snapshot(snapshot.runtime),
            replace(context, kill_switch=context.kill_switch or self._kill_switch.active),
        )
        if not risk.approved or not risk.inventory_reduction_verified:
            self._skipped_signals[
                "rotation_risk_" + (
                    risk.reason if not risk.approved else "reduction_not_verified"
                )
            ] += 1
            return ()
        operation_id = f"rotation-exit:{change.event_id}:{eid}"
        # BUILD-009's SQLite UNIQUE claim survives process restarts. Even a
        # timed-out send may not be replayed with this same operation identity.
        if not self._journal.claim_rotation_operation(
            operation_id, snapshot.tournament_id
        ):
            return ()
        plan = build_execution_plan(
            risk,
            logical_operation_id=operation_id + ":exit",
            created_monotonic_ns=change.observed_monotonic_ns,
        )
        self._reservations.reserve(plan.envelope.logical_operation_id, plan.intents)
        self._rotation_last_exit_ns[eid] = now
        _LOG.info(
            "ROTATION exit placing exchange=%s qty=%d action=%s tick=%d "
            "candidate_gain=%.3f replacement=%s",
            eid, quantity, action.value, ticks, revised.gain_after_buffer,
            fresh.assessment.replacement_exchange_id,
        )
        try:
            event = await self._dispatch(plan)
        except SigExecutionUncertainError:
            _LOG.warning("ROTATION exit placement UNCERTAIN op=%s", operation_id)
            return (ExecutionEvent(
                logical_operation_id=plan.envelope.logical_operation_id,
                state=LifecycleState.UNCERTAIN,
                observed_monotonic_ns=now,
                simulated=False,
                detail="rotation_exit_uncertain",
            ),)
        except SigApiError as exc:
            _LOG.warning("ROTATION exit rejected op=%s reason=%s", operation_id, exc)
            return (ExecutionEvent(
                logical_operation_id=plan.envelope.logical_operation_id,
                state=LifecycleState.REJECTED,
                observed_monotonic_ns=now,
                simulated=False,
                detail=type(exc).__name__,
            ),)
        result = [event]
        cancelled = await self._cancel_remainder(plan, event, snapshot.tournament_id)
        if cancelled is not None:
            result.append(cancelled)
        # Do not count a returned ACK as released capital: the new trusted
        # portfolio and operation-attributed journal fills must agree first.
        if (
            event.state not in {LifecycleState.REJECTED, LifecycleState.UNCERTAIN}
            and (cancelled is None or cancelled.state is not LifecycleState.UNCERTAIN)
            and fresh.assessment.replacement_exchange_id not in self._rotation_pending_entries
        ):
            entry_id = fresh.assessment.replacement_exchange_id
            self._rotation_pending_entries[entry_id] = _PendingRotationEntry(
                exit_operation_id=plan.envelope.logical_operation_id,
                exit_exchange_id=eid,
                entry_exchange_id=entry_id,
                entry_direction=fresh.entry_direction,
                tournament_id=snapshot.tournament_id,
                before_signed=int(signed),
                exit_shares=quantity,
                exit_concession_per_share=revised.exit_concession / quantity,
                exit_account_observed_ns=snapshot.account_observed_ns,
                created_ns=now,
                entry_before_signed=int(
                    portfolio.signed_inventory(entry_id, snapshot.tournament_id)
                ),
            )
            self._publish_rotation_priority(now)
            if self._request_rotation_recheck is not None:
                self._request_rotation_recheck(frozenset({entry_id}))
        return tuple(result)


    async def _execute_rotation_entry(
        self,
        change: MakerStateChange,
        snapshot: MakerMarketSnapshot,
    ) -> tuple[ExecutionEvent, ...]:
        """Reinvest actual exit fills at fresh executable prices, never an ACK."""
        eid = snapshot.exchange_id
        pending = self._rotation_pending_entries.get(eid)
        allocator = self._rotation_allocator
        if pending is None or allocator is None or self._kill_switch.active:
            return ()
        now = snapshot.now_monotonic_ns
        if now < pending.created_ns or now - pending.created_ns > 180_000_000_000:
            self._rotation_pending_entries.pop(eid, None)
            return ()
        portfolio = snapshot.runtime.portfolio
        if (
            snapshot.account_observed_ns <= pending.exit_account_observed_ns
            or not portfolio.account_trusted
            or portfolio.trust_grade is not AccountTrustGrade.TRUSTED
            or portfolio.account_proxy_uncertainty != 0
            or any(order.uncertain for order in portfolio.orders)
            or any(order.open and order.exchange_id in (
                pending.exit_exchange_id, eid
            ) for order in portfolio.orders)
            or rotation_mapped_token(self._mapping.get(eid)) is None
        ):
            return ()
        after = portfolio.signed_inventory(
            pending.exit_exchange_id, pending.tournament_id
        )
        if not math.isfinite(after) or after != math.trunc(after):
            return ()
        if pending.before_signed > 0:
            filled = pending.before_signed - int(after)
            reduced = 0 <= int(after) <= pending.before_signed
        else:
            filled = int(after) - pending.before_signed
            reduced = pending.before_signed <= int(after) <= 0
        if not reduced or not 0 < filled <= pending.exit_shares:
            return ()
        # Journal de-duplicates realtime and authoritative fill reports.
        # Every share in the new account position delta must be attributable
        # to THIS exit, not an unrelated maker fill on the same instrument.
        journal_shares = Decimal(0)
        saw_fill = False
        for evt in self._journal.events(pending.exit_operation_id):
            if evt.event_type not in {"REALTIME_FILL", "AUTHORITATIVE_FILL"}:
                continue
            if evt.exchange_id != pending.exit_exchange_id or evt.quantity is None:
                return ()
            try:
                amount = abs(Decimal(evt.quantity))
            except (InvalidOperation, ValueError):
                return ()
            if not amount.is_finite() or amount <= 0:
                return ()
            journal_shares += amount
            saw_fill = True
        if not saw_fill or journal_shares != filled:
            return ()
        current_entry = portfolio.signed_inventory(eid, pending.tournament_id)
        if not math.isfinite(current_entry):
            return ()
        already_acquired = (
            current_entry - pending.entry_before_signed
            if pending.entry_direction == "BUY"
            else pending.entry_before_signed - current_entry
        )
        already_acquired = max(0, math.floor(already_acquired))
        if already_acquired >= filled:
            self._rotation_pending_entries.pop(eid, None)
            self._publish_rotation_priority(now)
            _LOG.info(
                "ROTATION entry fulfilled by another strategy exchange=%s "
                "actual_shares=%d exit_shares=%d",
                eid, already_acquired, filled,
            )
            return ()
        if not snapshot.sig_depth_trusted or not snapshot.sig_bbo_trusted:
            return ()
        book = snapshot.runtime.book(eid)
        age = now - (snapshot.sig_depth_observed_ns or now + 1)
        bbo_age = now - snapshot.sig_bbo_observed_ns
        max_age = (
            self._max_sig_stream_depth_age_ns
            if snapshot.sig_stream_depth_corroborated
            else self._max_sig_depth_age_ns
        )
        if (
            book is None or not book.trusted_depth
            or not 0 <= age <= max_age
            or not 0 <= bbo_age <= self._max_sig_bbo_age_ns
        ):
            return ()
        entry = allocator.entries.get(eid)
        if entry is None or now - entry.observed_ns > allocator.max_age_ns:
            return ()
        action = OrderAction.BUY if entry.direction == "BUY" else OrderAction.SELL
        if entry.direction not in {"BUY", "SELL"}:
            return ()
        context = (
            self._risk_context_source()
            if callable(self._risk_context_source)
            else self._risk_context_source
        )
        capital = context.capital_state
        if (
            context.mode is not ExecutionMode.LIVE
            or context.kill_switch
            or capital is None
            or not capital.reconciliation_complete
            or not capital.account_trusted
            or not capital.exposure.trusted
            or capital.exposure.uncertain_order_exposure != 0
        ):
            return ()
        limits = [item for item in (
            context.limits,
            None if context.profile is None else context.profile.limits,
            None if context.profile is None else context.profile.hard_limits,
        ) if item is not None]
        if not limits:
            return ()
        levels = book.asks if action is OrderAction.BUY else book.bids
        if any(not math.isfinite(level.quantity) or level.quantity < 0 for level in levels):
            return ()
        depth_capacity = sum(
            int(math.floor(level.quantity)) for level in levels[:64]
        )
        quantity = min(
            filled - already_acquired, entry.capacity, depth_capacity,
            *(item.max_order_size for item in limits),
            *(max(0, math.floor(
                item.max_open_order_exposure - portfolio.open_order_exposure
            )) for item in limits),
        )
        if quantity < 1:
            return ()
        levels = book.asks if action is OrderAction.BUY else book.bids
        remaining = quantity
        ticks = None
        for level in levels:
            if not math.isfinite(level.quantity) or level.quantity <= 0:
                return ()
            remaining -= min(remaining, math.floor(level.quantity))
            ticks = level.price_ticks
            if remaining <= 0:
                break
        if remaining > 0 or ticks is None or not 1 <= ticks <= 199:
            return ()
        entry_ev = entry.net_ev(quantity)
        if entry_ev is None:
            return ()
        gain = entry_ev - quantity * (
            pending.exit_concession_per_share
            + allocator.exit_fee + allocator.rotation_buffer
        )
        if gain < allocator.min_rotation_gain or not math.isfinite(gain):
            self._skipped_signals["rotation_entry_ev_disappeared"] += 1
            return ()
        if self._crosses_own_quote(eid, action, ticks):
            return ()
        opportunity = Opportunity(
            family=StrategyFamily.FV_TAKE,
            strategy_id="rotation-entry-v1",
            legs=(CandidateLeg(
                exchange_id=eid,
                market_id=snapshot.market_id,
                tournament_id=snapshot.tournament_id,
                outcome_side=OutcomeSide.YES,
                action=action,
                quantity=quantity,
                limit_price_ticks=ticks,
            ),),
            gross_edge=gain / quantity,
            fair_value=None,
            decision_observation_ns=now,
        )
        approval = evaluate_risk(
            opportunity,
            self._reservations.overlay_snapshot(snapshot.runtime),
            replace(context, kill_switch=context.kill_switch or self._kill_switch.active),
        )
        if not approval.approved:
            self._skipped_signals["rotation_entry_risk_" + approval.reason] += 1
            return ()
        operation_id = pending.exit_operation_id.removesuffix(":exit") + ":entry"
        if not self._journal.claim_rotation_operation(
            operation_id, snapshot.tournament_id
        ):
            self._rotation_pending_entries.pop(eid, None)
            return ()
        plan = build_execution_plan(
            approval,
            logical_operation_id=operation_id,
            created_monotonic_ns=change.observed_monotonic_ns,
        )
        self._reservations.reserve(operation_id, plan.intents)
        self._rotation_pending_entries.pop(eid, None)
        self._publish_rotation_priority(now)
        _LOG.info(
            "ROTATION entry placing exchange=%s shares=%d direction=%s "
            "limit=%d net_incremental_gain=%.3f after_attributed_exit=%s",
            eid, quantity, entry.direction, ticks, gain, pending.exit_operation_id,
        )
        try:
            event = await self._dispatch(plan)
        except SigExecutionUncertainError:
            return (ExecutionEvent(
                logical_operation_id=operation_id,
                state=LifecycleState.UNCERTAIN,
                observed_monotonic_ns=now,
                simulated=False,
                detail="rotation_entry_uncertain",
            ),)
        except SigApiError as exc:
            return (ExecutionEvent(
                logical_operation_id=operation_id,
                state=LifecycleState.REJECTED,
                observed_monotonic_ns=now,
                simulated=False,
                detail=type(exc).__name__,
            ),)
        results = [event]
        cancelled = await self._cancel_remainder(plan, event, snapshot.tournament_id)
        if cancelled is not None:
            results.append(cancelled)
        return tuple(results)

    def _capped_quantity(
        self,
        snapshot: MakerMarketSnapshot,
        action: OrderAction,
        quantity: int,
        require_reducing: bool = False,
        portfolio: RuntimePortfolio | None = None,
    ) -> int:
        if portfolio is None:
            portfolio = self._reservations.overlay_portfolio(snapshot.runtime.portfolio)
        if self._max_position is None and (require_reducing or portfolio.proxy_active):
            low, high = portfolio.worst_case_inventory_bounds(
                snapshot.exchange_id,
                snapshot.tournament_id,
            )
            if action is OrderAction.BUY and high < 0.0:
                return max(0, min(quantity, math.floor(-high)))
            if action is OrderAction.SELL and low > 0.0:
                return max(0, min(quantity, math.floor(low)))
            return 0
        if self._max_position is None:
            return quantity

        # Apply the configured cap in every trust mode. Directional bounds count
        # open orders and reservations that would add to this taker direction,
        # while known opposite-direction orders do not consume taker room.
        low, high = portfolio.worst_case_inventory_bounds(
            snapshot.exchange_id,
            snapshot.tournament_id,
        )
        room = self._max_position - high if action is OrderAction.BUY else self._max_position + low
        return max(0, min(quantity, math.floor(room)))

    def _candidate_refresh_score(
        self,
        snapshot: MakerMarketSnapshot,
        signal: ResidualSignal,
        *,
        action: OrderAction,
    ) -> float | None:
        """Optimistic AFTER-COST bound before spending one scarce REST read.

        Uses trusted/young external bid/ask and SIG best price only. Never
        claims execution: full depth, account admission and risk are rechecked.
        """
        record = self._mapping.get(snapshot.exchange_id)
        book = snapshot.runtime.book(snapshot.exchange_id)
        if record is None or record.direct_polymarket is None or book is None:
            return None
        if not snapshot.sig_bbo_trusted or not book.bids or not book.asks:
            return None
        bbo_age = snapshot.now_monotonic_ns - snapshot.sig_bbo_observed_ns
        # A depth repair request is not an order. Use the independent BBO
        # freshness bound here, not the full-book execution TTL. Requiring
        # 1.5s BBO for a 5-10s REST recovery silently rejects good candidates.
        if bbo_age < 0 or bbo_age >= self._max_sig_bbo_age_ns:
            return None
        external = snapshot.external_quotes.get(record.direct_polymarket.mapped_token_id)
        if external is None or not external.trusted:
            return None
        age = snapshot.now_monotonic_ns - external.observed_monotonic_ns
        if age < 0 or age > self._max_fair_age_ns:
            return None
        fair = external.best_bid if action is OrderAction.BUY else external.best_ask
        if fair is None or not math.isfinite(fair):
            return None
        touch = (
            book.asks[0].price_ticks if action is OrderAction.BUY
            else book.bids[0].price_ticks
        ) * SIG_TICK_PROBABILITY
        if not math.isfinite(touch) or touch < 0 or touch > 1:
            return None
        upper_edge = (
            (fair - touch) if action is OrderAction.BUY else (touch - fair)
        ) - (
            self._fee_per_share + self._latency_haircut
            + self._adverse_haircut + self._min_net_edge
        )
        if upper_edge <= 0:
            return None
        # This is a maximum possible opportunity score, not executable EV.
        # No full-depth assumption or cash assumption is used for admission.
        return upper_edge * max(1, signal.quantity)

    async def _take(
        self,
        change: MakerStateChange,
        snapshot: MakerMarketSnapshot,
        signal: ResidualSignal,
        *,
        bbo_proxy: bool,
    ) -> tuple[ExecutionEvent, ...]:
        exchange_id = snapshot.exchange_id
        book = snapshot.runtime.book(exchange_id)
        assert book is not None and book.bids and book.asks
        # The battery convention is YES-side: BUY lifts the SIG ask, SELL hits
        # the SIG bid. SIG canonicalises a flat YES sell to a NO buy itself.
        if signal.direction == "BUY":
            action, ticks = OrderAction.BUY, book.asks[0].price_ticks
        else:
            action, ticks = OrderAction.SELL, book.bids[0].price_ticks
        # A trusted full-depth snapshot is mandatory for gated taking.
        # Cheaply reject stale/untrusted depth BEFORE portfolio and reservation
        # overlays. The economics gate repeats these checks defensively.
        # Only a continuous WS ladder corroborated by a newer matching
        # scalar touch gets the longer observation window. A REST book or
        # any uncorroborated source keeps the original 1.5s execution bound.
        bbo_age_for_depth = snapshot.now_monotonic_ns - snapshot.sig_bbo_observed_ns
        depth_age_limit_ns = (
            self._max_sig_stream_depth_age_ns
            if (
                snapshot.sig_stream_depth_corroborated
                and snapshot.sig_bbo_trusted
                and 0 <= bbo_age_for_depth < self._max_sig_bbo_age_ns
            )
            else self._max_sig_depth_age_ns
        )
        if self._economics_gate_enabled:
            depth_ns_for_stats = snapshot.sig_depth_observed_ns
            if snapshot.sig_depth_trusted and book.trusted_depth and depth_ns_for_stats is not None:
                depth_age_for_stats = snapshot.now_monotonic_ns - depth_ns_for_stats
                if (
                    self._max_sig_depth_age_ns < depth_age_for_stats
                    <= self._max_sig_stream_depth_age_ns
                ):
                    self._depth_window_counts[
                        "extended_eligible"
                        if depth_age_limit_ns == self._max_sig_stream_depth_age_ns
                        else "extended_ineligible"
                    ] += 1
                elif depth_age_for_stats > self._max_sig_stream_depth_age_ns:
                    self._depth_window_counts["older_than_stream_limit"] += 1
            if not (snapshot.sig_depth_trusted and book.trusted_depth):
                self._skipped_signals["economics_depth_untrusted"] += 1
                if self._request_fresh_depth is not None:
                    score = self._candidate_refresh_score(
                        snapshot, signal, action=action,
                    )
                    if score is not None:
                        self._request_fresh_depth(exchange_id, score)
                    else:
                        self._skipped_signals["depth_refresh_precheck_rejected"] += 1
                return ()
            depth_ns = snapshot.sig_depth_observed_ns
            if (
                depth_ns is None
                or snapshot.now_monotonic_ns < depth_ns
                or snapshot.now_monotonic_ns - depth_ns > depth_age_limit_ns
            ):
                self._skipped_signals["economics_stale_data"] += 1
                if self._request_fresh_depth is not None:
                    score = self._candidate_refresh_score(
                        snapshot, signal, action=action,
                    )
                    if score is not None:
                        self._request_fresh_depth(exchange_id, score)
                    else:
                        self._skipped_signals["depth_refresh_precheck_rejected"] += 1
                return ()
        reduce_only = bbo_proxy or snapshot.runtime.portfolio.proxy_active
        held = self._reservations.overlay_portfolio(snapshot.runtime.portfolio)
        quantity = self._capped_quantity(
            snapshot,
            action,
            signal.quantity,
            require_reducing=reduce_only,
            portfolio=held,
        )
        if quantity < 1:
            self._skipped_signals["position_cap"] += 1
            _LOG.debug(
                "TAKE skip exchange=%s direction=%s reason=position_cap",
                exchange_id,
                signal.direction,
            )
            return ()
        # Only inventory guaranteed to shrink under every partial fill may
        # offset new collateral in the conservative cash screen.
        low, high = held.worst_case_inventory_bounds(snapshot.exchange_id, snapshot.tournament_id)
        reducible = max(0, math.floor(-high if action is OrderAction.BUY else low))
        # Shadow on every signal, or fail closed as an opt-in pre-risk gate.
        # Verified SAME/EXACT mapping was already checked by the signal input.
        record = self._mapping.get(exchange_id)
        external = (
            None
            if record is None or record.direct_polymarket is None
            else snapshot.external_quotes.get(record.direct_polymarket.mapped_token_id)
        )
        if external is not None:
            fair_bound = external.best_bid if action is OrderAction.BUY else external.best_ask
        else:
            fair_bound = None
        economics_net = None
        if fair_bound is None:
            if self._economics_gate_enabled:
                self._skipped_signals["fair_missing"] += 1
                _LOG.debug("TAKE economics deny exchange=%s reason=fair_missing", exchange_id)
                return ()
        else:
            assert external is not None
            if signal.direction not in ("BUY", "SELL"):
                return ()
            levels = book.asks if action is OrderAction.BUY else book.bids
            economics = evaluate_taker_economics(
                direction=cast(Direction, signal.direction),
                levels=levels,
                conservative_fair=fair_bound,
                desired_quantity=quantity,
                trusted_depth=bool(snapshot.sig_depth_trusted and book.trusted_depth),
                book_age_ns=(
                    -1
                    if snapshot.sig_depth_observed_ns is None
                    else snapshot.now_monotonic_ns - snapshot.sig_depth_observed_ns
                ),
                reference_age_ns=snapshot.now_monotonic_ns - external.observed_monotonic_ns,
                max_book_age_ns=depth_age_limit_ns,
                max_reference_age_ns=self._max_fair_age_ns,
                fee_per_share=self._fee_per_share,
                latency_haircut=self._latency_haircut,
                adverse_selection_haircut=self._adverse_haircut,
                min_net_edge_per_share=self._min_net_edge,
                available_cash=(
                    None
                    if snapshot.runtime.portfolio.account_proxy_cash_balance is None
                    else float(snapshot.runtime.portfolio.account_proxy_cash_balance)
                ),
                reducible_inventory_shares=reducible,
                maker_price_ticks=(
                    book.bids[0].price_ticks
                    if action is OrderAction.BUY
                    else book.asks[0].price_ticks
                ),
            )
            _LOG.log(
                logging.INFO if economics.admissible else logging.DEBUG,
                "TAKE economics %s exchange=%s direction=%s reason=%s qty=%d "
                "net_per_share=%.5f net_total=%.4f max_loss=%.2f "
                "limit=%s maker_conditional=%s",
                "gate" if self._economics_gate_enabled else "shadow",
                exchange_id,
                signal.direction,
                economics.reason,
                economics.quantity,
                economics.net_edge_per_share,
                economics.net_expected_value,
                economics.worst_case_settlement_loss,
                economics.limit_price_ticks,
                economics.maker_conditional_edge_per_share,
            )
            if self._economics_gate_enabled:
                if not economics.admissible:
                    self._skipped_signals["economics_" + economics.reason] += 1
                    return ()
                quantity = economics.quantity
                economics_net = economics.net_edge_per_share
                assert economics.limit_price_ticks is not None
                ticks = economics.limit_price_ticks
        # Reserve exit proceeds for the selected entry, except when a new
        # independently priced residual has *better* executable marginal EV.
        # A reducing trade is always permitted as it releases more capital.
        redirect_from: str | None = None
        if self._publish_rotation_priority(snapshot.now_monotonic_ns):
            target = any(
                pending.entry_exchange_id == exchange_id
                and pending.entry_direction == signal.direction
                for pending in self._rotation_pending_entries.values()
            )
            signed = held.signed_inventory(exchange_id, snapshot.tournament_id)
            reducing = (
                (action is OrderAction.BUY and signed <= -quantity)
                or (action is OrderAction.SELL and signed >= quantity)
            )
            if not target and not reducing:
                redirect_from = self._rotation_alternative_beats_target(
                    net_per_share=economics_net, quantity=quantity,
                    now_ns=snapshot.now_monotonic_ns,
                )
                if redirect_from is None:
                    self._skipped_signals["rotation_capacity_priority"] += 1
                    return ()
        if self._crosses_own_quote(exchange_id, action, ticks):
            self._skipped_signals["own_quote_at_touch"] += 1
            _LOG.debug(
                "TAKE skip exchange=%s direction=%s reason=own_quote_at_touch",
                exchange_id,
                signal.direction,
            )
            return ()

        context = (
            self._risk_context_source()
            if callable(self._risk_context_source)
            else self._risk_context_source
        )
        context = replace(context, kill_switch=context.kill_switch or self._kill_switch.active)
        opportunity = Opportunity(
            family=StrategyFamily.FV_TAKE,
            strategy_id=STRATEGY_ID,
            legs=(
                CandidateLeg(
                    exchange_id=exchange_id,
                    market_id=snapshot.market_id,
                    tournament_id=snapshot.tournament_id,
                    outcome_side=OutcomeSide.YES,
                    action=action,
                    quantity=quantity,
                    limit_price_ticks=ticks,
                ),
            ),
            gross_edge=signal.residual if economics_net is None else economics_net,
            fair_value=signal.pm_mid if economics_net is None else fair_bound,
            decision_observation_ns=signal.observed_monotonic_ns,
        )
        risk = evaluate_risk(
            opportunity,
            self._reservations.overlay_snapshot(snapshot.runtime),
            context,
        )
        if not risk.approved:
            self._skipped_signals["risk_" + str(risk.reason)] += 1
            _LOG.debug(
                "TAKE denied exchange=%s direction=%s residual=%.4f reason=%s",
                exchange_id,
                signal.direction,
                signal.residual,
                risk.reason,
            )
            return ()

        logical_operation_id = f"{change.event_id}:take:{exchange_id}"
        plan = build_execution_plan(
            risk,
            logical_operation_id=logical_operation_id,
            created_monotonic_ns=change.observed_monotonic_ns,
        )
        self._reservations.reserve(logical_operation_id, plan.intents)
        # No cooldown was consumed by failed economics/position/risk gates.
        # Reserve first; then throttle before awaiting the network dispatch.
        self._signal.record_submission(signal)
        self._attempted_orders += 1
        _LOG.info(
            "TAKE placing exchange=%s direction=%s ticks=%d qty=%d residual=%.4f pm_mid=%.4f",
            exchange_id,
            signal.direction,
            ticks,
            quantity,
            signal.residual,
            signal.pm_mid,
        )
        try:
            event = await self._dispatch(plan)
        except SigExecutionUncertainError as exc:
            # The reservation stays; BUILD-009 reconciliation owns the outcome.
            _LOG.warning("TAKE placement uncertain op=%s: %s", logical_operation_id, exc)
            return (
                ExecutionEvent(
                    logical_operation_id=logical_operation_id,
                    state=LifecycleState.UNCERTAIN,
                    observed_monotonic_ns=change.observed_monotonic_ns,
                    simulated=False,
                    detail=type(exc).__name__,
                ),
            )
        except SigApiError as exc:
            _LOG.info("TAKE rejected op=%s: %s", logical_operation_id, exc)
            return (
                ExecutionEvent(
                    logical_operation_id=logical_operation_id,
                    state=LifecycleState.REJECTED,
                    observed_monotonic_ns=change.observed_monotonic_ns,
                    simulated=False,
                    detail=type(exc).__name__,
                ),
            )
        _LOG.info("TAKE outcome op=%s state=%s", logical_operation_id, event.state.value)
        if redirect_from is not None and event.state is LifecycleState.FILLED:
            # The ordinary taker has genuinely filled the better alternative.
            # Move the investment target; the reconciled-position check will
            # verify the new market fill before calling the rotation complete.
            original = self._rotation_pending_entries.pop(redirect_from, None)
            if original is not None:
                signed_before = snapshot.runtime.portfolio.signed_inventory(
                    exchange_id, snapshot.tournament_id,
                )
                self._rotation_pending_entries[exchange_id] = replace(
                    original,
                    entry_exchange_id=exchange_id,
                    entry_direction=signal.direction,
                    entry_before_signed=int(signed_before),
                )
                _LOG.info(
                    "ROTATION capital redirected from=%s to=%s "
                    "improved_net_per_share=%.5f",
                    redirect_from, exchange_id, economics_net,
                )
                if self._request_rotation_recheck is not None:
                    self._request_rotation_recheck(frozenset({exchange_id}))
        events = [event]
        cancel_event = await self._cancel_remainder(plan, event, snapshot.tournament_id)
        if cancel_event is not None:
            events.append(cancel_event)
        return tuple(events)

    def _crosses_own_quote(self, exchange_id: str, action: OrderAction, ticks: int) -> bool:
        state = self._quotes.state(exchange_id)
        if action is OrderAction.BUY:
            return any(
                quote.side is QuoteSide.ASK and quote.price_ticks <= ticks
                for quote in state.all_quotes()
            )
        return any(
            quote.side is QuoteSide.BID and quote.price_ticks >= ticks
            for quote in state.all_quotes()
        )

    async def _cancel_remainder(
        self,
        plan: ExecutionPlan,
        event: ExecutionEvent,
        tournament_id: str,
    ) -> ExecutionEvent | None:
        operation_id = plan.envelope.logical_operation_id
        acks = [
            item
            for item in self._journal.events(operation_id)
            if item.event_type == "ACK" and item.exchange_order_id is not None
        ]
        if not acks:
            if event.state in _RESTING_STATES:
                _LOG.warning(
                    "TAKE remainder may rest without order id op=%s state=%s",
                    operation_id,
                    event.state.value,
                )
            return None
        latest = acks[-1]
        state = event.state
        if latest.terminal_status is not None:
            with suppress(ValueError):
                state = LifecycleState(latest.terminal_status)
        if state not in _RESTING_STATES:
            return None
        try:
            order_id = int(latest.exchange_order_id or "")
        except ValueError:
            return None
        if order_id <= 0:
            return None
        envelope = ExecutionEnvelope.cancellation(
            logical_operation_id=f"{operation_id}:take-cancel",
            operation_kind=OperationKind.SINGLE_CANCELLATION,
            sink_mode=ExecutionMode.LIVE,
            created_monotonic_ns=event.observed_monotonic_ns,
            order_id=order_id,
            tournament_id=tournament_id,
        )
        return await self._try_cancel(operation_id, envelope)

    async def _try_cancel(
        self,
        operation_id: str,
        envelope: ExecutionEnvelope,
    ) -> ExecutionEvent | None:
        self._pending_cancels[operation_id] = envelope
        for attempt in (1, 2):
            try:
                cancelled = await self._cancel(envelope)
            except SigExecutionUncertainError as exc:
                _LOG.warning(
                    "TAKE remainder cancel uncertain op=%s attempt=%d status=%s",
                    operation_id,
                    attempt,
                    exc.status_code,
                )
                if exc.status_code == 409:
                    # SIG says the order already closed (filled or cancelled).
                    self._pending_cancels.pop(operation_id, None)
                    return None
                continue
            except SigApiError as exc:
                _LOG.warning("TAKE remainder cancel rejected op=%s: %s", operation_id, exc)
                self._pending_cancels.pop(operation_id, None)
                return None
            self._pending_cancels.pop(operation_id, None)
            _LOG.info(
                "TAKE remainder cancel op=%s state=%s",
                operation_id,
                cancelled.state.value,
            )
            return cancelled
        return None
