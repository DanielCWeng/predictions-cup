"""Offline service-startup regression: optional research must not gate maker startup.

No exchange client is constructed: the maker run is intercepted immediately after
the research constructor, before any market-data, execution or filesystem startup.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

from predictions_cup.config import AppSettings
from predictions_cup.maker import service as service_module
from predictions_cup.maker.service import MakerService
from predictions_cup.sig.depth_mirror import MakerDepthMirror


class NextStartupStageReached(Exception):
    """The maker progressed beyond optional research initialization."""


def _exercise_early_startup(
    monkeypatch: pytest.MonkeyPatch, output: Path | None,
) -> None:
    # This deliberately avoids creating a real MakerService, REST client, DB or
    # recorder. Execute its actual run() function until the next startup stage.
    maker = object.__new__(MakerService)
    maker.settings = cast(AppSettings, SimpleNamespace(
        research_collection_enabled=True,
        maker_depth_mirror_output_file=output,
        sig_research_path=Path("/unused/offline-research"),
        sig_capture_queue_max=1024,
        sig_capture_parquet_shard_seconds=30,
        sig_capture_parquet_max_rows_per_shard=1000,
    ))
    monkeypatch.setattr(
        MakerService, "_tournament_context", lambda self: ("offline-cup", "offline-cup"),
    )
    monkeypatch.setattr(service_module, "build_rest_governor", lambda settings: object())
    monkeypatch.setattr(
        service_module, "GovernedSigRestClient", lambda *args, **kwargs: object(),
    )

    def stop_at_next_component(*args: object, **kwargs: object) -> None:
        raise NextStartupStageReached

    monkeypatch.setattr(service_module, "ObservationCaptureRecorder", stop_at_next_component)
    with pytest.raises(NextStartupStageReached):
        asyncio.run(maker.run())


def test_invalid_research_destination_does_not_block_maker_startup(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    # Use the REAL constructor. A relative file is invalid by design, but
    # must not change the execution startup result.
    _exercise_early_startup(monkeypatch, Path("relative-mirror.jsonl"))
    assert "constructor failed (ValueError); capture disabled" in caplog.text


@pytest.mark.parametrize("failure", [PermissionError, MemoryError])
def test_research_constructor_resource_error_is_isolated(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    tmp_path: Path,
    failure: type[Exception],
) -> None:
    def fail_constructor(output: Path) -> MakerDepthMirror:
        raise failure("injected offline resource failure")

    monkeypatch.setattr(service_module, "MakerDepthMirror", fail_constructor)
    _exercise_early_startup(monkeypatch, tmp_path / "mirror.jsonl")
    assert "capture disabled" in caplog.text


def test_default_off_does_not_construct_mirror(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unexpected_constructor(output: Path) -> MakerDepthMirror:
        raise AssertionError("default OFF must not construct research mirror")

    monkeypatch.setattr(service_module, "MakerDepthMirror", unexpected_constructor)
    _exercise_early_startup(monkeypatch, None)
