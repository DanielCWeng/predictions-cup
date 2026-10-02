from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

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
    assert factory_calls == [("user:profile-1", "account_batch")]
    assert state.trusted is True


def test_fill_resync_keeps_token_and_socket() -> None:
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
    assert subscription_count == 1
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

    class QuietSubscriber:
        async def run(
            self,
            *,
            on_batch: Any,
            on_connected: Any,
            stop_event: asyncio.Event,
            on_maintenance: Any = None,
        ) -> SubscriberExit:
            del on_batch, on_maintenance
            on_connected()
            await stop_event.wait()
            return SubscriberExit.STOPPED

    def factory(
        *,
        topic: str,
        token: RealtimeTokenDto,
        event_name: str,
        maintenance_interval_seconds: float,
    ) -> QuietSubscriber:
        del topic, token, event_name, maintenance_interval_seconds
        return QuietSubscriber()

    async def scenario() -> None:
        stop = asyncio.Event()
        task = asyncio.create_task(AccountRealtimeController(
            state=state,
            mint_token=mint_token,
            authoritative_resync=resync,
            subscriber_factory=factory,
            refresh_interval_seconds=0.001,
        ).run(stop_event=stop))
        for _ in range(2000):
            if resync_count >= 2:
                break
            await asyncio.sleep(0.001)
        stop.set()
        await task

    asyncio.run(scenario())

    assert token_count == 1
    assert resync_count >= 2
    assert state.trusted is True
    assert state.last_authoritative_observed_at == observed_times[1]


def test_periodic_refresh_recovers_trust_after_socket_disconnect() -> None:
    state = AccountRealtimeStateEngine(tournament_id="t1")
    resync_count = 0
    refreshed = asyncio.Event()
    subscriptions = 0

    async def mint_token() -> RealtimeTokenDto:
        return _token()

    async def resync() -> AccountAuthoritativeSnapshot:
        nonlocal resync_count
        resync_count += 1
        if resync_count >= 2:
            refreshed.set()
        return _snapshot()

    class DisconnectThenWaitSubscriber:
        async def run(
            self,
            *,
            on_batch: Any,
            on_connected: Any,
            stop_event: asyncio.Event,
            on_maintenance: Any = None,
        ) -> SubscriberExit:
            del on_batch, on_maintenance
            on_connected()
            if subscriptions == 1:
                return SubscriberExit.SOCKET_ERROR
            await stop_event.wait()
            return SubscriberExit.STOPPED

    def factory(
        *,
        topic: str,
        token: RealtimeTokenDto,
        event_name: str,
        maintenance_interval_seconds: float,
    ) -> DisconnectThenWaitSubscriber:
        nonlocal subscriptions
        del topic, token, event_name, maintenance_interval_seconds
        subscriptions += 1
        return DisconnectThenWaitSubscriber()

    async def scenario() -> None:
        stop = asyncio.Event()
        controller = AccountRealtimeController(
            state=state,
            mint_token=mint_token,
            authoritative_resync=resync,
            subscriber_factory=factory,
            refresh_interval_seconds=0.001,
        )
        task = asyncio.create_task(controller.run(stop_event=stop))
        await asyncio.wait_for(refreshed.wait(), timeout=1.0)
        await asyncio.sleep(0)
        stop.set()
        await task

    asyncio.run(scenario())

    assert subscriptions >= 2
    assert resync_count >= 2
    assert state.trusted is True


def test_periodic_rest_failure_revokes_account_trust() -> None:
    state = AccountRealtimeStateEngine(tournament_id="t1")
    resync_count = 0
    failure_seen = asyncio.Event()

    async def mint_token() -> RealtimeTokenDto:
        return _token()

    async def resync() -> AccountAuthoritativeSnapshot:
        nonlocal resync_count
        resync_count += 1
        if resync_count == 2:
            failure_seen.set()
            raise RuntimeError("REST unavailable")
        return _snapshot()

    class QuietSubscriber:
        async def run(
            self,
            *,
            on_batch: Any,
            on_connected: Any,
            stop_event: asyncio.Event,
            on_maintenance: Any = None,
        ) -> SubscriberExit:
            del on_batch, on_maintenance
            on_connected()
            await stop_event.wait()
            return SubscriberExit.STOPPED

    def factory(
        *,
        topic: str,
        token: RealtimeTokenDto,
        event_name: str,
        maintenance_interval_seconds: float,
    ) -> QuietSubscriber:
        del topic, token, event_name, maintenance_interval_seconds
        return QuietSubscriber()

    async def scenario() -> None:
        stop = asyncio.Event()
        task = asyncio.create_task(AccountRealtimeController(
            state=state,
            mint_token=mint_token,
            authoritative_resync=resync,
            subscriber_factory=factory,
            refresh_interval_seconds=0.001,
        ).run(stop_event=stop))
        await asyncio.wait_for(failure_seen.wait(), timeout=1.0)
        stop.set()
        await task

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


