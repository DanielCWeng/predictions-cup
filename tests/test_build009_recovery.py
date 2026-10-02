from __future__ import annotations

import asyncio
import json
import sqlite3
from collections.abc import AsyncIterator, Callable
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
from predictions_cup.execution.models import (
    ExecutionEnvelope,
    ExecutionMode,
    LifecycleState,
    OperationKind,
)
from predictions_cup.execution.planner import build_execution_plan
from predictions_cup.execution.recovery import (
    RecoveryRest,
    recover_in_session_cancellations,
    recover_startup,
    reserve_unresolved_placements,
    unresolved_startup_exchange_ids,
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
from predictions_cup.sig.errors import SigRateLimitError, SigUnexpectedServerError
from predictions_cup.sig.trading_client import SigTradingClient
from predictions_cup.sig.trading_dto import (
    CancelAllResponseDto,
    OrderFillsResponseDto,
    OrderReadDto,
    OrderStatusFilter,
    PortfolioFillPageDto,
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

    async def list_portfolio_fills(
        self,
        *,
        exchange_id: str | None = None,
        market_id: str | None = None,
        tournament_id: str | None = None,
        limit: int = 50,
        cursor: str | None = None,
    ) -> PortfolioFillPageDto:
        del exchange_id, market_id, tournament_id, limit, cursor
        return PortfolioFillPageDto.model_validate(
            {
                "data": [],
                "pagination": {"limit": 200, "hasMore": False, "nextCursor": None},
                "coverage": {"complete": True, "projectedThroughSequence": None},
            }
        )

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


class _Order503ProjectionLagRest(_RecoveryRestFixture):
    def __init__(self) -> None:
        self.get_order_calls: list[int] = []
        self.order_list_statuses: list[OrderStatusFilter] = []
        self.portfolio_fill_calls = 0

    async def get_order(self, order_id: int) -> OrderReadDto:
        self.get_order_calls.append(order_id)
        raise SigUnexpectedServerError(
            status_code=503,
            code="SERVICE_UNAVAILABLE",
            safe_message="order projection temporarily unavailable",
        )

    def iter_orders(
        self,
        *,
        status: OrderStatusFilter = "open",
        exchange_id: str | None = None,
        market_id: str | None = None,
        tournament_id: str | None = None,
        limit: int = 200,
    ) -> AsyncIterator[OrderReadDto]:
        del exchange_id, market_id, tournament_id, limit
        self.order_list_statuses.append(status)

        async def empty() -> AsyncIterator[OrderReadDto]:
            if False:
                yield OrderReadDto.model_validate({})

        return empty()

    async def list_portfolio_fills(
        self,
        *,
        exchange_id: str | None = None,
        market_id: str | None = None,
        tournament_id: str | None = None,
        limit: int = 50,
        cursor: str | None = None,
    ) -> PortfolioFillPageDto:
        del exchange_id, market_id, tournament_id, limit, cursor
        self.portfolio_fill_calls += 1
        raise SigUnexpectedServerError(
            status_code=503,
            code="SERVICE_UNAVAILABLE",
            safe_message="portfolio fills projection temporarily unavailable",
        )


class _OrderRateLimitRest(_RecoveryRestFixture):
    def __init__(self) -> None:
        self.get_order_calls: list[int] = []
        self.portfolio_fill_calls = 0

    async def get_order(self, order_id: int) -> OrderReadDto:
        self.get_order_calls.append(order_id)
        raise SigRateLimitError(
            status_code=429,
            code="RATE_LIMITED",
            safe_message="temporary rate limit",
        )

    async def list_portfolio_fills(
        self,
        *,
        exchange_id: str | None = None,
        market_id: str | None = None,
        tournament_id: str | None = None,
        limit: int = 50,
        cursor: str | None = None,
    ) -> PortfolioFillPageDto:
        del exchange_id, market_id, tournament_id, limit, cursor
        self.portfolio_fill_calls += 1
        raise SigRateLimitError(
            status_code=429,
            code="RATE_LIMITED",
            safe_message="temporary rate limit",
        )


class _RecoveryTradingFixture:
    def __init__(self) -> None:
        self.payloads: list[str] = []
        self.cancelled_order_ids: list[int] = []
        self.on_cancel: Callable[[int], None] | None = None

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
        if self.on_cancel is not None:
            self.on_cancel(order_id)
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
    def __init__(self) -> None:
        self.order_open = True
        self.get_order_calls: list[int] = []
        self.order_list_statuses: list[OrderStatusFilter] = []

    async def get_order(self, order_id: int) -> OrderReadDto:
        assert order_id == 91
        self.get_order_calls.append(order_id)
        return OrderReadDto.model_validate(
            {
                "id": 91,
                "exchangeId": "36",
                "side": "yes",
                "action": "buy",
                "quantity": "2",
                "priceLimit": "0.495",
                "open": self.order_open,
                "createdAt": "2026-09-29T14:00:00Z",
                "expirationDate": None,
            }
        )

    def iter_orders(
        self,
        *,
        status: OrderStatusFilter = "open",
        exchange_id: str | None = None,
        market_id: str | None = None,
        tournament_id: str | None = None,
        limit: int = 200,
    ) -> AsyncIterator[OrderReadDto]:
        del exchange_id, market_id, tournament_id, limit
        self.order_list_statuses.append(status)

        async def rows() -> AsyncIterator[OrderReadDto]:
            if self.order_open or status == "all":
                yield OrderReadDto.model_validate(
                    {
                        "id": 91,
                        "exchangeId": "36",
                        "side": "yes",
                        "action": "buy",
                        "quantity": "2",
                        "priceLimit": "0.495",
                        "open": self.order_open,
                        "createdAt": "2026-09-29T14:00:00Z",
                        "expirationDate": None,
                    }
                )

        return rows()

    async def get_order_fills(
        self,
        order_id: int,
        *,
        limit: int = 50,
        cursor: str | None = None,
    ) -> OrderFillsResponseDto:
        assert order_id == 91
        assert limit == 200
        assert cursor is None
        return OrderFillsResponseDto.model_validate(
            {
                "orderId": 91,
                "exchangeId": "36",
                "tournamentId": "t1",
                "data": [],
                "pagination": {"limit": 200, "hasMore": False, "nextCursor": None},
                "coverage": {"complete": True, "projectedThroughSequence": 1},
                "totalQuantityFilled": "0",
                "avgFillPrice": None,
            }
        )


class _ClosedOrderIncompleteFillRest(_RecoveryRestFixture):
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
                "open": False,
                "createdAt": "2026-09-29T14:00:00Z",
                "expirationDate": None,
            }
        )

    async def get_order_fills(
        self,
        order_id: int,
        *,
        limit: int = 50,
        cursor: str | None = None,
    ) -> OrderFillsResponseDto:
        assert order_id == 91
        assert limit == 200
        assert cursor is None
        return OrderFillsResponseDto.model_validate(
            {
                "orderId": 91,
                "exchangeId": "36",
                "tournamentId": "t1",
                "data": [],
                "pagination": {
                    "limit": 200,
                    "hasMore": False,
                    "nextCursor": None,
                },
                "coverage": {
                    "complete": False,
                    "projectedThroughSequence": 0,
                },
                "totalQuantityFilled": "0",
                "avgFillPrice": None,
            }
        )


