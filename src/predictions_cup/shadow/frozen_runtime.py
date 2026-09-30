"""Exact frozen research runtime boundaries for CANDIDATE-RUNTIME-001.

No network/database I/O and no model fitting lives here. The runtime state builders
only consume explicitly observable evidence supplied by an upstream capture source.
"""

from __future__ import annotations

import hashlib
import json
import math
from bisect import bisect_right
from collections import defaultdict, deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from threading import RLock
from typing import Protocol

from predictions_cup.shadow.adapters import (
    Hazard005FSignal,
    Pred006Signal,
    RuntimeEvaluatorMetadata,
)
from predictions_cup.shadow.contracts import CanonicalShadowSnapshot

PRED006_RESEARCH_ID = "PRED-006"
PRED006_FROZEN_SPEC_VERSION = (
    "shortlist_freeze:5deff02e70bbb63a7355390c4babfc1ed63dd8d5b112087a2e9ff9dddfe835f6"
)
PRED006_FEATURES = (
    "p_yes",
    "logit_p",
    "boundary_distance",
    "size_log",
    "value_log",
    "since_prev",
    "count_30",
    "count_120",
    "count_600",
    "count_1800",
    "vol_30",
    "vol_120",
    "vol_600",
    "vol_1800",
    "mom_30",
    "mom_120",
    "mom_600",
    "mom_1800",
    "fee_charged",
    "fee_missing",
    "fee_no_leg",
    "fee_net_log",
    "fee_charge_log",
    "fee_refund_log",
    "charge_legs_log",
)
PRED006_CANDIDATES = {"PRED006-C01": 1800, "PRED006-C02": 600}
PRED006_SCHEMA_HASH = hashlib.sha256(
    json.dumps(PRED006_FEATURES, separators=(",", ":")).encode()
).hexdigest()

HAZARD005F_RESEARCH_ID = "EXPERIMENT-005F"
HAZARD005F_FROZEN_SPEC_VERSION = (
    "fit_freeze_manifest:c69506329c437d4cf38b9977572d1ec79389e004"
)
HAZARD005F_SCHEMA = {
    "PRE_ELECTION|clock|UPDATE_HAZARD": (
        "genuine_15",
        "genuine_60",
        "genuine_age_s",
    ),
    "ACTIVE_RESULTS|clock|UPDATE_HAZARD": (
        "genuine_15",
        "genuine_60",
        "genuine_age_s",
    ),
    "ACTIVE_RESULTS|clock|JUMP_HAZARD": (
        "abs_ret_15",
        "rv_60",
        "genuine_age_s",
    ),
}
HAZARD005F_EXPECTED_ARTIFACT_HASHES = {
    "PRE_ELECTION|clock|UPDATE_HAZARD": (
        "222fc36ae35d9dbc8f2bcb489e9a353d2efe5b06ebcfb9d8edb410a60829213c",
        "2a33de93b14e7f130894b2f496ba0be11d1e5b11840f8dd2e1f91c37e24d064e",
    ),
    "ACTIVE_RESULTS|clock|UPDATE_HAZARD": (
        "a5302b9760e9d6763e6a3ac6894b417942a453a3c4f7f883afced1f178f24b12",
        "42dfd8404467053bdde5bfb4dca30bc3e928f190a4f52f8d98ab0a76da48fe69",
    ),
    "ACTIVE_RESULTS|clock|JUMP_HAZARD": (
        "c4052781fca57915b3beed594aacd9cba3619c12d70426d1f3454539a8413a8d",
        "6a094fe82bd5fc55aa41da0b3964627fc7b68f7e08433f4ac53272c42ce3f4f9",
    ),
}
HAZARD005F_SCHEMA_HASH = hashlib.sha256(
    json.dumps(HAZARD005F_SCHEMA, sort_keys=True, separators=(",", ":")).encode()
).hexdigest()
HAZARD005F_SCORER_HASHES = {
    key: hashlib.sha256(
        json.dumps(value, separators=(",", ":")).encode()
    ).hexdigest()
    for key, value in HAZARD005F_EXPECTED_ARTIFACT_HASHES.items()
}


class FeatureParity(StrEnum):
    EXACT_LIVE_PARITY = "EXACT_LIVE_PARITY"
    EXACT_BUT_DELAYED = "EXACT_BUT_DELAYED"
    NOT_OBSERVABLE_LIVE = "NOT_OBSERVABLE_LIVE"
    SEMANTICS_MISMATCH = "SEMANTICS_MISMATCH"


@dataclass(frozen=True, slots=True)
class FeatureParityRecord:
    feature: str
    parity: FeatureParity
    source: str
    detail: str


class ProbabilityScorer(Protocol):
    @property
    def scorer_id(self) -> str: ...

    @property
    def artifact_hash(self) -> str: ...

    def score(self, values: tuple[float, ...]) -> float: ...