def test_fill_during_resync_is_journaled_once_with_take_source(tmp_path: Path) -> None:
    journal = ExecutionJournal(tmp_path / "execution.sqlite3")
    intent = RuntimeOrderIntent(
        intent_id="residual-taker-001:decision:36",
        exchange_id="36",
        market_id="m1",
        tournament_id="t1",
        outcome_side=OutcomeSide.YES,
        action=OrderAction.BUY,
        quantity=2,
        limit_price_ticks=None,
        strategy_id="residual-taker-001",
        decision_observation_ns=111,
    )
    envelope = ExecutionEnvelope.placement(
        logical_operation_id="op-92:take:36",
        operation_kind=OperationKind.SINGLE_PLACEMENT,
        sink_mode=ExecutionMode.LIVE,
        idempotency_key="key-92",
        intents=(intent,),
        created_monotonic_ns=112,
    )
    journal.record_before_dispatch(envelope, (intent,))
    journal.record_event(
        logical_operation_id=envelope.logical_operation_id,
        logical_intent_id=intent.intent_id,
        event_type="ACK",
        observed_monotonic_ns=113,
        exchange_id="36",
        exchange_order_id="92",
    )

    payload = _batch(1, 0)
    payload["fills"] = [
        {
            "orderId": 92,
            "exchangeId": "36",
            "marketId": "m1",
            "price": "0.42",
            "quantity": "2",
            "executedAt": "2026-10-02T13:00:00.000710Z",
            "tournamentId": "t1",
        }
    ]
    controller = AccountRealtimeController(
        state=AccountRealtimeStateEngine(tournament_id="t1"),
        mint_token=lambda: _unused_token(),
        authoritative_resync=lambda: _unused_snapshot(),
        execution_journal=journal,
        clock_ns=lambda: 999,
    )
    controller._resyncing = True

    async def scenario() -> None:
        await controller._handle_batch("user:profile-1", payload, datetime.now(UTC))
        await controller._handle_batch("user:profile-1", payload, datetime.now(UTC))

    try:
        asyncio.run(scenario())
        assert not journal.record_fill_event_once(
            logical_operation_id=envelope.logical_operation_id,
            logical_intent_id=intent.intent_id,
            event_type="AUTHORITATIVE_FILL",
            observed_monotonic_ns=1_000,
            source_timestamp="2026-10-02T13:00:00.000000+00:00",
            exchange_id="36",
            exchange_order_id="92",
            fill_id="sig-fill-92",
            quantity="-2.0",
            price="0.580",
            strategy_family="TAKE",
            strategy_id="residual-taker-001",
            detail_json='{"fill_source":"TAKE"}',
        )
        fills = [
            event
            for event in journal.events(envelope.logical_operation_id)
            if event.event_type == "REALTIME_FILL"
        ]
        assert len(fills) == 1
        assert fills[0].exchange_order_id == "92"
        assert fills[0].logical_intent_id == intent.intent_id
        assert fills[0].strategy_family == "TAKE"
        assert fills[0].strategy_id == "residual-taker-001"
        assert json.loads(fills[0].detail_json or "{}")["fill_source"] == "TAKE"
    finally:
        journal.close()


async def _unused_token() -> RealtimeTokenDto:
    raise AssertionError("token minting is not used by this test")


async def _unused_snapshot() -> AccountAuthoritativeSnapshot:
    raise AssertionError("authoritative resync is not used by this test")

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


