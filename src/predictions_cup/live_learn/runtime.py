"""Always-on maturity scheduler and scorer for LIVE-LEARN-001."""

from __future__ import annotations

import asyncio
import heapq
import math
from collections.abc import Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

from predictions_cup.live_learn.contracts import (
    DecisionOutcome,
    MarketEvidence,
    OutcomeStatus,
    outcome_id_for,
)
from predictions_cup.live_learn.evidence import (
    ExecutionEvidenceProvider,
    JournalExecutionEvidenceProvider,
    NullExecutionEvidenceProvider,
    ObservableMarketState,
)
from predictions_cup.live_learn.persistence import JsonlOutcomeStore
from predictions_cup.live_learn.reporting import FileReportSink, ReportSink, build_report
from predictions_cup.live_learn.scoring import ScoreContext, ScorerRegistry
from predictions_cup.shadow.contracts import (
    CandidateDecision,
    CanonicalShadowSnapshot,
    DecisionStatus,
)
from predictions_cup.shadow.persistence import PersistenceHealth, read_jsonl_records

DEFAULT_HORIZONS = (1, 5, 15, 30, 60, 300)
DEFAULT_REPORT_CADENCES = (300, 900, 3600)
SCORING_SPEC_ID = "live-learn-001"
SCORING_SPEC_VERSION = "1"


@dataclass(frozen=True, slots=True)
class _Pending:
    outcome_id: str
    decision_id: str
    horizon_seconds: int
    maturity_at: datetime
    expires_at: datetime
    last_failure_status: OutcomeStatus | None = None
    last_failure_reason: str | None = None


@dataclass(frozen=True, slots=True)
class _SnapshotEvent:
    state: ObservableMarketState


@dataclass(frozen=True, slots=True)
class _DecisionEvent:
    decision: CandidateDecision


QueueItem = _SnapshotEvent | _DecisionEvent


