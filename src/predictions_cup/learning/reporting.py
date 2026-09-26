"""Deterministic BUILD-008 reports and durable research-ledger records."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any

from predictions_cup.learning.research_spec import EvidencePolicy, canonical_data


class ResearchDisposition(StrEnum):
    PROMOTED = "PROMOTED"
    REJECTED = "REJECTED"
    INCONCLUSIVE = "INCONCLUSIVE"
    PLANNED = "PLANNED"
    RUNNING = "RUNNING"


@dataclass(frozen=True, slots=True)
class ResearchReport:
    schema_version: str
    run_id: str
    experiment_id: str
    hypothesis_family: str
    economic_mechanism: str
    code_revision: str
    dataset_id: str
    dataset_version: str
    dataset_hash: str
    config_hash: str
    universe: dict[str, tuple[str, ...]]
    folds: tuple[dict[str, Any], ...]
    purge_embargo: dict[str, Any]
    sample_counts: dict[str, int]
    horizon_results: tuple[dict[str, Any], ...]
    predictive_metrics: dict[str, Any]
    gross_executable_metrics: dict[str, Any]
    net_executable_metrics: dict[str, Any] | None
    bootstrap_results: tuple[dict[str, Any], ...]
    raw_statistical_tests: tuple[dict[str, Any], ...]
    fdr_results: tuple[dict[str, Any], ...]
    parameter_stability: dict[str, Any] | None
    negative_controls: tuple[dict[str, Any], ...]
    ablations: tuple[dict[str, Any], ...]
    execution_stresses: tuple[dict[str, Any], ...]
    invalidity_counts: dict[str, int]
    known_limitations: tuple[str, ...]
    disposition: ResearchDisposition
    evidence_policy: EvidencePolicy
    requested_disposition: ResearchDisposition | None = None
    disposition_evidence_missing: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        requested = (
            self.disposition
            if self.requested_disposition is None
            else self.requested_disposition
        )
        missing = _missing_required_evidence(self, self.evidence_policy)
        object.__setattr__(self, "requested_disposition", requested)
        object.__setattr__(self, "disposition_evidence_missing", missing)
        if requested is ResearchDisposition.PROMOTED and missing:
            object.__setattr__(self, "disposition", ResearchDisposition.INCONCLUSIVE)

    def serialize(self) -> bytes:
        payload = canonical_data(asdict(self))
        return (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _missing_required_evidence(
    report: ResearchReport,
    policy: EvidencePolicy,
) -> tuple[str, ...]:
    missing: list[str] = []
    if policy.require_statistical_tests and not report.raw_statistical_tests:
        missing.append("statistical_tests")
    if policy.require_fdr and not report.fdr_results:
        missing.append("fdr")
    if policy.require_bootstrap and not report.bootstrap_results:
        missing.append("bootstrap")
    if policy.require_stability and report.parameter_stability is None:
        missing.append("parameter_stability")
    if policy.require_negative_controls and not report.negative_controls:
        missing.append("negative_controls")
    if policy.require_ablations and not report.ablations:
        missing.append("ablations")
    if policy.require_execution_stress and not report.execution_stresses:
        missing.append("execution_stresses")
    return tuple(missing)


@dataclass(frozen=True, slots=True)
class ResearchLedgerEntry:
    experiment_id: str
    hypothesis_family: str
    run_id: str
    data_version: str
    code_revision: str
    disposition: ResearchDisposition
    headline_evidence: str
    report_path: str
    decision_date: str | None = None
    notes: str = ""

    def markdown_row(self) -> str:
        values = (
            self.experiment_id,
            self.hypothesis_family,
            self.run_id,
            self.data_version,
            self.code_revision,
            self.disposition.value,
            self.headline_evidence,
            self.report_path,
            self.decision_date or "",
            self.notes,
        )
        return "| " + " | ".join(value.replace("|", "\\|") for value in values) + " |"
