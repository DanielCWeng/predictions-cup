from __future__ import annotations

import asyncio
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pyarrow.dataset as ds

from predictions_cup.observe import (
    BoundedObservationEmitter,
    CallbackObservationSink,
    CrossVenueMapping,
    EconomicChange,
    EmitterHealth,
    FieldClassification,
    ObservationHealthProvider,
    ObservationHealthSnapshot,
    ObservationHealthState,
    ObservationHealthStatusPublisher,
    ObservationStatusState,
    ObservationKind,
    SigOfficialCompetitionContextProvider,
    VenueObservation,
    VenueSpanCollector,
    join_pm_to_sig,
    read_observation_health_status,
    replay_operation,
    summarize_competition_context,
    summarize_cross_venue,
    summarize_observations,
)
from predictions_cup.sig.dto import AccountDto
from predictions_cup.sig.launch_storage import LaunchSigRecorder, ObservationCaptureRecorder
from predictions_cup.sig.realtime_models import (
    TournamentLeaderboardDto,
    TournamentListStatus,
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
    logical_intent_id: str | None = None,
    exchange_order_id: str | None = None,
    detail: tuple[tuple[str, str], ...] = (),
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
        logical_intent_id=logical_intent_id,
        idempotency_key="idem-1",
        exchange_order_id=exchange_order_id,
        source_timestamp=source_timestamp,
        detail=detail,
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
        _obs(
            ObservationKind.QUOTE_PUBLISHED,
            130_000_000,
            operation="quote-op",
            logical_intent_id="quote-intent",
            detail=(("quote_key", "quote-op|e1|bid"), ("side", "bid")),
        ),
        _obs(
            ObservationKind.QUOTE_REPLENISHED,
            131_000_000,
            operation="quote-op",
            logical_intent_id="quote-intent",
            detail=(("quote_key", "quote-op|e1|bid"), ("side", "bid")),
        ),
        _obs(
            ObservationKind.QUOTE_WITHDRAWN,
            145_000_000,
            operation="quote-op",
            detail=(("quote_key", "quote-op|e1|bid"), ("side", "bid")),
        ),
        _obs(ObservationKind.REALTIME_REVISION_GAP, 150_000_000, operation=None),
    )
    summary = summarize_observations(rows)
    assert summary["rate_limit_429_count"] == 1
    assert summary["server_5xx_count"] == 1
    assert summary["transport_exception_count"] == 1
    assert summary["uncertainty_count"] == 1
    assert summary["uncertainty_rate"] == 0.5
    assert summary["rate_limit_429_rate"] == 0.5
    assert summary["server_5xx_rate"] == 0.5
    assert summary["reconnect_count"] == 1
    assert summary["reconnect_resolved_count"] == 1
    assert summary["reconnect_unresolved_count"] == 0
    assert summary["realtime_revision_gap_count"] == 1
    assert summary["replenishment_observation_count"] == 1
    assert summary["duplicate_ack_evidence"] == 1
    assert summary["duplicate_fill_evidence"] == 1
    latency = summary["latency_ms"]
    assert isinstance(latency, dict)
    dispatch_to_ack = latency["dispatch_to_ack"]
    ack_to_first_fill = latency["ack_to_first_fill"]
    cancel_to_ack = latency["cancel_to_ack"]
    quote_lifetime = latency["quote_lifetime"]
    assert isinstance(dispatch_to_ack, dict)
    assert isinstance(ack_to_first_fill, dict)
    assert isinstance(cancel_to_ack, dict)
    assert isinstance(quote_lifetime, dict)
    assert dispatch_to_ack["p50"] == 15.0
    assert ack_to_first_fill["p50"] == 5.0
    assert cancel_to_ack["p50"] == 5.0
    assert quote_lifetime["p50"] == 15.0


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


