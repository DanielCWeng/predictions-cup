"""Canonical machine-readable analysis evidence for launch-time economics."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Mapping

import pyarrow as pa
import pyarrow.parquet as pq


@dataclass(frozen=True, slots=True)
class AnalysisUncertainty:
    """Dependence-aware uncertainty metadata for one analytical estimate."""

    point_estimate: float | None
    interval: tuple[float, float] | None
    method: str | None
    cluster_unit: str | None
    support: int
    reason: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "point_estimate": self.point_estimate,
            "interval": None if self.interval is None else list(self.interval),
            "method": self.method,
            "cluster_unit": self.cluster_unit,
            "support": self.support,
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class AnalysisEvidence:
    """Small immutable evidence envelope shared by launch-time analysis lanes."""

    analysis_id: str
    analysis_version: str
    generated_at: datetime
    window_start: datetime | None
    window_end: datetime | None
    git_sha: str
    config_hash: str
    mapping_hash: str | None
    source_watermarks: tuple[tuple[str, str], ...]
    source_health: tuple[tuple[str, str], ...]
    market_id: str | None
    exchange_id: str | None
    risk_group_ids: tuple[str, ...]
    sample_count: int
    independent_event_count: int
    metric_name: str
    metric_value: float | None
    uncertainty: AnalysisUncertainty | None
    denominator: str | None
    status: str
    reasons: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    horizon_seconds: int | None = None
    metric_unit: str | None = None
    method_version: str | None = None
    quality_flags: tuple[str, ...] = ()
    source_versions: tuple[tuple[str, str], ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "analysis_id": self.analysis_id,
            "analysis_version": self.analysis_version,
            "generated_at": self.generated_at.astimezone(UTC).isoformat(),
            "window_start": (
                None if self.window_start is None else self.window_start.astimezone(UTC).isoformat()
            ),
            "window_end": (
                None if self.window_end is None else self.window_end.astimezone(UTC).isoformat()
            ),
            "git_sha": self.git_sha,
            "config_hash": self.config_hash,
            "mapping_hash": self.mapping_hash,
            "source_watermarks": dict(self.source_watermarks),
            "source_health": dict(self.source_health),
            "market_id": self.market_id,
            "exchange_id": self.exchange_id,
            "risk_group_ids": list(self.risk_group_ids),
            "sample_count": self.sample_count,
            "independent_event_count": self.independent_event_count,
            "metric_name": self.metric_name,
            "metric_value": self.metric_value,
            "uncertainty": None if self.uncertainty is None else self.uncertainty.to_dict(),
            "denominator": self.denominator,
            "status": self.status,
            "reasons": list(self.reasons),
            "evidence_refs": list(self.evidence_refs),
            "horizon_seconds": self.horizon_seconds,
            "metric_unit": self.metric_unit,
            "method_version": self.method_version,
            "quality_flags": list(self.quality_flags),
            "source_versions": dict(self.source_versions),
        }


def canonical_config_hash(payload: Mapping[str, object]) -> str:
    """Hash a JSON-safe analysis configuration deterministically."""
    encoded = json.dumps(
        dict(payload),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def sha256_file(path: Path) -> str:
    """Return SHA-256 for an immutable evidence/config artifact."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_evidence(
    *,
    output_root: Path,
    snapshot: Mapping[str, object],
    records: tuple[AnalysisEvidence, ...],
) -> None:
    """Persist current JSON, append-only JSONL and rectangular Parquet evidence."""
    output_root.mkdir(parents=True, exist_ok=True)
    snapshot_path = output_root / "current.json"
    temporary = snapshot_path.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(dict(snapshot), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(snapshot_path)

    if not records:
        return

    rows = [record.to_dict() for record in records]
    with (output_root / "analysis_events.jsonl").open("a", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True) + "\n")

    pq.write_table(pa.Table.from_pylist(rows), output_root / "analysis_evidence.parquet")
