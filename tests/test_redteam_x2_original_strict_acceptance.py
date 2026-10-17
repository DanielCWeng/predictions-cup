"""Independent X2 strict witness: exact original Red Team #265 body, no xfail.

Original test blob 12622085f3555aa6a40367e0a68c2bf3232cf6fd;
original function text below is byte-identical, without decorator.
No source changes, venue access, or production use.
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


def test_redteam_x2_negative_to_positive_direct_sign_reversal() -> None:
    t0 = datetime(2026, 11, 4, 2, tzinfo=UTC)
    sig = (Quote(t0, 0.49, 0.51),)
    ext = (
        Quote(t0, 0.49, 0.51),
        Quote(t0 + timedelta(seconds=1), 0.19, 0.21),
        Quote(t0 + timedelta(seconds=2), 0.79, 0.81),
    )
    episodes = construct_gap_episodes(sig_quotes=sig, external_quotes=ext, threshold_ticks=2)
    assert len(episodes) == 2
    assert [item.signed_gap > 0 for item in episodes] == [False, True]
    assert episodes[0].trigger_id != episodes[1].trigger_id


def test_redteam_x2_same_direction_jitter_does_not_inflate_episodes() -> None:
    t0 = datetime(2026, 11, 4, 2, tzinfo=UTC)
    sig = (Quote(t0, 0.49, 0.51),)
    external = (
        Quote(t0, 0.49, 0.51),
        Quote(t0 + timedelta(seconds=1), 0.79, 0.81),
        Quote(t0 + timedelta(seconds=2), 0.81, 0.83),
        Quote(t0 + timedelta(seconds=3), 0.78, 0.80),
        Quote(t0 + timedelta(seconds=4), 0.49, 0.51),
        Quote(t0 + timedelta(seconds=5), 0.19, 0.21),
    )
    episodes = construct_gap_episodes(
        sig_quotes=sig, external_quotes=external, threshold_ticks=2
    )
    assert len(episodes) == 2
    assert [i.observed_at for i in episodes] == [
        t0 + timedelta(seconds=1),
        t0 + timedelta(seconds=5),
    ]
    assert episodes[0].signed_gap > 0 > episodes[1].signed_gap
