from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from random import Random

import pytest
from pydantic import ValidationError

from predictions_cup.config import AppSettings
from predictions_cup.execution.journal import ExecutionJournal
from predictions_cup.execution.models import (
    ExecutionAudit,
    ExecutionEnvelope,
    ExecutionMode,
    OperationKind,
    RuntimeOrderIntent,
)
from predictions_cup.risk import (
    AuthoritativeRiskPosition,
    AuthoritativeRiskSnapshot,
    CapitalControlService,
    CapitalRiskState,
    CostBasisPosition,
    ExposureBucket,
    HaltScope,
    MarketExposureGroup,
    PnLReconstruction,
    ReconciliationError,
    RiskContext,
    RiskContextSource,
    RiskExposureSnapshot,
    RiskFill,
    RiskLimits,
    RiskMark,
    RiskProfile,
    RiskValuationPosition,
    SqliteRiskStateStore,
    apply_external_cash_flow_scan,
    attribute_strategy_exposure,
    build_exposure_snapshot,
    clear_strategy_halt,
    evaluate_risk,
    normalize_sig_risk_inputs,
    reconcile_capital_state,
    reconstruct_sig_cost_basis,
    replay_fills,
    reset_global_halt,
    scan_external_cash_flows,
    trip_global_halt,
    trip_strategy_halt,
    validate_restart_preflight,
)
from predictions_cup.runtime import (
    OrderAction,
    OutcomeSide,
    RuntimeMarket,
    RuntimeOrderState,
    RuntimePortfolio,
    RuntimePosition,
    RuntimeSnapshot,
)
from predictions_cup.runtime.models import AccountTrustGrade
from predictions_cup.sig.account_reconciliation import AccountAuthoritativeSnapshot
from predictions_cup.sig.account_state import (
    AccountRealtimeStateEngine,
    AccountTrustTransition,
)
from predictions_cup.sig.trading_dto import (
    FillReadDto,
    PortfolioPnlDto,
    PositionReadDto,
    TournamentTransactionPageDto,
)
from predictions_cup.strategy.core import CandidateLeg, Opportunity, StrategyFamily


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
    return RiskLimits(
        max_order_size=int(values["max_order_size"] or 0),
        max_gross_exposure=float(values["max_gross_exposure"] or 0),
        max_per_market_exposure=float(values["max_per_market_exposure"] or 0),
        max_open_order_exposure=float(values["max_open_order_exposure"] or 0),
        max_concurrent_open_orders=int(values["max_concurrent_open_orders"] or 0),
        max_per_strategy_exposure=_optional_float(values["max_per_strategy_exposure"]),
        max_event_group_exposure=_optional_float(values["max_event_group_exposure"]),
        max_tournament_exposure=_optional_float(values["max_tournament_exposure"]),
        session_loss_limit=_optional_float(values["session_loss_limit"]),
        drawdown_limit=_optional_float(values["drawdown_limit"]),
    )


def _optional_float(value: float | int | None) -> float | None:
    return None if value is None else float(value)


def _opportunity(
    *,
    quantity: int = 1,
    strategy_id: str = "s1",
    market_id: str = "m1",
    exchange_id: str = "1",
) -> Opportunity:
    return Opportunity(
        family=StrategyFamily.FV_TAKE,
        strategy_id=strategy_id,
        legs=(
            CandidateLeg(
                exchange_id=exchange_id,
                market_id=market_id,
                tournament_id="t1",
                outcome_side=OutcomeSide.YES,
                action=OrderAction.BUY,
                quantity=quantity,
                limit_price_ticks=100,
            ),
        ),
        gross_edge=0.02,
        fair_value=0.55,
        decision_observation_ns=1_000,
    )


def _snapshot(
    *,
    positions: tuple[RuntimePosition, ...] = (),
    orders: tuple[RuntimeOrderState, ...] = (),
    observed_ns: int = 1_000,
    trusted: bool = True,
) -> RuntimeSnapshot:
    markets = tuple(
        RuntimeMarket(
            market_id=f"m{index}",
            status="open",
            exchange_ids=(str(index),),
            tournament_id="t1",
            mapping_accepted=True,
            tradeable=True,
        )
        for index in range(1, 5)
    )
    return RuntimeSnapshot(
        markets=markets,
        books=(),
        portfolio=RuntimePortfolio(
            positions=positions,
            orders=orders,
            account_trusted=trusted,
        ),
        observation_monotonic_ns=observed_ns,
    )


def _capital(
    *,
    gross: float = 0.0,
    by_market: tuple[ExposureBucket, ...] = (),
    by_strategy: tuple[ExposureBucket, ...] = (),
    by_tournament: tuple[ExposureBucket, ...] = (),
    by_group: tuple[ExposureBucket, ...] = (),
    equity: str = "100",
    peak: str = "100",
    trusted: bool = True,
    reconciled: bool = True,
    account_ns: int = 1_000,
    mark_ns: int | None = 1_000,
) -> CapitalRiskState:
    current = Decimal(equity)
    peak_equity = Decimal(peak)
    return CapitalRiskState(
        session_id="session-1",
        session_start_equity=Decimal("100"),
        session_start_unrealised_pnl=Decimal("0"),
        realised_pnl=Decimal("0"),
        unrealised_pnl=current - Decimal("100"),
        current_equity=current,
        peak_session_equity=peak_equity,
        drawdown=peak_equity - current,
        net_external_cash_flow=Decimal("0"),
        exposure=RiskExposureSnapshot(
            gross_exposure=gross,
            net_directional_exposure=0.0,
            open_order_exposure=0.0,
            uncertain_order_exposure=0.0,
            by_market=by_market,
            by_strategy=by_strategy,
            by_tournament=by_tournament,
            by_group=by_group,
            trusted=trusted,
            strategy_attribution_complete=True,
            group_classification_complete=True,
        ),
        account_trusted=trusted,
        account_observed_monotonic_ns=account_ns,
        marks_trusted=trusted,
        oldest_mark_observed_monotonic_ns=mark_ns,
        reconciliation_complete=reconciled,
        global_halt=None,
        strategy_halts=(),
        limit_profile_version="v1",
    )


def _context(
    limits: RiskLimits,
    *,
    capital: CapitalRiskState | None = None,
    groups: tuple[MarketExposureGroup, ...] = (),
    observed_age_ns: int | None = None,
) -> RiskContext:
    return RiskContext(
        mode=ExecutionMode.LIVE,
        kill_switch=False,
        limits=limits,
        max_state_age_ns=1_000,
        capital_state=capital,
        exposure_groups=groups,
        max_account_age_ns=observed_age_ns,
        max_mark_age_ns=observed_age_ns,
        require_capital_state=capital is not None,
    )


def test_state_dependent_limits_cannot_run_without_capital_state() -> None:
    decision = evaluate_risk(
        _opportunity(),
        _snapshot(),
        _context(_limits(session_loss_limit=10)),
    )
    assert decision.reason == "capital_state_missing"

    with pytest.raises(ValidationError, match="state-dependent caps"):
        AppSettings(risk_session_loss_limit=10)