def test_quote_lifetime_is_side_specific_with_two_sided_operation() -> None:
    rows = (
        _obs(
            ObservationKind.QUOTE_PUBLISHED,
            10_000_000,
            operation="two-sided",
            logical_intent_id="bid-intent",
            detail=(("quote_key", "two-sided|e1|bid"), ("side", "bid")),
        ),
        _obs(
            ObservationKind.QUOTE_PUBLISHED,
            10_000_000,
            operation="two-sided",
            logical_intent_id="ask-intent",
            detail=(("quote_key", "two-sided|e1|ask"), ("side", "ask")),
        ),
        _obs(
            ObservationKind.FILL,
            20_000_000,
            operation="two-sided",
            logical_intent_id="bid-intent",
        ),
        _obs(
            ObservationKind.QUOTE_WITHDRAWN,
            40_000_000,
            operation="two-sided",
            detail=(("quote_key", "two-sided|e1|ask"), ("side", "ask")),
        ),
    )
    spans = tuple(
        span
        for span in VenueSpanCollector().collect(rows)
        if span.name.value == "quote_lifetime"
    )
    assert [(span.identity, span.duration_ns) for span in spans] == [
        ("intent:bid-intent", 10_000_000),
        ("quote:two-sided|e1|ask", 30_000_000),
    ]


def test_ambiguous_fill_does_not_end_quote_lifetime() -> None:
    rows = (
        _obs(
            ObservationKind.QUOTE_PUBLISHED,
            10_000_000,
            operation="quote-op",
            logical_intent_id="bid-intent",
            detail=(("quote_key", "quote-op|e1|bid"), ("side", "bid")),
        ),
        _obs(
            ObservationKind.FILL,
            20_000_000,
            operation="quote-op",
            logical_intent_id=None,
        ),
    )
    assert not any(
        span.name.value == "quote_lifetime"
        for span in VenueSpanCollector().collect(rows)
    )


def test_cancel_pending_is_ack_timing_not_confirmation() -> None:
    rows = (
        _obs(ObservationKind.CANCEL_REQUESTED, 10_000_000),
        _obs(
            ObservationKind.CANCEL_ACK,
            15_000_000,
            detail=(("state", "CANCEL_PENDING"),),
        ),
    )
    spans = VenueSpanCollector().collect(rows)
    cancel_spans = tuple(span for span in spans if span.name.value.startswith("cancel_"))
    assert len(cancel_spans) == 1
    assert cancel_spans[0].name.value == "cancel_to_ack"
    assert cancel_spans[0].duration_ns == 5_000_000


def test_reconnect_distribution_retains_multiple_reconnects() -> None:
    rows = (
        _obs(ObservationKind.RECONNECT_STARTED, 10_000_000, operation=None),
        _obs(ObservationKind.RECONNECT_RESOLVED, 20_000_000, operation=None),
        _obs(ObservationKind.RECONNECT_STARTED, 30_000_000, operation=None),
        _obs(ObservationKind.RECONNECT_RESOLVED, 50_000_000, operation=None),
    )
    spans = tuple(
        span
        for span in VenueSpanCollector().collect(rows)
        if span.name.value == "reconnect"
    )
    assert [span.duration_ns for span in spans] == [10_000_000, 20_000_000]


def test_replay_uses_utc_across_process_restart() -> None:
    late = VenueObservation(
        kind=ObservationKind.RECONCILIATION_RESOLVED,
        observed_at=_AT + timedelta(seconds=2),
        monotonic_ns=1,
        process_instance_id="aaa-new-process",
        source="fixture",
        source_version="observe-001",
        provenance="test",
        logical_operation_id="op-restart",
    )
    early = VenueObservation(
        kind=ObservationKind.UNCERTAIN,
        observed_at=_AT + timedelta(seconds=1),
        monotonic_ns=999_999_999,
        process_instance_id="zzz-old-process",
        source="fixture",
        source_version="observe-001",
        provenance="test",
        logical_operation_id="op-restart",
    )
    replay = replay_operation((late, early), "op-restart")
    assert [item.kind for item in replay] == [
        ObservationKind.UNCERTAIN,
        ObservationKind.RECONCILIATION_RESOLVED,
    ]


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


def test_combined_observation_health_is_typed_and_runtime_consumable(
    tmp_path: Path,
) -> None:
    recorder = ObservationCaptureRecorder(
        tmp_path / "observe-health",
        queue_max=16,
        shard_seconds=1,
        max_rows_per_shard=100,
        session_id="health-test",
    )
    emitter = BoundedObservationEmitter(
        CallbackObservationSink(recorder.record_venue_observation),
        queue_max=16,
    )
    provider = ObservationHealthProvider(emitter, recorder)
    try:
        snapshot = provider.health()
        assert snapshot.state is ObservationHealthState.HEALTHY
        payload = snapshot.to_dict()
        assert payload["state"] == "HEALTHY"
        assert isinstance(payload["emitter"], dict)
        assert isinstance(payload["capture"], dict)
    finally:
        emitter.close()
        recorder.close()


