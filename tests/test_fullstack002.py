from __future__ import annotations

import json
import os
import sqlite3
from collections.abc import Sequence
from pathlib import Path

import pytest

from predictions_cup.config import AppSettings
from predictions_cup.fullstack import control as fullstack

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SYSTEMD = PROJECT_ROOT / "deploy" / "systemd"


def test_fullstack_units_force_shadow_and_surface_failures() -> None:
    maker = (SYSTEMD / "predictions-cup-maker.service").read_text(encoding="utf-8")
    sig = (SYSTEMD / "predictions-cup-sig-capture.service").read_text(encoding="utf-8")
    poly = (SYSTEMD / "predictions-cup-polymarket-capture.service").read_text(
        encoding="utf-8"
    )

    assert "Environment=PREDICTIONS_CUP_TRADING_ENABLED=false" in maker
    assert "Environment=PREDICTIONS_CUP_EXECUTION_MODE=SHADOW" in maker
    assert "UnsetEnvironment=PREDICTIONS_CUP_SIG_TRADE_CREDENTIAL" in maker
    assert " --live" not in maker
    assert "OnFailure=predictions-cup-alert@%n.service" in maker
    assert "OnFailure=predictions-cup-alert@%n.service" in sig
    assert "OnFailure=predictions-cup-alert@%n.service" in poly


def test_shadow_admission_rejects_live_configuration() -> None:
    safe = AppSettings(
        maker_enabled=True,
        shadow_enabled=True,
        trading_enabled=False,
        execution_mode="SHADOW",
        global_kill_switch=False,
        tournament_id="test-tournament",
        tournament_slug="test-tournament",
        maker_mapping_path=PROJECT_ROOT / "data" / "mappings" / "sig_polymarket_2026.json",
    )
    assert fullstack.check_component(safe, "maker-shadow") == (True, "READY")

    unsafe = safe.model_copy(
        update={"trading_enabled": True, "execution_mode": "LIVE"}
    )
    assert fullstack.check_component(unsafe, "maker-shadow") == (
        False,
        "unsafe_live_configuration",
    )


def test_status_never_authorizes_live(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    settings = AppSettings(
        maker_enabled=True,
        shadow_enabled=True,
        live_learn_enabled=True,
        sig_research_path=tmp_path / "sig_research",
        risk_state_path=tmp_path / "risk.sqlite3",
        execution_journal_path=tmp_path / "execution.sqlite3",
    )

    def unit_state(unit: str) -> dict[str, object]:
        active = unit in {fullstack.MAKER_UNIT, fullstack.STATUS_TIMER_UNIT}
        return {
            "unit": unit,
            "installed": True,
            "active": active,
            "state": "ACTIVE" if active else "INACTIVE",
        }

    monkeypatch.setattr(fullstack, "_systemd_unit_state", unit_state)
    monkeypatch.setattr(
        fullstack,
        "_clock_health",
        lambda: {"state": "HEALTHY", "ntp_synchronized": True},
    )
    monkeypatch.setattr(
        fullstack,
        "_storage_health",
        lambda _settings, _paths: {"state": "HEALTHY", "filesystems": []},
    )
    monkeypatch.setattr(
        fullstack,
        "_risk_status",
        lambda _settings: {
            "state": "DISABLED",
            "configured": False,
            "global_halt": None,
            "session_id": None,
        },
    )
    monkeypatch.setattr(
        fullstack,
        "_unresolved_operations",
        lambda _settings: {"count": 0, "state": "CLEAR"},
    )
    monkeypatch.setattr(
        fullstack,
        "_observe_status",
        lambda _settings, *, maker_active: {
            "state": "HEALTHY" if maker_active else "NOT_RUNNING",
            "process_instance_id": "test-session" if maker_active else None,
        },
    )

    status = fullstack.build_status(settings, repo_root=PROJECT_ROOT)

    auth = status["trading_authorization"]
    assert isinstance(auth, dict)
    assert auth["authorized"] is False
    assert auth["state"] == "NOT_READY"

    components = status["components"]
    assert isinstance(components, dict)
    maker = components["MAKE"]
    execution = components["BUILD_009_EXECUTION"]
    assert isinstance(maker, dict)
    assert isinstance(execution, dict)
    assert maker["authorized"] is False
    assert execution["authorized"] is False

    live_learn = status["live_learn_health"]
    assert isinstance(live_learn, dict)
    assert live_learn["state"] == "PROCESS_RUNNING_HEALTH_UNKNOWN"
    assert live_learn["health_evidence"] == "PROCESS_ONLY"


def test_service_action_detects_failed_wanted_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, ...]] = []

    def systemctl(*args: str) -> fullstack.CommandResult:
        calls.append(args)
        if args[:3] == ("is-active", "--quiet", fullstack.MAKER_UNIT):
            return fullstack.CommandResult(3, "", "")
        return fullstack.CommandResult(0, "", "")

    monkeypatch.setattr(fullstack, "_systemctl", systemctl)
    result = fullstack._service_action(
        "start",
        (fullstack.RUNTIME_TARGET,),
        verify_active=(fullstack.SIG_CAPTURE_UNIT, fullstack.MAKER_UNIT),
    )

    assert result["state"] == "BLOCKED"
    assert result["inactive_after_action"] == [fullstack.MAKER_UNIT]
    assert ("start", fullstack.RUNTIME_TARGET) in calls


