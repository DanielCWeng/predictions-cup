# ruff: noqa: I001
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from predictions_cup.runtime.control_plane import (
    build_control_plane,
    build_session_manifest,
    clock_health,
    content_hash,
    persist_session_manifest,
    read_session_pointer,
    storage_health,
)


NOW = datetime(2026, 9, 30, 22, 0, tzinfo=UTC)


class _DiskUsage:
    def __init__(self, total: int, used: int, free: int) -> None:
        self.total = total
        self.used = used
        self.free = free


def test_clock_health_accepts_synchronised_host_with_small_offset() -> None:
    def runner(command: tuple[str, ...]) -> tuple[int, str]:
        if command[0] == "timedatectl":
            return 0, "yes"
        if command[0] == "chronyc":
            return 0, "Last offset     : -0.000120 seconds\nLeap status     : Normal"
        return 127, ""

    result = clock_health(
        threshold_seconds=0.001,
        runner=runner,
        wall_clock=lambda: NOW,
    )
    assert result["state"] == "HEALTHY"
    assert result["synchronized"] is True
    assert result["estimated_offset_seconds"] == pytest.approx(-0.00012)


def test_clock_health_blocks_offset_boundary_and_unknown_inspection() -> None:
    def bad_offset(command: tuple[str, ...]) -> tuple[int, str]:
        if command[0] == "timedatectl":
            return 0, "yes"
        return 0, "Last offset     : 0.250001 seconds\nLeap status     : Normal"

    blocked = clock_health(
        threshold_seconds=0.25,
        runner=bad_offset,
        wall_clock=lambda: NOW,
    )
    unknown = clock_health(
        threshold_seconds=0.25,
        runner=lambda _command: (127, ""),
        wall_clock=lambda: NOW,
    )
    assert blocked["state"] == "BLOCKED"
    assert "CLOCK_OFFSET_EXCEEDS_THRESHOLD" in blocked["reason_codes"]
    assert unknown["state"] == "UNKNOWN"
    assert "SYNC_STATE_UNAVAILABLE" in unknown["reason_codes"]


def _values(tmp_path: Path) -> dict[str, str]:
    capture = tmp_path / "capture"
    capture.mkdir()
    (capture / "part-000.parquet").write_bytes(b"x" * 1024)
    return {
        "PREDICTIONS_CUP_SIG_RESEARCH_PATH": str(capture),
        "PREDICTIONS_CUP_FULLSTACK_STATUS_DIR": str(tmp_path / "status"),
        "PREDICTIONS_CUP_FULLSTACK_WARN_FREE_DISK_GIB": "8",
        "PREDICTIONS_CUP_FULLSTACK_MIN_FREE_DISK_GIB": "3",
        "PREDICTIONS_CUP_EXECUTION_JOURNAL_PATH": str(tmp_path / "execution.sqlite3"),
        "PREDICTIONS_CUP_RISK_STATE_PATH": str(tmp_path / "risk.sqlite3"),
        "PREDICTIONS_CUP_SIG_REALTIME_STORAGE_PATH": str(tmp_path / "sig.sqlite3"),
        "PREDICTIONS_CUP_POLYMARKET_STORAGE_PATH": str(tmp_path / "pm.sqlite3"),
    }


