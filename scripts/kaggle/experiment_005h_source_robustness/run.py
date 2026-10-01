# ruff: noqa
from __future__ import annotations

import bisect
import json
import math
from collections import defaultdict, deque
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

WORK = Path("/kaggle/working")
ROOT = Path("/kaggle/input")
ORDERBOOK_SLUG = "sig-cup-data003-orderbooks"
FILL_SLUG = "sig-cup-data-003-sig-actual-fills"
SIZE_TOLERANCE = 1e-8
YES_NOTIONAL_TOLERANCE = 1e-3
CONTROL_INTERVAL_NS = 300_000_000_000
CONTROL_EXCLUSION_NS = 60_000_000_000
HISTORY_NS = 300_000_000_000
MIN_ARRIVAL_PAIRS = 100
MIN_DIRECTION_PER_SIDE = 100


def normalize_tx_hash(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (bytes, bytearray, memoryview)):
        return "0x" + bytes(value).hex()
    text = str(value).strip().lower()
    if not text or text in {"none", "nan", "<na>"}:
        return ""
    return text if text.startswith("0x") else "0x" + text


def bytes_to_token(value: Any) -> str:
    if isinstance(value, (bytes, bytearray, memoryview)):
        return str(int.from_bytes(bytes(value), "big", signed=False))
    return str(value)


def receive_ns(value: Any) -> int:
    return int(pd.Timestamp(value).tz_localize("UTC").value) if pd.Timestamp(value).tzinfo is None else int(pd.Timestamp(value).tz_convert("UTC").value)


def dataset_root(slug: str) -> Path:
    roots = [p for p in ROOT.rglob(slug) if p.is_dir()]
    if len(roots) != 1:
        raise RuntimeError(f"expected one {slug} root, found {roots}")
    return roots[0]


def locate_json(name: str) -> Path:
    matches = sorted(ROOT.rglob(name))
    if len(matches) != 1:
        raise RuntimeError(f"expected one {name}, found {matches}")
    return matches[0]


def load_json(name: str) -> dict[str, Any]:
    return json.loads(locate_json(name).read_text(encoding="utf-8"))


def fill_files(window: str) -> list[Path]:
    root = dataset_root(FILL_SLUG)
    files = sorted(
        p for p in root.rglob("*.parquet")
        if "fills" in p.parts
        and f"window_id={window}" in p.parts
        and not any(x in p.parts for x in ("fees", "rebates", "unattributed_fee_legs"))
    )
    if not files:
        raise RuntimeError(f"no fill files for {window}")
    return files


def accepted_groups(window: str) -> pd.DataFrame:
    frames = [pq.ParquetFile(p).read().to_pandas() for p in fill_files(window)]
    df = pd.concat(frames, ignore_index=True)
    df["tx_hash"] = df["tx_hash"].map(normalize_tx_hash)
    df["token_id"] = df["token_id"].astype(str)
    df["condition_id"] = df["condition_id"].astype(str)
    df["price"] = pd.to_numeric(df["price"], errors="raise").astype(float)
    df["size_shares"] = pd.to_numeric(df["size_shares"], errors="raise").astype(float)
    df["timestamp"] = pd.to_numeric(df["timestamp"], errors="raise").astype("int64")
    df["is_active"] = df["order_is_match_taker_order"].astype(bool)
    df["outcome"] = df["outcome_side"].astype(str).str.upper()
    rows: list[dict[str, Any]] = []
    for (condition_id, tx_hash), g in df.groupby(["condition_id", "tx_hash"], sort=False):
        active = g[g["is_active"]]
        passive = g[~g["is_active"]]
        if len(active) != 1 or passive.empty:
            continue
        if not g["outcome"].isin(["YES", "NO"]).all():
            continue
        a = active.iloc[0]
        active_size = float(a["size_shares"])
        passive_size = float(passive["size_shares"].sum())
        size_limit = SIZE_TOLERANCE * max(1.0, abs(active_size), abs(passive_size))
        if abs(active_size - passive_size) > size_limit:
            continue
        active_p_yes = float(a["price"]) if a["outcome"] == "YES" else 1.0 - float(a["price"])
        active_yes = active_p_yes * active_size
        passive_yes = 0.0
        for p in passive.itertuples(index=False):
            p_yes = float(p.price) if str(p.outcome) == "YES" else 1.0 - float(p.price)
            passive_yes += p_yes * float(p.size_shares)
        yes_limit = YES_NOTIONAL_TOLERANCE * max(1.0, abs(active_yes), abs(passive_yes))
        if abs(active_yes - passive_yes) > yes_limit:
            continue
        rows.append(
            {
                "window_id": window,
                "group_id": f"{condition_id}|{tx_hash}",
                "tx_hash": tx_hash,
                "token_id": str(a["token_id"]),
                "block_timestamp_s": int(a["timestamp"]),
                "episode_price": float(a["price"]),
                "episode_size": active_size,
            }
        )
    return pd.DataFrame(rows)