def test_sig_capture_freshness_uses_newer_wal(tmp_path: Path) -> None:
    db = tmp_path / "sig.sqlite3"
    wal = Path(f"{db}-wal")
    db.write_bytes(b"db")
    wal.write_bytes(b"wal")
    now = fullstack._now().timestamp()
    os.utime(db, (now - 120, now - 120))
    os.utime(wal, (now, now))

    result = fullstack._mtime_freshness(db, active=True, max_age_seconds=60.0)

    assert result["state"] == "HEALTHY"
    assert result["evidence_path"] == str(wal)


def test_status_sqlite_reads_are_non_mutating(tmp_path: Path) -> None:
    risk_path = tmp_path / "risk.sqlite3"
    with sqlite3.connect(risk_path) as connection:
        connection.execute(
            "CREATE TABLE risk_state "
            "(singleton INTEGER PRIMARY KEY, payload_json TEXT NOT NULL)"
        )
        connection.execute(
            "INSERT INTO risk_state(singleton, payload_json) VALUES (1, ?)",
            (
                json.dumps(
                    {
                        "session_id": "test-session",
                        "global_halt": {"active": False, "reason": "none"},
                        "reconciliation_complete": True,
                        "account_trusted": True,
                        "marks_trusted": True,
                    }
                ),
            ),
        )

    execution_path = tmp_path / "execution.sqlite3"
    with sqlite3.connect(execution_path) as connection:
        connection.execute(
            "CREATE TABLE execution_envelopes "
            "(logical_operation_id TEXT PRIMARY KEY, lifecycle_state TEXT NOT NULL)"
        )
        connection.executemany(
            "INSERT INTO execution_envelopes VALUES (?, ?)",
            (("open-op", "OPEN"), ("done-op", "FILLED")),
        )

    settings = AppSettings(
        risk_capital_control_enabled=True,
        risk_state_path=risk_path,
        execution_journal_path=execution_path,
    )
    risk = fullstack._risk_status(settings)
    unresolved = fullstack._unresolved_operations(settings)

    assert risk["state"] == "READY"
    assert risk["session_id"] == "test-session"
    assert unresolved["count"] == 1
    assert not Path(f"{risk_path}-wal").exists()
    assert not Path(f"{execution_path}-wal").exists()


def test_installer_validates_before_install_and_rejects_live() -> None:
    source = (PROJECT_ROOT / "scripts" / "install_fullstack002.sh").read_text(
        encoding="utf-8"
    )
    assert "EXECUTION_MODE=LIVE" in source
    verify_marker = '"${SYSTEMD_ANALYZE_BIN}" verify'
    assert source.index(verify_marker) < source.index("install -m 0644")