class _ClosedOrderCompleteFillRest(_ClosedOrderIncompleteFillRest):
    async def get_order_fills(
        self,
        order_id: int,
        *,
        limit: int = 50,
        cursor: str | None = None,
    ) -> OrderFillsResponseDto:
        assert order_id == 91
        assert limit == 200
        assert cursor is None
        return OrderFillsResponseDto.model_validate(
            {
                "orderId": 91,
                "exchangeId": "36",
                "tournamentId": "t1",
                "data": [
                    {
                        "id": 501,
                        "price": "0.4",
                        "quantity": "2",
                        "side": "yes",
                        "filledAt": "2026-10-02T09:00:01Z",
                    }
                ],
                "pagination": {
                    "limit": 200,
                    "hasMore": False,
                    "nextCursor": None,
                },
                "coverage": {
                    "complete": True,
                    "projectedThroughSequence": 501,
                },
                "totalQuantityFilled": "2",
                "avgFillPrice": "0.4",
            }
        )


def test_in_session_recovery_resolves_uncertain_cancel_without_restart(
    tmp_path: Path,
) -> None:
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
    rest = _OpenOrderRecoveryRest()
    trading.on_cancel = lambda order_id: setattr(rest, "order_open", False)
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
                rest=cast(RecoveryRest, rest),
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


