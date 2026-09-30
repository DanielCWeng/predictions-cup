from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from predictions_cup.analysis.live_diag import (
    align_probability,
    analyze_lead_lag,
    construct_gap_episodes,
    ecology_summary,
    InventoryPoint,
    MarketRecommendation,
    observe_snapback,
    Quote,
    recommend_market,
    ResearchStatus,
    summarize_inventory,
)
from predictions_cup.mapping.models import MappingDirection


BASE = datetime(2026, 10, 1, 16, 0, tzinfo=UTC)


def _quote(
    seconds: float,
    bid: float,
    ask: float,
    *,
    bid_depth: float | None = 10.0,
    ask_depth: float | None = 10.0,
) -> Quote:
    return Quote(
        observed_at=BASE + timedelta(seconds=seconds),
        best_bid=bid,
        best_ask=ask,
        bid_depth=bid_depth,
        ask_depth=ask_depth,
    )


def test_complement_alignment() -> None:
    assert align_probability(0.2, MappingDirection.SAME) == pytest.approx(0.2)
    assert align_probability(0.2, MappingDirection.COMPLEMENT) == pytest.approx(0.8)


def test_gap_episodes_use_asof_state_and_do_not_count_persistent_rows() -> None:
    sig = (
        _quote(0, 0.49, 0.51),
        _quote(2, 0.50, 0.52),
        _quote(4, 0.54, 0.56),
        _quote(6, 0.50, 0.52),
    )
    external = (
        _quote(0, 0.54, 0.56),
        _quote(1, 0.55, 0.57),
        _quote(3, 0.56, 0.58),
        _quote(5, 0.54, 0.56),
    )
    triggers = construct_gap_episodes(
        sig_quotes=sig,
        external_quotes=external,
        threshold_ticks=3,
    )
    assert len(triggers) == 2
    assert triggers[0].observed_at == BASE
    assert triggers[1].observed_at == BASE + timedelta(seconds=6)


def test_snapback_and_overshoot_are_visible_not_clipped() -> None:
    trigger_sig = (_quote(0, 0.49, 0.51),)
    external = (_quote(0, 0.54, 0.56),)
    triggers = construct_gap_episodes(
        sig_quotes=trigger_sig,
        external_quotes=external,
        threshold_ticks=5,
    )
    assert len(triggers) == 1
    sig = (
        *trigger_sig,
        _quote(5, 0.52, 0.54),
        _quote(15, 0.55, 0.57),
    )
    observations = observe_snapback(
        triggers=triggers,
        sig_quotes=sig,
        external_quotes=external,
        horizons_seconds=(5, 15),
    )
    by_horizon = {item.horizon_seconds: item for item in observations}
    assert by_horizon[5].fraction_closed == pytest.approx(0.6)
    assert not by_horizon[5].overshoot
    assert by_horizon[15].fraction_closed > 1.0
    assert by_horizon[15].overshoot


def test_latency_can_remove_active_edge() -> None:
    sig = (
        _quote(0, 0.49, 0.51),
        _quote(1, 0.49, 0.51),
        _quote(1.1, 0.54, 0.56),
    )
    external = (
        _quote(0, 0.49, 0.51),
        _quote(1, 0.54, 0.56),
    )
    observations = analyze_lead_lag(
        sig_quotes=sig,
        external_quotes=external,
        latency_ms=100.0,
    )
    assert len(observations) == 1
    result = observations[0]
    assert result.gross_executable_edge == pytest.approx(0.04)
    assert result.latency_adjusted_edge == pytest.approx(0.0)
    assert result.status is ResearchStatus.TOO_FAST_TO_MONETIZE
    assert "EDGE_GONE_AFTER_LATENCY" in result.reasons


def test_missing_depth_never_becomes_executable_edge() -> None:
    sig = (
        _quote(0, 0.49, 0.51, ask_depth=None),
        _quote(1, 0.49, 0.51, ask_depth=None),
        _quote(2, 0.52, 0.54, ask_depth=None),
    )
    external = (
        _quote(0, 0.49, 0.51),
        _quote(1, 0.54, 0.56),
    )
    result = analyze_lead_lag(
        sig_quotes=sig,
        external_quotes=external,
        latency_ms=10.0,
    )[0]
    assert result.status is ResearchStatus.INSUFFICIENT_DEPTH
    assert "EXECUTABLE_DEPTH_UNAVAILABLE" in result.reasons


def test_inventory_episode_and_capital_seconds() -> None:
    points = (
        InventoryPoint(BASE, 0.0, 0.0),
        InventoryPoint(BASE + timedelta(seconds=10), 8.0, 8.0),
        InventoryPoint(BASE + timedelta(seconds=20), 4.0, 4.0),
        InventoryPoint(BASE + timedelta(seconds=30), 0.0, 0.0),
    )
    result = summarize_inventory(points, inventory_limit=10.0)
    assert result.peak_abs_inventory == pytest.approx(8.0)
    assert result.returns_to_flat_count == 1
    assert result.time_to_flat_distribution == (20.0,)
    assert result.inventory_half_life == pytest.approx(10.0)
    assert result.capital_seconds_consumed == pytest.approx(120.0)
    assert result.time_above_75pct_limit == pytest.approx(10.0)
    assert result.time_above_25pct_limit == pytest.approx(20.0)


def test_thin_market_fails_to_no_data() -> None:
    result, reasons = recommend_market(
        sample_count=3,
        independent_event_count=3,
        expected_edge=0.03,
        adverse_selection=0.0,
        capital_time_efficiency=1.0,
    )
    assert result is MarketRecommendation.NO_DATA
    assert reasons == ("INSUFFICIENT_INDEPENDENT_EVENTS",)


def test_ecology_never_claims_participant_identity() -> None:
    result = ecology_summary(
        (
            _quote(0, 0.49, 0.51),
            _quote(1, 0.50, 0.52),
            _quote(2, 0.50, 0.52),
            _quote(4, 0.51, 0.53),
        )
    )
    assert result["bbo_renewal_count"] == 2
    assert "BBO state lifetime" in str(result["terminology"])
    assert "participant identity" in str(result["terminology"])
