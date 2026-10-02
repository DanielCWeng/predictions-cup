from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import cast

import pytest
from pydantic import SecretStr

from predictions_cup.config import AppSettings
from predictions_cup.execution.interlocks import (
    LiveExecutionPermit,
    assert_live_interlocks,
    assert_live_recovery_interlocks,
)
from predictions_cup.execution.journal import ExecutionJournal
from predictions_cup.execution.live import SigLiveSink
from predictions_cup.execution.models import (
    ExecutionEnvelope,
    ExecutionMode,
    LifecycleState,
    OperationKind,
    RuntimeOrderIntent,
)
from predictions_cup.execution.planner import build_execution_plan
from predictions_cup.execution.recovery import RecoveryRest, recover_startup
from predictions_cup.execution.reservations import ExecutionReservationBook
from predictions_cup.live_learn.evidence import JournalExecutionEvidenceProvider
from predictions_cup.risk import (
    CapitalRiskState,
    RiskContext,
    RiskExposureSnapshot,
    RiskLimits,
    build_exposure_snapshot,
    evaluate_risk,
    trip_global_halt,
)
from predictions_cup.runtime.models import (
    OrderAction,
    OutcomeSide,
    RuntimeMarket,
    RuntimePortfolio,
    RuntimePosition,
    RuntimeSnapshot,
)
from predictions_cup.shadow.contracts import CandidateDecision, DecisionStatus
from predictions_cup.sig.account_reconciliation import AccountAuthoritativeSnapshot
from predictions_cup.sig.account_runtime import (
    AccountRealtimeController,
)
from predictions_cup.sig.account_state import (
    AccountRealtimeStateEngine,
    AccountTrustTransition,
)
from predictions_cup.sig.errors import SigExecutionUncertainError
from predictions_cup.sig.realtime_models import RealtimeTokenDto
from predictions_cup.sig.trading_client import SigTradingClient
from predictions_cup.sig.trading_dto import (
    BatchOrderRequestDto,
    BatchOrderResponseDto,
    OrderFillsResponseDto,
    OrderReadDto,
    OrderStatusFilter,
    PositionsResponseDto,
)
from predictions_cup.strategy.core import CandidateLeg, Opportunity, StrategyFamily

STARTING_MAIN_SHA = "4a040a7d309df26098af5e81e96c7f1108d03117"
BASE = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
ARTIFACT = (
    Path(__file__).parents[1]
    / "data"
    / "launch_hardening"
    / "002"
    / "LIFECYCLE_RECONCILIATION.json"
)


@dataclass
class Oracle:
    cash: Decimal = Decimal("100")
    signed_inventory: Decimal = Decimal("0")
    gross_exposure: Decimal = Decimal("0")
    open_order_exposure: Decimal = Decimal("0")
    uncertain_exposure: Decimal = Decimal("0")
    filled_quantity: Decimal = Decimal("0")
    remaining_quantity: Decimal = Decimal("0")
    fees: Decimal = Decimal("0")
    order_identity: tuple[str, ...] = ()
    operation_identity: tuple[str, ...] = ()

    def fill_buy(self, *, quantity: Decimal, price: Decimal) -> None:
        self.cash -= quantity * price
        self.signed_inventory += quantity
        self.gross_exposure = abs(self.signed_inventory)
        self.filled_quantity += quantity

    def json(self) -> dict[str, object]:
        raw = asdict(self)
        return {
            key: (
                str(value)
                if isinstance(value, Decimal)
                else list(value)
                if isinstance(value, tuple)
                else value
            )
            for key, value in raw.items()
        }


@dataclass
class SyntheticAccountTruth:
    cash: Decimal = Decimal("100")
    signed_inventory: Decimal = Decimal("0")
    fills: Decimal = Decimal("0")

    def fill_buy(self, *, quantity: Decimal, price: Decimal) -> None:
        self.cash -= quantity * price
        self.signed_inventory += quantity
        self.fills += quantity


class TickClock:
    def __init__(self, value: int = 10_000) -> None:
        self.value = value

    def __call__(self) -> int:
        self.value += 10
        return self.value


class SyntheticVenue:
    def __init__(self, journal: ExecutionJournal) -> None:
        self.journal = journal
        self.placement_calls = 0
        self.cancel_calls = 0
        self.durable_before_dispatch = False
        self.cancel_uncertain = True
        self.placement_uncertain = False
        self.on_cancel: Callable[[int], None] | None = None

    async def place_batch_payload(self, payload_json: str) -> BatchOrderResponseDto:
        BatchOrderRequestDto.model_validate(json.loads(payload_json))
        self.placement_calls += 1
        self.durable_before_dispatch = any(
            event.event_type == "SUBMISSION"
            for event in self.journal.events("lh002-placement")
        )
        if self.placement_uncertain:
            raise SigExecutionUncertainError(
                status_code=None,
                code="TRANSPORT_OUTCOME_UNKNOWN",
                safe_message="synthetic placement disconnect",
            )
        return BatchOrderResponseDto.model_validate(
            {
                "results": [
                    {
                        "index": 0,
                        "ok": True,
                        "status": 200,
                        "data": {"orderId": 101},
                    },
                    {
                        "index": 1,
                        "ok": True,
                        "status": 200,
                        "data": {"orderId": 102},
                    },
                ]
            }
        )

    async def cancel_order(self, order_id: int) -> dict[str, object]:
        assert order_id in {101, 102}
        self.cancel_calls += 1
        if self.cancel_uncertain:
            raise SigExecutionUncertainError(
                status_code=None,
                code="TRANSPORT_OUTCOME_UNKNOWN",
                safe_message="synthetic cancel disconnect",
            )
        if self.on_cancel is not None:
            self.on_cancel(order_id)
        return {"cancelled": True, "orderId": order_id}


