"""RISK-002 synchronous central-risk benchmark."""

from __future__ import annotations

import argparse
import json
import statistics
import time
from dataclasses import asdict, dataclass

from predictions_cup.execution.models import ExecutionMode
from predictions_cup.risk import (
    CapitalRiskState,
    ExposureBucket,
    MarketExposureGroup,
    RiskContext,
    RiskExposureSnapshot,
    RiskLimits,
    evaluate_risk,
)
from predictions_cup.runtime import (
    OrderAction,
    OutcomeSide,
    RuntimeMarket,
    RuntimePortfolio,
    RuntimePosition,
    RuntimeSnapshot,
)
from predictions_cup.strategy.core import CandidateLeg, Opportunity, StrategyFamily


@dataclass(frozen=True, slots=True)
class BenchResult:
    name: str
    iterations: int
    mean_ns: float
    median_ns: float
    p95_ns: float
    p99_ns: float
    throughput_per_second: float


def _percentile(values: list[int], fraction: float) -> float:
    ordered = sorted(values)
    index = min(len(ordered) - 1, int((len(ordered) - 1) * fraction))
    return float(ordered[index])


def _bench(name: str, iterations: int, fn: object) -> BenchResult:
    if not callable(fn):
        raise TypeError("benchmark target must be callable")
    for _ in range(100):
        fn()
    samples: list[int] = []
    started = time.perf_counter_ns()
    for _ in range(iterations):
        call_started = time.perf_counter_ns()
        fn()
        samples.append(time.perf_counter_ns() - call_started)
    elapsed = time.perf_counter_ns() - started
    return BenchResult(
        name=name,
        iterations=iterations,
        mean_ns=elapsed / iterations,
        median_ns=statistics.median(samples),
        p95_ns=_percentile(samples, 0.95),
        p99_ns=_percentile(samples, 0.99),
        throughput_per_second=(
            1_000_000_000.0 / (elapsed / iterations)
            if elapsed > 0
            else float("inf")
        ),
    )


def _small_fixture() -> tuple[RuntimeSnapshot, RiskContext, Opportunity]:
    from decimal import Decimal

    snapshot = RuntimeSnapshot(
        markets=(
            RuntimeMarket(
                market_id="m1",
                status="open",
                exchange_ids=("1",),
                tournament_id="t1",
                mapping_accepted=True,
                tradeable=True,
            ),
        ),
        books=(),
        portfolio=RuntimePortfolio(account_trusted=True),
        observation_monotonic_ns=1_000_000,
    )
    capital = CapitalRiskState(
        session_id="cup",
        session_start_equity=Decimal("1000"),
        session_start_unrealised_pnl=Decimal("0"),
        realised_pnl=Decimal("0"),
        unrealised_pnl=Decimal("0"),
        current_equity=Decimal("1000"),
        peak_session_equity=Decimal("1000"),
        drawdown=Decimal("0"),
        net_external_cash_flow=Decimal("0"),
        exposure=RiskExposureSnapshot(
            gross_exposure=0.0,
            net_directional_exposure=0.0,
            open_order_exposure=0.0,
            uncertain_order_exposure=0.0,
            trusted=True,
            strategy_attribution_complete=True,
            group_classification_complete=True,
        ),
        account_trusted=True,
        account_observed_monotonic_ns=1_000_000,
        marks_trusted=True,
        oldest_mark_observed_monotonic_ns=None,
        reconciliation_complete=True,
        global_halt=None,
        strategy_halts=(),
        limit_profile_version="bench-v1",
    )
    context = RiskContext(
        mode=ExecutionMode.LIVE,
        kill_switch=False,
        limits=RiskLimits(
            max_order_size=10,
            max_gross_exposure=100.0,
            max_per_market_exposure=25.0,
            max_open_order_exposure=50.0,
            max_concurrent_open_orders=10,
            session_loss_limit=50.0,
            drawdown_limit=50.0,
        ),
        max_state_age_ns=1_000_000,
        capital_state=capital,
        max_account_age_ns=1_000_000,
        max_mark_age_ns=1_000_000,
        require_capital_state=True,
    )
    opportunity = Opportunity(
        family=StrategyFamily.FV_TAKE,
        strategy_id="fv-a",
        legs=(
            CandidateLeg(
                exchange_id="1",
                market_id="m1",
                tournament_id="t1",
                outcome_side=OutcomeSide.YES,
                action=OrderAction.BUY,
                quantity=2,
                limit_price_ticks=100,
            ),
        ),
        gross_edge=0.02,
        fair_value=0.55,
        decision_observation_ns=1_000_000,
    )
    return snapshot, context, opportunity


