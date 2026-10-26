#!/usr/bin/env python3
"""Read-only terminal microstructure, executable replay and information-lag report.

No live orders; no external probability estimate; no claim of achieved P&L.
Only cached Polymarket L2 samples and independently logged research evidence.
"""
from __future__ import annotations
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import json
from pathlib import Path
import statistics

from israel_terminal_depth import CLOSE

ROOT = Path(__file__).resolve().parent
TAPE = ROOT / "israel_rehearsal_events.jsonl"
DEPTH = ROOT / "israel_rehearsal_depth.jsonl"
VERIFIED = ROOT / "israel_verified_results_manual.jsonl"
END_EPOCH = datetime.fromisoformat("2026-10-28T08:00:00+00:00").timestamp()
WINDOWS = {
    "EARLY_BASELINE": (datetime.fromisoformat("2026-10-26T00:00:00+00:00").timestamp(), CLOSE - 21600),
    "FINAL_6H": (CLOSE - 21600, CLOSE),
    "FINAL_1H": (CLOSE - 3600, CLOSE),
    "FINAL_15M": (CLOSE - 900, CLOSE),
    "AFTER_CLOSE_FIRST_2H": (CLOSE, CLOSE + 7200),
}
SIZES = (50, 250, 1000)


def read_tape(path: Path) -> list[dict]:
    items = []
    for p in (path.with_suffix(".jsonl.1"), path):
        if not p.exists():
            continue
        with p.open(encoding="utf-8") as f:
            for line in f:
                try:
                    obj = json.loads(line)
                    if isinstance(obj, dict):
                        items.append(obj)
                except (ValueError, TypeError):
                    continue
    items.sort(key=lambda r: r.get("received_epoch_ms", 0))
    return items


