"""Regression guards: research capture OFF does not affect live trading authority.

All checks are offline. No SIG/PM network access, no operational SQLite writes,
no historical archive access and no EC2 service-management actions.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

from predictions_cup.config import AppSettings
from predictions_cup.external.polymarket import recorder as pm_capture
from predictions_cup.maker import service as maker_module
from predictions_cup.maker.service import MakerService
from predictions_cup.observe.emitter import NullObservationEmitter
from predictions_cup.sig import capture as sig_capture
from predictions_cup.supervisor.config import SupervisorRuntimeConfig
from predictions_cup.supervisor.contracts import HostRole


class PastResearchInitialization(BaseException):
    """Intentional early abort before any account, maker or market interaction."""


def test_research_defaults_off_and_can_be_explicitly_enabled() -> None:
    assert AppSettings(_env_file=None).research_collection_enabled is False
    assert AppSettings(_env_file=None, research_collection_enabled=True).research_collection_enabled


def test_sig_historical_capture_disabled_without_network_or_disk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = AppSettings(_env_file=None, research_collection_enabled=False)
    monkeypatch.setattr(sig_capture, "load_settings", lambda **kwargs: settings)
    monkeypatch.setattr(sys, "argv", ["sig.capture", "--runtime-env-only"])

    def unexpected(*args: object, **kwargs: object) -> None:
        raise AssertionError("SIG capture must not initialise")

    monkeypatch.setattr(sig_capture, "_run", unexpected)
    monkeypatch.setattr(sig_capture.asyncio, "run", unexpected)
    assert sig_capture.main() == 0


def test_polymarket_historical_capture_disabled_even_with_old_enable_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = AppSettings(
        _env_file=None,
        research_collection_enabled=False,
        polymarket_capture_enabled=True,
    )
    monkeypatch.setattr(pm_capture, "load_settings", lambda **kwargs: settings)

    def unexpected(*args: object, **kwargs: object) -> None:
        raise AssertionError("Polymarket capture must not initialise")

    monkeypatch.setattr(pm_capture, "PolymarketRecorder", unexpected)
    monkeypatch.setattr(pm_capture.asyncio, "run", unexpected)
    assert pm_capture.main(["--runtime-env-only", "--require-explicit-universe"]) == 0


def test_supervisor_does_not_restart_or_prune_retired_research(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setenv(
        "PREDICTIONS_CUP_SUPERVISOR_EXPECTED_SERVICES",
        "predictions-cup-sig-capture.service,predictions-cup-polymarket-capture.service",
    )
    monkeypatch.setenv(
        "PREDICTIONS_CUP_SUPERVISOR_SAFE_RESTART_SERVICES",
        "predictions-cup-sig-capture.service,predictions-cup-polymarket-capture.service",
    )
    settings = AppSettings(
        _env_file=None,
        research_collection_enabled=False,
        polymarket_capture_enabled=True,
        maker_enabled=True,
    )
    config = SupervisorRuntimeConfig.from_env(
        settings, repo_root=tmp_path, role_override=HostRole.WEST_EXECUTION
    )
    assert config.expected_services == ("predictions-cup-maker-live.service",)
    assert config.remediation.safe_restart_services == ()
    assert config.remediation.hot_capture_roots == ()


def test_supervisor_can_opt_in_to_capture_recovery(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.delenv("PREDICTIONS_CUP_SUPERVISOR_EXPECTED_SERVICES", raising=False)
    monkeypatch.delenv("PREDICTIONS_CUP_SUPERVISOR_SAFE_RESTART_SERVICES", raising=False)
    settings = AppSettings(
        _env_file=None,
        research_collection_enabled=True,
        polymarket_capture_enabled=True,
    )
    config = SupervisorRuntimeConfig.from_env(
        settings, repo_root=tmp_path, role_override=HostRole.EAST_ARCHIVE
    )
    assert "predictions-cup-sig-capture.service" in config.expected_services
    assert "predictions-cup-polymarket-capture.service" in config.expected_services
    assert "predictions-cup-sig-capture.service" in config.remediation.safe_restart_services
    assert len(config.remediation.hot_capture_roots) == 2


def test_maker_research_off_skips_optional_capture_initialization(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    maker = object.__new__(MakerService)
    maker.settings = cast(AppSettings, SimpleNamespace(
        research_collection_enabled=False,
        maker_depth_mirror_output_file=tmp_path / "unused.jsonl",
    ))

    class StopBeforeTrading:
        def __getattr__(self, name: str) -> None:
            raise PastResearchInitialization(name)

    maker.core = cast(object, StopBeforeTrading())
    monkeypatch.setattr(MakerService, "_tournament_context", lambda self: ("offline", "offline"))
    monkeypatch.setattr(maker_module, "build_rest_governor", lambda settings: object())
    monkeypatch.setattr(
        maker_module, "GovernedSigRestClient", lambda *args, **kwargs: object(),
    )

    def unexpected(*args: object, **kwargs: object) -> None:
        raise AssertionError("research-only service must not be constructed")

    monkeypatch.setattr(maker_module, "MakerDepthMirror", unexpected)
    monkeypatch.setattr(maker_module, "ObservationCaptureRecorder", unexpected)
    monkeypatch.setattr(maker_module, "CompetitionContextSampler", unexpected)
    monkeypatch.setattr(maker_module, "BoundedObservationEmitter", unexpected)
    with pytest.raises(PastResearchInitialization):
        asyncio.run(maker.run())
    assert not (tmp_path / "unused.jsonl").exists()


def test_null_observation_emitter_does_not_start_worker_or_save_data() -> None:
    emitter = NullObservationEmitter()
    assert emitter.emit(cast(object, object()))
    emitter.close()
