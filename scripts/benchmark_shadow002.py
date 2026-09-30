#!/usr/bin/env python3
"""SHADOW-002 orchestration/persistence benchmark."""

from __future__ import annotations

import argparse
import asyncio
import json
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter_ns

from predictions_cup.maker.contracts import ExternalQuoteState, MakerMarketSnapshot
from predictions_cup.runtime.models import (
    RuntimeBook,
    RuntimeLevel,
    RuntimeMarket,
    RuntimePortfolio,
    RuntimeSnapshot,
)
from predictions_cup.shadow import (
    CandidateOutput,
    CanonicalShadowSnapshot,
    DecisionStatus,
    InMemoryEventStore,
    JsonlEventStore,
    ShadowBus,
)


@dataclass
class FixedCandidate:
    candidate_id: str
    candidate_version: str = "benchmark-v1"
    strategy_family: str = "BENCH"

    def evaluate(self, snapshot: CanonicalShadowSnapshot) -> CandidateOutput:
        return CandidateOutput(
            status=DecisionStatus.OK,
            fair_value=0.5,
            confidence=1.0,
            score=float(snapshot.observed_monotonic_ns % 17) / 1000.0,
        )


def build_snapshot(index: int) -> CanonicalShadowSnapshot:
    now = 10_000_000_000 + index
    exchange_id = f"exchange-{index}"
    market_id = f"market-{index}"
    runtime = RuntimeSnapshot(
        markets=(
            RuntimeMarket(
                market_id=market_id,
                status="open",
                exchange_ids=(exchange_id,),
                tournament_id="benchmark-tournament",
                mapping_accepted=True,
                tradeable=True,
            ),
        ),
        books=(
            RuntimeBook(
                exchange_id=exchange_id,
                market_id=market_id,
                tournament_id="benchmark-tournament",
                bids=(RuntimeLevel(price_ticks=99, quantity=10.0),),
                asks=(RuntimeLevel(price_ticks=101, quantity=10.0),),
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
        tournament_id="benchmark-tournament",
        now_monotonic_ns=now,
        sig_bbo_observed_ns=now,
        sig_bbo_trusted=True,
        sig_depth_observed_ns=now,
        sig_depth_trusted=True,
        account_observed_ns=now,
        inventory_observed_ns=now,
        external_quotes={
            "token": ExternalQuoteState(
                token_id="token",
                best_bid=0.49,
                best_ask=0.51,
                observed_monotonic_ns=now,
                trusted=True,
                source_version="benchmark",
            )
        },
    )
    return CanonicalShadowSnapshot.freeze(
        maker,
        observed_at=datetime(2026, 10, 1, 16, 0, tzinfo=UTC),
        mapping_version="benchmark-mapping-v1",
        source_revision="benchmark-source-v1",
    )


def percentile(values: list[int], fraction: float) -> int:
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int((len(ordered) - 1) * fraction)))
    return ordered[index]


async def orchestration_benchmark(
    snapshots: tuple[CanonicalShadowSnapshot, ...],
    candidate_count: int,
) -> dict[str, object]:
    candidates = tuple(FixedCandidate(f"candidate-{index}") for index in range(candidate_count))
    store = InMemoryEventStore()
    bus = ShadowBus(
        candidates,
        store=store,
        queue_capacity=max(1, len(snapshots)),
        candidate_timeout_seconds=1.0,
    )
    await bus.start()
    publish_ns: list[int] = []
    started = perf_counter_ns()
    for snapshot in snapshots:
        one_started = perf_counter_ns()
        await bus.publish(snapshot)
        publish_ns.append(perf_counter_ns() - one_started)
    published = perf_counter_ns()
    await bus.flush()
    finished = perf_counter_ns()
    health = bus.health()
    await bus.close()

    elapsed_seconds = (finished - started) / 1e9
    decision_count = len(store.decisions)
    candidate_latencies = {
        item.candidate_id: {
            "p50_ns": item.evaluation_latency_p50_ns,
            "p95_ns": item.evaluation_latency_p95_ns,
            "p99_ns": item.evaluation_latency_p99_ns,
        }
        for item in health.candidates
    }
    return {
        "markets": len(snapshots),
        "candidates": candidate_count,
        "decisions": decision_count,
        "fanout_throughput_decisions_per_second": (
            decision_count / elapsed_seconds if elapsed_seconds > 0 else None
        ),
        "publish_overhead_p50_ns": percentile(publish_ns, 0.50),
        "publish_overhead_p95_ns": percentile(publish_ns, 0.95),
        "publish_overhead_p99_ns": percentile(publish_ns, 0.99),
        "publish_phase_ns": published - started,
        "flush_phase_ns": finished - published,
        "queue_high_water": max(
            (item.queue_high_water for item in health.candidates),
            default=0,
        ),
        "coalesced": health.snapshots_dropped_or_coalesced,
        "candidate_evaluation_latency": candidate_latencies,
    }


async def persistence_benchmark(
    snapshots: tuple[CanonicalShadowSnapshot, ...],
    event_count: int,
) -> dict[str, object]:
    count = min(event_count, len(snapshots))
    with tempfile.TemporaryDirectory(prefix="shadow002-bench-") as raw:
        path = Path(raw) / "events.jsonl"
        store = JsonlEventStore(path, queue_capacity=max(32, count * 3))
        bus = ShadowBus(
            (FixedCandidate("persist"),),
            store=store,
            queue_capacity=max(1, count),
            candidate_timeout_seconds=1.0,
        )
        await bus.start()
        started = perf_counter_ns()
        for snapshot in snapshots[:count]:
            await bus.publish(snapshot)
        await bus.flush()
        finished = perf_counter_ns()
        health = bus.health()
        await bus.close()
        return {
            "snapshots": count,
            "persisted_events": health.persistence.persisted_events,
            "elapsed_ns": finished - started,
            "queue_high_water": health.persistence.queue_high_water,
            "healthy": health.persistence.healthy,
            "bytes": path.stat().st_size,
        }


async def main_async(args: argparse.Namespace) -> None:
    snapshots = tuple(build_snapshot(index) for index in range(args.markets))
    orchestration = await orchestration_benchmark(snapshots, args.candidates)
    persistence = await persistence_benchmark(snapshots, args.persistence_events)
    print(
        json.dumps(
            {
                "shadow_002_benchmark": {
                    "orchestration": orchestration,
                    "persistence": persistence,
                }
            },
            indent=2,
            sort_keys=True,
        )
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--markets", type=int, default=237)
    parser.add_argument("--candidates", type=int, default=6)
    parser.add_argument("--persistence-events", type=int, default=32)
    args = parser.parse_args()
    if args.markets <= 0 or args.candidates <= 0 or args.persistence_events <= 0:
        raise SystemExit("all benchmark counts must be positive")
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
