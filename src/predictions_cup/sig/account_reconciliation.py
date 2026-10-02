"""Authoritative account/order recovery for explicit tournament scope."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Protocol

from predictions_cup.runtime.models import (
    RuntimeOrderState,
    RuntimePortfolio,
    RuntimePosition,
)
from predictions_cup.sig.dto import AccountDto
from predictions_cup.sig.trading_dto import (
    OrderReadDto,
    OrderStatusFilter,
    PositionReadDto,
    PositionsResponseDto,
)


class AccountReconciliationRest(Protocol):
    def iter_orders(
        self,
        *,
        status: OrderStatusFilter = "open",
        exchange_id: str | None = None,
        market_id: str | None = None,
        tournament_id: str | None = None,
        limit: int = 200,
    ) -> AsyncIterator[OrderReadDto]: ...

    async def get_tournament_positions(
        self,
        tournament_slug: str,
    ) -> PositionsResponseDto: ...


@dataclass(frozen=True, slots=True)
class AccountAuthoritativeSnapshot:
    tournament_id: str
    tournament_slug: str
    open_orders: tuple[OrderReadDto, ...]
    positions: tuple[PositionReadDto, ...]
    observed_at: datetime
    cash_balance: Decimal | None = None

    def to_runtime_portfolio(self) -> RuntimePortfolio:
        market_by_exchange = {
            position.exchange_id: position.market_id for position in self.positions
        }
        positions = tuple(
            RuntimePosition(
                exchange_id=position.exchange_id,
                market_id=position.market_id,
                tournament_id=self.tournament_id,
                # One currency unit/share is the conservative settlement bound.
                gross_exposure=float(abs(position.quantity)),
                signed_quantity=float(position.quantity),
            )
            for position in self.positions
            if position.quantity != 0
        )
        orders: list[RuntimeOrderState] = []
        for order in self.open_orders:
            market_id = market_by_exchange.get(order.exchange_id, "UNKNOWN")
            orders.append(
                RuntimeOrderState(
                    logical_intent_id=f"sig-order-{order.id}",
                    exchange_id=order.exchange_id,
                    market_id=market_id,
                    tournament_id=self.tournament_id,
                    reserved_exposure=float(abs(order.quantity)),
                    open=order.open,
                    uncertain=False,
                    signed_quantity=_signed_order_quantity(
                        order.side,
                        order.action,
                        order.quantity,
                    ),
                )
            )
        return RuntimePortfolio(
            positions=positions,
            orders=tuple(orders),
            account_trusted=True,
        )


def _signed_order_quantity(side: str, action: str, quantity: Decimal) -> float:
    amount = float(abs(quantity))
    return amount if (side == "yes") == (action == "buy") else -amount


async def reconcile_account(
    rest: AccountReconciliationRest,
    *,
    tournament_id: str,
    tournament_slug: str,
    cash_balance: Decimal | None = None,
    cash_balance_reader: Callable[[], Awaitable[AccountDto]] | None = None,
) -> AccountAuthoritativeSnapshot:
    if not tournament_id.strip() or not tournament_slug.strip():
        raise ValueError("explicit tournament id and slug are required for reconciliation")
    if cash_balance is not None and cash_balance_reader is not None:
        raise ValueError("provide a cash balance or reader, not both")
    # Keep a shared read-start fence. The account runtime also invalidates the
    # whole snapshot if realtime activity arrives while any read is in flight.
    observed_at = datetime.now(UTC)
    open_orders_task = asyncio.create_task(
        _collect_open_orders(rest, tournament_id=tournament_id)
    )
    positions_task = asyncio.create_task(
        rest.get_tournament_positions(tournament_slug)
    )
    cash_balance_task: asyncio.Task[AccountDto] | None = None
    if cash_balance_reader is not None:
        cash_balance_task = asyncio.create_task(
            _read_cash_balance(cash_balance_reader)
        )
    try:
        # Independent endpoint reads can overlap; each HTTP attempt still passes
        # through the shared SIG governor and no partial result is trusted alone.
        if cash_balance_task is None:
            open_orders, positions_response = await asyncio.gather(
                open_orders_task,
                positions_task,
            )
        else:
            open_orders, positions_response, account = await asyncio.gather(
                open_orders_task,
                positions_task,
                cash_balance_task,
            )
            cash_balance = account.balance
    except BaseException:
        for task in (open_orders_task, positions_task):
            if not task.done():
                task.cancel()
        if cash_balance_task is not None and not cash_balance_task.done():
            cash_balance_task.cancel()
        if cash_balance_task is None:
            await asyncio.gather(
                open_orders_task,
                positions_task,
                return_exceptions=True,
            )
        else:
            await asyncio.gather(
                open_orders_task,
                positions_task,
                cash_balance_task,
                return_exceptions=True,
            )
        raise
    return AccountAuthoritativeSnapshot(
        tournament_id=tournament_id,
        tournament_slug=tournament_slug,
        open_orders=open_orders,
        positions=positions_response.positions,
        observed_at=observed_at,
        cash_balance=cash_balance,
    )


async def _collect_open_orders(
    rest: AccountReconciliationRest,
    *,
    tournament_id: str,
) -> tuple[OrderReadDto, ...]:
    orders = [
        order
        async for order in rest.iter_orders(
            status="open",
            tournament_id=tournament_id,
            limit=200,
        )
    ]
    return tuple(orders)


async def _read_cash_balance(
    reader: Callable[[], Awaitable[AccountDto]],
) -> AccountDto:
    return await reader()
