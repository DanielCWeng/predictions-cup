"""Adapters from existing strategy mechanisms into SHADOW-002 decisions."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Protocol

from predictions_cup.maker.contracts import MakerDecision, MakerMarketSnapshot
from predictions_cup.maker.direct_pm import DirectPolymarketFairValueProvider
from predictions_cup.mapping.models import MappingDocument
from predictions_cup.runtime.models import SIG_TICK
from predictions_cup.shadow.contracts import (
    CandidateOutput,
    CanonicalShadowSnapshot,
    DecisionStatus,
)


class MakerQuoteEngine(Protocol):
    @property
    def strategy_id(self) -> str: ...

    def quote(self, snapshot: MakerMarketSnapshot) -> MakerDecision: ...


class MakerCandidate:
    """Expose MAKE's own hypothetical quote calculation without changing MAKE math."""

    strategy_family = "MAKE"

    def __init__(
        self,
        engine: MakerQuoteEngine,
        *,
        candidate_version: str = "make-001-v1",
    ) -> None:
        if not candidate_version.strip():
            raise ValueError("candidate_version must not be blank")
        self._engine = engine
        self.candidate_id = engine.strategy_id
        self.candidate_version = candidate_version

    def evaluate(self, snapshot: CanonicalShadowSnapshot) -> CandidateOutput:
        decision = self._engine.quote(snapshot.maker)
        trace = decision.trace
        desired = decision.desired
        fair_value = trace.adjusted_fv if trace.adjusted_fv is not None else trace.raw_fv
        lower_bound = None
        upper_bound = None
        if fair_value is not None:
            lower_bound = max(0.0, fair_value - trace.uncertainty)
            upper_bound = min(1.0, fair_value + trace.uncertainty)
        quote_intent: dict[str, object] | None = None
        if desired is not None:
            quote_intent = {
                "bid_ticks": desired.bid_ticks,
                "ask_ticks": desired.ask_ticks,
                "bid_size": desired.bid_size,
                "ask_size": desired.ask_size,
            }
        return CandidateOutput(
            status=DecisionStatus.OK if desired is not None else DecisionStatus.ABSTAIN,
            fair_value=fair_value,
            lower_bound=lower_bound,
            upper_bound=upper_bound,
            confidence=trace.confidence,
            action_intent="QUOTE" if desired is not None else None,
            quote_intent=quote_intent,
            abstain_reason=None if desired is not None else decision.gate.reason,
            quality_flags=(f"gate:{decision.gate.mode.value}",),
            candidate_payload={
                "fv_source": trace.fv_source,
                "fv_version": trace.fv_version,
                "raw_fv": trace.raw_fv,
                "predictive_shift": trace.predictive_shift,
                "uncertainty": trace.uncertainty,
                "update_hazard": trace.update_hazard,
                "adverse_selection": trace.adverse_selection,
                "signed_inventory": trace.signed_inventory,
                "reservation_price": trace.reservation_price,
                "half_spread": trace.half_spread,
                "gate_reason": decision.gate.reason,
                "next_recheck_monotonic_ns": decision.next_recheck_monotonic_ns,
            },
        )


class DirectPmCandidate:
    """Accepted direct Polymarket reference/residual baseline; not an alpha claim."""

    candidate_id = "direct-pm-reference"
    strategy_family = "REFERENCE"

    def __init__(
        self,
        provider: DirectPolymarketFairValueProvider,
        *,
        mapping: MappingDocument | None = None,
    ) -> None:
        self._provider = provider
        self._mapping = mapping
        self.candidate_version = provider.version

    def evaluate(self, snapshot: CanonicalShadowSnapshot) -> CandidateOutput:
        result = self._provider.fair_value(snapshot.maker)
        sig_mid = _sig_midpoint(snapshot.maker)
        discrepancy = (
            None
            if result.value is None or sig_mid is None
            else result.value - sig_mid
        )
        direction = None
        if discrepancy is not None:
            if discrepancy > 1e-12:
                direction = "PM_ABOVE_SIG"
            elif discrepancy < -1e-12:
                direction = "PM_BELOW_SIG"
            else:
                direction = "ALIGNED"

        mapping_direction: str | None = None
        if self._mapping is not None:
            try:
                record = self._mapping.mapping_for_sig_exchange(snapshot.exchange_id)
            except KeyError:
                record = None
            if record is not None and record.mapping_direction is not None:
                mapping_direction = record.mapping_direction.value

        if result.usable:
            status = DecisionStatus.OK
            abstain_reason = None
        elif result.reason in {"polymarket_source_unusable", "derived_component_unusable"}:
            status = DecisionStatus.UNTRUSTED_INPUT
            abstain_reason = result.reason
        else:
            status = DecisionStatus.ABSTAIN
            abstain_reason = result.reason

        age_seconds: float | None = None
        if result.observed_monotonic_ns > 0:
            age_seconds = max(
                0.0,
                (snapshot.observed_monotonic_ns - result.observed_monotonic_ns) / 1e9,
            )
        lower_bound = None
        upper_bound = None
        if result.value is not None:
            lower_bound = max(0.0, result.value - result.uncertainty)
            upper_bound = min(1.0, result.value + result.uncertainty)
        return CandidateOutput(
            status=status,
            fair_value=result.value,
            lower_bound=lower_bound,
            upper_bound=upper_bound,
            confidence=result.confidence if result.usable else None,
            direction=direction,
            score=discrepancy,
            abstain_reason=abstain_reason,
            quality_flags=(f"mapping:{result.mapping_class}",),
            candidate_payload={
                "mapping_class": result.mapping_class,
                "mapping_direction": mapping_direction,
                "source_id": result.source_id,
                "source_version": result.source_version,
                "source_age_seconds": age_seconds,
                "sig_midpoint": sig_mid,
                "pm_minus_sig": discrepancy,
                "reason": result.reason,
            },
        )