def test_storage_health_reports_insufficient_then_observed_runway(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    values = _values(tmp_path)
    monkeypatch.setattr(
        "predictions_cup.runtime.control_plane.shutil.disk_usage",
        lambda _path: _DiskUsage(200 * 1024**3, 100 * 1024**3, 100 * 1024**3),
    )
    queue = {
        "queue_depth": 1,
        "queue_capacity": 100,
        "high_water_mark": 2,
        "dropped_rows": 0,
        "storage_failures": 0,
    }
    first = storage_health(
        tmp_path,
        values,
        queue_health=queue,
        wall_clock=lambda: NOW,
    )
    assert first["state"] == "HEALTHY"
    assert first["runway_hours"] is None
    assert first["runway_reason"] == "INSUFFICIENT_HISTORY"

    capture = Path(values["PREDICTIONS_CUP_SIG_RESEARCH_PATH"])
    (capture / "part-001.parquet").write_bytes(b"x" * 1024)
    second = storage_health(
        tmp_path,
        values,
        queue_health=queue,
        wall_clock=lambda: NOW + timedelta(hours=1),
    )
    assert second["state"] == "HEALTHY"
    assert second["runway_reason"] == "OBSERVED_GROWTH"
    assert isinstance(second["runway_hours"], float)
    assert second["runway_hours"] > 0


def test_storage_health_handles_critical_missing_and_queue_pressure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    values = _values(tmp_path)
    Path(values["PREDICTIONS_CUP_SIG_RESEARCH_PATH"]).rename(tmp_path / "gone")
    monkeypatch.setattr(
        "predictions_cup.runtime.control_plane.shutil.disk_usage",
        lambda _path: _DiskUsage(100 * 1024**3, 98 * 1024**3, 2 * 1024**3),
    )
    result = storage_health(
        tmp_path,
        values,
        queue_health={
            "queue_depth": 99,
            "queue_capacity": 100,
            "dropped_rows": 1,
            "storage_failures": 0,
        },
        wall_clock=lambda: NOW,
    )
    assert result["state"] == "BLOCKED"
    assert "FILESYSTEM_FREE_CRITICAL" in result["reason_codes"]
    assert "CAPTURE_PATH_MISSING" in result["reason_codes"]
    assert "CAPTURE_QUEUE_OR_WRITER_FAILURE" in result["reason_codes"]



def test_storage_health_warning_and_non_positive_growth(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    values = _values(tmp_path)
    monkeypatch.setattr(
        "predictions_cup.runtime.control_plane.shutil.disk_usage",
        lambda _path: _DiskUsage(100 * 1024**3, 95 * 1024**3, 5 * 1024**3),
    )
    first = storage_health(tmp_path, values, wall_clock=lambda: NOW)
    second = storage_health(
        tmp_path,
        values,
        wall_clock=lambda: NOW + timedelta(hours=1),
    )
    assert first["state"] == "DEGRADED"
    assert "FILESYSTEM_FREE_WARNING" in first["reason_codes"]
    assert second["runway_hours"] is None
    assert second["runway_reason"] == "NON_POSITIVE_GROWTH"


def test_session_manifest_is_hashed_atomic_and_secret_free(tmp_path: Path) -> None:
    values = {
        "PREDICTIONS_CUP_FULLSTACK_STATUS_DIR": str(tmp_path / "status"),
        "PREDICTIONS_CUP_FULLSTACK_SESSION_ROOT": str(tmp_path / "sessions"),
        "PREDICTIONS_CUP_SIG_READ_CREDENTIAL": "DO_NOT_LEAK",
        "PREDICTIONS_CUP_MAKER_ENABLED": "true",
        "PREDICTIONS_CUP_EXECUTION_MODE": "SHADOW",
        "PREDICTIONS_CUP_STRUCTURAL_SHOCK_REGISTRY": str(tmp_path / "shock.json"),
    }
    (tmp_path / "shock.json").write_text('{"tokens":["1"]}', encoding="utf-8")
    snapshot: dict[str, object] = {
        "git_sha": "abc",
        "config_schema_version": "v1",
        "non_secret_config_sha256": "cfg",
        "mapping": {"sha256": "mapping"},
        "enabled_candidates_observed": [
            {"candidate_id": "c1", "candidate_version": "v1"}
        ],
    }
    manifest = build_session_manifest(
        tmp_path,
        values,
        session_id="session-1",
        started_at=NOW,
        snapshot=snapshot,
        source_state={"sig": {"last_observation": NOW.isoformat()}},
    )
    rendered = json.dumps(manifest, sort_keys=True)
    assert "DO_NOT_LEAK" not in rendered
    expected_hash_input = dict(manifest)
    expected_hash = str(expected_hash_input.pop("manifest_sha256"))
    assert content_hash(expected_hash_input) == expected_hash

    pointer = persist_session_manifest(values, manifest)
    loaded = read_session_pointer(values)
    assert pointer["state"] == "HEALTHY"
    assert loaded["manifest_sha256"] == manifest["manifest_sha256"]
    assert Path(str(pointer["manifest_path"])).exists()


def test_control_plane_has_required_machine_readable_tree() -> None:
    status: dict[str, object] = {
        "schema_version": "fullstack-001-v1",
        "git_sha": "abc",
        "environment": "test",
        "runtime_profile": "REHEARSAL",
        "session": {"state": "HEALTHY"},
        "services": [],
        "sig": {
            "last_observation": NOW.isoformat(),
            "health": {
                "research_storage": {
                    "queue_depth": 1,
                    "queue_capacity": 10,
                    "high_water_mark": 2,
                    "dropped_rows": 0,
                    "storage_failures": 0,
                }
            },
        },
        "polymarket": None,
        "kalshi": None,
        "storage": {"state": "HEALTHY"},
        "clock": {"state": "HEALTHY"},
        "observe": {"state": "PASS"},
        "live_learn": {"state": "PASS"},
        "shadow": {"state": "PASS"},
        "shadow_evidence": {"candidates": []},
        "risk_halt": {"state": "PASS", "active": False},
        "execution": {"unresolved_count": 0, "uncertain_count": 0},
        "config": {"schema_version": "v1"},
        "capabilities": {},
    }
    health = {"state": "PASS", "observed_at": NOW.isoformat(), "checks": []}
    result = build_control_plane(status, health)
    assert set(result) >= {
        "identity",
        "session",
        "services",
        "feeds",
        "queues",
        "capture",
        "observe",
        "learner",
        "candidates",
        "risk",
        "execution",
        "storage",
        "clock",
        "versions",
        "overall_health",
    }
    execution = result["execution"]
    assert isinstance(execution, dict)
    assert execution["fresh_economic_admission_possible"] is True
    overall = result["overall_health"]
    assert isinstance(overall, dict)
    assert overall["state"] == "HEALTHY"
