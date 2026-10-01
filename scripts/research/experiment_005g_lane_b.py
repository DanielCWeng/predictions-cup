# ruff: noqa
"""EXPERIMENT-005G Lane B: open V3 order-book discovery on fresh DATA-003 bytes.

Family definitions were frozen in LANE_B_DISCOVERY_DESIGN.json before fresh DEV results.
This runner uses observable timestamp_received only, backward as-of joins only, and never
downloads or reads the sealed Sep-5 HOLDOUT in TRAIN/DEV mode.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from collections import defaultdict
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.dataset as pads
from sklearn.linear_model import LogisticRegression, Ridge
from sklearn.preprocessing import StandardScaler

import experiment_005g_lane_a as lane_a

ROOT = Path(__file__).resolve().parents[2]
EXPECTED_DATASET = lane_a.EXPECTED_DATASET
SEED = 20261001006
SKLEARN_SEED = SEED % (2**32 - 1)
NS = 1_000_000_000
MAX_ROWS = 250_000
FDR_Q = 0.10
DEPTH_MAX_AGE_S = 300.0

CFG = {
    "window_id": "baseline_sep",
    "start": "2026-09-01T00:00:00Z",
    "train_end": "2026-09-04T00:00:00Z",
    "dev_end": "2026-09-05T00:00:00Z",
    "holdout_end": "2026-09-06T00:00:00Z",
    "sample_mod": 16,
}

FEATURES: dict[str, list[str]] = {
    "RENEWAL_FRESHNESS": [
        "renewal_acceleration",
        "price_change_age_s",
        "bbo_price_age_gap_s",
    ],
    "BBO_STATE": [
        "distance_from_0_5",
        "spread_x_distance",
    ],
    "DEPTH_GEOMETRY": [
        "top_imbalance",
        "depth_imbalance_5c",
        "depth_concentration_1c_5c",
        "depth_slope_1c_5c",
        "microprice_disp_over_spread",
        "snapshot_age_s",
        "buy_impact_q50",
        "sell_impact_q50",
    ],
    "FLOW_PRESSURE": [
        "ofi_15",
        "ofi_60",
        "ofi_acceleration",
    ],
    "TRADE_PRESSURE": [
        "trade_count_60",
        "trade_abs_impact_60",
    ],
    "INTERACTIONS": [
        "freshness_x_spread",
        "imbalance_x_ofi",
        "volatility_x_liquidity",
    ],
}

TARGETS: dict[str, dict[str, Any]] = {
    "price_h60": {
        "family": "PRICE",
        "kind": "regression",
        "baseline": ["ret_15", "ret_60", "spread", "rv_60"],
    },
    "abs_h60": {
        "family": "VOLATILITY",
        "kind": "regression",
        "baseline": ["abs_ret_15", "rv_60", "spread"],
    },
    "spread_h60": {
        "family": "LIQUIDITY",
        "kind": "regression",
        "baseline": ["spread", "ret_15", "rv_60"],
    },
    "depth_h60": {
        "family": "LIQUIDITY",
        "kind": "regression",
        "baseline": ["depth_5c", "spread", "top_imbalance"],
    },
    "bbo_update_h60": {
        "family": "RENEWAL",
        "kind": "classification",
        "baseline": ["bbo_age_s", "bbo_updates_60", "price_updates_60"],
    },
    "bbo_update_h300": {
        "family": "RENEWAL",
        "kind": "classification",
        "baseline": ["bbo_age_s", "bbo_updates_60", "price_updates_60"],
    },
}


def decode_asset(value: Any) -> str:
    if isinstance(value, (bytes, bytearray, memoryview)):
        return str(int.from_bytes(bytes(value), byteorder="big", signed=False))
    return str(value)


def numeric(value: Any) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return float("nan")
    return out if np.isfinite(out) else float("nan")


def levels(value: Any) -> list[tuple[float, float]]:
    if value is None:
        return []
    if hasattr(value, "tolist"):
        value = value.tolist()
    out: list[tuple[float, float]] = []
    for item in value:
        if isinstance(item, dict):
            price = numeric(item.get("price"))
            size = numeric(item.get("size"))
        elif isinstance(item, (tuple, list)) and len(item) >= 2:
            price = numeric(item[0])
            size = numeric(item[1])
        else:
            continue
        if np.isfinite(price) and np.isfinite(size) and size >= 0:
            out.append((price, size))
    return out


def impact(level_rows: list[tuple[float, float]], quantity: float, side: str) -> float:
    if not level_rows:
        return float("nan")
    ordered = (
        sorted(level_rows, key=lambda row: row[0])
        if side == "buy"
        else sorted(level_rows, key=lambda row: row[0], reverse=True)
    )
    best = ordered[0][0]
    remaining = quantity
    last = best
    for price, size in ordered:
        remaining -= size
        last = price
        if remaining <= 0:
            break
    if remaining > 0:
        return float("nan")
    return last - best if side == "buy" else best - last


def depth_row(row: dict[str, Any]) -> dict[str, Any] | None:
    bids = levels(row.get("bids"))
    asks = levels(row.get("asks"))
    if not bids or not asks:
        return None
    best_bid = max(price for price, _ in bids)
    best_ask = min(price for price, _ in asks)
    if not (0 < best_bid <= best_ask < 1):
        return None

    def side_depth(rows: list[tuple[float, float]], cents: float, bid: bool) -> float:
        if bid:
            return float(sum(size for price, size in rows if price >= best_bid - cents))
        return float(sum(size for price, size in rows if price <= best_ask + cents))

    bid_top = float(sum(size for price, size in bids if abs(price - best_bid) <= 1e-12))
    ask_top = float(sum(size for price, size in asks if abs(price - best_ask) <= 1e-12))
    top_total = bid_top + ask_top
    midpoint = (best_bid + best_ask) / 2.0
    spread = best_ask - best_bid
    micro = (
        (best_ask * bid_top + best_bid * ask_top) / top_total
        if top_total > 0
        else float("nan")
    )

    metrics: dict[str, Any] = {
        "token_id": decode_asset(row["asset_id"]),
        "depth_time": pd.Timestamp(row["timestamp_received"]),
        "depth_best_bid": best_bid,
        "depth_best_ask": best_ask,
        "top_bid_size": bid_top,
        "top_ask_size": ask_top,
        "top_imbalance": (bid_top - ask_top) / top_total if top_total > 0 else np.nan,
        "microprice_disp_over_spread": (
            (micro - midpoint) / spread if spread > 0 and np.isfinite(micro) else np.nan
        ),
        "buy_impact_q50": impact(asks, 50.0, "buy"),
        "sell_impact_q50": impact(bids, 50.0, "sell"),
    }
    for cents, label in ((0.01, "1c"), (0.02, "2c"), (0.05, "5c")):
        bid_depth = side_depth(bids, cents, True)
        ask_depth = side_depth(asks, cents, False)
        total = bid_depth + ask_depth
        metrics[f"depth_{label}"] = total
        metrics[f"depth_imbalance_{label}"] = (
            (bid_depth - ask_depth) / total if total > 0 else np.nan
        )
    d1 = metrics["depth_1c"]
    d5 = metrics["depth_5c"]
    metrics["depth_concentration_1c_5c"] = d1 / d5 if d5 > 0 else np.nan
    metrics["depth_slope_1c_5c"] = (d5 - d1) / 0.04
    return metrics


def load_v3_extras(
    inputs: list[dict[str, str]],
    tokens: set[str],
    start: pd.Timestamp,
    end: pd.Timestamp,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    depth_parts: list[pd.DataFrame] = []
    bbo_parts: list[pd.DataFrame] = []
    audit: defaultdict[str, int] = defaultdict(int)
    token_list = sorted(tokens)
    start_scalar = pa.scalar(start.to_pydatetime(), pa.timestamp("us", tz="UTC"))
    end_scalar = pa.scalar(end.to_pydatetime(), pa.timestamp("us", tz="UTC"))

    for index, item in enumerate(inputs, 1):
        path = Path(item["local"])
        dataset = pads.dataset([str(path)], format="parquet")
        asset_type = dataset.schema.field("asset_id").type
        if pa.types.is_binary(asset_type) or pa.types.is_large_binary(asset_type):
            token_filter = [
                int(token).to_bytes(32, byteorder="big", signed=False)
                for token in token_list
            ]
        else:
            token_filter = token_list

        available = set(dataset.schema.names)
        wanted = [
            "event_type",
            "timestamp_received",
            "asset_id",
            "best_bid",
            "best_ask",
            "spread",
            "bids",
            "asks",
        ]
        columns = [name for name in wanted if name in available]
        expression = (
            (pads.field("timestamp_received") >= start_scalar)
            & (pads.field("timestamp_received") < end_scalar)
            & pads.field("asset_id").isin(token_filter)
            & pads.field("event_type").isin(["book", "best_bid_ask"])
        )
        table = dataset.scanner(
            columns=columns,
            filter=expression,
            batch_size=100_000,
            use_threads=True,
        ).to_table()
        if table.num_rows == 0:
            continue
        frame = table.to_pandas()
        frame["asset_id"] = frame["asset_id"].map(decode_asset)
        frame["timestamp_received"] = pd.to_datetime(
            frame["timestamp_received"], utc=True
        )
        books = frame[frame["event_type"] == "book"]
        if not books.empty:
            rows = [depth_row(row) for row in books.to_dict(orient="records")]
            rows = [row for row in rows if row is not None]
            if rows:
                depth_parts.append(pd.DataFrame(rows))
            audit["book_rows"] += len(books)
        bbo = frame[frame["event_type"] == "best_bid_ask"].copy()
        if not bbo.empty:
            for col in ("best_bid", "best_ask", "spread"):
                bbo[col] = pd.to_numeric(bbo[col], errors="coerce")
            bbo = bbo[
                np.isfinite(bbo["best_bid"])
                & np.isfinite(bbo["best_ask"])
                & (bbo["best_bid"] > 0)
                & (bbo["best_ask"] < 1)
                & (bbo["best_bid"] <= bbo["best_ask"])
            ]
            bbo = bbo.rename(
                columns={
                    "asset_id": "token_id",
                    "timestamp_received": "bbo_time",
                }
            )
            bbo_parts.append(
                bbo[["token_id", "bbo_time", "best_bid", "best_ask", "spread"]]
            )
            audit["bbo_rows"] += len(bbo)
        if index % 24 == 0 or index == len(inputs):
            print(
                f"005G_LANE_B_EXTRA_PROGRESS files={index}/{len(inputs)} "
                f"books={audit['book_rows']} bbo={audit['bbo_rows']}",
                flush=True,
            )

    depth = (
        pd.concat(depth_parts, ignore_index=True)
        if depth_parts
        else pd.DataFrame()
    )
    bbo = (
        pd.concat(bbo_parts, ignore_index=True)
        if bbo_parts
        else pd.DataFrame()
    )
    if not depth.empty:
        depth = depth.sort_values(["token_id", "depth_time"], kind="stable")
    if not bbo.empty:
        bbo = bbo.sort_values(["token_id", "bbo_time"], kind="stable")
    audit["depth_tokens"] = int(depth["token_id"].nunique()) if not depth.empty else 0
    audit["bbo_tokens"] = int(bbo["token_id"].nunique()) if not bbo.empty else 0
    return depth, bbo, dict(audit)


def add_ofi(states: pd.DataFrame) -> pd.DataFrame:
    out_parts: list[pd.DataFrame] = []
    for _, group in states.groupby("token_id", sort=False):
        g = group.sort_values("observed_at").copy()
        prev = g.shift(1)
        same_segment = g["segment"].to_numpy() == prev["segment"].to_numpy()
        bid = g["best_bid"].to_numpy(float)
        ask = g["best_ask"].to_numpy(float)
        qbid = g["qbid"].to_numpy(float)
        qask = g["qask"].to_numpy(float)
        p_bid = prev["best_bid"].to_numpy(float)
        p_ask = prev["best_ask"].to_numpy(float)
        p_qbid = prev["qbid"].to_numpy(float)
        p_qask = prev["qask"].to_numpy(float)
        valid = (
            same_segment
            & np.isfinite(qbid)
            & np.isfinite(qask)
            & np.isfinite(p_qbid)
            & np.isfinite(p_qask)
        )
        ofi = np.full(len(g), np.nan, float)
        if np.any(valid):
            ofi[valid] = (
                np.where(bid[valid] >= p_bid[valid], qbid[valid], 0.0)
                - np.where(bid[valid] <= p_bid[valid], p_qbid[valid], 0.0)
                - np.where(ask[valid] <= p_ask[valid], qask[valid], 0.0)
                + np.where(ask[valid] >= p_ask[valid], p_qask[valid], 0.0)
            )
        g["ofi"] = ofi
        out_parts.append(g)
    return pd.concat(out_parts, ignore_index=True) if out_parts else states


def irregular_sum(
    times_ns: np.ndarray,
    values: np.ndarray,
    query_ns: np.ndarray,
    window_s: int,
) -> np.ndarray:
    vals = np.where(np.isfinite(values), values, 0.0)
    prefix = np.r_[0.0, np.cumsum(vals)]
    right = np.searchsorted(times_ns, query_ns, side="right")
    left = np.searchsorted(times_ns, query_ns - window_s * NS, side="right")
    return prefix[right] - prefix[left]


def asof_frame(
    frame: pd.DataFrame,
    time_col: str,
    query_ns: np.ndarray,
    columns: list[str],
) -> tuple[dict[str, np.ndarray], np.ndarray]:
    if frame.empty:
        return ({col: np.full(len(query_ns), np.nan) for col in columns}, np.full(len(query_ns), -1, np.int64))
    times = lane_a.datetime_ns(frame[time_col])
    index = np.searchsorted(times, query_ns, side="right") - 1
    ok = index >= 0
    out = {col: np.full(len(query_ns), np.nan, float) for col in columns}
    source_ns = np.full(len(query_ns), -1, np.int64)
    if np.any(ok):
        ii = index[ok]
        source_ns[ok] = times[ii]
        for col in columns:
            out[col][ok] = frame[col].to_numpy(float)[ii]
    return out, source_ns


def age_since_events(event_ns: np.ndarray, query_ns: np.ndarray) -> np.ndarray:
    index = np.searchsorted(event_ns, query_ns, side="right") - 1
    out = np.full(len(query_ns), np.nan, float)
    ok = index >= 0
    out[ok] = (query_ns[ok] - event_ns[index[ok]]) / NS
    return out


def time_since_flag(times: pd.DatetimeIndex, flag: np.ndarray) -> np.ndarray:
    values = times.as_unit("ns").asi8
    out = np.full(len(values), np.nan, float)
    last = -1
    for i, is_event in enumerate(flag):
        if bool(is_event):
            last = int(values[i])
        if last >= 0:
            out[i] = (int(values[i]) - last) / NS
    return out


def build_panel(
    states: pd.DataFrame,
    trades: pd.DataFrame,
    depth: pd.DataFrame,
    bbo: pd.DataFrame,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    states = add_ofi(states)
    trade_features = lane_a.trade_features(trades, states)
    start = pd.Timestamp(CFG["start"])
    train_end = pd.Timestamp(CFG["train_end"])
    dev_end = pd.Timestamp(CFG["dev_end"])
    parts: list[pd.DataFrame] = []

    for token, state in states.groupby("token_id", sort=False):
        state = state.sort_values("observed_at").reset_index(drop=True)
        if len(state) < 2:
            continue
        grid = pd.date_range(start, dev_end, freq="15s", inclusive="left", tz="UTC")
        qns = lane_a.datetime_ns(grid)
        mid, segment, state_ns = lane_a.asof_from_states(state, qns, "midpoint")
        spread, _, _ = lane_a.asof_from_states(state, qns, "spread")
        qbid, _, _ = lane_a.asof_from_states(state, qns, "qbid")
        qask, _, _ = lane_a.asof_from_states(state, qns, "qask")
        last_genuine, _, _ = lane_a.asof_from_states(state, qns, "last_genuine_ns")
        logmid = lane_a.logit_array(mid)

        frame = pd.DataFrame(
            {
                "token_id": str(token),
                "time": grid,
                "midpoint": mid,
                "spread": spread,
                "segment": segment,
                "price_change_age_s": np.where(
                    last_genuine >= 0,
                    (qns - last_genuine) / NS,
                    np.nan,
                ),
                "state_age_s": (qns - state_ns) / NS,
                "top_imbalance_pc": (qbid - qask) / np.where(
                    qbid + qask > 0, qbid + qask, np.nan
                ),
            }
        )

        for horizon in (15, 60, 300):
            past, past_segment, _ = lane_a.asof_from_states(
                state, qns - horizon * NS, "midpoint"
            )
            ret = logmid - lane_a.logit_array(past)
            ret[(past_segment != segment) | ~np.isfinite(past)] = np.nan
            frame[f"ret_{horizon}"] = ret
        frame["abs_ret_15"] = np.abs(frame["ret_15"])
        frame["rv_60"] = np.sqrt(
            frame["ret_15"].pow(2).rolling(4, min_periods=2).sum()
        )
        frame["distance_from_0_5"] = np.abs(frame["midpoint"] - 0.5)

        price_event_ns = lane_a.datetime_ns(
            state.loc[state["genuine_bbo"], "observed_at"]
        )
        frame["price_updates_15"] = lane_a.rolling_counts(
            price_event_ns, qns, 15
        )
        frame["price_updates_60"] = lane_a.rolling_counts(
            price_event_ns, qns, 60
        )
        frame["price_updates_300"] = lane_a.rolling_counts(
            price_event_ns, qns, 300
        )

        state_times = lane_a.datetime_ns(state["observed_at"])
        ofi_values = state["ofi"].to_numpy(float)
        frame["ofi_15"] = irregular_sum(state_times, ofi_values, qns, 15)
        frame["ofi_60"] = irregular_sum(state_times, ofi_values, qns, 60)
        frame["ofi_acceleration"] = frame["ofi_15"] / 15.0 - frame["ofi_60"] / 60.0

        tbbo = bbo[bbo["token_id"].astype(str) == str(token)].sort_values("bbo_time")
        if tbbo.empty:
            bbo_event_ns = np.array([], dtype=np.int64)
            frame["bbo_age_s"] = np.nan
            frame["bbo_updates_15"] = 0.0
            frame["bbo_updates_60"] = 0.0
            frame["bbo_updates_300"] = 0.0
        else:
            bbo_event_ns = lane_a.datetime_ns(tbbo["bbo_time"])
            frame["bbo_age_s"] = age_since_events(bbo_event_ns, qns)
            for horizon in (15, 60, 300):
                frame[f"bbo_updates_{horizon}"] = lane_a.rolling_counts(
                    bbo_event_ns, qns, horizon
                )
        frame["renewal_acceleration"] = (
            frame["bbo_updates_15"] / 15.0 - frame["bbo_updates_60"] / 60.0
        )
        frame["bbo_price_age_gap_s"] = frame["bbo_age_s"] - frame["price_change_age_s"]

        tdepth = depth[
            depth["token_id"].astype(str) == str(token)
        ].sort_values("depth_time")
        depth_cols = [
            "top_imbalance",
            "depth_1c",
            "depth_2c",
            "depth_5c",
            "depth_imbalance_1c",
            "depth_imbalance_2c",
            "depth_imbalance_5c",
            "depth_concentration_1c_5c",
            "depth_slope_1c_5c",
            "microprice_disp_over_spread",
            "buy_impact_q50",
            "sell_impact_q50",
        ]
        depth_values, depth_ns = asof_frame(
            tdepth, "depth_time", qns, depth_cols
        )
        frame["snapshot_age_s"] = np.where(
            depth_ns >= 0, (qns - depth_ns) / NS, np.nan
        )
        depth_fresh = frame["snapshot_age_s"].to_numpy(float) <= DEPTH_MAX_AGE_S
        for col in depth_cols:
            values = depth_values[col]
            values[~depth_fresh] = np.nan
            frame[col] = values

        token_trade = trades[
            trades["token_id"].astype(str) == str(token)
        ].sort_values("observed_at")
        if token_trade.empty:
            frame["trade_count_60"] = 0.0
        else:
            trade_ns = lane_a.datetime_ns(token_trade["observed_at"])
            frame["trade_count_60"] = lane_a.rolling_counts(
                trade_ns, qns, 60
            )
        tf = trade_features[
            trade_features["token_id"].astype(str) == str(token)
        ].sort_values("bin_time")
        if tf.empty:
            frame["trade_abs_impact_60"] = np.nan
        else:
            values, _ = asof_frame(
                tf, "bin_time", qns, ["trade_abs_impact_60"]
            )
            frame["trade_abs_impact_60"] = values["trade_abs_impact_60"]

        frame["freshness_x_spread"] = frame["bbo_age_s"] * frame["spread"]
        frame["imbalance_x_ofi"] = frame["top_imbalance"] * frame["ofi_60"]
        frame["volatility_x_liquidity"] = frame["rv_60"] * frame["spread"]
        frame["spread_x_distance"] = frame["spread"] * frame["distance_from_0_5"]

        labels = lane_a.split_label(grid, start, train_end, dev_end)
        frame["split"] = labels

        for horizon in (15, 60, 300):
            future_ns = qns + horizon * NS
            fmid, fsegment, _ = lane_a.asof_from_states(
                state, future_ns, "midpoint"
            )
            fspread, _, _ = lane_a.asof_from_states(
                state, future_ns, "spread"
            )
            future_labels = lane_a.split_label(
                pd.DatetimeIndex(pd.to_datetime(future_ns, utc=True)),
                start,
                train_end,
                dev_end,
            )
            good = (
                (fsegment == segment)
                & np.isfinite(fmid)
                & (future_labels == labels)
                & (labels != "")
            )
            price = lane_a.logit_array(fmid) - logmid
            price[~good] = np.nan
            frame[f"price_h{horizon}"] = price
            frame[f"abs_h{horizon}"] = np.abs(price)
            spread_change = fspread - spread
            spread_change[~good] = np.nan
            frame[f"spread_h{horizon}"] = spread_change

            left = np.searchsorted(bbo_event_ns, qns, side="right")
            right = np.searchsorted(bbo_event_ns, future_ns, side="right")
            update = (right > left).astype(float)
            update[future_labels != labels] = np.nan
            update[labels == ""] = np.nan
            frame[f"bbo_update_h{horizon}"] = update

        if not tdepth.empty:
            future_depth_values, future_depth_ns = asof_frame(
                tdepth,
                "depth_time",
                qns + 60 * NS,
                ["depth_5c"],
            )
            future_age = np.where(
                future_depth_ns >= 0,
                (qns + 60 * NS - future_depth_ns) / NS,
                np.nan,
            )
            depth_change = future_depth_values["depth_5c"] - frame["depth_5c"].to_numpy(float)
            invalid = (
                ~(future_age <= DEPTH_MAX_AGE_S)
                | ~np.isfinite(frame["depth_5c"].to_numpy(float))
                | (lane_a.split_label(
                    pd.DatetimeIndex(pd.to_datetime(qns + 60 * NS, utc=True)),
                    start,
                    train_end,
                    dev_end,
                ) != labels)
            )
            depth_change[invalid] = np.nan
            frame["depth_h60"] = depth_change
        else:
            frame["depth_h60"] = np.nan

        keep = (
            (frame["split"] != "")
            & np.isfinite(frame["midpoint"])
            & lane_a.stable_keep(str(token), grid, int(CFG["sample_mod"]))
        )
        if np.any(keep):
            parts.append(frame.loc[keep].copy())

    panel = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()
    if panel.empty:
        raise RuntimeError("Lane B canonical discovery panel is empty")

    panel = panel.sort_values(["token_id", "time"], kind="stable").reset_index(drop=True)
    audit = {
        "rows": len(panel),
        "tokens": int(panel["token_id"].nunique()),
        "train_rows": int((panel["split"] == "TRAIN").sum()),
        "dev_rows": int((panel["split"] == "DEV").sum()),
        "sample_mod": CFG["sample_mod"],
        "depth_max_age_s": DEPTH_MAX_AGE_S,
        "sequential_state_families": "SEMANTICS_BLOCKED_ON_SAMPLED_PANEL",
    }
    return panel, audit


def thin(frame: pd.DataFrame, n: int = MAX_ROWS) -> pd.DataFrame:
    if len(frame) <= n:
        return frame
    step = int(math.ceil(len(frame) / n))
    return frame.iloc[::step].head(n).copy()


def signflip_p(values: np.ndarray, key: str) -> float:
    x = values[np.isfinite(values)]
    if len(x) < 3:
        return 1.0
    observed = float(np.mean(x))
    if observed <= 0:
        return 1.0
    seed = int.from_bytes(
        hashlib.sha256(f"{SEED}|{key}".encode()).digest()[:8],
        "big",
    )
    rng = np.random.default_rng(seed)
    draws = 4096
    ge = 0
    for _ in range(draws):
        signs = rng.choice(np.array([-1.0, 1.0]), size=len(x))
        ge += float(np.mean(x * signs)) >= observed - 1e-15
    return (ge + 1) / (draws + 1)


def bootstrap_lower(values: np.ndarray, key: str) -> float:
    x = values[np.isfinite(values)]
    if len(x) < 3:
        return float("nan")
    seed = int.from_bytes(
        hashlib.sha256(f"{SEED}|boot|{key}".encode()).digest()[:8],
        "big",
    )
    rng = np.random.default_rng(seed)
    draws = np.empty(2000, float)
    for i in range(len(draws)):
        draws[i] = np.mean(rng.choice(x, size=len(x), replace=True))
    return float(np.quantile(draws, 0.025))


def screen_one(
    panel: pd.DataFrame,
    target: str,
    feature: str,
    family: str,
    override_feature: str | None = None,
    label: str = "PRIMARY",
    extra_baseline: list[str] | None = None,
) -> tuple[dict[str, Any], pd.DataFrame]:
    spec = TARGETS[target]
    feature_col = override_feature or feature
    baseline = [col for col in spec["baseline"] if col != feature_col]
    if extra_baseline:
        baseline.extend(
            col
            for col in extra_baseline
            if col in panel.columns and col != feature_col and col not in baseline
        )
    needed = [
        "split",
        "token_id",
        "time",
        "snapshot_age_s",
        "price_updates_60",
        target,
        *baseline,
        feature_col,
    ]
    needed = list(dict.fromkeys(needed))
    frame = panel[needed].replace([np.inf, -np.inf], np.nan).dropna(
        subset=[target, *baseline, feature_col]
    )
    train = thin(frame[frame["split"] == "TRAIN"].sort_values(["time", "token_id"]))
    dev = thin(frame[frame["split"] == "DEV"].sort_values(["time", "token_id"]))
    result: dict[str, Any] = {
        "target": target,
        "target_family": spec["family"],
        "feature": feature,
        "feature_column": feature_col,
        "feature_family": family,
        "evaluation": label,
        "train_rows": len(train),
        "dev_rows": len(dev),
        "markets": int(dev["token_id"].nunique()),
    }
    if len(train) < 500 or len(dev) < 200 or dev["token_id"].nunique() < 3:
        result["status"] = "INSUFFICIENT_SUPPORT"
        return result, pd.DataFrame()

    ytr = train[target].to_numpy(float)
    ydv = dev[target].to_numpy(float)
    if spec["kind"] == "classification" and (
        len(np.unique(ytr)) < 2 or len(np.unique(ydv)) < 2
    ):
        result["status"] = "ONE_CLASS_TARGET"
        return result, pd.DataFrame()

    sb = StandardScaler().fit(train[baseline])
    xb_tr = sb.transform(train[baseline])
    xb_dv = sb.transform(dev[baseline])
    if spec["kind"] == "classification":
        base = LogisticRegression(
            C=1.0, max_iter=500, random_state=SKLEARN_SEED
        ).fit(xb_tr, ytr.astype(int))
        pred_b = base.predict_proba(xb_dv)[:, 1]
    else:
        base = Ridge(alpha=1.0).fit(xb_tr, ytr)
        pred_b = base.predict(xb_dv)

    all_cols = [*baseline, feature_col]
    sf = StandardScaler().fit(train[all_cols])
    xf_tr = sf.transform(train[all_cols])
    xf_dv = sf.transform(dev[all_cols])
    if spec["kind"] == "classification":
        full = LogisticRegression(
            C=1.0, max_iter=500, random_state=SKLEARN_SEED
        ).fit(xf_tr, ytr.astype(int))
        pred_f = full.predict_proba(xf_dv)[:, 1]
    else:
        full = Ridge(alpha=1.0).fit(xf_tr, ytr)
        pred_f = full.predict(xf_dv)

    lb = (ydv - pred_b) ** 2
    lf = (ydv - pred_f) ** 2
    diff = lb - lf
    evidence = dev[
        ["token_id", "time", "snapshot_age_s", "price_updates_60"]
    ].copy()
    evidence["loss_improvement"] = diff
    evidence["block"] = evidence["time"].dt.floor("30min")
    blocks = (
        evidence.groupby("block", observed=True)["loss_improvement"]
        .mean()
        .to_numpy(float)
    )

    total_sum = float(np.sum(diff))
    total_n = len(diff)
    leave: list[float] = []
    market = evidence.groupby("token_id", observed=True)["loss_improvement"].agg(
        ["sum", "count"]
    )
    for _, row in market.iterrows():
        remaining = total_n - int(row["count"])
        if remaining > 0:
            leave.append((total_sum - float(row["sum"])) / remaining)

    fresh = evidence["snapshot_age_s"] <= 60
    activity_cut = float(evidence["price_updates_60"].median())
    active = evidence["price_updates_60"] >= activity_cut
    improvement = float(np.mean(diff))
    result.update(
        {
            "status": "EVALUATED",
            "baseline_mse": float(np.mean(lb)),
            "challenger_mse": float(np.mean(lf)),
            "mean_loss_improvement": improvement,
            "relative_mse_improvement": (
                improvement / float(np.mean(lb))
                if float(np.mean(lb)) > 0
                else np.nan
            ),
            "blocks": len(blocks),
            "positive_blocks": int(np.sum(blocks > 0)),
            "signflip_p": signflip_p(blocks, f"{target}|{family}|{feature_col}|{label}"),
            "block_bootstrap_lower": bootstrap_lower(
                blocks, f"{target}|{family}|{feature_col}|{label}"
            ),
            "leave_market_min": float(min(leave)) if leave else np.nan,
            "fresh_snapshot_mean_improvement": (
                float(evidence.loc[fresh, "loss_improvement"].mean())
                if fresh.any()
                else np.nan
            ),
            "high_activity_mean_improvement": (
                float(evidence.loc[active, "loss_improvement"].mean())
                if active.any()
                else np.nan
            ),
            "low_activity_mean_improvement": (
                float(evidence.loc[~active, "loss_improvement"].mean())
                if (~active).any()
                else np.nan
            ),
        }
    )
    return result, evidence


def apply_bh(rows: list[dict[str, Any]]) -> None:
    groups: defaultdict[tuple[str, str], list[int]] = defaultdict(list)
    for i, row in enumerate(rows):
        if row.get("evaluation") == "PRIMARY" and row.get("status") == "EVALUATED":
            groups[(str(row["target_family"]), str(row["feature_family"]))].append(i)
    for indexes in groups.values():
        ordered = sorted(indexes, key=lambda i: float(rows[i]["signflip_p"]))
        m = len(ordered)
        max_rank = 0
        adjusted = [1.0] * m
        running = 1.0
        for rank, index in enumerate(ordered, 1):
            if float(rows[index]["signflip_p"]) <= FDR_Q * rank / m:
                max_rank = rank
        for j in range(m - 1, -1, -1):
            rank = j + 1
            raw = float(rows[ordered[j]]["signflip_p"])
            running = min(running, raw * m / rank)
            adjusted[j] = min(1.0, running)
        for rank, (index, adj) in enumerate(zip(ordered, adjusted), 1):
            rows[index]["p_bh"] = adj
            rows[index]["fdr_pass"] = rank <= max_rank
            rows[index]["promotion_gate_pass"] = bool(
                rank <= max_rank
                and float(rows[index]["mean_loss_improvement"]) > 0
                and np.isfinite(rows[index]["block_bootstrap_lower"])
                and float(rows[index]["block_bootstrap_lower"]) > 0
                and np.isfinite(rows[index]["leave_market_min"])
                and float(rows[index]["leave_market_min"]) > 0
                and (
                    not np.isfinite(rows[index]["fresh_snapshot_mean_improvement"])
                    or float(rows[index]["fresh_snapshot_mean_improvement"]) > 0
                )
                and float(rows[index]["high_activity_mean_improvement"]) > 0
                and float(rows[index]["low_activity_mean_improvement"]) > 0
            )


def add_shifted_features(panel: pd.DataFrame, features: set[str]) -> pd.DataFrame:
    panel = panel.sort_values(["token_id", "time"], kind="stable").copy()
    for feature in sorted(features):
        grouped = panel.groupby("token_id", sort=False)[feature]
        panel[f"__{feature}_delay300"] = grouped.shift(20)
        panel[f"__{feature}_shift1800"] = grouped.shift(120)
        panel[f"__{feature}_future300"] = grouped.shift(-20)
    return panel


def horizon_shape(
    panel: pd.DataFrame,
    row: dict[str, Any],
) -> list[dict[str, Any]]:
    target = str(row["target"])
    if target.startswith("price_h"):
        horizons = ["price_h15", "price_h60", "price_h300"]
    elif target.startswith("abs_h"):
        horizons = ["abs_h15", "abs_h60", "abs_h300"]
    elif target.startswith("spread_h"):
        horizons = ["spread_h15", "spread_h60", "spread_h300"]
    elif target.startswith("bbo_update_h"):
        horizons = ["bbo_update_h15", "bbo_update_h60", "bbo_update_h300"]
    else:
        return []
    out: list[dict[str, Any]] = []
    for target_name in horizons:
        if target_name not in TARGETS:
            # temporary target spec inherits the original family's baseline/kind
            original = TARGETS[target]
            TARGETS[target_name] = {
                "family": original["family"],
                "kind": original["kind"],
                "baseline": original["baseline"],
            }
        result, _ = screen_one(
            panel,
            target_name,
            str(row["feature"]),
            str(row["feature_family"]),
            label="HORIZON_SHAPE",
        )
        out.append(result)
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-manifest", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()
    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=True)

    tokens, _ = lane_a.representative_tokens()
    inputs = lane_a.load_inputs(Path(args.input_manifest), CFG)
    start = pd.Timestamp(CFG["start"])
    dev_end = pd.Timestamp(CFG["dev_end"])

    states, trades, state_audit, file_audit = lane_a.build_states_and_trades(
        inputs, tokens, start, dev_end
    )
    depth, bbo, extras_audit = load_v3_extras(
        inputs, tokens, start, dev_end
    )
    panel, panel_audit = build_panel(states, trades, depth, bbo)

    rows: list[dict[str, Any]] = []
    evidence_frames: list[pd.DataFrame] = []
    for family, features in FEATURES.items():
        for feature in features:
            if feature not in panel.columns:
                continue
            for target in TARGETS:
                result, evidence = screen_one(
                    panel, target, feature, family
                )
                rows.append(result)
                if not evidence.empty:
                    tagged = evidence.copy()
                    tagged["target"] = target
                    tagged["feature"] = feature
                    tagged["feature_family"] = family
                    evidence_frames.append(tagged)

    apply_bh(rows)
    provisional = [
        row for row in rows
        if row.get("promotion_gate_pass")
    ]

    capture_control_rows: list[dict[str, Any]] = []
    capture_controls = [
        "price_change_age_s",
        "price_updates_60",
        "snapshot_age_s",
    ]
    for row in provisional:
        controlled, _ = screen_one(
            panel,
            str(row["target"]),
            str(row["feature"]),
            str(row["feature_family"]),
            label="CAPTURE_CONTROL",
            extra_baseline=capture_controls,
        )
        capture_control_rows.append(controlled)
        capture_pass = bool(
            controlled.get("status") == "EVALUATED"
            and float(controlled.get("mean_loss_improvement", float("nan"))) > 0
            and float(controlled.get("signflip_p", 1.0)) <= 0.10
            and np.isfinite(controlled.get("block_bootstrap_lower", np.nan))
            and float(controlled["block_bootstrap_lower"]) > 0
            and np.isfinite(controlled.get("leave_market_min", np.nan))
            and float(controlled["leave_market_min"]) > 0
        )
        row["capture_control_pass"] = capture_pass
        row["capture_control_mean_loss_improvement"] = controlled.get(
            "mean_loss_improvement"
        )
        row["capture_control_signflip_p"] = controlled.get("signflip_p")
        row["promotion_gate_pass"] = bool(
            row.get("promotion_gate_pass") and capture_pass
        )

    promoted = [
        row for row in rows
        if row.get("promotion_gate_pass")
    ]

    falsification_rows: list[dict[str, Any]] = []
    if promoted:
        features = {str(row["feature"]) for row in promoted}
        shifted = add_shifted_features(panel, features)
        for row in promoted:
            feature = str(row["feature"])
            for label, suffix in (
                ("FEATURE_DELAY_300S", "delay300"),
                ("TIME_SHIFT_1800S", "shift1800"),
                ("FUTURE_FEATURE_DIAGNOSTIC", "future300"),
            ):
                result, _ = screen_one(
                    shifted,
                    str(row["target"]),
                    feature,
                    str(row["feature_family"]),
                    override_feature=f"__{feature}_{suffix}",
                    label=label,
                )
                falsification_rows.append(result)

    horizon_rows: list[dict[str, Any]] = []
    for row in promoted:
        horizon_rows.extend(horizon_shape(panel, row))

    compact_cols = [
        "token_id",
        "time",
        "split",
        "midpoint",
        "spread",
        "ret_15",
        "ret_60",
        "rv_60",
        "bbo_age_s",
        "price_change_age_s",
        "bbo_updates_60",
        "price_updates_60",
        "snapshot_age_s",
        "top_imbalance",
        "depth_5c",
        "depth_imbalance_5c",
        "ofi_60",
        "trade_count_60",
        "trade_abs_impact_60",
        "price_h60",
        "abs_h60",
        "spread_h60",
        "depth_h60",
        "bbo_update_h60",
        "bbo_update_h300",
    ]
    panel[[col for col in compact_cols if col in panel.columns]].to_parquet(
        output / "LANE_B_CANONICAL_PANEL.parquet",
        index=False,
        compression="zstd",
    )

    result_doc = {
        "schema_version": 1,
        "experiment": "EXPERIMENT-005G",
        "lane": "B_OPEN_DISCOVERY",
        "phase": "TRAIN_DEV",
        "source_dataset": EXPECTED_DATASET,
        "dataset_version": 1,
        "holdout_read": False,
        "feature_families_frozen": sorted(FEATURES),
        "targets": TARGETS,
        "screen": rows,
        "promoted_train_dev": promoted,
        "capture_control": capture_control_rows,
        "falsification": falsification_rows,
        "horizon_shape": horizon_rows,
        "labels": {
            "promoted_train_dev": "DISCOVERY_ONLY",
            "not_promoted": "REJECTED_DISCOVERY",
        },
        "blocked_families": {
            "RESILIENCE": "SEMANTICS_BLOCKED: exact shock-age/recovery clocks require an unsampled sequential panel; sampled approximations are not accepted.",
            "STATE_TRANSITIONS": "SEMANTICS_BLOCKED: exact dwell/transition clocks require an unsampled sequential panel; sampled approximations are not accepted.",
            "TOXICITY": "SUPPORT_BLOCKED unless sufficiently dense observable trades exist; absence is not treated as a negative result."
        },
        "capture_control_rule": "Every provisionally promoted coordinate must remain positive with p<=0.10, bootstrap lower>0 and leave-market minimum>0 after adding price_change_age_s, price_updates_60 and snapshot_age_s where observable.",
        "limitations": [
            "TRADE_PRESSURE may be support-limited because V3 last_trade_price rows are sparse.",
            "No HOLDOUT bytes were supplied to this job."
        ],
        "real_sig_orders_sent": False,
    }
    (output / "LANE_B_DISCOVERY_RESULTS.json").write_text(
        json.dumps(result_doc, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    audit = {
        "schema_version": 1,
        "experiment": "EXPERIMENT-005G",
        "lane": "B_OPEN_DISCOVERY",
        "window": CFG,
        "holdout_read": False,
        "state_audit": state_audit,
        "extras_audit": extras_audit,
        "panel_audit": panel_audit,
        "file_audit": file_audit,
        "real_sig_orders_sent": False,
    }
    (output / "LANE_B_AUDIT.json").write_text(
        json.dumps(audit, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    if evidence_frames:
        pd.concat(evidence_frames, ignore_index=True).to_parquet(
            output / "LANE_B_DEV_EVIDENCE.parquet",
            index=False,
            compression="zstd",
        )

    print(
        json.dumps(
            {
                "panel_rows": len(panel),
                "tests": len(rows),
                "promoted_train_dev": len(promoted),
                "holdout_read": False,
                "real_sig_orders_sent": False,
            },
            indent=2,
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
