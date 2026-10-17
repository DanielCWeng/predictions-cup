"""Invented-source-only contract tests for signed X2 version separation.

No original book, trading, issuer, maker, fee, or other private evidence.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pyarrow.parquet as pq
import pytest

from predictions_cup.analysis import live_diag
from predictions_cup.analysis.evidence import AnalysisEvidence, write_evidence
from predictions_cup.analysis.snapback_version_gate import (
    ANALYSIS_VERSION,
    EPISODE_RULE_VERSION,
    INCOMPARABLE,
    SNAPBACK_METHOD_VERSION,
    TRIGGER_RULE,
    compare_snapback_rows,
)

T0 = datetime(2026, 11, 5, 1, tzinfo=UTC)
REVIEWED_SYNTHETIC_SHA = "a" * 40
LEGACY_SYNTHETIC_SHA = "b" * 40


def _old_magnitude_rule_count(gaps: tuple[float, ...], threshold: float) -> int:
    """Frozen old-main magnitude-gate model, NOT an import of X2 source."""
    active = False
    count = 0
    for gap in gaps:
        over = abs(gap) >= threshold
        if over and not active:
            count += 1
        active = over
    return count


def _episode_gaps(midpoints: tuple[float, ...]) -> tuple[live_diag.GapTrigger, ...]:
    sig = (live_diag.Quote(T0, 0.49, 0.51),)
    external = tuple(
        live_diag.Quote(
            T0 + timedelta(seconds=i),
            round(midpoint - 0.01, 5),
            round(midpoint + 0.01, 5),
        )
        for i, midpoint in enumerate(midpoints)
    )
    return live_diag.construct_gap_episodes(
        sig_quotes=sig,
        external_quotes=external,
        threshold_ticks=2,
    )


def _row(
    *,
    git_sha: str = REVIEWED_SYNTHETIC_SHA,
    metric: float = 0.2,
) -> dict[str, object]:
    return {
        **live_diag._signed_snapback_row_metadata(
            git_sha=git_sha,
            config_hash="synthetic-config-fixed",
            mapping_hash="synthetic-mapping-fixed",
            max_quote_age_seconds=15.0,
        ),
        "market_id": "invented-market",
        "exchange_id": "invented-sig",
        "polymarket_token_id": "invented-pm",
        "mapping_class": "invented-mapping",
        "mapping_direction": "SAME",
        "threshold_ticks": 2,
        "fraction_gap_closed_5s": metric,
        "trigger_count": 2,
        "independent_event_count": 2,
    }


def _rules() -> dict[str, str]:
    # The controlled source-rule map is EXTERNAL to the measured rows; this
    # synthetic example is not issuer authentication.
    return {REVIEWED_SYNTHETIC_SHA: EPISODE_RULE_VERSION}


def test_invented_direct_reversal_changes_denominator_but_not_same_sign_jitter() -> None:
    # SIG 0.50; PM 0.50 -> 0.80 -> 0.20 never goes below threshold
    # between the two shocks. Historical v1 counted one, signed X2 two.
    gaps = (0.0, 0.30, -0.30)
    assert _old_magnitude_rule_count(gaps, threshold=0.01) == 1
    episodes = _episode_gaps((0.50, 0.80, 0.20))
    assert len(episodes) == 2
    assert episodes[0].signed_gap > 0 > episodes[1].signed_gap
    assert _old_magnitude_rule_count((0.0, 0.30, 0.32, 0.29), 0.01) == 1
    assert len(_episode_gaps((0.50, 0.80, 0.82, 0.79))) == 1


def test_versions_on_standalone_row_and_positive_descriptive_comparison() -> None:
    older = _row(metric=0.2)
    newer = _row(metric=0.5)
    assert live_diag.ANALYSIS_VERSION == ANALYSIS_VERSION == "live-diag-001-v2"
    assert older["analysis_version"] == ANALYSIS_VERSION
    assert older["method_version"] == SNAPBACK_METHOD_VERSION
    assert older["episode_rule_version"] == EPISODE_RULE_VERSION
    assert older["trigger_rule"] == TRIGGER_RULE
    assert compare_snapback_rows(
        older, newer, metric_name="fraction_gap_closed_5s",
        verified_source_rules=_rules(),
    ) == ("DESCRIPTIVE_COMPARISON_ONLY", pytest.approx(0.3))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("analysis_version", "live-diag-001-v1"),
        ("method_version", "snapback-threshold-episodes-v1"),
        ("episode_rule_version", "magnitude-threshold-episodes-v1"),
        ("episode_rule_version", None),
        ("trigger_rule", "threshold_crossing_rearms_only_below_threshold"),
        ("git_sha", "UNKNOWN"),
        ("git_sha", LEGACY_SYNTHETIC_SHA),
        ("config_hash", "different-config"),
        ("mapping_hash", "different-mapping"),
        ("mapping_direction", "COMPLEMENT"),
        ("threshold_ticks", 3),
        ("max_quote_age_seconds", 30.0),
        ("market_id", None),
        ("fraction_gap_closed_5s", None),
        ("fraction_gap_closed_5s", float("nan")),
    ],
)
def test_cross_version_missing_or_mismatched_source_fails_to_null(
    field: str,
    value: object,
) -> None:
    altered = _row()
    altered[field] = value
    result = compare_snapback_rows(
        _row(), altered, metric_name="fraction_gap_closed_5s",
        verified_source_rules=_rules(),
    )
    assert result == (INCOMPARABLE, None)


def test_unknown_and_falsely_v2_labeled_original_v1_provenance_is_quarantined() -> None:
    row = _row(git_sha=LEGACY_SYNTHETIC_SHA)
    # A caller-reported v2 row from a source independently classified as v1
    # is NOT made safe by its apparent version fields or config hash.
    source_contracts = {
        REVIEWED_SYNTHETIC_SHA: EPISODE_RULE_VERSION,
        LEGACY_SYNTHETIC_SHA: "magnitude-threshold-episodes-v1",
    }
    assert compare_snapback_rows(
        _row(), row, metric_name="fraction_gap_closed_5s",
        verified_source_rules=source_contracts,
    ) == (INCOMPARABLE, None)
    assert compare_snapback_rows(
        _row(), _row(), metric_name="fraction_gap_closed_5s",
        verified_source_rules={},
    ) == (INCOMPARABLE, None)
    assert compare_snapback_rows(
        _row(), _row(), metric_name="fraction_gap_closed_invalid",
        verified_source_rules=_rules(),
    ) == (INCOMPARABLE, None)


def test_snapshot_evidence_and_standalone_parquet_preserve_distinct_v2_tags(
    tmp_path: Path,
) -> None:
    row = _row()
    root = tmp_path / "invented-only"
    live_diag._write_rows(root / "snapback.parquet", [row])
    materialized = pq.read_table(root / "snapback.parquet").to_pylist()[0]
    for field in (
        "analysis_version",
        "method_version",
        "episode_rule_version",
        "trigger_rule",
        "git_sha",
        "config_hash",
        "mapping_hash",
    ):
        assert materialized[field] == row[field]
    snapshot = {
        "analysis_id": "LIVE-DIAG-001",
        "analysis_version": ANALYSIS_VERSION,
        "episode_rule_version": EPISODE_RULE_VERSION,
        "snapback_method_version": SNAPBACK_METHOD_VERSION,
        "trigger_rule": TRIGGER_RULE,
        "snapback": [row],
    }
    record = AnalysisEvidence(
        analysis_id="LIVE-DIAG-001:SNAPBACK",
        analysis_version=ANALYSIS_VERSION,
        generated_at=T0,
        window_start=T0,
        window_end=T0 + timedelta(seconds=2),
        git_sha=REVIEWED_SYNTHETIC_SHA,
        config_hash="synthetic-config-fixed",
        mapping_hash="synthetic-mapping-fixed",
        source_watermarks=(),
        source_health=(),
        market_id="invented-market",
        exchange_id="invented-sig",
        risk_group_ids=(),
        sample_count=2,
        independent_event_count=2,
        metric_name="fraction_gap_closed_5s",
        metric_value=0.2,
        uncertainty=None,
        denominator="independent_signed_gap_episodes",
        status="DESCRIPTIVE_ONLY",
        reasons=(),
        evidence_refs=(),
        horizon_seconds=5,
        metric_unit="fraction",
        method_version=SNAPBACK_METHOD_VERSION,
        source_versions=(("episode_rule", EPISODE_RULE_VERSION),),
    )
    write_evidence(output_root=root, snapshot=snapshot, records=(record,))
    persisted = json.loads((root / "current.json").read_text(encoding="utf-8"))
    evidence_json = json.loads(
        (root / "analysis_events.jsonl").read_text(encoding="utf-8").strip()
    )
    evidence_parquet = pq.read_table(root / "analysis_evidence.parquet").to_pylist()[0]
    assert persisted["snapback"][0]["episode_rule_version"] == EPISODE_RULE_VERSION
    for observed in (evidence_json, evidence_parquet):
        assert observed["analysis_version"] == ANALYSIS_VERSION
        assert observed["method_version"] == SNAPBACK_METHOD_VERSION
        assert observed["denominator"] == "independent_signed_gap_episodes"
        assert dict(observed["source_versions"])["episode_rule"] == EPISODE_RULE_VERSION
    assert persisted["snapback_method_version"] == SNAPBACK_METHOD_VERSION
    assert persisted["episode_rule_version"] == EPISODE_RULE_VERSION
