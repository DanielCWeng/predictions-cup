from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

from predictions_cup.config import AppSettings
from predictions_cup.sig.capture import _tracked_exchange_ids

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SYSTEMD_DIR = PROJECT_ROOT / "deploy" / "systemd"
INSTALLER = PROJECT_ROOT / "scripts" / "install_runtime_services.sh"
SIG_UNIT = SYSTEMD_DIR / "predictions-cup-sig-capture.service"
POLY_UNIT = SYSTEMD_DIR / "predictions-cup-polymarket-capture.service"


def test_tracked_depth_defaults_empty_and_can_be_supplied_externally() -> None:
    default_settings = AppSettings()
    empty_args = argparse.Namespace(tracked_exchange_id=[])
    assert _tracked_exchange_ids(empty_args, default_settings) == ()

    configured = AppSettings(
        sig_realtime_tracked_exchange_ids="exchange-a, exchange-b,exchange-a"
    )
    cli_args = argparse.Namespace(tracked_exchange_id=["exchange-b", "exchange-c"])
    assert _tracked_exchange_ids(cli_args, configured) == (
        "exchange-a",
        "exchange-b",
        "exchange-c",
    )


def test_systemd_units_are_read_only_and_supervised() -> None:
    sig = SIG_UNIT.read_text(encoding="utf-8")
    poly = POLY_UNIT.read_text(encoding="utf-8")

    for unit in (sig, poly):
        assert "EnvironmentFile=@@RUNTIME_ENV@@" in unit
        assert "trade.env" not in unit
        assert "UnsetEnvironment=PREDICTIONS_CUP_SIG_TRADE_CREDENTIAL" in unit
        assert "Restart=on-failure" in unit
        assert "After=network-online.target" in unit
        assert "Wants=network-online.target" in unit
        assert "KillSignal=SIGTERM" in unit
        assert "StandardOutput=journal" in unit
        assert "StandardError=journal" in unit
        assert "NoNewPrivileges=true" in unit

    assert "ExecStart=@@PYTHON_BIN@@ -m predictions_cup.sig.capture --runtime-env-only" in sig
    assert "--tracked-exchange-id" not in sig
    assert (
        "ExecStart=@@PYTHON_BIN@@ -m predictions_cup.external.polymarket.recorder --runtime-env-only"
        in poly
    )


def _write_runtime_env(path: Path, *, include_trade_credential: bool = False) -> str:
    secret = "TEST_READ_SECRET_DO_NOT_PRINT"
    lines = [
        f"PREDICTIONS_CUP_SIG_READ_CREDENTIAL={secret}",
        "PREDICTIONS_CUP_TOURNAMENT_ID=test-tournament",
        "PREDICTIONS_CUP_TRADING_ENABLED=false",
        "PREDICTIONS_CUP_POLYMARKET_CAPTURE_ENABLED=true",
    ]
    if include_trade_credential:
        lines.append("PREDICTIONS_CUP_SIG_TRADE_CREDENTIAL=TEST_TRADE_SECRET_DO_NOT_PRINT")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    path.chmod(0o600)
    return secret


def _installer_env(tmp_path: Path) -> tuple[dict[str, str], Path, Path]:
    runtime_home = tmp_path / "home"
    config_dir = runtime_home / ".config" / "predictions-cup"
    config_dir.mkdir(parents=True)
    config_dir.chmod(0o700)
    runtime_env = config_dir / "runtime.env"
    _write_runtime_env(runtime_env)

    fake_systemctl = tmp_path / "systemctl"
    call_log = tmp_path / "systemctl.calls"
    fake_systemctl.write_text(
        "#!/usr/bin/env bash\n"
        "printf '%s\\n' \"$*\" >> \"$CALL_LOG\"\n"
        "if [[ \"$1\" == \"restart\" && \"${FAIL_RESTART_SERVICE:-}\" == \"${2:-}\" ]]; then\n"
        "  exit 1\n"
        "fi\n"
        "if [[ \"$1\" == \"is-active\" && \"${FAIL_ACTIVE_SERVICE:-}\" == \"${3:-}\" ]]; then\n"
        "  exit 3\n"
        "fi\n"
        "exit 0\n",
        encoding="utf-8",
    )
    fake_systemctl.chmod(0o755)

    env = os.environ.copy()
    env.update(
        {
            "PREDICTIONS_CUP_RUNTIME_USER": subprocess.check_output(
                ["id", "-un"], text=True
            ).strip(),
            "PREDICTIONS_CUP_RUNTIME_HOME": str(runtime_home),
            "PREDICTIONS_CUP_PYTHON": sys.executable,
            "PREDICTIONS_CUP_SYSTEMD_DIR": str(tmp_path / "systemd"),
            "PREDICTIONS_CUP_SYSTEMCTL": str(fake_systemctl),
            "CALL_LOG": str(call_log),
        }
    )
    return env, runtime_env, call_log


