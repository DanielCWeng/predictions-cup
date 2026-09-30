"""Startup recovery for unresolved LIVE execution journal entries."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from time import monotonic_ns
from typing import Protocol

from predictions_cup.execution.journal import ExecutionJournal
from predictions_cup.execution.live import SigLiveSink
from predictions_cup.execution.models import ExecutionEnvelope, LifecycleState, OperationKind
from predictions_cup.observe.contracts import ObservationEmitter, ObservationKind, VenueObservation
from predictions_cup.runtime.models import RuntimePortfolio
from predictions_cup.sig.account_reconciliation import reconcile_account
from predictions_cup.sig.errors import SigExecutionUncertainError
from predictions_cup.sig.trading_dto import (
    OrderFillsResponseDto,
    OrderReadDto,
    OrderStatusFilter,
    PositionsResponseDto,
)

ClockNs = Callable[[], int]
WallClock = Callable[[], datetime]


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
    wall_clock: WallClock = lambda: datetime.now(UTC),
    observation_emitter: ObservationEmitter | None = None,
    observation_process_instance_id: str | None = None,
) -> StartupRecoveryResult:
    """Recover journal first; LIVE remains blocked if anything stays uncertain."""
    authoritative = await reconcile_account(
        rest,
        tournament_id=tournament_id,
        tournament_slug=tournament_slug,
    )

    for envelope in journal.unresolved():
        original_state = envelope.lifecycle_state
        _emit_recovery_observation(
            observation_emitter,
            observation_process_instance_id,
            wall_clock,
            ObservationKind.RECONCILIATION_STARTED,
            envelope,
            clock_ns(),
            detail=(("original_state", original_state.value),),
        )
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
                    await live_sink.dispatch_recovery(envelope)
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

        if envelope.logical_operation_id not in {
            item.logical_operation_id for item in journal.unresolved()
        }:
            _emit_recovery_observation(
                observation_emitter,
                observation_process_instance_id,
                wall_clock,
                ObservationKind.RECONCILIATION_RESOLVED,
                envelope,
                clock_ns(),
            )

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
        resolved_ns = clock_ns()
        journal.mark_state(
            envelope.logical_operation_id,
            LifecycleState.RECONCILED,
            resolved_ns,
        )
        _emit_recovery_observation(
            observation_emitter,
            observation_process_instance_id,
            wall_clock,
            ObservationKind.RECONCILIATION_RESOLVED,
            envelope,
            resolved_ns,
            detail=(("resolved_state", LifecycleState.RECONCILED.value),),
        )

    unresolved = journal.unresolved()
    return StartupRecoveryResult(
        portfolio=authoritative.to_runtime_portfolio(),
        safe_to_resume_live=not unresolved,
        unresolved_operation_ids=tuple(
            envelope.logical_operation_id for envelope in unresolved
        ),
    )


def _emit_recovery_observation(
    emitter: ObservationEmitter | None,
    process_instance_id: str | None,
    wall_clock: WallClock,
    kind: ObservationKind,
    envelope: ExecutionEnvelope,
    monotonic_ns: int,
    *,
    detail: tuple[tuple[str, str], ...] = (),
) -> None:
    if emitter is None:
        return
    if process_instance_id is None or not process_instance_id.strip():
        raise ValueError(
            "observation_process_instance_id is required when recovery observation is enabled"
        )
    try:
        emitter.emit(
            VenueObservation(
                kind=kind,
                observed_at=wall_clock(),
                monotonic_ns=monotonic_ns,
                process_instance_id=process_instance_id,
                source="BUILD_009_STARTUP_RECOVERY",
                source_version="observe-001",
                provenance="AUTHORITATIVE_RECONCILIATION",
                tournament_id=envelope.tournament_id,
                logical_operation_id=envelope.logical_operation_id,
                idempotency_key=envelope.idempotency_key,
                detail=detail,
            )
        )
    except Exception:
        return


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
    observed = clock_ns()
    for fill in fills.data:
        journal.record_event(
            logical_operation_id=envelope.logical_operation_id,
            event_type="AUTHORITATIVE_FILL",
            observed_monotonic_ns=observed,
            source_timestamp=fill.filled_at.isoformat(),
            exchange_id=fill.exchange_id,
            exchange_order_id=str(order_id),
            fill_id=str(fill.id),
            quantity=str(fill.quantity),
            price=None if fill.price is None else str(fill.price),
        )
    terminal = (
        LifecycleState.FILLED
        if abs(fills.total_quantity_filled) >= abs(order.quantity)
        else LifecycleState.CANCELLED
    )
    journal.record_event(
        logical_operation_id=envelope.logical_operation_id,
        event_type="RECONCILED_TERMINAL",
        observed_monotonic_ns=observed,
        exchange_order_id=str(order_id),
        terminal_status=terminal.value,
    )
    journal.mark_state(envelope.logical_operation_id, terminal, observed)


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