@dataclass(frozen=True, slots=True)
class Pred006ArtifactManifest:
    """Explicit authorization for serialized frozen PRED-006 scorers."""

    manifest_version: str
    research_id: str
    frozen_spec_version: str
    feature_schema_hash: str
    artifacts: tuple[tuple[str, str], ...]
    provenance: str

    def __post_init__(self) -> None:
        if self.manifest_version != "pred006-artifact-manifest-v1":
            raise ValueError("unsupported PRED-006 artifact manifest version")
        if self.research_id != PRED006_RESEARCH_ID:
            raise ValueError("PRED-006 artifact manifest research identity mismatch")
        if self.frozen_spec_version != PRED006_FROZEN_SPEC_VERSION:
            raise ValueError("PRED-006 artifact manifest frozen spec mismatch")
        if self.feature_schema_hash != PRED006_SCHEMA_HASH:
            raise ValueError("PRED-006 artifact manifest feature schema mismatch")
        if not self.provenance.strip():
            raise ValueError("PRED-006 artifact manifest provenance must not be blank")
        artifact_map = dict(self.artifacts)
        if len(artifact_map) != len(self.artifacts):
            raise ValueError("duplicate PRED-006 artifact manifest candidate")
        if set(artifact_map) != set(PRED006_CANDIDATES):
            raise ValueError("PRED-006 artifact manifest must authorize C01 and C02")
        for artifact_hash in artifact_map.values():
            if (
                len(artifact_hash) != 64
                or any(ch not in "0123456789abcdef" for ch in artifact_hash.lower())
            ):
                raise ValueError("PRED-006 artifact hash must be SHA-256 hex")

    def expected_hash(self, candidate_id: str) -> str:
        return dict(self.artifacts)[candidate_id]

    @property
    def manifest_hash(self) -> str:
        payload = {
            "manifest_version": self.manifest_version,
            "research_id": self.research_id,
            "frozen_spec_version": self.frozen_spec_version,
            "feature_schema_hash": self.feature_schema_hash,
            "artifacts": dict(sorted(self.artifacts)),
            "provenance": self.provenance,
        }
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()


@dataclass(frozen=True, slots=True)
class Pred006BlockObservation:
    """Exact DATA-003-equivalent condition/block-end observation."""

    scope_id: str
    window_id: str
    block_number: int
    timestamp_s: int
    observed_monotonic_ns: int
    p_yes: float
    size_shares: float
    value_usd: float
    fee_charged: float
    fee_missing: float
    fee_no_leg: float
    active_fee_net: float
    active_fee_charged: float
    active_fee_refunded: float
    active_charge_legs: float
    source_version: str

    def __post_init__(self) -> None:
        if not self.scope_id or not self.window_id or not self.source_version:
            raise ValueError("scope_id/window_id/source_version must not be blank")
        if self.block_number < 0:
            raise ValueError("block_number must be non-negative")
        if self.timestamp_s < 0 or self.observed_monotonic_ns < 0:
            raise ValueError("observation timestamps must be non-negative")
        if not math.isfinite(self.p_yes) or not 0.0 <= self.p_yes <= 1.0:
            raise ValueError("p_yes must be finite within [0,1]")
        values = (
            self.size_shares,
            self.value_usd,
            self.fee_charged,
            self.fee_missing,
            self.fee_no_leg,
            self.active_fee_net,
            self.active_fee_charged,
            self.active_fee_refunded,
            self.active_charge_legs,
        )
        if any(not math.isfinite(value) for value in values):
            raise ValueError("PRED-006 source values must be finite")


@dataclass(frozen=True, slots=True)
class Pred006FeatureVector:
    scope_id: str
    window_id: str
    block_number: int
    timestamp_s: int
    observed_monotonic_ns: int
    source_version: str
    names: tuple[str, ...]
    values: tuple[float, ...]

    def __post_init__(self) -> None:
        if self.names != PRED006_FEATURES or len(self.values) != len(self.names):
            raise ValueError("PRED-006 feature schema mismatch")

    def as_mapping(self) -> dict[str, float]:
        return dict(zip(self.names, self.values, strict=True))


class Pred006FeatureProvider(Protocol):
    provider_id: str
    version: str

    def feature_vector(
        self,
        snapshot: CanonicalShadowSnapshot,
    ) -> Pred006FeatureVector | None: ...

    def parity(self) -> tuple[FeatureParityRecord, ...]: ...


@dataclass(slots=True)
class _PredWindow:
    first_timestamp_s: int
    previous_timestamp_s: int
    previous_block_number: int
    previous_p: float
    queues: dict[int, deque[tuple[int, float, float]]]
    sum_sq: dict[int, float]


