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
ORDERBOOK_SLUG = "sig-cup-data003-orderbooks"
WINDOWS = [
    ("W17", "ev17_ak_fl_wy", "TRAIN"),
    ("W18", "ev18_ok_sc_runoff_ga_runoff", "DEV"),
]
CLOCK_HORIZONS = (1, 5, 15, 30, 60, 300)
EVENT_HORIZONS = (1, 2, 5, 10, 25, 50)
CONTROL_INTERVAL_NS = 300_000_000_000
CONTROL_EXCLUSION_NS = 60_000_000_000
HISTORY_NS = 300_000_000_000
SIZE_TOLERANCE = 1e-8
YES_NOTIONAL_TOLERANCE = 1e-3


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


def receive_ns(series: pd.Series) -> np.ndarray:
    received = pd.to_datetime(series, utc=True)
    return received.to_numpy(dtype="datetime64[ns]").astype("int64")


def locate_orderbook_root() -> Path:
    roots = [
        path for path in Path("/kaggle/input").rglob(ORDERBOOK_SLUG)
        if path.is_dir() and (path / "_manifests").is_dir()
    ]
    if len(roots) != 1:
        raise RuntimeError(f"expected one orderbook root, found {roots}")
    return roots[0]


def locate_fill_files(window: str) -> list[Path]:
    files = sorted(
        path for path in Path("/kaggle/input").rglob("*.parquet")
        if "fills" in path.parts
        and f"window_id={window}" in path.parts
        and "fees" not in path.parts
        and "rebates" not in path.parts
        and "unattributed_fee_legs" not in path.parts
    )
    if not files:
        raise RuntimeError(f"no DATA-003 fill parquet files for {window}")
    return files


def load_fills(window: str) -> pd.DataFrame:
    frames = [pq.ParquetFile(path).read().to_pandas() for path in locate_fill_files(window)]
    frame = pd.concat(frames, ignore_index=True)
    required = {
        "timestamp", "tx_hash", "log_index", "condition_id", "token_id",
        "outcome_side", "price", "size_shares", "order_is_match_taker_order",
    }
    missing = required - set(frame.columns)
    if missing:
        raise RuntimeError(f"{window} fill schema missing {sorted(missing)}")
    frame["timestamp"] = pd.to_numeric(frame["timestamp"], errors="raise").astype("int64")
    frame["log_index"] = pd.to_numeric(frame["log_index"], errors="raise").astype("int64")
    frame["price"] = pd.to_numeric(frame["price"], errors="raise").astype(float)
    frame["size_shares"] = pd.to_numeric(frame["size_shares"], errors="raise").astype(float)
    frame["token_id"] = frame["token_id"].astype(str)
    frame["condition_id"] = frame["condition_id"].astype(str)
    frame["tx_hash"] = frame["tx_hash"].map(normalize_tx_hash)
    frame["outcome_side_norm"] = frame["outcome_side"].astype(str).str.upper()
    frame["is_active"] = frame["order_is_match_taker_order"].astype(bool)
    return frame


def accepted_groups(window: str, split: str) -> tuple[pd.DataFrame, dict[str, Any]]:
    frame = load_fills(window)
    groups: list[dict[str, Any]] = []
    failures: defaultdict[str, int] = defaultdict(int)
    grouped = frame.groupby(["condition_id", "tx_hash"], sort=False)
    optional = ["sig_market_id", "mapping_class", "mapping_direction", "day"]
    for (condition_id, tx_hash), rows in grouped:
        active = rows[rows["is_active"]]
        passive = rows[~rows["is_active"]]
        if len(active) != 1:
            failures["active_count_not_one"] += 1
            continue
        if passive.empty:
            failures["no_passive"] += 1
            continue
        if not rows["outcome_side_norm"].isin(["YES", "NO"]).all():
            failures["non_binary"] += 1
            continue
        a = active.iloc[0]
        active_size = float(a["size_shares"])
        passive_size = float(passive["size_shares"].sum())
        size_limit = SIZE_TOLERANCE * max(1.0, abs(active_size), abs(passive_size))
        if abs(active_size - passive_size) > size_limit:
            failures["size_conservation"] += 1
            continue
        active_p_yes = float(a["price"]) if a["outcome_side_norm"] == "YES" else 1.0 - float(a["price"])
        active_yes = active_p_yes * active_size
        passive_yes = 0.0
        for p in passive.itertuples(index=False):
            p_yes = float(p.price) if str(p.outcome_side_norm) == "YES" else 1.0 - float(p.price)
            passive_yes += p_yes * float(p.size_shares)
        yes_limit = YES_NOTIONAL_TOLERANCE * max(1.0, abs(active_yes), abs(passive_yes))
        if abs(active_yes - passive_yes) > yes_limit:
            failures["yes_notional_conservation"] += 1
            continue
        rec: dict[str, Any] = {
            "window_id": window,
            "split": split,
            "group_id": f"{condition_id}|{tx_hash}",
            "condition_id": condition_id,
            "tx_hash": tx_hash,
            "block_timestamp_s": int(a["timestamp"]),
            "token_id": str(a["token_id"]),
            "episode_price": float(a["price"]),
            "episode_size": active_size,
            "active_outcome": str(a["outcome_side_norm"]),
            "passive_rows": int(len(passive)),
        }
        for name in optional:
            rec[name] = a[name] if name in frame.columns else None
        groups.append(rec)
    out = pd.DataFrame(groups)
    audit = {
        "window": window,
        "participant_rows": int(len(frame)),
        "transaction_condition_groups": int(grouped.ngroups),
        "accepted_groups": int(len(out)),
        "rejected_groups": int(grouped.ngroups - len(out)),
        "failure_counts": dict(failures),
    }
    return out, audit


def level_map(levels: Any) -> dict[float, float]:
    out: dict[float, float] = {}
    if levels is None:
        return out
    try:
        for level in levels:
            if isinstance(level, dict):
                price = float(level["price"])
                size = float(level["size"])
            else:
                price = float(level[0])
                size = float(level[1])
            if math.isfinite(price) and math.isfinite(size) and size > 0.0:
                out[round(price, 6)] = size
    except (TypeError, ValueError, KeyError):
        return {}
    return out