def test_exact_order_boundary_and_one_unit_beyond() -> None:
    snapshot = _snapshot()
    limits = _limits(max_order_size=2)
    assert evaluate_risk(_opportunity(quantity=2), snapshot, _context(limits)).approved
    denied = evaluate_risk(_opportunity(quantity=3), snapshot, _context(limits))
    assert denied.reason == "max_order_size"


def test_exact_gross_boundary_and_one_unit_beyond() -> None:
    position = RuntimePosition("1", "m1", "t1", gross_exposure=8.0, signed_quantity=8.0)
    snapshot = _snapshot(positions=(position,))
    limits = _limits(max_gross_exposure=10)
    assert evaluate_risk(_opportunity(quantity=2), snapshot, _context(limits)).approved
    assert (
        evaluate_risk(_opportunity(quantity=3), snapshot, _context(limits)).reason
        == "max_gross_exposure"
    )


def test_proxy_worst_case_respects_global_and_market_caps() -> None:
    position = RuntimePosition("1", "m1", "t1", gross_exposure=175.0, signed_quantity=-175.0)
    open_order = RuntimeOrderState("old", "1", "m1", "t1", 15.0, True, False, signed_quantity=15.0)
    snapshot = _snapshot(positions=(position,), orders=(open_order,))
    portfolio = replace(
        snapshot.portfolio,
        account_trust_grade=AccountTrustGrade.PROXY,
        account_proxy_uncertainty=10.0,
    )
    snapshot = replace(snapshot, portfolio=portfolio)

    market_limited = evaluate_risk(
        _opportunity(quantity=1),
        snapshot,
        _context(_limits(max_gross_exposure=250.0, max_per_market_exposure=200.0)),
    )
    global_limited = evaluate_risk(
        _opportunity(quantity=1),
        snapshot,
        _context(_limits(max_gross_exposure=200.0, max_per_market_exposure=250.0)),
    )

    assert market_limited.reason == "max_per_market_exposure"
    assert global_limited.reason == "max_gross_exposure"


def test_exact_market_open_and_order_count_boundaries() -> None:
    open_order = RuntimeOrderState("old", "1", "m1", "t1", 8.0, True, False)
    snapshot = _snapshot(orders=(open_order,))
    limits = _limits(
        max_per_market_exposure=10,
        max_open_order_exposure=10,
        max_concurrent_open_orders=2,
    )
    assert evaluate_risk(_opportunity(quantity=2), snapshot, _context(limits)).approved

    assert (
        evaluate_risk(
            _opportunity(quantity=3),
            snapshot,
            _context(replace(limits, max_open_order_exposure=100.0)),
        ).reason
        == "max_per_market_exposure"
    )
    assert (
        evaluate_risk(
            _opportunity(quantity=3),
            snapshot,
            _context(replace(limits, max_per_market_exposure=100.0)),
        ).reason
        == "max_open_order_exposure"
    )
    second = RuntimeOrderState("old2", "2", "m2", "t1", 1.0, True, False)
    assert (
        evaluate_risk(
            _opportunity(quantity=1),
            _snapshot(orders=(open_order, second)),
            _context(
                _limits(
                    max_per_market_exposure=100,
                    max_open_order_exposure=100,
                    max_concurrent_open_orders=2,
                )
            ),
        ).reason
        == "max_concurrent_open_orders"
    )


def test_open_uncertain_and_position_all_consume_gross() -> None:
    position = RuntimePosition("1", "m1", "t1", 4.0, 4.0)
    open_order = RuntimeOrderState("open", "2", "m2", "t1", 3.0, True, False)
    uncertain = RuntimeOrderState("uncertain", "3", "m3", "t1", 2.0, False, True)
    decision = evaluate_risk(
        _opportunity(quantity=2, market_id="m4", exchange_id="4"),
        _snapshot(positions=(position,), orders=(open_order, uncertain)),
        _context(_limits(max_gross_exposure=10)),
    )
    assert decision.reason == "max_gross_exposure"


def test_strategy_tournament_and_group_caps() -> None:
    capital = _capital(
        gross=8.0,
        by_strategy=(ExposureBucket("s1", 8.0),),
        by_tournament=(ExposureBucket("t1", 8.0),),
        by_group=(ExposureBucket("election-us", 8.0),),
    )
    groups = (MarketExposureGroup("m1", "t1", ("election-us",)),)
    base = _limits(
        max_per_strategy_exposure=10,
        max_tournament_exposure=10,
        max_event_group_exposure=10,
    )
    assert evaluate_risk(
        _opportunity(quantity=2),
        _snapshot(),
        _context(base, capital=capital, groups=groups),
    ).approved

    assert (
        evaluate_risk(
            _opportunity(quantity=3),
            _snapshot(),
            _context(base, capital=capital, groups=groups),
        ).reason
        == "max_per_strategy_exposure"
    )

    tournament_only = replace(base, max_per_strategy_exposure=100.0)
    assert (
        evaluate_risk(
            _opportunity(quantity=3),
            _snapshot(),
            _context(tournament_only, capital=capital, groups=groups),
        ).reason
        == "max_tournament_exposure"
    )

    group_only = replace(tournament_only, max_tournament_exposure=100.0)
    assert (
        evaluate_risk(
            _opportunity(quantity=3),
            _snapshot(),
            _context(group_only, capital=capital, groups=groups),
        ).reason
        == "max_event_group_exposure"
    )


def test_local_pending_reservations_count_toward_advanced_caps() -> None:
    pending = RuntimeOrderState(
        "pending",
        "2",
        "m2",
        "t1",
        1.0,
        False,
        True,
        strategy_id="s1",
    )
    capital = _capital(
        by_strategy=(ExposureBucket("s1", 8.0),),
        by_tournament=(ExposureBucket("t1", 8.0),),
        by_group=(ExposureBucket("event", 8.0),),
    )
    groups = (
        MarketExposureGroup("m1", "t1", ("event",)),
        MarketExposureGroup("m2", "t1", ("event",)),
    )
    snapshot = _snapshot(orders=(pending,))
    base = _limits(
        max_per_strategy_exposure=10,
        max_tournament_exposure=10,
        max_event_group_exposure=10,
    )
    assert (
        evaluate_risk(
            _opportunity(quantity=2),
            snapshot,
            _context(base, capital=capital, groups=groups),
        ).reason
        == "max_per_strategy_exposure"
    )

    tournament_only = replace(base, max_per_strategy_exposure=100.0)
    assert (
        evaluate_risk(
            _opportunity(quantity=2),
            snapshot,
            _context(tournament_only, capital=capital, groups=groups),
        ).reason
        == "max_tournament_exposure"
    )

    group_only = replace(tournament_only, max_tournament_exposure=100.0)
    assert (
        evaluate_risk(
            _opportunity(quantity=2),
            snapshot,
            _context(group_only, capital=capital, groups=groups),
        ).reason
        == "max_event_group_exposure"
    )


def test_unclassified_group_fails_closed_when_group_cap_is_enabled() -> None:
    capital = _capital()
    decision = evaluate_risk(
        _opportunity(),
        _snapshot(),
        _context(
            _limits(max_event_group_exposure=10),
            capital=capital,
        ),
    )
    assert decision.reason == "exposure_group_unclassified"


