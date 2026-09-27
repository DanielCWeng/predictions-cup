"""Synthetic descriptive-reaction tests for EXPERIMENT-004A."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from predictions_cup.historical.event_reaction import SnapshotPoint, _summarize

T0 = datetime(2026, 6, 21, 21, tzinfo=UTC)


def _point(minutes: int, midpoint: float, spread: float = 0.02) -> SnapshotPoint:
    return SnapshotPoint(
        at=T0 + timedelta(minutes=minutes),
        midpoint=midpoint,
        spread=spread,
        top_bid_depth=100.0 + minutes,
        top_ask_depth=90.0 + minutes,
    )


def test_reaction_diagnostics_are_descriptive_and_anchor_preserving() -> None:
    points = [
        _point(-5, 0.55),
        _point(0, 0.56),
        _point(10, 0.78),
        _point(20, 0.91),
        _point(30, 0.96),
        _point(45, 0.995),
        _point(90, 0.997),
    ]
    result = _summarize(
        points,
        active_start=T0,
        active_end=T0 + timedelta(hours=1),
        corpus_end=T0 + timedelta(hours=2),
    )

    assert result["last_midpoint_side"] == "YES"
    assert result["baseline_relation"] == "AT_OR_BEFORE_RESULT_START"
    assert result["hours_to_90_from_result_start"] == 0.333333
    assert result["hours_to_95_from_result_start"] == 0.5
    assert result["hours_to_99_from_result_start"] == 0.75
    assert result["effectively_one_sided_99_observed"] is True
    assert result["used_to_choose_regime_boundary"] is False
    assert result["diagnostic_only"] is True
    assert result["max_abs_midpoint_change_1h"] == 0.435


def test_reaction_diagnostic_supports_no_side_and_no_resolution() -> None:
    points = [
        _point(0, 0.48),
        _point(15, 0.40),
        _point(30, 0.32),
        _point(60, 0.20),
        _point(120, 0.15),
    ]
    result = _summarize(
        points,
        active_start=T0,
        active_end=T0 + timedelta(hours=1),
        corpus_end=T0 + timedelta(hours=2),
    )

    assert result["last_midpoint_side"] == "NO"
    assert result["first_90_toward_last_side"] is None
    assert result["effectively_one_sided_99_observed"] is False
    assert result["first_sustained_75_toward_last_side"] == (
        T0 + timedelta(hours=1)
    ).isoformat().replace("+00:00", "Z")
