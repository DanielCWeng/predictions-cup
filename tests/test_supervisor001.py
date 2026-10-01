from __future__ import annotations

import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest

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
from predictions_cup.supervisor.remediation import RemediationConfig, RemediationExecutor
from predictions_cup.supervisor.rules import SupervisorPolicy, evaluate, severity_and_gate
from predictions_cup.supervisor.runtime import ResourceGrowthTracker
from predictions_cup.supervisor.sources import SourceCollection

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


def test_sig_stale_reconciliation_is_critical() -> None:
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
        "last_rest_reconciliation_age_seconds": 61.0,
    }
    findings = evaluate(SourceCollection(collection.statuses, sections), SupervisorPolicy())
    assert any(item.code == "FEED_SIG_RECONCILIATION_STALE" for item in findings)


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
