from __future__ import annotations

import ast
import json
from collections.abc import Mapping
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import SecretStr, ValidationError

from predictions_cup.config import AppSettings
from predictions_cup.execution.interlocks import LiveInterlockError, assert_live_interlocks
from predictions_cup.execution.models import (
    ExecutionEnvelope,
    ExecutionMode,
    LifecycleState,
    OperationKind,
    RuntimeOrderIntent,
)
from predictions_cup.execution.planner import build_execution_plan
from predictions_cup.execution.reservations import ExecutionReservationBook
from predictions_cup.execution.sinks import ExecutionPlan, ShadowSink
from predictions_cup.risk.core import (
    RiskContext,
    RiskLimits,
    evaluate_risk,
)
from predictions_cup.runtime import (
    OrderAction,
    OutcomeSide,
    RuntimeBook,
    RuntimeLevel,
    RuntimeMarket,
    RuntimeOrderState,
    RuntimePortfolio,
    RuntimeSnapshot,
    limit_price_to_ticks,
    ticks_to_limit_price,
)
from predictions_cup.runtime.telemetry import HotPathTelemetry
from predictions_cup.sig.trading_dto import (
    BatchOrderRequestDto,
    MultiLegOrderRequestDto,
    SingleOrderRequestDto,
)
from predictions_cup.strategy.core import (
    CandidateLeg,
    NoTrade,
    Opportunity,
    StrategyFamily,
    StrategyRegistry,
)
from predictions_cup.strategy.kernels import KernelRegistry, default_kernel_registry


def _snapshot(
    *,
    account_trusted: bool = True,
    trusted_depth: bool = True,
    observed_ns: int = 1_000_000,
    orders: tuple[RuntimeOrderState, ...] = (),
) -> RuntimeSnapshot:
    return RuntimeSnapshot(
        markets=(
            RuntimeMarket(
                market_id="m1",
                status="open",
                exchange_ids=("36", "37"),
                tournament_id="t1",
                mapping_accepted=True,
                tradeable=True,
            ),
        ),
        books=(
            RuntimeBook(
                exchange_id="36",
                market_id="m1",
                tournament_id="t1",
                bids=(RuntimeLevel(price_ticks=99, quantity=20.0),),
                asks=(RuntimeLevel(price_ticks=101, quantity=20.0),),
                trusted_depth=trusted_depth,
                observed_monotonic_ns=observed_ns,
            ),
        ),
        portfolio=RuntimePortfolio(
            orders=orders,
            account_trusted=account_trusted,
        ),
        observation_monotonic_ns=observed_ns,
    )


def _opportunity(
    *,
    quantity: int = 1,
    atomic: bool = False,
    relationship_id: str | None = None,
    requires_depth: bool = False,
) -> Opportunity:
    legs: tuple[CandidateLeg, ...] = (
        CandidateLeg(
            exchange_id="36",
            market_id="m1",
            tournament_id="t1",
            outcome_side=OutcomeSide.YES,
            action=OrderAction.BUY,
            quantity=quantity,
            limit_price_ticks=100,
        ),
    )
    if atomic:
        legs = legs + (
            CandidateLeg(
                exchange_id="37",
                market_id="m1",
                tournament_id="t1",
                outcome_side=OutcomeSide.NO,
                action=OrderAction.BUY,
                quantity=quantity,
                limit_price_ticks=100,
            ),
        )
    return Opportunity(
        family=StrategyFamily.STRUCT if relationship_id is not None else StrategyFamily.FV_TAKE,
        strategy_id="test-strategy",
        legs=legs,
        gross_edge=0.02,
        fair_value=0.55,
        decision_observation_ns=1_000_000,
        requires_trusted_depth=requires_depth,
        atomic=atomic,
        relationship_id=relationship_id,
    )


def _limits(**overrides: float | int) -> RiskLimits:
    values: dict[str, float | int] = {
        "max_order_size": 10,
        "max_gross_exposure": 100.0,
        "max_per_market_exposure": 100.0,
        "max_open_order_exposure": 100.0,
        "max_concurrent_open_orders": 10,
    }
    values.update(overrides)
    return RiskLimits(
        max_order_size=int(values["max_order_size"]),
        max_gross_exposure=float(values["max_gross_exposure"]),
        max_per_market_exposure=float(values["max_per_market_exposure"]),
        max_open_order_exposure=float(values["max_open_order_exposure"]),
        max_concurrent_open_orders=int(values["max_concurrent_open_orders"]),
    )


