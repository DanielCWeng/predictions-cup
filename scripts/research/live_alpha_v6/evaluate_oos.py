#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import gzip
import json
import math
import random
import re
import statistics
from collections import Counter, defaultdict
from pathlib import Path

EPS = 1e-12
NUMERIC_EXCLUDE = {"exchange_id","sig_market_id","title","token_id","executed_at","taker_side","at"}


def num(x):
    if x in (None, "", "None", "nan", "NaN"):
        return None
    try:
        v = float(x)
        return v if math.isfinite(v) else None
    except Exception:
        return None


def load_csv_gz(path: Path):
    rows = []
    with gzip.open(path, "rt", newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            for k in list(r):
                if k not in NUMERIC_EXCLUDE:
                    v = num(r[k])
                    if v is not None:
                        r[k] = v
            rows.append(r)
    return rows


def epoch(s):
    from datetime import datetime
    return datetime.fromisoformat(s.replace("Z","+00:00")).timestamp()


def floor_tick(px, tick):
    return math.floor(px/tick + 1e-9)*tick


def ceil_tick(px, tick):
    return math.ceil(px/tick - 1e-9)*tick


def mean(xs):
    xs = [x for x in xs if x is not None]
    return statistics.fmean(xs) if xs else None


def median(xs):
    xs = [x for x in xs if x is not None]
    return statistics.median(xs) if xs else None


def trimmed_mean(xs, frac=.10):
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return None
    k = int(len(xs)*frac)
    ys = xs[k:len(xs)-k] if len(xs)-2*k > 0 else xs
    return statistics.fmean(ys)


def pctile(xs, p):
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return None
    i = min(len(xs)-1, max(0, int(math.ceil(p*len(xs))-1)))
    return xs[i]


def cluster_bootstrap_lb(events, field, cluster_field, draws=5000, seed=20261002):
    groups = defaultdict(list)
    for e in events:
        v = e.get(field)
        if v is not None:
            groups[str(e[cluster_field])].append(v)
    keys = list(groups)
    if len(keys) < 5:
        return None
    rng = random.Random(seed)
    vals = []
    for _ in range(draws):
        sample = [rng.choice(keys) for _ in keys]
        x = [v for k in sample for v in groups[k]]
        vals.append(statistics.fmean(x))
    return pctile(vals, .025)


def leave_one_out_worst(events, field, cluster_field):
    groups = defaultdict(list)
    for e in events:
        if e.get(field) is not None:
            groups[str(e[cluster_field])].append(e[field])
    if len(groups) < 2:
        return None
    all_events = [(k,v) for k,vs in groups.items() for v in vs]
    vals = []
    for omit in groups:
        x = [v for k,v in all_events if k != omit]
        if x:
            vals.append(statistics.fmean(x))
    return min(vals) if vals else None


def concentration(events, cluster_field):
    if not events:
        return {"max_share":None,"hhi":None}
    c = Counter(str(e[cluster_field]) for e in events)
    n = sum(c.values())
    shares = [v/n for v in c.values()]
    return {"max_share":max(shares),"hhi":sum(x*x for x in shares)}


def positive_cluster_fraction(events, field, cluster_field):
    g = defaultdict(list)
    for e in events:
        if e.get(field) is not None:
            g[str(e[cluster_field])].append(e[field])
    means = [statistics.fmean(v) for v in g.values() if v]
    return (sum(x>0 for x in means)/len(means)) if means else None


def decluster(events, seconds=300, cluster=("exchange_id","direction")):
    out, last = [], {}
    for e in sorted(events, key=lambda x:x["ts"]):
        key = tuple(str(e.get(k,"")) for k in cluster)
        if e["ts"] - last.get(key, -1e30) < seconds:
            continue
        out.append(e)
        last[key] = e["ts"]
    return out


def summarize(events, field="m60", cluster_field="exchange_id", draws=5000):
    xs = [e.get(field) for e in events if e.get(field) is not None]
    c = concentration(events, cluster_field)
    return {
        "n":len(events),
        "n_scored":len(xs),
        "clusters":len({str(e[cluster_field]) for e in events}),
        "mean_c":100*mean(xs) if xs else None,
        "median_c":100*median(xs) if xs else None,
        "trimmed_mean_c":100*trimmed_mean(xs) if xs else None,
        "cluster_bootstrap_lo_c":100*cluster_bootstrap_lb(events,field,cluster_field,draws) if len({str(e[cluster_field]) for e in events})>=5 else None,
        "positive_cluster_fraction":positive_cluster_fraction(events,field,cluster_field),
        "leave_one_cluster_out_worst_c":100*leave_one_out_worst(events,field,cluster_field) if len({str(e[cluster_field]) for e in events})>=2 else None,
        "max_cluster_share":c["max_share"],
        "hhi":c["hhi"],
    }


def eligible_pm(r, max_spread, min_depth, max_age=65):
    return (
        r.get("pm_mid") is not None and r.get("pm_spread") is not None and r["pm_spread"] <= max_spread+EPS
        and r.get("pm_age_s") is not None and r["pm_age_s"] <= max_age
        and r.get("pm_bid1_size") is not None and r["pm_bid1_size"] >= min_depth
        and r.get("pm_ask1_size") is not None and r["pm_ask1_size"] >= min_depth
    )


def maker_events(trades, cfg, strict=False, quarantine=frozenset()):
    c = cfg["frozen_primary_rules"]["maker"]
    off, tick = c["pm_mid_offset_c"]/100, c["tick_c"]/100
    max_pm, min_depth = c["pm_max_spread_c"]/100, c["pm_min_top_size"]
    min_excess = c["sig_min_excess_spread_c"]/100
    out = []
    for r in trades:
        ex = str(r["exchange_id"])
        if ex in quarantine or r.get("taker_side") not in ("BUY","SELL"):
            continue
        if not eligible_pm(r,max_pm,min_depth,c["pm_max_age_s"]):
            continue
        if r.get("sig_bbo_age_s") is None or r["sig_bbo_age_s"] > c["sig_bbo_max_age_s"]:
            continue
        if None in (r.get("sig_bid"),r.get("sig_ask"),r.get("sig_spread"),r.get("price")):
            continue
        if r["pm_mid"] < r["sig_bid"] or r["pm_mid"] > r["sig_ask"]:
            continue
        if r["sig_spread"]-r["pm_spread"] < min_excess-EPS:
            continue
        if r["taker_side"] == "BUY":
            quote = ceil_tick(r["pm_mid"]+off,tick)
            if not (r["sig_bid"] < quote <= r["sig_ask"]+EPS):
                continue
            threshold = quote + (tick if strict else 0)
            if r["price"] < threshold-EPS:
                continue
            sign, direction = -1.0, "SELL_MAKER"
        else:
            quote = floor_tick(r["pm_mid"]-off,tick)
            if not (r["sig_bid"]-EPS <= quote < r["sig_ask"]):
                continue
            threshold = quote - (tick if strict else 0)
            if r["price"] > threshold+EPS:
                continue
            sign, direction = 1.0, "BUY_MAKER"
        e = {"exchange_id":ex,"direction":direction,"ts":epoch(r["executed_at"]),"entry":quote,"quantity":r.get("quantity")}
        for h in (1,5,15,30,60,300,1800):
            p = r.get(f"pm_mid_{h}s")
            e[f"m{h}"] = sign*(p-quote) if p is not None else None
            if sign > 0:
                bid = r.get(f"sig_bid_{h}s")
                e[f"recycle{h}"] = bid-quote if bid is not None else None
            else:
                ask = r.get(f"sig_ask_{h}s")
                e[f"recycle{h}"] = quote-ask if ask is not None else None
        e["pre_move_against"] = r.get("pre_move_against_maker")
        out.append(e)
    return out


def residual_events(states, cfg, threshold_c=None, quarantine=frozenset()):
    c = cfg["frozen_primary_rules"]["residual_taker"]
    threshold = (threshold_c if threshold_c is not None else c["threshold_c"])/100
    max_pm, min_depth = c["pm_max_spread_c"]/100, c["pm_min_top_size"]
    cooldown = c["cooldown_s"]
    out, last = [], {}
    for r in sorted(states,key=lambda x:x["at"]):
        ex = str(r["exchange_id"])
        if ex in quarantine or not eligible_pm(r,max_pm,min_depth,c["pm_max_age_s"]):
            continue
        if r.get("sig_age_s") is None or r["sig_age_s"] > c["sig_bbo_max_age_s"]:
            continue
        be,se = r.get("buy_edge"),r.get("sell_edge")
        if be is None or se is None:
            continue
        if max(be,se) < threshold-EPS:
            continue
        if be >= se:
            direction, entry, sign = "BUY_SIG", r.get("sig_ask"), 1.0
        else:
            direction, entry, sign = "SELL_SIG", r.get("sig_bid"), -1.0
        if entry is None:
            continue
        t = epoch(r["at"]); key=(ex,direction)
        if t-last.get(key,-1e30) < cooldown:
            continue
        last[key]=t
        e={"exchange_id":ex,"direction":direction,"ts":t,"entry":entry,"edge":max(be,se)}
        for h in (60,300,1800):
            p=r.get(f"pm_mid_{h}s")
            e[f"m{h}"]=sign*(p-entry) if p is not None else None
            if sign>0:
                b=r.get(f"sig_bid_{h}s"); e[f"recycle{h}"]=b-entry if b is not None else None
            else:
                a=r.get(f"sig_ask_{h}s"); e[f"recycle{h}"]=entry-a if a is not None else None
        out.append(e)
    return out


PAIR_RE = re.compile(r"^Will the (Democratic|Republican) Party win the (.+)\?$")


def structural_events(states, cfg, quarantine=frozenset()):
    c = cfg["frozen_primary_rules"]["structural_pair"]
    by_key = defaultdict(dict)
    for r in states:
        if str(r["exchange_id"]) in quarantine:
            continue
        m = PAIR_RE.match(str(r.get("title","")))
        if not m:
            continue
        by_key[(r["at"],m.group(2))][m.group(1)] = r
    out,last=[],{}
    threshold=c["threshold_c"]/100
    pair_tol=c["pm_pair_distance_from_one_max_c"]/100
    max_spread=c["pm_max_leg_spread_c"]/100
    min_depth=c["pm_min_top_size_each_leg"]
    for (at,race),legs in sorted(by_key.items()):
        if "Democratic" not in legs or "Republican" not in legs:
            continue
        d,r=legs["Democratic"],legs["Republican"]
        if not eligible_pm(d,max_spread,min_depth) or not eligible_pm(r,max_spread,min_depth):
            continue
        vals=(d.get("pm_mid"),r.get("pm_mid"),d.get("sig_ask"),r.get("sig_ask"),d.get("sig_bid"),r.get("sig_bid"))
        if any(x is None for x in vals):
            continue
        pm_pair=d["pm_mid"]+r["pm_mid"]
        if abs(pm_pair-1)>pair_tol+EPS:
            continue
        ask_pair=d["sig_ask"]+r["sig_ask"]; bid_pair=d["sig_bid"]+r["sig_bid"]
        buy=pm_pair-ask_pair; sell=bid_pair-pm_pair
        if max(buy,sell)<threshold-EPS:
            continue
        if buy>=sell:
            direction,entry,sign="BUY_PAIR",ask_pair,1.0
        else:
            direction,entry,sign="SELL_PAIR",bid_pair,-1.0
        t=epoch(at); key=(race,direction)
        if t-last.get(key,-1e30)<c["cooldown_s"]:
            continue
        last[key]=t
        e={"exchange_id":race,"race":race,"direction":direction,"ts":t,"entry":entry,"edge":max(buy,sell)}
        for h in (60,300,1800):
            pd,pr=d.get(f"pm_mid_{h}s"),r.get(f"pm_mid_{h}s")
            if pd is not None and pr is not None:
                e[f"m{h}"]=sign*((pd+pr)-entry)
            else:e[f"m{h}"]=None
            if sign>0:
                bd,br=d.get(f"sig_bid_{h}s"),r.get(f"sig_bid_{h}s")
                e[f"recycle{h}"]=(bd+br)-entry if bd is not None and br is not None else None
            else:
                ad,ar=d.get(f"sig_ask_{h}s"),r.get(f"sig_ask_{h}s")
                e[f"recycle{h}"]=entry-(ad+ar) if ad is not None and ar is not None else None
        out.append(e)
    return out


def reversal_score(states,cfg):
    thr=cfg["frozen_primary_rules"]["pm_five_minute_reversal"]["abs_pm_move_5m_min_c"]/100
    vals=[]
    for r in states:
        move=r.get("pm_ret5m"); fut=r.get("pm_mid_300s"); now=r.get("pm_mid")
        if move is None or fut is None or now is None or abs(move)<thr:
            continue
        vals.append((-1 if move>0 else 1)*(fut-now))
    return {
        "n":len(vals),"mean_c":100*mean(vals) if vals else None,
        "median_c":100*median(vals) if vals else None,
        "positive_fraction":sum(x>0 for x in vals)/len(vals) if vals else None
    }


def recycle_stats(events,h):
    vals=[e.get(f"recycle{h}") for e in events if e.get(f"recycle{h}") is not None]
    return {
        "n":len(vals),"mean_c":100*mean(vals) if vals else None,
        "nonnegative_fraction":sum(x>=0 for x in vals)/len(vals) if vals else None,
        "positive_fraction":sum(x>0 for x in vals)/len(vals) if vals else None
    }


def markout_curve(events):
    out={}
    for h in (1,5,15,30,60,300,1800):
        key=f"m{h}"; vals=[e.get(key) for e in events if e.get(key) is not None]
        if vals:
            out[str(h)]={"n":len(vals),"mean_c":100*mean(vals),"median_c":100*median(vals)}
    return out


def cost_stress(events,field,costs):
    base=[e.get(field) for e in events if e.get(field) is not None]
    return {str(c):100*mean([x-c/100 for x in base]) if base else None for c in costs}


def label_maker(events,cfg):
    g=cfg["promotion_gates"]["maker"]; s=summarize(events)
    if not events:return "FAILS_OOS"
    ok=(s["n"]>=g["min_oos_fills"] and s["clusters"]>=g["min_markets"]
        and s["max_cluster_share"] is not None and s["max_cluster_share"]<=g["max_market_fill_share"]
        and s["cluster_bootstrap_lo_c"] is not None and s["cluster_bootstrap_lo_c"]>g["cluster_bootstrap_60s_lower_bound_c_gt"]
        and mean([e.get("m300") for e in events]) is not None and 100*mean([e.get("m300") for e in events])>=g["mean_300s_markout_c_ge"]
        and s["positive_cluster_fraction"] is not None and s["positive_cluster_fraction"]>=g["min_positive_market_fraction"])
    return "SURVIVES_OOS" if ok else "PAPER_EXTEND"


def label_residual(events,cfg):
    g=cfg["promotion_gates"]["residual_taker"]; s=summarize(events)
    if not events:return "FAILS_OOS"
    cost=mean([e.get("m60") for e in events])-g["must_survive_cost_c"]/100 if mean([e.get("m60") for e in events]) is not None else None
    ok=(s["n"]>=g["min_oos_opportunities"] and s["clusters"]>=g["min_markets"]
        and s["max_cluster_share"] is not None and s["max_cluster_share"]<=g["max_market_opportunity_share"]
        and s["cluster_bootstrap_lo_c"] is not None and s["cluster_bootstrap_lo_c"]>g["cluster_bootstrap_60s_lower_bound_c_gt"]
        and mean([e.get("m300") for e in events]) is not None and 100*mean([e.get("m300") for e in events])>=g["mean_300s_markout_c_ge"]
        and cost is not None and cost>0)
    return "SURVIVES_OOS" if ok else "PAPER_EXTEND"


def label_struct(events,cfg):
    g=cfg["promotion_gates"]["structural_pair"]; s=summarize(events,cluster_field="race")
    if not events:return "FAILS_OOS"
    ok=(s["n"]>=g["min_oos_opportunities"] and s["clusters"]>=g["min_races"]
        and s["cluster_bootstrap_lo_c"] is not None and s["cluster_bootstrap_lo_c"]>g["cluster_bootstrap_60s_lower_bound_c_gt"]
        and mean([e.get("m300") for e in events]) is not None and 100*mean([e.get("m300") for e in events])>=g["mean_300s_markout_c_ge"])
    return "SURVIVES_OOS" if ok else "PAPER_EXTEND"


def result_block(events,cluster="exchange_id",costs=(0,.0025,.005,.01)):
    episodes=decluster(events,300,cluster=(cluster,"direction"))
    return {
        "raw_count":len(events),"episode_count_5m":len(episodes),
        "raw":summarize(events,cluster_field=cluster),
        "episodes":summarize(episodes,cluster_field=cluster),
        "markout_curve_raw":markout_curve(events),
        "markout_curve_episodes":markout_curve(episodes),
        "recycle_60":recycle_stats(events,60),"recycle_300":recycle_stats(events,300),"recycle_1800":recycle_stats(events,1800),
        "cost_stress_m60_c":cost_stress(episodes,"m60",costs),
    }


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--input",required=True)
    ap.add_argument("--freeze",required=True)
    ap.add_argument("--output",required=True)
    args=ap.parse_args()
    root,out=Path(args.input),Path(args.output); out.mkdir(parents=True,exist_ok=True)
    cfg=json.loads(Path(args.freeze).read_text())
    manifest=json.loads((root/"SNAPSHOT_MANIFEST.json").read_text())
    trades=load_csv_gz(root/"trade_features.csv.gz"); states=load_csv_gz(root/"state_minute.csv.gz")
    quarantine=frozenset(str(x) for x in cfg.get("quarantine_exchange_ids",[]))

    maker_touch=maker_events(trades,cfg,False,quarantine)
    maker_strict=maker_events(trades,cfg,True,quarantine)
    residual=residual_events(states,cfg,None,quarantine)
    structural=structural_events(states,cfg,quarantine)
    reversal=reversal_score(states,cfg)

    result={
        "name":"LIVE-ALPHA-V6-OVERNIGHT","manifest":manifest,
        "frozen_score":{
            "maker_touch":result_block(maker_touch),
            "maker_strict_print_through":result_block(maker_strict),
            "residual_taker":result_block(residual),
            "structural_pair":result_block(structural,cluster="race"),
            "pm_5m_reversal":reversal,
        },
        "labels":{
            "maker_touch":label_maker(decluster(maker_touch,300),cfg),
            "maker_strict_print_through":label_maker(decluster(maker_strict,300),cfg),
            "residual_taker":label_residual(decluster(residual,300),cfg),
            "structural_pair":label_struct(decluster(structural,300,cluster=("race","direction")),cfg),
            "pm_5m_reversal":"CONTEXT_ONLY"
        }
    }

    # Frozen challengers are scored only after primary results and are clearly separated.
    challengers={}
    for thr in cfg["frozen_challengers"]["residual_thresholds_c"]:
        e=residual_events(states,cfg,thr,quarantine)
        challengers[f"residual_{thr:.2f}c"]=result_block(e)
    result["frozen_challengers"]=challengers

    (out/"V6_RESULT.json").write_text(json.dumps(result,indent=2,sort_keys=True)+"\n")
    lines=[
        "# LIVE-ALPHA-V6 — Overnight OOS Score","",
        f"Window: **{manifest['evaluation_start']} → {manifest['evaluation_end']}**.",
        f"Trade features: **{manifest['counts']['trade_features']:,}**. Minute states: **{manifest['counts']['state_minutes']:,}**.","",
        "## Frozen decisions","",
    ]
    for k,v in result["labels"].items():
        lines.append(f"- **{k}: {v}**")
    lines += ["","## Frozen primary metrics",""]
    for name,block in result["frozen_score"].items():
        if name=="pm_5m_reversal":
            lines += [f"### {name}",f"- n={block['n']}; mean={block['mean_c']}c; median={block['median_c']}c; positive={block['positive_fraction']}",""]
            continue
        ep=block["episodes"]
        lines += [
            f"### {name}",
            f"- raw={block['raw_count']}; independent 5m episodes={block['episode_count_5m']}; clusters={ep['clusters']}",
            f"- 60s mean={ep['mean_c']}c; median={ep['median_c']}c; trimmed={ep['trimmed_mean_c']}c",
            f"- market-cluster 95% lower bound={ep['cluster_bootstrap_lo_c']}c; positive-cluster fraction={ep['positive_cluster_fraction']}",
            f"- max cluster share={ep['max_cluster_share']}; HHI={ep['hhi']}; worst leave-one-out={ep['leave_one_cluster_out_worst_c']}c",
            f"- recycle 60/300/1800 nonnegative={block['recycle_60']['nonnegative_fraction']} / {block['recycle_300']['nonnegative_fraction']} / {block['recycle_1800']['nonnegative_fraction']}",
            f"- cost-stress 60s means={block['cost_stress_m60_c']}",""
        ]
    lines += [
        "## Interpretation rules","",
        "- The primary score above is untouched OOS. Challengers do not overwrite it.",
        "- Maker must be read with strict-print-through and recycle evidence, not TOUCH fills alone.",
        "- PM markout is economic fair-value evidence; SIG recycle is monetisation evidence.",
        "- Hazard modelling and exploratory regime discovery run in Kaggle after this frozen score is sealed.",
        "- No result is automatic LIVE approval.",""
    ]
    (out/"V6_REPORT.md").write_text("\n".join(lines),encoding="utf-8")
    print("\n".join(lines[:20]))


if __name__=="__main__":
    main()
