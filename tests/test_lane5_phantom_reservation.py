"""LANE-5 independent PR #202 P1 integration regression.

Exercises real MAKE coordinator, BUILD-009 journal and reservations, LIVE sink,
and fake trading adapter. No SIG credentials, network, production risk or orders.

The cross-component fresh-cutoff expectation is strict-XFAIL until #202 is fixed;
after author correction remove xfail and demand a green non-xfail regression.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from pathlib import Path
from typing import cast

import pytest
from pydantic import SecretStr

from predictions_cup.config import AppSettings
from predictions_cup.execution.interlocks import LiveExecutionPermit, assert_live_interlocks
from predictions_cup.execution.journal import ExecutionJournal
from predictions_cup.execution.live import SigLiveSink
from predictions_cup.execution.models import ExecutionMode, LifecycleState
from predictions_cup.execution.reservations import ExecutionReservationBook
from predictions_cup.maker.adapters import LiveMakerExecutionAdapter
from predictions_cup.maker.contracts import (
    DesiredQuote,
    ExternalQuoteState,
    GateDecision,
    GateMode,
    MakerDecision,
    MakerMarketSnapshot,
    MakerTrace,
)
from predictions_cup.maker.coordinator import MakerCoordinator, MakerStateChange
from predictions_cup.maker.engine import MakerEngine
from predictions_cup.maker.lifecycle import QuoteLifecycleManager, QuoteRegistry
from predictions_cup.risk.core import RiskContext, RiskLimits
from predictions_cup.runtime.models import (
    RuntimeMarket,
    RuntimePortfolio,
    RuntimeSnapshot,
)
from predictions_cup.sig.competition_deadline import LAST_ORDER_SUBMISSION_UTC
from predictions_cup.sig.trading_client import SigTradingClient


class OneBidEngine:
    strategy_id = "lane5-fixture"

    def quote(self, snapshot: MakerMarketSnapshot) -> MakerDecision:
        trace = MakerTrace(
            strategy_id=self.strategy_id,
            strategy_version="lane5-test",
            fv_source="immutable-test",
            fv_version="1",
            raw_fv=0.6,
            predictive_shift=0.0,
            adjusted_fv=0.6,
            uncertainty=0.0,
            confidence=1.0,
            update_hazard=0.0,
            adverse_selection=0.0,
            signed_inventory=0.0,
            reservation_price=0.6,
            half_spread=0.01,
            desired_bid_ticks=100,
            desired_ask_ticks=None,
            desired_bid_size=1,
            desired_ask_size=0,
            gate_mode=GateMode.NORMAL,
            reason="test",
            decision_monotonic_ns=snapshot.now_monotonic_ns,
        )
        return MakerDecision(
            desired=DesiredQuote(
                exchange_id=snapshot.exchange_id,
                market_id=snapshot.market_id,
                tournament_id=snapshot.tournament_id,
                bid_ticks=100, ask_ticks=None, bid_size=1, ask_size=0,
            ),
            gate=GateDecision(mode=GateMode.NORMAL, reason="fixture"),
            trace=trace,
        )


class NoWireTrading:
    def __init__(self) -> None:
        self.calls = 0

    async def place_order_payload(self, payload: str) -> object:
        self.calls += 1
        raise AssertionError(f"Unreachable SIG placement: {payload}")

    async def place_batch_payload(self, payload: str) -> object:
        self.calls += 1
        raise AssertionError(f"Unreachable SIG batch: {payload}")

    async def place_multi_leg_payload(self, payload: str) -> object:
        self.calls += 1
        raise AssertionError(f"Unreachable SIG multi-leg: {payload}")


def permit() -> LiveExecutionPermit:
    settings = AppSettings(
        sig_trade_credential=SecretStr("fixture-not-real"),
        tournament_id="t1", tournament_slug="cup",
        trading_enabled=True, execution_mode="LIVE",
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
    return assert_live_interlocks(
        settings, explicit_live_invocation=True,
        account_trusted=True, capital_state_ready=True,
    )


def snapshot() -> MakerMarketSnapshot:
    now_ns = 1_000_000_000
    runtime = RuntimeSnapshot(
        markets=(RuntimeMarket(
            market_id="m1", status="open",
            exchange_ids=("36",), tournament_id="t1",
            mapping_accepted=True, tradeable=True,
        ),),
        books=(),
        portfolio=RuntimePortfolio(account_trusted=True),
        observation_monotonic_ns=now_ns,
    )
    return MakerMarketSnapshot(
        runtime=runtime,
        exchange_id="36", market_id="m1", tournament_id="t1",
        now_monotonic_ns=now_ns,
        sig_bbo_observed_ns=now_ns, sig_bbo_trusted=True,
        sig_depth_observed_ns=now_ns, sig_depth_trusted=True,
        account_observed_ns=now_ns, inventory_observed_ns=now_ns,
        external_quotes={"fixture": ExternalQuoteState(
            token_id="fixture", best_bid=0.5, best_ask=0.51,
            observed_monotonic_ns=now_ns, trusted=True, source_version="1",
        )},
    )


@pytest.mark.parametrize(
    "blocked_at",
    [
        LAST_ORDER_SUBMISSION_UTC,
        LAST_ORDER_SUBMISSION_UTC + timedelta(minutes=1),
        datetime(2026, 11, 4, 16, 59),
    ],
)
def test_prejournal_cutoff_must_clear_coordinator_quote_and_reservation(
    tmp_path: Path, blocked_at: datetime,
) -> None:
    journal = ExecutionJournal(tmp_path / "lane5-cutoff.sqlite3")
    reservations = ExecutionReservationBook()
    quotes = QuoteRegistry()
    no_wire = NoWireTrading()
    live = SigLiveSink(
        client=cast(SigTradingClient, no_wire),
        journal=journal,
        permit=permit(),
        reservations=reservations,
        wall_clock=lambda: blocked_at,
    )
    adapter = LiveMakerExecutionAdapter(live, journal=journal, quotes=quotes)
    coordinator = MakerCoordinator(
        engine=cast(MakerEngine, OneBidEngine()),
        lifecycle=QuoteLifecycleManager(),
        quote_registry=quotes,
        risk_context=RiskContext(
            mode=ExecutionMode.LIVE, kill_switch=False,
            limits=RiskLimits(
                max_order_size=10,
                max_gross_exposure=100.0,
                max_per_market_exposure=100.0,
                max_open_order_exposure=100.0,
                max_concurrent_open_orders=10,
            ),
            max_state_age_ns=1_000_000_000,
        ),
        placement_dispatch=adapter.place, cancel_dispatch=adapter.cancel,
        reservations=reservations,
    )
    try:
        result = asyncio.run(coordinator.on_state_change(
            MakerStateChange(
                event_id="lane5-p1",
                observed_monotonic_ns=1_000_000_000,
                exchange_ids=frozenset({"36"}),
            ),
            {"36": snapshot()},
        ))
        assert len(result.risk_decisions) == 1
        assert result.risk_decisions[0].approved
        assert len(result.execution_events) == 1
        assert result.execution_events[0].state is LifecycleState.REJECTED
        assert no_wire.calls == 0
        assert journal.envelopes() == ()
        assert reservations.reserved_orders() == ()
        assert not reservations.intent_ids()
        assert quotes.state("36").all_quotes() == ()
    finally:
        journal.close()
