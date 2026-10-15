"""Capture startup must prioritise reconnecting over large retention scans."""

from __future__ import annotations

import asyncio
import sys
from datetime import UTC, datetime, timedelta

import pytest

from predictions_cup.config import AppSettings
from predictions_cup.sig.capture import (
    _capture_retry_delay_seconds,
    _prune_at_startup,
    _wait_or_stop,
    parse_args,
)


class StubRecorder:
    def __init__(self) -> None:
        self.cutoffs: list[datetime] = []

    def prune_before(self, cutoff: datetime) -> None:
        self.cutoffs.append(cutoff)


def test_startup_pruning_defaults_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "argv", ["sig.capture"])
    assert parse_args().prune_on_startup is False
    recorder = StubRecorder()
    _prune_at_startup(recorder, enabled=False, retention_days=14)  # type: ignore[arg-type]
    assert recorder.cutoffs == []


def test_explicit_startup_pruning_only_with_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "argv", ["sig.capture", "--prune-on-startup"])
    assert parse_args().prune_on_startup is True
    recorder = StubRecorder()
    before = datetime.now(UTC) - timedelta(days=14)
    _prune_at_startup(recorder, enabled=True, retention_days=14)  # type: ignore[arg-type]
    after = datetime.now(UTC) - timedelta(days=14)
    assert len(recorder.cutoffs) == 1
    assert before <= recorder.cutoffs[0] <= after


@pytest.mark.parametrize(
    ("failures", "expected_seconds"),
    ((1, 1.0), (2, 2.0), (3, 4.0), (4, 8.0), (6, 32.0), (7, 60.0), (20, 60.0)),
)
def test_sig_capture_recovery_backoff_is_bounded(
    failures: int, expected_seconds: float
) -> None:
    assert _capture_retry_delay_seconds(failures) == expected_seconds


def test_sig_capture_recovery_backoff_rejects_invalid_counter() -> None:
    with pytest.raises(ValueError, match="positive"):
        _capture_retry_delay_seconds(0)


def test_sig_capture_backoff_wait_responds_to_shutdown() -> None:
    async def scenario() -> None:
        stop_event = asyncio.Event()
        worker = asyncio.create_task(_wait_or_stop(stop_event, 60.0))
        await asyncio.sleep(0)
        stop_event.set()
        await asyncio.wait_for(worker, timeout=0.5)

    asyncio.run(scenario())


def test_sig_capture_backoff_wait_expires_without_shutdown() -> None:
    async def scenario() -> None:
        stop_event = asyncio.Event()
        await _wait_or_stop(stop_event, 0.001)
        assert not stop_event.is_set()

    asyncio.run(scenario())

def test_research_capture_bulk_cadence_is_decoupled_from_live_maker() -> None:
    settings = AppSettings()
    assert settings.sig_capture_bulk_price_refresh_seconds == 60.0
    assert settings.sig_realtime_bulk_price_refresh_seconds == 10.0
    assert settings.maker_max_bbo_age_ms < 60_000
