from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from predictions_cup.live_learn import (
    DecisionOutcome,
    ExecutionEvidence,
    FillEvidence,
    JsonlOutcomeStore,
    LiveLearnEngine,
    MarketEvidence,
    OutcomeStatus,
)
from predictions_cup.live_learn.contracts import outcome_record
from predictions_cup.live_learn.reporting import RollingReport, build_report
from predictions_cup.live_learn.scoring import QuoteEconomicsScorer, ScoreContext
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
from predictions_cup.shadow.persistence import event_record


@dataclass
class _ReportSink:
    reports: list[RollingReport]

    async def emit(self, report: RollingReport) -> None:
        self.reports.append(report)


def _snapshot(
    observed_at: datetime,
    *,
    now_ns: int,
    exchange_id: str = "1",
    market_id: str = "m1",
    bid_ticks: int | None = 98,
    ask_ticks: int | None = 102,
    trusted: bool = True,
    source_age_ns: int = 10,
) -> CanonicalShadowSnapshot:
    bids = (
        ()
        if bid_ticks is None
        else (RuntimeLevel(price_ticks=bid_ticks, quantity=10.0),)
    )
    asks = (
        ()
        if ask_ticks is None
        else (RuntimeLevel(price_ticks=ask_ticks, quantity=10.0),)
    )
    runtime = RuntimeSnapshot(
        markets=(
            RuntimeMarket(
                market_id=market_id,
                status="open",
                exchange_ids=(exchange_id,),
                tournament_id="t1",
                mapping_accepted=True,
                tradeable=True,
            ),
        ),
        books=(
            RuntimeBook(
                exchange_id=exchange_id,
                market_id=market_id,
                tournament_id="t1",
                bids=bids,
                asks=asks,
                trusted_depth=trusted,
                observed_monotonic_ns=now_ns - source_age_ns,
            ),
        ),
        portfolio=RuntimePortfolio(account_trusted=True),
        observation_monotonic_ns=now_ns,
    )
    maker = MakerMarketSnapshot(
        runtime=runtime,
        exchange_id=exchange_id,
        market_id=market_id,
        tournament_id="t1",
        now_monotonic_ns=now_ns,
        sig_bbo_observed_ns=now_ns - source_age_ns,
        sig_bbo_trusted=trusted,
        sig_depth_observed_ns=now_ns - source_age_ns,
        sig_depth_trusted=trusted,
        account_observed_ns=now_ns,
        inventory_observed_ns=now_ns,
        external_quotes={},
    )
    return CanonicalShadowSnapshot.freeze(
        maker,
        observed_at=observed_at,
        mapping_version="map-v1",
        source_revision=f"event-{now_ns}",
    )


def _decision(
    snapshot: CanonicalShadowSnapshot,
    *,
    candidate_id: str = "candidate",
    version: str = "v1",
    family: str = "TEST",
    status: DecisionStatus = DecisionStatus.OK,
    fair_value: float | None = 0.55,
    quote_intent: Mapping[str, object] | None = None,
    direction: str | None = None,
    payload: Mapping[str, object] | None = None,
) -> CandidateDecision:
    return CandidateDecision(
        decision_id=f"d-{candidate_id}-{snapshot.snapshot_id}",
        candidate_id=candidate_id,
        candidate_version=version,
        strategy_family=family,
        observed_at=snapshot.observed_at,
        monotonic_time=snapshot.observed_monotonic_ns,
        tournament_id=snapshot.tournament_id,
        exchange_id=snapshot.exchange_id,
        market_id=snapshot.market_id,
        input_snapshot_id=snapshot.snapshot_id,
        mapping_version=snapshot.mapping_version,
        fair_value=fair_value,
        lower_bound=None,
        upper_bound=None,
        confidence=0.8 if fair_value is not None else None,
        direction=direction,
        score=0.1 if fair_value is None else None,
        action_intent="QUOTE" if quote_intent is not None else None,
        quote_intent=quote_intent,
        decision_status=status,
        abstain_reason="test_abstain" if status is DecisionStatus.ABSTAIN else None,
        quality_flags=(),
        compute_started_at=snapshot.observed_monotonic_ns,
        compute_finished_at=snapshot.observed_monotonic_ns + 1_000,
        compute_latency_ns=1_000,
        candidate_payload=dict(payload or {}),
    )


