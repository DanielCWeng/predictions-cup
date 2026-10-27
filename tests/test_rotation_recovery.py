"""Fail-closed rotation recovery regression tests."""
import asyncio

from predictions_cup.maker.rotation_recovery import (
    AuthoritativeProof,
    DispatchReceipt,
    EntryApproval,
    ExitApproval,
    RotationEvidence,
    RotationEvidenceCache,
    RotationRequest,
    TwoPhaseRotation,
)


def test_account_changes_are_exchange_scoped():
    cache = RotationEvidenceCache(max_history=2)
    positions = (("a", 500), ("c", -100))
    cache.update_account(trusted=True, positions=positions, pending_orders=())
    events = []
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


def test_partial_fill_requires_authoritative_reconcile():
    sequence = []
    async def before(_):
        return AuthoritativeProof("exit", 100, 5, True, True, 0, 0)
    async def exit_check(req, proof):
        return ExitApproval(100, 100, 20., 100, True, True)
    async def exit_send(req, approval):
        sequence.append("exit")
        return DispatchReceipt(req.operation_id + ":exit", "FILLED")
    async def reconcile(_):
        sequence.append("reconcile")
        return AuthoritativeProof("exit", 40, 6, True, True, 0, 0)
    async def entry_check(req, qty):
        assert qty == 60
        sequence.append("entry_check")
        return EntryApproval(60, 8., 40., 100, True)
    async def entry_send(req, approval):
        sequence.append("entry")
        return DispatchReceipt(req.operation_id + ":entry", "ACKED")
    async def go():
        controller = TwoPhaseRotation(live_enabled=True)
        kwargs = dict(read_before=before, prepare_exit=exit_check,
                      dispatch_exit=exit_send, reconcile_exit=reconcile,
                      prepare_entry=entry_check, dispatch_entry=entry_send)
        request = RotationRequest("r1", "exit", "entry", 100, 5.)
        first = await controller.run(request, **kwargs)
        duplicate = await controller.run(request, **kwargs)
        return first, duplicate
    first, duplicate = asyncio.run(go())
    assert first.status == "ENTRY_DISPATCHED"
    assert first.confirmed_reduction_shares == first.entry_shares == 60
    assert duplicate.status == "BLOCKED"
    assert sequence == ["exit", "reconcile", "entry_check", "entry"]
