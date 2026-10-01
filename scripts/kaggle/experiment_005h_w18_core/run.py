# ruff: noqa
from __future__ import annotations

import json
import math
from collections import defaultdict, deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

WORK = Path("/kaggle/working")
ORDERBOOK_SLUG = "sig-cup-data003-orderbooks"
WINDOW = "ev18_ok_sc_runoff_ga_runoff"
FILL_WINDOW = "W18"
CLOCK_HORIZONS_S = (1, 5, 15, 30, 60, 300)
EVENT_HORIZONS = (1, 2, 5, 10, 25, 50)
RECOVERY_LEVELS = (0.25, 0.50, 0.90)
SIZE_TOLERANCE = 1e-8
YES_NOTIONAL_TOLERANCE = 1e-3
TICK = 0.01


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


def ns_series(values: pd.Series) -> np.ndarray:
    received = pd.to_datetime(values, utc=True)
    return received.to_numpy(dtype="datetime64[ns]").astype("int64")


def level_pairs(levels: Any) -> list[tuple[float, float]]:
    if levels is None:
        return []
    out: list[tuple[float, float]] = []
    try:
        for level in levels:
            if isinstance(level, dict):
                p, s = float(level["price"]), float(level["size"])
            else:
                p, s = float(level[0]), float(level[1])
            if math.isfinite(p) and math.isfinite(s) and s > 0:
                out.append((p, s))
    except (TypeError, ValueError, KeyError):
        return []
    return out


def locate_orderbook_root() -> Path:
    roots = [
        path for path in Path("/kaggle/input").rglob(ORDERBOOK_SLUG)
        if path.is_dir() and (path / "_manifests").is_dir()
    ]
    if len(roots) != 1:
        raise RuntimeError(f"expected one orderbook root, found {roots}")
    return roots[0]


def locate_fill_files() -> list[Path]:
    files = sorted(
        path for path in Path("/kaggle/input").rglob("*.parquet")
        if "fills" in path.parts
        and f"window_id={FILL_WINDOW}" in path.parts
        and "fees" not in path.parts
        and "rebates" not in path.parts
        and "unattributed_fee_legs" not in path.parts
    )
    if not files:
        raise RuntimeError("no DATA-003 W18 fill parquet files found")
    return files


def accepted_groups(files: list[Path]) -> tuple[pd.DataFrame, dict[str, Any]]:
    frames = [pq.ParquetFile(path).read().to_pandas() for path in files]
    df = pd.concat(frames, ignore_index=True)
    df["tx_hash"] = df["tx_hash"].map(normalize_tx_hash)
    df["token_id"] = df["token_id"].astype(str)
    df["condition_id"] = df["condition_id"].astype(str)
    df["timestamp"] = pd.to_numeric(df["timestamp"], errors="raise").astype("int64")
    df["log_index"] = pd.to_numeric(df["log_index"], errors="raise").astype("int64")
    df["price"] = pd.to_numeric(df["price"], errors="raise").astype(float)
    df["size_shares"] = pd.to_numeric(df["size_shares"], errors="raise").astype(float)
    df["is_active"] = df["order_is_match_taker_order"].astype(bool)
    df["outcome_side_norm"] = df["outcome_side"].astype(str).str.upper()

    groups: list[dict[str, Any]] = []
    rejected: defaultdict[str, int] = defaultdict(int)
    grouped = df.groupby(["condition_id", "tx_hash"], sort=False)
    for (condition_id, tx_hash), rows in grouped:
        active = rows[rows["is_active"]]
        passive = rows[~rows["is_active"]]
        if len(active) != 1:
            rejected["active_count_not_one"] += 1
            continue
        if passive.empty:
            rejected["no_passive"] += 1
            continue
        if not rows["outcome_side_norm"].isin(["YES", "NO"]).all():
            rejected["non_binary"] += 1
            continue
        a = active.iloc[0]
        active_size = float(a["size_shares"])
        passive_size = float(passive["size_shares"].sum())
        size_limit = SIZE_TOLERANCE * max(1.0, abs(active_size), abs(passive_size))
        if abs(active_size - passive_size) > size_limit:
            rejected["size_conservation"] += 1
            continue
        active_p_yes = (
            float(a["price"])
            if a["outcome_side_norm"] == "YES"
            else 1.0 - float(a["price"])
        )
        active_yes_notional = active_p_yes * active_size
        passive_yes_notional = 0.0
        for _, p in passive.iterrows():
            p_yes = (
                float(p["price"])
                if p["outcome_side_norm"] == "YES"
                else 1.0 - float(p["price"])
            )
            passive_yes_notional += p_yes * float(p["size_shares"])
        yes_limit = YES_NOTIONAL_TOLERANCE * max(
            1.0, abs(active_yes_notional), abs(passive_yes_notional)
        )
        if abs(active_yes_notional - passive_yes_notional) > yes_limit:
            rejected["yes_notional_conservation"] += 1
            continue
        groups.append(
            {
                "group_id": f"{condition_id}|{tx_hash}",
                "condition_id": condition_id,
                "tx_hash": tx_hash,
                "block_timestamp_s": int(a["timestamp"]),
                "token_id": str(a["token_id"]),
                "onchain_price": float(a["price"]),
                "onchain_size": active_size,
                "onchain_outcome": str(a["outcome_side_norm"]),
                "mapping_class": str(a.get("mapping_class") or ""),
                "mapping_direction": str(a.get("mapping_direction") or ""),
                "sig_market_id": str(a.get("sig_market_id") or ""),
            }
        )
    out = pd.DataFrame(groups)
    return out, {
        "participant_rows": int(len(df)),
        "groups": int(grouped.ngroups),
        "accepted_groups": int(len(out)),
        "rejected_groups": int(grouped.ngroups - len(out)),
        "failure_counts": dict(rejected),
    }