class NullPred006FeatureProvider:
    provider_id = "pred006-null-live-provider"
    version = "candidate-runtime-001"

    def feature_vector(
        self,
        snapshot: CanonicalShadowSnapshot,
    ) -> Pred006FeatureVector | None:
        del snapshot
        return None

    def parity(self) -> tuple[FeatureParityRecord, ...]:
        market_features = set(PRED006_FEATURES[:18])
        rows: list[FeatureParityRecord] = []
        for feature in PRED006_FEATURES:
            if feature in market_features:
                rows.append(
                    FeatureParityRecord(
                        feature=feature,
                        parity=FeatureParity.EXACT_BUT_DELAYED,
                        source="DATA-003 condition/block-end economic-fill stream",
                        detail=(
                            "Exact only after the whole Polygon condition/block "
                            "observation is assembled."
                        ),
                    )
                )
            else:
                rows.append(
                    FeatureParityRecord(
                        feature=feature,
                        parity=FeatureParity.NOT_OBSERVABLE_LIVE,
                        source="DATA-002/DATA-003 custody fee attribution",
                        detail=(
                            "Current SHADOW snapshot/capture contract has no exact "
                            "custody-attribution field."
                        ),
                    )
                )
        return tuple(rows)


class IncrementalPred006FeatureState:
    """Incremental parity with pred006_final.py:add_features."""

    provider_id = "pred006-exact-block-state"
    version = "candidate-runtime-001"
    _windows = (30, 120, 600, 1800)

    def __init__(
        self,
        *,
        fee_parity: FeatureParity = FeatureParity.EXACT_BUT_DELAYED,
        scope_resolver: Callable[[CanonicalShadowSnapshot], str | None] | None = None,
    ) -> None:
        self._fee_parity = fee_parity
        self._scope_resolver = scope_resolver
        self._state: dict[tuple[str, str], _PredWindow] = {}
        self._latest: dict[str, Pred006FeatureVector] = {}
        self._lock = RLock()

    def parity(self) -> tuple[FeatureParityRecord, ...]:
        fee_features = set(PRED006_FEATURES[18:])
        return tuple(
            FeatureParityRecord(
                feature=feature,
                parity=(
                    self._fee_parity
                    if feature in fee_features
                    else FeatureParity.EXACT_BUT_DELAYED
                ),
                source=(
                    "exact custody-attributed condition/block observation"
                    if feature in fee_features
                    else "exact condition/block-end economic-fill observation"
                ),
                detail=(
                    "Matches frozen DATA-003 construction; availability is delayed "
                    "until source evidence is complete."
                ),
            )
            for feature in PRED006_FEATURES
        )

    def observe(self, observation: Pred006BlockObservation) -> Pred006FeatureVector:
        with self._lock:
            key = (observation.scope_id, observation.window_id)
            state = self._state.get(key)
            if state is None:
                state = _PredWindow(
                    first_timestamp_s=observation.timestamp_s,
                    previous_timestamp_s=observation.timestamp_s,
                    previous_block_number=observation.block_number,
                    previous_p=observation.p_yes,
                    queues={window: deque() for window in self._windows},
                    sum_sq={window: 0.0 for window in self._windows},
                )
                self._state[key] = state
                since_prev = math.nan
                dp_sq = 0.0
            else:
                if observation.block_number <= state.previous_block_number:
                    raise ValueError(
                        "PRED-006 block_number must strictly increase per scope/window"
                    )
                if observation.timestamp_s < state.previous_timestamp_s:
                    raise ValueError(
                        "PRED-006 source timestamp regressed across increasing blocks"
                    )
                since_prev = float(
                    observation.timestamp_s - state.previous_timestamp_s
                )
                dp = observation.p_yes - state.previous_p
                dp_sq = dp * dp

            window_values: dict[str, float] = {}
            for window in self._windows:
                queue = state.queues[window]
                queue.append((observation.timestamp_s, observation.p_yes, dp_sq))
                state.sum_sq[window] += dp_sq
                cutoff = observation.timestamp_s - window
                while queue and queue[0][0] < cutoff:
                    _, _, old_dp_sq = queue.popleft()
                    state.sum_sq[window] -= old_dp_sq
                supported = cutoff >= state.first_timestamp_s
                if supported and queue:
                    window_values[f"count_{window}"] = float(len(queue))
                    window_values[f"vol_{window}"] = math.sqrt(
                        max(0.0, state.sum_sq[window])
                    )
                    window_values[f"mom_{window}"] = (
                        observation.p_yes - queue[0][1]
                    )
                else:
                    window_values[f"count_{window}"] = math.nan
                    window_values[f"vol_{window}"] = math.nan
                    window_values[f"mom_{window}"] = math.nan

            p_yes = observation.p_yes
            clipped = min(1.0 - 1e-4, max(1e-4, p_yes))
            fee_net_log = (
                math.copysign(
                    math.log1p(abs(observation.active_fee_net)),
                    observation.active_fee_net,
                )
                if observation.active_fee_net != 0
                else 0.0
            )
            features = {
                "p_yes": p_yes,
                "logit_p": math.log(clipped / (1.0 - clipped)),
                "boundary_distance": min(p_yes, 1.0 - p_yes),
                "size_log": math.log1p(max(0.0, observation.size_shares)),
                "value_log": math.log1p(max(0.0, observation.value_usd)),
                "since_prev": since_prev,
                **window_values,
                "fee_charged": observation.fee_charged,
                "fee_missing": observation.fee_missing,
                "fee_no_leg": observation.fee_no_leg,
                "fee_net_log": fee_net_log,
                "fee_charge_log": math.log1p(
                    max(0.0, observation.active_fee_charged)
                ),
                "fee_refund_log": math.log1p(
                    max(0.0, observation.active_fee_refunded)
                ),
                "charge_legs_log": math.log1p(
                    max(0.0, observation.active_charge_legs)
                ),
            }
            vector = Pred006FeatureVector(
                scope_id=observation.scope_id,
                window_id=observation.window_id,
                block_number=observation.block_number,
                timestamp_s=observation.timestamp_s,
                observed_monotonic_ns=observation.observed_monotonic_ns,
                source_version=observation.source_version,
                names=PRED006_FEATURES,
                values=tuple(float(features[name]) for name in PRED006_FEATURES),
            )
            state.previous_timestamp_s = observation.timestamp_s
            state.previous_block_number = observation.block_number
            state.previous_p = observation.p_yes
            self._latest[observation.scope_id] = vector
            return vector

    def feature_vector(
        self,
        snapshot: CanonicalShadowSnapshot,
    ) -> Pred006FeatureVector | None:
        if self._scope_resolver is None:
            return None
        scope_id = self._scope_resolver(snapshot)
        if scope_id is None:
            return None
        with self._lock:
            vector = self._latest.get(scope_id)
            if vector is None:
                return None
            if vector.observed_monotonic_ns > snapshot.observed_monotonic_ns:
                return None
            return vector


