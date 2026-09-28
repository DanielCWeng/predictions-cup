from __future__ import annotations

import asyncio
from collections.abc import Mapping

from predictions_cup.execution.models import ExecutionMode
from predictions_cup.execution.sinks import ExecutionPlan
from predictions_cup.risk.core import RiskContext
from predictions_cup.runtime import RuntimePortfolio, RuntimeSnapshot
from predictions_cup.runtime.dispatcher import (
    EventDrivenCoordinator,
    StateChange,
    StrategyBinding,
)
from predictions_cup.runtime.engine import DecisionRuntime
from predictions_cup.strategy.core import NoTrade, StrategyRegistry, StrategyResult
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
    ) -> object:
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
        mode=ExecutionMode.SHADOW,
        dispatch=dispatch,  # type: ignore[arg-type]
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
    ) -> object:
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
        mode=ExecutionMode.SHADOW,
        dispatch=dispatch,  # type: ignore[arg-type]
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
