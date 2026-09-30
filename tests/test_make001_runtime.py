from __future__ import annotations

import asyncio
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest

from predictions_cup.config import AppSettings
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
from predictions_cup.execution.sinks import ExecutionPlan
from predictions_cup.maker.adapters import LiveMakerExecutionAdapter
from predictions_cup.maker.contracts import (
    DesiredQuote,
    GateDecision,
    GateMode,
    MakerDecision,
    MakerMarketSnapshot,
    MakerTrace,
    QuoteSide,
)
from predictions_cup.maker.coordinator import (
    MakerCoordinator,
    MakerCycleResult,
    MakerStateChange,
)
from predictions_cup.maker.lifecycle import QuoteRegistry
from predictions_cup.maker.recovery import (
    maker_unresolved_envelopes,
    reconcile_maker_quote_registry,
)
from predictions_cup.maker.runtime_loop import MakerRuntimeLoop
from predictions_cup.maker.service import MakerService
from predictions_cup.maker.sources import MakerSourceBridge
from predictions_cup.risk.core import RiskDecision
from predictions_cup.runtime.models import OrderAction, OutcomeSide
from predictions_cup.runtime.telemetry import HotPathTelemetry
from predictions_cup.shadow.live import LiveShadowRuntime
from predictions_cup.sig.account_reconciliation import AccountAuthoritativeSnapshot
from predictions_cup.sig.trading_dto import OrderReadDto


def _plan() -> ExecutionPlan:
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
        unrelated = quotes.state("99").bid
        assert unrelated is not None
        assert unrelated.exchange_order_id == 999
    finally:
        journal.close()


def test_reconciled_journal_history_still_tracks_resting_maker_quote(
    tmp_path: Path,
) -> None:
    journal = _journal_with_open_maker(tmp_path / "reconciled-history.sqlite3")
    quotes = QuoteRegistry()
    try:
        journal.mark_state(
            "maker-op-1",
            LifecycleState.RECONCILING,
            230,
        )
        journal.mark_state(
            "maker-op-1",
            LifecycleState.RECONCILED,
            240,
        )
        assert maker_unresolved_envelopes(journal) == ()

        # A later process restart must still recover the currently open SIG
        # order from durable MAKE attribution even though BUILD-009 execution
        # recovery correctly considers the operation reconciled.
        reconcile_maker_quote_registry(
            journal=journal,
            authoritative=_authoritative(),
            quotes=quotes,
            observed_monotonic_ns=300,
        )
        active = quotes.state("36").bid
        assert active is not None
        assert active.exchange_order_id == 91

        reconcile_maker_quote_registry(
            journal=journal,
            authoritative=_authoritative(open_order=False),
            quotes=quotes,
            observed_monotonic_ns=400,
        )
        assert quotes.state("36").bid is None
    finally:
        journal.close()


class _AckingLiveSink:
    def __init__(self, journal: ExecutionJournal) -> None:
        self.journal = journal

    async def dispatch(self, plan: ExecutionPlan) -> ExecutionEvent:
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
        exchange_ids: frozenset[str] | set[str] | tuple[str, ...],
        **kwargs: object,
    ) -> dict[str, MakerMarketSnapshot]:
        del kwargs
        return {
            exchange_id: cast(MakerMarketSnapshot, object())
            for exchange_id in exchange_ids
        }


def _runtime_decision(
    exchange_id: str,
    *,
    deadline_ns: int | None,
) -> MakerDecision:
    desired = DesiredQuote(
        exchange_id=exchange_id,
        market_id=f"market-{exchange_id}",
        tournament_id="t1",
        bid_ticks=99,
        ask_ticks=101,
        bid_size=1,
        ask_size=1,
    )
    gate = GateDecision(GateMode.NORMAL, "fixture")
    return MakerDecision(
        desired=desired,
        gate=gate,
        trace=MakerTrace(
            strategy_id="fixture",
            strategy_version="v1",
            fv_source="fixture",
            fv_version="v1",
            raw_fv=0.5,
            predictive_shift=0.0,
            adjusted_fv=0.5,
            uncertainty=0.0,
            confidence=1.0,
            update_hazard=0.0,
            adverse_selection=0.0,
            signed_inventory=0.0,
            reservation_price=0.5,
            half_spread=0.005,
            desired_bid_ticks=99,
            desired_ask_ticks=101,
            desired_bid_size=1,
            desired_ask_size=1,
            gate_mode=GateMode.NORMAL,
            reason="fixture",
            decision_monotonic_ns=100,
        ),
        next_recheck_monotonic_ns=deadline_ns,
    )


