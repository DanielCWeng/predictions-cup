"""Authoritative account/order recovery for explicit tournament scope."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from predictions_cup.runtime import (
    RuntimeOrderState,
    RuntimePortfolio,
    RuntimePosition,
)
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

    def to_runtime_portfolio(self) -> RuntimePortfolio:
        market_by_exchange = {
            position.exchange_id: position.market_id for position in self.positions
        }
        positions = tuple(
            RuntimePosition(
                exchange_id=position.exchange_id,
                market_id=position.market_id,
                # One currency unit/share is the conservative settlement bound.
                gross_exposure=float(abs(position.quantity)),
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
                    reserved_exposure=float(abs(order.quantity)),
                    open=order.open,
                    uncertain=False,
                )
            )
        return RuntimePortfolio(
            positions=positions,
            orders=tuple(orders),
            account_trusted=True,
        )


async def reconcile_account(
    rest: AccountReconciliationRest,
    *,
    tournament_id: str,
    tournament_slug: str,
) -> AccountAuthoritativeSnapshot:
    if not tournament_id.strip() or not tournament_slug.strip():
        raise ValueError("explicit tournament id and slug are required for reconciliation")
    open_orders = tuple(
        [
            order
            async for order in rest.iter_orders(
                status="open",
                tournament_id=tournament_id,
                limit=200,
            )
        ]
    )
    positions_response = await rest.get_tournament_positions(tournament_slug)
    return AccountAuthoritativeSnapshot(
        tournament_id=tournament_id,
        tournament_slug=tournament_slug,
        open_orders=open_orders,
        positions=positions_response.positions,
        observed_at=datetime.now(UTC),
    )
