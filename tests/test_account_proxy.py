from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from predictions_cup.config import AppSettings
from predictions_cup.execution.journal import ExecutionJournal
from predictions_cup.execution.models import (
    ExecutionEnvelope,
    ExecutionMode,
    LifecycleState,
    OperationKind,
    RuntimeOrderIntent,
)
from predictions_cup.execution.reservations import ExecutionReservationBook
from predictions_cup.runtime.models import AccountTrustGrade, OrderAction, OutcomeSide
from predictions_cup.sig.account_proxy import AccountProxyLedger
from predictions_cup.sig.account_reconciliation import AccountAuthoritativeSnapshot
from predictions_cup.sig.account_runtime import AccountRealtimeController
from predictions_cup.sig.account_state import AccountRealtimeStateEngine, AccountTrustTransition
from predictions_cup.sig.realtime_models import RealtimeTokenDto
from predictions_cup.sig.trading_dto import OrderReadDto, PositionReadDto

TOURNAMENT = "t1"
EXCHANGE = "36"
MARKET = "m1"


def _position(quantity: str) -> PositionReadDto:
    return PositionReadDto.model_validate(
        {
            "exchangeId": EXCHANGE,
            "marketId": MARKET,
            "marketTitle": "Fixture market",
            "option": "YES",
            "settled": False,
            "quantity": quantity,
            "avgCost": "0.40",
            "currentPrice": "0.50",
            "marketValue": str(Decimal(quantity) * Decimal("0.50")),
            "costBasis": str(Decimal(quantity) * Decimal("0.40")),
            "unrealizedPnl": "0",
            "unrealizedPnlPct": "0",
            "moneyEarned": "0",
            "lots": [],
        }
    )


def _snapshot(
    *,
    quantity: str | None = None,
    cash: str = "100",
) -> AccountAuthoritativeSnapshot:
    return AccountAuthoritativeSnapshot(
        tournament_id=TOURNAMENT,
        tournament_slug="cup",
        open_orders=(),
        positions=() if quantity is None else (_position(quantity),),
        observed_at=datetime.now(UTC),
        cash_balance=Decimal(cash),
    )


def _open_order() -> OrderReadDto:
    return OrderReadDto.model_validate(
        {
            "id": 101,
            "exchangeId": EXCHANGE,
            "side": "yes",
            "action": "buy",
            "quantity": "10",
            "priceLimit": "0.40",
            "open": True,
            "createdAt": "2026-10-02T00:00:00Z",
            "expirationDate": None,
        }
    )


def _batch(*, revision: int, quantity: str, total_cost: str) -> dict[str, object]:
    return {
        "fills": [
            {
                "orderId": 101,
                "exchangeId": EXCHANGE,
                "marketId": MARKET,
                "price": "0.40",
                "quantity": quantity,
                "executedAt": "2026-10-02T00:00:01Z",
                "tournamentId": TOURNAMENT,
            }
        ],
        "orderUpdates": [
            {
                "orderId": 101,
                "exchangeId": EXCHANGE,
                "marketId": MARKET,
                "open": True,
                "quantityTraded": quantity,
                "totalCost": total_cost,
                "latestTradePrice": "0.40",
                "tournamentId": TOURNAMENT,
                "at": "2026-10-02T00:00:01Z",
            }
        ],
        "settlements": [],
        "refunds": [],
        "collateralChanges": [],
        "delivery": {
            "model": "engine",
            "revision": revision,
            "previousRevision": revision - 1,
            "correlationId": f"batch-{revision}",
            "sourceSequenceFrom": revision,
            "sourceSequenceThrough": revision,
        },
    }


def _ledger(*, threshold: float = 1.0, max_age_ns: int = 100) -> AccountProxyLedger:
    return AccountProxyLedger(
        tournament_id=TOURNAMENT,
        enabled=True,
        max_age_ns=max_age_ns,
        position_diff_threshold=threshold,
        exchange_market_ids={EXCHANGE: MARKET},
    )


def test_trust_grade_transitions_and_age_limit() -> None:
    ledger = _ledger()
    ledger.seed_authoritative(_snapshot(), observed_monotonic_ns=1_000)

    assert ledger.trust_grade(account_state_trusted=True, proxy_age_ns=0) is (
        AccountTrustGrade.TRUSTED
    )
    assert ledger.trust_grade(account_state_trusted=False, proxy_age_ns=50) is (
        AccountTrustGrade.PROXY
    )
    assert ledger.trust_grade(account_state_trusted=False, proxy_age_ns=101) is (
        AccountTrustGrade.UNTRUSTED
    )

    ledger.mark_discrepancy("fixture_mismatch")
    assert ledger.trust_grade(account_state_trusted=False, proxy_age_ns=50) is (
        AccountTrustGrade.UNTRUSTED
    )
    ledger.seed_authoritative(_snapshot(), observed_monotonic_ns=2_000)
    assert ledger.trust_grade(account_state_trusted=True, proxy_age_ns=0) is (
        AccountTrustGrade.TRUSTED
    )