def test_multiple_strategies_safe_individually_but_globally_unsafe() -> None:
    positions = (
        RuntimePosition("1", "m1", "t1", 5.0, 5.0),
        RuntimePosition("2", "m2", "t1", 4.0, -4.0),
    )
    capital = _capital(
        gross=9.0,
        by_strategy=(ExposureBucket("s1", 4.0), ExposureBucket("s2", 5.0)),
    )
    limits = _limits(max_gross_exposure=10, max_per_strategy_exposure=10)
    decision = evaluate_risk(
        _opportunity(quantity=2, market_id="m3", exchange_id="3"),
        _snapshot(positions=positions),
        _context(limits, capital=capital),
    )
    assert decision.reason == "max_gross_exposure"


def test_related_markets_can_be_individually_safe_but_group_unsafe() -> None:
    capital = _capital(
        by_group=(ExposureBucket("same-election", 9.0),),
    )
    groups = (MarketExposureGroup("m2", "t1", ("same-election",)),)
    decision = evaluate_risk(
        _opportunity(quantity=2, market_id="m2", exchange_id="2"),
        _snapshot(),
        _context(
            _limits(max_per_market_exposure=10, max_event_group_exposure=10),
            capital=capital,
            groups=groups,
        ),
    )
    assert decision.reason == "max_event_group_exposure"


def test_stale_or_untrusted_capital_state_fails_closed() -> None:
    limits = _limits()
    stale_account = _capital(account_ns=1)
    assert (
        evaluate_risk(
            _opportunity(),
            _snapshot(observed_ns=2_000),
            _context(limits, capital=stale_account, observed_age_ns=100),
        ).reason
        == "risk_account_state_stale"
    )

    stale_mark = _capital(account_ns=2_000, mark_ns=1)
    assert (
        evaluate_risk(
            _opportunity(),
            _snapshot(observed_ns=2_000),
            _context(limits, capital=stale_mark, observed_age_ns=100),
        ).reason
        == "risk_mark_state_stale"
    )

    assert (
        evaluate_risk(
            _opportunity(),
            _snapshot(),
            _context(limits, capital=_capital(trusted=False)),
        ).reason
        == "risk_state_untrusted"
    )


def test_session_loss_drawdown_strategy_and_global_halts() -> None:
    loss_limits = _limits(session_loss_limit=10)
    assert (
        evaluate_risk(
            _opportunity(),
            _snapshot(),
            _context(loss_limits, capital=_capital(equity="90")),
        ).reason
        == "session_loss_limit"
    )

    drawdown_limits = _limits(drawdown_limit=10)
    assert (
        evaluate_risk(
            _opportunity(),
            _snapshot(),
            _context(drawdown_limits, capital=_capital(equity="100", peak="110")),
        ).reason
        == "peak_drawdown_limit"
    )

    strategy_halted = trip_strategy_halt(
        _capital(),
        scope=HaltScope.STRATEGY_ID,
        scope_value="s1",
        reason="operator",
        now_monotonic_ns=10,
    )
    assert (
        evaluate_risk(
            _opportunity(),
            _snapshot(),
            _context(_limits(), capital=strategy_halted),
        ).reason
        == "strategy_halt"
    )

    globally_halted = trip_global_halt(_capital(), reason="loss", now_monotonic_ns=10)
    assert (
        evaluate_risk(
            _opportunity(),
            _snapshot(),
            _context(_limits(), capital=globally_halted),
        ).reason
        == "global_capital_halt"
    )


def test_exploratory_profile_cannot_bypass_hard_caps() -> None:
    hard = _limits(max_gross_exposure=10)
    exploratory = _limits(max_gross_exposure=100)
    profile = RiskProfile(
        name="explore",
        version="v1",
        limits=exploratory,
        exploratory=True,
        hard_limits=hard,
    )
    position = RuntimePosition("1", "m1", "t1", 9.0, 9.0)
    context = RiskContext(
        mode=ExecutionMode.LIVE,
        kill_switch=False,
        limits=None,
        max_state_age_ns=1_000,
        profile=profile,
    )
    decision = evaluate_risk(
        _opportunity(quantity=2, market_id="m2", exchange_id="2"),
        _snapshot(positions=(position,)),
        context,
    )
    assert decision.reason == "max_gross_exposure"


def test_replay_partial_and_duplicate_fills_is_deterministic() -> None:
    fills = (
        RiskFill(
            "f1",
            "1",
            "m1",
            OutcomeSide.YES,
            OrderAction.BUY,
            Decimal("10"),
            Decimal("0.4"),
            1,
        ),
        RiskFill(
            "f2",
            "1",
            "m1",
            OutcomeSide.YES,
            OrderAction.SELL,
            Decimal("4"),
            Decimal("0.6"),
            2,
        ),
    )
    replay = replay_fills((*fills, fills[0]), cursor="cursor-2")
    assert replay.realised_pnl == Decimal("0.8")
    assert replay.cursor == "cursor-2"
    assert replay.processed_fill_ids == ("f1", "f2")
    assert replay.positions == (
        CostBasisPosition(
            exchange_id="1",
            market_id="m1",
            signed_quantity=Decimal("6"),
            avg_entry_yes=Decimal("0.4"),
            canonical_cost_basis=Decimal("2.4"),
        ),
    )

    with pytest.raises(ValueError, match="conflicting replay"):
        replay_fills(
            (
                fills[0],
                replace(fills[0], quantity=Decimal("11")),
            )
        )


def test_no_side_fill_normalizes_to_yes_denominated_inventory() -> None:
    replay = replay_fills(
        (
            RiskFill(
                "f1",
                "1",
                "m1",
                OutcomeSide.NO,
                OrderAction.BUY,
                Decimal("2"),
                Decimal("0.3"),
                1,
            ),
        )
    )
    assert replay.positions[0].signed_quantity == Decimal("-2")
    assert replay.positions[0].avg_entry_yes == Decimal("0.7")


def _reconcile_previous(*, halt: bool = False) -> CapitalRiskState:
    state = _capital(account_ns=0, mark_ns=None, trusted=False, reconciled=False)
    if halt:
        state = trip_global_halt(state, reason="prior_loss", now_monotonic_ns=5)
    return state


def test_reconciliation_rejects_journal_account_disagreement() -> None:
    reconstruction = PnLReconstruction(
        positions=(
            CostBasisPosition(
                "1",
                "m1",
                Decimal("2"),
                Decimal("0.4"),
                Decimal("0.8"),
            ),
        ),
        realised_pnl=Decimal("0"),
        processed_fill_ids=("f1",),
    )
    authoritative = AuthoritativeRiskSnapshot(
        session_id="session-1",
        equity=Decimal("100"),
        account_trusted=True,
        observed_monotonic_ns=100,
        positions=(
            AuthoritativeRiskPosition(
                "1",
                "m1",
                Decimal("3"),
                Decimal("1.2"),
            ),
        ),
    )
    with pytest.raises(ReconciliationError, match="quantity_disagreement"):
        reconcile_capital_state(
            previous=_reconcile_previous(),
            authoritative=authoritative,
            reconstruction=reconstruction,
            exposure=RiskExposureSnapshot(3, 3, 0, 0, trusted=True),
            marks=(RiskMark("1", "m1", Decimal("0.4"), "sig", 100, True, "v1", "rest"),),
            now_monotonic_ns=100,
            max_account_age_ns=100,
            max_mark_age_ns=100,
            session_loss_limit=None,
            drawdown_limit=None,
        )


