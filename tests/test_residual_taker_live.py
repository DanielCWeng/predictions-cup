from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path

from predictions_cup.execution.models import (
    ExecutionEnvelope,
    ExecutionEvent,
    ExecutionMode,
    LifecycleState,
)
from predictions_cup.execution.reservations import ExecutionReservationBook
from predictions_cup.execution.sinks import ExecutionPlan
from predictions_cup.maker.contracts import (
    ExternalQuoteState,
    MakerMarketSnapshot,
    QuoteSide,
)
from predictions_cup.maker.coordinator import MakerStateChange
from predictions_cup.maker.lifecycle import QuoteRegistry
from predictions_cup.maker.residual_live import ResidualTakerLiveCoordinator
from predictions_cup.maker.residual_taker import STRATEGY_ID, resolve_residual_universe
from predictions_cup.maker.safety import MakerKillSwitch
from predictions_cup.mapping.crosswalk import load_document
from predictions_cup.risk.core import RiskContext, RiskLimits
from predictions_cup.runtime.models import (
    AccountTrustGrade,
    OrderAction,
    OutcomeSide,
    RuntimeBook,
    RuntimeLevel,
    RuntimeMarket,
    RuntimePortfolio,
    RuntimePosition,
    RuntimeSnapshot,
)
from predictions_cup.sig.errors import SigExecutionUncertainError

MAPPING = load_document(Path("data/mappings/sig_polymarket_2026.json"))
RECORD = next(
    record
    for record in MAPPING.records
    if record.sig_exchange_id in resolve_residual_universe(MAPPING, "ALL_EXACT", "")
)
EXCHANGE = RECORD.sig_exchange_id
MARKET = RECORD.sig_market_id
TOURNAMENT = MAPPING.tournament_id
assert RECORD.direct_polymarket is not None
TOKEN = RECORD.direct_polymarket.mapped_token_id
NOW = 10_000_000_000


@dataclass
class _JournalEvent:
    event_type: str
    exchange_order_id: str | None
    terminal_status: str | None


class _Journal:
    def __init__(self) -> None:
        self.by_operation: dict[str, list[_JournalEvent]] = {}

    def events(self, logical_operation_id: str) -> list[_JournalEvent]:
        return self.by_operation.get(logical_operation_id, [])


def _snapshot(
    *,
    bid: int = 139,
    ask: int = 140,
    ask_qty: float = 30.0,
    pm_bid: float = 0.745,
    pm_ask: float = 0.755,
) -> MakerMarketSnapshot:
    runtime = RuntimeSnapshot(
        markets=(
            RuntimeMarket(
                market_id=MARKET,
                status="open",
                exchange_ids=(EXCHANGE,),
                tournament_id=TOURNAMENT,
                mapping_accepted=True,
                tradeable=True,
            ),
        ),
        books=(
            RuntimeBook(
                exchange_id=EXCHANGE,
                market_id=MARKET,
                tournament_id=TOURNAMENT,
                bids=(RuntimeLevel(bid, 100.0),),
                asks=(RuntimeLevel(ask, ask_qty),),
                trusted_depth=True,
                observed_monotonic_ns=NOW,
            ),
        ),
        portfolio=RuntimePortfolio(positions=(), orders=(), account_trusted=True),
        observation_monotonic_ns=NOW,
    )
    return MakerMarketSnapshot(
        runtime=runtime,
        exchange_id=EXCHANGE,
        market_id=MARKET,
        tournament_id=TOURNAMENT,
        now_monotonic_ns=NOW,
        sig_bbo_observed_ns=NOW,
        sig_bbo_trusted=True,
        sig_depth_observed_ns=NOW,
        sig_depth_trusted=True,
        account_observed_ns=NOW,
        inventory_observed_ns=NOW,
        external_quotes={
            TOKEN: ExternalQuoteState(
                token_id=TOKEN,
                best_bid=pm_bid,
                best_ask=pm_ask,
                observed_monotonic_ns=NOW,
                trusted=True,
                source_version="test",
                best_bid_size=100.0,
                best_ask_size=100.0,
            )
        },
    )


def _risk(*, kill_switch: bool = False) -> RiskContext:
    return RiskContext(
        mode=ExecutionMode.LIVE,
        kill_switch=kill_switch,
        limits=RiskLimits(
            max_order_size=200,
            max_gross_exposure=10_000.0,
            max_per_market_exposure=1_000.0,
            max_open_order_exposure=10_000.0,
            max_concurrent_open_orders=100,
        ),
        max_state_age_ns=60_000_000_000,
    )