class SyntheticRecoveryRest:
    def __init__(self) -> None:
        self.open_sell = _order(102, open_=True, action="sell", price="0.6")
        self.closed_sell = _order(102, open_=False, action="sell", price="0.6")
        self.open_orders: tuple[OrderReadDto, ...] = (self.open_sell,)
        self.closed_buy = _order(101, open_=False, action="buy", price="0.4")
        self.positions = _positions_response(quantity="6")
        self.fill_queries = 0

    def mark_cancelled(self, order_id: int) -> None:
        assert order_id == 102
        self.open_orders = ()

    async def iter_orders(
        self,
        *,
        status: OrderStatusFilter = "open",
        exchange_id: str | None = None,
        market_id: str | None = None,
        tournament_id: str | None = None,
        limit: int = 200,
    ) -> AsyncIterator[OrderReadDto]:
        del exchange_id, market_id, limit
        assert status == "open"
        assert tournament_id == "t1"
        for order in self.open_orders:
            yield order

    async def get_order(self, order_id: int) -> OrderReadDto:
        if order_id == 101:
            return self.closed_buy
        assert order_id == 102
        return self.closed_sell if not self.open_orders else self.open_sell

    async def get_order_fills(
        self,
        order_id: int,
        *,
        limit: int = 50,
        cursor: str | None = None,
    ) -> OrderFillsResponseDto:
        del cursor
        assert limit == 200
        self.fill_queries += 1
        if order_id == 102:
            return OrderFillsResponseDto.model_validate(
                {
                    "orderId": 102,
                    "exchangeId": "36",
                    "tournamentId": "t1",
                    "data": [],
                    "pagination": {
                        "limit": 200,
                        "hasMore": False,
                        "nextCursor": None,
                    },
                    "coverage": {
                        "complete": True,
                        "projectedThroughSequence": 12,
                    },
                    "totalQuantityFilled": "0",
                    "avgFillPrice": None,
                }
            )
        assert order_id == 101
        return OrderFillsResponseDto.model_validate(
            {
                "orderId": 101,
                "exchangeId": "36",
                "tournamentId": "t1",
                "data": [
                    {
                        "id": 5001,
                        "orderId": 101,
                        "exchangeId": "36",
                        "marketId": "m1",
                        "price": "0.4",
                        "quantity": "4",
                        "side": "yes",
                        "filledAt": (BASE + timedelta(seconds=3)).isoformat(),
                    },
                    {
                        "id": 5002,
                        "orderId": 101,
                        "exchangeId": "36",
                        "marketId": "m1",
                        "price": "0.4",
                        "quantity": "2",
                        "side": "yes",
                        "filledAt": (BASE + timedelta(seconds=6)).isoformat(),
                    },
                ],
                "pagination": {
                    "limit": 200,
                    "hasMore": False,
                    "nextCursor": None,
                },
                "coverage": {
                    "complete": True,
                    "projectedThroughSequence": 12,
                },
                "totalQuantityFilled": "6",
                "avgFillPrice": "0.4",
            }
        )

    async def get_tournament_positions(
        self,
        tournament_slug: str,
    ) -> PositionsResponseDto:
        assert tournament_slug == "cup"
        return self.positions


def _settings() -> AppSettings:
    return AppSettings(
        sig_trade_credential=SecretStr("synthetic-only"),
        tournament_id="t1",
        tournament_slug="cup",
        trading_enabled=True,
        execution_mode="LIVE",
        global_kill_switch=False,
        risk_max_order_size=20,
        risk_max_gross_exposure=100.0,
        risk_max_tournament_exposure=100.0,
        risk_max_per_market_exposure=100.0,
        risk_max_open_order_exposure=100.0,
        risk_max_concurrent_open_orders=20,
        risk_capital_control_enabled=True,
        risk_session_loss_limit=20.0,
        risk_drawdown_limit=20.0,
    )


def _live_permit() -> LiveExecutionPermit:
    return assert_live_interlocks(
        _settings(),
        explicit_live_invocation=True,
        account_trusted=True,
        capital_state_ready=True,
    )


def _recovery_permit() -> LiveExecutionPermit:
    return assert_live_recovery_interlocks(
        _settings(),
        explicit_live_invocation=True,
        account_trusted=True,
    )


def _limits() -> RiskLimits:
    return RiskLimits(
        max_order_size=20,
        max_gross_exposure=100.0,
        max_per_market_exposure=100.0,
        max_open_order_exposure=100.0,
        max_concurrent_open_orders=20,
    )


def _runtime(
    portfolio: RuntimePortfolio,
    *,
    observed_ns: int,
) -> RuntimeSnapshot:
    return RuntimeSnapshot(
        markets=(
            RuntimeMarket(
                market_id="m1",
                status="open",
                exchange_ids=("36",),
                tournament_id="t1",
                mapping_accepted=True,
                tradeable=True,
            ),
        ),
        books=(),
        portfolio=portfolio,
        observation_monotonic_ns=observed_ns,
    )


def _proposal(*, decision_ns: int = 1_000) -> Opportunity:
    return Opportunity(
        family=StrategyFamily.MAKE,
        strategy_id="lh002-maker",
        legs=(
            CandidateLeg(
                exchange_id="36",
                market_id="m1",
                tournament_id="t1",
                outcome_side=OutcomeSide.YES,
                action=OrderAction.BUY,
                quantity=10,
                limit_price_ticks=80,
            ),
            CandidateLeg(
                exchange_id="36",
                market_id="m1",
                tournament_id="t1",
                outcome_side=OutcomeSide.YES,
                action=OrderAction.SELL,
                quantity=10,
                limit_price_ticks=120,
            ),
        ),
        gross_edge=0.01,
        fair_value=0.5,
        decision_observation_ns=decision_ns,
    )


def _candidate() -> CandidateDecision:
    return CandidateDecision(
        decision_id="lh002-maker-decision",
        candidate_id="lh002-maker",
        candidate_version="launch-hardening-002",
        strategy_family="MAKE",
        observed_at=BASE,
        monotonic_time=1_000,
        tournament_id="t1",
        exchange_id="36",
        market_id="m1",
        input_snapshot_id="lh002-input",
        mapping_version="accepted-live-map",
        fair_value=0.5,
        lower_bound=None,
        upper_bound=None,
        confidence=1.0,
        direction=None,
        score=None,
        action_intent="QUOTE",
        quote_intent={
            "bid_ticks": 80,
            "ask_ticks": 120,
            "bid_size": 10,
            "ask_size": 10,
        },
        decision_status=DecisionStatus.OK,
        abstain_reason=None,
        quality_flags=(),
        compute_started_at=1_000,
        compute_finished_at=1_001,
        compute_latency_ns=1,
        candidate_payload={},
    )


def _order(
    order_id: int,
    *,
    open_: bool,
    action: str,
    price: str,
) -> OrderReadDto:
    return OrderReadDto.model_validate(
        {
            "id": order_id,
            "exchangeId": "36",
            "side": "yes",
            "action": action,
            "quantity": "10",
            "priceLimit": price,
            "open": open_,
            "createdAt": BASE.isoformat(),
            "expirationDate": None,
        }
    )