def test_halt_stops_maker_before_durable_risk_command(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    risk_path = tmp_path / "risk.sqlite3"
    risk_path.touch()
    settings = AppSettings(
        sig_research_path=tmp_path / "sig_research",
        risk_state_path=risk_path,
    )
    calls: list[tuple[str, ...]] = []

    monkeypatch.setattr(
        fullstack,
        "_stop_maker",
        lambda: fullstack.CommandResult(0, "", ""),
    )
    monkeypatch.setattr(
        fullstack,
        "_systemctl",
        lambda *args: fullstack.CommandResult(3, "", "") if args[0] == "is-active"
        else fullstack.CommandResult(0, "", ""),
    )

    def run(command: Sequence[str], *, timeout: float = 5.0) -> fullstack.CommandResult:
        del timeout
        calls.append(tuple(command))
        return fullstack.CommandResult(0, "{}", "")

    monkeypatch.setattr(fullstack, "_run", run)
    result = fullstack.halt_global(settings, reason="test-halt")

    assert result["state"] == "HALTED"
    assert calls
    assert "scripts/risk002_control.py" in calls[0]
    assert "halt-global" in calls[0]
    assert "--ack-service-stopped" in calls[0]


def test_flatten_is_explicitly_not_exposed() -> None:
    source = (
        PROJECT_ROOT / "src" / "predictions_cup" / "fullstack" / "control.py"
    ).read_text(encoding="utf-8")
    assert '"state": "NOT_READY"' in source
    assert "no accepted current-main operator flatten contract" in source


def test_transition_alerts_include_unresolved_execution_operations(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    emitted: list[tuple[str, dict[str, object]]] = []

    monkeypatch.setattr(
        fullstack,
        "append_alert",
        lambda _path, *, event_type, detail: emitted.append((event_type, dict(detail))),
    )
    previous = {
        "unresolved_operations": {"state": "CLEAR", "count": 0},
        "process_health": {},
    }
    current = {
        "unresolved_operations": {"state": "BLOCKED", "count": 1},
        "process_health": {},
    }

    fullstack._transition_alerts(previous, current, tmp_path / "alerts.jsonl")

    assert emitted == [
        (
            "UNRESOLVED_EXECUTION_OPERATIONS",
            {"before": "CLEAR", "after": "BLOCKED"},
        )
    ]


def test_alert_command_survives_invalid_runtime_settings(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    alert_path = tmp_path / "alerts.jsonl"
    monkeypatch.setenv("PREDICTIONS_CUP_FULLSTACK_ALERT_PATH", str(alert_path))
    monkeypatch.setenv("PREDICTIONS_CUP_EXECUTION_MODE", "INVALID")

    result = fullstack.main(
        ["alert", "--unit", "predictions-cup-maker.service", "--runtime-env-only"]
    )

    assert result == 0
    assert alert_path.is_file()
    payload = json.loads(alert_path.read_text(encoding="utf-8").strip())
    assert payload["event_type"] == "SYSTEMD_ON_FAILURE"
    assert payload["detail"]["unit"] == "predictions-cup-maker.service"


def test_alert_webhook_is_optional_and_durable_first(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    alert_path = tmp_path / "alerts.jsonl"
    monkeypatch.setenv(
        "PREDICTIONS_CUP_FULLSTACK_ALERT_WEBHOOK_URL",
        "https://alerts.example.test/predictions-cup",
    )
    observed_durable_before_push: list[bool] = []

    class _Response:
        status = 204

        def __enter__(self) -> _Response:
            return self

        def __exit__(self, *args: object) -> None:
            return None

    def fake_urlopen(request: object, *, timeout: float) -> _Response:
        del request, timeout
        observed_durable_before_push.append(alert_path.is_file())
        return _Response()

    monkeypatch.setattr(fullstack, "urlopen", fake_urlopen)
    fullstack.append_alert(
        alert_path,
        event_type="SERVICE_FAILED",
        detail={"unit": "predictions-cup-maker.service"},
    )

    assert observed_durable_before_push == [True]
    rows = [
        json.loads(line)
        for line in alert_path.read_text(encoding="utf-8").splitlines()
    ]
    assert [row["event_type"] for row in rows] == ["SERVICE_FAILED"]
