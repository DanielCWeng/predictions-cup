"""Account Realtime controller with mandatory authoritative recovery boundaries."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping
from contextlib import suppress
from datetime import UTC, datetime
from time import monotonic_ns
from typing import Protocol, cast
from uuid import uuid4

from predictions_cup.execution.journal import ExecutionJournal
from predictions_cup.observe.contracts import ObservationEmitter, ObservationKind, VenueObservation
from predictions_cup.sig.account_reconciliation import AccountAuthoritativeSnapshot
from predictions_cup.sig.account_state import (
    AccountRealtimeStateEngine,
    AccountTrustTransition,
)
from predictions_cup.sig.realtime_models import AccountBatchDto, RealtimeTokenDto
from predictions_cup.sig.realtime_subscriber import (
    SubscriberExit,
    SupabaseTournamentSubscriber,
)

MintToken = Callable[[], Awaitable[RealtimeTokenDto]]
AuthoritativeResync = Callable[[], Awaitable[AccountAuthoritativeSnapshot]]
ClockNs = Callable[[], int]
logger = logging.getLogger(__name__)
_ACTIVITY_RETRY_ATTEMPTS = 3


def _account_batch_category_counts(payload: object) -> tuple[int, int, int, int, int]:
    if isinstance(payload, AccountBatchDto):
        return (
            len(payload.order_updates),
            len(payload.fills),
            len(payload.settlements),
            len(payload.refunds),
            len(payload.collateral_changes),
        )
    if not isinstance(payload, Mapping):
        return (0, 0, 0, 0, 0)

    def count(*keys: str) -> int:
        for key in keys:
            try:
                values = payload.get(key)
            except Exception:
                return 0
            if isinstance(values, (list, tuple)):
                return len(values)
        return 0

    return (
        count("orderUpdates", "order_updates"),
        count("fills"),
        count("settlements"),
        count("refunds"),
        count("collateralChanges", "collateral_changes"),
    )


class AccountSubscriber(Protocol):
    async def run(
        self,
        *,
        on_batch: Callable[[str, object, datetime], Awaitable[None]],
        on_connected: Callable[[], None],
        stop_event: asyncio.Event,
        on_maintenance: Callable[[datetime], Awaitable[None]] | None = None,
    ) -> SubscriberExit: ...


class AccountSubscriberFactory(Protocol):
    def __call__(
        self,
        *,
        topic: str,
        token: RealtimeTokenDto,
        event_name: str,
        maintenance_interval_seconds: float,
    ) -> AccountSubscriber: ...


def _default_subscriber_factory(
    *,
    topic: str,
    token: RealtimeTokenDto,
    event_name: str,
    maintenance_interval_seconds: float,
) -> AccountSubscriber:
    return SupabaseTournamentSubscriber(
        topic=topic,
        token=token,
        event_name=event_name,
        maintenance_interval_seconds=maintenance_interval_seconds,
    )


class AccountRealtimeController:
    """Run account_batch as a fast hint layer around authoritative REST state.

    Every initial subscription and every recovery boundary performs a full
    authoritative resync before account state becomes trusted again.
    """

    def __init__(
        self,
        *,
        state: AccountRealtimeStateEngine,
        mint_token: MintToken,
        authoritative_resync: AuthoritativeResync,
        refresh_interval_seconds: float = 5.0,
        subscriber_factory: AccountSubscriberFactory = _default_subscriber_factory,
        execution_journal: ExecutionJournal | None = None,
        clock_ns: ClockNs = monotonic_ns,
        observation_emitter: ObservationEmitter | None = None,
        observation_process_instance_id: str | None = None,
    ) -> None:
        self._state = state
        self._mint_token = mint_token
        self._authoritative_resync = authoritative_resync
        if refresh_interval_seconds <= 0:
            raise ValueError("refresh_interval_seconds must be positive")
        self._refresh_interval_seconds = refresh_interval_seconds
        self._subscriber_factory = subscriber_factory
        self._execution_journal = execution_journal
        self._clock_ns = clock_ns
        self._observation_emitter = observation_emitter
        self._observation_process_instance_id = (
            uuid4().hex
            if observation_process_instance_id is None
            else observation_process_instance_id
        )
        if not self._observation_process_instance_id.strip():
            raise ValueError("observation_process_instance_id must not be blank")
        self._resyncing = False
        self._resync_generation = 0
        self._refresh_lock = asyncio.Lock()

    async def run(self, *, stop_event: asyncio.Event) -> None:
        refresh_task = asyncio.create_task(self._refresh_periodically(stop_event))
        try:
            while not stop_event.is_set():
                token = await self._mint_token()
                subscriber = self._subscriber_factory(
                    topic=token.channels.user,
                    token=token,
                    event_name="account_batch",
                    maintenance_interval_seconds=self._refresh_interval_seconds,
                )
                connected = asyncio.Event()
                self._resyncing = True

                subscriber_task = asyncio.create_task(
                    subscriber.run(
                        on_batch=self._handle_batch,
                        on_connected=connected.set,
                        stop_event=stop_event,
                    )
                )
                connected_wait = asyncio.create_task(connected.wait())
                waiters = {
                    cast(asyncio.Task[object], subscriber_task),
                    cast(asyncio.Task[object], connected_wait),
                }
                done, _ = await asyncio.wait(
                    waiters,
                    return_when=asyncio.FIRST_COMPLETED,
                )

                if subscriber_task in done and not connected.is_set():
                    connected_wait.cancel()
                    with suppress(asyncio.CancelledError):
                        await connected_wait
                    outcome = subscriber_task.result()
                    if outcome is SubscriberExit.STOPPED:
                        return
                    self._mark_exit_untrusted(outcome)
                    continue

                await connected_wait

                try:
                    await self._restore_trust_while_subscribed()
                    if subscriber_task.done():
                        outcome = subscriber_task.result()
                    else:
                        outcome = await subscriber_task
                except BaseException:
                    subscriber_task.cancel()
                    with suppress(asyncio.CancelledError):
                        await subscriber_task
                    raise

                if outcome is SubscriberExit.STOPPED:
                    return
                self._mark_exit_untrusted(outcome)
        finally:
            refresh_task.cancel()
            with suppress(asyncio.CancelledError):
                await refresh_task

    async def _refresh_periodically(self, stop_event: asyncio.Event) -> None:
        while not stop_event.is_set():
            try:
                await asyncio.wait_for(
                    stop_event.wait(), timeout=self._refresh_interval_seconds
                )
            except TimeoutError:
                await self._refresh_authoritative(
                    datetime.now(UTC), trigger_reason="periodic"
                )

    async def _restore_trust_while_subscribed(self) -> None:
        async with self._refresh_lock:
            await self._restore_trust_while_subscribed_locked()

    async def _restore_trust_while_subscribed_locked(self) -> None:
        while True:
            self._resyncing = True
            generation_before = self._resync_generation
            self._observe(
                ObservationKind.RECONCILIATION_STARTED,
                monotonic_ns=self._clock_ns(),
            )
            authoritative = await self._authoritative_resync()
            self._state.apply_authoritative(
                authoritative,
                mark_trusted=False,
            )
            # Drain callbacks already queued by the subscribed socket while trust
            # is still false. A batch seen here makes the REST snapshot ambiguous.
            await asyncio.sleep(0)
            if self._resync_generation == generation_before:
                self._state.mark_trusted_after_reconciliation()
                self._observe(
                    ObservationKind.RECONCILIATION_RESOLVED,
                    monotonic_ns=self._clock_ns(),
                )
                self._resyncing = False
                return
            self._state.mark_untrusted(
                AccountTrustTransition.UNTRUSTED_RESYNC_ACTIVITY
            )

    def _mark_exit_untrusted(self, outcome: SubscriberExit) -> None:
        if outcome is SubscriberExit.TOKEN_REFRESH:
            transition = AccountTrustTransition.UNTRUSTED_TOKEN_REFRESH
        elif outcome is SubscriberExit.SOCKET_ERROR:
            transition = AccountTrustTransition.UNTRUSTED_SOCKET_ERROR
        else:
            transition = AccountTrustTransition.UNTRUSTED_RECONNECT
        self._state.mark_untrusted(transition)

    async def _handle_batch(
        self,
        topic: str,
        payload: object,
        observed_at: datetime,
    ) -> None:
        del topic
        if self._resyncing:
            self._resync_generation += 1
            order_updates, fills, settlements, refunds, collateral = (
                _account_batch_category_counts(payload)
            )
            logger.info(
                "SIG account resync batch discarded: order_updates=%d fills=%d "
                "settlements=%d refunds=%d collateral=%d",
                order_updates,
                fills,
                settlements,
                refunds,
                collateral,
            )
            return
        result = self._state.handle_raw_batch(payload, observed_at=observed_at)
        if self._execution_journal is not None and (
            result.accepted
            or result.transition
            is AccountTrustTransition.UNTRUSTED_FILL_REQUIRES_RECONCILIATION
        ):
            # Valid fill payloads remain useful audit evidence even though they
            # are not safe to mutate exposure from without direction/recovery
            # fencing.
            self._record_execution_events(AccountBatchDto.model_validate(payload))
        if result.requires_reconciliation:
            # Keep the active subscription in place while authoritative state
            # catches up. A fill must not briefly restore trust after its socket
            # has already closed, nor force a fresh token for the same connection.
            await self._refresh_authoritative(
                observed_at, trigger_reason="account_batch_reconciliation"
            )

    async def _refresh_authoritative(
        self, observed_at: datetime, *, trigger_reason: str | None = None
    ) -> None:
        del observed_at
        async with self._refresh_lock:
            refresh_id = uuid4().hex
            started_ns = self._clock_ns()
            generation_at_start = self._resync_generation
            resync_calls = 0
            outcome = "activity_exhausted"
            self._resyncing = True
            try:
                # Our own placements/cancels produce account activity, so a busy
                # LIVE account often sees a batch during the multi-request
                # resync. Retry immediately instead of holding untrusted until
                # the next periodic refresh.
                for _ in range(_ACTIVITY_RETRY_ATTEMPTS):
                    generation_before = self._resync_generation
                    try:
                        resync_calls += 1
                        authoritative = await self._authoritative_resync()
                    except Exception as exc:
                        outcome = f"exception:{type(exc).__name__}"
                        self._state.mark_untrusted(
                            AccountTrustTransition.UNTRUSTED_REFRESH_FAILURE
                        )
                        logger.warning(
                            "SIG account authoritative refresh failed: %s: %s",
                            type(exc).__name__,
                            exc,
                        )
                        return
                    # REST is the authoritative source. A quiet/disconnected
                    # socket does not invalidate a successful reconciliation;
                    # activity arriving during the fetch does.
                    self._state.apply_authoritative(authoritative, mark_trusted=False)
                    await asyncio.sleep(0)
                    if self._resync_generation == generation_before:
                        self._state.mark_trusted_after_reconciliation()
                        outcome = "trusted"
                        return
                    self._state.mark_untrusted(
                        AccountTrustTransition.UNTRUSTED_RESYNC_ACTIVITY
                    )
            except BaseException as exc:
                outcome = f"exception:{type(exc).__name__}"
                raise
            finally:
                self._resyncing = False
                duration_ms = (self._clock_ns() - started_ns) / 1_000_000
                logger.info(
                    "SIG account refresh cycle refresh_id=%s trigger_reason=%s "
                    "duration_ms=%.3f resync_calls=%d generation_before=%d "
                    "generation_after=%d outcome=%s",
                    refresh_id,
                    trigger_reason or "unknown",
                    duration_ms,
                    resync_calls,
                    generation_at_start,
                    self._resync_generation,
                    outcome,
                )

    def _observe(
        self,
        kind: ObservationKind,
        *,
        monotonic_ns: int,
        logical_operation_id: str | None = None,
        exchange_id: str | None = None,
        market_id: str | None = None,
        exchange_order_id: str | None = None,
        source_timestamp: datetime | None = None,
        detail: tuple[tuple[str, str], ...] = (),
    ) -> None:
        emitter = self._observation_emitter
        if emitter is None:
            return
        try:
            emitter.emit(
                VenueObservation(
                    kind=kind,
                    observed_at=datetime.now(UTC),
                    monotonic_ns=monotonic_ns,
                    process_instance_id=self._observation_process_instance_id,
                    source="SIG_ACCOUNT_REALTIME",
                    source_version="observe-001",
                    provenance="SIG_ACCOUNT_BATCH_OR_RECONCILIATION",
                    tournament_id=self._state.tournament_id,
                    market_id=market_id,
                    exchange_id=exchange_id,
                    logical_operation_id=logical_operation_id,
                    exchange_order_id=exchange_order_id,
                    source_timestamp=source_timestamp,
                    detail=detail,
                )
            )
        except Exception:
            return

    def _record_execution_events(self, batch: AccountBatchDto) -> None:
        journal = self._execution_journal
        if journal is None:
            return
        observed_ns = self._clock_ns()
        for fill in batch.fills:
            if fill.order_id is None:
                continue
            order_id = str(fill.order_id)
            placement = journal.placement_identity_for_exchange_order_id(order_id)
            if placement is None:
                continue
            logical_operation_id, logical_intent_id = placement
            journal.record_event(
                logical_operation_id=logical_operation_id,
                logical_intent_id=logical_intent_id,
                event_type="REALTIME_FILL",
                observed_monotonic_ns=observed_ns,
                source_timestamp=fill.executed_at.isoformat(),
                exchange_id=fill.exchange_id,
                exchange_order_id=order_id,
                quantity=str(fill.quantity),
                price=None if fill.price is None else str(fill.price),
            )
            self._observe(
                ObservationKind.PARTIAL_FILL,
                monotonic_ns=observed_ns,
                logical_operation_id=logical_operation_id,
                exchange_id=fill.exchange_id,
                market_id=fill.market_id,
                exchange_order_id=order_id,
                source_timestamp=fill.executed_at,
                detail=(("quantity", str(fill.quantity)), ("price", str(fill.price))),
            )
        for update in batch.order_updates:
            if update.order_id is None:
                continue
            order_id = str(update.order_id)
            placement = journal.placement_identity_for_exchange_order_id(order_id)
            if placement is None:
                continue
            logical_operation_id, logical_intent_id = placement
            journal.record_event(
                logical_operation_id=logical_operation_id,
                logical_intent_id=logical_intent_id,
                event_type="REALTIME_ORDER_UPDATE",
                observed_monotonic_ns=observed_ns,
                source_timestamp=update.at.isoformat(),
                exchange_id=update.exchange_id,
                exchange_order_id=order_id,
                quantity=str(update.quantity_traded),
                price=(
                    None
                    if update.latest_trade_price is None
                    else str(update.latest_trade_price)
                ),
                terminal_status="OPEN" if update.open else "CLOSED",
            )
