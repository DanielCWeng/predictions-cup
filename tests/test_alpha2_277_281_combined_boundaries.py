"""Offline composition regression: #277 schema validity MUST NOT promote #281.

All files are synthetic temporary fixtures. No venue, maker or source admission.
"""

from pathlib import Path
from typing import Any

import pytest

from predictions_cup.research.offline_paired_tape_gate import validate_offline_tape
from test_alpha2_229_corrected_independent import _audit
from test_offline_paired_tape_gate import _sample


def test_both_research_gates_positive_still_no_real_paper_admission(
    tmp_path: Path,
) -> None:
    schema_dir = tmp_path / "schema"
    schema_dir.mkdir()
    manifest_and_files, manifest_sha = _sample(schema_dir)
    schema = validate_offline_tape(
        *manifest_and_files, expected_manifest_sha256=manifest_sha,
    )
    assert schema.verdict == "SCHEMA_ONLY/PAPER_NOT_ADMITTED"
    assert schema.schema_valid_intervals == 1
    assert schema.external_issuer_authenticated is False
    assert schema.source_admitted is False
    assert schema.paper_admitted is False
    assert schema.network_requests == 0

    mirror_dir = tmp_path / "mirror"
    mirror_dir.mkdir()
    report, ledger, snapshots = _audit(mirror_dir)
    assert len(snapshots) == 1
    assert report["offline_liquidity_eligible_observations"] == 1
    assert report["independently_certified_causal_paired_paper_observations"] == 0
    assert report["native_sig_event_time"] is None
    assert report["native_sig_revision"] is None
    assert report["real_fill_probability"] is None
    assert report["net_pnl"] is None
    assert ledger[0]["paired_depth_trusted"] is False
    assert ledger[0]["certified_paired_paper"] is False
    # No bridge/adapter linking these two modules is implemented in production.


@pytest.mark.parametrize(
    ("case", "expected_reason"),
    [
        ("spoofed_publisher_time", "NONCAUSAL_CLOCK_ORDER"),
        ("pm_seed_mismatch", "PM_SEED_EPOCH_MISMATCH"),
        ("pm_ws_disconnected", "PM_WS_CONTINUITY_LOST"),
        ("reversed_mapping", "MAPPING_ORIENTATION_INVALID"),
    ],
)
def test_invalid_native_pair_cannot_be_promoted_by_a_valid_indicative_mirror(
    tmp_path: Path, case: str, expected_reason: str,
) -> None:
    schema_dir = tmp_path / "schema"
    schema_dir.mkdir()

    def alter(
        mapping: dict[str, Any],
        sig: dict[str, Any],
        pm: dict[str, Any],
        _manifest: dict[str, Any],
    ) -> None:
        if case == "spoofed_publisher_time":
            sig["source_at"] = "2026-10-16T13:00:00Z"
        elif case == "pm_seed_mismatch":
            pm["connection_seed_generation"] = 99
        elif case == "pm_ws_disconnected":
            pm["ws_contiguous"] = False
        elif case == "reversed_mapping":
            mapping["mapping_direction"] = "COMPLEMENT"

    archive, manifest_sha = _sample(schema_dir, alter)
    result = validate_offline_tape(*archive, expected_manifest_sha256=manifest_sha)
    assert result.verdict == "PAIRED_DEPTH_NOT_CERTIFIED"
    assert result.schema_valid_intervals == 0
    assert expected_reason in [reason for _interval, reason in result.rejections]
    assert result.source_admitted is False
    assert result.paper_admitted is False
    assert result.external_issuer_authenticated is False

    mirror_dir = tmp_path / "mirror"
    mirror_dir.mkdir()
    report, ledger, snapshots = _audit(mirror_dir)
    assert len(snapshots) == 1
    assert report["offline_liquidity_eligible_observations"] == 1
    assert report["independently_certified_causal_paired_paper_observations"] == 0
    assert ledger[0]["paired_depth_trusted"] is False
