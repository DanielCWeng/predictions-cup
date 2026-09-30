"""Registered scorer plugins for LIVE-LEARN-001."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Protocol

from predictions_cup.live_learn.contracts import ExecutionEvidence, MarketEvidence
from predictions_cup.shadow.contracts import CandidateDecision


@dataclass(frozen=True, slots=True)
class ScoreContext:
    decision: CandidateDecision
    initial: MarketEvidence
    future: MarketEvidence
    execution: ExecutionEvidence


@dataclass(frozen=True, slots=True)
class ScoreResult:
    scorer_id: str
    scorer_version: str
    metrics: dict[str, float]
    component_status: dict[str, str]


class OutcomeScorer(Protocol):
    scorer_id: str
    version: str

    def supports(self, decision: CandidateDecision) -> bool: ...

    def score(self, context: ScoreContext) -> ScoreResult: ...


class FairValueScorer:
    """Score an explicit fair_value field; never reinterpret candidate.score."""

    scorer_id = "fair-value"
    version = "v1"

    def supports(self, decision: CandidateDecision) -> bool:
        return decision.fair_value is not None

    def score(self, context: ScoreContext) -> ScoreResult:
        fair_value = context.decision.fair_value
        assert fair_value is not None
        initial = context.initial.midpoint
        future = context.future.midpoint
        if initial is None or future is None:
            return ScoreResult(
                scorer_id=self.scorer_id,
                scorer_version=self.version,
                metrics={},
                component_status={"forecast": "SOURCE_UNAVAILABLE"},
            )
        initial_error = abs(fair_value - initial)
        future_error = abs(fair_value - future)
        return ScoreResult(
            scorer_id=self.scorer_id,
            scorer_version=self.version,
            metrics={
                "forecast_error": future_error,
                "forecast_signed_error": fair_value - future,
                "initial_fv_distance": initial_error,
                "signal_decay": initial_error - future_error,
            },
            component_status={"forecast": "MATURED_SCORED"},
        )


class DirectPmResidualScorer:
    """Candidate-specific direction semantics for the direct PM reference plugin."""

    scorer_id = "direct-pm-residual"
    version = "v1"

    def supports(self, decision: CandidateDecision) -> bool:
        return (
            decision.candidate_id == "direct-pm-reference"
            and decision.fair_value is not None
        )

    def score(self, context: ScoreContext) -> ScoreResult:
        base = FairValueScorer().score(context)
        metrics = dict(base.metrics)
        statuses = dict(base.component_status)
        initial = context.initial.midpoint
        future = context.future.midpoint
        direction = context.decision.direction
        if initial is not None and future is not None:
            move = future - initial
            if direction == "PM_ABOVE_SIG":
                metrics["direction_accuracy"] = 1.0 if move > 0.0 else 0.0
                statuses["direction"] = "MATURED_SCORED"
            elif direction == "PM_BELOW_SIG":
                metrics["direction_accuracy"] = 1.0 if move < 0.0 else 0.0
                statuses["direction"] = "MATURED_SCORED"
            elif direction == "ALIGNED":
                statuses["direction"] = "UNSUPPORTED_SCORE_SEMANTICS"
        return ScoreResult(
            scorer_id=self.scorer_id,
            scorer_version=self.version,
            metrics=metrics,
            component_status=statuses,
        )


class QuoteEconomicsScorer:
    """Score quote/fill economics only from explicit execution evidence."""

    scorer_id = "quote-economics"
    version = "v1"

    def supports(self, decision: CandidateDecision) -> bool:
        return decision.quote_intent is not None

    def score(self, context: ScoreContext) -> ScoreResult:
        metrics: dict[str, float] = {}
        statuses: dict[str, str] = {}
        if context.decision.fair_value is not None:
            fv_result = FairValueScorer().score(context)
            metrics.update(fv_result.metrics)
            statuses.update(fv_result.component_status)

        execution = context.execution
        if not execution.supported:
            statuses["execution"] = "EXECUTION_EVIDENCE_UNAVAILABLE"
            return ScoreResult(
                scorer_id=self.scorer_id,
                scorer_version=self.version,
                metrics=metrics,
                component_status=statuses,
            )

        total_filled = 0.0
        for fill in execution.fills:
            total_filled += fill.quantity
        fill_rate = min(1.0, total_filled / execution.planned_quantity)
        metrics["fill_rate"] = fill_rate
        metrics["partial_fill_rate"] = (
            1.0 if 0.0 < total_filled < execution.planned_quantity else 0.0
        )
        statuses["execution"] = "MATURED_SCORED"
        if not execution.fills:
            return ScoreResult(
                scorer_id=self.scorer_id,
                scorer_version=self.version,
                metrics=metrics,
                component_status=statuses,
            )

        priced = tuple(fill for fill in execution.fills if fill.price is not None)
        if priced:
            weight = 0.0
            weighted_fill_price = 0.0
            for fill in priced:
                assert fill.price is not None
                weight += fill.quantity
                weighted_fill_price += fill.quantity * fill.price
            avg_fill = weighted_fill_price / weight
            metrics["avg_fill_price"] = avg_fill
            initial_mid = context.initial.midpoint
            future_mid = context.future.midpoint
            if initial_mid is not None and future_mid is not None:
                signed_capture = 0.0
                signed_post_fill = 0.0
                adverse = 0.0
                for fill in priced:
                    assert fill.price is not None
                    sign = 1.0 if fill.action == "buy" else -1.0
                    signed_capture += (
                        fill.quantity * sign * (initial_mid - fill.price)
                    )
                    signed_post_fill += (
                        fill.quantity * sign * (future_mid - fill.price)
                    )
                    adverse += (
                        fill.quantity
                        * max(0.0, -sign * (future_mid - fill.price))
                    )
                metrics["spread_capture"] = signed_capture / weight
                metrics["realised_spread"] = signed_post_fill / weight
                metrics["post_fill_markout"] = signed_post_fill / weight
                metrics["adverse_selection"] = adverse / weight

        fill_times = tuple(
            fill.filled_at
            for fill in execution.fills
            if fill.filled_at is not None
        )
        if fill_times:
            first_fill = min(fill_times)
            latency = (
                first_fill - context.decision.observed_at
            ).total_seconds()
            if math.isfinite(latency) and latency >= 0.0:
                metrics["fill_latency_seconds"] = latency
        return ScoreResult(
            scorer_id=self.scorer_id,
            scorer_version=self.version,
            metrics=metrics,
            component_status=statuses,
        )


class ScorerRegistry:
    """Ordered scorer registry. Candidate score semantics are never guessed."""

    def __init__(self, scorers: tuple[OutcomeScorer, ...]) -> None:
        if not scorers:
            raise ValueError("at least one scorer must be registered")
        ids = tuple((scorer.scorer_id, scorer.version) for scorer in scorers)
        if len(ids) != len(set(ids)):
            raise ValueError("scorer id/version pairs must be unique")
        self._scorers = scorers

    @classmethod
    def default(cls) -> ScorerRegistry:
        return cls(
            (
                QuoteEconomicsScorer(),
                DirectPmResidualScorer(),
                FairValueScorer(),
            )
        )

    def select(self, decision: CandidateDecision) -> OutcomeScorer | None:
        for scorer in self._scorers:
            if scorer.supports(decision):
                return scorer
        return None
