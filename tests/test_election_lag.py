"""Adversarial, synthetic-only tests for election quote-lag research contract."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from predictions_cup.observe.election_lag import (
    Abstain,
    ElectionEvidenceKind,
    ELECTION_2026_CANDIDATE_CUTOFF_UTC,
    ElectionLagPolicy,
    ElectionProbabilityUpdate,
    LagDirection,
    ShadowCapacity,
    ShadowLagCandidate,
    SigExecutableTop,
    evaluate_election_lag,
)

NOW = 6_000_000_000
T = datetime(2026, 11, 4, 2, tzinfo=UTC)


def _event(**kwargs: object) -> ElectionProbabilityUpdate:
    defaults: dict[str, object] = dict(
        event_id="call-42",
        source_id="verified-event-feed",
        exchange_id="sig-123",
        market_id="market-456",
        published_at=T,
        received_at=T + timedelta(milliseconds=100),
        received_monotonic_ns=NOW - 200_000_000,
        clock_domain="maker-process-1",
        probability_lower=0.80,
        probability_upper=0.90,
        kind=ElectionEvidenceKind.VERIFIED_MEDIA_CALL,
        source_verified=True,
        exact_mapping_verified=True,
        sig_yes_payoff_verified=True,
        probability_interval_calibrated=True,
    )
    defaults.update(kwargs)
    return ElectionProbabilityUpdate(**defaults)  # type: ignore[arg-type]


def _quote(**kwargs: object) -> SigExecutableTop:
    defaults: dict[str, object] = dict(
        exchange_id="sig-123",
        market_id="market-456",
        observed_monotonic_ns=NOW - 100_000_000,
        clock_domain="maker-process-1",
        best_bid_ticks=78,
        best_ask_ticks=80,
        bid_shares=100,
        ask_shares=25,
        depth_trusted=True,
        bbo_trusted=True,
        tradeable=True,
    )
    defaults.update(kwargs)
    return SigExecutableTop(**defaults)  # type: ignore[arg-type]


def _capacity(**kwargs: object) -> ShadowCapacity:
    defaults: dict[str, object] = dict(
        cash_available=100.0,
        free_gross_shares=10,
        held_yes_shares=0,
        account_trusted=True,
    )
    defaults.update(kwargs)
    return ShadowCapacity(**defaults)  # type: ignore[arg-type]


def _decide(
    event: ElectionProbabilityUpdate | None = None,
    quote: SigExecutableTop | None = None,
    capacity: ShadowCapacity | None = None,
    now: int = NOW,
) -> ShadowLagCandidate | Abstain:
    return evaluate_election_lag(
        event or _event(), quote or _quote(), capacity or _capacity(),
        now_monotonic_ns=now,
        now_utc=T + timedelta(seconds=1),
    )


def test_buy_uses_ask_not_midpoint_and_respects_gross_cap() -> None:
    candidate = _decide()
    assert isinstance(candidate, ShadowLagCandidate)
    assert candidate.direction is LagDirection.BUY_YES
    assert candidate.price_ticks == 80
    assert candidate.quantity == 10
    assert candidate.source_receive_age_ns == 200_000_000
    assert candidate.sig_book_age_ns == 100_000_000
    assert candidate.conservative_edge_per_share == pytest.approx(0.38)


def test_exiting_held_yes_uses_real_bid_and_never_sells_unowned_inventory() -> None:
    event = _event(probability_lower=0.10, probability_upper=0.20)
    no_inventory = _decide(event)
    assert isinstance(no_inventory, Abstain)
    assert no_inventory.reason == "insufficient_executable_capacity"
    candidate = _decide(event, capacity=_capacity(held_yes_shares=12, free_gross_shares=0))
    assert isinstance(candidate, ShadowLagCandidate)
    assert candidate.direction is LagDirection.SELL_HELD_YES
    assert candidate.price_ticks == 78
    assert candidate.quantity == 12
    assert candidate.conservative_edge_per_share == pytest.approx(0.17)


def test_cash_and_depth_limits_are_not_fictitious() -> None:
    candidate = _decide(capacity=_capacity(cash_available=1.30))
    assert isinstance(candidate, ShadowLagCandidate)
    assert candidate.quantity == 3
    candidate = _decide(quote=_quote(ask_shares=0.9))
    assert isinstance(candidate, Abstain)
    assert candidate.reason == "insufficient_executable_capacity"


@pytest.mark.parametrize(
    ("event", "quote", "capacity", "reason"),
    [
        (
            _event(exact_mapping_verified=False), _quote(), _capacity(),
            "unverified_source_or_mapping",
        ),
        (_event(source_verified=False), _quote(), _capacity(), "unverified_source_or_mapping"),
        (
            _event(sig_yes_payoff_verified=False), _quote(), _capacity(),
            "unverified_source_or_mapping",
        ),
        (
            _event(probability_interval_calibrated=False), _quote(), _capacity(),
            "uncalibrated_interval",
        ),
        (_event(market_id="wrong"), _quote(), _capacity(), "identity_mismatch"),
        (_event(clock_domain="east-host"), _quote(), _capacity(), "incomparable_monotonic_clocks"),
        (_event(received_monotonic_ns=NOW + 1), _quote(), _capacity(), "future_observation"),
        (_event(received_monotonic_ns=0), _quote(), _capacity(), "event_stale"),
        (
            _event(received_at=T - timedelta(seconds=1)), _quote(), _capacity(),
            "source_publication_clock_or_delay",
        ),
        (
            _event(received_at=T + timedelta(minutes=1)), _quote(), _capacity(),
            "source_publication_clock_or_delay",
        ),
        (_event(), _quote(depth_trusted=False), _capacity(), "sig_market_untrusted"),
        (_event(), _quote(observed_monotonic_ns=0), _capacity(), "sig_book_stale"),
        (_event(), _quote(tradeable=False), _capacity(), "sig_market_untrusted"),
        (_event(), _quote(), _capacity(account_trusted=False), "account_untrusted"),
    ],
)
def test_negative_controls(
    event: ElectionProbabilityUpdate,
    quote: SigExecutableTop,
    capacity: ShadowCapacity,
    reason: str,
) -> None:
    decision = _decide(event, quote, capacity)
    assert isinstance(decision, Abstain)
    assert decision.reason == reason


def test_spread_and_cost_destroy_apparent_midpoint_edge() -> None:
    event = _event(probability_lower=0.42, probability_upper=0.57)
    assert isinstance(_decide(event), Abstain)
    quote = _quote(best_bid_ticks=78, best_ask_ticks=90)
    assert isinstance(_decide(event, quote), Abstain)
    assert isinstance(
        _decide(_event(probability_lower=0.425, probability_upper=0.56)), Abstain
    )


def test_contract_fails_on_invalid_data() -> None:
    with pytest.raises(ValueError, match="unsupported"):
        _event(kind="PRELIMINARY_LEAD")
    with pytest.raises(ValueError, match="probability"):
        _event(probability_lower=0.9, probability_upper=0.5)
    with pytest.raises(ValueError, match="timezone"):
        _event(published_at=datetime(2026, 11, 4))
    with pytest.raises(ValueError, match="ticks"):
        _quote(best_bid_ticks=120, best_ask_ticks=80)
    with pytest.raises(ValueError, match="ticks"):
        _quote(best_bid_ticks=80, best_ask_ticks=80)
    with pytest.raises(ValueError, match="capacity"):
        _capacity(cash_available=float("nan"))


def test_no_trade_with_zero_share_risk_capacity() -> None:
    result = _decide(capacity=_capacity(free_gross_shares=0))
    assert isinstance(result, Abstain)
    assert result.reason == "insufficient_executable_capacity"


def test_policy_validation_and_strict_threshold() -> None:
    with pytest.raises(ValueError, match="positive"):
        ElectionLagPolicy(max_quote_age_ns=0)
    with pytest.raises(ValueError, match="invalid"):
        ElectionLagPolicy(cost_per_share=float("nan"))
    assert isinstance(
        evaluate_election_lag(
            _event(), _quote(), _capacity(),
            now_monotonic_ns=NOW,
            now_utc=T + timedelta(seconds=1),
            policy=ElectionLagPolicy(min_net_edge_per_share=0.4),
        ),
        Abstain,
    )


@pytest.mark.parametrize(
    ("decision_time", "expected"),
    [
        (ELECTION_2026_CANDIDATE_CUTOFF_UTC - timedelta(microseconds=1), "CANDIDATE"),
        (ELECTION_2026_CANDIDATE_CUTOFF_UTC, "competition_closed_for_candidates"),
        (
            ELECTION_2026_CANDIDATE_CUTOFF_UTC + timedelta(minutes=1),
            "competition_closed_for_candidates",
        ),
        (datetime(2026, 11, 4, 16, 59, 55), "decision_clock_untrusted"),
        (T - timedelta(milliseconds=1), "decision_before_event_receipt"),
    ],
)
def test_candidate_clock_cutoff_boundary(
    decision_time: datetime, expected: str
) -> None:
    decision = evaluate_election_lag(
        _event(), _quote(), _capacity(),
        now_monotonic_ns=NOW,
        now_utc=decision_time,
    )
    if expected == "CANDIDATE":
        assert isinstance(decision, ShadowLagCandidate)
    else:
        assert isinstance(decision, Abstain)
        assert decision.reason == expected


def test_locked_market_never_emits_candidate() -> None:
    with pytest.raises(ValueError, match="ticks"):
        evaluate_election_lag(
            _event(),
            _quote(best_bid_ticks=80, best_ask_ticks=80),
            _capacity(),
            now_monotonic_ns=NOW,
            now_utc=T + timedelta(seconds=1),
        )