def _positions_response(*, quantity: str) -> PositionsResponseDto:
    q = Decimal(quantity)
    market_value = q * Decimal("0.5")
    cost_basis = q * Decimal("0.4")
    pnl = market_value - cost_basis
    return PositionsResponseDto.model_validate(
        {
            "positions": (
                []
                if q == 0
                else [
                    {
                        "exchangeId": "36",
                        "marketId": "m1",
                        "marketTitle": "Synthetic market",
                        "option": "yes",
                        "settled": False,
                        "quantity": str(q),
                        "avgCost": "0.4",
                        "currentPrice": "0.5",
                        "marketValue": str(market_value),
                        "costBasis": str(cost_basis),
                        "unrealizedPnl": str(pnl),
                        "unrealizedPnlPct": "0.25",
                        "moneyEarned": "0",
                        "lots": [],
                    }
                ]
            ),
            "summary": {
                "totalMarketValue": str(market_value),
                "totalCostBasis": str(cost_basis),
                "totalUnrealizedPnl": str(pnl),
            },
        }
    )


def _authoritative(
    *,
    observed_at: datetime,
    position_quantity: str = "0",
    open_sell: bool = False,
) -> AccountAuthoritativeSnapshot:
    positions = _positions_response(quantity=position_quantity).positions
    return AccountAuthoritativeSnapshot(
        tournament_id="t1",
        tournament_slug="cup",
        open_orders=(
            (_order(102, open_=True, action="sell", price="0.6"),)
            if open_sell
            else ()
        ),
        positions=positions,
        observed_at=observed_at,
    )


def _delivery(revision: int, previous: int) -> dict[str, object]:
    return {
        "model": "account_batch",
        "revision": revision,
        "previousRevision": previous,
        "correlationId": f"lh002-{revision}",
        "sourceSequenceFrom": revision,
        "sourceSequenceThrough": revision,
    }


def _open_update(order_id: int) -> dict[str, object]:
    return {
        "orderId": order_id,
        "exchangeId": "36",
        "marketId": "m1",
        "open": True,
        "quantityTraded": "0",
        "totalCost": "0",
        "latestTradePrice": None,
        "tournamentId": "t1",
        "at": (BASE + timedelta(seconds=2)).isoformat(),
    }


def _fill_payload(
    *,
    revision: int,
    previous: int,
    quantity: str,
    at: datetime,
) -> dict[str, object]:
    return {
        "fills": [
            {
                "orderId": 101,
                "exchangeId": "36",
                "marketId": "m1",
                "price": "0.4",
                "quantity": quantity,
                "executedAt": at.isoformat(),
                "tournamentId": "t1",
            }
        ],
        "orderUpdates": [],
        "settlements": [],
        "refunds": [],
        "collateralChanges": [],
        "delivery": _delivery(revision, previous),
    }


def _order_batch(*, revision: int, previous: int, order_ids: tuple[int, ...]) -> dict[str, object]:
    return {
        "fills": [],
        "orderUpdates": [_open_update(order_id) for order_id in order_ids],
        "settlements": [],
        "refunds": [],
        "collateralChanges": [],
        "delivery": _delivery(revision, previous),
    }


async def _unused_token() -> RealtimeTokenDto:
    raise AssertionError("subscriber path is not used by deterministic lifecycle test")


async def _unused_resync() -> AccountAuthoritativeSnapshot:
    raise AssertionError("explicit authoritative snapshots are injected by the test")


def _portfolio_numbers(portfolio: RuntimePortfolio) -> dict[str, object]:
    return {
        "trusted": portfolio.account_trusted,
        "signed_inventory": sum(
            position.signed_quantity for position in portfolio.positions
        ),
        "gross_exposure": portfolio.gross_exposure,
        "open_order_exposure": portfolio.open_order_exposure,
        "uncertain_exposure": sum(
            order.reserved_exposure
            for order in portfolio.orders
            if order.uncertain
        ),
        "open_orders": sorted(
            order.logical_intent_id
            for order in portfolio.orders
            if order.open or order.uncertain
        ),
    }


def _journal_state(
    journal: ExecutionJournal,
    operation_ids: tuple[str, ...],
) -> dict[str, object]:
    events = [
        event.event_type
        for operation_id in operation_ids
        for event in journal.events(operation_id)
    ]
    return {
        "event_types": events,
        "unresolved": [
            item.logical_operation_id for item in journal.unresolved()
        ],
    }


def _stage(
    *,
    name: str,
    inputs: dict[str, object],
    oracle: Oracle,
    truth: SyntheticAccountTruth,
    portfolio: RuntimePortfolio,
    reservations: ExecutionReservationBook | None,
    journal: ExecutionJournal,
    risk_state: dict[str, object],
    learner_state: dict[str, object],
    pass_: bool,
    failure_reason: str | None = None,
) -> dict[str, object]:
    account = _portfolio_numbers(portfolio)
    reservation_orders = () if reservations is None else reservations.reserved_orders()
    residuals: dict[str, object] = {
        "synthetic_cash": str(oracle.cash - truth.cash),
        "synthetic_inventory": str(
            oracle.signed_inventory - truth.signed_inventory
        ),
        "synthetic_filled_quantity": str(
            oracle.filled_quantity - truth.fills
        ),
    }
    account_inventory = Decimal(str(account["signed_inventory"]))
    account_open = Decimal(str(account["open_order_exposure"]))
    account_uncertain = Decimal(str(account["uncertain_exposure"]))
    risk_exposure = build_exposure_snapshot(
        portfolio,
        strategy_attribution_complete=True,
    )
    residuals.update(
        {
            "risk_total_gross_exposure": str(
                oracle.gross_exposure
                + oracle.open_order_exposure
                - Decimal(str(risk_exposure.gross_exposure))
            ),
            "risk_open_order_exposure": str(
                oracle.open_order_exposure
                - Decimal(str(risk_exposure.open_order_exposure))
            ),
            "risk_uncertain_exposure": str(
                oracle.uncertain_exposure
                - Decimal(str(risk_exposure.uncertain_order_exposure))
            ),
        }
    )
    if portfolio.account_trusted:
        residuals.update(
            {
                "account_inventory": str(
                    oracle.signed_inventory - account_inventory
                ),
                "account_open_order_exposure": str(
                    oracle.open_order_exposure - account_open
                ),
                "account_uncertain_exposure": str(
                    oracle.uncertain_exposure - account_uncertain
                ),
            }
        )
    else:
        residuals.update(
            {
                "account_inventory": "UNTRUSTED_RECONCILE",
                "account_open_order_exposure": "UNTRUSTED_RECONCILE",
                "account_uncertain_exposure": "UNTRUSTED_RECONCILE",
            }
        )
    return {
        "stage": name,
        "inputs": inputs,
        "expected_oracle": oracle.json(),
        "account_state": {
            **account,
            "cash": "NOT_EXPOSED_BY_RUNTIME_ACCOUNT_CONTRACT",
            "synthetic_authoritative_cash": str(truth.cash),
        },
        "reservation_state": {
            "intent_ids": (
                [] if reservations is None else sorted(reservations.intent_ids())
            ),
            "exposure": sum(
                order.reserved_exposure for order in reservation_orders
            ),
        },
        "risk_state": {
            **risk_state,
            "exposure": {
                "gross_exposure": risk_exposure.gross_exposure,
                "open_order_exposure": risk_exposure.open_order_exposure,
                "uncertain_order_exposure": risk_exposure.uncertain_order_exposure,
                "trusted": risk_exposure.trusted,
            },
        },
        "journal_state": _journal_state(
            journal,
            ("lh002-placement", "lh002-cancel-101"),
        ),
        "learner_state": learner_state,
        "residuals": residuals,
        "pass": pass_,
        "failure_reason": failure_reason,
    }


