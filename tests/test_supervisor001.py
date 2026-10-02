from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from predictions_cup.config import AppSettings
from predictions_cup.supervisor.bundles import BundleWriter
from predictions_cup.supervisor.contracts import (
    ActionCode,
    Finding,
    HostRole,
    LaunchGate,
    RemediationLevel,
    Severity,
    SourceStatus,
    SupervisorSnapshot,
    deterministic_id,
)
from predictions_cup.supervisor.persistence import SupervisorStore
from predictions_cup.supervisor.remediation import (
    ActionLimiter,
    RemediationConfig,
    RemediationExecutor,
)
from predictions_cup.supervisor.rules import SupervisorPolicy, evaluate, severity_and_gate
from predictions_cup.supervisor.runtime import ResourceGrowthTracker, ServiceRestartTracker
from predictions_cup.supervisor.sources import SourceCollection, SupervisorSources

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)


def _status(
    source_id: str,
    *,
    required: bool = True,
    available: bool = True,
    valid: bool = True,
    fresh: bool = True,
) -> SourceStatus:
    return SourceStatus(
        source_id=source_id,
        required=required,
        available=available,
        valid=valid,
        fresh=fresh,
        observed_at=NOW,
        read_at=NOW,
        age_seconds=0.0,
    )


def _healthy_collection() -> SourceCollection:
    statuses = (
        _status("sig_capture"),
        _status("polymarket_capture"),
        _status("system"),
        _status("clock"),
        _status("observe", required=False),
        _status("risk", required=False),
        _status("execution", required=False),
        _status("shadow", required=False),
        _status("live_learn", required=False),
    )
    sections: dict[str, object] = {
        "system": {
            "services": {
                "svc": {
                    "active_state": "active",
                    "memory_current_bytes": 100 * 1024**2,
                    "restart_count": 0,
                    "restart_count_last_hour": 0,
                    "restart_tracking_seconds": 3600.0,
                }
            },
            "disk": {"used_fraction": 0.4, "free_bytes": 20 * 1024**3},
        },
        "clock": {"ntp_synchronized": True, "probe_error": None},
        "sig_capture": {
            "storage_failures": 0,
            "dropped_rows": 0,
            "writer_alive": True,
            "queue_depth": 0,
            "queue_capacity": 100,
            "realtime_delivery_age_seconds": 30.0,
            "realtime_delivery_table_available": True,
            "price_observation_age_seconds": 30.0,
            "price_observation_table_available": True,
        },
        "polymarket_capture": {"storage_failures": 0},
        "observe": {"state": "HEALTHY"},
        "risk": {},
        "execution": {"unresolved": [], "wall_clock_age_available": False},
        "shadow": {},
        "live_learn": {},
    }
    return SourceCollection(statuses, sections)


def _snapshot(tmp_path: Path) -> SupervisorSnapshot:
    del tmp_path
    finding = Finding("X", Severity.WARN, "x")
    return SupervisorSnapshot(
        snapshot_id="supervisor-abc",
        host_id="host",
        host_role=HostRole.WEST_EXECUTION,
        git_head="deadbeef",
        observed_at=NOW,
        severity=Severity.WARN,
        launch_gate=LaunchGate.PASS,
        findings=(finding,),
        sources=(_status("system"),),
        sections={"system": {"disk": {"free_bytes": 1}}},
    )


def test_healthy_collection_has_no_findings() -> None:
    assert evaluate(_healthy_collection(), SupervisorPolicy()) == ()
    assert severity_and_gate(()) == (Severity.OK, LaunchGate.PASS)


def test_required_missing_source_fails_closed() -> None:
    collection = _healthy_collection()
    statuses = tuple(
        _status("sig_capture", available=False, valid=False, fresh=False)
        if item.source_id == "sig_capture"
        else item
        for item in collection.statuses
    )
    findings = evaluate(SourceCollection(statuses, collection.sections), SupervisorPolicy())
    assert any(item.code == "SOURCE_SIG_CAPTURE_MISSING" for item in findings)
    assert severity_and_gate(findings) == (Severity.CRITICAL, LaunchGate.HOLD)


def test_disk_emergency_is_critical() -> None:
    collection = _healthy_collection()
    sections = dict(collection.sections)
    sections["system"] = {
        "services": {"svc": {"active_state": "active", "memory_current_bytes": 1}},
        "disk": {"used_fraction": 0.95, "free_bytes": 1024},
    }
    findings = evaluate(SourceCollection(collection.statuses, sections), SupervisorPolicy())
    assert any(item.code == "DISK_PRESSURE_EMERGENCY" for item in findings)