def test_account_proxy_configuration_is_default_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Keep the opt-in assertion independent of the shell environment used by CI.
    monkeypatch.delenv("PREDICTIONS_CUP_ACCOUNT_PROXY_ENABLED", raising=False)
    assert AppSettings().account_proxy_enabled is False


def test_journal_ack_partial_duplicate_fill_and_cancel_are_conservative(
    tmp_path: Path,
) -> None:
    journal = ExecutionJournal(tmp_path / "execution.sqlite3")
    ledger = _ledger()
    ledger.initialize_journal_cursor(journal)
    journal.add_operation_listener(
        lambda operation_id: ledger.apply_journal_operation(journal, operation_id)
    )
    ledger.seed_authoritative(_snapshot(), observed_monotonic_ns=1_000)
    intent = RuntimeOrderIntent(
        intent_id="intent-1",
        exchange_id=EXCHANGE,
        market_id=MARKET,
        tournament_id=TOURNAMENT,
        outcome_side=OutcomeSide.YES,
        action=OrderAction.BUY,
        quantity=10,
        limit_price_ticks=80,
        strategy_id="test",
        decision_observation_ns=1_001,
    )
    envelope = ExecutionEnvelope.placement(
        logical_operation_id="op-1",
        operation_kind=OperationKind.SINGLE_PLACEMENT,
        sink_mode=ExecutionMode.LIVE,
        idempotency_key="key-1",
        intents=(intent,),
        created_monotonic_ns=1_002,
    )
    journal.record_before_dispatch(envelope, (intent,))
    journal.record_event(
        logical_operation_id="op-1",
        logical_intent_id="intent-1",
        event_type="ACK",
        observed_monotonic_ns=1_003,
        exchange_id=EXCHANGE,
        exchange_order_id="101",
        quantity="10",
        terminal_status="OPEN",
        detail_json='{"open":true,"quantityTraded":"0","totalCost":"0"}',
    )
    journal.mark_state("op-1", LifecycleState.OPEN, 1_003)
    journal.record_event(
        logical_operation_id="op-1",
        logical_intent_id="intent-1",
        event_type="REALTIME_FILL",
        observed_monotonic_ns=1_004,
        source_timestamp="2026-10-02T00:00:01+00:00",
        exchange_id=EXCHANGE,
        exchange_order_id="101",
        fill_id="fill-1",
        quantity="3",
        price="0.40",
    )
    # SIG may report the same fill through the realtime stream and a later
    # order-fill recovery query. The fill id and canonical tuple dedupe it.
    journal.record_event(
        logical_operation_id="op-1",
        logical_intent_id="intent-1",
        event_type="AUTHORITATIVE_FILL",
        observed_monotonic_ns=1_005,
        source_timestamp="2026-10-02T00:00:01+00:00",
        exchange_id=EXCHANGE,
        exchange_order_id="101",
        fill_id="fill-1",
        quantity="3",
        price="0.40",
    )

    portfolio = ledger.runtime_portfolio(
        account_state_trusted=False,
        now_monotonic_ns=1_050,
    )
    assert portfolio.trust_grade is AccountTrustGrade.PROXY
    assert portfolio.signed_inventory(EXCHANGE, TOURNAMENT) == 3.0
    assert portfolio.account_proxy_cash_balance == Decimal("98.80")
    assert len(portfolio.orders) == 1
    assert portfolio.orders[0].reserved_exposure == 7.0

    journal.record_event(
        logical_operation_id="op-1",
        event_type="CANCEL_ACK",
        observed_monotonic_ns=1_006,
        exchange_order_id="101",
        terminal_status="CANCELLED",
    )
    cancelled = ledger.runtime_portfolio(
        account_state_trusted=False,
        now_monotonic_ns=1_060,
    )
    assert cancelled.orders[0].open is False
    assert cancelled.orders[0].uncertain is True
    assert cancelled.worst_case_inventory_bounds(EXCHANGE, TOURNAMENT) == (3.0, 10.0)

    ledger.seed_authoritative(_snapshot(quantity="3", cash="98.80"), observed_monotonic_ns=2_000)
    reconciled = ledger.runtime_portfolio(
        account_state_trusted=True,
        now_monotonic_ns=2_000,
    )
    assert reconciled.trust_grade is AccountTrustGrade.TRUSTED
    assert reconciled.signed_inventory(EXCHANGE, TOURNAMENT) == 3.0
    assert reconciled.orders == ()
    journal.close()


