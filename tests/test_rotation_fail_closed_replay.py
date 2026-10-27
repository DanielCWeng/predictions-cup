"""Rotation safety failures that must never become an entry order.

Pure replay tests. No network, exchange orders, or live credentials required.
"""
import asyncio

import pytest

from predictions_cup.maker.rotation_recovery import (
    AttributedExitProof,
    AuthoritativeProof,
    DispatchReceipt,
    EntryApproval,
    ExitApproval,
    RotationOutcome,
    RotationRequest,
    TwoPhaseRotation,
)


@pytest.mark.parametrize(
    ("scenario", "expected"),
    [
        ("clean_partial", "ENTRY_DISPATCHED"),
        ("exit_uncertain", "HALTED"),
        ("stale_account_version", "HALTED"),
        ("wrong_exit_operation", "HALTED"),
        ("concurrent_maker_fill", "HALTED"),
        ("attribution_not_final", "HALTED"),
        ("entry_no_longer_positive", "RELEASED_ONLY"),
    ],
)
def test_rotation_never_reinvests_unverified_exposure(scenario: str, expected: str) -> None:
    """A same-market maker fill, ACK or stale snapshot is not a rotation fill."""
    actions: list[str] = []
    claimed: set[str] = set()
    request = RotationRequest("replay-001", "held", "replacement", 500, 5.0)

    async def claim(op: str) -> bool:
        actions.append("claim")
        if op in claimed:
            return False
        claimed.add(op)
        return True

    async def read_before(exchange: str) -> AuthoritativeProof:
        actions.append("before")
        assert exchange == "held"
        return AuthoritativeProof("held", 500, 10, True, True, 0, 0)

    async def prepare_exit(
        req: RotationRequest, before: AuthoritativeProof
    ) -> ExitApproval:
        actions.append("risk_exit")
        return ExitApproval(500, 100, 80.0, 10, True, True)

    async def dispatch_exit(
        req: RotationRequest, approval: ExitApproval
    ) -> DispatchReceipt:
        actions.append("exit")
        state = "UNCERTAIN" if scenario == "exit_uncertain" else "ACKED"
        return DispatchReceipt(req.operation_id + ":exit", state)

    async def reconcile_exit(exchange: str) -> AuthoritativeProof:
        actions.append("reconcile")
        assert exchange == "held"
        signed_after = 430 if scenario == "concurrent_maker_fill" else 440
        new_version = 10 if scenario == "stale_account_version" else 11
        return AuthoritativeProof("held", signed_after, new_version, True, True, 0, 0)

    async def read_attributed_exit(op: str) -> AttributedExitProof:
        actions.append("attribution")
        assert op == "replay-001:exit"
        proof_op = "different:exit" if scenario == "wrong_exit_operation" else op
        return AttributedExitProof(
            proof_op,
            "held",
            -60,
            60,
            scenario != "attribution_not_final",
            True,
        )

    async def prepare_entry(req: RotationRequest, qty: int) -> EntryApproval:
        actions.append("risk_entry")
        gain = 0.0 if scenario == "entry_no_longer_positive" else 12.0
        return EntryApproval(qty, gain, 100.0, 10, True)

    async def dispatch_entry(
        req: RotationRequest, approval: EntryApproval
    ) -> DispatchReceipt:
        actions.append("entry")
        return DispatchReceipt(req.operation_id + ":entry", "ACKED")

    async def execute() -> tuple[RotationOutcome, RotationOutcome]:
        controller = TwoPhaseRotation(
            live_enabled=True,
            max_tranche_shares=500,
            max_entry_notional=500.0,
        )

        async def invoke() -> RotationOutcome:
            return await controller.run(
                request,
                read_before=read_before,
                claim_operation=claim,
                prepare_exit=prepare_exit,
                dispatch_exit=dispatch_exit,
                reconcile_exit=reconcile_exit,
                read_attributed_exit=read_attributed_exit,
                prepare_entry=prepare_entry,
                dispatch_entry=dispatch_entry,
            )

        first = await invoke()
        duplicate = await invoke()
        return first, duplicate

    result, duplicate = asyncio.run(execute())
    assert result.status == expected
    assert duplicate.status == "BLOCKED"
    assert actions.count("exit") == 1
    assert actions.count("claim") == 1
    assert ("entry" in actions) is (scenario == "clean_partial")
    if scenario == "clean_partial":
        assert result.confirmed_reduction_shares == 60
        assert result.entry_shares == 60
    elif scenario == "entry_no_longer_positive":
        assert result.confirmed_reduction_shares == 60
        assert result.entry_shares == 0