class _Harness:
    def __init__(
        self,
        *,
        risk: RiskContext | None = None,
        resting: bool = True,
        cancel_failures: int = 0,
        allow_bbo_proxy: bool = False,
    ) -> None:
        self.journal = _Journal()
        self.quotes = QuoteRegistry()
        self.kill_switch = MakerKillSwitch()
        self.reservations = ExecutionReservationBook()
        self.plans: list[ExecutionPlan] = []
        self.cancels: list[ExecutionEnvelope] = []
        self._resting = resting
        self._cancel_failures = cancel_failures
        self.coordinator = ResidualTakerLiveCoordinator(
            mapping=MAPPING,
            tracked_exchange_ids=frozenset({EXCHANGE}),
            size=50,
            max_pm_book_age_ns=35_000_000_000,
            risk_context=risk or _risk(),
            reservations=self.reservations,
            journal=self.journal,  # type: ignore[arg-type]
            quotes=self.quotes,
            dispatch=self._dispatch,
            cancel=self._cancel,
            kill_switch=self.kill_switch,
            allow_bbo_proxy=allow_bbo_proxy,
            max_sig_bbo_age_ns=1_000_000_000,
        )

    async def _dispatch(self, plan: ExecutionPlan) -> ExecutionEvent:
        self.plans.append(plan)
        state = LifecycleState.OPEN if self._resting else LifecycleState.FILLED
        self.journal.by_operation[plan.envelope.logical_operation_id] = [
            _JournalEvent("ACK", "4242", state.value)
        ]
        return ExecutionEvent(
            logical_operation_id=plan.envelope.logical_operation_id,
            state=state,
            observed_monotonic_ns=NOW + 1,
            simulated=False,
        )

    async def _cancel(self, envelope: ExecutionEnvelope) -> ExecutionEvent:
        self.cancels.append(envelope)
        if self._cancel_failures > 0:
            self._cancel_failures -= 1
            raise SigExecutionUncertainError(status_code=None, code=None, safe_message="timeout")
        return ExecutionEvent(
            logical_operation_id=envelope.logical_operation_id,
            state=LifecycleState.CANCELLED,
            observed_monotonic_ns=NOW + 2,
            simulated=False,
        )

    def run(self, snapshot: MakerMarketSnapshot, sequence: int = 1) -> None:
        change = MakerStateChange(
            event_id=f"evt-{sequence}",
            observed_monotonic_ns=NOW,
            exchange_ids=frozenset({EXCHANGE}),
        )
        asyncio.run(
            self.coordinator.on_state_change(
                change,
                datetime(2026, 10, 1, tzinfo=UTC),
                {EXCHANGE: snapshot},
            )
        )


def test_buy_lifts_sig_ask_capped_by_touch_depth_then_cancels_remainder() -> None:
    harness = _Harness()
    harness.run(_snapshot(ask_qty=30.0))
    assert len(harness.plans) == 1
    (intent,) = harness.plans[0].intents
    assert intent.strategy_id == STRATEGY_ID
    assert (intent.outcome_side, intent.action) == (OutcomeSide.YES, OrderAction.BUY)
    assert intent.limit_price_ticks == 140
    assert intent.quantity == 30
    assert [item.payload_json for item in harness.cancels] == ['{"orderId":4242}']


def test_sell_hits_sig_bid_as_yes_sell() -> None:
    harness = _Harness()
    harness.run(_snapshot(bid=160, ask=161, pm_bid=0.745, pm_ask=0.755))
    (intent,) = harness.plans[0].intents
    assert (intent.outcome_side, intent.action) == (OutcomeSide.YES, OrderAction.SELL)
    assert intent.limit_price_ticks == 160
    assert intent.quantity == 50


def test_filled_order_needs_no_cancel() -> None:
    harness = _Harness(resting=False)
    harness.run(_snapshot())
    assert len(harness.plans) == 1
    assert harness.cancels == []


def test_no_signal_inside_threshold() -> None:
    harness = _Harness()
    harness.run(_snapshot(bid=147, ask=148))
    assert harness.plans == []


def test_risk_denial_and_kill_switch_place_nothing() -> None:
    denied = _Harness(risk=_risk(kill_switch=True))
    denied.run(_snapshot())
    assert denied.plans == []
    latched = _Harness()
    latched.kill_switch.activate("test")
    latched.run(_snapshot())
    assert latched.plans == []