def test_reconciliation_trips_loss_halt_and_never_clears_existing_halt() -> None:
    empty_reconstruction = PnLReconstruction((), Decimal("0"), (), cursor="done")
    account = AuthoritativeRiskSnapshot(
        session_id="session-1",
        equity=Decimal("89"),
        account_trusted=True,
        observed_monotonic_ns=100,
        positions=(),
        realised_pnl=Decimal("-11"),
        unrealised_pnl=Decimal("0"),
    )
    reconciled = reconcile_capital_state(
        previous=_reconcile_previous(),
        authoritative=account,
        reconstruction=empty_reconstruction,
        exposure=RiskExposureSnapshot(0, 0, 0, 0, trusted=True),
        marks=(),
        now_monotonic_ns=100,
        max_account_age_ns=100,
        max_mark_age_ns=100,
        session_loss_limit=Decimal("10"),
        drawdown_limit=None,
    )
    assert reconciled.global_halt is not None
    assert reconciled.global_halt.reason == "session_loss_limit"
    assert reconciled.realised_pnl_cursor == "done"

    preserved = reconcile_capital_state(
        previous=_reconcile_previous(halt=True),
        authoritative=replace(
            account,
            equity=Decimal("100"),
            realised_pnl=Decimal("0"),
        ),
        reconstruction=empty_reconstruction,
        exposure=RiskExposureSnapshot(0, 0, 0, 0, trusted=True),
        marks=(),
        now_monotonic_ns=101,
        max_account_age_ns=100,
        max_mark_age_ns=100,
        session_loss_limit=None,
        drawdown_limit=None,
    )
    assert preserved.global_halt is not None
    assert preserved.global_halt.active
    assert preserved.global_halt.reason == "prior_loss"


def test_reconciliation_requires_uncertainty_to_be_authoritatively_cleared() -> None:
    account = AuthoritativeRiskSnapshot(
        session_id="session-1",
        equity=Decimal("100"),
        account_trusted=True,
        observed_monotonic_ns=100,
        positions=(),
        unresolved_operation_ids=("op-uncertain",),
    )
    with pytest.raises(ReconciliationError, match="execution_state_unresolved"):
        reconcile_capital_state(
            previous=_reconcile_previous(halt=True),
            authoritative=account,
            reconstruction=PnLReconstruction((), Decimal("0"), ()),
            exposure=RiskExposureSnapshot(0, 0, 0, 0, trusted=True),
            marks=(),
            now_monotonic_ns=100,
            max_account_age_ns=100,
            max_mark_age_ns=100,
            session_loss_limit=None,
            drawdown_limit=None,
        )


def test_persistent_halts_survive_restart_and_require_explicit_reset(tmp_path: Path) -> None:
    path = tmp_path / "risk.sqlite3"
    state = trip_global_halt(_capital(), reason="drawdown", now_monotonic_ns=10)
    state = trip_strategy_halt(
        state,
        scope=HaltScope.STRATEGY_ID,
        scope_value="s1",
        reason="operator",
        now_monotonic_ns=11,
    )
    with SqliteRiskStateStore(path) as store:
        store.save(state, event_type="HALT", detail="fixture")
        assert store.event_count() == 1

    with SqliteRiskStateStore(path) as reopened:
        loaded = reopened.load()
        assert loaded is not None
        assert loaded.global_halt is not None and loaded.global_halt.active
        assert loaded.strategy_halted("s1", "FV-TAKE")

        with pytest.raises(ValueError, match="reason changed"):
            reset_global_halt(
                loaded,
                expected_reason="wrong",
                operator="operator",
                now_monotonic_ns=20,
            )

        reset = reset_global_halt(
            loaded,
            expected_reason="drawdown",
            operator="operator",
            now_monotonic_ns=20,
        )
        reset = clear_strategy_halt(
            reset,
            scope=HaltScope.STRATEGY_ID,
            scope_value="s1",
            operator="operator",
            now_monotonic_ns=21,
        )
        reopened.save(reset, event_type="RESET", detail="operator")
        assert reopened.event_count() == 2


def test_settlement_realtime_event_forces_authoritative_capital_resync() -> None:
    engine = AccountRealtimeStateEngine(tournament_id="t1")
    engine.apply_authoritative(
        AccountAuthoritativeSnapshot(
            tournament_id="t1",
            tournament_slug="cup",
            open_orders=(),
            positions=(),
            observed_at=datetime.fromisoformat("2026-09-30T12:00:00+00:00"),
        )
    )
    result = engine.handle_raw_batch(
        {
            "fills": [],
            "orderUpdates": [],
            "settlements": [
                {
                    "marketId": "m1",
                    "exchangeId": "1",
                    "outcomeSide": "YES",
                    "shares": "5",
                    "costBasis": "2",
                    "payout": "5",
                    "realizedPnl": "3",
                    "settlementOutcome": "YES",
                    "tournamentId": "t1",
                    "at": "2026-09-30T12:00:01Z",
                }
            ],
            "refunds": [],
            "collateralChanges": [],
            "delivery": {
                "model": "engine",
                "revision": 1,
                "previousRevision": 0,
                "correlationId": "settle-1",
                "sourceSequenceFrom": 1,
                "sourceSequenceThrough": 1,
            },
        },
        observed_at=datetime.fromisoformat("2026-09-30T12:00:01+00:00"),
    )
    assert result.requires_reconciliation
    assert not engine.trusted
    assert (
        result.transition is AccountTrustTransition.UNTRUSTED_ECONOMIC_EVENT_REQUIRES_RECONCILIATION
    )


def test_durable_global_halt_allows_recovery_preflight_and_stays_latched() -> None:
    halted = trip_global_halt(
        _capital(),
        reason="peak_drawdown_limit",
        now_monotonic_ns=10,
    )

    # A durable halt blocks fresh admission, not the authoritative recovery
    # reads/cancels needed to understand and remove existing risk.
    validate_restart_preflight(
        halted,
        session_id="session-1",
        profile_version="v1",
        live_recovery=True,
    )
    validate_restart_preflight(
        halted,
        session_id="session-1",
        profile_version="v1",
        live_recovery=False,
    )
    assert halted.global_halt is not None
    assert halted.global_halt.active
    assert halted.global_halt.reason == "peak_drawdown_limit"

    with pytest.raises(RuntimeError, match="different session"):
        validate_restart_preflight(
            halted,
            session_id="other-session",
            profile_version="v1",
            live_recovery=True,
        )
    with pytest.raises(RuntimeError, match="profile version mismatch"):
        validate_restart_preflight(
            halted,
            session_id="session-1",
            profile_version="other-version",
            live_recovery=True,
        )


