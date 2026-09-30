from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import cast

import pytest
from pydantic import SecretStr

from predictions_cup.config import AppSettings
from predictions_cup.execution.interlocks import LiveExecutionPermit, assert_live_interlocks
from predictions_cup.execution.journal import ExecutionJournal
from predictions_cup.execution.live import SigLiveSink
from predictions_cup.execution.models import (
    ExecutionEnvelope,
    ExecutionMode,
    LifecycleState,
    OperationKind,
    RuntimeOrderIntent,
)
from predictions_cup.execution.planner import build_execution_plan
from predictions_cup.execution.reservations import ExecutionReservationBook
from predictions_cup.observe import (
    BoundedObservationEmitter,
    InMemoryObservationSink,
    ObservationKind,
)
from predictions_cup.risk.core import RiskDecision
from predictions_cup.runtime import OrderAction, OutcomeSide
from predictions_cup.sig.errors import SigClientRequestError, SigExecutionUncertainError
from predictions_cup.sig.trading_client import SigTradingClient
from predictions_cup.sig.trading_dto import (
    BatchOrderResponseDto,
    MultiLegOrderRequestDto,
    MultiLegResponseDto,
    SingleOrderRequestDto,
    SingleOrderResponseDto,
)


class FakeTradingClient:
    async def place_order(
        self,
        request: SingleOrderRequestDto,
    ) -> SingleOrderResponseDto:
        assert request.idempotency_key
        return SingleOrderResponseDto.model_validate(
            {
                "orderId": 91,
                "exchangeId": "36",
                "open": True,
                "remainingQuantity": "1",
                "action": "buy",
                "side": "yes",
                "price": "0.5",
                "quantity": 1,
                "terminalReasonCode": None,
                "quantityTraded": "0",
                "totalCost": "0",
                "fillPrice": None,
                "all": None,
            }
        )

    async def place_order_payload(
        self,
        payload_json: str,
    ) -> SingleOrderResponseDto:
        request = SingleOrderRequestDto.model_validate(json.loads(payload_json))
        return await self.place_order(request)

    async def cancel_order(self, order_id: int) -> object:
        assert order_id == 91
        return {"cancelled": True}


class FakeRejectedTradingClient(FakeTradingClient):
    async def place_order_payload(
        self,
        payload_json: str,
    ) -> SingleOrderResponseDto:
        del payload_json
        raise SigClientRequestError(
            status_code=422,
            code="VALIDATION_ERROR",
            safe_message="fixture terminal rejection",
        )


class FakeRelationshipRejectedTradingClient(FakeTradingClient):
    async def place_multi_leg_payload(
        self,
        payload_json: str,
    ) -> MultiLegResponseDto:
        MultiLegOrderRequestDto.model_validate(json.loads(payload_json))
        raise SigClientRequestError(
            status_code=422,
            code="RELATIONSHIP_VIOLATION",
            safe_message="fixture relationship rejection",
        )


class FakeUncertainTradingClient(FakeTradingClient):
    @staticmethod
    def _raise_uncertain(payload_json: str) -> None:
        del payload_json
        raise SigExecutionUncertainError(
            status_code=500,
            code="INTERNAL_ERROR",
            safe_message="fixture generic server uncertainty",
        )

    async def place_order_payload(
        self,
        payload_json: str,
    ) -> SingleOrderResponseDto:
        self._raise_uncertain(payload_json)
        raise AssertionError("unreachable")

    async def place_batch_payload(
        self,
        payload_json: str,
    ) -> BatchOrderResponseDto:
        self._raise_uncertain(payload_json)
        raise AssertionError("unreachable")

    async def place_multi_leg_payload(
        self,
        payload_json: str,
    ) -> MultiLegResponseDto:
        self._raise_uncertain(payload_json)
        raise AssertionError("unreachable")


def _permit() -> LiveExecutionPermit:
    settings = AppSettings(
        sig_trade_credential=SecretStr("trade-secret"),
        tournament_id="t1",
        tournament_slug="cup",
        trading_enabled=True,
        execution_mode="LIVE",
        global_kill_switch=False,
        risk_max_order_size=10,
        risk_max_gross_exposure=100.0,
        risk_max_per_market_exposure=100.0,
        risk_max_open_order_exposure=100.0,
        risk_max_concurrent_open_orders=10,
    )
    return assert_live_interlocks(
        settings,
        explicit_live_invocation=True,
        account_trusted=True,
    )


