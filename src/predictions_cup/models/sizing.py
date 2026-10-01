"""Central bounded sizing for MODEL-RUNTIME-001.

This layer converts a model signal into a small requested economic quantity.  It
does not replace RISK-002: the resulting Opportunity must still be independently
approved by central Risk before BUILD-009 can create an execution plan.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from predictions_cup.models.contracts import (
    ModelCapability,
    ModelDecision,
    ModelDecisionKind,
    ModelSpec,
)
from predictions_cup.runtime.models import OrderAction
from predictions_cup.shadow.contracts import CanonicalShadowSnapshot


@dataclass(frozen=True, slots=True)
class SizingResult:
    quantity: int
    limit_price_ticks: int | None
    reason: str

    @property
    def tradeable(self) -> bool:
        return self.quantity > 0 and self.limit_price_ticks is not None


def size_model_decision(
    *,
    spec: ModelSpec,
    decision: ModelDecision,
    snapshot: CanonicalShadowSnapshot,
    available_capital: float | None = None,
) -> SizingResult:
    """Apply only model-local bounded sizing; global limits remain RISK-002's job."""

    if spec.capability is ModelCapability.CONTEXT_ONLY:
        return SizingResult(0, None, "context_only_model")
    if decision.kind in {ModelDecisionKind.NO_TRADE, ModelDecisionKind.CONTEXT}:
        return SizingResult(0, None, "model_no_trade")
    if decision.direction is None:
        return SizingResult(0, None, "direction_missing")

    runtime = snapshot.maker.runtime
    market = runtime.market(snapshot.market_id)
    book = runtime.book(snapshot.exchange_id)
    if market is None or book is None:
        return SizingResult(0, None, "market_or_book_missing")
    if market.status != "open" or not market.mapping_accepted or not market.tradeable:
        return SizingResult(0, None, "market_not_tradeable")
    if spec.requires_trusted_depth and not book.trusted_depth:
        return SizingResult(0, None, "trusted_depth_required")
    if not book.bids or not book.asks:
        return SizingResult(0, None, "empty_book")

    uncertain = sum(
        order.reserved_exposure
        for order in runtime.portfolio.orders
        if order.market_id == snapshot.market_id and order.uncertain
    )
    if uncertain > 0.0:
        return SizingResult(0, None, "uncertain_market_exposure")

    signed_inventory = sum(
        position.signed_quantity
        for position in runtime.portfolio.positions
        if position.exchange_id == snapshot.exchange_id
    )
    market_exposure = sum(
        abs(position.gross_exposure)
        for position in runtime.portfolio.positions
        if position.market_id == snapshot.market_id
    )
    market_exposure += sum(
        order.reserved_exposure
        for order in runtime.portfolio.orders
        if order.market_id == snapshot.market_id and (order.open or order.uncertain)
    )

    envelope = spec.risk
    quantity = min(
        envelope.base_order_size,
        envelope.max_order_size,
        max(0, int(math.floor(envelope.max_model_position - abs(signed_inventory)))),
        max(0, int(math.floor(envelope.max_market_exposure - market_exposure))),
    )
    if envelope.strategy_exposure_budget is not None:
        quantity = min(
            quantity,
            max(
                0,
                int(math.floor(envelope.strategy_exposure_budget - market_exposure)),
            ),
        )
    if available_capital is not None:
        if not math.isfinite(available_capital) or available_capital <= 0.0:
            return SizingResult(0, None, "available_budget_exhausted")
        quantity = min(quantity, int(math.floor(available_capital)))

    if quantity <= 0:
        return SizingResult(0, None, "model_risk_envelope_exhausted")

    limit_ticks = (
        book.asks[0].price_ticks
        if decision.direction is OrderAction.BUY
        else book.bids[0].price_ticks
    )
    if not 1 <= limit_ticks <= 199:
        return SizingResult(0, None, "invalid_book_tick")
    return SizingResult(quantity, limit_ticks, "sized")
