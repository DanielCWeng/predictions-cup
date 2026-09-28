"""LIVE sink: durable journal first, then exact SIG write adapter dispatch."""

from __future__ import annotations

import json
from collections.abc import Callable
from time import monotonic_ns

from predictions_cup.execution.journal import ExecutionJournal
from predictions_cup.execution.models import (
    ExecutionEnvelope,
    ExecutionEvent,
    ExecutionMode,
    LifecycleState,
    OperationKind,
)
from predictions_cup.execution.sinks import ExecutionPlan
from predictions_cup.sig.errors import SigApiError, SigExecutionUncertainError
from predictions_cup.sig.trading_client import SigTradingClient
from predictions_cup.sig.trading_dto import (
    BatchOrderRequestDto,
    MultiLegOrderRequestDto,
    SingleOrderRequestDto,
)

ClockNs = Callable[[], int]


class SigLiveSink:
    """No strategy reaches this class without already passing central Risk."""

    def __init__(
        self,
        *,
        client: SigTradingClient,
        journal: ExecutionJournal,
        clock_ns: ClockNs = monotonic_ns,
    ) -> None:
        self._client = client
        self._journal = journal
        self._clock_ns = clock_ns

    async def dispatch(self, plan: ExecutionPlan) -> ExecutionEvent:
        envelope = plan.envelope
        if envelope.sink_mode is not ExecutionMode.LIVE:
            raise ValueError("SigLiveSink accepts LIVE envelopes only")
        if envelope.operation_kind not in {
            OperationKind.SINGLE_PLACEMENT,
            OperationKind.BEST_EFFORT_BATCH,
            OperationKind.ATOMIC_MULTI_LEG,
        }:
            raise ValueError("placement dispatch requires a placement operation kind")

        # Safety-critical ordering: durable identity precedes network dispatch.
        self._journal.record_before_dispatch(envelope)

        try:
            raw = json.loads(envelope.payload_json)
            if envelope.operation_kind is OperationKind.SINGLE_PLACEMENT:
                response = await self._client.place_order(
                    SingleOrderRequestDto.model_validate(raw)
                )
                state = LifecycleState.OPEN if response.open else LifecycleState.FILLED
                response_json = response.model_dump_json(by_alias=True)
            elif envelope.operation_kind is OperationKind.BEST_EFFORT_BATCH:
                response = await self._client.place_batch(
                    BatchOrderRequestDto.model_validate(raw)
                )
                state = LifecycleState.ACKED
                response_json = response.model_dump_json(by_alias=True)
            else:
                response = await self._client.place_multi_leg(
                    MultiLegOrderRequestDto.model_validate(raw)
                )
                state = LifecycleState.ACKED
                response_json = response.model_dump_json(by_alias=True)
        except SigExecutionUncertainError:
            observed = self._clock_ns()
            self._journal.mark_state(
                envelope.logical_operation_id,
                LifecycleState.UNCERTAIN,
                observed,
            )
            raise
        except SigApiError:
            observed = self._clock_ns()
            self._journal.mark_state(
                envelope.logical_operation_id,
                LifecycleState.REJECTED,
                observed,
            )
            raise

        observed = self._clock_ns()
        self._journal.mark_state(
            envelope.logical_operation_id,
            state,
            observed,
            response_json=response_json,
        )
        return ExecutionEvent(
            logical_operation_id=envelope.logical_operation_id,
            state=state,
            observed_monotonic_ns=observed,
            simulated=False,
        )

    async def cancel(self, envelope: ExecutionEnvelope) -> ExecutionEvent:
        if envelope.sink_mode is not ExecutionMode.LIVE:
            raise ValueError("SigLiveSink accepts LIVE envelopes only")
        if envelope.operation_kind not in {
            OperationKind.SINGLE_CANCELLATION,
            OperationKind.CANCEL_ALL,
        }:
            raise ValueError("cancel requires a cancellation envelope")
        self._journal.record_before_dispatch(envelope)
        raw = json.loads(envelope.payload_json)

        try:
            if envelope.operation_kind is OperationKind.SINGLE_CANCELLATION:
                order_id = raw.get("orderId")
                if not isinstance(order_id, int):
                    raise ValueError("single cancellation envelope is malformed")
                response = await self._client.cancel_order(order_id)
                response_json = json.dumps(
                    response,
                    default=str,
                    separators=(",", ":"),
                )
                state = LifecycleState.CANCELLED
            else:
                tournament_id = raw.get("tournamentId")
                exchange_id = raw.get("exchangeId")
                market_id = raw.get("marketId")
                response = await self._client.cancel_all(
                    tournament_id=(
                        tournament_id if isinstance(tournament_id, str) else None
                    ),
                    exchange_id=exchange_id if isinstance(exchange_id, str) else None,
                    market_id=market_id if isinstance(market_id, str) else None,
                )
                response_json = response.model_dump_json(by_alias=True)
                state = (
                    LifecycleState.CANCELLED
                    if not response.errors
                    else LifecycleState.CANCEL_PENDING
                )
        except SigExecutionUncertainError:
            observed = self._clock_ns()
            self._journal.mark_state(
                envelope.logical_operation_id,
                LifecycleState.UNCERTAIN,
                observed,
            )
            raise
        except SigApiError:
            observed = self._clock_ns()
            self._journal.mark_state(
                envelope.logical_operation_id,
                LifecycleState.REJECTED,
                observed,
            )
            raise

        observed = self._clock_ns()
        self._journal.mark_state(
            envelope.logical_operation_id,
            state,
            observed,
            response_json=response_json,
        )
        return ExecutionEvent(
            logical_operation_id=envelope.logical_operation_id,
            state=state,
            observed_monotonic_ns=observed,
            simulated=False,
        )
