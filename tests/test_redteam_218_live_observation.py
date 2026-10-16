"""Red Team independent #84 OFF-NETWORK functional LIVE-observer review.

Uses exact frozen PR #218 source; SIG trading requests are intercepted by
httpx.MockTransport. No service startup, credential, venue, real order or API.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import cast

import httpx
import pytest
from pydantic import SecretStr

from predictions_cup.config import AppSettings
from predictions_cup.execution.journal import ExecutionJournal
from predictions_cup.execution.live import SigLiveSink
from predictions_cup.execution.models import ExecutionMode, LifecycleState, OperationKind
from predictions_cup.execution.planner import build_execution_plan
from predictions_cup.execution.reservations import ExecutionReservationBook
from predictions_cup.execution.sinks import ExecutionPlan
from predictions_cup.observe import (
    BoundedObservationEmitter,
    InMemoryObservationSink,
    ObservationKind,
)
from predictions_cup.observe.contracts import ObservationEmitter, VenueObservation
from predictions_cup.risk.core import RiskDecision
from predictions_cup.sig.client import RetryPolicy
from predictions_cup.sig.rest_governor import SigRestGovernor
from predictions_cup.sig.trading_client import SigTradingClient
from test_build009_live_sink import _intent, _permit


class FakeGovernor:
    async def acquire(self, priority: object) -> None:
        del priority

    def record_response(self, status: int, *, retry_after_seconds: float | None) -> None:
        del status, retry_after_seconds


class RejectingObserver:
    def __init__(self) -> None:
        self.attempts: list[VenueObservation] = []

    def emit(self, observation: VenueObservation) -> bool:
        self.attempts.append(observation)
        return False


def _make_plan(op: str) -> ExecutionPlan:
    intent = _intent()
    decision = RiskDecision(
        approved=True, reason="approved",
        execution_mode=ExecutionMode.LIVE,
        operation_kind=OperationKind.SINGLE_PLACEMENT,
        intents=(intent,), strategy_family="FV-TAKE",
        strategy_id="fixture", signal_value=0.025,
        fair_value=0.55, decision_observation_ns=100,
    )
    return build_execution_plan(
        decision, logical_operation_id=op, created_monotonic_ns=150,
    )


def _client(respond: httpx.MockTransport, *, attempts: int = 1) -> SigTradingClient:
    settings = AppSettings(
        sig_trade_credential=SecretStr("fake-offline-only"),
        tournament_id="t1", tournament_slug="cup",
    )
    return SigTradingClient(
        settings,
        governor=cast(SigRestGovernor, FakeGovernor()),
        transport=respond,
        retry_policy=RetryPolicy(
            max_attempts=attempts, base_delay_seconds=0,
            max_delay_seconds=0, jitter_ratio=0,
        ),
        sleep=asyncio.sleep,
    )


def _success(*, partial: bool = False) -> httpx.Response:
    return httpx.Response(200, json={
        "orderId": 91, "exchangeId": "36", "open": True,
        "remainingQuantity": "0.5" if partial else "1",
        "action": "buy", "side": "yes", "price": "0.5",
        "quantity": 1, "terminalReasonCode": None,
        "quantityTraded": "0.5" if partial else "0",
        "totalCost": "0.25" if partial else "0",
        "fillPrice": "0.5" if partial else None,
        "all": None,
    })


def test_84_fresh_fake_http_ack_partial_fill_identity_and_order(tmp_path: Path) -> None:
    plan = _make_plan("fresh-84-partial")
    wires: list[bytes] = []

    def respond(req: httpx.Request) -> httpx.Response:
        assert req.method == "POST"
        assert req.url.path.endswith("/orders")
        wires.append(req.content)
        return _success(partial=True)

    client = _client(httpx.MockTransport(respond))
    journal = ExecutionJournal(tmp_path / "fresh.sqlite3")
    reservations = ExecutionReservationBook()
    reservations.reserve(plan.envelope.logical_operation_id, plan.intents)
    collected = InMemoryObservationSink()
    emitter = BoundedObservationEmitter(collected, queue_max=100)
    ticks = iter((200, 300, 400))
    sink = SigLiveSink(
        client=client, journal=journal, permit=_permit(),
        reservations=reservations, observation_emitter=emitter,
        observation_process_instance_id="maker-session-84",
        clock_ns=lambda: next(ticks),
    )
    try:
        result = asyncio.run(sink.dispatch(plan))
        emitter.close()
        assert result.state is LifecycleState.OPEN
        assert wires == [plan.envelope.payload_json.encode()]
        kinds = [x.kind for x in collected.observations]
        assert kinds == [
            ObservationKind.DECISION_OBSERVED, ObservationKind.PLAN_CREATED,
            ObservationKind.REQUEST_ENQUEUED, ObservationKind.REQUEST_DISPATCHED,
            ObservationKind.RESPONSE_RECEIVED, ObservationKind.RESPONSE_PARSED,
            ObservationKind.ACK, ObservationKind.PARTIAL_FILL,
        ]
        assert all(
            x.process_instance_id == "maker-session-84"
            and x.logical_operation_id == plan.envelope.logical_operation_id
            and x.idempotency_key == plan.envelope.idempotency_key
            and x.observed_at.tzinfo is not None
            and x.observed_at.utcoffset() is not None
            and x.source == "BUILD_009_SIG_LIVE_SINK"
            for x in collected.observations
        )
        assert collected.observations[3].monotonic_ns == 300
        assert collected.observations[6].monotonic_ns == 400
        assert collected.observations[7].exchange_order_id == "91"
        assert collected.observations[7].logical_intent_id == plan.intents[0].intent_id
        assert sum(k is ObservationKind.REQUEST_DISPATCHED for k in kinds) == 1
        assert sum(k is ObservationKind.PARTIAL_FILL for k in kinds) == 1
        assert sum(k is ObservationKind.FILL for k in kinds) == 0
        assert any(
            x.event_type == "FILL_SUMMARY"
            for x in journal.events(plan.envelope.logical_operation_id)
        )
    finally:
        emitter.close()
        asyncio.run(client.aclose())
        journal.close()


def test_84_observer_rejection_must_stop_new_economic_wire(tmp_path: Path) -> None:
    plan = _make_plan("fresh-84-observer-deny")
    wires: list[bytes] = []

    def respond(req: httpx.Request) -> httpx.Response:
        wires.append(req.content)
        return _success()

    client = _client(httpx.MockTransport(respond))
    journal = ExecutionJournal(tmp_path / "rejecting.sqlite3")
    reservations = ExecutionReservationBook()
    reservations.reserve(plan.envelope.logical_operation_id, plan.intents)
    unavailable = RejectingObserver()
    sink = SigLiveSink(
        client=client, journal=journal, permit=_permit(),
        reservations=reservations,
        observation_emitter=cast(ObservationEmitter, unavailable),
        observation_process_instance_id="maker-session-84",
    )
    try:
        # Required for auditable LIVE admission: never silently send an
        # unobservable placement if queue/write admission has failed.
        asyncio.run(sink.dispatch(plan))
        assert unavailable.attempts
        assert not wires, "LIVE placement dispatched despite all observation emits being rejected"
    finally:
        asyncio.run(client.aclose())
        journal.close()


def test_84_cancellation_during_http_must_emit_uncertain(tmp_path: Path) -> None:
    plan = _make_plan("fresh-84-task-cancel")
    wires: list[bytes] = []

    def respond(req: httpx.Request) -> httpx.Response:
        wires.append(req.content)
        raise asyncio.CancelledError

    client = _client(httpx.MockTransport(respond))
    journal = ExecutionJournal(tmp_path / "cancelled.sqlite3")
    reservations = ExecutionReservationBook()
    reservations.reserve(plan.envelope.logical_operation_id, plan.intents)
    collected = InMemoryObservationSink()
    emitter = BoundedObservationEmitter(collected, queue_max=100)
    sink = SigLiveSink(
        client=client, journal=journal, permit=_permit(),
        reservations=reservations, observation_emitter=emitter,
        observation_process_instance_id="maker-session-84",
    )
    try:
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(sink.dispatch(plan))
        emitter.close()
        assert len(wires) == 1
        assert journal.lifecycle_state(plan.envelope.logical_operation_id) is (
            LifecycleState.UNCERTAIN
        )
        assert reservations.contains_operation(
            plan.envelope.logical_operation_id, plan.envelope.intent_ids
        )
        assert any(o.kind is ObservationKind.REQUEST_DISPATCHED for o in collected.observations)
        assert any(
            o.kind is ObservationKind.UNCERTAIN for o in collected.observations
        ), "HTTP task cancellation journalled UNCERTAIN but emitted no UNCERTAIN observation"
    finally:
        emitter.close()
        asyncio.run(client.aclose())
        journal.close()


def test_84_rate_limit_retry_must_emit_each_actual_wire_and_rate_limit(tmp_path: Path) -> None:
    plan = _make_plan("fresh-84-retry")
    wires: list[bytes] = []

    def respond(req: httpx.Request) -> httpx.Response:
        wires.append(req.content)
        if len(wires) == 1:
            return httpx.Response(429, json={
                "error": {"code": "RATE_LIMITED", "message": "offline mock"},
            }, headers={"Retry-After": "0"})
        return _success()

    client = _client(httpx.MockTransport(respond), attempts=2)
    journal = ExecutionJournal(tmp_path / "retry.sqlite3")
    reservations = ExecutionReservationBook()
    reservations.reserve(plan.envelope.logical_operation_id, plan.intents)
    collected = InMemoryObservationSink()
    emitter = BoundedObservationEmitter(collected, queue_max=100)
    sink = SigLiveSink(
        client=client, journal=journal, permit=_permit(),
        reservations=reservations, observation_emitter=emitter,
        observation_process_instance_id="maker-session-84",
    )
    try:
        event = asyncio.run(sink.dispatch(plan))
        emitter.close()
        assert event.state is LifecycleState.OPEN
        assert wires == [plan.envelope.payload_json.encode()] * 2
        kinds = [o.kind for o in collected.observations]
        assert kinds.count(ObservationKind.ACK) == 1
        assert kinds.count(ObservationKind.REQUEST_DISPATCHED) == len(wires), (
            "Observer misreports per-wire dispatch: internal 429 retry "
            "produced two actual HTTP attempts"
        )
        assert ObservationKind.RATE_LIMIT in kinds, (
            "429 transient attempt missing RATE_LIMIT observation"
        )
    finally:
        emitter.close()
        asyncio.run(client.aclose())
        journal.close()


def test_84_recovery_fake_http_same_operation_and_session(tmp_path: Path) -> None:
    """Recovery sink delivers per-dispatch telemetry without fake fresh IDs."""
    plan = _make_plan("recovery-84-partial")
    wires: list[bytes] = []

    def respond(req: httpx.Request) -> httpx.Response:
        wires.append(req.content)
        return _success(partial=True)

    client = _client(httpx.MockTransport(respond))
    journal = ExecutionJournal(tmp_path / "recovery.sqlite3")
    journal.record_before_dispatch(
        plan.envelope, plan.intents, submitted_monotonic_ns=160
    )
    journal.mark_state(
        plan.envelope.logical_operation_id, LifecycleState.UNCERTAIN, 170
    )
    reservations = ExecutionReservationBook()
    reservations.reserve(plan.envelope.logical_operation_id, plan.intents)
    collected = InMemoryObservationSink()
    emitter = BoundedObservationEmitter(collected, queue_max=100)
    sink = SigLiveSink(
        client=client, journal=journal, permit=_permit(),
        reservations=reservations, observation_emitter=emitter,
        observation_process_instance_id="maker-session-84",
        clock_ns=iter((200, 300, 400, 500)).__next__,
    )
    try:
        result = asyncio.run(sink.dispatch_recovery(plan.envelope, plan=plan))
        emitter.close()
        assert result.state is LifecycleState.OPEN
        assert wires == [plan.envelope.payload_json.encode()]
        kinds = [x.kind for x in collected.observations]
        assert kinds == [
            ObservationKind.PLAN_CREATED, ObservationKind.REQUEST_ENQUEUED,
            ObservationKind.REQUEST_DISPATCHED, ObservationKind.RESPONSE_RECEIVED,
            ObservationKind.RESPONSE_PARSED, ObservationKind.ACK,
            ObservationKind.PARTIAL_FILL,
        ]
        assert all(
            x.logical_operation_id == plan.envelope.logical_operation_id
            and x.process_instance_id == "maker-session-84"
            for x in collected.observations
        )
        assert kinds.count(ObservationKind.ACK) == 1
        assert kinds.count(ObservationKind.PARTIAL_FILL) == 1
        assert any(
            x.event_type == "RESUBMISSION"
            for x in journal.events(plan.envelope.logical_operation_id)
        )
    finally:
        emitter.close()
        asyncio.run(client.aclose())
        journal.close()


def test_84_transient_rate_limit_visibility_even_if_retry_acknowledged(
    tmp_path: Path,
) -> None:
    """A transient 429 is still a wire outcome requiring RATE_LIMIT evidence."""
    plan = _make_plan("fresh-84-transient-429")
    wires: list[bytes] = []

    def respond(req: httpx.Request) -> httpx.Response:
        wires.append(req.content)
        if len(wires) == 1:
            return httpx.Response(
                429, json={"error": {"code": "RATE_LIMITED", "message": "retry"}},
                headers={"Retry-After": "0"},
            )
        return _success()

    client = _client(httpx.MockTransport(respond), attempts=2)
    journal = ExecutionJournal(tmp_path / "rate-limit.sqlite3")
    reservations = ExecutionReservationBook()
    reservations.reserve(plan.envelope.logical_operation_id, plan.intents)
    collected = InMemoryObservationSink()
    emitter = BoundedObservationEmitter(collected, queue_max=100)
    sink = SigLiveSink(
        client=client, journal=journal, permit=_permit(),
        reservations=reservations, observation_emitter=emitter,
        observation_process_instance_id="maker-session-84",
    )
    try:
        event = asyncio.run(sink.dispatch(plan))
        emitter.close()
        assert event.state is LifecycleState.OPEN
        assert len(wires) == 2
        assert any(x.kind is ObservationKind.ACK for x in collected.observations)
        assert any(
            x.kind is ObservationKind.RATE_LIMIT for x in collected.observations
        ), "Successful bounded retry hid preceding rate-limit attempt from observer"
    finally:
        emitter.close()
        asyncio.run(client.aclose())
        journal.close()
