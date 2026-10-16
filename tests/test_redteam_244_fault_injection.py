"""Reviewer-only #244 falsification. No venue client, socket or production edits.

Run after overlaying the unchanged independent #208 test blobs onto #244.
Cross-process SQLite identity is tested separately from the *missing* shared
risk-reservation authority (documented as strict XFAIL).
"""
from __future__ import annotations

import multiprocessing as mp
import sqlite3
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from predictions_cup.execution.journal import ExecutionJournal
from predictions_cup.execution.models import (
    ExecutionEnvelope,
    ExecutionMode,
    OperationKind,
    RuntimeOrderIntent,
)
from predictions_cup.execution.planner import build_execution_plan
from predictions_cup.execution.reservations import ExecutionReservationBook
from predictions_cup.risk.core import RiskDecision
from predictions_cup.runtime import OrderAction, OutcomeSide


def _plan() -> tuple[ExecutionEnvelope, tuple[RuntimeOrderIntent, ...]]:
    intent = RuntimeOrderIntent(
        intent_id="redteam-intent",
        exchange_id="36",
        market_id="m1",
        tournament_id="t1",
        outcome_side=OutcomeSide.YES,
        action=OrderAction.BUY,
        quantity=1,
        limit_price_ticks=100,
        strategy_id="redteam",
        decision_observation_ns=100,
    )
    decision = RiskDecision(
        approved=True,
        reason="redteam fixture",
        execution_mode=ExecutionMode.LIVE,
        operation_kind=OperationKind.SINGLE_PLACEMENT,
        intents=(intent,),
        strategy_family="FV-TAKE",
        strategy_id="redteam",
        signal_value=0.025,
        fair_value=0.55,
        decision_observation_ns=100,
    )
    plan = build_execution_plan(
        decision, logical_operation_id="redteam-durable-op", created_monotonic_ns=150,
    )
    return plan.envelope, plan.intents


def _claim(
    path: str,
    envelope: ExecutionEnvelope,
    intents: tuple[RuntimeOrderIntent, ...],
    barrier: Any,
    results: Any,
) -> None:
    journal = ExecutionJournal(Path(path))
    try:
        barrier.wait(timeout=10)
        try:
            journal.record_before_dispatch(envelope, intents, fresh_only=True)
        except ValueError as exc:
            results.put(("DUPLICATE", type(exc).__name__))
        else:
            results.put(("CLAIMED", ""))
    except Exception as exc:
        results.put(("ERROR", type(exc).__name__))
    finally:
        journal.close()


@pytest.mark.skipif(
    "fork" not in mp.get_all_start_methods(),
    reason="cross-process fork test requires Linux runner",
)
def test_two_processes_racing_same_durable_fresh_id_exactly_one_claim(
    tmp_path: Path,
) -> None:
    """Different OS processes and DB connections cannot double-claim an ID."""
    path = tmp_path / "cross_process.sqlite3"
    first = ExecutionJournal(path)
    first.close()  # initialise schema before racing independent process openers
    envelope, intents = _plan()
    ctx = mp.get_context("fork")
    barrier = ctx.Barrier(2)
    results = ctx.Queue()
    workers = [
        ctx.Process(
            target=_claim,
            args=(str(path), envelope, intents, barrier, results),
        )
        for _ in range(2)
    ]
    for worker in workers:
        worker.start()
    try:
        outputs = [results.get(timeout=15) for _ in workers]
        for worker in workers:
            worker.join(timeout=5)
            assert worker.exitcode == 0
        assert sorted(tag for tag, _ in outputs) == ["CLAIMED", "DUPLICATE"]
        check = ExecutionJournal(path)
        try:
            assert len(check.envelopes()) == 1
            assert len(check.events(envelope.logical_operation_id)) == 1
        finally:
            check.close()
    finally:
        for worker in workers:
            if worker.is_alive():
                worker.terminate()
                worker.join(timeout=2)


def test_sqlite_busy_rejects_fresh_claim_without_releasing_reservation(
    tmp_path: Path,
) -> None:
    """A locked journal cannot permit a send or silently free provisional risk."""
    path = tmp_path / "busy.sqlite3"
    journal = ExecutionJournal(path)
    journal._connection.execute("PRAGMA busy_timeout=30")
    envelope, intents = _plan()
    book = ExecutionReservationBook()
    book.reserve(envelope.logical_operation_id, intents)
    blocker = sqlite3.connect(path, isolation_level=None, timeout=0.03)
    try:
        blocker.execute("BEGIN IMMEDIATE")
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            journal.record_before_dispatch(envelope, intents, fresh_only=True)
        assert book.contains_operation(envelope.logical_operation_id, envelope.intent_ids)
    finally:
        blocker.rollback()
        blocker.close()
    try:
        assert journal.envelopes() == ()
    finally:
        journal.close()


@pytest.mark.xfail(
    strict=True,
    reason="LIVE blocker: separate sender-local reservation books are not globally serialised",
)
def test_distinct_senders_cannot_reserve_the_same_one_share_global_slice() -> None:
    """Explicit negative: #244 guarantees journal ID uniqueness, not shared gross risk."""
    _, intents = _plan()
    second = (replace(intents[0], intent_id="second-local-sender"),)
    sender_a = ExecutionReservationBook()
    sender_b = ExecutionReservationBook()
    sender_a.reserve("logical-a", intents)
    sender_b.reserve("logical-b", second)
    assert sum(o.reserved_exposure for o in sender_a.reserved_orders()) + sum(
        o.reserved_exposure for o in sender_b.reserved_orders()
    ) <= 1.0
