# ruff: noqa
from __future__ import annotations

import csv
import hashlib
import json
import math
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.dataset as pads
from sklearn.ensemble import HistGradientBoostingClassifier, HistGradientBoostingRegressor
from sklearn.linear_model import ElasticNet, LinearRegression, LogisticRegression, Ridge
from sklearn.preprocessing import StandardScaler

INPUT = Path("/kaggle/input")
WORK = Path("/kaggle/working/005f_microstructure_liquidity_atlas_train_dev")
WORK.mkdir(parents=True, exist_ok=True)

EXPECTED_MANIFEST = "e3d95735262b0be3e9fe7dd32fb54e53dc7e65b403ef9b5a2261ac107a8ea4c4"
EXPECTED_IDENTITY = "e89fe8e25dcc494dad3ed96fe6672ab9f247c01eb70d4c0270a913f18a200a72"
EXPECTED_QUALITY = "354801b67b9c32ae82d419f8a6198b7fd814923e8c424ac8716862b47907ccb5"
DESIGN_FREEZE_COMMIT = "b5bf4cfe7482a58161523b66097910903050fc6b"
EXECUTION_FREEZE_COMMIT = "9ec9511536514bbaa97929a4b88d701cbc09987c"
SEED = 20260928005
SKLEARN_SEED = SEED % (2**32 - 1)
NS = 1_000_000_000
GRID_SECONDS = 15
CAPTURE_BIN_SECONDS = 5
GAP_SECONDS = 300
CLOCK_H = (1, 5, 15, 30, 60, 120, 300)
EVENT_H = (1, 2, 5, 10)
WINDOWS = (5, 15, 30, 60, 120, 300)
FDR_Q = 0.10
MAX_MODEL_ROWS = 250_000
CLAIM_REGIMES = ("PRE_ELECTION", "ACTIVE_RESULTS")

MICROFV_BLOCKS = {
    "BLOCK_BBO": [
        "spread", "snapshot_interval_s",
    ],
    "BLOCK_DEPTH": [
        "spread", "snapshot_interval_s",
        "imbalance_top", "microprice_disp_over_spread",
        "depth_1c", "depth_2c", "depth_5c",
        "depth_imbalance_1c", "depth_imbalance_2c", "depth_imbalance_5c",
        "depth_concentration_1c_5c", "depth_slope_1c_5c",
        "buy_impact_q10", "sell_impact_q10", "buy_impact_q50", "sell_impact_q50",
        "buy_impact_q100", "sell_impact_q100",
    ],
    "BLOCK_OFI": [
        "spread", "snapshot_interval_s",
        "imbalance_top", "microprice_disp_over_spread",
        "depth_1c", "depth_2c", "depth_5c",
        "depth_imbalance_1c", "depth_imbalance_2c", "depth_imbalance_5c",
        "snapshot_ofi", "snapshot_ofi_norm",
    ],
    "BLOCK_TRADE": [
        "spread", "snapshot_interval_s",
        "trade_count_15", "trade_count_60", "trade_value_60",
        "trade_size_mean_60", "trade_size_max_60",
        "trade_interarrival_median_60", "trade_burstiness_60",
        "trade_activity_accel", "trade_abs_impact_60",
    ],
    "BLOCK_ALL": [
        "spread", "snapshot_interval_s",
        "imbalance_top", "microprice_disp_over_spread",
        "depth_1c", "depth_2c", "depth_5c",
        "depth_imbalance_1c", "depth_imbalance_2c", "depth_imbalance_5c",
        "depth_concentration_1c_5c", "depth_slope_1c_5c",
        "snapshot_ofi", "snapshot_ofi_norm",
        "buy_impact_q10", "sell_impact_q10", "buy_impact_q50", "sell_impact_q50",
        "buy_impact_q100", "sell_impact_q100",
        "trade_count_15", "trade_count_60", "trade_value_60",
        "trade_size_mean_60", "trade_size_max_60",
        "trade_interarrival_median_60", "trade_burstiness_60",
        "trade_activity_accel", "trade_abs_impact_60",
    ],
}

REGIME_WINDOWS = {
    "colombia_first_round": {
        "family": "COL_2026",
        "PRE_ELECTION": ("2026-05-29T00:00:00Z", "2026-05-31T13:00:00Z"),
        "ACTIVE_RESULTS": ("2026-05-31T21:11:00Z", "2026-06-01T03:11:00Z"),
    },
    "colombia_runoff": {
        "family": "COL_2026",
        "PRE_ELECTION": ("2026-06-19T00:00:00Z", "2026-06-21T13:00:00Z"),
        "ACTIVE_RESULTS": ("2026-06-21T21:11:00Z", "2026-06-22T03:11:00Z"),
    },
    "peru_first_round": {
        "family": "PER_2026",
        "PRE_ELECTION": ("2026-04-10T00:00:00Z", "2026-04-12T12:00:00Z"),
        "ACTIVE_RESULTS": ("2026-04-12T23:00:00Z", "2026-04-13T05:00:00Z"),
    },
    "peru_runoff": {
        "family": "PER_2026",
        "PRE_ELECTION": ("2026-06-05T00:00:00Z", "2026-06-07T12:00:00Z"),
        "ACTIVE_RESULTS": ("2026-06-07T23:12:00Z", "2026-06-08T05:12:00Z"),
    },
    "hungary_election": {
        "family": "HUN_2026",
        "PRE_ELECTION": ("2026-04-05T00:00:00Z", "2026-04-12T04:00:00Z"),
        "ACTIVE_RESULTS": ("2026-04-12T18:18:00Z", "2026-04-13T00:18:00Z"),
    },
}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def one(name: str) -> Path:
    matches = sorted(INPUT.rglob(name))
    if len(matches) != 1:
        raise RuntimeError(f"expected exactly one {name}: {matches}")
    return matches[0]


def dt(value: str) -> pd.Timestamp:
    return pd.Timestamp(value)


def datetime_ns(values: Any) -> np.ndarray:
    """Normalize any Pandas/Arrow datetime unit to nanosecond integer time."""
    return pd.DatetimeIndex(values).as_unit("ns").asi8.astype(np.int64, copy=False)


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        rows = [{"status": "EMPTY"}]
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)


def safe_float(x: Any) -> float:
    try:
        y = float(x)
    except (TypeError, ValueError):
        return np.nan
    return y if np.isfinite(y) else np.nan


def logit_array(p: np.ndarray) -> np.ndarray:
    x = np.clip(p.astype(float), 1e-6, 1 - 1e-6)
    return np.log(x / (1.0 - x))


def irregular_ewma(values: np.ndarray, times_ns: np.ndarray, half_life_s: float) -> np.ndarray:
    out = np.full(len(values), np.nan, float)
    state = np.nan
    last_t: int | None = None
    rate = math.log(2.0) / float(half_life_s)
    for i, value in enumerate(values.astype(float)):
        if not np.isfinite(value):
            out[i] = state
            continue
        if not np.isfinite(state) or last_t is None:
            state = value
        else:
            dt_s = max(0.0, (int(times_ns[i]) - int(last_t)) / NS)
            weight = 1.0 - math.exp(-rate * dt_s)
            state = state + weight * (value - state)
        out[i] = state
        last_t = int(times_ns[i])
    return out


def split_bounds(start: pd.Timestamp, end: pd.Timestamp) -> dict[str, tuple[pd.Timestamp, pd.Timestamp]]:
    span = end - start
    train_end = start + span * 0.60
    dev_end = start + span * 0.80
    return {"TRAIN": (start, train_end), "DEV": (train_end, dev_end), "HOLDOUT": (dev_end, end)}


def assign_split(times: pd.Series, bounds: dict[str, tuple[pd.Timestamp, pd.Timestamp]]) -> pd.Series:
    out = pd.Series(pd.NA, index=times.index, dtype="object")
    purge = pd.Timedelta(seconds=300)
    ts, te = bounds["TRAIN"]
    ds, de = bounds["DEV"]
    out.loc[(times >= ts) & (times < te - purge)] = "TRAIN"
    out.loc[(times >= ds) & (times < de - purge)] = "DEV"
    return out


def yes_tokens(identity_path: Path, event: str) -> tuple[set[str], dict[str, str], dict[str, str]]:
    rows = pd.read_csv(identity_path, dtype=str)
    rows = rows[(rows["regime_id"] == event) & (rows["outcome"].str.lower() == "yes")]
    rows = rows[rows["book_available"].str.lower() == "true"]
    tokens = set(rows["token_id"].dropna().astype(str))
    market = dict(zip(rows["token_id"].astype(str), rows["market_id"].astype(str)))
    family = dict(zip(rows["token_id"].astype(str), rows["market_family"].fillna("").astype(str)))
    return tokens, market, family


def filtered_batches(
    root: Path,
    columns: list[str],
    tokens: set[str],
    time_col: str,
    start: pd.Timestamp,
    end: pd.Timestamp,
    batch_size: int = 500_000,
):
    files = sorted(root.rglob("*.parquet"))
    if not files:
        return
    token_list = sorted(tokens)
    start_scalar = pa.scalar(start.to_pydatetime(), pa.timestamp("us", tz="UTC"))
    end_scalar = pa.scalar(end.to_pydatetime(), pa.timestamp("us", tz="UTC"))
    for path in files:
        dataset = pads.dataset([str(path)], format="parquet")
        expr = (
            (pads.field(time_col) >= start_scalar)
            & (pads.field(time_col) < end_scalar)
            & pads.field("token_id").isin(token_list)
        )
        scanner = dataset.scanner(
            columns=columns,
            filter=expr,
            batch_size=batch_size,
            use_threads=False,
        )
        for batch in scanner.to_batches():
            if batch.num_rows:
                yield batch