def test_cancel_recovery_keeps_operation_unresolved_on_incomplete_fill_coverage(
    tmp_path: Path,
) -> None:
    cancel = ExecutionEnvelope.cancellation(
        logical_operation_id="cancel-incomplete-fill-91",
        operation_kind=OperationKind.SINGLE_CANCELLATION,
        sink_mode=ExecutionMode.LIVE,
        created_monotonic_ns=100,
        order_id=91,
        tournament_id="t1",
    )
    journal = ExecutionJournal(tmp_path / "incomplete-cancel-fill.sqlite3")
    journal.record_before_dispatch(cancel, submitted_monotonic_ns=101)
    journal.mark_state(cancel.logical_operation_id, LifecycleState.UNCERTAIN, 102)
    trading = _RecoveryTradingFixture()
    rest = _OpenOrderRecoveryRest()
    trading.on_cancel = lambda order_id: setattr(rest, "order_open", False)
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
                rest=cast(RecoveryRest, _ClosedOrderIncompleteFillRest()),
                live_sink=sink,
                tournament_id="t1",
                clock_ns=iter(range(500, 800)).__next__,
            )
        )

        assert resolved == ()
        assert [item.logical_operation_id for item in journal.unresolved()] == [
            "cancel-incomplete-fill-91"
        ]
        assert all(
            event.event_type != "RECONCILED_TERMINAL"
            for event in journal.events("cancel-incomplete-fill-91")
        )
        assert trading.cancelled_order_ids == []
    finally:
        journal.close()


def test_cancel_recovery_records_fills_before_resolving_with_complete_coverage(
    tmp_path: Path,
) -> None:
    cancel = ExecutionEnvelope.cancellation(
        logical_operation_id="cancel-complete-fill-91",
        operation_kind=OperationKind.SINGLE_CANCELLATION,
        sink_mode=ExecutionMode.LIVE,
        created_monotonic_ns=100,
        order_id=91,
        tournament_id="t1",
    )
    journal = ExecutionJournal(tmp_path / "complete-cancel-fill.sqlite3")
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
                rest=cast(RecoveryRest, _ClosedOrderCompleteFillRest()),
                live_sink=sink,
                tournament_id="t1",
                clock_ns=iter(range(500, 800)).__next__,
            )
        )

        assert resolved == ("cancel-complete-fill-91",)
        assert journal.unresolved() == ()
        events = journal.events("cancel-complete-fill-91")
        assert any(event.event_type == "AUTHORITATIVE_FILL" for event in events)
        terminal = next(
            event for event in events if event.event_type == "RECONCILED_TERMINAL"
        )
        assert terminal.terminal_status == LifecycleState.FILLED.value
        assert trading.cancelled_order_ids == []
    finally:
        journal.close()


def test_cancel_all_shutdown_response_is_durably_journaled(tmp_path: Path) -> None:
    class CancelAllTradingFixture:
        def __init__(self) -> None:
            self.calls: list[tuple[str | None, str | None, str | None]] = []

        async def cancel_all(
            self,
            *,
            tournament_id: str | None = None,
            exchange_id: str | None = None,
            market_id: str | None = None,
        ) -> CancelAllResponseDto:
            self.calls.append((tournament_id, exchange_id, market_id))
            return CancelAllResponseDto.model_validate({"cancelled": 4, "errors": []})

    journal = ExecutionJournal(tmp_path / "shutdown-cancel-all.sqlite3")
    trading = CancelAllTradingFixture()
    sink = SigLiveSink(
        client=cast(SigTradingClient, trading),
        journal=journal,
        permit=_recovery_permit(),
        reservations=ExecutionReservationBook(),
        clock_ns=iter(range(200, 500)).__next__,
    )
    envelope = ExecutionEnvelope.cancellation(
        logical_operation_id="make-shutdown-cancel-all-test",
        operation_kind=OperationKind.CANCEL_ALL,
        sink_mode=ExecutionMode.LIVE,
        created_monotonic_ns=100,
        tournament_id="t1",
    )
    try:
        result = asyncio.run(sink.cancel(envelope))
        events = journal.events(envelope.logical_operation_id)

        assert trading.calls == [("t1", None, None)]
        assert result.state is LifecycleState.CANCELLED
        assert journal.unresolved() == ()
        cancel_ack = next(event for event in events if event.event_type == "CANCEL_ACK")
        assert cancel_ack.terminal_status == LifecycleState.CANCELLED.value
        assert json.loads(cancel_ack.detail_json or "{}") == {
            "cancelled": 4,
            "errors": [],
        }
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
    rest = _OpenOrderRecoveryRest()
    trading.on_cancel = lambda order_id: setattr(rest, "order_open", False)
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
                rest=cast(RecoveryRest, rest),
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


