from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from predictions_cup.sig.account_reconciliation import AccountAuthoritativeSnapshot
from predictions_cup.sig.account_runtime import AccountRealtimeController
from predictions_cup.sig.account_state import AccountRealtimeStateEngine
from predictions_cup.sig.realtime_models import RealtimeTokenDto
from predictions_cup.sig.realtime_subscriber import SubscriberExit


def _token() -> RealtimeTokenDto:
    return RealtimeTokenDto.model_validate(
        {
            "token": "fixture",
            "expiresAt": "2026-09-29T00:00:00Z",
            "supabaseUrl": "https://example.supabase.co",
            "anonKey": "fixture",
            "channels": {"user": "user:profile-1"},
        }
    )


def _snapshot() -> AccountAuthoritativeSnapshot:
    return AccountAuthoritativeSnapshot(
        tournament_id="t1",
        tournament_slug="cup",
        open_orders=(),
        positions=(),
        observed_at=datetime(2026, 9, 28, 21, 0, tzinfo=UTC),
    )


def _batch(revision: int, previous: int) -> dict[str, object]:
    return {
        "fills": [],
        "orderUpdates": [],
        "settlements": [],
        "refunds": [],
        "collateralChanges": [],
        "delivery": {
            "model": "engine",
            "revision": revision,
            "previousRevision": previous,
            "correlationId": f"c-{revision}",
            "sourceSequenceFrom": revision,
            "sourceSequenceThrough": revision,
        },
    }


class FakeSubscriber:
    def __init__(
        self,
        payloads: tuple[object, ...],
        outcome: SubscriberExit,
    ) -> None:
        self.payloads = payloads
        self.outcome = outcome

    async def run(
        self,
        *,
        on_batch: Any,
        on_connected: Any,
        stop_event: asyncio.Event,
        on_maintenance: Any = None,
    ) -> SubscriberExit:
        del stop_event, on_maintenance
        on_connected()
        for payload in self.payloads:
            await on_batch(
                "user:profile-1",
                payload,
                datetime(2026, 9, 28, 21, 0, tzinfo=UTC),
            )
        return self.outcome


def test_revision_gap_forces_authoritative_resync() -> None:
    state = AccountRealtimeStateEngine(tournament_id="t1")
    subscribers = [
        FakeSubscriber((_batch(1, 0), _batch(3, 0)), SubscriberExit.STOPPED),
        FakeSubscriber((), SubscriberExit.STOPPED),
    ]
    resync_count = 0
    factory_calls: list[tuple[str, str]] = []

    async def mint_token() -> RealtimeTokenDto:
        return _token()

    async def resync() -> AccountAuthoritativeSnapshot:
        nonlocal resync_count
        resync_count += 1
        return _snapshot()

    def factory(**kwargs: Any) -> FakeSubscriber:
        factory_calls.append((str(kwargs["topic"]), str(kwargs["event_name"])))
        return subscribers.pop(0)

    async def scenario() -> None:
        controller = AccountRealtimeController(
            state=state,
            mint_token=mint_token,
            authoritative_resync=resync,
            subscriber_factory=factory,
        )
        await controller.run(stop_event=asyncio.Event())

    asyncio.run(scenario())

    assert resync_count == 2
    assert factory_calls == [
        ("user:profile-1", "account_batch"),
        ("user:profile-1", "account_batch"),
    ]
    assert state.trusted is True


def test_token_refresh_revokes_trust_before_resync() -> None:
    state = AccountRealtimeStateEngine(tournament_id="t1")
    subscribers = [
        FakeSubscriber((), SubscriberExit.TOKEN_REFRESH),
        FakeSubscriber((), SubscriberExit.STOPPED),
    ]
    trust_seen: list[bool] = []

    async def mint_token() -> RealtimeTokenDto:
        return _token()

    async def resync() -> AccountAuthoritativeSnapshot:
        trust_seen.append(state.trusted)
        return _snapshot()

    def factory(**kwargs: Any) -> FakeSubscriber:
        del kwargs
        return subscribers.pop(0)

    async def scenario() -> None:
        controller = AccountRealtimeController(
            state=state,
            mint_token=mint_token,
            authoritative_resync=resync,
            subscriber_factory=factory,
        )
        await controller.run(stop_event=asyncio.Event())

    asyncio.run(scenario())

    assert trust_seen == [False, False]
    assert state.trusted is True