def test_all_legal_sig_ticks_round_trip_exactly() -> None:
    for ticks in range(1, 200):
        price = ticks_to_limit_price(ticks)
        assert limit_price_to_ticks(price) == ticks
        assert price == Decimal(ticks) * Decimal("0.005")

    with pytest.raises(ValueError):
        limit_price_to_ticks(Decimal("0.501"))
    with pytest.raises(ValueError):
        ticks_to_limit_price(0)
    with pytest.raises(ValueError):
        ticks_to_limit_price(200)


def test_strategy_extension_does_not_touch_execution_core() -> None:
    registry = StrategyRegistry()

    def extra_strategy(
        snapshot: RuntimeSnapshot,
        kernels: KernelRegistry,
        config: Mapping[str, float],
    ) -> NoTrade:
        del snapshot, kernels, config
        return NoTrade(reason="fixture")

    registry.register("extra", extra_strategy)
    result = registry.evaluate("extra", _snapshot(), default_kernel_registry(), {})
    assert isinstance(result, NoTrade)
    assert result.family is StrategyFamily.NO_TRADE


def test_risk_fails_closed_on_live_limits_and_account_trust() -> None:
    decision = evaluate_risk(
        _opportunity(),
        _snapshot(account_trusted=False),
        RiskContext(
            mode=ExecutionMode.LIVE,
            kill_switch=False,
            limits=None,
            max_state_age_ns=1_000_000,
        ),
    )
    assert decision.approved is False
    assert decision.reason == "live_limits_not_configured"

    decision = evaluate_risk(
        _opportunity(),
        _snapshot(account_trusted=False),
        RiskContext(
            mode=ExecutionMode.LIVE,
            kill_switch=False,
            limits=_limits(),
            max_state_age_ns=1_000_000,
        ),
    )
    assert decision.approved is False
    assert decision.reason == "account_state_untrusted"


def test_mixed_tournament_operation_fails_closed() -> None:
    snapshot = RuntimeSnapshot(
        markets=(
            RuntimeMarket(
                market_id="m1",
                status="open",
                exchange_ids=("36",),
                tournament_id="t1",
                mapping_accepted=True,
                tradeable=True,
            ),
            RuntimeMarket(
                market_id="m2",
                status="open",
                exchange_ids=("46",),
                tournament_id="t2",
                mapping_accepted=True,
                tradeable=True,
            ),
        ),
        books=(),
        portfolio=RuntimePortfolio(account_trusted=True),
        observation_monotonic_ns=1_000_000,
    )
    proposal = Opportunity(
        family=StrategyFamily.FV_TAKE,
        strategy_id="mixed-tournament",
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
            CandidateLeg(
                exchange_id="46",
                market_id="m2",
                tournament_id="t2",
                outcome_side=OutcomeSide.YES,
                action=OrderAction.BUY,
                quantity=1,
                limit_price_ticks=100,
            ),
        ),
        gross_edge=0.02,
        fair_value=0.55,
        decision_observation_ns=1_000_000,
    )
    decision = evaluate_risk(
        proposal,
        snapshot,
        RiskContext(
            mode=ExecutionMode.SHADOW,
            kill_switch=False,
            limits=None,
            max_state_age_ns=1_000_000,
        ),
    )
    assert decision.approved is False
    assert decision.reason == "mixed_tournament_operation"


def test_depth_sensitive_opportunity_rejects_untrusted_or_stale_depth() -> None:
    context = RiskContext(
        mode=ExecutionMode.SHADOW,
        kill_switch=False,
        limits=None,
        max_state_age_ns=100,
    )
    decision = evaluate_risk(
        _opportunity(requires_depth=True),
        _snapshot(trusted_depth=False),
        context,
    )
    assert decision.reason == "trusted_depth_required"

    stale = _snapshot(observed_ns=1_000)
    stale_book = RuntimeBook(
        exchange_id="36",
        market_id="m1",
        tournament_id="t1",
        bids=stale.books[0].bids,
        asks=stale.books[0].asks,
        trusted_depth=True,
        observed_monotonic_ns=1,
    )
    stale = RuntimeSnapshot(
        markets=stale.markets,
        books=(stale_book,),
        portfolio=stale.portfolio,
        observation_monotonic_ns=1_000,
    )
    decision = evaluate_risk(_opportunity(requires_depth=True), stale, context)
    assert decision.reason == "depth_state_stale"


def test_uncertain_orders_continue_to_consume_open_order_risk() -> None:
    uncertain = RuntimeOrderState(
        logical_intent_id="old",
        exchange_id="36",
        market_id="m1",
        tournament_id="t1",
        reserved_exposure=9.0,
        open=False,
        uncertain=True,
    )
    decision = evaluate_risk(
        _opportunity(quantity=2),
        _snapshot(orders=(uncertain,)),
        RiskContext(
            mode=ExecutionMode.LIVE,
            kill_switch=False,
            limits=_limits(max_open_order_exposure=10.0),
            max_state_age_ns=1_000_000,
        ),
    )
    assert decision.approved is False
    assert decision.reason == "max_open_order_exposure"



