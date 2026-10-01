from __future__ import annotations

import asyncio
import json
import sqlite3
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest
from pydantic import SecretStr

from predictions_cup.config import AppSettings
from predictions_cup.execution.interlocks import (
    LiveExecutionPermit,
    assert_live_recovery_interlocks,
)
from predictions_cup.execution.journal import ExecutionJournal
from predictions_cup.execution.live import SigLiveSink
from predictions_cup.execution.models import ExecutionMode, LifecycleState, OperationKind
from predictions_cup.execution.planner import build_execution_plan
from predictions_cup.execution.recovery import (
    RecoveryRest,
    recover_in_session_cancellations,
    recover_startup,
)
from predictions_cup.execution.replacement import quote_replacement_allowed
from predictions_cup.execution.reservations import ExecutionReservationBook
from predictions_cup.observe import (
    BoundedObservationEmitter,
    InMemoryObservationSink,
    ObservationKind,
)
from predictions_cup.risk.core import RiskContext, RiskDecision, evaluate_risk
from predictions_cup.runtime import (
    OrderAction,
    OutcomeSide,
    RuntimeOrderState,
    RuntimePortfolio,
    RuntimeSnapshot,
)
from predictions_cup.sig.account_reconciliation import AccountAuthoritativeSnapshot
from predictions_cup.sig.account_state import (
    AccountRealtimeStateEngine,
    AccountTrustTransition,
)
from predictions_cup.sig.trading_client import SigTradingClient
from predictions_cup.sig.trading_dto import (
    OrderFillsResponseDto,
    OrderReadDto,
    OrderStatusFilter,
    PositionsResponseDto,
    SingleOrderResponseDto,
)
from predictions_cup.strategy.core import NoTrade


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


def test_journal_persists_identity_before_dispatch_and_restores_unresolved(
    tmp_path: Path,
) -> None:
    from predictions_cup.execution.models import RuntimeOrderIntent
    from predictions_cup.runtime import OrderAction, OutcomeSide

    intent = RuntimeOrderIntent(
        intent_id="intent-1",
        exchange_id="36",
        market_id="m1",
        tournament_id="t1",
        outcome_side=OutcomeSide.YES,
        action=OrderAction.BUY,
        quantity=1,
        limit_price_ticks=100,
        strategy_id="fixture",
        decision_observation_ns=123,
    )
    decision = RiskDecision(
        approved=True,
        reason="approved",
        execution_mode=ExecutionMode.LIVE,
        operation_kind=OperationKind.SINGLE_PLACEMENT,
        intents=(intent,),
        relationship_constraint=None,
        strategy_family="FV-TAKE",
        strategy_id="fixture",
        signal_value=0.025,
        fair_value=0.55,
        decision_observation_ns=123,
    )
    plan = build_execution_plan(
        decision,
        logical_operation_id="logical-1",
        created_monotonic_ns=456,
    )

    path = tmp_path / "execution.sqlite3"
    journal = ExecutionJournal(path)
    journal.record_before_dispatch(
        plan.envelope,
        plan.intents,
        audit=plan.audit,
        submitted_monotonic_ns=480,
    )
    journal.mark_state("logical-1", LifecycleState.UNCERTAIN, 500)
    journal.close()

    reopened = ExecutionJournal(path)
    try:
        unresolved = reopened.unresolved()
        assert len(unresolved) == 1
        assert unresolved[0].logical_operation_id == "logical-1"
        assert unresolved[0].idempotency_key is not None
        assert unresolved[0].payload_json == plan.envelope.payload_json
        assert unresolved[0].payload_sha256 == plan.envelope.payload_sha256
        submission = reopened.events("logical-1")[0]
        assert submission.event_type == "SUBMISSION"
        assert submission.tournament_id == "t1"
        assert submission.logical_intent_id == "intent-1"
        assert submission.decision_observation_ns == 123
        assert submission.decision_monotonic_ns == 456
        assert submission.observed_monotonic_ns == 480
        assert submission.strategy_family == "FV-TAKE"
        assert submission.strategy_id == "fixture"
        assert submission.signal_value == pytest.approx(0.025)
        assert submission.fair_value == pytest.approx(0.55)
        assert submission.exchange_id == "36"

        reopened.mark_state("logical-1", LifecycleState.RECONCILED, 600)
        assert reopened.unresolved() == ()
    finally:
        reopened.close()


