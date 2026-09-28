from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from predictions_cup.config import AppSettings
from predictions_cup.execution import (
    ExecutionEnvelope,
    ExecutionJournal,
    ExecutionMode,
    LifecycleState,
    OperationKind,
    RuntimeOrderIntent,
    ShadowSink,
)
from predictions_cup.execution.planner import deterministic_idempotency_key
from predictions_cup.execution.sinks import ExecutionPlan
from predictions_cup.risk import RiskContext, RiskLimits
from predictions_cup.runtime import (
    DecisionRuntime,
    OrderAction,
    OutcomeSide,
    RuntimeBook,
    RuntimeLevel,
    RuntimeMarket,
    RuntimePortfolio,
    RuntimeSnapshot,
    limit_price_to_ticks,
    ticks_to_limit_price,
)
from predictions_cup.strategy import StrategyRegistry, default_kernel_registry
from predictions_cup.strategy.core import synthetic_threshold_strategy


def _snapshot(
    *,
    account_trusted: bool = True,
    trusted_depth: bool = True,
    mapping_accepted: bool = True,
) -> RuntimeSnapshot:
    market = RuntimeMarket(
        market_id="26",
        status="open",
        exchange_ids=("36",),
        tournament_id="tournament-1",
        mapping_accepted=mapping_accepted,
        tradeable=True,
    )
    book = RuntimeBook(
        exchange_id="36",
        market_id="26",
        tournament_id="tournament-1",
        bids=(RuntimeLevel(price_ticks=98, quantity=10.0),),
        asks=(RuntimeLevel(price_ticks=102, quantity=10.0),),
        trusted_depth=trusted_depth,
        observed_monotonic_ns=1_000,
    )
    return RuntimeSnapshot(
        markets=(market,),
        books=(book,),
        portfolio=RuntimePortfolio(account_trusted=account_trusted),
        observation_monotonic_ns=1_100,
    )


def _limits() -> RiskLimits:
    return RiskLimits(
        max_order_size=10,
        max_gross_exposure=100.0,
        max_per_market_exposure=50.0,
        max_open_order_exposure=50.0,
        max_concurrent_open_orders=10,
    )


def _runtime() -> DecisionRuntime:
    strategies = StrategyRegistry()
    strategies.register("synthetic-threshold", synthetic_threshold_strategy)
    return DecisionRuntime(
        strategies=strategies,
        kernels=default_kernel_registry(),
    )


def test_shadow_is_default_and_live_requires_all_explicit_gates() -> None:
    default = AppSettings()
    assert default.execution_mode == "SHADOW"
    assert default.trading_enabled is False
    assert default.global_kill_switch is True

    with pytest.raises(ValidationError, match="trading_enabled"):
        AppSettings(execution_mode="LIVE")

    with pytest.raises(ValidationError, match="every central risk cap"):
        AppSettings(
            execution_mode="LIVE",
            trading_enabled=True,
            sig_trade_credential=SecretStr("trade-secret"),
            tournament_id="tournament-1",
            tournament_slug="cup",
        )

    live = AppSettings(
        execution_mode="LIVE",
        trading_enabled=True,
        sig_trade_credential=SecretStr("trade-secret"),
        tournament_id="tournament-1",
        tournament_slug="cup",
        risk_max_order_size=10,
        risk_max_gross_exposure=100.0,
        risk_max_per_market_exposure=50.0,
        risk_max_open_order_exposure=50.0,
        risk_max_concurrent_open_orders=10,
    )
    assert live.execution_mode == "LIVE"
    assert live.global_kill_switch is True


def test_exact_sig_tick_boundary_round_trip() -> None:
    assert limit_price_to_ticks(Decimal("0.005")) == 1
    assert limit_price_to_ticks(Decimal("0.995")) == 199
    assert ticks_to_limit_price(84) == Decimal("0.420")
    with pytest.raises(ValueError, match="0.005 tick"):
        limit_price_to_ticks(Decimal("0.421"))
    with pytest.raises(ValueError, match="between 1 and 199"):
        ticks_to_limit_price(200)


