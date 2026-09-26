"""Canonical, serializable research-evaluation contracts."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Any


STANDARD_HORIZONS = (
    timedelta(seconds=1),
    timedelta(seconds=5),
    timedelta(seconds=30),
    timedelta(minutes=1),
    timedelta(minutes=5),
)


class SplitMethod(StrEnum):
    WALK_FORWARD = "WALK_FORWARD"
    LEAVE_EVENT_OUT = "LEAVE_EVENT_OUT"
    LEAVE_FAMILY_OUT = "LEAVE_FAMILY_OUT"
    CHRONOLOGICAL_EVENT_HOLDOUT = "CHRONOLOGICAL_EVENT_HOLDOUT"


class NegativeControlKind(StrEnum):
    ZERO_SIGNAL = "ZERO_SIGNAL"
    DELAYED_PAST_ONLY = "DELAYED_PAST_ONLY"
    FEATURE_EXCLUSION = "FEATURE_EXCLUSION"


class EventBootstrapWeighting(StrEnum):
    OBSERVATION_WEIGHTED_CLUSTER = "OBSERVATION_WEIGHTED_CLUSTER"
    EQUAL_EVENT = "EQUAL_EVENT"


@dataclass(frozen=True, slots=True)
class DatasetVersion:
    dataset_id: str
    schema_version: str
    manifest_sha256: str
    source_version: str | None = None

    def __post_init__(self) -> None:
        if not self.dataset_id or not self.schema_version:
            raise ValueError("dataset_id and schema_version must be non-blank")
        digest = self.manifest_sha256.lower()
        if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise ValueError("manifest_sha256 must be a 64-character SHA-256 hex digest")


@dataclass(frozen=True, slots=True)
class WalkForwardProtocol:
    training_window: timedelta
    development_window: timedelta
    holdout_window: timedelta
    step: timedelta
    expanding_training: bool = False

    def __post_init__(self) -> None:
        values = (
            self.training_window,
            self.development_window,
            self.holdout_window,
            self.step,
        )
        if any(value <= timedelta(0) for value in values):
            raise ValueError("walk-forward windows and step must be positive")


@dataclass(frozen=True, slots=True)
class FDRProtocol:
    family_id: str
    alpha: Decimal
    hypothesis_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.family_id:
            raise ValueError("FDR family_id must be non-blank")
        if not (Decimal("0") < self.alpha <= Decimal("1")):
            raise ValueError("FDR alpha must be in (0, 1]")
        if not self.hypothesis_ids or any(not value for value in self.hypothesis_ids):
            raise ValueError("FDR search space requires explicit hypothesis IDs")
        if len(self.hypothesis_ids) != len(set(self.hypothesis_ids)):
            raise ValueError("FDR hypothesis IDs must be unique")


@dataclass(frozen=True, slots=True)
class BootstrapProtocol:
    method: str = "moving_block"
    draws: int = 1000
    block_size: int = 10
    event_weighting: EventBootstrapWeighting = (
        EventBootstrapWeighting.OBSERVATION_WEIGHTED_CLUSTER
    )

    def __post_init__(self) -> None:
        if not self.method:
            raise ValueError("bootstrap method must be non-blank")
        if self.draws < 1 or self.block_size < 1:
            raise ValueError("bootstrap draws and block size must be positive")


@dataclass(frozen=True, slots=True)
class StabilityProtocol:
    tolerance: Decimal

    def __post_init__(self) -> None:
        if self.tolerance < Decimal("0"):
            raise ValueError("stability tolerance must be non-negative")


@dataclass(frozen=True, slots=True)
class NegativeControlSpec:
    name: str
    kind: NegativeControlKind
    delay: timedelta | None = None
    excluded_components: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("negative-control name must be non-blank")
        if self.kind is NegativeControlKind.DELAYED_PAST_ONLY:
            if self.delay is None or self.delay <= timedelta(0):
                raise ValueError("delayed past-only control requires a positive delay")
        elif self.delay is not None:
            raise ValueError("delay is only valid for DELAYED_PAST_ONLY controls")
        if self.kind is NegativeControlKind.FEATURE_EXCLUSION and not self.excluded_components:
            raise ValueError("feature-exclusion control requires excluded components")


@dataclass(frozen=True, slots=True)
class AblationSpec:
    name: str
    removed_components: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.name or not self.removed_components:
            raise ValueError("ablation requires a name and explicit removed components")


@dataclass(frozen=True, slots=True)
class ExecutionStressSpec:
    name: str
    extra_cost_per_share: Decimal | None = None
    execution_delay: timedelta | None = None

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("stress name must be non-blank")
        if self.extra_cost_per_share is not None and self.extra_cost_per_share < 0:
            raise ValueError("extra execution cost must not be negative")
        if self.execution_delay is not None and self.execution_delay < timedelta(0):
            raise ValueError("execution delay must not be negative")


@dataclass(frozen=True, slots=True)
class EvidencePolicy:
    require_statistical_tests: bool = True
    require_fdr: bool = True
    require_bootstrap: bool = True
    require_stability: bool = True
    require_negative_controls: bool = True
    require_ablations: bool = True
    require_execution_stress: bool = True


@dataclass(frozen=True, slots=True)
class ResearchEvaluationSpec:
    experiment_id: str
    hypothesis_family: str
    economic_mechanism: str
    dataset: DatasetVersion
    feature_set_id: str
    feature_availability_rule: str
    target: str
    target_horizons: tuple[timedelta, ...]
    market_universe: tuple[str, ...]
    event_universe: tuple[str, ...]
    event_family_universe: tuple[str, ...]
    split_method: SplitMethod
    walk_forward: WalkForwardProtocol | None
    fdr: FDRProtocol
    bootstrap: BootstrapProtocol
    stability: StabilityProtocol
    disposition_policy: EvidencePolicy
    purge: bool = True
    embargo: timedelta = timedelta(0)
    parameter_grid: tuple[tuple[str, tuple[str, ...]], ...] = ()
    negative_controls: tuple[NegativeControlSpec, ...] = ()
    ablations: tuple[AblationSpec, ...] = ()
    execution_assumptions: tuple[str, ...] = ()
    execution_stresses: tuple[ExecutionStressSpec, ...] = ()
    expected_failure_condition: str = ""

    def __post_init__(self) -> None:
        required = (
            self.experiment_id,
            self.hypothesis_family,
            self.economic_mechanism,
            self.feature_set_id,
            self.feature_availability_rule,
            self.target,
        )
        if any(not value for value in required):
            raise ValueError("canonical research identifiers must be non-blank")
        if not self.target_horizons or any(h <= timedelta(0) for h in self.target_horizons):
            raise ValueError("target_horizons must be positive")
        if len(self.target_horizons) != len(set(self.target_horizons)):
            raise ValueError("target_horizons must be unique")
        if self.embargo < timedelta(0):
            raise ValueError("embargo must not be negative")
        if self.split_method is SplitMethod.WALK_FORWARD and self.walk_forward is None:
            raise ValueError("WALK_FORWARD requires an explicit walk_forward protocol")
        if self.split_method is not SplitMethod.WALK_FORWARD and self.walk_forward is not None:
            raise ValueError("walk_forward protocol is only valid for WALK_FORWARD")
        names = [name for name, _ in self.parameter_grid]
        if len(names) != len(set(names)):
            raise ValueError("parameter_grid names must be unique")
        _require_unique_names(self.negative_controls, "negative controls")
        _require_unique_names(self.ablations, "ablations")
        _require_unique_names(self.execution_stresses, "execution stresses")
        for label, values in (
            ("market universe", self.market_universe),
            ("event universe", self.event_universe),
            ("event-family universe", self.event_family_universe),
        ):
            if len(values) != len(set(values)):
                raise ValueError(f"{label} must not contain duplicates")


def _require_unique_names(values: tuple[Any, ...], label: str) -> None:
    names = [value.name for value in values]
    if len(names) != len(set(names)):
        raise ValueError(f"{label} names must be unique")


def canonical_data(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, timedelta):
        seconds = Decimal(value.days * 86400 + value.seconds)
        micros = Decimal(value.microseconds) / Decimal(1_000_000)
        return str(seconds + micros)
    if isinstance(value, StrEnum):
        return value.value
    if hasattr(value, "__dataclass_fields__"):
        return {k: canonical_data(v) for k, v in asdict(value).items()}
    if isinstance(value, dict):
        ordered = sorted(value.items(), key=lambda item: str(item[0]))
        return {str(k): canonical_data(v) for k, v in ordered}
    if isinstance(value, (tuple, list)):
        return [canonical_data(v) for v in value]
    return value


def canonical_json_bytes(value: Any) -> bytes:
    payload = json.dumps(canonical_data(value), sort_keys=True, separators=(",", ":"))
    return (payload + "\n").encode()


def config_hash(spec: ResearchEvaluationSpec) -> str:
    return hashlib.sha256(canonical_json_bytes(spec)).hexdigest()


@dataclass(frozen=True, slots=True)
class RunIdentity:
    run_id: str
    config_hash: str
    code_revision: str
    dataset_hash: str

    @property
    def display_id(self) -> str:
        return self.run_id[:16]


def make_run_identity(spec: ResearchEvaluationSpec, code_revision: str) -> RunIdentity:
    if not code_revision:
        raise ValueError("code_revision must be supplied explicitly")
    cfg_hash = config_hash(spec)
    payload = {
        "config_hash": cfg_hash,
        "dataset_hash": spec.dataset.manifest_sha256.lower(),
        "code_revision": code_revision,
    }
    run_id = hashlib.sha256(canonical_json_bytes(payload)).hexdigest()
    return RunIdentity(run_id, cfg_hash, code_revision, spec.dataset.manifest_sha256.lower())
