from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping

from predictions_cup.execution.models import ExecutionEvent, ExecutionMode, LifecycleState
from predictions_cup.execution.reservations import ExecutionReservationBook
from predictions_cup.execution.sinks import ExecutionPlan
from predictions_cup.risk.core import RiskContext, RiskLimits
from predictions_cup.runtime import (
    OrderAction,
    OutcomeSide,
    RuntimeMarket,
    RuntimePortfolio,
    RuntimeSnapshot,
)
from predictions_cup.runtime.dispatcher import (
    EventDrivenCoordinator,
    StateChange,
    StrategyBinding,
)
from predictions_cup.runtime.engine import DecisionRuntime
from predictions_cup.strategy.core import (
    CandidateLeg,
    NoTrade,
    Opportunity,
    StrategyFamily,
    StrategyRegistry,
    StrategyResult,
)
from predictions_cup.strategy.kernels import KernelRegistry, default_kernel_registry


def test_event_driven_coordinator_evaluates_only_affected_strategy() -> None:
    calls = {"a": 0, "b": 0}
    strategies = StrategyRegistry()

    def strategy_a(
        snapshot: RuntimeSnapshot,
        kernels: KernelRegistry,
        config: Mapping[str, float],
    ) -> StrategyResult:
        del snapshot, kernels, config
        calls["a"] += 1
        return NoTrade(reason="fixture-a")

    def strategy_b(
        snapshot: RuntimeSnapshot,
        kernels: KernelRegistry,
        config: Mapping[str, float],
    ) -> StrategyResult:
        del snapshot, kernels, config
        calls["b"] += 1
        return NoTrade(reason="fixture-b")

    strategies.register("a", strategy_a)
    strategies.register("b", strategy_b)
    runtime = DecisionRuntime(
        strategies=strategies,
        kernels=default_kernel_registry(),
    )
    snapshot = RuntimeSnapshot(
        markets=(),
        books=(),
        portfolio=RuntimePortfolio(account_trusted=False),
        observation_monotonic_ns=10,
    )

    async def dispatch(
        plan: ExecutionPlan,
        state: RuntimeSnapshot,
    ) -> ExecutionEvent:
        del plan, state
        raise AssertionError("NO_TRADE must not reach the sink")

    coordinator = EventDrivenCoordinator(
        runtime=runtime,
        bindings=(
            StrategyBinding(
                strategy_id="a",
                config={},
                exchange_ids=frozenset({"36"}),
            ),
            StrategyBinding(
                strategy_id="b",
                config={},
                exchange_ids=frozenset({"37"}),
            ),
        ),
        risk_context=RiskContext(
            mode=ExecutionMode.SHADOW,
            kill_switch=False,
            limits=None,
            max_state_age_ns=1_000,
        ),
        dispatch=dispatch,
    )

    async def scenario() -> None:
        events = await coordinator.on_state_change(
            StateChange(
                event_id="market-batch-1",
                observed_monotonic_ns=10,
                exchange_ids=frozenset({"36"}),
            ),
            snapshot,
        )
        assert events == ()

    asyncio.run(scenario())
    assert calls == {"a": 1, "b": 0}


def test_explicit_scheduled_trigger_works_without_market_change() -> None:
    calls = 0
    strategies = StrategyRegistry()

    def scheduled(
        snapshot: RuntimeSnapshot,
        kernels: KernelRegistry,
        config: Mapping[str, float],
    ) -> StrategyResult:
        nonlocal calls
        del snapshot, kernels, config
        calls += 1
        return NoTrade(reason="scheduled")

    strategies.register("scheduled", scheduled)

    async def dispatch(
        plan: ExecutionPlan,
        state: RuntimeSnapshot,
    ) -> ExecutionEvent:
        del plan, state
        raise AssertionError("NO_TRADE must not reach the sink")

    coordinator = EventDrivenCoordinator(
        runtime=DecisionRuntime(
            strategies=strategies,
            kernels=default_kernel_registry(),
        ),
        bindings=(StrategyBinding(strategy_id="scheduled", config={}),),
        risk_context=RiskContext(
            mode=ExecutionMode.SHADOW,
            kill_switch=False,
            limits=None,
            max_state_age_ns=1_000,
        ),
        dispatch=dispatch,
    )
    snapshot = RuntimeSnapshot(
        markets=(),
        books=(),
        portfolio=RuntimePortfolio(account_trusted=False),
        observation_monotonic_ns=11,
    )

    asyncio.run(
        coordinator.on_state_change(
            StateChange(
                event_id="scheduled-1",
                observed_monotonic_ns=11,
                scheduled_strategy_ids=frozenset({"scheduled"}),
            ),
            snapshot,
        )
    )
    assert calls == 1

def _live_limits() -> RiskLimits:
    return RiskLimits(
        max_order_size=1,
        max_gross_exposure=1.0,
        max_per_market_exposure=1.0,
        max_open_order_exposure=1.0,
        max_concurrent_open_orders=1,
    )


