from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from predictions_cup.execution.journal import ExecutionJournal
from predictions_cup.execution.models import (
    ExecutionEnvelope,
    ExecutionMode,
    OperationKind,
    RuntimeOrderIntent,
)
from predictions_cup.execution.reservations import ExecutionReservationBook
from predictions_cup.runtime import OrderAction, OutcomeSide
from predictions_cup.sig.account_reconciliation import AccountAuthoritativeSnapshot
from predictions_cup.sig.account_runtime import AccountRealtimeController
from predictions_cup.sig.account_state import AccountRealtimeStateEngine
from predictions_cup.sig.realtime_models import AccountBatchDto, RealtimeTokenDto
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


def test_account_batch_accepts_openapi_nullable_fields() -> None:
    # Field shapes and example values follow the SIG OpenAPI Realtime account
    # batch documentation at /realtime/token and settlement schema examples.
    payload = _batch(1, 0)
    payload["fills"] = [
        {
            "orderId": None,
            "exchangeId": "36",
            "marketId": "26",
            "price": None,
            "quantity": 100,
            "executedAt": "2026-10-01T16:00:00.000Z",
            "tournamentId": None,
        }
    ]
    payload["settlements"] = [
        {
            "marketId": "26",
            "exchangeId": "36",
            "outcomeSide": "YES",
            "shares": 100,
            "costBasis": None,
            "payout": 100,
            "realizedPnl": None,
            "settlementOutcome": None,
            "tournamentId": None,
            "at": "2026-10-01T16:00:00.000Z",
        }
    ]

    batch = AccountBatchDto.model_validate(payload)

    assert batch.fills[0].order_id is None
    assert batch.fills[0].price is None
    assert batch.settlements[0].cost_basis is None
    assert batch.settlements[0].realized_pnl is None
    assert batch.settlements[0].settlement_outcome is None


class FakeSubscriber:
    def __init__(
        self,
        payloads: tuple[object, ...],
        outcome: SubscriberExit,
        *,
        connect_delay: float = 0.001,
        payload_gate: asyncio.Event | None = None,
        maintenance_call: bool = False,
    ) -> None:
        self.payloads = payloads
        self.outcome = outcome
        self.connect_delay = connect_delay
        self.payload_gate = payload_gate
        self.maintenance_call = maintenance_call

    async def run(
        self,
        *,
        on_batch: Any,
        on_connected: Any,
        stop_event: asyncio.Event,
        on_maintenance: Any = None,
    ) -> SubscriberExit:
        del stop_event
        on_connected()
        await asyncio.sleep(self.connect_delay)
        if self.maintenance_call and on_maintenance is not None:
            await on_maintenance(datetime(2026, 9, 28, 21, 0, tzinfo=UTC))
        if self.payload_gate is not None:
            await self.payload_gate.wait()
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


def test_fill_resync_reuses_token_and_socket() -> None:
    state = AccountRealtimeStateEngine(tournament_id="t1")
    payload = _batch(1, 0)
    payload["fills"] = [
        {
            "orderId": 91,
            "exchangeId": "36",
            "marketId": "26",
            "price": 0.42,
            "quantity": 1,
            "executedAt": "2026-09-28T21:00:00Z",
            "tournamentId": "t1",
        }
    ]
    token_count = 0
    resync_count = 0
    subscription_count = 0

    async def mint_token() -> RealtimeTokenDto:
        nonlocal token_count
        token_count += 1
        return _token()

    async def resync() -> AccountAuthoritativeSnapshot:
        nonlocal resync_count
        resync_count += 1
        return _snapshot()

    def factory(**kwargs: Any) -> FakeSubscriber:
        nonlocal subscription_count
        del kwargs
        subscription_count += 1
        return FakeSubscriber(
            (payload,) if subscription_count == 1 else (),
            SubscriberExit.STOPPED,
        )

    async def scenario() -> None:
        await AccountRealtimeController(
            state=state,
            mint_token=mint_token,
            authoritative_resync=resync,
            subscriber_factory=factory,
        ).run(stop_event=asyncio.Event())

    asyncio.run(scenario())

    assert token_count == 1
    assert subscription_count == 2
    assert resync_count == 2
    assert state.trusted is True


