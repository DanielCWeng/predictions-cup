"""Recover one unresolved LIVE execution operation (ops tool).

Placement recovery replays only the durable placement payload with its existing
idempotency key. Cancellation recovery is read-only: it never sends a placement
or cancellation request, and closes the journal operation only after SIG's
authoritative order and fill reads confirm a terminal outcome.
"""

from __future__ import annotations

import asyncio
import json
import sys
from dataclasses import dataclass
from decimal import Decimal
from time import monotonic_ns
from typing import Protocol

from predictions_cup.config import AppSettings
from predictions_cup.execution.interlocks import assert_live_recovery_interlocks
from predictions_cup.execution.journal import ExecutionJournal
from predictions_cup.execution.live import SigLiveSink
from predictions_cup.execution.models import (
    ExecutionEnvelope,
    ExecutionEvent,
    LifecycleState,
    OperationKind,
)
from predictions_cup.execution.recovery import (
    RecoveryRest,
    read_complete_fills_with_fallback,
    read_order_with_fallback,
)
from predictions_cup.execution.reservations import ExecutionReservationBook
from predictions_cup.sig.governed_client import GovernedSigRestClient
from predictions_cup.sig.rest_governor import SigRestGovernor
from predictions_cup.sig.trading_client import SigTradingClient
from predictions_cup.sig.trading_dto import OrderFillItemDto


class PlacementRecoverySink(Protocol):
    async def dispatch_recovery(self, envelope: ExecutionEnvelope) -> object: ...


@dataclass(frozen=True, slots=True)
class RecoveryResult:
    resolved: bool
    detail: str


_PLACEMENT_KINDS = frozenset(
    {
        OperationKind.SINGLE_PLACEMENT,
        OperationKind.BEST_EFFORT_BATCH,
        OperationKind.ATOMIC_MULTI_LEG,
    }
)
_REPLAYABLE_PLACEMENT_STATES = frozenset(
    {
        LifecycleState.PENDING,
        LifecycleState.UNCERTAIN,
        LifecycleState.RECONCILING,
    }
)


async def recover_operation(
    *,
    envelope: ExecutionEnvelope,
    journal: ExecutionJournal,
    rest: RecoveryRest,
    placement_sink: PlacementRecoverySink | None = None,
) -> RecoveryResult:
    """Recover one envelope, keeping all cancellation paths read-only."""
    durable = next(
        (
            candidate
            for candidate in journal.unresolved()
            if candidate.logical_operation_id == envelope.logical_operation_id
        ),
        None,
    )
    if durable != envelope:
        raise ValueError("recovery requires the matching unresolved journal envelope")

    if envelope.operation_kind is OperationKind.SINGLE_CANCELLATION:
        return await _recover_single_cancel(envelope, journal, rest)
    if envelope.operation_kind is OperationKind.CANCEL_ALL:
        return await _recover_cancel_all(envelope, journal, rest)

    # CANCEL_PENDING is a cancellation outcome even when the durable envelope
    # originated as a best-effort placement batch. Never replay that placement.
    if (
        envelope.operation_kind is OperationKind.BEST_EFFORT_BATCH
        and envelope.lifecycle_state is LifecycleState.CANCEL_PENDING
    ):
        return await _recover_cancel_pending_batch(envelope, journal, rest)

    if envelope.operation_kind not in _PLACEMENT_KINDS:
        raise ValueError(f"unsupported operation kind: {envelope.operation_kind.value}")
    acknowledgements = _acknowledged_orders(envelope, journal)
    if acknowledgements:
        return await _recover_acknowledged_placement(
            envelope,
            journal,
            rest,
            acknowledgements,
        )
    if envelope.lifecycle_state not in _REPLAYABLE_PLACEMENT_STATES:
        return RecoveryResult(
            resolved=False,
            detail=(f"placement replay held for lifecycle state {envelope.lifecycle_state.value}"),
        )
    if not envelope.idempotency_key:
        raise ValueError("placement recovery requires its durable idempotency key")
    if placement_sink is None:
        raise ValueError("placement recovery requires a LIVE recovery sink")

    await placement_sink.dispatch_recovery(envelope)
    unresolved = any(
        item.logical_operation_id == envelope.logical_operation_id for item in journal.unresolved()
    )
    return RecoveryResult(
        resolved=not unresolved,
        detail=(
            "placement replay used the existing idempotency key; "
            + ("operation remains unresolved" if unresolved else "operation resolved")
        ),
    )