@dataclass
class BookState:
    bids: dict[float, float] = field(default_factory=dict)
    asks: dict[float, float] = field(default_factory=dict)
    best_bid: float | None = None
    best_ask: float | None = None
    last_key: tuple[int, int] | None = None
    last_full_book_ns: int | None = None
    update_times: deque[int] = field(default_factory=deque)
    mid_history: deque[tuple[int, float]] = field(default_factory=deque)
    trade_history: deque[tuple[int, int, float]] = field(default_factory=deque)
    previous_trade_side: int = 0
    same_side_run: int = 0

    def valid_bbo(self) -> bool:
        return (
            self.best_bid is not None
            and self.best_ask is not None
            and 0.0 < self.best_bid <= self.best_ask < 1.0
        )

    def mid(self) -> float | None:
        return (
            (float(self.best_bid) + float(self.best_ask)) / 2.0
            if self.valid_bbo()
            else None
        )

    def spread(self) -> float | None:
        return (
            float(self.best_ask) - float(self.best_bid)
            if self.valid_bbo()
            else None
        )

    def clean_old(self, now_ns: int) -> None:
        cutoff = now_ns - 60_000_000_000
        while self.update_times and self.update_times[0] < cutoff:
            self.update_times.popleft()
        while self.mid_history and self.mid_history[0][0] < cutoff:
            self.mid_history.popleft()
        while self.trade_history and self.trade_history[0][0] < cutoff:
            self.trade_history.popleft()

    def apply_book(self, rec: dict[str, Any], key: tuple[int, int]) -> None:
        self.bids = {p: s for p, s in level_pairs(rec.get("bids"))}
        self.asks = {p: s for p, s in level_pairs(rec.get("asks"))}
        self.best_bid = max(self.bids) if self.bids else None
        self.best_ask = min(self.asks) if self.asks else None
        self.last_full_book_ns = key[0]
        self._mark_update(key)

    def apply_price_change(self, rec: dict[str, Any], key: tuple[int, int]) -> None:
        try:
            price = float(rec.get("price"))
            size = float(rec.get("size"))
        except (TypeError, ValueError):
            return
        side = str(rec.get("side") or "").upper()
        levels = self.bids if side == "BUY" else self.asks if side == "SELL" else None
        if levels is not None and math.isfinite(price) and math.isfinite(size):
            if size <= 0:
                levels.pop(price, None)
            else:
                levels[price] = size
        try:
            bb = float(rec.get("best_bid"))
            ba = float(rec.get("best_ask"))
            if 0.0 < bb <= ba < 1.0:
                self.best_bid, self.best_ask = bb, ba
            else:
                raise ValueError
        except (TypeError, ValueError):
            self.best_bid = max(self.bids) if self.bids else self.best_bid
            self.best_ask = min(self.asks) if self.asks else self.best_ask
        self._mark_update(key)

    def _mark_update(self, key: tuple[int, int]) -> None:
        self.last_key = key
        self.update_times.append(key[0])
        mid = self.mid()
        if mid is not None:
            if not self.mid_history or self.mid_history[-1][1] != mid:
                self.mid_history.append((key[0], mid))
        self.clean_old(key[0])

    def depth_metrics(self, aggressor_sign: int) -> dict[str, float]:
        consumed = self.asks if aggressor_sign > 0 else self.bids
        opposite = self.bids if aggressor_sign > 0 else self.asks
        touch = self.best_ask if aggressor_sign > 0 else self.best_bid
        if touch is None:
            return {
                "same_top_depth": math.nan,
                "same_depth_1c": math.nan,
                "same_depth_2c": math.nan,
                "same_depth_5c": math.nan,
                "opposite_top_depth": math.nan,
                "total_bid_depth": float(sum(self.bids.values())),
                "total_ask_depth": float(sum(self.asks.values())),
            }
        top_depth = float(consumed.get(float(touch), 0.0))
        opp_touch = self.best_bid if aggressor_sign > 0 else self.best_ask
        opp_top = (
            float(opposite.get(float(opp_touch), 0.0))
            if opp_touch is not None
            else math.nan
        )
        def within(delta: float) -> float:
            if aggressor_sign > 0:
                return float(sum(s for p, s in consumed.items() if p <= touch + delta + 1e-12))
            return float(sum(s for p, s in consumed.items() if p >= touch - delta - 1e-12))
        return {
            "same_top_depth": top_depth,
            "same_depth_1c": within(0.01),
            "same_depth_2c": within(0.02),
            "same_depth_5c": within(0.05),
            "opposite_top_depth": opp_top,
            "total_bid_depth": float(sum(self.bids.values())),
            "total_ask_depth": float(sum(self.asks.values())),
        }

    def recent_features(self, now_ns: int) -> dict[str, float]:
        self.clean_old(now_ns)
        trades = list(self.trade_history)
        updates = list(self.update_times)
        mids = [x[1] for x in self.mid_history]
        def recent_count(seconds: int) -> int:
            cutoff = now_ns - seconds * 1_000_000_000
            return sum(ts >= cutoff for ts, _, _ in trades)
        def signed_volume(seconds: int) -> float:
            cutoff = now_ns - seconds * 1_000_000_000
            return float(sum(sign * size for ts, sign, size in trades if ts >= cutoff))
        vol = 0.0
        if len(mids) >= 2:
            diffs = np.diff(np.asarray(mids, dtype=float))
            vol = float(np.sqrt(np.sum(diffs * diffs)))
        bid_total = float(sum(self.bids.values()))
        ask_total = float(sum(self.asks.values()))
        denom = bid_total + ask_total
        imbalance = (bid_total - ask_total) / denom if denom > 0 else math.nan
        if self.best_bid is not None and self.best_ask is not None:
            bid_top = float(self.bids.get(float(self.best_bid), 0.0))
            ask_top = float(self.asks.get(float(self.best_ask), 0.0))
            td = bid_top + ask_top
            microprice = (
                (self.best_ask * bid_top + self.best_bid * ask_top) / td
                if td > 0 else math.nan
            )
        else:
            microprice = math.nan
        return {
            "updates_60s": float(len(updates)),
            "trades_5s": float(recent_count(5)),
            "trades_15s": float(recent_count(15)),
            "trades_60s": float(recent_count(60)),
            "signed_volume_15s": signed_volume(15),
            "signed_volume_60s": signed_volume(60),
            "rv_mid_60s": vol,
            "book_imbalance": imbalance,
            "microprice": microprice,
        }