class _DegradedCaptureHealth:
    def capture_health_snapshot(self) -> dict[str, object]:
        return {
            "writer_alive": True,
            "queue_depth": 0,
            "queue_capacity": 16,
            "queue_high_water": 16,
            "written_rows": 10,
            "written_shards": 1,
            "dropped_rows": 1,
            "storage_failures": 0,
            "last_write_at": _AT,
        }


class _FailedCaptureHealth:
    def capture_health_snapshot(self) -> dict[str, object]:
        return {
            "writer_alive": False,
            "queue_depth": 0,
            "queue_capacity": 16,
            "queue_high_water": 16,
            "written_rows": 10,
            "written_shards": 1,
            "dropped_rows": 0,
            "storage_failures": 1,
            "last_write_at": _AT,
        }


class _SinkFailureEmitterHealth:
    def health(self) -> EmitterHealth:
        return EmitterHealth(
            queue_depth=0,
            queue_capacity=16,
            queue_high_water=1,
            accepted=10,
            dropped=0,
            sink_failures=1,
            worker_alive=True,
        )


def test_combined_observation_health_marks_capture_loss_degraded() -> None:
    emitter = BoundedObservationEmitter(
        CallbackObservationSink(lambda observation: None),
        queue_max=16,
    )
    try:
        snapshot = ObservationHealthProvider(emitter, _DegradedCaptureHealth()).health()
        assert snapshot.state is ObservationHealthState.DEGRADED
        assert "CAPTURE_DROPPED_ROWS" in snapshot.reasons
    finally:
        emitter.close()


def test_combined_observation_health_marks_sink_failure_degraded() -> None:
    snapshot = ObservationHealthProvider(
        _SinkFailureEmitterHealth(),
        _DegradedCaptureHealth(),
    ).health()
    assert snapshot.state is ObservationHealthState.DEGRADED
    assert "EMITTER_SINK_FAILURES" in snapshot.reasons


def test_combined_observation_health_marks_storage_failure_blocked() -> None:
    emitter = BoundedObservationEmitter(
        CallbackObservationSink(lambda observation: None),
        queue_max=16,
    )
    try:
        snapshot = ObservationHealthProvider(emitter, _FailedCaptureHealth()).health()
        assert snapshot.state is ObservationHealthState.BLOCKED
        assert "CAPTURE_STORAGE_FAILURE" in snapshot.reasons
        assert "CAPTURE_WRITER_NOT_ALIVE" in snapshot.reasons
    finally:
        emitter.close()


def _status_snapshot(
    state: ObservationHealthState,
    *,
    dropped: int = 0,
    sink_failures: int = 0,
    capture_dropped: int = 0,
    storage_failures: int = 0,
    writer_alive: bool = True,
) -> ObservationHealthSnapshot:
    reasons: tuple[str, ...] = ()
    if state is ObservationHealthState.DEGRADED:
        reasons = ("EMITTER_DROPPED_OBSERVATIONS",)
    elif state is ObservationHealthState.BLOCKED:
        reasons = ("CAPTURE_STORAGE_FAILURE",)
    return ObservationHealthSnapshot(
        state=state,
        reasons=reasons,
        emitter=EmitterHealth(
            queue_depth=0,
            queue_capacity=16,
            queue_high_water=4,
            accepted=10,
            dropped=dropped,
            sink_failures=sink_failures,
            worker_alive=True,
        ),
        capture=CaptureWriterHealth(
            writer_alive=writer_alive,
            queue_depth=0,
            queue_capacity=16,
            queue_high_water=4,
            written_rows=10,
            written_shards=1,
            dropped_rows=capture_dropped,
            storage_failures=storage_failures,
            last_write_at=_AT,
        ),
    )


