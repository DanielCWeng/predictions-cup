from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from pathlib import Path

import pytest

from predictions_cup.mapping.acceptance import (
    build_acceptance_evidence,
    write_acceptance_evidence,
)
from predictions_cup.mapping.models import (
    MappingClass,
    MappingDirection,
    MappingDocument,
    MappingOverride,
    MappingOverrideDocument,
    MappingStatus,
    MarketMapping,
    PolymarketContractIdentity,
    PolymarketOverrideLeg,
)


def _document(status: MappingStatus = MappingStatus.VERIFIED) -> MappingDocument:
    identity = PolymarketContractIdentity(
        market_id="poly-1",
        condition_id="cid-1",
        event_id="event-1",
        slug="poly-1",
        question="Will the Republican Party win?",
        outcomes=("Yes", "No"),
        token_ids=("token-yes", "token-no"),
        mapped_outcome="Yes",
        mapped_token_id="token-yes",
    )
    return MappingDocument(
        tournament_id="tournament-2026",
        records=(
            MarketMapping(
                sig_tournament_id="tournament-2026",
                sig_market_id="sig-market",
                sig_market_title="Will the Republican Party win?",
                sig_exchange_id="sig-exchange",
                sig_outcome_label="Yes",
                mapping_class=MappingClass.EXACT,
                mapping_direction=MappingDirection.SAME,
                mapping_confidence=Decimal("1"),
                status=status,
                direct_polymarket=identity,
                semantic_notes="Reviewed semantic equivalence.",
                resolution_notes="Reviewed settlement semantics.",
            ),
        ),
    )


def _overrides() -> MappingOverrideDocument:
    return MappingOverrideDocument(
        tournament_id="tournament-2026",
        records=(
            MappingOverride(
                sig_market_id="sig-market",
                sig_exchange_id="sig-exchange",
                sig_outcome_label="Yes",
                mapping_class=MappingClass.EXACT,
                mapping_direction=MappingDirection.SAME,
                mapping_confidence=Decimal("1"),
                status=MappingStatus.VERIFIED,
                polymarket_legs=(
                    PolymarketOverrideLeg(market_id="poly-1", outcome="Yes"),
                ),
                semantic_notes="Reviewed semantic equivalence.",
                resolution_notes="Reviewed settlement semantics.",
            ),
        ),
    )


def _artifact_paths(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    json_path = tmp_path / "crosswalk.json"
    csv_path = tmp_path / "crosswalk.csv"
    summary_path = tmp_path / "summary.json"
    overrides_path = tmp_path / "overrides.json"
    json_path.write_text('{"crosswalk": true}\n', encoding="utf-8")
    csv_path.write_text("sig_exchange_id\nsig-exchange\n", encoding="utf-8")
    summary_path.write_text('{"sig_exchanges": 1}\n', encoding="utf-8")
    overrides_path.write_text('{"reviewed": true}\n', encoding="utf-8")
    return json_path, csv_path, summary_path, overrides_path


def test_live_acceptance_evidence_records_review_and_clob_smoke(tmp_path: Path) -> None:
    json_path, csv_path, summary_path, overrides_path = _artifact_paths(tmp_path)
    evidence_path = tmp_path / "acceptance.json"

    write_acceptance_evidence(
        evidence_path,
        document=_document(),
        overrides=_overrides(),
        overrides_path=overrides_path,
        json_path=json_path,
        csv_path=csv_path,
        summary_path=summary_path,
        smoke_requested=True,
        mapped_token_ids=("token-yes",),
        books_returned=1,
    )

    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    assert evidence["all_records_verified"] is True
    assert evidence["sig_exchange_count"] == 1
    assert evidence["reviewer_override_count"] == 1
    assert evidence["clob_smoke"] == {
        "books_returned": 1,
        "mapped_token_count": 1,
        "mapped_token_ids": ["token-yes"],
        "passed": True,
    }
    assert evidence["artifact_sha256"]["crosswalk_json"] == hashlib.sha256(
        json_path.read_bytes()
    ).hexdigest()
    assert evidence["artifact_sha256"]["reviewer_overrides"] == hashlib.sha256(
        overrides_path.read_bytes()
    ).hexdigest()


def test_live_acceptance_evidence_rejects_unverified_mapping(tmp_path: Path) -> None:
    json_path, csv_path, summary_path, overrides_path = _artifact_paths(tmp_path)

    with pytest.raises(ValueError, match="every SIG exchange to be VERIFIED"):
        build_acceptance_evidence(
            document=_document(MappingStatus.REVIEW_REQUIRED),
            overrides=_overrides(),
            overrides_path=overrides_path,
            json_path=json_path,
            csv_path=csv_path,
            summary_path=summary_path,
            smoke_requested=True,
            mapped_token_ids=("token-yes",),
            books_returned=1,
        )


def test_live_acceptance_evidence_requires_recorded_clob_smoke(tmp_path: Path) -> None:
    json_path, csv_path, summary_path, overrides_path = _artifact_paths(tmp_path)

    with pytest.raises(ValueError, match="requires --smoke-clob"):
        build_acceptance_evidence(
            document=_document(),
            overrides=_overrides(),
            overrides_path=overrides_path,
            json_path=json_path,
            csv_path=csv_path,
            summary_path=summary_path,
            smoke_requested=False,
            mapped_token_ids=(),
            books_returned=0,
        )


def test_live_acceptance_evidence_requires_exact_override_coverage(tmp_path: Path) -> None:
    json_path, csv_path, summary_path, overrides_path = _artifact_paths(tmp_path)
    empty_overrides = MappingOverrideDocument(tournament_id="tournament-2026")

    with pytest.raises(ValueError, match="cover exactly"):
        build_acceptance_evidence(
            document=_document(),
            overrides=empty_overrides,
            overrides_path=overrides_path,
            json_path=json_path,
            csv_path=csv_path,
            summary_path=summary_path,
            smoke_requested=True,
            mapped_token_ids=("token-yes",),
            books_returned=1,
        )
