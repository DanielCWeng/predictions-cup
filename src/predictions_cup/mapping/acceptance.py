"""Acceptance evidence for the MAPPING-001 live crosswalk gate."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from predictions_cup.mapping.models import (
    MappingDocument,
    MappingOverrideDocument,
    MappingStatus,
)


def build_acceptance_evidence(
    *,
    document: MappingDocument,
    overrides: MappingOverrideDocument,
    overrides_path: Path | None,
    json_path: Path,
    csv_path: Path,
    summary_path: Path,
    smoke_requested: bool,
    mapped_token_ids: tuple[str, ...],
    books_returned: int,
) -> dict[str, object]:
    """Build deterministic evidence only when the live acceptance gate has actually passed."""
    if overrides_path is None:
        raise ValueError("live acceptance evidence requires a reviewer-owned overrides artifact")
    if not overrides_path.is_file():
        raise ValueError(f"reviewer-owned overrides artifact does not exist: {overrides_path}")
    if overrides.tournament_id != document.tournament_id:
        raise ValueError("reviewer-owned overrides do not match the mapping tournament")

    if any(record.status is not MappingStatus.VERIFIED for record in document.records):
        raise ValueError("live acceptance evidence requires every SIG exchange to be VERIFIED")

    document_exchange_ids = {record.sig_exchange_id for record in document.records}
    override_exchange_ids = {record.sig_exchange_id for record in overrides.records}
    if document_exchange_ids != override_exchange_ids:
        raise ValueError(
            "reviewer-owned overrides must cover exactly the live SIG exchange universe"
        )

    if not smoke_requested:
        raise ValueError("live acceptance evidence requires --smoke-clob")
    unique_token_ids = tuple(sorted(set(mapped_token_ids)))
    if not unique_token_ids:
        raise ValueError("live acceptance evidence requires at least one verified mapped token")
    if len(unique_token_ids) != len(mapped_token_ids):
        raise ValueError("mapped token smoke set contains duplicate token IDs")
    if books_returned != len(unique_token_ids):
        raise ValueError(
            "CLOB smoke evidence is incomplete: returned book count does not match token count"
        )

    required_artifacts = {
        "crosswalk_json": json_path,
        "crosswalk_csv": csv_path,
        "summary_json": summary_path,
        "reviewer_overrides": overrides_path,
    }
    missing = [name for name, path in required_artifacts.items() if not path.is_file()]
    if missing:
        raise ValueError(f"live acceptance artifacts are missing: {sorted(missing)!r}")

    return {
        "schema_version": 1,
        "tournament_id": document.tournament_id,
        "sig_exchange_count": len(document.records),
        "reviewer_override_count": len(overrides.records),
        "all_records_verified": True,
        "artifact_sha256": {
            name: _sha256_file(path) for name, path in sorted(required_artifacts.items())
        },
        "clob_smoke": {
            "passed": True,
            "mapped_token_count": len(unique_token_ids),
            "books_returned": books_returned,
            "mapped_token_ids": list(unique_token_ids),
        },
    }


def acceptance_evidence_json(evidence: dict[str, object]) -> str:
    """Serialize acceptance evidence deterministically for review and version control."""
    return json.dumps(evidence, indent=2, sort_keys=True) + "\n"


def write_acceptance_evidence(
    path: Path,
    *,
    document: MappingDocument,
    overrides: MappingOverrideDocument,
    overrides_path: Path | None,
    json_path: Path,
    csv_path: Path,
    summary_path: Path,
    smoke_requested: bool,
    mapped_token_ids: tuple[str, ...],
    books_returned: int,
) -> None:
    """Write auditable evidence after all credentialed live acceptance checks pass."""
    evidence = build_acceptance_evidence(
        document=document,
        overrides=overrides,
        overrides_path=overrides_path,
        json_path=json_path,
        csv_path=csv_path,
        summary_path=summary_path,
        smoke_requested=smoke_requested,
        mapped_token_ids=mapped_token_ids,
        books_returned=books_returned,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(acceptance_evidence_json(evidence), encoding="utf-8")


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
