"""Depth-weighted entry and exit matching; no live placement permissions."""
from __future__ import annotations

from dataclasses import replace
from typing import Any, cast

import pytest

from predictions_cup.maker.capital_rotation import BookLevel
from predictions_cup.maker.contracts import ExternalQuoteState, MakerMarketSnapshot
from predictions_cup.maker.liquidity_allocator import (
    EntryTier,
    LiquidityWeightedAllocator,
    RankedEntry,
    RankedExit,
    RankedRotation,
)
from predictions_cup.runtime.models import (
    AccountTrustGrade,
    RuntimeBook,
    RuntimeLevel,
    RuntimeMarket,
    RuntimeOrderState,
    RuntimePortfolio,
    RuntimePosition,
    RuntimeSnapshot,
)


def snapshot(
    *, eid: str = "11", market: str = "m11", signed: int = 0,
    yes_bid: int = 100, yes_ask: int = 104, pm_bid: float = .58,
    pm_ask: float = .60, now: int = 30_000_000_000,
    depth_age_ms: int = 5, pm_age_ms: int = 5, trust: bool = True,
    open_order: bool = False, proxy: bool = False,
    bid_depth: int = 500, ask_depth: int = 500,
    all_positions: tuple[RuntimePosition, ...] | None = None,
) -> MakerMarketSnapshot:
    positions = (
        (() if not signed else (RuntimePosition(eid, market, "t1", abs(signed), signed),))
        if all_positions is None else all_positions
    )
    portfolio = RuntimePortfolio(
        positions=positions,
        orders=() if not open_order else (RuntimeOrderState(
            "order", eid, market, "t1", 100, True, False, signed_quantity=100
        ),),
        account_trusted=trust,
        account_trust_grade=(AccountTrustGrade.PROXY if proxy else AccountTrustGrade.TRUSTED),
    )
    runtime = RuntimeSnapshot(
        markets=(RuntimeMarket(market, "open", (eid,), "t1", True, True),),
        books=(RuntimeBook(eid, market, "t1",
            (RuntimeLevel(yes_bid, bid_depth),),
            (RuntimeLevel(yes_ask, ask_depth),),
            trust, now-int(depth_age_ms*1_000_000)),),
        portfolio=portfolio, observation_monotonic_ns=now,
    )
    q = ExternalQuoteState(
        "token-"+eid, pm_bid, pm_ask, now-int(pm_age_ms*1_000_000),
        trust, "v1", best_bid_size=2000, best_ask_size=2000,
    )
    return MakerMarketSnapshot(
        runtime=runtime, exchange_id=eid, market_id=market, tournament_id="t1",
        now_monotonic_ns=now,
        sig_bbo_observed_ns=now-int(depth_age_ms*1_000_000),
        sig_bbo_trusted=trust,
        sig_depth_observed_ns=now-int(depth_age_ms*1_000_000),
        sig_depth_trusted=trust,
        account_observed_ns=now, inventory_observed_ns=now,
        external_quotes={q.token_id:q},
    )


def test_entry_uses_executable_depth_and_not_midpoint() -> None:
    a = LiquidityWeightedAllocator()
    a.observe(snapshot(eid="11", pm_bid=.60, pm_ask=.62, yes_ask=106), "token-11")
    assert a.entries["11"].direction == "BUY"
    assert a.entries["11"].capacity == 500
    # 0.60 - 0.53 - 0.01 => .06 net/share.
    assert a.entries["11"].net_ev(500) == pytest.approx(30)