class FrozenPred006Evaluator:
    evaluator_id = "pred006-frozen-runtime"
    version = "candidate-runtime-001"
    research_id = PRED006_RESEARCH_ID
    frozen_spec_version = PRED006_FROZEN_SPEC_VERSION
    feature_schema_hash = PRED006_SCHEMA_HASH

    def __init__(
        self,
        provider: Pred006FeatureProvider | None = None,
        *,
        scorers: Mapping[str, ProbabilityScorer] | None = None,
        artifact_manifest: Pred006ArtifactManifest | None = None,
    ) -> None:
        self._provider = provider or NullPred006FeatureProvider()
        self._scorers = dict(scorers or {})
        self._artifact_manifest = artifact_manifest

    def metadata(self, snapshot: CanonicalShadowSnapshot) -> RuntimeEvaluatorMetadata:
        parity = self._provider.parity()
        manifest = self._artifact_manifest
        if manifest is None:
            return RuntimeEvaluatorMetadata(
                research_id=self.research_id,
                frozen_spec_version=self.frozen_spec_version,
                artifact_hash=self._artifact_hash() if self._scorers else None,
                expected_artifact_hash=(
                    "NO_AUTHORIZED_SERIALIZED_FITTED_MODEL_MANIFEST"
                ),
                feature_schema_hash=self.feature_schema_hash,
                ready=False,
                readiness_reason="model_artifact_missing",
                freshness_seconds=None,
                quality_flags=(
                    "pred006:no_retraining",
                    "pred006:frozen_no_approximation",
                    "pred006:artifact_manifest_required",
                ),
            )

        missing = tuple(
            candidate
            for candidate in PRED006_CANDIDATES
            if candidate not in self._scorers
        )
        if missing:
            return RuntimeEvaluatorMetadata(
                research_id=self.research_id,
                frozen_spec_version=self.frozen_spec_version,
                artifact_hash=self._artifact_hash() if self._scorers else None,
                expected_artifact_hash=manifest.manifest_hash,
                feature_schema_hash=self.feature_schema_hash,
                ready=False,
                readiness_reason="model_artifact_missing:" + ",".join(missing),
                freshness_seconds=None,
                quality_flags=("pred006:artifact_manifest_authorized",),
            )

        bad_hash = tuple(
            candidate
            for candidate in PRED006_CANDIDATES
            if self._scorers[candidate].artifact_hash
            != manifest.expected_hash(candidate)
        )
        if bad_hash:
            return RuntimeEvaluatorMetadata(
                research_id=self.research_id,
                frozen_spec_version=self.frozen_spec_version,
                artifact_hash=self._artifact_hash(),
                expected_artifact_hash=manifest.manifest_hash,
                feature_schema_hash=self.feature_schema_hash,
                ready=False,
                readiness_reason="model_artifact_hash_mismatch:" + ",".join(bad_hash),
                freshness_seconds=None,
                quality_flags=("pred006:artifact_manifest_authorized",),
            )

        bad = tuple(
            row.feature
            for row in parity
            if row.parity
            in {
                FeatureParity.NOT_OBSERVABLE_LIVE,
                FeatureParity.SEMANTICS_MISMATCH,
            }
        )
        if bad:
            return RuntimeEvaluatorMetadata(
                research_id=self.research_id,
                frozen_spec_version=self.frozen_spec_version,
                artifact_hash=self._artifact_hash(),
                expected_artifact_hash=manifest.manifest_hash,
                feature_schema_hash=self.feature_schema_hash,
                ready=False,
                readiness_reason="feature_parity_unavailable:" + ",".join(bad),
                freshness_seconds=None,
                quality_flags=("pred006:observable_time_gate_failed",),
            )

        vector = self._provider.feature_vector(snapshot)
        if vector is None:
            return RuntimeEvaluatorMetadata(
                research_id=self.research_id,
                frozen_spec_version=self.frozen_spec_version,
                artifact_hash=self._artifact_hash(),
                expected_artifact_hash=manifest.manifest_hash,
                feature_schema_hash=self.feature_schema_hash,
                ready=False,
                readiness_reason="required_feature_history_unavailable",
                freshness_seconds=None,
                quality_flags=("pred006:exact_history_required",),
            )

        age = max(
            0.0,
            (snapshot.observed_monotonic_ns - vector.observed_monotonic_ns) / 1e9,
        )
        return RuntimeEvaluatorMetadata(
            research_id=self.research_id,
            frozen_spec_version=self.frozen_spec_version,
            artifact_hash=self._artifact_hash(),
            expected_artifact_hash=manifest.manifest_hash,
            feature_schema_hash=self.feature_schema_hash,
            ready=True,
            readiness_reason=None,
            freshness_seconds=age,
            quality_flags=tuple(
                f"pred006:parity:{row.feature}:{row.parity.value}"
                for row in parity
            ),
        )

    def evaluate(self, snapshot: CanonicalShadowSnapshot) -> Pred006Signal | None:
        metadata = self.metadata(snapshot)
        if not metadata.ready:
            return None
        vector = self._provider.feature_vector(snapshot)
        if vector is None:
            return None
        scores = {
            candidate: self._bounded_score(self._scorers[candidate], vector.values)
            for candidate in PRED006_CANDIDATES
        }
        return Pred006Signal(
            observed_monotonic_ns=vector.observed_monotonic_ns,
            quality_flags=("pred006:frozen_exact_runtime",),
            payload={
                "research_id": self.research_id,
                "frozen_spec_version": self.frozen_spec_version,
                "feature_schema_hash": self.feature_schema_hash,
                "artifact_manifest_hash": self._artifact_manifest.manifest_hash
                if self._artifact_manifest is not None
                else None,
                "feature_block_number": vector.block_number,
                "feature_timestamp_s": vector.timestamp_s,
                "window_id": vector.window_id,
                "source_version": vector.source_version,
                "hazard_probabilities": scores,
                "horizon_seconds": dict(PRED006_CANDIDATES),
            },
        )

    def _artifact_hash(self) -> str:
        payload = {
            key: self._scorers[key].artifact_hash
            for key in sorted(self._scorers)
            if key in PRED006_CANDIDATES
        }
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()

    @staticmethod
    def _bounded_score(
        scorer: ProbabilityScorer,
        values: tuple[float, ...],
    ) -> float:
        value = float(scorer.score(values))
        if not math.isfinite(value) or not 0.0 <= value <= 1.0:
            raise ValueError(f"{scorer.scorer_id} returned invalid probability")
        return value