def test_risk_halt_and_untrusted_state_are_critical() -> None:
    collection = _healthy_collection()
    sections = dict(collection.sections)
    sections["risk"] = {
        "session_id": "cup",
        "global_halt": {"active": True, "reason": "drawdown"},
        "strategy_halts": [],
        "account_trusted": False,
        "marks_trusted": True,
        "reconciliation_complete": True,
        "exposure": {
            "trusted": True,
            "gross_exposure": 1.0,
            "by_market": [],
            "by_tournament": [],
        },
    }
    findings = evaluate(SourceCollection(collection.statuses, sections), SupervisorPolicy())
    codes = {item.code for item in findings}
    assert "RISK_GLOBAL_HALT" in codes
    assert "RISK_STATE_UNTRUSTED" in codes


def test_execution_uncertain_reports_age_unavailable() -> None:
    collection = _healthy_collection()
    sections = dict(collection.sections)
    sections["execution"] = {
        "unresolved": [{"logical_operation_id": "op", "lifecycle_state": "UNCERTAIN"}],
        "wall_clock_age_available": False,
    }
    findings = evaluate(SourceCollection(collection.statuses, sections), SupervisorPolicy())
    codes = {item.code for item in findings}
    assert "EXECUTION_UNCERTAIN" in codes
    assert "EXECUTION_AGE_UNAVAILABLE" in codes


def test_thin_economics_does_not_alarm() -> None:
    collection = _healthy_collection()
    sections = dict(collection.sections)
    sections["live_learn"] = {
        "candidates": [
            {
                "candidate_id": "make",
                "scored_outcomes": 5,
                "metrics": {"adverse_selection": 99.0},
            }
        ]
    }
    policy = SupervisorPolicy(max_adverse_selection=0.01, min_economic_samples=20)
    findings = evaluate(SourceCollection(collection.statuses, sections), policy)
    assert not any(item.code == "MAKER_ADVERSE_SELECTION" for item in findings)


def test_supported_economics_can_warn() -> None:
    collection = _healthy_collection()
    sections = dict(collection.sections)
    sections["live_learn"] = {
        "candidates": [
            {
                "candidate_id": "make",
                "scored_outcomes": 30,
                "metrics": {"adverse_selection": 0.02},
            }
        ]
    }
    policy = SupervisorPolicy(max_adverse_selection=0.01, min_economic_samples=20)
    findings = evaluate(SourceCollection(collection.statuses, sections), policy)
    assert any(item.code == "MAKER_ADVERSE_SELECTION" for item in findings)


def test_deterministic_id_is_stable() -> None:
    payload = {"b": 2, "a": [1, 2]}
    assert deterministic_id("x", payload) == deterministic_id("x", payload)


def test_store_latest_and_append_only_history(tmp_path: Path) -> None:
    store = SupervisorStore(tmp_path)
    snapshot = _snapshot(tmp_path)
    store.publish_latest(snapshot)
    store.append_snapshot(snapshot)
    store.append_event({"event": 1})
    assert json.loads(store.latest_path.read_text())["snapshot_id"] == snapshot.snapshot_id
    assert store.events_path.read_text().count("\n") == 1
    history = tmp_path / "snapshots" / "2026" / "10" / "01.jsonl"
    assert history.read_text().count("\n") == 1


def test_bundle_contains_manifest_and_hashes(tmp_path: Path) -> None:
    path = BundleWriter(tmp_path).emit(
        bundle_type="events",
        snapshot=_snapshot(tmp_path),
        trigger_codes=("X",),
    )
    manifest = json.loads((path / "manifest.json").read_text())
    assert manifest["analysis_requested"] is True
    assert "supervisor.json" in manifest["files"]
    assert (path / "system.json").exists()


def test_remediation_level_denies_service_restart(tmp_path: Path) -> None:
    config = RemediationConfig(
        max_level=RemediationLevel.HOUSEKEEPING,
        host_role=HostRole.WEST_EXECUTION,
        supervisor_root=tmp_path,
        hot_capture_roots=(),
        hot_capture_retention_hours=24,
        bundle_retention_hours=48,
        safe_cache_paths=(),
        safe_restart_services=("safe.service",),
    )
    result = RemediationExecutor(config).execute(
        ActionCode.RESTART_SAFE_SERVICE,
        target="safe.service",
        requested_by="test",
    )
    assert result["result"] == "DENIED_LEVEL"


