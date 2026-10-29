"""Explain zero rotation candidates without weakening any execution gates."""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest

from predictions_cup.maker.capital_rotation import BookLevel
from predictions_cup.maker.liquidity_allocator import (
    EntryTier,
    LiquidityWeightedAllocator,
    RankedEntry,
    RankedExit,
)
from predictions_cup.maker.residual_live import ResidualTakerLiveCoordinator
from test_liquidity_allocator import snapshot


def _reason(allocator: LiquidityWeightedAllocator, eid: str = "11") -> str:
    return allocator.latest_eligibility[eid][1]


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ("mapping", "mapping_not_exact_same"),
        ("risk", "capital_risk_not_ready"),
        ("proxy", "account_not_trusted"),
        ("resting", "resting_order_on_market"),
        ("sig_untrusted", "sig_depth_untrusted"),
        ("sig_stale", "sig_depth_stale"),
        ("pm_stale", "pm_quote_stale"),
        ("pm_missing", "pm_quote_missing_or_untrusted"),
        ("pm_depth_missing", "pm_depth_size_missing"),
        ("no_edge", "no_marginal_entry_edge_or_reducing_exit"),
    ],
)
def test_allocator_reports_exact_gate_rejection(change: str, reason: str) -> None:
    allocator = LiquidityWeightedAllocator()
    state = snapshot(
        eid="11", signed=0, pm_bid=.60, pm_ask=.62,
        yes_bid=100, yes_ask=106,
    )
    mapped_token: str | None = "token-11"
    risk_ready = True
    if change == "mapping":
        mapped_token = None
    elif change == "risk":
        risk_ready = False
    elif change == "proxy":
        state = replace(
            state,
            runtime=replace(
                state.runtime,
                portfolio=replace(
                    state.runtime.portfolio,
                    account_trusted=False,
                ),
            ),
        )
    elif change == "resting":
        state = snapshot(
            eid="11", signed=0, pm_bid=.60, pm_ask=.62,
            yes_bid=100, yes_ask=106, open_order=True,
        )
    elif change == "sig_untrusted":
        state = replace(state, sig_depth_trusted=False)
    elif change == "sig_stale":
        state = replace(
            state, sig_depth_observed_ns=state.now_monotonic_ns - 20_000_000_000,
        )
    elif change == "pm_stale":
        state = replace(
            state,
            external_quotes={
                "token-11": replace(
                    state.external_quotes["token-11"],
                    observed_monotonic_ns=state.now_monotonic_ns - 8_000_000_000,
                ),
            },
        )
    elif change == "pm_missing":
        state = replace(state, external_quotes={})
    elif change == "pm_depth_missing":
        state = replace(
            state,
            external_quotes={
                "token-11": replace(
                    state.external_quotes["token-11"],
                    best_bid_size=None, best_ask_size=None,
                ),
            },
        )
    elif change == "no_edge":
        state = snapshot(
            eid="11", pm_bid=.49, pm_ask=.51, yes_bid=98, yes_ask=104,
        )
    allocator.observe(state, mapped_token, risk_state_trusted=risk_ready)
    assert _reason(allocator) == reason
    report = allocator.eligibility_report(state.now_monotonic_ns)
    assert report["recent_markets"] == 1
    assert report["recent_reasons"] == {reason: 1}
    assert report["total_reasons"] == {reason: 1}


def test_report_distinguishes_eligible_exit_and_entry() -> None:
    allocator = LiquidityWeightedAllocator()
    state = snapshot(eid="11", signed=500, pm_bid=.45, pm_ask=.47)
    allocator.observe(state, "token-11")
    assert _reason(allocator) in {"eligible_exit_only", "eligible_entry_and_exit"}
    assert "11" in allocator.exits


def test_report_keeps_advisory_order_screen_independent() -> None:
    strict = LiquidityWeightedAllocator()
    advisory = LiquidityWeightedAllocator(advisory_include_resting_orders=True)
    state = snapshot(
        eid="11", signed=500, pm_bid=.45, pm_ask=.47, open_order=True,
    )
    strict.observe(state, "token-11")
    advisory.observe(state, "token-11")
    assert _reason(strict) == "resting_order_on_market"
    assert _reason(advisory) != "resting_order_on_market"
    assert strict.exits == {}
    assert advisory.blocked_by_orders == {"11"}