async def _recover_single_cancel(
    envelope: ExecutionEnvelope,
    journal: ExecutionJournal,
    rest: RecoveryRest,
) -> RecoveryResult:
    raw = json.loads(envelope.payload_json)
    order_id = raw.get("orderId")
    if not isinstance(order_id, int) or order_id <= 0:
        raise RuntimeError("journal contains malformed single-cancel envelope")

    _mark_reconciling(envelope, journal)
    order = await read_order_with_fallback(
        rest,
        order_id,
        tournament_id=envelope.tournament_id,
    )
    if order is None:
        return RecoveryResult(
            resolved=False,
            detail=f"order {order_id} is absent from direct and list order reads",
        )
    if order.open:
        return RecoveryResult(
            resolved=False,
            detail=f"authoritative order {order_id} is still open; cancellation unresolved",
        )

    fills = await read_complete_fills_with_fallback(
        rest,
        order_id,
        tournament_id=envelope.tournament_id,
        exchange_id=order.exchange_id,
    )
    if fills is None:
        return RecoveryResult(
            resolved=False,
            detail=f"authoritative fill evidence for order {order_id} is incomplete",
        )
    fill_rows, total_quantity_filled = fills
    placement = journal.placement_identity_for_exchange_order_id(str(order_id))
    fill_operation_id = envelope.logical_operation_id if placement is None else placement[0]
    fill_intent_id = None if placement is None else placement[1]
    _record_authoritative_fills(
        journal,
        operation_id=fill_operation_id,
        intent_id=fill_intent_id,
        order_id=order_id,
        exchange_id=order.exchange_id,
        fills=fill_rows,
    )
    terminal = (
        LifecycleState.FILLED
        if abs(total_quantity_filled) >= abs(order.quantity)
        else LifecycleState.CANCELLED
    )
    _record_terminal(journal, envelope, str(order_id), terminal)
    return RecoveryResult(
        resolved=True,
        detail=f"order {order_id} authoritatively {terminal.value}",
    )


async def _recover_cancel_all(
    envelope: ExecutionEnvelope,
    journal: ExecutionJournal,
    rest: RecoveryRest,
) -> RecoveryResult:
    raw = json.loads(envelope.payload_json)
    explicit_tournament = raw.get("tournamentId")
    if explicit_tournament is not None and explicit_tournament != envelope.tournament_id:
        raise RuntimeError("cancel-all journal entry targets a different tournament")
    exchange_id = raw.get("exchangeId")
    market_id = raw.get("marketId")
    _mark_reconciling(envelope, journal)
    async for order in rest.iter_orders(
        status="open",
        exchange_id=exchange_id if isinstance(exchange_id, str) else None,
        market_id=market_id if isinstance(market_id, str) else None,
        tournament_id=envelope.tournament_id,
        limit=200,
    ):
        return RecoveryResult(
            resolved=False,
            detail=f"authoritative open order {order.id} remains in cancel-all scope",
        )

    _record_terminal(journal, envelope, None, LifecycleState.CANCELLED)
    return RecoveryResult(
        resolved=True,
        detail="no authoritative open orders remain in cancel-all scope",
    )