def _risk_context(*, kill_switch: bool = False) -> RiskContext:
    return RiskContext(
        mode=ExecutionMode.LIVE,
        kill_switch=kill_switch,
        limits=_limits(),
        max_state_age_ns=1_000_000_000,
    )


def _account_controller(
    state: AccountRealtimeStateEngine,
    journal: ExecutionJournal,
    clock: TickClock,
) -> AccountRealtimeController:
    return AccountRealtimeController(
        state=state,
        mint_token=_unused_token,
        authoritative_resync=_unused_resync,
        execution_journal=journal,
        clock_ns=clock,
        observation_process_instance_id="lh002-account",
    )


def _learner_state(
    journal: ExecutionJournal,
    *,
    maturity_at: datetime,
) -> dict[str, object]:
    evidence = asyncio.run(
        JournalExecutionEvidenceProvider(journal.path).evidence_for(
            _candidate(),
            maturity_at=maturity_at,
        )
    )
    return {
        "supported": evidence.supported,
        "reason": evidence.reason,
        "planned_quantity": evidence.planned_quantity,
        "filled_quantity": sum(fill.quantity for fill in evidence.fills),
        "fill_ids": [fill.evidence_id for fill in evidence.fills],
        "actions": [fill.action for fill in evidence.fills],
    }


