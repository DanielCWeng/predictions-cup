"""Cross-module invariants on pinned main; synthetic events only, no network."""
import asyncio
import importlib.util
import sys
import sqlite3
from pathlib import Path
from datetime import UTC, datetime, timedelta
from dataclasses import replace
import pytest
ROOT=Path(__file__).resolve().parents[2]
def fixture_module(name):
    spec=importlib.util.spec_from_file_location(name,ROOT/'tests'/f'{name}.py')
    mod=importlib.util.module_from_spec(spec);sys.modules[name]=mod;spec.loader.exec_module(mod)
    return mod
l=fixture_module('test_live_learn001')
m=fixture_module('test_make001')

@pytest.mark.xfail(strict=True,reason="ASTRA-009: known realtime fills become supported zero fills")
def test_live_fill_gap_is_not_reported_as_zero_fill_rate(tmp_path):
    t=datetime(2026,10,1,tzinfo=UTC)
    decision=l._decision(l._snapshot(t,now_ns=1_000_000_000),quote_intent={'bid':.49})
    path=tmp_path/'journal.sqlite3'
    l._write_execution_journal(path,decision,(
        ('REALTIME_FILL','f1',10,.49,t+timedelta(seconds=.2),1_200_000_000),))
    evidence=asyncio.run(l.JournalExecutionEvidenceProvider(path).evidence_for(decision,maturity_at=t+timedelta(seconds=1)))
    assert not evidence.supported or evidence.fills

@pytest.mark.xfail(strict=True,reason="ASTRA-009: authoritative fills on cancel operation not joined to placement")
def test_cancel_recovery_fills_join_original_quote(tmp_path):
    t=datetime(2026,10,1,tzinfo=UTC)
    decision=l._decision(l._snapshot(t,now_ns=1_000_000_000),quote_intent={'bid':.49})
    path=tmp_path/'journal.sqlite3'
    l._write_execution_journal(path,decision,(
        ('AUTHORITATIVE_FILL','f1',10,.49,t+timedelta(seconds=.2),1_200_000_000),))
    with sqlite3.connect(path) as c:
        c.execute("UPDATE execution_events SET logical_operation_id='cancel-1' WHERE event_type='AUTHORITATIVE_FILL'")
    evidence=asyncio.run(l.JournalExecutionEvidenceProvider(path).evidence_for(decision,maturity_at=t+timedelta(seconds=1)))
    assert len(evidence.fills)==1

@pytest.mark.xfail(strict=True,reason="ASTRA-013: maker retains quotes when capital marks untrusted")
def test_untrusted_risk_marks_withdraw_existing_quotes():
    engine=m._engine()
    desired=engine.quote(m._maker_snapshot()).desired
    assert desired is not None
    registry=m.QuoteRegistry()
    for side,ticks,size,order_id in [(m.QuoteSide.BID,desired.bid_ticks,desired.bid_size,91),
                                    (m.QuoteSide.ASK,desired.ask_ticks,desired.ask_size,92)]:
        registry.apply_authoritative(exchange_id='36',side=side,price_ticks=ticks,size=size,
            remaining_size=size,logical_operation_id=f'old-{side}',exchange_order_id=order_id,
            lifecycle_state=m.LifecycleState.OPEN,observed_monotonic_ns=m.NOW-1)
    adapter=m.ShadowMakerExecutionAdapter()
    context=m.RiskContext(mode=m.ExecutionMode.SHADOW,kill_switch=False,limits=None,
        max_state_age_ns=100_000_000,capital_state=replace(m._maker_capital(),marks_trusted=False),require_capital_state=True)
    coordinator=m.MakerCoordinator(engine=engine,lifecycle=m.QuoteLifecycleManager(),
        quote_registry=registry,risk_context=context,placement_dispatch=adapter.place,cancel_dispatch=adapter.cancel)
    result=asyncio.run(coordinator.on_state_change(m.MakerStateChange(event_id='bad-risk',
        observed_monotonic_ns=m.NOW,exchange_ids=frozenset({'36'})),{'36':m._maker_snapshot()}))
    assert len(result.execution_events)==2
    assert registry.state('36').bid is None and registry.state('36').ask is None
