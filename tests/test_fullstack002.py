from __future__ import annotations

from pathlib import Path
from typing import Sequence

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
