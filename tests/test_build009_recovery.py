from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
import sqlite3

import pytest

from predictions_cup.execution.journal import ExecutionJournal
from predictions_cup.execution.models import ExecutionMode, LifecycleState, OperationKind
from predictions_cup.execution.planner import build_execution_plan
from predictions_cup.execution.replacement import quote_replacement_allowed
from predictions_cup.risk.core import RiskContext, RiskDecision, evaluate_risk
from predictions_cup.runtime import RuntimeOrderState, RuntimePortfolio, RuntimeSnapshot
from predictions_cup.sig.account_reconciliation import AccountAuthoritativeSnapshot
from predictions_cup.sig.account_state import (
    AccountRealtimeStateEngine,
    AccountTrustTransition,
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
        mode=ExecutionMode.LIVE,
        logical_operation_id="logical-1",
        created_monotonic_ns=456,
    )

    path = tmp_path / "execution.sqlite3"
    journal = ExecutionJournal(path)
    journal.record_before_dispatch(plan.envelope, plan.intents)
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
        assert submission.logical_intent_id == "intent-1"
        assert submission.decision_observation_ns == 123
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
    assert {"strategy_family", "strategy_id", "signal_value", "fair_value"} <= columns


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