def _live_snapshot() -> RuntimeSnapshot:
    return RuntimeSnapshot(
        markets=(
            RuntimeMarket(
                market_id="m1",
                status="open",
                exchange_ids=("36",),
                tournament_id="t1",
                mapping_accepted=True,
                tradeable=True,
            ),
        ),
        books=(),
        portfolio=RuntimePortfolio(account_trusted=True),
        observation_monotonic_ns=100,
    )


def _opportunity(strategy_id: str) -> Opportunity:
    return Opportunity(
        family=StrategyFamily.FV_TAKE,
        strategy_id=strategy_id,
        legs=(
            CandidateLeg(
                exchange_id="36",
                market_id="m1",
                tournament_id="t1",
                outcome_side=OutcomeSide.YES,
                action=OrderAction.BUY,
                quantity=1,
                limit_price_ticks=100,
            ),
        ),
        gross_edge=0.02,
        fair_value=0.55,
        decision_observation_ns=100,
    )


def test_live_same_state_change_reserves_first_approval_before_second_risk_check() -> None:
    strategies = StrategyRegistry()

    def make_strategy(
        strategy_id: str,
    ) -> Callable[
        [RuntimeSnapshot, KernelRegistry, Mapping[str, float]],
        StrategyResult,
    ]:
        def strategy(
            snapshot: RuntimeSnapshot,
            kernels: KernelRegistry,
            config: Mapping[str, float],
        ) -> StrategyResult:
            del snapshot, kernels, config
            return _opportunity(strategy_id)

        return strategy

    strategies.register("a", make_strategy("a"))
    strategies.register("b", make_strategy("b"))
    reservations = ExecutionReservationBook()
    dispatched: list[str] = []

    async def dispatch(
        plan: ExecutionPlan,
        state: RuntimeSnapshot,
    ) -> ExecutionEvent:
        del state
        dispatched.append(plan.envelope.logical_operation_id)
        return ExecutionEvent(
            logical_operation_id=plan.envelope.logical_operation_id,
            state=LifecycleState.ACKED,
            observed_monotonic_ns=101,
            simulated=False,
        )

    coordinator = EventDrivenCoordinator(
        runtime=DecisionRuntime(
            strategies=strategies,
            kernels=default_kernel_registry(),
        ),
        bindings=(
            StrategyBinding("a", {}, market_ids=frozenset({"m1"})),
            StrategyBinding("b", {}, market_ids=frozenset({"m1"})),
        ),
        risk_context=RiskContext(
            mode=ExecutionMode.LIVE,
            kill_switch=False,
            limits=_live_limits(),
            max_state_age_ns=1_000,
        ),
        dispatch=dispatch,
        reservations=reservations,
    )

    events = asyncio.run(
        coordinator.on_state_change(
            StateChange(
                event_id="e1",
                observed_monotonic_ns=100,
                market_ids=frozenset({"m1"}),
            ),
            _live_snapshot(),
        )
    )

    assert len(events) == 1
    assert dispatched == ["e1:a"]
    assert reservations.intent_ids() == frozenset({"a:100:0"})


def test_live_duplicate_intent_different_event_id_is_not_dispatched_twice() -> None:
    strategies = StrategyRegistry()

    def strategy(
        snapshot: RuntimeSnapshot,
        kernels: KernelRegistry,
        config: Mapping[str, float],
    ) -> StrategyResult:
        del snapshot, kernels, config
        return _opportunity("a")

    strategies.register("a", strategy)
    reservations = ExecutionReservationBook()
    dispatched: list[str] = []

    async def dispatch(
        plan: ExecutionPlan,
        state: RuntimeSnapshot,
    ) -> ExecutionEvent:
        del state
        dispatched.append(plan.envelope.logical_operation_id)
        return ExecutionEvent(
            logical_operation_id=plan.envelope.logical_operation_id,
            state=LifecycleState.ACKED,
            observed_monotonic_ns=101,
            simulated=False,
        )

    coordinator = EventDrivenCoordinator(
        runtime=DecisionRuntime(
            strategies=strategies,
            kernels=default_kernel_registry(),
        ),
        bindings=(StrategyBinding("a", {}, market_ids=frozenset({"m1"})),),
        risk_context=RiskContext(
            mode=ExecutionMode.LIVE,
            kill_switch=False,
            limits=RiskLimits(
                max_order_size=10,
                max_gross_exposure=10.0,
                max_per_market_exposure=10.0,
                max_open_order_exposure=10.0,
                max_concurrent_open_orders=10,
            ),
            max_state_age_ns=1_000,
        ),
        dispatch=dispatch,
        reservations=reservations,
    )
    snapshot = _live_snapshot()

    async def scenario() -> None:
        await coordinator.on_state_change(
            StateChange("e1", 100, market_ids=frozenset({"m1"})),
            snapshot,
        )
        second = await coordinator.on_state_change(
            StateChange("e2", 101, market_ids=frozenset({"m1"})),
            snapshot,
        )
        assert second == ()

    asyncio.run(scenario())
    assert dispatched == ["e1:a"]

