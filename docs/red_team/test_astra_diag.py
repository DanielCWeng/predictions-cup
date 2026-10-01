"""Hostile invariants: run against pinned PR #74; xfails document unfixed defects."""
from datetime import UTC, datetime, timedelta
from dataclasses import replace
import pytest
from predictions_cup.analysis import live_diag as d

T = datetime(2026, 10, 1, tzinfo=UTC)
def q(t, bid, ask, depth=10):
    return d.Quote(T + timedelta(seconds=t), bid, ask, depth, depth)

@pytest.mark.xfail(strict=True, reason="ASTRA-002: unchanged price drops depth updates")
def test_zero_depth_update_must_remove_executable_candidate():
    sig = (q(0,.49,.51), q(1,.49,.51,0), q(3,.52,.54,0))
    pm = (q(0,.49,.51),q(2,.54,.56))
    result = d.analyze_lead_lag(sig_quotes=sig, external_quotes=pm, latency_ms=10)[0]
    assert result.status != d.ResearchStatus.MONETIZABLE_CANDIDATE

@pytest.mark.xfail(strict=True, reason="ASTRA-002: invalidation removed from asof state")
def test_invalidation_must_remove_executable_candidate():
    sig = (q(0,.49,.51),replace(q(1,.49,.51), trusted=False),q(3,.52,.54))
    pm = (q(0,.49,.51),q(2,.54,.56))
    result = d.analyze_lead_lag(sig_quotes=sig, external_quotes=pm, latency_ms=10)[0]
    assert result.status != d.ResearchStatus.MONETIZABLE_CANDIDATE

@pytest.mark.xfail(strict=True, reason="ASTRA-003: BUY edge relabeled as SELL edge")
def test_latency_preserves_original_side():
    result = d.analyze_lead_lag(sig_quotes=(q(0,.49,.51),q(1.1,.59,.61)),
        external_quotes=(q(0,.49,.51),q(1,.54,.56)),latency_ms=100)[0]
    assert result.latency_adjusted_edge == pytest.approx(.55-.61)

@pytest.mark.xfail(strict=True, reason="ASTRA-004: no outcome watermark")
def test_unobserved_300_second_horizon_not_scored():
    sig = (q(0,.49,.51), q(1,.54,.56))
    pm = (q(0,.55,.57),)
    triggers = d.construct_gap_episodes(sig_quotes=sig,external_quotes=pm,threshold_ticks=3)
    assert not d.observe_snapback(triggers=triggers,sig_quotes=sig,
        external_quotes=pm,horizons_seconds=(300,))

@pytest.mark.xfail(strict=True, reason="ASTRA-005: missing economics returns QUOTE_NORMAL")
def test_missing_economics_not_quote_permission():
    result,_ = d.recommend_market(sample_count=10,independent_event_count=10,
        expected_edge=None,adverse_selection=None,capital_time_efficiency=None)
    assert result == d.MarketRecommendation.NO_DATA

@pytest.mark.xfail(strict=True, reason="ASTRA-005: negative edge evaluated after WIDEN")
def test_negative_edge_must_pause_even_with_adverse_selection():
    result,_ = d.recommend_market(sample_count=10,independent_event_count=10,
        expected_edge=-.03,adverse_selection=.01,capital_time_efficiency=-1)
    assert result == d.MarketRecommendation.PAUSE

@pytest.mark.xfail(strict=True, reason="ASTRA-002 amendment: deep liquidity attributed to best price")
def test_loaded_depth_at_best_ask_excludes_expensive_levels(tmp_path):
    import json
    import pyarrow as pa
    import pyarrow.parquet as pq
    root = tmp_path/'normalized_events'
    root.mkdir()
    common = dict(exchange_id='x',observed_at=T,best_bid='.49',best_ask='.51')
    rows = [dict(common,event_type='DEPTH_SNAPSHOT',payload_json=json.dumps({
        'bids':[{'price':'.49','quantity':1}],
        'asks':[{'price':'.51','quantity':1},{'price':'.99','quantity':100}],
    })),dict(common,event_type='BBO_SNAPSHOT',payload_json=None)]
    pq.write_table(pa.Table.from_pylist(rows),root/'fixture.parquet')
    quotes = d._load_quotes(tmp_path,stream='normalized_events',identity_column='exchange_id',identities={'x'},sig=True)
    assert quotes['x'][0].ask_depth == 1