def test_restart_auto_cancels_unresolved_open_placement_and_reconciles(
    tmp_path: Path,
) -> None:
    from predictions_cup.execution.models import RuntimeOrderIntent

    intent = RuntimeOrderIntent(
        intent_id="restart-open-intent",
        exchange_id="36",
        market_id="m1",
        tournament_id="t1",
        outcome_side=OutcomeSide.YES,
        action=OrderAction.BUY,
        quantity=2,
        limit_price_ticks=99,
        strategy_id="make-direct-pm",
        decision_observation_ns=123,
    )
    envelope = ExecutionEnvelope.placement(
        logical_operation_id="restart-open-placement",
        operation_kind=OperationKind.SINGLE_PLACEMENT,
        sink_mode=ExecutionMode.LIVE,
        idempotency_key="restart-open-original-key",
        intents=(intent,),
        created_monotonic_ns=100,
    )
    journal = ExecutionJournal(tmp_path / "restart-open.sqlite3")
    journal.record_before_dispatch(envelope, (intent,), submitted_monotonic_ns=101)
    journal.record_event(
        logical_operation_id=envelope.logical_operation_id,
        tournament_id="t1",
        logical_intent_id=intent.intent_id,
        event_type="ACK",
        observed_monotonic_ns=102,
        exchange_id="36",
        exchange_order_id="91",
        terminal_status=LifecycleState.OPEN.value,
    )
    journal.mark_state(envelope.logical_operation_id, LifecycleState.OPEN, 102)

    rest = _OpenOrderRecoveryRest()
    trading = _RecoveryTradingFixture()
    trading.on_cancel = lambda order_id: setattr(rest, "order_open", False)
    sink = SigLiveSink(
        client=cast(SigTradingClient, trading),
        journal=journal,
        permit=_recovery_permit(),
        reservations=ExecutionReservationBook(),
        clock_ns=iter(range(200, 600)).__next__,
    )
    try:
        result = asyncio.run(
            recover_startup(
                journal=journal,
                rest=cast(RecoveryRest, rest),
                live_sink=sink,
                tournament_id="t1",
                tournament_slug="cup",
                clock_ns=iter(range(600, 900)).__next__,
                market_by_exchange={"36": "m1"},
                exchange_ids_by_market={"m1": ("36",)},
                reservations=ExecutionReservationBook(),
            )
        )

        assert result.safe_to_resume_live
        assert result.unresolved_operation_ids == ()
        assert result.unresolved_exchange_ids == ()
        assert trading.payloads == []
        assert trading.cancelled_order_ids == [91]
        assert rest.get_order_calls == []
        assert rest.order_list_statuses == ["open", "open"]
        assert journal.unresolved() == ()
        terminal = next(
            event
            for event in journal.events(envelope.logical_operation_id)
            if event.event_type == "RECONCILED_TERMINAL"
        )
        assert terminal.terminal_status == LifecycleState.CANCELLED.value
    finally:
        journal.close()


