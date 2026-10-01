"""Deterministic acceptance tests for MODEL-RUNTIME-001."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from predictions_cup.execution.models import ExecutionEvent, ExecutionMode, LifecycleState
from predictions_cup.execution.reservations import ExecutionReservationBook
from predictions_cup.maker.coordinator import MakerStateChange
from predictions_cup.models.contracts import (
    ModelCapability,
    ModelDecision,
    ModelDecisionKind,
    ModelRiskEnvelope,
    ModelSpec,
)
from predictions_cup.models.registry import ModelRegistry
from predictions_cup.models.runtime import (
    EffectiveModelMode,
    LiveModelCoordinator,
    ModelRuntime,
    PaperModelCandidate,
    paper_shadow_candidates,
)
from predictions_cup.risk.capital import (
    CapitalRiskState,
    ExposureBucket,
    RiskExposureSnapshot,
)
from predictions_cup.risk.core import RiskContext, RiskLimits
from predictions_cup.runtime.models import (
    OrderAction,
    RuntimeBook,
    RuntimeLevel,
    RuntimeMarket,
    RuntimeOrderState,
    RuntimePortfolio,
    RuntimePosition,
    RuntimeSnapshot,
)
from predictions_cup.shadow.contracts import CanonicalShadowSnapshot, DecisionStatus
from predictions_cup.strategy.core import StrategyFamily


class _Provider:
    def __init__(
        self,
        model_id: str,
        *,
        live_eligible: bool = False,
        capability: ModelCapability = ModelCapability.DIRECTIONAL,
        decision: ModelDecision | None = None,
        fail: bool = False,
    ) -> None:
        self.spec = ModelSpec(
            model_id=model_id,
            model_version="v1",
            capability=capability,
            source_hash=("a" * 63) + model_id[-1:],
            strategy_family=StrategyFamily.PRED,
            live_eligible=live_eligible,
            risk=ModelRiskEnvelope(
                base_order_size=2,
                max_model_position=3,
                max_order_size=2,
                max_market_exposure=3.0,
            ),
            max_input_age_ns=100,
        )
        self._decision = decision or ModelDecision(
            kind=ModelDecisionKind.DIRECTIONAL,
            signal_value=0.1,
            fair_value=0.55,
            confidence=0.8,
            direction=OrderAction.BUY,
            reason="test_signal",
        )
        self._fail = fail

    def evaluate(self, snapshot: CanonicalShadowSnapshot) -> ModelDecision:
        del snapshot
        if self._fail:
            raise RuntimeError("boom")
        return self._decision


def _snapshot(
    *,
    now_ns: int = 1_000,
    book_ns: int = 1_000,
    market: bool = True,
    trusted: bool = True,
    positions: tuple[RuntimePosition, ...] = (),
    orders: tuple[RuntimeOrderState, ...] = (),
) -> CanonicalShadowSnapshot:
    runtime = RuntimeSnapshot(
        markets=(
            (
                RuntimeMarket(
                    market_id="m1",
                    status="open",
                    exchange_ids=("e1",),
                    tournament_id="t1",
                    mapping_accepted=True,
                    tradeable=True,
                ),
            )
            if market
            else ()
        ),
        books=(
            RuntimeBook(
                exchange_id="e1",
                market_id="m1",
                tournament_id="t1",
                bids=(RuntimeLevel(99, 20.0),),
                asks=(RuntimeLevel(101, 20.0),),
                trusted_depth=True,
                observed_monotonic_ns=book_ns,
            ),
        ),
        portfolio=RuntimePortfolio(
            positions=positions,
            orders=orders,
            account_trusted=trusted,
        ),
        observation_monotonic_ns=now_ns,
    )
    from predictions_cup.maker.contracts import MakerMarketSnapshot

    maker = MakerMarketSnapshot(
        runtime=runtime,
        exchange_id="e1",
        market_id="m1",
        tournament_id="t1",
        now_monotonic_ns=now_ns,
        sig_bbo_observed_ns=book_ns,
        sig_bbo_trusted=True,
        sig_depth_observed_ns=book_ns,
        sig_depth_trusted=True,
        account_observed_ns=now_ns,
        inventory_observed_ns=now_ns,
        external_quotes={},
    )
    return CanonicalShadowSnapshot.freeze(
        maker,
        observed_at=datetime(2026, 10, 1, 16, 0, tzinfo=UTC),
        mapping_version="test-mapping",
        source_revision="test-revision",
    )


def _limits(**overrides: float | int | None) -> RiskLimits:
    values: dict[str, float | int | None] = {
        "max_order_size": 10,
        "max_gross_exposure": 100.0,
        "max_per_market_exposure": 100.0,
        "max_open_order_exposure": 100.0,
        "max_concurrent_open_orders": 10,
        "max_per_strategy_exposure": None,
        "max_event_group_exposure": None,
        "max_tournament_exposure": None,
        "session_loss_limit": None,
        "drawdown_limit": None,
    }
    values.update(overrides)

    def optional(name: str) -> float | None:
        value = values[name]
        return None if value is None else float(value)

    return RiskLimits(
        max_order_size=int(values["max_order_size"] or 0),
        max_gross_exposure=float(values["max_gross_exposure"] or 0),
        max_per_market_exposure=float(values["max_per_market_exposure"] or 0),
        max_open_order_exposure=float(values["max_open_order_exposure"] or 0),
        max_concurrent_open_orders=int(values["max_concurrent_open_orders"] or 0),
        max_per_strategy_exposure=optional("max_per_strategy_exposure"),
        max_event_group_exposure=optional("max_event_group_exposure"),
        max_tournament_exposure=optional("max_tournament_exposure"),
        session_loss_limit=optional("session_loss_limit"),
        drawdown_limit=optional("drawdown_limit"),
    )


def _capital(
    *,
    equity: str = "100",
    peak: str = "100",
    by_tournament: tuple[ExposureBucket, ...] = (),
) -> CapitalRiskState:
    current = Decimal(equity)
    peak_value = Decimal(peak)
    exposure = RiskExposureSnapshot(
        gross_exposure=sum(item.exposure for item in by_tournament),
        net_directional_exposure=0.0,
        open_order_exposure=0.0,
        uncertain_order_exposure=0.0,
        by_tournament=by_tournament,
        trusted=True,
        strategy_attribution_complete=True,
        group_classification_complete=True,
    )
    return CapitalRiskState(
        session_id="session",
        session_start_equity=Decimal("100"),
        session_start_unrealised_pnl=Decimal("0"),
        realised_pnl=Decimal("0"),
        unrealised_pnl=current - Decimal("100"),
        current_equity=current,
        peak_session_equity=peak_value,
        drawdown=peak_value - current,
        net_external_cash_flow=Decimal("0"),
        exposure=exposure,
        account_trusted=True,
        account_observed_monotonic_ns=1_000,
        marks_trusted=True,
        oldest_mark_observed_monotonic_ns=1_000,
        reconciliation_complete=True,
        global_halt=None,
        strategy_halts=(),
        limit_profile_version="test",
    )


def _risk(
    *,
    kill_switch: bool = False,
    limits: RiskLimits | None = None,
    capital: CapitalRiskState | None = None,
) -> RiskContext:
    return RiskContext(
        mode=ExecutionMode.LIVE,
        kill_switch=kill_switch,
        limits=limits or _limits(),
        max_state_age_ns=100,
        capital_state=capital,
        require_capital_state=capital is not None,
    )


def test_registry_add_remove_duplicate_and_unknown_config() -> None:
    provider = _Provider("model1")
    registry = ModelRegistry()
    registry.register(provider)
    assert registry.get("model1") is provider
    with pytest.raises(ValueError, match="duplicate"):
        registry.register(_Provider("model1"))
    assert registry.unregister("model1") is provider
    with pytest.raises(KeyError, match="unknown model"):
        registry.get("model1")

    with pytest.raises(ValueError, match="unknown configured"):
        ModelRuntime(ModelRegistry(), paper_model_ids=("missing",))


def test_two_key_live_truth_table_and_blocked_configuration() -> None:
    code_off = _Provider("off", live_eligible=False)
    code_on = _Provider("on", live_eligible=True)

    off = ModelRuntime(ModelRegistry((code_off,)))
    assert off.effective_mode("off") is EffectiveModelMode.NOT_LIVE

    on_env_off = ModelRuntime(ModelRegistry((code_on,)), platform_live_ready=True)
    assert on_env_off.effective_mode("on") is EffectiveModelMode.NOT_LIVE

    with pytest.raises(ValueError, match="without code LIVE authorization"):
        ModelRuntime(
            ModelRegistry((code_off,)),
            live_model_ids=("off",),
            platform_live_ready=True,
        )

    on_both = ModelRuntime(
        ModelRegistry((code_on,)),
        live_model_ids=("on",),
        platform_live_ready=True,
    )
    assert on_both.effective_mode("on") is EffectiveModelMode.LIVE


def test_paper_uses_same_logic_and_cannot_create_execution_plan() -> None:
    provider = _Provider("paper")
    runtime = ModelRuntime(
        ModelRegistry((provider,)),
        paper_model_ids=("paper",),
    )
    result = runtime.evaluate("paper", _snapshot(), risk_context=_risk())
    assert result.effective_mode is EffectiveModelMode.PAPER
    assert result.risk_decision is not None
    assert result.risk_decision.approved
    assert result.execution_plan is None
    assert result.decision.candidate_payload["model_mode"] == "PAPER"
    assert result.decision.candidate_payload["hypothetical_size"] == 2


def test_multiple_paper_models_and_context_model_never_expose_direction() -> None:
    directional = _Provider("directional")
    context = _Provider(
        "context",
        capability=ModelCapability.CONTEXT_ONLY,
        decision=ModelDecision(
            kind=ModelDecisionKind.CONTEXT,
            signal_value=0.7,
            urgency=0.7,
            reason="context_only",
        ),
    )
    candidates = paper_shadow_candidates(
        ModelRegistry((directional, context)),
        ("directional", "context"),
        risk_context=_risk(),
    )
    assert len(candidates) == 2
    output = candidates[1].evaluate(_snapshot())
    assert output.status is DecisionStatus.OK
    assert output.direction is None
    assert output.candidate_payload["hypothetical_size"] == 0


@pytest.mark.parametrize(
    ("snapshot", "risk_context", "expected"),
    (
        (_snapshot(), _risk(kill_switch=True), "global_kill_switch"),
        (_snapshot(trusted=False), _risk(), "account_state_untrusted"),
        (
            _snapshot(),
            _risk(
                limits=_limits(session_loss_limit=10.0),
                capital=_capital(equity="80", peak="100"),
            ),
            "session_loss_limit",
        ),
        (
            _snapshot(),
            _risk(
                limits=_limits(max_tournament_exposure=5.0),
                capital=_capital(
                    by_tournament=(ExposureBucket("t1", 5.0),),
                ),
            ),
            "max_tournament_exposure",
        ),
    ),
)
def test_fully_authorized_model_cannot_bypass_central_risk(
    snapshot: CanonicalShadowSnapshot,
    risk_context: RiskContext,
    expected: str,
) -> None:
    provider = _Provider("live", live_eligible=True)
    runtime = ModelRuntime(
        ModelRegistry((provider,)),
        live_model_ids=("live",),
        platform_live_ready=True,
    )
    result = runtime.evaluate("live", snapshot, risk_context=risk_context)
    assert result.risk_decision is not None
    assert result.risk_decision.reason == expected
    assert result.execution_plan is None


def test_live_authorization_only_yields_build009_plan_after_risk_approval() -> None:
    provider = _Provider("live", live_eligible=True)
    runtime = ModelRuntime(
        ModelRegistry((provider,)),
        live_model_ids=("live",),
        platform_live_ready=True,
    )
    result = runtime.evaluate(
        "live",
        _snapshot(),
        risk_context=_risk(),
        logical_operation_id="test:model:live",
    )
    assert result.risk_decision is not None and result.risk_decision.approved
    assert result.execution_plan is not None
    assert result.execution_plan.envelope.logical_operation_id == "test:model:live"
    assert result.execution_plan.audit.strategy_id.startswith("MODEL::live::v1::")


def test_sizing_inventory_market_uncertain_and_budget_clamps() -> None:
    provider = _Provider("live", live_eligible=True)
    runtime = ModelRuntime(
        ModelRegistry((provider,)),
        live_model_ids=("live",),
        platform_live_ready=True,
    )
    position = RuntimePosition(
        exchange_id="e1",
        market_id="m1",
        tournament_id="t1",
        gross_exposure=2.0,
        signed_quantity=2.0,
    )
    clamped = runtime.evaluate(
        "live",
        _snapshot(positions=(position,)),
        risk_context=_risk(),
    )
    assert clamped.sizing is not None
    assert clamped.sizing.quantity == 1

    uncertain = RuntimeOrderState(
        logical_intent_id="old",
        exchange_id="e1",
        market_id="m1",
        tournament_id="t1",
        reserved_exposure=1.0,
        open=True,
        uncertain=True,
    )
    blocked = runtime.evaluate(
        "live",
        _snapshot(orders=(uncertain,)),
        risk_context=_risk(),
    )
    assert blocked.sizing is not None
    assert blocked.sizing.reason == "uncertain_market_exposure"
    assert blocked.execution_plan is None

    no_budget = runtime.evaluate(
        "live",
        _snapshot(),
        risk_context=_risk(),
        available_capital=0.0,
    )
    assert no_budget.sizing is not None
    assert no_budget.sizing.reason == "available_budget_exhausted"


def test_model_exception_isolated_then_quarantined_until_explicit_recovery() -> None:
    broken = _Provider("broken", fail=True)
    healthy = _Provider("healthy")
    runtime = ModelRuntime(
        ModelRegistry((broken, healthy)),
        paper_model_ids=("broken", "healthy"),
        failure_quarantine_threshold=2,
    )
    first = runtime.evaluate("broken", _snapshot())
    second = runtime.evaluate("broken", _snapshot())
    third = runtime.evaluate("broken", _snapshot())
    other = runtime.evaluate("healthy", _snapshot())

    assert first.decision.decision_status is DecisionStatus.EXCEPTION
    assert second.decision.decision_status is DecisionStatus.EXCEPTION
    assert third.decision.decision_status is DecisionStatus.DISABLED
    assert other.decision.decision_status is DecisionStatus.OK
    assert next(row for row in runtime.status() if row.model_id == "broken").quarantined

    runtime.recover("broken")
    assert not next(
        row for row in runtime.status() if row.model_id == "broken"
    ).quarantined


def test_stale_and_missing_market_fail_closed_before_model_evaluation() -> None:
    provider = _Provider("paper")
    runtime = ModelRuntime(
        ModelRegistry((provider,)),
        paper_model_ids=("paper",),
    )
    stale = runtime.evaluate("paper", _snapshot(now_ns=1_000, book_ns=800))
    missing = runtime.evaluate("paper", _snapshot(market=False))
    assert stale.decision.decision_status is DecisionStatus.STALE_INPUT
    assert missing.decision.decision_status is DecisionStatus.NOT_READY
    assert stale.execution_plan is None
    assert missing.execution_plan is None


def test_provenance_retains_exact_model_version_mode_hash_and_risk() -> None:
    provider = _Provider("paper")
    runtime = ModelRuntime(
        ModelRegistry((provider,)),
        paper_model_ids=("paper",),
    )
    result = runtime.evaluate("paper", _snapshot(), risk_context=_risk())
    payload = result.decision.candidate_payload
    assert payload["model_id"] == provider.spec.model_id
    assert payload["model_version"] == provider.spec.model_version
    assert payload["model_source_hash"] == provider.spec.source_hash
    assert payload["model_mode"] == "PAPER"
    assert payload["risk_approved"] is True


def test_paper_candidate_records_hypothetical_central_risk_disposition() -> None:
    candidate = PaperModelCandidate(
        _Provider("paper"),
        risk_context=_risk(kill_switch=True),
    )
    output = candidate.evaluate(_snapshot())
    assert output.candidate_payload["risk_approved"] is False
    assert output.candidate_payload["risk_reason"] == "global_kill_switch"


@pytest.mark.asyncio
async def test_live_coordinator_reserves_before_accepted_dispatch_and_persists() -> None:
    provider = _Provider("live", live_eligible=True)
    runtime = ModelRuntime(
        ModelRegistry((provider,)),
        live_model_ids=("live",),
        platform_live_ready=True,
    )
    reservations = ExecutionReservationBook()
    dispatched: list[str] = []
    persisted: list[str] = []

    async def dispatch(plan: object, maker: object) -> ExecutionEvent:
        from predictions_cup.execution.sinks import ExecutionPlan
        from predictions_cup.maker.contracts import MakerMarketSnapshot

        assert isinstance(plan, ExecutionPlan)
        assert isinstance(maker, MakerMarketSnapshot)
        dispatched.append(plan.envelope.logical_operation_id)
        assert reservations.overlay_snapshot(maker.runtime).portfolio.orders
        return ExecutionEvent(
            logical_operation_id=plan.envelope.logical_operation_id,
            state=LifecycleState.ACKED,
            observed_monotonic_ns=1_001,
            simulated=True,
        )

    async def persist(decision: object) -> None:
        from predictions_cup.shadow.contracts import CandidateDecision

        assert isinstance(decision, CandidateDecision)
        persisted.append(decision.decision_id)

    coordinator = LiveModelCoordinator(
        runtime,
        mapping_version="test-mapping",
        risk_context=_risk(),
        reservations=reservations,
        dispatch=dispatch,
        decision_observer=persist,
    )
    canonical = _snapshot()
    events = await coordinator.on_state_change(
        MakerStateChange(
            event_id="event-1",
            observed_monotonic_ns=1_000,
            exchange_ids=frozenset({"e1"}),
        ),
        canonical.observed_at,
        {"e1": canonical.maker},
    )
    assert len(events) == 1
    assert dispatched == ["event-1:model:live:e1"]
    assert len(persisted) == 1
