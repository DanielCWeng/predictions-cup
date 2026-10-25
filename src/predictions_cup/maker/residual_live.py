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
from predictions_cup.maker.residual_taker import (
    COOLDOWN_NS,
    MIN_PM_DEPTH,
    PM_SPREAD_CAP,
    STRATEGY_ID,
    THRESHOLD,
    ResidualSignal,
    ResidualTakerSignal,
    residual_input_from_snapshot,
)
from predictions_cup.maker.safety import MakerKillSwitch
from predictions_cup.maker.taker_economics import evaluate_taker_economics
from predictions_cup.mapping.models import MappingDocument
from predictions_cup.risk.core import RiskContext, evaluate_risk
from predictions_cup.runtime.models import OrderAction, OutcomeSide, RuntimePortfolio
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
        max_fair_age_ns: int = 5_000_000_000,
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
        self._max_fair_age_ns = max_fair_age_ns
        self._allow_bbo_proxy = allow_bbo_proxy
        # Remainder cancels that did not conclude; retried every cycle so a
        # taker order is never left resting at a stale price.
        self._pending_cancels: dict[str, ExecutionEnvelope] = {}
        self._evaluation_cycles = 0
        self._evaluated_markets = 0
        self._input_ready_markets = 0
        self._signals = 0
        self._attempted_orders = 0
        self._skipped_signals: Counter[str] = Counter()
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
            "TAKE heartbeat evaluated=%d input_ready=%d signals=%d attempts=%d cycles=%d skipped=%s",
            self._evaluated_markets,
            self._input_ready_markets,
            self._signals,
            self._attempted_orders,
            self._evaluation_cycles,
            dict(self._skipped_signals),
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
                        "TAKE liveness stalled evaluated=%d input_ready=%d signals=%d cycles=%d",
                        self._evaluated_markets,
                        self._input_ready_markets,
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
        for exchange_id in sorted(snapshots):
            if self._kill_switch.active:
                break
            if exchange_id not in self._signal.tracked_exchange_ids:
                continue
            self._evaluated_markets += 1
            snapshot = snapshots[exchange_id]
            sig_bbo_age = snapshot.now_monotonic_ns - snapshot.sig_bbo_observed_ns
            bbo_proxy = (
                not snapshot.sig_bbo_trusted
                or sig_bbo_age < 0
                or sig_bbo_age >= self._max_sig_bbo_age_ns
            )
            state = residual_input_from_snapshot(
                snapshot,
                self._mapping.get(exchange_id),
                max_pm_book_age_ns=self._max_pm_book_age_ns,
                observed_monotonic_ns=snapshot.now_monotonic_ns,
                allow_bbo_proxy=self._allow_bbo_proxy,
                threshold=self._threshold,
            )
            if state is None or not (self._min_fair_value <= state.pm_mid <= self._max_fair_value):
                continue
            self._input_ready_markets += 1
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
        if self._economics_gate_enabled:
            if not (snapshot.sig_depth_trusted and book.trusted_depth):
                self._skipped_signals["economics_depth_untrusted"] += 1
                return ()
            depth_ns = snapshot.sig_depth_observed_ns
            if (
                depth_ns is None
                or snapshot.now_monotonic_ns < depth_ns
                or snapshot.now_monotonic_ns - depth_ns > self._max_sig_depth_age_ns
            ):
                self._skipped_signals["economics_stale_data"] += 1
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
                max_book_age_ns=self._max_sig_depth_age_ns,
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