@dataclass(frozen=True, slots=True)
class Pred006Signal:
    observed_monotonic_ns: int
    direction: str | None = None
    score: float | None = None
    confidence: float | None = None
    fair_value: float | None = None
    quality_flags: tuple[str, ...] = ()
    payload: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RuntimeEvaluatorMetadata:
    research_id: str
    frozen_spec_version: str
    artifact_hash: str | None
    expected_artifact_hash: str | None
    feature_schema_hash: str
    ready: bool
    readiness_reason: str | None
    freshness_seconds: float | None
    quality_flags: tuple[str, ...] = ()
    diagnostic_payload: Mapping[str, object] = field(default_factory=dict)


class Pred006RuntimeEvaluator(Protocol):
    evaluator_id: str
    version: str

    def metadata(self, snapshot: CanonicalShadowSnapshot) -> RuntimeEvaluatorMetadata: ...

    def evaluate(self, snapshot: CanonicalShadowSnapshot) -> Pred006Signal | None: ...


class Pred006Candidate:
    """Frozen PRED-006 runtime hook. No evaluator means explicit NOT_READY."""

    candidate_id = "pred-006"
    strategy_family = "PRED"

    def __init__(
        self,
        evaluator: Pred006RuntimeEvaluator | None = None,
        *,
        frozen_version: str = "frozen-spec-runtime-not-wired",
    ) -> None:
        self._evaluator = evaluator
        self.candidate_version = (
            frozen_version if evaluator is None else evaluator.version
        )

    def evaluate(self, snapshot: CanonicalShadowSnapshot) -> CandidateOutput:
        if self._evaluator is None:
            return CandidateOutput(
                status=DecisionStatus.NOT_READY,
                abstain_reason="runtime_feature_parity_not_wired",
                quality_flags=("pred006:frozen_no_approximation",),
            )
        metadata = self._evaluator.metadata(snapshot)
        if not metadata.ready:
            return CandidateOutput(
                status=DecisionStatus.NOT_READY,
                abstain_reason=metadata.readiness_reason or "runtime_features_unavailable",
                quality_flags=(
                    "pred006:frozen_no_approximation",
                    *metadata.quality_flags,
                ),
                candidate_payload=_runtime_metadata_payload(metadata),
            )
        signal = self._evaluator.evaluate(snapshot)
        if signal is None:
            return CandidateOutput(
                status=DecisionStatus.NOT_READY,
                abstain_reason="runtime_features_unavailable",
                quality_flags=("pred006:frozen_no_approximation",),
                candidate_payload=_runtime_metadata_payload(metadata),
            )
        return CandidateOutput(
            status=DecisionStatus.OK,
            fair_value=signal.fair_value,
            confidence=signal.confidence,
            direction=signal.direction,
            score=signal.score,
            quality_flags=(*metadata.quality_flags, *signal.quality_flags),
            candidate_payload={
                **_runtime_metadata_payload(metadata),
                "signal_observed_monotonic_ns": signal.observed_monotonic_ns,
                "frozen_definition": True,
                **dict(signal.payload),
            },
        )


@dataclass(frozen=True, slots=True)
class Hazard005FSignal:
    update_hazard: float | None
    jump_hazard: float | None
    observed_monotonic_ns: int
    quality_flags: tuple[str, ...] = ()
    payload: Mapping[str, object] = field(default_factory=dict)


class Hazard005FEvaluator(Protocol):
    evaluator_id: str
    version: str

    def metadata(self, snapshot: CanonicalShadowSnapshot) -> RuntimeEvaluatorMetadata: ...

    def evaluate(self, snapshot: CanonicalShadowSnapshot) -> Hazard005FSignal | None: ...


