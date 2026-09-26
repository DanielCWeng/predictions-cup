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
    purge: bool = True
    embargo: timedelta = timedelta(0)
    parameter_grid: tuple[tuple[str, tuple[str, ...]], ...] = ()
    negative_controls: tuple[str, ...] = ()
    ablations: tuple[str, ...] = ()
    execution_assumptions: tuple[str, ...] = ()
    execution_stresses: tuple[str, ...] = ()
    multiple_testing_family: str = "default"
    bootstrap_method: str = "moving_block"
    bootstrap_draws: int = 1000
    bootstrap_block_size: int = 10
    expected_failure_condition: str = ""

    def __post_init__(self) -> None:
        required = (
            self.experiment_id,
            self.hypothesis_family,
            self.economic_mechanism,
            self.feature_set_id,
            self.feature_availability_rule,
            self.target,
            self.multiple_testing_family,
        )
        if any(not value for value in required):
            raise ValueError("canonical research identifiers must be non-blank")
        if not self.target_horizons or any(h <= timedelta(0) for h in self.target_horizons):
            raise ValueError("target_horizons must be positive")
        if self.embargo < timedelta(0):
            raise ValueError("embargo must not be negative")
        if self.bootstrap_draws < 1 or self.bootstrap_block_size < 1:
            raise ValueError("bootstrap draws and block size must be positive")
        names = [name for name, _ in self.parameter_grid]
        if len(names) != len(set(names)):
            raise ValueError("parameter_grid names must be unique")


def canonical_data(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, timedelta):
        return str(Decimal(value.days * 86400 + value.seconds) + Decimal(value.microseconds) / Decimal(1_000_000))
    if isinstance(value, StrEnum):
        return value.value
    if hasattr(value, "__dataclass_fields__"):
        return {k: canonical_data(v) for k, v in asdict(value).items()}
    if isinstance(value, dict):
        return {str(k): canonical_data(v) for k, v in sorted(value.items(), key=lambda item: str(item[0]))}
    if isinstance(value, (tuple, list)):
        return [canonical_data(v) for v in value]
    return value


def canonical_json_bytes(value: Any) -> bytes:
    return (json.dumps(canonical_data(value), sort_keys=True, separators=(",", ":")) + "\n").encode()


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
