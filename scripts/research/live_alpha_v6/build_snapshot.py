#!/usr/bin/env python3
from __future__ import annotations

import argparse
import bisect
import csv
import gzip
import hashlib
import json
import math
import shutil
import sqlite3
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pyarrow.parquet as pq

HORIZONS = (1, 5, 15, 30, 60, 300, 1800)
LOOKBACK_S = 300


def dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def ts(value) -> float:
    if isinstance(value, datetime):
        return value.timestamp()
    return dt(str(value)).timestamp()


def iso(x: float) -> str:
    return datetime.fromtimestamp(x, timezone.utc).isoformat().replace("+00:00", "Z")


def fnum(x):
    if x in (None, ""):
        return None
    try:
        return float(x)
    except Exception:
        return None


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def hourly_files(pm_root: Path, kind: str, start_ts: float, end_ts: float):
    cur = datetime.fromtimestamp(start_ts, timezone.utc).replace(minute=0, second=0, microsecond=0)
    end = datetime.fromtimestamp(end_ts, timezone.utc).replace(minute=0, second=0, microsecond=0)
    files = []
    while cur <= end:
        d = pm_root / kind / f"{cur:%Y/%m/%d/%H}"
        if d.exists():
            files.extend(sorted(d.glob("*.parquet")))
        cur += timedelta(hours=1)
    return files


def asof_index(times, target):
    i = bisect.bisect_right(times, target) - 1
    return i if i >= 0 else None


def bbo_mid(row):
    if row is None:
        return None
    bid, ask = row[1], row[2]
    if bid is None or ask is None or ask < bid:
        return None
    return (bid + ask) / 2.0