def test_kernel_registry_reference_candidates_are_equivalent() -> None:
    kernels = default_kernel_registry()
    for probability in (0.01, 0.1, 0.42, 0.5, 0.9, 0.99):
        ratio = kernels.run("M-038", "ratio", probability)
        log1p = kernels.run("M-038", "log1p", probability)
        assert ratio == pytest.approx(log1p, rel=1e-14, abs=1e-14)

    exact = kernels.run("M-041", "stable", 0.42, 0.1, 2.0)
    naive = kernels.run("M-041", "naive", 0.42, 0.1, 2.0)
    assert exact == pytest.approx(naive, rel=1e-14, abs=1e-14)

    first_order = kernels.run("M-042", "python", 0.42, 0.1, 2.0)
    assert 0.0 <= first_order <= 1.0
    assert first_order != exact


def test_same_strategy_path_produces_same_plan_identity_in_shadow_and_live() -> None:
    runtime = _runtime()
    snapshot = _snapshot(account_trusted=True)

    shadow = runtime.decide(
        strategy_id="synthetic-threshold",
        snapshot=snapshot,
        strategy_config={"edge": 0.03, "threshold": 0.01},
        risk_context=RiskContext(
            mode=ExecutionMode.SHADOW,
            kill_switch=False,
            limits=_limits(),
            max_state_age_ns=1_000_000,
        ),
        logical_operation_id="decision-1",
        mode=ExecutionMode.SHADOW,
        created_monotonic_ns=2_000,
    )
    live = runtime.decide(
        strategy_id="synthetic-threshold",
        snapshot=snapshot,
        strategy_config={"edge": 0.03, "threshold": 0.01},
        risk_context=RiskContext(
            mode=ExecutionMode.LIVE,
            kill_switch=False,
            limits=_limits(),
            max_state_age_ns=1_000_000,
        ),
        logical_operation_id="decision-1",
        mode=ExecutionMode.LIVE,
        created_monotonic_ns=2_000,
    )

    assert shadow.execution_plan is not None
    assert live.execution_plan is not None
    assert shadow.execution_plan.intents == live.execution_plan.intents
    assert (
        shadow.execution_plan.envelope.idempotency_key
        == live.execution_plan.envelope.idempotency_key
    )
    assert (
        shadow.execution_plan.envelope.payload_json
        == live.execution_plan.envelope.payload_json
    )


def test_live_risk_fails_closed_for_account_mapping_and_kill_switch() -> None:
    runtime = _runtime()

    untrusted = runtime.decide(
        strategy_id="synthetic-threshold",
        snapshot=_snapshot(account_trusted=False),
        strategy_config={"edge": 0.03, "threshold": 0.01},
        risk_context=RiskContext(
            mode=ExecutionMode.LIVE,
            kill_switch=False,
            limits=_limits(),
            max_state_age_ns=1_000_000,
        ),
        logical_operation_id="untrusted",
        mode=ExecutionMode.LIVE,
        created_monotonic_ns=2_000,
    )
    assert untrusted.execution_plan is None
    assert untrusted.risk_decision.reason == "account_state_untrusted"

    unmapped = runtime.decide(
        strategy_id="synthetic-threshold",
        snapshot=_snapshot(mapping_accepted=False),
        strategy_config={"edge": 0.03, "threshold": 0.01},
        risk_context=RiskContext(
            mode=ExecutionMode.LIVE,
            kill_switch=False,
            limits=_limits(),
            max_state_age_ns=1_000_000,
        ),
        logical_operation_id="unmapped",
        mode=ExecutionMode.LIVE,
        created_monotonic_ns=2_000,
    )
    assert unmapped.execution_plan is None
    assert unmapped.risk_decision.reason == "mapping_or_tradeability_not_accepted"

    killed = runtime.decide(
        strategy_id="synthetic-threshold",
        snapshot=_snapshot(),
        strategy_config={"edge": 0.03, "threshold": 0.01},
        risk_context=RiskContext(
            mode=ExecutionMode.LIVE,
            kill_switch=True,
            limits=_limits(),
            max_state_age_ns=1_000_000,
        ),
        logical_operation_id="killed",
        mode=ExecutionMode.LIVE,
        created_monotonic_ns=2_000,
    )
    assert killed.execution_plan is None
    assert killed.risk_decision.reason == "global_kill_switch"