async def _engine(
    tmp_path: Path,
    *,
    horizons: tuple[int, ...] = (1,),
    grace: float = 5.0,
) -> tuple[LiveLearnEngine, _ReportSink]:
    sink = _ReportSink([])
    engine = LiveLearnEngine(
        shadow_journal_path=tmp_path / "shadow.jsonl",
        outcome_store=JsonlOutcomeStore(tmp_path / "outcomes.jsonl"),
        report_sink=sink,
        horizons=horizons,
        report_cadences=(300,),
        evidence_grace_seconds=grace,
    )
    await engine.start()
    return engine, sink


def test_exact_boundary_and_no_future_leakage(tmp_path: Path) -> None:
    async def run() -> None:
        t0 = datetime.now(UTC) + timedelta(hours=1)
        engine, _ = await _engine(tmp_path)
        initial = _snapshot(t0, now_ns=10_000_000_000)
        decision = _decision(initial)
        early = _snapshot(
            t0 + timedelta(milliseconds=999),
            now_ns=10_999_000_000,
        )
        exact = _snapshot(
            t0 + timedelta(seconds=1),
            now_ns=11_000_000_000,
            bid_ticks=100,
            ask_ticks=104,
        )
        await engine.persist_snapshot(initial)
        await engine.persist_decision(decision)
        await engine.persist_snapshot(early)
        await engine.flush()
        assert engine.outcomes() == ()
        await engine.persist_snapshot(exact)
        await engine.flush()
        assert len(engine.outcomes()) == 1
        outcome = engine.outcomes()[0]
        assert outcome.horizon_seconds == 1
        assert outcome.evidence_observed_at == exact.observed_at
        assert outcome.metric_values["midpoint_markout"] == pytest.approx(0.01)
        await engine.close()

    asyncio.run(run())


def test_deterministic_replay_and_duplicate_inputs_are_idempotent(
    tmp_path: Path,
) -> None:
    async def one(root: Path) -> list[dict[str, object]]:
        t0 = datetime.now(UTC) + timedelta(hours=1)
        engine, _ = await _engine(root)
        initial = _snapshot(t0, now_ns=1_000_000_000)
        decision = _decision(initial)
        future = _snapshot(
            t0 + timedelta(seconds=1),
            now_ns=2_000_000_000,
            bid_ticks=100,
            ask_ticks=102,
        )
        await engine.persist_snapshot(initial)
        await engine.persist_decision(decision)
        await engine.persist_decision(decision)
        await engine.persist_snapshot(future)
        await engine.persist_snapshot(future)
        await engine.flush()
        records = [outcome_record(item) for item in engine.outcomes()]
        await engine.close()
        return records

    first = asyncio.run(one(tmp_path / "a"))
    second = asyncio.run(one(tmp_path / "b"))
    assert first == second
    assert len(first) == 1


def test_restart_recovers_pending_maturity(tmp_path: Path) -> None:
    async def run() -> None:
        t0 = datetime.now(UTC) + timedelta(hours=2)
        initial = _snapshot(t0, now_ns=1_000_000_000)
        decision = _decision(initial)
        shadow_path = tmp_path / "shadow.jsonl"
        shadow_path.write_text(
            json.dumps(event_record(initial)) + "\n"
            + json.dumps(event_record(decision)) + "\n",
            encoding="utf-8",
        )
        first = LiveLearnEngine(
            shadow_journal_path=shadow_path,
            outcome_store=JsonlOutcomeStore(tmp_path / "outcomes.jsonl"),
            report_sink=_ReportSink([]),
            horizons=(1,),
            report_cadences=(300,),
        )
        await first.start()
        assert first.pending_count == 1
        await first.close()

        future = _snapshot(
            t0 + timedelta(seconds=1),
            now_ns=2_000_000_000,
            bid_ticks=100,
            ask_ticks=104,
        )
        with shadow_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event_record(future)) + "\n")

        second = LiveLearnEngine(
            shadow_journal_path=shadow_path,
            outcome_store=JsonlOutcomeStore(tmp_path / "outcomes.jsonl"),
            report_sink=_ReportSink([]),
            horizons=(1,),
            report_cadences=(300,),
        )
        await second.start()
        assert second.pending_count == 0
        assert len(second.outcomes()) == 1
        await second.close()

    asyncio.run(run())