def test_position_reconciliation_diff_blocks_proxy_until_clean_snapshot() -> None:
    ledger = _ledger(threshold=0.5)
    ledger.seed_authoritative(_snapshot(), observed_monotonic_ns=1_000)
    diff = ledger.seed_authoritative(
        _snapshot(quantity="2", cash="100"),
        observed_monotonic_ns=2_000,
    )

    assert diff.position_diff_exceeded is True
    assert ledger.diff_count == 1
    assert ledger.trust_grade(account_state_trusted=False, proxy_age_ns=0) is (
        AccountTrustGrade.UNTRUSTED
    )

    ledger.seed_authoritative(
        _snapshot(quantity="2", cash="100"),
        observed_monotonic_ns=3_000,
    )
    assert ledger.trust_grade(account_state_trusted=True, proxy_age_ns=0) is (
        AccountTrustGrade.TRUSTED
    )


def test_realtime_batch_applies_fill_and_order_update_once() -> None:
    ledger = _ledger()
    snapshot = AccountAuthoritativeSnapshot(
        tournament_id=TOURNAMENT,
        tournament_slug="cup",
        open_orders=(_open_order(),),
        positions=(_position("-5"),),
        observed_at=datetime.now(UTC),
        cash_balance=Decimal("100"),
    )
    ledger.seed_authoritative(snapshot, observed_monotonic_ns=1_000)
    batch = _batch(revision=1, quantity="2", total_cost="0.80")

    ledger.apply_realtime_batch(batch)
    ledger.apply_realtime_batch(batch)
    portfolio = ledger.runtime_portfolio(
        account_state_trusted=False,
        now_monotonic_ns=1_050,
    )

    assert portfolio.trust_grade is AccountTrustGrade.PROXY
    assert portfolio.signed_inventory(EXCHANGE, TOURNAMENT) == -3.0
    assert portfolio.account_proxy_cash_balance == Decimal("99.20")
    assert portfolio.worst_case_inventory_bounds(EXCHANGE, TOURNAMENT) == (-3.0, 5.0)


@pytest.mark.parametrize(
    ("order_id", "expected_grade", "expected_inventory"),
    [
        (101, AccountTrustGrade.PROXY, -4.0),
        (999, AccountTrustGrade.UNTRUSTED, -5.0),
    ],
)
def test_resync_batch_keeps_known_proxy_activity_and_rejects_unknown_identity(
    order_id: int,
    expected_grade: AccountTrustGrade,
    expected_inventory: float,
) -> None:
    ledger = _ledger(max_age_ns=10_000)
    state = AccountRealtimeStateEngine(
        tournament_id=TOURNAMENT,
        account_proxy=ledger,
        clock_ns=lambda: 1_050,
    )
    snapshot = AccountAuthoritativeSnapshot(
        tournament_id=TOURNAMENT,
        tournament_slug="cup",
        open_orders=(_open_order(),),
        positions=(_position("-5"),),
        observed_at=datetime.now(UTC),
        cash_balance=Decimal("100"),
    )
    state.apply_authoritative(snapshot, observed_monotonic_ns=1_000)

    async def unused_mint_token() -> RealtimeTokenDto:
        raise AssertionError("resync callback must not mint a token")

    async def unused_authoritative_resync() -> AccountAuthoritativeSnapshot:
        raise AssertionError("resync callback must not run")

    controller = AccountRealtimeController(
        state=state,
        mint_token=unused_mint_token,
        authoritative_resync=unused_authoritative_resync,
    )
    controller._resyncing = True
    payload = _batch(revision=1, quantity="1", total_cost="0.40")
    for field in ("fills", "orderUpdates"):
        events = payload[field]
        assert isinstance(events, list)
        assert isinstance(events[0], dict)
        events[0]["orderId"] = order_id

    asyncio.run(controller._handle_batch("account", payload, datetime.now(UTC)))
    state.mark_untrusted(AccountTrustTransition.UNTRUSTED_RESYNC_ACTIVITY)

    portfolio = state.runtime_portfolio()
    assert portfolio.account_trust_grade is expected_grade
    assert portfolio.signed_inventory(EXCHANGE, TOURNAMENT) == expected_inventory
    if expected_grade is AccountTrustGrade.PROXY:
        assert ledger.discrepancy_reason is None
    else:
        assert ledger.discrepancy_reason == "fill_for_unknown_order"