class LiveLearnEngine:
    """SHADOW event-store mirror plus bounded, deadline-driven scoring runtime."""

    def __init__(
        self,
        *,
        shadow_journal_path: Path,
        outcome_store: JsonlOutcomeStore,
        report_sink: ReportSink,
        execution_provider: ExecutionEvidenceProvider | None = None,
        scorer_registry: ScorerRegistry | None = None,
        horizons: Sequence[int] = DEFAULT_HORIZONS,
        report_cadences: Sequence[int] = DEFAULT_REPORT_CADENCES,
        queue_capacity: int = 200_000,
        evidence_grace_seconds: float = 5.0,
        max_evidence_age_seconds: float = 15.0,
    ) -> None:
        normalized_horizons = tuple(sorted(set(int(value) for value in horizons)))
        normalized_cadences = tuple(
            sorted(set(int(value) for value in report_cadences))
        )
        if not normalized_horizons or min(normalized_horizons) <= 0:
            raise ValueError("LIVE-LEARN horizons must be positive")
        if not normalized_cadences or min(normalized_cadences) <= 0:
            raise ValueError("LIVE-LEARN report cadences must be positive")
        if queue_capacity <= 0:
            raise ValueError("LIVE-LEARN queue capacity must be positive")
        if evidence_grace_seconds < 0.0:
            raise ValueError("evidence grace must be non-negative")
        if max_evidence_age_seconds <= 0.0:
            raise ValueError("max evidence age must be positive")

        self._shadow_journal_path = shadow_journal_path
        self._outcome_store = outcome_store
        self._report_sink = report_sink
        self._execution_provider = (
            execution_provider or NullExecutionEvidenceProvider()
        )
        self._scorers = scorer_registry or ScorerRegistry.default()
        self._horizons = normalized_horizons
        self._report_cadences = normalized_cadences
        self._evidence_grace_seconds = evidence_grace_seconds
        self._max_evidence_age_seconds = max_evidence_age_seconds
        self._queue: asyncio.Queue[QueueItem] = asyncio.Queue(maxsize=queue_capacity)
        self._queue_high_water = 0
        self._worker_task: asyncio.Task[None] | None = None
        self._deadline_task: asyncio.Task[None] | None = None
        self._wakeup = asyncio.Event()
        self._started = False
        self._closed = False
        self._failures = 0
        self._last_error: str | None = None
        self._decisions: dict[str, CandidateDecision] = {}
        self._states_by_snapshot: dict[str, ObservableMarketState] = {}
        self._pending: dict[str, _Pending] = {}
        self._market_heaps: dict[str, list[tuple[float, int, str]]] = {}
        self._expiry_heap: list[tuple[float, int, str]] = []
        self._sequence = 0
        self._next_report_at: dict[int, datetime] = {}
        self._reports_emitted = 0

    @classmethod
    def from_paths(
        cls,
        *,
        shadow_journal_path: Path,
        outcome_path: Path,
        report_root: Path,
        execution_journal_path: Path | None = None,
        queue_capacity: int = 200_000,
        evidence_grace_seconds: float = 5.0,
        max_evidence_age_seconds: float = 15.0,
    ) -> LiveLearnEngine:
        execution_provider: ExecutionEvidenceProvider
        if execution_journal_path is None:
            execution_provider = NullExecutionEvidenceProvider()
        else:
            execution_provider = JournalExecutionEvidenceProvider(
                execution_journal_path
            )
        return cls(
            shadow_journal_path=shadow_journal_path,
            outcome_store=JsonlOutcomeStore(outcome_path),
            report_sink=FileReportSink(report_root),
            execution_provider=execution_provider,
            queue_capacity=queue_capacity,
            evidence_grace_seconds=evidence_grace_seconds,
            max_evidence_age_seconds=max_evidence_age_seconds,
        )

    async def start(self) -> None:
        if self._closed:
            raise RuntimeError("LIVE-LEARN engine is closed")
        if self._started:
            return
        await self._outcome_store.start()
        await self._recover()
        now = datetime.now(UTC)
        await self._expire_due(now)
        self._next_report_at = {
            cadence: _next_boundary(now, cadence)
            for cadence in self._report_cadences
        }
        self._started = True
        self._worker_task = asyncio.create_task(
            self._worker(),
            name="live-learn-worker",
        )
        self._deadline_task = asyncio.create_task(
            self._deadline_loop(),
            name="live-learn-deadlines",
        )

    async def persist_snapshot(self, snapshot: CanonicalShadowSnapshot) -> None:
        self._require_started()
        self._submit(_SnapshotEvent(ObservableMarketState.from_snapshot(snapshot)))

    async def persist_decision(self, decision: CandidateDecision) -> None:
        self._require_started()
        self._submit(_DecisionEvent(decision))

    async def flush(self) -> None:
        if not self._started:
            return
        await self._queue.join()
        if self._last_error is not None:
            raise RuntimeError(f"LIVE-LEARN unhealthy: {self._last_error}")

    async def close(self) -> None:
        if self._closed:
            return
        error: Exception | None = None
        if self._started:
            try:
                await self.flush()
            except Exception as exc:
                error = exc
            for task in (self._worker_task, self._deadline_task):
                if task is not None:
                    task.cancel()
            await asyncio.gather(
                *(
                    task
                    for task in (self._worker_task, self._deadline_task)
                    if task is not None
                ),
                return_exceptions=True,
            )
        self._worker_task = None
        self._deadline_task = None
        self._started = False
        self._closed = True
        await self._outcome_store.close()
        if error is not None:
            raise error

    @property
    def health(self) -> PersistenceHealth:
        return PersistenceHealth(
            healthy=self._last_error is None
            and self._outcome_store.last_error is None,
            persisted_events=len(self._outcome_store.outcomes()),
            failures=self._failures + self._outcome_store.failures,
            last_error=self._last_error or self._outcome_store.last_error,
            queue_depth=self._queue.qsize(),
            queue_high_water=self._queue_high_water,
            write_batches=self._outcome_store.write_batches,
            write_latency_p95_ns=self._outcome_store.write_latency_p95_ns,
        )

    @property
    def pending_count(self) -> int:
        return len(self._pending)

    @property
    def reports_emitted(self) -> int:
        return self._reports_emitted

    def decisions(self) -> tuple[CandidateDecision, ...]:
        return tuple(
            sorted(
                self._decisions.values(),
                key=lambda item: (item.observed_at, item.decision_id),
            )
        )

    def outcomes(self) -> tuple[DecisionOutcome, ...]:
        return self._outcome_store.outcomes()

    async def build_report_now(
        self,
        *,
        cadence_seconds: int,
        window_seconds: int | None = None,
        window_end: datetime | None = None,
    ) -> object:
        end = (window_end or datetime.now(UTC)).astimezone(UTC)
        report = build_report(
            decisions=self.decisions(),
            outcomes=self.outcomes(),
            cadence_seconds=cadence_seconds,
            window_seconds=window_seconds or cadence_seconds,
            window_end=end,
            horizons=self._horizons,
        )
        await self._report_sink.emit(report)
        return report

    def _submit(self, item: QueueItem) -> None:
        try:
            self._queue.put_nowait(item)
        except asyncio.QueueFull as exc:
            self._failures += 1
            self._last_error = "bounded_input_queue_full"
            raise RuntimeError("LIVE-LEARN bounded input queue is full") from exc
        self._queue_high_water = max(self._queue_high_water, self._queue.qsize())
        self._wakeup.set()

    async def _recover(self) -> None:
        if not self._shadow_journal_path.exists():
            return
        records = await asyncio.to_thread(
            read_jsonl_records,
            self._shadow_journal_path,
        )
        for record in records:
            event_type = record.get("event_type")
            if event_type == "snapshot":
                state = ObservableMarketState.from_shadow_record(record)
                await self._handle_state(state)
            elif event_type == "decision":
                await self._handle_decision(_decision_from_record(record))

    async def _worker(self) -> None:
        while True:
            item = await self._queue.get()
            try:
                if isinstance(item, _SnapshotEvent):
                    await self._handle_state(item.state)
                else:
                    await self._handle_decision(item.decision)
            except Exception as exc:
                self._failures += 1
                self._last_error = f"{type(exc).__name__}:{exc}"
            finally:
                self._queue.task_done()
                self._wakeup.set()

    async def _handle_decision(self, decision: CandidateDecision) -> None:
        existing = self._decisions.get(decision.decision_id)
        if existing is not None:
            if existing != decision:
                raise ValueError("decision_id reused with changed decision")
            return
        self._decisions[decision.decision_id] = decision
        for horizon in self._horizons:
            outcome_id = outcome_id_for(
                decision.decision_id,
                SCORING_SPEC_ID,
                SCORING_SPEC_VERSION,
                horizon,
            )
            if self._outcome_store.contains(outcome_id):
                continue
            maturity = decision.observed_at.astimezone(UTC) + timedelta(
                seconds=horizon
            )
            expires = maturity + timedelta(seconds=self._evidence_grace_seconds)
            pending = _Pending(
                outcome_id=outcome_id,
                decision_id=decision.decision_id,
                horizon_seconds=horizon,
                maturity_at=maturity,
                expires_at=expires,
            )
            self._pending[outcome_id] = pending
            self._sequence += 1
            heap = self._market_heaps.setdefault(decision.exchange_id, [])
            heapq.heappush(
                heap,
                (maturity.timestamp(), self._sequence, outcome_id),
            )
            heapq.heappush(
                self._expiry_heap,
                (expires.timestamp(), self._sequence, outcome_id),
            )

    async def _handle_state(self, state: ObservableMarketState) -> None:
        self._states_by_snapshot[state.snapshot_id] = state
        heap = self._market_heaps.get(state.exchange_id)
        if not heap:
            return
        due: list[_Pending] = []
        while heap and heap[0][0] <= state.observed_at.timestamp():
            _, _, outcome_id = heapq.heappop(heap)
            pending = self._pending.get(outcome_id)
            if pending is not None:
                due.append(pending)
        retry: list[_Pending] = []
        outcomes: list[DecisionOutcome] = []
        for pending in due:
            outcome = await self._score_pending(pending, state)
            if outcome is None:
                retry.append(self._pending[pending.outcome_id])
            else:
                outcomes.append(outcome)
                self._pending.pop(pending.outcome_id, None)
        for pending in retry:
            self._sequence += 1
            heapq.heappush(
                heap,
                (pending.maturity_at.timestamp(), self._sequence, pending.outcome_id),
            )
        if outcomes:
            await self._outcome_store.persist_many(outcomes)

    async def _score_pending(
        self,
        pending: _Pending,
        future_state: ObservableMarketState,
    ) -> DecisionOutcome | None:
        decision = self._decisions[pending.decision_id]
        if future_state.observed_at < pending.maturity_at:
            raise AssertionError("future evidence selected before maturity")
        if future_state.observed_at > pending.expires_at:
            return self._failure_outcome(
                pending,
                decision,
                OutcomeStatus.STALE_UNTRUSTED_EVIDENCE,
                future_state,
                "future_observation_arrived_after_grace",
            )

        initial_state = self._states_by_snapshot.get(decision.input_snapshot_id)
        if initial_state is None:
            return self._failure_outcome(
                pending,
                decision,
                OutcomeStatus.INSUFFICIENT_HISTORY,
                future_state,
                "input_snapshot_unavailable",
            )
        initial = initial_state.evidence()
        future = future_state.evidence()

        if initial.midpoint is None:
            return self._failure_outcome(
                pending,
                decision,
                OutcomeStatus.INSUFFICIENT_HISTORY,
                future_state,
                "initial_midpoint_unavailable",
            )
        if not initial.trusted or initial.freshness_seconds > self._max_evidence_age_seconds:
            return self._failure_outcome(
                pending,
                decision,
                OutcomeStatus.STALE_UNTRUSTED_EVIDENCE,
                future_state,
                "initial_evidence_stale_or_untrusted",
            )

        if future.midpoint is None:
            self._remember_failure(
                pending,
                OutcomeStatus.SOURCE_UNAVAILABLE,
                "future_midpoint_unavailable",
            )
            return None
        if not future.trusted or future.freshness_seconds > self._max_evidence_age_seconds:
            self._remember_failure(
                pending,
                OutcomeStatus.STALE_UNTRUSTED_EVIDENCE,
                "future_evidence_stale_or_untrusted",
            )
            return None

        if decision.decision_status is DecisionStatus.ABSTAIN:
            return self._completed_outcome(
                pending=pending,
                decision=decision,
                initial=initial,
                future=future,
                metrics={
                    "compute_latency_ms": decision.compute_latency_ns / 1e6,
                },
                component_status={
                    "price": OutcomeStatus.MATURED_SCORED.value,
                    "forecast": OutcomeStatus.DECISION_ABSTAINED.value,
                    "execution": OutcomeStatus.DECISION_ABSTAINED.value,
                },
                status=OutcomeStatus.DECISION_ABSTAINED,
                missing_reason=decision.abstain_reason,
            )
        if decision.decision_status is not DecisionStatus.OK:
            return self._completed_outcome(
                pending=pending,
                decision=decision,
                initial=initial,
                future=future,
                metrics={
                    "compute_latency_ms": decision.compute_latency_ns / 1e6,
                },
                component_status={
                    "price": OutcomeStatus.MATURED_SCORED.value,
                    "forecast": OutcomeStatus.INSUFFICIENT_HISTORY.value,
                },
                status=OutcomeStatus.INSUFFICIENT_HISTORY,
                missing_reason=f"decision_status:{decision.decision_status.value}",
            )

        execution = await self._execution_provider.evidence_for(
            decision,
            maturity_at=pending.maturity_at,
        )
        metrics = {
            "midpoint_markout": future.midpoint - initial.midpoint,
            "compute_latency_ms": decision.compute_latency_ns / 1e6,
        }
        executable = _executable_markout(decision, initial, future)
        if executable is not None:
            metrics["executable_markout"] = executable

        component_status = {"price": OutcomeStatus.MATURED_SCORED.value}
        scorer = self._scorers.select(decision)
        if scorer is None:
            component_status["forecast"] = (
                OutcomeStatus.UNSUPPORTED_SCORE_SEMANTICS.value
            )
        else:
            result = scorer.score(
                ScoreContext(
                    decision=decision,
                    initial=initial,
                    future=future,
                    execution=execution,
                )
            )
            metrics.update(result.metrics)
            component_status.update(result.component_status)
            component_status["scorer"] = f"{result.scorer_id}@{result.scorer_version}"

        if decision.quote_intent is not None and "execution" not in component_status:
            component_status["execution"] = (
                OutcomeStatus.EXECUTION_EVIDENCE_UNAVAILABLE.value
                if not execution.supported
                else OutcomeStatus.MATURED_SCORED.value
            )
        return self._completed_outcome(
            pending=pending,
            decision=decision,
            initial=initial,
            future=future,
            metrics=metrics,
            component_status=component_status,
            status=OutcomeStatus.MATURED_SCORED,
            missing_reason=(
                execution.reason
                if decision.quote_intent is not None and not execution.supported
                else None
            ),
            extra_source_ids=execution.evidence_source_ids,
        )

    def _remember_failure(
        self,
        pending: _Pending,
        status: OutcomeStatus,
        reason: str,
    ) -> None:
        self._pending[pending.outcome_id] = _Pending(
            outcome_id=pending.outcome_id,
            decision_id=pending.decision_id,
            horizon_seconds=pending.horizon_seconds,
            maturity_at=pending.maturity_at,
            expires_at=pending.expires_at,
            last_failure_status=status,
            last_failure_reason=reason,
        )

    async def _expire_due(self, now: datetime) -> None:
        expired: list[DecisionOutcome] = []
        while self._expiry_heap and self._expiry_heap[0][0] <= now.timestamp():
            _, _, outcome_id = heapq.heappop(self._expiry_heap)
            pending = self._pending.pop(outcome_id, None)
            if pending is None:
                continue
            decision = self._decisions[pending.decision_id]
            status = pending.last_failure_status or OutcomeStatus.SOURCE_UNAVAILABLE
            reason = pending.last_failure_reason or "no_future_observation_within_grace"
            expired.append(
                self._failure_outcome(
                    pending,
                    decision,
                    status,
                    None,
                    reason,
                )
            )
        if expired:
            await self._outcome_store.persist_many(expired)

    def _failure_outcome(
        self,
        pending: _Pending,
        decision: CandidateDecision,
        status: OutcomeStatus,
        future_state: ObservableMarketState | None,
        reason: str,
    ) -> DecisionOutcome:
        initial_state = self._states_by_snapshot.get(decision.input_snapshot_id)
        initial = None if initial_state is None else initial_state.evidence()
        future = None if future_state is None else future_state.evidence()
        return DecisionOutcome(
            outcome_id=pending.outcome_id,
            decision_id=decision.decision_id,
            candidate_id=decision.candidate_id,
            candidate_version=decision.candidate_version,
            strategy_family=decision.strategy_family,
            input_snapshot_id=decision.input_snapshot_id,
            scoring_spec_id=SCORING_SPEC_ID,
            scoring_spec_version=SCORING_SPEC_VERSION,
            horizon_seconds=pending.horizon_seconds,
            maturity_at=pending.maturity_at,
            evidence_observed_at=(
                None if future is None else future.observed_at
            ),
            evidence_source_ids=tuple(
                item.source_id for item in (initial, future) if item is not None
            ),
            outcome_status=status,
            component_status={"price": status.value},
            metric_values={
                "compute_latency_ms": decision.compute_latency_ns / 1e6,
            },
            dimensions=_dimensions(decision),
            initial_price=None if initial is None else initial.midpoint,
            future_price=None if future is None else future.midpoint,
            price_source=None if future is None else future.source_id,
            source_timestamp=(
                None if future is None else future.source_observed_at
            ),
            source_freshness_seconds=(
                None if future is None else future.freshness_seconds
            ),
            source_trusted=None if future is None else future.trusted,
            price_convention="YES_PROBABILITY",
            missing_reason=reason,
        )

    def _completed_outcome(
        self,
        *,
        pending: _Pending,
        decision: CandidateDecision,
        initial: MarketEvidence,
        future: MarketEvidence,
        metrics: Mapping[str, float],
        component_status: Mapping[str, str],
        status: OutcomeStatus,
        missing_reason: str | None,
        extra_source_ids: Sequence[str] = (),
    ) -> DecisionOutcome:
        return DecisionOutcome(
            outcome_id=pending.outcome_id,
            decision_id=decision.decision_id,
            candidate_id=decision.candidate_id,
            candidate_version=decision.candidate_version,
            strategy_family=decision.strategy_family,
            input_snapshot_id=decision.input_snapshot_id,
            scoring_spec_id=SCORING_SPEC_ID,
            scoring_spec_version=SCORING_SPEC_VERSION,
            horizon_seconds=pending.horizon_seconds,
            maturity_at=pending.maturity_at,
            evidence_observed_at=future.observed_at,
            evidence_source_ids=tuple(
                dict.fromkeys(
                    (initial.source_id, future.source_id, *extra_source_ids)
                )
            ),
            outcome_status=status,
            component_status=dict(component_status),
            metric_values=dict(metrics),
            dimensions=_dimensions(decision),
            initial_price=initial.midpoint,
            future_price=future.midpoint,
            price_source=future.source_id,
            source_timestamp=future.source_observed_at,
            source_freshness_seconds=future.freshness_seconds,
            source_trusted=future.trusted,
            price_convention=future.price_convention,
            missing_reason=missing_reason,
        )

    async def _deadline_loop(self) -> None:
        while True:
            now = datetime.now(UTC)
            try:
                await self._expire_due(now)
                await self._emit_due_reports(now)
            except Exception as exc:
                self._failures += 1
                self._last_error = f"{type(exc).__name__}:{exc}"
            timeout = self._next_timeout_seconds(datetime.now(UTC))
            self._wakeup.clear()
            with suppress(TimeoutError):
                await asyncio.wait_for(self._wakeup.wait(), timeout=timeout)

    async def _emit_due_reports(self, now: datetime) -> None:
        for cadence in self._report_cadences:
            due = self._next_report_at[cadence]
            while due <= now:
                report = build_report(
                    decisions=self.decisions(),
                    outcomes=self.outcomes(),
                    cadence_seconds=cadence,
                    window_seconds=cadence,
                    window_end=due,
                    horizons=self._horizons,
                )
                await self._report_sink.emit(report)
                self._reports_emitted += 1
                due = due + timedelta(seconds=cadence)
            self._next_report_at[cadence] = due

    def _next_timeout_seconds(self, now: datetime) -> float:
        deadlines = [
            value.timestamp()
            for value in self._next_report_at.values()
        ]
        if self._expiry_heap:
            deadlines.append(self._expiry_heap[0][0])
        if not deadlines:
            return 60.0
        return max(0.01, min(60.0, min(deadlines) - now.timestamp()))

    def _require_started(self) -> None:
        if not self._started:
            raise RuntimeError("LIVE-LEARN engine is not started")