def _intent() -> RuntimeOrderIntent:
    return RuntimeOrderIntent(
        intent_id="intent-91",
        exchange_id="36",
        market_id="m1",
        tournament_id="t1",
        outcome_side=OutcomeSide.YES,
        action=OrderAction.BUY,
        quantity=1,
        limit_price_ticks=100,
        strategy_id="fixture",
        decision_observation_ns=100,
    )


def test_live_sink_records_observation_decision_dispatch_and_ack_clocks(
    tmp_path: Path,
) -> None:
    intent = _intent()
    decision = RiskDecision(
        approved=True,
        reason="approved",
        execution_mode=ExecutionMode.LIVE,
        operation_kind=OperationKind.SINGLE_PLACEMENT,
        intents=(intent,),
        strategy_family="FV-TAKE",
        strategy_id="fixture",
        signal_value=0.025,
        fair_value=0.55,
        decision_observation_ns=100,
    )
    plan = build_execution_plan(
        decision,
        logical_operation_id="op-91",
        created_monotonic_ns=150,
    )
    ticks = iter((200, 300, 400))
    journal = ExecutionJournal(tmp_path / "execution.sqlite3")
    reservations = ExecutionReservationBook()
    reservations.reserve(plan.envelope.logical_operation_id, plan.intents)
    observation_sink = InMemoryObservationSink()
    emitter = BoundedObservationEmitter(observation_sink, queue_max=100)
    sink = SigLiveSink(
        client=cast(SigTradingClient, FakeTradingClient()),
        journal=journal,
        permit=_permit(),
        reservations=reservations,
        clock_ns=lambda: next(ticks),
        observation_emitter=emitter,
        observation_process_instance_id="test-process",
    )

    try:
        event = asyncio.run(sink.dispatch(plan))
        emitter.close()
        assert event.state is LifecycleState.OPEN
        events = journal.events("op-91")
        submission = next(item for item in events if item.event_type == "SUBMISSION")
        dispatch = next(
            item for item in events if item.event_type == "NETWORK_DISPATCH"
        )
        ack = next(item for item in events if item.event_type == "ACK")

        assert submission.tournament_id == "t1"
        assert submission.decision_observation_ns == 100
        assert submission.decision_monotonic_ns == 150
        assert submission.observed_monotonic_ns == 200
        assert submission.signal_value == 0.025
        assert submission.fair_value == 0.55
        assert dispatch.tournament_id == "t1"
        assert dispatch.observed_monotonic_ns == 300
        assert ack.tournament_id == "t1"
        assert ack.observed_monotonic_ns == 400
        kinds = [item.kind for item in observation_sink.observations]
        assert kinds == [
            ObservationKind.DECISION_OBSERVED,
            ObservationKind.PLAN_CREATED,
            ObservationKind.REQUEST_ENQUEUED,
            ObservationKind.REQUEST_DISPATCHED,
            ObservationKind.RESPONSE_RECEIVED,
            ObservationKind.RESPONSE_PARSED,
            ObservationKind.ACK,
        ]
        assert observation_sink.observations[-1].logical_operation_id == "op-91"
        assert observation_sink.observations[-1].idempotency_key == plan.envelope.idempotency_key
    finally:
        emitter.close()
        journal.close()


def test_cancel_dispatch_timestamp_is_sampled_after_durable_identity(
    tmp_path: Path,
) -> None:
    envelope = ExecutionEnvelope.cancellation(
        logical_operation_id="cancel-91",
        operation_kind=OperationKind.SINGLE_CANCELLATION,
        sink_mode=ExecutionMode.LIVE,
        created_monotonic_ns=500,
        order_id=91,
        tournament_id="t1",
    )
    ticks = iter((600, 700, 800))
    journal = ExecutionJournal(tmp_path / "execution.sqlite3")
    sink = SigLiveSink(
        client=cast(SigTradingClient, FakeTradingClient()),
        journal=journal,
        permit=_permit(),
        reservations=ExecutionReservationBook(),
        clock_ns=lambda: next(ticks),
    )

    try:
        event = asyncio.run(sink.cancel(envelope))
        assert event.state is LifecycleState.CANCELLED
        events = journal.events("cancel-91")
        submission = next(item for item in events if item.event_type == "SUBMISSION")
        cancel_submitted = next(
            item for item in events if item.event_type == "CANCEL_SUBMITTED"
        )
        ack = next(item for item in events if item.event_type == "CANCEL_ACK")

        assert submission.tournament_id == "t1"
        assert submission.observed_monotonic_ns == 600
        assert cancel_submitted.tournament_id == "t1"
        assert cancel_submitted.observed_monotonic_ns == 700
        assert ack.tournament_id == "t1"
        assert ack.observed_monotonic_ns == 800
    finally:
        journal.close()