def test_open_and_uncertain_orders_count_toward_worst_case_gross_cap() -> None:
    uncertain = RuntimeOrderState(
        logical_intent_id="old-gross",
        exchange_id="36",
        market_id="m1",
        tournament_id="t1",
        reserved_exposure=9.0,
        open=False,
        uncertain=True,
    )
    decision = evaluate_risk(
        _opportunity(quantity=2),
        _snapshot(orders=(uncertain,)),
        RiskContext(
            mode=ExecutionMode.LIVE,
            kill_switch=False,
            limits=_limits(
                max_gross_exposure=10.0,
                max_open_order_exposure=100.0,
            ),
            max_state_age_ns=1_000_000,
        ),
    )
    assert decision.approved is False
    assert decision.reason == "max_gross_exposure"


def test_atomic_relationship_is_risked_as_one_execution_operation() -> None:
    decision = evaluate_risk(
        _opportunity(
            quantity=2,
            atomic=True,
            relationship_id="22222222-2222-2222-2222-222222222222",
        ),
        _snapshot(),
        RiskContext(
            mode=ExecutionMode.LIVE,
            kill_switch=False,
            limits=_limits(),
            max_state_age_ns=1_000_000,
        ),
    )
    assert decision.approved is True
    assert decision.operation_kind is OperationKind.ATOMIC_MULTI_LEG
    assert len(decision.intents) == 2
    assert decision.relationship_constraint == "22222222-2222-2222-2222-222222222222"


def test_same_timestamp_multi_market_intents_have_distinct_global_identity() -> None:
    context = RiskContext(
        mode=ExecutionMode.LIVE,
        kill_switch=False,
        limits=_limits(),
        max_state_age_ns=1_000_000,
    )

    def proposal(exchange_id: str) -> Opportunity:
        return Opportunity(
            family=StrategyFamily.MAKE,
            strategy_id="make-direct-pm",
            legs=(
                CandidateLeg(
                    exchange_id=exchange_id,
                    market_id="m1",
                    tournament_id="t1",
                    outcome_side=OutcomeSide.YES,
                    action=OrderAction.BUY,
                    quantity=1,
                    limit_price_ticks=100,
                ),
            ),
            gross_edge=0.01,
            fair_value=0.55,
            decision_observation_ns=1_000_000,
        )

    first = evaluate_risk(proposal("36"), _snapshot(), context)
    second = evaluate_risk(proposal("37"), _snapshot(), context)
    assert first.approved and second.approved
    assert first.intents[0].intent_id != second.intents[0].intent_id

    reservations = ExecutionReservationBook()
    reservations.reserve("op-36", first.intents)
    reservations.reserve("op-37", second.intents)
    assert reservations.intent_ids() == frozenset(
        {
            first.intents[0].intent_id,
            second.intents[0].intent_id,
        }
    )


def test_shadow_and_live_share_identical_post_risk_intents() -> None:
    shadow_decision = evaluate_risk(
        _opportunity(),
        _snapshot(),
        RiskContext(
            mode=ExecutionMode.SHADOW,
            kill_switch=False,
            limits=_limits(),
            max_state_age_ns=1_000_000,
        ),
    )
    live_decision = evaluate_risk(
        _opportunity(),
        _snapshot(),
        RiskContext(
            mode=ExecutionMode.LIVE,
            kill_switch=False,
            limits=_limits(),
            max_state_age_ns=1_000_000,
        ),
    )
    assert shadow_decision.approved and live_decision.approved

    shadow = build_execution_plan(
        shadow_decision,
        logical_operation_id="logical-1",
        created_monotonic_ns=123,
    )
    live = build_execution_plan(
        live_decision,
        logical_operation_id="logical-1",
        created_monotonic_ns=123,
    )
    assert shadow.intents == live.intents
    assert shadow.envelope.payload_json == live.envelope.payload_json
    assert shadow.envelope.payload_sha256 == live.envelope.payload_sha256
    assert shadow.envelope.sink_mode is ExecutionMode.SHADOW
    assert live.envelope.sink_mode is ExecutionMode.LIVE

    # Execution mode is no longer an independent planner input. A SHADOW Risk
    # approval therefore cannot be relabelled LIVE by a caller.
    with pytest.raises(TypeError):
        build_execution_plan(
            shadow_decision,
            logical_operation_id="bad-mode-override",
            created_monotonic_ns=123,
            mode=ExecutionMode.LIVE,  # type: ignore[call-arg]
        )