class _Coordinator:
    def __init__(self) -> None:
        self.calls: list[frozenset[str]] = []
        self.event_ids: list[str] = []
        self.snapshot_object_ids: list[dict[str, int]] = []
        self.killed = False
        self.deadline_ns: int | None = None

    async def on_state_change(
        self,
        change: MakerStateChange,
        snapshots: Mapping[str, MakerMarketSnapshot],
    ) -> MakerCycleResult:
        self.event_ids.append(change.event_id)
        self.calls.append(frozenset(snapshots))
        self.snapshot_object_ids.append({key: id(value) for key, value in snapshots.items()})
        decisions = tuple(
            _runtime_decision(exchange_id, deadline_ns=self.deadline_ns)
            for exchange_id in sorted(snapshots)
        )
        return MakerCycleResult(decisions, (), (), ())

    def activate_kill_switch(self, reason: str) -> None:
        assert reason
        self.killed = True


class _MutableClock:
    def __init__(self, value: int) -> None:
        self.value = value

    def __call__(self) -> int:
        return self.value


def test_runtime_loop_coalesces_exchange_and_token_updates() -> None:
    bridge = _Bridge()
    coordinator = _Coordinator()
    telemetry = HotPathTelemetry()
    clock = _MutableClock(100)
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


def test_runtime_snapshot_observer_receives_exact_decision_boundary() -> None:
    bridge = _Bridge()
    coordinator = _Coordinator()
    observed: list[tuple[str, datetime, dict[str, int]]] = []

    def snapshot_observer(
        change: MakerStateChange,
        observed_at: datetime,
        snapshots: Mapping[str, MakerMarketSnapshot],
    ) -> None:
        observed.append(
            (
                change.event_id,
                observed_at,
                {key: id(value) for key, value in snapshots.items()},
            )
        )

    wall_now = datetime(2026, 9, 29, 14, 0, tzinfo=UTC)
    runtime = MakerRuntimeLoop(
        bridge=cast(MakerSourceBridge, bridge),
        coordinator=cast(MakerCoordinator, coordinator),
        polymarket_feed_trusted=lambda: True,
        snapshot_observer=snapshot_observer,
        wall_clock=lambda: wall_now,
        mono_clock=lambda: 100,
        runtime_session_id="shadow-boundary",
    )

    runtime.notify_sig({"36"}, observed_monotonic_ns=90)
    asyncio.run(runtime.drain_once())

    assert len(observed) == 1
    event_id, observed_at, object_ids = observed[0]
    assert event_id == "make-runtime-shadow-boundary-1"
    assert observed_at == wall_now
    assert object_ids == coordinator.snapshot_object_ids[0]


def test_runtime_deadline_rechecks_exchange_without_new_source_event() -> None:
    bridge = _Bridge()
    coordinator = _Coordinator()
    coordinator.deadline_ns = 150
    clock = _MutableClock(100)
    telemetry = HotPathTelemetry()
    runtime = MakerRuntimeLoop(
        bridge=cast(MakerSourceBridge, bridge),
        coordinator=cast(MakerCoordinator, coordinator),
        polymarket_feed_trusted=lambda: True,
        telemetry=telemetry,
        wall_clock=lambda: datetime(2026, 9, 29, 14, 0, tzinfo=UTC),
        mono_clock=clock,
        runtime_session_id="deadline",
    )

    runtime.notify_sig({"36"}, observed_monotonic_ns=90)
    asyncio.run(runtime.drain_once())
    assert coordinator.calls == [frozenset({"36"})]

    coordinator.deadline_ns = None
    clock.value = 149
    assert asyncio.run(runtime.drain_once()) is None
    assert coordinator.calls == [frozenset({"36"})]

    clock.value = 150
    asyncio.run(runtime.drain_once())
    assert coordinator.calls == [
        frozenset({"36"}),
        frozenset({"36"}),
    ]
    assert (
        telemetry.snapshot().counters["maker_freshness_deadline_triggers"]
        == 1
    )


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

