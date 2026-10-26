from dataclasses import replace
from time import perf_counter_ns

import pytest

from predictions_cup.maker.election_catalyst import (
    CatalystReport, CatalystWakeHandoff, ElectionCatalystGate, ElectionCatalystRouter, MarketView,
)
from predictions_cup.runtime.models import RuntimeLevel

NOW = 20_000_000_000
EPOCH = 2_000_000
IDS = frozenset({"844", "845"})
REGISTRY = {
    "ap": ("wire", "apnews.com"),
    "ap2": ("wire", "apnews.com"),
    "virginia": ("official", "elections.virginia.gov"),
    "network": ("broadcaster", "abcnews.go.com"),
}


def gate(**changes):
    args = dict(exact_exchange_ids=IDS, source_registry=REGISTRY, size=20)
    args.update(changes)
    return ElectionCatalystGate(**args)


def report(source_id="ap", **changes):
    family, host = REGISTRY[source_id]
    defaults = dict(
        event_key="VA-01:early-batch:county-a", exchange_id="844",
        report_key=f"report-{source_id}-1", source_id=source_id,
        source_family=family, source_url=f"https://{host}/live/2026/va01",
        direction="BUY", fair_lower=0.78, fair_upper=0.82,
        published_epoch_ms=EPOCH - 1_000,
        received_monotonic_ns=NOW - 100_000_000,
    )
    defaults.update(changes)
    return CatalystReport(**defaults)


def market(**changes):
    defaults = dict(
        exchange_id="844", market_open=True, account_trusted=True,
        bbo_trusted=True, depth_trusted=True,
        depth_observed_ns=NOW - 200_000_000, now_monotonic_ns=NOW,
        bids=(RuntimeLevel(139, 50),), asks=(RuntimeLevel(140, 50),),
        available_cash=100.0, reducible_shares=0,
    )
    defaults.update(changes)
    return MarketView(**defaults)


def ingest(g, r, view=None, now=NOW, epoch=EPOCH):
    return g.consider(r, view=market() if view is None else view,
                      now_epoch_ms=epoch, now_monotonic_ns=now)


def paired(g=None):
    g = g or gate()
    first = ingest(g, report())
    second = ingest(g, report("virginia"))
    return g, first, second


def test_two_independent_sources_qualify_and_wake_existing_taker():
    g, first, (why, candidate) = paired()
    assert first == ("awaiting_independent_confirmation", None)
    assert why == "qualified_for_taker_recheck"
    assert candidate and candidate.exchange_id == "844"
    assert candidate.direction == "BUY"
    assert candidate.quantity == 20 and candidate.limit_price_ticks == 140
    assert candidate.net_expected_value > 0
    wakes = []
    bridge = CatalystWakeHandoff(lambda ids, *, observed_monotonic_ns: wakes.append((ids, observed_monotonic_ns)))
    bridge.forward(candidate)
    assert wakes == [(("844",), NOW)]
    assert bridge.forwarded == 1
    # The output is not an exchange order, order plan, or RISK-002 admission.
    assert not hasattr(candidate, "idempotency_key")


def test_same_underlying_reporting_family_is_not_independent():
    g = gate()
    assert ingest(g, report())[1] is None
    why, result = ingest(g, report("ap2"))
    assert why == "awaiting_independent_confirmation" and result is None


def test_duplicate_report_and_duplicate_event_not_forwarded():
    g = gate()
    ingest(g, report())
    assert ingest(g, report())[0] == "duplicate_report"
    assert ingest(g, report("virginia"))[1] is not None
    assert ingest(g, report("virginia"))[0] == "duplicate_event"


def test_conflicting_direction_quarantines_entire_event():
    g = gate()
    ingest(g, report())
    assert ingest(g, report("virginia", direction="SELL"))[0] == "contradictory_reports"
    assert ingest(g, report("network"))[0] == "contradicted_event"


def test_contradictory_probability_intervals_fail_closed():
    g = gate()
    ingest(g, report(fair_lower=0.78, fair_upper=0.81))
    why, _ = ingest(g, report("virginia", fair_lower=0.85, fair_upper=0.90))
    assert why == "contradictory_bounds"


