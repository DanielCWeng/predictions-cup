from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts/research"))
from datetime import datetime, timezone
import json
import pytest
import israel_terminal_depth as depth
import israel_terminal_analysis as analysis


TOKEN = "test-asset"


def test_terminal_windows_and_interval():
    t=depth.CLOSE
    assert depth.terminal_window(t-21601)=="BASELINE"
    assert depth.terminal_window(t-21600)=="FINAL_6H"
    assert depth.terminal_window(t-3600)=="FINAL_1H"
    assert depth.terminal_window(t-900)=="FINAL_15M"
    assert depth.terminal_window(t+1)=="AFTER_CLOSE"
    assert depth.sample_interval(t-900)==1


def test_depth_ladder_vwap_and_removal_is_not_a_confirmed_cancel(monkeypatch):
    rows=[]
    monkeypatch.setattr(depth,"write_record",lambda r:rows.append(r))
    clock=depth.CLOSE-300
    m=depth.DepthMonitor({TOKEN})
    m.on_book(TOKEN,{"bids":[{"price":"0.45","size":"100"},{"price":"0.44","size":"1000"}],
                     "asks":[{"price":"0.46","size":"100"},{"price":"0.47","size":"1000"}]})
    m.on_delta(TOKEN,{"side":"SELL","price":"0.46","size":"50"})
    m.on_delta(TOKEN,{"side":"BUY","price":"0.45","size":"140"})
    m.on_trade(TOKEN,{"price":"0.46","size":"25"})
    m.flush(now_epoch=clock,force=True)
    r=rows[-1]
    assert r["buy_sweep"]["50"]["vwap"]==0.46
    assert r["buy_sweep"]["250"]["vwap"]==0.468
    assert r["sell_sweep"]["50"]["vwap"]==0.45
    assert r["counts_since_previous"]["display_removed_qty"]==50
    assert r["counts_since_previous"]["display_added_qty"]==40
    assert r["counts_since_previous"]["trade_shares"]==25
    assert r["cancellation_count_confirmed"] is None


def test_no_liquidity_no_executable_sweep():
    x=depth.sweep([(0.7,10),(0.8,15)],50)
    assert x["vwap"] is None


def test_depth_deltas_cannot_create_valid_book_from_nothing(monkeypatch):
    m=depth.DepthMonitor({TOKEN})
    m.on_delta(TOKEN,{"side":"BUY","price":"0.2","size":"50"})
    assert not m.books
    m.on_book(TOKEN,{"bids":[{"price":"0.6","size":"100"}],
                     "asks":[{"price":"0.4","size":"100"}]})
    assert not m.books


def test_roundtrip_includes_sweeps_fees_and_haircut():
    ms=120000
    entries=[
        {"received_epoch_ms":ms+1000,"book_age_ms":500,
         "buy_sweep":{"50":{"vwap":0.6}},"sell_sweep":{"50":{"vwap":0.59}}},
        {"received_epoch_ms":ms+62000,"book_age_ms":500,
         "buy_sweep":{"50":{"vwap":0.66}},"sell_sweep":{"50":{"vwap":0.65}}},
    ]
    a=analysis.roundtrip(ms,"UP",50,entries,latency_s=1,hold_s=60,
                         fee_bps=50,extra_haircut_cents=0.005)
    assert a is not None
    assert a["net_pnl_susq_like"]==pytest.approx(50*(.65-.6-.005*(.6+.65)-.005))


def test_roundtrip_rejects_stale_book_or_missing_depth():
    data=[
        {"received_epoch_ms":101000,"book_age_ms":7000,
         "buy_sweep":{"50":{"vwap":0.5}},"sell_sweep":{"50":{"vwap":0.49}}},
        {"received_epoch_ms":161000,"book_age_ms":10,
         "buy_sweep":{"50":{"vwap":0.6}},"sell_sweep":{"50":{"vwap":0.59}}},
    ]
    assert analysis.roundtrip(100000,"UP",50,data,latency_s=1,hold_s=60,
                             fee_bps=50,extra_haircut_cents=0.005) is None


def test_verified_result_lag_needs_independent_evidence(tmp_path):
    path=tmp_path/"verified.jsonl"
    path.write_text(json.dumps({"kind":"verified_results_event",
     "source_url":"https://example.com/official","asset_id":TOKEN,
     "published_epoch_ms":100000,"received_epoch_ms":101000,
     "independent_evidence":False})+"\n")
    r=analysis.information_lag([],[],path)
    assert r["verified_external_events"]==0
    assert r["google_news_rss_syndication_delay_ms"]["n"]==0


def test_windows_preclose_empty_is_explicit_not_filled():
    r=analysis.report([],[],verified_path=depth.ROOT/"nonexistent-verified-tape.jsonl")
    assert r["terminal_windows"]["FINAL_6H"]["captured_depth_samples"]==0
    assert r["terminal_windows"]["FINAL_15M"]["confirmed_cancel_count"] is None
    assert r["replay_roundtrip_scenarios_evaluated"]==0
