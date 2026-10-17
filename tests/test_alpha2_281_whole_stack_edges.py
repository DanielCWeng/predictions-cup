"""Extra independent #281 whole-stack adversarial invariants, fake-only."""

import json
from pathlib import Path
from typing import Any

import pytest

from predictions_cup.research.maker_depth_audit import (
    AuditError,
    accepted_specs,
    audit,
    sha256_file,
)
from test_alpha2_229_corrected_independent import _audit, _mapping, _row


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        ({"asks": []}, "ONE_SIDED_OR_ZERO_DEPTH"),
        ({"asks": [{"price": "0.45", "quantity": "20"}]}, "CROSSED_OR_LOCKED_BOOK"),
        ({"bids": [{"price": "NaN", "quantity": "20"}]}, "NONFINITE_DECIMAL"),
        ({"trusted_for_paired_paper": True}, "SPOOFED_SOURCE_PROVENANCE"),
        ({"source_clock_verified": True}, "SPOOFED_SOURCE_PROVENANCE"),
        ({"venue_timestamp": "2026-10-17T00:00:00+00:00"}, "SPOOFED_SOURCE_PROVENANCE"),
        ({"venue_revision": 123}, "SPOOFED_SOURCE_PROVENANCE"),
        ({"market_id": "spoofed"}, "MARKET_OR_TOURNAMENT_MISMATCH"),
    ],
)
def test_untrusted_or_malformed_current_main_source_rejected(
    tmp_path: Path, mutation: dict[str, Any], reason: str,
) -> None:
    specs = _mapping(tmp_path)
    record = {**_row(), **mutation}
    path = tmp_path / "untrusted.jsonl"
    path.write_text(json.dumps(record) + "\n", encoding="utf-8")
    report, ledger, snapshots = audit(path, sha256_file(path), specs)
    assert snapshots == []
    assert report["valid_two_sided_snapshots"] == 0
    assert report["independently_certified_causal_paired_paper_observations"] == 0
    assert ledger[0]["state"] == "REJECTED"
    assert ledger[0]["reason"] == reason
    assert ledger[0]["paired_depth_trusted"] is False
    assert ledger[0]["certified_paired_paper"] is False


def test_truncation_even_with_valid_hash_fails_closed(tmp_path: Path) -> None:
    specs = _mapping(tmp_path)
    path = tmp_path / "truncated.jsonl"
    path.write_text(json.dumps(_row()), encoding="utf-8")
    report, ledger, books = audit(path, sha256_file(path), specs)
    assert books == []
    assert report["input"]["complete_final_newline"] is False
    assert report["rejection_reasons"] == {"TRUNCATED_FINAL_LINE": 1}
    assert ledger[0]["paired_depth_trusted"] is False


def test_recomputed_mapping_hash_not_independent_attestation(tmp_path: Path) -> None:
    specs = _mapping(tmp_path)
    assert specs["947"].mapping_sha256
    mapping = tmp_path / "exact.json"
    acceptance = tmp_path / "accepted.json"
    original_hash = sha256_file(mapping)
    mapping.write_text(mapping.read_text().replace('"EXACT"', '"NEAR"'), encoding="utf-8")
    assert sha256_file(mapping) != original_hash
    with pytest.raises(AuditError, match="ACCEPTED_MAPPING_HASH_MISMATCH"):
        accepted_specs(mapping, acceptance, ("947",))


def test_valid_fake_is_indicative_only_without_native_clock_or_fills(tmp_path: Path) -> None:
    report, ledger, rows = _audit(tmp_path)
    assert len(rows) == 1
    assert ledger[0]["state"] == "INDICATIVE_LIQUIDITY_ONLY"
    assert ledger[0]["paired_depth_trusted"] is False
    assert ledger[0]["certified_paired_paper"] is False
    assert report["offline_liquidity_eligible_observations"] == 1
    assert report["independently_certified_causal_paired_paper_observations"] == 0
    assert report["source_book_age_ms"] is None
    assert report["native_sig_event_time"] is None
    assert report["native_sig_revision"] is None
    assert report["real_trades"] is None
    assert report["real_fill_probability"] is None
    assert report["net_pnl"] is None
    assert report["fee_per_share_assumption"] is None
    assert report["rehearsal_authorised"] is False
    assert report["live_trading_authorised"] is False