class TokenState:
    def __init__(self) -> None:
        self.bids: dict[float, float] = {}
        self.asks: dict[float, float] = {}
        self.initialized = False
        self.light: dict[str, float] | None = None
        self.last_book_ts_ns: int | None = None
        self.update_times: deque[int] = deque()
        self.mid_hist: deque[tuple[int, float]] = deque()
        self.ofi_hist: deque[tuple[int, float]] = deque()
        self.trade_hist: deque[tuple[int, int, float]] = deque()
        self.last_control_bucket: int | None = None

    def _best(self) -> tuple[float, float] | None:
        if not self.bids or not self.asks:
            return None
        bb = max(self.bids)
        ba = min(self.asks)
        if not (0.0 < bb <= ba < 1.0):
            return None
        return bb, ba

    def light_metrics(
        self,
        bb_hint: Any = None,
        ba_hint: Any = None,
    ) -> dict[str, float] | None:
        if not self.initialized:
            return None
        try:
            bb = float(bb_hint)
            ba = float(ba_hint)
            if not (math.isfinite(bb) and math.isfinite(ba) and 0.0 < bb <= ba < 1.0):
                raise ValueError
            bb = round(bb, 6)
            ba = round(ba, 6)
            if self.bids.get(bb, 0.0) <= 0.0 or self.asks.get(ba, 0.0) <= 0.0:
                raise ValueError
        except (TypeError, ValueError):
            best = self._best()
            if best is None:
                return None
            bb, ba = best
        bsz = float(self.bids.get(bb, 0.0))
        asz = float(self.asks.get(ba, 0.0))
        if bsz <= 0.0 or asz <= 0.0:
            return None
        denom = bsz + asz
        mid = (bb + ba) / 2.0
        return {
            "best_bid": bb,
            "best_ask": ba,
            "bid_size": bsz,
            "ask_size": asz,
            "mid": mid,
            "spread": ba - bb,
            "imbalance_touch": (bsz - asz) / denom,
            "microprice": (ba * bsz + bb * asz) / denom,
        }

    def apply_snapshot(self, bids: Any, asks: Any, ts_ns: int) -> tuple[dict[str, float] | None, dict[str, float] | None]:
        old = self.light
        self.bids = level_map(bids)
        self.asks = level_map(asks)
        self.initialized = bool(self.bids and self.asks)
        self.last_book_ts_ns = ts_ns
        self.light = self.light_metrics()
        return old, self.light

    def apply_delta(
        self,
        side: str,
        price: Any,
        size: Any,
        ts_ns: int,
        bb_hint: Any,
        ba_hint: Any,
    ) -> tuple[dict[str, float] | None, dict[str, float] | None]:
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
        if s <= 0.0:
            book.pop(p, None)
        else:
            book[p] = s
        self.last_book_ts_ns = ts_ns
        self.light = self.light_metrics(bb_hint, ba_hint)
        return old, self.light

    def full_metrics(self, ts_ns: int) -> dict[str, float] | None:
        light = self.light
        if light is None:
            return None
        bb = light["best_bid"]
        ba = light["best_ask"]
        out = dict(light)
        for width, label in [(0.01, "1c"), (0.02, "2c"), (0.05, "5c")]:
            out[f"bid_depth_{label}"] = float(
                sum(size for price, size in self.bids.items() if price >= bb - width - 1e-12)
            )
            out[f"ask_depth_{label}"] = float(
                sum(size for price, size in self.asks.items() if price <= ba + width + 1e-12)
            )
        out["book_age_ms"] = (
            float((ts_ns - self.last_book_ts_ns) / 1e6)
            if self.last_book_ts_ns is not None
            else math.nan
        )
        return out

    def fixed_band_depth(self, q: int, touch: float, width: float = 0.02) -> float:
        if q > 0:
            return float(sum(size for price, size in self.asks.items() if touch - 1e-12 <= price <= touch + width + 1e-12))
        return float(sum(size for price, size in self.bids.items() if touch - width - 1e-12 <= price <= touch + 1e-12))


def ofi_value(old: dict[str, float] | None, new: dict[str, float] | None) -> float:
    if old is None or new is None:
        return 0.0
    pb0, pb1 = old["best_bid"], new["best_bid"]
    pa0, pa1 = old["best_ask"], new["best_ask"]
    qb0, qb1 = old["bid_size"], new["bid_size"]
    qa0, qa1 = old["ask_size"], new["ask_size"]
    bid = (qb1 if pb1 >= pb0 else 0.0) - (qb0 if pb1 <= pb0 else 0.0)
    ask = (qa1 if pa1 <= pa0 else 0.0) - (qa0 if pa1 >= pa0 else 0.0)
    return float(bid - ask)


def trim_histories(state: TokenState, ts_ns: int) -> None:
    cutoff = ts_ns - HISTORY_NS
    while state.update_times and state.update_times[0] < cutoff:
        state.update_times.popleft()
    while state.mid_hist and state.mid_hist[0][0] < cutoff:
        state.mid_hist.popleft()
    while state.ofi_hist and state.ofi_hist[0][0] < cutoff:
        state.ofi_hist.popleft()
    while state.trade_hist and state.trade_hist[0][0] < cutoff:
        state.trade_hist.popleft()


def asof_mid(hist: deque[tuple[int, float]], cutoff_ns: int) -> float | None:
    for ts_ns, mid in reversed(hist):
        if ts_ns <= cutoff_ns:
            return float(mid)
    return None


def pre_features(state: TokenState, ts_ns: int, q: int) -> dict[str, Any] | None:
    full = state.full_metrics(ts_ns)
    if full is None:
        return None
    trim_histories(state, ts_ns)
    updates_60 = sum(1 for value in state.update_times if value >= ts_ns - 60_000_000_000)
    ofi_60 = sum(value for time, value in state.ofi_hist if time >= ts_ns - 60_000_000_000)
    mids_60 = [mid for time, mid in state.mid_hist if time >= ts_ns - 60_000_000_000]
    vol_60 = float(np.std(np.diff(mids_60))) if len(mids_60) >= 3 else 0.0
    mid_30 = asof_mid(state.mid_hist, ts_ns - 30_000_000_000)
    buy_60 = sum(size for time, side, size in state.trade_hist if time >= ts_ns - 60_000_000_000 and side > 0)
    sell_60 = sum(size for time, side, size in state.trade_hist if time >= ts_ns - 60_000_000_000 and side < 0)
    total_60 = buy_60 + sell_60
    signed_flow_60 = (buy_60 - sell_60) / total_60 if total_60 > 0 else 0.0
    vpin_60 = abs(buy_60 - sell_60) / total_60 if total_60 > 0 else 0.0
    total_depth_2c = full["bid_depth_2c"] + full["ask_depth_2c"]
    full.update(
        {
            "activity_60": float(updates_60),
            "mid_vol_60": vol_60,
            "ofi_60": float(ofi_60),
            "signed_ofi_60": float(q * ofi_60),
            "pre_move_30": float(full["mid"] - mid_30) if mid_30 is not None else math.nan,
            "signed_pre_move_30": float(q * (full["mid"] - mid_30)) if mid_30 is not None else math.nan,
            "trade_volume_60": float(total_60),
            "signed_flow_60": float(signed_flow_60),
            "signed_flow_with_trade_60": float(q * signed_flow_60),
            "vpin_60": float(vpin_60),
            "microprice_deviation": float(full["microprice"] - full["mid"]),
            "signed_microprice_deviation": float(q * (full["microprice"] - full["mid"])),
            "signed_imbalance_touch": float(q * full["imbalance_touch"]),
            "log_depth_2c": float(math.log1p(max(total_depth_2c, 0.0))),
        }
    )
    return full


