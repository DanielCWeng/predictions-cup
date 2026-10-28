"""Offline election-settlement scenario comparison, not an order generator.

A scenario is a *joint* election outcome. This prevents the common mistake of
treating perfectly correlated Senate/chamber positions as independent bets.
Probability views are supplied, documented assumptions, never inferred from
Polymarket or preliminary vote leads. Prices must be executable for the entire
requested quantity; this module neither collects quotes nor sends orders.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

Side = Literal["YES", "NO"]
TradeAction = Literal["BUY", "SELL"]


@dataclass(frozen=True)
class SettlementScenario:
    name: str
    resolves_yes: Mapping[str, bool]


@dataclass(frozen=True)
class ProbabilityView:
    name: str
    evidence_reference: str
    probabilities: Mapping[str, float]


@dataclass(frozen=True)
class Holding:
    market_id: str
    yes_shares: int = 0
    no_shares: int = 0


@dataclass(frozen=True)
class ExecutableTrade:
    market_id: str
    action: TradeAction
    side: Side
    quantity: int
    # All-in quote price / proceeds per share, measured for this exact size;
    # it is NOT a best-bid/ask reference extrapolated beyond visible depth.
    executable_price: float
    displayed_shares: int
    extra_cost_per_share: float = 0.0


@dataclass(frozen=True)
class TradePlan:
    name: str
    trades: tuple[ExecutableTrade, ...]


@dataclass(frozen=True)
class ModelScore:
    model_name: str
    baseline_expected_balance: float
    expected_balance: float
    incremental_expected_balance: float
    baseline_probability_beat_target: float
    probability_beat_target: float


@dataclass(frozen=True)
class PlanScore:
    name: str
    model_scores: tuple[ModelScore, ...]
    # Conservative across the explicitly supplied competing forecasts.
    min_expected_gain: float
    min_probability_improvement: float
    worst_settlement_balance: float
    best_settlement_balance: float
    remaining_cash: float


def _valid_quantity(value: int) -> bool:
    return type(value) is int and value >= 0


def _validate_inputs(
    scenarios: tuple[SettlementScenario, ...],
    models: tuple[ProbabilityView, ...],
    holdings: tuple[Holding, ...],
    cash: float,
    target: float,
) -> set[str]:
    if not math.isfinite(cash) or cash < 0 or not math.isfinite(target) or target < 0:
        raise ValueError("invalid cash or leaderboard target")
    if not scenarios or not models:
        raise ValueError("scenario outcomes and probability views are required")
    names = [s.name for s in scenarios]
    if not all(names) or len(set(names)) != len(names):
        raise ValueError("duplicate or empty scenario name")
    markets = set(scenarios[0].resolves_yes)
    if not markets or any(
        set(s.resolves_yes) != markets
        or any(type(value) is not bool for value in s.resolves_yes.values())
        for s in scenarios
    ):
        raise ValueError("each scenario must resolve every market explicitly")
    seen_models: set[str] = set()
    references: set[str] = set()
    for model in models:
        if (
            not model.name or not model.evidence_reference
            or model.name in seen_models or model.evidence_reference in references
            or set(model.probabilities) != set(names)
        ):
            raise ValueError("model identity or scenario support mismatch")
        seen_models.add(model.name)
        references.add(model.evidence_reference)
        weights = tuple(model.probabilities.values())
        if (
            any(not math.isfinite(p) or p < 0 or p > 1 for p in weights)
            or not math.isclose(sum(weights), 1.0, rel_tol=0, abs_tol=1e-8)
        ):
            raise ValueError("model probabilities must sum to one")
    seen_holdings: set[str] = set()
    for held in holdings:
        if (
            held.market_id not in markets or held.market_id in seen_holdings
            or not _valid_quantity(held.yes_shares)
            or not _valid_quantity(held.no_shares)
        ):
            raise ValueError("invalid or duplicate holding")
        seen_holdings.add(held.market_id)
    return markets


def _terminal_balances(
    scenarios: tuple[SettlementScenario, ...],
    cash: float,
    holdings: Mapping[str, tuple[int, int]],
) -> dict[str, float]:
    return {
        s.name: cash + sum(
            qty[0] if s.resolves_yes[market] else qty[1]
            for market, qty in holdings.items()
        )
        for s in scenarios
    }


def evaluate_trade_plans(
    *,
    scenarios: tuple[SettlementScenario, ...],
    probability_views: tuple[ProbabilityView, ...],
    holdings: tuple[Holding, ...],
    cash: float,
    target_balance: float,
    plans: tuple[TradePlan, ...],
) -> tuple[PlanScore, ...]:
    """Compare complete settlement outcomes against a fixed target balance.

    No independence assumption: each scenario resolves ALL listed markets.
    Rank first by the worst-model improvement in beating the target, then by
    worst-model expected gain. This is a competition objective, not Kelly EV.
    No fill probability, future opponent balance or forecast calibration claim.
    """
    markets = _validate_inputs(
        scenarios, probability_views, holdings, cash, target_balance
    )
    if not plans or len({p.name for p in plans}) != len(plans):
        raise ValueError("plans must have unique names")
    if any(not p.name or not p.trades for p in plans):
        raise ValueError("every plan needs a name and at least one trade")

    before = {h.market_id: (h.yes_shares, h.no_shares) for h in holdings}
    baseline = _terminal_balances(scenarios, cash, before)
    scores: list[PlanScore] = []
    for plan in plans:
        position = before.copy()
        available_cash = cash
        for trade in plan.trades:
            if (
                trade.market_id not in markets
                or trade.action not in ("BUY", "SELL")
                or trade.side not in ("YES", "NO")
                or not _valid_quantity(trade.quantity)
                or trade.quantity == 0
                or not _valid_quantity(trade.displayed_shares)
                or trade.quantity > trade.displayed_shares
                or not math.isfinite(trade.executable_price)
                or not 0 < trade.executable_price < 1
                or not math.isfinite(trade.extra_cost_per_share)
                or trade.extra_cost_per_share < 0
            ):
                raise ValueError(f"{plan.name}: invalid executable trade")
            yes, no = position.get(trade.market_id, (0, 0))
            if trade.action == "BUY":
                available_cash -= trade.quantity * (
                    trade.executable_price + trade.extra_cost_per_share
                )
                if available_cash < -1e-8:
                    raise ValueError(f"{plan.name}: insufficient cash for staged trades")
                if trade.side == "YES":
                    yes += trade.quantity
                else:
                    no += trade.quantity
            else:
                if trade.executable_price < trade.extra_cost_per_share:
                    raise ValueError(f"{plan.name}: negative sell proceeds")
                if trade.side == "YES":
                    if yes < trade.quantity:
                        raise ValueError(f"{plan.name}: unowned YES inventory")
                    yes -= trade.quantity
                else:
                    if no < trade.quantity:
                        raise ValueError(f"{plan.name}: unowned NO inventory")
                    no -= trade.quantity
                available_cash += trade.quantity * (
                    trade.executable_price - trade.extra_cost_per_share
                )
            position[trade.market_id] = (yes, no)
        terminal = _terminal_balances(scenarios, available_cash, position)
        by_model: list[ModelScore] = []
        for model in probability_views:
            weights = model.probabilities
            base_ev = sum(weights[name] * amount for name, amount in baseline.items())
            after_ev = sum(weights[name] * amount for name, amount in terminal.items())
            base_win = sum(
                weights[name] for name, amount in baseline.items()
                if amount > target_balance
            )
            after_win = sum(
                weights[name] for name, amount in terminal.items()
                if amount > target_balance
            )
            by_model.append(ModelScore(
                model.name, base_ev, after_ev, after_ev - base_ev, base_win, after_win
            ))
        scores.append(PlanScore(
            plan.name, tuple(by_model),
            min(s.incremental_expected_balance for s in by_model),
            min(
                s.probability_beat_target - s.baseline_probability_beat_target
                for s in by_model
            ),
            min(terminal.values()), max(terminal.values()), max(available_cash, 0.0),
        ))
    return tuple(sorted(
        scores, key=lambda score: (
            score.min_probability_improvement,
            score.min_expected_gain,
            score.worst_settlement_balance,
        ), reverse=True
    ))