def test_hot_parquet_prune_is_scoped_to_old_parquet(tmp_path: Path) -> None:
    capture = tmp_path / "capture"
    capture.mkdir()
    old = capture / "old.parquet"
    keep = capture / "keep.txt"
    old.write_bytes(b"x" * 100)
    keep.write_text("keep")
    stale = time.time() - 48 * 3600
    os.utime(old, (stale, stale))
    config = RemediationConfig(
        max_level=RemediationLevel.HOUSEKEEPING,
        host_role=HostRole.WEST_EXECUTION,
        supervisor_root=tmp_path / "supervisor",
        hot_capture_roots=(capture,),
        hot_capture_retention_hours=24,
        bundle_retention_hours=48,
        safe_cache_paths=(),
        safe_restart_services=(),
    )
    result = RemediationExecutor(config).execute(
        ActionCode.PRUNE_HOT_PARQUET,
        target=None,
        requested_by="test",
    )
    assert result["result"] == "SUCCESS"
    assert not old.exists()
    assert keep.exists()


def test_polymarket_disconnected_is_critical() -> None:
    collection = _healthy_collection()
    sections = dict(collection.sections)
    sections["polymarket_capture"] = {
        "storage_failures": 0,
        "websocket_connected": False,
        "last_message_age_seconds": 1.0,
    }
    findings = evaluate(SourceCollection(collection.statuses, sections), SupervisorPolicy())
    assert any(item.code == "FEED_POLYMARKET_DISCONNECTED" for item in findings)


def test_sig_activity_fallback_is_visible_warning() -> None:
    collection = _healthy_collection()
    sections = dict(collection.sections)
    sections["sig_capture"] = {
        "storage_failures": 0,
        "dropped_rows": 0,
        "writer_alive": True,
        "health_surface": "activity_fallback",
        "health_surface_reason": "OperationalError:no such table: capture_health",
    }
    findings = evaluate(SourceCollection(collection.statuses, sections), SupervisorPolicy())
    assert any(item.code == "SIG_STRUCTURED_HEALTH_UNAVAILABLE" for item in findings)


def test_hourly_bundle_shape_matches_event_bundle(tmp_path: Path) -> None:
    path = BundleWriter(tmp_path).emit(
        bundle_type="hourly",
        snapshot=_snapshot(tmp_path),
        trigger_codes=("X",),
    )
    manifest = json.loads((path / "manifest.json").read_text())
    assert manifest["bundle_type"] == "hourly"
    assert manifest["trigger_codes"] == ["X"]


def test_fast_disk_growth_is_critical() -> None:
    collection = _healthy_collection()
    sections = dict(collection.sections)
    sections["system"] = {
        "services": {"svc": {"active_state": "active", "memory_current_bytes": 1}},
        "disk": {
            "used_fraction": 0.4,
            "free_bytes": 20 * 1024**3,
            "growth_bytes_per_min": 600 * 1024**2,
        },
    }
    findings = evaluate(SourceCollection(collection.statuses, sections), SupervisorPolicy())
    assert any(item.code == "DISK_GROWTH_CRITICAL" for item in findings)