def test_signed_no_exit_and_ranked_rotation_costs() -> None:
    a = LiquidityWeightedAllocator(min_rotation_gain=0)
    holdings = (RuntimePosition("11", "m11", "t1", 500, -500),)
    a.observe(snapshot(eid="11", market="m11", signed=-500,
                       pm_bid=.80, pm_ask=.82, yes_ask=164,
                       all_positions=holdings), "token-11")
    a.observe(snapshot(eid="22", market="m22", signed=0,
                       pm_bid=.60, pm_ask=.62, yes_ask=106,
                       all_positions=holdings), "token-22")
    best=a.rank(now_ns=30_000_000_000)
    assert best
    item=best[0]
    assert item.assessment.exit_exchange_id == "11"
    assert item.assessment.replacement_exchange_id == "22"
    assert item.assessment.shares == 500
    # NO exit proceeds 500*(1-.82)=90; holding benchmark
    # is NO fair 1-PM bid = .20, hence 100 expected hold value.
    assert item.assessment.exit_proceeds == pytest.approx(90)
    assert item.assessment.hold_fair_value == pytest.approx(100)
    assert item.entry_ev_susq == pytest.approx(30)
    # 0.25c per leg extra post-signal repricing haircut reduces 12.5 to 10.
    assert item.gain_susq == pytest.approx(10.0)


def test_ranking_picks_deeper_opportunity_over_flashy_touch() -> None:
    a=LiquidityWeightedAllocator(min_rotation_gain=0)
    a.exits["1"] = RankedExit("1","m1",500,.6,100,
                             (BookLevel(.60,500),),(BookLevel(.62,500),))
    a.entries["tiny"]=RankedEntry("tiny","m2","BUY",100,
                                    (EntryTier(10,.20),))
    a.entries["deep"]=RankedEntry("deep","m3","BUY",100,
                                    (EntryTier(500,.06),))
    best=a.rank(now_ns=100)
    assert best[0].assessment.replacement_exchange_id=="deep"
    assert best[0].gain_susq == pytest.approx(20.0)


def test_marginal_edge_is_not_assumed_constant_across_depth() -> None:
    entry=RankedEntry("22","m2","BUY",100,
                      (EntryTier(100,.10),EntryTier(150,.04)))
    assert entry.net_ev(100)==pytest.approx(10)
    assert entry.net_ev(200)==pytest.approx(14)
    assert entry.net_ev(250)==pytest.approx(16)
    assert entry.net_ev(251) is None


@pytest.mark.parametrize("override", [
    {"trust":False}, {"proxy":True}, {"open_order":True},
    {"depth_age_ms":20_000}, {"pm_age_ms":7000},
])
def test_untrusted_proxy_outstanding_and_stale_books_excluded(
    override: dict[str, object],
) -> None:
    a=LiquidityWeightedAllocator()
    args=dict(eid="11",signed=500,yes_bid=100,pm_bid=.45,pm_ask=.47)
    args.update(override)
    a.observe(snapshot(**cast(Any, args)), "token-11")
    assert a.exits=={}
    assert a.entries=={}


def test_expiry_and_conflicting_same_market_not_rotated() -> None:
    a=LiquidityWeightedAllocator(max_age_ns=500, min_rotation_gain=0)
    a.exits["1"]=RankedExit("1","race",500,.6,100,
                           (BookLevel(.6,500),),(BookLevel(.7,500),))
    a.entries["2"]=RankedEntry("2","race","BUY",100,(EntryTier(500,.2),))
    assert a.rank(now_ns=100)==()
    a.entries["2"]=RankedEntry("2","different","BUY",100,(EntryTier(500,.2),))
    assert a.rank(now_ns=700)==()


def test_respects_depth_exit_cost_and_fairness_floor() -> None:
    a=LiquidityWeightedAllocator(min_rotation_gain=5)
    a.exits["1"]=RankedExit("1","m1",500,.8,100,
                           (BookLevel(.50,500),),(BookLevel(.60,500),))
    a.entries["2"]=RankedEntry("2","m2","BUY",100,(EntryTier(500,.1),))
    assert a.rank(now_ns=100)==()


def test_internal_cache_invalidates_old_market_on_new_untrusted_event() -> None:
    a=LiquidityWeightedAllocator()
    a.observe(snapshot(eid="11"),"token-11")
    assert "11" in a.entries
    a.observe(snapshot(eid="11",trust=False),"token-11")
    assert "11" not in a.entries


