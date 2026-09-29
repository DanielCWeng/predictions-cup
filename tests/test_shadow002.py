from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from predictions_cup.maker.contracts import ExternalQuoteState, MakerMarketSnapshot
from predictions_cup.maker.direct_pm import DirectPolymarketFairValueProvider
from predictions_cup.mapping.models import (
    MappingClass,
    MappingDirection,
    MappingDocument,
    MappingStatus,
    MarketMapping,
    PolymarketContractIdentity,
)
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
    DirectPmCandidate,
    Hazard005FCandidate,
    InMemoryEventStore,
    JsonlEventStore,
    Pred006Candidate,
    ShadowBus,
    ShadowReplayRunner,
    StructuralFairValueCandidate,
    load_persisted_snapshots,
)

TOURNAMENT = "tournament-1"
NOW = 10_000_000_000


def _maker_snapshot(
    *,
    now: int = NOW,
    exchange_id: str = "e1",
    market_id: str = "m1",
) -> MakerMarketSnapshot:
    runtime = RuntimeSnapshot(
        markets=(
            RuntimeMarket(
                market_id=market_id,
                status="open",
                exchange_ids=(exchange_id,),
                tournament_id=TOURNAMENT,
                mapping_accepted=True,
                tradeable=True,
            ),
        ),
        books=(
            RuntimeBook(
                exchange_id=exchange_id,
                market_id=market_id,
                tournament_id=TOURNAMENT,
                bids=(RuntimeLevel(price_ticks=98, quantity=10.0),),
                asks=(RuntimeLevel(price_ticks=102, quantity=10.0),),
                trusted_depth=True,
                observed_monotonic_ns=now - 10,
            ),
        ),
        portfolio=RuntimePortfolio(account_trusted=True),
        observation_monotonic_ns=now,
    )
    return MakerMarketSnapshot(
        runtime=runtime,
        exchange_id=exchange_id,
        market_id=market_id,
        tournament_id=TOURNAMENT,
        now_monotonic_ns=now,
        sig_bbo_observed_ns=now - 10,
        sig_bbo_trusted=True,
        sig_depth_observed_ns=now - 10,
        sig_depth_trusted=True,
        account_observed_ns=now - 10,
        inventory_observed_ns=now - 10,
        external_quotes={
            "token-yes": ExternalQuoteState(
                token_id="token-yes",
                best_bid=0.54,
                best_ask=0.56,
                observed_monotonic_ns=now - 20,
                trusted=True,
                source_version="pm-test-v1",
            )
        },
    )


def _snapshot(
    *,
    now: int = NOW,
    exchange_id: str = "e1",
    market_id: str = "m1",
) -> CanonicalShadowSnapshot:
    return CanonicalShadowSnapshot.freeze(
        _maker_snapshot(now=now, exchange_id=exchange_id, market_id=market_id),
        observed_at=datetime(2026, 9, 30, 0, 0, tzinfo=UTC),
        mapping_version="mapping-v1",
        source_revision="capture-v1",
        source_provenance={"sig": "test", "polymarket": "test"},
    )


@dataclass
class _FixedCandidate:
    candidate_id: str
    candidate_version: str = "v1"
    strategy_family: str = "TEST"

    def __post_init__(self) -> None:
        self.snapshot_ids: list[str] = []
        self.calls = 0

    def evaluate(self, snapshot: CanonicalShadowSnapshot) -> CandidateOutput:
        self.calls += 1
        self.snapshot_ids.append(snapshot.snapshot_id)
        return CandidateOutput(
            status=DecisionStatus.OK,
            fair_value=0.55,
            confidence=0.8,
            score=0.05,
        )


class _BoomCandidate:
    candidate_id = "boom"
    candidate_version = "v1"
    strategy_family = "TEST"

    def evaluate(self, snapshot: CanonicalShadowSnapshot) -> CandidateOutput:
        del snapshot
        raise RuntimeError("boom")