def level_map(levels: Any) -> dict[float, float]:
    out: dict[float, float] = {}
    if levels is None:
        return out
    try:
        for level in levels:
            if isinstance(level, dict):
                p = float(level["price"])
                s = float(level["size"])
            else:
                p = float(level[0])
                s = float(level[1])
            if math.isfinite(p) and math.isfinite(s) and s > 0:
                out[round(p, 6)] = s
    except (TypeError, ValueError, KeyError):
        return {}
    return out


class State:
    def __init__(self) -> None:
        self.bids: dict[float, float] = {}
        self.asks: dict[float, float] = {}
        self.initialized = False
        self.light: dict[str, float] | None = None
        self.last_book_ts: int | None = None
        self.update_times: deque[int] = deque()
        self.mid_hist: deque[tuple[int, float]] = deque()
        self.ofi_hist: deque[tuple[int, float]] = deque()
        self.trade_hist: deque[tuple[int, int, float]] = deque()
        self.control_bucket: int | None = None

    def best(self) -> tuple[float, float] | None:
        if not self.bids or not self.asks:
            return None
        bb = max(self.bids)
        ba = min(self.asks)
        if not (0.0 < bb <= ba < 1.0):
            return None
        return bb, ba

    def metrics(self, bb_hint: Any = None, ba_hint: Any = None) -> dict[str, float] | None:
        if not self.initialized:
            return None
        try:
            bb = round(float(bb_hint), 6)
            ba = round(float(ba_hint), 6)
            if not (0.0 < bb <= ba < 1.0 and self.bids.get(bb, 0) > 0 and self.asks.get(ba, 0) > 0):
                raise ValueError
        except (TypeError, ValueError):
            best = self.best()
            if best is None:
                return None
            bb, ba = best
        bsz = float(self.bids.get(bb, 0.0))
        asz = float(self.asks.get(ba, 0.0))
        if bsz <= 0 or asz <= 0:
            return None
        denom = bsz + asz
        return {
            "best_bid": bb,
            "best_ask": ba,
            "bid_size": bsz,
            "ask_size": asz,
            "mid": (bb + ba) / 2.0,
            "spread": ba - bb,
            "imbalance_touch": (bsz - asz) / denom,
            "microprice": (ba * bsz + bb * asz) / denom,
        }

    def snapshot(self, bids: Any, asks: Any, ts: int) -> tuple[dict[str, float] | None, dict[str, float] | None]:
        old = self.light
        self.bids = level_map(bids)
        self.asks = level_map(asks)
        self.initialized = bool(self.bids and self.asks)
        self.last_book_ts = ts
        self.light = self.metrics()
        return old, self.light

    def delta(self, side: str, price: Any, size: Any, ts: int, bb: Any, ba: Any) -> tuple[dict[str, float] | None, dict[str, float] | None]:
        old = self.light
        if not self.initialized:
            return old, old
        try:
            p = round(float(price), 6)
            s = float(size)
        except (TypeError, ValueError):
            return old, old
        book = self.bids if side == "BUY" else self.asks if side == "SELL" else None
        if book is None:
            return old, old
        if s <= 0:
            book.pop(p, None)
        else:
            book[p] = s
        self.last_book_ts = ts
        self.light = self.metrics(bb, ba)
        return old, self.light

    def depth_2c(self) -> tuple[float, float]:
        if self.light is None:
            return 0.0, 0.0
        bb = self.light["best_bid"]
        ba = self.light["best_ask"]
        bid = float(sum(s for p, s in self.bids.items() if p >= bb - 0.02 - 1e-12))
        ask = float(sum(s for p, s in self.asks.items() if p <= ba + 0.02 + 1e-12))
        return bid, ask