def scan_linked_trades(
    root: Path,
    groups: pd.DataFrame,
    ob_window: str,
) -> tuple[dict[tuple[str, str], dict[str, Any]], dict[str, Any]]:
    accepted_hashes = set(groups["tx_hash"].astype(str))
    active_tokens_by_hash = {
        str(row.tx_hash): str(row.token_id)
        for row in groups.itertuples(index=False)
    }
    found: defaultdict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    counts: defaultdict[str, int] = defaultdict(int)
    files = sorted((root / ob_window).rglob("*.parquet"))
    if not files:
        raise RuntimeError(f"no orderbook files for {ob_window}")
    columns = [
        "event_type", "timestamp_received", "sequence", "asset_id",
        "price", "size", "side", "transaction_hash",
    ]
    for number, path in enumerate(files, start=1):
        pf = pq.ParquetFile(path)
        available = [column for column in columns if column in pf.schema_arrow.names]
        frame = pf.read(columns=available).to_pandas()
        counts["raw_rows"] += len(frame)
        if frame.empty:
            continue
        frame = frame[frame["event_type"].astype(str) == "last_trade_price"].copy()
        counts["trade_rows"] += len(frame)
        if frame.empty:
            continue
        frame["tx_hash"] = frame["transaction_hash"].map(normalize_tx_hash)
        frame = frame[frame["tx_hash"].isin(accepted_hashes)].copy()
        if frame.empty:
            continue
        frame["ts_ns"] = receive_ns(frame["timestamp_received"])
        for rec in frame.itertuples(index=False):
            tx_hash = str(rec.tx_hash)
            token = bytes_to_token(rec.asset_id)
            if active_tokens_by_hash.get(tx_hash) != token:
                continue
            try:
                price = float(rec.price)
                size = float(rec.size)
            except (TypeError, ValueError):
                continue
            key = (tx_hash, token)
            found[key].append(
                {
                    "trade_ts_ns": int(rec.ts_ns),
                    "trade_sequence": int(rec.sequence),
                    "venue_price": price,
                    "venue_size": size,
                    "trade_side": str(rec.side or "").upper(),
                }
            )
        print(json.dumps({"stage": "link_scan", "window": ob_window, "file": number, "of": len(files)}), flush=True)
    linked: dict[tuple[str, str], dict[str, Any]] = {}
    ambiguous = 0
    for key, rows in found.items():
        if len(rows) == 1:
            linked[key] = rows[0]
        else:
            ambiguous += 1
    audit = {
        "orderbook_window": ob_window,
        "files": len(files),
        "raw_rows": counts["raw_rows"],
        "venue_trade_rows": counts["trade_rows"],
        "accepted_groups": int(len(groups)),
        "unique_hash_token_links": int(len(linked)),
        "ambiguous_hash_token_links": int(ambiguous),
        "unlinked_groups": int(len(groups) - len(linked)),
    }
    return linked, audit


def resolve_clock(
    pending_rows: list[dict[str, Any]],
    state: TokenState,
    ts_ns: int,
) -> None:
    light = state.light
    if light is None or not pending_rows:
        return
    for anchor in pending_rows:
        for horizon in CLOCK_HORIZONS:
            key = f"mid_{horizon}s"
            if key in anchor:
                continue
            if ts_ns > int(anchor["anchor_ts_ns"]) + horizon * 1_000_000_000:
                anchor[key] = float(light["mid"])
                anchor[f"spread_{horizon}s"] = float(light["spread"])


def process_post_update(
    pending_rows: list[dict[str, Any]],
    state: TokenState,
    ts_ns: int,
    sequence: int,
) -> None:
    if not pending_rows or state.light is None:
        return
    for anchor in pending_rows:
        if ts_ns < int(anchor["anchor_ts_ns"]):
            continue
        if ts_ns == int(anchor["anchor_ts_ns"]) and sequence <= int(anchor["anchor_sequence"]):
            continue
        anchor["_post_updates"] = int(anchor.get("_post_updates", 0)) + 1
        count = int(anchor["_post_updates"])
        if count in EVENT_HORIZONS:
            anchor[f"mid_e{count}"] = float(state.light["mid"])
        if anchor["kind"] != "FILL":
            continue
        q = int(anchor["q"])
        pre_depth = float(anchor.get("pre_consumed_depth_2c") or 0.0)
        if pre_depth <= 0.0:
            continue
        current = state.fixed_band_depth(q, float(anchor["pre_touch_price"]), 0.02)
        frac = current / pre_depth
        elapsed_ms = (ts_ns - int(anchor["anchor_ts_ns"])) / 1e6
        if elapsed_ms <= 5000:
            current_min = anchor.get("min_depth_fraction_5s")
            anchor["min_depth_fraction_5s"] = float(frac if current_min is None else min(float(current_min), frac))
        if "first_post_depth_fraction" not in anchor:
            anchor["first_post_depth_fraction"] = float(frac)
        for threshold, label in [
            (0.25, "25"),
            (0.5, "50"),
            (0.8, "80"),
            (0.9, "90"),
            (1.0, "100"),
        ]:
            key = f"replenish_{label}_ms"
            if key not in anchor and frac >= threshold:
                anchor[key] = float(elapsed_ms)


def drop_expired(pending_rows: list[dict[str, Any]], ts_ns: int) -> list[dict[str, Any]]:
    kept = []
    for anchor in pending_rows:
        if ts_ns <= int(anchor["anchor_ts_ns"]) + 305_000_000_000:
            kept.append(anchor)
    return kept


def build_anchor(
    kind: str,
    window: str,
    split: str,
    token: str,
    ts_ns: int,
    sequence: int,
    state: TokenState,
    q: int,
) -> dict[str, Any] | None:
    features = pre_features(state, ts_ns, q)
    if features is None:
        return None
    row: dict[str, Any] = {
        "kind": kind,
        "window_id": window,
        "split": split,
        "token_id": token,
        "anchor_ts_ns": int(ts_ns),
        "anchor_sequence": int(sequence),
        "q": int(q),
        **features,
    }
    return row