def test_composed_lifecycle_oracle_restart_and_reconciliation(tmp_path: Path) -> None:
    clock = TickClock()
    journal_path = tmp_path / "execution.sqlite3"
    journal = ExecutionJournal(journal_path)
    reservations = ExecutionReservationBook()
    account = AccountRealtimeStateEngine(
        tournament_id="t1",
        reservations=reservations,
    )
    account.apply_authoritative(_authoritative(observed_at=BASE))
    controller = _account_controller(account, journal, clock)
    truth = SyntheticAccountTruth()
    oracle = Oracle()
    stages: list[dict[str, object]] = []

    initial_risk = evaluate_risk(
        _proposal(),
        _runtime(account.runtime_portfolio(), observed_ns=1_000),
        _risk_context(),
    )
    assert initial_risk.approved
    stages.append(
        _stage(
            name="stage_0_clean_startup",
            inputs={"authoritative_empty_account": True},
            oracle=replace(
                oracle,
                open_order_exposure=Decimal("0"),
                uncertain_exposure=Decimal("0"),
            ),
            truth=truth,
            portfolio=account.runtime_portfolio(),
            reservations=reservations,
            journal=journal,
            risk_state={"approved": True, "reason": initial_risk.reason},
            learner_state={"status": "not_yet_dispatched"},
            pass_=True,
        )
    )

    oracle.remaining_quantity = Decimal("20")
    oracle.order_identity = ("101", "102")
    oracle.operation_identity = ("lh002-placement",)

    plan = build_execution_plan(
        initial_risk,
        logical_operation_id="lh002-placement",
        created_monotonic_ns=1_000,
    )
    reservations.reserve(plan.envelope.logical_operation_id, plan.intents)
    venue = SyntheticVenue(journal)
    sink = SigLiveSink(
        client=cast(SigTradingClient, venue),
        journal=journal,
        permit=_live_permit(),
        reservations=reservations,
        clock_ns=clock,
        wall_clock=lambda: BASE + timedelta(seconds=1),
        observation_process_instance_id="lh002-live",
    )
    event = asyncio.run(sink.dispatch(plan))
    assert event.state is LifecycleState.ACKED
    assert venue.durable_before_dispatch
    assert venue.placement_calls == 1
    oracle.open_order_exposure = Decimal("20")
    oracle.uncertain_exposure = Decimal("20")
    stages.append(
        _stage(
            name="stage_1_two_sided_quote",
            inputs={
                "risk_approved": True,
                "reserved_before_dispatch": True,
                "durable_before_dispatch": venue.durable_before_dispatch,
            },
            oracle=oracle,
            truth=truth,
            portfolio=reservations.overlay_snapshot(
                _runtime(account.runtime_portfolio(), observed_ns=1_100)
            ).portfolio,
            reservations=reservations,
            journal=journal,
            risk_state={"approved": True, "reason": "approved"},
            learner_state=_learner_state(
                journal,
                maturity_at=BASE + timedelta(seconds=1),
            ),
            pass_=True,
        )
    )

    asyncio.run(
        controller._handle_batch(
            "unused",
            _order_batch(revision=1, previous=0, order_ids=(101, 102)),
            BASE + timedelta(seconds=2),
        )
    )
    assert account.trusted
    assert account.last_accepted_revision == 1
    assert reservations.reservation_for_exchange_order_id("101") is not None
    stages.append(
        _stage(
            name="stage_2_ack_and_open_orders",
            inputs={"ack_ids": [101, 102], "account_revision": 1},
            oracle=oracle,
            truth=truth,
            portfolio=reservations.overlay_snapshot(
                _runtime(account.runtime_portfolio(), observed_ns=1_200)
            ).portfolio,
            reservations=reservations,
            journal=journal,
            risk_state={"approved": True, "reason": "known_own_orders"},
            learner_state=_learner_state(
                journal,
                maturity_at=BASE + timedelta(seconds=2),
            ),
            pass_=True,
        )
    )

    fill_one = _fill_payload(
        revision=2,
        previous=1,
        quantity="4",
        at=BASE + timedelta(seconds=3),
    )
    asyncio.run(
        controller._handle_batch(
            "unused",
            fill_one,
            BASE + timedelta(seconds=3),
        )
    )
    truth.fill_buy(quantity=Decimal("4"), price=Decimal("0.4"))
    oracle.fill_buy(quantity=Decimal("4"), price=Decimal("0.4"))
    oracle.open_order_exposure = Decimal("16")
    oracle.uncertain_exposure = Decimal("16")
    oracle.remaining_quantity = Decimal("16")
    denied = evaluate_risk(
        _proposal(decision_ns=2_000),
        reservations.overlay_snapshot(
            _runtime(account.runtime_portfolio(), observed_ns=2_000)
        ),
        _risk_context(),
    )
    assert denied.reason == "account_state_untrusted"
    learner_partial = _learner_state(
        journal,
        maturity_at=BASE + timedelta(seconds=4),
    )
    assert not learner_partial["supported"]
    assert (
        learner_partial["reason"]
        == "provisional_fill_evidence_requires_authoritative_reconciliation"
    )
    assert learner_partial["filled_quantity"] == pytest.approx(4.0)
    stages.append(
        _stage(
            name="stage_3_partial_fill",
            inputs={"realtime_fill": {"order_id": 101, "quantity": "4", "price": "0.4"}},
            oracle=oracle,
            truth=truth,
            portfolio=reservations.overlay_snapshot(
                _runtime(account.runtime_portfolio(), observed_ns=2_000)
            ).portfolio,
            reservations=reservations,
            journal=journal,
            risk_state={"approved": False, "reason": denied.reason},
            learner_state=learner_partial,
            pass_=not account.trusted and denied.reason == "account_state_untrusted",
        )
    )

    cancel = ExecutionEnvelope.cancellation(
        logical_operation_id="lh002-cancel-101",
        operation_kind=OperationKind.SINGLE_CANCELLATION,
        sink_mode=ExecutionMode.LIVE,
        created_monotonic_ns=2_100,
        order_id=101,
        tournament_id="t1",
    )
    with pytest.raises(SigExecutionUncertainError):
        asyncio.run(sink.cancel(cancel))
    cancel_state = next(
        item
        for item in journal.envelopes()
        if item.logical_operation_id == "lh002-cancel-101"
    )
    assert cancel_state.lifecycle_state is LifecycleState.UNCERTAIN
    oracle.operation_identity = ("lh002-placement", "lh002-cancel-101")
    stages.append(
        _stage(
            name="stage_4_cancel_disconnect_uncertain",
            inputs={"cancel_order_id": 101, "disconnect": True},
            oracle=oracle,
            truth=truth,
            portfolio=reservations.overlay_snapshot(
                _runtime(account.runtime_portfolio(), observed_ns=2_100)
            ).portfolio,
            reservations=reservations,
            journal=journal,
            risk_state={
                "approved": False,
                "reason": "account_state_untrusted",
                "uncertain_exposure_preserved": True,
            },
            learner_state=learner_partial,
            pass_=reservations.reservation_for_exchange_order_id("101") is not None,
        )
    )

    fill_two = _fill_payload(
        revision=3,
        previous=2,
        quantity="2",
        at=BASE + timedelta(seconds=6),
    )
    asyncio.run(
        controller._handle_batch(
            "unused",
            fill_two,
            BASE + timedelta(seconds=6),
        )
    )
    truth.fill_buy(quantity=Decimal("2"), price=Decimal("0.4"))
    oracle.fill_buy(quantity=Decimal("2"), price=Decimal("0.4"))
    oracle.open_order_exposure = Decimal("14")
    oracle.uncertain_exposure = Decimal("14")
    oracle.remaining_quantity = Decimal("14")
    late_identity = journal.placement_identity_for_exchange_order_id("101")
    assert late_identity == (
        "lh002-placement",
        plan.intents[0].intent_id,
    )
    assert all(
        event.event_type != "REALTIME_FILL"
        for event in journal.events("lh002-cancel-101")
    )
    learner_late = _learner_state(
        journal,
        maturity_at=BASE + timedelta(seconds=7),
    )
    assert not learner_late["supported"]
    assert learner_late["filled_quantity"] == pytest.approx(6.0)
    stages.append(
        _stage(
            name="stage_5_late_fill_during_cancel_uncertainty",
            inputs={"late_fill": {"order_id": 101, "quantity": "2", "price": "0.4"}},
            oracle=oracle,
            truth=truth,
            portfolio=reservations.overlay_snapshot(
                _runtime(account.runtime_portfolio(), observed_ns=2_200)
            ).portfolio,
            reservations=reservations,
            journal=journal,
            risk_state={
                "approved": False,
                "reason": "account_state_untrusted",
                "uncertain_exposure_preserved": True,
            },
            learner_state=learner_late,
            pass_=late_identity[0] == "lh002-placement",
        )
    )

    # The venue eventually resolves the cancel after the late fill. Authoritative
    # truth is now +6 inventory, closed bid, resting ask.
    oracle.open_order_exposure = Decimal("10")
    oracle.uncertain_exposure = Decimal("0")
    oracle.remaining_quantity = Decimal("10")
    account.apply_authoritative(
        _authoritative(
            observed_at=BASE + timedelta(seconds=10),
            position_quantity="6",
            open_sell=True,
        )
    )
    assert account.trusted
    assert reservations.intent_ids() == frozenset()
    reconciled_portfolio = account.runtime_portfolio()
    assert reconciled_portfolio.gross_exposure == pytest.approx(6.0)
    assert reconciled_portfolio.open_order_exposure == pytest.approx(10.0)
    learner_before_restart = _learner_state(
        journal,
        maturity_at=BASE + timedelta(seconds=11),
    )
    assert not learner_before_restart["supported"]
    stages.append(
        _stage(
            name="stage_6_reconnect_authoritative_snapshot",
            inputs={
                "position_quantity": "6",
                "open_order_ids": [102],
                "authoritative": True,
            },
            oracle=oracle,
            truth=truth,
            portfolio=reconciled_portfolio,
            reservations=reservations,
            journal=journal,
            risk_state={"approved": True, "reason": "trusted_reconciled_account"},
            learner_state=learner_before_restart,
            pass_=True,
        )
    )

    journal.close()

    # Process objects are destroyed and reconstructed solely from durable state.
    journal = ExecutionJournal(journal_path)
    recovery_rest = SyntheticRecoveryRest()
    venue.cancel_uncertain = False
    venue.on_cancel = recovery_rest.mark_cancelled
    recovery_sink = SigLiveSink(
        client=cast(SigTradingClient, venue),
        journal=journal,
        permit=_recovery_permit(),
        reservations=ExecutionReservationBook(),
        clock_ns=clock,
        wall_clock=lambda: BASE + timedelta(seconds=12),
        observation_process_instance_id="lh002-recovery",
    )
    recovery = asyncio.run(
        recover_startup(
            journal=journal,
            rest=cast(RecoveryRest, recovery_rest),
            live_sink=recovery_sink,
            tournament_id="t1",
            tournament_slug="cup",
            clock_ns=clock,
            wall_clock=lambda: BASE + timedelta(seconds=12),
        )
    )
    assert recovery.safe_to_resume_live
    assert recovery.unresolved_operation_ids == ()
    assert venue.placement_calls == 1
    assert venue.cancel_calls == 2
    assert recovery_rest.fill_queries == 3
    oracle.open_order_exposure = Decimal("0")
    oracle.uncertain_exposure = Decimal("0")

    placement_events = journal.events("lh002-placement")
    authoritative = [
        event for event in placement_events if event.event_type == "AUTHORITATIVE_FILL"
    ]
    assert [event.fill_id for event in authoritative] == ["5001", "5002"]
    assert all(event.logical_intent_id == plan.intents[0].intent_id for event in authoritative)
    assert all(
        event.event_type != "AUTHORITATIVE_FILL"
        for event in journal.events("lh002-cancel-101")
    )
    learner_final = _learner_state(
        journal,
        maturity_at=BASE + timedelta(seconds=20),
    )
    assert not learner_final["supported"]
    assert learner_final["reason"] == "fill_evidence_incomplete"
    assert learner_final["filled_quantity"] == pytest.approx(6.0)
    assert learner_final["actions"] == ["buy", "buy"]

    # A second restart/recovery pass must be a no-op economically.
    second_recovery = asyncio.run(
        recover_startup(
            journal=journal,
            rest=cast(RecoveryRest, recovery_rest),
            live_sink=recovery_sink,
            tournament_id="t1",
            tournament_slug="cup",
            clock_ns=clock,
            wall_clock=lambda: BASE + timedelta(seconds=13),
        )
    )
    assert second_recovery.safe_to_resume_live
    assert recovery_rest.fill_queries == 3
    assert venue.placement_calls == 1
    assert venue.cancel_calls == 2
    assert len(
        [
            event
            for event in journal.events("lh002-placement")
            if event.event_type == "AUTHORITATIVE_FILL"
        ]
    ) == 2

    stages.append(
        _stage(
            name="stage_7_restart_and_recovery",
            inputs={
                "process_objects_recreated": True,
                "recovery_passes": 2,
                "fill_queries": recovery_rest.fill_queries,
            },
            oracle=oracle,
            truth=truth,
            portfolio=recovery.portfolio,
            reservations=None,
            journal=journal,
            risk_state={
                "safe_to_resume_live": recovery.safe_to_resume_live,
                "unresolved_operation_ids": list(recovery.unresolved_operation_ids),
            },
            learner_state=learner_final,
            pass_=True,
        )
    )

    final_snapshot = _runtime(recovery.portfolio, observed_ns=5_000)
    final_risk = evaluate_risk(
        _proposal(decision_ns=5_000),
        final_snapshot,
        _risk_context(),
    )
    assert final_risk.approved
    halted = evaluate_risk(
        _proposal(decision_ns=5_001),
        final_snapshot,
        _risk_context(kill_switch=True),
    )
    assert halted.reason == "global_kill_switch"
    assert not _recovery_permit().fresh_admission_allowed

    stages.append(
        _stage(
            name="stage_8_final_state",
            inputs={
                "fresh_risk_healthy": True,
                "halt_blocks_fresh": True,
                "recovery_permit_fresh_admission_allowed": False,
            },
            oracle=oracle,
            truth=truth,
            portfolio=recovery.portfolio,
            reservations=None,
            journal=journal,
            risk_state={
                "approved": final_risk.approved,
                "reason": final_risk.reason,
                "halted_fresh_reason": halted.reason,
            },
            learner_state=learner_final,
            pass_=True,
        )
    )

    assert truth.cash == oracle.cash == Decimal("97.6")
    assert truth.signed_inventory == oracle.signed_inventory == Decimal("6")
    assert truth.fills == oracle.filled_quantity == Decimal("6")
    assert recovery.portfolio.gross_exposure == pytest.approx(6.0)
    assert recovery.portfolio.open_order_exposure == pytest.approx(0.0)
    assert all(stage["pass"] is True for stage in stages)

    committed = json.loads(ARTIFACT.read_text(encoding="utf-8"))
    assert committed["starting_main_sha"] == STARTING_MAIN_SHA
    assert committed["real_sig_orders_sent"] is False
    assert [item["stage"] for item in committed["stages"]] == [
        item["stage"] for item in stages
    ]
    assert [item["expected_oracle"] for item in committed["stages"]] == [
        item["expected_oracle"] for item in stages
    ]
    assert all(item["pass"] is True for item in committed["stages"])

    journal.close()


