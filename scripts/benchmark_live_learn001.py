#!/usr/bin/env python3
"""Production-shaped 237-market LIVE-LEARN burst benchmark."""

from __future__ import annotations

import argparse
import asyncio
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from time import perf_counter

from predictions_cup.live_learn.persistence import JsonlOutcomeStore
from predictions_cup.live_learn.reporting import RollingReport
from predictions_cup.live_learn.runtime import LiveLearnEngine
from predictions_cup.maker.contracts import MakerMarketSnapshot
from predictions_cup.runtime.models import (
    RuntimeBook,
    RuntimeLevel,
    RuntimeMarket,
    RuntimePortfolio,
    RuntimeSnapshot,
)
from predictions_cup.shadow.contracts import (
    CandidateDecision,
    CanonicalShadowSnapshot,
    DecisionStatus,
)


@dataclass
class _NullReportSink:
    async def emit(self, report: RollingReport) -> None:
        del report


def _snapshot(
    *,
    exchange_id: str,
    market_id: str,
    observed_at: datetime,
    now_ns: int,
    future: bool,
) -> CanonicalShadowSnapshot:
    bid_ticks = 100 if future else 98
    ask_ticks = 104 if future else 102
    runtime = RuntimeSnapshot(
        markets=(
            RuntimeMarket(
                market_id=market_id,
                status="open",
                exchange_ids=(exchange_id,),
                tournament_id="benchmark",
                mapping_accepted=True,
                tradeable=True,
            ),
        ),
        books=(
            RuntimeBook(
                exchange_id=exchange_id,
                market_id=market_id,
                tournament_id="benchmark",
                bids=(RuntimeLevel(price_ticks=bid_ticks, quantity=100.0),),
                asks=(RuntimeLevel(price_ticks=ask_ticks, quantity=100.0),),
                trusted_depth=True,
                observed_monotonic_ns=now_ns - 10,
            ),
        ),
        portfolio=RuntimePortfolio(account_trusted=True),
        observation_monotonic_ns=now_ns,
    )
    maker = MakerMarketSnapshot(
        runtime=runtime,
        exchange_id=exchange_id,
        market_id=market_id,
        tournament_id="benchmark",
        now_monotonic_ns=now_ns,
        sig_bbo_observed_ns=now_ns - 10,
        sig_bbo_trusted=True,
        sig_depth_observed_ns=now_ns - 10,
        sig_depth_trusted=True,
        account_observed_ns=now_ns,
        inventory_observed_ns=now_ns,
        external_quotes={},
    )
    return CanonicalShadowSnapshot.freeze(
        maker,
        observed_at=observed_at,
        mapping_version="benchmark-map-v1",
        source_revision=f"benchmark-{exchange_id}-{now_ns}",
    )


def _decision(
    snapshot: CanonicalShadowSnapshot,
    candidate_index: int,
) -> CandidateDecision:
    candidate_id = f"benchmark-candidate-{candidate_index}"
    return CandidateDecision(
        decision_id=f"bench-{snapshot.snapshot_id}-{candidate_id}",
        candidate_id=candidate_id,
        candidate_version="v1",
        strategy_family="BENCHMARK",
        observed_at=snapshot.observed_at,
        monotonic_time=snapshot.observed_monotonic_ns,
        tournament_id=snapshot.tournament_id,
        exchange_id=snapshot.exchange_id,
        market_id=snapshot.market_id,
        input_snapshot_id=snapshot.snapshot_id,
        mapping_version=snapshot.mapping_version,
        fair_value=0.55,
        lower_bound=None,
        upper_bound=None,
        confidence=0.8,
        direction=None,
        score=None,
        action_intent=None,
        quote_intent=None,
        decision_status=DecisionStatus.OK,
        abstain_reason=None,
        quality_flags=(),
        compute_started_at=snapshot.observed_monotonic_ns,
        compute_finished_at=snapshot.observed_monotonic_ns + 1_000,
        compute_latency_ns=1_000,
        candidate_payload={"mapping_class": "EXACT"},
    )


async def _run(markets: int, candidates: int) -> dict[str, float | int]:
    with tempfile.TemporaryDirectory(prefix="live-learn-bench-") as raw:
        root = Path(raw)
        engine = LiveLearnEngine(
            shadow_journal_path=root / "shadow.jsonl",
            outcome_store=JsonlOutcomeStore(root / "outcomes.jsonl"),
            report_sink=_NullReportSink(),
            horizons=(1,),
            report_cadences=(3600,),
            queue_capacity=max(10_000, markets * candidates * 4),
        )
        await engine.start()
        t0 = datetime.now(UTC) + timedelta(hours=1)
        snapshots: list[CanonicalShadowSnapshot] = []
        started = perf_counter()
        for index in range(markets):
            exchange_id = str(index + 1)
            initial = _snapshot(
                exchange_id=exchange_id,
                market_id=f"market-{index}",
                observed_at=t0,
                now_ns=1_000_000_000 + index,
                future=False,
            )
            snapshots.append(initial)
            await engine.persist_snapshot(initial)
            for candidate_index in range(candidates):
                await engine.persist_decision(
                    _decision(initial, candidate_index)
                )
        for index, initial in enumerate(snapshots):
            await engine.persist_snapshot(
                _snapshot(
                    exchange_id=initial.exchange_id,
                    market_id=initial.market_id,
                    observed_at=t0 + timedelta(seconds=1),
                    now_ns=2_000_000_000 + index,
                    future=True,
                )
            )
        await engine.flush()
        elapsed = perf_counter() - started
        expected = markets * candidates
        outcomes = len(engine.outcomes())
        health = engine.health
        if outcomes != expected:
            raise RuntimeError(
                f"benchmark outcome mismatch: expected={expected} actual={outcomes}"
            )
        if health.failures:
            raise RuntimeError(f"benchmark failures={health.failures}")
        await engine.close()
        return {
            "markets": markets,
            "candidates": candidates,
            "decisions": expected,
            "outcomes": outcomes,
            "elapsed_seconds": elapsed,
            "decisions_per_second": expected / elapsed,
            "queue_high_water": health.queue_high_water,
        }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--markets", type=int, default=237)
    parser.add_argument("--candidates", type=int, default=6)
    args = parser.parse_args()
    if args.markets <= 0 or args.candidates <= 0:
        raise ValueError("markets and candidates must be positive")
    result = asyncio.run(_run(args.markets, args.candidates))
    for key, value in result.items():
        print(f"{key}={value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