def quantiles(nums: list[float]) -> dict:
    if not nums:
        return {"n": 0, "p50": None, "p95": None, "maximum": None}
    numbers = sorted(nums)
    return {
        "n": len(numbers), "p50": round(numbers[len(numbers)//2], 5),
        "p95": round(numbers[int((len(numbers)-1)*.95)], 5),
        "maximum": round(numbers[-1], 5),
    }


def nearest_after(samples: list[dict], t_ms: int, tolerance_ms: int = 2000):
    for row in samples:
        if row["received_epoch_ms"] >= t_ms:
            return row if row["received_epoch_ms"] - t_ms <= tolerance_ms else None
    return None


def roundtrip(
    signal_ms: int, direction: str, qty: int, samples: list[dict],
    *, latency_s: int, hold_s: int, fee_bps: float, extra_haircut_cents: float,
):
    """Hypothetical 2-leg liquidity-taking, not a realised or executable SIG fill."""
    entry = nearest_after(samples, signal_ms + latency_s * 1000)
    if entry is None or entry.get("book_age_ms", 10**9) > 5000:
        return None
    exit_ = nearest_after(samples, entry["received_epoch_ms"] + hold_s * 1000)
    if exit_ is None or exit_.get("book_age_ms", 10**9) > 5000:
        return None
    k = str(qty)
    if direction == "UP":
        entry_quote = entry["buy_sweep"][k]["vwap"]
        exit_quote = exit_["sell_sweep"][k]["vwap"]
    else:
        yes_sell = entry["sell_sweep"][k]["vwap"]
        yes_buy = exit_["buy_sweep"][k]["vwap"]
        entry_quote = None if yes_sell is None else 1.0 - yes_sell
        exit_quote = None if yes_buy is None else 1.0 - yes_buy
    if entry_quote is None or exit_quote is None:
        return None
    fees = fee_bps * 1e-4 * (entry_quote + exit_quote)
    profit = qty * (exit_quote - entry_quote - fees - extra_haircut_cents)
    return {
        "entry_price": round(entry_quote, 5),
        "exit_price": round(exit_quote, 5),
        "net_pnl_usdc_model": round(profit, 5),
        "gross_capture_cents_per_share": round((exit_quote-entry_quote)*100, 4),
        "entry_received_ms": entry["received_epoch_ms"],
        "exit_received_ms": exit_["received_epoch_ms"],
        "effective_entry_delay_s": round((entry["received_epoch_ms"] - signal_ms) / 1000, 3),
        "entry_snapshot_sample_delay_s": round((entry["received_epoch_ms"] - signal_ms - latency_s * 1000) / 1000, 3),
        "quantity": qty, "latency_s": latency_s, "hold_s": hold_s,
        "fee_bps_each_leg": fee_bps,
        "haircut_cents_per_share": extra_haircut_cents*100,
        "model": "Polymarket theoretical depth replay; no submitted order or SIG price",
    }


def window_stats(name, bounds, depth_rows, market_rows):
    start, end = bounds
    ts = [r for r in depth_rows if start <= r.get("received_epoch_ms", 0)/1000 < end]
    spread = [r["spread"] for r in ts if r.get("spread") is not None]
    top_bid = [r["best_bid_size"] for r in ts if r.get("best_bid_size") is not None]
    top_ask = [r["best_ask_size"] for r in ts if r.get("best_ask_size") is not None]
    age = [r["book_age_ms"] for r in ts if r.get("book_age_ms") is not None]
    removed = sum(r.get("counts_since_previous",{}).get("display_removed_qty",0) for r in ts)
    added = sum(r.get("counts_since_previous",{}).get("display_added_qty",0) for r in ts)
    trades = sum(r.get("counts_since_previous",{}).get("trade_shares",0) for r in ts)
    quote_by_token = defaultdict(list)
    for r in market_rows:
        if r.get("kind") == "market_quote" and start <= r.get("received_epoch_ms",0)/1000 < end:
            quote_by_token[r["asset_id"]].append(r)
    returns = {}
    for token, rows in quote_by_token.items():
        mids = [r["midpoint"] for r in rows]
        returns[token] = {
            "first_mid": mids[0], "last_mid": mids[-1],
            "abs_change_pp": round(abs(mids[-1]-mids[0])*100,3),
            "range_pp": round((max(mids)-min(mids))*100,3),
            "observations": len(mids),
        }
    return {
        "window": name, "start_utc": datetime.fromtimestamp(start, timezone.utc).isoformat(),
        "end_utc": datetime.fromtimestamp(end, timezone.utc).isoformat(),
        "captured_depth_samples": len(ts),
        "captured_assets": len(set(r["asset_id"] for r in ts)),
        "spread_probability_points": quantiles([v*100 for v in spread]),
        "best_bid_shares": quantiles(top_bid),
        "best_ask_shares": quantiles(top_ask),
        "book_age_ms": quantiles(age),
        "observed_net_displayed_qty_removed": round(removed,2),
        "observed_net_displayed_qty_added": round(added,2),
        "observed_trade_shares": round(trades,2),
        "confirmed_cancel_count": None,
        "cancel_note": "No individual order cancellation identification in public L2; net removal also includes executions/replaces",
        "asset_quote_changes": returns,
    }


def information_lag(market_rows, depth_rows, verified_path: Path):
    verified = read_tape(verified_path)
    results = []
    quotes_by_token = defaultdict(list)
    for row in depth_rows:
        quotes_by_token[row.get("asset_id")].append(row)
    for item in verified:
        if item.get("kind") != "verified_results_event" or not (
            item.get("independent_evidence") is True and item.get("source_url")
        ):
            continue
        token = item.get("asset_id")
        published = item.get("published_epoch_ms")
        received = item.get("received_epoch_ms")
        if not (isinstance(published,int) and isinstance(received,int) and published<=received):
            continue
        pre = [row for row in quotes_by_token.get(token, [])
               if published - 60000 <= row["received_epoch_ms"] < published]
        later = [row for row in quotes_by_token.get(token, [])
                 if published <= row["received_epoch_ms"] <= published + 1800000]
        if not pre or not later:
            results.append({"asset_id":token,"source_url":item["source_url"],
                            "reason":"insufficient pre/post market depth samples"})
            continue
        baseline = (pre[-1]["bid"]+pre[-1]["ask"])/2
        threshold = float(item.get("move_threshold_pp", 2))/100
        first = next((row for row in later
                      if abs((row["bid"]+row["ask"])/2 - baseline)>=threshold),None)
        results.append({
            "asset_id":token,"source_url":item["source_url"],
            "event_description":item.get("description"),
            "source_published_ms":published,"verified_first_received_ms":received,
            "source_to_observer_ms":received-published,
            "market_first_threshold_ms":None if first is None else first["received_epoch_ms"],
            "market_delay_from_publication_ms":None if first is None else first["received_epoch_ms"]-published,
            "warning":"Market move after report does not establish causation; polling statements are not certified results",
        })
    rss_lags=[]
    for row in market_rows:
        if row.get("kind") != "headline_discovered":
            continue
        try:
            t=parsedate_to_datetime(row["reported_publish_time"])
            pub=int(t.timestamp()*1000)
            lag=row["received_epoch_ms"]-pub
            if -2000<=lag<86_400_000:
                rss_lags.append(lag)
        except (KeyError,TypeError,ValueError,OverflowError):
            pass
    return {
        "verified_external_events": len(results),
        "verified_event_lags": results,
        "google_news_rss_syndication_delay_ms":quantiles(rss_lags),
        "critical_limitation":"RSS first sighting and syndicated pubDate are NOT official-results latency. No authenticated verified source feed exists unless manually recorded.",
    }


def report(depth_rows, market_rows, verified_path=VERIFIED, *, fee_bps=50.0,
           haircut_cents=0.5):
    by_asset=defaultdict(list)
    for row in depth_rows:
        if row.get("kind")=="depth_sample":
            by_asset[row["asset_id"]].append(row)
    triggers=[x for x in market_rows if x.get("kind")=="price_acceleration_observed"]
    calculations=[]
    for row in triggers:
        token=row["asset_id"]
        direction="UP" if (row.get("move_60s") or row.get("move_300s") or 0)>0 else "DOWN"
        for qty in SIZES:
            for latency in (1,5,30):
                for hold in (60,300):
                    sim=roundtrip(row["received_epoch_ms"],direction,qty,
                                   by_asset[token],latency_s=latency,hold_s=hold,
                                   fee_bps=fee_bps,
                                   extra_haircut_cents=haircut_cents/100)
                    if sim:
                        calculations.append({"asset_id":token,
                                            "trigger_received_ms":row["received_epoch_ms"],
                                            "direction":direction,**sim})
    return {
        "asof_utc":datetime.now(timezone.utc).isoformat(),
        "markets":"Polymarket research, not SIG Cup contracts",
        "terminal_windows":{
            name:window_stats(name,bounds,depth_rows,market_rows)
            for name,bounds in WINDOWS.items()
        },
        "observed_price_acceleration_events":len(triggers),
        "replay_roundtrip_scenarios_evaluated":len(calculations),
        "replay_roundtrip_scenarios":calculations[:120],
        "economics_assumptions":{
            "fee_bps_per_leg_assumed":fee_bps,
            "actual_fee_metadata_verified":False,
            "execution_uncertainty_haircut_cents_per_share":haircut_cents,
            "latency_senarios_seconds":[1,5,30],
            "holding_period_seconds":[60,300],
            "size_shares":list(SIZES),
            "note":"Hindsight simulation in Polymarket USDC using sampled displayed depth. No queue priority, fill guarantee, actual fee confirmation, SIG fair value, or genuine alpha. No trades placed.",
        },
        "information_lag":information_lag(market_rows,depth_rows,verified_path),
    }


if __name__=="__main__":
    p=argparse.ArgumentParser()
    p.add_argument("--fee-bps",type=float,default=50.0)
    p.add_argument("--haircut-cents",type=float,default=0.5)
    p.add_argument("--output",type=Path,default=ROOT/"israel_terminal_report.json")
    p.add_argument("--depth",type=Path,default=DEPTH)
    p.add_argument("--market",type=Path,default=TAPE)
    p.add_argument("--verified",type=Path,default=VERIFIED)
    args=p.parse_args()
    result=report(read_tape(args.depth),read_tape(args.market),args.verified,
                  fee_bps=args.fee_bps,haircut_cents=args.haircut_cents)
    args.output.write_text(json.dumps(result,indent=2))
    print(json.dumps({
        "output":str(args.output),
        "windows":{name:r["captured_depth_samples"] for name,r in result["terminal_windows"].items()},
        "price_alerts":result["observed_price_acceleration_events"],
        "replay_scenarios":result["replay_roundtrip_scenarios_evaluated"],
        "verified_event_lags":result["information_lag"]["verified_external_events"],
    },indent=2))