def test_installer_is_idempotent_and_renders_absolute_runtime_env(tmp_path: Path) -> None:
    env, runtime_env, call_log = _installer_env(tmp_path)

    first = subprocess.run(
        ["bash", str(INSTALLER)],
        cwd=PROJECT_ROOT,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    installed_dir = Path(env["PREDICTIONS_CUP_SYSTEMD_DIR"])
    first_units = {
        path.name: path.read_text(encoding="utf-8")
        for path in installed_dir.glob("*.service")
    }

    second = subprocess.run(
        ["bash", str(INSTALLER)],
        cwd=PROJECT_ROOT,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    second_units = {
        path.name: path.read_text(encoding="utf-8")
        for path in installed_dir.glob("*.service")
    }

    assert first_units == second_units
    assert set(first_units) == {
        "predictions-cup-sig-capture.service",
        "predictions-cup-polymarket-capture.service",
    }
    for content in first_units.values():
        assert f"EnvironmentFile={runtime_env}" in content
        assert "@@" not in content
        assert "trade.env" not in content
        assert "--runtime-env-only" in content
        assert "UnsetEnvironment=PREDICTIONS_CUP_SIG_TRADE_CREDENTIAL" in content
    assert "--tracked-exchange-id" not in first_units[
        "predictions-cup-sig-capture.service"
    ]

    calls = call_log.read_text(encoding="utf-8").splitlines()
    assert calls.count("daemon-reload") == 2
    assert calls.count("enable predictions-cup-sig-capture.service") == 2
    assert calls.count("enable predictions-cup-polymarket-capture.service") == 2
    assert calls.count("restart predictions-cup-sig-capture.service") == 2
    assert calls.count("restart predictions-cup-polymarket-capture.service") == 2
    assert "TEST_READ_SECRET_DO_NOT_PRINT" not in first.stdout + first.stderr
    assert "TEST_READ_SECRET_DO_NOT_PRINT" not in second.stdout + second.stderr


def test_installer_propagates_restart_failure(tmp_path: Path) -> None:
    env, _, _ = _installer_env(tmp_path)
    env["FAIL_RESTART_SERVICE"] = "predictions-cup-sig-capture.service"

    result = subprocess.run(
        ["bash", str(INSTALLER)],
        cwd=PROJECT_ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "restart failed for predictions-cup-sig-capture.service" in result.stderr


def test_installer_propagates_inactive_service(tmp_path: Path) -> None:
    env, _, _ = _installer_env(tmp_path)
    env["FAIL_ACTIVE_SERVICE"] = "predictions-cup-polymarket-capture.service"

    result = subprocess.run(
        ["bash", str(INSTALLER)],
        cwd=PROJECT_ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "predictions-cup-polymarket-capture.service is not active" in result.stderr

def test_installer_rejects_trade_credential_without_printing_secret(tmp_path: Path) -> None:
    env, runtime_env, _ = _installer_env(tmp_path)
    _write_runtime_env(runtime_env, include_trade_credential=True)

    result = subprocess.run(
        ["bash", str(INSTALLER)],
        cwd=PROJECT_ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "PREDICTIONS_CUP_SIG_TRADE_CREDENTIAL" in result.stderr
    assert "TEST_TRADE_SECRET_DO_NOT_PRINT" not in result.stdout + result.stderr
