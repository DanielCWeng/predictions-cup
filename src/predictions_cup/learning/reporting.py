"""Deterministic BUILD-008 reports and durable research-ledger records."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any

from predictions_cup.learning.research_spec import canonical_data


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

    def serialize(self) -> bytes:
        payload = canonical_data(asdict(self))
        return (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode()


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
