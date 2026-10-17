"""Independent X1 strict witness: frozen #265 body verbatim, no xfail.

Original frozen Red Team test blob 12622085f3555aa6a40367e0a68c2bf3232cf6fd.
No live data, execution source, or trading calls.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from predictions_cup.analysis.live_diag import Quote, ResearchStatus, analyze_lead_lag


def test_unchanged_price_zero_depth_update_prevents_monetizable_classification() -> None:
    """A zero-depth update after a quote invalidates hypothetical executable edge."""
    start = datetime(2026, 11, 4, 1, tzinfo=UTC)
    impulse = start + timedelta(seconds=1)
    delayed_update = impulse + timedelta(milliseconds=200)
    response = impulse + timedelta(seconds=2)
    quotes = (
        Quote(start, 0.40, 0.50, bid_depth=50, ask_depth=50,
              depth_observed_at=start),
        Quote(delayed_update, 0.40, 0.50, bid_depth=0, ask_depth=0,
              depth_observed_at=delayed_update),
        Quote(response, 0.45, 0.55, bid_depth=0, ask_depth=0,
              depth_observed_at=response),
    )
    external = (
        Quote(start, 0.48, 0.52),
        Quote(impulse, 0.68, 0.72),
    )
    rows = analyze_lead_lag(
        sig_quotes=quotes,
        external_quotes=external,
        latency_ms=500,
        max_depth_age_seconds=60,
    )
    assert len(rows) == 1
    assert rows[0].status is ResearchStatus.INSUFFICIENT_DEPTH
    assert rows[0].available_depth == 0


def test_redteam_x1_post_arrival_recovery_cannot_restore_executable_depth() -> None:
    start = datetime(2026, 11, 4, 1, tzinfo=UTC)
    impulse = start + timedelta(seconds=1)
    no_depth = impulse + timedelta(milliseconds=200)
    after_arrival = impulse + timedelta(milliseconds=600)
    quotes = (
        Quote(start, 0.40, 0.50, bid_depth=50, ask_depth=50, depth_observed_at=start),
        Quote(
            no_depth, 0.40, 0.50, bid_depth=0, ask_depth=0,
            depth_observed_at=no_depth,
        ),
        Quote(
            after_arrival, 0.40, 0.50, bid_depth=50, ask_depth=50,
            depth_observed_at=after_arrival,
        ),
    )
    external = (
        Quote(start, 0.48, 0.52),
        Quote(impulse, 0.68, 0.72),
    )
    rows = analyze_lead_lag(
        sig_quotes=quotes, external_quotes=external, latency_ms=500,
        max_depth_age_seconds=60,
    )
    assert len(rows) == 1
    assert rows[0].status is ResearchStatus.INSUFFICIENT_DEPTH
    assert rows[0].available_depth == 0
