"""Event-driven MAKE-001 coordinator layered on BUILD-009 risk/execution."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass

from predictions_cup.execution.models import ExecutionEvent, ExecutionMode, LifecycleState
from predictions_cup.execution.planner import build_execution_plan
from predictions_cup.execution.reservations import ExecutionReservationBook
from predictions_cup.execution.sinks import ExecutionPlan
from predictions_cup.maker.contracts import MakerDecision, MakerMarketSnapshot, QuoteSide
from predictions_cup.maker.engine import MakerEngine
from predictions_cup.maker.lifecycle import (
    ActiveQuote,
    QuoteLifecycleAction,
    QuoteLifecycleActionKind,
    QuoteLifecycleManager,
    QuoteRegistry,
)
from predictions_cup.risk.core import RiskContext, RiskDecision, evaluate_risk
from predictions_cup.runtime.models import OrderAction, OutcomeSide
from predictions_cup.strategy.core import CandidateLeg, Opportunity, StrategyFamily

PlacementDispatcher = Callable[
    [ExecutionPlan, MakerMarketSnapshot],
    Awaitable[ExecutionEvent],
]
CancelDispatcher = Callable[[ActiveQuote, str], Awaitable[ExecutionEvent]]


@dataclass(frozen=True, slots=True)
class MakerStateChange:
    event_id: str
    observed_monotonic_ns: int
    exchange_ids: frozenset[str]
    global_recheck: bool = False

    def __post_init__(self) -> None:
        if not self.event_id.strip():
            raise ValueError("maker event_id must not be blank")
        if self.observed_monotonic_ns < 0:
            raise ValueError("maker state-change timestamp must be non-negative")


@dataclass(frozen=True, slots=True)
class MakerCycleResult:
    decisions: tuple[MakerDecision, ...]
    lifecycle_actions: tuple[QuoteLifecycleAction, ...]
    risk_decisions: tuple[RiskDecision, ...]
    execution_events: tuple[ExecutionEvent, ...]


class MakerCoordinator:
    """Bounded event-driven shell: recompute only affected exchanges.

    The coordinator does not own market data. Callers provide explicit immutable
    MakerMarketSnapshot objects. No task-per-market polling is created.
    """

    def __init__(
        self,
        *,
        engine: MakerEngine,
        lifecycle: QuoteLifecycleManager,
        quote_registry: QuoteRegistry,
        risk_context: RiskContext,
        placement_dispatch: PlacementDispatcher,
        cancel_dispatch: CancelDispatcher,
        reservations: ExecutionReservationBook | None = None,
    ) -> None:
        if risk_context.mode is ExecutionMode.LIVE and reservations is None:
            raise ValueError("LIVE maker requires BUILD-009 execution reservations")
        self._engine = engine
        self._lifecycle = lifecycle
        self._registry = quote_registry
        self._risk_context = risk_context
        self._placement_dispatch = placement_dispatch
        self._cancel_dispatch = cancel_dispatch
        self._reservations = reservations

    async def on_state_change(
        self,
        change: MakerStateChange,
        snapshots: Mapping[str, MakerMarketSnapshot],
    ) -> MakerCycleResult:
        exchange_ids = (
            tuple(sorted(snapshots))
            if change.global_recheck
            else tuple(sorted(change.exchange_ids))
        )
        decisions: list[MakerDecision] = []
        actions: list[QuoteLifecycleAction] = []
        risk_decisions: list[RiskDecision] = []
        events: list[ExecutionEvent] = []

        for exchange_id in exchange_ids:
            snapshot = snapshots.get(exchange_id)
            if snapshot is None:
                continue
            decision = self._engine.quote(snapshot)
            decisions.append(decision)
            current = self._registry.state(exchange_id)
            force_cancel = self._risk_context.kill_switch
            desired = None if force_cancel else decision.desired
            side_actions = self._lifecycle.decide(
                desired=desired,
                current=current,
                now_monotonic_ns=change.observed_monotonic_ns,
                force_cancel=force_cancel,
            )
            actions.extend(side_actions)

            # Cancels always precede new placement. A cancel-for-replace is
            # intentionally two phase; this cycle never adds replacement exposure.
            cancelled_side = False
            for action in side_actions:
                if action.kind is not QuoteLifecycleActionKind.CANCEL:
                    continue
                active = action.active
                if active is None:
                    continue
                if active.exchange_order_id is None:
                    self._registry.mark_lifecycle(
                        exchange_id=exchange_id,
                        side=action.side,
                        lifecycle_state=LifecycleState.UNCERTAIN,
                        observed_monotonic_ns=change.observed_monotonic_ns,
                    )
                    cancelled_side = True
                    continue
                self._registry.mark_lifecycle(
                    exchange_id=exchange_id,
                    side=action.side,
                    lifecycle_state=LifecycleState.CANCEL_PENDING,
                    observed_monotonic_ns=change.observed_monotonic_ns,
                )
                logical_id = (
                    f"{change.event_id}:make-cancel:{exchange_id}:{action.side.value}"
                )
                try:
                    event = await self._cancel_dispatch(active, logical_id)
                except BaseException:
                    self._registry.mark_lifecycle(
                        exchange_id=exchange_id,
                        side=action.side,
                        lifecycle_state=LifecycleState.UNCERTAIN,
                        observed_monotonic_ns=change.observed_monotonic_ns,
                    )
                    raise
                events.append(event)
                if event.state is LifecycleState.CANCELLED:
                    self._registry.clear_side(
                        exchange_id=exchange_id,
                        side=action.side,
                        observed_monotonic_ns=event.observed_monotonic_ns,
                    )
                else:
                    self._registry.mark_lifecycle(
                        exchange_id=exchange_id,
                        side=action.side,
                        lifecycle_state=event.state,
                        observed_monotonic_ns=event.observed_monotonic_ns,
                    )
                cancelled_side = True

            if cancelled_side:
                continue

            place_actions = tuple(
                action
                for action in side_actions
                if action.kind is QuoteLifecycleActionKind.PLACE
            )
            if not place_actions or decision.desired is None:
                continue

            opportunity = self._opportunity(
                decision,
                snapshot,
                place_actions,
            )
            risk_snapshot = (
                snapshot.runtime
                if self._reservations is None
                else self._reservations.overlay_snapshot(snapshot.runtime)
            )
            risk = evaluate_risk(opportunity, risk_snapshot, self._risk_context)
            risk_decisions.append(risk)
            if not risk.approved:
                continue

            logical_operation_id = f"{change.event_id}:make-place:{exchange_id}"
            plan = build_execution_plan(
                risk,
                logical_operation_id=logical_operation_id,
                created_monotonic_ns=change.observed_monotonic_ns,
            )
            if self._reservations is not None:
                self._reservations.reserve(
                    logical_operation_id,
                    plan.intents,
                )

            # Local lifecycle reservation precedes network dispatch so duplicate
            # source events cannot stack fresh maker exposure.
            for action in place_actions:
                assert action.desired_ticks is not None
                self._registry.mark_submitted(
                    exchange_id=exchange_id,
                    side=action.side,
                    price_ticks=action.desired_ticks,
                    size=action.desired_size,
                    logical_operation_id=logical_operation_id,
                    observed_monotonic_ns=change.observed_monotonic_ns,
                )

            try:
                event = await self._placement_dispatch(plan, snapshot)
            except BaseException:
                # Terminal rejection releases BUILD-009 risk reservation. If the
                # reservation still exists the economic outcome is unresolved and
                # local lifecycle must remain blocked pending reconciliation.
                retained = (
                    self._reservations is not None
                    and self._reservations.contains_operation(
                        logical_operation_id,
                        plan.envelope.intent_ids,
                    )
                )
                if not retained:
                    for action in place_actions:
                        self._registry.clear_side(
                            exchange_id=exchange_id,
                            side=action.side,
                            observed_monotonic_ns=change.observed_monotonic_ns,
                        )
                raise

            events.append(event)
            if event.simulated:
                self._apply_shadow_event(
                    exchange_id=exchange_id,
                    logical_operation_id=logical_operation_id,
                    actions=place_actions,
                    event=event,
                )

        return MakerCycleResult(
            decisions=tuple(decisions),
            lifecycle_actions=tuple(actions),
            risk_decisions=tuple(risk_decisions),
            execution_events=tuple(events),
        )

    def _opportunity(
        self,
        decision: MakerDecision,
        snapshot: MakerMarketSnapshot,
        actions: tuple[QuoteLifecycleAction, ...],
    ) -> Opportunity:
        assert decision.desired is not None
        legs: list[CandidateLeg] = []
        edges: list[float] = []
        adjusted_fv = decision.trace.adjusted_fv
        if adjusted_fv is None:
            raise ValueError("approved maker quote requires adjusted fair value")

        for action in actions:
            ticks = action.desired_ticks
            if ticks is None or action.desired_size <= 0:
                continue
            if action.side is QuoteSide.BID:
                order_action = OrderAction.BUY
                edge = adjusted_fv - ticks * 0.005
            else:
                order_action = OrderAction.SELL
                edge = ticks * 0.005 - adjusted_fv
            legs.append(
                CandidateLeg(
                    exchange_id=snapshot.exchange_id,
                    market_id=snapshot.market_id,
                    tournament_id=snapshot.tournament_id,
                    outcome_side=OutcomeSide.YES,
                    action=order_action,
                    quantity=action.desired_size,
                    limit_price_ticks=ticks,
                )
            )
            edges.append(edge)

        if not legs:
            raise ValueError("maker placement requires at least one quote leg")
        return Opportunity(
            family=StrategyFamily.MAKE,
            strategy_id=self._engine.strategy_id,
            legs=tuple(legs),
            gross_edge=min(edges),
            fair_value=adjusted_fv,
            decision_observation_ns=snapshot.now_monotonic_ns,
            requires_trusted_depth=False,
        )

    def _apply_shadow_event(
        self,
        *,
        exchange_id: str,
        logical_operation_id: str,
        actions: tuple[QuoteLifecycleAction, ...],
        event: ExecutionEvent,
    ) -> None:
        if event.state is LifecycleState.FILLED:
            for action in actions:
                self._registry.clear_side(
                    exchange_id=exchange_id,
                    side=action.side,
                    observed_monotonic_ns=event.observed_monotonic_ns,
                )
            return
        if event.state not in {LifecycleState.OPEN, LifecycleState.PARTIALLY_FILLED}:
            return
        for action in actions:
            assert action.desired_ticks is not None
            self._registry.apply_authoritative(
                exchange_id=exchange_id,
                side=action.side,
                price_ticks=action.desired_ticks,
                size=action.desired_size,
                remaining_size=action.desired_size,
                logical_operation_id=logical_operation_id,
                # Synthetic local identity only; Shadow cancel adapters must never
                # route this into the real SIG write client.
                exchange_order_id=0,
                lifecycle_state=event.state,
                observed_monotonic_ns=event.observed_monotonic_ns,
            )