@dataclass(frozen=True, slots=True)
class Hazard005FBboObservation:
    """Grouped BBO observation with exact research timestamp semantics."""

    scope_id: str
    timestamp_ns: int
    observed_monotonic_ns: int
    best_bid: float | None
    best_ask: float | None
    source_version: str = "unknown"
    ambiguous: bool = False

    def __post_init__(self) -> None:
        if not self.scope_id:
            raise ValueError("005F scope_id must not be blank")
        if self.timestamp_ns < 0 or self.observed_monotonic_ns < 0:
            raise ValueError("005F timestamps must be non-negative")

    @property
    def valid(self) -> bool:
        return (
            self.best_bid is not None
            and self.best_ask is not None
            and math.isfinite(self.best_bid)
            and math.isfinite(self.best_ask)
            and 0.0 < self.best_bid <= self.best_ask < 1.0
            and not self.ambiguous
        )


@dataclass(frozen=True, slots=True)
class Hazard005FFeatureVector:
    scope_id: str
    grid_time_ns: int
    observed_monotonic_ns: int
    values: Mapping[str, float]
    source_version: str


class Hazard005FFeatureProvider(Protocol):
    provider_id: str
    version: str

    def feature_vector(
        self,
        snapshot: CanonicalShadowSnapshot,
    ) -> Hazard005FFeatureVector | None: ...


class Hazard005FRegimeProvider(Protocol):
    provider_id: str
    version: str

    def regime(self, snapshot: CanonicalShadowSnapshot) -> str | None: ...


class NullHazard005FFeatureProvider:
    provider_id = "005f-null-orderbook-provider"
    version = "candidate-runtime-001"

    def feature_vector(
        self,
        snapshot: CanonicalShadowSnapshot,
    ) -> Hazard005FFeatureVector | None:
        del snapshot
        return None


class NullHazard005FRegimeProvider:
    provider_id = "005f-null-regime-provider"
    version = "candidate-runtime-001"

    def regime(self, snapshot: CanonicalShadowSnapshot) -> str | None:
        del snapshot
        return None