def _fixture() -> tuple[RuntimeSnapshot, RiskContext, Opportunity]:
    markets = tuple(
        RuntimeMarket(
            market_id=f"m{index}",
            status="open",
            exchange_ids=(str(index),),
            tournament_id="t1",
            mapping_accepted=True,
            tradeable=True,
        )
        for index in range(1, 238)
    )
    positions = tuple(
        RuntimePosition(
            exchange_id=str(index),
            market_id=f"m{index}",
            tournament_id="t1",
            gross_exposure=1.0,
            signed_quantity=1.0 if index % 2 else -1.0,
        )
        for index in range(2, 238)
    )
    snapshot = RuntimeSnapshot(
        markets=markets,
        books=(),
        portfolio=RuntimePortfolio(
            positions=positions,
            orders=(),
            account_trusted=True,
        ),
        observation_monotonic_ns=1_000_000,
    )
    by_market = tuple(ExposureBucket(f"m{index}", 1.0) for index in range(2, 238))
    by_group = tuple(
        ExposureBucket(f"group-{index}", 20.0)
        for index in range(12)
    )
    exposure = RiskExposureSnapshot(
        gross_exposure=236.0,
        net_directional_exposure=0.0,
        open_order_exposure=0.0,
        uncertain_order_exposure=0.0,
        by_market=by_market,
        by_strategy=(
            ExposureBucket("maker-a", 80.0),
            ExposureBucket("pred-a", 70.0),
            ExposureBucket("fv-a", 86.0),
        ),
        by_tournament=(ExposureBucket("t1", 236.0),),
        by_group=by_group,
        trusted=True,
        strategy_attribution_complete=True,
        group_classification_complete=True,
    )
    from decimal import Decimal

    capital = CapitalRiskState(
        session_id="cup",
        session_start_equity=Decimal("1000"),
        session_start_unrealised_pnl=Decimal("0"),
        realised_pnl=Decimal("2"),
        unrealised_pnl=Decimal("3"),
        current_equity=Decimal("1005"),
        peak_session_equity=Decimal("1008"),
        drawdown=Decimal("3"),
        net_external_cash_flow=Decimal("0"),
        exposure=exposure,
        account_trusted=True,
        account_observed_monotonic_ns=1_000_000,
        marks_trusted=True,
        oldest_mark_observed_monotonic_ns=1_000_000,
        reconciliation_complete=True,
        global_halt=None,
        strategy_halts=(),
        limit_profile_version="bench-v1",
    )
    limits = RiskLimits(
        max_order_size=10,
        max_gross_exposure=300.0,
        max_per_market_exposure=10.0,
        max_open_order_exposure=100.0,
        max_concurrent_open_orders=100,
        max_per_strategy_exposure=100.0,
        max_event_group_exposure=50.0,
        max_tournament_exposure=300.0,
        session_loss_limit=50.0,
        drawdown_limit=50.0,
    )
    groups = tuple(
        MarketExposureGroup(
            market_id=f"m{index}",
            tournament_id="t1",
            group_ids=(f"group-{index % 12}",),
        )
        for index in range(1, 238)
    )
    context = RiskContext(
        mode=ExecutionMode.LIVE,
        kill_switch=False,
        limits=limits,
        max_state_age_ns=1_000_000,
        capital_state=capital,
        exposure_groups=groups,
        max_account_age_ns=1_000_000,
        max_mark_age_ns=1_000_000,
        require_capital_state=True,
    )
    opportunity = Opportunity(
        family=StrategyFamily.FV_TAKE,
        strategy_id="fv-a",
        legs=(
            CandidateLeg(
                exchange_id="1",
                market_id="m1",
                tournament_id="t1",
                outcome_side=OutcomeSide.YES,
                action=OrderAction.BUY,
                quantity=2,
                limit_price_ticks=100,
            ),
        ),
        gross_edge=0.02,
        fair_value=0.55,
        decision_observation_ns=1_000_000,
    )
    return snapshot, context, opportunity


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--iterations", type=int, default=20_000)
    args = parser.parse_args()
    small_snapshot, small_context, small_opportunity = _small_fixture()
    snapshot, context, opportunity = _fixture()

    def normal_approved() -> object:
        return evaluate_risk(small_opportunity, small_snapshot, small_context)

    def portfolio_approved() -> object:
        return evaluate_risk(opportunity, snapshot, context)

    denied_context = RiskContext(
        mode=small_context.mode,
        kill_switch=small_context.kill_switch,
        limits=RiskLimits(
            max_order_size=1,
            max_gross_exposure=300.0,
            max_per_market_exposure=10.0,
            max_open_order_exposure=100.0,
            max_concurrent_open_orders=100,
        ),
        max_state_age_ns=small_context.max_state_age_ns,
        capital_state=small_context.capital_state,
        exposure_groups=small_context.exposure_groups,
        max_account_age_ns=small_context.max_account_age_ns,
        max_mark_age_ns=small_context.max_mark_age_ns,
        require_capital_state=True,
    )
    def denied() -> object:
        return evaluate_risk(small_opportunity, small_snapshot, denied_context)

    if not normal_approved().approved:
        raise AssertionError("normal approval fixture unexpectedly rejected")
    if not portfolio_approved().approved:
        raise AssertionError("portfolio approval fixture unexpectedly rejected")
    if denied().approved:
        raise AssertionError("rejection fixture unexpectedly approved")

    results = (
        _bench("approval_normal", args.iterations, normal_approved),
        _bench("rejection_order_cap", args.iterations, denied),
        _bench(
            "approval_237_market_multi_strategy_group",
            args.iterations,
            portfolio_approved,
        ),
    )
    print(json.dumps([asdict(item) for item in results], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
