"""Canonical audit/learning decision primitive."""

from predictions_cup.models.common import (
    AwareDateTime,
    CanonicalModel,
    Identifier,
    NonBlankStr,
)


class DecisionRecord(CanonicalModel):
    decision_id: Identifier
    timestamp: AwareDateTime
    component: NonBlankStr
    exchange_id: Identifier | None = None
    market_id: Identifier | None = None
    action: NonBlankStr
    reason_code: NonBlankStr
