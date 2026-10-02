"""Startup recovery for unresolved LIVE execution journal entries."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from time import monotonic_ns
from typing import Protocol

from predictions_cup.execution.journal import ExecutionJournal, ExecutionJournalEvent
from predictions_cup.execution.live import SigLiveSink
from predictions_cup.execution.models import (
    ExecutionEnvelope,
    ExecutionEvent,
    LifecycleState,
    OperationKind,
    RuntimeOrderIntent,
)
from predictions_cup.execution.reservations import ExecutionReservationBook
from predictions_cup.observe.contracts import ObservationEmitter, ObservationKind, VenueObservation
from predictions_cup.runtime.models import OrderAction, OutcomeSide, RuntimePortfolio
from predictions_cup.sig.account_reconciliation import (
    AccountAuthoritativeSnapshot,
    reconcile_account,
)
from predictions_cup.sig.errors import (
    SigApiError,
    SigExecutionUncertainError,
    SigRateLimitError,
    SigTemporaryServiceError,
    SigTransportError,
)
from predictions_cup.sig.trading_dto import (
    FillReadDto,
    OrderFillItemDto,
    OrderFillsResponseDto,
    OrderReadDto,
    OrderStatusFilter,
    PortfolioFillPageDto,
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

    async def list_portfolio_fills(
        self,
        *,
        exchange_id: str | None = None,
        market_id: str | None = None,
        tournament_id: str | None = None,
        limit: int = 50,
        cursor: str | None = None,
    ) -> PortfolioFillPageDto: ...

    async def get_tournament_positions(
        self,
        tournament_slug: str,
    ) -> PositionsResponseDto: ...


@dataclass(frozen=True, slots=True)
class StartupRecoveryResult:
    portfolio: RuntimePortfolio
    safe_to_resume_live: bool
    unresolved_operation_ids: tuple[str, ...]
    unresolved_exchange_ids: tuple[str, ...] = ()


_RECOVERY_MAX_ATTEMPTS = 3
_RECOVERY_BACKOFF_SECONDS = (1.0, 2.0)


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
    operation_ids: frozenset[str] | None = None,
    authoritative_snapshot: AccountAuthoritativeSnapshot | None = None,
    exchange_ids_by_market: dict[str, tuple[str, ...]] | None = None,
    reservations: ExecutionReservationBook | None = None,
    market_by_exchange: dict[str, str] | None = None,
) -> StartupRecoveryResult:
    """Recover journal-owned order state without taking down unrelated markets.

    SIG reads/writes that fail transiently are retried a bounded number of times.
    Any operation still unresolved is returned with the exchanges whose new
    exposure must remain blocked by the caller.
    """
    if authoritative_snapshot is None:
        try:
            authoritative_snapshot = await _retry_recovery_sig_operation(
                "startup_account_reconciliation",
                lambda: reconcile_account(
                    rest,
                    tournament_id=tournament_id,
                    tournament_slug=tournament_slug,
                ),
            )
        except SigApiError:
            # The caller may already have a trusted startup snapshot and will
            # perform its own account reconciliation before admission.
            authoritative_snapshot = None

    candidates = journal.unresolved()
    if operation_ids is not None:
        candidates = tuple(
            envelope
            for envelope in candidates
            if envelope.logical_operation_id in operation_ids
        )
    for envelope in candidates:
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
        placement_kind = envelope.operation_kind in {
            OperationKind.SINGLE_PLACEMENT,
            OperationKind.BEST_EFFORT_BATCH,
            OperationKind.ATOMIC_MULTI_LEG,
        }
        replayable_state = original_state in {
            LifecycleState.PENDING,
            LifecycleState.UNCERTAIN,
            LifecycleState.RECONCILING,
        }
        try:
            if placement_kind:
                async def recover_placement(
                    current_envelope: ExecutionEnvelope = envelope,
                    current_replayable_state: bool = replayable_state,
                ) -> bool:
                    return await _recover_startup_placement(
                        journal=journal,
                        rest=rest,
                        live_sink=live_sink,
                        envelope=current_envelope,
                        replayable_state=current_replayable_state,
                        clock_ns=clock_ns,
                        wall_clock=wall_clock,
                    )

                await _retry_recovery_sig_operation(
                    f"placement_recovery:{envelope.logical_operation_id}",
                    recover_placement,
                )
            elif envelope.operation_kind is OperationKind.SINGLE_CANCELLATION:
                async def recover_cancellation(
                    current_envelope: ExecutionEnvelope = envelope,
                ) -> None:
                    await _recover_single_cancel(
                        journal=journal,
                        rest=rest,
                        live_sink=live_sink,
                        envelope=current_envelope,
                        clock_ns=clock_ns,
                    )

                await _retry_recovery_sig_operation(
                    f"cancellation_reconciliation:{envelope.logical_operation_id}",
                    recover_cancellation,
                )
            elif envelope.operation_kind is OperationKind.CANCEL_ALL:
                async def recover_cancel_all(
                    current_envelope: ExecutionEnvelope = envelope,
                ) -> None:
                    await _recover_startup_cancel_all(
                        journal=journal,
                        rest=rest,
                        live_sink=live_sink,
                        envelope=current_envelope,
                        tournament_id=tournament_id,
                        clock_ns=clock_ns,
                    )

                await _retry_recovery_sig_operation(
                    f"cancel_all_reconciliation:{envelope.logical_operation_id}",
                    recover_cancel_all,
                )
        except SigExecutionUncertainError:
            continue
        except SigApiError:
            # Exhausted bounded transient retries or a non-transient venue
            # error. Keep this operation unresolved and let its exchange stay
            # fail-closed while the rest of MAKE starts.
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

    with suppress(SigApiError):
        authoritative_snapshot = await _retry_recovery_sig_operation(
            "post_recovery_account_reconciliation",
            lambda: reconcile_account(
                rest,
                tournament_id=tournament_id,
                tournament_slug=tournament_slug,
            ),
        )
    # Preserve the first complete snapshot if the follow-up read is temporarily
    # unavailable; the caller still receives unresolved exchange IDs.

    unresolved = journal.unresolved()
    scoped_unresolved = (
        unresolved
        if operation_ids is None
        else tuple(
            envelope
            for envelope in unresolved
            if envelope.logical_operation_id in operation_ids
        )
    )
    unresolved_ids = {item.logical_operation_id for item in unresolved}
    if reservations is not None:
        for envelope in candidates:
            if envelope.logical_operation_id not in unresolved_ids:
                reservations.release_operation(envelope.logical_operation_id)
        _reserve_unresolved_placements(
            journal,
            scoped_unresolved,
            reservations=reservations,
            market_by_exchange=market_by_exchange or {},
        )
    if authoritative_snapshot is None:
        portfolio = RuntimePortfolio(account_trusted=False)
    else:
        portfolio = authoritative_snapshot.to_runtime_portfolio()
    return StartupRecoveryResult(
        portfolio=portfolio,
        safe_to_resume_live=not scoped_unresolved,
        unresolved_operation_ids=tuple(
            envelope.logical_operation_id for envelope in scoped_unresolved
        ),
        unresolved_exchange_ids=_unresolved_exchange_ids(
            journal,
            scoped_unresolved,
            exchange_ids_by_market=exchange_ids_by_market or {},
        ),
    )


def _reserve_unresolved_placements(
    journal: ExecutionJournal,
    unresolved: tuple[ExecutionEnvelope, ...],
    *,
    reservations: ExecutionReservationBook,
    market_by_exchange: dict[str, str],
) -> None:
    for envelope in unresolved:
        if envelope.operation_kind not in {
            OperationKind.SINGLE_PLACEMENT,
            OperationKind.BEST_EFFORT_BATCH,
            OperationKind.ATOMIC_MULTI_LEG,
        }:
            continue
        try:
            payload = json.loads(envelope.payload_json)
            if not isinstance(payload, dict):
                continue
            raw_legs = payload.get("orders", payload.get("legs", [payload]))
            if not isinstance(raw_legs, list) or len(raw_legs) != len(envelope.intent_ids):
                continue
            submissions = {
                event.logical_intent_id: event
                for event in journal.events(envelope.logical_operation_id)
                if event.event_type == "SUBMISSION" and event.logical_intent_id is not None
            }
            intents: list[RuntimeOrderIntent] = []
            for intent_id, leg in zip(envelope.intent_ids, raw_legs, strict=True):
                if not isinstance(leg, dict):
                    raise ValueError("placement leg is not an object")
                exchange_id = leg.get("exchangeId")
                market_id = market_by_exchange.get(str(exchange_id))
                side = leg.get("side")
                action = leg.get("action")
                quantity = leg.get("quantity")
                if (
                    not isinstance(exchange_id, str)
                    or market_id is None
                    or side not in {item.value for item in OutcomeSide}
                    or action not in {item.value for item in OrderAction}
                    or not isinstance(quantity, int)
                    or quantity <= 0
                ):
                    raise ValueError("placement leg identity is incomplete")
                submission = submissions.get(intent_id)
                strategy_id = "unknown-unresolved-operation"
                decision_observation_ns = 0
                if submission is not None:
                    if submission.strategy_id is not None:
                        strategy_id = submission.strategy_id
                    if submission.decision_observation_ns is not None:
                        decision_observation_ns = submission.decision_observation_ns
                intents.append(
                    RuntimeOrderIntent(
                        intent_id=intent_id,
                        exchange_id=exchange_id,
                        market_id=market_id,
                        tournament_id=envelope.tournament_id,
                        outcome_side=OutcomeSide(side),
                        action=OrderAction(action),
                        quantity=quantity,
                        limit_price_ticks=None,
                        strategy_id=strategy_id,
                        decision_observation_ns=decision_observation_ns,
                    )
                )
            reservations.reserve(envelope.logical_operation_id, tuple(intents))
            intent_by_id = {item.intent_id: item for item in intents}
            for event in journal.events(envelope.logical_operation_id):
                if (
                    event.event_type == "ACK"
                    and event.logical_intent_id in intent_by_id
                    and event.exchange_order_id is not None
                    and event.exchange_order_id.isdigit()
                ):
                    reservations.bind_recovered_exchange_order(
                        event.logical_intent_id,
                        event.exchange_order_id,
                    )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            # The unresolved operation remains an all-scope blocker when its
            # durable economics cannot be reconstructed exactly.
            continue


async def _retry_recovery_sig_operation[T](
    operation: str,
    read_or_idempotent_write: Callable[[], Awaitable[T]],
) -> T:
    """Retry only transient SIG failures with a finite exponential backoff."""
    for attempt in range(1, _RECOVERY_MAX_ATTEMPTS + 1):
        try:
            return await read_or_idempotent_write()
        except SigApiError as exc:
            transient = (
                isinstance(
                    exc,
                    (SigRateLimitError, SigTemporaryServiceError, SigTransportError),
                )
                or exc.status_code == 408
                or (exc.status_code is not None and exc.status_code >= 500)
            )
            if not transient or attempt == _RECOVERY_MAX_ATTEMPTS:
                raise
            await asyncio.sleep(_RECOVERY_BACKOFF_SECONDS[attempt - 1])
    raise AssertionError(f"unreachable retry loop for {operation}")


async def _recover_startup_placement(
    *,
    journal: ExecutionJournal,
    rest: RecoveryRest,
    live_sink: SigLiveSink,
    envelope: ExecutionEnvelope,
    replayable_state: bool,
    clock_ns: ClockNs,
    wall_clock: WallClock,
) -> bool:
    """Reconcile a placement by its acknowledged order IDs, cancelling rests."""
    operation_id = envelope.logical_operation_id
    current = _unresolved_envelope(journal, operation_id)
    if current is None:
        return True
    if current.lifecycle_state is not LifecycleState.RECONCILING:
        journal.mark_state(operation_id, LifecycleState.RECONCILING, clock_ns())
        current = _unresolved_envelope(journal, operation_id)
        if current is None:
            return True

    order_ids = _acknowledged_order_ids(journal, operation_id)
    if not order_ids and replayable_state:
        # This is the only permitted placement retry. SigLiveSink validates the
        # durable payload and sends its existing idempotency key unchanged.
        await live_sink.dispatch_recovery(current)
        current = _unresolved_envelope(journal, operation_id)
        if current is None:
            return True
        order_ids = _acknowledged_order_ids(journal, operation_id)

    if not order_ids:
        return False

    fills_complete = True
    every_order_filled = True
    for order_id in order_ids:
        acknowledged = next(
            event
            for event in journal.events(operation_id)
            if event.event_type == "ACK" and event.exchange_order_id == str(order_id)
        )
        order = await read_order_with_fallback(
            rest,
            order_id,
            tournament_id=envelope.tournament_id,
            exchange_id=acknowledged.exchange_id,
        )
        if order is None:
            return False

        # A resting placement from a previous process is owned by this durable
        # ACK. Cancel it under the recovery permit, then verify closure before
        # resolving the placement envelope.
        for _cancel_attempt in range(_RECOVERY_MAX_ATTEMPTS):
            if not order.open:
                break

            async def cancel_owned_order(
                current_order_id: int = order_id,
                current_envelope: ExecutionEnvelope = envelope,
            ) -> ExecutionEvent:
                cancel_envelope = ExecutionEnvelope.cancellation(
                    logical_operation_id=(
                        f"{operation_id}:startup-cancel:{current_order_id}:"
                        f"{clock_ns()}"
                    ),
                    operation_kind=OperationKind.SINGLE_CANCELLATION,
                    sink_mode=current_envelope.sink_mode,
                    created_monotonic_ns=clock_ns(),
                    order_id=current_order_id,
                    tournament_id=current_envelope.tournament_id,
                )
                return await live_sink.cancel(cancel_envelope)

            await _retry_recovery_sig_operation(
                f"cancel_owned_order:{order_id}",
                cancel_owned_order,
            )

            async def verify_cancelled_order(
                current_order_id: int = order_id,
                current_envelope: ExecutionEnvelope = envelope,
                current_acknowledged: ExecutionJournalEvent = acknowledged,
            ) -> OrderReadDto | None:
                return await read_order_with_fallback(
                    rest,
                    current_order_id,
                    tournament_id=current_envelope.tournament_id,
                    exchange_id=current_acknowledged.exchange_id,
                )

            order = await _retry_recovery_sig_operation(
                f"verify_cancelled_order:{order_id}",
                verify_cancelled_order,
            )
            if order is None:
                return False
        if order is None:
            return False
        if order.open:
            return False
        closed_order = order

        async def read_order_fills(
            current_order_id: int = order_id,
            current_envelope: ExecutionEnvelope = envelope,
            current_order: OrderReadDto = closed_order,
        ) -> tuple[tuple[OrderFillItemDto, ...], Decimal] | None:
            return await read_complete_fills_with_fallback(
                rest,
                current_order_id,
                tournament_id=current_envelope.tournament_id,
                exchange_id=current_order.exchange_id,
            )

        fills = await _retry_recovery_sig_operation(
            f"read_order_fills:{order_id}",
            read_order_fills,
        )
        if fills is None:
            fills_complete = False
            break
        fill_rows, filled_quantity = fills
        placement = journal.placement_identity_for_exchange_order_id(str(order_id))
        fill_operation_id = operation_id if placement is None else placement[0]
        fill_intent_id = None if placement is None else placement[1]
        _record_authoritative_fills(
            journal,
            operation_id=fill_operation_id,
            intent_id=fill_intent_id,
            order_id=order_id,
            exchange_id=order.exchange_id,
            fills=fill_rows,
        )
        every_order_filled = every_order_filled and (
            abs(filled_quantity) >= abs(order.quantity)
        )

    if not fills_complete:
        return False
    _record_terminal(
        journal,
        envelope,
        None,
        LifecycleState.FILLED if every_order_filled else LifecycleState.CANCELLED,
    )
    return True


def _unresolved_envelope(
    journal: ExecutionJournal,
    operation_id: str,
) -> ExecutionEnvelope | None:
    return next(
        (
            item
            for item in journal.unresolved()
            if item.logical_operation_id == operation_id
        ),
        None,
    )


def _acknowledged_order_ids(
    journal: ExecutionJournal,
    operation_id: str,
) -> tuple[int, ...]:
    return tuple(
        dict.fromkeys(
            int(event.exchange_order_id)
            for event in journal.events(operation_id)
            if event.event_type == "ACK"
            and event.exchange_order_id is not None
            and event.exchange_order_id.isdigit()
            and int(event.exchange_order_id) > 0
        )
    )


async def read_order_with_fallback(
    rest: RecoveryRest,
    order_id: int,
    *,
    tournament_id: str,
    exchange_id: str | None = None,
) -> OrderReadDto | None:
    """Read a single order, falling back to the authoritative all-orders list."""
    try:
        return await rest.get_order(order_id)
    except SigApiError as exc:
        if exc.status_code not in {404, 503}:
            raise
    async for order in rest.iter_orders(
        status="all",
        exchange_id=exchange_id,
        tournament_id=tournament_id,
        limit=200,
    ):
        if order.id == order_id:
            return order
    return None


async def read_complete_fills_with_fallback(
    rest: RecoveryRest,
    order_id: int,
    *,
    tournament_id: str,
    exchange_id: str,
) -> tuple[tuple[OrderFillItemDto, ...], Decimal] | None:
    """Read complete per-order fills, falling back from 503 to portfolio fills."""
    try:
        return await _read_complete_fills(rest, order_id)
    except SigApiError as exc:
        if exc.status_code != 503:
            raise

    fills: dict[int, FillReadDto] = {}
    cursor: str | None = None
    while True:
        page = await rest.list_portfolio_fills(
            exchange_id=exchange_id,
            tournament_id=tournament_id,
            limit=200,
            cursor=cursor,
        )
        if page.coverage is None or not page.coverage.complete:
            return None
        for fill in page.data:
            if fill.order_id is None:
                # Without order identity this exchange-scoped projection cannot
                # prove complete fills for the target order.
                if fill.exchange_id == exchange_id:
                    return None
                continue
            if fill.order_id == order_id:
                existing = fills.get(fill.id)
                if existing is not None and existing != fill:
                    return None
                fills[fill.id] = fill
        if not page.pagination.has_more:
            rows = tuple(
                OrderFillItemDto.model_validate(
                    {
                        "id": item.id,
                        "orderId": item.order_id,
                        "exchangeId": item.exchange_id,
                        "marketId": item.market_id,
                        "price": None if item.price is None else str(item.price),
                        "quantity": str(item.quantity),
                        "side": item.side,
                        "filledAt": item.filled_at.isoformat(),
                    }
                )
                for item in sorted(fills.values(), key=lambda item: item.id)
            )
            total = sum((item.quantity for item in rows), Decimal("0"))
            return rows, total
        next_cursor = page.pagination.next_cursor
        if next_cursor is None or not next_cursor.strip() or next_cursor == cursor:
            return None
        cursor = next_cursor


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
    observed = monotonic_ns()
    journal.record_event(
        logical_operation_id=envelope.logical_operation_id,
        event_type="RECONCILED_TERMINAL",
        observed_monotonic_ns=observed,
        exchange_order_id=order_id,
        terminal_status=terminal.value,
    )
    journal.mark_state(envelope.logical_operation_id, terminal, observed)


async def _recover_startup_cancel_all(
    *,
    journal: ExecutionJournal,
    rest: RecoveryRest,
    live_sink: SigLiveSink,
    envelope: ExecutionEnvelope,
    tournament_id: str,
    clock_ns: ClockNs,
) -> None:
    raw = json.loads(envelope.payload_json)
    explicit_tournament = raw.get("tournamentId")
    if explicit_tournament is not None and explicit_tournament != tournament_id:
        raise RuntimeError("cancel-all journal entry targets a different tournament")
    exchange_id = raw.get("exchangeId")
    market_id = raw.get("marketId")
    if exchange_id is not None and not isinstance(exchange_id, str):
        raise RuntimeError("cancel-all journal entry contains malformed exchangeId")
    if market_id is not None and not isinstance(market_id, str):
        raise RuntimeError("cancel-all journal entry contains malformed marketId")

    if envelope.lifecycle_state is not LifecycleState.RECONCILING:
        journal.mark_state(envelope.logical_operation_id, LifecycleState.RECONCILING, clock_ns())
    open_orders = [
        order
        async for order in rest.iter_orders(
            status="open",
            exchange_id=exchange_id,
            market_id=market_id,
            tournament_id=tournament_id,
            limit=200,
        )
    ]
    for order in open_orders:
        async def cancel_open_order(
            current_order: OrderReadDto = order,
            current_envelope: ExecutionEnvelope = envelope,
        ) -> ExecutionEvent:
            cancel_envelope = ExecutionEnvelope.cancellation(
                logical_operation_id=(
                    f"{current_envelope.logical_operation_id}:startup-cancel:"
                    f"{current_order.id}:{clock_ns()}"
                ),
                operation_kind=OperationKind.SINGLE_CANCELLATION,
                sink_mode=current_envelope.sink_mode,
                created_monotonic_ns=clock_ns(),
                order_id=current_order.id,
                tournament_id=current_envelope.tournament_id,
            )
            return await live_sink.cancel(cancel_envelope)

        await _retry_recovery_sig_operation(
            f"cancel_owned_order:{order.id}",
            cancel_open_order,
        )
    if await _cancel_all_scope_has_open_orders(
        rest=rest,
        envelope_payload=envelope.payload_json,
        tournament_id=tournament_id,
    ):
        return
    journal.mark_state(envelope.logical_operation_id, LifecycleState.CANCELLED, clock_ns())


def _unresolved_exchange_ids(
    journal: ExecutionJournal,
    unresolved: tuple[ExecutionEnvelope, ...],
    *,
    exchange_ids_by_market: dict[str, tuple[str, ...]],
) -> tuple[str, ...]:
    """Map unresolved journal ownership to a conservative fail-closed scope."""
    exchange_ids: set[str] = set()
    all_known = {
        event.exchange_order_id: event.exchange_id
        for envelope in journal.envelopes()
        for event in journal.events(envelope.logical_operation_id)
        if event.event_type == "ACK"
        and event.exchange_order_id is not None
        and event.exchange_id is not None
    }
    for envelope in unresolved:
        if envelope.operation_kind in {
            OperationKind.SINGLE_PLACEMENT,
            OperationKind.BEST_EFFORT_BATCH,
            OperationKind.ATOMIC_MULTI_LEG,
        }:
            try:
                payload = json.loads(envelope.payload_json)
                if not isinstance(payload, dict):
                    return ("*",)
                legs = payload.get("orders", payload.get("legs", [payload]))
                if not isinstance(legs, list):
                    return ("*",)
                for leg in legs:
                    if not isinstance(leg, dict):
                        return ("*",)
                    value = leg.get("exchangeId")
                    if not isinstance(value, str) or not value.strip():
                        return ("*",)
                    exchange_ids.add(value)
            except (json.JSONDecodeError, TypeError):
                return ("*",)
        elif envelope.operation_kind is OperationKind.SINGLE_CANCELLATION:
            payload = json.loads(envelope.payload_json)
            order_id = payload.get("orderId") if isinstance(payload, dict) else None
            exchange_id = all_known.get(str(order_id))
            if exchange_id is None:
                return ("*",)
            exchange_ids.add(exchange_id)
        elif envelope.operation_kind is OperationKind.CANCEL_ALL:
            payload = json.loads(envelope.payload_json)
            scoped_exchange = payload.get("exchangeId") if isinstance(payload, dict) else None
            scoped_market = payload.get("marketId") if isinstance(payload, dict) else None
            if isinstance(scoped_exchange, str):
                exchange_ids.add(scoped_exchange)
            elif isinstance(scoped_market, str):
                exchange_ids.update(exchange_ids_by_market.get(scoped_market, ()))
            else:
                return ("*",)
    return tuple(sorted(exchange_ids))


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


async def recover_in_session_cancellations(
    *,
    journal: ExecutionJournal,
    rest: RecoveryRest,
    live_sink: SigLiveSink,
    tournament_id: str,
    clock_ns: ClockNs = monotonic_ns,
) -> tuple[str, ...]:
    """Resolve/retry only existing cancellation risk during a live session.

    Fresh placements are deliberately out of scope. A cancellation gets one
    authoritative order/fill check and at most one same-envelope cancel retry.
    """
    resolved: list[str] = []
    for envelope in tuple(journal.unresolved()):
        if envelope.tournament_id != tournament_id:
            continue
        if envelope.operation_kind not in {
            OperationKind.SINGLE_CANCELLATION,
            OperationKind.CANCEL_ALL,
        }:
            continue
        if envelope.lifecycle_state is not LifecycleState.RECONCILING:
            journal.mark_state(
                envelope.logical_operation_id,
                LifecycleState.RECONCILING,
                clock_ns(),
            )
        try:
            if envelope.operation_kind is OperationKind.SINGLE_CANCELLATION:
                await _recover_single_cancel(
                    journal=journal,
                    rest=rest,
                    live_sink=live_sink,
                    envelope=envelope,
                    clock_ns=clock_ns,
                )
            elif await _cancel_all_scope_has_open_orders(
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

        unresolved_ids = {
            item.logical_operation_id for item in journal.unresolved()
        }
        if envelope.logical_operation_id not in unresolved_ids:
            resolved.append(envelope.logical_operation_id)
    return tuple(resolved)


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

    if envelope.lifecycle_state is not LifecycleState.RECONCILING:
        journal.mark_state(envelope.logical_operation_id, LifecycleState.RECONCILING, clock_ns())
    order = await read_order_with_fallback(
        rest,
        order_id,
        tournament_id=envelope.tournament_id,
    )
    if order is None:
        raise SigExecutionUncertainError(
            status_code=None,
            code="ORDER_PROJECTION_MISSING",
            safe_message="SIG order is not present in direct or list order reads",
        )
    if order.open:
        retry_envelope = ExecutionEnvelope.cancellation(
            logical_operation_id=f"{envelope.logical_operation_id}:startup-retry:{clock_ns()}",
            operation_kind=OperationKind.SINGLE_CANCELLATION,
            sink_mode=envelope.sink_mode,
            created_monotonic_ns=clock_ns(),
            order_id=order_id,
            tournament_id=envelope.tournament_id,
        )
        await live_sink.cancel(retry_envelope)
        order = await read_order_with_fallback(
            rest,
            order_id,
            tournament_id=envelope.tournament_id,
        )
        if order is None or order.open:
            return

    fills_result = await read_complete_fills_with_fallback(
        rest,
        order_id,
        tournament_id=envelope.tournament_id,
        exchange_id=order.exchange_id,
    )
    if fills_result is None:
        raise SigExecutionUncertainError(
            status_code=None,
            code="FILL_COVERAGE_INCOMPLETE",
            safe_message=(
                "SIG fill coverage is incomplete during cancellation recovery"
            ),
        )
    fill_rows, total_quantity_filled = fills_result
    observed = clock_ns()
    placement = journal.placement_identity_for_exchange_order_id(str(order_id))
    fill_operation_id = (
        envelope.logical_operation_id if placement is None else placement[0]
    )
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
