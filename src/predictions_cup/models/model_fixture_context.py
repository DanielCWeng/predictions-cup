"""Architecture fixture: context-only provider, not production alpha."""

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


class FixtureContextModel:
    spec = ModelSpec(
        model_id="fixture_context",
        model_version="1",
        capability=ModelCapability.CONTEXT_ONLY,
        source_hash=hashlib.sha256(b"fixture_context:1").hexdigest(),
        strategy_family=StrategyFamily.PRED,
        live_eligible=False,
    )

    def evaluate(self, snapshot: CanonicalShadowSnapshot) -> ModelDecision:
        book = snapshot.maker.runtime.book(snapshot.exchange_id)
        if book is None or not book.bids or not book.asks:
            return ModelDecision(
                kind=ModelDecisionKind.NO_TRADE,
                reason="fixture_book_unavailable",
            )
        spread_ticks = max(0, book.asks[0].price_ticks - book.bids[0].price_ticks)
        urgency = min(1.0, spread_ticks / 20.0)
        return ModelDecision(
            kind=ModelDecisionKind.CONTEXT,
            signal_value=float(spread_ticks),
            urgency=urgency,
            reason="fixture_context_only",
        )
