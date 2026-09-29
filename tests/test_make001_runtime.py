from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from predictions_cup.execution.journal import ExecutionJournal
from predictions_cup.execution.live import SigLiveSink
from predictions_cup.execution.models import (
    ExecutionEvent,
    ExecutionMode,
    LifecycleState,
    OperationKind,
    RuntimeOrderIntent,
)
from predictions_cup.execution.planner import build_execution_plan
from predictions_cup.maker.adapters import LiveMakerExecutionAdapter
from predictions_cup.maker.contracts import MakerMarketSnapshot, QuoteSide
from predictions_cup.maker.coordinator import (
    MakerCoordinator,
    MakerCycleResult,
)
from predictions_cup.maker.lifecycle import QuoteRegistry
from predictions_cup.maker.recovery import (
    maker_unresolved_envelopes,
    reconcile_maker_quote_registry,
)
from predictions_cup.maker.runtime_loop import MakerRuntimeLoop
from predictions_cup.maker.sources import MakerSourceBridge
from predictions_cup.risk.core import RiskDecision
from predictions_cup.runtime.models import OrderAction, OutcomeSide
from predictions_cup.runtime.telemetry import HotPathTelemetry
from predictions_cup.sig.account_reconciliation import AccountAuthoritativeSnapshot
from predictions_cup.sig.trading_dto import OrderReadDto


