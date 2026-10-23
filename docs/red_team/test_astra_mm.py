"""Hostile replay invariants, pinned existing PR #72; this is not a second replay."""
from dataclasses import replace
import pytest
from predictions_cup import mm_replay_001 as m
N=1_000_000_000
def obs(t,bid=.49,ask=.51,**kw):
    return m.BookObservation('x',int(t*N),bid,ask,**kw)

@pytest.mark.xfail(strict=True,reason="ASTRA-010: known queue ahead ignored by conservative model")
def test_trade_at_quote_does_not_jump_known_queue():
    quote=m.build_quote(obs(0),m.default_policies()[0])
    event=obs(1,trade_price=quote.bid,trade_size=1,aggressor_side=m.Side.SELL,queue_ahead_bid=100)
    assert not m.ConservativeTradeFillModel().fills(quote,event)

@pytest.mark.xfail(strict=True,reason="ASTRA-011: new pending price inherits earlier deadline")
def test_every_quote_revision_receives_declared_latency():
    observations=(obs(0),obs(1,.54,.56),obs(1.09,.59,.61),
        obs(1.10,.59,.61,trade_price=.59,trade_size=1,aggressor_side=m.Side.SELL))
    fills,_=m.replay_market(observations,policy=m.default_policies()[0],
        fill_model=m.ConservativeTradeFillModel(),reaction_delay_ms=100)
    assert all(f.fill.timestamp_ns-f.quote_timestamp_ns >= 100_000_000 for f in fills)

@pytest.mark.xfail(strict=True,reason="ASTRA-012: 1-second markout uses hour-later FV")
def test_missing_horizon_cannot_jump_one_hour_forward():
    observations=(obs(0,external_fv=.5),obs(3600,external_fv=.9))
    assert m._future_fv(observations,[o.timestamp_ns for o in observations],0,1) is None

@pytest.mark.xfail(strict=True,reason="ASTRA-007: preloaded MM 005F bins include future events")
def test_preloaded_005f_adapter_excludes_future_bin():
    adapter=m.Frozen005FTransferAdapter(scope_id='x',grid_origin_ns=0)
    for t,bid in [(0,.4),(10,.42),(19,.44)]:
        adapter.observe(timestamp_ns=t*N,best_bid=bid,best_ask=.6)
    v=adapter.features(query_timestamp_ns=15*N)
    assert v is not None
    assert v['genuine_15']==1 # 10s change only; 19s change is not available.