def _decision_from_record(record: Mapping[str, object]) -> CandidateDecision:
    quote_raw = record.get("quote_intent")
    payload_raw = record.get("candidate_payload", {})
    return CandidateDecision(
        decision_id=str(record["decision_id"]),
        candidate_id=str(record["candidate_id"]),
        candidate_version=str(record["candidate_version"]),
        strategy_family=str(record["strategy_family"]),
        observed_at=datetime.fromisoformat(str(record["observed_at"])),
        monotonic_time=int(record["monotonic_time"]),
        tournament_id=str(record["tournament_id"]),
        exchange_id=str(record["exchange_id"]),
        market_id=str(record["market_id"]),
        input_snapshot_id=str(record["input_snapshot_id"]),
        mapping_version=str(record["mapping_version"]),
        fair_value=(
            None if record.get("fair_value") is None else float(record["fair_value"])
        ),
        lower_bound=(
            None if record.get("lower_bound") is None else float(record["lower_bound"])
        ),
        upper_bound=(
            None if record.get("upper_bound") is None else float(record["upper_bound"])
        ),
        confidence=(
            None if record.get("confidence") is None else float(record["confidence"])
        ),
        direction=(
            None if record.get("direction") is None else str(record["direction"])
        ),
        score=None if record.get("score") is None else float(record["score"]),
        action_intent=(
            None
            if record.get("action_intent") is None
            else str(record["action_intent"])
        ),
        quote_intent=(
            None
            if quote_raw is None
            else cast(Mapping[str, object], dict(cast(Mapping[str, object], quote_raw)))
        ),
        decision_status=DecisionStatus(str(record["decision_status"])),
        abstain_reason=(
            None
            if record.get("abstain_reason") is None
            else str(record["abstain_reason"])
        ),
        quality_flags=tuple(
            str(value)
            for value in cast(Sequence[object], record.get("quality_flags", ()))
        ),
        compute_started_at=int(record.get("compute_started_at", 0)),
        compute_finished_at=int(record.get("compute_finished_at", 0)),
        compute_latency_ns=int(record.get("compute_latency_ns", 0)),
        candidate_payload=dict(cast(Mapping[str, object], payload_raw)),
        schema_version=int(record.get("schema_version", 1)),
    )


def _dimensions(decision: CandidateDecision) -> dict[str, str]:
    result = {
        "candidate": decision.candidate_id,
        "candidate_version": decision.candidate_version,
        "strategy_family": decision.strategy_family,
        "market": decision.market_id,
        "exchange": decision.exchange_id,
        "mapping_version": decision.mapping_version,
    }
    for key in ("mapping_class", "liquidity_bucket", "regime"):
        value = decision.candidate_payload.get(key)
        if isinstance(value, str) and value.strip():
            result[key] = value
    return result


def _executable_markout(
    decision: CandidateDecision,
    initial: MarketEvidence,
    future: MarketEvidence,
) -> float | None:
    action = (decision.action_intent or "").upper()
    if action == "BUY" and initial.best_ask is not None and future.best_bid is not None:
        return future.best_bid - initial.best_ask
    if action == "SELL" and initial.best_bid is not None and future.best_ask is not None:
        return initial.best_bid - future.best_ask
    return None


def _next_boundary(now: datetime, cadence_seconds: int) -> datetime:
    timestamp = now.timestamp()
    boundary = (math.floor(timestamp / cadence_seconds) + 1) * cadence_seconds
    return datetime.fromtimestamp(boundary, tz=UTC)
