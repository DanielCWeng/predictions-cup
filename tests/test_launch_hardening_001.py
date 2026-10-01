"""Deterministic hostile regressions for LAUNCH-HARDENING-001."""

from __future__ import annotations

from pathlib import Path

import pytest

from predictions_cup.execution.journal import ExecutionJournal
from predictions_cup.execution.models import ExecutionEnvelope, ExecutionMode, OperationKind
from predictions_cup.maker.instance_lock import MakerInstanceLock


def test_execution_journal_persists_wall_clock_for_recovery(tmp_path: Path) -> None:
    journal = ExecutionJournal(tmp_path / "execution.sqlite3")
    try:
        envelope = ExecutionEnvelope.cancellation(
            logical_operation_id="cancel-1",
            operation_kind=OperationKind.CANCEL_ALL,
            sink_mode=ExecutionMode.LIVE,
            created_monotonic_ns=123,
            tournament_id="t1",
        )
        journal.record_before_dispatch(envelope)
        created_at = journal.operation_created_at("cancel-1")
        assert created_at is not None
        assert created_at.tzinfo is not None
        assert created_at.utcoffset() is not None
    finally:
        journal.close()


def test_live_make_instance_lock_rejects_duplicate_and_recovers(tmp_path: Path) -> None:
    path = tmp_path / "maker.lock"
    first = MakerInstanceLock(path)
    second = MakerInstanceLock(path)
    first.acquire()
    try:
        with pytest.raises(RuntimeError, match="another LIVE MAKE instance"):
            second.acquire()
    finally:
        first.close()

    second.acquire()
    second.close()