def test_risk_reconciliation_loss_clears_cross_market_cache() -> None:
    a=LiquidityWeightedAllocator()
    holdings=(RuntimePosition("11","m11","t1",500,-500),)
    a.observe(snapshot(eid="11", signed=-500, all_positions=holdings,
                       pm_bid=.8, pm_ask=.82, yes_ask=164),"token-11")
    assert a.exits
    a.observe(snapshot(eid="22",all_positions=holdings),
              "token-22",risk_state_trusted=False)
    assert not a.exits and not a.entries


def test_signed_fill_invalidates_previous_rotation_candidates() -> None:
    a=LiquidityWeightedAllocator()
    holdings=(RuntimePosition("11","m11","t1",500,-500),)
    a.observe(snapshot(eid="11",signed=-500,all_positions=holdings,
                       pm_bid=.80,pm_ask=.82,yes_ask=164),"token-11")
    assert "11" in a.exits
    after=(RuntimePosition("11","m11","t1",300,-300),)
    a.observe(snapshot(eid="22",all_positions=after),"token-22")
    assert "11" not in a.exits


def test_directional_position_cap_limits_entry_size() -> None:
    a=LiquidityWeightedAllocator(max_position=650)
    s=snapshot(eid="11",signed=500,pm_bid=.8,pm_ask=.82,yes_ask=106)
    a.observe(s,"token-11")
    assert a.entries["11"].direction=="BUY"
    assert a.entries["11"].capacity == 150


def test_unrelated_resting_order_does_not_flush_all_entry_candidates() -> None:
    allocator = LiquidityWeightedAllocator()
    positions = (RuntimePosition("11", "m11", "t1", 500, -500),)
    allocator.observe(
        snapshot(eid="11", signed=-500, all_positions=positions,
                 pm_bid=.80, pm_ask=.82, yes_ask=164), "token-11",
    )
    allocator.observe(
        snapshot(eid="22", all_positions=positions,
                 pm_bid=.60, pm_ask=.62, yes_ask=106), "token-22",
    )
    assert "11" in allocator.exits and "22" in allocator.entries
    changed = snapshot(eid="33", all_positions=positions)
    fresh_portfolio = replace(
        changed.runtime.portfolio,
        orders=(RuntimeOrderState(
            "some-maker-order", "33", "m33", "t1", 10,
            True, False, signed_quantity=10,
        ),),
    )
    changed = replace(changed, runtime=replace(
        changed.runtime, portfolio=fresh_portfolio,
    ))
    allocator.observe(changed, "token-33")
    assert "11" in allocator.exits
    assert "22" in allocator.entries


def test_quote_churn_for_one_market_invalidates_only_that_market() -> None:
    allocator = LiquidityWeightedAllocator()
    positions = (RuntimePosition("11", "m11", "t1", 500, -500),)
    allocator.observe(
        snapshot(eid="11", signed=-500, all_positions=positions,
                 pm_bid=.80, pm_ask=.82, yes_ask=164), "token-11",
    )
    allocator.observe(
        snapshot(eid="22", all_positions=positions,
                 pm_bid=.60, pm_ask=.62, yes_ask=106), "token-22",
    )
    # This order is on the exit market, so only the exit becomes unsafe.
    stale_exit = snapshot(eid="11", signed=-500, all_positions=positions,
                          pm_bid=.80, pm_ask=.82, yes_ask=164)
    fresh_portfolio = replace(
        stale_exit.runtime.portfolio,
        orders=(RuntimeOrderState(
            "open-maker-order", "11", "m11", "t1", 50,
            True, False, signed_quantity=50,
        ),),
    )
    stale_exit = replace(stale_exit, runtime=replace(
        stale_exit.runtime, portfolio=fresh_portfolio,
    ))
    allocator.observe(stale_exit, "token-11")
    assert "11" not in allocator.exits
    assert "22" in allocator.entries

