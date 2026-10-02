from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from decimal import Decimal

from predictions_cup.sig.account_reconciliation import (
    AccountAuthoritativeSnapshot,
    reconcile_account,
)
from predictions_cup.sig.account_runtime import AccountRealtimeController
from predictions_cup.sig.account_state import AccountRealtimeStateEngine
from predictions_cup.sig.dto import AccountDto
from predictions_cup.sig.realtime_models import RealtimeTokenDto
from predictions_cup.sig.trading_dto import (
    OrderReadDto,
    PositionsResponseDto,
)


def _order() -> OrderReadDto:
    return OrderReadDto.model_validate(
        {
            "id": 91,
            "exchangeId": "36",
            "side": "yes",
            "action": "buy",
            "quantity": "2",
            "priceLimit": "0.495",
            "open": True,
            "createdAt": "2026-10-02T09:00:00Z",
            "expirationDate": None,
        }
    )


def _positions(quantity: str) -> PositionsResponseDto:
    return PositionsResponseDto.model_validate(
        {
            "positions": (
                []
                if quantity == "0"
                else [
                    {
                        "exchangeId": "36",
                        "marketId": "m1",
                        "marketTitle": "Synthetic market",
                        "option": "yes",
                        "settled": False,
                        "quantity": quantity,
                        "avgCost": "0.4",
                        "currentPrice": "0.5",
                        "marketValue": "0.5",
                        "costBasis": "0.4",
                        "unrealizedPnl": "0.1",
                        "unrealizedPnlPct": "0.25",
                        "moneyEarned": "0",
                        "lots": [],
                    }
                ]
            ),
            "summary": {
                "totalMarketValue": "0.5" if quantity != "0" else "0",
                "totalCostBasis": "0.4" if quantity != "0" else "0",
                "totalUnrealizedPnl": "0.1" if quantity != "0" else "0",
            },
        }
    )


def _token() -> RealtimeTokenDto:
    return RealtimeTokenDto.model_validate(
        {
            "token": "synthetic",
            "expiresAt": "2026-10-02T10:00:00Z",
            "supabaseUrl": "https://example.supabase.co",
            "anonKey": "synthetic",
            "channels": {"user": "user:synthetic"},
        }
    )


class _ConcurrentSnapshotRest:
    def __init__(self) -> None:
        self.snapshot_number = 0
        self.account_started = asyncio.Event()
        self.order_started = asyncio.Event()
        self.positions_started = asyncio.Event()
        self.release_reads = asyncio.Event()
        self.read_started_at: list[datetime] = []

    async def get_account(self) -> AccountDto:
        attempt = self.snapshot_number
        self.read_started_at.append(datetime.now(UTC))
        self.account_started.set()
        await self.release_reads.wait()
        balance = "1000.00" if attempt == 1 else "1000.25"
        return AccountDto.model_validate(
            {
                "id": "profile-1",
                "username": "synthetic",
                "email": None,
                "createdAt": "2026-10-02T09:00:00Z",
                "avatarUrl": None,
                "bio": None,
                "balance": balance,
            }
        )

    async def iter_orders(
        self,
        *,
        status: str = "open",
        exchange_id: str | None = None,
        market_id: str | None = None,
        tournament_id: str | None = None,
        limit: int = 200,
    ) -> AsyncIterator[OrderReadDto]:
        assert status == "open"
        assert exchange_id is None
        assert market_id is None
        assert tournament_id == "t1"
        assert limit == 200
        attempt = self.snapshot_number
        self.read_started_at.append(datetime.now(UTC))
        self.order_started.set()
        await self.release_reads.wait()
        if attempt == 1:
            yield _order()

    async def get_tournament_positions(
        self,
        tournament_slug: str,
    ) -> PositionsResponseDto:
        assert tournament_slug == "cup"
        attempt = self.snapshot_number
        self.read_started_at.append(datetime.now(UTC))
        self.positions_started.set()
        await self.release_reads.wait()
        # The first pair predates a fill. The retry sees the post-fill position
        # and no longer sees the now-closed order.
        return _positions("0" if attempt == 1 else "1")


def test_parallel_account_reads_share_fence_and_retry_after_concurrent_fill() -> None:
    async def scenario() -> None:
        rest = _ConcurrentSnapshotRest()
        state = AccountRealtimeStateEngine(tournament_id="t1")
        snapshots: list[AccountAuthoritativeSnapshot] = []
        resync_calls = 0

        async def mint_token() -> RealtimeTokenDto:
            return _token()

        async def authoritative_resync() -> AccountAuthoritativeSnapshot:
            nonlocal resync_calls
            resync_calls += 1
            rest.snapshot_number = resync_calls
            snapshot = await reconcile_account(
                rest,
                tournament_id="t1",
                tournament_slug="cup",
                cash_balance_reader=rest.get_account,
            )
            snapshots.append(snapshot)
            return snapshot

        controller = AccountRealtimeController(
            state=state,
            mint_token=mint_token,
            authoritative_resync=authoritative_resync,
        )
        refresh = asyncio.create_task(
            controller._refresh_authoritative(datetime.now(UTC))
        )
        await asyncio.wait_for(
            asyncio.gather(
                rest.account_started.wait(),
                rest.order_started.wait(),
                rest.positions_started.wait(),
            ),
            timeout=1,
        )

        # Both endpoint reads are in flight when a fill arrives. The first
        # cross-snapshot view must be discarded and the whole account retried.
        await controller._handle_batch(
            "unused",
            {
                "fills": [{"orderId": 91}],
                "orderUpdates": [],
                "settlements": [],
                "refunds": [],
                "collateralChanges": [],
            },
            datetime.now(UTC),
        )
        rest.release_reads.set()
        await asyncio.wait_for(refresh, timeout=1)

        assert resync_calls == 2
        assert len(snapshots) == 2
        assert snapshots[0].observed_at <= min(rest.read_started_at[:3])
        assert snapshots[1].cash_balance == Decimal("1000.25")
        assert state.trusted
        portfolio = state.runtime_portfolio()
        assert portfolio.orders == ()
        assert [
            (position.exchange_id, position.signed_quantity)
            for position in portfolio.positions
        ] == [("36", 1.0)]

    asyncio.run(scenario())