def test_duplicate_reordered_and_unknown_order_evidence_fail_closed(
    tmp_path: Path,
) -> None:
    journal = ExecutionJournal(tmp_path / "hostile.sqlite3")
    reservations = ExecutionReservationBook()
    intent = RuntimeOrderIntent(
        intent_id="hostile-intent",
        exchange_id="36",
        market_id="m1",
        tournament_id="t1",
        outcome_side=OutcomeSide.YES,
        action=OrderAction.BUY,
        quantity=5,
        limit_price_ticks=80,
        strategy_id="lh002-maker",
        decision_observation_ns=1_000,
    )
    envelope = ExecutionEnvelope.placement(
        logical_operation_id="hostile-placement",
        operation_kind=OperationKind.SINGLE_PLACEMENT,
        sink_mode=ExecutionMode.LIVE,
        idempotency_key="hostile-key",
        intents=(intent,),
        created_monotonic_ns=1_000,
    )
    journal.record_before_dispatch(
        envelope,
        (intent,),
        submitted_monotonic_ns=1_010,
    )
    reservations.reserve("hostile-placement", (intent,))
    reservations.bind_exchange_order(
        "hostile-intent",
        "201",
        acknowledged_at=BASE + timedelta(seconds=1),
    )
    for observed in (1_020, 1_030):
        journal.record_event(
            logical_operation_id="hostile-placement",
            logical_intent_id="hostile-intent",
            tournament_id="t1",
            event_type="ACK",
            observed_monotonic_ns=observed,
            exchange_id="36",
            exchange_order_id="201",
            terminal_status=LifecycleState.ACKED.value,
        )
    assert journal.placement_identity_for_exchange_order_id("201") == (
        "hostile-placement",
        "hostile-intent",
    )

    state = AccountRealtimeStateEngine(
        tournament_id="t1",
        reservations=reservations,
    )
    # Snapshot predates the ACK: the in-flight reservation must survive.
    state.apply_authoritative(_authoritative(observed_at=BASE))
    assert reservations.intent_ids() == frozenset({"hostile-intent"})

    known = _order_batch(revision=1, previous=0, order_ids=(201,))
    result = state.handle_raw_batch(known, observed_at=BASE + timedelta(seconds=2))
    assert result.accepted
    duplicate = state.handle_raw_batch(known, observed_at=BASE + timedelta(seconds=2))
    assert duplicate.duplicate
    assert state.trusted

    unknown = _order_batch(revision=2, previous=1, order_ids=(999,))
    result = state.handle_raw_batch(
        unknown,
        observed_at=BASE + timedelta(seconds=3),
    )
    assert result.requires_reconciliation
    assert state.transition is AccountTrustTransition.UNTRUSTED_UNKNOWN_OPEN_ORDER
    journal.close()


