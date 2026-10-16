"""Independent economic falsification: NO accounting and stale-depth survivorship.

Research fixture only. No order calls, no model parameters, no LIVE controls.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from predictions_cup.analysis.live_diag import (
    Quote,
    ResearchStatus,
    analyze_lead_lag,
    construct_gap_episodes,
)
from predictions_cup.risk.capital import RiskFill, replay_fills
from predictions_cup.runtime.models import OrderAction, OutcomeSide


def test_no_side_partial_close_and_fee_conservation() -> None:
    """NO BUY is short YES; YES BUY partly closes it, fees hit realised PnL."""
    open_no = RiskFill(
        fill_id="no-1", exchange_id="e", market_id="m",
        outcome_side=OutcomeSide.NO, action=OrderAction.BUY,
        quantity=Decimal("10"), price=Decimal("0.30"),
        filled_monotonic_ns=1, fee=Decimal("0.10"),
    )
    close_yes = RiskFill(
        fill_id="yes-2", exchange_id="e", market_id="m",
        outcome_side=OutcomeSide.YES, action=OrderAction.BUY,
        quantity=Decimal("4"), price=Decimal("0.60"),
        filled_monotonic_ns=2, fee=Decimal("0.05"),
    )
    result = replay_fills([close_yes, open_no, open_no])
    assert result.processed_fill_ids == ("no-1", "yes-2")
    assert result.realised_pnl == Decimal("0.25")
    assert len(result.positions) == 1
    position = result.positions[0]
    assert position.signed_quantity == Decimal("-6")
    assert position.avg_entry_yes == Decimal("0.70")
    assert position.canonical_cost_basis == Decimal("1.80")


def test_conflicting_duplicate_fill_is_rejected() -> None:
    first = RiskFill(
        fill_id="duplicated", exchange_id="e", market_id="m",
        outcome_side=OutcomeSide.NO, action=OrderAction.BUY,
        quantity=Decimal("2"), price=Decimal("0.25"), filled_monotonic_ns=1,
    )
    changed = RiskFill(
        fill_id="duplicated", exchange_id="e", market_id="m",
        outcome_side=OutcomeSide.NO, action=OrderAction.BUY,
        quantity=Decimal("3"), price=Decimal("0.25"), filled_monotonic_ns=1,
    )
    with pytest.raises(ValueError, match="conflicting replay"):
        replay_fills([first, changed])


@pytest.mark.xfail(
    strict=True,
    reason=(
        "Known economics FAIL: live_diag._economic deduplicates unchanged BBO "
        "without retaining updated depth; delayed executable size can be fictitious"
    ),
)
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


@pytest.mark.xfail(
    strict=True,
    reason=(
        "Known election-night reversal gap: construct_gap_episodes tracks only "
        "threshold-active state; opposite-sign source call doesn't start new episode"
    ),
)
def test_election_call_reversal_must_be_a_new_independent_gap_episode() -> None:
    """Positive event gap flipping straight negative is an independent market shock."""
    t0 = datetime(2026, 11, 4, 2, tzinfo=UTC)
    sig = (Quote(t0, 0.49, 0.51),)
    ext = (
        Quote(t0, 0.49, 0.51),
        Quote(t0 + timedelta(seconds=1), 0.79, 0.81),
        Quote(t0 + timedelta(seconds=2), 0.19, 0.21),
    )
    episodes = construct_gap_episodes(
        sig_quotes=sig, external_quotes=ext, threshold_ticks=2,
    )
    assert len(episodes) == 2
    assert episodes[0].signed_gap > 0
    assert episodes[1].signed_gap < 0
