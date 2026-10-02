"""Synchronous fail-closed central risk engine for executable opportunities."""

from __future__ import annotations

from dataclasses import dataclass

from predictions_cup.execution.models import (
    ExecutionMode,
    OperationKind,
    RuntimeOrderIntent,
)
from predictions_cup.risk.capital import CapitalRiskState, MarketExposureGroup
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
    max_per_strategy_exposure: float | None = None
    max_event_group_exposure: float | None = None
    max_tournament_exposure: float | None = None
    session_loss_limit: float | None = None
    drawdown_limit: float | None = None

    def __post_init__(self) -> None:
        if self.max_order_size <= 0:
            raise ValueError("max_order_size must be positive")
        if (
            min(
                self.max_gross_exposure,
                self.max_per_market_exposure,
                self.max_open_order_exposure,
            )
            <= 0.0
        ):
            raise ValueError("exposure limits must be positive")
        if self.max_concurrent_open_orders <= 0:
            raise ValueError("max_concurrent_open_orders must be positive")
        optional = (
            self.max_per_strategy_exposure,
            self.max_event_group_exposure,
            self.max_tournament_exposure,
            self.session_loss_limit,
            self.drawdown_limit,
        )
        if any(value is not None and value <= 0.0 for value in optional):
            raise ValueError("optional risk limits must be positive when configured")


@dataclass(frozen=True, slots=True)
class RiskProfile:
    name: str
    version: str
    limits: RiskLimits
    exploratory: bool = False
    hard_limits: RiskLimits | None = None

    def __post_init__(self) -> None:
        if not self.name.strip() or not self.version.strip():
            raise ValueError("risk profile name/version must not be blank")
        if self.exploratory and self.hard_limits is None:
            raise ValueError("exploratory profile requires hard absolute caps")


@dataclass(frozen=True, slots=True)
class RiskContext:
    mode: ExecutionMode
    kill_switch: bool
    limits: RiskLimits | None
    max_state_age_ns: int
    existing_logical_intent_ids: frozenset[str] = frozenset()
    capital_state: CapitalRiskState | None = None
    profile: RiskProfile | None = None
    exposure_groups: tuple[MarketExposureGroup, ...] = ()
    max_account_age_ns: int | None = None
    max_mark_age_ns: int | None = None
    require_capital_state: bool = False


@dataclass(frozen=True, slots=True)
class RiskDecision:
    approved: bool
    reason: str
    execution_mode: ExecutionMode | None = None
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


def _limit_sets(context: RiskContext) -> tuple[RiskLimits, ...]:
    if context.profile is not None:
        if context.profile.hard_limits is None:
            return (context.profile.limits,)
        return (context.profile.limits, context.profile.hard_limits)
    if context.limits is None:
        return ()
    return (context.limits,)


def _group_lookup(
    memberships: tuple[MarketExposureGroup, ...],
) -> dict[tuple[str, str], tuple[str, ...]]:
    return {(item.market_id, item.tournament_id): item.group_ids for item in memberships}