def test_quiet_socket_periodic_refresh_keeps_account_fresh() -> None:
    state = AccountRealtimeStateEngine(tournament_id="t1")
    token_count = 0
    resync_count = 0
    observed_times = [
        datetime(2026, 9, 28, 21, 0, tzinfo=UTC),
        datetime(2026, 9, 28, 21, 0, 5, tzinfo=UTC),
    ]

    async def mint_token() -> RealtimeTokenDto:
        nonlocal token_count
        token_count += 1
        return _token()

    async def resync() -> AccountAuthoritativeSnapshot:
        nonlocal resync_count
        observed_at = observed_times[min(resync_count, len(observed_times) - 1)]
        resync_count += 1
        snapshot = _snapshot()
        return AccountAuthoritativeSnapshot(
            tournament_id=snapshot.tournament_id,
            tournament_slug=snapshot.tournament_slug,
            open_orders=snapshot.open_orders,
            positions=snapshot.positions,
            observed_at=observed_at,
        )

    def factory(**kwargs: Any) -> FakeSubscriber:
        del kwargs
        return FakeSubscriber(
            (), SubscriberExit.STOPPED, maintenance_call=True
        )

    async def scenario() -> None:
        await AccountRealtimeController(
            state=state,
            mint_token=mint_token,
            authoritative_resync=resync,
            subscriber_factory=factory,
            refresh_interval_seconds=5.0,
        ).run(stop_event=asyncio.Event())

    asyncio.run(scenario())

    assert token_count == 1
    assert resync_count == 2
    assert state.trusted is True
    assert state.last_authoritative_observed_at == observed_times[1]


def test_periodic_rest_failure_revokes_account_trust() -> None:
    state = AccountRealtimeStateEngine(tournament_id="t1")
    resync_count = 0

    async def mint_token() -> RealtimeTokenDto:
        return _token()

    async def resync() -> AccountAuthoritativeSnapshot:
        nonlocal resync_count
        resync_count += 1
        if resync_count == 2:
            raise RuntimeError("REST unavailable")
        return _snapshot()

    def factory(**kwargs: Any) -> FakeSubscriber:
        del kwargs
        return FakeSubscriber(
            (), SubscriberExit.STOPPED, maintenance_call=True
        )

    async def scenario() -> None:
        await AccountRealtimeController(
            state=state,
            mint_token=mint_token,
            authoritative_resync=resync,
            subscriber_factory=factory,
        ).run(stop_event=asyncio.Event())

    asyncio.run(scenario())

    assert state.trusted is False
    assert state.transition.value == "UNTRUSTED_REFRESH_FAILURE"


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



def test_account_batch_during_rest_snapshot_forces_another_resync() -> None:
    state = AccountRealtimeStateEngine(tournament_id="t1")
    payload_gate = asyncio.Event()
    subscribers = [
        FakeSubscriber(
            (_batch(1, 0),),
            SubscriberExit.TOKEN_REFRESH,
            connect_delay=0.0,
            payload_gate=payload_gate,
        ),
        FakeSubscriber((), SubscriberExit.STOPPED),
    ]
    resync_count = 0
    trust_seen: list[bool] = []

    async def mint_token() -> RealtimeTokenDto:
        return _token()

    async def resync() -> AccountAuthoritativeSnapshot:
        nonlocal resync_count
        resync_count += 1
        trust_seen.append(state.trusted)
        if resync_count == 1:
            payload_gate.set()
            await asyncio.sleep(0)
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

    # One batch arrived while the first authoritative snapshot was in flight,
    # so that connection requires a second snapshot before trust can return.
    # TOKEN_REFRESH then starts a fresh subscribed+resync cycle.
    assert resync_count == 3
    assert trust_seen == [False, False, False]
    assert state.trusted is True