def load_mapping(root: Path):
    path = root / "data/mappings/sig_polymarket_2026.csv"
    out = []
    with path.open(newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r.get("mapping_class") == "EXACT" and r.get("mapping_direction") == "SAME" and r.get("polymarket_token_id"):
                out.append(r)
    if not out:
        raise RuntimeError("no EXACT/SAME mappings")
    return out


def load_sig(root: Path, exchanges, start_ts: float, end_ts: float):
    db = root / "data/sig_realtime.sqlite3"
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    q = ",".join("?" for _ in exchanges)
    start_iso, end_iso = iso(start_ts), iso(end_ts)

    bbo = defaultdict(list)
    sql = (
        "select exchange_id,best_bid,best_ask,latest_price,rest_observed_at "
        f"from price_observations where exchange_id in ({q}) "
        "and rest_observed_at>=? and rest_observed_at<=? order by id"
    )
    for ex, bid, ask, last, at in con.execute(sql, [*exchanges, start_iso, end_iso]):
        bbo[str(ex)].append((ts(at), fnum(bid), fnum(ask), fnum(last)))
    for ex in bbo:
        bbo[ex].sort(key=lambda x: x[0])

    trades = defaultdict(list)
    sql = (
        "select exchange_id,price,quantity,executed_at,observed_at "
        f"from realtime_trades where exchange_id in ({q}) "
        "and executed_at>=? and executed_at<=? order by id"
    )
    for ex, px, qty, at, observed in con.execute(sql, [*exchanges, start_iso, end_iso]):
        trades[str(ex)].append((ts(at), fnum(px), fnum(qty), ts(observed)))
    for ex in trades:
        trades[ex].sort(key=lambda x: x[0])
    con.close()
    return dict(bbo), dict(trades)


def sig_at(bbo, times, ex, target):
    xs = bbo.get(ex) or []
    tt = times.get(ex) or []
    i = asof_index(tt, target)
    return xs[i] if i is not None else None


def build_queries(mapping, trades, eval_start, eval_end):
    minute0 = math.ceil(eval_start / 60.0) * 60.0
    state_times = []
    t = minute0
    while t <= eval_end:
        state_times.append(t)
        t += 60.0

    by_token = defaultdict(set)
    depth_by_token = defaultdict(set)
    for r in mapping:
        token = r["polymarket_token_id"]
        ex = str(r["sig_exchange_id"])
        for t in state_times:
            depth_by_token[token].add(t)
            for off in (-300, 0, 60, 300, 1800):
                by_token[token].add(t + off)
        for tr in trades.get(ex, []):
            t = tr[0]
            if eval_start <= t <= eval_end:
                depth_by_token[token].add(t)
                for off in (-5, 0, 1, 5, 15, 30, 60, 300, 1800):
                    by_token[token].add(t + off)
    return state_times, {k: sorted(v) for k, v in by_token.items()}, {k: sorted(v) for k, v in depth_by_token.items()}


def stream_asof(files, tokens, query_map, kind):
    token_set = set(tokens)
    qidx = {k: 0 for k in query_map}
    last = {}
    out = {k: {} for k in query_map}

    if kind == "obs":
        cols = ["token_id", "source_timestamp", "best_bid", "best_ask", "book_valid"]
    else:
        cols = ["token_id", "source_timestamp", "bids", "asks"]

    for fn in files:
        buckets = defaultdict(list)
        pf = pq.ParquetFile(fn)
        for batch in pf.iter_batches(columns=cols, batch_size=4096):
            for r in batch.to_pylist():
                token = str(r["token_id"])
                if token not in token_set:
                    continue
                at = ts(r["source_timestamp"])
                if kind == "obs":
                    if r.get("book_valid") is not True:
                        continue
                    bid, ask = fnum(r.get("best_bid")), fnum(r.get("best_ask"))
                    if bid is None or ask is None or ask < bid:
                        continue
                    state = (at, bid, ask)
                else:
                    bids = r.get("bids") or []
                    asks = r.get("asks") or []
                    parsed_bids = [(fnum(x.get("price")), fnum(x.get("size"))) for x in bids]
                    parsed_asks = [(fnum(x.get("price")), fnum(x.get("size"))) for x in asks]
                    parsed_bids = [x for x in parsed_bids if x[0] is not None and x[1] is not None]
                    parsed_asks = [x for x in parsed_asks if x[0] is not None and x[1] is not None]
                    if not parsed_bids or not parsed_asks:
                        continue
                    parsed_bids.sort(key=lambda x: x[0], reverse=True)
                    parsed_asks.sort(key=lambda x: x[0])
                    state = (
                        at,
                        parsed_bids[0][0], parsed_asks[0][0],
                        parsed_bids[0][1], parsed_asks[0][1],
                        sum(x[1] for x in parsed_bids[:5]),
                        sum(x[1] for x in parsed_asks[:5]),
                    )
                buckets[token].append(state)

        for token, events in buckets.items():
            events.sort(key=lambda x: x[0])
            qs = query_map.get(token) or []
            idx = qidx.get(token, 0)
            prev = last.get(token)
            for event in events:
                et = event[0]
                while idx < len(qs) and qs[idx] < et:
                    out[token][qs[idx]] = prev
                    idx += 1
                prev = event
            qidx[token] = idx
            last[token] = prev

    for token, qs in query_map.items():
        idx = qidx.get(token, 0)
        prev = last.get(token)
        while idx < len(qs):
            out[token][qs[idx]] = prev
            idx += 1
    return out


def pm_at(store, token, target):
    return (store.get(token) or {}).get(target)


def sig_snapshot(bbo, bbo_times, ex, target):
    r = sig_at(bbo, bbo_times, ex, target)
    if r is None:
        return None
    return {
        "source_ts": r[0], "bid": r[1], "ask": r[2], "last": r[3],
        "mid": bbo_mid(r), "age_s": target-r[0],
        "spread": (r[2]-r[1]) if r[1] is not None and r[2] is not None else None,
    }


def pm_snapshot(obs, depth, token, target):
    r = pm_at(obs, token, target)
    if r is None:
        return None
    d = pm_at(depth, token, target)
    out = {
        "source_ts": r[0], "bid": r[1], "ask": r[2], "mid": (r[1]+r[2])/2,
        "spread": r[2]-r[1], "age_s": target-r[0],
        "bid1_size": None, "ask1_size": None, "bid_depth5": None, "ask_depth5": None,
        "depth_age_s": None,
    }
    if d is not None:
        out.update({
            "bid1_size": d[3], "ask1_size": d[4], "bid_depth5": d[5], "ask_depth5": d[6],
            "depth_age_s": target-d[0],
        })
    return out


def infer_taker_side(px, sig):
    if sig is None or px is None:
        return "UNKNOWN"
    bid, ask = sig["bid"], sig["ask"]
    if ask is not None and px >= ask-1e-9:
        return "BUY"
    if bid is not None and px <= bid+1e-9:
        return "SELL"
    return "UNKNOWN"


def write_gz_csv(path: Path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        with gzip.open(path, "wt", newline="", encoding="utf-8") as f:
            f.write("")
        return
    fields = list(rows[0])
    with gzip.open(path, "wt", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source-root", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--start", required=True, help="Untouched OOS start, ISO UTC")
    ap.add_argument("--end", required=True, help="Last entry timestamp; horizons can extend past this")
    ap.add_argument("--paper-events", default="")
    args = ap.parse_args()

    root, out = Path(args.source_root), Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    eval_start, eval_end = ts(args.start), ts(args.end)
    source_start = eval_start - LOOKBACK_S - 60
    source_end = eval_end + max(HORIZONS) + 60

    mapping = load_mapping(root)
    by_ex = {str(r["sig_exchange_id"]): r for r in mapping}
    exchanges = sorted(by_ex)
    tokens = sorted({r["polymarket_token_id"] for r in mapping})

    bbo, trades = load_sig(root, exchanges, source_start, source_end)
    bbo_times = {k: [x[0] for x in v] for k, v in bbo.items()}

    state_times, pm_queries, depth_queries = build_queries(mapping, trades, eval_start, eval_end)
    pm_root = root / "data/004c_c_crossvenue/polymarket_research"
    obs_files = hourly_files(pm_root, "observations", source_start, source_end)
    depth_files = hourly_files(pm_root, "depth_snapshots", source_start, source_end)
    if not obs_files:
        raise RuntimeError("no PM observation files for V6 window")
    obs = stream_asof(obs_files, tokens, pm_queries, "obs")
    depth = stream_asof(depth_files, tokens, depth_queries, "depth") if depth_files else {t:{} for t in tokens}

    signed_trades = defaultdict(list)
    trade_rows = []
    for ex, xs in trades.items():
        m = by_ex[ex]
        token = m["polymarket_token_id"]
        for t, px, qty, observed in xs:
            if not (eval_start <= t <= eval_end):
                continue
            ss = sig_snapshot(bbo, bbo_times, ex, t)
            ps = pm_snapshot(obs, depth, token, t)
            side = infer_taker_side(px, ss)
            signed = qty if side == "BUY" else (-qty if side == "SELL" else 0.0)
            signed_trades[ex].append((t, signed or 0.0))
            maker_sign = -1.0 if side == "BUY" else (1.0 if side == "SELL" else None)
            row = {
                "exchange_id": ex, "sig_market_id": m.get("sig_market_id"), "title": m.get("sig_market_title"),
                "token_id": token, "executed_at": iso(t), "price": px, "quantity": qty,
                "taker_side": side, "maker_sign": maker_sign,
                "sig_bid": ss["bid"] if ss else None, "sig_ask": ss["ask"] if ss else None,
                "sig_mid": ss["mid"] if ss else None, "sig_spread": ss["spread"] if ss else None,
                "sig_bbo_age_s": ss["age_s"] if ss else None,
                "pm_bid": ps["bid"] if ps else None, "pm_ask": ps["ask"] if ps else None,
                "pm_mid": ps["mid"] if ps else None, "pm_spread": ps["spread"] if ps else None,
                "pm_age_s": ps["age_s"] if ps else None,
                "pm_bid1_size": ps["bid1_size"] if ps else None, "pm_ask1_size": ps["ask1_size"] if ps else None,
                "pm_bid_depth5": ps["bid_depth5"] if ps else None, "pm_ask_depth5": ps["ask_depth5"] if ps else None,
                "pm_depth_age_s": ps["depth_age_s"] if ps else None,
            }
            if maker_sign is not None and ps and px is not None:
                row["maker_edge"] = maker_sign * (ps["mid"] - px)
                pre = pm_snapshot(obs, depth, token, t-5)
                row["pre_move_against_maker"] = (-maker_sign*(ps["mid"]-pre["mid"])) if pre else None
            else:
                row["maker_edge"] = None
                row["pre_move_against_maker"] = None

            for h in HORIZONS:
                p = pm_snapshot(obs, depth, token, t+h)
                s = sig_snapshot(bbo, bbo_times, ex, t+h)
                row[f"pm_mid_{h}s"] = p["mid"] if p else None
                row[f"sig_bid_{h}s"] = s["bid"] if s else None
                row[f"sig_ask_{h}s"] = s["ask"] if s else None
                row[f"sig_mid_{h}s"] = s["mid"] if s else None
                row[f"maker_m{h}"] = (
                    maker_sign*(p["mid"]-px) if maker_sign is not None and p and px is not None else None
                )
                if maker_sign == 1.0 and s and px is not None:
                    row[f"maker_recycle_{h}"] = s["bid"]-px if s["bid"] is not None else None
                elif maker_sign == -1.0 and s and px is not None:
                    row[f"maker_recycle_{h}"] = px-s["ask"] if s["ask"] is not None else None
                else:
                    row[f"maker_recycle_{h}"] = None
            trade_rows.append(row)

    signed_times = {k:[x[0] for x in v] for k,v in signed_trades.items()}
    state_rows = []
    for m in mapping:
        ex, token = str(m["sig_exchange_id"]), m["polymarket_token_id"]
        flow = signed_trades.get(ex) or []
        ft = signed_times.get(ex) or []
        for t in state_times:
            ss, ps = sig_snapshot(bbo, bbo_times, ex, t), pm_snapshot(obs, depth, token, t)
            if ss is None or ps is None:
                continue
            i0, i1 = bisect.bisect_left(ft, t-60), bisect.bisect_right(ft, t)
            prior = flow[i0:i1]
            row = {
                "exchange_id": ex, "sig_market_id": m.get("sig_market_id"), "title": m.get("sig_market_title"),
                "token_id": token, "at": iso(t),
                "sig_bid": ss["bid"], "sig_ask": ss["ask"], "sig_mid": ss["mid"], "sig_spread": ss["spread"], "sig_age_s": ss["age_s"],
                "pm_bid": ps["bid"], "pm_ask": ps["ask"], "pm_mid": ps["mid"], "pm_spread": ps["spread"], "pm_age_s": ps["age_s"],
                "pm_bid1_size": ps["bid1_size"], "pm_ask1_size": ps["ask1_size"], "pm_bid_depth5": ps["bid_depth5"], "pm_ask_depth5": ps["ask_depth5"],
                "pm_depth_age_s": ps["depth_age_s"],
                "buy_edge": ps["mid"]-ss["ask"] if ss["ask"] is not None else None,
                "sell_edge": ss["bid"]-ps["mid"] if ss["bid"] is not None else None,
                "sig_residual": ss["mid"]-ps["mid"] if ss["mid"] is not None else None,
                "abs_residual": abs(ss["mid"]-ps["mid"]) if ss["mid"] is not None else None,
                "prior60_count": len(prior), "prior60_signed_qty": sum(x[1] for x in prior),
            }
            p5, s5 = pm_snapshot(obs, depth, token, t-300), sig_snapshot(bbo, bbo_times, ex, t-300)
            row["pm_ret5m"] = ps["mid"]-p5["mid"] if p5 else None
            row["sig_ret5m"] = ss["mid"]-s5["mid"] if s5 and s5["mid"] is not None and ss["mid"] is not None else None
            for h in (60,300,1800):
                p, s = pm_snapshot(obs, depth, token, t+h), sig_snapshot(bbo, bbo_times, ex, t+h)
                row[f"pm_mid_{h}s"] = p["mid"] if p else None
                row[f"sig_bid_{h}s"] = s["bid"] if s else None
                row[f"sig_ask_{h}s"] = s["ask"] if s else None
                row[f"sig_mid_{h}s"] = s["mid"] if s else None
            state_rows.append(row)

    trade_path = out / "trade_features.csv.gz"
    state_path = out / "state_minute.csv.gz"
    mapping_path = out / "mapping_exact_same.csv.gz"
    write_gz_csv(trade_path, trade_rows)
    write_gz_csv(state_path, state_rows)
    with gzip.open(mapping_path, "wt", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(mapping[0]))
        w.writeheader(); w.writerows(mapping)

    paper_out = None
    if args.paper_events:
        src = Path(args.paper_events)
        if src.exists():
            paper_out = out / "paper_events.jsonl.gz"
            with src.open("rb") as inp, gzip.open(paper_out, "wb") as dst:
                shutil.copyfileobj(inp, dst)

    manifest = {
        "schema_version": 1,
        "name": "LIVE-ALPHA-V6-OVERNIGHT",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "evaluation_start": iso(eval_start), "evaluation_end": iso(eval_end),
        "source_start": iso(source_start), "source_end": iso(source_end),
        "counts": {
            "mappings": len(mapping), "trade_features": len(trade_rows), "state_minutes": len(state_rows),
            "sig_bbo_rows": sum(len(v) for v in bbo.values()), "sig_trade_rows_source": sum(len(v) for v in trades.values()),
            "pm_observation_files": len(obs_files), "pm_depth_files": len(depth_files),
        },
        "files": {}
    }
    files = [trade_path, state_path, mapping_path] + ([paper_out] if paper_out else [])
    for p in files:
        manifest["files"][p.name] = {"bytes": p.stat().st_size, "sha256": sha256(p)}
    (out / "SNAPSHOT_MANIFEST.json").write_text(json.dumps(manifest, indent=2, sort_keys=True)+"\n", encoding="utf-8")
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