def evaluate_risk(
    proposal: StrategyResult,
    snapshot: RuntimeSnapshot,
    context: RiskContext,
) -> RiskDecision:
    if isinstance(proposal, NoTrade):
        return _deny(proposal.reason)
    opportunity: Opportunity = proposal
    execution_tournament_id = opportunity.legs[0].tournament_id
    if any(leg.tournament_id != execution_tournament_id for leg in opportunity.legs):
        return _deny("mixed_tournament_operation")

    limit_sets = _limit_sets(context)
    if context.mode is ExecutionMode.LIVE:
        if context.kill_switch:
            return _deny("global_kill_switch")
        if not limit_sets:
            return _deny("live_limits_not_configured")
        if not snapshot.portfolio.account_trusted:
            return _deny("account_state_untrusted")

    capital = context.capital_state
    state_dependent_limits = any(
        any(
            value is not None
            for value in (
                limits.max_per_strategy_exposure,
                limits.max_event_group_exposure,
                limits.max_tournament_exposure,
                limits.session_loss_limit,
                limits.drawdown_limit,
            )
        )
        for limits in limit_sets
    )
    if (context.require_capital_state or state_dependent_limits) and capital is None:
        return _deny("capital_state_missing")
    if capital is not None:
        if not capital.reconciliation_complete:
            return _deny("risk_state_unreconciled")
        if not capital.account_trusted or not capital.exposure.trusted:
            return _deny("risk_state_untrusted")
        if capital.global_halt is not None and capital.global_halt.active:
            return _deny("global_capital_halt")
        if capital.strategy_halted(opportunity.strategy_id, opportunity.family.value):
            return _deny("strategy_halt")
        if context.max_account_age_ns is not None:
            account_age = snapshot.observation_monotonic_ns - capital.account_observed_monotonic_ns
            if account_age > context.max_account_age_ns:
                return _deny("risk_account_state_stale")
        if not capital.marks_trusted:
            return _deny("risk_marks_untrusted")
        if (
            context.max_mark_age_ns is not None
            and capital.oldest_mark_observed_monotonic_ns is not None
        ):
            mark_age = snapshot.observation_monotonic_ns - capital.oldest_mark_observed_monotonic_ns
            if mark_age > context.max_mark_age_ns:
                return _deny("risk_mark_state_stale")
        for limits in limit_sets:
            if (
                limits.session_loss_limit is not None
                and float(capital.session_pnl) <= -limits.session_loss_limit
            ):
                return _deny("session_loss_limit")
            if (
                limits.drawdown_limit is not None
                and float(capital.drawdown) >= limits.drawdown_limit
            ):
                return _deny("peak_drawdown_limit")

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
            state_age_ns = snapshot.observation_monotonic_ns - book.observed_monotonic_ns
            if state_age_ns > context.max_state_age_ns:
                return _deny("depth_state_stale")

        intent_id = (
            f"{opportunity.strategy_id}:"
            f"{opportunity.decision_observation_ns}:"
            f"{leg.exchange_id}:{index}"
        )
        known_intent_ids = context.existing_logical_intent_ids.union(
            order.logical_intent_id for order in snapshot.portfolio.orders
        )
        if intent_id in known_intent_ids:
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
        new_exposure_by_market[leg.market_id] = new_exposure_by_market.get(
            leg.market_id, 0.0
        ) + float(leg.quantity)

    new_gross = sum(new_exposure_by_market.values())
    for limits in limit_sets:
        denial = _evaluate_limit_set(
            limits=limits,
            opportunity=opportunity,
            execution_tournament_id=execution_tournament_id,
            intents=tuple(intents),
            new_exposure_by_market=new_exposure_by_market,
            new_gross=new_gross,
            snapshot=snapshot,
            capital=capital,
            exposure_groups=context.exposure_groups,
        )
        if denial is not None:
            return _deny(denial)

    return RiskDecision(
        approved=True,
        reason="approved",
        execution_mode=context.mode,
        operation_kind=operation_kind,
        intents=tuple(intents),
        relationship_constraint=opportunity.relationship_id,
        strategy_family=opportunity.family.value,
        strategy_id=opportunity.strategy_id,
        signal_value=opportunity.gross_edge,
        fair_value=opportunity.fair_value,
        decision_observation_ns=opportunity.decision_observation_ns,
    )


