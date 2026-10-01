"""Canonical immutable state and decision contracts for SHADOW-002."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Protocol

from predictions_cup.maker.contracts import MakerMarketSnapshot


class DecisionStatus(StrEnum):
    OK = "OK"
    ABSTAIN = "ABSTAIN"
    NOT_READY = "NOT_READY"
    STALE_INPUT = "STALE_INPUT"
    UNTRUSTED_INPUT = "UNTRUSTED_INPUT"
    INVALID_OUTPUT = "INVALID_OUTPUT"
    TIMEOUT = "TIMEOUT"
    EXCEPTION = "EXCEPTION"
    DISABLED = "DISABLED"


@dataclass(frozen=True, slots=True)
class CandidateOutput:
    """Candidate-owned economic output before SHADOW adds durable provenance/timing."""

    status: DecisionStatus
    fair_value: float | None = None
    lower_bound: float | None = None
    upper_bound: float | None = None
    confidence: float | None = None
    direction: str | None = None
    score: float | None = None
    action_intent: str | None = None
    quote_intent: Mapping[str, object] | None = None
    abstain_reason: str | None = None
    quality_flags: tuple[str, ...] = ()
    candidate_payload: Mapping[str, object] = field(default_factory=dict)


class ShadowCandidate(Protocol):
    candidate_id: str
    candidate_version: str
    strategy_family: str

    def evaluate(self, snapshot: CanonicalShadowSnapshot) -> CandidateOutput: ...


@dataclass(frozen=True, slots=True)
class CanonicalShadowSnapshot:
    """One immutable observable decision boundary delivered identically to all candidates."""

    snapshot_id: str
    observed_at: datetime
    observed_monotonic_ns: int
    maker: MakerMarketSnapshot
    mapping_version: str
    source_revision: str
    source_provenance: tuple[tuple[str, str], ...] = ()
    schema_version: int = 1

    @classmethod
    def freeze(
        cls,
        maker: MakerMarketSnapshot,
        *,
        observed_at: datetime,
        mapping_version: str,
        source_revision: str,
        source_provenance: Mapping[str, str] | None = None,
    ) -> CanonicalShadowSnapshot:
        if observed_at.tzinfo is None or observed_at.utcoffset() is None:
            raise ValueError("observed_at must be timezone-aware")
        if not mapping_version.strip() or not source_revision.strip():
            raise ValueError("mapping/source revision must not be blank")
        if maker.now_monotonic_ns < 0:
            raise ValueError("observable monotonic timestamp must be non-negative")

        normalized_at = observed_at.astimezone(UTC)
        provenance = tuple(sorted((source_provenance or {}).items()))
        frozen_maker = replace(
            maker,
            external_quotes=MappingProxyType(dict(maker.external_quotes)),
        )
        fingerprint = {
            "schema_version": 1,
            "observed_at": normalized_at.isoformat(),
            "observed_monotonic_ns": maker.now_monotonic_ns,
            "mapping_version": mapping_version,
            "source_revision": source_revision,
            "source_provenance": provenance,
            "maker": maker_snapshot_record(frozen_maker),
        }
        raw = json.dumps(
            fingerprint,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        snapshot_id = "sh2_" + hashlib.sha256(raw).hexdigest()[:32]
        return cls(
            snapshot_id=snapshot_id,
            observed_at=normalized_at,
            observed_monotonic_ns=maker.now_monotonic_ns,
            maker=frozen_maker,
            mapping_version=mapping_version,
            source_revision=source_revision,
            source_provenance=provenance,
        )

    @classmethod
    def restore(
        cls,
        *,
        snapshot_id: str,
        maker: MakerMarketSnapshot,
        observed_at: datetime,
        mapping_version: str,
        source_revision: str,
        source_provenance: Mapping[str, str] | None = None,
    ) -> CanonicalShadowSnapshot:
        restored = cls.freeze(
            maker,
            observed_at=observed_at,
            mapping_version=mapping_version,
            source_revision=source_revision,
            source_provenance=source_provenance,
        )
        if restored.snapshot_id != snapshot_id:
            raise ValueError("persisted snapshot fingerprint mismatch")
        return restored

    @property
    def tournament_id(self) -> str:
        return self.maker.tournament_id

    @property
    def exchange_id(self) -> str:
        return self.maker.exchange_id

    @property
    def market_id(self) -> str:
        return self.maker.market_id


@dataclass(frozen=True, slots=True)
class CandidateDecision:
    """Durable, forward-compatible SHADOW decision envelope."""

    decision_id: str
    candidate_id: str
    candidate_version: str
    strategy_family: str
    observed_at: datetime
    monotonic_time: int
    tournament_id: str
    exchange_id: str
    market_id: str
    input_snapshot_id: str
    mapping_version: str
    fair_value: float | None
    lower_bound: float | None
    upper_bound: float | None
    confidence: float | None
    direction: str | None
    score: float | None
    action_intent: str | None
    quote_intent: Mapping[str, object] | None
    decision_status: DecisionStatus
    abstain_reason: str | None
    quality_flags: tuple[str, ...]
    compute_started_at: int
    compute_finished_at: int
    compute_latency_ns: int
    candidate_payload: Mapping[str, object]
    schema_version: int = 1

    def __post_init__(self) -> None:
        identities = (
            self.decision_id,
            self.candidate_id,
            self.candidate_version,
            self.strategy_family,
            self.tournament_id,
            self.exchange_id,
            self.market_id,
            self.input_snapshot_id,
            self.mapping_version,
        )
        if any(not value.strip() for value in identities):
            raise ValueError("decision identity/provenance fields must not be blank")
        if self.observed_at.tzinfo is None or self.observed_at.utcoffset() is None:
            raise ValueError("decision observed_at must be timezone-aware")
        if min(
            self.monotonic_time,
            self.compute_started_at,
            self.compute_finished_at,
            self.compute_latency_ns,
        ) < 0:
            raise ValueError("decision monotonic/timing values must be non-negative")
        if self.compute_finished_at < self.compute_started_at:
            raise ValueError("compute finish cannot precede start")
        if self.compute_latency_ns != self.compute_finished_at - self.compute_started_at:
            raise ValueError("compute latency must match start/finish")

        for name, value in (
            ("fair_value", self.fair_value),
            ("lower_bound", self.lower_bound),
            ("upper_bound", self.upper_bound),
            ("confidence", self.confidence),
        ):
            if value is not None and (
                not math.isfinite(value) or not 0.0 <= value <= 1.0
            ):
                raise ValueError(f"{name} must be finite within [0,1]")
        if (
            self.lower_bound is not None
            and self.upper_bound is not None
            and self.lower_bound > self.upper_bound
        ):
            raise ValueError("lower_bound cannot exceed upper_bound")
        if (
            self.fair_value is not None
            and self.lower_bound is not None
            and self.fair_value < self.lower_bound
        ):
            raise ValueError("fair_value cannot lie below lower_bound")
        if (
            self.fair_value is not None
            and self.upper_bound is not None
            and self.fair_value > self.upper_bound
        ):
            raise ValueError("fair_value cannot lie above upper_bound")
        if self.score is not None and not math.isfinite(self.score):
            raise ValueError("score must be finite")
        if any(not flag.strip() for flag in self.quality_flags):
            raise ValueError("quality flags must not be blank")
        _ensure_json_mapping(self.candidate_payload, "candidate_payload")
        if self.quote_intent is not None:
            _ensure_json_mapping(self.quote_intent, "quote_intent")


def decision_from_output(
    *,
    snapshot: CanonicalShadowSnapshot,
    candidate: ShadowCandidate,
    output: CandidateOutput,
    compute_started_at: int,
    compute_finished_at: int,
) -> CandidateDecision:
    return CandidateDecision(
        decision_id=decision_id_for(
            snapshot.snapshot_id,
            candidate.candidate_id,
            candidate.candidate_version,
        ),
        candidate_id=candidate.candidate_id,
        candidate_version=candidate.candidate_version,
        strategy_family=candidate.strategy_family,
        observed_at=snapshot.observed_at,
        monotonic_time=snapshot.observed_monotonic_ns,
        tournament_id=snapshot.tournament_id,
        exchange_id=snapshot.exchange_id,
        market_id=snapshot.market_id,
        input_snapshot_id=snapshot.snapshot_id,
        mapping_version=snapshot.mapping_version,
        fair_value=output.fair_value,
        lower_bound=output.lower_bound,
        upper_bound=output.upper_bound,
        confidence=output.confidence,
        direction=output.direction,
        score=output.score,
        action_intent=output.action_intent,
        quote_intent=output.quote_intent,
        decision_status=output.status,
        abstain_reason=output.abstain_reason,
        quality_flags=output.quality_flags,
        compute_started_at=compute_started_at,
        compute_finished_at=compute_finished_at,
        compute_latency_ns=compute_finished_at - compute_started_at,
        candidate_payload=output.candidate_payload,
    )


def failure_output(
    status: DecisionStatus,
    reason: str,
    *,
    detail: str | None = None,
) -> CandidateOutput:
    payload: dict[str, object] = {}
    if detail is not None:
        payload["diagnostic"] = detail
    return CandidateOutput(
        status=status,
        abstain_reason=reason,
        quality_flags=(f"shadow:{status.value.lower()}",),
        candidate_payload=payload,
    )


def decision_id_for(snapshot_id: str, candidate_id: str, candidate_version: str) -> str:
    raw = f"{snapshot_id}|{candidate_id}|{candidate_version}".encode()
    return "shd_" + hashlib.sha256(raw).hexdigest()[:32]


def maker_snapshot_record(snapshot: MakerMarketSnapshot) -> dict[str, object]:
    runtime = snapshot.runtime
    return {
        "exchange_id": snapshot.exchange_id,
        "market_id": snapshot.market_id,
        "tournament_id": snapshot.tournament_id,
        "now_monotonic_ns": snapshot.now_monotonic_ns,
        "sig_bbo_observed_ns": snapshot.sig_bbo_observed_ns,
        "sig_bbo_trusted": snapshot.sig_bbo_trusted,
        "sig_depth_observed_ns": snapshot.sig_depth_observed_ns,
        "sig_depth_trusted": snapshot.sig_depth_trusted,
        "account_observed_ns": snapshot.account_observed_ns,
        "inventory_observed_ns": snapshot.inventory_observed_ns,
        "volatility": snapshot.volatility,
        "external_quotes": {
            token_id: {
                "token_id": quote.token_id,
                "best_bid": quote.best_bid,
                "best_ask": quote.best_ask,
                "best_bid_size": quote.best_bid_size,
                "best_ask_size": quote.best_ask_size,
                "observed_monotonic_ns": quote.observed_monotonic_ns,
                "observed_at": (
                    None
                    if quote.observed_at is None
                    else quote.observed_at.isoformat()
                ),
                "trusted": quote.trusted,
                "source_version": quote.source_version,
            }
            for token_id, quote in sorted(snapshot.external_quotes.items())
        },
        "runtime": {
            "observation_monotonic_ns": runtime.observation_monotonic_ns,
            "markets": [
                {
                    "market_id": market.market_id,
                    "status": market.status,
                    "exchange_ids": list(market.exchange_ids),
                    "tournament_id": market.tournament_id,
                    "mapping_accepted": market.mapping_accepted,
                    "tradeable": market.tradeable,
                }
                for market in runtime.markets
            ],
            "books": [
                {
                    "exchange_id": book.exchange_id,
                    "market_id": book.market_id,
                    "tournament_id": book.tournament_id,
                    "bids": [
                        {"price_ticks": level.price_ticks, "quantity": level.quantity}
                        for level in book.bids
                    ],
                    "asks": [
                        {"price_ticks": level.price_ticks, "quantity": level.quantity}
                        for level in book.asks
                    ],
                    "trusted_depth": book.trusted_depth,
                    "observed_monotonic_ns": book.observed_monotonic_ns,
                }
                for book in runtime.books
            ],
            "portfolio": {
                "account_trusted": runtime.portfolio.account_trusted,
                "positions": [
                    {
                        "exchange_id": position.exchange_id,
                        "market_id": position.market_id,
                        "tournament_id": position.tournament_id,
                        "gross_exposure": position.gross_exposure,
                        "signed_quantity": position.signed_quantity,
                    }
                    for position in runtime.portfolio.positions
                ],
                "orders": [
                    {
                        "logical_intent_id": order.logical_intent_id,
                        "exchange_id": order.exchange_id,
                        "market_id": order.market_id,
                        "tournament_id": order.tournament_id,
                        "reserved_exposure": order.reserved_exposure,
                        "open": order.open,
                        "uncertain": order.uncertain,
                    }
                    for order in runtime.portfolio.orders
                ],
            },
        },
    }


def decision_semantic_record(decision: CandidateDecision) -> dict[str, object]:
    return {
        "schema_version": decision.schema_version,
        "decision_id": decision.decision_id,
        "candidate_id": decision.candidate_id,
        "candidate_version": decision.candidate_version,
        "strategy_family": decision.strategy_family,
        "observed_at": decision.observed_at.isoformat(),
        "monotonic_time": decision.monotonic_time,
        "tournament_id": decision.tournament_id,
        "exchange_id": decision.exchange_id,
        "market_id": decision.market_id,
        "input_snapshot_id": decision.input_snapshot_id,
        "mapping_version": decision.mapping_version,
        "fair_value": decision.fair_value,
        "lower_bound": decision.lower_bound,
        "upper_bound": decision.upper_bound,
        "confidence": decision.confidence,
        "direction": decision.direction,
        "score": decision.score,
        "action_intent": decision.action_intent,
        "quote_intent": None if decision.quote_intent is None else dict(decision.quote_intent),
        "decision_status": decision.decision_status.value,
        "abstain_reason": decision.abstain_reason,
        "quality_flags": list(decision.quality_flags),
        "candidate_payload": dict(decision.candidate_payload),
    }


def _ensure_json_mapping(value: Mapping[str, object], name: str) -> None:
    try:
        json.dumps(
            dict(value),
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be JSON serializable without NaN/Inf") from exc