def test_report_tracks_stale_reason_without_preserving_candidate() -> None:
    allocator = LiquidityWeightedAllocator()
    state = snapshot(eid="11", pm_bid=.60, pm_ask=.62, yes_ask=106)
    allocator.observe(state, "token-11")
    assert "11" in allocator.entries
    stale = replace(
        state, sig_depth_observed_ns=state.now_monotonic_ns - 17_000_000_000,
    )
    allocator.observe(stale, "token-11")
    assert allocator.entries == {}
    assert _reason(allocator) == "sig_depth_stale"
    assert allocator.eligibility_report(state.now_monotonic_ns + 60_000_000_001)[
        "recent_markets"
    ] == 0


def test_rank_report_prices_a_pair_but_keeps_100_susq_hurdle() -> None:
    allocator = LiquidityWeightedAllocator(min_rotation_gain=100)
    allocator.exits["old"] = RankedExit(
        "old", "held-market", 500, .60, 100,
        (BookLevel(.60, 500),), (BookLevel(.62, 500),),
    )
    allocator.entries["new"] = RankedEntry(
        "new", "new-market", "BUY", 100, (EntryTier(500, .10),),
    )
    assert allocator.rank(now_ns=100) == ()
    priced = allocator.last_rank_diagnostic["economically_priced_pairs"]
    assert isinstance(priced, int) and priced > 0
    best = allocator.last_rank_diagnostic["best_gain"]
    assert isinstance(best, float)
    assert 0 < best < 100
    assert allocator.last_rank_diagnostic["gain_hurdle"] == 100
    assert allocator.entries and allocator.exits


def test_rank_report_explains_missing_entry_or_exit() -> None:
    allocator = LiquidityWeightedAllocator(min_rotation_gain=100)
    assert allocator.rank(now_ns=100) == ()
    assert allocator.last_rank_diagnostic == {
        "entries": 0, "exits": 0, "economically_priced_pairs": 0,
        "best_gain": None, "gain_hurdle": 100,
    }

def test_coordinated_rotation_recheck_is_bounded_throttled_and_in_memory() -> None:
    requested: list[frozenset[str]] = []
    coordinator = object.__new__(ResidualTakerLiveCoordinator)
    coordinator._rotation_allocator = LiquidityWeightedAllocator()
    coordinator._signal = SimpleNamespace(
        tracked_exchange_ids=frozenset(str(i) for i in range(300)),
    )
    coordinator._request_rotation_recheck = requested.append
    coordinator._active_rotation_exits = True
    coordinator._rotation_last_coordinated_recheck_ns = 0
    coordinator._rotation_coordinated_rechecks = 0

    coordinator._schedule_rotation_coordinated_recheck(
        trusted=True, now_ns=30_000_000_000,
    )
    assert len(requested) == 1
    assert len(requested[0]) == 256
    assert coordinator._rotation_coordinated_rechecks == 1

    coordinator._schedule_rotation_coordinated_recheck(
        trusted=True, now_ns=31_000_000_000,
    )
    coordinator._schedule_rotation_coordinated_recheck(
        trusted=False, now_ns=46_000_000_000,
    )
    assert len(requested) == 1

    coordinator._schedule_rotation_coordinated_recheck(
        trusted=True, now_ns=46_000_000_000,
    )
    assert len(requested) == 2
    assert coordinator._rotation_coordinated_rechecks == 2

    coordinator._rotation_allocator.entries["entry"] = RankedEntry(
        "entry", "new-market", "BUY", 46_000_000_000,
        (EntryTier(500, .10),),
    )
    coordinator._rotation_allocator.exits["exit"] = RankedExit(
        "exit", "old-market", 500, .60, 46_000_000_000,
        (BookLevel(.60, 500),), (BookLevel(.62, 500),),
    )
    coordinator._schedule_rotation_coordinated_recheck(
        trusted=True, now_ns=90_000_000_000,
    )
    assert len(requested) == 2


def test_coordinated_recheck_disabled_without_live_active_rotation() -> None:
    requested: list[frozenset[str]] = []
    coordinator = object.__new__(ResidualTakerLiveCoordinator)
    coordinator._rotation_allocator = LiquidityWeightedAllocator()
    coordinator._signal = SimpleNamespace(tracked_exchange_ids=frozenset({"11", "22"}))
    coordinator._request_rotation_recheck = requested.append
    coordinator._active_rotation_exits = False
    coordinator._rotation_last_coordinated_recheck_ns = 0
    coordinator._rotation_coordinated_rechecks = 0
    coordinator._schedule_rotation_coordinated_recheck(
        trusted=True, now_ns=30_000_000_000,
    )
    assert requested == []
