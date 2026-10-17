"""Independent #311 v2 research metadata/provenance falsifiers and immutable X1/X2.

All row values are invented. An external reviewed source-rule map is required
for descriptive-only comparability; this does not verify publisher source.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from predictions_cup.analysis import live_diag
from predictions_cup.analysis.snapback_version_gate import (
    ANALYSIS_VERSION,
    EPISODE_RULE_VERSION,
    INCOMPARABLE,
    SNAPBACK_METHOD_VERSION,
    TRIGGER_RULE,
    compare_snapback_rows,
)

FROZEN = (
    ("test_redteam_x1_original_strict_acceptance.py",
     "b77e1da5f1823e4462362dfc24eb26c00805e2b0"),
    ("test_redteam_x2_original_strict_acceptance.py",
     "d7680140f646022af3555a7d603d53251f8449b6"),
    ("test_alpha2_x1x2_consumer_falsification.py",
     "7f25b308b4a5ec7131aaf87ad719de97c3fa0f15"),
    ("test_alpha2_x1_loader_depth_only_strict.py",
     "ab667345a38a52d546f87cf717624b3fd4926c18"),
)
REVIEWED_SHA = "a" * 40
OLD_V1_SHA = "b" * 40
RULE_MAP = {
    REVIEWED_SHA: EPISODE_RULE_VERSION,
    OLD_V1_SHA: "magnitude-threshold-episodes-v1",
}


@pytest.mark.parametrize("filename,expected", FROZEN)
def test_original_strict_x1_x2_and_loader_test_git_blobs(
    filename: str, expected: str,
) -> None:
    raw = Path(__file__).with_name(filename).read_bytes()
    assert hashlib.sha1(
        b"blob " + str(len(raw)).encode() + b"\x00" + raw,
    ).hexdigest() == expected


def _row(sha: str = REVIEWED_SHA, *, value: float = 0.2) -> dict[str, object]:
    meta = live_diag._signed_snapback_row_metadata(
        git_sha=sha, config_hash="invented-hash",
        mapping_hash="invented-mapping", max_quote_age_seconds=15.0,
    )
    return {
        **meta,
        "market_id": "invented-market", "exchange_id": "invented-exchange",
        "polymarket_token_id": "invented-token",
        "mapping_class": "invented-paired", "mapping_direction": "COMPLEMENT",
        "threshold_ticks": 2,
        "fraction_gap_closed_5s": value,
    }


def test_v2_export_tags_have_consistent_signed_x2_names() -> None:
    assert ANALYSIS_VERSION == "live-diag-001-v2"
    assert EPISODE_RULE_VERSION == "signed-threshold-episodes-v2"
    assert SNAPBACK_METHOD_VERSION == "snapback-signed-threshold-episodes-v2"
    assert "direct_sign_reversal" in TRIGGER_RULE
    row = _row()
    assert row["analysis_version"] == ANALYSIS_VERSION
    assert row["method_version"] == SNAPBACK_METHOD_VERSION
    assert row["episode_rule_version"] == EPISODE_RULE_VERSION
    assert row["trigger_rule"] == TRIGGER_RULE
    assert live_diag.ANALYSIS_VERSION == ANALYSIS_VERSION


def test_explicit_reviewed_v2_source_only_allows_descriptive_metric_delta() -> None:
    status, delta = compare_snapback_rows(
        _row(value=0.3), _row(value=0.4),
        metric_name="fraction_gap_closed_5s",
        verified_source_rules={REVIEWED_SHA: EPISODE_RULE_VERSION},
    )
    assert status == "DESCRIPTIVE_COMPARISON_ONLY"
    assert delta == pytest.approx(0.1)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("analysis_version", "live-diag-001-v1"),
        ("method_version", "snapback-threshold-episodes-v1"),
        ("episode_rule_version", "magnitude-threshold-episodes-v1"),
        ("trigger_rule", "threshold_crossing_rearms_only_below_threshold"),
        ("analysis_version", None),
        ("method_version", None),
        ("episode_rule_version", None),
        ("trigger_rule", None),
        ("git_sha", "UNKNOWN"),
        ("git_sha", OLD_V1_SHA),
        ("git_sha", "not a git sha"),
        ("config_hash", "conflicting-config"),
        ("mapping_direction", "SAME"),
        ("threshold_ticks", 3),
        ("fraction_gap_closed_5s", float("inf")),
        ("fraction_gap_closed_5s", False),
    ],
)
def test_misleading_historical_v1_unknown_or_cross_cohort_fails_closed(
    field: str, value: object,
) -> None:
    later = _row()
    later[field] = value
    assert compare_snapback_rows(
        _row(), later, metric_name="fraction_gap_closed_5s",
        verified_source_rules=RULE_MAP,
    ) == (INCOMPARABLE, None)


def test_unapproved_forty_hex_self_label_does_not_authorize_comparison() -> None:
    # Both v2 tags match; row content alone still cannot approve a provenance.
    a = _row(sha="c" * 40)
    b = _row(sha="c" * 40, value=0.4)
    assert compare_snapback_rows(
        a, b, metric_name="fraction_gap_closed_5s",
        verified_source_rules={},
    ) == (INCOMPARABLE, None)
    assert compare_snapback_rows(
        _row(), _row(value=0.4),
        metric_name="fraction_gap_closed_5s",
        verified_source_rules={},
    ) == (INCOMPARABLE, None)


def test_legacy_v1_mislabeled_as_signed_v2_is_rejected_by_reviewed_sha() -> None:
    # Historical experimental signed X2 incorrectly bore v1 labels;
    # a locally relabeled v2 row with its independently rated v1 source
    # cannot become comparable.
    false_new = _row(sha=OLD_V1_SHA, value=0.4)
    assert compare_snapback_rows(
        _row(), false_new, metric_name="fraction_gap_closed_5s",
        verified_source_rules=RULE_MAP,
    ) == (INCOMPARABLE, None)
