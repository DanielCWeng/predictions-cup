"""Decision-cycle quiescence tests for trusted account reconciliation.

Never permit a new quote/taker decision to straddle the authoritative account
snapshot; retain queued work until the snapshot releases.
"""

from __future__ import annotations

import asyncio
from types import MethodType

from predictions_cup.maker.runtime_loop import MakerRuntimeLoop


def test_account_snapshot_waits_for_inflight_cycle_then_blocks_new_cycle() -> None:
    async def case() -> None:
        runtime = object.__new__(MakerRuntimeLoop)
        runtime._account_snapshot_gate = asyncio.Lock()
        dispatched: list[str] = []
        entered_cycle = asyncio.Event()
        release_cycle = asyncio.Event()

        async def decision_cycle(self: MakerRuntimeLoop) -> None:
            dispatched.append("before-snapshot")
            entered_cycle.set()
            await release_cycle.wait()

        runtime._drain_once_under_gate = MethodType(decision_cycle, runtime)
        live_cycle = asyncio.create_task(runtime._drain_once())
        await entered_cycle.wait()

        holding = asyncio.Event()
        release_snapshot = asyncio.Event()

        async def reconcile() -> None:
            async with runtime.quiet_for_account_snapshot():
                holding.set()
                await release_snapshot.wait()

        snapshot = asyncio.create_task(reconcile())
        await asyncio.sleep(0)
        assert not holding.is_set()  # Must wait for existing venue work.

        release_cycle.set()
        await live_cycle
        await asyncio.wait_for(holding.wait(), timeout=1)

        after_snapshot = asyncio.create_task(runtime._drain_once())
        await asyncio.sleep(0)
        assert dispatched == ["before-snapshot"]
        assert not after_snapshot.done()

        release_snapshot.set()
        await asyncio.wait_for(snapshot, timeout=1)
        await asyncio.wait_for(after_snapshot, timeout=1)
        assert dispatched == ["before-snapshot", "before-snapshot"]

    asyncio.run(case())


def test_snapshot_context_releases_gate_after_reconciliation_error() -> None:
    async def case() -> None:
        runtime = object.__new__(MakerRuntimeLoop)
        runtime._account_snapshot_gate = asyncio.Lock()
        try:
            async with runtime.quiet_for_account_snapshot():
                raise RuntimeError("issuer unavailable")
        except RuntimeError:
            pass
        assert not runtime._account_snapshot_gate.locked()
        async with runtime.quiet_for_account_snapshot():
            assert runtime._account_snapshot_gate.locked()

    asyncio.run(case())
