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
        submitted = self._clock_ns()
        self._journal.record_before_dispatch(
            envelope,
            plan.intents,
            audit=plan.audit,
            submitted_monotonic_ns=submitted,
        )
        network_dispatch_ns = self._clock_ns()

        try:
            raw = json.loads(envelope.payload_json)
            if envelope.operation_kind is OperationKind.SINGLE_PLACEMENT:
                single_response = await self._client.place_order(
                    SingleOrderRequestDto.model_validate(raw)
                )
                state = (
                    LifecycleState.OPEN
                    if single_response.open
                    else LifecycleState.FILLED
                )
                response_json = single_response.model_dump_json(by_alias=True)
            elif envelope.operation_kind is OperationKind.BEST_EFFORT_BATCH:
                batch_response = await self._client.place_batch(
                    BatchOrderRequestDto.model_validate(raw)
                )
                state = LifecycleState.ACKED
                response_json = batch_response.model_dump_json(by_alias=True)
            else:
                multi_response = await self._client.place_multi_leg(
                    MultiLegOrderRequestDto.model_validate(raw)
                )
                state = LifecycleState.ACKED
                response_json = multi_response.model_dump_json(by_alias=True)
        except SigExecutionUncertainError:
            observed = self._clock_ns()
            self._journal.record_event(
                logical_operation_id=envelope.logical_operation_id,
                event_type="NETWORK_DISPATCH",
                observed_monotonic_ns=network_dispatch_ns,
            )
            self._journal.mark_state(
                envelope.logical_operation_id,
                LifecycleState.UNCERTAIN,
                observed,
            )
            self._journal.record_event(
                logical_operation_id=envelope.logical_operation_id,
                event_type="UNCERTAIN",
                observed_monotonic_ns=observed,
                terminal_status=LifecycleState.UNCERTAIN.value,
            )
            raise
        except SigApiError:
            observed = self._clock_ns()
            self._journal.record_event(
                logical_operation_id=envelope.logical_operation_id,
                event_type="NETWORK_DISPATCH",
                observed_monotonic_ns=network_dispatch_ns,
            )
            self._journal.mark_state(
                envelope.logical_operation_id,
                LifecycleState.REJECTED,
                observed,
            )
            self._journal.record_event(
                logical_operation_id=envelope.logical_operation_id,
                event_type="REJECTED",
                observed_monotonic_ns=observed,
                terminal_status=LifecycleState.REJECTED.value,
            )
            raise

        observed = self._clock_ns()
        self._journal.record_event(
            logical_operation_id=envelope.logical_operation_id,
            event_type="NETWORK_DISPATCH",
            observed_monotonic_ns=network_dispatch_ns,
        )
        if envelope.operation_kind is OperationKind.SINGLE_PLACEMENT:
            intent = plan.intents[0] if plan.intents else None
            self._journal.record_event(
                logical_operation_id=envelope.logical_operation_id,
                logical_intent_id=None if intent is None else intent.intent_id,
                event_type="ACK",
                observed_monotonic_ns=observed,
                decision_observation_ns=(
                    None if intent is None else intent.decision_observation_ns
                ),
                exchange_id=single_response.exchange_id,
                exchange_order_id=(
                    None
                    if single_response.order_id is None
                    else str(single_response.order_id)
                ),
                quantity=(
                    None
                    if single_response.quantity is None
                    else str(single_response.quantity)
                ),
                price=(
                    None
                    if single_response.price is None
                    else str(single_response.price)
                ),
                terminal_status=state.value,
                detail_json=response_json,
            )
            if single_response.quantity_traded > 0:
                self._journal.record_event(
                    logical_operation_id=envelope.logical_operation_id,
                    logical_intent_id=None if intent is None else intent.intent_id,
                    event_type="FILL_SUMMARY",
                    observed_monotonic_ns=observed,
                    decision_observation_ns=(
                        None if intent is None else intent.decision_observation_ns
                    ),
                    exchange_id=single_response.exchange_id,
                    exchange_order_id=(
                        None
                        if single_response.order_id is None
                        else str(single_response.order_id)
                    ),
                    quantity=str(single_response.quantity_traded),
                    price=(
                        None
                        if single_response.fill_price is None
                        else str(single_response.fill_price)
                    ),
                    terminal_status=state.value,
                )
        elif envelope.operation_kind is OperationKind.BEST_EFFORT_BATCH:
            for batch_result in batch_response.results:
                intent = (
                    plan.intents[batch_result.index]
                    if 0 <= batch_result.index < len(plan.intents)
                    else None
                )
                order_id = batch_result.data.get("orderId")
                self._journal.record_event(
                    logical_operation_id=envelope.logical_operation_id,
                    logical_intent_id=None if intent is None else intent.intent_id,
                    event_type="ACK" if batch_result.ok else "REJECTED",
                    observed_monotonic_ns=observed,
                    decision_observation_ns=(
                        None if intent is None else intent.decision_observation_ns
                    ),
                    exchange_id=None if intent is None else intent.exchange_id,
                    exchange_order_id=(
                        str(order_id) if isinstance(order_id, (int, str)) else None
                    ),
                    terminal_status=(
                        LifecycleState.ACKED.value
                        if batch_result.ok
                        else LifecycleState.REJECTED.value
                    ),
                    detail_json=json.dumps(
                        batch_result.data,
                        default=str,
                        separators=(",", ":"),
                    ),
                )
        else:
            for multi_result in multi_response.results:
                intent = (
                    plan.intents[multi_result.index]
                    if 0 <= multi_result.index < len(plan.intents)
                    else None
                )
                order_id = multi_result.data.get("orderId")
                self._journal.record_event(
                    logical_operation_id=envelope.logical_operation_id,
                    logical_intent_id=None if intent is None else intent.intent_id,
                    event_type="ACK" if multi_result.ok else "REJECTED",
                    observed_monotonic_ns=observed,
                    decision_observation_ns=(
                        None if intent is None else intent.decision_observation_ns
                    ),
                    exchange_id=None if intent is None else intent.exchange_id,
                    exchange_order_id=(
                        str(order_id) if isinstance(order_id, (int, str)) else None
                    ),
                    terminal_status=(
                        LifecycleState.ACKED.value
                        if multi_result.ok
                        else LifecycleState.REJECTED.value
                    ),
                    detail_json=json.dumps(
                        multi_result.data,
                        default=str,
                        separators=(",", ":"),
                    ),
                )
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
        submitted = self._clock_ns()
        self._journal.record_before_dispatch(
            envelope,
            submitted_monotonic_ns=submitted,
        )
        raw = json.loads(envelope.payload_json)
        network_dispatch_ns = self._clock_ns()

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
            self._journal.record_event(
                logical_operation_id=envelope.logical_operation_id,
                event_type="CANCEL_SUBMITTED",
                observed_monotonic_ns=network_dispatch_ns,
                exchange_order_id=(
                    str(raw["orderId"])
                    if isinstance(raw.get("orderId"), (int, str))
                    else None
                ),
                detail_json=envelope.payload_json,
            )
            self._journal.mark_state(
                envelope.logical_operation_id,
                LifecycleState.UNCERTAIN,
                observed,
            )
            self._journal.record_event(
                logical_operation_id=envelope.logical_operation_id,
                event_type="CANCEL_UNCERTAIN",
                observed_monotonic_ns=observed,
                exchange_order_id=(
                    str(raw["orderId"])
                    if isinstance(raw.get("orderId"), (int, str))
                    else None
                ),
                terminal_status=LifecycleState.UNCERTAIN.value,
            )
            raise
        except SigApiError:
            observed = self._clock_ns()
            self._journal.record_event(
                logical_operation_id=envelope.logical_operation_id,
                event_type="CANCEL_SUBMITTED",
                observed_monotonic_ns=network_dispatch_ns,
                exchange_order_id=(
                    str(raw["orderId"])
                    if isinstance(raw.get("orderId"), (int, str))
                    else None
                ),
                detail_json=envelope.payload_json,
            )
            self._journal.mark_state(
                envelope.logical_operation_id,
                LifecycleState.REJECTED,
                observed,
            )
            self._journal.record_event(
                logical_operation_id=envelope.logical_operation_id,
                event_type="CANCEL_REJECTED",
                observed_monotonic_ns=observed,
                exchange_order_id=(
                    str(raw["orderId"])
                    if isinstance(raw.get("orderId"), (int, str))
                    else None
                ),
                terminal_status=LifecycleState.REJECTED.value,
            )
            raise

        observed = self._clock_ns()
        self._journal.record_event(
            logical_operation_id=envelope.logical_operation_id,
            event_type="CANCEL_SUBMITTED",
            observed_monotonic_ns=network_dispatch_ns,
            exchange_order_id=(
                str(raw["orderId"])
                if isinstance(raw.get("orderId"), (int, str))
                else None
            ),
            detail_json=envelope.payload_json,
        )
        self._journal.record_event(
            logical_operation_id=envelope.logical_operation_id,
            event_type="CANCEL_ACK",
            observed_monotonic_ns=observed,
            exchange_order_id=(
                str(raw["orderId"])
                if isinstance(raw.get("orderId"), (int, str))
                else None
            ),
            terminal_status=state.value,
            detail_json=response_json,
        )
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
