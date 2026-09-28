"""Account Realtime controller with mandatory authoritative recovery boundaries."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Protocol

from predictions_cup.sig.account_reconciliation import AccountAuthoritativeSnapshot
from predictions_cup.sig.account_state import (
    AccountRealtimeStateEngine,
    AccountTrustTransition,
)
from predictions_cup.sig.realtime_models import RealtimeTokenDto
from predictions_cup.sig.realtime_subscriber import (
    SubscriberExit,
    SupabaseTournamentSubscriber,
)

MintToken = Callable[[], Awaitable[RealtimeTokenDto]]
AuthoritativeResync = Callable[[], Awaitable[AccountAuthoritativeSnapshot]]


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
    ) -> None:
        self._state = state
        self._mint_token = mint_token
        self._authoritative_resync = authoritative_resync
        self._subscriber_factory = subscriber_factory

    async def run(self, *, stop_event: asyncio.Event) -> None:
        while not stop_event.is_set():
            token = await self._mint_token()
            authoritative = await self._authoritative_resync()
            self._state.apply_authoritative(authoritative)

            subscriber = self._subscriber_factory(
                topic=token.channels.user,
                token=token,
                event_name="account_batch",
            )

            try:
                outcome = await subscriber.run(
                    on_batch=self._handle_batch,
                    on_connected=self._on_connected,
                    stop_event=stop_event,
                )
            except AccountResyncRequired:
                # AccountRealtimeStateEngine already revoked trust for a malformed
                # payload, revision gap, or unknown resting-order update.
                continue

            if outcome is SubscriberExit.STOPPED:
                return
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
        result = self._state.handle_raw_batch(payload, observed_at=observed_at)
        if result.requires_reconciliation:
            raise AccountResyncRequired

    @staticmethod
    def _on_connected() -> None:
        # Trust is established only by the authoritative resync immediately
        # before subscribe, never by the socket connection itself.
        return None