class _SlowCandidate:
    candidate_id = "slow"
    candidate_version = "v1"
    strategy_family = "TEST"

    def evaluate(self, snapshot: CanonicalShadowSnapshot) -> CandidateOutput:
        del snapshot
        time.sleep(0.05)
        return CandidateOutput(status=DecisionStatus.OK)


class _InvalidCandidate:
    candidate_id = "invalid"
    candidate_version = "v1"
    strategy_family = "TEST"

    def evaluate(self, snapshot: CanonicalShadowSnapshot) -> CandidateOutput:
        del snapshot
        return CandidateOutput(
            status=DecisionStatus.OK,
            fair_value=float("nan"),
        )


def test_same_state_delivery_and_candidate_isolation() -> None:
    async def run() -> None:
        first = _FixedCandidate("first")
        second = _FixedCandidate("second")
        store = InMemoryEventStore()
        bus = ShadowBus((first, _BoomCandidate(), second), store=store)
        await bus.start()
        snapshot = _snapshot()
        await bus.publish(snapshot)
        await bus.flush()
        await bus.close()

        assert first.snapshot_ids == [snapshot.snapshot_id]
        assert second.snapshot_ids == [snapshot.snapshot_id]
        statuses = {
            decision.candidate_id: decision.decision_status
            for decision in store.decisions
        }
        assert statuses["first"] is DecisionStatus.OK
        assert statuses["second"] is DecisionStatus.OK
        assert statuses["boom"] is DecisionStatus.EXCEPTION

    asyncio.run(run())


def test_timeout_isolation_and_invalid_output_fail_closed() -> None:
    async def run() -> None:
        good = _FixedCandidate("good")
        store = InMemoryEventStore()
        bus = ShadowBus(
            (_SlowCandidate(), _InvalidCandidate(), good),
            store=store,
            candidate_timeout_seconds=0.005,
        )
        await bus.start()
        await bus.publish(_snapshot())
        await bus.flush()
        health = bus.health()
        await bus.close()

        statuses = {
            decision.candidate_id: decision.decision_status
            for decision in store.decisions
        }
        assert statuses["slow"] is DecisionStatus.TIMEOUT
        assert statuses["invalid"] is DecisionStatus.INVALID_OUTPUT
        assert statuses["good"] is DecisionStatus.OK
        slow_health = next(
            item for item in health.candidates if item.candidate_id == "slow"
        )
        assert slow_health.timeout_count == 1

    asyncio.run(run())


def test_disable_and_global_pause_are_control_plane_only() -> None:
    async def run() -> None:
        candidate = _FixedCandidate("toggle")
        store = InMemoryEventStore()
        bus = ShadowBus((candidate,), store=store)
        await bus.start()

        bus.disable_candidate("toggle")
        await bus.publish(_snapshot())
        await bus.flush()
        assert candidate.calls == 0
        health = bus.health()
        assert not health.candidates[0].enabled

        bus.enable_candidate("toggle")
        bus.pause()
        await bus.publish(_snapshot(now=NOW + 1))
        await bus.flush()
        assert candidate.calls == 0

        bus.resume()
        await bus.publish(_snapshot(now=NOW + 2))
        await bus.flush()
        assert candidate.calls == 1
        await bus.close()

    asyncio.run(run())


def test_backpressure_coalesces_pending_state_with_bounded_queue() -> None:
    async def run() -> None:
        candidate = _SlowCandidate()
        store = InMemoryEventStore()
        bus = ShadowBus(
            (candidate,),
            store=store,
            queue_capacity=1,
            candidate_timeout_seconds=0.5,
        )
        await bus.start()
        await bus.publish(_snapshot(now=NOW))
        await bus.publish(_snapshot(now=NOW + 1))
        await bus.publish(_snapshot(now=NOW + 2))
        await bus.flush()
        health = bus.health()
        await bus.close()

        assert health.snapshots_dropped_or_coalesced >= 1
        assert health.candidates[0].queue_high_water <= 1
        assert health.candidates[0].skipped_states >= 1

    asyncio.run(run())