class Hazard005FCandidate:
    """005F remains a movement/jump-hazard diagnostic, never directional FV."""

    candidate_id = "experiment-005f-hazard"
    strategy_family = "EVENT"

    def __init__(
        self,
        evaluator: Hazard005FEvaluator | None = None,
        *,
        frozen_version: str = "005f-runtime-not-wired",
    ) -> None:
        self._evaluator = evaluator
        self.candidate_version = (
            frozen_version if evaluator is None else evaluator.version
        )

    def evaluate(self, snapshot: CanonicalShadowSnapshot) -> CandidateOutput:
        if self._evaluator is None:
            return CandidateOutput(
                status=DecisionStatus.NOT_READY,
                abstain_reason="005f_live_feature_parity_not_wired",
                quality_flags=("005f:hazard_not_directional",),
            )
        metadata = self._evaluator.metadata(snapshot)
        if not metadata.ready:
            return CandidateOutput(
                status=DecisionStatus.NOT_READY,
                abstain_reason=metadata.readiness_reason or "005f_live_features_unavailable",
                quality_flags=(
                    "005f:hazard_not_directional",
                    *metadata.quality_flags,
                ),
                candidate_payload=_runtime_metadata_payload(metadata),
            )
        signal = self._evaluator.evaluate(snapshot)
        if signal is None:
            return CandidateOutput(
                status=DecisionStatus.NOT_READY,
                abstain_reason="005f_live_features_unavailable",
                quality_flags=("005f:hazard_not_directional",),
                candidate_payload=_runtime_metadata_payload(metadata),
            )
        hazards = tuple(
            value
            for value in (signal.update_hazard, signal.jump_hazard)
            if value is not None
        )
        if not hazards or any(
            not math.isfinite(value) or not 0.0 <= value <= 1.0
            for value in hazards
        ):
            return CandidateOutput(
                status=DecisionStatus.INVALID_OUTPUT,
                abstain_reason="005f_hazard_out_of_range",
                quality_flags=("005f:hazard_not_directional",),
            )
        return CandidateOutput(
            status=DecisionStatus.OK,
            score=(
                signal.update_hazard
                if signal.update_hazard is not None
                else signal.jump_hazard
            ),
            quality_flags=(
                "005f:hazard_not_directional",
                *metadata.quality_flags,
                *signal.quality_flags,
            ),
            candidate_payload={
                **_runtime_metadata_payload(metadata),
                "output_type": "movement_hazard_not_directional",
                "update_hazard": signal.update_hazard,
                "jump_hazard": signal.jump_hazard,
                "signal_observed_monotonic_ns": signal.observed_monotonic_ns,
                **dict(signal.payload),
            },
        )


@dataclass(frozen=True, slots=True)
class StructuralFairValue:
    fair_value: float
    lower_bound: float
    upper_bound: float
    confidence: float
    method_id: str
    source_count: int
    freshness_seconds: float
    quality_flags: tuple[str, ...] = ()


class StructuralFairValueProvider(Protocol):
    provider_id: str
    version: str

    def get(
        self,
        *,
        sig_market_id: str,
        asof_monotonic_ns: int,
    ) -> StructuralFairValue | None: ...


class NullStructuralFairValueProvider:
    provider_id = "r3-ets-null"
    version = "hook-only-v1"

    def get(
        self,
        *,
        sig_market_id: str,
        asof_monotonic_ns: int,
    ) -> StructuralFairValue | None:
        del sig_market_id, asof_monotonic_ns
        return None


class StructuralFairValueCandidate:
    """Optional R3/ETS provider hook with no dependency on the active research branch."""

    candidate_id = "r3-ets-structural-fv"
    strategy_family = "STRUCT"

    def __init__(
        self,
        provider: StructuralFairValueProvider | None = None,
    ) -> None:
        self._provider = provider or NullStructuralFairValueProvider()
        self.candidate_version = self._provider.version

    def evaluate(self, snapshot: CanonicalShadowSnapshot) -> CandidateOutput:
        result = self._provider.get(
            sig_market_id=snapshot.market_id,
            asof_monotonic_ns=snapshot.observed_monotonic_ns,
        )
        if result is None:
            return CandidateOutput(
                status=DecisionStatus.NOT_READY,
                abstain_reason="structural_provider_unavailable",
                quality_flags=("r3:optional_provider_hook",),
            )
        return CandidateOutput(
            status=DecisionStatus.OK,
            fair_value=result.fair_value,
            lower_bound=result.lower_bound,
            upper_bound=result.upper_bound,
            confidence=result.confidence,
            quality_flags=result.quality_flags,
            candidate_payload={
                "provider_id": self._provider.provider_id,
                "method_id": result.method_id,
                "source_count": result.source_count,
                "freshness_seconds": result.freshness_seconds,
            },
        )



def _runtime_metadata_payload(
    metadata: RuntimeEvaluatorMetadata,
) -> dict[str, object]:
    return {
        "research_id": metadata.research_id,
        "frozen_spec_version": metadata.frozen_spec_version,
        "artifact_hash": metadata.artifact_hash,
        "expected_artifact_hash": metadata.expected_artifact_hash,
        "feature_schema_hash": metadata.feature_schema_hash,
        "readiness": metadata.ready,
        "readiness_reason": metadata.readiness_reason,
        "freshness_seconds": metadata.freshness_seconds,
        **dict(metadata.diagnostic_payload),
    }

def _sig_midpoint(snapshot: MakerMarketSnapshot) -> float | None:
    book = snapshot.runtime.book(snapshot.exchange_id)
    if book is None or not book.bids or not book.asks:
        return None
    best_bid = max(level.price_ticks for level in book.bids)
    best_ask = min(level.price_ticks for level in book.asks)
    if best_bid > best_ask:
        return None
    return ((best_bid + best_ask) * 0.5) * float(SIG_TICK)
