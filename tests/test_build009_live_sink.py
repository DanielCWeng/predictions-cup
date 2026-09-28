from __future__ import annotations

import asyncio
from pathlib import Path
from typing import cast

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
from predictions_cup.risk.core import RiskDecision
from predictions_cup.runtime import OrderAction, OutcomeSide
from predictions_cup.sig.trading_client import SigTradingClient
from predictions_cup.sig.trading_dto import (
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

    async def cancel_order(self, order_id: int) -> object:
        assert order_id == 91
        return {"cancelled": True}


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
        mode=ExecutionMode.LIVE,
        created_monotonic_ns=150,
    )
    ticks = iter((200, 300, 400))
    journal = ExecutionJournal(tmp_path / "execution.sqlite3")
    sink = SigLiveSink(
        client=cast(SigTradingClient, FakeTradingClient()),
        journal=journal,
        clock_ns=lambda: next(ticks),
    )

    try:
        event = asyncio.run(sink.dispatch(plan))
        assert event.state is LifecycleState.OPEN
        events = journal.events("op-91")
        submission = next(item for item in events if item.event_type == "SUBMISSION")
        dispatch = next(
            item for item in events if item.event_type == "NETWORK_DISPATCH"
        )
        ack = next(item for item in events if item.event_type == "ACK")

        assert submission.decision_observation_ns == 100
        assert submission.decision_monotonic_ns == 150
        assert submission.observed_monotonic_ns == 200
        assert submission.signal_value == 0.025
        assert submission.fair_value == 0.55
        assert dispatch.observed_monotonic_ns == 300
        assert ack.observed_monotonic_ns == 400
    finally:
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

        assert submission.observed_monotonic_ns == 600
        assert cancel_submitted.observed_monotonic_ns == 700
        assert ack.observed_monotonic_ns == 800
    finally:
        journal.close()
