"""Architecture fixture: explicit no-trade model, not production alpha."""

from __future__ import annotations

import hashlib

from predictions_cup.models.contracts import (
    ModelCapability,
    ModelDecision,
    ModelDecisionKind,
    ModelSpec,
)
from predictions_cup.shadow.contracts import CanonicalShadowSnapshot
from predictions_cup.strategy.core import StrategyFamily


class NoTradeModel:
    spec = ModelSpec(
        model_id="fixture_no_trade",
        model_version="1",
        capability=ModelCapability.CONTEXT_ONLY,
        source_hash=hashlib.sha256(b"fixture_no_trade:1").hexdigest(),
        strategy_family=StrategyFamily.PRED,
        live_eligible=False,
    )

    def evaluate(self, snapshot: CanonicalShadowSnapshot) -> ModelDecision:
        del snapshot
        return ModelDecision(
            kind=ModelDecisionKind.NO_TRADE,
            reason="fixture_explicit_no_trade",
        )
