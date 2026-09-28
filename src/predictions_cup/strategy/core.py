"""Pure strategy contract and fixed BUILD-009 family registry."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum

from predictions_cup.runtime.models import OrderAction, OutcomeSide, RuntimeSnapshot
from predictions_cup.strategy.kernels import KernelRegistry


class StrategyFamily(StrEnum):
    FV_TAKE = "FV-TAKE"
    MAKE = "MAKE"
    STRUCT = "STRUCT"
    PRED = "PRED"
    EVENT = "EVENT"
    NO_TRADE = "NO_TRADE"


@dataclass(frozen=True, slots=True)
class CandidateLeg:
    exchange_id: str
    market_id: str
    tournament_id: str
    outcome_side: OutcomeSide
    action: OrderAction
    quantity: int
    limit_price_ticks: int | None


@dataclass(frozen=True, slots=True)
class Opportunity:
    family: StrategyFamily
    strategy_id: str
    legs: tuple[CandidateLeg, ...]
    gross_edge: float
    fair_value: float | None
    decision_observation_ns: int
    requires_trusted_depth: bool = False
    atomic: bool = False
    relationship_id: str | None = None

    def __post_init__(self) -> None:
        if self.family is StrategyFamily.NO_TRADE:
            raise ValueError("Opportunity cannot use NO_TRADE family")
        if not self.legs:
            raise ValueError("Opportunity requires at least one candidate leg")
        if self.relationship_id is not None and self.family is not StrategyFamily.STRUCT:
            raise ValueError("relationship identity is valid only for STRUCT opportunities")


@dataclass(frozen=True, slots=True)
class NoTrade:
    reason: str
    family: StrategyFamily = StrategyFamily.NO_TRADE


StrategyResult = Opportunity | NoTrade
StrategyFn = Callable[
    [RuntimeSnapshot, KernelRegistry, Mapping[str, float]],
    StrategyResult,
]


class StrategyRegistry:
    def __init__(self) -> None:
        self._strategies: dict[str, StrategyFn] = {}

    def register(self, strategy_id: str, strategy: StrategyFn) -> None:
        if not strategy_id.strip():
            raise ValueError("strategy_id must not be blank")
        if strategy_id in self._strategies:
            raise ValueError(f"strategy already registered: {strategy_id}")
        self._strategies[strategy_id] = strategy

    def evaluate(
        self,
        strategy_id: str,
        snapshot: RuntimeSnapshot,
        kernels: KernelRegistry,
        config: Mapping[str, float],
    ) -> StrategyResult:
        return self._strategies[strategy_id](snapshot, kernels, config)


def no_trade_strategy(
    snapshot: RuntimeSnapshot,
    kernels: KernelRegistry,
    config: Mapping[str, float],
) -> StrategyResult:
    del snapshot, kernels, config
    return NoTrade(reason="explicit_no_trade")


def synthetic_threshold_strategy(
    snapshot: RuntimeSnapshot,
    kernels: KernelRegistry,
    config: Mapping[str, float],
) -> StrategyResult:
    """Deterministic acceptance fixture only; this is not an alpha strategy."""
    del kernels
    if not snapshot.markets:
        return NoTrade(reason="no_market")
    threshold = config.get("threshold", 0.01)
    edge = config.get("edge", 0.0)
    if edge <= threshold:
        return NoTrade(reason="synthetic_below_threshold")
    market = snapshot.markets[0]
    if not market.exchange_ids:
        return NoTrade(reason="no_exchange")
    return Opportunity(
        family=StrategyFamily.FV_TAKE,
        strategy_id="synthetic-threshold",
        legs=(
            CandidateLeg(
                exchange_id=market.exchange_ids[0],
                market_id=market.market_id,
                tournament_id=market.tournament_id,
                outcome_side=OutcomeSide.YES,
                action=OrderAction.BUY,
                quantity=1,
                limit_price_ticks=100,
            ),
        ),
        gross_edge=edge,
        fair_value=0.51,
        decision_observation_ns=snapshot.observation_monotonic_ns,
    )