def test_journal_upgrades_pre_audit_event_schema(tmp_path: Path) -> None:
    path = tmp_path / "legacy.sqlite3"
    connection = sqlite3.connect(path)
    try:
        connection.execute(
            """
            CREATE TABLE execution_events (
                event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                logical_operation_id TEXT NOT NULL,
                logical_intent_id TEXT,
                event_type TEXT NOT NULL,
                observed_monotonic_ns INTEGER NOT NULL,
                source_timestamp TEXT,
                decision_observation_ns INTEGER,
                exchange_id TEXT,
                exchange_order_id TEXT,
                fill_id TEXT,
                quantity TEXT,
                price TEXT,
                terminal_status TEXT,
                detail_json TEXT
            )
            """
        )
        connection.commit()
    finally:
        connection.close()

    journal = ExecutionJournal(path)
    journal.close()

    connection = sqlite3.connect(path)
    try:
        columns = {
            str(row[1])
            for row in connection.execute(
                "PRAGMA table_info(execution_events)"
            ).fetchall()
        }
    finally:
        connection.close()
    assert {
        "tournament_id",
        "decision_monotonic_ns",
        "strategy_family",
        "strategy_id",
        "signal_value",
        "fair_value",
    } <= columns


def test_journal_rejects_changed_payload_under_same_logical_operation(
    tmp_path: Path,
) -> None:
    from predictions_cup.execution.models import ExecutionEnvelope, RuntimeOrderIntent
    from predictions_cup.runtime import OrderAction, OutcomeSide

    def envelope(quantity: int) -> ExecutionEnvelope:
        intent = RuntimeOrderIntent(
            intent_id=f"intent-{quantity}",
            exchange_id="36",
            market_id="m1",
            tournament_id="t1",
            outcome_side=OutcomeSide.YES,
            action=OrderAction.BUY,
            quantity=quantity,
            limit_price_ticks=100,
            strategy_id="fixture",
            decision_observation_ns=1,
        )
        return ExecutionEnvelope.placement(
            logical_operation_id="same-logical-operation",
            operation_kind=OperationKind.SINGLE_PLACEMENT,
            sink_mode=ExecutionMode.LIVE,
            idempotency_key="same-key",
            intents=(intent,),
            created_monotonic_ns=1,
        )

    journal = ExecutionJournal(tmp_path / "journal.sqlite3")
    try:
        journal.record_before_dispatch(envelope(1))
        with pytest.raises(ValueError):
            journal.record_before_dispatch(envelope(2))
    finally:
        journal.close()


def test_account_realtime_duplicate_and_gap_semantics() -> None:
    engine = AccountRealtimeStateEngine(tournament_id="t1")
    engine.apply_authoritative(
        AccountAuthoritativeSnapshot(
            tournament_id="t1",
            tournament_slug="cup",
            open_orders=(),
            positions=(),
            observed_at=datetime.now(UTC),
        )
    )
    assert engine.trusted is True

    first = engine.handle_raw_batch(_batch(1, 0), observed_at=datetime.now(UTC))
    assert first.accepted is True
    assert first.requires_reconciliation is False

    duplicate = engine.handle_raw_batch(_batch(1, 0), observed_at=datetime.now(UTC))
    assert duplicate.accepted is False
    assert duplicate.duplicate is True
    assert engine.trusted is True

    gap = engine.handle_raw_batch(_batch(3, 0), observed_at=datetime.now(UTC))
    assert gap.accepted is False
    assert gap.requires_reconciliation is True
    assert engine.trusted is False
    assert engine.transition is AccountTrustTransition.UNTRUSTED_REVISION_GAP


def test_reconnect_token_refresh_and_socket_error_all_revoke_account_trust() -> None:
    for transition in (
        AccountTrustTransition.UNTRUSTED_RECONNECT,
        AccountTrustTransition.UNTRUSTED_TOKEN_REFRESH,
        AccountTrustTransition.UNTRUSTED_SOCKET_ERROR,
    ):
        engine = AccountRealtimeStateEngine(tournament_id="t1")
        engine.apply_authoritative(
            AccountAuthoritativeSnapshot(
                tournament_id="t1",
                tournament_slug="cup",
                open_orders=(),
                positions=(),
                observed_at=datetime.now(UTC),
            )
        )
        engine.mark_untrusted(transition)
        assert engine.trusted is False
        assert engine.transition is transition


