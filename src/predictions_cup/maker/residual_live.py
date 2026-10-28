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
from dataclasses import replace
from datetime import datetime

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
from predictions_cup.maker.contracts import MakerMarketSnapshot, QuoteSide
from predictions_cup.maker.coordinator import MakerStateChange
from predictions_cup.maker.lifecycle import QuoteRegistry
from predictions_cup.maker.liquidity_allocator import LiquidityWeightedAllocator
from predictions_cup.maker.rotation_recovery import RotationEvidence, RotationEvidenceCache
from predictions_cup.maker.residual_taker import (
    COOLDOWN_NS,
    MIN_PM_DEPTH,
    PM_SPREAD_CAP,
    STRATEGY_ID,
    THRESHOLD,
    ResidualSignal,
    ResidualTakerSignal,
    SIG_TICK_PROBABILITY,
    residual_input_from_snapshot,
)
from predictions_cup.maker.safety import MakerKillSwitch
from predictions_cup.maker.taker_economics import evaluate_taker_economics
from predictions_cup.mapping.models import (
    MappingClass, MappingDirection, MappingDocument, MappingStatus, MarketMapping,
)
from predictions_cup.risk.core import RiskContext, evaluate_risk
from predictions_cup.runtime.models import AccountTrustGrade, OrderAction, OutcomeSide, RuntimePortfolio
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
            "TAKE heartbeat evaluated=%d candidate_ready=%d no_residual=%d signals=%d attempts=%d cycles=%d skipped=%s depth_windows=%s",
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
        if not trusted or not allocator.entries or not allocator.exits:
            if self._rotation_reduce_only_targets is not None:
                self._rotation_reduce_only_targets(frozenset(), now_ns)
            return
        if now_ns - self._rotation_last_rank_ns < 500_000_000:
            return
        self._rotation_last_rank_ns = now_ns
        ranked = allocator.rank(now_ns=now_ns, limit=3)
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
                        "TAKE liveness stalled evaluated=%d candidate_ready=%d signals=%d cycles=%d",
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
            levels = book.asks if action is OrderAction.BUY else book.bids
            economics = evaluate_taker_economics(
                direction=signal.direction,
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
