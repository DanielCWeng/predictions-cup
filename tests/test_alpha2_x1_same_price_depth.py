"""X1: synthetic same-price depth changes must survive executable as-of replay.

Includes unchanged frozen Red Team X1 fixture assertions (without its strict-xfail
decorator) and additional decision/arrival/clock/size cutoff counterexamples.
No venue, maker, order path, LIVE economics promotion, or X2 source changes.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from predictions_cup.analysis.live_diag import Quote, ResearchStatus, analyze_lead_lag

START = datetime(2026, 11, 4, 1, tzinfo=UTC)
IMPULSE = START + timedelta(seconds=1)
RESPONSE = IMPULSE + timedelta(seconds=2)


def _sig(at: datetime, bid_size: float, ask_size: float,
         *, depth_at: datetime | None = None) -> Quote:
    return Quote(
        at, 0.40, 0.50, bid_depth=bid_size, ask_depth=ask_size,
        depth_observed_at=at if depth_at is None else depth_at,
    )


def _external(up: bool = True) -> tuple[Quote, Quote]:
    if up:
        return (Quote(START, 0.48, 0.52), Quote(IMPULSE, 0.68, 0.72))
    return (Quote(START, 0.48, 0.52), Quote(IMPULSE, 0.18, 0.22))


def _run(sig: tuple[Quote, ...], *, latency_ms: float = 500,
         max_quote_age_seconds: float = 15,
         max_depth_age_seconds: float = 60, up: bool = True):
    result = analyze_lead_lag(
        sig_quotes=sig, external_quotes=_external(up), latency_ms=latency_ms,
        max_quote_age_seconds=max_quote_age_seconds,
        max_depth_age_seconds=max_depth_age_seconds,
    )
    assert len(result) == 1
    return result[0]


def test_frozen_original_x1_unchanged_price_zero_depth_prevents_monetizable() -> None:
    """Frozen Red Team X1 quote data and strict assertions; no xfail marker."""
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


@pytest.mark.parametrize("up", [True, False])
def test_both_side_sizes_drop_at_unchanged_prices(up: bool) -> None:
    zero = IMPULSE + timedelta(milliseconds=200)
    quote = _run((
        _sig(START, 50, 50),
        _sig(zero, 0, 0),
        Quote(RESPONSE, 0.45, 0.55, bid_depth=0, ask_depth=0,
              depth_observed_at=RESPONSE),
    ), up=up)
    assert quote.status is ResearchStatus.INSUFFICIENT_DEPTH
    assert quote.available_depth == 0
    assert quote.reasons == ("LATENCY_EXECUTABLE_DEPTH_UNAVAILABLE",)


def test_zero_depth_recovery_same_prices_before_arrival_reprices_available_size() -> None:
    depleted = IMPULSE + timedelta(milliseconds=100)
    recovered = IMPULSE + timedelta(milliseconds=300)
    quote = _run((
        _sig(START, 50, 50),
        _sig(depleted, 0, 0),
        _sig(recovered, 12, 12),
        Quote(RESPONSE, 0.45, 0.55, bid_depth=12, ask_depth=12,
              depth_observed_at=RESPONSE),
    ))
    # This is only a synthetic conditional research classification, NOT fills.
    assert quote.status is ResearchStatus.MONETIZABLE_CANDIDATE
    assert quote.available_depth == 12


def test_recovery_after_arrival_must_not_retroactively_restore_liquidity() -> None:
    depleted = IMPULSE + timedelta(milliseconds=200)
    recovered_too_late = IMPULSE + timedelta(milliseconds=600)
    quote = _run((
        _sig(START, 50, 50),
        _sig(depleted, 0, 0),
        _sig(recovered_too_late, 50, 50),
        Quote(RESPONSE, 0.45, 0.55, bid_depth=50, ask_depth=50,
              depth_observed_at=RESPONSE),
    ))
    assert quote.status is ResearchStatus.INSUFFICIENT_DEPTH
    assert quote.available_depth == 0


def test_missing_feed_interval_before_latency_deadline_cannot_use_stale_book() -> None:
    quote = _run((
        _sig(START, 50, 50),
        Quote(RESPONSE, 0.45, 0.55, bid_depth=50, ask_depth=50,
              depth_observed_at=RESPONSE),
    ), max_quote_age_seconds=1.2)
    assert quote.status is ResearchStatus.INSUFFICIENT_EVIDENCE
    assert quote.available_depth is None
    assert quote.reasons == ("NO_LATENCY_ASOF_BOOK",)


@pytest.mark.parametrize("bad_depth_at", [
    IMPULSE + timedelta(milliseconds=250),  # source event after local receipt
    IMPULSE + timedelta(milliseconds=600),  # future event after arrival cutoff
])
def test_future_source_depth_cannot_certify_delayed_fill(
    bad_depth_at: datetime,
) -> None:
    receipt = IMPULSE + timedelta(milliseconds=200)
    quote = _run((
        _sig(START, 50, 50),
        _sig(receipt, 50, 50, depth_at=bad_depth_at),
        Quote(RESPONSE, 0.45, 0.55, bid_depth=50, ask_depth=50,
              depth_observed_at=RESPONSE),
    ))
    assert quote.status is ResearchStatus.INSUFFICIENT_DEPTH
    assert quote.available_depth is None
    assert quote.reasons == ("LATENCY_EXECUTABLE_DEPTH_UNAVAILABLE",)


def test_new_receipt_does_not_refresh_stale_older_source_depth() -> None:
    receipt = IMPULSE + timedelta(milliseconds=200)
    quote = _run((
        _sig(START, 50, 50),
        _sig(receipt, 49, 49, depth_at=START),
        Quote(RESPONSE, 0.45, 0.55, bid_depth=49, ask_depth=49,
              depth_observed_at=RESPONSE),
    ), max_depth_age_seconds=1.1)
    assert quote.status is ResearchStatus.INSUFFICIENT_DEPTH
    assert quote.available_depth is None


def test_zero_or_missing_depth_does_not_become_economic_quote_when_price_moves() -> None:
    zero = IMPULSE + timedelta(milliseconds=200)
    quote = _run((
        _sig(START, 50, 50),
        _sig(zero, 0, 0),
        Quote(RESPONSE, 0.45, 0.55, bid_depth=0, ask_depth=0,
              depth_observed_at=RESPONSE),
    ))
    assert quote.status is not ResearchStatus.MONETIZABLE_CANDIDATE
    assert quote.available_depth == 0
    assert quote.latency_adjusted_edge is not None  # indicative price only