async def _recover_cancel_pending_batch(
    envelope: ExecutionEnvelope,
    journal: ExecutionJournal,
    rest: RecoveryRest,
) -> RecoveryResult:
    acknowledgements = tuple(
        event
        for event in journal.events(envelope.logical_operation_id)
        if event.event_type == "ACK"
    )
    if not acknowledgements or any(
        event.exchange_order_id is None or not event.exchange_order_id.isdigit()
        for event in acknowledgements
    ):
        return RecoveryResult(
            resolved=False,
            detail="batch cancellation lacks complete acknowledged order IDs; held unresolved",
        )

    order_ids = tuple(
        dict.fromkeys(
            int(event.exchange_order_id)
            for event in acknowledgements
            if event.exchange_order_id is not None
        )
    )
    _mark_reconciling(envelope, journal)
    all_filled = True
    for order_id in order_ids:
        acknowledgement = next(
            event
            for event in acknowledgements
            if event.exchange_order_id == str(order_id)
        )
        order = await read_order_with_fallback(
            rest,
            order_id,
            tournament_id=envelope.tournament_id,
            exchange_id=acknowledgement.exchange_id,
        )
        if order is None:
            return RecoveryResult(
                resolved=False,
                detail=f"order {order_id} is absent from direct and list order reads",
            )
        if order.open:
            return RecoveryResult(
                resolved=False,
                detail=(
                    f"authoritative batch order {order_id} is still open; cancellation unresolved"
                ),
            )
        fills = await read_complete_fills_with_fallback(
            rest,
            order_id,
            tournament_id=envelope.tournament_id,
            exchange_id=order.exchange_id,
        )
        if fills is None:
            return RecoveryResult(
                resolved=False,
                detail=f"authoritative fill evidence for batch order {order_id} is incomplete",
            )
        fill_rows, total_quantity_filled = fills
        intent_ids = {
            event.exchange_order_id: event.logical_intent_id for event in acknowledgements
        }
        _record_authoritative_fills(
            journal,
            operation_id=envelope.logical_operation_id,
            intent_id=intent_ids.get(str(order_id)),
            order_id=order_id,
            exchange_id=order.exchange_id,
            fills=fill_rows,
        )
        all_filled = all_filled and abs(total_quantity_filled) >= abs(order.quantity)

    terminal = LifecycleState.FILLED if all_filled else LifecycleState.CANCELLED
    _record_terminal(journal, envelope, None, terminal)
    return RecoveryResult(
        resolved=True,
        detail=f"all {len(order_ids)} acknowledged batch orders authoritatively {terminal.value}",
    )


def _acknowledged_orders(
    envelope: ExecutionEnvelope,
    journal: ExecutionJournal,
) -> tuple[ExecutionEvent, ...]:
    return tuple(
        event
        for event in journal.events(envelope.logical_operation_id)
        if event.event_type == "ACK"
        and event.exchange_order_id is not None
        and event.exchange_order_id.isdigit()
        and int(event.exchange_order_id) > 0
    )


async def _recover_acknowledged_placement(
    envelope: ExecutionEnvelope,
    journal: ExecutionJournal,
    rest: RecoveryRest,
    acknowledgements: tuple[ExecutionEvent, ...],
) -> RecoveryResult:
    """Never replay an acknowledged placement; reconcile its durable order IDs."""
    _mark_reconciling(envelope, journal)
    unique = {
        int(event.exchange_order_id): event
        for event in acknowledgements
        if event.exchange_order_id is not None
    }
    if not unique:
        return RecoveryResult(resolved=False, detail="placement has no usable ACK order IDs")
    all_filled = True
    for order_id, acknowledgement in sorted(unique.items()):
        order = await read_order_with_fallback(
            rest,
            order_id,
            tournament_id=envelope.tournament_id,
            exchange_id=acknowledgement.exchange_id,
        )
        if order is None:
            return RecoveryResult(
                resolved=False,
                detail=f"order {order_id} is absent from direct and list order reads",
            )
        if order.open:
            return RecoveryResult(
                resolved=False,
                detail=(
                    f"authoritative order {order_id} is still open; "
                    "placement remains unresolved"
                ),
            )
        fills = await read_complete_fills_with_fallback(
            rest,
            order_id,
            tournament_id=envelope.tournament_id,
            exchange_id=order.exchange_id,
        )
        if fills is None:
            return RecoveryResult(
                resolved=False,
                detail=f"authoritative fill evidence for order {order_id} is incomplete",
            )
        fill_rows, total_quantity_filled = fills
        _record_authoritative_fills(
            journal,
            operation_id=envelope.logical_operation_id,
            intent_id=acknowledgement.logical_intent_id,
            order_id=order_id,
            exchange_id=order.exchange_id,
            fills=fill_rows,
        )
        all_filled = all_filled and abs(total_quantity_filled) >= abs(order.quantity)

    terminal = LifecycleState.FILLED if all_filled else LifecycleState.CANCELLED
    _record_terminal(journal, envelope, None, terminal)
    return RecoveryResult(
        resolved=True,
        detail=f"all {len(unique)} acknowledged placement orders authoritatively {terminal.value}",
    )


