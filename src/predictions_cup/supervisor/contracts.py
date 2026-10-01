"""Typed contracts for the read-only SUPERVISOR-001 control surface."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import IntEnum, StrEnum

SCHEMA_VERSION = "supervisor-001-v1"
BUNDLE_SCHEMA_VERSION = "supervisor-001-bundle-v1"
ACTION_SCHEMA_VERSION = "supervisor-001-action-v1"


class Severity(StrEnum):
    OK = "OK"
    WARN = "WARN"
    CRITICAL = "CRITICAL"
    UNKNOWN = "UNKNOWN"


class LaunchGate(StrEnum):
    PASS = "PASS"
    HOLD = "HOLD"


class HostRole(StrEnum):
    WEST_EXECUTION = "west-execution"
    EAST_ARCHIVE = "east-archive"


class RemediationLevel(IntEnum):
    OBSERVE = 0
    HOUSEKEEPING = 1
    SERVICE_RECOVERY = 2
    HOST_PROTECTION = 3
    ECONOMIC = 4


class ActionCode(StrEnum):
    PRUNE_SUPERVISOR_BUNDLES = "PRUNE_SUPERVISOR_BUNDLES"
    PRUNE_HOT_PARQUET = "PRUNE_HOT_PARQUET"
    CLEAR_SAFE_CACHE = "CLEAR_SAFE_CACHE"
    RESTART_SAFE_SERVICE = "RESTART_SAFE_SERVICE"
    STOP_NONESSENTIAL_SERVICE = "STOP_NONESSENTIAL_SERVICE"


@dataclass(frozen=True, slots=True)
class SourceStatus:
    source_id: str
    required: bool
    available: bool
    valid: bool
    fresh: bool
    observed_at: datetime | None
    read_at: datetime
    age_seconds: float | None
    reason: str | None = None
    detail: Mapping[str, object] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return {
            "source_id": self.source_id,
            "required": self.required,
            "available": self.available,
            "valid": self.valid,
            "fresh": self.fresh,
            "observed_at": _iso(self.observed_at),
            "read_at": _iso(self.read_at),
            "age_seconds": self.age_seconds,
            "reason": self.reason,
            "detail": dict(self.detail),
        }


@dataclass(frozen=True, slots=True)
class Finding:
    code: str
    severity: Severity
    summary: str
    evidence: Mapping[str, object] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return {
            "code": self.code,
            "severity": self.severity.value,
            "summary": self.summary,
            "evidence": dict(self.evidence),
        }


@dataclass(frozen=True, slots=True)
class SupervisorSnapshot:
    snapshot_id: str
    host_id: str
    host_role: HostRole
    git_head: str
    observed_at: datetime
    severity: Severity
    launch_gate: LaunchGate
    findings: tuple[Finding, ...]
    sources: tuple[SourceStatus, ...]
    sections: Mapping[str, object]

    @property
    def reason_codes(self) -> tuple[str, ...]:
        return tuple(finding.code for finding in self.findings)

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": SCHEMA_VERSION,
            "snapshot_id": self.snapshot_id,
            "host_id": self.host_id,
            "host_role": self.host_role.value,
            "git_head": self.git_head,
            "observed_at": _iso(self.observed_at),
            "severity": self.severity.value,
            "launch_gate": self.launch_gate.value,
            "reason_codes": list(self.reason_codes),
            "findings": [finding.to_dict() for finding in self.findings],
            "sources": [source.to_dict() for source in self.sources],
            "sections": dict(self.sections),
        }


def deterministic_id(prefix: str, payload: Mapping[str, object]) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
        default=str,
    ).encode("utf-8")
    return f"{prefix}-{hashlib.sha256(encoded).hexdigest()[:24]}"


def utc_now() -> datetime:
    return datetime.now(UTC)


def _iso(value: datetime | None) -> str | None:
    return None if value is None else value.astimezone(UTC).isoformat()
