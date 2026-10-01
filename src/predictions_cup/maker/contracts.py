"""Tiny versionable contracts for the MAKE-001 hot path.

Plugins are pure calculations over explicit snapshots. They must not perform I/O,
read hidden global state, or mutate execution/risk state.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol

from predictions_cup.runtime.models import RuntimeSnapshot


class GateMode(StrEnum):
    NORMAL = "NORMAL"
    WIDER = "WIDER"
    BID_ONLY = "BID_ONLY"
    ASK_ONLY = "ASK_ONLY"
    HOLD = "HOLD"
    CANCEL = "CANCEL"
    SUSPEND = "SUSPEND"
    NO_TRADE = "NO_TRADE"


class QuoteSide(StrEnum):
    BID = "BID"
    ASK = "ASK"


@dataclass(frozen=True, slots=True)
class ExternalQuoteState:
    """Observable external probability book for one mapped Polymarket token."""

    token_id: str
    best_bid: float | None
    best_ask: float | None
    observed_monotonic_ns: int
    trusted: bool
    source_version: str
    observed_at: datetime | None = None

    def __post_init__(self) -> None:
        if not self.token_id.strip() or not self.source_version.strip():
            raise ValueError("external quote identity/version must not be blank")
        if self.observed_monotonic_ns < 0:
            raise ValueError("external quote timestamp must be non-negative")
        if (
            self.observed_at is not None
            and (
                self.observed_at.tzinfo is None
                or self.observed_at.utcoffset() is None
            )
        ):
            raise ValueError("external quote observed_at must be timezone-aware")
        for value in (self.best_bid, self.best_ask):
            if value is not None and (not math.isfinite(value) or not 0.0 <= value <= 1.0):
                raise ValueError("external quote probability must be finite within [0,1]")


@dataclass(frozen=True, slots=True)
class MakerMarketSnapshot:
    """All state a maker/plugin may inspect for one decision."""

    runtime: RuntimeSnapshot
    exchange_id: str
    market_id: str
    tournament_id: str
    now_monotonic_ns: int
    sig_bbo_observed_ns: int
    sig_bbo_trusted: bool
    sig_depth_observed_ns: int | None
    sig_depth_trusted: bool
    account_observed_ns: int
    inventory_observed_ns: int
    external_quotes: Mapping[str, ExternalQuoteState]
    volatility: float | None = None
    external_feed_observed_ns: int | None = None

    def __post_init__(self) -> None:
        if not self.exchange_id.strip() or not self.market_id.strip():
            raise ValueError("maker market identity must not be blank")
        if not self.tournament_id.strip():
            raise ValueError("maker tournament identity must not be blank")
        timestamps = (
            self.now_monotonic_ns,
            self.sig_bbo_observed_ns,
            self.account_observed_ns,
            self.inventory_observed_ns,
        )
        if min(timestamps) < 0:
            raise ValueError("maker timestamps must be non-negative")
        if self.sig_depth_observed_ns is not None and self.sig_depth_observed_ns < 0:
            raise ValueError("depth timestamp must be non-negative")
        if (
            self.external_feed_observed_ns is not None
            and self.external_feed_observed_ns < 0
        ):
            raise ValueError("external feed timestamp must be non-negative")
        if self.volatility is not None and (
            not math.isfinite(self.volatility) or self.volatility < 0.0
        ):
            raise ValueError("volatility must be finite and non-negative")


@dataclass(frozen=True, slots=True)
class FairValueResult:
    value: float | None
    uncertainty: float
    confidence: float
    observed_monotonic_ns: int
    trusted: bool
    source_id: str
    source_version: str
    mapping_class: str
    reason: str

    @property
    def usable(self) -> bool:
        return (
            self.trusted
            and self.value is not None
            and math.isfinite(self.value)
            and 0.0 <= self.value <= 1.0
            and math.isfinite(self.uncertainty)
            and self.uncertainty >= 0.0
            and math.isfinite(self.confidence)
            and 0.0 <= self.confidence <= 1.0
        )


@dataclass(frozen=True, slots=True)
class PredictiveAdjustment:
    probability_shift: float
    confidence: float
    observed_monotonic_ns: int
    trusted: bool
    model_id: str
    version: str

    def __post_init__(self) -> None:
        if not math.isfinite(self.probability_shift):
            raise ValueError("predictive shift must be finite")
        if not math.isfinite(self.confidence) or not 0.0 <= self.confidence <= 1.0:
            raise ValueError("predictive confidence must be within [0,1]")


@dataclass(frozen=True, slots=True)
class ToxicityEstimate:
    update_hazard: float
    adverse_selection: float
    observed_monotonic_ns: int
    trusted: bool
    model_id: str
    version: str

    def __post_init__(self) -> None:
        for value in (self.update_hazard, self.adverse_selection):
            if not math.isfinite(value) or not 0.0 <= value <= 1.0:
                raise ValueError("toxicity values must be finite within [0,1]")


@dataclass(frozen=True, slots=True)
class GateDecision:
    mode: GateMode
    reason: str
    spread_multiplier: float = 1.0
    size_multiplier: float = 1.0

    def __post_init__(self) -> None:
        if not math.isfinite(self.spread_multiplier) or self.spread_multiplier < 1.0:
            raise ValueError("spread multiplier must be finite and >= 1")
        if not math.isfinite(self.size_multiplier) or not 0.0 <= self.size_multiplier <= 1.0:
            raise ValueError("size multiplier must be finite within [0,1]")


@dataclass(frozen=True, slots=True)
class QuoteContext:
    snapshot: MakerMarketSnapshot
    raw_fair_value: FairValueResult
    adjusted_fair_value: float
    prediction: PredictiveAdjustment
    toxicity: ToxicityEstimate
    signed_inventory: float
    max_abs_inventory: float
    reservation_price: float = 0.0
    half_spread: float = 0.0


@dataclass(frozen=True, slots=True)
class QuoteSizes:
    bid: int
    ask: int


@dataclass(frozen=True, slots=True)
class DesiredQuote:
    exchange_id: str
    market_id: str
    tournament_id: str
    bid_ticks: int | None
    ask_ticks: int | None
    bid_size: int
    ask_size: int

    def __post_init__(self) -> None:
        if self.bid_ticks is None and self.bid_size != 0:
            raise ValueError("bid size requires bid price")
        if self.ask_ticks is None and self.ask_size != 0:
            raise ValueError("ask size requires ask price")
        if self.bid_ticks is not None and not 1 <= self.bid_ticks <= 199:
            raise ValueError("bid ticks outside SIG limit range")
        if self.ask_ticks is not None and not 1 <= self.ask_ticks <= 199:
            raise ValueError("ask ticks outside SIG limit range")
        if (
            self.bid_ticks is not None
            and self.ask_ticks is not None
            and self.bid_ticks >= self.ask_ticks
        ):
            raise ValueError("maker quote must have bid < ask")
        if self.bid_size < 0 or self.ask_size < 0:
            raise ValueError("maker sizes must be non-negative")


@dataclass(frozen=True, slots=True)
class MakerTrace:
    strategy_id: str
    strategy_version: str
    fv_source: str
    fv_version: str
    raw_fv: float | None
    predictive_shift: float
    adjusted_fv: float | None
    uncertainty: float
    confidence: float
    update_hazard: float
    adverse_selection: float
    signed_inventory: float
    reservation_price: float | None
    half_spread: float | None
    desired_bid_ticks: int | None
    desired_ask_ticks: int | None
    desired_bid_size: int
    desired_ask_size: int
    gate_mode: GateMode
    reason: str
    decision_monotonic_ns: int


@dataclass(frozen=True, slots=True)
class MakerDecision:
    desired: DesiredQuote | None
    gate: GateDecision
    trace: MakerTrace
    next_recheck_monotonic_ns: int | None = None


class FairValueProvider(Protocol):
    provider_id: str
    version: str

    def fair_value(
        self,
        snapshot: MakerMarketSnapshot,
    ) -> FairValueResult: ...


class PredictiveAdjuster(Protocol):
    model_id: str
    version: str

    def adjust(
        self,
        snapshot: MakerMarketSnapshot,
        fair_value: FairValueResult,
    ) -> PredictiveAdjustment: ...


class ToxicityProvider(Protocol):
    model_id: str
    version: str

    def estimate(
        self,
        snapshot: MakerMarketSnapshot,
        fair_value: FairValueResult,
    ) -> ToxicityEstimate: ...


class InventoryModel(Protocol):
    model_id: str
    version: str

    def reservation_price(self, context: QuoteContext) -> float: ...


class SpreadPolicy(Protocol):
    policy_id: str
    version: str

    def half_spread(self, context: QuoteContext) -> float: ...


class SizePolicy(Protocol):
    policy_id: str
    version: str

    def sizes(self, context: QuoteContext) -> QuoteSizes: ...


class EligibilityPolicy(Protocol):
    policy_id: str
    version: str

    def gate(self, context: QuoteContext) -> GateDecision: ...

    def next_recheck_monotonic_ns(
        self,
        context: QuoteContext,
    ) -> int | None: ...