def test_startup_bulk_recovery_paginates_tournament_fills_without_order_reads(
    tmp_path: Path,
) -> None:
    from predictions_cup.execution.models import RuntimeOrderIntent

    intent = RuntimeOrderIntent(
        intent_id="bulk-fill-intent",
        exchange_id="36",
        market_id="m1",
        tournament_id="t1",
        outcome_side=OutcomeSide.YES,
        action=OrderAction.BUY,
        quantity=2,
        limit_price_ticks=99,
        strategy_id="make-direct-pm",
        decision_observation_ns=123,
    )
    envelope = ExecutionEnvelope.placement(
        logical_operation_id="bulk-fill-placement",
        operation_kind=OperationKind.SINGLE_PLACEMENT,
        sink_mode=ExecutionMode.LIVE,
        idempotency_key="bulk-fill-original-key",
        intents=(intent,),
        created_monotonic_ns=100,
    )
    journal = ExecutionJournal(tmp_path / "bulk-fill.sqlite3")
    journal.record_before_dispatch(envelope, (intent,), submitted_monotonic_ns=101)
    journal.record_event(
        logical_operation_id=envelope.logical_operation_id,
        tournament_id="t1",
        logical_intent_id=intent.intent_id,
        event_type="ACK",
        observed_monotonic_ns=102,
        exchange_id="36",
        exchange_order_id="91",
        quantity="2",
        terminal_status=LifecycleState.OPEN.value,
    )
    journal.mark_state(envelope.logical_operation_id, LifecycleState.OPEN, 102)

    class PaginatedFillRest(_RecoveryRestFixture):
        def __init__(self) -> None:
            self.cursors: list[str | None] = []

        async def list_portfolio_fills(
            self,
            *,
            exchange_id: str | None = None,
            market_id: str | None = None,
            tournament_id: str | None = None,
            limit: int = 50,
            cursor: str | None = None,
        ) -> PortfolioFillPageDto:
            assert exchange_id is None and market_id is None
            assert tournament_id == "t1" and limit == 200
            self.cursors.append(cursor)
            if cursor is None:
                return PortfolioFillPageDto.model_validate(
                    {
                        "data": [],
                        "pagination": {
                            "limit": 200,
                            "hasMore": True,
                            "nextCursor": "fills-page-2",
                        },
                        "coverage": {
                            "complete": True,
                            "projectedThroughSequence": 500,
                        },
                    }
                )
            assert cursor == "fills-page-2"
            return PortfolioFillPageDto.model_validate(
                {
                    "data": [
                        {
                            "id": 501,
                            "orderId": 91,
                            "exchangeId": "36",
                            "marketId": "m1",
                            "price": "0.4",
                            "quantity": "2",
                            "side": "yes",
                            "filledAt": "2026-10-02T09:00:01Z",
                        }
                    ],
                    "pagination": {
                        "limit": 200,
                        "hasMore": False,
                        "nextCursor": None,
                    },
                    "coverage": {
                        "complete": True,
                        "projectedThroughSequence": 501,
                    },
                }
            )

    rest = PaginatedFillRest()
    sink = SigLiveSink(
        client=cast(SigTradingClient, _RecoveryTradingFixture()),
        journal=journal,
        permit=_recovery_permit(),
        reservations=ExecutionReservationBook(),
    )
    account = AccountAuthoritativeSnapshot(
        tournament_id="t1",
        tournament_slug="cup",
        open_orders=(),
        positions=(),
        observed_at=datetime(2026, 10, 2, tzinfo=UTC),
    )
    try:
        result = asyncio.run(
            recover_startup(
                journal=journal,
                rest=cast(RecoveryRest, rest),
                live_sink=sink,
                tournament_id="t1",
                tournament_slug="cup",
                authoritative_snapshot=account,
                clock_ns=iter(range(500, 800)).__next__,
            )
        )

        assert rest.cursors == [None, "fills-page-2"]
        assert result.safe_to_resume_live
        assert result.unresolved_operation_ids == ()
        assert any(
            event.event_type == "AUTHORITATIVE_FILL" and event.fill_id == "501"
            for event in journal.events(envelope.logical_operation_id)
        )
        terminal = next(
            event
            for event in journal.events(envelope.logical_operation_id)
            if event.event_type == "RECONCILED_TERMINAL"
        )
        assert terminal.terminal_status == LifecycleState.FILLED.value
    finally:
        journal.close()