class FixedHazard005FRegimeProvider:
    provider_id = "005f-fixed-regime"
    version = "candidate-runtime-001"

    def __init__(self, regime: str) -> None:
        if regime not in {"PRE_ELECTION", "ACTIVE_RESULTS"}:
            raise ValueError("unsupported 005F regime")
        self._regime = regime

    def regime(self, snapshot: CanonicalShadowSnapshot) -> str:
        del snapshot
        return self._regime


@dataclass(frozen=True, slots=True)
class _HazardState:
    timestamp_ns: int
    midpoint: float
    segment: int
    last_genuine_ns: int | None
    observed_monotonic_ns: int
    source_version: str


@dataclass(slots=True)
class _HazardScope:
    previous_timestamp_ns: int | None = None
    previous_bid: float | None = None
    previous_ask: float | None = None
    previous_valid: bool = False
    segment: int = 0
    last_genuine_ns: int | None = None
    states: list[_HazardState] = field(default_factory=list)
    state_times_ns: list[int] = field(default_factory=list)
    genuine_bins_ns: dict[int, int] = field(default_factory=dict)


class IncrementalHazard005FState:
    """Reconstruct accepted 005F features using integer nanosecond time."""

    provider_id = "005f-exact-genuine-bbo-state"
    version = "candidate-runtime-001"
    _ns = 1_000_000_000
    _grid_ns = 15 * _ns
    _capture_bin_ns = 5 * _ns
    _gap_ns = 300 * _ns
    _epoch = datetime(1970, 1, 1, tzinfo=UTC)

    def __init__(
        self,
        *,
        grid_origin_ns: int | None = None,
        scope_resolver: Callable[[CanonicalShadowSnapshot], str | None] | None = None,
    ) -> None:
        if grid_origin_ns is not None and grid_origin_ns < 0:
            raise ValueError("grid_origin_ns must be non-negative")
        self._grid_origin_ns = grid_origin_ns
        self._scope_resolver = scope_resolver
        self._scopes: dict[str, _HazardScope] = defaultdict(_HazardScope)
        self._lock = RLock()

    @classmethod
    def datetime_ns(cls, value: datetime) -> int:
        if value.tzinfo is None:
            raise ValueError("005F query datetime must be timezone-aware")
        delta = value.astimezone(UTC) - cls._epoch
        return (
            (delta.days * 86_400 + delta.seconds) * cls._ns
            + delta.microseconds * 1_000
        )

    def observe(self, observation: Hazard005FBboObservation) -> None:
        with self._lock:
            scope = self._scopes[observation.scope_id]
            previous_time = scope.previous_timestamp_ns
            if (
                previous_time is not None
                and observation.timestamp_ns < previous_time
            ):
                raise ValueError(
                    "005F BBO observations must be time ordered per scope"
                )

            valid = observation.valid
            gap = (
                previous_time is not None
                and observation.timestamp_ns - previous_time > self._gap_ns
            )
            contiguous = valid and scope.previous_valid and not gap
            same_bbo = (
                contiguous
                and observation.best_bid is not None
                and observation.best_ask is not None
                and scope.previous_bid is not None
                and scope.previous_ask is not None
                and math.isclose(
                    observation.best_bid,
                    scope.previous_bid,
                    rel_tol=0.0,
                    abs_tol=1e-12,
                )
                and math.isclose(
                    observation.best_ask,
                    scope.previous_ask,
                    rel_tol=0.0,
                    abs_tol=1e-12,
                )
            )
            genuine = bool(contiguous and not same_bbo)
            establish = bool(valid and not contiguous)

            if establish:
                scope.segment += 1
            if genuine:
                scope.last_genuine_ns = observation.timestamp_ns
                bin_time_ns = (
                    observation.timestamp_ns // self._capture_bin_ns
                ) * self._capture_bin_ns
                scope.genuine_bins_ns[bin_time_ns] = (
                    scope.genuine_bins_ns.get(bin_time_ns, 0) + 1
                )

            if valid and (establish or genuine):
                assert observation.best_bid is not None
                assert observation.best_ask is not None
                state = _HazardState(
                    timestamp_ns=observation.timestamp_ns,
                    midpoint=(observation.best_bid + observation.best_ask) / 2.0,
                    segment=scope.segment,
                    last_genuine_ns=scope.last_genuine_ns,
                    observed_monotonic_ns=observation.observed_monotonic_ns,
                    source_version=observation.source_version,
                )
                scope.states.append(state)
                scope.state_times_ns.append(observation.timestamp_ns)

            scope.previous_timestamp_ns = observation.timestamp_ns
            scope.previous_valid = valid
            if valid:
                scope.previous_bid = observation.best_bid
                scope.previous_ask = observation.best_ask
            else:
                scope.previous_bid = None
                scope.previous_ask = None
                scope.last_genuine_ns = None

    def feature_vector(
        self,
        snapshot: CanonicalShadowSnapshot,
    ) -> Hazard005FFeatureVector | None:
        if self._grid_origin_ns is None or self._scope_resolver is None:
            return None
        scope_id = self._scope_resolver(snapshot)
        if scope_id is None:
            return None
        observed_ns = self.datetime_ns(snapshot.observed_at)
        if observed_ns < self._grid_origin_ns:
            return None
        elapsed_ns = observed_ns - self._grid_origin_ns
        query_ns = self._grid_origin_ns + (
            elapsed_ns // self._grid_ns
        ) * self._grid_ns
        with self._lock:
            scope = self._scopes.get(scope_id)
            if scope is None or not scope.states:
                return None
            current = self._asof(scope, query_ns)
            if current is None or current.last_genuine_ns is None:
                return None

            ret_15 = self._return(scope, query_ns, 15)
            returns = [
                self._return(scope, query_ns - offset * self._ns, 15)
                for offset in (45, 30, 15, 0)
            ]
            finite = [value for value in returns if math.isfinite(value)]
            rv_60 = (
                math.sqrt(sum(value * value for value in finite))
                if len(finite) >= 2
                else math.nan
            )
            values = {
                "genuine_15": float(self._genuine_count(scope, query_ns, 15)),
                "genuine_60": float(self._genuine_count(scope, query_ns, 60)),
                "genuine_age_s": (
                    query_ns - current.last_genuine_ns
                ) / self._ns,
                "abs_ret_15": abs(ret_15) if math.isfinite(ret_15) else math.nan,
                "rv_60": rv_60,
            }
            return Hazard005FFeatureVector(
                scope_id=scope_id,
                grid_time_ns=query_ns,
                observed_monotonic_ns=current.observed_monotonic_ns,
                values=values,
                source_version=current.source_version,
            )

    @staticmethod
    def _logit(value: float) -> float:
        if not 0.0 < value < 1.0:
            return math.nan
        return math.log(value / (1.0 - value))

    def _return(
        self,
        scope: _HazardScope,
        query_ns: int,
        lag_s: int,
    ) -> float:
        current = self._asof(scope, query_ns)
        past = self._asof(scope, query_ns - lag_s * self._ns)
        if current is None or past is None or current.segment != past.segment:
            return math.nan
        current_logit = self._logit(current.midpoint)
        past_logit = self._logit(past.midpoint)
        if not math.isfinite(current_logit) or not math.isfinite(past_logit):
            return math.nan
        return current_logit - past_logit

    @staticmethod
    def _asof(
        scope: _HazardScope,
        query_ns: int,
    ) -> _HazardState | None:
        index = bisect_right(scope.state_times_ns, query_ns) - 1
        return scope.states[index] if index >= 0 else None

    def _genuine_count(
        self,
        scope: _HazardScope,
        query_ns: int,
        window_s: int,
    ) -> int:
        lower_ns = query_ns - window_s * self._ns
        return sum(
            count
            for bin_time_ns, count in scope.genuine_bins_ns.items()
            if lower_ns < bin_time_ns <= query_ns
        )