def ofi(old: dict[str, float] | None, new: dict[str, float] | None) -> float:
    if old is None or new is None:
        return 0.0
    pb0, pb1 = old["best_bid"], new["best_bid"]
    pa0, pa1 = old["best_ask"], new["best_ask"]
    qb0, qb1 = old["bid_size"], new["bid_size"]
    qa0, qa1 = old["ask_size"], new["ask_size"]
    return float(
        (qb1 if pb1 >= pb0 else 0.0)
        - (qb0 if pb1 <= pb0 else 0.0)
        - (qa1 if pa1 <= pa0 else 0.0)
        + (qa0 if pa1 >= pa0 else 0.0)
    )


def trim(state: State, ts: int) -> None:
    cutoff = ts - HISTORY_NS
    while state.update_times and state.update_times[0] < cutoff:
        state.update_times.popleft()
    while state.mid_hist and state.mid_hist[0][0] < cutoff:
        state.mid_hist.popleft()
    while state.ofi_hist and state.ofi_hist[0][0] < cutoff:
        state.ofi_hist.popleft()
    while state.trade_hist and state.trade_hist[0][0] < cutoff:
        state.trade_hist.popleft()


def asof_mid(hist: deque[tuple[int, float]], cutoff: int) -> float | None:
    for ts, mid in reversed(hist):
        if ts <= cutoff:
            return float(mid)
    return None


def feature_row(state: State, ts: int) -> dict[str, float] | None:
    light = state.light
    if light is None:
        return None
    trim(state, ts)
    bid2, ask2 = state.depth_2c()
    total2 = bid2 + ask2
    updates60 = sum(t >= ts - 60_000_000_000 for t in state.update_times)
    ofi60 = sum(v for t, v in state.ofi_hist if t >= ts - 60_000_000_000)
    mids60 = [m for t, m in state.mid_hist if t >= ts - 60_000_000_000]
    vol60 = float(np.std(np.diff(mids60))) if len(mids60) >= 3 else 0.0
    mid30 = asof_mid(state.mid_hist, ts - 30_000_000_000)
    buy = sum(s for t, q, s in state.trade_hist if t >= ts - 60_000_000_000 and q > 0)
    sell = sum(s for t, q, s in state.trade_hist if t >= ts - 60_000_000_000 and q < 0)
    total = buy + sell
    signed_flow = (buy - sell) / total if total > 0 else 0.0
    vpin = abs(buy - sell) / total if total > 0 else 0.0
    return {
        "mid": float(light["mid"]),
        "spread": float(light["spread"]),
        "log_depth_2c": float(math.log1p(max(total2, 0.0))),
        "activity_60": float(updates60),
        "mid_vol_60": vol60,
        "vpin_60": float(vpin),
        "imbalance_touch": float(light["imbalance_touch"]),
        "microprice_deviation": float(light["microprice"] - light["mid"]),
        "ofi_60": float(ofi60),
        "signed_flow_60": float(signed_flow),
        "pre_move_30": float(light["mid"] - mid30) if mid30 is not None else math.nan,
        "book_age_ms": float((ts - state.last_book_ts) / 1e6) if state.last_book_ts is not None else math.nan,
    }


def auc(y: np.ndarray, score: np.ndarray) -> float:
    y = np.asarray(y, dtype=int)
    score = np.asarray(score, dtype=float)
    pos = int((y == 1).sum())
    neg = int((y == 0).sum())
    if pos == 0 or neg == 0:
        return math.nan
    order = np.argsort(score, kind="stable")
    ranks = np.empty(len(score), dtype=float)
    i = 0
    while i < len(score):
        j = i + 1
        while j < len(score) and score[order[j]] == score[order[i]]:
            j += 1
        rank = (i + 1 + j) / 2.0
        ranks[order[i:j]] = rank
        i = j
    return float((ranks[y == 1].sum() - pos * (pos + 1) / 2) / (pos * neg))