def test_live_interlocks_require_explicit_invocation_and_trusted_account() -> None:
    settings = AppSettings(
        sig_trade_credential=SecretStr("trade-secret"),
        tournament_id="t1",
        tournament_slug="cup",
        trading_enabled=True,
        execution_mode="LIVE",
        global_kill_switch=False,
        risk_max_order_size=10,
        risk_max_gross_exposure=100.0,
        risk_max_per_market_exposure=100.0,
        risk_max_open_order_exposure=100.0,
        risk_max_concurrent_open_orders=10,
    )
    with pytest.raises(LiveInterlockError):
        assert_live_interlocks(
            settings,
            explicit_live_invocation=False,
            account_trusted=True,
        )
    with pytest.raises(LiveInterlockError):
        assert_live_interlocks(
            settings,
            explicit_live_invocation=True,
            account_trusted=False,
        )

    permit = assert_live_interlocks(
        settings,
        explicit_live_invocation=True,
        account_trusted=True,
    )
    assert permit.tournament_id == "t1"


def test_hot_path_telemetry_is_bounded_and_counts_drops() -> None:
    telemetry = HotPathTelemetry(capacity=2)
    telemetry.observe("a", 1)
    telemetry.observe("b", 2)
    telemetry.observe("c", 3)
    telemetry.increment("approved")
    snapshot = telemetry.snapshot()
    assert tuple(item.name for item in snapshot.observations) == ("b", "c")
    assert snapshot.dropped_observations == 1
    assert snapshot.counters == {"approved": 1}


def test_hot_path_modules_do_not_import_io_heavy_dependencies() -> None:
    forbidden = {"httpx", "aiohttp", "sqlite3", "pyarrow", "supabase"}
    root = Path(__file__).resolve().parents[1] / "src" / "predictions_cup"
    paths = (
        root / "runtime" / "models.py",
        root / "runtime" / "engine.py",
        root / "strategy" / "core.py",
        root / "strategy" / "kernels.py",
        root / "risk" / "core.py",
    )
    imported: set[str] = set()
    for path in paths:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".", 1)[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".", 1)[0])
    assert imported.isdisjoint(forbidden)



def test_settings_default_to_shadow_and_live_requires_complete_caps() -> None:
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
            tournament_id="t1",
            tournament_slug="cup",
        )


def test_registered_equivalent_kernel_candidates_match_reference() -> None:
    kernels = default_kernel_registry()
    for probability in (0.01, 0.1, 0.42, 0.5, 0.9, 0.99):
        assert kernels.run("M-038", "ratio", probability) == pytest.approx(
            kernels.run("M-038", "log1p", probability),
            rel=1e-14,
            abs=1e-14,
        )

    assert kernels.run("M-041", "stable", 0.42, 0.1, 2.0) == pytest.approx(
        kernels.run("M-041", "naive", 0.42, 0.1, 2.0),
        rel=1e-14,
        abs=1e-14,
    )


def test_live_risk_rejects_unaccepted_mapping_and_global_kill_switch() -> None:
    base = _snapshot()
    market = base.markets[0]
    unmapped = RuntimeSnapshot(
        markets=(
            RuntimeMarket(
                market_id=market.market_id,
                status=market.status,
                exchange_ids=market.exchange_ids,
                tournament_id=market.tournament_id,
                mapping_accepted=False,
                tradeable=True,
            ),
        ),
        books=base.books,
        portfolio=base.portfolio,
        observation_monotonic_ns=base.observation_monotonic_ns,
    )
    context = RiskContext(
        mode=ExecutionMode.LIVE,
        kill_switch=False,
        limits=_limits(),
        max_state_age_ns=1_000_000,
    )
    assert evaluate_risk(_opportunity(), unmapped, context).reason == (
        "mapping_or_tradeability_not_accepted"
    )

    killed = RiskContext(
        mode=ExecutionMode.LIVE,
        kill_switch=True,
        limits=_limits(),
        max_state_age_ns=1_000_000,
    )
    assert evaluate_risk(_opportunity(), base, killed).reason == "global_kill_switch"