def test_service_restart_marks_loaded_state_untrusted_until_reconciled(tmp_path: Path) -> None:
    path = tmp_path / "risk.sqlite3"
    with SqliteRiskStateStore(path) as store:
        service = CapitalControlService(
            store=store,
            max_account_age_ns=100,
            max_mark_age_ns=100,
            session_loss_limit=None,
            drawdown_limit=None,
        )
        initial = service.load_or_initialize(
            session_id="session-1",
            start_equity=Decimal("100"),
            start_unrealised_pnl=Decimal("0"),
            profile_version="v1",
            observed_monotonic_ns=0,
        )
        store.save(
            replace(
                initial,
                account_trusted=True,
                marks_trusted=True,
                reconciliation_complete=True,
                exposure=replace(initial.exposure, trusted=True),
            ),
            event_type="RECONCILED",
            detail="fixture",
        )

    with SqliteRiskStateStore(path) as reopened:
        service = CapitalControlService(
            store=reopened,
            max_account_age_ns=100,
            max_mark_age_ns=100,
            session_loss_limit=None,
            drawdown_limit=None,
        )
        loaded = service.load_or_initialize(
            session_id="session-1",
            start_equity=Decimal("999"),
            start_unrealised_pnl=Decimal("999"),
            profile_version="v1",
            observed_monotonic_ns=50,
        )
        assert loaded.session_start_equity == Decimal("100")
        assert loaded.session_start_unrealised_pnl == Decimal("0")
        assert not loaded.account_trusted
        assert not loaded.reconciliation_complete


def test_risk_context_source_publishes_state_without_hidden_globals() -> None:
    base = RiskContext(
        mode=ExecutionMode.LIVE,
        kill_switch=False,
        limits=_limits(),
        max_state_age_ns=1_000,
        require_capital_state=True,
    )
    source = RiskContextSource(base)
    assert source().capital_state is None
    state = _capital()
    source.publish(state)
    assert source().capital_state == state


def test_exposure_snapshot_tracks_market_tournament_open_and_uncertain() -> None:
    portfolio = RuntimePortfolio(
        positions=(RuntimePosition("1", "m1", "t1", 4.0, -4.0),),
        orders=(
            RuntimeOrderState("open", "2", "m2", "t1", 3.0, True, False),
            RuntimeOrderState("uncertain", "3", "m3", "t1", 2.0, False, True),
        ),
        account_trusted=True,
    )
    exposure = build_exposure_snapshot(
        portfolio,
        memberships=(
            MarketExposureGroup("m1", "t1", ("event",)),
            MarketExposureGroup("m2", "t1", ("event",)),
            MarketExposureGroup("m3", "t1", ("event",)),
        ),
    )
    assert exposure.gross_exposure == 9.0
    assert exposure.net_directional_exposure == -4.0
    assert exposure.open_order_exposure == 5.0
    assert exposure.uncertain_order_exposure == 2.0
    assert exposure.tournament("t1") == 9.0
    assert exposure.group("event") == 9.0


class _TransactionRest:
    def __init__(self, pages: tuple[TournamentTransactionPageDto, ...]) -> None:
        self.pages = pages
        self.calls = 0

    async def list_tournament_transactions(
        self,
        tournament_slug: str,
        *,
        limit: int = 200,
        cursor: str | None = None,
        event_types: tuple[str, ...] | None = None,
    ) -> TournamentTransactionPageDto:
        del limit, cursor, event_types
        assert tournament_slug == "cup"
        page = self.pages[self.calls]
        self.calls += 1
        return page


def _transaction_page(
    rows: list[dict[str, object]],
    *,
    has_more: bool = False,
    next_cursor: str | None = None,
    complete: bool = True,
) -> TournamentTransactionPageDto:
    return TournamentTransactionPageDto.model_validate(
        {
            "data": rows,
            "pagination": {
                "limit": 200,
                "hasMore": has_more,
                "nextCursor": next_cursor,
            },
            "coverage": {"complete": complete},
        }
    )


def _transaction(
    event_id: str,
    event_type: str,
    *,
    amount: str | None,
) -> dict[str, object]:
    return {
        "eventId": event_id,
        "eventType": event_type,
        "createdAt": "2026-09-30T12:00:00Z",
        "price": None,
        "quantity": "0",
        "exchangeId": None,
        "marketId": None,
        "settlementOption": None,
        "currentPrice": None,
        "marketTitle": None,
        "orderType": None,
        "contractType": None,
        "description": None,
        "amount": amount,
        "transactionType": None,
    }


def test_exposure_aggregate_invariants_seeded_fuzz() -> None:
    rng = Random(20260930)
    for case in range(100):
        positions = tuple(
            RuntimePosition(
                exchange_id=f"p-{case}-{index}",
                market_id=f"m{index % 7}",
                tournament_id="t1",
                gross_exposure=float(rng.randint(1, 8)),
                signed_quantity=float(rng.randint(-8, 8)),
            )
            for index in range(rng.randint(0, 12))
        )
        orders = tuple(
            RuntimeOrderState(
                logical_intent_id=f"o-{case}-{index}",
                exchange_id=f"oex-{case}-{index}",
                market_id=f"m{index % 7}",
                tournament_id="t1",
                reserved_exposure=float(rng.randint(1, 8)),
                open=bool(rng.getrandbits(1)),
                uncertain=bool(rng.getrandbits(1)),
            )
            for index in range(rng.randint(0, 12))
        )
        portfolio = RuntimePortfolio(
            positions=positions,
            orders=orders,
            account_trusted=True,
        )
        exposure = build_exposure_snapshot(portfolio)

        expected_open = sum(
            order.reserved_exposure for order in orders if order.open or order.uncertain
        )
        expected_uncertain = sum(order.reserved_exposure for order in orders if order.uncertain)
        expected_positions = sum(abs(item.gross_exposure) for item in positions)
        expected_net = sum(item.signed_quantity for item in positions)

        assert exposure.open_order_exposure == expected_open
        assert exposure.uncertain_order_exposure == expected_uncertain
        assert exposure.gross_exposure == expected_positions + expected_open
        assert exposure.net_directional_exposure == expected_net
        assert exposure.uncertain_order_exposure <= exposure.open_order_exposure
        assert sum(bucket.exposure for bucket in exposure.by_market) == (
            expected_positions + expected_open
        )


def test_external_cash_flow_scan_stops_at_durable_cursor() -> None:
    rest = _TransactionRest(
        (
            _transaction_page(
                [
                    _transaction("new-2", "trade", amount=None),
                    _transaction("new-1", "deposit", amount="25"),
                    _transaction("old", "trade", amount=None),
                ]
            ),
        )
    )
    scan = asyncio.run(
        scan_external_cash_flows(
            rest,
            tournament_slug="cup",
            prior_event_id="old",
        )
    )
    assert scan.delta == Decimal("25")
    assert scan.newest_event_id == "new-2"
    assert scan.prior_cursor_found
    assert scan.events_scanned == 2

    updated = apply_external_cash_flow_scan(_capital(), scan)
    assert updated.net_external_cash_flow == Decimal("25")
    assert updated.external_cash_flow_cursor == "new-2"