def depth_ratio(size: float, depth: float) -> float:
    return size / depth if math.isfinite(depth) and depth > 0 else math.nan


def price_region(price: float) -> str:
    edges = [0, .05, .15, .30, .45, .55, .70, .85, .95, 1.000001]
    labels = ["0-5","5-15","15-30","30-45","45-55","55-70","70-85","85-95","95-100"]
    for lo, hi, label in zip(edges[:-1], edges[1:], labels, strict=True):
        if lo <= price < hi:
            return label
    return "UNKNOWN"


def process() -> tuple[pd.DataFrame, dict[str, Any]]:
    groups, group_audit = accepted_groups(locate_fill_files())
    group_by_hash = {
        str(row["tx_hash"]): row
        for row in groups.to_dict(orient="records")
    }
    target_tokens = set(groups["token_id"].astype(str))
    root = locate_orderbook_root()
    files = sorted((root / WINDOW).rglob("*.parquet"))
    if len(files) != 96:
        raise RuntimeError(f"expected 96 W18 files, found {len(files)}")
    token_bytes = {int(t).to_bytes(32, "big") for t in target_tokens}
    states: defaultdict[str, BookState] = defaultdict(BookState)
    events: list[dict[str, Any]] = []
    pending: defaultdict[str, list[int]] = defaultdict(list)
    seen_hashes: defaultdict[str, int] = defaultdict(int)
    counts: defaultdict[str, int] = defaultdict(int)

    wanted = [
        "event_type","timestamp_received","sequence","market","asset_id",
        "best_bid","best_ask","bids","asks","price","size","side",
        "transaction_hash",
    ]
    for file_no, path in enumerate(files, start=1):
        pf = pq.ParquetFile(path)
        available = [c for c in wanted if c in pf.schema_arrow.names]
        frame = pf.read(columns=available).to_pandas()
        counts["raw_rows"] += len(frame)
        if frame.empty:
            continue
        frame = frame[frame["asset_id"].isin(token_bytes)].copy()
        counts["mapped_rows"] += len(frame)
        if frame.empty:
            continue
        frame = frame[
            frame["event_type"].astype(str).isin(
                ["book","price_change","last_trade_price"]
            )
        ].copy()
        if frame.empty:
            continue
        frame["ts_ns"] = ns_series(frame["timestamp_received"])
        frame["sequence"] = pd.to_numeric(frame["sequence"], errors="raise").astype("uint64")
        frame.sort_values(["ts_ns","sequence"], kind="stable", inplace=True)

        for rec in frame.to_dict(orient="records"):
            token = bytes_to_token(rec["asset_id"])
            state = states[token]
            ts_ns = int(rec["ts_ns"])
            seq = int(rec["sequence"])
            key = (ts_ns, seq)
            kind = str(rec["event_type"])

            if kind in {"book","price_change"}:
                if kind == "book":
                    state.apply_book(rec, key)
                    counts["book_rows"] += 1
                else:
                    state.apply_price_change(rec, key)
                    counts["price_change_rows"] += 1
                mid_now = state.mid()
                spread_now = state.spread()
                for idx in list(pending[token]):
                    event = events[idx]
                    if ts_ns <= event["trade_ts_ns"]:
                        continue
                    event["post_bbo_events_seen"] += 1
                    ec = int(event["post_bbo_events_seen"])
                    if ec in EVENT_HORIZONS and mid_now is not None:
                        event[f"mid_e{ec}"] = mid_now
                        event[f"spread_e{ec}"] = spread_now
                    for h in CLOCK_HORIZONS_S:
                        field = f"mid_{h}s"
                        if event.get(f"frozen_{h}s", False):
                            continue
                        target = int(event["trade_ts_ns"]) + h * 1_000_000_000
                        if ts_ns <= target:
                            if mid_now is not None:
                                event[f"asof_mid_{h}s"] = mid_now
                                event[f"asof_spread_{h}s"] = spread_now
                        else:
                            event[field] = event.get(f"asof_mid_{h}s", event["pre_mid"])
                            event[f"spread_{h}s"] = event.get(
                                f"asof_spread_{h}s", event["pre_spread"]
                            )
                            event[f"frozen_{h}s"] = True
                    sign = int(event["aggressor_sign"])
                    depths = state.depth_metrics(sign)
                    pre_depth = float(event.get("same_depth_1c", math.nan))
                    cur_depth = float(depths["same_depth_1c"])
                    if math.isfinite(pre_depth) and pre_depth > 0 and math.isfinite(cur_depth):
                        if "first_post_depth_1c" not in event:
                            event["first_post_depth_1c"] = cur_depth
                            event["depth_lost_1c"] = pre_depth - cur_depth
                        ratio = cur_depth / pre_depth
                        for level in RECOVERY_LEVELS:
                            name = f"recovery_{int(level*100)}_ms"
                            if name not in event and ratio >= level:
                                event[name] = (ts_ns - event["trade_ts_ns"]) / 1e6
                    if (
                        event["post_bbo_events_seen"] >= max(EVENT_HORIZONS)
                        and all(event.get(f"frozen_{h}s", False) for h in CLOCK_HORIZONS_S)
                    ):
                        pending[token].remove(idx)
                continue

            if kind != "last_trade_price":
                continue
            counts["trade_rows"] += 1
            try:
                trade_price = float(rec["price"])
                trade_size = float(rec["size"])
            except (TypeError, ValueError):
                continue
            side = str(rec.get("side") or "").upper()
            sign = 1 if side == "BUY" else -1 if side == "SELL" else 0
            tx_hash = normalize_tx_hash(rec.get("transaction_hash"))
            state.clean_old(ts_ns)

            if tx_hash in group_by_hash and token == str(group_by_hash[tx_hash]["token_id"]):
                seen_hashes[tx_hash] += 1
                g = group_by_hash[tx_hash]
                if sign == 0 or not state.valid_bbo() or state.last_key is None:
                    counts["accepted_missing_prestate_or_side"] += 1
                else:
                    recent = state.recent_features(ts_ns)
                    depths = state.depth_metrics(sign)
                    pre_mid = float(state.mid())
                    pre_spread = float(state.spread())
                    last_update_ns = int(state.last_key[0])
                    full_age_ms = (
                        (ts_ns - state.last_full_book_ns) / 1e6
                        if state.last_full_book_ns is not None
                        else math.nan
                    )
                    same_time = last_update_ns == ts_ns
                    rec_out: dict[str, Any] = {
                        "event_id": f"{tx_hash}|{token}|{seq}",
                        "group_id": g["group_id"],
                        "tx_hash": tx_hash,
                        "condition_id": g["condition_id"],
                        "sig_market_id": g["sig_market_id"],
                        "mapping_class": g["mapping_class"],
                        "mapping_direction": g["mapping_direction"],
                        "token_id": token,
                        "trade_ts_ns": ts_ns,
                        "trade_sequence": seq,
                        "block_timestamp_s": int(g["block_timestamp_s"]),
                        "trade_minus_block_s": (
                            ts_ns - int(g["block_timestamp_s"]) * 1_000_000_000
                        ) / 1e9,
                        "aggressor_side": side,
                        "aggressor_sign": sign,
                        "fill_price": trade_price,
                        "fill_size": trade_size,
                        "onchain_price": float(g["onchain_price"]),
                        "onchain_size": float(g["onchain_size"]),
                        "pre_bid": float(state.best_bid),
                        "pre_ask": float(state.best_ask),
                        "pre_mid": pre_mid,
                        "pre_spread": pre_spread,
                        "book_age_ms": (ts_ns - last_update_ns) / 1e6,
                        "full_book_age_ms": full_age_ms,
                        "same_receive_time_prebook": same_time,
                        "pre_same_side_run": int(
                            state.same_side_run if state.previous_trade_side == sign else 0
                        ),
                        "post_bbo_events_seen": 0,
                        "price_region": price_region(trade_price),
                        **depths,
                        **recent,
                    }
                    rec_out["size_over_top_depth"] = depth_ratio(
                        trade_size, float(depths["same_top_depth"])
                    )
                    for cents in (1,2,5):
                        rec_out[f"size_over_depth_{cents}c"] = depth_ratio(
                            trade_size, float(depths[f"same_depth_{cents}c"])
                        )
                    for h in CLOCK_HORIZONS_S:
                        rec_out[f"asof_mid_{h}s"] = pre_mid
                        rec_out[f"asof_spread_{h}s"] = pre_spread
                        rec_out[f"frozen_{h}s"] = False
                    idx = len(events)
                    events.append(rec_out)
                    pending[token].append(idx)
                    counts["accepted_events"] += 1

            state.trade_history.append((ts_ns, sign, trade_size))
            if sign != 0:
                if sign == state.previous_trade_side:
                    state.same_side_run += 1
                else:
                    state.same_side_run = 1
                    state.previous_trade_side = sign
            state.clean_old(ts_ns)

        print(json.dumps({
            "file": file_no,
            "of": len(files),
            "accepted_events": counts["accepted_events"],
            "mapped_rows": counts["mapped_rows"],
        }), flush=True)

    if not events:
        raise RuntimeError("zero canonical W18 fill events")
    end_ns = max(
        (state.last_key[0] for state in states.values() if state.last_key is not None),
        default=0,
    )
    for token, indices in pending.items():
        for idx in indices:
            event = events[idx]
            for h in CLOCK_HORIZONS_S:
                target = int(event["trade_ts_ns"]) + h * 1_000_000_000
                if not event.get(f"frozen_{h}s", False) and target <= end_ns:
                    event[f"mid_{h}s"] = event.get(f"asof_mid_{h}s", event["pre_mid"])
                    event[f"spread_{h}s"] = event.get(
                        f"asof_spread_{h}s", event["pre_spread"]
                    )
                    event[f"frozen_{h}s"] = True

    frame = pd.DataFrame(events)
    for h in CLOCK_HORIZONS_S:
        mid = pd.to_numeric(frame.get(f"mid_{h}s"), errors="coerce")
        frame[f"signed_impact_{h}s"] = (
            frame["aggressor_sign"].astype(float) * (mid - frame["pre_mid"].astype(float))
        )
        frame[f"signed_markout_{h}s"] = (
            frame["aggressor_sign"].astype(float) * (mid - frame["fill_price"].astype(float))
        )
    for e in EVENT_HORIZONS:
        mid = pd.to_numeric(frame.get(f"mid_e{e}"), errors="coerce")
        frame[f"signed_impact_e{e}"] = (
            frame["aggressor_sign"].astype(float) * (mid - frame["pre_mid"].astype(float))
        )
    for field in [c for c in frame.columns if c.startswith("frozen_") or c.startswith("asof_")]:
        del frame[field]

    frame.sort_values(["trade_ts_ns","trade_sequence"], inplace=True, ignore_index=True)
    for token, idxs in frame.groupby("token_id", sort=False).groups.items():
        ordered = list(idxs)
        for pos, idx in enumerate(ordered):
            if pos + 1 < len(ordered):
                nxt = ordered[pos+1]
                frame.at[idx, "next_fill_dt_s"] = (
                    int(frame.at[nxt, "trade_ts_ns"]) - int(frame.at[idx, "trade_ts_ns"])
                ) / 1e9
                frame.at[idx, "next_fill_same_side"] = float(
                    int(frame.at[nxt, "aggressor_sign"]) == int(frame.at[idx, "aggressor_sign"])
                )

    duplicate_hashes = sum(v > 1 for v in seen_hashes.values())
    audit = {
        "group_audit": group_audit,
        "accepted_hashes_seen": int(len(seen_hashes)),
        "accepted_events": int(len(frame)),
        "accepted_hashes_with_multiple_venue_prints": int(duplicate_hashes),
        "accepted_hashes_missing": int(len(group_by_hash) - len(seen_hashes)),
        "counts": dict(counts),
        "clock_horizons_s": list(CLOCK_HORIZONS_S),
        "event_horizons": list(EVENT_HORIZONS),
        "prebook_rule": (
            "state after the last V3 event strictly earlier in "
            "(timestamp_received_ns, sequence); equal receive time is allowed only "
            "when sequence proves the book update came first"
        ),
        "clock_outcome_rule": "last observable BBO state at or before the horizon",
        "real_sig_orders_sent": False,
    }
    return frame, audit