def test_quote_replacement_requires_trusted_authoritative_absence() -> None:
    outstanding = RuntimeOrderState(
        logical_intent_id="old-quote",
        exchange_id="36",
        market_id="m1",
        tournament_id="t1",
        reserved_exposure=5.0,
        open=False,
        uncertain=True,
    )
    assert quote_replacement_allowed(
        RuntimePortfolio(orders=(), account_trusted=False),
        exchange_id="36",
    ) is False
    assert quote_replacement_allowed(
        RuntimePortfolio(orders=(outstanding,), account_trusted=True),
        exchange_id="36",
    ) is False
    assert quote_replacement_allowed(
        RuntimePortfolio(orders=(), account_trusted=True),
        exchange_id="36",
    ) is True


def test_no_trade_remains_first_class_risk_result() -> None:
    snapshot = RuntimeSnapshot(
        markets=(),
        books=(),
        portfolio=RuntimePortfolio(account_trusted=False),
        observation_monotonic_ns=1,
    )
    decision = evaluate_risk(
        NoTrade(reason="fixture"),
        snapshot,
        RiskContext(
            mode=ExecutionMode.SHADOW,
            kill_switch=False,
            limits=None,
            max_state_age_ns=1,
        ),
    )
    assert decision.approved is False
    assert decision.reason == "fixture"

class _RecoveryRestFixture:
    def iter_orders(
        self,
        *,
        status: OrderStatusFilter = "open",
        exchange_id: str | None = None,
        market_id: str | None = None,
        tournament_id: str | None = None,
        limit: int = 200,
    ) -> AsyncIterator[OrderReadDto]:
        del status, exchange_id, market_id, tournament_id, limit

        async def empty() -> AsyncIterator[OrderReadDto]:
            if False:
                yield OrderReadDto.model_validate({})

        return empty()

    async def get_order(self, order_id: int) -> OrderReadDto:
        raise AssertionError(f"unexpected get_order({order_id})")

    async def get_order_fills(
        self,
        order_id: int,
        *,
        limit: int = 50,
        cursor: str | None = None,
    ) -> OrderFillsResponseDto:
        del order_id, limit, cursor
        raise AssertionError("unexpected get_order_fills")

    async def get_tournament_positions(
        self,
        tournament_slug: str,
    ) -> PositionsResponseDto:
        assert tournament_slug == "cup"
        return PositionsResponseDto.model_validate(
            {
                "positions": [],
                "summary": {
                    "totalMarketValue": "0",
                    "totalCostBasis": "0",
                    "totalUnrealizedPnl": "0",
                },
            }
        )


class _RecoveryTradingFixture:
    def __init__(self) -> None:
        self.payloads: list[str] = []
        self.cancelled_order_ids: list[int] = []

    async def place_order_payload(
        self,
        payload_json: str,
    ) -> SingleOrderResponseDto:
        self.payloads.append(payload_json)
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
        self.cancelled_order_ids.append(order_id)
        return {"cancelled": True}


def _recovery_permit() -> LiveExecutionPermit:
    settings = AppSettings(
        sig_trade_credential=SecretStr("trade-secret"),
        tournament_id="t1",
        tournament_slug="cup",
        trading_enabled=True,
        execution_mode="LIVE",
        global_kill_switch=False,
        risk_max_order_size=10,
        risk_max_gross_exposure=100.0,
        risk_max_tournament_exposure=100.0,
        risk_max_per_market_exposure=100.0,
        risk_max_open_order_exposure=100.0,
        risk_max_concurrent_open_orders=10,
        risk_capital_control_enabled=True,
        risk_session_loss_limit=10.0,
        risk_drawdown_limit=10.0,
    )
    return assert_live_recovery_interlocks(
        settings,
        explicit_live_invocation=True,
        account_trusted=True,
    )


class _OpenOrderRecoveryRest(_RecoveryRestFixture):
    async def get_order(self, order_id: int) -> OrderReadDto:
        assert order_id == 91
        return OrderReadDto.model_validate(
            {
                "id": 91,
                "exchangeId": "36",
                "side": "yes",
                "action": "buy",
                "quantity": "2",
                "priceLimit": "0.495",
                "open": True,
                "createdAt": "2026-09-29T14:00:00Z",
                "expirationDate": None,
            }
        )


