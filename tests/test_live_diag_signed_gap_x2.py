"""X2 reviewer-only gap sign-reversal regressions.

The primary test preserves the assertions from frozen #265 reviewer
test_election_call_reversal_must_be_a_new_independent_gap_episode verbatim;
the strict-XFAIL decorator is intentionally not copied into this PASS probe.
No executable venue, order, maker, model or new market-data interfaces.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from predictions_cup.analysis.live_diag import Quote, construct_gap_episodes


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


def test_same_direction_jitter_does_not_multiply_episode_and_quiet_rearms() -> None:
    t0 = datetime(2026, 11, 4, 2, tzinfo=UTC)
    sig = (Quote(t0, 0.49, 0.51),)
    ext = (
        Quote(t0, 0.49, 0.51),
        Quote(t0 + timedelta(seconds=1), 0.79, 0.81),
        Quote(t0 + timedelta(seconds=2), 0.81, 0.83),
        Quote(t0 + timedelta(seconds=3), 0.49, 0.51),
        Quote(t0 + timedelta(seconds=4), 0.77, 0.79),
        Quote(t0 + timedelta(seconds=5), 0.19, 0.21),
    )
    episodes = construct_gap_episodes(
        sig_quotes=sig, external_quotes=ext, threshold_ticks=2,
    )
    assert [episode.observed_at for episode in episodes] == [
        t0 + timedelta(seconds=1),
        t0 + timedelta(seconds=4),
        t0 + timedelta(seconds=5),
    ]
    assert [episode.signed_gap > 0 for episode in episodes] == [True, True, False]
    assert len({episode.trigger_id for episode in episodes}) == 3


def test_negative_to_positive_direct_reversal_is_independent() -> None:
    t0 = datetime(2026, 11, 4, 2, tzinfo=UTC)
    sig = (Quote(t0, 0.49, 0.51),)
    ext = (
        Quote(t0, 0.49, 0.51),
        Quote(t0 + timedelta(seconds=1), 0.19, 0.21),
        Quote(t0 + timedelta(seconds=2), 0.79, 0.81),
    )
    episodes = construct_gap_episodes(
        sig_quotes=sig, external_quotes=ext, threshold_ticks=2,
    )
    assert len(episodes) == 2
    assert episodes[0].signed_gap < 0
    assert episodes[1].signed_gap > 0
    assert episodes[0].observed_at < episodes[1].observed_at
    assert episodes[0].trigger_id != episodes[1].trigger_id
