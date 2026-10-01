"""Tiny typed contracts for MODEL-RUNTIME-001.

The contracts deliberately depend on the already accepted immutable SHADOW snapshot
and BUILD-009 hot-path enums.  Model implementations own only model-specific logic;
execution, account truth and central risk remain outside this package.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from predictions_cup.runtime.models import OrderAction, OutcomeSide
from predictions_cup.shadow.contracts import CanonicalShadowSnapshot
from predictions_cup.strategy.core import StrategyFamily


class ModelCapability(StrEnum):
    CONTEXT_ONLY = "CONTEXT_ONLY"
    DIRECTIONAL = "DIRECTIONAL"
    FAIR_VALUE = "FAIR_VALUE"
    EXECUTION = "EXECUTION"
    QUOTING = "QUOTING"


class ModelDecisionKind(StrEnum):
    NO_TRADE = "NO_TRADE"
    CONTEXT = "CONTEXT"
    DIRECTIONAL = "DIRECTIONAL"
    FAIR_VALUE = "FAIR_VALUE"
    EXECUTION = "EXECUTION"
    QUOTING = "QUOTING"


@dataclass(frozen=True, slots=True)
class ModelRiskEnvelope:
    """Small model-local envelope.  Central RISK-002 remains authoritative."""

    base_order_size: int = 1
    max_model_position: int = 1
    max_order_size: int = 1
    max_market_exposure: float = 1.0
    strategy_exposure_budget: float | None = None

    def __post_init__(self) -> None:
        if min(self.base_order_size, self.max_model_position, self.max_order_size) <= 0:
            raise ValueError("model sizing limits must be positive")
        if self.max_market_exposure <= 0.0 or not math.isfinite(self.max_market_exposure):
            raise ValueError("max_market_exposure must be finite and positive")
        if (
            self.strategy_exposure_budget is not None
            and (
                self.strategy_exposure_budget <= 0.0
                or not math.isfinite(self.strategy_exposure_budget)
            )
        ):
            raise ValueError("strategy_exposure_budget must be finite and positive")


@dataclass(frozen=True, slots=True)
class ModelSpec:
    model_id: str
    model_version: str
    capability: ModelCapability
    source_hash: str
    strategy_family: StrategyFamily
    live_eligible: bool = False
    risk: ModelRiskEnvelope = ModelRiskEnvelope()
    requires_trusted_depth: bool = False
    max_input_age_ns: int = 1_000_000_000

    def __post_init__(self) -> None:
        if not self.model_id.strip() or not self.model_version.strip():
            raise ValueError("model identity/version must not be blank")
        if not self.source_hash.strip():
            raise ValueError("model source hash must not be blank")
        if self.strategy_family is StrategyFamily.NO_TRADE:
            raise ValueError("model strategy family cannot be NO_TRADE")
        if self.max_input_age_ns <= 0:
            raise ValueError("max_input_age_ns must be positive")


@dataclass(frozen=True, slots=True)
class ModelDecision:
    kind: ModelDecisionKind
    signal_value: float = 0.0
    fair_value: float | None = None
    confidence: float | None = None
    urgency: float | None = None
    direction: OrderAction | None = None
    outcome_side: OutcomeSide = OutcomeSide.YES
    reason: str = "model_decision"

    def __post_init__(self) -> None:
        if not self.reason.strip():
            raise ValueError("model decision reason must not be blank")
        if not math.isfinite(self.signal_value):
            raise ValueError("model signal must be finite")
        if self.fair_value is not None and (
            not math.isfinite(self.fair_value) or not 0.0 <= self.fair_value <= 1.0
        ):
            raise ValueError("fair_value must be finite within [0,1]")
        for name, value in (("confidence", self.confidence), ("urgency", self.urgency)):
            if value is not None and (
                not math.isfinite(value) or not 0.0 <= value <= 1.0
            ):
                raise ValueError(f"{name} must be finite within [0,1]")
        directional = {
            ModelDecisionKind.DIRECTIONAL,
            ModelDecisionKind.EXECUTION,
            ModelDecisionKind.QUOTING,
        }
        if self.kind in directional and self.direction is None:
            raise ValueError("directional/economic model decisions require direction")
        if self.kind not in directional and self.direction is not None:
            raise ValueError("non-directional model decisions cannot set direction")


class ModelProvider(Protocol):
    spec: ModelSpec

    def evaluate(self, snapshot: CanonicalShadowSnapshot) -> ModelDecision: ...