def test_never_crosses_own_maker_quote() -> None:
    harness = _Harness()
    harness.quotes.apply_authoritative(
        exchange_id=EXCHANGE,
        side=QuoteSide.ASK,
        price_ticks=140,
        size=50,
        remaining_size=50,
        logical_operation_id="make-op",
        exchange_order_id=7,
        lifecycle_state=LifecycleState.OPEN,
        observed_monotonic_ns=NOW,
    )
    harness.run(_snapshot())
    assert harness.plans == []


def test_unconcluded_remainder_cancel_is_retried_next_cycle() -> None:
    harness = _Harness(cancel_failures=2)
    harness.run(_snapshot())
    assert len(harness.cancels) == 2
    # Cooldown suppresses a second take; the pending cancel is retried.
    harness.run(_snapshot(), sequence=2)
    assert len(harness.plans) == 1
    assert len(harness.cancels) == 3
    harness.run(_snapshot(), sequence=3)
    assert len(harness.cancels) == 3


def test_near_certain_market_is_not_taken() -> None:
    harness = _Harness()
    harness.coordinator._min_fair_value = 0.10
    harness.coordinator._max_fair_value = 0.90
    # PM mid 0.95: SIG ask 0.90 would otherwise be a 5c BUY residual.
    harness.run(_snapshot(bid=179, ask=180, pm_bid=0.945, pm_ask=0.955))
    assert harness.plans == []
    harness.run(_snapshot(bid=139, ask=140), sequence=2)
    assert len(harness.plans) == 1


def _with_inventory(snapshot: MakerMarketSnapshot, quantity: float) -> MakerMarketSnapshot:
    position = RuntimePosition(
        exchange_id=EXCHANGE,
        market_id=MARKET,
        tournament_id=TOURNAMENT,
        gross_exposure=abs(quantity),
        signed_quantity=quantity,
    )
    portfolio = RuntimePortfolio(positions=(position,), orders=(), account_trusted=True)
    return replace(snapshot, runtime=replace(snapshot.runtime, portfolio=portfolio))


def test_position_cap_clips_and_blocks_only_inventory_increasing_takes() -> None:
    clipped = _Harness()
    clipped.coordinator._max_position = 200
    # Short 170 YES; a 50 SELL would reach -220, so only 30 more may be sold.
    clipped.run(_with_inventory(_snapshot(bid=160, ask=161), -170.0))
    (intent,) = clipped.plans[0].intents
    assert intent.action is OrderAction.SELL
    assert intent.quantity == 30

    blocked = _Harness()
    blocked.coordinator._max_position = 200
    blocked.run(_with_inventory(_snapshot(bid=160, ask=161), -200.0))
    assert blocked.plans == []

    reducing = _Harness()
    reducing.coordinator._max_position = 200
    # Short 250 YES (beyond the cap): a BUY reduces inventory and is allowed.
    reducing.run(_with_inventory(_snapshot(ask_qty=100.0), -250.0))
    (intent,) = reducing.plans[0].intents
    assert intent.action is OrderAction.BUY
    assert intent.quantity == 50


def test_taker_accepts_proxy_for_reducing_trade_with_bbo_proxy() -> None:
    harness = _Harness(allow_bbo_proxy=True)
    snapshot = _with_inventory(_snapshot(ask_qty=30.0), -50.0)
    portfolio = replace(
        snapshot.runtime.portfolio,
        account_trust_grade=AccountTrustGrade.PROXY,
        account_proxy_age_ns=1,
    )
    snapshot = replace(
        snapshot,
        sig_bbo_trusted=False,
        runtime=replace(snapshot.runtime, portfolio=portfolio),
    )

    harness.run(snapshot)

    assert len(harness.plans) == 1
    (intent,) = harness.plans[0].intents
    assert intent.action is OrderAction.BUY
    assert intent.quantity == 30


def test_taker_bbo_proxy_does_not_trade_when_flat() -> None:
    harness = _Harness(allow_bbo_proxy=True)
    snapshot = _snapshot(ask_qty=30.0)
    portfolio = replace(
        snapshot.runtime.portfolio,
        account_trust_grade=AccountTrustGrade.PROXY,
        account_proxy_age_ns=1,
    )
    snapshot = replace(
        snapshot,
        sig_bbo_trusted=False,
        runtime=replace(snapshot.runtime, portfolio=portfolio),
    )

    harness.run(snapshot)

    assert harness.plans == []