def test_live_sink_rejects_unreserved_plan_before_network_dispatch(
    tmp_path: Path,
) -> None:
    intent = _intent()
    decision = RiskDecision(
        approved=True,
        reason="approved",
        execution_mode=ExecutionMode.LIVE,
        operation_kind=OperationKind.SINGLE_PLACEMENT,
        intents=(intent,),
        strategy_family="FV-TAKE",
        strategy_id="fixture",
        signal_value=0.025,
        fair_value=0.55,
        decision_observation_ns=100,
    )
    plan = build_execution_plan(
        decision,
        logical_operation_id="op-unreserved",
        created_monotonic_ns=150,
    )
    journal = ExecutionJournal(tmp_path / "unreserved.sqlite3")
    sink = SigLiveSink(
        client=cast(SigTradingClient, FakeTradingClient()),
        journal=journal,
        permit=_permit(),
        reservations=ExecutionReservationBook(),
    )
    try:
        with pytest.raises(ValueError, match="missing its synchronous reservation"):
            asyncio.run(sink.dispatch(plan))
        assert journal.unresolved() == ()
    finally:
        journal.close()


def test_terminal_single_rejection_releases_only_its_operation_reservation(
    tmp_path: Path,
) -> None:
    intent = _intent()
    decision = RiskDecision(
        approved=True,
        reason="approved",
        execution_mode=ExecutionMode.LIVE,
        operation_kind=OperationKind.SINGLE_PLACEMENT,
        intents=(intent,),
        strategy_family="FV-TAKE",
        strategy_id="fixture",
        signal_value=0.025,
        fair_value=0.55,
        decision_observation_ns=100,
    )
    plan = build_execution_plan(
        decision,
        logical_operation_id="op-rejected",
        created_monotonic_ns=150,
    )
    unrelated = RuntimeOrderIntent(
        intent_id="intent-unrelated",
        exchange_id="37",
        market_id="m1",
        tournament_id="t1",
        outcome_side=OutcomeSide.YES,
        action=OrderAction.BUY,
        quantity=1,
        limit_price_ticks=100,
        strategy_id="other",
        decision_observation_ns=101,
    )
    reservations = ExecutionReservationBook()
    reservations.reserve(plan.envelope.logical_operation_id, plan.intents)
    reservations.reserve("op-unrelated", (unrelated,))
    ticks = iter((200, 300, 400))
    journal = ExecutionJournal(tmp_path / "rejected.sqlite3")
    sink = SigLiveSink(
        client=cast(SigTradingClient, FakeRejectedTradingClient()),
        journal=journal,
        permit=_permit(),
        reservations=reservations,
        clock_ns=lambda: next(ticks),
    )
    try:
        with pytest.raises(SigClientRequestError):
            asyncio.run(sink.dispatch(plan))
        assert reservations.intent_ids() == frozenset({"intent-unrelated"})
        assert journal.unresolved() == ()
        rejected = journal.events("op-rejected")
        assert any(item.event_type == "REJECTED" for item in rejected)
    finally:
        journal.close()


def test_terminal_atomic_relationship_rejection_releases_bundle_reservation(
    tmp_path: Path,
) -> None:
    first = _intent()
    second = RuntimeOrderIntent(
        intent_id="intent-92",
        exchange_id="37",
        market_id="m1",
        tournament_id="t1",
        outcome_side=OutcomeSide.NO,
        action=OrderAction.BUY,
        quantity=1,
        limit_price_ticks=100,
        strategy_id="fixture",
        decision_observation_ns=100,
    )
    decision = RiskDecision(
        approved=True,
        reason="approved",
        execution_mode=ExecutionMode.LIVE,
        operation_kind=OperationKind.ATOMIC_MULTI_LEG,
        intents=(first, second),
        relationship_constraint="22222222-2222-2222-2222-222222222222",
        strategy_family="STRUCT",
        strategy_id="fixture",
        signal_value=0.025,
        fair_value=0.55,
        decision_observation_ns=100,
    )
    plan = build_execution_plan(
        decision,
        logical_operation_id="op-atomic-rejected",
        created_monotonic_ns=150,
    )
    reservations = ExecutionReservationBook()
    reservations.reserve(plan.envelope.logical_operation_id, plan.intents)
    ticks = iter((200, 300, 400))
    journal = ExecutionJournal(tmp_path / "atomic-rejected.sqlite3")
    sink = SigLiveSink(
        client=cast(SigTradingClient, FakeRelationshipRejectedTradingClient()),
        journal=journal,
        permit=_permit(),
        reservations=reservations,
        clock_ns=lambda: next(ticks),
    )
    try:
        with pytest.raises(SigClientRequestError) as caught:
            asyncio.run(sink.dispatch(plan))
        assert caught.value.code == "RELATIONSHIP_VIOLATION"
        assert reservations.intent_ids() == frozenset()
        assert journal.unresolved() == ()
    finally:
        journal.close()