def test_late_evidence_and_missing_price(tmp_path: Path) -> None:
    async def run() -> None:
        t0 = datetime.now(UTC) + timedelta(hours=1)
        late_engine, _ = await _engine(tmp_path / "late", grace=5.0)
        initial = _snapshot(t0, now_ns=1_000_000_000)
        await late_engine.persist_snapshot(initial)
        await late_engine.persist_decision(_decision(initial))
        late = _snapshot(
            t0 + timedelta(seconds=3),
            now_ns=4_000_000_000,
            bid_ticks=100,
            ask_ticks=102,
        )
        await late_engine.persist_snapshot(late)
        await late_engine.flush()
        assert late_engine.outcomes()[0].outcome_status is OutcomeStatus.MATURED_SCORED
        await late_engine.close()

        missing_engine, _ = await _engine(tmp_path / "missing", grace=0.0)
        missing_initial = _snapshot(t0, now_ns=5_000_000_000)
        await missing_engine.persist_snapshot(missing_initial)
        await missing_engine.persist_decision(_decision(missing_initial))
        missing = _snapshot(
            t0 + timedelta(seconds=1),
            now_ns=6_000_000_000,
            bid_ticks=None,
            ask_ticks=None,
        )
        await missing_engine.persist_snapshot(missing)
        await missing_engine.flush()
        await missing_engine._expire_due(t0 + timedelta(seconds=2))
        outcome = missing_engine.outcomes()[0]
        assert outcome.outcome_status is OutcomeStatus.SOURCE_UNAVAILABLE
        assert outcome.missing_reason == "future_midpoint_unavailable"
        await missing_engine.close()

    asyncio.run(run())


def test_abstention_and_unsupported_score_semantics(tmp_path: Path) -> None:
    async def run() -> None:
        t0 = datetime.now(UTC) + timedelta(hours=1)
        engine, _ = await _engine(tmp_path)
        initial = _snapshot(t0, now_ns=1_000_000_000)
        await engine.persist_snapshot(initial)
        await engine.persist_decision(
            _decision(initial, candidate_id="a", status=DecisionStatus.ABSTAIN)
        )
        await engine.persist_decision(
            _decision(initial, candidate_id="u", fair_value=None)
        )
        await engine.persist_snapshot(
            _snapshot(
                t0 + timedelta(seconds=1),
                now_ns=2_000_000_000,
                bid_ticks=100,
                ask_ticks=102,
            )
        )
        await engine.flush()
        outcomes = {item.candidate_id: item for item in engine.outcomes()}
        assert outcomes["a"].outcome_status is OutcomeStatus.DECISION_ABSTAINED
        assert (
            outcomes["u"].component_status["forecast"]
            == OutcomeStatus.UNSUPPORTED_SCORE_SEMANTICS.value
        )
        await engine.close()

    asyncio.run(run())


def test_partial_fill_and_adverse_selection_math() -> None:
    observed = datetime(2026, 9, 30, 12, tzinfo=UTC)
    snapshot = _snapshot(observed, now_ns=1_000_000_000)
    decision = _decision(
        snapshot,
        quote_intent={"bid_ticks": 98, "bid_size": 10},
    )
    initial = MarketEvidence(
        snapshot_id="i",
        exchange_id="1",
        market_id="m1",
        observed_at=observed,
        source_id="sig:i",
        source_observed_at=observed,
        source_monotonic_ns=1,
        best_bid=0.49,
        best_ask=0.51,
        midpoint=0.50,
        trusted=True,
        freshness_seconds=0.0,
    )
    future = MarketEvidence(
        snapshot_id="f",
        exchange_id="1",
        market_id="m1",
        observed_at=observed + timedelta(seconds=1),
        source_id="sig:f",
        source_observed_at=observed + timedelta(seconds=1),
        source_monotonic_ns=2,
        best_bid=0.44,
        best_ask=0.46,
        midpoint=0.45,
        trusted=True,
        freshness_seconds=0.0,
    )
    execution = ExecutionEvidence(
        supported=True,
        reason=None,
        planned_quantity=10.0,
        fills=(
            FillEvidence(
                evidence_id="fill-1",
                logical_operation_id="op",
                exchange_order_id="10",
                exchange_id="1",
                action="buy",
                quantity=5.0,
                price=0.49,
                filled_at=observed + timedelta(milliseconds=200),
                observed_monotonic_ns=1_200_000_000,
            ),
        ),
    )
    result = QuoteEconomicsScorer().score(
        ScoreContext(
            decision=decision,
            initial=initial,
            future=future,
            execution=execution,
        )
    )
    assert result.metrics["fill_rate"] == pytest.approx(0.5)
    assert result.metrics["partial_fill_rate"] == pytest.approx(1.0)
    assert result.metrics["spread_capture"] == pytest.approx(0.01)
    assert result.metrics["post_fill_markout"] == pytest.approx(-0.04)
    assert result.metrics["adverse_selection"] == pytest.approx(0.04)
    assert result.metrics["fill_latency_seconds"] == pytest.approx(0.2)