def test_journal_persists_identity_and_rejects_mutation(tmp_path: Path) -> None:
    path = tmp_path / "journal.sqlite3"
    journal = ExecutionJournal(path)
    try:
        intent = RuntimeOrderIntent(
            intent_id="intent-1",
            exchange_id="36",
            market_id="26",
            tournament_id="tournament-1",
            outcome_side=OutcomeSide.YES,
            action=OrderAction.BUY,
            quantity=1,
            limit_price_ticks=84,
            strategy_id="synthetic",
            decision_observation_ns=1,
        )
        envelope = ExecutionEnvelope.placement(
            logical_operation_id="op-1",
            operation_kind=OperationKind.SINGLE_PLACEMENT,
            sink_mode=ExecutionMode.LIVE,
            idempotency_key="key-1",
            intents=(intent,),
            created_monotonic_ns=1,
        )
        journal.record_before_dispatch(envelope)
        journal.record_before_dispatch(envelope)
        assert journal.unresolved()[0].payload_sha256 == envelope.payload_sha256

        mutated = ExecutionEnvelope.placement(
            logical_operation_id="op-1",
            operation_kind=OperationKind.SINGLE_PLACEMENT,
            sink_mode=ExecutionMode.LIVE,
            idempotency_key="key-1",
            intents=(
                RuntimeOrderIntent(
                    intent_id="intent-1",
                    exchange_id="36",
                    market_id="26",
                    tournament_id="tournament-1",
                    outcome_side=OutcomeSide.YES,
                    action=OrderAction.BUY,
                    quantity=2,
                    limit_price_ticks=84,
                    strategy_id="synthetic",
                    decision_observation_ns=1,
                ),
            ),
            created_monotonic_ns=1,
        )
        with pytest.raises(ValueError, match="cannot be reused"):
            journal.record_before_dispatch(mutated)

        journal.mark_state("op-1", LifecycleState.OPEN, 2)
        with pytest.raises(ValueError, match="invalid lifecycle transition"):
            journal.mark_state("op-1", LifecycleState.REJECTED, 3)
    finally:
        journal.close()


def test_shadow_sink_only_fills_executable_crosses() -> None:
    snapshot = _snapshot()
    intent = RuntimeOrderIntent(
        intent_id="intent-1",
        exchange_id="36",
        market_id="26",
        tournament_id="tournament-1",
        outcome_side=OutcomeSide.YES,
        action=OrderAction.BUY,
        quantity=1,
        limit_price_ticks=102,
        strategy_id="synthetic",
        decision_observation_ns=1,
    )
    envelope = ExecutionEnvelope.placement(
        logical_operation_id="shadow-1",
        operation_kind=OperationKind.SINGLE_PLACEMENT,
        sink_mode=ExecutionMode.SHADOW,
        idempotency_key=deterministic_idempotency_key(
            "shadow-1",
            OperationKind.SINGLE_PLACEMENT.value,
        ),
        intents=(intent,),
        created_monotonic_ns=1,
    )
    event = ShadowSink(clock_ns=lambda: 2).dispatch(
        ExecutionPlan(envelope=envelope, intents=(intent,)),
        snapshot,
    )
    assert event.state is LifecycleState.FILLED

    passive = RuntimeOrderIntent(
        intent_id="intent-2",
        exchange_id="36",
        market_id="26",
        tournament_id="tournament-1",
        outcome_side=OutcomeSide.YES,
        action=OrderAction.BUY,
        quantity=1,
        limit_price_ticks=100,
        strategy_id="synthetic",
        decision_observation_ns=1,
    )
    passive_envelope = ExecutionEnvelope.placement(
        logical_operation_id="shadow-2",
        operation_kind=OperationKind.SINGLE_PLACEMENT,
        sink_mode=ExecutionMode.SHADOW,
        idempotency_key="shadow-2",
        intents=(passive,),
        created_monotonic_ns=1,
    )
    passive_event = ShadowSink(clock_ns=lambda: 3).dispatch(
        ExecutionPlan(envelope=passive_envelope, intents=(passive,)),
        snapshot,
    )
    assert passive_event.state is LifecycleState.OPEN
