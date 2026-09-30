from __future__ import annotations

import argparse
import json
import statistics
import time
from dataclasses import dataclass
from datetime import UTC, datetime

from predictions_cup.maker.contracts import MakerMarketSnapshot
from predictions_cup.runtime.models import (
    RuntimeBook,
    RuntimeLevel,
    RuntimeMarket,
    RuntimePortfolio,
    RuntimeSnapshot,
)
from predictions_cup.shadow import (
    CanonicalShadowSnapshot,
    FixedHazard005FRegimeProvider,
    Frozen005FEvaluator,
    FrozenPred006Evaluator,
    Hazard005FBboObservation,
    IncrementalHazard005FState,
    IncrementalPred006FeatureState,
    Pred006BlockObservation,
)
from predictions_cup.shadow.frozen_runtime import HAZARD005F_SCORER_HASHES

BASE_MONO = 50_000_000_000_000


@dataclass(frozen=True)
class _ConstantScorer:
    scorer_id: str
    artifact_hash: str
    value: float

    def score(self, values: tuple[float, ...]) -> float:
        if not values:
            raise ValueError("empty feature vector")
        return self.value


def _snapshot(market_id: str, timestamp_s: int) -> CanonicalShadowSnapshot:
    exchange_id = f"e-{market_id}"
    now = BASE_MONO + timestamp_s * 1_000_000_000
    runtime = RuntimeSnapshot(
        markets=(
            RuntimeMarket(
                market_id=market_id,
                status="open",
                exchange_ids=(exchange_id,),
                tournament_id="bench",
                mapping_accepted=True,
                tradeable=True,
            ),
        ),
        books=(
            RuntimeBook(
                exchange_id=exchange_id,
                market_id=market_id,
                tournament_id="bench",
                bids=(RuntimeLevel(price_ticks=90, quantity=10.0),),
                asks=(RuntimeLevel(price_ticks=110, quantity=10.0),),
                trusted_depth=True,
                observed_monotonic_ns=now,
            ),
        ),
        portfolio=RuntimePortfolio(account_trusted=True),
        observation_monotonic_ns=now,
    )
    maker = MakerMarketSnapshot(
        runtime=runtime,
        exchange_id=exchange_id,
        market_id=market_id,
        tournament_id="bench",
        now_monotonic_ns=now,
        sig_bbo_observed_ns=now,
        sig_bbo_trusted=True,
        sig_depth_observed_ns=now,
        sig_depth_trusted=True,
        account_observed_ns=now,
        inventory_observed_ns=now,
        external_quotes={},
    )
    return CanonicalShadowSnapshot.freeze(
        maker,
        observed_at=datetime.fromtimestamp(timestamp_s, tz=UTC),
        mapping_version="bench",
        source_revision=f"bench-{market_id}",
    )


def _quantile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    index = min(len(ordered) - 1, int(fraction * (len(ordered) - 1)))
    return ordered[index]


def run(markets: int, bursts: int) -> dict[str, float | int]:
    pred_state = IncrementalPred006FeatureState(
        scope_resolver=lambda snapshot: snapshot.market_id
    )
    hazard_state = IncrementalHazard005FState(
        grid_origin_s=0,
        scope_resolver=lambda snapshot: snapshot.market_id,
    )
    snapshots: list[CanonicalShadowSnapshot] = []

    for index in range(markets):
        market_id = f"m{index:03d}"
        for timestamp_s, probability in (
            (0, 0.40),
            (30, 0.50),
            (120, 0.45),
            (1800, 0.55),
        ):
            pred_state.observe(
                Pred006BlockObservation(
                    scope_id=market_id,
                    window_id="bench-window",
                    timestamp_s=timestamp_s,
                    observed_monotonic_ns=BASE_MONO
                    + timestamp_s * 1_000_000_000,
                    p_yes=probability,
                    size_shares=10.0,
                    value_usd=5.0,
                    fee_charged=1.0,
                    fee_missing=0.0,
                    fee_no_leg=0.0,
                    active_fee_net=0.1,
                    active_fee_charged=0.1,
                    active_fee_refunded=0.0,
                    active_charge_legs=1.0,
                    source_version="benchmark-data003",
                )
            )
        for timestamp_s, bid, ask in (
            (0, 0.40, 0.60),
            (20, 0.42, 0.60),
            (45, 0.44, 0.60),
        ):
            hazard_state.observe(
                Hazard005FBboObservation(
                    scope_id=market_id,
                    timestamp_s=timestamp_s,
                    observed_monotonic_ns=BASE_MONO
                    + timestamp_s * 1_000_000_000,
                    best_bid=bid,
                    best_ask=ask,
                    source_version="benchmark",
                )
            )
        snapshots.append(_snapshot(market_id, 1800))

    pred = FrozenPred006Evaluator(
        pred_state,
        scorers={
            "PRED006-C01": _ConstantScorer("c01", "a" * 64, 0.6),
            "PRED006-C02": _ConstantScorer("c02", "b" * 64, 0.4),
        },
    )
    hazard = Frozen005FEvaluator(
        hazard_state,
        FixedHazard005FRegimeProvider("ACTIVE_RESULTS"),
        scorers={
            "ACTIVE_RESULTS|clock|UPDATE_HAZARD": _ConstantScorer(
                "update",
                HAZARD005F_SCORER_HASHES["ACTIVE_RESULTS|clock|UPDATE_HAZARD"],
                0.5
            ),
            "ACTIVE_RESULTS|clock|JUMP_HAZARD": _ConstantScorer(
                "jump",
                HAZARD005F_SCORER_HASHES["ACTIVE_RESULTS|clock|JUMP_HAZARD"],
                0.2
            ),
        },
    )

    call_us: list[float] = []
    burst_ms: list[float] = []
    started = time.perf_counter_ns()
    for _ in range(bursts):
        burst_started = time.perf_counter_ns()
        for snapshot in snapshots:
            for evaluator in (pred, hazard):
                call_started = time.perf_counter_ns()
                signal = evaluator.evaluate(snapshot)
                if signal is None:
                    raise RuntimeError("benchmark fixture unexpectedly not ready")
                call_us.append((time.perf_counter_ns() - call_started) / 1_000.0)
        burst_ms.append((time.perf_counter_ns() - burst_started) / 1_000_000.0)
    elapsed_s = (time.perf_counter_ns() - started) / 1_000_000_000.0
    evaluations = markets * bursts * 2
    return {
        "markets": markets,
        "bursts": bursts,
        "evaluations": evaluations,
        "elapsed_seconds": elapsed_s,
        "evaluations_per_second": evaluations / elapsed_s,
        "evaluation_us_p50": statistics.median(call_us),
        "evaluation_us_p95": _quantile(call_us, 0.95),
        "evaluation_us_p99": _quantile(call_us, 0.99),
        "burst_ms_p50": statistics.median(burst_ms),
        "burst_ms_p95": _quantile(burst_ms, 0.95),
        "burst_ms_p99": _quantile(burst_ms, 0.99),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--markets", type=int, default=237)
    parser.add_argument("--bursts", type=int, default=20)
    args = parser.parse_args()
    if args.markets <= 0 or args.bursts <= 0:
        raise SystemExit("markets and bursts must be positive")
    print(json.dumps(run(args.markets, args.bursts), sort_keys=True))


if __name__ == "__main__":
    main()
