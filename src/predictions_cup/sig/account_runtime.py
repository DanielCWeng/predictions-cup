"""Account Realtime controller with mandatory authoritative recovery boundaries."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import suppress
from datetime import datetime
from time import monotonic_ns
from typing import Protocol, cast

from predictions_cup.execution.journal import ExecutionJournal
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
    ) -> AccountSubscriber: ...


class AccountResyncRequired(RuntimeError):
    """Internal control-flow signal: leave the socket and restore REST truth."""


def _default_subscriber_factory(
    *,
    topic: str,
    token: RealtimeTokenDto,
    event_name: str,
) -> AccountSubscriber:
    return SupabaseTournamentSubscriber(
        topic=topic,
        token=token,
        event_name=event_name,
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
        subscriber_factory: AccountSubscriberFactory = _default_subscriber_factory,
        execution_journal: ExecutionJournal | None = None,
        clock_ns: ClockNs = monotonic_ns,
    ) -> None:
        self._state = state
        self._mint_token = mint_token
        self._authoritative_resync = authoritative_resync
        self._subscriber_factory = subscriber_factory
        self._execution_journal = execution_journal
        self._clock_ns = clock_ns
        self._resyncing = False
        self._resync_generation = 0

    async def run(self, *, stop_event: asyncio.Event) -> None:
        while not stop_event.is_set():
            token = await self._mint_token()
            subscriber = self._subscriber_factory(
                topic=token.channels.user,
                token=token,
                event_name="account_batch",
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
            except AccountResyncRequired:
                # The state engine has already revoked trust. Re-subscribe and
                # reconcile authoritatively before accepting more account data.
                continue
            except BaseException:
                subscriber_task.cancel()
                with suppress(asyncio.CancelledError):
                    await subscriber_task
                raise

            if outcome is SubscriberExit.STOPPED:
                return
            self._mark_exit_untrusted(outcome)

    async def _restore_trust_while_subscribed(self) -> None:
        while True:
            self._resyncing = True
            generation_before = self._resync_generation
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
            raise AccountResyncRequired

    def _record_execution_events(self, batch: AccountBatchDto) -> None:
        journal = self._execution_journal
        if journal is None:
            return
        observed_ns = self._clock_ns()
        for fill in batch.fills:
            order_id = str(fill.order_id)
            logical_operation_id = journal.logical_operation_for_exchange_order_id(
                order_id
            )
            if logical_operation_id is None:
                continue
            journal.record_event(
                logical_operation_id=logical_operation_id,
                event_type="REALTIME_FILL",
                observed_monotonic_ns=observed_ns,
                source_timestamp=fill.executed_at.isoformat(),
                exchange_id=fill.exchange_id,
                exchange_order_id=order_id,
                quantity=str(fill.quantity),
                price=str(fill.price),
            )
        for update in batch.order_updates:
            order_id = str(update.order_id)
            logical_operation_id = journal.logical_operation_for_exchange_order_id(
                order_id
            )
            if logical_operation_id is None:
                continue
            journal.record_event(
                logical_operation_id=logical_operation_id,
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