def test_unmatched_baseline_order_update_fails_closed() -> None:
    ledger = _ledger()
    snapshot = AccountAuthoritativeSnapshot(
        tournament_id=TOURNAMENT,
        tournament_slug="cup",
        open_orders=(_open_order(),),
        positions=(_position("-5"),),
        observed_at=datetime.now(UTC),
        cash_balance=Decimal("100"),
    )
    ledger.seed_authoritative(snapshot, observed_monotonic_ns=1_000)
    batch = _batch(revision=1, quantity="2", total_cost="0.80")
    batch["fills"] = []

    ledger.apply_realtime_batch(batch)

    portfolio = ledger.runtime_portfolio(
        account_state_trusted=False,
        now_monotonic_ns=1_050,
    )
    assert portfolio.trust_grade is AccountTrustGrade.UNTRUSTED
    assert ledger.discrepancy_reason == "baseline_order_fill_delta_unknown"


def test_proxy_to_trusted_resync_applies_position_diff() -> None:
    ledger = _ledger()
    initial = AccountAuthoritativeSnapshot(
        tournament_id=TOURNAMENT,
        tournament_slug="cup",
        open_orders=(_open_order(),),
        positions=(_position("-5"),),
        observed_at=datetime.now(UTC),
        cash_balance=Decimal("100"),
    )
    ledger.seed_authoritative(initial, observed_monotonic_ns=1_000)
    ledger.apply_realtime_batch(_batch(revision=1, quantity="2", total_cost="0.80"))
    proxy = ledger.runtime_portfolio(
        account_state_trusted=False,
        now_monotonic_ns=1_050,
    )
    assert proxy.trust_grade is AccountTrustGrade.PROXY

    reconciled_snapshot = AccountAuthoritativeSnapshot(
        tournament_id=TOURNAMENT,
        tournament_slug="cup",
        open_orders=(_open_order(),),
        positions=(_position("-2.5"),),
        observed_at=datetime.now(UTC),
        cash_balance=Decimal("99.20"),
    )
    diff = ledger.seed_authoritative(
        reconciled_snapshot,
        observed_monotonic_ns=2_000,
    )
    trusted = ledger.runtime_portfolio(
        account_state_trusted=True,
        now_monotonic_ns=2_000,
    )

    assert diff.position_diffs == ((EXCHANGE, MARKET, -0.5),)
    assert diff.has_diff
    assert trusted.trust_grade is AccountTrustGrade.TRUSTED
    assert trusted.signed_inventory(EXCHANGE, TOURNAMENT) == -2.5


def test_ack_after_snapshot_cutoff_remains_risk_bearing_when_absent() -> None:
    observed_at = datetime.now(UTC)
    snapshot = AccountAuthoritativeSnapshot(
        tournament_id=TOURNAMENT,
        tournament_slug="cup",
        open_orders=(),
        positions=(),
        observed_at=observed_at,
        cash_balance=Decimal("100"),
    )
    reservations = ExecutionReservationBook()
    ledger = _ledger(max_age_ns=10_000_000_000)
    state = AccountRealtimeStateEngine(
        tournament_id=TOURNAMENT,
        reservations=reservations,
        account_proxy=ledger,
        clock_ns=lambda: 1_000_000_000,
    )
    state.apply_authoritative(snapshot, observed_monotonic_ns=1_000_000_000)

    intent = RuntimeOrderIntent(
        intent_id="late-ack-intent",
        exchange_id=EXCHANGE,
        market_id=MARKET,
        tournament_id=TOURNAMENT,
        outcome_side=OutcomeSide.YES,
        action=OrderAction.BUY,
        quantity=10,
        limit_price_ticks=80,
        strategy_id="test",
        decision_observation_ns=1_000_000_001,
    )
    reservations.reserve("late-ack-op", (intent,))
    reservations.bind_exchange_order(
        intent.intent_id,
        "202",
        acknowledged_at=observed_at + timedelta(seconds=1),
    )

    # This read began before the ACK and has no row for it. A later callback
    # invalidates trust while the local reservation remains the exposure floor.
    state.apply_authoritative(
        snapshot,
        mark_trusted=False,
        observed_monotonic_ns=1_000_000_001,
    )
    state.mark_untrusted(AccountTrustTransition.UNTRUSTED_RESYNC_ACTIVITY)
    portfolio = state.runtime_portfolio()

    assert portfolio.trust_grade is AccountTrustGrade.PROXY
    assert len(portfolio.orders) == 1
    assert portfolio.orders[0].reserved_exposure == 10
    assert portfolio.worst_case_inventory_bounds(EXCHANGE, TOURNAMENT) == (0.0, 10.0)