def test_resource_growth_resets_memory_history_on_pid_change(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = ResourceGrowthTracker(minimum_elapsed_seconds=30.0)
    times = iter((0.0, 31.0, 62.0))
    monkeypatch.setattr("predictions_cup.supervisor.runtime.time.monotonic", lambda: next(times))
    first = {"services": {"svc": {"main_pid": "1", "memory_current_bytes": 100}}}
    second = {"services": {"svc": {"main_pid": "1", "memory_current_bytes": 200}}}
    restarted = {"services": {"svc": {"main_pid": "2", "memory_current_bytes": 500}}}
    growth1, _ = tracker.update(first)
    growth2, _ = tracker.update(second)
    growth3, _ = tracker.update(restarted)
    assert growth1 == {}
    assert growth2["svc"] > 0
    assert growth3 == {}


def test_resource_growth_ignores_normal_startup_ramp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = ResourceGrowthTracker()
    times = iter((0.0, 120.0))
    monkeypatch.setattr("predictions_cup.supervisor.runtime.time.monotonic", lambda: next(times))
    first = {"services": {"svc": {"main_pid": "1", "memory_current_bytes": 100}}}
    ramped = {"services": {"svc": {"main_pid": "1", "memory_current_bytes": 800 * 1024**2}}}
    growth1, _ = tracker.update(first)
    growth2, _ = tracker.update(ramped)
    assert growth1 == {}
    assert growth2 == {}


def test_level_zero_plan_emits_no_automatic_actions(tmp_path: Path) -> None:
    config = RemediationConfig(
        max_level=RemediationLevel.OBSERVE,
        host_role=HostRole.WEST_EXECUTION,
        supervisor_root=tmp_path,
        hot_capture_roots=(),
        hot_capture_retention_hours=24,
        bundle_retention_hours=48,
        safe_cache_paths=(),
        safe_restart_services=("safe.service",),
    )
    finding = Finding(
        "SERVICE_MEMORY_GROWTH",
        Severity.CRITICAL,
        "growing",
        {"service": "safe.service"},
    )
    snapshot = SupervisorSnapshot(
        snapshot_id="s",
        host_id="h",
        host_role=HostRole.WEST_EXECUTION,
        git_head="g",
        observed_at=NOW,
        severity=Severity.CRITICAL,
        launch_gate=LaunchGate.HOLD,
        findings=(finding,),
        sources=(),
        sections={},
    )
    assert RemediationExecutor(config).plan(snapshot) == ()


def test_sig_nested_research_storage_failure_is_critical() -> None:
    collection = _healthy_collection()
    sections = dict(collection.sections)
    sections["sig_capture"] = {
        "research_storage": {
            "storage_failures": 1,
            "dropped_rows": 0,
            "writer_alive": True,
            "queue_depth": 0,
            "queue_capacity": 100,
        }
    }
    findings = evaluate(SourceCollection(collection.statuses, sections), SupervisorPolicy())
    assert any(item.code == "CAPTURE_SIG_STORAGE_FAILURE" for item in findings)


def test_sig_disconnected_is_critical() -> None:
    collection = _healthy_collection()
    sections = dict(collection.sections)
    sections["sig_capture"] = {
        "research_storage": {
            "storage_failures": 0,
            "dropped_rows": 0,
            "writer_alive": True,
            "queue_depth": 0,
            "queue_capacity": 100,
        },
        "connected": False,
        "last_rest_reconciliation_age_seconds": 1.0,
    }
    findings = evaluate(SourceCollection(collection.statuses, sections), SupervisorPolicy())
    assert any(item.code == "FEED_SIG_DISCONNECTED" for item in findings)


def test_sig_stalled_bulk_price_progress_is_critical() -> None:
    collection = _healthy_collection()
    sections = dict(collection.sections)
    sections["sig_capture"] = {
        "research_storage": {
            "storage_failures": 0,
            "dropped_rows": 0,
            "writer_alive": True,
            "queue_depth": 0,
            "queue_capacity": 100,
        },
        "connected": True,
        "bulk_price_refresh_progress_age_seconds": 61.0,
        "bulk_price_refresh_progress_max_age_seconds": 60.0,
    }
    findings = evaluate(SourceCollection(collection.statuses, sections), SupervisorPolicy())
    assert any(item.code == "FEED_SIG_REST_PROGRESS_STALE" for item in findings)


def test_maker_stale_outcome_and_kill_latch_are_critical() -> None:
    collection = _healthy_collection()
    sections = dict(collection.sections)
    sections["maker_liveness"] = {
        "enabled": True,
        "service": "predictions-cup-maker-live.service",
        "main_pid": "123",
        "query_ok": True,
        "last_outcome_at": None,
        "last_outcome_age_seconds": None,
        "kill_latched": True,
    }
    findings = evaluate(SourceCollection(collection.statuses, sections), SupervisorPolicy())
    by_code = {item.code: item for item in findings}
    assert by_code["MAKER_OUTCOME_STALE"].severity is Severity.CRITICAL
    assert by_code["MAKER_KILL_LATCHED"].severity is Severity.CRITICAL


def test_maker_outcome_probe_failure_is_unknown() -> None:
    collection = _healthy_collection()
    sections = dict(collection.sections)
    sections["maker_liveness"] = {
        "enabled": True,
        "service": "predictions-cup-maker-live.service",
        "main_pid": "123",
        "query_ok": False,
        "query_error": "PermissionError",
    }
    findings = evaluate(SourceCollection(collection.statuses, sections), SupervisorPolicy())
    maker_finding = next(item for item in findings if item.code == "MAKER_OUTCOME_UNKNOWN")
    assert maker_finding.severity is Severity.UNKNOWN


@pytest.mark.parametrize(
    ("realtime_age", "price_age", "expected_code"),
    (
        (600.0, None, None),
        (None, 120.0, None),
        (901.0, 181.0, "SIG_CAPTURE_ACTIVITY_STALE"),
        (None, None, "SIG_CAPTURE_ACTIVITY_UNKNOWN"),
    ),
)
def test_sig_capture_liveness_accepts_either_recent_signal(
    realtime_age: float | None,
    price_age: float | None,
    expected_code: str | None,
) -> None:
    collection = _healthy_collection()
    sections = dict(collection.sections)
    raw_capture = sections["sig_capture"]
    assert isinstance(raw_capture, dict)
    capture = dict(raw_capture)
    capture["realtime_delivery_age_seconds"] = realtime_age
    capture["price_observation_age_seconds"] = price_age
    capture["realtime_delivery_table_available"] = realtime_age is not None
    capture["price_observation_table_available"] = price_age is not None
    sections["sig_capture"] = capture
    findings = evaluate(SourceCollection(collection.statuses, sections), SupervisorPolicy())
    liveness_findings = {
        item.code
        for item in findings
        if item.code.startswith("SIG_CAPTURE_ACTIVITY_")
    }
    assert liveness_findings == (set() if expected_code is None else {expected_code})


def test_restart_budget_excess_is_critical() -> None:
    collection = _healthy_collection()
    sections = dict(collection.sections)
    raw_system = sections["system"]
    assert isinstance(raw_system, dict)
    system = dict(raw_system)
    system["services"] = {
        "predictions-cup-sig-capture.service": {
            "active_state": "active",
            "restart_count": 4,
            "restart_count_last_hour": 3,
            "memory_current_bytes": 100,
        }
    }
    sections["system"] = system
    findings = evaluate(SourceCollection(collection.statuses, sections), SupervisorPolicy())
    restart_loop = next(item for item in findings if item.code == "SERVICE_RESTART_LOOP")
    assert restart_loop.severity is Severity.CRITICAL
    assert restart_loop.evidence["restarts_last_hour"] == 3


def test_incomplete_restart_history_is_unknown() -> None:
    collection = _healthy_collection()
    sections = dict(collection.sections)
    raw_system = sections["system"]
    assert isinstance(raw_system, dict)
    system = dict(raw_system)
    system["services"] = {
        "predictions-cup-sig-capture.service": {
            "active_state": "active",
            "restart_count": 1,
            "restart_count_last_hour": 0,
            "restart_tracking_seconds": 15.0,
            "memory_current_bytes": 100,
        }
    }
    sections["system"] = system
    findings = evaluate(SourceCollection(collection.statuses, sections), SupervisorPolicy())
    history = next(
        item for item in findings if item.code == "SERVICE_RESTART_HISTORY_UNKNOWN"
    )
    assert history.severity is Severity.UNKNOWN


def test_restart_tracker_counts_pid_and_systemd_restarts_within_hour() -> None:
    now = [0.0]
    tracker = ServiceRestartTracker(clock=lambda: now[0])
    service = "predictions-cup-sig-capture.service"
    first = {"services": {service: {"restart_count": 0, "main_pid": "10"}}}
    second = {"services": {service: {"restart_count": 1, "main_pid": "11"}}}
    third = {"services": {service: {"restart_count": 3, "main_pid": "12"}}}
    assert tracker.update(first)[service]["restart_count_last_hour"] == 0
    now[0] += 5.0
    assert tracker.update(second)[service]["restart_count_last_hour"] == 1
    now[0] += 5.0
    assert tracker.update(third)[service]["restart_count_last_hour"] == 3
    now[0] += 3601.0
    assert tracker.update(third)[service]["restart_count_last_hour"] == 0


def test_restart_tracker_keeps_window_when_systemd_counter_resets() -> None:
    now = [0.0]
    tracker = ServiceRestartTracker(clock=lambda: now[0])
    service = "predictions-cup-sig-capture.service"
    first = {"services": {service: {"restart_count": 4, "main_pid": "10"}}}
    restarted = {"services": {service: {"restart_count": 5, "main_pid": "11"}}}
    counter_reset = {"services": {service: {"restart_count": 0, "main_pid": "12"}}}
    assert tracker.update(first)[service]["restart_count_last_hour"] == 0
    now[0] += 5.0
    assert tracker.update(restarted)[service]["restart_count_last_hour"] == 1
    now[0] += 5.0
    assert tracker.update(counter_reset)[service]["restart_count_last_hour"] == 2


def test_restart_action_limiter_allows_at_most_two_per_service_per_hour(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    times = iter((0.0, 301.0, 602.0, 3601.0))
    monkeypatch.setattr(
        "predictions_cup.supervisor.remediation.time.monotonic",
        lambda: next(times),
    )
    limiter = ActionLimiter()
    service = "predictions-cup-sig-capture.service"
    decisions = tuple(
        limiter.allow(
            f"restart:{service}",
            cooldown_seconds=300.0,
            restart_service=service,
            max_restarts_per_hour=2,
        )
        for _ in range(4)
    )
    assert decisions == (True, True, False, True)


def test_maker_journal_probe_is_scoped_to_current_pid(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    observed = datetime.now(UTC)
    kill_event = {
        "MESSAGE": "MAKE outcome exchange=960 force_cancel kill=True marks_trusted=True",
        "__REALTIME_TIMESTAMP": str(
            int((observed - timedelta(seconds=1)).timestamp() * 1_000_000)
        ),
    }
    event = {
        "MESSAGE": "MAKE outcome exchange=961 no_place gate=HOLD",
        "__REALTIME_TIMESTAMP": str(int(observed.timestamp() * 1_000_000)),
    }
    calls: list[list[str]] = []

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        calls.append(args)
        return subprocess.CompletedProcess(
            args,
            0,
            f"{json.dumps(kill_event)}\n{json.dumps(event)}\n",
            "",
        )

    monkeypatch.setattr("predictions_cup.supervisor.sources.subprocess.run", fake_run)
    sources = SupervisorSources(
        AppSettings(maker_enabled=False),
        repo_root=tmp_path,
        host_role=HostRole.WEST_EXECUTION,
        expected_services=(),
    )
    assert "predictions-cup-maker-live.service" not in sources.expected_services
    sources._system_cache = {
        "services": {
            "predictions-cup-maker-live.service": {
                "active_state": "active",
                "main_pid": "123",
            }
        }
    }
    status, section = sources._read_maker_liveness()
    assert status.valid is True
    assert section["kill_latched"] is True
    assert section["enabled"] is True
    assert section["main_pid"] == "123"
    journal_calls = [args for args in calls if args and args[0] == "journalctl"]
    assert journal_calls[0][-1] == "_PID=123"


def test_enabled_maker_is_added_to_expected_services(tmp_path: Path) -> None:
    sources = SupervisorSources(
        AppSettings(maker_enabled=True),
        repo_root=tmp_path,
        host_role=HostRole.WEST_EXECUTION,
        expected_services=(),
    )
    assert "predictions-cup-maker-live.service" in sources.expected_services


def test_sig_activity_reads_last_delivery_and_price_observation(tmp_path: Path) -> None:
    database = tmp_path / "sig.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE realtime_deliveries (id INTEGER PRIMARY KEY, observed_at TEXT)"
        )
        connection.execute(
            "CREATE TABLE price_observations "
            "(id INTEGER PRIMARY KEY, rest_observed_at TEXT)"
        )
        connection.execute(
            "INSERT INTO realtime_deliveries(observed_at) VALUES (?)",
            ((NOW - timedelta(seconds=15)).isoformat(),),
        )
        connection.execute(
            "INSERT INTO price_observations(rest_observed_at) VALUES (?)",
            ((NOW - timedelta(seconds=45)).isoformat(),),
        )
    sources = SupervisorSources(
        AppSettings(),
        repo_root=tmp_path,
        host_role=HostRole.WEST_EXECUTION,
        expected_services=(),
    )
    activity = sources._read_sig_activity_liveness(database, NOW)
    assert activity["realtime_delivery_age_seconds"] == 15.0
    assert activity["price_observation_age_seconds"] == 45.0
    assert activity["realtime_delivery_table_available"] is True
    assert activity["price_observation_table_available"] is True


def test_stale_sig_activity_restarts_only_allowlisted_non_loop_service(
    tmp_path: Path,
) -> None:
    service = "predictions-cup-sig-capture.service"
    config = RemediationConfig(
        max_level=RemediationLevel.SERVICE_RECOVERY,
        host_role=HostRole.WEST_EXECUTION,
        supervisor_root=tmp_path,
        hot_capture_roots=(),
        hot_capture_retention_hours=24,
        bundle_retention_hours=48,
        safe_cache_paths=(),
        safe_restart_services=(service,),
    )
    executor = RemediationExecutor(config)
    stale = Finding(
        "SIG_CAPTURE_ACTIVITY_STALE",
        Severity.CRITICAL,
        "capture stalled",
    )
    snapshot = SupervisorSnapshot(
        snapshot_id="stale-capture",
        host_id="host",
        host_role=HostRole.WEST_EXECUTION,
        git_head="deadbeef",
        observed_at=NOW,
        severity=Severity.CRITICAL,
        launch_gate=LaunchGate.HOLD,
        findings=(stale,),
        sources=(),
        sections={"system": {"services": {service: {"main_pid": "5"}}}},
    )
    assert executor.plan(snapshot) == ((ActionCode.RESTART_SAFE_SERVICE, service),)

    loop = Finding(
        "SERVICE_RESTART_LOOP",
        Severity.CRITICAL,
        "restart budget exceeded",
        {"service": service},
    )
    loop_snapshot = SupervisorSnapshot(
        snapshot_id="looping-capture",
        host_id="host",
        host_role=HostRole.WEST_EXECUTION,
        git_head="deadbeef",
        observed_at=NOW,
        severity=Severity.CRITICAL,
        launch_gate=LaunchGate.HOLD,
        findings=(stale, loop),
        sources=(),
        sections={"system": {"services": {service: {"main_pid": "5"}}}},
    )
    assert executor.plan(loop_snapshot) == ()


def test_maker_kill_restart_requires_explicit_allowlist(tmp_path: Path) -> None:
    service = "predictions-cup-maker-live.service"
    finding = Finding("MAKER_KILL_LATCHED", Severity.CRITICAL, "kill=True")
    snapshot = SupervisorSnapshot(
        snapshot_id="maker-kill",
        host_id="host",
        host_role=HostRole.WEST_EXECUTION,
        git_head="deadbeef",
        observed_at=NOW,
        severity=Severity.CRITICAL,
        launch_gate=LaunchGate.HOLD,
        findings=(finding,),
        sources=(),
        sections={"system": {"services": {service: {"main_pid": "5"}}}},
    )

    def executor(allowed: tuple[str, ...]) -> RemediationExecutor:
        return RemediationExecutor(
            RemediationConfig(
                max_level=RemediationLevel.SERVICE_RECOVERY,
                host_role=HostRole.WEST_EXECUTION,
                supervisor_root=tmp_path,
                hot_capture_roots=(),
                hot_capture_retention_hours=24,
                bundle_retention_hours=48,
                safe_cache_paths=(),
                safe_restart_services=allowed,
                restart_grace_seconds=3600,
                startup_grace_seconds=3600,
            )
        )

    assert executor(("predictions-cup-sig-capture.service",)).plan(snapshot) == ()
    assert executor((service,)).plan(snapshot) == (
        (ActionCode.RESTART_SAFE_SERVICE, service),
    )


def test_remediation_plans_safe_capture_restart_for_stale_feed(tmp_path: Path) -> None:
    config = RemediationConfig(
        max_level=RemediationLevel.SERVICE_RECOVERY,
        host_role=HostRole.WEST_EXECUTION,
        supervisor_root=tmp_path,
        hot_capture_roots=(),
        hot_capture_retention_hours=24,
        bundle_retention_hours=48,
        safe_cache_paths=(),
        safe_restart_services=("predictions-cup-polymarket-capture.service",),
    )
    finding = Finding("FEED_POLYMARKET_STALE", Severity.CRITICAL, "stale")
    snapshot = SupervisorSnapshot(
        snapshot_id="supervisor-stale",
        host_id="host",
        host_role=HostRole.WEST_EXECUTION,
        git_head="deadbeef",
        observed_at=NOW,
        severity=Severity.CRITICAL,
        launch_gate=LaunchGate.HOLD,
        findings=(finding,),
        sources=(),
        sections={},
    )
    assert RemediationExecutor(config).plan(snapshot) == (
        (
            ActionCode.RESTART_SAFE_SERVICE,
            "predictions-cup-polymarket-capture.service",
        ),
    )


def test_disk_growth_plans_housekeeping(tmp_path: Path) -> None:
    config = RemediationConfig(
        max_level=RemediationLevel.HOST_PROTECTION,
        host_role=HostRole.WEST_EXECUTION,
        supervisor_root=tmp_path / "supervisor",
        hot_capture_roots=(tmp_path / "capture",),
        hot_capture_retention_hours=24,
        bundle_retention_hours=48,
        safe_cache_paths=(),
        safe_restart_services=(),
    )
    finding = Finding("DISK_GROWTH_CRITICAL", Severity.CRITICAL, "rapid growth")
    snapshot = SupervisorSnapshot(
        snapshot_id="supervisor-disk",
        host_id="host",
        host_role=HostRole.WEST_EXECUTION,
        git_head="deadbeef",
        observed_at=NOW,
        severity=Severity.CRITICAL,
        launch_gate=LaunchGate.HOLD,
        findings=(finding,),
        sources=(),
        sections={},
    )
    planned = RemediationExecutor(config).plan(snapshot)
    assert (ActionCode.PRUNE_SUPERVISOR_BUNDLES, None) in planned
    assert (ActionCode.PRUNE_HOT_PARQUET, None) in planned


def _pm_snapshot(code: str, pid: str, severity: Severity = Severity.CRITICAL) -> SupervisorSnapshot:
    evidence = {"service": "predictions-cup-polymarket-capture.service"}
    return SupervisorSnapshot(
        snapshot_id="supervisor-grace",
        host_id="host",
        host_role=HostRole.WEST_EXECUTION,
        git_head="deadbeef",
        observed_at=NOW,
        severity=severity,
        launch_gate=LaunchGate.HOLD,
        findings=(Finding(code, severity, "x", evidence),),
        sources=(),
        sections={
            "system": {
                "services": {
                    "predictions-cup-polymarket-capture.service": {"main_pid": pid}
                }
            }
        },
    )


def test_restart_waits_for_grace_and_startup_window(tmp_path: Path) -> None:
    """2026-10-01: a 2 s PM websocket drop was restarted into an 11-minute cold
    reseed, then the reseeding process was restarted for startup memory growth."""
    service = "predictions-cup-polymarket-capture.service"
    config = RemediationConfig(
        max_level=RemediationLevel.SERVICE_RECOVERY,
        host_role=HostRole.WEST_EXECUTION,
        supervisor_root=tmp_path,
        hot_capture_roots=(),
        hot_capture_retention_hours=24,
        bundle_retention_hours=48,
        safe_cache_paths=(),
        safe_restart_services=(service,),
        restart_grace_seconds=90.0,
        startup_grace_seconds=720.0,
    )
    now = [1000.0]
    executor = RemediationExecutor(config, clock=lambda: now[0])
    restart = ((ActionCode.RESTART_SAFE_SERVICE, service),)

    # Long-running process (first seen well before) with a transient disconnect.
    assert executor.plan(_pm_snapshot("FEED_SIG_REST_PROGRESS_STALE", "1")) == ()
    now[0] += 800.0
    assert executor.plan(_pm_snapshot("FEED_POLYMARKET_DISCONNECTED", "1")) == ()
    now[0] += 30.0
    assert executor.plan(_pm_snapshot("FEED_POLYMARKET_DISCONNECTED", "1")) == ()
    now[0] += 61.0
    assert executor.plan(_pm_snapshot("FEED_POLYMARKET_DISCONNECTED", "1")) == restart

    # New PID: stale feed and memory growth are deferred during startup grace.
    now[0] += 5.0
    assert executor.plan(_pm_snapshot("FEED_POLYMARKET_STALE", "2")) == ()
    now[0] += 300.0
    assert executor.plan(_pm_snapshot("SERVICE_MEMORY_GROWTH", "2")) == ()
    assert executor.plan(_pm_snapshot("FEED_POLYMARKET_STALE", "2")) == ()
    # A dead service is still restarted immediately.
    assert executor.plan(_pm_snapshot("SERVICE_FAILURE", "2")) == restart
    # After the startup window, a stale feed that persists past the grace is acted on.
    now[0] += 500.0
    assert executor.plan(_pm_snapshot("FEED_POLYMARKET_STALE", "2")) == ()
    now[0] += 91.0
    assert executor.plan(_pm_snapshot("FEED_POLYMARKET_STALE", "2")) == restart