def test_accepted_realtime_fill_is_linked_to_execution_journal(tmp_path: Path) -> None:
    journal = ExecutionJournal(tmp_path / "execution.sqlite3")
    intent = RuntimeOrderIntent(
        intent_id="intent-91",
        exchange_id="36",
        market_id="m1",
        tournament_id="t1",
        outcome_side=OutcomeSide.YES,
        action=OrderAction.BUY,
        quantity=1,
        limit_price_ticks=84,
        strategy_id="fixture",
        decision_observation_ns=111,
    )
    envelope = ExecutionEnvelope.placement(
        logical_operation_id="op-91",
        operation_kind=OperationKind.SINGLE_PLACEMENT,
        sink_mode=ExecutionMode.LIVE,
        idempotency_key="key-91",
        intents=(intent,),
        created_monotonic_ns=112,
    )
    journal.record_before_dispatch(envelope, (intent,))
    journal.record_event(
        logical_operation_id="op-91",
        logical_intent_id="intent-91",
        event_type="ACK",
        observed_monotonic_ns=113,
        exchange_id="36",
        exchange_order_id="91",
    )

    payload = _batch(1, 0)
    payload["fills"] = [
        {
            "orderId": 91,
            "exchangeId": "36",
            "marketId": "m1",
            "price": "0.42",
            "quantity": "1",
            "executedAt": "2026-09-28T21:00:00Z",
            "tournamentId": "t1",
        }
    ]
    state = AccountRealtimeStateEngine(tournament_id="t1")
    subscribers = [
        FakeSubscriber((payload,), SubscriberExit.STOPPED),
        FakeSubscriber((), SubscriberExit.STOPPED),
    ]

    async def mint_token() -> RealtimeTokenDto:
        return _token()

    async def resync() -> AccountAuthoritativeSnapshot:
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
            execution_journal=journal,
            clock_ns=lambda: 999,
        )
        await controller.run(stop_event=asyncio.Event())

    try:
        asyncio.run(scenario())
        fills = [
            event
            for event in journal.events("op-91")
            if event.event_type == "REALTIME_FILL"
        ]
        assert len(fills) == 1
        assert fills[0].exchange_order_id == "91"
        assert fills[0].source_timestamp == "2026-09-28T21:00:00+00:00"
        assert fills[0].quantity == "1"
        assert fills[0].price == "0.42"
    finally:
        journal.close()

def test_authoritative_account_snapshot_clears_only_covered_reservations() -> None:
    reservations = ExecutionReservationBook()
    covered = RuntimeOrderIntent(
        intent_id="intent-covered",
        exchange_id="36",
        market_id="m1",
        tournament_id="t1",
        outcome_side=OutcomeSide.YES,
        action=OrderAction.BUY,
        quantity=3,
        limit_price_ticks=100,
        strategy_id="fixture",
        decision_observation_ns=100,
    )
    in_flight = RuntimeOrderIntent(
        intent_id="intent-in-flight",
        exchange_id="37",
        market_id="m2",
        tournament_id="t1",
        outcome_side=OutcomeSide.YES,
        action=OrderAction.BUY,
        quantity=2,
        limit_price_ticks=100,
        strategy_id="fixture",
        decision_observation_ns=101,
    )
    reservations.reserve("op-covered", (covered,))
    reservations.reserve("op-in-flight", (in_flight,))
    reservations.bind_exchange_order(
        "intent-covered",
        "91",
        acknowledged_at=datetime(2026, 9, 28, 20, 59, 59, tzinfo=UTC),
    )
    reservations.bind_exchange_order(
        "intent-in-flight",
        "92",
        acknowledged_at=datetime(2026, 9, 28, 21, 0, 1, tzinfo=UTC),
    )
    state = AccountRealtimeStateEngine(
        tournament_id="t1",
        reservations=reservations,
    )

    state.apply_authoritative(_snapshot())

    assert reservations.intent_ids() == frozenset({"intent-in-flight"})
    assert state.trusted is True


def test_authoritative_account_snapshot_preserves_unacknowledged_reservation() -> None:
    reservations = ExecutionReservationBook()
    intent = RuntimeOrderIntent(
        intent_id="intent-unacked",
        exchange_id="36",
        market_id="m1",
        tournament_id="t1",
        outcome_side=OutcomeSide.YES,
        action=OrderAction.BUY,
        quantity=3,
        limit_price_ticks=100,
        strategy_id="fixture",
        decision_observation_ns=100,
    )
    reservations.reserve("op-unacked", (intent,))
    state = AccountRealtimeStateEngine(
        tournament_id="t1",
        reservations=reservations,
    )

    state.apply_authoritative(_snapshot())

    assert reservations.intent_ids() == frozenset({"intent-unacked"})
    assert state.trusted is True