def group_book_batch(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return frame
    for col in ("best_bid", "best_ask", "price", "size"):
        frame[col] = pd.to_numeric(frame[col], errors="coerce")
    frame["observed_at"] = pd.to_datetime(frame["observed_at"], utc=True)
    frame["bid_best_update"] = np.where(
        (frame["side"] == "BUY") & np.isclose(frame["price"], frame["best_bid"], atol=1e-12, rtol=0),
        frame["size"],
        np.nan,
    )
    frame["ask_best_update"] = np.where(
        (frame["side"] == "SELL") & np.isclose(frame["price"], frame["best_ask"], atol=1e-12, rtol=0),
        frame["size"],
        np.nan,
    )
    grp = frame.groupby(["token_id", "observed_at"], sort=False, observed=True)
    out = grp.agg(
        raw_rows=("token_id", "size"),
        best_bid=("best_bid", "first"),
        best_ask=("best_ask", "first"),
        bid_n=("best_bid", "nunique"),
        ask_n=("best_ask", "nunique"),
        bid_update=("bid_best_update", "max"),
        ask_update=("ask_best_update", "max"),
        bid_update_n=("bid_best_update", "nunique"),
        ask_update_n=("ask_best_update", "nunique"),
        source_version=("source_version", "first"),
    ).reset_index()
    out["ambiguous"] = (
        (out["bid_n"] > 1)
        | (out["ask_n"] > 1)
        | (out["bid_update_n"] > 1)
        | (out["ask_update_n"] > 1)
    )
    out["bbo_valid"] = (
        np.isfinite(out["best_bid"])
        & np.isfinite(out["best_ask"])
        & (out["best_bid"] > 0)
        & (out["best_ask"] < 1)
        & (out["best_bid"] <= out["best_ask"])
        & ~out["ambiguous"]
    )
    return out


def load_book_state(
    root: Path,
    tokens: set[str],
    start: pd.Timestamp,
    end: pd.Timestamp,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    columns = [
        "token_id", "observed_at", "side", "price", "size",
        "best_bid", "best_ask", "source_version",
    ]
    carry_raw = pd.DataFrame()
    state_carry: dict[str, dict[str, Any]] = {}
    state_parts: list[pd.DataFrame] = []
    capture_parts: list[pd.DataFrame] = []
    audit = defaultdict(int)

    def consume(g: pd.DataFrame) -> None:
        nonlocal state_parts, capture_parts
        if g.empty:
            return
        g = g.sort_values(["token_id", "observed_at"], kind="stable")
        for token, sub in g.groupby("token_id", sort=False):
            sub = sub.copy().reset_index(drop=True)
            n = len(sub)
            if not n:
                continue
            c = state_carry.get(str(token))
            bid = sub["best_bid"].to_numpy(float)
            ask = sub["best_ask"].to_numpy(float)
            times_i = datetime_ns(sub["observed_at"])
            valid = sub["bbo_valid"].to_numpy(bool)

            prev_bid = np.empty(n, float)
            prev_ask = np.empty(n, float)
            prev_time = np.empty(n, np.int64)
            prev_valid = np.empty(n, bool)
            if c:
                prev_bid[0] = c["bid"]
                prev_ask[0] = c["ask"]
                prev_time[0] = c["time_ns"]
                prev_valid[0] = c["valid"]
            else:
                prev_bid[0] = np.nan
                prev_ask[0] = np.nan
                prev_time[0] = times_i[0]
                prev_valid[0] = False
            if n > 1:
                prev_bid[1:] = bid[:-1]
                prev_ask[1:] = ask[:-1]
                prev_time[1:] = times_i[:-1]
                prev_valid[1:] = valid[:-1]

            gap = (times_i - prev_time) > GAP_SECONDS * NS
            contiguous = valid & prev_valid & ~gap
            same_bbo = np.isclose(bid, prev_bid, atol=1e-12, rtol=0) & np.isclose(
                ask, prev_ask, atol=1e-12, rtol=0
            )
            genuine = contiguous & ~same_bbo
            repeated = contiguous & same_bbo
            establish = valid & ~contiguous

            def top_quantity(update_col: str, price: np.ndarray, prev_price: np.ndarray, carry_q: float) -> np.ndarray:
                values = sub[update_col].to_numpy(float)
                if n and c and contiguous[0] and np.isclose(price[0], prev_price[0], atol=1e-12, rtol=0):
                    if not np.isfinite(values[0]) and np.isfinite(carry_q):
                        values[0] = carry_q
                boundary = (~valid) | (~contiguous) | (~np.isclose(price, prev_price, atol=1e-12, rtol=0))
                segment = np.cumsum(boundary)
                return pd.Series(values).groupby(segment).ffill().to_numpy(float)

            qbid = top_quantity("bid_update", bid, prev_bid, c["qbid"] if c else np.nan)
            qask = top_quantity("ask_update", ask, prev_ask, c["qask"] if c else np.nan)
            prev_qbid = np.empty(n, float)
            prev_qask = np.empty(n, float)
            prev_qbid[0] = c["qbid"] if c else np.nan
            prev_qask[0] = c["qask"] if c else np.nan
            if n > 1:
                prev_qbid[1:] = qbid[:-1]
                prev_qask[1:] = qask[:-1]
            q_change = (
                contiguous
                & np.isfinite(qbid)
                & np.isfinite(qask)
                & np.isfinite(prev_qbid)
                & np.isfinite(prev_qask)
                & (
                    ~np.isclose(qbid, prev_qbid, atol=1e-12, rtol=0)
                    | ~np.isclose(qask, prev_qask, atol=1e-12, rtol=0)
                )
            )
            economic = genuine | q_change

            ofi = np.full(n, np.nan)
            ok = (
                contiguous
                & np.isfinite(qbid)
                & np.isfinite(qask)
                & np.isfinite(prev_qbid)
                & np.isfinite(prev_qask)
            )
            if np.any(ok):
                ofi[ok] = (
                    np.where(bid[ok] >= prev_bid[ok], qbid[ok], 0.0)
                    - np.where(bid[ok] <= prev_bid[ok], prev_qbid[ok], 0.0)
                    - np.where(ask[ok] <= prev_ask[ok], qask[ok], 0.0)
                    + np.where(ask[ok] >= prev_ask[ok], prev_qask[ok], 0.0)
                )

            establish_i = establish.astype(int)
            base_segment = int(c["segment"]) if c else 0
            segments = base_segment + np.cumsum(establish_i)
            last_genuine = np.full(n, -1, np.int64)
            lg = int(c["last_genuine"]) if c else -1
            for idx in np.flatnonzero(genuine):
                lg = int(times_i[idx])
                last_genuine[idx:] = lg
            if lg >= 0 and not np.any(genuine):
                last_genuine[:] = lg
            elif lg >= 0:
                first = int(np.flatnonzero(genuine)[0])
                last_genuine[:first] = int(c["last_genuine"]) if c else -1

            sub["valid"] = valid
            sub["genuine_bbo"] = genuine
            sub["repeated_unchanged"] = repeated
            sub["establish"] = establish
            sub["top_depth_change"] = q_change
            sub["economic_change"] = economic
            sub["qbid"] = qbid
            sub["qask"] = qask
            sub["ofi"] = ofi
            sub["segment"] = segments
            sub["last_genuine_ns"] = last_genuine
            sub["midpoint"] = (bid + ask) / 2.0
            sub["spread"] = ask - bid
            sub["logit_mid"] = logit_array(sub["midpoint"].to_numpy(float))

            keep = valid & (establish | economic)
            if np.any(keep):
                state_parts.append(sub.loc[keep, [
                    "token_id", "observed_at", "best_bid", "best_ask", "midpoint",
                    "logit_mid", "spread", "qbid", "qask", "ofi", "source_version",
                    "genuine_bbo", "top_depth_change", "establish", "segment",
                    "last_genuine_ns",
                ]].copy())

            cap = pd.DataFrame({
                "token_id": str(token),
                "bin_time": sub["observed_at"].dt.floor(f"{CAPTURE_BIN_SECONDS}s"),
                "raw_rows": sub["raw_rows"].to_numpy(int),
                "raw_groups": 1,
                "repeated_groups": repeated.astype(int),
                "ambiguous_groups": sub["ambiguous"].to_numpy(bool).astype(int),
                "genuine_bbo": genuine.astype(int),
                "top_depth_changes": q_change.astype(int),
            })
            capture_parts.append(cap.groupby(["token_id", "bin_time"], as_index=False).sum())

            audit["raw_rows"] += int(sub["raw_rows"].sum())
            audit["raw_groups"] += n
            audit["ambiguous_groups"] += int(sub["ambiguous"].sum())
            audit["valid_groups"] += int(valid.sum())
            audit["genuine_bbo"] += int(genuine.sum())
            audit["repeated_unchanged"] += int(repeated.sum())
            audit["ofi_observable_transitions"] += int(np.isfinite(ofi).sum())
            audit["top_depth_changes"] += int(q_change.sum())

            if valid[-1]:
                state_carry[str(token)] = {
                    "bid": float(bid[-1]), "ask": float(ask[-1]),
                    "qbid": float(qbid[-1]), "qask": float(qask[-1]),
                    "time_ns": int(times_i[-1]), "valid": True,
                    "segment": int(segments[-1]), "last_genuine": int(last_genuine[-1]),
                }
            else:
                state_carry[str(token)] = {
                    "bid": np.nan, "ask": np.nan, "qbid": np.nan, "qask": np.nan,
                    "time_ns": int(times_i[-1]), "valid": False,
                    "segment": int(segments[-1]), "last_genuine": -1,
                }

    for batch in filtered_batches(root, columns, tokens, "observed_at", start, end):
        df = batch.to_pandas()
        if not carry_raw.empty:
            df = pd.concat([carry_raw, df], ignore_index=True)
            carry_raw = pd.DataFrame()
        df["observed_at"] = pd.to_datetime(df["observed_at"], utc=True)
        max_time = df["observed_at"].max()
        carry_raw = df[df["observed_at"] == max_time].copy()
        process = df[df["observed_at"] < max_time].copy()
        consume(group_book_batch(process))
    if not carry_raw.empty:
        consume(group_book_batch(carry_raw))

    states = pd.concat(state_parts, ignore_index=True) if state_parts else pd.DataFrame()
    captures = pd.concat(capture_parts, ignore_index=True) if capture_parts else pd.DataFrame()
    if not captures.empty:
        captures = captures.groupby(["token_id", "bin_time"], as_index=False).sum()
    audit["state_rows_retained"] = len(states)
    audit["tokens_seen"] = int(states["token_id"].nunique()) if not states.empty else 0
    return states, captures, dict(audit)


def rolling_counts(
    bin_times_ns: np.ndarray,
    values: np.ndarray,
    query_ns: np.ndarray,
    window_s: int,
) -> np.ndarray:
    prefix = np.r_[0.0, np.cumsum(values.astype(float))]
    right = np.searchsorted(bin_times_ns, query_ns, side="right")
    left = np.searchsorted(bin_times_ns, query_ns - window_s * NS, side="right")
    return prefix[right] - prefix[left]


def asof_from_states(states: pd.DataFrame, query_ns: np.ndarray, field: str) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    t = datetime_ns(states["observed_at"])
    idx = np.searchsorted(t, query_ns, side="right") - 1
    ok = idx >= 0
    values = np.full(len(query_ns), np.nan, float)
    seg = np.full(len(query_ns), -1, np.int64)
    source_time = np.full(len(query_ns), -1, np.int64)
    if np.any(ok):
        ii = idx[ok]
        values[ok] = states[field].to_numpy(float)[ii]
        seg[ok] = states["segment"].to_numpy(np.int64)[ii]
        source_time[ok] = t[ii]
    return values, seg, source_time



def load_trade_features(
    root: Path,
    tokens: set[str],
    start: pd.Timestamp,
    end: pd.Timestamp,
    states: pd.DataFrame,
) -> pd.DataFrame:
    """Trailing, identity-free PMXT V2 trade/activity features.

    Same-time trades are later joined with allow_exact_matches=False so a quote event
    never consumes an archive trade sharing its observable timestamp.
    """
    columns = ["token_id", "observed_at", "price", "size", "source_version"]
    parts: list[pd.DataFrame] = []
    for batch in filtered_batches(root, columns, tokens, "observed_at", start, end, batch_size=100_000):
        parts.append(batch.to_pandas())
    if not parts:
        return pd.DataFrame()
    trades = pd.concat(parts, ignore_index=True)
    trades["observed_at"] = pd.to_datetime(trades["observed_at"], utc=True)
    trades["price"] = pd.to_numeric(trades["price"], errors="coerce")
    trades["size"] = pd.to_numeric(trades["size"], errors="coerce")
    trades = trades[
        np.isfinite(trades["price"])
        & np.isfinite(trades["size"])
        & (trades["size"] > 0)
    ].copy()
    out_parts: list[pd.DataFrame] = []
    for token, g in trades.groupby("token_id", sort=False):
        g = g.sort_values("observed_at").copy()
        s = states[states["token_id"].astype(str) == str(token)].sort_values("observed_at")
        qns = datetime_ns(g["observed_at"])
        if not s.empty:
            mid, _, _ = asof_from_states(s, qns - 1, "midpoint")
            g["abs_trade_mid_disp"] = np.abs(g["price"].to_numpy(float) - mid)
        else:
            g["abs_trade_mid_disp"] = np.nan
        g["trade_value"] = g["price"] * g["size"]
        g["interarrival_s"] = g["observed_at"].diff().dt.total_seconds()
        indexed = g.set_index("observed_at")
        base = indexed.resample(
            f"{CAPTURE_BIN_SECONDS}s", label="right", closed="right"
        ).agg(
            trade_count=("size", "size"),
            trade_value=("trade_value", "sum"),
            trade_size_sum=("size", "sum"),
            trade_size_max=("size", "max"),
            abs_trade_mid_disp_sum=("abs_trade_mid_disp", "sum"),
            impact_obs=("abs_trade_mid_disp", "count"),
        )
        base["trade_count"] = base["trade_count"].fillna(0.0)
        for col in ("trade_value", "trade_size_sum", "abs_trade_mid_disp_sum", "impact_obs"):
            base[col] = base[col].fillna(0.0)
        base["trade_size_max"] = base["trade_size_max"].fillna(0.0)

        dt_series = indexed["interarrival_s"]
        ia_med = dt_series.rolling("60s", closed="left").median().resample(
            f"{CAPTURE_BIN_SECONDS}s", label="right", closed="right"
        ).last().reindex(base.index).ffill()
        ia_mean = dt_series.rolling("60s", closed="left").mean().resample(
            f"{CAPTURE_BIN_SECONDS}s", label="right", closed="right"
        ).last().reindex(base.index).ffill()
        ia_std = dt_series.rolling("60s", closed="left").std().resample(
            f"{CAPTURE_BIN_SECONDS}s", label="right", closed="right"
        ).last().reindex(base.index).ffill()
        base["trade_interarrival_median_60"] = ia_med
        denom = ia_std + ia_mean
        base["trade_burstiness_60"] = (ia_std - ia_mean) / denom.replace(0, np.nan)

        for w in WINDOWS:
            periods = max(1, w // CAPTURE_BIN_SECONDS)
            base[f"trade_count_{w}"] = base["trade_count"].rolling(periods, min_periods=1).sum()
            base[f"trade_value_{w}"] = base["trade_value"].rolling(periods, min_periods=1).sum()
            size_sum = base["trade_size_sum"].rolling(periods, min_periods=1).sum()
            count = base[f"trade_count_{w}"]
            base[f"trade_size_mean_{w}"] = size_sum / count.replace(0, np.nan)
            base[f"trade_size_max_{w}"] = base["trade_size_max"].rolling(periods, min_periods=1).max()
            imp_sum = base["abs_trade_mid_disp_sum"].rolling(periods, min_periods=1).sum()
            imp_n = base["impact_obs"].rolling(periods, min_periods=1).sum()
            base[f"trade_abs_impact_{w}"] = imp_sum / imp_n.replace(0, np.nan)
        base["trade_activity_accel"] = base["trade_count_15"] / 15.0 - base["trade_count_60"] / 60.0
        base = base.reset_index().rename(columns={"observed_at": "bin_time"})
        base["token_id"] = str(token)
        keep = ["token_id", "bin_time", "trade_interarrival_median_60", "trade_burstiness_60", "trade_activity_accel"]
        for w in WINDOWS:
            keep += [
                f"trade_count_{w}", f"trade_value_{w}", f"trade_size_mean_{w}",
                f"trade_size_max_{w}", f"trade_abs_impact_{w}",
            ]
        out_parts.append(base[keep])
    return pd.concat(out_parts, ignore_index=True) if out_parts else pd.DataFrame()


def attach_trade_features(frame: pd.DataFrame, trade_features: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return frame
    if trade_features.empty:
        return frame
    parts: list[pd.DataFrame] = []
    feature_cols = [c for c in trade_features.columns if c not in {"token_id", "bin_time"}]
    for token, g in frame.groupby("token_id", sort=False):
        t = trade_features[trade_features["token_id"].astype(str) == str(token)].sort_values("bin_time")
        g = g.sort_values("time").copy()
        if t.empty:
            for col in feature_cols:
                g[col] = np.nan
            parts.append(g)
            continue
        g["__merge_ns"] = pd.DatetimeIndex(g["time"]).as_unit("ns").asi8
        t = t.copy()
        t["__merge_ns"] = pd.DatetimeIndex(t["bin_time"]).as_unit("ns").asi8
        merged = pd.merge_asof(
            g.sort_values("__merge_ns"),
            t.drop(columns=["token_id"]).sort_values("__merge_ns"),
            on="__merge_ns",
            direction="backward",
            allow_exact_matches=False,
        ).drop(columns=["bin_time", "__merge_ns"])
        parts.append(merged)
    return pd.concat(parts, ignore_index=True) if parts else frame

def build_clock(
    event: str,
    regime: str,
    states: pd.DataFrame,
    captures: pd.DataFrame,
    market_map: dict[str, str],
    start: pd.Timestamp,
    end: pd.Timestamp,
) -> pd.DataFrame:
    bounds = split_bounds(start, end)
    dev_end = bounds["DEV"][1]
    parts: list[pd.DataFrame] = []
    for token, s in states.groupby("token_id", sort=False):
        s = s.sort_values("observed_at").reset_index(drop=True)
        if s.empty:
            continue
        grid = pd.date_range(start, dev_end, freq=f"{GRID_SECONDS}s", inclusive="left", tz="UTC")
        qns = datetime_ns(grid)
        mid, seg, state_ns = asof_from_states(s, qns, "midpoint")
        spread, _, _ = asof_from_states(s, qns, "spread")
        logmid = logit_array(mid)
        last_genuine, _, _ = asof_from_states(s, qns, "last_genuine_ns")
        genuine_age = np.where(last_genuine >= 0, (qns - last_genuine) / NS, np.nan)
        frame = pd.DataFrame({
            "event": event, "regime": regime, "token_id": str(token),
            "market_id": market_map.get(str(token), ""), "time": grid,
            "midpoint": mid, "logit_mid": logmid, "spread": spread,
            "segment": seg, "state_age_s": (qns - state_ns) / NS,
            "genuine_age_s": genuine_age,
        })
        for w in (1, 5, 15, 30, 60, 120, 300):
            past, past_seg, _ = asof_from_states(s, qns - w * NS, "midpoint")
            ret = logmid - logit_array(past)
            ret[(past_seg != seg) | ~np.isfinite(past)] = np.nan
            frame[f"ret_{w}"] = ret

        cap = captures[captures["token_id"].astype(str) == str(token)].sort_values("bin_time")
        if not cap.empty:
            bt = datetime_ns(cap["bin_time"])
            last_bin_idx = np.searchsorted(bt, qns, side="right") - 1
            raw_bin_age = np.full(len(qns), np.nan, float)
            has_bin = last_bin_idx >= 0
            raw_bin_age[has_bin] = (qns[has_bin] - bt[last_bin_idx[has_bin]]) / NS
            frame["raw_capture_bin_age_s"] = raw_bin_age
            for w in WINDOWS:
                for col, name in (
                    ("raw_rows", "raw"),
                    ("raw_groups", "record"),
                    ("repeated_groups", "repeated"),
                    ("genuine_bbo", "genuine"),
                    ("ambiguous_groups", "ambiguous"),
                    ("top_depth_changes", "top_depth"),
                ):
                    frame[f"{name}_{w}"] = rolling_counts(
                        bt, cap[col].to_numpy(float), qns, w
                    )
                frame[f"genuine_raw_ratio_{w}"] = frame[f"genuine_{w}"] / np.maximum(frame[f"raw_{w}"], 1.0)
        else:
            frame["raw_capture_bin_age_s"] = np.nan
            for w in WINDOWS:
                for name in ("raw", "record", "repeated", "genuine", "ambiguous", "top_depth"):
                    frame[f"{name}_{w}"] = 0.0
                frame[f"genuine_raw_ratio_{w}"] = 0.0

        frame["abs_ret_15"] = np.abs(frame["ret_15"])
        frame["rv_60"] = np.sqrt(frame["ret_15"].pow(2).rolling(4, min_periods=2).sum())
        frame["rv_300"] = np.sqrt(frame["ret_15"].pow(2).rolling(20, min_periods=4).sum())
        frame["activity_norm_vol_60"] = frame["rv_60"] / np.sqrt(np.maximum(frame["genuine_60"], 1.0))
        frame["vol_of_vol_300"] = frame["rv_60"].rolling(20, min_periods=4).std()
        frame["split"] = assign_split(frame["time"], bounds)

        for h in CLOCK_H:
            future_ns = qns + h * NS
            fmid, fseg, _ = asof_from_states(s, future_ns, "midpoint")
            fspread, _, _ = asof_from_states(s, future_ns, "spread")
            y = logit_array(fmid) - logmid
            good = (fseg == seg) & np.isfinite(fmid)
            target_split = assign_split(
                pd.Series(pd.to_datetime(future_ns, utc=True)), bounds
            ).fillna("__NONE__").astype(str).to_numpy()
            current_split = frame["split"].fillna("__NONE__").astype(str).to_numpy()
            same_split = target_split == current_split
            good &= same_split
            y[~good] = np.nan
            frame[f"price_h{h}"] = y
            frame[f"abs_h{h}"] = np.abs(y)
            sd = fspread - spread
            sd[~good] = np.nan
            frame[f"spread_h{h}"] = sd

            genuine_times = datetime_ns(s.loc[s["genuine_bbo"], "observed_at"])
            a = np.searchsorted(genuine_times, qns, side="right")
            b = np.searchsorted(genuine_times, future_ns, side="right")
            hazard = (b > a).astype(float)
            hazard[~same_split] = np.nan
            frame[f"update_h{h}"] = hazard

        frame = frame[frame["split"].notna() & np.isfinite(frame["midpoint"])].copy()
        parts.append(frame)
    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()


def build_event_time(
    event: str,
    regime: str,
    states: pd.DataFrame,
    market_map: dict[str, str],
    start: pd.Timestamp,
    end: pd.Timestamp,
) -> pd.DataFrame:
    bounds = split_bounds(start, end)
    parts: list[pd.DataFrame] = []
    for token, all_states in states.groupby("token_id", sort=False):
        g = all_states[all_states["genuine_bbo"]].sort_values("observed_at").copy().reset_index(drop=True)
        if len(g) < 12:
            continue
        g["event"] = event
        g["regime"] = regime
        g["market_id"] = market_map.get(str(token), "")
        g["time"] = g["observed_at"]
        g["event_ret1"] = g["logit_mid"].diff()
        g["event_ret2"] = g["logit_mid"].diff(2)
        g["inter_event_s"] = g["time"].diff().dt.total_seconds()
        depth = g["qbid"] + g["qask"]
        g["ofi_norm"] = g["ofi"] / depth.replace(0, np.nan)
        g["imbalance_top"] = (g["qbid"] - g["qask"]) / depth.replace(0, np.nan)
        indexed_ofi = g.set_index("time")["ofi"]
        g["ofi_sum_5s"] = indexed_ofi.rolling("5s").sum().to_numpy()
        g["ofi_sum_30s"] = indexed_ofi.rolling("30s").sum().to_numpy()
        event_times_ns = datetime_ns(g["time"])
        g["ofi_ewma_5s"] = irregular_ewma(g["ofi"].to_numpy(float), event_times_ns, 5.0)
        g["ofi_ewma_30s"] = irregular_ewma(g["ofi"].to_numpy(float), event_times_ns, 30.0)
        micro = (g["best_ask"] * g["qbid"] + g["best_bid"] * g["qask"]) / depth.replace(0, np.nan)
        g["microprice_disp_over_spread"] = (micro - g["midpoint"]) / g["spread"].replace(0, np.nan)
        g["split"] = assign_split(g["time"], bounds)

        split_arr = g["split"].fillna("__NONE__").astype(str).to_numpy(dtype=object)
        for boundary_name in ("TRAIN", "DEV"):
            idx = np.flatnonzero(split_arr == boundary_name)
            if len(idx):
                split_arr[idx[-10:]] = None
        g["split"] = split_arr

        for k in EVENT_H:
            future = g.shift(-k)
            same = (
                (future["segment"].to_numpy() == g["segment"].to_numpy())
                & (
                    future["split"].fillna("__NONE__").astype(str).to_numpy()
                    == g["split"].fillna("__NONE__").astype(str).to_numpy()
                )
            )
            y = future["logit_mid"].to_numpy(float) - g["logit_mid"].to_numpy(float)
            y[~same] = np.nan
            g[f"event_price_k{k}"] = y
            g[f"event_abs_k{k}"] = np.abs(y)
            sp = future["spread"].to_numpy(float) - g["spread"].to_numpy(float)
            sp[~same] = np.nan
            g[f"event_spread_k{k}"] = sp
        parts.append(g[g["split"].notna()].copy())
    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()


def level_pairs(raw: Any) -> list[tuple[float, float]]:
    out: list[tuple[float, float]] = []
    if raw is None:
        return out
    for item in raw:
        if isinstance(item, dict):
            p, q = safe_float(item.get("price")), safe_float(item.get("size"))
        elif isinstance(item, (list, tuple)) and len(item) >= 2:
            p, q = safe_float(item[0]), safe_float(item[1])
        else:
            continue
        if np.isfinite(p) and np.isfinite(q) and q > 0:
            out.append((p, q))
    return out


def level_vwap_impact(
    levels: list[tuple[float, float]], quantity: float, midpoint: float, *, buy: bool
) -> float:
    ordered = sorted(levels, key=lambda x: x[0], reverse=not buy)
    remaining = float(quantity)
    notional = 0.0
    filled = 0.0
    for price, size in ordered:
        take = min(remaining, size)
        notional += take * price
        filled += take
        remaining -= take
        if remaining <= 1e-12:
            break
    if remaining > 1e-12 or filled <= 0:
        return np.nan
    vwap = notional / filled
    return float(vwap - midpoint if buy else midpoint - vwap)


def snapshot_metrics(row: dict[str, Any]) -> dict[str, float]:
    bids = level_pairs(row.get("bids"))
    asks = level_pairs(row.get("asks"))
    if not bids or not asks:
        return {}
    bid = max(p for p, _ in bids)
    ask = min(p for p, _ in asks)
    if not (0 < bid <= ask < 1):
        return {}
    midpoint = (bid + ask) / 2
    qb = sum(q for p, q in bids if abs(p - bid) <= 1e-12)
    qa = sum(q for p, q in asks if abs(p - ask) <= 1e-12)
    total_top = qb + qa
    out = {
        "best_bid": bid, "best_ask": ask, "midpoint": midpoint, "spread": ask - bid,
        "qbid": qb, "qask": qa,
        "imbalance_top": np.nan if total_top <= 0 else (qb - qa) / total_top,
        "primitive_microprice": np.nan if total_top <= 0 else (ask * qb + bid * qa) / total_top,
    }
    for band in (0.01, 0.02, 0.05):
        key = int(round(band * 100))
        bd = sum(q for p, q in bids if abs(p - midpoint) <= band + 1e-12)
        ad = sum(q for p, q in asks if abs(p - midpoint) <= band + 1e-12)
        tot = bd + ad
        out[f"depth_{key}c"] = tot
        out[f"depth_imbalance_{key}c"] = np.nan if tot <= 0 else (bd - ad) / tot
    out["depth_concentration_1c_5c"] = (
        np.nan if out["depth_5c"] <= 0 else out["depth_1c"] / out["depth_5c"]
    )
    out["depth_slope_1c_5c"] = (out["depth_5c"] - out["depth_1c"]) / 0.04
    for quantity in (10.0, 50.0, 100.0, 500.0):
        label = int(quantity)
        out[f"buy_impact_q{label}"] = level_vwap_impact(asks, quantity, midpoint, buy=True)
        out[f"sell_impact_q{label}"] = level_vwap_impact(bids, quantity, midpoint, buy=False)
    return out


def load_depth(
    root: Path,
    tokens: set[str],
    start: pd.Timestamp,
    end: pd.Timestamp,
) -> pd.DataFrame:
    columns = ["token_id", "recorded_at", "bids", "asks", "source_version"]
    rows: list[dict[str, Any]] = []
    for batch in filtered_batches(root, columns, tokens, "recorded_at", start, end, batch_size=50_000):
        for row in batch.to_pylist():
            metrics = snapshot_metrics(row)
            if metrics:
                rows.append({
                    "token_id": str(row["token_id"]),
                    "time": pd.Timestamp(row["recorded_at"]),
                    "source_version": str(row.get("source_version", "")),
                    **metrics,
                })
    if not rows:
        return pd.DataFrame()
    out = pd.DataFrame(rows).sort_values(["token_id", "time"])
    dup = out.duplicated(["token_id", "time"], keep=False)
    out = out[~dup].copy()
    out["logit_mid"] = logit_array(out["midpoint"].to_numpy(float))
    parts = []
    for token, g in out.groupby("token_id", sort=False):
        g = g.copy().reset_index(drop=True)
        prev = g.shift(1)
        gap = g["time"].diff().dt.total_seconds()
        valid = gap.le(GAP_SECONDS) & gap.notna()
        ofi = (
            np.where(g["best_bid"] >= prev["best_bid"], g["qbid"], 0.0)
            - np.where(g["best_bid"] <= prev["best_bid"], prev["qbid"], 0.0)
            - np.where(g["best_ask"] <= prev["best_ask"], g["qask"], 0.0)
            + np.where(g["best_ask"] >= prev["best_ask"], prev["qask"], 0.0)
        )
        g["snapshot_ofi"] = np.where(valid, ofi, np.nan)
        g["snapshot_ofi_norm"] = g["snapshot_ofi"] / (g["qbid"] + g["qask"]).replace(0, np.nan)
        g["snapshot_interval_s"] = gap
        g["microprice_disp_over_spread"] = (
            (g["primitive_microprice"] - g["midpoint"]) / g["spread"].replace(0, np.nan)
        )
        g["depth2_log_change"] = np.log1p(g["depth_2c"]) - np.log1p(prev["depth_2c"])
        g["large_depth_shock"] = valid & (g["depth_2c"] <= 0.5 * prev["depth_2c"])
        g["pre_shock_depth2"] = prev["depth_2c"]
        parts.append(g)
    return pd.concat(parts, ignore_index=True)


def attach_depth_targets(
    depth: pd.DataFrame,
    states: pd.DataFrame,
    event: str,
    regime: str,
    market_map: dict[str, str],
    start: pd.Timestamp,
    end: pd.Timestamp,
) -> pd.DataFrame:
    if depth.empty:
        return depth
    bounds = split_bounds(start, end)
    parts = []
    for token, d in depth.groupby("token_id", sort=False):
        s = states[states["token_id"].astype(str) == str(token)].sort_values("observed_at")
        if s.empty:
            continue
        d = d.copy().sort_values("time").reset_index(drop=True)
        qns = datetime_ns(d["time"])
        d["event"] = event
        d["regime"] = regime
        d["market_id"] = market_map.get(str(token), "")
        d["split"] = assign_split(d["time"], bounds)
        for w in (15, 30, 60):
            past, pseg, _ = asof_from_states(s, qns - w * NS, "midpoint")
            cur, cseg, _ = asof_from_states(s, qns, "midpoint")
            ret = logit_array(cur) - logit_array(past)
            ret[(pseg != cseg) | ~np.isfinite(past)] = np.nan
            d[f"ret_{w}"] = ret
        cur_mid, cur_seg, _ = asof_from_states(s, qns, "midpoint")
        d["state_mid"] = cur_mid
        for h in CLOCK_H:
            fmid, fseg, _ = asof_from_states(s, qns + h * NS, "midpoint")
            y = logit_array(fmid) - logit_array(cur_mid)
            target_split = assign_split(
                pd.Series(pd.to_datetime(qns + h * NS, utc=True)), bounds
            ).fillna("__NONE__").astype(str).to_numpy()
            depth_split = d["split"].fillna("__NONE__").astype(str).to_numpy()
            same = (
                (fseg == cur_seg)
                & (target_split == depth_split)
                & np.isfinite(cur_mid)
                & np.isfinite(fmid)
            )
            y[~same] = np.nan
            d[f"depth_price_h{h}"] = y
            d[f"depth_abs_h{h}"] = np.abs(y)

        # Observable resilience: require at least one later depth snapshot by the horizon.
        times = datetime_ns(d["time"])
        depth2 = d["depth_2c"].to_numpy(float)
        pre = d["pre_shock_depth2"].to_numpy(float)
        shock = d["large_depth_shock"].to_numpy(bool)
        half_life = np.full(len(d), np.nan)
        split_values = d["split"].fillna("__NONE__").astype(str).to_numpy()
        shock_indices = np.flatnonzero(shock)
        for i in shock_indices:
            denom = pre[i] - depth2[i]
            if not np.isfinite(denom) or denom <= 0:
                continue
            for j in range(i + 1, len(d)):
                if split_values[j] != split_values[i]:
                    break
                recovery_fraction = (depth2[j] - depth2[i]) / denom
                if np.isfinite(recovery_fraction) and recovery_fraction >= 0.5:
                    half_life[i] = (times[j] - times[i]) / NS
                    break
        d["replenishment_half_life_s"] = half_life
        for h in CLOCK_H:
            target = times + h * NS
            idx = np.searchsorted(times, target, side="right") - 1
            good = shock & (idx > np.arange(len(d)))
            recovery = np.full(len(d), np.nan)
            gi = np.flatnonzero(good)
            if len(gi):
                post = depth2[gi]
                future = depth2[idx[gi]]
                denom = pre[gi] - post
                okd = denom > 0
                rec = np.full(len(gi), np.nan)
                rec[okd] = (future[okd] - post[okd]) / denom[okd]
                recovery[gi] = rec
            d[f"recovery_h{h}"] = recovery
        parts.append(d[d["split"].notna()].copy())
    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()


def thin(frame: pd.DataFrame, n: int = MAX_MODEL_ROWS) -> pd.DataFrame:
    if len(frame) <= n:
        return frame
    step = int(math.ceil(len(frame) / n))
    return frame.iloc[::step].head(n).copy()


def signflip_p(block_values: np.ndarray, seed_key: str) -> float:
    x = block_values[np.isfinite(block_values)]
    if len(x) < 3:
        return 1.0
    observed = float(np.mean(x))
    if observed <= 0:
        return 1.0
    if len(x) <= 18:
        total = 1 << len(x)
        ge = 0
        for mask in range(total):
            signs = np.array([1.0 if (mask >> j) & 1 else -1.0 for j in range(len(x))])
            if float(np.mean(x * signs)) >= observed - 1e-15:
                ge += 1
        return ge / total
    seed = int.from_bytes(hashlib.sha256(f"{SEED}|{seed_key}".encode()).digest()[:8], "big")
    rng = np.random.default_rng(seed)
    ge = 0
    draws = 4096
    for _ in range(draws):
        signs = rng.choice(np.array([-1.0, 1.0]), size=len(x))
        ge += float(np.mean(x * signs)) >= observed - 1e-15
    return (ge + 1) / (draws + 1)


def bh(rows: list[dict[str, Any]]) -> None:
    groups: dict[tuple[str, str, str, str], list[int]] = defaultdict(list)
    for i, row in enumerate(rows):
        groups[(row["dataset"], row["regime"], row["target_family"], row["feature_family"])].append(i)
    for idxs in groups.values():
        ordered = sorted(idxs, key=lambda i: float(rows[i]["p_raw"]))
        m = len(ordered)
        adjusted = [1.0] * m
        running = 1.0
        max_rank = 0
        for rank, i in enumerate(ordered, 1):
            if float(rows[i]["p_raw"]) <= FDR_Q * rank / m:
                max_rank = rank
        for j in range(m - 1, -1, -1):
            rank = j + 1
            p = float(rows[ordered[j]]["p_raw"])
            running = min(running, p * m / rank)
            adjusted[j] = min(1.0, running)
        for rank, (i, p_adj) in enumerate(zip(ordered, adjusted), 1):
            rows[i]["p_bh"] = p_adj
            rows[i]["fdr_pass"] = rank <= max_rank


def feature_family(name: str) -> str:
    if name.startswith(("raw_", "record_", "repeated_", "ambiguous_", "genuine_raw_ratio_")):
        return "CAPTURE_PROCESS"
    if name.startswith(("genuine_", "state_age", "inter_event")):
        return "BBO_AGE"
    if name.startswith(("ofi", "snapshot_ofi")):
        return "OFI"
    if "microprice" in name or "imbalance_top" in name:
        return "MICROPRICE"
    if name.startswith(("depth_", "depth2_", "qbid", "qask")):
        return "DEPTH"
    if name.startswith("trade_"):
        return "TRADE_ACTIVITY"
    if name.startswith(("rv_", "activity_norm", "vol_of_vol", "abs_ret")):
        return "VOLATILITY"
    if "spread" in name:
        return "BBO_SPREAD"
    return "OTHER"


def screen_regression(
    frame: pd.DataFrame,
    dataset: str,
    regime: str,
    target_family: str,
    target_col: str,
    horizon: str,
    base_cols: list[str],
    candidates: list[str],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    needed_base = [c for c in base_cols if c in frame.columns]
    for candidate in candidates:
        if candidate not in frame.columns or candidate in needed_base:
            continue
        cols = ["split", "event", "token_id", "time", target_col, *needed_base, candidate]
        x = frame[cols].replace([np.inf, -np.inf], np.nan).dropna()
        tr = thin(x[x["split"] == "TRAIN"].sort_values(["time", "token_id"]))
        dv = thin(x[x["split"] == "DEV"].sort_values(["time", "token_id"]))
        if len(tr) < 500 or len(dv) < 200 or dv["token_id"].nunique() < 3:
            continue
        sx = StandardScaler()
        Xtr_b = sx.fit_transform(tr[needed_base]) if needed_base else np.zeros((len(tr), 0))
        Xdv_b = sx.transform(dv[needed_base]) if needed_base else np.zeros((len(dv), 0))
        ytr = tr[target_col].to_numpy(float)
        ydv = dv[target_col].to_numpy(float)
        base = Ridge(alpha=1.0).fit(Xtr_b, ytr) if needed_base else None
        pred_b = base.predict(Xdv_b) if base is not None else np.full(len(dv), ytr.mean())

        all_cols = needed_base + [candidate]
        sc = StandardScaler()
        Xtr = sc.fit_transform(tr[all_cols])
        Xdv = sc.transform(dv[all_cols])
        model = Ridge(alpha=1.0).fit(Xtr, ytr)
        pred = model.predict(Xdv)
        lb = (ydv - pred_b) ** 2
        lc = (ydv - pred) ** 2
        diff = lb - lc
        tmp = dv[["event", "time"]].copy()
        tmp["diff"] = diff
        tmp["block"] = tmp["time"].dt.floor("30min")
        blocks = tmp.groupby(["event", "block"], observed=True)["diff"].mean().to_numpy(float)
        p = signflip_p(blocks, f"{dataset}|{regime}|{target_col}|{candidate}")
        delta = float(np.mean(diff))
        positive_blocks = int(np.sum(blocks > 0))
        rows.append({
            "dataset": dataset,
            "regime": regime,
            "target_family": target_family,
            "target": target_col,
            "horizon": horizon,
            "feature": candidate,
            "feature_family": feature_family(candidate),
            "train_n": len(tr),
            "dev_n": len(dv),
            "markets": int(dv["token_id"].nunique()),
            "events": int(dv["event"].nunique()),
            "blocks": len(blocks),
            "positive_blocks": positive_blocks,
            "baseline_mse": float(np.mean(lb)),
            "challenger_mse": float(np.mean(lc)),
            "delta_mse": delta,
            "coef_std": float(model.coef_[-1]),
            "p_raw": p,
        })
    return rows



def screen_feature_block(
    frame: pd.DataFrame,
    regime: str,
    target_col: str,
    horizon: str,
    block_name: str,
    block_cols: list[str],
) -> list[dict[str, Any]]:
    base_cols = [c for c in ("ret_15", "ret_30", "ret_60") if c in frame.columns]
    challenger = [c for c in block_cols if c in frame.columns and c not in base_cols]
    if not challenger:
        return []
    cols = ["split", "event", "token_id", "time", target_col, *base_cols, *challenger]
    x = frame[cols].replace([np.inf, -np.inf], np.nan).dropna()
    tr = thin(x[x["split"] == "TRAIN"].sort_values(["time", "token_id"]))
    dv = thin(x[x["split"] == "DEV"].sort_values(["time", "token_id"]))
    if len(tr) < 500 or len(dv) < 200 or dv["token_id"].nunique() < 3:
        return []
    ytr = tr[target_col].to_numpy(float)
    ydv = dv[target_col].to_numpy(float)

    if base_cols:
        sb = StandardScaler().fit(tr[base_cols])
        Xtr_b, Xdv_b = sb.transform(tr[base_cols]), sb.transform(dv[base_cols])
        base = Ridge(alpha=1.0).fit(Xtr_b, ytr)
        pred_b = base.predict(Xdv_b)
    else:
        pred_b = np.full(len(dv), ytr.mean())

    all_cols = base_cols + challenger
    sc = StandardScaler().fit(tr[all_cols])
    Xtr, Xdv = sc.transform(tr[all_cols]), sc.transform(dv[all_cols])
    model = Ridge(alpha=1.0).fit(Xtr, ytr)
    pred = model.predict(Xdv)
    lb = (ydv - pred_b) ** 2
    lc = (ydv - pred) ** 2
    diff = lb - lc
    tmp = dv[["event", "time"]].copy()
    tmp["diff"] = diff
    tmp["block"] = tmp["time"].dt.floor("30min")
    blocks = tmp.groupby(["event", "block"], observed=True)["diff"].mean().to_numpy(float)
    p = signflip_p(blocks, f"depth|{regime}|{target_col}|{block_name}")
    return [{
        "dataset": "depth",
        "regime": regime,
        "target_family": "MICRO_FV",
        "target": target_col,
        "horizon": horizon,
        "feature": block_name,
        "feature_family": "MICRO_FV_BLOCK",
        "train_n": len(tr),
        "dev_n": len(dv),
        "markets": int(dv["token_id"].nunique()),
        "events": int(dv["event"].nunique()),
        "blocks": len(blocks),
        "positive_blocks": int(np.sum(blocks > 0)),
        "baseline_mse": float(np.mean(lb)),
        "challenger_mse": float(np.mean(lc)),
        "delta_mse": float(np.mean(diff)),
        "coef_std": np.nan,
        "p_raw": p,
        "block_columns": "|".join(challenger),
    }]


def screen_classification(
    frame: pd.DataFrame,
    dataset: str,
    regime: str,
    target_family: str,
    target_col: str,
    horizon: str,
    base_cols: list[str],
    candidates: list[str],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    needed_base = [c for c in base_cols if c in frame.columns]
    for candidate in candidates:
        if candidate not in frame.columns or candidate in needed_base:
            continue
        cols = ["split", "event", "token_id", "time", target_col, *needed_base, candidate]
        x = frame[cols].replace([np.inf, -np.inf], np.nan).dropna()
        tr = thin(x[x["split"] == "TRAIN"].sort_values(["time", "token_id"]))
        dv = thin(x[x["split"] == "DEV"].sort_values(["time", "token_id"]))
        if (
            len(tr) < 500 or len(dv) < 200
            or tr[target_col].nunique() < 2 or dv[target_col].nunique() < 2
            or dv["token_id"].nunique() < 3
        ):
            continue
        ytr = tr[target_col].to_numpy(int)
        ydv = dv[target_col].to_numpy(int)

        if needed_base:
            sb = StandardScaler().fit(tr[needed_base])
            Xtr_b, Xdv_b = sb.transform(tr[needed_base]), sb.transform(dv[needed_base])
            base = LogisticRegression(C=1.0, max_iter=500, random_state=SKLEARN_SEED).fit(Xtr_b, ytr)
            pred_b = base.predict_proba(Xdv_b)[:, 1]
        else:
            pred_b = np.full(len(dv), ytr.mean())

        all_cols = needed_base + [candidate]
        sc = StandardScaler().fit(tr[all_cols])
        Xtr, Xdv = sc.transform(tr[all_cols]), sc.transform(dv[all_cols])
        model = LogisticRegression(C=1.0, max_iter=500, random_state=SKLEARN_SEED).fit(Xtr, ytr)
        pred = model.predict_proba(Xdv)[:, 1]
        lb = (ydv - pred_b) ** 2
        lc = (ydv - pred) ** 2
        diff = lb - lc
        tmp = dv[["event", "time"]].copy()
        tmp["diff"] = diff
        tmp["block"] = tmp["time"].dt.floor("30min")
        blocks = tmp.groupby(["event", "block"], observed=True)["diff"].mean().to_numpy(float)
        p = signflip_p(blocks, f"{dataset}|{regime}|{target_col}|{candidate}|hazard")
        rows.append({
            "dataset": dataset,
            "regime": regime,
            "target_family": target_family,
            "target": target_col,
            "horizon": horizon,
            "feature": candidate,
            "feature_family": feature_family(candidate),
            "train_n": len(tr),
            "dev_n": len(dv),
            "markets": int(dv["token_id"].nunique()),
            "events": int(dv["event"].nunique()),
            "blocks": len(blocks),
            "positive_blocks": int(np.sum(blocks > 0)),
            "baseline_mse": float(np.mean(lb)),
            "challenger_mse": float(np.mean(lc)),
            "delta_mse": float(np.mean(diff)),
            "coef_std": float(model.coef_[0, -1]),
            "p_raw": p,
            "loss_metric": "BRIER",
        })
    return rows

def model_tournament(
    frame: pd.DataFrame,
    selected: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for sel in selected:
        target = sel["target"]
        regime = sel["regime"]
        candidate = sel["feature"]
        dataset = sel["dataset"]
        sub = frame[frame["regime"] == regime].copy()
        if dataset == "clock" and str(target).startswith("update_h"):
            base_cols = ["genuine_15", "genuine_60"]
            classification = True
        elif dataset == "clock" and str(target).startswith("jump_h"):
            base_cols = ["abs_ret_15", "rv_60"]
            classification = True
        elif dataset == "clock":
            base_cols = ["ret_15", "ret_30", "ret_60"]
            classification = False
        elif dataset == "event":
            base_cols = ["event_ret1", "event_ret2"]
            classification = False
        else:
            base_cols = ["ret_15", "ret_30", "ret_60"]
            classification = False
        base_cols = [c for c in base_cols if c in sub.columns]
        if dataset == "depth" and candidate in MICROFV_BLOCKS:
            candidate_cols = [
                c for c in MICROFV_BLOCKS[candidate]
                if c in sub.columns and c not in base_cols
            ]
        else:
            candidate_cols = [candidate] if candidate in sub.columns and candidate not in base_cols else []
        cols = base_cols + candidate_cols
        if not candidate_cols:
            continue
        x = sub[["split", target, *cols]].replace([np.inf, -np.inf], np.nan).dropna()
        tr = thin(x[x["split"] == "TRAIN"])
        dv = thin(x[x["split"] == "DEV"])
        if len(tr) < 500 or len(dv) < 200:
            continue
        scaler = StandardScaler().fit(tr[cols])
        Xtr, Xdv = scaler.transform(tr[cols]), scaler.transform(dv[cols])
        if classification:
            ytr = tr[target].to_numpy(int)
            ydv = dv[target].to_numpy(int)
            if len(np.unique(ytr)) < 2 or len(np.unique(ydv)) < 2:
                continue
            configs: list[tuple[str, Any]] = [
                (f"LOGIT_C{value}", LogisticRegression(C=value, max_iter=500, random_state=SKLEARN_SEED))
                for value in (0.1, 1.0, 10.0)
            ]
            configs += [
                (
                    f"HGB_D{depth}_LR{lr}",
                    HistGradientBoostingClassifier(
                        max_depth=depth, learning_rate=lr, max_iter=200, random_state=SKLEARN_SEED
                    ),
                )
                for depth in (2, 3) for lr in (0.03, 0.1)
            ]
            for name, model in configs:
                model.fit(Xtr, ytr)
                pred = model.predict_proba(Xdv)[:, 1]
                rows.append({
                    "dataset": dataset, "regime": regime, "target": target,
                    "feature": candidate, "model": name,
                    "dev_mse": float(np.mean((ydv - pred) ** 2)),
                    "dev_mae": float(np.mean(np.abs(ydv - pred))),
                    "loss_metric": "BRIER",
                })
        else:
            ytr = tr[target].to_numpy(float)
            ydv = dv[target].to_numpy(float)
            configs = [("OLS", LinearRegression())]
            configs += [(f"RIDGE_{a}", Ridge(alpha=a)) for a in (0.1, 1.0, 10.0)]
            for a in (0.001, 0.01, 0.1):
                for l1 in (0.1, 0.5, 0.9):
                    configs.append((f"ENET_{a}_{l1}", ElasticNet(alpha=a, l1_ratio=l1, max_iter=2000)))
            configs += [
                (
                    f"HGB_D{depth}_LR{lr}",
                    HistGradientBoostingRegressor(
                        max_depth=depth, learning_rate=lr, max_iter=200, random_state=SKLEARN_SEED
                    ),
                )
                for depth in (2, 3) for lr in (0.03, 0.1)
            ]
            for name, model in configs:
                model.fit(Xtr, ytr)
                pred = model.predict(Xdv)
                rows.append({
                    "dataset": dataset, "regime": regime, "target": target,
                    "feature": candidate, "model": name,
                    "dev_mse": float(np.mean((ydv - pred) ** 2)),
                    "dev_mae": float(np.mean(np.abs(ydv - pred))),
                    "loss_metric": "MSE",
                })
    return rows


def main() -> None:
    manifest_path = one("corpus_manifest.json")
    identity_path = one("market_identity.csv")
    quality_path = one("corpus_quality.json")
    if sha256(manifest_path) != EXPECTED_MANIFEST:
        raise RuntimeError("DATA-001 manifest mismatch")
    if sha256(identity_path) != EXPECTED_IDENTITY:
        raise RuntimeError("DATA-001 identity mismatch")
    if sha256(quality_path) != EXPECTED_QUALITY:
        raise RuntimeError("DATA-001 quality mismatch")
    manifest = json.loads(manifest_path.read_text())
    corpus = manifest_path.parent

    all_clock: list[pd.DataFrame] = []
    all_event: list[pd.DataFrame] = []
    all_depth: list[pd.DataFrame] = []
    audits: list[dict[str, Any]] = []
    resilience: list[dict[str, Any]] = []

    for event, meta in REGIME_WINDOWS.items():
        tokens, market_map, market_family = yes_tokens(identity_path, event)
        for regime in CLAIM_REGIMES:
            start_s, end_s = meta[regime]
            start, end = dt(start_s), dt(end_s)
            bounds = split_bounds(start, end)
            empirical_end = bounds["DEV"][1]
            book_root = corpus / event / "books" / "book_changes"
            depth_root = corpus / event / "books" / "depth_snapshots"
            trade_root = corpus / event / "books" / "trades"
            states, captures, audit = load_book_state(book_root, tokens, start, empirical_end)
            audits.append({
                "event": event, "event_family": meta["family"], "regime": regime,
                "start": start.isoformat(), "empirical_end": empirical_end.isoformat(),
                "holdout_read": False, **audit,
            })
            if states.empty:
                continue
            trade_features = load_trade_features(trade_root, tokens, start, empirical_end, states)
            clock = build_clock(event, regime, states, captures, market_map, start, end)
            if not clock.empty:
                clock = attach_trade_features(clock, trade_features)
                clock["trade_vs_quote_60"] = clock.get("trade_count_60", np.nan) / np.maximum(clock["genuine_60"], 1.0)
                all_clock.append(clock)
            ev = build_event_time(event, regime, states, market_map, start, end)
            if not ev.empty:
                ev = attach_trade_features(ev, trade_features)
                all_event.append(ev)
            depth = load_depth(depth_root, tokens, start, empirical_end)
            depth = attach_depth_targets(depth, states, event, regime, market_map, start, end)
            if not depth.empty:
                depth = attach_trade_features(depth, trade_features)
                all_depth.append(depth)
                shocks = depth[depth["large_depth_shock"]]
                for h in CLOCK_H:
                    col = f"recovery_h{h}"
                    vals = shocks[col].dropna() if col in shocks else pd.Series(dtype=float)
                    resilience.append({
                        "event": event, "regime": regime, "horizon_s": h,
                        "shock_rows": int(len(shocks)), "measured_recoveries": int(len(vals)),
                        "mean_recovery_fraction": float(vals.mean()) if len(vals) else np.nan,
                        "median_recovery_fraction": float(vals.median()) if len(vals) else np.nan,
                    })

    clock = pd.concat(all_clock, ignore_index=True) if all_clock else pd.DataFrame()
    event_df = pd.concat(all_event, ignore_index=True) if all_event else pd.DataFrame()
    depth_df = pd.concat(all_depth, ignore_index=True) if all_depth else pd.DataFrame()

    # TRAIN-only jump threshold, then fixed on DEV.
    if not clock.empty:
        clock["jump_state"] = 0.0
        for regime in CLAIM_REGIMES:
            tr = clock[(clock["regime"] == regime) & (clock["split"] == "TRAIN")]
            threshold = float(tr["abs_ret_15"].quantile(0.95)) if len(tr) else np.nan
            mask = clock["regime"] == regime
            clock.loc[mask, "jump_state"] = (clock.loc[mask, "abs_ret_15"] >= threshold).astype(float)
            audits.append({"event": "__ALL__", "regime": regime, "metric": "train_jump_threshold_abs_logit15", "value": threshold})
            regime_mask = clock["regime"] == regime
            for h in CLOCK_H:
                source = clock.loc[regime_mask, f"abs_h{h}"]
                clock.loc[regime_mask, f"jump_h{h}"] = np.where(
                    source.notna(), (source >= threshold).astype(float), np.nan
                )

    screen: list[dict[str, Any]] = []
    clock_candidates = [
        "spread", "state_age_s", "genuine_age_s", "raw_capture_bin_age_s", "raw_15", "raw_60", "raw_300",
        "repeated_15", "repeated_60", "genuine_15", "genuine_60", "genuine_300",
        "genuine_raw_ratio_15", "genuine_raw_ratio_60", "genuine_raw_ratio_300",
        "rv_60", "rv_300", "activity_norm_vol_60", "vol_of_vol_300", "jump_state",
        "trade_count_15", "trade_count_60", "trade_count_300",
        "trade_value_15", "trade_value_60", "trade_size_mean_60", "trade_size_max_60",
        "trade_interarrival_median_60", "trade_burstiness_60", "trade_activity_accel",
        "trade_abs_impact_60", "trade_vs_quote_60",
    ]
    event_candidates = [
        "spread", "inter_event_s", "ofi", "ofi_norm", "ofi_sum_5s", "ofi_sum_30s", "ofi_ewma_5s", "ofi_ewma_30s", "imbalance_top",
        "microprice_disp_over_spread", "qbid", "qask",
        "trade_count_15", "trade_count_60", "trade_value_60",
        "trade_interarrival_median_60", "trade_burstiness_60", "trade_activity_accel",
        "trade_abs_impact_60",
    ]
    depth_candidates = [
        "spread", "imbalance_top", "microprice_disp_over_spread",
        "snapshot_ofi", "snapshot_ofi_norm", "snapshot_interval_s",
        "depth_1c", "depth_2c", "depth_5c",
        "depth_imbalance_1c", "depth_imbalance_2c", "depth_imbalance_5c",
        "depth_concentration_1c_5c", "depth_slope_1c_5c", "depth2_log_change",
        "buy_impact_q10", "sell_impact_q10", "buy_impact_q50", "sell_impact_q50",
        "buy_impact_q100", "sell_impact_q100", "buy_impact_q500", "sell_impact_q500",
    ]
    for regime in CLAIM_REGIMES:
        if not clock.empty:
            f = clock[clock["regime"] == regime]
            for h in CLOCK_H:
                screen += screen_regression(f, "clock", regime, "PRICE", f"price_h{h}", str(h), ["ret_15", "ret_30", "ret_60"], clock_candidates)
                screen += screen_regression(f, "clock", regime, "VOLATILITY", f"abs_h{h}", str(h), ["abs_ret_15", "rv_60"], clock_candidates)
                screen += screen_regression(f, "clock", regime, "LIQUIDITY", f"spread_h{h}", str(h), ["spread", "ret_15"], clock_candidates)
                screen += screen_classification(f, "clock", regime, "UPDATE_HAZARD", f"update_h{h}", str(h), ["genuine_15", "genuine_60"], clock_candidates)
                screen += screen_classification(f, "clock", regime, "JUMP_HAZARD", f"jump_h{h}", str(h), ["abs_ret_15", "rv_60"], clock_candidates)
        if not event_df.empty:
            f = event_df[event_df["regime"] == regime]
            for k in EVENT_H:
                screen += screen_regression(f, "event", regime, "PRICE_EVENT", f"event_price_k{k}", str(k), ["event_ret1", "event_ret2"], event_candidates)
                screen += screen_regression(f, "event", regime, "VOL_EVENT", f"event_abs_k{k}", str(k), ["event_ret1", "event_ret2"], event_candidates)
        if not depth_df.empty:
            f = depth_df[depth_df["regime"] == regime]
            for h in CLOCK_H:
                screen += screen_regression(f, "depth", regime, "MICRO_FV", f"depth_price_h{h}", str(h), ["ret_15", "ret_30", "ret_60"], depth_candidates)
                for block_name, block_cols in MICROFV_BLOCKS.items():
                    screen += screen_feature_block(
                        f, regime, f"depth_price_h{h}", str(h), block_name, block_cols
                    )
                screen += screen_regression(f, "depth", regime, "DEPTH_VOL", f"depth_abs_h{h}", str(h), ["ret_15", "ret_30", "ret_60"], depth_candidates)

    bh(screen)
    for row in screen:
        row["support_pass"] = int(row["markets"]) >= 10
        row["stability_pass"] = int(row["positive_blocks"]) >= 3
        row["positive_lift"] = float(row["delta_mse"]) > 0
        row["confirmatory_dev_candidate"] = bool(
            row["support_pass"] and row["stability_pass"] and row["positive_lift"] and row.get("fdr_pass", False)
        )

    selected: list[dict[str, Any]] = []
    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in screen:
        grouped[(row["dataset"], row["regime"], row["target_family"])].append(row)
    for key, rows in grouped.items():
        confirm = [r for r in rows if r["confirmatory_dev_candidate"]]
        if confirm:
            best = max(confirm, key=lambda r: float(r["delta_mse"]))
            selected.append({**best, "selection_class": "CONFIRMATORY_ELIGIBLE"})
        else:
            shadow = [
                r for r in rows
                if r["support_pass"] and r["stability_pass"] and r["positive_lift"]
            ]
            if shadow:
                best = max(shadow, key=lambda r: float(r["delta_mse"]))
                selected.append({**best, "selection_class": "SHADOW_ONLY_NO_FDR"})

    # Small model tournament only on already selected DEV coordinates.
    tournament: list[dict[str, Any]] = []
    for dataset, frame in (("clock", clock), ("event", event_df), ("depth", depth_df)):
        subset = [s for s in selected if s["dataset"] == dataset]
        if subset and not frame.empty:
            tournament += model_tournament(frame, subset)

    # For each selected coordinate, freeze the best DEV model; no holdout data was loaded.
    freeze_candidates: list[dict[str, Any]] = []
    for sel in selected:
        models = [
            r for r in tournament
            if r["dataset"] == sel["dataset"]
            and r["regime"] == sel["regime"]
            and r["target"] == sel["target"]
            and r["feature"] == sel["feature"]
        ]
        best_model = min(models, key=lambda r: r["dev_mse"]) if models else None
        freeze_candidates.append({
            "dataset": sel["dataset"],
            "regime": sel["regime"],
            "target_family": sel["target_family"],
            "target": sel["target"],
            "horizon": sel["horizon"],
            "feature": sel["feature"],
            "feature_family": sel["feature_family"],
            "selection_class": sel["selection_class"],
            "dev_delta_mse": sel["delta_mse"],
            "dev_p_raw": sel["p_raw"],
            "dev_p_bh": sel.get("p_bh"),
            "markets": sel["markets"],
            "events": sel["events"],
            "blocks": sel["blocks"],
            "model": best_model["model"] if best_model else (
                "LOGIT_C1.0" if str(sel["target"]).startswith(("update_h", "jump_h")) else "RIDGE_1.0"
            ),
            "model_dev_mse": best_model["dev_mse"] if best_model else sel["challenger_mse"],
        })

    write_csv(WORK / "book_state_capture_audit.csv", audits)
    write_csv(WORK / "train_dev_feature_screen.csv", screen)
    write_csv(WORK / "dev_model_tournament.csv", tournament)
    write_csv(WORK / "resilience_summary.csv", resilience)
    (WORK / "dev_selection.json").write_text(json.dumps({
        "experiment": "EXPERIMENT-005F",
        "phase": "TRAIN_DEV",
        "design_freeze_commit": DESIGN_FREEZE_COMMIT,
        "execution_freeze_commit": EXECUTION_FREEZE_COMMIT,
        "holdout_read": False,
        "selection_rule": "confirmatory FDR/support/stability first; otherwise shadow-only positive support/stability",
        "candidates": freeze_candidates,
    }, indent=2, sort_keys=True))
    (WORK / "feature_atlas.json").write_text(json.dumps({
        "clock_candidates": clock_candidates,
        "event_candidates": event_candidates,
        "depth_candidates": depth_candidates,
        "microfv_blocks": MICROFV_BLOCKS,
        "trade_features_enabled": True,
        "clock_horizons_seconds": CLOCK_H,
        "event_horizons": EVENT_H,
        "clock_grid_seconds": GRID_SECONDS,
    }, indent=2, sort_keys=True))
    (WORK / "target_atlas.json").write_text(json.dumps({
        "clock": ["price_logit_change", "absolute_logit_change", "spread_change", "genuine_update_hazard"],
        "event": ["event_logit_change", "event_absolute_logit_change", "event_spread_change"],
        "depth": ["future_logit_change", "future_absolute_logit_change", "observable_recovery_fraction"],
    }, indent=2, sort_keys=True))
    (WORK / "provenance.json").write_text(json.dumps({
        "runner_sha256": sha256(Path(__file__)),
        "data_manifest_sha256": sha256(manifest_path),
        "identity_sha256": sha256(identity_path),
        "quality_sha256": sha256(quality_path),
        "design_freeze_commit": DESIGN_FREEZE_COMMIT,
        "execution_freeze_commit": EXECUTION_FREEZE_COMMIT,
        "holdout_read": False,
        "empirical_end_policy": "per event/regime DEV boundary only",
        "namespace": "005f_microstructure_liquidity_atlas",
    }, indent=2, sort_keys=True))

    summary = {
        "clock_rows": len(clock),
        "event_rows": len(event_df),
        "depth_rows": len(depth_df),
        "screen_cells": len(screen),
        "selected_coordinates": len(freeze_candidates),
        "confirmatory_candidates": sum(c["selection_class"] == "CONFIRMATORY_ELIGIBLE" for c in freeze_candidates),
        "shadow_only_candidates": sum(c["selection_class"] == "SHADOW_ONLY_NO_FDR" for c in freeze_candidates),
        "holdout_read": False,
    }
    (WORK / "train_dev_summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True))
    print(json.dumps(summary, sort_keys=True))



CANONICAL_TRAIN_DEV_FREEZE = json.loads(r"""{
  "schema_version": 1,
  "experiment": "EXPERIMENT-005F",
  "phase": "CANONICAL_TRAIN_DEV_FREEZE",
  "status": "FROZEN_BEFORE_FIT_FREEZE",
  "holdout_read": false,
  "design_freeze_commit": "b5bf4cfe7482a58161523b66097910903050fc6b",
  "execution_freeze_commit": "9ec9511536514bbaa97929a4b88d701cbc09987c",
  "start_commit": "ba938bedcf63f562be8b26c9502e828391123867",
  "source_freezes": [
    {
      "path": "data/experiments/experiment_005f/active_train_dev_freeze.json",
      "freeze_commit": "f6714f20618c5f2d2bb80b9992826eff9dbe7cf3",
      "blob_sha": "cb6b650c670f4f7caaad621e6f938bfddab8c539",
      "source_kernel": "polyleviathan/005f-microstructure-liquidity-active-train-dev",
      "source_kernel_version": 1,
      "source_runner_sha256": "43a21ea5047038a7f02c6d8b43ba3c11f4bc487e6a19e58e35f6c27ed96691d7"
    },
    {
      "path": "data/experiments/experiment_005f/pre_nonclock_train_dev_freeze.json",
      "freeze_commit": "775242e",
      "blob_sha": "5dc47ca2e618d9f6693b2c74b99006306a1f413d",
      "source_kernel": "polyleviathan/005f-pre-nonclock-train-dev",
      "source_kernel_version": 1,
      "source_runner_sha256": "92f73c50f8408a4634b7500837363d0fc683d0dc601f38b4d281722cb0c572de"
    },
    {
      "path": "data/experiments/experiment_005f/pre_clock_train_dev_freeze.json",
      "freeze_commit": "132d5ac5db3754eb62fc7c454aefd7e0c0104664",
      "blob_sha": "4e63bec1e8305d402c8217c1463d21d4a5bb91b2",
      "source_kernel": "polyleviathan/005f-pre-clock-train-dev",
      "source_kernel_version": 1,
      "source_runner_sha256": "a7a6611ebf62bb70c04764b2c29f31d018fd0a595a49c7e4ffd9eb04bebed652"
    }
  ],
  "governing_protocol": "docs/experiments/EXPERIMENT_005F_HOLDOUT_PROTOCOL.md",
  "governing_amendments": [
    "005F-AMENDMENT-001",
    "005F-AMENDMENT-002",
    "005F-AMENDMENT-003",
    "005F-AMENDMENT-004",
    "005F-AMENDMENT-005",
    "005F-AMENDMENT-006",
    "005F-AMENDMENT-007",
    "005F-AMENDMENT-008",
    "005F-AMENDMENT-009"
  ],
  "integrity": {
    "source_freezes": 3,
    "selected_coordinates": 17,
    "unique_candidate_groups": 17,
    "exact_unique_groups": true,
    "confirmatory_candidates": 14,
    "shadow_only_candidates": 3,
    "regimes": [
      "ACTIVE_RESULTS",
      "PRE_ELECTION"
    ],
    "datasets": [
      "clock",
      "depth",
      "event"
    ]
  },
  "candidates": [
    {
      "dataset": "clock",
      "target_family": "JUMP_HAZARD",
      "target": "jump_h300",
      "horizon": "300",
      "feature": "genuine_age_s",
      "feature_family": "BBO_AGE",
      "selection_class": "CONFIRMATORY_ELIGIBLE",
      "model": "HGB_D2_LR0.03",
      "dev_delta_mse": 0.023896650845006605,
      "dev_p_raw": 0.00048828125,
      "dev_p_bh": 0.0009494357638888889,
      "markets": 60,
      "events": 5,
      "blocks": 15,
      "regime": "ACTIVE_RESULTS",
      "candidate_id": "ACTIVE_RESULTS|clock|JUMP_HAZARD",
      "source_freeze": "active_train_dev_freeze.json"
    },
    {
      "dataset": "clock",
      "target_family": "LIQUIDITY",
      "target": "spread_h300",
      "horizon": "300",
      "feature": "trade_burstiness_60",
      "feature_family": "TRADE_ACTIVITY",
      "selection_class": "SHADOW_ONLY_NO_FDR",
      "model": "HGB_D2_LR0.03",
      "dev_delta_mse": 1.8193849154844839e-7,
      "dev_p_raw": 0.25,
      "dev_p_bh": 1,
      "markets": 15,
      "events": 2,
      "blocks": 6,
      "regime": "ACTIVE_RESULTS",
      "candidate_id": "ACTIVE_RESULTS|clock|LIQUIDITY",
      "source_freeze": "active_train_dev_freeze.json"
    },
    {
      "dataset": "clock",
      "target_family": "PRICE",
      "target": "price_h300",
      "horizon": "300",
      "feature": "genuine_age_s",
      "feature_family": "BBO_AGE",
      "selection_class": "SHADOW_ONLY_NO_FDR",
      "model": "HGB_D2_LR0.1",
      "dev_delta_mse": 0.00016227114924507633,
      "dev_p_raw": 0.16021728515625,
      "dev_p_bh": 0.591689400050951,
      "markets": 59,
      "events": 5,
      "blocks": 15,
      "regime": "ACTIVE_RESULTS",
      "candidate_id": "ACTIVE_RESULTS|clock|PRICE",
      "source_freeze": "active_train_dev_freeze.json"
    },
    {
      "dataset": "clock",
      "target_family": "UPDATE_HAZARD",
      "target": "update_h300",
      "horizon": "300",
      "feature": "genuine_age_s",
      "feature_family": "BBO_AGE",
      "selection_class": "CONFIRMATORY_ELIGIBLE",
      "model": "HGB_D2_LR0.1",
      "dev_delta_mse": 0.06915634775996235,
      "dev_p_raw": 0.000030517578125,
      "dev_p_bh": 0.000080108642578125,
      "markets": 64,
      "events": 5,
      "blocks": 15,
      "regime": "ACTIVE_RESULTS",
      "candidate_id": "ACTIVE_RESULTS|clock|UPDATE_HAZARD",
      "source_freeze": "active_train_dev_freeze.json"
    },
    {
      "dataset": "clock",
      "target_family": "VOLATILITY",
      "target": "abs_h300",
      "horizon": "300",
      "feature": "rv_300",
      "feature_family": "VOLATILITY",
      "selection_class": "CONFIRMATORY_ELIGIBLE",
      "model": "HGB_D3_LR0.03",
      "dev_delta_mse": 0.00033771860769266375,
      "dev_p_raw": 0.005096435546875,
      "dev_p_bh": 0.02675628662109375,
      "markets": 87,
      "events": 5,
      "blocks": 15,
      "regime": "ACTIVE_RESULTS",
      "candidate_id": "ACTIVE_RESULTS|clock|VOLATILITY",
      "source_freeze": "active_train_dev_freeze.json"
    },
    {
      "dataset": "depth",
      "target_family": "DEPTH_VOL",
      "target": "depth_abs_h5",
      "horizon": "5",
      "feature": "snapshot_ofi_norm",
      "feature_family": "OFI",
      "selection_class": "CONFIRMATORY_ELIGIBLE",
      "model": "ENET_0.01_0.9",
      "dev_delta_mse": 3.200823393658753e-7,
      "dev_p_raw": 0.000030517578125,
      "dev_p_bh": 0.00042724609375,
      "markets": 20,
      "events": 5,
      "blocks": 16,
      "regime": "ACTIVE_RESULTS",
      "candidate_id": "ACTIVE_RESULTS|depth|DEPTH_VOL",
      "source_freeze": "active_train_dev_freeze.json"
    },
    {
      "dataset": "depth",
      "target_family": "MICRO_FV",
      "target": "depth_price_h60",
      "horizon": "60",
      "feature": "snapshot_ofi_norm",
      "feature_family": "OFI",
      "selection_class": "CONFIRMATORY_ELIGIBLE",
      "model": "ENET_0.01_0.5",
      "dev_delta_mse": 0.000009563034559182842,
      "dev_p_raw": 0.01220703125,
      "dev_p_bh": 0.08544921875,
      "markets": 19,
      "events": 5,
      "blocks": 14,
      "regime": "ACTIVE_RESULTS",
      "candidate_id": "ACTIVE_RESULTS|depth|MICRO_FV",
      "source_freeze": "active_train_dev_freeze.json"
    },
    {
      "dataset": "event",
      "target_family": "PRICE_EVENT",
      "target": "event_price_k2",
      "horizon": "2",
      "feature": "qbid",
      "feature_family": "DEPTH",
      "selection_class": "CONFIRMATORY_ELIGIBLE",
      "model": "HGB_D2_LR0.03",
      "dev_delta_mse": 0.00000843779178971621,
      "dev_p_raw": 0.001953125,
      "dev_p_bh": 0.010986328125,
      "markets": 20,
      "events": 5,
      "blocks": 13,
      "regime": "ACTIVE_RESULTS",
      "candidate_id": "ACTIVE_RESULTS|event|PRICE_EVENT",
      "source_freeze": "active_train_dev_freeze.json"
    },
    {
      "dataset": "event",
      "target_family": "VOL_EVENT",
      "target": "event_abs_k1",
      "horizon": "1",
      "feature": "qbid",
      "feature_family": "DEPTH",
      "selection_class": "CONFIRMATORY_ELIGIBLE",
      "model": "HGB_D2_LR0.1",
      "dev_delta_mse": 0.00006945307690587753,
      "dev_p_raw": 0.00140380859375,
      "dev_p_bh": 0.01123046875,
      "markets": 22,
      "events": 5,
      "blocks": 14,
      "regime": "ACTIVE_RESULTS",
      "candidate_id": "ACTIVE_RESULTS|event|VOL_EVENT",
      "source_freeze": "active_train_dev_freeze.json"
    },
    {
      "blocks": 74,
      "dataset": "clock",
      "dev_delta_mse": 2.672134591463433e-8,
      "dev_p_bh": 0.01594662761370108,
      "dev_p_raw": 0.000976324139614352,
      "events": 3,
      "feature": "trade_abs_impact_60",
      "feature_family": "TRADE_ACTIVITY",
      "horizon": "15",
      "markets": 55,
      "model": "HGB_D3_LR0.03",
      "model_dev_mse": 0.000013597929853197555,
      "regime": "PRE_ELECTION",
      "selection_class": "CONFIRMATORY_ELIGIBLE",
      "target": "spread_h15",
      "target_family": "LIQUIDITY",
      "candidate_id": "PRE_ELECTION|clock|LIQUIDITY",
      "source_freeze": "pre_clock_train_dev_freeze.json"
    },
    {
      "blocks": 167,
      "dataset": "clock",
      "dev_delta_mse": 0.000032302796158321856,
      "dev_p_bh": 0.0854283622162558,
      "dev_p_raw": 0.0122040517451794,
      "events": 5,
      "feature": "rv_60",
      "feature_family": "VOLATILITY",
      "horizon": "300",
      "markets": 129,
      "model": "RIDGE_10.0",
      "model_dev_mse": 0.004853728530594327,
      "regime": "PRE_ELECTION",
      "selection_class": "CONFIRMATORY_ELIGIBLE",
      "target": "price_h300",
      "target_family": "PRICE",
      "candidate_id": "PRE_ELECTION|clock|PRICE",
      "source_freeze": "pre_clock_train_dev_freeze.json"
    },
    {
      "blocks": 167,
      "dataset": "clock",
      "dev_delta_mse": 0.036235622649057164,
      "dev_p_bh": 0.0003015118666456087,
      "dev_p_raw": 0.000244081034903588,
      "events": 5,
      "feature": "genuine_age_s",
      "feature_family": "BBO_AGE",
      "horizon": "300",
      "markets": 103,
      "model": "HGB_D3_LR0.03",
      "model_dev_mse": 0.08759374372392721,
      "regime": "PRE_ELECTION",
      "selection_class": "CONFIRMATORY_ELIGIBLE",
      "target": "update_h300",
      "target_family": "UPDATE_HAZARD",
      "candidate_id": "PRE_ELECTION|clock|UPDATE_HAZARD",
      "source_freeze": "pre_clock_train_dev_freeze.json"
    },
    {
      "blocks": 167,
      "dataset": "clock",
      "dev_delta_mse": 0.00032907464230877865,
      "dev_p_bh": 0.02255308762509153,
      "dev_p_raw": 0.010739565535757872,
      "events": 5,
      "feature": "rv_300",
      "feature_family": "VOLATILITY",
      "horizon": "300",
      "markets": 129,
      "model": "OLS",
      "model_dev_mse": 0.00432796234066078,
      "regime": "PRE_ELECTION",
      "selection_class": "CONFIRMATORY_ELIGIBLE",
      "target": "abs_h300",
      "target_family": "VOLATILITY",
      "candidate_id": "PRE_ELECTION|clock|VOLATILITY",
      "source_freeze": "pre_clock_train_dev_freeze.json"
    },
    {
      "blocks": 167,
      "dataset": "depth",
      "dev_delta_mse": 0.00006274435212166938,
      "dev_p_bh": 0.0008093213262592655,
      "dev_p_raw": 0.000244081034903588,
      "events": 5,
      "feature": "depth_slope_1c_5c",
      "feature_family": "DEPTH",
      "horizon": "300",
      "markets": 112,
      "model": "HGB_D2_LR0.1",
      "model_dev_mse": 0.002735024373355307,
      "regime": "PRE_ELECTION",
      "selection_class": "CONFIRMATORY_ELIGIBLE",
      "target": "depth_abs_h300",
      "target_family": "DEPTH_VOL",
      "candidate_id": "PRE_ELECTION|depth|DEPTH_VOL",
      "source_freeze": "pre_nonclock_train_dev_freeze.json"
    },
    {
      "blocks": 167,
      "dataset": "depth",
      "dev_delta_mse": 0.00002516788443205634,
      "dev_p_bh": 0.022211374176226508,
      "dev_p_raw": 0.00366121552355382,
      "events": 5,
      "feature": "imbalance_top",
      "feature_family": "MICROPRICE",
      "horizon": "300",
      "markets": 112,
      "model": "ENET_0.001_0.5",
      "model_dev_mse": 0.002926009626322218,
      "regime": "PRE_ELECTION",
      "selection_class": "CONFIRMATORY_ELIGIBLE",
      "target": "depth_price_h300",
      "target_family": "MICRO_FV",
      "candidate_id": "PRE_ELECTION|depth|MICRO_FV",
      "source_freeze": "pre_nonclock_train_dev_freeze.json"
    },
    {
      "blocks": 145,
      "dataset": "event",
      "dev_delta_mse": 0.00005695523666072089,
      "dev_p_bh": 1,
      "dev_p_raw": 1,
      "events": 5,
      "feature": "imbalance_top",
      "feature_family": "MICROPRICE",
      "horizon": "1",
      "markets": 69,
      "model": "HGB_D3_LR0.1",
      "model_dev_mse": 0.00573768931263419,
      "regime": "PRE_ELECTION",
      "selection_class": "SHADOW_ONLY_NO_FDR",
      "target": "event_price_k1",
      "target_family": "PRICE_EVENT",
      "candidate_id": "PRE_ELECTION|event|PRICE_EVENT",
      "source_freeze": "pre_nonclock_train_dev_freeze.json"
    },
    {
      "blocks": 154,
      "dataset": "event",
      "dev_delta_mse": 0.000006248540430449025,
      "dev_p_bh": 0.0006508827597429013,
      "dev_p_raw": 0.000244081034903588,
      "events": 5,
      "feature": "qask",
      "feature_family": "DEPTH",
      "horizon": "2",
      "markets": 78,
      "model": "HGB_D3_LR0.03",
      "model_dev_mse": 0.009773491804740296,
      "regime": "PRE_ELECTION",
      "selection_class": "CONFIRMATORY_ELIGIBLE",
      "target": "event_abs_k2",
      "target_family": "VOL_EVENT",
      "candidate_id": "PRE_ELECTION|event|VOL_EVENT",
      "source_freeze": "pre_nonclock_train_dev_freeze.json"
    }
  ]
}
""")
CANONICAL_TRAIN_DEV_FREEZE_SHA256 = "0543d94700f5ab9b00e4565411d9a61bd52066140e80001e96b61bdfa42270b0"


def _fit_base_columns(dataset: str, target: str) -> list[str]:
    if dataset == "clock" and target.startswith("update_h"):
        return ["genuine_15", "genuine_60"]
    if dataset == "clock" and target.startswith("jump_h"):
        return ["abs_ret_15", "rv_60"]
    if dataset == "clock":
        return ["ret_15", "ret_30", "ret_60"]
    if dataset == "event":
        return ["event_ret1", "event_ret2"]
    if dataset == "depth":
        return ["ret_15", "ret_30", "ret_60"]
    raise ValueError(f"unsupported dataset {dataset!r}")


def _fit_candidate_columns(dataset: str, candidate: str, available: set[str], base: list[str]) -> list[str]:
    if dataset == "depth" and candidate in MICROFV_BLOCKS:
        return [c for c in MICROFV_BLOCKS[candidate] if c in available and c not in base]
    return [candidate] if candidate in available and candidate not in base else []


def _fit_model(model_name: str, classification: bool):
    if classification:
        if model_name.startswith("LOGIT_C"):
            return LogisticRegression(
                C=float(model_name.removeprefix("LOGIT_C")),
                max_iter=500,
                random_state=SKLEARN_SEED,
            )
        if model_name.startswith("HGB_D"):
            tail=model_name.removeprefix("HGB_D")
            d, lr = tail.split("_LR", 1)
            return HistGradientBoostingClassifier(
                max_depth=int(d), learning_rate=float(lr), max_iter=200,
                random_state=SKLEARN_SEED,
            )
        raise ValueError(f"unsupported classification model {model_name!r}")
    if model_name == "OLS":
        return LinearRegression()
    if model_name.startswith("RIDGE_"):
        return Ridge(alpha=float(model_name.removeprefix("RIDGE_")))
    if model_name.startswith("ENET_"):
        a, l1 = model_name.removeprefix("ENET_").split("_", 1)
        return ElasticNet(alpha=float(a), l1_ratio=float(l1), max_iter=2000)
    if model_name.startswith("HGB_D"):
        tail=model_name.removeprefix("HGB_D")
        d, lr = tail.split("_LR", 1)
        return HistGradientBoostingRegressor(
            max_depth=int(d), learning_rate=float(lr), max_iter=200,
            random_state=SKLEARN_SEED,
        )
    raise ValueError(f"unsupported regression model {model_name!r}")


def _is_classification(dataset: str, target: str) -> bool:
    return dataset == "clock" and (
        target.startswith("update_h") or target.startswith("jump_h")
    )


def _candidate_seed(candidate_id: str) -> int:
    digest=hashlib.sha256(f"{SEED}|{candidate_id}".encode()).digest()
    return int.from_bytes(digest[:4], "big")


def _block_length_minutes(frame: pd.DataFrame, target: str) -> tuple[int, list[dict[str, Any]]]:
    train=frame.loc[
        (frame["split"] == "TRAIN") & frame[target].notna(),
        ["event", "time", target],
    ].copy()
    minute=(
        train.assign(minute=train["time"].dt.floor("min"))
        .groupby(["event", "minute"], observed=True)[target]
        .mean()
        .reset_index()
    )
    diagnostics=[]
    chosen=60
    for lag in range(5, 61):
        products=[]
        left_sq=[]
        right_sq=[]
        pairs=0
        for event, g in minute.groupby("event", sort=False):
            g=g.sort_values("minute")
            if len(g) < 3:
                continue
            mean=float(g[target].mean())
            mp={int(t): float(v) for t,v in zip(datetime_ns(g["minute"]), g[target])}
            step=lag * 60 * NS
            for t, value in mp.items():
                prev=mp.get(t-step)
                if prev is None:
                    continue
                a=value-mean
                b=prev-mean
                products.append(a*b)
                left_sq.append(a*a)
                right_sq.append(b*b)
                pairs += 1
        denom=math.sqrt(sum(left_sq)*sum(right_sq)) if pairs else 0.0
        corr=(sum(products)/denom) if denom > 0 else np.nan
        diagnostics.append({"lag_minutes":lag,"pairs":pairs,"corr":corr})
        if pairs >= 3 and np.isfinite(corr) and abs(corr) < 0.10:
            chosen=lag
            break
    return chosen, diagnostics


def fit_freeze_main() -> None:
    import joblib
    import sklearn

    out=Path("/kaggle/working/005f_fit_freeze")
    out.mkdir(parents=True, exist_ok=True)
    artifact_root=out/"artifacts"
    artifact_root.mkdir(parents=True, exist_ok=True)

    manifest_path=one("corpus_manifest.json")
    identity_path=one("market_identity.csv")
    quality_path=one("corpus_quality.json")
    if sha256(manifest_path) != EXPECTED_MANIFEST:
        raise RuntimeError("DATA-001 manifest mismatch")
    if sha256(identity_path) != EXPECTED_IDENTITY:
        raise RuntimeError("DATA-001 identity mismatch")
    if sha256(quality_path) != EXPECTED_QUALITY:
        raise RuntimeError("DATA-001 quality mismatch")
    corpus=manifest_path.parent

    all_clock=[]
    all_event=[]
    all_depth=[]
    audit=[]
    for event, meta in REGIME_WINDOWS.items():
        tokens, market_map, _ = yes_tokens(identity_path, event)
        for regime in CLAIM_REGIMES:
            start_s, end_s = meta[regime]
            start, end = dt(start_s), dt(end_s)
            bounds=split_bounds(start, end)
            empirical_end=bounds["DEV"][1]
            states, captures, state_audit = load_book_state(
                corpus/event/"books"/"book_changes", tokens, start, empirical_end
            )
            audit.append({"event":event,"regime":regime,"holdout_read":False,**state_audit})
            if states.empty:
                continue
            trade=load_trade_features(
                corpus/event/"books"/"trades", tokens, start, empirical_end, states
            )
            clock=build_clock(event, regime, states, captures, market_map, start, end)
            if not clock.empty:
                clock=attach_trade_features(clock, trade)
                clock["trade_vs_quote_60"]=clock.get("trade_count_60", np.nan) / np.maximum(clock["genuine_60"], 1.0)
                all_clock.append(clock)
            ev=build_event_time(event, regime, states, market_map, start, end)
            if not ev.empty:
                ev=attach_trade_features(ev, trade)
                all_event.append(ev)
            depth=load_depth(corpus/event/"books"/"depth_snapshots", tokens, start, empirical_end)
            depth=attach_depth_targets(depth, states, event, regime, market_map, start, end)
            if not depth.empty:
                all_depth.append(depth)

    clock=pd.concat(all_clock, ignore_index=True) if all_clock else pd.DataFrame()
    event_df=pd.concat(all_event, ignore_index=True) if all_event else pd.DataFrame()
    depth_df=pd.concat(all_depth, ignore_index=True) if all_depth else pd.DataFrame()

    jump_thresholds={}
    if not clock.empty:
        clock["jump_state"]=0.0
        for regime in CLAIM_REGIMES:
            tr=clock[(clock["regime"]==regime)&(clock["split"]=="TRAIN")]
            threshold=float(tr["abs_ret_15"].quantile(0.95)) if len(tr) else np.nan
            jump_thresholds[regime]=threshold
            mask=clock["regime"]==regime
            clock.loc[mask,"jump_state"]=(clock.loc[mask,"abs_ret_15"]>=threshold).astype(float)
            for h in CLOCK_H:
                source=clock.loc[mask,f"abs_h{h}"]
                clock.loc[mask,f"jump_h{h}"]=np.where(
                    source.notna(), (source>=threshold).astype(float), np.nan
                )

    frames={"clock":clock,"event":event_df,"depth":depth_df}
    fit_rows=[]
    block_rows=[]
    candidate_manifests=[]

    for candidate in CANONICAL_TRAIN_DEV_FREEZE["candidates"]:
        cid=candidate["candidate_id"]
        dataset=candidate["dataset"]
        regime=candidate["regime"]
        target=candidate["target"]
        feature=candidate["feature"]
        model_name=candidate["model"]
        frame=frames[dataset]
        sub=frame[frame["regime"]==regime].copy()
        if target not in sub.columns:
            raise RuntimeError(f"target missing for {cid}: {target}")
        base=[c for c in _fit_base_columns(dataset,target) if c in sub.columns]
        challenger=_fit_candidate_columns(dataset, feature, set(sub.columns), base)
        if not challenger:
            raise RuntimeError(f"challenger columns missing for {cid}")
        full=base+challenger
        row_cols=["split","event","token_id","time",target,*full]
        x=sub[row_cols].replace([np.inf,-np.inf],np.nan).dropna()
        train_all=x[x["split"]=="TRAIN"].sort_values(["event","time","token_id"],kind="stable")
        dev_all=x[x["split"]=="DEV"].sort_values(["event","time","token_id"],kind="stable")
        tr=thin(train_all)
        dv=thin(dev_all)
        fit=pd.concat([tr,dv],ignore_index=True)
        if len(tr)<500 or len(dv)<200:
            raise RuntimeError(f"insufficient fit support for {cid}: {len(tr)=} {len(dv)=}")
        classification=_is_classification(dataset,target)
        y=fit[target].to_numpy(int if classification else float)
        if classification and len(np.unique(y))<2:
            raise RuntimeError(f"classification target degenerate for {cid}")

        b_scaler=StandardScaler().fit(fit[base])
        c_scaler=StandardScaler().fit(fit[full])
        baseline=_fit_model(model_name,classification)
        challenger_model=_fit_model(model_name,classification)
        baseline.fit(b_scaler.transform(fit[base]),y)
        challenger_model.fit(c_scaler.transform(fit[full]),y)

        safe=hashlib.sha256(cid.encode()).hexdigest()[:16]
        cdir=artifact_root/safe
        cdir.mkdir(parents=True,exist_ok=True)
        objects={
            "baseline_scaler.joblib":b_scaler,
            "challenger_scaler.joblib":c_scaler,
            "baseline_model.joblib":baseline,
            "challenger_model.joblib":challenger_model,
        }
        hashes={}
        for name,obj in objects.items():
            path=cdir/name
            joblib.dump(obj,path)
            hashes[name]=sha256(path)

        block_minutes, diagnostics=_block_length_minutes(sub,target)
        for row in diagnostics:
            block_rows.append({"candidate_id":cid,**row})

        fit_rows.append({
            "candidate_id":cid,"regime":regime,"dataset":dataset,"target":target,
            "feature":feature,"model":model_name,"selection_class":candidate["selection_class"],
            "train_complete_rows":len(train_all),"dev_complete_rows":len(dev_all),
            "train_fit_rows":len(tr),"dev_fit_rows":len(dv),"fit_rows":len(fit),
            "block_length_minutes":block_minutes,
        })
        candidate_manifests.append({
            "candidate_id":cid,
            "regime":regime,
            "dataset":dataset,
            "target_family":candidate["target_family"],
            "target":target,
            "horizon":candidate["horizon"],
            "feature":feature,
            "feature_family":candidate["feature_family"],
            "selection_class":candidate["selection_class"],
            "model":model_name,
            "classification":classification,
            "baseline_columns":base,
            "challenger_columns":full,
            "train_complete_rows":len(train_all),
            "dev_complete_rows":len(dev_all),
            "train_fit_rows":len(tr),
            "dev_fit_rows":len(dv),
            "fit_rows":len(fit),
            "block_length_minutes":block_minutes,
            "artifact_directory":safe,
            "artifact_sha256":hashes,
            "seed":_candidate_seed(cid),
        })

    write_csv(out/"fit_row_audit.csv",fit_rows)
    write_csv(out/"block_length_diagnostics.csv",block_rows)
    write_csv(out/"book_state_capture_audit.csv",audit)
    versions={
        "python":".".join(map(str,__import__("sys").version_info[:3])),
        "numpy":np.__version__,
        "pandas":pd.__version__,
        "pyarrow":pa.__version__,
        "sklearn":sklearn.__version__,
        "joblib":joblib.__version__,
    }
    manifest={
        "experiment":"EXPERIMENT-005F",
        "phase":"FIT_FREEZE",
        "holdout_read":False,
        "candidate_count":len(candidate_manifests),
        "canonical_train_dev_freeze_sha256":CANONICAL_TRAIN_DEV_FREEZE_SHA256,
        "design_freeze_commit":DESIGN_FREEZE_COMMIT,
        "execution_freeze_commit":EXECUTION_FREEZE_COMMIT,
        "data_manifest_sha256":sha256(manifest_path),
        "identity_sha256":sha256(identity_path),
        "quality_sha256":sha256(quality_path),
        "runner_sha256":sha256(Path(__file__)),
        "jump_threshold_abs_logit15":jump_thresholds,
        "versions":versions,
        "candidates":candidate_manifests,
    }
    (out/"fit_freeze_manifest.json").write_text(json.dumps(manifest,indent=2,sort_keys=True))
    print(json.dumps({
        "candidate_count":len(candidate_manifests),
        "holdout_read":False,
        "jump_thresholds":jump_thresholds,
    },sort_keys=True))


if __name__ == "__main__":
    fit_freeze_main()

[executed on device: ip-172-31-73-211.ec2.internal (c5706598-02ec-4925-9d6e-43e31c32d097)]