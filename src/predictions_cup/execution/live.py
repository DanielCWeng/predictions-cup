"""LIVE sink: durable journal first, then exact SIG write adapter dispatch."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import UTC, datetime
from time import monotonic_ns
from uuid import uuid4

from predictions_cup.execution.interlocks import LiveExecutionPermit
from predictions_cup.execution.journal import ExecutionJournal
from predictions_cup.execution.models import (
    ExecutionEnvelope,
    ExecutionEvent,
    ExecutionMode,
    LifecycleState,
    OperationKind,
)
from predictions_cup.execution.reservations import ExecutionReservationBook
from predictions_cup.execution.sinks import ExecutionPlan
from predictions_cup.observe.contracts import ObservationEmitter, ObservationKind, VenueObservation
from predictions_cup.sig.errors import SigApiError, SigExecutionUncertainError
from predictions_cup.sig.trading_client import SigTradingClient

ClockNs = Callable[[], int]
WallClock = Callable[[], datetime]


class SigLiveSink:
    """No strategy reaches this class without already passing central Risk."""

    def __init__(
        self,
        *,
        client: SigTradingClient,
        journal: ExecutionJournal,
        permit: LiveExecutionPermit,
        reservations: ExecutionReservationBook,
        clock_ns: ClockNs = monotonic_ns,
        wall_clock: WallClock = lambda: datetime.now(UTC),
        observation_emitter: ObservationEmitter | None = None,
        observation_process_instance_id: str | None = None,
    ) -> None:
        self._client = client
        self._journal = journal
        self._permit = permit
        self._reservations = reservations
        self._clock_ns = clock_ns
        self._wall_clock = wall_clock
        self._observation_emitter = observation_emitter
        self._observation_process_instance_id = (
            uuid4().hex
            if observation_process_instance_id is None
            else observation_process_instance_id
        )
        if not self._observation_process_instance_id.strip():
            raise ValueError("observation_process_instance_id must not be blank")

    async def dispatch(self, plan: ExecutionPlan) -> ExecutionEvent:
        return await self._dispatch_placement(plan, require_reservation=True)

    async def dispatch_recovery(self, envelope: ExecutionEnvelope) -> ExecutionEvent:
        """Redispatch one durable unresolved placement while fresh LIVE is blocked."""
        self._assert_recovery_authority(envelope)
        return await self._dispatch_placement(
            ExecutionPlan(envelope=envelope, intents=()),
            require_reservation=False,
        )

    def _assert_recovery_authority(self, envelope: ExecutionEnvelope) -> None:
        durable = next(
            (
                candidate
                for candidate in self._journal.unresolved()
                if candidate.logical_operation_id == envelope.logical_operation_id
            ),
            None,
        )
        if durable is None:
            raise ValueError("recovery dispatch requires an unresolved journal operation")
        identity = (
            durable.tournament_id,
            durable.operation_kind,
            durable.sink_mode,
            durable.idempotency_key,
            durable.payload_sha256,
            durable.payload_json,
            durable.intent_ids,
        )
        requested = (
            envelope.tournament_id,
            envelope.operation_kind,
            envelope.sink_mode,
            envelope.idempotency_key,
            envelope.payload_sha256,
            envelope.payload_json,
            envelope.intent_ids,
        )
        if identity != requested:
            raise ValueError("recovery dispatch does not match durable journal identity")
        if durable.lifecycle_state not in {
            LifecycleState.PENDING,
            LifecycleState.UNCERTAIN,
            LifecycleState.RECONCILING,
        }:
            raise ValueError("recovery dispatch requires a recoverable lifecycle state")

    async def _dispatch_placement(
        self,
        plan: ExecutionPlan,
        *,
        require_reservation: bool,
    ) -> ExecutionEvent:
        envelope = plan.envelope
        if envelope.sink_mode is not ExecutionMode.LIVE:
            raise ValueError("SigLiveSink accepts LIVE envelopes only")
        if envelope.tournament_id != self._permit.tournament_id:
            raise ValueError("LIVE permit tournament does not match execution envelope")
        if require_reservation and not self._reservations.contains_operation(
            envelope.logical_operation_id,
            envelope.intent_ids,
        ):
            raise ValueError("LIVE execution plan is missing its synchronous reservation")
        if hashlib.sha256(envelope.payload_json.encode("utf-8")).hexdigest() != (
            envelope.payload_sha256
        ):
            raise ValueError("execution payload hash mismatch")
        if envelope.operation_kind not in {
            OperationKind.SINGLE_PLACEMENT,
            OperationKind.BEST_EFFORT_BATCH,
            OperationKind.ATOMIC_MULTI_LEG,
        }:
            raise ValueError("placement dispatch requires a placement operation kind")

        if plan.audit is not None:
            self._observe(
                ObservationKind.DECISION_OBSERVED,
                envelope,
                monotonic_ns=plan.audit.decision_observation_ns,
                plan=plan,
            )
        self._observe(
            ObservationKind.PLAN_CREATED,
            envelope,
            monotonic_ns=envelope.created_monotonic_ns,
            plan=plan,
        )

        # Safety-critical ordering: durable identity precedes network dispatch.
        submitted = self._clock_ns()
        self._observe(
            ObservationKind.REQUEST_ENQUEUED,
            envelope,
            monotonic_ns=submitted,
            plan=plan,
        )
        self._journal.record_before_dispatch(
            envelope,
            plan.intents,
            audit=plan.audit,
            submitted_monotonic_ns=submitted,
        )
        network_dispatch_ns = self._clock_ns()
        self._observe(
            ObservationKind.REQUEST_DISPATCHED,
            envelope,
            monotonic_ns=network_dispatch_ns,
            plan=plan,
        )

        try:
            if envelope.operation_kind is OperationKind.SINGLE_PLACEMENT:
                single_response = await self._client.place_order_payload(
                    envelope.payload_json
                )
                state = (
                    LifecycleState.OPEN
                    if single_response.open
                    else LifecycleState.FILLED
                )
                response_json = single_response.model_dump_json(by_alias=True)
                response_status: int | None = 200
            elif envelope.operation_kind is OperationKind.BEST_EFFORT_BATCH:
                batch_response = await self._client.place_batch_payload(
                    envelope.payload_json
                )
                state = (
                    LifecycleState.REJECTED
                    if self._batch_conclusively_rejected(batch_response.results)
                    else LifecycleState.ACKED
                )
                response_json = batch_response.model_dump_json(by_alias=True)
                # SigTradingClient currently returns the validated DTO without
                # retaining the outer 200/207/422 status.
                response_status = None
            else:
                multi_response = await self._client.place_multi_leg_payload(
                    envelope.payload_json
                )
                state = (
                    LifecycleState.REJECTED
                    if multi_response.results
                    and all(not result.ok for result in multi_response.results)
                    else LifecycleState.ACKED
                )
                response_json = multi_response.model_dump_json(by_alias=True)
                response_status = 200
        except SigExecutionUncertainError as exc:
            observed = self._clock_ns()
            self._observe_error(exc, envelope, observed, plan=plan)
            self._observe(
                ObservationKind.UNCERTAIN,
                envelope,
                monotonic_ns=observed,
                plan=plan,
                status_code=exc.status_code,
            )
            self._journal.record_event(
                logical_operation_id=envelope.logical_operation_id,
                tournament_id=envelope.tournament_id,
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
                tournament_id=envelope.tournament_id,
                event_type="UNCERTAIN",
                observed_monotonic_ns=observed,
                terminal_status=LifecycleState.UNCERTAIN.value,
            )
            raise
        except SigApiError as exc:
            observed = self._clock_ns()
            self._observe_error(exc, envelope, observed, plan=plan)
            self._observe(
                ObservationKind.REJECTED,
                envelope,
                monotonic_ns=observed,
                plan=plan,
                status_code=exc.status_code,
            )
            self._journal.record_event(
                logical_operation_id=envelope.logical_operation_id,
                tournament_id=envelope.tournament_id,
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
                tournament_id=envelope.tournament_id,
                event_type="REJECTED",
                observed_monotonic_ns=observed,
                terminal_status=LifecycleState.REJECTED.value,
            )
            self._reservations.release_operation(envelope.logical_operation_id)
            raise

        observed = self._clock_ns()
        self._observe(
            ObservationKind.RESPONSE_RECEIVED,
            envelope,
            monotonic_ns=observed,
            plan=plan,
            status_code=response_status,
        )
        self._observe(
            ObservationKind.RESPONSE_PARSED,
            envelope,
            monotonic_ns=observed,
            plan=plan,
            status_code=response_status,
        )
        self._journal.record_event(
            logical_operation_id=envelope.logical_operation_id,
            tournament_id=envelope.tournament_id,
            event_type="NETWORK_DISPATCH",
            observed_monotonic_ns=network_dispatch_ns,
        )
        if envelope.operation_kind is OperationKind.SINGLE_PLACEMENT:
            intent = plan.intents[0] if plan.intents else None
            self._journal.record_event(
                logical_operation_id=envelope.logical_operation_id,
                tournament_id=envelope.tournament_id,
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
            self._observe(
                ObservationKind.ACK,
                envelope,
                monotonic_ns=observed,
                plan=plan,
                intent=intent,
                exchange_id=single_response.exchange_id,
                exchange_order_id=(
                    None if single_response.order_id is None else str(single_response.order_id)
                ),
                status_code=response_status,
            )
            if single_response.quantity_traded > 0:
                self._observe(
                    (
                        ObservationKind.FILL
                        if state is LifecycleState.FILLED
                        else ObservationKind.PARTIAL_FILL
                    ),
                    envelope,
                    monotonic_ns=observed,
                    plan=plan,
                    intent=intent,
                    exchange_id=single_response.exchange_id,
                    exchange_order_id=(
                        None if single_response.order_id is None else str(single_response.order_id)
                    ),
                    status_code=response_status,
                    detail=(("quantity", str(single_response.quantity_traded)),),
                )
                self._journal.record_event(
                    logical_operation_id=envelope.logical_operation_id,
                tournament_id=envelope.tournament_id,
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
                tournament_id=envelope.tournament_id,
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
                self._observe(
                    (
                        ObservationKind.ACK
                        if batch_result.ok
                        else ObservationKind.REJECTED
                    ),
                    envelope,
                    monotonic_ns=observed,
                    plan=plan,
                    intent=intent,
                    exchange_id=None if intent is None else intent.exchange_id,
                    exchange_order_id=(
                        str(order_id) if isinstance(order_id, (int, str)) else None
                    ),
                    status_code=batch_result.status,
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
                tournament_id=envelope.tournament_id,
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
                self._observe(
                    (
                        ObservationKind.ACK
                        if multi_result.ok
                        else ObservationKind.REJECTED
                    ),
                    envelope,
                    monotonic_ns=observed,
                    plan=plan,
                    intent=intent,
                    exchange_id=None if intent is None else intent.exchange_id,
                    exchange_order_id=(
                        str(order_id) if isinstance(order_id, (int, str)) else None
                    ),
                    status_code=200,
                )
        self._journal.mark_state(
            envelope.logical_operation_id,
            state,
            observed,
            response_json=response_json,
        )
        if state is LifecycleState.REJECTED:
            self._reservations.release_operation(envelope.logical_operation_id)
        return ExecutionEvent(
            logical_operation_id=envelope.logical_operation_id,
            state=state,
            observed_monotonic_ns=observed,
            simulated=False,
        )

    @staticmethod
    def _batch_conclusively_rejected(results: tuple[object, ...]) -> bool:
        if not results:
            return False
        for result in results:
            ok = getattr(result, "ok", None)
            status = getattr(result, "status", None)
            if ok is not False or not isinstance(status, int):
                return False
            if not 400 <= status < 500 or status == 409:
                return False
        return True

    async def cancel(self, envelope: ExecutionEnvelope) -> ExecutionEvent:
        if envelope.sink_mode is not ExecutionMode.LIVE:
            raise ValueError("SigLiveSink accepts LIVE envelopes only")
        if envelope.tournament_id != self._permit.tournament_id:
            raise ValueError("LIVE permit tournament does not match execution envelope")
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
        self._observe(
            ObservationKind.CANCEL_REQUESTED,
            envelope,
            monotonic_ns=network_dispatch_ns,
            exchange_order_id=(
                str(raw["orderId"])
                if isinstance(raw.get("orderId"), (int, str))
                else None
            ),
        )

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
                response_status = 200
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
                response_status = None
        except SigExecutionUncertainError as exc:
            observed = self._clock_ns()
            self._observe_error(exc, envelope, observed)
            self._observe(
                ObservationKind.UNCERTAIN,
                envelope,
                monotonic_ns=observed,
                exchange_order_id=(
                    str(raw["orderId"])
                    if isinstance(raw.get("orderId"), (int, str))
                    else None
                ),
                status_code=exc.status_code,
            )
            self._journal.record_event(
                logical_operation_id=envelope.logical_operation_id,
                tournament_id=envelope.tournament_id,
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
                tournament_id=envelope.tournament_id,
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
        except SigApiError as exc:
            observed = self._clock_ns()
            self._observe_error(exc, envelope, observed)
            self._observe(
                ObservationKind.REJECTED,
                envelope,
                monotonic_ns=observed,
                exchange_order_id=(
                    str(raw["orderId"])
                    if isinstance(raw.get("orderId"), (int, str))
                    else None
                ),
                status_code=exc.status_code,
            )
            self._journal.record_event(
                logical_operation_id=envelope.logical_operation_id,
                tournament_id=envelope.tournament_id,
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
                tournament_id=envelope.tournament_id,
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
        self._observe(
            ObservationKind.RESPONSE_RECEIVED,
            envelope,
            monotonic_ns=observed,
            status_code=response_status,
        )
        self._observe(
            ObservationKind.RESPONSE_PARSED,
            envelope,
            monotonic_ns=observed,
            status_code=200,
        )
        self._observe(
            ObservationKind.CANCEL_ACK,
            envelope,
            monotonic_ns=observed,
            exchange_order_id=(
                str(raw["orderId"])
                if isinstance(raw.get("orderId"), (int, str))
                else None
            ),
            status_code=200,
            detail=(("state", state.value),),
        )
        self._journal.record_event(
            logical_operation_id=envelope.logical_operation_id,
            tournament_id=envelope.tournament_id,
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
            tournament_id=envelope.tournament_id,
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

    def _observe_error(
        self,
        exc: SigApiError,
        envelope: ExecutionEnvelope,
        observed_ns: int,
        *,
        plan: ExecutionPlan | None = None,
    ) -> None:
        if exc.status_code == 429:
            kind = ObservationKind.RATE_LIMIT
        elif exc.status_code is not None and exc.status_code >= 500:
            kind = ObservationKind.SERVER_ERROR
        elif exc.status_code is None:
            kind = ObservationKind.TRANSPORT_EXCEPTION
        else:
            return
        self._observe(
            kind,
            envelope,
            monotonic_ns=observed_ns,
            plan=plan,
            status_code=exc.status_code,
            detail=(("error_code", exc.code or type(exc).__name__),),
        )

    def _observe(
        self,
        kind: ObservationKind,
        envelope: ExecutionEnvelope,
        *,
        monotonic_ns: int,
        plan: ExecutionPlan | None = None,
        intent: object | None = None,
        exchange_id: str | None = None,
        exchange_order_id: str | None = None,
        status_code: int | None = None,
        detail: tuple[tuple[str, str], ...] = (),
    ) -> None:
        emitter = self._observation_emitter
        if emitter is None:
            return
        logical_intent_id = getattr(intent, "intent_id", None)
        market_id = getattr(intent, "market_id", None)
        resolved_exchange = exchange_id or getattr(intent, "exchange_id", None)
        audit = None if plan is None else plan.audit
        try:
            emitter.emit(
                VenueObservation(
                    kind=kind,
                    observed_at=self._wall_clock(),
                    monotonic_ns=monotonic_ns,
                    process_instance_id=self._observation_process_instance_id,
                    source="BUILD_009_SIG_LIVE_SINK",
                    source_version="observe-001",
                    provenance="LOCAL_EXECUTION_BOUNDARY",
                    tournament_id=envelope.tournament_id,
                    market_id=market_id,
                    exchange_id=resolved_exchange,
                    strategy_family=None if audit is None else audit.strategy_family,
                    strategy_id=None if audit is None else audit.strategy_id,
                    logical_operation_id=envelope.logical_operation_id,
                    logical_intent_id=logical_intent_id,
                    idempotency_key=envelope.idempotency_key,
                    exchange_order_id=exchange_order_id,
                    status_code=status_code,
                    detail=detail,
                )
            )
        except Exception:
            # Observation must never become a safety dependency.
            return