def test_runtime_session_namespace_prevents_operation_identity_reuse_after_restart() -> None:
    bridge = _Bridge()
    first = _Coordinator()
    second = _Coordinator()
    runtime_a = MakerRuntimeLoop(
        bridge=cast(MakerSourceBridge, bridge),
        coordinator=cast(MakerCoordinator, first),
        polymarket_feed_trusted=lambda: True,
        wall_clock=lambda: datetime(2026, 9, 29, 14, 0, tzinfo=UTC),
        mono_clock=lambda: 100,
        runtime_session_id="session-a",
    )
    runtime_b = MakerRuntimeLoop(
        bridge=cast(MakerSourceBridge, bridge),
        coordinator=cast(MakerCoordinator, second),
        polymarket_feed_trusted=lambda: True,
        wall_clock=lambda: datetime(2026, 9, 29, 14, 0, tzinfo=UTC),
        mono_clock=lambda: 100,
        runtime_session_id="session-b",
    )

    runtime_a.notify_sig({"36"}, observed_monotonic_ns=90)
    runtime_b.notify_sig({"36"}, observed_monotonic_ns=90)
    asyncio.run(runtime_a.drain_once())
    asyncio.run(runtime_b.drain_once())

    assert first.event_ids == ["make-runtime-session-a-1"]
    assert second.event_ids == ["make-runtime-session-b-1"]
    assert first.event_ids[0] != second.event_ids[0]

def test_runtime_paces_global_work_and_requeues_remaining_exchanges() -> None:
    bridge = _Bridge()
    coordinator = _Coordinator()
    runtime = MakerRuntimeLoop(
        bridge=cast(MakerSourceBridge, bridge),
        coordinator=cast(MakerCoordinator, coordinator),
        polymarket_feed_trusted=lambda: True,
        wall_clock=lambda: datetime(2026, 9, 29, 14, 0, tzinfo=UTC),
        mono_clock=lambda: 100,
        runtime_session_id="paced",
        max_exchanges_per_cycle=1,
    )

    runtime.notify_account(observed_monotonic_ns=90)
    asyncio.run(runtime.drain_once())
    asyncio.run(runtime.drain_once())

    assert coordinator.calls == [
        frozenset({"36"}),
        frozenset({"37"}),
    ]
    assert coordinator.event_ids == [
        "make-runtime-paced-1",
        "make-runtime-paced-2",
    ]

class _KillDrainRuntime:
    def __init__(self) -> None:
        self.calls = 0

    async def drain_once(self) -> MakerCycleResult | None:
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("simulated uncertain cancel")
        if self.calls == 2:
            return MakerCycleResult((), (), (), ())
        return None


def test_service_kill_drain_continues_after_one_failed_cancel_cycle() -> None:
    service = MakerService(
        AppSettings(maker_enabled=True),
        explicit_live_invocation=False,
    )
    runtime = _KillDrainRuntime()

    asyncio.run(
        service._best_effort_kill_drain(
            cast(MakerRuntimeLoop, runtime),
        )
    )

    assert runtime.calls == 3

class _PmNotifyRuntime:
    def __init__(self) -> None:
        self.tokens: list[frozenset[str]] = []

    def notify_polymarket(
        self,
        token_ids: set[str] | frozenset[str],
        *,
        observed_monotonic_ns: int | None = None,
    ) -> None:
        del observed_monotonic_ns
        self.tokens.append(frozenset(token_ids))


class _Pm005FObserver:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def observe_polymarket_bbo(
        self,
        *,
        token_id: str,
        observed_at: datetime,
        observed_monotonic_ns: int,
        best_bid: float | None,
        best_ask: float | None,
        source_version: str,
        trusted: bool,
    ) -> bool:
        self.calls.append(
            {
                "token_id": token_id,
                "observed_at": observed_at,
                "observed_monotonic_ns": observed_monotonic_ns,
                "best_bid": best_bid,
                "best_ask": best_ask,
                "source_version": source_version,
                "trusted": trusted,
            }
        )
        return True