def test_external_cash_flow_cursor_survives_fill_cursor_overwrite() -> None:
    """2026-10-01 LIVE canary: the authoritative refresh overwrote the shared
    cursor with the (empty) fill cursor, so every scan re-counted the original
    deposit and RISK-002 latched peak_drawdown_limit with zero exposure."""
    deposit_only = (
        _transaction_page([_transaction("engine-1059258", "deposit", amount="100000")]),
    )
    first = asyncio.run(
        scan_external_cash_flows(
            _TransactionRest(deposit_only), tournament_slug="cup", prior_event_id=None
        )
    )
    state = apply_external_cash_flow_scan(_capital(), first)
    # Simulate reconcile_capital_state replacing the fill-reconstruction cursor.
    state = replace(state, realised_pnl_cursor=None)
    again = asyncio.run(
        scan_external_cash_flows(
            _TransactionRest(deposit_only),
            tournament_slug="cup",
            prior_event_id=state.external_cash_flow_cursor,
        )
    )
    assert again.delta == Decimal("0")
    final = apply_external_cash_flow_scan(state, again)
    assert final.net_external_cash_flow == state.net_external_cash_flow


def test_external_cash_flow_scan_fails_if_durable_cursor_disappears() -> None:
    rest = _TransactionRest(
        (
            _transaction_page(
                [_transaction("new", "trade", amount=None)],
            ),
        )
    )
    with pytest.raises(RuntimeError, match="cursor was not found"):
        asyncio.run(
            scan_external_cash_flows(
                rest,
                tournament_slug="cup",
                prior_event_id="missing",
            )
        )


def _position(quantity: str = "5") -> PositionReadDto:
    return PositionReadDto.model_validate(
        {
            "exchangeId": "1",
            "marketId": "m1",
            "marketTitle": "Fixture",
            "option": "YES",
            "settled": False,
            "quantity": quantity,
            "avgCost": "0.4",
            "currentPrice": "0.5",
            "marketValue": "2.5",
            "costBasis": "2",
            "unrealizedPnl": "0.5",
            "unrealizedPnlPct": "0.25",
            "moneyEarned": "0",
            "lots": [],
        }
    )


def _fill(order_id: int = 7, quantity: str = "5") -> FillReadDto:
    return FillReadDto.model_validate(
        {
            "id": 101,
            "orderId": order_id,
            "exchangeId": "1",
            "marketId": "m1",
            "price": "0.4",
            "quantity": quantity,
            "side": "yes",
            "filledAt": "2026-09-30T12:00:00Z",
        }
    )


def test_strategy_exposure_is_attributed_from_journal_and_authoritative_fills(
    tmp_path: Path,
) -> None:
    journal = ExecutionJournal(tmp_path / "execution.sqlite3")
    try:
        intent = RuntimeOrderIntent(
            intent_id="intent-1",
            exchange_id="1",
            market_id="m1",
            tournament_id="t1",
            outcome_side=OutcomeSide.YES,
            action=OrderAction.BUY,
            quantity=5,
            limit_price_ticks=80,
            strategy_id="strategy-a",
            decision_observation_ns=10,
        )
        envelope = ExecutionEnvelope.placement(
            logical_operation_id="op-1",
            operation_kind=OperationKind.SINGLE_PLACEMENT,
            sink_mode=ExecutionMode.LIVE,
            idempotency_key="key-1",
            intents=(intent,),
            created_monotonic_ns=11,
        )
        journal.record_before_dispatch(
            envelope,
            (intent,),
            audit=ExecutionAudit(
                strategy_family="FV-TAKE",
                strategy_id="strategy-a",
                signal_value=0.02,
                fair_value=0.55,
                decision_observation_ns=10,
                decision_monotonic_ns=11,
            ),
        )
        journal.record_event(
            logical_operation_id="op-1",
            logical_intent_id="intent-1",
            event_type="ACK",
            observed_monotonic_ns=12,
            exchange_id="1",
            exchange_order_id="7",
        )

        account = AccountAuthoritativeSnapshot(
            tournament_id="t1",
            tournament_slug="cup",
            open_orders=(),
            positions=(_position(),),
            observed_at=datetime.fromisoformat("2026-09-30T12:00:00+00:00"),
        )
        result = attribute_strategy_exposure(
            journal=journal,
            account=account,
            fills=(_fill(),),
            market_by_exchange={"1": "m1"},
        )
        assert result.complete
        assert result.reason == "complete"
        assert len(result.attributions) == 1
        assert result.attributions[0].strategy_id == "strategy-a"
        assert result.attributions[0].exposure == 5.0
    finally:
        journal.close()


def test_sig_fifo_cost_basis_reconstructs_no_position_in_yes_space() -> None:
    no_position = PositionReadDto.model_validate(
        {
            "exchangeId": "1",
            "marketId": "m1",
            "marketTitle": "Fixture",
            "option": "NO",
            "settled": False,
            "quantity": "-5",
            "avgCost": "0.3",
            "currentPrice": "0.6",
            "marketValue": "2",
            "costBasis": "1.5",
            "unrealizedPnl": "0.5",
            "unrealizedPnlPct": "0.3333333333",
            "moneyEarned": "0",
            "lots": [
                {
                    "lotId": "lot-1",
                    "side": "NO",
                    "quantity": "5",
                    "entryPrice": "0.3",
                    "openedAt": "2026-09-30T12:00:00Z",
                }
            ],
        }
    )
    account = AccountAuthoritativeSnapshot(
        tournament_id="t1",
        tournament_slug="cup",
        open_orders=(),
        positions=(no_position,),
        observed_at=datetime.fromisoformat("2026-09-30T12:00:00+00:00"),
    )
    reconstruction = reconstruct_sig_cost_basis(account)
    assert reconstruction.positions == (
        CostBasisPosition(
            exchange_id="1",
            market_id="m1",
            signed_quantity=Decimal("-5"),
            avg_entry_yes=Decimal("0.7"),
            canonical_cost_basis=Decimal("1.5"),
        ),
    )


def test_live_audit_fixture_reconstructs_real_no_lots() -> None:
    fixture_path = Path(__file__).parent / "fixtures/live_audit/no_side_positions.json"
    payload = json.loads(fixture_path.read_text())
    positions = tuple(PositionReadDto.model_validate(item) for item in payload["positions"])
    account = AccountAuthoritativeSnapshot(
        tournament_id="fixture-tournament",
        tournament_slug="fixture-cup",
        open_orders=(),
        positions=positions,
        observed_at=datetime.fromisoformat("2026-10-01T19:52:00+00:00"),
    )

    reconstruction = reconstruct_sig_cost_basis(account)

    assert [item.exchange_id for item in reconstruction.positions] == [
        "1045",
        "1077",
        "960",
    ]
    assert [item.signed_quantity for item in reconstruction.positions] == [
        Decimal("-193"),
        Decimal("-100"),
        Decimal("-10"),
    ]
    assert [item.canonical_cost_basis for item in reconstruction.positions] == [
        Decimal("130.81"),
        Decimal("98"),
        Decimal("5.65"),
    ]


