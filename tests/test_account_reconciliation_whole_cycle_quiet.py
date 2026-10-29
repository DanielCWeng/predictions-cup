"""Keep maker/taker dispatch quiescent through every account retry and commit."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from predictions_cup.sig.account_reconciliation import AccountAuthoritativeSnapshot
from predictions_cup.sig.account_runtime import AccountRealtimeController
from predictions_cup.sig.account_state import AccountRealtimeStateEngine
from predictions_cup.sig.realtime_models import RealtimeTokenDto


def test_entire_periodic_refresh_is_one_quiet_window() -> None:
    async def scenario() -> None:
        events: list[str] = []
        state = AccountRealtimeStateEngine(tournament_id="t1")
        controller: AccountRealtimeController
        calls = 0

        @asynccontextmanager
        async def quiet() -> AsyncIterator[None]:
            events.append("pause")
            try:
                yield
            finally:
                events.append("resume")

        async def mint() -> RealtimeTokenDto:
            raise AssertionError("token not required for periodic refresh")

        async def authoritative() -> AccountAuthoritativeSnapshot:
            nonlocal calls
            calls += 1
            events.append(f"read{calls}")
            if calls == 1:
                # Simulate local or socket activity mid-snapshot. The next
                # retry must execute before the maker resumes dispatch.
                controller._resync_generation += 1
            return AccountAuthoritativeSnapshot(
                tournament_id="t1",
                tournament_slug="cup",
                positions=(),
                open_orders=(),
                observed_at=datetime.now(UTC),
            )

        controller = AccountRealtimeController(
            state=state,
            mint_token=mint,
            authoritative_resync=authoritative,
            reconciliation_quiescence=quiet,
        )
        await controller._refresh_authoritative(datetime.now(UTC))
        assert events == ["pause", "read1", "read2", "resume"]
        assert state.trusted

    asyncio.run(scenario())


def test_subscription_restore_also_holds_quiet_through_trust_commit() -> None:
    async def scenario() -> None:
        events: list[str] = []
        state = AccountRealtimeStateEngine(tournament_id="t1")

        @asynccontextmanager
        async def quiet() -> AsyncIterator[None]:
            events.append("pause")
            try:
                yield
            finally:
                events.append("resume")

        async def mint() -> RealtimeTokenDto:
            raise AssertionError("not called")

        async def authoritative() -> AccountAuthoritativeSnapshot:
            events.append("read")
            return AccountAuthoritativeSnapshot(
                tournament_id="t1",
                tournament_slug="cup",
                positions=(),
                open_orders=(),
                observed_at=datetime.now(UTC),
            )

        controller = AccountRealtimeController(
            state=state,
            mint_token=mint,
            authoritative_resync=authoritative,
            reconciliation_quiescence=quiet,
        )
        await controller._restore_trust_while_subscribed()
        assert events == ["pause", "read", "resume"]
        assert state.trusted

    asyncio.run(scenario())