def _outcome(
    decision: CandidateDecision,
    *,
    horizon: int,
    forecast_error: float,
) -> DecisionOutcome:
    maturity = decision.observed_at + timedelta(seconds=horizon)
    return DecisionOutcome(
        outcome_id=f"o-{decision.decision_id}-{horizon}",
        decision_id=decision.decision_id,
        candidate_id=decision.candidate_id,
        candidate_version=decision.candidate_version,
        strategy_family=decision.strategy_family,
        input_snapshot_id=decision.input_snapshot_id,
        scoring_spec_id="live-learn-001",
        scoring_spec_version="1",
        horizon_seconds=horizon,
        maturity_at=maturity,
        evidence_observed_at=maturity,
        evidence_source_ids=("sig",),
        outcome_status=OutcomeStatus.MATURED_SCORED,
        component_status={"forecast": "MATURED_SCORED"},
        metric_values={"forecast_error": forecast_error},
        dimensions={"market": decision.market_id},
        initial_price=0.5,
        future_price=0.5,
        price_source="sig",
        source_timestamp=maturity,
        source_freshness_seconds=0.0,
        source_trusted=True,
        price_convention="YES_PROBABILITY",
    )


def test_matched_support_comparison_and_report_windows() -> None:
    end = datetime(2026, 9, 30, 12, 10, tzinfo=UTC)
    inside = _snapshot(end - timedelta(minutes=4), now_ns=1_000_000_000)
    outside = _snapshot(end - timedelta(minutes=6), now_ns=2_000_000_000)
    candidate = _decision(inside, candidate_id="alpha")
    baseline = _decision(
        inside,
        candidate_id="direct-pm-reference",
        family="REFERENCE",
    )
    old = _decision(outside, candidate_id="old")
    report = build_report(
        decisions=(candidate, baseline, old),
        outcomes=(
            _outcome(candidate, horizon=1, forecast_error=0.01),
            _outcome(baseline, horizon=1, forecast_error=0.02),
            _outcome(old, horizon=1, forecast_error=0.00),
        ),
        cadence_seconds=300,
        window_seconds=300,
        window_end=end,
        horizons=(1,),
    )
    assert report.payload["decision_count"] == 2
    comparisons = report.payload["matched_support_comparisons"]
    assert isinstance(comparisons, list)
    alpha = next(item for item in comparisons if item["candidate_id"] == "alpha")
    assert alpha["matched_support"] == 1
    assert alpha["candidate_minus_baseline_forecast_error"] == pytest.approx(-0.01)


def test_237_market_burst(tmp_path: Path) -> None:
    async def run() -> None:
        t0 = datetime.now(UTC) + timedelta(hours=1)
        engine, _ = await _engine(tmp_path)
        for index in range(237):
            exchange = str(index + 1)
            initial = _snapshot(
                t0,
                now_ns=1_000_000_000 + index,
                exchange_id=exchange,
                market_id=f"m{index}",
            )
            await engine.persist_snapshot(initial)
            await engine.persist_decision(
                _decision(initial, candidate_id=f"c-{index}")
            )
        for index in range(237):
            await engine.persist_snapshot(
                _snapshot(
                    t0 + timedelta(seconds=1),
                    now_ns=2_000_000_000 + index,
                    exchange_id=str(index + 1),
                    market_id=f"m{index}",
                    bid_ticks=100,
                    ask_ticks=102,
                )
            )
        await engine.flush()
        assert len(engine.outcomes()) == 237
        assert engine.health.queue_high_water <= 200_000
        assert engine.health.failures == 0
        await engine.close()

    asyncio.run(run())