def process_window(
    root: Path,
    groups: pd.DataFrame,
    linked: dict[tuple[str, str], dict[str, Any]],
    ob_window: str,
    window: str,
    split: str,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    group_by_key = {
        (str(row.tx_hash), str(row.token_id)): row._asdict()
        for row in groups.itertuples(index=False)
        if (str(row.tx_hash), str(row.token_id)) in linked
    }
    target_tokens = {key[1] for key in group_by_key}
    target_bytes = {int(token).to_bytes(32, "big") for token in target_tokens}
    states = {token: TokenState() for token in target_tokens}
    pending: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    fills: list[dict[str, Any]] = []
    controls: list[dict[str, Any]] = []
    seen_link_keys: set[tuple[str, str]] = set()
    counts: defaultdict[str, int] = defaultdict(int)
    files = sorted((root / ob_window).rglob("*.parquet"))
    wanted = [
        "event_type", "timestamp_received", "sequence", "asset_id",
        "bids", "asks", "price", "size", "side", "best_bid", "best_ask",
        "transaction_hash",
    ]
    last_seen_by_token: dict[str, int] = {}
    window_end_ns = 0

    for number, path in enumerate(files, start=1):
        pf = pq.ParquetFile(path)
        available = [column for column in wanted if column in pf.schema_arrow.names]
        frame = pf.read(columns=available).to_pandas()
        counts["raw_rows"] += len(frame)
        if frame.empty:
            continue
        raw_received_ns = receive_ns(frame["timestamp_received"])
        if len(raw_received_ns):
            window_end_ns = max(window_end_ns, int(raw_received_ns.max()))
        frame = frame[frame["asset_id"].isin(target_bytes)].copy()
        counts["target_token_rows"] += len(frame)
        if frame.empty:
            continue
        frame = frame[
            frame["event_type"].astype(str).isin(["book", "price_change", "last_trade_price"])
        ].copy()
        if frame.empty:
            continue
        frame["ts_ns"] = receive_ns(frame["timestamp_received"])
        frame["sequence"] = pd.to_numeric(frame["sequence"], errors="raise").astype("uint64")
        frame.sort_values(["ts_ns", "sequence"], kind="stable", inplace=True)

        for rec in frame.itertuples(index=False):
            token = bytes_to_token(rec.asset_id)
            state = states[token]
            ts_ns = int(rec.ts_ns)
            sequence = int(rec.sequence)
            last_seen_by_token[token] = ts_ns
            if pending[token]:
                resolve_clock(pending[token], state, ts_ns)
                pending[token] = drop_expired(pending[token], ts_ns)
            kind = str(rec.event_type)

            if kind == "book":
                old, new = state.apply_snapshot(rec.bids, rec.asks, ts_ns)
                if new is not None:
                    trim_histories(state, ts_ns)
                    state.update_times.append(ts_ns)
                    state.ofi_hist.append((ts_ns, ofi_value(old, new)))
                    if not state.mid_hist or new["mid"] != state.mid_hist[-1][1]:
                        state.mid_hist.append((ts_ns, float(new["mid"])))
                    process_post_update(pending[token], state, ts_ns, sequence)
                    bucket = ts_ns // CONTROL_INTERVAL_NS
                    if state.last_control_bucket != bucket:
                        control = build_anchor("CONTROL", window, split, token, ts_ns, sequence, state, 1)
                        if control is not None:
                            controls.append(control)
                            pending[token].append(control)
                            state.last_control_bucket = bucket
                continue

            if kind == "price_change":
                old, new = state.apply_delta(
                    str(rec.side or "").upper(),
                    rec.price,
                    rec.size,
                    ts_ns,
                    rec.best_bid,
                    rec.best_ask,
                )
                if new is not None:
                    trim_histories(state, ts_ns)
                    state.update_times.append(ts_ns)
                    state.ofi_hist.append((ts_ns, ofi_value(old, new)))
                    if not state.mid_hist or new["mid"] != state.mid_hist[-1][1]:
                        state.mid_hist.append((ts_ns, float(new["mid"])))
                    process_post_update(pending[token], state, ts_ns, sequence)
                    bucket = ts_ns // CONTROL_INTERVAL_NS
                    if state.last_control_bucket != bucket:
                        control = build_anchor("CONTROL", window, split, token, ts_ns, sequence, state, 1)
                        if control is not None:
                            controls.append(control)
                            pending[token].append(control)
                            state.last_control_bucket = bucket
                continue

            if kind == "last_trade_price":
                side = str(rec.side or "").upper()
                q = 1 if side == "BUY" else -1 if side == "SELL" else 0
                tx_hash = normalize_tx_hash(rec.transaction_hash)
                key = (tx_hash, token)
                if key in group_by_key and key not in seen_link_keys:
                    group = group_by_key[key]
                    anchor = build_anchor("FILL", window, split, token, ts_ns, sequence, state, q)
                    if anchor is not None and q != 0:
                        venue_price = float(rec.price)
                        venue_size = float(rec.size)
                        exact = (
                            round(venue_price, 6) == round(float(group["episode_price"]), 6)
                            and round(venue_size, 6) == round(float(group["episode_size"]), 6)
                        )
                        consumed_touch = anchor["ask_size"] if q > 0 else anchor["bid_size"]
                        consumed_2c = anchor["ask_depth_2c"] if q > 0 else anchor["bid_depth_2c"]
                        anchor.update(
                            {
                                **group,
                                "venue_price": venue_price,
                                "venue_size": venue_size,
                                "trade_side": side,
                                "economic_exact": bool(exact),
                                "venue_minus_block_s": (
                                    ts_ns - int(group["block_timestamp_s"]) * 1_000_000_000
                                ) / 1e9,
                                "pre_touch_price": anchor["best_ask"] if q > 0 else anchor["best_bid"],
                                "pre_consumed_touch_depth": float(consumed_touch),
                                "pre_consumed_depth_2c": float(consumed_2c),
                                "venue_size_over_touch": venue_size / consumed_touch if consumed_touch > 0 else math.nan,
                                "venue_size_over_depth_2c": venue_size / consumed_2c if consumed_2c > 0 else math.nan,
                                "episode_size_over_touch": float(group["episode_size"]) / consumed_touch if exact and consumed_touch > 0 else math.nan,
                                "episode_size_over_depth_2c": float(group["episode_size"]) / consumed_2c if exact and consumed_2c > 0 else math.nan,
                            }
                        )
                        fills.append(anchor)
                        pending[token].append(anchor)
                        seen_link_keys.add(key)
                if q != 0:
                    trim_histories(state, ts_ns)
                    try:
                        trade_size = float(rec.size)
                    except (TypeError, ValueError):
                        trade_size = 0.0
                    if trade_size > 0:
                        state.trade_hist.append((ts_ns, q, trade_size))

        print(
            json.dumps(
                {
                    "stage": "state_replay",
                    "window": window,
                    "file": number,
                    "of": len(files),
                    "fills": len(fills),
                    "controls": len(controls),
                    "target_rows": counts["target_token_rows"],
                }
            ),
            flush=True,
        )

    for token, rows in pending.items():
        state = states[token]
        last_seen = last_seen_by_token.get(token)
        if last_seen is not None and window_end_ns > 0:
            # Clock-time outcomes are last-observation-carried-forward as of the
            # horizon. A quiet token must not lose support merely because it had
            # no later update to trigger resolve_clock.
            resolve_clock(rows, state, window_end_ns + 1)
    fill_frame = pd.DataFrame(fills)
    control_frame = pd.DataFrame(controls)
    audit = {
        "window": window,
        "split": split,
        "target_tokens": len(target_tokens),
        "linked_groups_expected": len(group_by_key),
        "fill_anchors_built": len(fill_frame),
        "linked_trade_rows_seen": len(seen_link_keys),
        "controls_built": len(control_frame),
        "raw_rows_scanned": counts["raw_rows"],
        "target_token_rows": counts["target_token_rows"],
        "window_end_ns": int(window_end_ns),
        "clock_outcome_semantics": (
            "last observable reconstructed state at or before horizon; "
            "quiet tokens carry the last state forward through acquisition end"
        ),
    }
    return fill_frame, control_frame, audit


def add_derived(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return frame
    frame = frame.copy()
    q = frame["q"].astype(float)
    if "venue_price" in frame.columns:
        frame["effective_spread"] = q * (frame["venue_price"] - frame["mid"])
    for horizon in CLOCK_HORIZONS:
        mid_col = f"mid_{horizon}s"
        if mid_col not in frame.columns:
            continue
        frame[f"signed_move_{horizon}s"] = q * (frame[mid_col] - frame["mid"])
        frame[f"abs_move_{horizon}s"] = (frame[mid_col] - frame["mid"]).abs()
        if "venue_price" in frame.columns:
            frame[f"aggressor_markout_{horizon}s"] = q * (frame[mid_col] - frame["venue_price"])
            frame[f"realized_spread_{horizon}s"] = q * (frame["venue_price"] - frame[mid_col])
    for count in EVENT_HORIZONS:
        col = f"mid_e{count}"
        if col in frame.columns:
            frame[f"signed_move_e{count}"] = q * (frame[col] - frame["mid"])
    if "replenish_80_ms" in frame.columns:
        frame["failed_replenish_80_30s"] = (
            frame["replenish_80_ms"].isna() | (frame["replenish_80_ms"] > 30000)
        )
    else:
        frame["failed_replenish_80_30s"] = True
    frame["price_region"] = pd.cut(
        frame["mid"],
        bins=[0.0, 0.05, 0.15, 0.30, 0.45, 0.55, 0.70, 0.85, 0.95, 1.0],
        labels=[
            "0-5",
            "5-15",
            "15-30",
            "30-45",
            "45-55",
            "55-70",
            "70-85",
            "85-95",
            "95-100",
        ],
        include_lowest=True,
    ).astype(str)
    return frame


def add_future_flow(fills: pd.DataFrame) -> pd.DataFrame:
    if fills.empty:
        return fills
    fills = fills.copy()
    fills["next_same_count_30s"] = 0
    fills["next_opp_count_30s"] = 0
    for token, group in fills.groupby("token_id", sort=False):
        idx = list(group.sort_values("anchor_ts_ns").index)
        times = fills.loc[idx, "anchor_ts_ns"].astype("int64").to_numpy()
        sides = fills.loc[idx, "q"].astype(int).to_numpy()
        for pos, row_index in enumerate(idx):
            stop = np.searchsorted(times, times[pos] + 30_000_000_000, side="right")
            future = sides[pos + 1 : stop]
            fills.at[row_index, "next_same_count_30s"] = int(np.sum(future == sides[pos]))
            fills.at[row_index, "next_opp_count_30s"] = int(np.sum(future == -sides[pos]))
    fills["flow_persistence_30s"] = fills["next_same_count_30s"] - fills["next_opp_count_30s"]
    return fills


def build_episodes(
    fills: pd.DataFrame,
    threshold_s: float = 10.0,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    if fills.empty:
        return fills.copy(), pd.DataFrame()
    out = fills.copy()
    out["episode_id"] = ""
    out["episode_position"] = 0
    episode_counter = 0
    for token, group in out.groupby("token_id", sort=False):
        ordered = list(group.sort_values("anchor_ts_ns").index)
        previous_index: int | None = None
        position = 0
        current_id = ""
        for index in ordered:
            start_new = True
            if previous_index is not None:
                dt_s = (
                    int(out.at[index, "anchor_ts_ns"])
                    - int(out.at[previous_index, "anchor_ts_ns"])
                ) / 1e9
                same_side = int(out.at[index, "q"]) == int(out.at[previous_index, "q"])
                start_new = not (same_side and dt_s <= threshold_s)
            if start_new:
                episode_counter += 1
                current_id = f"E{episode_counter:07d}"
                position = 1
            else:
                position += 1
            out.at[index, "episode_id"] = current_id
            out.at[index, "episode_position"] = position
            previous_index = index

    rows: list[dict[str, Any]] = []
    for episode_id, group in out.groupby("episode_id", sort=False):
        ordered = group.sort_values("anchor_ts_ns")
        first = ordered.iloc[0]
        last = ordered.iloc[-1]
        record: dict[str, Any] = {
            "episode_id": episode_id,
            "window_id": first["window_id"],
            "split": first["split"],
            "token_id": first["token_id"],
            "side": int(first["q"]),
            "fills": int(len(ordered)),
            "start_ts_ns": int(first["anchor_ts_ns"]),
            "end_ts_ns": int(last["anchor_ts_ns"]),
            "duration_s": (
                int(last["anchor_ts_ns"]) - int(first["anchor_ts_ns"])
            ) / 1e9,
            "total_venue_size": float(ordered["venue_size"].sum()),
            "total_episode_size_exact": float(
                ordered.loc[ordered["economic_exact"].astype(bool), "episode_size"].sum()
            ),
            "pre_mid": float(first["mid"]),
            "pre_spread": float(first["spread"]),
            "pre_depth_2c": float(first["pre_consumed_depth_2c"]),
            "first_failed_replenish_80_30s": bool(
                first["failed_replenish_80_30s"]
            ),
        }
        for horizon in CLOCK_HORIZONS:
            column = f"signed_move_{horizon}s"
            if column in ordered.columns:
                values = pd.to_numeric(ordered[column], errors="coerce").dropna()
                record[f"mean_fill_signed_move_{horizon}s"] = (
                    float(values.mean()) if not values.empty else math.nan
                )
                last_value = ordered.iloc[-1].get(column)
                record[f"last_fill_signed_move_{horizon}s"] = (
                    float(last_value) if pd.notna(last_value) else math.nan
                )
        rows.append(record)
    return out, pd.DataFrame(rows)


def episode_sensitivity(fills: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for threshold in (5.0, 10.0, 30.0):
        _, episodes = build_episodes(fills, threshold_s=threshold)
        if episodes.empty:
            continue
        rows.append(
            {
                "threshold_s": threshold,
                "episodes": int(len(episodes)),
                "multi_fill_episode_share": float((episodes["fills"] > 1).mean()),
                "mean_fills_per_episode": float(episodes["fills"].mean()),
                "median_duration_s": float(episodes["duration_s"].median()),
            }
        )
    return pd.DataFrame(rows)


def nearest_fill_distance(ts: int, sorted_times: list[int]) -> int:
    if not sorted_times:
        return 10**30
    pos = bisect.bisect_left(sorted_times, ts)
    values = []
    if pos < len(sorted_times):
        values.append(abs(sorted_times[pos] - ts))
    if pos > 0:
        values.append(abs(sorted_times[pos - 1] - ts))
    return min(values) if values else 10**30


def matched_controls(fills: pd.DataFrame, controls: pd.DataFrame) -> pd.DataFrame:
    if fills.empty or controls.empty:
        return pd.DataFrame()
    controls = controls.copy()
    fill_times = {
        (window, token): sorted(group["anchor_ts_ns"].astype("int64").tolist())
        for (window, token), group in fills.groupby(["window_id", "token_id"], sort=False)
    }
    controls["near_fill"] = [
        nearest_fill_distance(
            int(row.anchor_ts_ns),
            fill_times.get((str(row.window_id), str(row.token_id)), []),
        ) <= CONTROL_EXCLUSION_NS
        for row in controls.itertuples(index=False)
    ]
    controls = controls[~controls["near_fill"]].copy()
    rows: list[dict[str, Any]] = []
    grouped_controls = {
        key: group
        for key, group in controls.groupby(["window_id", "token_id"], sort=False)
    }
    for fill in fills.itertuples(index=False):
        candidates = grouped_controls.get((str(fill.window_id), str(fill.token_id)))
        if candidates is None or candidates.empty:
            continue
        f_depth = math.log1p(max(float(fill.bid_depth_2c + fill.ask_depth_2c), 0.0))
        score = (
            ((candidates["mid"] - float(fill.mid)) / 0.05) ** 2
            + ((candidates["spread"] - float(fill.spread)) / 0.02) ** 2
            + ((candidates["log_depth_2c"] - f_depth) / 2.0) ** 2
            + ((candidates["activity_60"] - float(fill.activity_60)) / 25.0) ** 2
        )
        control = candidates.loc[score.idxmin()]
        rec = {
            "window_id": fill.window_id,
            "split": fill.split,
            "group_id": fill.group_id,
            "token_id": fill.token_id,
            "fill_ts_ns": int(fill.anchor_ts_ns),
            "control_ts_ns": int(control["anchor_ts_ns"]),
            "match_score": float(score.loc[control.name]),
        }
        for horizon in (30, 300):
            fcol = f"mid_{horizon}s"
            if fcol in fills.columns and fcol in controls.columns:
                if pd.notna(getattr(fill, fcol)) and pd.notna(control[fcol]):
                    q = int(fill.q)
                    fill_move = q * (float(getattr(fill, fcol)) - float(fill.mid))
                    control_move = q * (float(control[fcol]) - float(control["mid"]))
                    rec[f"fill_signed_move_{horizon}s"] = fill_move
                    rec[f"control_signed_move_{horizon}s"] = control_move
                    rec[f"delta_signed_move_{horizon}s"] = fill_move - control_move
                    rec[f"fill_abs_move_{horizon}s"] = abs(float(getattr(fill, fcol)) - float(fill.mid))
                    rec[f"control_abs_move_{horizon}s"] = abs(float(control[fcol]) - float(control["mid"]))
        rows.append(rec)
    return pd.DataFrame(rows)


def cluster_bootstrap_mean(frame: pd.DataFrame, value: str, cluster: str = "token_id", seed: int = 5005) -> dict[str, Any]:
    work = frame[[cluster, value]].dropna()
    if work.empty:
        return {"n": 0}
    grouped = {key: values[value].to_numpy(float) for key, values in work.groupby(cluster)}
    keys = list(grouped)
    point = float(work[value].mean())
    rng = np.random.default_rng(seed)
    boots = []
    for _ in range(500):
        sampled = rng.choice(keys, size=len(keys), replace=True)
        values = np.concatenate([grouped[key] for key in sampled])
        boots.append(float(np.mean(values)))
    return {
        "n": int(len(work)),
        "clusters": int(len(keys)),
        "mean": point,
        "ci_2_5": float(np.quantile(boots, 0.025)),
        "ci_97_5": float(np.quantile(boots, 0.975)),
    }


def fit_ols(train: pd.DataFrame, test: pd.DataFrame, features: list[str], target: str) -> dict[str, Any]:
    cols = features + [target]
    tr = train[cols].replace([np.inf, -np.inf], np.nan).dropna()
    te = test[cols].replace([np.inf, -np.inf], np.nan).dropna()
    if len(tr) < max(50, len(features) * 10) or len(te) < 30:
        return {"features": features, "train_n": len(tr), "dev_n": len(te), "status": "INSUFFICIENT"}
    xtr = tr[features].to_numpy(float)
    xte = te[features].to_numpy(float)
    mean = xtr.mean(axis=0)
    std = xtr.std(axis=0)
    std[std == 0] = 1.0
    xtr = (xtr - mean) / std
    xte = (xte - mean) / std
    xtr = np.column_stack([np.ones(len(xtr)), xtr])
    xte = np.column_stack([np.ones(len(xte)), xte])
    ytr = tr[target].to_numpy(float)
    yte = te[target].to_numpy(float)
    beta, *_ = np.linalg.lstsq(xtr, ytr, rcond=None)
    ptr = xtr @ beta
    pte = xte @ beta
    def metrics(y: np.ndarray, pred: np.ndarray) -> tuple[float, float]:
        mse = float(np.mean((y - pred) ** 2))
        denom = float(np.sum((y - y.mean()) ** 2))
        r2 = 1.0 - float(np.sum((y - pred) ** 2)) / denom if denom > 0 else math.nan
        return math.sqrt(mse), r2
    tr_rmse, tr_r2 = metrics(ytr, ptr)
    te_rmse, te_r2 = metrics(yte, pte)
    return {
        "features": features,
        "target": target,
        "train_n": int(len(tr)),
        "dev_n": int(len(te)),
        "train_rmse": tr_rmse,
        "train_r2": tr_r2,
        "dev_rmse": te_rmse,
        "dev_r2": te_r2,
        "coefficients_standardized": [float(value) for value in beta],
        "status": "OK",
    }


def response_rows(fills: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    train = fills[fills["split"] == "TRAIN"]
    mechanisms = [
        ("episode_size_over_touch", fills["economic_exact"].astype(bool)),
        ("venue_size_over_touch", pd.Series(True, index=fills.index)),
        ("signed_imbalance_touch", pd.Series(True, index=fills.index)),
        ("signed_microprice_deviation", pd.Series(True, index=fills.index)),
        ("signed_ofi_60", pd.Series(True, index=fills.index)),
        ("vpin_60", pd.Series(True, index=fills.index)),
    ]
    for feature, eligible in mechanisms:
        source = train.loc[eligible.loc[train.index], feature].replace([np.inf, -np.inf], np.nan).dropna()
        if len(source) < 50:
            continue
        edges = np.unique(np.quantile(source, [0, .2, .4, .6, .8, 1]))
        if len(edges) < 3:
            continue
        work = fills.loc[eligible].copy()
        work["feature_bin"] = pd.cut(work[feature], bins=edges, include_lowest=True, duplicates="drop")
        for (split, bucket), group in work.groupby(["split", "feature_bin"], observed=True):
            rows.append(
                {
                    "mechanism": feature,
                    "split": split,
                    "bin": str(bucket),
                    "n": int(len(group)),
                    "mean_signed_move_30s": float(group["signed_move_30s"].mean()),
                    "mean_signed_move_300s": float(group["signed_move_300s"].mean()),
                    "mean_aggressor_markout_30s": float(group["aggressor_markout_30s"].mean()),
                    "failed_replenish_rate": float(group["failed_replenish_80_30s"].mean()),
                    "mean_flow_persistence_30s": float(group["flow_persistence_30s"].mean()),
                }
            )
    for (split, region), group in fills.groupby(["split", "price_region"], observed=True):
        rows.append(
            {
                "mechanism": "price_region",
                "split": split,
                "bin": str(region),
                "n": int(len(group)),
                "mean_signed_move_30s": float(group["signed_move_30s"].mean()),
                "mean_signed_move_300s": float(group["signed_move_300s"].mean()),
                "mean_aggressor_markout_30s": float(group["aggressor_markout_30s"].mean()),
                "failed_replenish_rate": float(group["failed_replenish_80_30s"].mean()),
                "mean_flow_persistence_30s": float(group["flow_persistence_30s"].mean()),
            }
        )
    return pd.DataFrame(rows)


def main() -> None:
    root = locate_orderbook_root()
    all_groups: list[pd.DataFrame] = []
    join_audits: list[dict[str, Any]] = []
    group_audits: list[dict[str, Any]] = []
    linked_by_window: dict[str, dict[tuple[str, str], dict[str, Any]]] = {}

    for window, ob_window, split in WINDOWS:
        groups, audit = accepted_groups(window, split)
        linked, link_audit = scan_linked_trades(root, groups, ob_window)
        all_groups.append(groups)
        group_audits.append(audit)
        join_audits.append(link_audit)
        linked_by_window[window] = linked

    join_report = {
        "experiment": "EXPERIMENT-005H",
        "stage": "V3_W17_W18_JOIN_AUDIT",
        "identity_rule": "exact transaction_hash + active token_id",
        "prebook_rule": "last reconstructed V3 book state strictly before linked trade in timestamp_received,sequence order",
        "clock_unit": "nanoseconds normalized explicitly from recorder receive timestamps",
        "groups": group_audits,
        "links": join_audits,
        "b0_opened": False,
        "real_sig_orders_sent": False,
    }
    (WORK / "JOIN_AUDIT.json").write_text(json.dumps(join_report, indent=2, sort_keys=True) + "\n")
    (WORK / "JOIN_AUDIT.md").write_text(
        "# EXPERIMENT-005H — V3 Join Audit\n\n"
        "Identity is exact transaction hash plus active token. Price/size are diagnostics, not identity. "
        "Pre-fill state is reconstructed only from recorder events strictly earlier in V3 receive-time+sequence order.\n\n"
        + "\n".join(
            f"- {row['orderbook_window']}: {row['unique_hash_token_links']}/{row['accepted_groups']} unique linked groups"
            for row in join_audits
        )
        + "\n\nB0 opened: NO\n\nREAL SIG ORDERS SENT: NO\n"
    )

    fill_frames = []
    control_frames = []
    replay_audits = []
    for window, ob_window, split in WINDOWS:
        groups = next(frame for frame in all_groups if str(frame.iloc[0]["window_id"]) == window)
        fills, controls, replay = process_window(
            root,
            groups,
            linked_by_window[window],
            ob_window,
            window,
            split,
        )
        fill_frames.append(fills)
        control_frames.append(controls)
        replay_audits.append(replay)

    fills = add_derived(pd.concat(fill_frames, ignore_index=True, sort=False))
    fills = add_future_flow(fills)
    fills, episodes = build_episodes(fills, threshold_s=10.0)
    episode_sensitivity_frame = episode_sensitivity(fills)
    controls = add_derived(pd.concat(control_frames, ignore_index=True, sort=False))
    pairs = matched_controls(fills, controls)

    fills["log_episode_size"] = np.log1p(fills["episode_size"].astype(float))
    fills["log_venue_size"] = np.log1p(fills["venue_size"].astype(float))
    fills["log_episode_size_over_touch"] = np.log1p(fills["episode_size_over_touch"].astype(float))
    fills["log_venue_size_over_touch"] = np.log1p(fills["venue_size_over_touch"].astype(float))

    fills.to_parquet(WORK / "FILL_EVENTS.parquet", index=False)
    episodes.to_parquet(WORK / "FILL_EPISODES.parquet", index=False)
    episode_sensitivity_frame.to_csv(WORK / "EPISODE_SENSITIVITY.csv", index=False)
    controls.to_parquet(WORK / "NONFILL_CONTROLS.parquet", index=False)
    pairs.to_csv(WORK / "MATCHED_CONTROLS.csv", index=False)

    responses = response_rows(fills)
    responses.to_csv(WORK / "FILL_INTERACTION_ATLAS.csv", index=False)
    responses.to_csv(WORK / "TRADE_STATE_RESPONSE.csv", index=False)

    replenishment = (
        fills.groupby(["split", "failed_replenish_80_30s"], dropna=False)
        .agg(
            n=("group_id", "size"),
            mean_signed_move_30s=("signed_move_30s", "mean"),
            mean_signed_move_300s=("signed_move_300s", "mean"),
            mean_markout_30s=("aggressor_markout_30s", "mean"),
            mean_flow_persistence_30s=("flow_persistence_30s", "mean"),
            median_replenish_80_ms=("replenish_80_ms", "median"),
        )
        .reset_index()
    )
    replenishment.to_csv(WORK / "REPLENISHMENT_ATLAS.csv", index=False)

    toxicity = (
        fills.groupby(["split", "economic_exact", "failed_replenish_80_30s"], dropna=False)
        .agg(
            n=("group_id", "size"),
            mean_effective_spread=("effective_spread", "mean"),
            mean_realized_spread_30s=("realized_spread_30s", "mean"),
            mean_realized_spread_300s=("realized_spread_300s", "mean"),
            mean_aggressor_markout_30s=("aggressor_markout_30s", "mean"),
            mean_aggressor_markout_300s=("aggressor_markout_300s", "mean"),
        )
        .reset_index()
    )
    toxicity.to_csv(WORK / "TOXICITY_ATLAS.csv", index=False)

    train = fills[fills["split"] == "TRAIN"].copy()
    dev = fills[fills["split"] == "DEV"].copy()
    base_features = ["mid", "spread", "log_depth_2c", "activity_60", "signed_pre_move_30"]
    models = {
        "STATE_BASELINE": fit_ols(train, dev, base_features, "signed_move_30s"),
        "OFI": fit_ols(train, dev, base_features + ["signed_ofi_60"], "signed_move_30s"),
        "MICROPRICE": fit_ols(train, dev, base_features + ["signed_microprice_deviation"], "signed_move_30s"),
        "FLOW": fit_ols(train, dev, base_features + ["signed_flow_with_trade_60", "vpin_60"], "signed_move_30s"),
        "VENUE_ABS_SIZE": fit_ols(train, dev, base_features + ["log_venue_size"], "signed_move_30s"),
        "VENUE_REL_SIZE": fit_ols(train, dev, base_features + ["log_venue_size_over_touch"], "signed_move_30s"),
    }
    exact_train = train[train["economic_exact"].astype(bool)]
    exact_dev = dev[dev["economic_exact"].astype(bool)]
    models["EPISODE_ABS_SIZE_EXACT"] = fit_ols(
        exact_train, exact_dev, base_features + ["log_episode_size"], "signed_move_30s"
    )
    models["EPISODE_REL_SIZE_EXACT"] = fit_ols(
        exact_train, exact_dev, base_features + ["log_episode_size_over_touch"], "signed_move_30s"
    )
    tradfi_rows = []
    for name, result in models.items():
        tradfi_rows.append({"model": name, **result})
    pd.DataFrame(tradfi_rows).to_csv(WORK / "TRADFI_CHALLENGERS.csv", index=False)

    matched_stats = {
        "TRAIN_30S": cluster_bootstrap_mean(
            pairs[pairs["split"] == "TRAIN"], "delta_signed_move_30s"
        ) if "delta_signed_move_30s" in pairs.columns else {"n": 0},
        "DEV_30S": cluster_bootstrap_mean(
            pairs[pairs["split"] == "DEV"], "delta_signed_move_30s"
        ) if "delta_signed_move_30s" in pairs.columns else {"n": 0},
        "TRAIN_300S": cluster_bootstrap_mean(
            pairs[pairs["split"] == "TRAIN"], "delta_signed_move_300s"
        ) if "delta_signed_move_300s" in pairs.columns else {"n": 0},
        "DEV_300S": cluster_bootstrap_mean(
            pairs[pairs["split"] == "DEV"], "delta_signed_move_300s"
        ) if "delta_signed_move_300s" in pairs.columns else {"n": 0},
    }

    dev_repl = dev.groupby("failed_replenish_80_30s")["signed_move_30s"].agg(["count", "mean"])
    repl_gap = math.nan
    if True in dev_repl.index and False in dev_repl.index:
        repl_gap = float(dev_repl.loc[True, "mean"] - dev_repl.loc[False, "mean"])

    survivors = []
    rel = models.get("EPISODE_REL_SIZE_EXACT", {})
    abs_model = models.get("EPISODE_ABS_SIZE_EXACT", {})
    if (
        rel.get("status") == "OK"
        and abs_model.get("status") == "OK"
        and float(rel.get("dev_r2", -999)) > float(abs_model.get("dev_r2", -999))
        and float(rel.get("dev_r2", -999)) > 0
    ):
        survivors.append(
            {
                "candidate_id": "005H-C01-RELATIVE-SIZE",
                "mechanism": "episode size normalized by visible touch liquidity",
                "gate": "DEV R2 positive and exceeds absolute-size model on exact-economics subset",
                "send_to": ["SEND_TO_MM_REPLAY", "SEND_TO_005I"],
            }
        )
    if math.isfinite(repl_gap) and repl_gap > 0:
        survivors.append(
            {
                "candidate_id": "005H-C02-FAILED-REPLENISHMENT",
                "mechanism": "failure to recover 80% of pre-trade consumed-side 2c depth within 30s",
                "dev_signed_move_gap": repl_gap,
                "send_to": ["SEND_TO_MM_REPLAY", "SEND_TO_LIVE_DIAG", "SEND_TO_SHADOW"],
            }
        )
    dev_pair = matched_stats.get("DEV_30S", {})
    if dev_pair.get("n", 0) >= 50 and float(dev_pair.get("ci_2_5", -999)) > 0:
        survivors.append(
            {
                "candidate_id": "005H-C03-FILL-BEYOND-STATE",
                "mechanism": "fill episodes move further in aggressor direction than matched non-fill states",
                "matched_control_dev": dev_pair,
                "send_to": ["SEND_TO_005I", "SEND_TO_LIVE_DIAG"],
            }
        )

    freeze = {
        "schema_version": 1,
        "experiment": "EXPERIMENT-005H",
        "stage": "V3_CORE_SHORTLIST_FREEZE",
        "train_window": "W17",
        "dev_window": "W18",
        "final_holdout": "B0",
        "b0_opened": False,
        "survivors": survivors,
        "model_diagnostics": models,
        "matched_control_diagnostics": matched_stats,
        "replay_audits": replay_audits,
        "rule": "This freezes only V3-core candidates. It is not the experiment-wide pre-holdout freeze and does not authorize B0 access.",
        "real_sig_orders_sent": False,
    }
    (WORK / "V3_CORE_SHORTLIST_FREEZE.json").write_text(json.dumps(freeze, indent=2, sort_keys=True) + "\n")

    summary = {
        "experiment": "EXPERIMENT-005H",
        "stage": "V3_DISCOVERY_AND_FALSIFICATION",
        "fill_rows": int(len(fills)),
        "train_rows": int((fills["split"] == "TRAIN").sum()),
        "dev_rows": int((fills["split"] == "DEV").sum()),
        "exact_economics_rows": int(fills["economic_exact"].sum()),
        "control_rows": int(len(controls)),
        "matched_pairs": int(len(pairs)),
        "fill_episodes_10s": int(len(episodes)),
        "survivors": survivors,
        "matched_control_diagnostics": matched_stats,
        "models": models,
        "b0_opened": False,
        "real_sig_orders_sent": False,
    }
    (WORK / "V3_DISCOVERY_SUMMARY.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    (WORK / "V3_DISCOVERY_REPORT.md").write_text(
        "# EXPERIMENT-005H — V3 Discovery / Falsification\n\n"
        f"- TRAIN W17 fill episodes: **{summary['train_rows']:,}**\n"
        f"- DEV W18 fill episodes: **{summary['dev_rows']:,}**\n"
        f"- Exact price+size economics: **{summary['exact_economics_rows']:,}**\n"
        f"- Matched non-fill controls: **{summary['matched_pairs']:,}**\n"
        f"- Frozen survivors before B0: **{len(survivors)}**\n"
        "- B0 opened: **NO**\n\n"
        "This run uses exact transaction-hash identity, strict V3 receive-time+sequence chronology, "
        "and reconstructed full depth. No result is promoted to live execution from this stage.\n\n"
        "REAL SIG ORDERS SENT: NO\n"
    )
    print(json.dumps(summary, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
