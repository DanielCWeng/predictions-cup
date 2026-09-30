from __future__ import annotations

import asyncio
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

import pyarrow.dataset as ds

from predictions_cup.observe import (
    BoundedObservationEmitter,
    CallbackObservationSink,
    CompetitionContextSnapshot,
    CrossVenueMapping,
    EconomicChange,
    FieldClassification,
    InMemoryObservationSink,
    ObservationKind,
    SigOfficialCompetitionContextProvider,
    VenueObservation,
    VenueSpanCollector,
    join_pm_to_sig,
    replay_operation,
    summarize_cross_venue,
    summarize_observations,
)
from predictions_cup.sig.dto import AccountDto
from predictions_cup.sig.launch_storage import LaunchSigRecorder
from predictions_cup.sig.realtime_models import (
    TournamentLeaderboardDto,
    TournamentPageDto,
)


_AT = datetime(2026, 10, 1, 16, 0, tzinfo=UTC)


def _obs(
    kind: ObservationKind,
    ns: int,
    *,
    operation: str | None = "op-1",
    process: str = "proc-1",
    strategy_family: str | None = "MAKE",
    source_timestamp: datetime | None = None,
) -> VenueObservation:
    return VenueObservation(
        kind=kind,
        observed_at=_AT + timedelta(microseconds=ns // 1_000),
        monotonic_ns=ns,
        process_instance_id=process,
        source="fixture",
        source_version="observe-001",
        provenance="test",
        tournament_id="cup",
        market_id="m1",
        exchange_id="e1",
        strategy_family=strategy_family,
        strategy_id="s1",
        logical_operation_id=operation,
        idempotency_key="idem-1",
        source_timestamp=source_timestamp,
    )


def test_lifecycle_summary_covers_latency_errors_cancel_reconnect_and_duplicates() -> None:
    rows = (
        _obs(ObservationKind.DECISION_OBSERVED, 0),
        _obs(ObservationKind.REQUEST_DISPATCHED, 10_000_000),
        _obs(ObservationKind.RESPONSE_RECEIVED, 20_000_000),
        _obs(ObservationKind.ACK, 25_000_000),
        _obs(ObservationKind.ACK, 26_000_000),
        _obs(ObservationKind.PARTIAL_FILL, 30_000_000),
        _obs(ObservationKind.FILL, 50_000_000),
        _obs(ObservationKind.FILL, 51_000_000),
        _obs(ObservationKind.CANCEL_REQUESTED, 60_000_000),
        _obs(ObservationKind.CANCEL_ACK, 65_000_000),
        _obs(ObservationKind.RATE_LIMIT, 66_000_000),
        _obs(ObservationKind.SERVER_ERROR, 67_000_000),
        _obs(ObservationKind.TRANSPORT_EXCEPTION, 68_000_000),
        _obs(ObservationKind.UNCERTAIN, 69_000_000),
        _obs(ObservationKind.RECONCILIATION_STARTED, 70_000_000),
        _obs(ObservationKind.RECONCILIATION_RESOLVED, 80_000_000),
        _obs(ObservationKind.RECONNECT_STARTED, 90_000_000, operation=None),
        _obs(ObservationKind.RECONNECT_RESOLVED, 120_000_000, operation=None),
        _obs(ObservationKind.REALTIME_REVISION_GAP, 121_000_000, operation=None),
    )
    summary = summarize_observations(rows)
    assert summary["rate_limit_429_count"] == 1
    assert summary["server_5xx_count"] == 1
    assert summary["transport_exception_count"] == 1
    assert summary["uncertainty_count"] == 1
    assert summary["reconnect_count"] == 1
    assert summary["realtime_revision_gap_count"] == 1
    assert summary["duplicate_ack_evidence"] == 1
    assert summary["duplicate_fill_evidence"] == 1
    latency = cast(dict[str, dict[str, float | None]], summary["latency_ms"])
    assert latency["dispatch_to_ack"]["p50"] == 15.0
    assert latency["ack_to_first_fill"]["p50"] == 5.0
    assert latency["cancel_to_confirmation"]["p50"] == 5.0


def test_replay_orders_lifecycle_and_missing_server_timestamp_stays_explicit() -> None:
    rows = (
        _obs(ObservationKind.ACK, 30),
        _obs(ObservationKind.REQUEST_DISPATCHED, 20),
        _obs(ObservationKind.PLAN_CREATED, 10),
    )
    replay = replay_operation(rows, "op-1")
    assert [item.kind for item in replay] == [
        ObservationKind.PLAN_CREATED,
        ObservationKind.REQUEST_DISPATCHED,
        ObservationKind.ACK,
    ]
    assert replay[-1].source_timestamp is None


def test_spans_never_compare_monotonic_values_across_processes() -> None:
    rows = (
        _obs(ObservationKind.REQUEST_DISPATCHED, 100, process="host-a"),
        _obs(ObservationKind.ACK, 1, process="host-b"),
    )
    assert VenueSpanCollector().collect(rows) == ()


def test_cross_venue_timing_is_descriptive_and_handles_complement() -> None:
    mapping = CrossVenueMapping("pm-no", "sig-1", "COMPLEMENT", "EXACT", "mapping-v1")
    pm = (
        EconomicChange("PM", "pm-no", _AT, 0.50, "pm"),
        EconomicChange("PM", "pm-no", _AT + timedelta(milliseconds=500), 0.40, "pm"),
    )
    sig = (
        EconomicChange("SIG", "sig-1", _AT, 0.50, "sig"),
        EconomicChange("SIG", "sig-1", _AT + timedelta(seconds=1), 0.60, "sig"),
    )
    rows = join_pm_to_sig(mapping=mapping, pm=pm, sig=sig, max_delay_seconds=5)
    assert len(rows) == 1
    assert rows[0].elapsed_seconds == 0.5
    assert rows[0].same_direction is True
    summary = summarize_cross_venue(rows)
    assert summary["matches"] == 1
    assert "not causal" in str(summary["interpretation"])


class _BlockingSink:
    def __init__(self) -> None:
        self.entered = threading.Event()
        self.release = threading.Event()

    def write(self, observation: VenueObservation) -> None:
        del observation
        self.entered.set()
        self.release.wait(timeout=2)


def test_backpressure_is_counted_without_blocking_producer() -> None:
    sink = _BlockingSink()
    emitter = BoundedObservationEmitter(sink, queue_max=1)
    try:
        assert emitter.emit(_obs(ObservationKind.ACK, 1))
        assert sink.entered.wait(timeout=1)
        assert emitter.emit(_obs(ObservationKind.ACK, 2))
        assert emitter.emit(_obs(ObservationKind.ACK, 3)) is False
        assert emitter.health().dropped == 1
    finally:
        sink.release.set()
        emitter.close()


def test_sink_failure_isolated_from_emitter() -> None:
    def fail(observation: VenueObservation) -> None:
        del observation
        raise RuntimeError("fixture sink failure")

    emitter = BoundedObservationEmitter(CallbackObservationSink(fail), queue_max=4)
    assert emitter.emit(_obs(ObservationKind.ACK, 1))
    emitter.close()
    assert emitter.health().sink_failures == 1


class _ContextRest:
    async def list_tournaments(
        self, *, status: str = "any", limit: int = 50, offset: int = 0
    ) -> TournamentPageDto:
        del status, limit, offset
        return TournamentPageDto.model_validate(
            {
                "data": [
                    {
                        "id": "cup-id",
                        "slug": "cup",
                        "name": "SIG Cup",
                        "description": None,
                        "status": "active",
                        "startDate": "2026-10-01T16:00:00Z",
                        "endDate": None,
                        "initialBalance": "1000",
                        "currencyName": "coins",
                        "myBalance": "975",
                        "joinedAt": "2026-09-30T10:00:00Z",
                        "isPendingEnrolment": False,
                    }
                ],
                "pagination": {"limit": 100, "offset": 0, "hasMore": False, "total": 1},
            }
        )

    async def get_account(self) -> AccountDto:
        return AccountDto.model_validate(
            {
                "id": "me",
                "username": "daniel",
                "email": None,
                "createdAt": "2026-09-01T00:00:00Z",
                "avatarUrl": None,
                "bio": None,
                "balance": "975",
            }
        )

    async def get_tournament_leaderboard(
        self,
        tournament_slug: str,
        *,
        period: str = "all",
        limit: int = 50,
        offset: int = 0,
    ) -> TournamentLeaderboardDto:
        assert tournament_slug == "cup"
        del period, limit, offset
        return TournamentLeaderboardDto.model_validate(
            {
                "leaderboard": [
                    {
                        "rank": 3,
                        "profileId": "me",
                        "username": "daniel",
                        "pnl": "15",
                        "tradesCount": 12,
                        "volume": "200",
                        "winRate": "60",
                        "roi": "7.5",
                    }
                ],
                "total": 20,
                "period": "all",
                "limit": 100,
                "offset": 0,
                "myRank": 3,
                "season": None,
                "boundGroups": [],
                "activeGroupId": None,
            }
        )


def test_official_context_exposes_rank_and_marks_super_signal_unavailable() -> None:
    provider = SigOfficialCompetitionContextProvider(
        cast(object, _ContextRest()),
        tournament_id="cup-id",
    )
    snapshot = asyncio.run(provider.snapshot())
    assert snapshot.field("participant_rank").value == 3
    assert snapshot.field("leaderboard").classification is FieldClassification.NORMALIZED
    super_signal = snapshot.field("super_signal")
    assert super_signal.classification is FieldClassification.UNAVAILABLE
    assert super_signal.unavailable_reason == "ADMIN_ONLY_PARTICIPANT_KEY_UNSUPPORTED"


def test_capture001_persists_observation_and_context_streams(tmp_path: Path) -> None:
    recorder = LaunchSigRecorder(
        tmp_path / "sig.sqlite3",
        research_root=tmp_path / "research",
        shard_seconds=1,
        max_rows_per_shard=100,
        queue_max=100,
        session_id="observe-test",
    )
    provider = SigOfficialCompetitionContextProvider(
        cast(object, _ContextRest()),
        tournament_id="cup-id",
    )
    snapshot = asyncio.run(provider.snapshot())
    recorder.record_venue_observation(_obs(ObservationKind.REALTIME_REVISION_GAP, 5))
    recorder.record_competition_context(snapshot)
    recorder.close()

    venue_files = sorted((tmp_path / "research" / "venue_observations").rglob("*.parquet"))
    context_files = sorted((tmp_path / "research" / "competition_context").rglob("*.parquet"))
    assert venue_files and context_files
    venue = ds.dataset([str(path) for path in venue_files], format="parquet").to_table().to_pylist()
    context = ds.dataset([str(path) for path in context_files], format="parquet").to_table().to_pylist()
    assert venue[0]["kind"] == "REALTIME_REVISION_GAP"
    assert context[0]["tournament_id"] == "cup-id"
    assert "super_signal" in str(context[0]["fields_json"])