def test_startup_recovery_blocks_only_unresolved_batch_exchange_and_skips_cancelled_acks(
    tmp_path: Path,
) -> None:
    from predictions_cup.execution.models import RuntimeOrderIntent

    intents = (
        RuntimeOrderIntent(
            intent_id="batch-intent-36",
            exchange_id="36",
            market_id="m1",
            tournament_id="t1",
            outcome_side=OutcomeSide.YES,
            action=OrderAction.BUY,
            quantity=1,
            limit_price_ticks=99,
            strategy_id="make-direct-pm",
            decision_observation_ns=123,
        ),
        RuntimeOrderIntent(
            intent_id="batch-intent-37",
            exchange_id="37",
            market_id="m2",
            tournament_id="t1",
            outcome_side=OutcomeSide.YES,
            action=OrderAction.BUY,
            quantity=1,
            limit_price_ticks=99,
            strategy_id="make-direct-pm",
            decision_observation_ns=123,
        ),
    )
    envelope = ExecutionEnvelope.placement(
        logical_operation_id="batch-startup-placement",
        operation_kind=OperationKind.BEST_EFFORT_BATCH,
        sink_mode=ExecutionMode.LIVE,
        idempotency_key="batch-startup-key",
        intents=intents,
        created_monotonic_ns=100,
    )
    journal = ExecutionJournal(tmp_path / "batch-startup.sqlite3")
    journal.record_before_dispatch(envelope, intents, submitted_monotonic_ns=101)
    for intent, order_id in zip(intents, (91, 92), strict=True):
        journal.record_event(
            logical_operation_id=envelope.logical_operation_id,
            tournament_id="t1",
            logical_intent_id=intent.intent_id,
            event_type="ACK",
            observed_monotonic_ns=102,
            exchange_id=intent.exchange_id,
            exchange_order_id=str(order_id),
            quantity="1",
            terminal_status=LifecycleState.OPEN.value,
        )
    journal.mark_state(envelope.logical_operation_id, LifecycleState.ACKED, 102)
    prior_cancel = ExecutionEnvelope.cancellation(
        logical_operation_id="batch-prior-cancel-91",
        operation_kind=OperationKind.SINGLE_CANCELLATION,
        sink_mode=ExecutionMode.LIVE,
        created_monotonic_ns=110,
        order_id=91,
        tournament_id="t1",
    )
    journal.record_before_dispatch(prior_cancel, submitted_monotonic_ns=111)
    journal.record_event(
        logical_operation_id=prior_cancel.logical_operation_id,
        tournament_id="t1",
        event_type="CANCEL_ACK",
        observed_monotonic_ns=112,
        exchange_order_id="91",
        terminal_status=LifecycleState.CANCELLED.value,
    )
    journal.mark_state(prior_cancel.logical_operation_id, LifecycleState.CANCELLED, 112)

    class BatchRest(_RecoveryRestFixture):
        def __init__(self) -> None:
            self.open_order = OrderReadDto.model_validate(
                {
                    "id": 92,
                    "exchangeId": "37",
                    "side": "yes",
                    "action": "buy",
                    "quantity": "1",
                    "priceLimit": "0.495",
                    "open": True,
                    "createdAt": "2026-09-29T14:00:00Z",
                    "expirationDate": None,
                }
            )
            self.order_list_statuses: list[OrderStatusFilter] = []

        def iter_orders(
            self,
            *,
            status: OrderStatusFilter = "open",
            exchange_id: str | None = None,
            market_id: str | None = None,
            tournament_id: str | None = None,
            limit: int = 200,
        ) -> AsyncIterator[OrderReadDto]:
            del exchange_id, market_id, tournament_id, limit
            self.order_list_statuses.append(status)

            async def rows() -> AsyncIterator[OrderReadDto]:
                if self.open_order.open:
                    yield self.open_order

            return rows()

    rest = BatchRest()
    reservations = ExecutionReservationBook()
    open_order = rest.open_order
    snapshot = AccountAuthoritativeSnapshot(
        tournament_id="t1",
        tournament_slug="cup",
        open_orders=(open_order,),
        positions=(),
        observed_at=datetime(2026, 10, 2, tzinfo=UTC),
    )
    exchange_ids_by_market: dict[str, tuple[str, ...]] = {
        "m1": ("36",),
        "m2": ("37",),
    }
    try:
        initial_blockers = unresolved_startup_exchange_ids(
            journal,
            journal.unresolved(),
            exchange_ids_by_market=exchange_ids_by_market,
            open_order_ids=frozenset({92}),
        )
        assert initial_blockers == ("37",)
        reserve_unresolved_placements(
            journal,
            journal.unresolved(),
            reservations=reservations,
            market_by_exchange={"36": "m1", "37": "m2"},
            open_order_ids=frozenset({92}),
        )
        assert reservations.intent_ids() == frozenset({"batch-intent-37"})

        trading = _RecoveryTradingFixture()

        def close_order(order_id: int) -> None:
            assert order_id == 92
            rest.open_order = rest.open_order.model_copy(update={"open": False})

        trading.on_cancel = close_order
        sink = SigLiveSink(
            client=cast(SigTradingClient, trading),
            journal=journal,
            permit=_recovery_permit(),
            reservations=reservations,
        )
        result = asyncio.run(
            recover_startup(
                journal=journal,
                rest=cast(RecoveryRest, rest),
                live_sink=sink,
                tournament_id="t1",
                tournament_slug="cup",
                authoritative_snapshot=snapshot,
                exchange_ids_by_market=exchange_ids_by_market,
                market_by_exchange={"36": "m1", "37": "m2"},
                reservations=reservations,
            )
        )
        assert trading.cancelled_order_ids == [92]
        assert rest.order_list_statuses == ["open"]
        assert result.unresolved_exchange_ids == ()
        assert result.unresolved_operation_ids == ()
        assert journal.unresolved() == ()
    finally:
        journal.close()


