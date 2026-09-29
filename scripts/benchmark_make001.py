#!/usr/bin/env python3
"""Target-host/internal benchmark harness for MAKE-001.

No network writes occur. This script is safe to run on the target host and emits
machine-readable JSON for the exact checked-out commit.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import platform
import statistics
import subprocess
import sys
import tempfile
import time
from collections import defaultdict
from collections.abc import Callable, Mapping
from dataclasses import replace
from pathlib import Path
from typing import cast

from predictions_cup.execution.journal import ExecutionJournal
from predictions_cup.execution.models import ExecutionMode, LifecycleState
from predictions_cup.execution.planner import build_execution_plan
from predictions_cup.maker import (
    BinaryCaraInventoryModel,
    ConservativeEligibilityPolicy,
    ConservativeSpreadPolicy,
    DirectPolymarketFairValueProvider,
    ExternalQuoteState,
    ActiveQuote,
    InventoryConfidenceSizePolicy,
    MakerConfig,
    MakerCoordinator,
    MakerCycleResult,
    MakerEngine,
    MakerMarketSnapshot,
    MakerQuoteState,
    MakerRuntimeLoop,
    MakerSourceBridge,
    MakerStateChange,
    NullPredictiveAdjuster,
    NullToxicityProvider,
    QuoteLifecycleManager,
    QuoteRegistry,
    QuoteSide,
)
from predictions_cup.maker.contracts import QuoteContext
from predictions_cup.mapping.crosswalk import load_document
from predictions_cup.mapping.models import MappingClass, MappingStatus
from predictions_cup.risk.core import RiskContext, RiskLimits, evaluate_risk
from predictions_cup.runtime.models import (
    OrderAction,
    OutcomeSide,
    RuntimeBook,
    RuntimeLevel,
    RuntimeMarket,
    RuntimePortfolio,
    RuntimePosition,
    RuntimeSnapshot,
)
from predictions_cup.strategy.core import CandidateLeg, Opportunity, StrategyFamily

NOW = 10_000_000_000


def _git_sha() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def _percentile(values: list[int], p: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    index = min(len(ordered) - 1, max(0, math.ceil(p * len(ordered)) - 1))
    return ordered[index] / 1_000.0


def _stats(values: list[int]) -> dict[str, float]:
    total_ns = sum(values)
    return {
        "count": float(len(values)),
        "mean_us": statistics.fmean(values) / 1_000.0 if values else 0.0,
        "p50_us": _percentile(values, 0.50),
        "p95_us": _percentile(values, 0.95),
        "p99_us": _percentile(values, 0.99),
        "throughput_per_s": (
            len(values) * 1_000_000_000.0 / total_ns if total_ns > 0 else 0.0
        ),
    }


def _measure(iterations: int, fn: Callable[[], object]) -> list[int]:
    durations: list[int] = []
    for _ in range(iterations):
        started = time.perf_counter_ns()
        fn()
        durations.append(time.perf_counter_ns() - started)
    return durations


def _build_fixture(mapping_path: Path):
    document = load_document(mapping_path)
    provider = DirectPolymarketFairValueProvider(
        document,
        max_age_ns=1_000_000_000,
    )
    engine = MakerEngine(
        fair_value=provider,
        predictive=NullPredictiveAdjuster(),
        toxicity=NullToxicityProvider(),
        inventory=BinaryCaraInventoryModel(),
        spread=ConservativeSpreadPolicy(),
        size=InventoryConfidenceSizePolicy(base_size=4),
        eligibility=ConservativeEligibilityPolicy(
            max_bbo_age_ns=1_000_000_000,
            max_fv_age_ns=1_000_000_000,
            max_account_age_ns=1_000_000_000,
            max_inventory_age_ns=1_000_000_000,
            max_optional_signal_age_ns=1_000_000_000,
        ),
        config=MakerConfig(max_abs_inventory=10.0),
    )

    exchange_ids_by_market: dict[str, list[str]] = defaultdict(list)
    external: dict[str, ExternalQuoteState] = {}
    tradeable_records = []
    for record in document.records:
        exchange_ids_by_market[record.sig_market_id].append(record.sig_exchange_id)
        if (
            record.status is not MappingStatus.VERIFIED
            or record.mapping_class in {MappingClass.NO_TRADE, MappingClass.MODEL_ONLY}
        ):
            continue
        tradeable_records.append(record)
        if record.direct_polymarket is not None:
            token_ids = (record.direct_polymarket.mapped_token_id,)
            midpoint = 0.50
        else:
            token_ids = tuple(
                component.mapped_token_id
                for component in record.polymarket_components
            )
            midpoint = 0.50 / max(1, len(token_ids))
        for token_id in token_ids:
            if token_id in external:
                continue
            half = min(0.0025, midpoint * 0.25)
            external[token_id] = ExternalQuoteState(
                token_id=token_id,
                best_bid=max(0.0001, midpoint - half),
                best_ask=min(0.9999, midpoint + half),
                observed_monotonic_ns=NOW,
                trusted=True,
                source_version="synthetic-benchmark",
            )

    markets = tuple(
        RuntimeMarket(
            market_id=market_id,
            status="open",
            exchange_ids=tuple(sorted(exchange_ids)),
            tournament_id=document.tournament_id,
            mapping_accepted=True,
            tradeable=True,
        )
        for market_id, exchange_ids in sorted(exchange_ids_by_market.items())
    )
    books = tuple(
        RuntimeBook(
            exchange_id=record.sig_exchange_id,
            market_id=record.sig_market_id,
            tournament_id=document.tournament_id,
            bids=(RuntimeLevel(price_ticks=98, quantity=100.0),),
            asks=(RuntimeLevel(price_ticks=102, quantity=100.0),),
            trusted_depth=True,
            observed_monotonic_ns=NOW,
        )
        for record in tradeable_records
    )
    runtime = RuntimeSnapshot(
        markets=markets,
        books=books,
        portfolio=RuntimePortfolio(account_trusted=True),
        observation_monotonic_ns=NOW,
    )
    snapshots = {
        record.sig_exchange_id: MakerMarketSnapshot(
            runtime=runtime,
            exchange_id=record.sig_exchange_id,
            market_id=record.sig_market_id,
            tournament_id=document.tournament_id,
            now_monotonic_ns=NOW,
            sig_bbo_observed_ns=NOW,
            sig_bbo_trusted=True,
            sig_depth_observed_ns=NOW,
            sig_depth_trusted=True,
            account_observed_ns=NOW,
            inventory_observed_ns=NOW,
            external_quotes=external,
            volatility=0.01,
        )
        for record in tradeable_records
    }
    return document, engine, snapshots



class _BenchmarkBridge:
    def __init__(self, snapshot: MakerMarketSnapshot) -> None:
        self._snapshot = snapshot
        self.tradeable_exchange_ids = frozenset({snapshot.exchange_id})

    def sig_exchanges_for_polymarket_token(self, token_id: str) -> frozenset[str]:
        del token_id
        return frozenset({self._snapshot.exchange_id})

    def build_many(
        self,
        exchange_ids: frozenset[str] | set[str] | tuple[str, ...],
        **kwargs: object,
    ) -> dict[str, MakerMarketSnapshot]:
        del kwargs
        return {
            exchange_id: self._snapshot
            for exchange_id in exchange_ids
            if exchange_id == self._snapshot.exchange_id
        }


class _BenchmarkCoordinator:
    def __init__(self, engine: MakerEngine) -> None:
        self._engine = engine

    async def on_state_change(
        self,
        change: MakerStateChange,
        snapshots: Mapping[str, MakerMarketSnapshot],
    ) -> MakerCycleResult:
        del change
        for snapshot in snapshots.values():
            self._engine.quote(snapshot)
        return MakerCycleResult((), (), (), ())

    def activate_kill_switch(self, reason: str) -> None:
        del reason


async def _runtime_cycle_measure(
    iterations: int,
    *,
    engine: MakerEngine,
    snapshot: MakerMarketSnapshot,
) -> list[int]:
    bridge = _BenchmarkBridge(snapshot)
    coordinator = _BenchmarkCoordinator(engine)
    runtime = MakerRuntimeLoop(
        bridge=cast(MakerSourceBridge, bridge),
        coordinator=cast(MakerCoordinator, coordinator),
        polymarket_feed_trusted=lambda: True,
    )
    durations: list[int] = []
    for _ in range(iterations):
        started = time.perf_counter_ns()
        runtime.notify_sig(
            {snapshot.exchange_id},
            observed_monotonic_ns=started,
        )
        await runtime.drain_once()
        durations.append(time.perf_counter_ns() - started)
    return durations


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--mapping",
        type=Path,
        default=Path("data/mappings/sig_polymarket_2026.json"),
    )
    parser.add_argument("--iterations", type=int, default=20_000)
    parser.add_argument("--bursts", type=int, default=100)
    parser.add_argument("--journal-iterations", type=int, default=200)
    args = parser.parse_args()

    document, engine, snapshots = _build_fixture(args.mapping)
    if not snapshots:
        raise RuntimeError("canonical mapping contains no tradeable maker exchanges")
    representative = snapshots[next(iter(sorted(snapshots)))]

    # Warm cache/interpreter paths before measurement.
    for _ in range(2_000):
        engine.quote(representative)

    quote_durations = _measure(
        args.iterations,
        lambda: engine.quote(representative),
    )

    provider = DirectPolymarketFairValueProvider(
        document,
        max_age_ns=1_000_000_000,
    )
    fv_durations = _measure(
        args.iterations,
        lambda: provider.fair_value(representative),
    )
    pred = NullPredictiveAdjuster()
    tox = NullToxicityProvider()
    fair = provider.fair_value(representative)
    prediction = pred.adjust(representative, fair)
    toxicity = tox.estimate(representative, fair)
    plugin_durations = _measure(
        args.iterations,
        lambda: (
            pred.adjust(representative, fair),
            tox.estimate(representative, fair),
        ),
    )
    if fair.value is None:
        raise RuntimeError("representative FV unexpectedly unavailable")
    quote_context = QuoteContext(
        snapshot=representative,
        raw_fair_value=fair,
        adjusted_fair_value=fair.value,
        prediction=prediction,
        toxicity=toxicity,
        signed_inventory=0.0,
        max_abs_inventory=10.0,
    )
    inventory_model = BinaryCaraInventoryModel()
    reservation_durations = _measure(
        args.iterations,
        lambda: inventory_model.reservation_price(quote_context),
    )
    reservation = inventory_model.reservation_price(quote_context)
    spread_policy = ConservativeSpreadPolicy()
    size_policy = InventoryConfidenceSizePolicy(base_size=4)
    policy_context = replace(
        quote_context,
        reservation_price=reservation,
    )
    spread_size_durations = _measure(
        args.iterations,
        lambda: (
            spread_policy.half_spread(policy_context),
            size_policy.sizes(policy_context),
        ),
    )

    lifecycle = QuoteLifecycleManager()
    registry = QuoteRegistry()
    desired = engine.quote(representative).desired
    lifecycle_durations = _measure(
        args.iterations,
        lambda: lifecycle.decide(
            desired=desired,
            current=registry.state(representative.exchange_id),
            now_monotonic_ns=NOW,
        ),
    )
    if desired is None or desired.bid_ticks is None or desired.bid_size <= 0:
        raise RuntimeError("representative maker bid unavailable for lifecycle benchmark")
    bid_only = replace(desired, ask_ticks=None, ask_size=0)
    old_ticks = max(1, desired.bid_ticks - 2)
    replace_state = MakerQuoteState(
        exchange_id=representative.exchange_id,
        bid=ActiveQuote(
            side=QuoteSide.BID,
            price_ticks=old_ticks,
            size=desired.bid_size,
            remaining_size=desired.bid_size,
            logical_operation_id="make-bench-old",
            exchange_order_id=99,
            lifecycle_state=LifecycleState.OPEN,
            observed_monotonic_ns=NOW - 1,
        ),
    )
    replacement_durations = _measure(
        args.iterations,
        lambda: lifecycle.decide(
            desired=bid_only,
            current=replace_state,
            now_monotonic_ns=NOW,
        ),
    )

    inventory_runtime = replace(
        representative.runtime,
        portfolio=RuntimePortfolio(
            positions=(
                RuntimePosition(
                    exchange_id=representative.exchange_id,
                    market_id=representative.market_id,
                    tournament_id=representative.tournament_id,
                    gross_exposure=5.0,
                    signed_quantity=5.0,
                ),
            ),
            account_trusted=True,
        ),
    )
    inventory_snapshot = replace(representative, runtime=inventory_runtime)
    inventory_requote_durations = _measure(
        args.iterations,
        lambda: engine.quote(inventory_snapshot),
    )
    runtime_cycle_durations = asyncio.run(
        _runtime_cycle_measure(
            min(args.iterations, 5_000),
            engine=engine,
            snapshot=representative,
        )
    )

    universe_durations: list[int] = []
    ordered_snapshots = tuple(snapshots[key] for key in sorted(snapshots))
    for _ in range(args.bursts):
        started = time.perf_counter_ns()
        for snapshot in ordered_snapshots:
            engine.quote(snapshot)
        universe_durations.append(time.perf_counter_ns() - started)

    quote = engine.quote(representative)
    if quote.desired is None or quote.trace.adjusted_fv is None:
        raise RuntimeError("representative maker quote failed closed during benchmark")
    legs = []
    if quote.desired.bid_ticks is not None:
        legs.append(
            CandidateLeg(
                exchange_id=representative.exchange_id,
                market_id=representative.market_id,
                tournament_id=representative.tournament_id,
                outcome_side=OutcomeSide.YES,
                action=OrderAction.BUY,
                quantity=max(1, quote.desired.bid_size),
                limit_price_ticks=quote.desired.bid_ticks,
            )
        )
    if quote.desired.ask_ticks is not None:
        legs.append(
            CandidateLeg(
                exchange_id=representative.exchange_id,
                market_id=representative.market_id,
                tournament_id=representative.tournament_id,
                outcome_side=OutcomeSide.YES,
                action=OrderAction.SELL,
                quantity=max(1, quote.desired.ask_size),
                limit_price_ticks=quote.desired.ask_ticks,
            )
        )
    opportunity = Opportunity(
        family=StrategyFamily.MAKE,
        strategy_id=engine.strategy_id,
        legs=tuple(legs),
        gross_edge=0.005,
        fair_value=quote.trace.adjusted_fv,
        decision_observation_ns=NOW,
    )
    risk_context = RiskContext(
        mode=ExecutionMode.SHADOW,
        kill_switch=False,
        limits=RiskLimits(
            max_order_size=100,
            max_gross_exposure=10_000.0,
            max_per_market_exposure=1_000.0,
            max_open_order_exposure=10_000.0,
            max_concurrent_open_orders=10_000,
        ),
        max_state_age_ns=1_000_000_000,
    )
    risk_durations = _measure(
        args.iterations,
        lambda: evaluate_risk(opportunity, representative.runtime, risk_context),
    )
    risk_decision = evaluate_risk(opportunity, representative.runtime, risk_context)
    if not risk_decision.approved:
        raise RuntimeError(f"benchmark risk unexpectedly rejected: {risk_decision.reason}")

    serial_counter = 0

    def serialize() -> object:
        nonlocal serial_counter
        serial_counter += 1
        return build_execution_plan(
            risk_decision,
            logical_operation_id=f"make-bench-{serial_counter}",
            created_monotonic_ns=NOW,
        )

    serialization_durations = _measure(args.iterations, serialize)

    journal_durations: list[int] = []
    with tempfile.TemporaryDirectory(prefix="make001-bench-") as tmp:
        journal = ExecutionJournal(Path(tmp) / "execution.sqlite3")
        try:
            for index in range(args.journal_iterations):
                plan = build_execution_plan(
                    risk_decision,
                    logical_operation_id=f"make-journal-{index}",
                    created_monotonic_ns=NOW + index,
                )
                started = time.perf_counter_ns()
                journal.record_before_dispatch(
                    plan.envelope,
                    plan.intents,
                    audit=plan.audit,
                    submitted_monotonic_ns=NOW + index,
                )
                journal_durations.append(time.perf_counter_ns() - started)
        finally:
            journal.close()

    burst_us = [value / 1_000.0 for value in universe_durations]
    result = {
        "benchmark": "MAKE-001",
        "git_sha": _git_sha(),
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "machine": platform.machine(),
        "cpu_affinity": (
            sorted(os.sched_getaffinity(0))
            if hasattr(os, "sched_getaffinity")
            else None
        ),
        "canonical": {
            "tournament_id": document.tournament_id,
            "sig_exchange_count": len(document.records),
            "tradeable_exchange_count": len(snapshots),
        },
        "stages": {
            "direct_pm_fv": _stats(fv_durations),
            "null_predictive_toxicity_plugins": _stats(plugin_durations),
            "binary_cara_reservation_price": _stats(reservation_durations),
            "spread_and_size_policy": _stats(spread_size_durations),
            "maker_quote_total": _stats(quote_durations),
            "maker_inventory_requote_total": _stats(inventory_requote_durations),
            "source_event_to_maker_cycle_completion": _stats(runtime_cycle_durations),
            "quote_lifecycle_initial": _stats(lifecycle_durations),
            "quote_lifecycle_material_replace_cancel": _stats(replacement_durations),
            "central_risk": _stats(risk_durations),
            "execution_plan_serialization": _stats(serialization_durations),
            "journal_predispatch": _stats(journal_durations),
            "full_universe_burst": {
                "bursts": len(burst_us),
                "mean_ms": statistics.fmean(burst_us) / 1_000.0,
                "p50_ms": sorted(burst_us)[len(burst_us) // 2] / 1_000.0,
                "p95_ms": sorted(burst_us)[math.ceil(0.95 * len(burst_us)) - 1]
                / 1_000.0,
                "p99_ms": sorted(burst_us)[math.ceil(0.99 * len(burst_us)) - 1]
                / 1_000.0,
                "exchange_evaluations_per_s": (
                    len(snapshots)
                    * len(burst_us)
                    / (sum(universe_durations) / 1_000_000_000.0)
                ),
            },
        },
    }
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