def test_cross_process_health_status_reader_detects_health_and_failure_states(
    tmp_path: Path,
) -> None:
    path = tmp_path / "runtime" / "observe_health.json"
    clock = iter((0.0, 2.0, 4.0)).__next__
    wall_times = iter(
        (
            _AT,
            _AT + timedelta(seconds=2),
            _AT + timedelta(seconds=4),
        )
    ).__next__
    publisher = ObservationHealthStatusPublisher(
        path,
        process_instance_id="proc-health",
        owner="maker",
        min_interval_seconds=1.0,
        monotonic_clock=clock,
        wall_clock=wall_times,
    )

    assert publisher.publish(_status_snapshot(ObservationHealthState.HEALTHY))
    healthy = read_observation_health_status(
        path,
        expected_owner="maker",
        expected_process_instance_id="proc-health",
        max_age_seconds=5,
        now=_AT + timedelta(seconds=1),
    )
    assert healthy.state is ObservationStatusState.HEALTHY
    assert healthy.healthy is True

    assert publisher.publish(
        _status_snapshot(
            ObservationHealthState.DEGRADED,
            dropped=1,
        )
    )
    degraded = read_observation_health_status(
        path,
        expected_owner="maker",
        max_age_seconds=5,
        now=_AT + timedelta(seconds=3),
    )
    assert degraded.state is ObservationStatusState.DEGRADED
    assert degraded.healthy is False

    assert publisher.publish(
        _status_snapshot(
            ObservationHealthState.BLOCKED,
            storage_failures=1,
            writer_alive=False,
        )
    )
    blocked = read_observation_health_status(
        path,
        expected_owner="maker",
        max_age_seconds=5,
        now=_AT + timedelta(seconds=5),
    )
    assert blocked.state is ObservationStatusState.BLOCKED
    assert blocked.healthy is False

    stale = read_observation_health_status(
        path,
        expected_owner="maker",
        max_age_seconds=1,
        now=_AT + timedelta(seconds=10),
    )
    assert stale.state is ObservationStatusState.STALE
    assert stale.healthy is False

    wrong_owner = read_observation_health_status(
        path,
        expected_owner="capture",
        max_age_seconds=60,
        now=_AT + timedelta(seconds=5),
    )
    assert wrong_owner.state is ObservationStatusState.OWNER_MISMATCH
    assert wrong_owner.healthy is False

    missing = read_observation_health_status(
        tmp_path / "runtime" / "missing.json",
        expected_owner="maker",
        max_age_seconds=5,
        now=_AT,
    )
    assert missing.state is ObservationStatusState.MISSING
    assert missing.healthy is False


class _ContextRest:
    async def list_tournaments_with_raw(
        self,
        *,
        status: TournamentListStatus = "any",
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[TournamentPageDto, object]:
        del status, limit, offset
        raw: dict[str, object] = {
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
        return TournamentPageDto.model_validate(raw), raw

    async def get_account_with_raw(self) -> tuple[AccountDto, object]:
        raw: dict[str, object] = {
            "id": "me",
            "username": "daniel",
            "email": None,
            "createdAt": "2026-09-01T00:00:00Z",
            "avatarUrl": None,
            "bio": None,
            "balance": "975",
        }
        return AccountDto.model_validate(raw), raw

    async def get_tournament_leaderboard_with_raw(
        self,
        tournament_slug: str,
        *,
        period: str = "all",
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[TournamentLeaderboardDto, object]:
        assert tournament_slug == "cup"
        del period, limit, offset
        raw: dict[str, object] = {
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
        return TournamentLeaderboardDto.model_validate(raw), raw


def test_official_context_exposes_rank_and_marks_super_signal_unavailable() -> None:
    provider = SigOfficialCompetitionContextProvider(
        _ContextRest(),
        tournament_id="cup-id",
    )
    snapshot = asyncio.run(provider.snapshot())
    assert snapshot.field("participant_rank").value == 3
    assert isinstance(snapshot.raw_tournament, dict)
    assert isinstance(snapshot.raw_account, dict)
    assert isinstance(snapshot.raw_leaderboard, dict)
    assert snapshot.field("leaderboard").classification is FieldClassification.NORMALIZED
    super_signal = snapshot.field("super_signal")
    assert super_signal.classification is FieldClassification.UNAVAILABLE
    assert super_signal.unavailable_reason == "ADMIN_ONLY_PARTICIPANT_KEY_UNSUPPORTED"
    summary = summarize_competition_context(snapshot)
    fields = summary["fields"]
    assert isinstance(fields, dict)
    assert fields["participant_rank"]["value"] == 3
    assert fields["super_signal"]["classification"] == "unavailable"


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
        _ContextRest(),
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
    context = (
        ds.dataset([str(path) for path in context_files], format="parquet")
        .to_table()
        .to_pylist()
    )
    assert venue[0]["kind"] == "REALTIME_REVISION_GAP"
    assert context[0]["tournament_id"] == "cup-id"
    assert "super_signal" in str(context[0]["fields_json"])