def _plan():
    intent = RuntimeOrderIntent(
        intent_id="make-direct-pm:123:0",
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
    decision = RiskDecision(
        approved=True,
        reason="approved",
        execution_mode=ExecutionMode.LIVE,
        operation_kind=OperationKind.SINGLE_PLACEMENT,
        intents=(intent,),
        strategy_family="MAKE",
        strategy_id="make-direct-pm",
        signal_value=0.005,
        fair_value=0.5,
        decision_observation_ns=123,
    )
    return build_execution_plan(
        decision,
        logical_operation_id="maker-op-1",
        created_monotonic_ns=200,
    )


def _open_order(order_id: int = 91) -> OrderReadDto:
    return OrderReadDto.model_validate(
        {
            "id": order_id,
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


def _authoritative(*, open_order: bool = True) -> AccountAuthoritativeSnapshot:
    return AccountAuthoritativeSnapshot(
        tournament_id="t1",
        tournament_slug="cup",
        open_orders=(_open_order(),) if open_order else (),
        positions=(),
        observed_at=datetime(2026, 9, 29, 14, 0, tzinfo=UTC),
    )


def _journal_with_open_maker(path: Path) -> ExecutionJournal:
    journal = ExecutionJournal(path)
    plan = _plan()
    journal.record_before_dispatch(
        plan.envelope,
        plan.intents,
        audit=plan.audit,
        submitted_monotonic_ns=210,
    )
    journal.record_event(
        logical_operation_id=plan.envelope.logical_operation_id,
        tournament_id="t1",
        logical_intent_id=plan.intents[0].intent_id,
        event_type="ACK",
        observed_monotonic_ns=220,
        exchange_id="36",
        exchange_order_id="91",
        terminal_status=LifecycleState.OPEN.value,
    )
    journal.mark_state(
        plan.envelope.logical_operation_id,
        LifecycleState.OPEN,
        220,
    )
    return journal


def test_restart_rebuilds_only_journal_proven_maker_quote(tmp_path: Path) -> None:
    journal = _journal_with_open_maker(tmp_path / "journal.sqlite3")
    quotes = QuoteRegistry()
    quotes.apply_authoritative(
        exchange_id="99",
        side=QuoteSide.BID,
        price_ticks=80,
        size=1,
        remaining_size=1,
        logical_operation_id="other-strategy-op",
        exchange_order_id=999,
        lifecycle_state=LifecycleState.OPEN,
        observed_monotonic_ns=1,
    )
    try:
        captured = maker_unresolved_envelopes(journal)
        assert len(captured) == 1

        reconcile_maker_quote_registry(
            journal=journal,
            authoritative=_authoritative(),
            quotes=quotes,
            observed_monotonic_ns=300,
            envelopes=captured,
        )
        state = quotes.state("36")
        assert state.bid is not None
        assert state.bid.exchange_order_id == 91
        assert state.bid.price_ticks == 99
        assert state.bid.lifecycle_state is LifecycleState.OPEN

        # Authoritative absence clears the MAKE quote but leaves unrelated
        # strategy state untouched.
        reconcile_maker_quote_registry(
            journal=journal,
            authoritative=_authoritative(open_order=False),
            quotes=quotes,
            observed_monotonic_ns=400,
            envelopes=captured,
        )
        assert quotes.state("36").bid is None
        assert quotes.state("99").bid is not None
        assert quotes.state("99").bid.exchange_order_id == 999
    finally:
        journal.close()


class _AckingLiveSink:
    def __init__(self, journal: ExecutionJournal) -> None:
        self.journal = journal

    async def dispatch(self, plan) -> ExecutionEvent:
        self.journal.record_before_dispatch(
            plan.envelope,
            plan.intents,
            audit=plan.audit,
            submitted_monotonic_ns=210,
        )
        self.journal.record_event(
            logical_operation_id=plan.envelope.logical_operation_id,
            tournament_id="t1",
            logical_intent_id=plan.intents[0].intent_id,
            event_type="ACK",
            observed_monotonic_ns=220,
            exchange_id="36",
            exchange_order_id="91",
            terminal_status=LifecycleState.OPEN.value,
        )
        self.journal.mark_state(
            plan.envelope.logical_operation_id,
            LifecycleState.OPEN,
            220,
        )
        return ExecutionEvent(
            logical_operation_id=plan.envelope.logical_operation_id,
            state=LifecycleState.OPEN,
            observed_monotonic_ns=220,
            simulated=False,
            detail="fixture",
        )


def test_live_adapter_resolves_ack_order_identity_into_quote_registry(
    tmp_path: Path,
) -> None:
    journal = ExecutionJournal(tmp_path / "adapter.sqlite3")
    quotes = QuoteRegistry()
    sink = _AckingLiveSink(journal)
    adapter = LiveMakerExecutionAdapter(
        cast(SigLiveSink, sink),
        journal=journal,
        quotes=quotes,
    )
    try:
        event = asyncio.run(
            adapter.place(
                _plan(),
                cast(MakerMarketSnapshot, object()),
            )
        )
        assert event.state is LifecycleState.OPEN
        active = quotes.state("36").bid
        assert active is not None
        assert active.exchange_order_id == 91
        assert active.lifecycle_state is LifecycleState.OPEN
        assert active.price_ticks == 99
    finally:
        journal.close()


class _Bridge:
    tradeable_exchange_ids = frozenset({"36", "37"})

    def sig_exchanges_for_polymarket_token(self, token_id: str) -> frozenset[str]:
        if token_id == "token-36":
            return frozenset({"36"})
        return frozenset()

    def build_many(
        self,
        exchange_ids,
        **kwargs,
    ) -> dict[str, MakerMarketSnapshot]:
        del kwargs
        return {
            exchange_id: cast(MakerMarketSnapshot, object())
            for exchange_id in exchange_ids
        }


class _Coordinator:
    def __init__(self) -> None:
        self.calls: list[frozenset[str]] = []
        self.killed = False

    async def on_state_change(self, change, snapshots) -> MakerCycleResult:
        del change
        self.calls.append(frozenset(snapshots))
        return MakerCycleResult((), (), (), ())

    def activate_kill_switch(self, reason: str) -> None:
        assert reason
        self.killed = True


def test_runtime_loop_coalesces_exchange_and_token_updates() -> None:
    bridge = _Bridge()
    coordinator = _Coordinator()
    telemetry = HotPathTelemetry()
    clock = iter((100, 110, 120, 130, 140, 150, 160)).__next__
    runtime = MakerRuntimeLoop(
        bridge=cast(MakerSourceBridge, bridge),
        coordinator=cast(MakerCoordinator, coordinator),
        polymarket_feed_trusted=lambda: True,
        telemetry=telemetry,
        wall_clock=lambda: datetime(2026, 9, 29, 14, 0, tzinfo=UTC),
        mono_clock=clock,
    )

    runtime.notify_sig({"36"}, observed_monotonic_ns=90)
    runtime.notify_sig({"36"}, observed_monotonic_ns=95)
    runtime.notify_polymarket({"token-36"}, observed_monotonic_ns=92)
    asyncio.run(runtime.drain_once())

    assert coordinator.calls == [frozenset({"36"})]
    snapshot = telemetry.snapshot()
    assert snapshot.counters["maker_cycles"] == 1
    assert snapshot.counters["maker_exchange_evaluations"] == 1

    runtime.notify_account(observed_monotonic_ns=120)
    asyncio.run(runtime.drain_once())
    assert coordinator.calls[-1] == frozenset({"36", "37"})


def test_runtime_kill_switch_requests_global_recheck() -> None:
    bridge = _Bridge()
    coordinator = _Coordinator()
    runtime = MakerRuntimeLoop(
        bridge=cast(MakerSourceBridge, bridge),
        coordinator=cast(MakerCoordinator, coordinator),
        polymarket_feed_trusted=lambda: True,
        wall_clock=lambda: datetime(2026, 9, 29, 14, 0, tzinfo=UTC),
        mono_clock=lambda: 100,
    )

    runtime.activate_kill_switch("operator")
    asyncio.run(runtime.drain_once())

    assert coordinator.killed is True
    assert coordinator.calls == [frozenset({"36", "37"})]