async def _read_complete_fills(
    rest: RecoveryRest,
    order_id: int,
) -> tuple[tuple[OrderFillItemDto, ...], Decimal] | None:
    fills: list[OrderFillItemDto] = []
    cursor: str | None = None
    total_quantity_filled: Decimal | None = None
    while True:
        page = await rest.get_order_fills(order_id, limit=200, cursor=cursor)
        if not page.coverage.complete:
            return None
        if total_quantity_filled is None:
            total_quantity_filled = page.total_quantity_filled
        elif total_quantity_filled != page.total_quantity_filled:
            return None
        fills.extend(page.data)
        if not page.pagination.has_more:
            if total_quantity_filled is None:
                return None
            return tuple(fills), total_quantity_filled
        next_cursor = page.pagination.next_cursor
        if next_cursor is None or next_cursor == cursor:
            return None
        cursor = next_cursor


def _record_authoritative_fills(
    journal: ExecutionJournal,
    *,
    operation_id: str,
    intent_id: str | None,
    order_id: int,
    exchange_id: str,
    fills: tuple[OrderFillItemDto, ...],
) -> None:
    existing_fill_ids = {
        event.fill_id
        for event in journal.events(operation_id)
        if event.event_type == "AUTHORITATIVE_FILL" and event.fill_id is not None
    }
    for fill in fills:
        fill_id = str(fill.id)
        if fill_id in existing_fill_ids:
            continue
        journal.record_event(
            logical_operation_id=operation_id,
            logical_intent_id=intent_id,
            event_type="AUTHORITATIVE_FILL",
            observed_monotonic_ns=monotonic_ns(),
            source_timestamp=fill.filled_at.isoformat(),
            exchange_id=fill.exchange_id or exchange_id,
            exchange_order_id=str(order_id),
            fill_id=fill_id,
            quantity=str(fill.quantity),
            price=None if fill.price is None else str(fill.price),
        )
        existing_fill_ids.add(fill_id)


def _record_terminal(
    journal: ExecutionJournal,
    envelope: ExecutionEnvelope,
    order_id: str | None,
    terminal: LifecycleState,
) -> None:
    journal.record_event(
        logical_operation_id=envelope.logical_operation_id,
        event_type="RECONCILED_TERMINAL",
        observed_monotonic_ns=monotonic_ns(),
        exchange_order_id=order_id,
        terminal_status=terminal.value,
    )
    journal.mark_state(envelope.logical_operation_id, terminal, monotonic_ns())


def _mark_reconciling(
    envelope: ExecutionEnvelope,
    journal: ExecutionJournal,
) -> None:
    if envelope.lifecycle_state is not LifecycleState.RECONCILING:
        journal.mark_state(
            envelope.logical_operation_id,
            LifecycleState.RECONCILING,
            monotonic_ns(),
        )


async def _run(operation_id: str) -> None:
    settings = AppSettings()
    governor = SigRestGovernor(
        rate_per_second=min(settings.sig_rest_governor_rate_per_second, 1.0),
        max_shared_cooldown_seconds=settings.sig_rest_shared_cooldown_max_seconds,
    )
    journal = ExecutionJournal(settings.execution_journal_path)
    try:
        envelope = next(
            (item for item in journal.unresolved() if item.logical_operation_id == operation_id),
            None,
        )
        if envelope is None:
            raise KeyError(f"unresolved operation not found: {operation_id}")

        async with GovernedSigRestClient(settings, governor=governor) as rest:
            placement_sink: SigLiveSink | None = None
            if envelope.operation_kind in _PLACEMENT_KINDS and not (
                envelope.operation_kind is OperationKind.BEST_EFFORT_BATCH
                and envelope.lifecycle_state is LifecycleState.CANCEL_PENDING
            ):
                permit = assert_live_recovery_interlocks(
                    settings,
                    explicit_live_invocation=True,
                    account_trusted=True,
                )
                async with SigTradingClient(settings, governor=governor) as trading_client:
                    placement_sink = SigLiveSink(
                        client=trading_client,
                        journal=journal,
                        permit=permit,
                        reservations=ExecutionReservationBook(),
                    )
                    result = await recover_operation(
                        envelope=envelope,
                        journal=journal,
                        rest=rest,
                        placement_sink=placement_sink,
                    )
            else:
                result = await recover_operation(
                    envelope=envelope,
                    journal=journal,
                    rest=rest,
                    placement_sink=placement_sink,
                )

        print("recovery", operation_id, result.detail)
        print(
            "still unresolved",
            [item.logical_operation_id for item in journal.unresolved()],
        )
    finally:
        journal.close()


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: recover_one_operation.py <logical_operation_id>")
    asyncio.run(_run(sys.argv[1]))


if __name__ == "__main__":
    main()