def test_depth_weighted_exit_shortlist_does_not_prefer_one_share_touch() -> None:
    a = LiquidityWeightedAllocator(min_rotation_gain=0)
    now = 100
    a.entries["best"] = RankedEntry(
        "best", "entry-market", "BUY", now, (EntryTier(500, .09),)
    )
    # Twenty-five 1-share bids look ideal at the best bid, but cannot release
    # the minimum practical 50-share slice. Old touch-only shortlist took them
    # all and would omit the genuinely liquid 500-share exit.
    for i in range(25):
        eid = f"flash-{i}"
        a.exits[eid] = RankedExit(
            eid, eid, 500, .60, now,
            (BookLevel(.60, 1),), (BookLevel(.62, 500),),
        )
    a.exits["liquid"] = RankedExit(
        "liquid", "real-exit", 500, .60, now,
        (BookLevel(.59, 500),), (BookLevel(.62, 500),),
    )
    result = a.rank(now_ns=now, limit=1)
    assert result
    assert result[0].assessment.exit_exchange_id == "liquid"
    assert result[0].assessment.shares == 500


def test_repricing_haircut_rejects_fragile_rotations() -> None:
    def make(*, reprice_slippage_per_leg: float) -> tuple[RankedRotation, ...]:
        a = LiquidityWeightedAllocator(
            min_rotation_gain=5, reprice_slippage_per_leg=reprice_slippage_per_leg
        )
        a.entries["new"] = RankedEntry(
            "new", "new-market", "BUY", 10, (EntryTier(500, .03),)
        )
        a.exits["old"] = RankedExit(
            "old", "old-market", 500, .6, 10,
            (BookLevel(.6, 500),), (BookLevel(.62, 500),),
        )
        return a.rank(now_ns=10)
    # Baseline proposal at .03 gross marginal edge yields 7.5 after 1.5c
    # costs. A further 0.25c per leg reduces this to exactly 5.0.
    base = make(reprice_slippage_per_leg=0)
    cautious = make(reprice_slippage_per_leg=.0025)
    assert base and cautious
    assert base[0].gain_susq == pytest.approx(7.5)
    assert cautious[0].gain_susq == pytest.approx(5)
    assert make(reprice_slippage_per_leg=.005) == ()


def test_rotation_finds_sparse_depth_breakpoint_instead_of_round_lot() -> None:
    """Take 37 profitable shares instead of walking 50 into a thin bad bid."""
    allocator = LiquidityWeightedAllocator(min_rotation_gain=1.0)
    allocator.exits["old"] = RankedExit(
        "old", "held", 100, .60, 100,
        (BookLevel(.60, 37), BookLevel(.30, 63)),
        (BookLevel(.65, 100),),
    )
    allocator.entries["new"] = RankedEntry(
        "new", "replacement", "BUY", 100,
        (EntryTier(37, .12), EntryTier(63, .02)),
    )
    results = allocator.rank(now_ns=100)
    assert len(results) == 1
    assert results[0].assessment.shares == 37
    assert results[0].gain_susq > 1.0


def test_rotation_shortlist_does_not_repeat_same_exit_or_replacement() -> None:
    allocator = LiquidityWeightedAllocator(min_rotation_gain=1.0)
    for eid in ("old1", "old2"):
        allocator.exits[eid] = RankedExit(
            eid, eid, 500, .60, 100,
            (BookLevel(.60, 500),), (BookLevel(.65, 500),),
        )
    for eid, edge in (("new1", .12), ("new2", .10)):
        allocator.entries[eid] = RankedEntry(
            eid, eid, "BUY", 100, (EntryTier(500, edge),),
        )
    selected = allocator.rank(now_ns=100, limit=3)
    assert len(selected) == 2
    assert len({x.assessment.exit_exchange_id for x in selected}) == 2
    assert len({x.assessment.replacement_exchange_id for x in selected}) == 2
