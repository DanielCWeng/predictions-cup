from __future__ import annotations

import os
import socket
import subprocess
import sys

import pytest

from predictions_cup.app import run


def _network_forbidden(*args: object, **kwargs: object) -> None:
    del args, kwargs
    raise AssertionError("network access is forbidden during BUILD-001 smoke tests")


def test_application_entry_point_starts_without_secrets() -> None:
    env = {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONPATH": os.environ.get("PYTHONPATH", ""),
    }
    result = subprocess.run(
        [sys.executable, "-m", "predictions_cup.app", "--smoke-test"],
        check=False,
        capture_output=True,
        text=True,
        env=env,
        timeout=10,
    )

    assert result.returncode == 0
    assert "trading_capability=NONE" in result.stderr
    assert "state=unconfigured" in result.stderr


def test_smoke_path_does_not_attempt_network_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(socket, "create_connection", _network_forbidden)
    monkeypatch.setattr(socket.socket, "connect", _network_forbidden)

    assert run(smoke_test=True) == 0


def test_invalid_log_level_falls_back_to_info(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PREDICTIONS_CUP_LOG_LEVEL", "not-a-level")

    assert run(smoke_test=True) == 0
