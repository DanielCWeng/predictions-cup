"""Rank complete-portfolio election opportunities, without generating live orders.

Each candidate is an independently sourced all-in, depth-qualified quote.
The portfolio is evaluated under *joint* settlement scenarios using the
existing terminal scorer. Nothing here estimates forecasting probabilities,
sends an order, or overrides central Risk/BUILD-009.
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any

from predictions_cup.analysis.election_terminal import (
    ExecutableTrade,
    Holding,
    PlanScore,
    ProbabilityView,
    SettlementScenario,
    TradePlan,
    evaluate_trade_plans,
)


@dataclass(frozen=True, slots=True)
class CandidateQuote:
    identifier: str
    trade: ExecutableTrade
    observed_at: datetime
    source_reference: str


@dataclass(frozen=True, slots=True)
class Opportunity:
    name: str
    constituent_ids: tuple[str, ...]
    score: PlanScore
    worst_outcome_delta: float
    best_outcome_delta: float
    max_initial_cash_required: float
    capital_released_by_exits: float

    def as_document(self) -> dict[str, object]:
        return {
            "name": self.name,
            "constituent_ids": list(self.constituent_ids),
            "model_scores": [asdict(score) for score in self.score.model_scores],
            "min_expected_gain": self.score.min_expected_gain,
            "min_probability_improvement": self.score.min_probability_improvement,
            "worst_outcome_delta": self.worst_outcome_delta,
            "best_outcome_delta": self.best_outcome_delta,
            "worst_settlement_balance": self.score.worst_settlement_balance,
            "best_settlement_balance": self.score.best_settlement_balance,
            "remaining_cash": self.score.remaining_cash,
            "max_initial_cash_required": self.max_initial_cash_required,
            "capital_released_by_exits": self.capital_released_by_exits,
        }


@dataclass(frozen=True, slots=True)
class OpportunityReport:
    ranked: tuple[Opportunity, ...]
    rejected: dict[str, int]
    plans_evaluated: int
    inputs_trusted: bool
    execution_authority: bool = False

    def as_document(self) -> dict[str, Any]:
        return {
            "mode": "READ_ONLY_SETTLEMENT_OPPORTUNITY_RANKING",
            "execution_authority": False,
            "forecast_calibration_claim": False,
            "inputs_trusted": self.inputs_trusted,
            "plans_evaluated": self.plans_evaluated,
            "rejection_counts": self.rejected,
            "ranked_opportunities": [item.as_document() for item in self.ranked],
            "note": (
                "Scenario weights and quotes are supplied, not verified here. "
                "Ranked output is NOT an execution instruction, risk approval, "
                "fill prediction, or credible top-three probability unless all "
                "joint scenarios, competitor targets and probabilities are defensible."
            ),
        }


def _payout(trade: ExecutableTrade, scenario: SettlementScenario) -> float:
    resolves = scenario.resolves_yes[trade.market_id]
    return float(resolves if trade.side == "YES" else not resolves)


def _scenario_gain(plan: TradePlan, scenario: SettlementScenario) -> float:
    result = 0.0
    for trade in plan.trades:
        payoff = _payout(trade, scenario)
        unit = (
            payoff - trade.executable_price - trade.extra_cost_per_share
            if trade.action == "BUY"
            else trade.executable_price - trade.extra_cost_per_share - payoff
        )
        result += trade.quantity * unit
    return result


def _cash_path(
    plan: TradePlan, cash: float,
) -> tuple[float, float]:
    remaining = cash
    minimum = remaining
    exit_proceeds = 0.0
    for t in plan.trades:
        if t.action == "SELL":
            proceeds = t.quantity * (t.executable_price - t.extra_cost_per_share)
            remaining += proceeds
            exit_proceeds += proceeds
        else:
            remaining -= t.quantity * (t.executable_price + t.extra_cost_per_share)
        minimum = min(minimum, remaining)
    return max(0.0, cash - minimum), exit_proceeds


def rank_opportunities(
    *,
    scenarios: tuple[SettlementScenario, ...],
    probability_views: tuple[ProbabilityView, ...],
    holdings: tuple[Holding, ...],
    cash: float,
    target_balance: float,
    candidates: tuple[CandidateQuote, ...],
    now: datetime,
    authoritative_position_market_ids: frozenset[str],
    portfolio_complete: bool,
    account_reconciled: bool,
    uncertain_order_exposure: float,
    max_quote_age_seconds: float = 15.0,
    min_robust_gain: float = 0.0,
    max_candidates: int = 40,
    max_results: int = 15,
) -> OpportunityReport:
    """Evaluate all singles and sale-first, two-leg funded rotations.

    An incomplete portfolio is a hard error, not an optimistic projection.
    Uncertain orders or untrusted accounts can be ranked for SHADOW review,
    but this function never labels any output executable or authorised.
    The current competing forecast views are *assumptions*, not alpha proof.
    """
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("as-of clock must be timezone aware")
    if (
        not math.isfinite(max_quote_age_seconds)
        or not 0 < max_quote_age_seconds <= 120
        or not math.isfinite(min_robust_gain)
        or not math.isfinite(uncertain_order_exposure)
        or uncertain_order_exposure < 0
        or not 1 <= max_candidates <= 100
        or not 1 <= max_results <= 100
    ):
        raise ValueError("invalid bounded opportunity search settings")
    if not portfolio_complete:
        raise ValueError("complete authoritative inventory is required")
    current_markets = {h.market_id for h in holdings}
    if not authoritative_position_market_ids or current_markets != authoritative_position_market_ids:
        raise ValueError("inventory differs from authoritative account market IDs")
    if len({c.identifier for c in candidates}) != len(candidates):
        raise ValueError("duplicate candidate identifier")
    if len(candidates) > max_candidates:
        raise ValueError("candidate search bound exceeded")

    # Validate scenarios, models and entire portfolio even when no market
    # passes the quote filters: an invalid input cannot yield recommendations.
    evaluate_trade_plans(
        scenarios=scenarios, probability_views=probability_views,
        holdings=holdings, cash=cash, target_balance=target_balance,
        plans=(TradePlan("validation", (
            ExecutableTrade(
                market_id=next(iter(scenarios[0].resolves_yes)),
                action="BUY", side="YES", quantity=1,
                executable_price=.5, displayed_shares=1,
            ),
        )),),
    ) if cash >= .5 else _validate_with_no_cash(
        scenarios, probability_views, holdings, cash, target_balance,
    )

    refused: Counter[str] = Counter()
    fresh: list[CandidateQuote] = []
    scenario_ids = set(scenarios[0].resolves_yes)
    for item in candidates:
        t = item.trade
        if (
            not item.identifier.strip() or not item.source_reference.strip()
            or t.market_id not in scenario_ids
        ):
            refused["missing_identity_or_settlement"] += 1
            continue
        if item.observed_at.tzinfo is None or item.observed_at.utcoffset() is None:
            refused["naive_quote_timestamp"] += 1
            continue
        age = (now.astimezone(UTC) - item.observed_at.astimezone(UTC)).total_seconds()
        if age < 0 or age > max_quote_age_seconds:
            refused["stale_or_future_quote"] += 1
            continue
        if (
            type(t.quantity) is not int or t.quantity < 1
            or type(t.displayed_shares) is not int or t.quantity > t.displayed_shares
            or not math.isfinite(t.executable_price)
            or not 0 < t.executable_price < 1
            or not math.isfinite(t.extra_cost_per_share)
            or t.extra_cost_per_share < 0
            or t.action not in ("BUY", "SELL")
            or t.side not in ("YES", "NO")
        ):
            refused["invalid_executable_quote"] += 1
            continue
        fresh.append(item)

    proposals: list[tuple[tuple[str, ...], TradePlan]] = [
        ((c.identifier,), TradePlan(c.identifier, (c.trade,))) for c in fresh
    ]
    exits = [c for c in fresh if c.trade.action == "SELL"]
    entries = [c for c in fresh if c.trade.action == "BUY"]
    for exit_candidate in exits:
        for entry in entries:
            if entry.trade.market_id == exit_candidate.trade.market_id:
                continue
            ids = (exit_candidate.identifier, entry.identifier)
            proposals.append((ids, TradePlan("rotate:" + ":".join(ids), (
                exit_candidate.trade, entry.trade,
            ))))

    ranked: list[Opportunity] = []
    for ids, plan in proposals:
        try:
            score = evaluate_trade_plans(
                scenarios=scenarios, probability_views=probability_views,
                holdings=holdings, cash=cash, target_balance=target_balance,
                plans=(plan,),
            )[0]
        except ValueError as exc:
            message = str(exc)
            reason = (
                "insufficient_cash" if "insufficient cash" in message else
                "unowned_inventory" if "unowned" in message else
                "invalid_or_unverifiable_plan"
            )
            refused[reason] += 1
            continue
        if score.min_expected_gain <= min_robust_gain:
            refused["nonpositive_robust_expected_gain"] += 1
            continue
        outcomes = [_scenario_gain(plan, scenario) for scenario in scenarios]
        cash_needed, proceeds = _cash_path(plan, cash)
        ranked.append(Opportunity(
            name=plan.name, constituent_ids=ids, score=score,
            worst_outcome_delta=min(outcomes),
            best_outcome_delta=max(outcomes),
            max_initial_cash_required=cash_needed,
            capital_released_by_exits=proceeds,
        ))
    ranked.sort(key=lambda item: (
        item.score.min_probability_improvement,
        item.score.min_expected_gain,
        item.worst_outcome_delta,
        -item.max_initial_cash_required,
    ), reverse=True)
    return OpportunityReport(
        ranked=tuple(ranked[:max_results]),
        rejected=dict(sorted(refused.items())),
        plans_evaluated=len(proposals),
        inputs_trusted=account_reconciled and uncertain_order_exposure == 0,
    )


def _validate_with_no_cash(
    scenarios: tuple[SettlementScenario, ...],
    probability_views: tuple[ProbabilityView, ...],
    holdings: tuple[Holding, ...],
    cash: float,
    target_balance: float,
) -> None:
    # The existing terminal validator does not expose a no-trade plan.
    # Use a one-share covered sale if possible, else validate by calling
    # its common input validator (pure, with no dependence on quote data).
    from predictions_cup.analysis.election_terminal import _validate_inputs

    _validate_inputs(scenarios, probability_views, holdings, cash, target_balance)