def _evaluate_limit_set(
    *,
    limits: RiskLimits,
    opportunity: Opportunity,
    execution_tournament_id: str,
    intents: tuple[RuntimeOrderIntent, ...],
    new_exposure_by_market: dict[str, float],
    new_gross: float,
    snapshot: RuntimeSnapshot,
    capital: CapitalRiskState | None,
    exposure_groups: tuple[MarketExposureGroup, ...],
) -> str | None:
    if any(intent.quantity > limits.max_order_size for intent in intents):
        return "max_order_size"

    # Worst-case gross assumes every open/UNCERTAIN reservation can become a
    # position before the next authoritative reconciliation.
    current_gross = (
        snapshot.portfolio.gross_exposure
        + snapshot.portfolio.open_order_exposure
        + snapshot.portfolio.account_proxy_uncertainty
    )
    if current_gross + new_gross > limits.max_gross_exposure:
        return "max_gross_exposure"

    for market_id, additional in new_exposure_by_market.items():
        current_market = (
            sum(
                abs(position.gross_exposure)
                for position in snapshot.portfolio.positions
                if (
                    position.market_id == market_id
                    and position.tournament_id == execution_tournament_id
                )
            )
            + sum(
                order.reserved_exposure
                for order in snapshot.portfolio.orders
                if (
                    order.market_id == market_id
                    and order.tournament_id == execution_tournament_id
                    and (order.open or order.uncertain)
                )
            )
            + snapshot.portfolio.account_proxy_uncertainty
        )
        if current_market + additional > limits.max_per_market_exposure:
            return "max_per_market_exposure"

    if (
        snapshot.portfolio.open_order_exposure
        + snapshot.portfolio.account_proxy_uncertainty
        + new_gross
        > limits.max_open_order_exposure
    ):
        return "max_open_order_exposure"

    current_open_orders = sum(
        1 for order in snapshot.portfolio.orders if order.open or order.uncertain
    )
    if current_open_orders + len(intents) > limits.max_concurrent_open_orders:
        return "max_concurrent_open_orders"

    advanced_configured = any(
        value is not None
        for value in (
            limits.max_per_strategy_exposure,
            limits.max_event_group_exposure,
            limits.max_tournament_exposure,
        )
    )
    if advanced_configured and capital is None:
        return "capital_state_required_for_advanced_limits"
    if capital is None:
        return None

    exposure = capital.exposure
    local_pending = tuple(
        order
        for order in snapshot.portfolio.orders
        if (order.open or order.uncertain) and order.strategy_id is not None
    )
    if limits.max_per_strategy_exposure is not None:
        if not exposure.strategy_attribution_complete:
            return "strategy_exposure_untrusted"
        pending_strategy = sum(
            order.reserved_exposure
            for order in local_pending
            if order.strategy_id == opportunity.strategy_id
        )
        if (
            exposure.strategy(opportunity.strategy_id) + pending_strategy + new_gross
            > limits.max_per_strategy_exposure
        ):
            return "max_per_strategy_exposure"

    if limits.max_tournament_exposure is not None:
        pending_tournament = sum(
            order.reserved_exposure
            for order in local_pending
            if order.tournament_id == execution_tournament_id
        )
        if (
            exposure.tournament(execution_tournament_id) + pending_tournament + new_gross
            > limits.max_tournament_exposure
        ):
            return "max_tournament_exposure"

    if limits.max_event_group_exposure is not None:
        if not exposure.group_classification_complete:
            return "exposure_group_state_untrusted"
        lookup = _group_lookup(exposure_groups)
        pending_by_group: dict[str, float] = {}
        for order in local_pending:
            identity = (order.market_id, order.tournament_id)
            groups = lookup.get(identity)
            if groups is None:
                return "exposure_group_unclassified"
            for group_id in groups:
                pending_by_group[group_id] = (
                    pending_by_group.get(group_id, 0.0) + order.reserved_exposure
                )
        additional_by_group: dict[str, float] = {}
        for market_id, additional in new_exposure_by_market.items():
            identity = (market_id, execution_tournament_id)
            groups = lookup.get(identity)
            if groups is None:
                return "exposure_group_unclassified"
            for group_id in groups:
                additional_by_group[group_id] = additional_by_group.get(group_id, 0.0) + additional
        for group_id, additional in additional_by_group.items():
            if (
                exposure.group(group_id) + pending_by_group.get(group_id, 0.0) + additional
                > limits.max_event_group_exposure
            ):
                return "max_event_group_exposure"

    return None
