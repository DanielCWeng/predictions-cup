"""Architecture fixture: deterministic direction provider, not production alpha."""

from __future__ import annotations

import hashlib

from predictions_cup.models.contracts import (
    ModelCapability,
    ModelDecision,
    ModelDecisionKind,
    ModelRiskEnvelope,
    ModelSpec,
)
from predictions_cup.runtime.models import OrderAction
from predictions_cup.shadow.contracts import CanonicalShadowSnapshot
from predictions_cup.strategy.core import StrategyFamily


class FixtureDirectionModel:
    spec = ModelSpec(
        model_id="fixture_direction",
        model_version="1",
        capability=ModelCapability.DIRECTIONAL,
        source_hash=hashlib.sha256(b"fixture_direction:1").hexdigest(),
        strategy_family=StrategyFamily.PRED,
        live_eligible=False,
        risk=ModelRiskEnvelope(
            base_order_size=1,
            max_model_position=1,
            max_order_size=1,
            max_market_exposure=1.0,
        ),
    )

    def evaluate(self, snapshot: CanonicalShadowSnapshot) -> ModelDecision:
        book = snapshot.maker.runtime.book(snapshot.exchange_id)
        if book is None or not book.bids or not book.asks:
            return ModelDecision(
                kind=ModelDecisionKind.NO_TRADE,
                reason="fixture_book_unavailable",
            )
        midpoint_ticks = (book.bids[0].price_ticks + book.asks[0].price_ticks) / 2.0
        direction = (
            OrderAction.BUY if midpoint_ticks < 100.0 else OrderAction.SELL
        )
        return ModelDecision(
            kind=ModelDecisionKind.DIRECTIONAL,
            signal_value=(100.0 - midpoint_ticks) / 100.0,
            fair_value=0.5,
            direction=direction,
            reason="fixture_direction_only",
        )