def test_in_session_recovery_resolves_uncertain_cancel_without_restart(
    tmp_path: Path,
) -> None:
    from predictions_cup.execution.models import ExecutionEnvelope

    journal = ExecutionJournal(tmp_path / "in-session-cancel.sqlite3")
    cancel = ExecutionEnvelope.cancellation(
        logical_operation_id="cancel-in-session-91",
        operation_kind=OperationKind.SINGLE_CANCELLATION,
        sink_mode=ExecutionMode.LIVE,
        created_monotonic_ns=100,
        order_id=91,
        tournament_id="t1",
    )
    journal.record_before_dispatch(cancel, submitted_monotonic_ns=101)
    journal.mark_state(cancel.logical_operation_id, LifecycleState.UNCERTAIN, 102)

    trading = _RecoveryTradingFixture()
    sink = SigLiveSink(
        client=cast(SigTradingClient, trading),
        journal=journal,
        permit=_recovery_permit(),
        reservations=ExecutionReservationBook(),
        clock_ns=iter(range(200, 500)).__next__,
    )
    try:
        resolved = asyncio.run(
            recover_in_session_cancellations(
                journal=journal,
                rest=cast(RecoveryRest, _OpenOrderRecoveryRest()),
                live_sink=sink,
                tournament_id="t1",
                clock_ns=iter(range(500, 800)).__next__,
            )
        )
        assert resolved == ("cancel-in-session-91",)
        assert trading.cancelled_order_ids == [91]
        assert journal.unresolved() == ()
    finally:
        journal.close()


def test_recovery_only_authority_cancels_unresolved_open_order_but_cannot_place(
    tmp_path: Path,
) -> None:
    from predictions_cup.execution.models import ExecutionEnvelope, RuntimeOrderIntent

    journal = ExecutionJournal(tmp_path / "cancel-recovery.sqlite3")
    cancel = ExecutionEnvelope.cancellation(
        logical_operation_id="cancel-recovery-91",
        operation_kind=OperationKind.SINGLE_CANCELLATION,
        sink_mode=ExecutionMode.LIVE,
        created_monotonic_ns=100,
        order_id=91,
        tournament_id="t1",
    )
    journal.record_before_dispatch(cancel, submitted_monotonic_ns=101)
    journal.mark_state(
        cancel.logical_operation_id,
        LifecycleState.UNCERTAIN,
        102,
    )

    trading = _RecoveryTradingFixture()
    sink = SigLiveSink(
        client=cast(SigTradingClient, trading),
        journal=journal,
        permit=_recovery_permit(),
        reservations=ExecutionReservationBook(),
        clock_ns=iter(range(200, 500)).__next__,
    )
    try:
        recovered = asyncio.run(
            recover_startup(
                journal=journal,
                rest=cast(RecoveryRest, _OpenOrderRecoveryRest()),
                live_sink=sink,
                tournament_id="t1",
                tournament_slug="cup",
                clock_ns=iter(range(500, 800)).__next__,
            )
        )
        assert trading.cancelled_order_ids == [91]
        assert recovered.safe_to_resume_live
        assert recovered.unresolved_operation_ids == ()
        assert journal.unresolved() == ()

        fresh_intent = RuntimeOrderIntent(
            intent_id="fresh-under-halt",
            exchange_id="36",
            market_id="m1",
            tournament_id="t1",
            outcome_side=OutcomeSide.YES,
            action=OrderAction.BUY,
            quantity=1,
            limit_price_ticks=100,
            strategy_id="fixture",
            decision_observation_ns=900,
        )
        fresh_plan = build_execution_plan(
            RiskDecision(
                approved=True,
                reason="approved",
                execution_mode=ExecutionMode.LIVE,
                operation_kind=OperationKind.SINGLE_PLACEMENT,
                intents=(fresh_intent,),
                strategy_family="FV-TAKE",
                strategy_id="fixture",
                signal_value=0.01,
                fair_value=0.55,
                decision_observation_ns=900,
            ),
            logical_operation_id="fresh-under-halt",
            created_monotonic_ns=901,
        )
        with pytest.raises(ValueError, match="recovery-only LIVE permit"):
            asyncio.run(sink.dispatch(fresh_plan))
        assert trading.payloads == []
    finally:
        journal.close()