def test_shadow_sink_only_fills_immediately_executable_crosses() -> None:
    snapshot = _snapshot()
    crossing = RuntimeOrderIntent(
        intent_id="crossing",
        exchange_id="36",
        market_id="m1",
        tournament_id="t1",
        outcome_side=OutcomeSide.YES,
        action=OrderAction.BUY,
        quantity=1,
        limit_price_ticks=101,
        strategy_id="fixture",
        decision_observation_ns=1,
    )
    envelope = ExecutionEnvelope.placement(
        logical_operation_id="shadow-cross",
        operation_kind=OperationKind.SINGLE_PLACEMENT,
        sink_mode=ExecutionMode.SHADOW,
        idempotency_key="shadow-cross",
        intents=(crossing,),
        created_monotonic_ns=1,
    )
    event = ShadowSink(clock_ns=lambda: 2).dispatch(
        ExecutionPlan(envelope=envelope, intents=(crossing,)),
        snapshot,
    )
    assert event.state is LifecycleState.FILLED

    passive = RuntimeOrderIntent(
        intent_id="passive",
        exchange_id="36",
        market_id="m1",
        tournament_id="t1",
        outcome_side=OutcomeSide.YES,
        action=OrderAction.BUY,
        quantity=1,
        limit_price_ticks=100,
        strategy_id="fixture",
        decision_observation_ns=1,
    )
    passive_envelope = ExecutionEnvelope.placement(
        logical_operation_id="shadow-passive",
        operation_kind=OperationKind.SINGLE_PLACEMENT,
        sink_mode=ExecutionMode.SHADOW,
        idempotency_key="shadow-passive",
        intents=(passive,),
        created_monotonic_ns=1,
    )
    passive_event = ShadowSink(clock_ns=lambda: 3).dispatch(
        ExecutionPlan(envelope=passive_envelope, intents=(passive,)),
        snapshot,
    )
    assert passive_event.state is LifecycleState.OPEN


    oversized = RuntimeOrderIntent(
        intent_id="partial-depth",
        exchange_id="36",
        market_id="m1",
        tournament_id="t1",
        outcome_side=OutcomeSide.YES,
        action=OrderAction.BUY,
        quantity=25,
        limit_price_ticks=101,
        strategy_id="fixture",
        decision_observation_ns=1,
    )
    oversized_envelope = ExecutionEnvelope.placement(
        logical_operation_id="shadow-partial",
        operation_kind=OperationKind.SINGLE_PLACEMENT,
        sink_mode=ExecutionMode.SHADOW,
        idempotency_key="shadow-partial",
        intents=(oversized,),
        created_monotonic_ns=1,
    )
    partial_event = ShadowSink(clock_ns=lambda: 4).dispatch(
        ExecutionPlan(envelope=oversized_envelope, intents=(oversized,)),
        snapshot,
    )
    assert partial_event.state is LifecycleState.PARTIALLY_FILLED

def test_generated_placement_envelopes_validate_against_transport_dtos() -> None:
    first = RuntimeOrderIntent(
        intent_id="dto-1",
        exchange_id="36",
        market_id="m1",
        tournament_id="t1",
        outcome_side=OutcomeSide.YES,
        action=OrderAction.BUY,
        quantity=1,
        limit_price_ticks=100,
        strategy_id="fixture",
        decision_observation_ns=1,
    )
    second = RuntimeOrderIntent(
        intent_id="dto-2",
        exchange_id="37",
        market_id="m1",
        tournament_id="t1",
        outcome_side=OutcomeSide.NO,
        action=OrderAction.SELL,
        quantity=2,
        limit_price_ticks=120,
        strategy_id="fixture",
        decision_observation_ns=1,
    )
    single = ExecutionEnvelope.placement(
        logical_operation_id="dto-single",
        operation_kind=OperationKind.SINGLE_PLACEMENT,
        sink_mode=ExecutionMode.LIVE,
        idempotency_key="dto-single",
        intents=(first,),
        created_monotonic_ns=1,
    )
    batch = ExecutionEnvelope.placement(
        logical_operation_id="dto-batch",
        operation_kind=OperationKind.BEST_EFFORT_BATCH,
        sink_mode=ExecutionMode.LIVE,
        idempotency_key="dto-batch",
        intents=(first, second),
        created_monotonic_ns=1,
    )
    multi = ExecutionEnvelope.placement(
        logical_operation_id="dto-multi",
        operation_kind=OperationKind.ATOMIC_MULTI_LEG,
        sink_mode=ExecutionMode.LIVE,
        idempotency_key="dto-multi",
        intents=(first, second),
        created_monotonic_ns=1,
        relationship_constraint="22222222-2222-2222-2222-222222222222",
    )

    SingleOrderRequestDto.model_validate(json.loads(single.payload_json))
    BatchOrderRequestDto.model_validate(json.loads(batch.payload_json))
    MultiLegOrderRequestDto.model_validate(json.loads(multi.payload_json))