def test_bulk_fill_projection_failure_keeps_only_affected_exchange_blocked(
    tmp_path: Path,
) -> None:
    from predictions_cup.execution.models import RuntimeOrderIntent

    intent = RuntimeOrderIntent(
        intent_id="projection-lag-intent",
        exchange_id="36",
        market_id="m1",
        tournament_id="t1",
        outcome_side=OutcomeSide.YES,
        action=OrderAction.BUY,
        quantity=2,
        limit_price_ticks=99,
        strategy_id="make-direct-pm",
        decision_observation_ns=123,
    )
    envelope = ExecutionEnvelope.placement(
        logical_operation_id="projection-lag-placement",
        operation_kind=OperationKind.SINGLE_PLACEMENT,
        sink_mode=ExecutionMode.LIVE,
        idempotency_key="projection-lag-original-key",
        intents=(intent,),
        created_monotonic_ns=100,
    )
    journal = ExecutionJournal(tmp_path / "projection-lag.sqlite3")
    journal.record_before_dispatch(envelope, (intent,), submitted_monotonic_ns=101)
    journal.record_event(
        logical_operation_id=envelope.logical_operation_id,
        tournament_id="t1",
        logical_intent_id=intent.intent_id,
        event_type="ACK",
        observed_monotonic_ns=102,
        exchange_id="36",
        exchange_order_id="91",
        terminal_status=LifecycleState.OPEN.value,
    )
    journal.mark_state(envelope.logical_operation_id, LifecycleState.OPEN, 102)
    reservations = ExecutionReservationBook()
    rest = _Order503ProjectionLagRest()
    trading = _RecoveryTradingFixture()
    sink = SigLiveSink(
        client=cast(SigTradingClient, trading),
        journal=journal,
        permit=_recovery_permit(),
        reservations=reservations,
        clock_ns=iter(range(200, 500)).__next__,
    )
    account = AccountAuthoritativeSnapshot(
        tournament_id="t1",
        tournament_slug="cup",
        open_orders=(),
        positions=(),
        observed_at=datetime(2026, 10, 2, tzinfo=UTC),
    )
    try:
        result = asyncio.run(
            recover_startup(
                journal=journal,
                rest=cast(RecoveryRest, rest),
                live_sink=sink,
                tournament_id="t1",
                tournament_slug="cup",
                authoritative_snapshot=account,
                market_by_exchange={"36": "m1"},
                exchange_ids_by_market={"m1": ("36",)},
                reservations=reservations,
                clock_ns=iter(range(500, 800)).__next__,
            )
        )

        assert result.safe_to_resume_live is False
        assert result.unresolved_operation_ids == (envelope.logical_operation_id,)
        assert result.unresolved_exchange_ids == ("36",)
        assert rest.get_order_calls == []
        assert rest.order_list_statuses == []
        assert rest.portfolio_fill_calls == 3
        assert trading.cancelled_order_ids == []
        assert reservations.contains_operation(
            envelope.logical_operation_id,
            envelope.intent_ids,
        )
    finally:
        journal.close()