def test_maker_pm_handler_tolerates_unmapped_sibling_delta() -> None:
    service = MakerService(
        AppSettings(maker_enabled=True),
        explicit_live_invocation=False,
    )
    service._pm_token_ids = ("required-token",)
    observed = datetime(2026, 9, 29, 17, 0, tzinfo=UTC)
    service.pm_books.apply_full_snapshot(
        {
            "event_type": "book",
            "market": "0xmarket",
            "asset_id": "required-token",
            "timestamp": "1782753357257",
            "bids": [{"price": "0.49", "size": "10"}],
            "asks": [{"price": "0.51", "size": "10"}],
        },
        observed,
    )
    runtime = _PmNotifyRuntime()
    payload = {
        "event_type": "price_change",
        "market": "0xmarket",
        "timestamp": "1782753358257",
        "price_changes": [
            {
                "asset_id": "required-token",
                "price": "0.495",
                "size": "9",
                "side": "BUY",
            },
            {
                "asset_id": "unmapped-sibling",
                "price": "0.505",
                "size": "11",
                "side": "SELL",
            },
        ],
    }

    asyncio.run(
        service._handle_pm_message(
            payload,
            observed,
            cast(MakerRuntimeLoop, runtime),
        )
    )

    assert runtime.tokens == [frozenset({"required-token"})]
    assert service.pm_health.book_uninitialized_delta_count == 1
    snapshot = service.pm_books.snapshot("required-token", 1)
    assert snapshot is not None
    assert str(snapshot.best_bid) == "0.495"


def test_maker_pm_handler_groups_conflicting_equal_time_bbo_for_005f() -> None:
    service = MakerService(
        AppSettings(maker_enabled=True),
        explicit_live_invocation=False,
    )
    service._pm_token_ids = ("required-token",)
    observed = datetime(2026, 9, 29, 17, 0, tzinfo=UTC)
    service.pm_books.apply_full_snapshot(
        {
            "event_type": "book",
            "market": "0xmarket",
            "asset_id": "required-token",
            "timestamp": "1782753357257",
            "bids": [{"price": "0.49", "size": "10"}],
            "asks": [{"price": "0.51", "size": "10"}],
        },
        observed,
    )
    runtime = _PmNotifyRuntime()
    observer = _Pm005FObserver()
    payload = {
        "event_type": "price_change",
        "market": "0xmarket",
        "timestamp": "1782753358257",
        "price_changes": [
            {
                "asset_id": "required-token",
                "price": "0.495",
                "size": "9",
                "side": "BUY",
            },
            {
                "asset_id": "required-token",
                "price": "0.505",
                "size": "11",
                "side": "SELL",
            },
        ],
    }

    asyncio.run(
        service._handle_pm_message(
            payload,
            observed,
            cast(MakerRuntimeLoop, runtime),
            cast(LiveShadowRuntime, observer),
        )
    )

    assert runtime.tokens == [frozenset({"required-token"})]
    assert len(observer.calls) == 1
    call = observer.calls[0]
    assert call["token_id"] == "required-token"
    assert call["best_bid"] is None
    assert call["best_ask"] is None
    assert call["trusted"] is False


def test_maker_pm_handler_reconnects_when_required_token_is_unseeded() -> None:
    service = MakerService(
        AppSettings(maker_enabled=True),
        explicit_live_invocation=False,
    )
    service._pm_token_ids = ("required-token",)
    observed = datetime(2026, 9, 29, 17, 0, tzinfo=UTC)
    payload = {
        "event_type": "price_change",
        "market": "0xmarket",
        "timestamp": "1782753358257",
        "price_changes": [
            {
                "asset_id": "required-token",
                "price": "0.495",
                "size": "9",
                "side": "BUY",
            },
        ],
    }

    with pytest.raises(RuntimeError, match="required mapped token"):
        asyncio.run(
            service._handle_pm_message(
                payload,
                observed,
                cast(MakerRuntimeLoop, _PmNotifyRuntime()),
            )
        )

