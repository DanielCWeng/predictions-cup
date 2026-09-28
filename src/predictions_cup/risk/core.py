"""Synchronous fail-closed central risk engine for executable opportunities."""

from __future__ import annotations

from dataclasses import dataclass

from predictions_cup.execution.models import (
    ExecutionMode,
    OperationKind,
    RuntimeOrderIntent,
)
from predictions_cup.runtime.models import RuntimeSnapshot
from predictions_cup.strategy.core import NoTrade, Opportunity, StrategyResult

MAX_SIG_QUANTITY = 2_147_483_647


@dataclass(frozen=True, slots=True)
class RiskLimits:
    max_order_size: int
    max_gross_exposure: float
    max_per_market_exposure: float
    max_open_order_exposure: float
    max_concurrent_open_orders: int

    def __post_init__(self) -> None:
        if self.max_order_size <= 0:
            raise ValueError("max_order_size must be positive")
        if min(
            self.max_gross_exposure,
            self.max_per_market_exposure,
            self.max_open_order_exposure,
        ) <= 0.0:
            raise ValueError("exposure limits must be positive")
        if self.max_concurrent_open_orders <= 0:
            raise ValueError("max_concurrent_open_orders must be positive")


@dataclass(frozen=True, slots=True)
class RiskContext:
    mode: ExecutionMode
    kill_switch: bool
    limits: RiskLimits | None
    max_state_age_ns: int
    existing_logical_intent_ids: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class RiskDecision:
    approved: bool
    reason: str
    operation_kind: OperationKind | None = None
    intents: tuple[RuntimeOrderIntent, ...] = ()
    relationship_constraint: str | None = None
    strategy_family: str | None = None
    strategy_id: str | None = None
    signal_value: float | None = None
    fair_value: float | None = None
    decision_observation_ns: int | None = None


def _deny(reason: str) -> RiskDecision:
    return RiskDecision(approved=False, reason=reason)


def evaluate_risk(
    proposal: StrategyResult,
    snapshot: RuntimeSnapshot,
    context: RiskContext,
) -> RiskDecision:
    if isinstance(proposal, NoTrade):
        return _deny(proposal.reason)
    opportunity: Opportunity = proposal

    if context.mode is ExecutionMode.LIVE:
        if context.kill_switch:
            return _deny("global_kill_switch")
        if context.limits is None:
            return _deny("live_limits_not_configured")
        if not snapshot.portfolio.account_trusted:
            return _deny("account_state_untrusted")

    if opportunity.atomic:
        operation_kind = OperationKind.ATOMIC_MULTI_LEG
        if len(opportunity.legs) > 10:
            return _deny("multi_leg_cardinality")
    elif len(opportunity.legs) == 1:
        operation_kind = OperationKind.SINGLE_PLACEMENT
    else:
        operation_kind = OperationKind.BEST_EFFORT_BATCH
        if len(opportunity.legs) > 50:
            return _deny("batch_cardinality")

    if opportunity.relationship_id is not None and not opportunity.atomic:
        return _deny("relationship_constraint_requires_atomic_bundle")

    intents: list[RuntimeOrderIntent] = []
    new_exposure_by_market: dict[str, float] = {}

    for index, leg in enumerate(opportunity.legs):
        if leg.quantity <= 0 or leg.quantity > MAX_SIG_QUANTITY:
            return _deny("invalid_quantity")
        if leg.limit_price_ticks is not None and not 1 <= leg.limit_price_ticks <= 199:
            return _deny("invalid_limit_tick")

        market = snapshot.market(leg.market_id)
        if market is None:
            return _deny("unknown_market")
        if market.status != "open":
            return _deny("market_not_open")
        if market.tournament_id != leg.tournament_id:
            return _deny("tournament_identity_mismatch")
        if leg.exchange_id not in market.exchange_ids:
            return _deny("exchange_market_identity_mismatch")
        if not market.mapping_accepted or not market.tradeable:
            return _deny("mapping_or_tradeability_not_accepted")

        if opportunity.requires_trusted_depth:
            book = snapshot.book(leg.exchange_id)
            if book is None or not book.trusted_depth:
                return _deny("trusted_depth_required")
            state_age_ns = (
                snapshot.observation_monotonic_ns - book.observed_monotonic_ns
            )
            if state_age_ns > context.max_state_age_ns:
                return _deny("depth_state_stale")

        intent_id = f"{opportunity.strategy_id}:{opportunity.decision_observation_ns}:{index}"
        if intent_id in context.existing_logical_intent_ids:
            return _deny("duplicate_logical_intent")
        intents.append(
            RuntimeOrderIntent(
                intent_id=intent_id,
                exchange_id=leg.exchange_id,
                market_id=leg.market_id,
                tournament_id=leg.tournament_id,
                outcome_side=leg.outcome_side,
                action=leg.action,
                quantity=leg.quantity,
                limit_price_ticks=leg.limit_price_ticks,
                strategy_id=opportunity.strategy_id,
                decision_observation_ns=opportunity.decision_observation_ns,
            )
        )
        # A SELL can be canonicalised into a complement BUY by SIG. Reserving one
        # full currency unit per share is deliberately conservative.
        new_exposure_by_market[leg.market_id] = (
            new_exposure_by_market.get(leg.market_id, 0.0) + float(leg.quantity)
        )

    limits = context.limits
    if limits is not None:
        if any(intent.quantity > limits.max_order_size for intent in intents):
            return _deny("max_order_size")

        current_gross = snapshot.portfolio.gross_exposure
        new_gross = sum(new_exposure_by_market.values())
        if current_gross + new_gross > limits.max_gross_exposure:
            return _deny("max_gross_exposure")

        for market_id, additional in new_exposure_by_market.items():
            current_market = sum(
                abs(position.gross_exposure)
                for position in snapshot.portfolio.positions
                if position.market_id == market_id
            ) + sum(
                order.reserved_exposure
                for order in snapshot.portfolio.orders
                if order.market_id == market_id and (order.open or order.uncertain)
            )
            if current_market + additional > limits.max_per_market_exposure:
                return _deny("max_per_market_exposure")

        current_open_exposure = snapshot.portfolio.open_order_exposure
        if current_open_exposure + new_gross > limits.max_open_order_exposure:
            return _deny("max_open_order_exposure")

        current_open_orders = sum(
            1 for order in snapshot.portfolio.orders if order.open or order.uncertain
        )
        if current_open_orders + len(intents) > limits.max_concurrent_open_orders:
            return _deny("max_concurrent_open_orders")

    return RiskDecision(
        approved=True,
        reason="approved",
        operation_kind=operation_kind,
        intents=tuple(intents),
        relationship_constraint=opportunity.relationship_id,
        strategy_family=opportunity.family.value,
        strategy_id=opportunity.strategy_id,
        signal_value=opportunity.gross_edge,
        fair_value=opportunity.fair_value,
        decision_observation_ns=opportunity.decision_observation_ns,
    )