def test_authoritative_snapshot_before_delayed_realtime_fill_forces_reconcile(
    tmp_path: Path,
) -> None:
    journal = ExecutionJournal(tmp_path / "delayed-after-snapshot.sqlite3")
    reservations = ExecutionReservationBook()
    intent = RuntimeOrderIntent(
        intent_id="delayed-intent",
        exchange_id="36",
        market_id="m1",
        tournament_id="t1",
        outcome_side=OutcomeSide.YES,
        action=OrderAction.BUY,
        quantity=5,
        limit_price_ticks=80,
        strategy_id="lh002-maker",
        decision_observation_ns=1_000,
    )
    envelope = ExecutionEnvelope.placement(
        logical_operation_id="delayed-placement",
        operation_kind=OperationKind.SINGLE_PLACEMENT,
        sink_mode=ExecutionMode.LIVE,
        idempotency_key="delayed-key",
        intents=(intent,),
        created_monotonic_ns=1_000,
    )
    journal.record_before_dispatch(
        envelope,
        (intent,),
        submitted_monotonic_ns=1_010,
    )
    journal.record_event(
        logical_operation_id="delayed-placement",
        logical_intent_id="delayed-intent",
        tournament_id="t1",
        event_type="ACK",
        observed_monotonic_ns=1_020,
        exchange_id="36",
        exchange_order_id="401",
        terminal_status=LifecycleState.ACKED.value,
    )
    reservations.reserve("delayed-placement", (intent,))
    reservations.bind_exchange_order(
        "delayed-intent",
        "401",
        acknowledged_at=BASE + timedelta(seconds=1),
    )
    state = AccountRealtimeStateEngine(
        tournament_id="t1",
        reservations=reservations,
    )
    # Authoritative read occurs after the ACK and legitimately supersedes the
    # reservation. A delayed realtime fill arriving afterward must not mutate
    # the canonical account from stale evidence; it must revoke trust and be
    # retained only as provisional audit evidence.
    state.apply_authoritative(
        _authoritative(
            observed_at=BASE + timedelta(seconds=5),
            position_quantity="0",
        )
    )
    assert reservations.intent_ids() == frozenset()
    assert state.trusted
    before = state.runtime_portfolio()
    controller = _account_controller(state, journal, TickClock(3_000))
    payload = _fill_payload(
        revision=7,
        previous=6,
        quantity="1",
        at=BASE + timedelta(seconds=2),
    )
    fill = cast(list[dict[str, object]], payload["fills"])[0]
    fill["orderId"] = 401
    asyncio.run(
        controller._handle_batch(
            "unused",
            payload,
            BASE + timedelta(seconds=6),
        )
    )
    after = state.runtime_portfolio()
    assert not state.trusted
    assert after.positions == before.positions == ()
    provisional = [
        event
        for event in journal.events("delayed-placement")
        if event.event_type == "REALTIME_FILL"
    ]
    assert len(provisional) == 1
    assert provisional[0].logical_intent_id == "delayed-intent"
    journal.close()


def test_fill_before_order_update_and_cancel_evidence_keeps_original_identity(
    tmp_path: Path,
) -> None:
    journal = ExecutionJournal(tmp_path / "reordered.sqlite3")
    reservations = ExecutionReservationBook()
    intent = RuntimeOrderIntent(
        intent_id="reordered-intent",
        exchange_id="36",
        market_id="m1",
        tournament_id="t1",
        outcome_side=OutcomeSide.YES,
        action=OrderAction.BUY,
        quantity=5,
        limit_price_ticks=80,
        strategy_id="lh002-maker",
        decision_observation_ns=1_000,
    )
    envelope = ExecutionEnvelope.placement(
        logical_operation_id="reordered-placement",
        operation_kind=OperationKind.SINGLE_PLACEMENT,
        sink_mode=ExecutionMode.LIVE,
        idempotency_key="reordered-key",
        intents=(intent,),
        created_monotonic_ns=1_000,
    )
    journal.record_before_dispatch(
        envelope,
        (intent,),
        submitted_monotonic_ns=1_010,
    )
    journal.record_event(
        logical_operation_id="reordered-placement",
        logical_intent_id="reordered-intent",
        tournament_id="t1",
        event_type="ACK",
        observed_monotonic_ns=1_020,
        exchange_id="36",
        exchange_order_id="301",
        terminal_status=LifecycleState.ACKED.value,
    )
    reservations.reserve("reordered-placement", (intent,))
    reservations.bind_exchange_order(
        "reordered-intent",
        "301",
        acknowledged_at=BASE + timedelta(seconds=1),
    )
    cancel = ExecutionEnvelope.cancellation(
        logical_operation_id="reordered-cancel",
        operation_kind=OperationKind.SINGLE_CANCELLATION,
        sink_mode=ExecutionMode.LIVE,
        created_monotonic_ns=1_030,
        order_id=301,
        tournament_id="t1",
    )
    journal.record_before_dispatch(cancel, submitted_monotonic_ns=1_030)
    journal.record_event(
        logical_operation_id="reordered-cancel",
        event_type="CANCEL_SUBMITTED",
        observed_monotonic_ns=1_040,
        exchange_order_id="301",
    )
    journal.mark_state("reordered-cancel", LifecycleState.UNCERTAIN, 1_050)

    state = AccountRealtimeStateEngine(
        tournament_id="t1",
        reservations=reservations,
    )
    state.apply_authoritative(_authoritative(observed_at=BASE))
    controller = _account_controller(state, journal, TickClock(2_000))
    payload = _fill_payload(
        revision=1,
        previous=0,
        quantity="1",
        at=BASE + timedelta(seconds=2),
    )
    payload["fills"][0]["orderId"] = 301  # type: ignore[index]
    asyncio.run(
        controller._handle_batch(
            "unused",
            payload,
            BASE + timedelta(seconds=2),
        )
    )
    fills = [
        event
        for event in journal.events("reordered-placement")
        if event.event_type == "REALTIME_FILL"
    ]
    assert len(fills) == 1
    assert fills[0].logical_intent_id == "reordered-intent"
    assert all(
        event.event_type != "REALTIME_FILL"
        for event in journal.events("reordered-cancel")
    )
    journal.close()