def assign_episodes(frame: pd.DataFrame, threshold_s: float = 10.0) -> pd.DataFrame:
    out = frame.copy()
    episode_ids = [""] * len(out)
    episode_no = 0
    for token, idxs in out.groupby("token_id", sort=False).groups.items():
        prev_idx: int | None = None
        for idx in idxs:
            new_episode = True
            if prev_idx is not None:
                dt = (
                    int(out.at[idx, "trade_ts_ns"])
                    - int(out.at[prev_idx, "trade_ts_ns"])
                ) / 1e9
                same_side = (
                    int(out.at[idx, "aggressor_sign"])
                    == int(out.at[prev_idx, "aggressor_sign"])
                )
                new_episode = not (same_side and dt <= threshold_s)
            if new_episode:
                episode_no += 1
            episode_ids[idx] = f"W18E{episode_no:06d}"
            prev_idx = idx
    out["episode_id"] = episode_ids
    return out


def aggregate_episodes(frame: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for episode_id, g in frame.groupby("episode_id", sort=False):
        first = g.iloc[0]
        last = g.iloc[-1]
        row = {
            "episode_id": episode_id,
            "token_id": first["token_id"],
            "aggressor_side": first["aggressor_side"],
            "fills": int(len(g)),
            "total_size": float(g["fill_size"].sum()),
            "duration_s": (
                int(last["trade_ts_ns"]) - int(first["trade_ts_ns"])
            ) / 1e9,
            "pre_mid": float(first["pre_mid"]),
            "pre_spread": float(first["pre_spread"]),
            "pre_same_side_run": int(first["pre_same_side_run"]),
            "size_over_depth_1c_sum": float(
                pd.to_numeric(g["size_over_depth_1c"], errors="coerce").sum()
            ),
        }
        for h in CLOCK_HORIZONS_S:
            vals = pd.to_numeric(g[f"signed_markout_{h}s"], errors="coerce").dropna()
            row[f"last_fill_signed_markout_{h}s"] = (
                float(g.iloc[-1][f"signed_markout_{h}s"])
                if pd.notna(g.iloc[-1][f"signed_markout_{h}s"])
                else math.nan
            )
            row[f"mean_fill_signed_markout_{h}s"] = (
                float(vals.mean()) if not vals.empty else math.nan
            )
        rows.append(row)
    return pd.DataFrame(rows)


def qcut_safe(series: pd.Series, q: int = 5) -> pd.Series:
    x = pd.to_numeric(series, errors="coerce")
    valid = x.notna() & np.isfinite(x)
    result = pd.Series("MISSING", index=series.index, dtype="object")
    if valid.sum() < q * 5:
        return result
    try:
        result.loc[valid] = pd.qcut(
            x.loc[valid], q=q, labels=[f"Q{i+1}" for i in range(q)], duplicates="drop"
        ).astype(str)
    except ValueError:
        pass
    return result


def conditional_rows(frame: pd.DataFrame) -> pd.DataFrame:
    work = frame.copy()
    work["size_q"] = qcut_safe(work["fill_size"])
    work["size_depth_q"] = qcut_safe(work["size_over_depth_1c"])
    work["book_age_q"] = qcut_safe(work["book_age_ms"])
    work["failed_replenish_50_60s"] = (
        pd.to_numeric(work.get("recovery_50_ms"), errors="coerce").isna()
        | (pd.to_numeric(work.get("recovery_50_ms"), errors="coerce") > 60_000)
    )
    rows: list[dict[str, Any]] = []
    dimensions = [
        "size_q","size_depth_q","price_region","aggressor_side",
        "failed_replenish_50_60s","book_age_q",
    ]
    for dim in dimensions:
        for value, g in work.groupby(dim, dropna=False):
            rec: dict[str, Any] = {
                "dimension": dim,
                "bucket": str(value),
                "fills": int(len(g)),
                "tokens": int(g["token_id"].nunique()),
            }
            for h in (5,15,60,300):
                y = pd.to_numeric(g[f"signed_markout_{h}s"], errors="coerce").dropna()
                rec[f"markout_{h}s_mean"] = float(y.mean()) if not y.empty else math.nan
                rec[f"markout_{h}s_median"] = float(y.median()) if not y.empty else math.nan
                rec[f"toxic_{h}s_share"] = float((y > 0).mean()) if not y.empty else math.nan
            rows.append(rec)
    return pd.DataFrame(rows)


def simple_feature_tests(frame: pd.DataFrame) -> pd.DataFrame:
    features = [
        "fill_size","size_over_top_depth","size_over_depth_1c","size_over_depth_2c",
        "size_over_depth_5c","pre_spread","book_imbalance","microprice",
        "book_age_ms","updates_60s","trades_15s","trades_60s",
        "signed_volume_15s","signed_volume_60s","rv_mid_60s","pre_same_side_run",
    ]
    targets = ["signed_markout_15s","signed_markout_60s","signed_markout_300s"]
    rows: list[dict[str, Any]] = []
    for target in targets:
        yall = pd.to_numeric(frame[target], errors="coerce")
        for feature in features:
            xall = pd.to_numeric(frame[feature], errors="coerce")
            mask = xall.notna() & yall.notna() & np.isfinite(xall) & np.isfinite(yall)
            if mask.sum() < 50:
                continue
            x = xall[mask].to_numpy(float)
            y = yall[mask].to_numpy(float)
            if np.std(x) <= 0:
                continue
            corr = float(np.corrcoef(x, y)[0,1])
            X = np.column_stack([np.ones(len(x)), x])
            beta = np.linalg.lstsq(X, y, rcond=None)[0]
            pred = X @ beta
            sst = float(np.sum((y - np.mean(y)) ** 2))
            r2 = 1.0 - float(np.sum((y - pred) ** 2)) / sst if sst > 0 else math.nan
            rows.append({
                "target": target,
                "feature": feature,
                "n": int(len(x)),
                "pearson": corr,
                "slope": float(beta[1]),
                "r2": r2,
            })
    return pd.DataFrame(rows)


def flow_persistence(frame: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for threshold in (5.0, 10.0, 30.0):
        same = []
        for token, g in frame.groupby("token_id", sort=False):
            g = g.sort_values("trade_ts_ns")
            signs = g["aggressor_sign"].astype(int).to_numpy()
            times = g["trade_ts_ns"].astype("int64").to_numpy()
            for i in range(len(g)-1):
                dt = (times[i+1]-times[i]) / 1e9
                if dt <= threshold:
                    same.append(signs[i+1] == signs[i])
        rows.append({
            "metric": "next_fill_same_side",
            "condition": f"dt_le_{threshold:g}s",
            "observations": int(len(same)),
            "value": float(np.mean(same)) if same else math.nan,
        })
    for run_bucket, g in frame.assign(
        run_bucket=pd.cut(
            frame["pre_same_side_run"],
            bins=[-1,0,1,2,4,1e9],
            labels=["0","1","2","3-4","5+"],
        )
    ).groupby("run_bucket", observed=True):
        y = pd.to_numeric(g["next_fill_same_side"], errors="coerce").dropna()
        rows.append({
            "metric": "next_fill_same_side",
            "condition": f"prior_run_{run_bucket}",
            "observations": int(len(y)),
            "value": float(y.mean()) if not y.empty else math.nan,
        })
    return pd.DataFrame(rows)


def continuation_table(frame: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for h0, h1 in [(5,60),(15,60),(15,300)]:
        a = pd.to_numeric(frame[f"signed_impact_{h0}s"], errors="coerce")
        b = pd.to_numeric(frame[f"signed_impact_{h1}s"], errors="coerce")
        mask = a.notna() & b.notna()
        if not mask.any():
            continue
        tmp = frame.loc[mask].copy()
        tmp["initial"] = a[mask]
        tmp["later"] = b[mask]
        eps = TICK / 2
        tmp["class"] = np.select(
            [
                (tmp["initial"] > eps) & (tmp["later"] >= tmp["initial"]),
                (tmp["initial"] > eps) & (tmp["later"] > eps) & (tmp["later"] < tmp["initial"]),
                (tmp["initial"] > eps) & (tmp["later"] <= eps),
                (tmp["initial"].abs() <= eps),
            ],
            ["CONTINUE","PARTIAL_REVERSE","FULL_REVERSE_OR_OVERSHOOT","FLAT_INITIAL"],
            default="OTHER",
        )
        for cls, g in tmp.groupby("class"):
            rows.append({
                "initial_horizon_s": h0,
                "later_horizon_s": h1,
                "class": cls,
                "fills": int(len(g)),
                "share": float(len(g) / len(tmp)),
                "mean_size_over_depth_1c": float(
                    pd.to_numeric(g["size_over_depth_1c"], errors="coerce").mean()
                ),
                "mean_pre_same_side_run": float(g["pre_same_side_run"].mean()),
            })
    return pd.DataFrame(rows)


def main() -> None:
    fills, audit = process()
    fills = assign_episodes(fills, threshold_s=10.0)
    episodes = aggregate_episodes(fills)
    conditional = conditional_rows(fills)
    feature_tests = simple_feature_tests(fills)
    persistence = flow_persistence(fills)
    continuation = continuation_table(fills)

    fills.to_parquet(WORK / "W18_FILL_EVENTS.parquet", index=False)
    episodes.to_parquet(WORK / "W18_FILL_EPISODES.parquet", index=False)
    conditional.to_parquet(WORK / "W18_CONDITIONAL_ATLAS.parquet", index=False)
    feature_tests.to_parquet(WORK / "W18_FEATURE_TESTS.parquet", index=False)
    persistence.to_parquet(WORK / "W18_FLOW_PERSISTENCE.parquet", index=False)
    continuation.to_parquet(WORK / "W18_REVERSAL_CONTINUATION.parquet", index=False)

    summary = {
        "experiment": "EXPERIMENT-005H",
        "stage": "W18_V3_DISCOVERY_CORE",
        "scientific_status": "DISCOVERY_ONLY_NOT_HOLDOUT",
        "audit": audit,
        "fill_events": int(len(fills)),
        "fill_episodes_10s": int(len(episodes)),
        "tokens": int(fills["token_id"].nunique()),
        "same_time_prebook_share": float(fills["same_receive_time_prebook"].mean()),
        "prebook_full_depth_share": float(
            pd.to_numeric(fills["same_depth_1c"], errors="coerce").notna().mean()
        ),
        "clock_outcome_support": {
            str(h): int(pd.to_numeric(fills[f"mid_{h}s"], errors="coerce").notna().sum())
            for h in CLOCK_HORIZONS_S
        },
        "median_trade_minus_block_s": float(fills["trade_minus_block_s"].median()),
        "feature_tests_top_abs_corr": (
            feature_tests.assign(abs_corr=feature_tests["pearson"].abs())
            .sort_values("abs_corr", ascending=False)
            .head(20)
            .drop(columns=["abs_corr"])
            .to_dict(orient="records")
        ),
        "real_sig_orders_sent": False,
    }
    (WORK / "W18_DISCOVERY_SUMMARY.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True, default=str) + "\n"
    )
    (WORK / "W18_DISCOVERY_REPORT.md").write_text(
        "# EXPERIMENT-005H — W18 V3 Discovery Core\n\n"
        "This is TRAIN/DEV-style discovery evidence only. B0 was not accessed.\n\n"
        f"- Canonical fill events: **{len(fills):,}**\n"
        f"- 10-second same-side episodes: **{len(episodes):,}**\n"
        f"- Tokens: **{fills['token_id'].nunique():,}**\n"
        f"- Median venue print minus Polygon block time: **{summary['median_trade_minus_block_s']:.3f}s**\n"
        f"- Full-depth prestate support: **{summary['prebook_full_depth_share']:.1%}**\n\n"
        "Outputs include conditional impact/toxicity tables, feature tests, flow "
        "persistence and continuation/reversal classifications. They are discovery "
        "surfaces, not production rules.\n\n"
        "REAL SIG ORDERS SENT: NO\n"
    )
    print(json.dumps(summary, sort_keys=True, default=str), flush=True)


if __name__ == "__main__":
    main()
