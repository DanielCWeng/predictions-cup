"""Startup recovery must use a targeted index and retain cancellation semantics."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from predictions_cup.execution.journal import ExecutionJournal
from predictions_cup.execution.models import (
    ExecutionEnvelope,
    ExecutionMode,
    LifecycleState,
    OperationKind,
)


def make_cancel(
    journal: ExecutionJournal,
    *,
    op_id: str,
    order_id: int,
    state: LifecycleState,
) -> None:
    envelope = ExecutionEnvelope.cancellation(
        logical_operation_id=op_id,
        operation_kind=OperationKind.SINGLE_CANCELLATION,
        sink_mode=ExecutionMode.LIVE,
        created_monotonic_ns=100,
        order_id=order_id,
        tournament_id="t1",
    )
    journal.record_before_dispatch(envelope, submitted_monotonic_ns=101)
    journal.record_event(
        logical_operation_id=op_id,
        event_type="CANCEL_ACK",
        observed_monotonic_ns=102,
        exchange_order_id=str(order_id),
    )
    journal.mark_state(op_id, state, 103)


def test_terminal_cancellation_lookup_uses_partial_index(tmp_path: Path) -> None:
    path = tmp_path / "cancel-recovery-index.sqlite3"
    journal = ExecutionJournal(path)
    try:
        make_cancel(
            journal, op_id="cancelled-91", order_id=91,
            state=LifecycleState.CANCELLED,
        )
        make_cancel(
            journal, op_id="rejected-92", order_id=92,
            state=LifecycleState.REJECTED,
        )
        make_cancel(
            journal, op_id="uncertain-93", order_id=93,
            state=LifecycleState.UNCERTAIN,
        )
        assert journal.confirmed_cancelled_order_ids() == frozenset({"91"})
        with sqlite3.connect(path) as connection:
            rows = connection.execute(
                "SELECT name FROM sqlite_master WHERE type='index' "
                "AND name='execution_events_cancel_terminal_idx'"
            ).fetchall()
            assert rows == [("execution_events_cancel_terminal_idx",)]
    finally:
        journal.close()


def test_partial_index_exists_on_reopened_journal(tmp_path: Path) -> None:
    path = tmp_path / "existing-journal.sqlite3"
    journal = ExecutionJournal(path)
    journal.close()
    reopened = ExecutionJournal(path)
    try:
        assert reopened.confirmed_cancelled_order_ids() == frozenset()
    finally:
        reopened.close()
