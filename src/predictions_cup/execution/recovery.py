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

from predictions_cup.execution.journal import ExecutionJournal
from predictions_cup.execution.live import SigLiveSink
from predictions_cup.execution.models import (
    ExecutionEnvelope,
    LifecycleState,
    OperationKind,
    RuntimeOrderIntent,
    lifecycle_transition_allowed,
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
    authoritative_snapshot: AccountAuthoritativeSnapshot | None = None


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
    """Reconcile unresolved operations from paginated tournament snapshots.

    Order and fill projections are read in bulk. Only acknowledged orders still
    open in the authoritative snapshot are cancelled individually. Callers may
    run this coroutine in the background and keep only returned exchange IDs
    blocked while reconciliation is in flight.
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

    candidates = tuple(
        envelope
        for envelope in journal.unresolved()
        if envelope.tournament_id == tournament_id
    )
    if operation_ids is not None:
        candidates = tuple(
            envelope
            for envelope in candidates
            if envelope.logical_operation_id in operation_ids
        )
    open_order_ids = (
        frozenset(order.id for order in authoritative_snapshot.open_orders if order.open)
        if authoritative_snapshot is not None
        else frozenset()
    )
    if reservations is not None:
        reserve_unresolved_placements(
            journal,
            candidates,
            reservations=reservations,
            market_by_exchange=market_by_exchange or {},
            open_order_ids=open_order_ids,
        )

    for envelope in candidates:
        _emit_recovery_observation(
            observation_emitter,
            observation_process_instance_id,
            wall_clock,
            ObservationKind.RECONCILIATION_STARTED,
            envelope,
            clock_ns(),
            detail=(("original_state", envelope.lifecycle_state.value),),
        )

    # Preserve same-key recovery for an interrupted dispatch that never reached
    # a durable ACK. Its exchange scope stays blocked until this task resolves.
    replayed = False
    for envelope in candidates:
        placement = envelope.operation_kind in {
            OperationKind.SINGLE_PLACEMENT,
            OperationKind.BEST_EFFORT_BATCH,
            OperationKind.ATOMIC_MULTI_LEG,
        }
        replayable_state = envelope.lifecycle_state in {
            LifecycleState.PENDING,
            LifecycleState.UNCERTAIN,
            LifecycleState.RECONCILING,
        }
        if not placement or _acknowledged_order_ids(journal, envelope.logical_operation_id):
            continue
        if not replayable_state:
            continue
        try:
            current = _unresolved_envelope(journal, envelope.logical_operation_id)
            if current is None:
                continue
            if current.lifecycle_state is not LifecycleState.RECONCILING:
                journal.mark_state(
                    current.logical_operation_id,
                    LifecycleState.RECONCILING,
                    clock_ns(),
                )
                current = _unresolved_envelope(journal, envelope.logical_operation_id)
            if current is None:
                continue
            replay_envelope = current
            async def recover_placement(
                current_envelope: ExecutionEnvelope = replay_envelope,
            ) -> object:
                return await live_sink.dispatch_recovery(current_envelope)

            await _retry_recovery_sig_operation(
                f"placement_recovery:{envelope.logical_operation_id}",
                recover_placement,
            )
            replayed = True
        except SigApiError:
            continue

    if replayed:
        try:
            authoritative_snapshot = await _retry_recovery_sig_operation(
                "post_replay_account_reconciliation",
                lambda: reconcile_account(
                    rest,
                    tournament_id=tournament_id,
                    tournament_slug=tournament_slug,
                ),
            )
        except SigApiError:
            # The pre-replay snapshot cannot prove the state of a new ACK.
            authoritative_snapshot = None

    open_orders = (
        {
            order.id: order
            for order in authoritative_snapshot.open_orders
            if order.open
        }
        if authoritative_snapshot is not None
        else {}
    )
    order_quantities: dict[int, Decimal] = {
        order.id: abs(order.quantity)
        for order in (() if authoritative_snapshot is None else authoritative_snapshot.open_orders)
    }
    order_exchange_ids: dict[int, str] = {
        order.id: order.exchange_id
        for order in (() if authoritative_snapshot is None else authoritative_snapshot.open_orders)
    }
    wrote_cancellation = False
    for envelope in candidates:
        if envelope.operation_kind in {
            OperationKind.SINGLE_PLACEMENT,
            OperationKind.BEST_EFFORT_BATCH,
            OperationKind.ATOMIC_MULTI_LEG,
        }:
            order_ids = _acknowledged_order_ids(journal, envelope.logical_operation_id)
            for order_id in order_ids:
                if order_id not in open_orders:
                    continue
                try:
                    cancel_envelope = ExecutionEnvelope.cancellation(
                        logical_operation_id=(
                            f"{envelope.logical_operation_id}:startup-cancel:{order_id}:"
                            f"{clock_ns()}"
                        ),
                        operation_kind=OperationKind.SINGLE_CANCELLATION,
                        sink_mode=envelope.sink_mode,
                        created_monotonic_ns=clock_ns(),
                        order_id=order_id,
                        tournament_id=envelope.tournament_id,
                    )

                    async def cancel_owned_order(
                        current_envelope: ExecutionEnvelope = cancel_envelope,
                    ) -> object:
                        return await live_sink.cancel(current_envelope)

                    await _retry_recovery_sig_operation(
                        f"cancel_owned_order:{order_id}",
                        cancel_owned_order,
                    )
                    wrote_cancellation = True
                except SigApiError:
                    continue
        elif envelope.operation_kind is OperationKind.SINGLE_CANCELLATION:
            cancel_order_id = _single_cancel_order_id(envelope)
            if cancel_order_id is None or cancel_order_id not in open_orders:
                continue
            retry_envelope = ExecutionEnvelope.cancellation(
                logical_operation_id=(
                    f"{envelope.logical_operation_id}:startup-retry:{clock_ns()}"
                ),
                operation_kind=OperationKind.SINGLE_CANCELLATION,
                sink_mode=envelope.sink_mode,
                created_monotonic_ns=clock_ns(),
                order_id=cancel_order_id,
                tournament_id=envelope.tournament_id,
            )
            try:
                async def retry_single_cancel(
                    current_envelope: ExecutionEnvelope = retry_envelope,
                ) -> object:
                    return await live_sink.cancel(current_envelope)

                await _retry_recovery_sig_operation(
                    f"cancellation_reconciliation:{envelope.logical_operation_id}",
                    retry_single_cancel,
                )
                wrote_cancellation = True
            except SigApiError:
                continue
        elif envelope.operation_kind is OperationKind.CANCEL_ALL:
            if _cancel_all_scope_has_open_orders_in_snapshot(
                envelope.payload_json,
                tournament_id=tournament_id,
                open_orders=tuple(open_orders.values()),
                market_by_exchange=market_by_exchange or {},
            ):
                try:
                    async def retry_cancel_all(
                        current_envelope: ExecutionEnvelope = envelope,
                    ) -> object:
                        return await live_sink.cancel(current_envelope)

                    await _retry_recovery_sig_operation(
                        f"cancel_all_reconciliation:{envelope.logical_operation_id}",
                        retry_cancel_all,
                    )
                    wrote_cancellation = True
                except SigApiError:
                    continue

    if wrote_cancellation:
        with suppress(SigApiError):
            authoritative_snapshot = await _retry_recovery_sig_operation(
                "post_recovery_account_reconciliation",
                lambda: reconcile_account(
                    rest,
                    tournament_id=tournament_id,
                    tournament_slug=tournament_slug,
                ),
            )

    fill_projection: _PortfolioFillProjection | None = None
    if any(_candidate_has_order_identity(journal, envelope) for envelope in candidates):
        try:
            fill_projection = await _retry_recovery_sig_operation(
                "startup_portfolio_fill_reconciliation",
                lambda: _read_tournament_fills(rest, tournament_id=tournament_id),
            )
        except SigApiError:
            fill_projection = None

    final_open_orders = (
        {
            order.id: order
            for order in authoritative_snapshot.open_orders
            if order.open
        }
        if authoritative_snapshot is not None
        else {}
    )
    if authoritative_snapshot is not None:
        order_quantities.update(
            {
                order.id: abs(order.quantity)
                for order in authoritative_snapshot.open_orders
            }
        )
        order_exchange_ids.update(
            {order.id: order.exchange_id for order in authoritative_snapshot.open_orders}
        )
    successful_cancels = journal.confirmed_cancelled_order_ids()
    envelopes_by_id = {item.logical_operation_id: item for item in journal.envelopes()}
    for envelope in candidates:
        if envelope.operation_kind in {
            OperationKind.SINGLE_PLACEMENT,
            OperationKind.BEST_EFFORT_BATCH,
            OperationKind.ATOMIC_MULTI_LEG,
        }:
            _reconcile_bulk_placement(
                journal=journal,
                envelope=envelope,
                open_orders=final_open_orders,
                successful_cancels=successful_cancels,
                fills=fill_projection,
                order_quantities=order_quantities,
                order_exchange_ids=order_exchange_ids,
                envelopes_by_id=envelopes_by_id,
                snapshot_available=authoritative_snapshot is not None,
                clock_ns=clock_ns,
            )
        elif envelope.operation_kind is OperationKind.SINGLE_CANCELLATION:
            _reconcile_bulk_single_cancel(
                journal=journal,
                envelope=envelope,
                open_orders=final_open_orders,
                fills=fill_projection,
                order_quantities=order_quantities,
                order_exchange_ids=order_exchange_ids,
                envelopes_by_id=envelopes_by_id,
                snapshot_available=authoritative_snapshot is not None,
                successful_cancels=successful_cancels,
                clock_ns=clock_ns,
            )
        elif (
            envelope.operation_kind is OperationKind.CANCEL_ALL
            and authoritative_snapshot is not None
            and not _cancel_all_scope_has_open_orders_in_snapshot(
                envelope.payload_json,
                tournament_id=tournament_id,
                open_orders=tuple(final_open_orders.values()),
                market_by_exchange=market_by_exchange or {},
            )
            and journal.lifecycle_state(envelope.logical_operation_id)
            is not LifecycleState.CANCELLED
        ):
            journal.mark_state(
                envelope.logical_operation_id,
                LifecycleState.CANCELLED,
                clock_ns(),
            )

    unresolved = tuple(
        envelope
        for envelope in journal.unresolved()
        if envelope.tournament_id == tournament_id
    )
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
        reserve_unresolved_placements(
            journal,
            scoped_unresolved,
            reservations=reservations,
            market_by_exchange=market_by_exchange or {},
            open_order_ids=frozenset(final_open_orders),
        )
    if authoritative_snapshot is None:
        portfolio = RuntimePortfolio(account_trusted=False)
    else:
        portfolio = authoritative_snapshot.to_runtime_portfolio()
    unresolved_operation_ids = tuple(
        envelope.logical_operation_id for envelope in scoped_unresolved
    )
    unresolved_ids = set(unresolved_operation_ids)
    for envelope in candidates:
        if envelope.logical_operation_id in unresolved_ids:
            continue
        _emit_recovery_observation(
            observation_emitter,
            observation_process_instance_id,
            wall_clock,
            ObservationKind.RECONCILIATION_RESOLVED,
            envelope,
            clock_ns(),
        )
    return StartupRecoveryResult(
        portfolio=portfolio,
        safe_to_resume_live=not scoped_unresolved,
        unresolved_operation_ids=unresolved_operation_ids,
        unresolved_exchange_ids=_unresolved_exchange_ids(
            journal,
            scoped_unresolved,
            exchange_ids_by_market=exchange_ids_by_market or {},
            open_order_ids=frozenset(final_open_orders),
        ),
        authoritative_snapshot=authoritative_snapshot,
    )
def reserve_unresolved_placements(
    journal: ExecutionJournal,
    unresolved: tuple[ExecutionEnvelope, ...],
    *,
    reservations: ExecutionReservationBook,
    market_by_exchange: dict[str, str],
    open_order_ids: frozenset[int] = frozenset(),
) -> None:
    confirmed_closed = journal.confirmed_cancelled_order_ids()
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
            unresolved_intent_ids = unresolved_placement_intent_ids(
                journal,
                envelope,
                open_order_ids=open_order_ids,
                confirmed_closed=confirmed_closed,
            )
            intents: list[RuntimeOrderIntent] = []
            for intent_id, leg in zip(envelope.intent_ids, raw_legs, strict=True):
                if intent_id not in unresolved_intent_ids:
                    continue
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
            if intents:
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
            # The operation remains a blocker when its durable economics cannot
            # be reconstructed exactly.
            continue


def unresolved_startup_exchange_ids(
    journal: ExecutionJournal,
    unresolved: tuple[ExecutionEnvelope, ...],
    *,
    exchange_ids_by_market: dict[str, tuple[str, ...]],
    open_order_ids: frozenset[int] = frozenset(),
) -> tuple[str, ...]:
    return _unresolved_exchange_ids(
        journal,
        unresolved,
        exchange_ids_by_market=exchange_ids_by_market,
        open_order_ids=open_order_ids,
    )


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


@dataclass(frozen=True, slots=True)
class _PortfolioFillProjection:
    by_order_id: dict[int, tuple[FillReadDto, ...]]
    incomplete_exchange_ids: frozenset[str]


def _candidate_has_order_identity(
    journal: ExecutionJournal,
    envelope: ExecutionEnvelope,
) -> bool:
    if envelope.operation_kind in {
        OperationKind.SINGLE_PLACEMENT,
        OperationKind.BEST_EFFORT_BATCH,
        OperationKind.ATOMIC_MULTI_LEG,
    }:
        return bool(_acknowledged_order_ids(journal, envelope.logical_operation_id))
    return (
        envelope.operation_kind is OperationKind.SINGLE_CANCELLATION
        and _single_cancel_order_id(envelope) is not None
    )


def _single_cancel_order_id(envelope: ExecutionEnvelope) -> int | None:
    try:
        payload = json.loads(envelope.payload_json)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    order_id = payload.get("orderId")
    return order_id if isinstance(order_id, int) and order_id > 0 else None


async def _read_tournament_fills(
    rest: RecoveryRest,
    *,
    tournament_id: str,
) -> _PortfolioFillProjection | None:
    """Read one complete, paginated tournament fill projection."""
    fills: dict[int, FillReadDto] = {}
    incomplete_exchange_ids: set[str] = set()
    cursor: str | None = None
    while True:
        page = await rest.list_portfolio_fills(
            tournament_id=tournament_id,
            limit=200,
            cursor=cursor,
        )
        if page.coverage is None or not page.coverage.complete:
            return None
        for fill in page.data:
            if fill.order_id is None:
                incomplete_exchange_ids.add(fill.exchange_id)
                continue
            existing = fills.get(fill.id)
            if existing is not None and existing != fill:
                return None
            fills[fill.id] = fill
        if not page.pagination.has_more:
            by_order: dict[int, list[FillReadDto]] = {}
            for fill in fills.values():
                assert fill.order_id is not None
                by_order.setdefault(fill.order_id, []).append(fill)
            return _PortfolioFillProjection(
                by_order_id={
                    order_id: tuple(sorted(rows, key=lambda row: row.id))
                    for order_id, rows in by_order.items()
                },
                incomplete_exchange_ids=frozenset(incomplete_exchange_ids),
            )
        next_cursor = page.pagination.next_cursor
        if next_cursor is None or not next_cursor.strip() or next_cursor == cursor:
            return None
        cursor = next_cursor


def unresolved_placement_intent_ids(
    journal: ExecutionJournal,
    envelope: ExecutionEnvelope,
    *,
    open_order_ids: frozenset[int] = frozenset(),
    confirmed_closed: frozenset[str] | None = None,
) -> frozenset[str]:
    confirmed = (
        journal.confirmed_cancelled_order_ids()
        if confirmed_closed is None
        else confirmed_closed
    )
    events = journal.events(envelope.logical_operation_id)
    rejected_intents = {
        event.logical_intent_id
        for event in events
        if event.event_type == "REJECTED" and event.logical_intent_id is not None
    }
    unresolved: set[str] = set()
    acknowledged_intents: set[str] = set()
    for event in events:
        if (
            event.event_type != "ACK"
            or event.logical_intent_id is None
            or event.exchange_order_id is None
            or not event.exchange_order_id.isdigit()
        ):
            continue
        acknowledged_intents.add(event.logical_intent_id)
        if (
            event.exchange_order_id not in confirmed
            or int(event.exchange_order_id) in open_order_ids
        ):
            unresolved.add(event.logical_intent_id)
    if envelope.lifecycle_state in {
        LifecycleState.PENDING,
        LifecycleState.ACKED,
        LifecycleState.UNCERTAIN,
        LifecycleState.RECONCILING,
        LifecycleState.OPEN,
        LifecycleState.PARTIALLY_FILLED,
        LifecycleState.CANCEL_PENDING,
    }:
        unresolved.update(
            set(envelope.intent_ids) - acknowledged_intents - rejected_intents
        )
    return frozenset(unresolved)


def _quantity_for_order_id(
    journal: ExecutionJournal,
    order_id: int,
    *,
    envelopes_by_id: dict[str, ExecutionEnvelope],
) -> Decimal | None:
    placement = journal.placement_identity_for_exchange_order_id(str(order_id))
    if placement is None:
        return None
    operation_id, intent_id = placement
    if intent_id is None:
        return None
    envelope = envelopes_by_id.get(operation_id)
    if envelope is None:
        return None
    ack = next(
        (
            event
            for event in journal.events(operation_id)
            if event.event_type == "ACK"
            and event.exchange_order_id == str(order_id)
        ),
        None,
    )
    if ack is not None and ack.quantity is not None:
        try:
            return abs(Decimal(ack.quantity))
        except Exception:
            return None
    try:
        payload = json.loads(envelope.payload_json)
        if not isinstance(payload, dict):
            return None
        raw_legs = payload.get("orders", payload.get("legs", [payload]))
        if not isinstance(raw_legs, list):
            return None
        by_intent = dict(zip(envelope.intent_ids, raw_legs, strict=True))
        leg = by_intent.get(intent_id)
        if not isinstance(leg, dict):
            return None
        quantity = leg.get("quantity")
        if not isinstance(quantity, (int, float, str, Decimal)):
            return None
        return abs(Decimal(str(quantity)))
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None


def _portfolio_fill_rows(
    fills: _PortfolioFillProjection,
    order_id: int,
) -> tuple[OrderFillItemDto, ...]:
    return tuple(
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
        for item in fills.by_order_id.get(order_id, ())
    )


def _reconcile_bulk_placement(
    *,
    journal: ExecutionJournal,
    envelope: ExecutionEnvelope,
    open_orders: dict[int, OrderReadDto],
    successful_cancels: frozenset[str],
    fills: _PortfolioFillProjection | None,
    order_quantities: dict[int, Decimal],
    order_exchange_ids: dict[int, str],
    envelopes_by_id: dict[str, ExecutionEnvelope],
    snapshot_available: bool,
    clock_ns: ClockNs,
) -> None:
    order_ids = _acknowledged_order_ids(journal, envelope.logical_operation_id)
    if not order_ids or fills is None:
        return
    events = journal.events(envelope.logical_operation_id)
    ack_by_id = {
        int(event.exchange_order_id): event
        for event in events
        if event.event_type == "ACK"
        and event.exchange_order_id is not None
        and event.exchange_order_id.isdigit()
    }
    every_order_filled = True
    for order_id in order_ids:
        if order_id in open_orders:
            return
        acknowledged = ack_by_id.get(order_id)
        if acknowledged is None:
            return
        exchange_id = acknowledged.exchange_id or order_exchange_ids.get(order_id)
        if exchange_id is None or exchange_id in fills.incomplete_exchange_ids:
            return
        if not snapshot_available and str(order_id) not in successful_cancels:
            return
        fill_rows = _portfolio_fill_rows(fills, order_id)
        placement = journal.placement_identity_for_exchange_order_id(str(order_id))
        fill_operation_id = envelope.logical_operation_id if placement is None else placement[0]
        fill_intent_id = None if placement is None else placement[1]
        _record_authoritative_fills(
            journal,
            operation_id=fill_operation_id,
            intent_id=fill_intent_id,
            order_id=order_id,
            exchange_id=exchange_id,
            fills=fill_rows,
        )
        quantity = order_quantities.get(order_id) or _quantity_for_order_id(
            journal,
            order_id,
            envelopes_by_id=envelopes_by_id,
        )
        if quantity is None:
            return
        filled_quantity = sum((abs(fill.quantity) for fill in fill_rows), Decimal("0"))
        every_order_filled = every_order_filled and filled_quantity >= quantity
    _record_terminal(
        journal,
        envelope,
        None,
        LifecycleState.FILLED if every_order_filled else LifecycleState.CANCELLED,
    )


def _reconcile_bulk_single_cancel(
    *,
    journal: ExecutionJournal,
    envelope: ExecutionEnvelope,
    open_orders: dict[int, OrderReadDto],
    fills: _PortfolioFillProjection | None,
    order_quantities: dict[int, Decimal],
    order_exchange_ids: dict[int, str],
    envelopes_by_id: dict[str, ExecutionEnvelope],
    snapshot_available: bool,
    successful_cancels: frozenset[str],
    clock_ns: ClockNs,
) -> None:
    order_id = _single_cancel_order_id(envelope)
    if order_id is None or order_id in open_orders or fills is None:
        return
    if not snapshot_available and str(order_id) not in successful_cancels:
        return
    placement = journal.placement_identity_for_exchange_order_id(str(order_id))
    exchange_id = (
        None
        if placement is None
        else next(
            (
                event.exchange_id
                for event in journal.events(placement[0])
                if event.event_type == "ACK"
                and event.exchange_order_id == str(order_id)
            ),
            None,
        )
    )
    exchange_id = (
        exchange_id
        or order_exchange_ids.get(order_id)
        or journal.exchange_id_for_order_id(str(order_id))
    )
    if exchange_id is None or exchange_id in fills.incomplete_exchange_ids:
        return
    fill_rows = _portfolio_fill_rows(fills, order_id)
    fill_operation_id = envelope.logical_operation_id if placement is None else placement[0]
    fill_intent_id = None if placement is None else placement[1]
    _record_authoritative_fills(
        journal,
        operation_id=fill_operation_id,
        intent_id=fill_intent_id,
        order_id=order_id,
        exchange_id=exchange_id,
        fills=fill_rows,
    )
    quantity = order_quantities.get(order_id) or _quantity_for_order_id(
        journal,
        order_id,
        envelopes_by_id=envelopes_by_id,
    )
    filled_quantity = sum((abs(fill.quantity) for fill in fill_rows), Decimal("0"))
    terminal = (
        LifecycleState.RECONCILED
        if quantity is None
        else (
            LifecycleState.FILLED
            if filled_quantity >= quantity
            else LifecycleState.CANCELLED
        )
    )
    journal.record_event(
        logical_operation_id=envelope.logical_operation_id,
        event_type="RECONCILED_TERMINAL",
        observed_monotonic_ns=clock_ns(),
        exchange_order_id=str(order_id),
        terminal_status=terminal.value,
    )
    journal.mark_state(envelope.logical_operation_id, terminal, clock_ns())


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
    current = journal.lifecycle_state(envelope.logical_operation_id)
    if current in {
        LifecycleState.FILLED,
        LifecycleState.CANCELLED,
        LifecycleState.RECONCILED,
        LifecycleState.REJECTED,
    }:
        return
    if not lifecycle_transition_allowed(current, terminal):
        journal.mark_state(
            envelope.logical_operation_id,
            LifecycleState.RECONCILING,
            observed,
        )
    journal.record_event(
        logical_operation_id=envelope.logical_operation_id,
        event_type="RECONCILED_TERMINAL",
        observed_monotonic_ns=observed,
        exchange_order_id=order_id,
        terminal_status=terminal.value,
    )
    journal.mark_state(envelope.logical_operation_id, terminal, observed)


def _unresolved_exchange_ids(
    journal: ExecutionJournal,
    unresolved: tuple[ExecutionEnvelope, ...],
    *,
    exchange_ids_by_market: dict[str, tuple[str, ...]],
    open_order_ids: frozenset[int] = frozenset(),
) -> tuple[str, ...]:
    """Map unresolved economics to only the exchanges that can still be exposed."""
    exchange_ids: set[str] = set()
    confirmed_closed = journal.confirmed_cancelled_order_ids()
    all_mapped = {
        exchange_id
        for values in exchange_ids_by_market.values()
        for exchange_id in values
    }

    def fallback_all() -> tuple[str, ...]:
        return tuple(sorted(all_mapped)) if all_mapped else ("*",)

    for envelope in unresolved:
        if envelope.operation_kind in {
            OperationKind.SINGLE_PLACEMENT,
            OperationKind.BEST_EFFORT_BATCH,
            OperationKind.ATOMIC_MULTI_LEG,
        }:
            try:
                payload = json.loads(envelope.payload_json)
                if not isinstance(payload, dict):
                    return fallback_all()
                legs = payload.get("orders", payload.get("legs", [payload]))
                if not isinstance(legs, list):
                    return fallback_all()
                by_intent = dict(zip(envelope.intent_ids, legs, strict=True))
                active_intents = unresolved_placement_intent_ids(
                    journal,
                    envelope,
                    open_order_ids=open_order_ids,
                    confirmed_closed=confirmed_closed,
                )
                events = journal.events(envelope.logical_operation_id)
                ack_by_intent = {
                    event.logical_intent_id: event
                    for event in events
                    if event.event_type == "ACK" and event.logical_intent_id is not None
                }
                for intent_id in active_intents:
                    leg = by_intent.get(intent_id)
                    if not isinstance(leg, dict):
                        return fallback_all()
                    ack = ack_by_intent.get(intent_id)
                    value = (
                        None if ack is None else ack.exchange_id
                    ) or leg.get("exchangeId")
                    if not isinstance(value, str) or not value.strip():
                        return fallback_all()
                    exchange_ids.add(value)
            except (json.JSONDecodeError, TypeError, ValueError):
                return fallback_all()
        elif envelope.operation_kind is OperationKind.SINGLE_CANCELLATION:
            order_id = _single_cancel_order_id(envelope)
            if order_id is not None and (
                str(order_id) in confirmed_closed and order_id not in open_order_ids
            ):
                continue
            exchange_id = (
                None if order_id is None else journal.exchange_id_for_order_id(str(order_id))
            )
            if exchange_id is None:
                return fallback_all()
            exchange_ids.add(exchange_id)
        elif envelope.operation_kind is OperationKind.CANCEL_ALL:
            try:
                payload = json.loads(envelope.payload_json)
            except json.JSONDecodeError:
                return fallback_all()
            scoped_exchange = payload.get("exchangeId") if isinstance(payload, dict) else None
            scoped_market = payload.get("marketId") if isinstance(payload, dict) else None
            if isinstance(scoped_exchange, str):
                exchange_ids.add(scoped_exchange)
            elif isinstance(scoped_market, str):
                market_exchanges = exchange_ids_by_market.get(scoped_market)
                if market_exchanges is None:
                    return fallback_all()
                exchange_ids.update(market_exchanges)
            else:
                if not all_mapped:
                    return fallback_all()
                exchange_ids.update(all_mapped)
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


def _cancel_all_scope_has_open_orders_in_snapshot(
    envelope_payload: str,
    *,
    tournament_id: str,
    open_orders: tuple[OrderReadDto, ...],
    market_by_exchange: dict[str, str],
) -> bool:
    raw = json.loads(envelope_payload)
    exchange_id = raw.get("exchangeId")
    market_id = raw.get("marketId")
    explicit_tournament = raw.get("tournamentId")
    if explicit_tournament is not None and explicit_tournament != tournament_id:
        raise RuntimeError("cancel-all journal entry targets a different tournament")
    if exchange_id is not None and not isinstance(exchange_id, str):
        raise RuntimeError("cancel-all journal entry contains malformed exchangeId")
    if market_id is not None and not isinstance(market_id, str):
        raise RuntimeError("cancel-all journal entry contains malformed marketId")
    return any(
        order.open
        and (exchange_id is None or order.exchange_id == exchange_id)
        and (
            market_id is None
            or market_by_exchange.get(order.exchange_id) == market_id
        )
        for order in open_orders
    )


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
