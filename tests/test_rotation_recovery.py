"""Fail-closed rotation recovery regression tests."""
import asyncio
from pathlib import Path

import pytest

from predictions_cup.execution.journal import ExecutionJournal
from predictions_cup.maker.rotation_recovery import (
    AttributedExitProof,
    AuthoritativeProof,
    DispatchReceipt,
    EntryApproval,
    ExitApproval,
    RotationEvidence,
    RotationEvidenceCache,
    RotationOutcome,
    RotationRequest,
    TwoPhaseRotation,
)


def test_account_changes_are_exchange_scoped() -> None:
    cache = RotationEvidenceCache(max_history=2)
    positions = (("a", 500), ("c", -100))
    cache.update_account(trusted=True, positions=positions, pending_orders=())
    events: list[RotationEvidence] = []
    assert cache.offer(
        RotationEvidence("a", "b", 100, 10, 15, 100),
        now_ns=100, publish=events.append,
    )
    assert not cache.offer(
        RotationEvidence("a", "b", 100, 10, 15, 100),
        now_ns=101, publish=events.append,
    )
    assert len(events) == 1
    cache.offer(RotationEvidence("c", "d", 100, 10, 15, 100), now_ns=101)
    cache.update_account(trusted=True, positions=positions,
                         pending_orders=(("c", "order-id", 50),))
    assert tuple(x.key for x in cache.eligible(102)) == (("a", "b"),)
    cache.update_account(trusted=False, positions=positions, pending_orders=())
    assert not cache.eligible(103)
    assert len(cache.history) == 2


def test_partial_fill_requires_authoritative_reconcile() -> None:
    sequence: list[str] = []
    claimed: set[str] = set()
    async def claim(opid: str) -> bool:
        sequence.append("claim")
        if opid in claimed:
            return False
        claimed.add(opid)
        return True
    async def attribution(opid: str) -> AttributedExitProof:
        sequence.append("attribution")
        return AttributedExitProof(opid, "exit", -60, 60, True, True)
    async def before(_: str) -> AuthoritativeProof:
        return AuthoritativeProof("exit", 100, 5, True, True, 0, 0)
    async def exit_check(req: RotationRequest, proof: AuthoritativeProof) -> ExitApproval:
        return ExitApproval(100, 100, 20., 100, True, True)
    async def exit_send(req: RotationRequest, approval: ExitApproval) -> DispatchReceipt:
        sequence.append("exit")
        return DispatchReceipt(req.operation_id + ":exit", "FILLED")
    async def reconcile(_: str) -> AuthoritativeProof:
        sequence.append("reconcile")
        return AuthoritativeProof("exit", 40, 6, True, True, 0, 0)
    async def entry_check(req: RotationRequest, qty: int) -> EntryApproval:
        assert qty == 60
        sequence.append("entry_check")
        return EntryApproval(60, 8., 40., 100, True)
    async def entry_send(req: RotationRequest, approval: EntryApproval) -> DispatchReceipt:
        sequence.append("entry")
        return DispatchReceipt(req.operation_id + ":entry", "ACKED")
    async def go() -> tuple[RotationOutcome, RotationOutcome]:
        controller = TwoPhaseRotation(live_enabled=True)
        request = RotationRequest("r1", "exit", "entry", 100, 5.)
        first = await controller.run(
            request,
            read_before=before, claim_operation=claim,
            prepare_exit=exit_check, dispatch_exit=exit_send,
            reconcile_exit=reconcile, read_attributed_exit=attribution,
            prepare_entry=entry_check, dispatch_entry=entry_send,
        )
        duplicate = await controller.run(
            request,
            read_before=before, claim_operation=claim,
            prepare_exit=exit_check, dispatch_exit=exit_send,
            reconcile_exit=reconcile, read_attributed_exit=attribution,
            prepare_entry=entry_check, dispatch_entry=entry_send,
        )
        return first, duplicate
    first, duplicate = asyncio.run(go())
    assert first.status == "ENTRY_DISPATCHED"
    assert first.confirmed_reduction_shares == first.entry_shares == 60
    assert duplicate.status == "BLOCKED"
    assert sequence == ["claim", "exit", "reconcile", "attribution", "entry_check", "entry"]


@pytest.mark.parametrize("restart", [False, True])
def test_rotation_claim_is_durable_and_exact(tmp_path: Path, restart: bool) -> None:
    journal_path = tmp_path / "journal.sqlite3"
    first = ExecutionJournal(journal_path)
    try:
        assert first.claim_rotation_operation("rotation:1", "tournament-west")
        assert not first.claim_rotation_operation("rotation:1", "tournament-west")
    finally:
        first.close()

    if restart:
        # Simulate the actual process-restart boundary.
        second = ExecutionJournal(journal_path)
        try:
            assert not second.claim_rotation_operation("rotation:1", "tournament-west")
            assert not second.claim_rotation_operation("rotation:1", "different-tournament")
            assert second.claim_rotation_operation("rotation:2", "tournament-west")
        finally:
            second.close()