def logistic_score(frame: pd.DataFrame, spec: dict[str, Any]) -> np.ndarray:
    features = list(spec["features"])
    x = frame[features].to_numpy(float)
    mean = np.asarray(spec["feature_mean"], dtype=float)
    std = np.asarray(spec["feature_std"], dtype=float)
    std[std == 0] = 1.0
    x = (x - mean) / std
    beta = np.asarray(spec["coefficients_standardized"], dtype=float)
    z = beta[0] + x @ beta[1:]
    z = np.clip(z, -40, 40)
    return 1.0 / (1.0 + np.exp(-z))


def nearest_distance(ts: int, times: list[int]) -> int:
    pos = bisect.bisect_left(times, ts)
    values = []
    if pos < len(times):
        values.append(abs(times[pos] - ts))
    if pos > 0:
        values.append(abs(times[pos - 1] - ts))
    return min(values) if values else 10**30


def match_controls(fills: pd.DataFrame, controls: pd.DataFrame) -> pd.DataFrame:
    if fills.empty or controls.empty:
        return pd.DataFrame()
    fill_times = {
        (str(w), str(s), str(t)): sorted(g["ts_ns"].astype("int64").tolist())
        for (w, s, t), g in fills.groupby(["window_id", "source_version", "token_id"], sort=False)
    }
    keep = []
    for r in controls.itertuples(index=False):
        times = fill_times.get((str(r.window_id), str(r.source_version), str(r.token_id)), [])
        keep.append(nearest_distance(int(r.ts_ns), times) > CONTROL_EXCLUSION_NS)
    controls = controls.loc[keep].copy()
    used: set[int] = set()
    rows: list[dict[str, Any]] = []
    groups = {
        key: g for key, g in controls.groupby(["window_id", "source_version", "token_id"], sort=False)
    }
    for fill in fills.sort_values("ts_ns").itertuples(index=False):
        key = (str(fill.window_id), str(fill.source_version), str(fill.token_id))
        candidates = groups.get(key)
        if candidates is None:
            continue
        candidates = candidates.loc[~candidates.index.isin(used)]
        if candidates.empty:
            continue
        score = (
            ((candidates["mid"] - float(fill.mid)) / 0.05) ** 2
            + ((candidates["spread"] - float(fill.spread)) / 0.02) ** 2
            + ((candidates["log_depth_2c"] - float(fill.log_depth_2c)) / 2.0) ** 2
            + ((candidates["activity_60"] - float(fill.activity_60)) / 25.0) ** 2
        )
        idx = int(score.idxmin())
        used.add(idx)
        c = candidates.loc[idx]
        rec = {
            "window_id": fill.window_id,
            "source_version": fill.source_version,
            "token_id": fill.token_id,
            "fill_ts_ns": int(fill.ts_ns),
            "control_ts_ns": int(c["ts_ns"]),
        }
        for name in ["mid","spread","log_depth_2c","activity_60","mid_vol_60","vpin_60"]:
            rec[f"fill_{name}"] = float(getattr(fill, name))
            rec[f"control_{name}"] = float(c[name])
        rows.append(rec)
    return pd.DataFrame(rows)