def test_startup_recovery_uses_durable_authority_with_fresh_empty_reservations(
    tmp_path: Path,
) -> None:
    from predictions_cup.execution.models import RuntimeOrderIntent

    intent = RuntimeOrderIntent(
        intent_id="intent-recovery",
        exchange_id="36",
        market_id="m1",
        tournament_id="t1",
        outcome_side=OutcomeSide.YES,
        action=OrderAction.BUY,
        quantity=1,
        limit_price_ticks=100,
        strategy_id="fixture",
        decision_observation_ns=123,
    )
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
        decision_observation_ns=123,
    )
    plan = build_execution_plan(
        decision,
        logical_operation_id="op-recovery",
        created_monotonic_ns=456,
    )

    path = tmp_path / "recovery.sqlite3"
    first = ExecutionJournal(path)
    first.record_before_dispatch(
        plan.envelope,
        plan.intents,
        audit=plan.audit,
        submitted_monotonic_ns=480,
    )
    first.mark_state("op-recovery", LifecycleState.UNCERTAIN, 500)
    first.close()

    journal = ExecutionJournal(path)
    reservations = ExecutionReservationBook()
    trading = _RecoveryTradingFixture()
    observation_sink = InMemoryObservationSink()
    emitter = BoundedObservationEmitter(observation_sink, queue_max=100)
    sink = SigLiveSink(
        client=cast(SigTradingClient, trading),
        journal=journal,
        permit=_recovery_permit(),
        reservations=reservations,
        clock_ns=iter(range(600, 900)).__next__,
    )
    try:
        # Fresh LIVE remains blocked: recovery authority does not weaken the
        # normal reservation gate.
        with pytest.raises(ValueError, match="recovery-only LIVE permit"):
            asyncio.run(sink.dispatch(plan))

        result = asyncio.run(
            recover_startup(
                journal=journal,
                rest=cast(RecoveryRest, _RecoveryRestFixture()),
                live_sink=sink,
                tournament_id="t1",
                tournament_slug="cup",
                clock_ns=iter(range(900, 1200)).__next__,
                observation_emitter=emitter,
                observation_process_instance_id="recovery-test-process",
                placement_replay_allowed=lambda _envelope: True,
            )
        )
        emitter.close()

        kinds = [item.kind for item in observation_sink.observations]
        assert ObservationKind.RECONCILIATION_STARTED in kinds
        assert ObservationKind.RECONCILIATION_RESOLVED in kinds
        resolved = next(
            item
            for item in observation_sink.observations
            if item.kind is ObservationKind.RECONCILIATION_RESOLVED
        )
        assert resolved.logical_operation_id == "op-recovery"

        assert trading.payloads == [plan.envelope.payload_json]
        resent = json.loads(trading.payloads[0])
        original = json.loads(plan.envelope.payload_json)
        assert resent["idempotencyKey"] == original["idempotencyKey"]
        assert reservations.intent_ids() == frozenset()
        assert result.safe_to_resume_live is True
        assert result.unresolved_operation_ids == ()
    finally:
        emitter.close()
        journal.close()



def test_order_fills_accepts_live_sig_item_shape() -> None:
    # Live 2026-10-01 19:51Z: per-order fill rows omit orderId/exchangeId/marketId.
    payload = {
        "orderId": 972918,
        "exchangeId": "1045",
        "tournamentId": "bda92870-621e-47b0-bc3c-3602c5c26f55",
        "data": [
            {
                "id": 2902569,
                "price": 0.67,
                "quantity": -93,
                "side": "no",
                "filledAt": "2026-10-01T19:47:54.590Z",
            }
        ],
        "pagination": {"limit": 50, "hasMore": False, "nextCursor": None},
        "coverage": {"complete": True, "projectedThroughSequence": 2927025},
        "totalQuantityFilled": -93,
        "avgFillPrice": 0.67,
    }
    parsed = OrderFillsResponseDto.model_validate(payload)
    assert parsed.data[0].exchange_id is None
    assert parsed.exchange_id == "1045"