def test_uncertain_dispatch_keeps_local_reservation_until_reconciliation(
    tmp_path: Path,
) -> None:
    intent = _intent()
    decision = RiskDecision(
        approved=True,
        reason="approved",
        execution_mode=ExecutionMode.LIVE,
        operation_kind=OperationKind.SINGLE_PLACEMENT,
        intents=(intent,),
        strategy_family="FV-TAKE",
        strategy_id="fixture",
        signal_value=0.025,
        fair_value=0.55,
        decision_observation_ns=100,
    )
    plan = build_execution_plan(
        decision,
        logical_operation_id="op-uncertain",
        created_monotonic_ns=150,
    )
    reservations = ExecutionReservationBook()
    reservations.reserve(plan.envelope.logical_operation_id, plan.intents)
    ticks = iter((200, 300, 400))
    journal = ExecutionJournal(tmp_path / "uncertain.sqlite3")
    sink = SigLiveSink(
        client=cast(SigTradingClient, FakeUncertainTradingClient()),
        journal=journal,
        permit=_permit(),
        reservations=reservations,
        clock_ns=lambda: next(ticks),
    )
    try:
        with pytest.raises(SigExecutionUncertainError):
            asyncio.run(sink.dispatch(plan))
        assert reservations.intent_ids() == frozenset({"intent-91"})
        unresolved = journal.unresolved()
        assert len(unresolved) == 1
        assert unresolved[0].lifecycle_state is LifecycleState.UNCERTAIN
    finally:
        journal.close()

@pytest.mark.parametrize(
    "operation_kind",
    (
        OperationKind.SINGLE_PLACEMENT,
        OperationKind.BEST_EFFORT_BATCH,
        OperationKind.ATOMIC_MULTI_LEG,
    ),
)
def test_generic_5xx_execution_outcome_stays_uncertain_and_reserved(
    tmp_path: Path,
    operation_kind: OperationKind,
) -> None:
    first = _intent()
    intents: tuple[RuntimeOrderIntent, ...] = (first,)
    relationship_constraint: str | None = None
    if operation_kind is not OperationKind.SINGLE_PLACEMENT:
        second = RuntimeOrderIntent(
            intent_id="intent-92",
            exchange_id="37",
            market_id="m1",
            tournament_id="t1",
            outcome_side=OutcomeSide.NO,
            action=OrderAction.BUY,
            quantity=1,
            limit_price_ticks=100,
            strategy_id="fixture",
            decision_observation_ns=100,
        )
        intents = (first, second)
    if operation_kind is OperationKind.ATOMIC_MULTI_LEG:
        relationship_constraint = "22222222-2222-2222-2222-222222222222"

    decision = RiskDecision(
        approved=True,
        reason="approved",
        execution_mode=ExecutionMode.LIVE,
        operation_kind=operation_kind,
        intents=intents,
        relationship_constraint=relationship_constraint,
        strategy_family="STRUCT" if relationship_constraint is not None else "FV-TAKE",
        strategy_id="fixture",
        signal_value=0.025,
        fair_value=0.55,
        decision_observation_ns=100,
    )
    plan = build_execution_plan(
        decision,
        logical_operation_id=f"op-5xx-{operation_kind.value}",
        created_monotonic_ns=150,
    )
    reservations = ExecutionReservationBook()
    reservations.reserve(plan.envelope.logical_operation_id, plan.intents)
    ticks = iter((200, 300, 400))
    journal = ExecutionJournal(tmp_path / f"{operation_kind.value}.sqlite3")
    sink = SigLiveSink(
        client=cast(SigTradingClient, FakeUncertainTradingClient()),
        journal=journal,
        permit=_permit(),
        reservations=reservations,
        clock_ns=lambda: next(ticks),
    )
    try:
        with pytest.raises(SigExecutionUncertainError) as caught:
            asyncio.run(sink.dispatch(plan))
        assert caught.value.status_code == 500
        assert caught.value.code == "INTERNAL_ERROR"
        assert reservations.intent_ids() == frozenset(
            intent.intent_id for intent in plan.intents
        )
        unresolved = journal.unresolved()
        assert len(unresolved) == 1
        assert unresolved[0].logical_operation_id == plan.envelope.logical_operation_id
        assert unresolved[0].lifecycle_state is LifecycleState.UNCERTAIN
    finally:
        journal.close()