class Frozen005FEvaluator:
    evaluator_id = "005f-frozen-runtime"
    version = "candidate-runtime-001"
    research_id = HAZARD005F_RESEARCH_ID
    frozen_spec_version = HAZARD005F_FROZEN_SPEC_VERSION
    feature_schema_hash = HAZARD005F_SCHEMA_HASH

    def __init__(
        self,
        provider: Hazard005FFeatureProvider | None = None,
        regime_provider: Hazard005FRegimeProvider | None = None,
        *,
        scorers: Mapping[str, ProbabilityScorer] | None = None,
    ) -> None:
        self._provider = provider or NullHazard005FFeatureProvider()
        self._regime_provider = regime_provider or NullHazard005FRegimeProvider()
        self._scorers = dict(scorers or {})

    def metadata(self, snapshot: CanonicalShadowSnapshot) -> RuntimeEvaluatorMetadata:
        vector = self._provider.feature_vector(snapshot)
        if vector is None:
            return RuntimeEvaluatorMetadata(
                research_id=self.research_id,
                frozen_spec_version=self.frozen_spec_version,
                artifact_hash=self._artifact_hash() if self._scorers else None,
                expected_artifact_hash=self._expected_artifact_hash(),
                feature_schema_hash=self.feature_schema_hash,
                ready=False,
                readiness_reason="required_orderbook_history_unavailable",
                freshness_seconds=None,
                quality_flags=("005f:genuine_bbo_not_websocket_age",),
            )

        regime = self._regime_provider.regime(snapshot)
        if regime is None:
            return RuntimeEvaluatorMetadata(
                research_id=self.research_id,
                frozen_spec_version=self.frozen_spec_version,
                artifact_hash=self._artifact_hash() if self._scorers else None,
                expected_artifact_hash=self._expected_artifact_hash(),
                feature_schema_hash=self.feature_schema_hash,
                ready=False,
                readiness_reason="regime_unavailable",
                freshness_seconds=None,
                quality_flags=("005f:pre_active_semantics_frozen",),
            )

        required = (
            ("PRE_ELECTION|clock|UPDATE_HAZARD",)
            if regime == "PRE_ELECTION"
            else (
                "ACTIVE_RESULTS|clock|UPDATE_HAZARD",
                "ACTIVE_RESULTS|clock|JUMP_HAZARD",
            )
        )
        missing = tuple(key for key in required if key not in self._scorers)
        if missing:
            return RuntimeEvaluatorMetadata(
                research_id=self.research_id,
                frozen_spec_version=self.frozen_spec_version,
                artifact_hash=self._artifact_hash() if self._scorers else None,
                expected_artifact_hash=self._expected_artifact_hash(),
                feature_schema_hash=self.feature_schema_hash,
                ready=False,
                readiness_reason="model_artifact_missing:" + ",".join(missing),
                freshness_seconds=None,
                quality_flags=(
                    "005f:no_refit",
                    "005f:frozen_artifact_hashes_required",
                ),
            )

        bad_hash = tuple(
            key
            for key in required
            if self._scorers[key].artifact_hash != HAZARD005F_SCORER_HASHES[key]
        )
        if bad_hash:
            return RuntimeEvaluatorMetadata(
                research_id=self.research_id,
                frozen_spec_version=self.frozen_spec_version,
                artifact_hash=self._artifact_hash(),
                expected_artifact_hash=self._expected_artifact_hash(),
                feature_schema_hash=self.feature_schema_hash,
                ready=False,
                readiness_reason="model_artifact_hash_mismatch:" + ",".join(bad_hash),
                freshness_seconds=None,
                quality_flags=("005f:frozen_artifact_hashes_required",),
            )

        if any(
            not math.isfinite(vector.values[name])
            for key in required
            for name in HAZARD005F_SCHEMA[key]
        ):
            return RuntimeEvaluatorMetadata(
                research_id=self.research_id,
                frozen_spec_version=self.frozen_spec_version,
                artifact_hash=self._artifact_hash(),
                expected_artifact_hash=self._expected_artifact_hash(),
                feature_schema_hash=self.feature_schema_hash,
                ready=False,
                readiness_reason="required_orderbook_history_unavailable",
                freshness_seconds=None,
                quality_flags=("005f:insufficient_exact_grid_history",),
            )

        age = max(
            0.0,
            (snapshot.observed_monotonic_ns - vector.observed_monotonic_ns) / 1e9,
        )
        return RuntimeEvaluatorMetadata(
            research_id=self.research_id,
            frozen_spec_version=self.frozen_spec_version,
            artifact_hash=self._artifact_hash(),
            expected_artifact_hash=self._expected_artifact_hash(),
            feature_schema_hash=self.feature_schema_hash,
            ready=True,
            readiness_reason=None,
            freshness_seconds=age,
            quality_flags=(
                "005f:genuine_age_ns_exact",
                f"005f:regime:{regime}",
            ),
        )

    def evaluate(
        self,
        snapshot: CanonicalShadowSnapshot,
    ) -> Hazard005FSignal | None:
        metadata = self.metadata(snapshot)
        if not metadata.ready:
            return None
        vector = self._provider.feature_vector(snapshot)
        regime = self._regime_provider.regime(snapshot)
        if vector is None or regime is None:
            return None

        if regime == "PRE_ELECTION":
            update_key = "PRE_ELECTION|clock|UPDATE_HAZARD"
            update = self._score(update_key, vector)
            jump = None
        else:
            update_key = "ACTIVE_RESULTS|clock|UPDATE_HAZARD"
            jump_key = "ACTIVE_RESULTS|clock|JUMP_HAZARD"
            update = self._score(update_key, vector)
            jump = self._score(jump_key, vector)

        return Hazard005FSignal(
            update_hazard=update,
            jump_hazard=jump,
            observed_monotonic_ns=vector.observed_monotonic_ns,
            quality_flags=("005f:frozen_exact_runtime",),
            payload={
                "research_id": self.research_id,
                "frozen_spec_version": self.frozen_spec_version,
                "feature_schema_hash": self.feature_schema_hash,
                "regime": regime,
                "grid_time_ns": vector.grid_time_ns,
                "source_version": vector.source_version,
            },
        )

    def _score(
        self,
        key: str,
        vector: Hazard005FFeatureVector,
    ) -> float:
        values = tuple(
            float(vector.values[name]) for name in HAZARD005F_SCHEMA[key]
        )
        score = float(self._scorers[key].score(values))
        if not math.isfinite(score) or not 0.0 <= score <= 1.0:
            raise ValueError(
                f"{self._scorers[key].scorer_id} returned invalid probability"
            )
        return score

    def _artifact_hash(self) -> str:
        payload = {
            key: self._scorers[key].artifact_hash
            for key in sorted(self._scorers)
            if key in HAZARD005F_SCHEMA
        }
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()

    @staticmethod
    def _expected_artifact_hash() -> str:
        return hashlib.sha256(
            json.dumps(
                HAZARD005F_EXPECTED_ARTIFACT_HASHES,
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()


def pred006_live_parity_matrix() -> tuple[FeatureParityRecord, ...]:
    """Current-stack parity without a separate exact custody feature source."""
    return NullPred006FeatureProvider().parity()
