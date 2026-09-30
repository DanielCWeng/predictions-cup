#!/usr/bin/env python3
"""Production-shaped sustained SHADOW-002 persistence/backpressure soak."""

from __future__ import annotations

import argparse
import asyncio
import json
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
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
from predictions_cup.shadow import CandidateOutput, DecisionStatus, JsonlEventStore, ShadowBus


@dataclass
class FixedCandidate:
    candidate_id: str
    candidate_version: str = "soak-v1"
    strategy_family: str = "SOAK"

    def evaluate(self, snapshot: object) -> CandidateOutput:
        del snapshot
        return CandidateOutput(
            status=DecisionStatus.OK,
            fair_value=0.5,
            confidence=1.0,
        )


def build_maker(index: int, cycle: int) -> MakerMarketSnapshot:
    now = 10_000_000_000 + cycle * 1_000_000 + index
    exchange_id = f"exchange-{index}"
    market_id = f"market-{index}"
    runtime = RuntimeSnapshot(
        markets=(
            RuntimeMarket(
                market_id=market_id,
                status="open",
                exchange_ids=(exchange_id,),
                tournament_id="soak-tournament",
                mapping_accepted=True,
                tradeable=True,
            ),
        ),
        books=(
            RuntimeBook(
                exchange_id=exchange_id,
                market_id=market_id,
                tournament_id="soak-tournament",
                bids=(RuntimeLevel(price_ticks=99, quantity=10.0),),
                asks=(RuntimeLevel(price_ticks=101, quantity=10.0),),
                trusted_depth=True,
                observed_monotonic_ns=now,
            ),
        ),
        portfolio=RuntimePortfolio(account_trusted=True),
        observation_monotonic_ns=now,
    )
    return MakerMarketSnapshot(
        runtime=runtime,
        exchange_id=exchange_id,
        market_id=market_id,
        tournament_id="soak-tournament",
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
                source_version="soak",
            )
        },
    )


def percentile(values: list[int], fraction: float) -> int:
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int((len(ordered) - 1) * fraction)))
    return ordered[index]


async def run_soak(args: argparse.Namespace) -> dict[str, object]:
    candidates = tuple(FixedCandidate(f"candidate-{index}") for index in range(args.candidates))
    with tempfile.TemporaryDirectory(prefix="shadow002-soak-") as raw:
        path = Path(raw) / "events.jsonl"
        store = JsonlEventStore(
            path,
            queue_capacity=args.persistence_queue,
            batch_size=args.persistence_batch,
        )
        bus = ShadowBus(
            candidates,
            store=store,
            queue_capacity=args.candidate_queue,
            ingress_capacity=args.ingress_queue,
            candidate_timeout_seconds=1.0,
        )
        await bus.start()
        submit_ns: list[int] = []
        started = perf_counter_ns()
        wall_base = datetime(2026, 10, 1, 16, 0, tzinfo=UTC)

        for cycle in range(args.cycles):
            observed_at = wall_base + timedelta(milliseconds=cycle * args.cycle_pause_ms)
            for market in range(args.markets):
                maker = build_maker(market, cycle)
                one_started = perf_counter_ns()
                accepted = bus.submit_maker(
                    maker,
                    observed_at=observed_at,
                    mapping_version="soak-mapping-v1",
                    source_revision=f"soak-cycle-{cycle}",
                    source_provenance={"source": "production-shaped-soak"},
                )
                submit_ns.append(perf_counter_ns() - one_started)
                if not accepted:
                    raise RuntimeError("SHADOW ingress rejected a soak decision boundary")
            await asyncio.sleep(args.cycle_pause_ms / 1_000.0)

        producer_finished = perf_counter_ns()
        await bus.flush()
        drained = perf_counter_ns()
        health = bus.health()
        expected_snapshots = args.markets * args.cycles
        expected_decisions = expected_snapshots * args.candidates
        expected_events = expected_snapshots + expected_decisions
        await bus.close()
        lines = sum(1 for _ in path.open("r", encoding="utf-8"))
        bytes_written = path.stat().st_size

        if health.ingress_rejected != 0:
            raise RuntimeError(f"ingress rejected {health.ingress_rejected} states")
        if health.snapshots_dropped_or_coalesced != 0:
            raise RuntimeError(
                f"candidate states coalesced/dropped: {health.snapshots_dropped_or_coalesced}"
            )
        if not health.persistence.healthy:
            raise RuntimeError(f"persistence unhealthy: {health.persistence.last_error}")
        if health.persistence.persisted_events != expected_events:
            raise RuntimeError(
                "persisted event count mismatch "
                f"{health.persistence.persisted_events} != {expected_events}"
            )
        if lines != expected_events:
            raise RuntimeError(f"JSONL readback mismatch {lines} != {expected_events}")

        elapsed_seconds = (drained - started) / 1e9
        return {
            "markets": args.markets,
            "candidates": args.candidates,
            "cycles": args.cycles,
            "snapshots": expected_snapshots,
            "decisions": expected_decisions,
            "persisted_events": expected_events,
            "jsonl_readback_events": lines,
            "bytes": bytes_written,
            "ingress_rejected": health.ingress_rejected,
            "coalesced_or_dropped": health.snapshots_dropped_or_coalesced,
            "ingress_queue_high_water": health.ingress_queue_high_water,
            "candidate_queue_high_water": max(
                item.queue_high_water for item in health.candidates
            ),
            "persistence_queue_high_water": health.persistence.queue_high_water,
            "persistence_write_batches": health.persistence.write_batches,
            "persistence_write_latency_p95_ns": health.persistence.write_latency_p95_ns,
            "submit_overhead_p50_ns": percentile(submit_ns, 0.50),
            "submit_overhead_p95_ns": percentile(submit_ns, 0.95),
            "submit_overhead_p99_ns": percentile(submit_ns, 0.99),
            "producer_phase_seconds": (producer_finished - started) / 1e9,
            "drain_seconds": (drained - producer_finished) / 1e9,
            "event_throughput_per_second": expected_events / elapsed_seconds,
            "healthy": health.persistence.healthy,
        }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--markets", type=int, default=237)
    parser.add_argument("--candidates", type=int, default=6)
    parser.add_argument("--cycles", type=int, default=10)
    parser.add_argument("--cycle-pause-ms", type=int, default=200)
    parser.add_argument("--candidate-queue", type=int, default=512)
    parser.add_argument("--ingress-queue", type=int, default=4096)
    parser.add_argument("--persistence-queue", type=int, default=65536)
    parser.add_argument("--persistence-batch", type=int, default=256)
    args = parser.parse_args()
    if min(
        args.markets,
        args.candidates,
        args.cycles,
        args.cycle_pause_ms,
        args.candidate_queue,
        args.ingress_queue,
        args.persistence_queue,
        args.persistence_batch,
    ) <= 0:
        raise SystemExit("all soak parameters must be positive")
    result = asyncio.run(run_soak(args))
    print(json.dumps({"shadow_002_sustained_soak": result}, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