def test_startup_recovery_backs_off_boundedly_on_429_and_keeps_blocker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from predictions_cup.execution.models import RuntimeOrderIntent

    intent = RuntimeOrderIntent(
        intent_id="rate-limit-intent",
        exchange_id="36",
        market_id="m1",
        tournament_id="t1",
        outcome_side=OutcomeSide.YES,
        action=OrderAction.BUY,
        quantity=1,
        limit_price_ticks=99,
        strategy_id="make-direct-pm",
        decision_observation_ns=123,
    )
    envelope = ExecutionEnvelope.placement(
        logical_operation_id="rate-limit-placement",
        operation_kind=OperationKind.SINGLE_PLACEMENT,
        sink_mode=ExecutionMode.LIVE,
        idempotency_key="rate-limit-original-key",
        intents=(intent,),
        created_monotonic_ns=100,
    )
    journal = ExecutionJournal(tmp_path / "rate-limit-recovery.sqlite3")
    journal.record_before_dispatch(envelope, (intent,), submitted_monotonic_ns=101)
    journal.record_event(
        logical_operation_id=envelope.logical_operation_id,
        tournament_id="t1",
        logical_intent_id=intent.intent_id,
        event_type="ACK",
        observed_monotonic_ns=102,
        exchange_id="36",
        exchange_order_id="91",
        terminal_status=LifecycleState.OPEN.value,
    )
    journal.mark_state(envelope.logical_operation_id, LifecycleState.OPEN, 102)
    rest = _OrderRateLimitRest()
    trading = _RecoveryTradingFixture()
    sink = SigLiveSink(
        client=cast(SigTradingClient, trading),
        journal=journal,
        permit=_recovery_permit(),
        reservations=ExecutionReservationBook(),
        clock_ns=iter(range(200, 500)).__next__,
    )
    delays: list[float] = []

    async def no_sleep(delay: float) -> None:
        delays.append(delay)

    monkeypatch.setattr("predictions_cup.execution.recovery.asyncio.sleep", no_sleep)
    account = AccountAuthoritativeSnapshot(
        tournament_id="t1",
        tournament_slug="cup",
        open_orders=(),
        positions=(),
        observed_at=datetime(2026, 10, 2, tzinfo=UTC),
    )
    try:
        result = asyncio.run(
            recover_startup(
                journal=journal,
                rest=cast(RecoveryRest, rest),
                live_sink=sink,
                tournament_id="t1",
                tournament_slug="cup",
                authoritative_snapshot=account,
                market_by_exchange={"36": "m1"},
                exchange_ids_by_market={"m1": ("36",)},
                reservations=ExecutionReservationBook(),
                clock_ns=iter(range(500, 800)).__next__,
            )
        )

        assert rest.get_order_calls == []
        assert rest.portfolio_fill_calls == 3
        assert delays == [1.0, 2.0]
        assert result.safe_to_resume_live is False
        assert result.unresolved_exchange_ids == ("36",)
        assert result.unresolved_operation_ids == (envelope.logical_operation_id,)
        assert trading.cancelled_order_ids == []
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
    rest = _OpenOrderRecoveryRest()
    trading.on_cancel = lambda order_id: setattr(rest, "order_open", False)
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
                rest=cast(RecoveryRest, rest),
                live_sink=sink,
                tournament_id="t1",
                tournament_slug="cup",
                clock_ns=iter(range(900, 1200)).__next__,
                observation_emitter=emitter,
                observation_process_instance_id="recovery-test-process",
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