def test_sig_normalization_reconciles_fifo_cost_basis_marks_and_authoritative_pnl() -> None:
    position = PositionReadDto.model_validate(
        {
            "exchangeId": "1",
            "marketId": "m1",
            "marketTitle": "Fixture",
            "option": "YES",
            "settled": False,
            "quantity": "5",
            "avgCost": "0.4",
            "currentPrice": "0.5",
            "marketValue": "2.5",
            "costBasis": "2",
            "unrealizedPnl": "0.5",
            "unrealizedPnlPct": "0.25",
            "moneyEarned": "0",
            "lots": [
                {
                    "lotId": "lot-1",
                    "side": "YES",
                    "quantity": "5",
                    "entryPrice": "0.4",
                    "openedAt": "2026-09-30T12:00:00Z",
                }
            ],
        }
    )
    account = AccountAuthoritativeSnapshot(
        tournament_id="t1",
        tournament_slug="cup",
        open_orders=(),
        positions=(position,),
        observed_at=datetime.fromisoformat("2026-09-30T12:00:00+00:00"),
    )
    pnl = PortfolioPnlDto.model_validate(
        {
            "period": "all",
            "periodStart": None,
            "periodEnd": "2026-09-30T12:00:00Z",
            "periodPnl": "0.5",
            "unrealizedPnl": "0.5",
            "totalAccountValue": "100.5",
            "totalHoldingsValue": "2.5",
            "totalCostBasis": "2",
            "roi": None,
            "sharpe": None,
        }
    )
    inputs = normalize_sig_risk_inputs(
        session_id="session-1",
        session_start_equity=Decimal("100"),
        session_start_unrealised_pnl=Decimal("0"),
        account=account,
        pnl=pnl,
        observed_monotonic_ns=100,
        net_external_cash_flow=Decimal("0"),
    )
    reconciled = reconcile_capital_state(
        previous=_reconcile_previous(),
        authoritative=inputs.authoritative,
        reconstruction=inputs.reconstruction,
        exposure=inputs.exposure,
        marks=inputs.marks,
        now_monotonic_ns=100,
        max_account_age_ns=100,
        max_mark_age_ns=100,
        session_loss_limit=None,
        drawdown_limit=None,
    )
    assert reconciled.realised_pnl == Decimal("0.0")
    assert reconciled.unrealised_pnl == Decimal("0.5")
    assert reconciled.current_equity == Decimal("100.5")


def test_session_pnl_decomposition_uses_start_unrealised_baseline() -> None:
    position = PositionReadDto.model_validate(
        {
            "exchangeId": "1",
            "marketId": "m1",
            "marketTitle": "Fixture",
            "option": "YES",
            "settled": False,
            "quantity": "5",
            "avgCost": "0.4",
            "currentPrice": "0.5",
            "marketValue": "2.5",
            "costBasis": "2",
            "unrealizedPnl": "0.5",
            "unrealizedPnlPct": "0.25",
            "moneyEarned": "0",
            "lots": [
                {
                    "lotId": "lot-1",
                    "side": "YES",
                    "quantity": "5",
                    "entryPrice": "0.4",
                    "openedAt": "2026-09-30T12:00:00Z",
                }
            ],
        }
    )
    account = AccountAuthoritativeSnapshot(
        tournament_id="t1",
        tournament_slug="cup",
        open_orders=(),
        positions=(position,),
        observed_at=datetime.fromisoformat("2026-09-30T12:00:00+00:00"),
    )
    pnl = PortfolioPnlDto.model_validate(
        {
            "period": "all",
            "periodStart": None,
            "periodEnd": "2026-09-30T12:00:00Z",
            "periodPnl": "0.5",
            "unrealizedPnl": "0.5",
            "totalAccountValue": "100.5",
            "totalHoldingsValue": "2.5",
            "totalCostBasis": "2",
            "roi": None,
            "sharpe": None,
        }
    )
    inputs = normalize_sig_risk_inputs(
        session_id="session-1",
        session_start_equity=Decimal("100"),
        session_start_unrealised_pnl=Decimal("0.2"),
        account=account,
        pnl=pnl,
        observed_monotonic_ns=100,
        net_external_cash_flow=Decimal("0"),
    )
    assert inputs.authoritative.realised_pnl == Decimal("0.2")

    previous = replace(
        _reconcile_previous(),
        session_start_unrealised_pnl=Decimal("0.2"),
        unrealised_pnl=Decimal("0.2"),
    )
    reconciled = reconcile_capital_state(
        previous=previous,
        authoritative=inputs.authoritative,
        reconstruction=inputs.reconstruction,
        exposure=inputs.exposure,
        marks=inputs.marks,
        now_monotonic_ns=100,
        max_account_age_ns=100,
        max_mark_age_ns=100,
        session_loss_limit=None,
        drawdown_limit=None,
    )
    assert reconciled.session_pnl == Decimal("0.5")
    assert reconciled.realised_pnl == Decimal("0.2")
    assert reconciled.unrealised_pnl - reconciled.session_start_unrealised_pnl == Decimal("0.3")


def test_strategy_attribution_handles_signed_no_fill_quantity(tmp_path: Path) -> None:
    journal = ExecutionJournal(tmp_path / "execution-no.sqlite3")
    try:
        intent = RuntimeOrderIntent(
            intent_id="intent-no",
            exchange_id="1",
            market_id="m1",
            tournament_id="t1",
            outcome_side=OutcomeSide.NO,
            action=OrderAction.BUY,
            quantity=5,
            limit_price_ticks=60,
            strategy_id="strategy-no",
            decision_observation_ns=10,
        )
        envelope = ExecutionEnvelope.placement(
            logical_operation_id="op-no",
            operation_kind=OperationKind.SINGLE_PLACEMENT,
            sink_mode=ExecutionMode.LIVE,
            idempotency_key="key-no",
            intents=(intent,),
            created_monotonic_ns=11,
        )
        journal.record_before_dispatch(
            envelope,
            (intent,),
            audit=ExecutionAudit(
                strategy_family="FV-TAKE",
                strategy_id="strategy-no",
                signal_value=0.02,
                fair_value=0.45,
                decision_observation_ns=10,
                decision_monotonic_ns=11,
            ),
        )
        journal.record_event(
            logical_operation_id="op-no",
            logical_intent_id="intent-no",
            event_type="ACK",
            observed_monotonic_ns=12,
            exchange_id="1",
            exchange_order_id="8",
        )
        no_position = _position(quantity="-5").model_copy(update={"option": "NO"})
        account = AccountAuthoritativeSnapshot(
            tournament_id="t1",
            tournament_slug="cup",
            open_orders=(),
            positions=(no_position,),
            observed_at=datetime.fromisoformat("2026-09-30T12:00:00+00:00"),
        )
        fill = FillReadDto.model_validate(
            {
                "id": 102,
                "orderId": 8,
                "exchangeId": "1",
                "marketId": "m1",
                "price": "0.4",
                "quantity": "-5",
                "side": "no",
                "filledAt": "2026-09-30T12:00:00Z",
            }
        )
        result = attribute_strategy_exposure(
            journal=journal,
            account=account,
            fills=(fill,),
            market_by_exchange={"1": "m1"},
        )
        assert result.complete
        assert result.attributions[0].strategy_id == "strategy-no"
        assert result.attributions[0].exposure == 5.0
    finally:
        journal.close()


