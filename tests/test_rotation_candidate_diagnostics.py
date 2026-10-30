"""Explain zero rotation candidates without weakening any execution gates."""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace
from typing import Any, cast

import pytest

from predictions_cup.maker.capital_rotation import BookLevel
from predictions_cup.maker.liquidity_allocator import (
    EntryTier,
    LiquidityWeightedAllocator,
    RankedEntry,
    RankedExit,
)
from predictions_cup.maker.residual_live import ResidualTakerLiveCoordinator
from predictions_cup.maker.residual_taker import ResidualTakerSignal
from predictions_cup.mapping.models import MappingClass, MappingDirection, MappingStatus
from predictions_cup.runtime.models import RuntimePosition
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
    coordinator._signal = cast(
        ResidualTakerSignal,
        SimpleNamespace(tracked_exchange_ids=frozenset(str(i) for i in range(300))),
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
    coordinator._signal = cast(
        ResidualTakerSignal,
        SimpleNamespace(tracked_exchange_ids=frozenset({"11", "22"})),
    )
    coordinator._request_rotation_recheck = requested.append
    coordinator._active_rotation_exits = False
    coordinator._rotation_last_coordinated_recheck_ns = 0
    coordinator._rotation_coordinated_rechecks = 0
    coordinator._schedule_rotation_coordinated_recheck(
        trusted=True, now_ns=30_000_000_000,
    )
    assert requested == []


def test_coordinated_sweep_uses_atomic_cache_not_paced_maker_queue() -> None:
    paced: list[frozenset[str]] = []
    atomic: list[tuple[frozenset[str], int]] = []
    coordinator = object.__new__(ResidualTakerLiveCoordinator)
    coordinator._rotation_allocator = LiquidityWeightedAllocator()
    coordinator._signal = cast(
        ResidualTakerSignal, SimpleNamespace(tracked_exchange_ids=frozenset({"11", "22"})),
    )
    coordinator._request_rotation_recheck = paced.append
    coordinator._rotation_cached_sweep = lambda ids, now: atomic.append((ids, now))
    coordinator._active_rotation_exits = True
    coordinator._rotation_last_coordinated_recheck_ns = 0
    coordinator._rotation_coordinated_rechecks = 0
    coordinator._schedule_rotation_coordinated_recheck(
        trusted=True, now_ns=30_000_000_000,
    )
    assert atomic == [(frozenset({"11", "22"}), 30_000_000_000)]
    assert paced == []
    assert coordinator._rotation_coordinated_rechecks == 1
    coordinator._schedule_rotation_coordinated_recheck(
        trusted=True, now_ns=31_000_000_000,
    )
    coordinator._schedule_rotation_coordinated_recheck(
        trusted=False, now_ns=46_000_000_000,
    )
    assert len(atomic) == 1


def test_atomic_cache_refresh_prices_both_sides_without_order_sender() -> None:
    def mapping(token: str) -> SimpleNamespace:
        return SimpleNamespace(
            status=MappingStatus.VERIFIED,
            mapping_class=MappingClass.EXACT,
            mapping_direction=MappingDirection.SAME,
            direct_polymarket=SimpleNamespace(mapped_token_id=token),
        )

    coordinator = object.__new__(ResidualTakerLiveCoordinator)
    coordinator._active_rotation_exits = True
    coordinator._rotation_allocator = LiquidityWeightedAllocator(min_rotation_gain=0)
    coordinator._rotation_preclear_allocator = LiquidityWeightedAllocator(
        min_rotation_gain=0, advisory_include_resting_orders=True,
    )
    coordinator._mapping = cast(Any, {
        "11": mapping("token-11"),
        "22": mapping("token-22"),
    })
    coordinator._signal = cast(
        ResidualTakerSignal, SimpleNamespace(tracked_exchange_ids=frozenset({"11", "22"})),
    )
    capital = SimpleNamespace(
        account_trusted=True, reconciliation_complete=True, marks_trusted=True,
        exposure=SimpleNamespace(trusted=True, uncertain_order_exposure=0),
        global_halt=None,
    )
    coordinator._risk_context_source = cast(
        Any, SimpleNamespace(capital_state=capital, kill_switch=False),
    )
    coordinator._kill_switch = cast(Any, SimpleNamespace(active=False))
    held = (RuntimePosition("11", "m11", "t1", 500, -500),)
    snapshots = {
        "11": snapshot(
            eid="11", market="m11", signed=-500, pm_bid=.80, pm_ask=.82,
            yes_ask=164, all_positions=held,
        ),
        "22": snapshot(
            eid="22", market="m22", signed=0, pm_bid=.60, pm_ask=.62,
            yes_ask=106, all_positions=held,
        ),
    }
    # No dispatch, cancel, quote pause, or resting-order sink is wired.
    coordinator.observe_rotation_cached_batch(snapshots)
    strict = coordinator._rotation_allocator
    assert strict is not None
    assert strict.entries.keys() == {"22"}
    assert strict.exits.keys() == {"11"}
    assert strict.rank(now_ns=30_000_000_000)
    assert coordinator._rotation_preclear_allocator is not None
    assert coordinator._rotation_preclear_allocator.rank(now_ns=30_000_000_000)

    # On central risk trust loss, no cached proposal remains executable.
    capital.marks_trusted = False
    coordinator.observe_rotation_cached_batch(snapshots)
    assert strict.entries == {} and strict.exits == {}
    assert coordinator._rotation_preclear_allocator.entries == {}


def test_disabled_atomic_sweep_cannot_prime_rotation_cache() -> None:
    coordinator = object.__new__(ResidualTakerLiveCoordinator)
    allocator = LiquidityWeightedAllocator()
    coordinator._rotation_allocator = allocator
    coordinator._active_rotation_exits = False
    coordinator.observe_rotation_cached_batch({
        "11": snapshot(eid="11", pm_bid=.70, pm_ask=.72, yes_ask=106),
    })
    assert allocator.observed == 0


def test_bbo_only_depth_scout_requires_current_strong_after_cost_edge() -> None:
    allocator = LiquidityWeightedAllocator(min_marginal_net_edge=.08)
    good = snapshot(
        eid="22", market="m22", pm_bid=.80, pm_ask=.82,
        yes_bid=100, yes_ask=106,
    )
    stale = replace(
        good, sig_depth_observed_ns=good.now_monotonic_ns - 20_000_000_000,
    )
    allocator.observe(stale, "token-22")
    assert _reason(allocator, "22") == "sig_depth_stale"
    assert allocator.entries == {}
    score = allocator.stale_depth_entry_probe(
        stale, "token-22", max_sig_bbo_age_ns=1_000_000_000,
    )
    assert score is not None and score > 0
    assert allocator.entries == {}  # Scouting is not execution authority.

    weak = snapshot(eid="22", pm_bid=.60, pm_ask=.62, yes_ask=106)
    assert allocator.stale_depth_entry_probe(
        weak, "token-22", max_sig_bbo_age_ns=1_000_000_000,
    ) is None
    old_bbo = replace(stale, sig_bbo_observed_ns=stale.now_monotonic_ns - 2_000_000_000)
    assert allocator.stale_depth_entry_probe(
        old_bbo, "token-22", max_sig_bbo_age_ns=1_000_000_000,
    ) is None
    old_pm = replace(stale, external_quotes={
        "token-22": replace(
            stale.external_quotes["token-22"],
            observed_monotonic_ns=stale.now_monotonic_ns - 6_000_000_000,
        ),
    })
    assert allocator.stale_depth_entry_probe(
        old_pm, "token-22", max_sig_bbo_age_ns=1_000_000_000,
    ) is None
    own_quote = snapshot(eid="22", pm_bid=.80, pm_ask=.82, yes_ask=106,
                         open_order=True)
    assert allocator.stale_depth_entry_probe(
        own_quote, "token-22", max_sig_bbo_age_ns=1_000_000_000,
    ) is None


def test_atomic_scout_only_requests_depth_on_trusted_price_qualified_books() -> None:
    record = SimpleNamespace(
        status=MappingStatus.VERIFIED,
        mapping_class=MappingClass.EXACT,
        mapping_direction=MappingDirection.SAME,
        direct_polymarket=SimpleNamespace(mapped_token_id="token-22"),
    )
    coordinator = object.__new__(ResidualTakerLiveCoordinator)
    coordinator._rotation_allocator = LiquidityWeightedAllocator(
        min_rotation_gain=100, min_marginal_net_edge=.08,
    )
    coordinator._rotation_preclear_allocator = None
    coordinator._active_rotation_exits = True
    coordinator._signal = cast(
        ResidualTakerSignal,
        SimpleNamespace(tracked_exchange_ids=frozenset({"22"})),
    )
    coordinator._mapping = cast(Any, {"22": record})
    capital = SimpleNamespace(
        account_trusted=True, reconciliation_complete=True, marks_trusted=True,
        exposure=SimpleNamespace(trusted=True, uncertain_order_exposure=0),
        global_halt=None,
    )
    coordinator._risk_context_source = cast(
        Any, SimpleNamespace(capital_state=capital, kill_switch=False),
    )
    coordinator._kill_switch = cast(Any, SimpleNamespace(active=False))
    coordinator._max_sig_bbo_age_ns = 1_000_000_000
    requested: list[tuple[str, float]] = []
    coordinator._request_fresh_depth = lambda eid, score: requested.append((eid, score))
    snap = snapshot(eid="22", market="m22", pm_bid=.80, pm_ask=.82, yes_ask=106)
    stale = replace(
        snap, sig_depth_observed_ns=snap.now_monotonic_ns - 20_000_000_000,
    )
    coordinator.observe_rotation_cached_batch({"22": stale})
    assert len(requested) == 1 and requested[0][0] == "22"
    assert requested[0][1] > 0
    assert coordinator._rotation_allocator.entries == {}
    # A later risk block cannot make a queued market executable or add requests.
    capital.exposure.uncertain_order_exposure = 50
    coordinator.observe_rotation_cached_batch({"22": stale})
    assert len(requested) == 1
    assert coordinator._rotation_allocator.entries == {}
    # With risk trusted again, a clean fresh book is observed without REST.
    capital.exposure.uncertain_order_exposure = 0
    coordinator.observe_rotation_cached_batch({"22": snap})
    assert coordinator._rotation_allocator.entries.keys() == {"22"}
    assert len(requested) == 1
