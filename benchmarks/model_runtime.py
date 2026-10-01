#!/usr/bin/env python3
"""MODEL-RUNTIME-001 microbenchmark.  Never dispatches an order."""

from __future__ import annotations

import argparse
import json
import statistics
from dataclasses import dataclass
from datetime import UTC, datetime
from time import perf_counter_ns
from typing import Callable

from predictions_cup.maker.contracts import MakerMarketSnapshot
from predictions_cup.models.contracts import (
    ModelCapability,
    ModelDecision,
    ModelDecisionKind,
    ModelRiskEnvelope,
    ModelSpec,
)
from predictions_cup.models.registry import ModelRegistry
from predictions_cup.models.runtime import ModelRuntime, PaperModelCandidate
from predictions_cup.runtime.models import (
    OrderAction,
    RuntimeBook,
    RuntimeLevel,
    RuntimeMarket,
    RuntimePortfolio,
    RuntimeSnapshot,
)
from predictions_cup.shadow.contracts import CanonicalShadowSnapshot
from predictions_cup.strategy.core import StrategyFamily


@dataclass(frozen=True, slots=True)
class BenchResult:
    median_us: float
    p95_us: float
    p99_us: float
    max_us: float
    evaluations_per_second: float

    def as_dict(self) -> dict[str, float]:
        return {
            "median_us": self.median_us,
            "p95_us": self.p95_us,
            "p99_us": self.p99_us,
            "max_us": self.max_us,
            "evaluations_per_second": self.evaluations_per_second,
        }


class BenchModel:
    def __init__(self, index: int) -> None:
        self.spec = ModelSpec(
            model_id=f"bench_{index}",
            model_version="1",
            capability=ModelCapability.DIRECTIONAL,
            source_hash=f"{index + 1:064x}",
            strategy_family=StrategyFamily.PRED,
            live_eligible=False,
            risk=ModelRiskEnvelope(),
        )

    def evaluate(self, snapshot: CanonicalShadowSnapshot) -> ModelDecision:
        book = snapshot.maker.runtime.books[0]
        signal = (book.asks[0].price_ticks - book.bids[0].price_ticks) / 200.0
        return ModelDecision(
            kind=ModelDecisionKind.DIRECTIONAL,
            signal_value=signal,
            fair_value=0.5,
            direction=OrderAction.BUY,
            reason="benchmark_fixture",
        )


def _snapshot() -> CanonicalShadowSnapshot:
    now_ns = 1_000_000_000
    runtime = RuntimeSnapshot(
        markets=(
            RuntimeMarket(
                market_id="m1",
                status="open",
                exchange_ids=("e1",),
                tournament_id="t1",
                mapping_accepted=True,
                tradeable=True,
            ),
        ),
        books=(
            RuntimeBook(
                exchange_id="e1",
                market_id="m1",
                tournament_id="t1",
                bids=(RuntimeLevel(price_ticks=99, quantity=100.0),),
                asks=(RuntimeLevel(price_ticks=101, quantity=100.0),),
                trusted_depth=True,
                observed_monotonic_ns=now_ns,
            ),
        ),
        portfolio=RuntimePortfolio(account_trusted=True),
        observation_monotonic_ns=now_ns,
    )
    maker = MakerMarketSnapshot(
        runtime=runtime,
        exchange_id="e1",
        market_id="m1",
        tournament_id="t1",
        now_monotonic_ns=now_ns,
        sig_bbo_observed_ns=now_ns,
        sig_bbo_trusted=True,
        sig_depth_observed_ns=now_ns,
        sig_depth_trusted=True,
        account_observed_ns=now_ns,
        inventory_observed_ns=now_ns,
        external_quotes={},
    )
    return CanonicalShadowSnapshot.freeze(
        maker,
        observed_at=datetime(2026, 10, 1, 16, 0, tzinfo=UTC),
        mapping_version="benchmark",
        source_revision="benchmark",
    )


def _measure(fn: Callable[[], object], iterations: int) -> BenchResult:
    samples: list[int] = []
    for _ in range(iterations):
        started = perf_counter_ns()
        fn()
        samples.append(perf_counter_ns() - started)
    ordered = sorted(samples)
    median = statistics.median(ordered)
    p95 = ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))]
    p99 = ordered[min(len(ordered) - 1, int(len(ordered) * 0.99))]
    return BenchResult(
        median_us=median / 1_000.0,
        p95_us=p95 / 1_000.0,
        p99_us=p99 / 1_000.0,
        max_us=max(ordered) / 1_000.0,
        evaluations_per_second=1_000_000_000.0 / median,
    )


def run(iterations: int) -> dict[str, object]:
    snapshot = _snapshot()
    providers = tuple(BenchModel(index) for index in range(10))
    registry = ModelRegistry(providers)
    runtime = ModelRuntime(
        registry,
        paper_model_ids=("bench_0",),
        platform_live_ready=False,
    )
    single = providers[0]
    candidates = tuple(PaperModelCandidate(provider) for provider in providers)

    results: dict[str, BenchResult] = {}
    results["registry_router"] = _measure(lambda: registry.get("bench_0"), iterations)
    results["single_model_evaluation"] = _measure(
        lambda: single.evaluate(snapshot),
        iterations,
    )
    results["model_to_candidate_contract"] = _measure(
        lambda: runtime.evaluate("bench_0", snapshot),
        iterations,
    )
    for count in (1, 5, 10):
        selected = candidates[:count]
        results[f"paper_models_{count}"] = _measure(
            lambda selected=selected: tuple(
                candidate.evaluate(snapshot) for candidate in selected
            ),
            max(100, iterations // count),
        )

    payload = {
        "iterations": iterations,
        "targets_us": {
            "single_model_median_max": 25.0,
            "single_model_p99_max": 50.0,
            "millisecond_regression_guard": 1_000.0,
        },
        "results": {
            name: result.as_dict()
            for name, result in results.items()
        },
        "real_sig_orders_sent": False,
    }
    single_result = results["single_model_evaluation"]
    if single_result.p99_us >= 1_000.0:
        raise RuntimeError(
            "MODEL-RUNTIME-001 regression: simple model p99 reached millisecond scale"
        )
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--iterations", type=int, default=20_000)
    args = parser.parse_args()
    if args.iterations < 100:
        raise SystemExit("--iterations must be >= 100")
    print(json.dumps(run(args.iterations), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
