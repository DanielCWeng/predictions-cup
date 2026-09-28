from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from pydantic import SecretStr

from predictions_cup.sig.account_reconciliation import AccountAuthoritativeSnapshot
from predictions_cup.sig.account_runtime import AccountRealtimeController
from predictions_cup.sig.account_state import AccountRealtimeStateEngine
from predictions_cup.sig.realtime_models import RealtimeTokenDto
from predictions_cup.sig.realtime_subscriber import SubscriberExit


def _token() -> RealtimeTokenDto:
    return RealtimeTokenDto(
        token=SecretStr("realtime-token"),
        expiresAt="2026-09-29T00:00:00Z",
        supabaseUrl="https://example.supabase.co",
        anonKey=SecretStr("anon-key"),
        channels={"user": "user:profile-1"},
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


class _FakeSubscriber:
    def __init__(
        self,
        *,
        payloads: tuple[object, ...],
        outcome: SubscriberExit,
    ) -> None:
        self._payloads = payloads
        self._outcome = outcome

    async def run(
        self,
        *,
        on_batch: object,
        on_connected: object,
        stop_event: asyncio.Event,
        on_maintenance: object = None,
    ) -> SubscriberExit:
        del stop_event, on_maintenance
        assert callable(on_connected)
        on_connected()
        assert callable(on_batch)
        for payload in self._payloads:
            result = on_batch(
                "user:profile-1",
                payload,
                datetime(2026, 9, 28, 21, 0, tzinfo=UTC),
            )
            assert asyncio.iscoroutine(result)
            await result
        return self._outcome


def test_revision_gap_forces_authoritative_resync_before_next_subscription() -> None:
    state = AccountRealtimeStateEngine(tournament_id="t1")
    resync_count = 0
    subscribers = [
        _FakeSubscriber(
            payloads=(_batch(1, 0), _batch(3, 0)),
            outcome=SubscriberExit.STOPPED,
        ),
        _FakeSubscriber(payloads=(), outcome=SubscriberExit.STOPPED),
    ]
    factory_calls: list[tuple[str, str]] = []

    async def mint_token() -> RealtimeTokenDto:
        return _token()

    async def resync() -> AccountAuthoritativeSnapshot:
        nonlocal resync_count
        resync_count += 1
        return _snapshot()

    def factory(
        *,
        topic: str,
        token: RealtimeTokenDto,
        event_name: str,
    ) -> _FakeSubscriber:
        del token
        factory_calls.append((topic, event_name))
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


def test_token_refresh_revokes_trust_before_authoritative_resync() -> None:
    state = AccountRealtimeStateEngine(tournament_id="t1")
    trust_seen_at_resync: list[bool] = []
    subscribers = [
        _FakeSubscriber(payloads=(), outcome=SubscriberExit.TOKEN_REFRESH),
        _FakeSubscriber(payloads=(), outcome=SubscriberExit.STOPPED),
    ]

    async def mint_token() -> RealtimeTokenDto:
        return _token()

    async def resync() -> AccountAuthoritativeSnapshot:
        trust_seen_at_resync.append(state.trusted)
        return _snapshot()

    def factory(
        *,
        topic: str,
        token: RealtimeTokenDto,
        event_name: str,
    ) -> _FakeSubscriber:
        del topic, token, event_name
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
    assert trust_seen_at_resync == [False, False]
    assert state.trusted is True