class _MarkProvider:
    def __init__(self, price: Decimal) -> None:
        self.price = price

    def marks_for(
        self,
        exchange_ids: frozenset[str],
        *,
        now_monotonic_ns: int,
    ) -> tuple[RiskMark, ...]:
        assert exchange_ids == frozenset({"1"})
        return (
            RiskMark(
                exchange_id="1",
                market_id="m1",
                price=self.price,
                source="fixture",
                observed_monotonic_ns=now_monotonic_ns,
                trusted=True,
                version="v1",
                method="fixture",
            ),
        )


def test_realtime_drawdown_latches_and_checkpoints_once() -> None:
    base_state = replace(
        _capital(equity="100", peak="100"),
        realised_pnl=Decimal("0"),
        unrealised_pnl=Decimal("0"),
    )
    checkpoints: list[str] = []
    provider = _MarkProvider(Decimal("0.4"))
    source = RiskContextSource(
        RiskContext(
            mode=ExecutionMode.LIVE,
            kill_switch=False,
            limits=_limits(drawdown_limit=5),
            max_state_age_ns=1_000,
            max_mark_age_ns=1_000_000_000,
            require_capital_state=True,
        ),
        state=base_state,
        valuation_positions=(
            RiskValuationPosition(
                exchange_id="1",
                market_id="m1",
                signed_quantity=Decimal("10"),
                baseline_mark=Decimal("0.5"),
                baseline_unrealised_pnl=Decimal("0"),
            ),
        ),
        mark_provider=provider,
        halt_checkpoint=lambda state, reason: checkpoints.append(reason),
    )
    first = source().capital_state
    assert first is not None
    assert first.drawdown == Decimal("1.0")
    assert first.global_halt is None

    provider.price = Decimal("0.0")
    second = source().capital_state
    assert second is not None
    assert second.drawdown == Decimal("5.0")
    assert second.global_halt is not None
    assert second.global_halt.reason == "peak_drawdown_limit"
    assert checkpoints == ["peak_drawdown_limit"]

    third = source().capital_state
    assert third is not None and third.global_halt is not None
    assert checkpoints == ["peak_drawdown_limit"]


@pytest.mark.parametrize(
    ("sig_unrealised", "accepted"),
    [(Decimal("-0.15"), True), (Decimal("-0.20"), False)],
)
def test_reconciliation_tolerates_sig_cent_rounding_only(
    sig_unrealised: Decimal, accepted: bool
) -> None:
    # Live 2026-10-01 18:33Z: short 10 YES via NO@0.565, SIG mark 0.450297.
    # Exact local PnL is -0.15297; SIG reports unrealizedPnl rounded to -0.15.
    reconstruction = PnLReconstruction(
        positions=(
            CostBasisPosition("960", "271", Decimal("-10"), Decimal("0.435"), Decimal("5.65")),
        ),
        realised_pnl=Decimal("0"),
        processed_fill_ids=("f1",),
    )
    authoritative = AuthoritativeRiskSnapshot(
        session_id="session-1",
        equity=Decimal("100") + sig_unrealised,
        account_trusted=True,
        observed_monotonic_ns=100,
        positions=(
            AuthoritativeRiskPosition(
                "960", "271", Decimal("-10"), Decimal("5.65"), sig_unrealised
            ),
        ),
        realised_pnl=Decimal("0"),
        unrealised_pnl=sig_unrealised,
    )

    def reconcile() -> CapitalRiskState:
        return reconcile_capital_state(
            previous=_reconcile_previous(),
            authoritative=authoritative,
            reconstruction=reconstruction,
            exposure=RiskExposureSnapshot(10, -10, 0, 0, trusted=True),
            marks=(RiskMark("960", "271", Decimal("0.450297"), "sig", 100, True, "v1", "rest"),),
            now_monotonic_ns=100,
            max_account_age_ns=100,
            max_mark_age_ns=100,
            session_loss_limit=None,
            drawdown_limit=None,
        )

    if accepted:
        assert reconcile().marks_trusted
    else:
        with pytest.raises(ReconciliationError, match="mark_pnl_disagreement"):
            reconcile()


@pytest.mark.parametrize(
    ("summary", "accepted"), [(Decimal("-0.55"), True), (Decimal("-3.0"), False)]
)
def test_reconciliation_allows_one_tick_drift_between_position_and_pnl_reads(
    summary: Decimal, accepted: bool
) -> None:
    # Live 2026-10-01 19:43Z: positions read, then the PnL summary seconds later.
    authoritative = AuthoritativeRiskSnapshot(
        session_id="session-1",
        equity=Decimal("100") + summary,
        account_trusted=True,
        observed_monotonic_ns=100,
        positions=(
            AuthoritativeRiskPosition(
                "960", "271", Decimal("-100"), Decimal("56.5"), Decimal("-0.15")
            ),
        ),
        realised_pnl=Decimal("0"),
        unrealised_pnl=summary,
    )

    def reconcile() -> CapitalRiskState:
        return reconcile_capital_state(
            previous=_reconcile_previous(),
            authoritative=authoritative,
            reconstruction=None,
            exposure=RiskExposureSnapshot(100, -100, 0, 0, trusted=True),
            marks=(RiskMark("960", "271", Decimal("0.4365"), "sig", 100, True, "v1", "rest"),),
            now_monotonic_ns=100,
            max_account_age_ns=100,
            max_mark_age_ns=100,
            session_loss_limit=None,
            drawdown_limit=None,
        )

    if accepted:
        assert reconcile().reconciliation_complete
    else:
        with pytest.raises(ReconciliationError, match="unrealised_pnl_disagreement"):
            reconcile()


def test_sig_cost_basis_uses_position_basis_after_partial_close() -> None:
    # Live 2026-10-01 20:00Z payload: surviving lot 93@0.67 (62.31) but SIG
    # costBasis 63.03 / avgCost 0.677772, which its unrealizedPnl -7.72 uses.
    position = PositionReadDto.model_validate(
        {
            "exchangeId": "1045",
            "marketId": "356",
            "marketTitle": "Will the Democratic Party win the Kansas Senate?",
            "option": "YES",
            "settled": False,
            "quantity": -93,
            "avgCost": 0.677772,
            "currentPrice": 0.405269,
            "marketValue": 55.31,
            "costBasis": 63.03,
            "unrealizedPnl": -7.72,
            "unrealizedPnlPct": -12.25,
            "moneyEarned": 0,
            "lots": [
                {
                    "lotId": "2902571",
                    "side": "NO",
                    "quantity": 93,
                    "entryPrice": 0.67,
                    "openedAt": "2026-10-01T19:47:54.5909454+00:00",
                }
            ],
        }
    )
    account = AccountAuthoritativeSnapshot(
        tournament_id="t1",
        tournament_slug="cup",
        open_orders=(),
        positions=(position,),
        observed_at=datetime.fromisoformat("2026-10-01T20:00:00+00:00"),
    )
    (rebuilt,) = reconstruct_sig_cost_basis(account).positions
    assert rebuilt.canonical_cost_basis == Decimal("63.03")
    local_pnl = rebuilt.signed_quantity * (Decimal("0.405269") - rebuilt.avg_entry_yes)
    assert abs(local_pnl - Decimal("-7.72")) <= Decimal("0.01")
