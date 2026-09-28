"""Startup recovery for unresolved LIVE execution journal entries."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from time import monotonic_ns
from typing import Protocol

from predictions_cup.execution.journal import ExecutionJournal
from predictions_cup.execution.live import SigLiveSink
from predictions_cup.execution.models import ExecutionEnvelope, LifecycleState, OperationKind
from predictions_cup.execution.sinks import ExecutionPlan
from predictions_cup.runtime.models import RuntimePortfolio
from predictions_cup.sig.account_reconciliation import (
    AccountAuthoritativeSnapshot,
    reconcile_account,
)
from predictions_cup.sig.errors import SigExecutionUncertainError
from predictions_cup.sig.trading_dto import (
    OrderFillsResponseDto,
    OrderReadDto,
    OrderStatusFilter,
    PositionsResponseDto,
)

ClockNs = Callable[[], int]


class RecoveryRest(Protocol):
    def iter_orders(
        self,
        *,
        status: OrderStatusFilter = "open",
        exchange_id: str | None = None,
        market_id: str | None = None,
        tournament_id: str | None = None,
        limit: int = 200,
    ) -> AsyncIterator[OrderReadDto]: ...

    async def get_order(self, order_id: int) -> OrderReadDto: ...

    async def get_order_fills(
        self,
        order_id: int,
        *,
        limit: int = 50,
        cursor: str | None = None,
    ) -> OrderFillsResponseDto: ...

    async def get_tournament_positions(
        self,
        tournament_slug: str,
    ) -> PositionsResponseDto: ...


@dataclass(frozen=True, slots=True)
class StartupRecoveryResult:
    portfolio: RuntimePortfolio
    safe_to_resume_live: bool
    unresolved_operation_ids: tuple[str, ...]


async def recover_startup(
    *,
    journal: ExecutionJournal,
    rest: RecoveryRest,
    live_sink: SigLiveSink,
    tournament_id: str,
    tournament_slug: str,
    clock_ns: ClockNs = monotonic_ns,
) -> StartupRecoveryResult:
    """Recover journal first; LIVE remains blocked if anything stays uncertain."""
    authoritative = await reconcile_account(
        rest,
        tournament_id=tournament_id,
        tournament_slug=tournament_slug,
    )

    for envelope in journal.unresolved():
        original_state = envelope.lifecycle_state
        if original_state is not LifecycleState.RECONCILING:
            journal.mark_state(
                envelope.logical_operation_id,
                LifecycleState.RECONCILING,
                clock_ns(),
            )

        try:
            if envelope.operation_kind in {
                OperationKind.SINGLE_PLACEMENT,
                OperationKind.BEST_EFFORT_BATCH,
                OperationKind.ATOMIC_MULTI_LEG,
            }:
                if original_state in {
                    LifecycleState.PENDING,
                    LifecycleState.UNCERTAIN,
                    LifecycleState.RECONCILING,
                }:
                    await live_sink.dispatch(
                        ExecutionPlan(envelope=envelope, intents=())
                    )
            elif envelope.operation_kind is OperationKind.SINGLE_CANCELLATION:
                await _recover_single_cancel(
                    journal=journal,
                    rest=rest,
                    live_sink=live_sink,
                    envelope=envelope,
                    clock_ns=clock_ns,
                )
            elif envelope.operation_kind is OperationKind.CANCEL_ALL:
                if await _cancel_all_scope_has_open_orders(
                    rest=rest,
                    envelope_payload=envelope.payload_json,
                    tournament_id=tournament_id,
                ):
                    await live_sink.cancel(envelope)
                else:
                    journal.mark_state(
                        envelope.logical_operation_id,
                        LifecycleState.CANCELLED,
                        clock_ns(),
                    )
        except SigExecutionUncertainError:
            continue

    authoritative = await reconcile_account(
        rest,
        tournament_id=tournament_id,
        tournament_slug=tournament_slug,
    )

    # Any non-terminal operation whose economics are now covered by the
    # authoritative order/position snapshot is explicitly closed as reconciled.
    for envelope in journal.unresolved():
        if envelope.lifecycle_state in {
            LifecycleState.UNCERTAIN,
            LifecycleState.PENDING,
            LifecycleState.CANCEL_PENDING,
        }:
            continue
        if envelope.lifecycle_state is not LifecycleState.RECONCILING:
            journal.mark_state(
                envelope.logical_operation_id,
                LifecycleState.RECONCILING,
                clock_ns(),
            )
        journal.mark_state(
            envelope.logical_operation_id,
            LifecycleState.RECONCILED,
            clock_ns(),
        )

    unresolved = journal.unresolved()
    return StartupRecoveryResult(
        portfolio=authoritative.to_runtime_portfolio(),
        safe_to_resume_live=not unresolved,
        unresolved_operation_ids=tuple(
            envelope.logical_operation_id for envelope in unresolved
        ),
    )


async def _recover_single_cancel(
    *,
    journal: ExecutionJournal,
    rest: RecoveryRest,
    live_sink: SigLiveSink,
    envelope: ExecutionEnvelope,
    clock_ns: ClockNs,
) -> None:
    raw = json.loads(envelope.payload_json)
    order_id = raw.get("orderId")
    if not isinstance(order_id, int) or order_id <= 0:
        raise RuntimeError("journal contains malformed single-cancel envelope")

    order = await rest.get_order(order_id)
    if order.open:
        await live_sink.cancel(envelope)
        return

    fills = await rest.get_order_fills(order_id, limit=200)
    terminal = (
        LifecycleState.FILLED
        if abs(fills.total_quantity_filled) >= abs(order.quantity)
        else LifecycleState.CANCELLED
    )
    journal.mark_state(envelope.logical_operation_id, terminal, clock_ns())


async def _cancel_all_scope_has_open_orders(
    *,
    rest: RecoveryRest,
    envelope_payload: str,
    tournament_id: str,
) -> bool:
    raw = json.loads(envelope_payload)
    exchange_id = raw.get("exchangeId")
    market_id = raw.get("marketId")
    explicit_tournament = raw.get("tournamentId")
    if explicit_tournament is not None and explicit_tournament != tournament_id:
        raise RuntimeError("cancel-all journal entry targets a different tournament")
    async for _ in rest.iter_orders(
        status="open",
        exchange_id=exchange_id if isinstance(exchange_id, str) else None,
        market_id=market_id if isinstance(market_id, str) else None,
        tournament_id=tournament_id,
        limit=200,
    ):
        return True
    return False