@pytest.mark.parametrize("changes,reason", [
    ({"exchange_id": "999"}, "unknown_mapping"),
    ({"source_url": "https://apnews.com.attacker.example/report"}, "unverified_source"),
    ({"source_url": "http://apnews.com/report"}, "unverified_source"),
    ({"fair_lower": 0.90, "fair_upper": 0.50}, "invalid_probability_bound"),
    ({"received_monotonic_ns": NOW - 11_000_000_000}, "stale_or_future_evidence"),
    ({"received_monotonic_ns": NOW + 1}, "stale_or_future_evidence"),
    ({"published_epoch_ms": EPOCH - 121_000}, "stale_or_future_evidence"),
    ({"published_epoch_ms": EPOCH + 3_000}, "stale_or_future_evidence"),
])
def test_fail_closed_for_bad_ingest(changes, reason):
    assert ingest(gate(), report(**changes))[0] == reason


@pytest.mark.parametrize("changes", [
    {"market_open": False},
    {"account_trusted": False},
    {"bbo_trusted": False},
    {"depth_trusted": False},
    {"available_cash": None},
])
def test_venue_suspension_or_untrusted_account_and_book(changes):
    g = gate()
    ingest(g, report())
    reason, item = ingest(g, report("virginia"), view=market(**changes))
    assert reason == "market_or_account_untrusted" and item is None


def test_stale_depth_and_empty_cash_are_not_executable():
    g = gate()
    ingest(g, report())
    why, item = ingest(g, report("virginia"), view=market(depth_observed_ns=NOW-2_000_000_000))
    assert item is None and why == "not_executable_stale_data"
    g = gate()
    ingest(g, report())
    why, item = ingest(g, report("virginia"), view=market(available_cash=0.0))
    assert item is None and why == "not_executable_edge_or_depth_insufficient"


def test_no_edge_even_when_news_is_verified():
    g = gate()
    ingest(g, report())
    why, item = ingest(g, report("virginia"), view=market(asks=(RuntimeLevel(170, 50),)))
    assert item is None and why == "not_executable_edge_or_depth_insufficient"


def test_stale_independent_confirmation_is_rejected():
    g = gate()
    ingest(g, report(received_monotonic_ns=NOW-9_000_000_000))
    why, result = ingest(g, report("virginia"), now=NOW + 2_000_000_000)
    assert why in {"stale_corrobation", "awaiting_independent_confirmation"}
    assert result is None


def test_directional_sell_uses_upper_bound_and_bid_depth():
    g = gate()
    first = report(direction="SELL", fair_lower=0.65, fair_upper=0.71)
    second = report("virginia", direction="SELL", fair_lower=0.67, fair_upper=0.72)
    ingest(g, first)
    why, item = ingest(g, second, view=market(bids=(RuntimeLevel(160, 60),)))
    assert why == "qualified_for_taker_recheck"
    assert item.direction == "SELL" and item.fair_bound == 0.72


def test_burst_is_bounded_and_deduped():
    g = gate()
    for i in range(300):
        ingest(g, report(event_key=f"VA-01:update-{i}"))
    assert len(g._evidence) <= 128
    assert sum(g.counters.values()) == 300


def test_hot_path_micro_latency_smoke():
    # Bounded CPU budget: a verified two-report qualification is pure CPU/zero IO.
    durations = []
    for index in range(250):
        g = gate()
        a = replace(report(), event_key=f"VA-01:{index}")
        b = replace(report("virginia"), event_key=f"VA-01:{index}")
        start = perf_counter_ns()
        ingest(g, a)
        _, decision = ingest(g, b)
        durations.append(perf_counter_ns() - start)
        assert decision is not None
    durations.sort()
    assert durations[237] < 20_000_000  # 20 ms p95 regression ceiling, not a guarantee


def test_ingress_router_once():
    seen=[]
    route=ElectionCatalystRouter(gate(),current_market_view=lambda ident: market(exchange_id=ident),notify_sig=lambda ids, *, observed_monotonic_ns: seen.append((ids,observed_monotonic_ns)),utc_ms=lambda:EPOCH,monotonic_ns=lambda:NOW)
    assert route.on_verified_report(report())[1] is None
    assert route.on_verified_report(report('virginia'))[1] is not None
    assert route.on_verified_report(report('virginia'))[1] is None
    assert seen==[(('844',),NOW)]


def test_delayed_snapshot_rejected():
    g=gate()
    ingest(g,report())
    stale=market(now_monotonic_ns=NOW-400_000_000,depth_observed_ns=NOW-500_000_000)
    reason,decision=ingest(g,report('virginia'),view=stale)
    assert reason=='market_or_account_untrusted' and decision is None