def process_source(
    source_dir: Path,
    window: str,
    groups: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    by_key = {
        (str(r.tx_hash), str(r.token_id)): r._asdict()
        for r in groups.itertuples(index=False)
    }
    target_tokens = {key[1] for key in by_key}
    target_bytes = {int(t).to_bytes(32, "big") for t in target_tokens}
    states: defaultdict[tuple[str, str], State] = defaultdict(State)
    fills: list[dict[str, Any]] = []
    controls: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    ambiguous_same_ts = 0
    files = sorted(source_dir.rglob("*.parquet"))
    wanted = [
        "event_type","timestamp_received","asset_id","bids","asks","price","size",
        "side","best_bid","best_ask","transaction_hash","source_version"
    ]
    for file_no, path in enumerate(files, start=1):
        pf = pq.ParquetFile(path)
        available = [c for c in wanted if c in pf.schema_arrow.names]
        frame = pf.read(columns=available).to_pandas()
        if frame.empty:
            continue
        frame = frame[frame["asset_id"].isin(target_bytes)].copy()
        if frame.empty:
            continue
        frame = frame[frame["event_type"].astype(str).isin(["book","price_change","last_trade_price"])].copy()
        if frame.empty:
            continue
        frame["ts_ns"] = [receive_ns(v) for v in frame["timestamp_received"]]
        frame["source_version"] = frame.get("source_version", pd.Series("UNKNOWN", index=frame.index)).astype(str)
        frame.sort_values("ts_ns", kind="stable", inplace=True)

        for ts_ns, same_ts in frame.groupby("ts_ns", sort=False):
            ts_ns = int(ts_ns)
            # Anchors first: state/history contains strictly earlier receive timestamps only.
            trades = same_ts[same_ts["event_type"].astype(str) == "last_trade_price"]
            for rec in trades.to_dict(orient="records"):
                token = bytes_to_token(rec["asset_id"])
                version = str(rec.get("source_version") or "UNKNOWN")
                state = states[(version, token)]
                tx_hash = normalize_tx_hash(rec.get("transaction_hash"))
                key = (tx_hash, token)
                side = str(rec.get("side") or "").upper()
                q = 1 if side == "BUY" else -1 if side == "SELL" else 0
                if key in by_key and key not in seen and q != 0:
                    feat = feature_row(state, ts_ns)
                    if feat is not None:
                        row = {
                            **by_key[key],
                            **feat,
                            "source_version": version,
                            "ts_ns": ts_ns,
                            "q": q,
                            "trade_side": side,
                        }
                        fills.append(row)
                        seen.add(key)
                # Same-timestamp trade history is not observable to another event at this timestamp.
                if len(trades) > 1:
                    ambiguous_same_ts += 1

            # Apply all book changes only after anchors at this timestamp.
            updates = same_ts[same_ts["event_type"].astype(str).isin(["book","price_change"])]
            for rec in updates.to_dict(orient="records"):
                token = bytes_to_token(rec["asset_id"])
                version = str(rec.get("source_version") or "UNKNOWN")
                state = states[(version, token)]
                kind = str(rec["event_type"])
                if kind == "book":
                    old, new = state.snapshot(rec.get("bids"), rec.get("asks"), ts_ns)
                else:
                    old, new = state.delta(
                        str(rec.get("side") or "").upper(),
                        rec.get("price"), rec.get("size"), ts_ns,
                        rec.get("best_bid"), rec.get("best_ask"),
                    )
                if new is not None:
                    trim(state, ts_ns)
                    state.update_times.append(ts_ns)
                    state.ofi_hist.append((ts_ns, ofi(old, new)))
                    if not state.mid_hist or state.mid_hist[-1][1] != new["mid"]:
                        state.mid_hist.append((ts_ns, float(new["mid"])))
                    bucket = ts_ns // CONTROL_INTERVAL_NS
                    if state.control_bucket != bucket:
                        feat = feature_row(state, ts_ns)
                        if feat is not None:
                            controls.append(
                                {
                                    "window_id": window,
                                    "source_version": version,
                                    "token_id": token,
                                    "ts_ns": ts_ns,
                                    **feat,
                                }
                            )
                            state.control_bucket = bucket

            # Add trade history only after all anchors at the timestamp.
            for rec in trades.to_dict(orient="records"):
                token = bytes_to_token(rec["asset_id"])
                version = str(rec.get("source_version") or "UNKNOWN")
                state = states[(version, token)]
                side = str(rec.get("side") or "").upper()
                q = 1 if side == "BUY" else -1 if side == "SELL" else 0
                try:
                    size = float(rec.get("size"))
                except (TypeError, ValueError):
                    size = 0.0
                if q and size > 0:
                    state.trade_hist.append((ts_ns, q, size))
                    trim(state, ts_ns)
        if file_no % 50 == 0 or file_no == len(files):
            print(json.dumps({"window":window,"source":source_dir.name,"file":file_no,"of":len(files),"fills":len(fills),"controls":len(controls)}), flush=True)

    return pd.DataFrame(fills), pd.DataFrame(controls), {
        "window_id": window,
        "source_directory": source_dir.name,
        "accepted_groups": int(len(groups)),
        "linked_feature_rows": int(len(fills)),
        "controls": int(len(controls)),
        "ambiguous_same_timestamp_trade_groups": int(ambiguous_same_ts),
    }


def evaluate_stratum(
    fills: pd.DataFrame,
    controls: pd.DataFrame,
    arrival_spec: dict[str, Any],
    direction_spec: dict[str, Any],
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    if fills.empty:
        return records
    for (window, version), f in fills.groupby(["window_id","source_version"], sort=False):
        c = controls[(controls["window_id"] == window) & (controls["source_version"] == version)].copy()
        pairs = match_controls(f, c)
        arrival = {
            "window_id": str(window),
            "source_version": str(version),
            "candidate_id": "005H-C04-ARRIVAL-STATE",
            "fill_rows": int(len(f)),
            "matched_pairs": int(len(pairs)),
        }
        if len(pairs) >= MIN_ARRIVAL_PAIRS:
            fill_cols = [f"fill_{name}" for name in arrival_spec["features"]]
            ctrl_cols = [f"control_{name}" for name in arrival_spec["features"]]
            xfill = pairs[fill_cols].copy()
            xfill.columns = arrival_spec["features"]
            xctrl = pairs[ctrl_cols].copy()
            xctrl.columns = arrival_spec["features"]
            x = pd.concat([xfill, xctrl], ignore_index=True)
            y = np.concatenate([np.ones(len(xfill), dtype=int), np.zeros(len(xctrl), dtype=int)])
            valid = x.replace([np.inf,-np.inf],np.nan).notna().all(axis=1).to_numpy()
            score = logistic_score(x.loc[valid], arrival_spec)
            value = auc(y[valid], score)
            arrival.update({"status":"TESTED","auc":value,"n":int(valid.sum())})
        else:
            arrival.update({"status":"UNTESTABLE_STRATUM","reason":"insufficient matched pairs"})
        records.append(arrival)

        direction = {
            "window_id": str(window),
            "source_version": str(version),
            "candidate_id": "005H-C05-DIRECTION-STATE",
        }
        cols = list(direction_spec["features"])
        work = f[cols + ["q"]].replace([np.inf,-np.inf],np.nan).dropna()
        y = (work["q"].astype(int).to_numpy() > 0).astype(int)
        pos = int(y.sum())
        neg = int(len(y)-pos)
        direction.update({"n":int(len(y)),"buy_n":pos,"sell_n":neg})
        if pos >= MIN_DIRECTION_PER_SIDE and neg >= MIN_DIRECTION_PER_SIDE:
            score = logistic_score(work[cols], direction_spec)
            direction.update({"status":"TESTED","auc":auc(y,score)})
        else:
            direction.update({"status":"UNTESTABLE_STRATUM","reason":"insufficient BUY/SELL support"})
        records.append(direction)
    return records


def summarize_candidate(candidate_id: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    tested = [r for r in rows if r["candidate_id"] == candidate_id and r.get("status") == "TESTED" and math.isfinite(float(r.get("auc", math.nan)))]
    all_rows = [r for r in rows if r["candidate_id"] == candidate_id]
    if not tested:
        return {
            "candidate_id": candidate_id,
            "status": "UNTESTABLE_SOURCE",
            "tested_strata": 0,
            "strata": all_rows,
            "reason": "no pre-V3 source stratum met exact feature-parity support thresholds",
        }
    aucs = [float(r["auc"]) for r in tested]
    positive = sum(v > 0.5 for v in aucs)
    status = "SUPPORTED" if positive > len(aucs) / 2 else "SOURCE_SPECIFIC"
    return {
        "candidate_id": candidate_id,
        "status": status,
        "tested_strata": len(tested),
        "positive_auc_strata": positive,
        "majority_positive": bool(positive > len(aucs) / 2),
        "median_auc": float(np.median(aucs)),
        "min_auc": float(min(aucs)),
        "max_auc": float(max(aucs)),
        "strata": all_rows,
    }


def main() -> None:
    source_audit = load_json("SOURCE_VERSION_AUDIT.json")
    shortlist = load_json("EXTENDED_V3_SHORTLIST.json")
    if source_audit.get("b0_opened") is not False or shortlist.get("b0_opened") is not False:
        raise RuntimeError("upstream sources do not prove B0 stayed sealed")

    arrival_spec = shortlist["arrival_result"]
    direction_spec = shortlist["direction_result"]
    candidate_ids = {str(x["candidate_id"]) for x in shortlist.get("candidates", [])}

    decisions = [
        row for row in source_audit["best_window_source_decisions"]
        if row.get("sequential_eligibility") == "ELIGIBLE"
    ]
    ob_root = dataset_root(ORDERBOOK_SLUG)
    fill_frames: list[pd.DataFrame] = []
    control_frames: list[pd.DataFrame] = []
    processing: list[dict[str, Any]] = []
    for decision in decisions:
        window = str(decision["window_id"])
        source = ob_root / str(decision["source_directory"])
        groups = accepted_groups(window)
        fills, controls, audit = process_source(source, window, groups)
        if not fills.empty:
            fill_frames.append(fills)
        if not controls.empty:
            control_frames.append(controls)
        processing.append(audit)

    fills = pd.concat(fill_frames, ignore_index=True, sort=False) if fill_frames else pd.DataFrame()
    controls = pd.concat(control_frames, ignore_index=True, sort=False) if control_frames else pd.DataFrame()
    fills.to_parquet(WORK / "SOURCE_ROBUSTNESS_FILL_FEATURES.parquet", index=False)
    controls.to_parquet(WORK / "SOURCE_ROBUSTNESS_CONTROLS.parquet", index=False)

    stratum_results = evaluate_stratum(fills, controls, arrival_spec, direction_spec)
    pd.DataFrame(stratum_results).to_parquet(WORK / "SOURCE_ROBUSTNESS_RESULTS.parquet", index=False)

    records: list[dict[str, Any]] = []
    for cid in ["005H-C04-ARRIVAL-STATE","005H-C05-DIRECTION-STATE"]:
        if cid in candidate_ids:
            records.append(summarize_candidate(cid, stratum_results))
    for cid in ["005H-C01-RELATIVE-SIZE","005H-C02-FAILED-REPLENISHMENT","005H-C03-FILL-BEYOND-STATE"]:
        if cid in candidate_ids:
            records.append(
                {
                    "candidate_id": cid,
                    "status": "NOT_APPLICABLE",
                    "reason": (
                        "candidate failed to achieve ROBUST_V3_SURVIVOR status in the preregistered hostile V3 gate; "
                        "pre-V3 outcome replication cannot restore B0 eligibility"
                    ),
                }
            )
    if "005H-C06-BOOK-MICROSTRUCTURE-CHALLENGER" in candidate_ids:
        records.append(
            {
                "candidate_id":"005H-C06-BOOK-MICROSTRUCTURE-CHALLENGER",
                "status":"UNTESTABLE_SOURCE",
                "reason":"no C06 challenger was shortlisted by the frozen extended V3 gate",
            }
        )

    summary = {
        "schema_version":1,
        "experiment":"EXPERIMENT-005H",
        "stage":"SOURCE_VERSION_ROBUSTNESS",
        "selection_boundary":"V2/AG6 evidence cannot add candidates, retune V3 candidates, or alter B0 thresholds",
        "processing_audit":processing,
        "candidate_records":records,
        "b0_opened":False,
        "real_sig_orders_sent":False,
    }
    (WORK/"SOURCE_ROBUSTNESS_SUMMARY.json").write_text(json.dumps(summary,indent=2,sort_keys=True,default=str)+"\n")
    lines=["# EXPERIMENT-005H — Source Robustness",""]
    for row in records:
        lines.append(f"- {row['candidate_id']}: **{row['status']}**")
    lines.extend(["","B0 opened: **NO**","","REAL SIG ORDERS SENT: NO"])
    (WORK/"SOURCE_ROBUSTNESS_REPORT.md").write_text("\n".join(lines)+"\n")
    print(json.dumps(summary,sort_keys=True,default=str),flush=True)


if __name__=="__main__":
    main()
