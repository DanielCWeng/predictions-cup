"""Capture startup must prioritise reconnecting over large retention scans."""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta

import pytest

from predictions_cup.sig.capture import _prune_at_startup, parse_args


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