def test_observable_time_ordering_is_rejected_if_it_moves_backwards() -> None:
    async def run() -> None:
        bus = ShadowBus((_FixedCandidate("ordered"),))
        await bus.start()
        await bus.publish(_snapshot(now=NOW + 1))
        with pytest.raises(ValueError, match="out-of-order"):
            await bus.publish(_snapshot(now=NOW))
        await bus.close()

    asyncio.run(run())


def test_replay_is_deterministic_and_candidate_selectable() -> None:
    candidate = _FixedCandidate("deterministic")
    runner = ShadowReplayRunner((candidate, _FixedCandidate("other")))
    snapshots = (_snapshot(now=NOW), _snapshot(now=NOW + 1))
    first = runner.replay(snapshots, candidate_ids={"deterministic"})
    second = runner.replay(snapshots, candidate_ids={"deterministic"})
    assert first.semantic_hash == second.semantic_hash
    assert len(first.decisions) == 2
    assert all(
        decision.candidate_id == "deterministic"
        for decision in first.decisions
    )


def test_jsonl_restart_preserves_old_events_and_replays_snapshots(
    tmp_path: Path,
) -> None:
    async def run_once(path: Path, snapshot: CanonicalShadowSnapshot) -> None:
        store = JsonlEventStore(path, queue_capacity=16)
        bus = ShadowBus((_FixedCandidate("persisted"),), store=store)
        await bus.start()
        await bus.publish(snapshot)
        await bus.flush()
        await bus.close()

    path = tmp_path / "shadow.jsonl"
    first = _snapshot(now=NOW)
    second = _snapshot(now=NOW + 1)
    asyncio.run(run_once(path, first))
    before = path.read_text(encoding="utf-8")
    asyncio.run(run_once(path, second))
    after = path.read_text(encoding="utf-8")

    assert after.startswith(before)
    restored = load_persisted_snapshots(path)
    assert tuple(item.snapshot_id for item in restored) == (
        first.snapshot_id,
        second.snapshot_id,
    )


def test_shadow_bus_refuses_live_trading_configuration() -> None:
    with pytest.raises(ValueError, match="trading_enabled=false"):
        ShadowBus((_FixedCandidate("safe"),), trading_enabled=True)


def _mapping(direction: MappingDirection = MappingDirection.SAME) -> MappingDocument:
    identity = PolymarketContractIdentity(
        market_id="pm-market",
        condition_id="condition",
        event_id="event",
        slug="pm-market",
        question="Will it happen?",
        outcomes=("Yes", "No"),
        token_ids=("token-yes", "token-no"),
        mapped_outcome="Yes",
        mapped_token_id="token-yes",
    )
    return MappingDocument(
        tournament_id=TOURNAMENT,
        records=(
            MarketMapping(
                sig_tournament_id=TOURNAMENT,
                sig_market_id="m1",
                sig_market_title="Market 1",
                sig_exchange_id="e1",
                sig_outcome_label="Yes",
                mapping_class=MappingClass.EXACT,
                mapping_direction=direction,
                mapping_confidence=Decimal("0.99"),
                status=MappingStatus.VERIFIED,
                direct_polymarket=identity,
            ),
        ),
    )


def test_direct_pm_reference_exposes_mapping_and_residual() -> None:
    mapping = _mapping()
    candidate = DirectPmCandidate(
        DirectPolymarketFairValueProvider(mapping),
        mapping=mapping,
    )
    output = candidate.evaluate(_snapshot())
    assert output.status is DecisionStatus.OK
    assert output.fair_value == pytest.approx(0.55)
    assert output.score == pytest.approx(0.05)
    assert output.candidate_payload["mapping_class"] == "EXACT"
    assert output.candidate_payload["mapping_direction"] == "SAME"


def test_unwired_research_candidates_fail_closed_as_not_ready() -> None:
    snapshot = _snapshot()
    for candidate in (
        Pred006Candidate(),
        Hazard005FCandidate(),
        StructuralFairValueCandidate(),
    ):
        output = candidate.evaluate(snapshot)
        assert output.status is DecisionStatus.NOT_READY
        assert output.action_intent is None