def test_periodic_refresh_retries_when_activity_lands_during_resync() -> None:
    state = AccountRealtimeStateEngine(tournament_id="t1")
    resync_count = 0
    controller: AccountRealtimeController | None = None

    async def mint_token() -> RealtimeTokenDto:
        return _token()

    async def resync() -> AccountAuthoritativeSnapshot:
        nonlocal resync_count
        resync_count += 1
        if resync_count == 1:
            # Our own order ACK arrives on the socket mid-resync.
            assert controller is not None
            await controller._handle_batch(
                "user:profile-1", _batch(1, 0), datetime.now(UTC)
            )
        return _snapshot()

    async def scenario() -> None:
        nonlocal controller
        controller = AccountRealtimeController(
            state=state,
            mint_token=mint_token,
            authoritative_resync=resync,
            subscriber_factory=lambda **_: None,  # type: ignore[arg-type]
        )
        await controller._refresh_authoritative(datetime.now(UTC))

    asyncio.run(scenario())

    assert resync_count == 2
    assert state.trusted is True


@pytest.mark.parametrize(
    ("scenario", "expected_outcome", "expected_resync_calls", "expected_batches"),
    [
        ("success", "trusted", 1, 0),
        ("retry", "trusted", 2, 1),
        ("exhausted", "activity_exhausted", 3, 3),
        ("exception", "exception:RuntimeError", 1, 0),
    ],
)
def test_refresh_cycle_telemetry_is_correlated_and_payload_free(
    caplog: pytest.LogCaptureFixture,
    scenario: str,
    expected_outcome: str,
    expected_resync_calls: int,
    expected_batches: int,
) -> None:
    state = AccountRealtimeStateEngine(tournament_id="t1")
    controller: AccountRealtimeController | None = None
    resync_count = 0
    payload_marker = "private-payload-marker"
    resync_payload: dict[str, object] = {
        "orderUpdates": [payload_marker, payload_marker],
        "fills": [payload_marker],
        "settlements": [payload_marker, payload_marker, payload_marker],
        "refunds": [payload_marker],
        "collateralChanges": [payload_marker, payload_marker],
    }

    async def mint_token() -> RealtimeTokenDto:
        return _token()

    async def resync() -> AccountAuthoritativeSnapshot:
        nonlocal resync_count
        resync_count += 1
        if scenario == "exception":
            raise RuntimeError
        if scenario == "retry" and resync_count == 1:
            assert controller is not None
            await controller._handle_batch(
                "user:profile-1", resync_payload, datetime.now(UTC)
            )
        if scenario == "exhausted":
            assert controller is not None
            await controller._handle_batch(
                "user:profile-1", resync_payload, datetime.now(UTC)
            )
        return _snapshot()

    async def scenario_run() -> None:
        nonlocal controller
        controller = AccountRealtimeController(
            state=state,
            mint_token=mint_token,
            authoritative_resync=resync,
            subscriber_factory=lambda **_: None,  # type: ignore[arg-type]
        )
        await controller._refresh_authoritative(
            datetime.now(UTC), trigger_reason="test_refresh"
        )

    with caplog.at_level(logging.INFO, logger="predictions_cup.sig.account_runtime"):
        asyncio.run(scenario_run())

    cycle_logs = [
        record.getMessage()
        for record in caplog.records
        if "SIG account refresh cycle refresh_id=" in record.getMessage()
    ]
    assert len(cycle_logs) == 1
    cycle_log = cycle_logs[0]
    assert "refresh_id=" in cycle_log
    assert "trigger_reason=test_refresh" in cycle_log
    assert "duration_ms=" in cycle_log
    duration_ms = float(cycle_log.split("duration_ms=", maxsplit=1)[1].split()[0])
    assert duration_ms >= 0
    assert f"resync_calls={expected_resync_calls}" in cycle_log
    assert "generation_before=" in cycle_log
    assert "generation_after=" in cycle_log
    assert f"outcome={expected_outcome}" in cycle_log
    assert resync_count == expected_resync_calls

    batch_logs = [
        record.getMessage()
        for record in caplog.records
        if "SIG account resync batch observed:" in record.getMessage()
    ]
    assert len(batch_logs) == expected_batches
    assert all(
        "order_updates=2 fills=1 settlements=3 refunds=1 collateral=2" in message
        for message in batch_logs
    )
    assert all(payload_marker not in record.getMessage() for record in caplog.records)
