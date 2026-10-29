"""Rotation must discover screened opportunities without granting execution authority."""

from __future__ import annotations

from dataclasses import replace

from predictions_cup.maker import GateMode
from predictions_cup.maker.liquidity_allocator import LiquidityWeightedAllocator
from predictions_cup.maker.residual_live import ResidualTakerLiveCoordinator
from predictions_cup.runtime.models import RuntimePosition
from test_liquidity_allocator import snapshot as allocator_snapshot
from test_make001 import NOW, _engine, _maker_snapshot


def _advisory_candidates() -> tuple[LiquidityWeightedAllocator, LiquidityWeightedAllocator]:
    strict = LiquidityWeightedAllocator(min_rotation_gain=0)
    screen = LiquidityWeightedAllocator(
        min_rotation_gain=0, advisory_include_resting_orders=True,
    )
    held = (RuntimePosition("11", "m11", "t1", 500, -500),)
    exit_market = allocator_snapshot(
        eid="11", market="m11", signed=-500, pm_bid=.80,
        pm_ask=.82, yes_ask=164, open_order=True,
        all_positions=held,
    )
    replacement = allocator_snapshot(
        eid="22", market="m22", pm_bid=.60, pm_ask=.62,
        yes_ask=106, open_order=True, all_positions=held,
    )
    for allocator in (strict, screen):
        allocator.observe(exit_market, "token-11")
        allocator.observe(replacement, "token-22")
    return strict, screen


def test_preclear_screen_finds_economics_but_strict_allocator_stays_blocked() -> None:
    strict, screen = _advisory_candidates()
    assert strict.rank(now_ns=30_000_000_000) == ()
    assert strict.entries == {} and strict.exits == {}
    candidate = screen.rank(now_ns=30_000_000_000)
    assert len(candidate) == 1
    assert candidate[0].assessment.exit_exchange_id == "11"
    assert candidate[0].assessment.replacement_exchange_id == "22"
    assert candidate[0].gain_susq > 0
    assert screen.blocked_by_orders == {"11", "22"}


def test_strict_allocator_requires_fresh_clean_observations_after_cancellation() -> None:
    strict, screen = _advisory_candidates()
    held = (RuntimePosition("11", "m11", "t1", 500, -500),)
    for allocator in (strict, screen):
        allocator.observe(
            allocator_snapshot(
                eid="11", market="m11", signed=-500,
                pm_bid=.80, pm_ask=.82, yes_ask=164,
                all_positions=held,
            ),
            "token-11",
        )
        allocator.observe(
            allocator_snapshot(
                eid="22", market="m22", pm_bid=.60,
                pm_ask=.62, yes_ask=106, all_positions=held,
            ),
            "token-22",
        )
    assert strict.rank(now_ns=30_000_000_000)
    assert screen.blocked_by_orders == set()


def test_preclear_lease_cancels_quotes_then_expires_without_sticking() -> None:
    engine = _engine()
    initial = _maker_snapshot()
    assert initial.now_monotonic_ns == NOW
    assert engine.quote(initial).desired is not None
    engine.set_rotation_quote_pause_targets(
        frozenset({"36"}), observed_ns=NOW, ttl_ns=10,
    )
    paused = engine.quote(initial)
    assert paused.gate.mode is GateMode.CANCEL
    assert paused.gate.reason == "rotation_preclear_owned_quotes"
    assert paused.desired is None
    assert engine.quote(replace(initial, now_monotonic_ns=NOW + 11)).desired is not None
    engine.set_rotation_quote_pause_targets(frozenset(), observed_ns=NOW + 11)
    assert engine.quote(initial).desired is not None


def test_preclear_publication_is_bounded_and_clears_on_trust_loss() -> None:
    _, screen = _advisory_candidates()
    published: list[frozenset[str]] = []
    requested: list[frozenset[str]] = []
    coordinator = object.__new__(ResidualTakerLiveCoordinator)
    coordinator._rotation_preclear_allocator = screen
    coordinator._rotation_pause_quotes = lambda ids, now: published.append(ids)
    coordinator._active_rotation_exits = True
    coordinator._rotation_last_preclear_rank_ns = 0
    coordinator._rotation_preclear_targets = frozenset()
    coordinator._rotation_pending_entries = {}
    coordinator._request_rotation_recheck = requested.append
    coordinator._publish_rotation_preclear(trusted=True, now_ns=30_000_000_000)
    assert published == [frozenset({"11", "22"})]
    assert requested == [frozenset({"11", "22"})]
    coordinator._publish_rotation_preclear(trusted=False, now_ns=30_000_000_001)
    assert published[-1] == frozenset()
    coordinator._publish_rotation_preclear(trusted=True, now_ns=40_000_000_000)
    assert published[-1] == frozenset()


def test_disabled_preclear_never_requests_maker_cancellations() -> None:
    _, screen = _advisory_candidates()
    published: list[frozenset[str]] = []
    coordinator = object.__new__(ResidualTakerLiveCoordinator)
    coordinator._rotation_preclear_allocator = screen
    coordinator._rotation_pause_quotes = lambda ids, now: published.append(ids)
    coordinator._active_rotation_exits = False
    coordinator._publish_rotation_preclear(trusted=True, now_ns=30_000_000_000)
    assert published == []