def test_uncertain_placement_retains_reservation_and_recovery_authority(
    tmp_path: Path,
) -> None:
    journal = ExecutionJournal(tmp_path / "uncertain.sqlite3")
    reservations = ExecutionReservationBook()
    risk = evaluate_risk(
        _proposal(),
        _runtime(RuntimePortfolio(account_trusted=True), observed_ns=1_000),
        _risk_context(),
    )
    assert risk.approved
    plan = build_execution_plan(
        risk,
        logical_operation_id="lh002-placement",
        created_monotonic_ns=1_000,
    )
    reservations.reserve(plan.envelope.logical_operation_id, plan.intents)
    venue = SyntheticVenue(journal)
    venue.placement_uncertain = True
    sink = SigLiveSink(
        client=cast(SigTradingClient, venue),
        journal=journal,
        permit=_live_permit(),
        reservations=reservations,
        clock_ns=TickClock(),
        wall_clock=lambda: BASE,
        observation_process_instance_id="lh002-uncertain-placement",
    )
    with pytest.raises(SigExecutionUncertainError):
        asyncio.run(sink.dispatch(plan))
    persisted = next(
        item
        for item in journal.envelopes()
        if item.logical_operation_id == "lh002-placement"
    )
    assert persisted.lifecycle_state is LifecycleState.UNCERTAIN
    assert reservations.contains_operation(
        "lh002-placement",
        plan.envelope.intent_ids,
    )
    unresolved_snapshot = reservations.overlay_snapshot(
        _runtime(RuntimePortfolio(account_trusted=True), observed_ns=10_000)
    )
    unresolved_exposure = build_exposure_snapshot(
        unresolved_snapshot.portfolio,
        strategy_attribution_complete=True,
    )
    assert unresolved_exposure.uncertain_order_exposure == pytest.approx(20.0)
    halted_capital = trip_global_halt(
        _capital(marks_trusted=True, mark_ns=10_000),
        reason="synthetic_unresolved_halt",
        now_monotonic_ns=10_000,
    )
    halted = evaluate_risk(
        _proposal(decision_ns=10_000),
        unresolved_snapshot,
        RiskContext(
            mode=ExecutionMode.LIVE,
            kill_switch=False,
            limits=_limits(),
            max_state_age_ns=1_000_000_000,
            capital_state=halted_capital,
            require_capital_state=True,
        ),
    )
    assert halted.reason == "global_capital_halt"
    assert unresolved_exposure.uncertain_order_exposure == pytest.approx(20.0)

    recovery_permit = _recovery_permit()
    assert not recovery_permit.fresh_admission_allowed
    recovery_sink = SigLiveSink(
        client=cast(SigTradingClient, venue),
        journal=journal,
        permit=recovery_permit,
        reservations=reservations,
        clock_ns=TickClock(20_000),
        wall_clock=lambda: BASE,
        observation_process_instance_id="lh002-recovery-only",
    )
    with pytest.raises(ValueError, match="recovery-only LIVE permit"):
        asyncio.run(recovery_sink.dispatch(plan))
    journal.close()


def _capital(*, marks_trusted: bool, mark_ns: int | None) -> CapitalRiskState:
    return CapitalRiskState(
        session_id="lh002",
        session_start_equity=Decimal("100"),
        session_start_unrealised_pnl=Decimal("0"),
        realised_pnl=Decimal("0"),
        unrealised_pnl=Decimal("0"),
        current_equity=Decimal("100"),
        peak_session_equity=Decimal("100"),
        drawdown=Decimal("0"),
        net_external_cash_flow=Decimal("0"),
        exposure=RiskExposureSnapshot(
            gross_exposure=9.0,
            net_directional_exposure=1.0,
            open_order_exposure=0.0,
            uncertain_order_exposure=0.0,
            trusted=True,
            strategy_attribution_complete=True,
            group_classification_complete=True,
        ),
        account_trusted=True,
        account_observed_monotonic_ns=10_000,
        marks_trusted=marks_trusted,
        oldest_mark_observed_monotonic_ns=mark_ns,
        reconciliation_complete=True,
        global_halt=None,
        strategy_halts=(),
        limit_profile_version="lh002",
    )


def test_multi_market_invalid_or_stale_mark_and_global_halt_block_fresh_risk() -> None:
    portfolio = RuntimePortfolio(
        positions=(
            RuntimePosition("36", "m1", "t1", 6.0, 6.0),
            RuntimePosition("37", "m2", "t1", 3.0, 3.0),
        ),
        account_trusted=True,
    )
    snapshot = RuntimeSnapshot(
        markets=(
            RuntimeMarket("m1", "open", ("36",), "t1", True, True),
            RuntimeMarket("m2", "open", ("37",), "t1", True, True),
        ),
        books=(),
        portfolio=portfolio,
        observation_monotonic_ns=10_000,
    )
    invalid_context = RiskContext(
        mode=ExecutionMode.LIVE,
        kill_switch=False,
        limits=_limits(),
        max_state_age_ns=1_000,
        capital_state=_capital(marks_trusted=False, mark_ns=10_000),
        require_capital_state=True,
        max_mark_age_ns=100,
    )
    invalid = evaluate_risk(_proposal(decision_ns=10_000), snapshot, invalid_context)
    assert invalid.reason == "risk_marks_untrusted"

    stale_context = replace(
        invalid_context,
        capital_state=_capital(marks_trusted=True, mark_ns=1),
    )
    stale = evaluate_risk(_proposal(decision_ns=10_001), snapshot, stale_context)
    assert stale.reason == "risk_mark_state_stale"

    halted_capital = trip_global_halt(
        _capital(marks_trusted=True, mark_ns=10_000),
        reason="synthetic_halt",
        now_monotonic_ns=10_000,
    )
    halted_context = replace(
        invalid_context,
        capital_state=halted_capital,
    )
    halted = evaluate_risk(_proposal(decision_ns=10_002), snapshot, halted_context)
    assert halted.reason == "global_capital_halt"
    assert not _recovery_permit().fresh_admission_allowed
