# ruff: noqa
"""Fresh DATA-003 strict-replication runner for EXPERIMENT-005G Lane A.

The primary path deliberately reconstructs the EXPERIMENT-005F state from V3 price_change
rows. V3-only best_bid_ask events are not consumed here: using them would change the
capture semantics of the replication feature.

This script is designed for the authenticated dataset_script GitHub/Kaggle transport. It
never places orders and never reads a HOLDOUT file in TRAIN/DEV mode.
"""

from __future__ import annotations

import argparse
import csv
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
from sklearn.ensemble import HistGradientBoostingClassifier, HistGradientBoostingRegressor
from sklearn.preprocessing import StandardScaler

ROOT = Path(__file__).resolve().parents[2]
MAPPING = ROOT / "data/mappings/sig_polymarket_2026.json"

EXPECTED_DATASET = "polyleviathan/sig-cup-data003-orderbooks"
EXPECTED_MAPPING_SHA256 = "9bc3d55317ace5e7e56eb0e4deaf2d36d09421b2861b7104977a96bedfc16df2"
EXPECTED_CONDITIONS = 693
SEED = 20261001005
SKLEARN_SEED = SEED % (2**32 - 1)

NS = 1_000_000_000
GRID_SECONDS = 15
CAPTURE_BIN_SECONDS = 5
GAP_SECONDS = 300
MAX_MODEL_ROWS = 250_000

SLICE_CONFIG = {
    "active_ev18": {
        "regime": "ACTIVE_RESULTS_CORE_EV18",
        "window_id": "ev18_ok_sc_runoff_ga_runoff",
        "start": "2026-08-25T23:00:00Z",
        "train_end": "2026-08-26T02:00:00Z",
        "dev_end": "2026-08-26T03:00:00Z",
        "holdout_end": "2026-08-26T04:00:00Z",
        "sample_mod": 1,
        "candidates": [
            {
                "id": "005F_REPL_ACTIVE_UPDATE",
                "target": "update_h300",
                "baseline": ["genuine_15", "genuine_60"],
                "feature": "genuine_age_s",
                "kind": "classification",
                "depth": 2,
                "learning_rate": 0.1,
            },
            {
                "id": "005F_REPL_ACTIVE_JUMP",
                "target": "jump_h300",
                "baseline": ["abs_ret_15", "rv_60"],
                "feature": "genuine_age_s",
                "kind": "classification",
                "depth": 2,
                "learning_rate": 0.03,
            },
            {
                "id": "005F_REPL_ACTIVE_PRICE_NEGATIVE",
                "target": "price_h300",
                "baseline": ["ret_15", "ret_30", "ret_60"],
                "feature": "genuine_age_s",
                "kind": "regression",
                "depth": 2,
                "learning_rate": 0.1,
            },
        ],
    },
    "pre_baseline": {
        "regime": "PRE_ELECTION_BASELINE_SEP",
        "window_id": "baseline_sep",
        "start": "2026-09-01T00:00:00Z",
        "train_end": "2026-09-04T00:00:00Z",
        "dev_end": "2026-09-05T00:00:00Z",
        "holdout_end": "2026-09-06T00:00:00Z",
        "sample_mod": 16,
        "candidates": [
            {
                "id": "005F_REPL_PRE_UPDATE",
                "target": "update_h300",
                "baseline": ["genuine_15", "genuine_60"],
                "feature": "genuine_age_s",
                "kind": "classification",
                "depth": 3,
                "learning_rate": 0.03,
            },
            {
                "id": "005F_REPL_PRE_LIQUIDITY",
                "target": "spread_h15",
                "baseline": ["spread", "ret_15"],
                "feature": "trade_abs_impact_60",
                "kind": "regression",
                "depth": 3,
                "learning_rate": 0.03,
            },
        ],
    },
}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def datetime_ns(values: Any) -> np.ndarray:
    return pd.DatetimeIndex(values).as_unit("ns").asi8.astype(np.int64, copy=False)


def logit_array(values: np.ndarray) -> np.ndarray:
    x = np.clip(values.astype(float), 1e-6, 1 - 1e-6)
    return np.log(x / (1.0 - x))


def representative_tokens() -> tuple[set[str], dict[str, str]]:
    payload = json.loads(MAPPING.read_text(encoding="utf-8"))
    by_condition: dict[str, str] = {}
    for record in payload["records"]:
        identities: list[dict[str, Any]] = []
        direct = record.get("direct_polymarket")
        if direct:
            identities.append(direct)
        identities.extend(record.get("polymarket_components") or [])
        for identity in identities:
            cid = str(identity["condition_id"])
            outcomes = [str(value) for value in identity["outcomes"]]
            token_ids = [str(value) for value in identity["token_ids"]]
            try:
                index = [value.lower() for value in outcomes].index("yes")
            except ValueError:
                index = 0
            chosen = token_ids[index]
            previous = by_condition.get(cid)
            if previous is not None and previous != chosen:
                raise RuntimeError(f"condition {cid} has conflicting representative tokens")
            by_condition[cid] = chosen
    if len(by_condition) != EXPECTED_CONDITIONS:
        raise RuntimeError(
            f"expected {EXPECTED_CONDITIONS} mapped conditions; got {len(by_condition)}"
        )
    return set(by_condition.values()), {token: cid for cid, token in by_condition.items()}


def load_inputs(path: Path, cfg: dict[str, Any]) -> list[dict[str, str]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("dataset") != EXPECTED_DATASET:
        raise RuntimeError(f"wrong dataset: {payload.get('dataset')!r}")
    files = payload.get("files")
    if not isinstance(files, list) or not files:
        raise RuntimeError("dataset_script input manifest has no files")

    window_id = str(cfg["window_id"])
    dev_end = pd.Timestamp(cfg["dev_end"])
    out: list[dict[str, str]] = []
    for item in files:
        remote = str(item["remote"])
        local = str(item["local"])
        if not remote.startswith(f"{window_id}/"):
            raise RuntimeError(f"source file crosses frozen window: {remote}")
        date_part = remote.split("date=", 1)[1].split("/", 1)[0]
        hour_part = remote.split("hour=", 1)[1][:2]
        hour_start = pd.Timestamp(f"{date_part}T{hour_part}:00:00Z")
        if hour_start >= dev_end:
            raise RuntimeError(f"HOLDOUT-or-later file present in TRAIN/DEV job: {remote}")
        out.append({"remote": remote, "local": local})
    return sorted(out, key=lambda row: row["remote"])


def group_book_batch(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return frame
    for col in ("best_bid", "best_ask", "price", "size"):
        frame[col] = pd.to_numeric(frame[col], errors="coerce")
    frame["observed_at"] = pd.to_datetime(frame["timestamp_received"], utc=True)
    frame["bid_best_update"] = np.where(
        (frame["side"] == "BUY")
        & np.isclose(frame["price"], frame["best_bid"], atol=1e-12, rtol=0),
        frame["size"],
        np.nan,
    )
    frame["ask_best_update"] = np.where(
        (frame["side"] == "SELL")
        & np.isclose(frame["price"], frame["best_ask"], atol=1e-12, rtol=0),
        frame["size"],
        np.nan,
    )
    grp = frame.groupby(["asset_id", "observed_at"], sort=False, observed=True)
    out = grp.agg(
        raw_rows=("asset_id", "size"),
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
    out = out.rename(columns={"asset_id": "token_id"})
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


def build_states_and_trades(
    inputs: list[dict[str, str]],
    tokens: set[str],
    start: pd.Timestamp,
    end: pd.Timestamp,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any], list[dict[str, Any]]]:
    state_carry: dict[str, dict[str, Any]] = {}
    pending_states: list[pd.DataFrame] = []
    state_chunks: list[pd.DataFrame] = []
    trade_chunks: list[pd.DataFrame] = []
    carry_raw = pd.DataFrame()
    audit: defaultdict[str, int] = defaultdict(int)
    file_audit: list[dict[str, Any]] = []

    start_scalar = pa.scalar(start.to_pydatetime(), pa.timestamp("us", tz="UTC"))
    end_scalar = pa.scalar(end.to_pydatetime(), pa.timestamp("us", tz="UTC"))
    token_list = sorted(tokens)

    def flush_pending() -> None:
        if pending_states:
            state_chunks.append(pd.concat(pending_states, ignore_index=True))
            pending_states.clear()

    def consume(grouped: pd.DataFrame) -> None:
        if grouped.empty:
            return
        grouped = grouped.sort_values(["token_id", "observed_at"], kind="stable")
        for token, sub in grouped.groupby("token_id", sort=False):
            sub = sub.copy().reset_index(drop=True)
            n = len(sub)
            if n == 0:
                continue
            key = str(token)
            carry = state_carry.get(key)

            bid = sub["best_bid"].to_numpy(float)
            ask = sub["best_ask"].to_numpy(float)
            times_i = datetime_ns(sub["observed_at"])
            valid = sub["bbo_valid"].to_numpy(bool)

            prev_bid = np.empty(n, float)
            prev_ask = np.empty(n, float)
            prev_time = np.empty(n, np.int64)
            prev_valid = np.empty(n, bool)
            if carry:
                prev_bid[0] = carry["bid"]
                prev_ask[0] = carry["ask"]
                prev_time[0] = carry["time_ns"]
                prev_valid[0] = carry["valid"]
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

            def top_quantity(
                update_col: str,
                price: np.ndarray,
                prev_price: np.ndarray,
                carry_q: float,
            ) -> np.ndarray:
                values = sub[update_col].to_numpy(float)
                if (
                    n
                    and carry
                    and contiguous[0]
                    and np.isclose(price[0], prev_price[0], atol=1e-12, rtol=0)
                    and not np.isfinite(values[0])
                    and np.isfinite(carry_q)
                ):
                    values[0] = carry_q
                boundary = (
                    (~valid)
                    | (~contiguous)
                    | (~np.isclose(price, prev_price, atol=1e-12, rtol=0))
                )
                segment = np.cumsum(boundary)
                return pd.Series(values).groupby(segment).ffill().to_numpy(float)

            qbid = top_quantity(
                "bid_update", bid, prev_bid, carry["qbid"] if carry else np.nan
            )
            qask = top_quantity(
                "ask_update", ask, prev_ask, carry["qask"] if carry else np.nan
            )
            prev_qbid = np.empty(n, float)
            prev_qask = np.empty(n, float)
            prev_qbid[0] = carry["qbid"] if carry else np.nan
            prev_qask[0] = carry["qask"] if carry else np.nan
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

            base_segment = int(carry["segment"]) if carry else 0
            segments = base_segment + np.cumsum(establish.astype(int))

            last_genuine_base = int(carry["last_genuine"]) if carry else -1
            last_genuine = np.where(genuine, times_i, -1).astype(np.int64)
            if n:
                last_genuine[0] = max(int(last_genuine[0]), last_genuine_base)
                last_genuine = np.maximum.accumulate(last_genuine)

            sub["genuine_bbo"] = genuine
            sub["repeated_unchanged"] = repeated
            sub["top_depth_change"] = q_change
            sub["establish"] = establish
            sub["segment"] = segments
            sub["last_genuine_ns"] = last_genuine
            sub["qbid"] = qbid
            sub["qask"] = qask
            sub["midpoint"] = (bid + ask) / 2.0
            sub["spread"] = ask - bid
            sub["logit_mid"] = logit_array(sub["midpoint"].to_numpy(float))

            keep = valid & (establish | economic)
            if np.any(keep):
                pending_states.append(
                    sub.loc[
                        keep,
                        [
                            "token_id",
                            "observed_at",
                            "best_bid",
                            "best_ask",
                            "midpoint",
                            "logit_mid",
                            "spread",
                            "qbid",
                            "qask",
                            "source_version",
                            "genuine_bbo",
                            "top_depth_change",
                            "establish",
                            "segment",
                            "last_genuine_ns",
                        ],
                    ].copy()
                )
                if len(pending_states) >= 1000:
                    flush_pending()

            audit["raw_groups"] += n
            audit["ambiguous_groups"] += int(sub["ambiguous"].sum())
            audit["valid_groups"] += int(valid.sum())
            audit["genuine_bbo"] += int(genuine.sum())
            audit["repeated_unchanged"] += int(repeated.sum())
            audit["top_depth_changes"] += int(q_change.sum())

            if valid[-1]:
                state_carry[key] = {
                    "bid": float(bid[-1]),
                    "ask": float(ask[-1]),
                    "qbid": float(qbid[-1]),
                    "qask": float(qask[-1]),
                    "time_ns": int(times_i[-1]),
                    "valid": True,
                    "segment": int(segments[-1]),
                    "last_genuine": int(last_genuine[-1]),
                }
            else:
                state_carry[key] = {
                    "bid": np.nan,
                    "ask": np.nan,
                    "qbid": np.nan,
                    "qask": np.nan,
                    "time_ns": int(times_i[-1]),
                    "valid": False,
                    "segment": int(segments[-1]),
                    "last_genuine": -1,
                }

    columns = [
        "event_type",
        "timestamp_received",
        "asset_id",
        "price",
        "size",
        "side",
        "best_bid",
        "best_ask",
        "source_version",
    ]

    for index, item in enumerate(inputs, 1):
        path = Path(item["local"])
        dataset = pads.dataset([str(path)], format="parquet")
        asset_type = dataset.schema.field("asset_id").type
        if pa.types.is_binary(asset_type) or pa.types.is_large_binary(asset_type):
            token_filter = [
                int(token).to_bytes(32, byteorder="big", signed=False)
                for token in token_list
            ]
        elif pa.types.is_string(asset_type) or pa.types.is_large_string(asset_type):
            token_filter = token_list
        else:
            raise RuntimeError(f"unsupported V3 asset_id type: {asset_type}")
        expression = (
            (pads.field("timestamp_received") >= start_scalar)
            & (pads.field("timestamp_received") < end_scalar)
            & pads.field("asset_id").isin(token_filter)
            & pads.field("event_type").isin(["price_change", "last_trade_price"])
        )
        table = dataset.scanner(
            columns=columns,
            filter=expression,
            batch_size=500_000,
            use_threads=True,
        ).to_table()
        audit["filtered_rows"] += table.num_rows
        if table.num_rows == 0:
            file_audit.append(
                {
                    "remote": item["remote"],
                    "sha256": sha256(path),
                    "filtered_rows": 0,
                }
            )
            continue

        frame = table.to_pandas()
        frame["asset_id"] = frame["asset_id"].map(
            lambda value: (
                str(
                    int.from_bytes(
                        bytes(value),
                        byteorder="big",
                        signed=False,
                    )
                )
                if isinstance(value, (bytes, bytearray, memoryview))
                else str(value)
            )
        )
        changes = frame[frame["event_type"] == "price_change"].copy()
        trades = frame[frame["event_type"] == "last_trade_price"].copy()

        if not changes.empty:
            changes["timestamp_received"] = pd.to_datetime(
                changes["timestamp_received"], utc=True
            )
            if not carry_raw.empty:
                changes = pd.concat([carry_raw, changes], ignore_index=True)
                carry_raw = pd.DataFrame()
            max_time = changes["timestamp_received"].max()
            carry_raw = changes[changes["timestamp_received"] == max_time].copy()
            process = changes[changes["timestamp_received"] < max_time].copy()
            grouped = group_book_batch(process)
            audit["price_change_rows"] += len(process)
            audit["raw_rows"] += int(grouped["raw_rows"].sum()) if not grouped.empty else 0
            consume(grouped)

        if not trades.empty:
            trades = trades.rename(
                columns={"asset_id": "token_id", "timestamp_received": "observed_at"}
            )
            trades["observed_at"] = pd.to_datetime(trades["observed_at"], utc=True)
            trades["price"] = pd.to_numeric(trades["price"], errors="coerce")
            trades["size"] = pd.to_numeric(trades["size"], errors="coerce")
            trades = trades[
                np.isfinite(trades["price"])
                & np.isfinite(trades["size"])
                & (trades["size"] > 0)
            ][["token_id", "observed_at", "price", "size", "source_version"]].copy()
            trade_chunks.append(trades)
            audit["trade_rows"] += len(trades)

        file_audit.append(
            {
                "remote": item["remote"],
                "sha256": sha256(path),
                "filtered_rows": table.num_rows,
                "price_change_rows": len(changes),
                "trade_rows": len(trades),
            }
        )
        if index % 12 == 0 or index == len(inputs):
            print(
                f"005G_LANE_A_PROGRESS files={index}/{len(inputs)} "
                f"filtered_rows={audit['filtered_rows']}",
                flush=True,
            )

    if not carry_raw.empty:
        grouped = group_book_batch(carry_raw)
        audit["price_change_rows"] += len(carry_raw)
        audit["raw_rows"] += int(grouped["raw_rows"].sum()) if not grouped.empty else 0
        consume(grouped)
    flush_pending()

    states = pd.concat(state_chunks, ignore_index=True) if state_chunks else pd.DataFrame()
    trades = pd.concat(trade_chunks, ignore_index=True) if trade_chunks else pd.DataFrame()
    if not states.empty:
        states = states.sort_values(["token_id", "observed_at"], kind="stable").reset_index(
            drop=True
        )
    if not trades.empty:
        trades = trades.sort_values(["token_id", "observed_at"], kind="stable").reset_index(
            drop=True
        )
    audit["state_rows_retained"] = len(states)
    audit["tokens_seen"] = int(states["token_id"].nunique()) if not states.empty else 0
    audit["trade_tokens_seen"] = int(trades["token_id"].nunique()) if not trades.empty else 0
    return states, trades, dict(audit), file_audit


def asof_from_states(
    states: pd.DataFrame,
    query_ns: np.ndarray,
    field: str,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    times = datetime_ns(states["observed_at"])
    index = np.searchsorted(times, query_ns, side="right") - 1
    ok = index >= 0
    values = np.full(len(query_ns), np.nan, float)
    segments = np.full(len(query_ns), -1, np.int64)
    source_time = np.full(len(query_ns), -1, np.int64)
    if np.any(ok):
        ii = index[ok]
        values[ok] = states[field].to_numpy(float)[ii]
        segments[ok] = states["segment"].to_numpy(np.int64)[ii]
        source_time[ok] = times[ii]
    return values, segments, source_time


def rolling_counts(
    event_ns: np.ndarray,
    query_ns: np.ndarray,
    window_s: int,
) -> np.ndarray:
    right = np.searchsorted(event_ns, query_ns, side="right")
    left = np.searchsorted(event_ns, query_ns - window_s * NS, side="right")
    return (right - left).astype(float)


def trade_features(
    trades: pd.DataFrame,
    states: pd.DataFrame,
) -> pd.DataFrame:
    if trades.empty or states.empty:
        return pd.DataFrame()
    out_parts: list[pd.DataFrame] = []
    for token, group in trades.groupby("token_id", sort=False):
        group = group.sort_values("observed_at").copy()
        state = states[states["token_id"].astype(str) == str(token)].sort_values(
            "observed_at"
        )
        if state.empty:
            continue
        query_ns = datetime_ns(group["observed_at"])
        midpoint, _, _ = asof_from_states(state, query_ns - 1, "midpoint")
        group["abs_trade_mid_disp"] = np.abs(group["price"].to_numpy(float) - midpoint)
        indexed = group.set_index("observed_at")
        base = indexed.resample(
            f"{CAPTURE_BIN_SECONDS}s",
            label="right",
            closed="right",
        ).agg(
            abs_trade_mid_disp_sum=("abs_trade_mid_disp", "sum"),
            impact_obs=("abs_trade_mid_disp", "count"),
        )
        base["abs_trade_mid_disp_sum"] = base["abs_trade_mid_disp_sum"].fillna(0.0)
        base["impact_obs"] = base["impact_obs"].fillna(0.0)
        periods = 60 // CAPTURE_BIN_SECONDS
        impact_sum = base["abs_trade_mid_disp_sum"].rolling(
            periods, min_periods=1
        ).sum()
        impact_n = base["impact_obs"].rolling(periods, min_periods=1).sum()
        base["trade_abs_impact_60"] = impact_sum / impact_n.replace(0, np.nan)
        base = base.reset_index().rename(columns={"observed_at": "bin_time"})
        base["token_id"] = str(token)
        out_parts.append(base[["token_id", "bin_time", "trade_abs_impact_60"]])
    return pd.concat(out_parts, ignore_index=True) if out_parts else pd.DataFrame()


def split_label(
    times: pd.DatetimeIndex,
    start: pd.Timestamp,
    train_end: pd.Timestamp,
    dev_end: pd.Timestamp,
) -> np.ndarray:
    labels = np.full(len(times), "", dtype=object)
    values = times.as_unit("ns").asi8
    purge = 300 * NS
    labels[(values >= start.value) & (values < train_end.value - purge)] = "TRAIN"
    labels[(values >= train_end.value) & (values < dev_end.value - purge)] = "DEV"
    return labels


def stable_keep(token: str, times: pd.DatetimeIndex, mod: int) -> np.ndarray:
    """Deterministic SplitMix64 hash over token and whole-second clock time."""
    if mod <= 1:
        return np.ones(len(times), dtype=bool)
    token_hash = np.uint64(
        int.from_bytes(hashlib.sha256(token.encode()).digest()[:8], "big")
    )
    seconds = (times.as_unit("ns").asi8 // NS).astype(np.uint64, copy=False)
    values = seconds ^ token_hash
    values ^= values >> np.uint64(30)
    values *= np.uint64(0xBF58476D1CE4E5B9)
    values ^= values >> np.uint64(27)
    values *= np.uint64(0x94D049BB133111EB)
    values ^= values >> np.uint64(31)
    return (values % np.uint64(mod)) == 0


def build_clock(
    states: pd.DataFrame,
    trades: pd.DataFrame,
    cfg: dict[str, Any],
) -> tuple[pd.DataFrame, dict[str, Any]]:
    start = pd.Timestamp(cfg["start"])
    train_end = pd.Timestamp(cfg["train_end"])
    dev_end = pd.Timestamp(cfg["dev_end"])
    sample_mod = int(cfg["sample_mod"])

    trade_frame = trade_features(trades, states)
    parts: list[pd.DataFrame] = []
    token_audit: list[dict[str, Any]] = []

    for token, state in states.groupby("token_id", sort=False):
        state = state.sort_values("observed_at").reset_index(drop=True)
        if len(state) < 2:
            continue
        grid = pd.date_range(
            start,
            dev_end,
            freq=f"{GRID_SECONDS}s",
            inclusive="left",
            tz="UTC",
        )
        query_ns = datetime_ns(grid)

        midpoint, segment, state_ns = asof_from_states(state, query_ns, "midpoint")
        spread, _, _ = asof_from_states(state, query_ns, "spread")
        logmid = logit_array(midpoint)
        last_genuine, _, _ = asof_from_states(state, query_ns, "last_genuine_ns")
        genuine_age = np.where(
            last_genuine >= 0,
            (query_ns - last_genuine) / NS,
            np.nan,
        )

        frame = pd.DataFrame(
            {
                "token_id": str(token),
                "time": grid,
                "midpoint": midpoint,
                "logit_mid": logmid,
                "spread": spread,
                "segment": segment,
                "state_age_s": (query_ns - state_ns) / NS,
                "genuine_age_s": genuine_age,
            }
        )

        for horizon in (15, 30, 60):
            past, past_segment, _ = asof_from_states(
                state, query_ns - horizon * NS, "midpoint"
            )
            ret = logmid - logit_array(past)
            ret[(past_segment != segment) | ~np.isfinite(past)] = np.nan
            frame[f"ret_{horizon}"] = ret

        frame["abs_ret_15"] = np.abs(frame["ret_15"])
        frame["rv_60"] = np.sqrt(
            frame["ret_15"].pow(2).rolling(4, min_periods=2).sum()
        )

        genuine_times = datetime_ns(state.loc[state["genuine_bbo"], "observed_at"])
        frame["genuine_15"] = rolling_counts(genuine_times, query_ns, 15)
        frame["genuine_60"] = rolling_counts(genuine_times, query_ns, 60)

        labels = split_label(grid, start, train_end, dev_end)
        frame["split"] = labels

        future_300 = query_ns + 300 * NS
        future_mid, future_segment, _ = asof_from_states(
            state, future_300, "midpoint"
        )
        future_logit = logit_array(future_mid)
        same_segment_300 = (future_segment == segment) & np.isfinite(future_mid)
        future_labels_300 = split_label(
            pd.DatetimeIndex(pd.to_datetime(future_300, utc=True)),
            start,
            train_end,
            dev_end,
        )
        same_split_300 = future_labels_300 == labels
        valid_300 = same_segment_300 & same_split_300 & (labels != "")

        price_h300 = future_logit - logmid
        price_h300[~valid_300] = np.nan
        frame["price_h300"] = price_h300
        frame["abs_h300"] = np.abs(price_h300)

        left = np.searchsorted(genuine_times, query_ns, side="right")
        right = np.searchsorted(genuine_times, future_300, side="right")
        update = (right > left).astype(float)
        update[~same_split_300] = np.nan
        frame["update_h300"] = update

        future_15 = query_ns + 15 * NS
        future_spread, future_segment_15, _ = asof_from_states(
            state, future_15, "spread"
        )
        future_labels_15 = split_label(
            pd.DatetimeIndex(pd.to_datetime(future_15, utc=True)),
            start,
            train_end,
            dev_end,
        )
        valid_15 = (
            (future_segment_15 == segment)
            & np.isfinite(future_spread)
            & (future_labels_15 == labels)
            & (labels != "")
        )
        spread_h15 = future_spread - spread
        spread_h15[~valid_15] = np.nan
        frame["spread_h15"] = spread_h15

        token_trades = trade_frame[
            trade_frame["token_id"].astype(str) == str(token)
        ].sort_values("bin_time")
        if token_trades.empty:
            frame["trade_abs_impact_60"] = np.nan
        else:
            left_frame = frame[["time"]].copy()
            left_frame["__ns"] = datetime_ns(left_frame["time"])
            right_frame = token_trades.copy()
            right_frame["__ns"] = datetime_ns(right_frame["bin_time"])
            merged = pd.merge_asof(
                left_frame.sort_values("__ns"),
                right_frame[["__ns", "trade_abs_impact_60"]].sort_values("__ns"),
                on="__ns",
                direction="backward",
                allow_exact_matches=False,
            )
            frame["trade_abs_impact_60"] = merged["trade_abs_impact_60"].to_numpy()

        frame["genuine_age_delay300"] = frame["genuine_age_s"].shift(20)
        frame["genuine_age_shift1800"] = frame["genuine_age_s"].shift(120)
        frame["genuine_age_future300"] = frame["genuine_age_s"].shift(-20)
        frame["trade_abs_impact_delay300"] = frame["trade_abs_impact_60"].shift(20)
        frame["trade_abs_impact_shift1800"] = frame["trade_abs_impact_60"].shift(120)
        frame["trade_abs_impact_future300"] = frame["trade_abs_impact_60"].shift(-20)

        keep = (
            (frame["split"] != "")
            & np.isfinite(frame["midpoint"])
            & stable_keep(str(token), grid, sample_mod)
        )
        sampled = frame.loc[keep].copy()
        if not sampled.empty:
            parts.append(sampled)

        token_audit.append(
            {
                "token_id": str(token),
                "state_rows": len(state),
                "genuine_updates": int(state["genuine_bbo"].sum()),
                "sampled_clock_rows": len(sampled),
            }
        )

    clock = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()
    if clock.empty:
        raise RuntimeError("no usable clock rows after state construction")

    train = clock[clock["split"] == "TRAIN"]
    threshold = float(train["abs_ret_15"].quantile(0.95))
    if not np.isfinite(threshold):
        raise RuntimeError("TRAIN jump threshold is not finite")
    clock["jump_h300"] = np.where(
        clock["abs_h300"].notna(),
        (clock["abs_h300"] >= threshold).astype(float),
        np.nan,
    )

    audit = {
        "clock_rows": len(clock),
        "train_rows": int((clock["split"] == "TRAIN").sum()),
        "dev_rows": int((clock["split"] == "DEV").sum()),
        "tokens": int(clock["token_id"].nunique()),
        "sample_mod": sample_mod,
        "train_jump_threshold_abs_logit15": threshold,
        "token_summary": token_audit,
    }
    return clock, audit


def thin(frame: pd.DataFrame, n: int = MAX_MODEL_ROWS) -> pd.DataFrame:
    if len(frame) <= n:
        return frame
    step = int(math.ceil(len(frame) / n))
    return frame.iloc[::step].head(n).copy()


def make_model(candidate: dict[str, Any]) -> Any:
    kwargs = {
        "max_depth": int(candidate["depth"]),
        "learning_rate": float(candidate["learning_rate"]),
        "max_iter": 200,
        "random_state": SKLEARN_SEED,
    }
    if candidate["kind"] == "classification":
        return HistGradientBoostingClassifier(**kwargs)
    return HistGradientBoostingRegressor(**kwargs)


def signflip_p(values: np.ndarray, key: str) -> float:
    x = values[np.isfinite(values)]
    if len(x) < 3:
        return 1.0
    observed = float(np.mean(x))
    if observed <= 0:
        return 1.0
    if len(x) <= 18:
        total = 1 << len(x)
        ge = 0
        for mask in range(total):
            signs = np.array(
                [1.0 if (mask >> index) & 1 else -1.0 for index in range(len(x))]
            )
            ge += float(np.mean(x * signs)) >= observed - 1e-15
        return ge / total
    seed = int.from_bytes(
        hashlib.sha256(f"{SEED}|{key}".encode()).digest()[:8],
        "big",
    )
    rng = np.random.default_rng(seed)
    ge = 0
    draws = 4096
    for _ in range(draws):
        signs = rng.choice(np.array([-1.0, 1.0]), size=len(x))
        ge += float(np.mean(x * signs)) >= observed - 1e-15
    return (ge + 1) / (draws + 1)


def bootstrap_blocks(values: np.ndarray, key: str) -> tuple[float, float, float]:
    x = values[np.isfinite(values)]
    if len(x) < 3:
        return float("nan"), float("nan"), float("nan")
    seed = int.from_bytes(
        hashlib.sha256(f"{SEED}|bootstrap|{key}".encode()).digest()[:8],
        "big",
    )
    rng = np.random.default_rng(seed)
    draws = np.empty(2000, float)
    for index in range(len(draws)):
        draws[index] = float(np.mean(rng.choice(x, size=len(x), replace=True)))
    return float(np.mean(x)), float(np.quantile(draws, 0.025)), float(np.quantile(draws, 0.975))


def evaluate_feature(
    clock: pd.DataFrame,
    candidate: dict[str, Any],
    feature_col: str,
    label: str,
) -> tuple[dict[str, Any], pd.DataFrame]:
    target = str(candidate["target"])
    baseline = [str(value) for value in candidate["baseline"]]
    needed = ["split", "token_id", "time", target, *baseline, feature_col]
    frame = clock[needed].replace([np.inf, -np.inf], np.nan).dropna().copy()
    frame = frame.sort_values(["time", "token_id"], kind="stable")
    train = thin(frame[frame["split"] == "TRAIN"])
    dev = thin(frame[frame["split"] == "DEV"])

    if len(train) < 500 or len(dev) < 200 or dev["token_id"].nunique() < 3:
        return (
            {
                "candidate_id": candidate["id"],
                "evaluation": label,
                "status": "INSUFFICIENT_SUPPORT",
                "train_rows": len(train),
                "dev_rows": len(dev),
                "markets": int(dev["token_id"].nunique()),
            },
            pd.DataFrame(),
        )

    y_train = train[target].to_numpy(float)
    y_dev = dev[target].to_numpy(float)
    if candidate["kind"] == "classification":
        if len(np.unique(y_train)) < 2 or len(np.unique(y_dev)) < 2:
            return (
                {
                    "candidate_id": candidate["id"],
                    "evaluation": label,
                    "status": "ONE_CLASS_TARGET",
                    "train_rows": len(train),
                    "dev_rows": len(dev),
                },
                pd.DataFrame(),
            )

    scaler_base = StandardScaler().fit(train[baseline])
    base_train = scaler_base.transform(train[baseline])
    base_dev = scaler_base.transform(dev[baseline])
    base_model = make_model(candidate)
    base_model.fit(base_train, y_train)
    if candidate["kind"] == "classification":
        pred_base = base_model.predict_proba(base_dev)[:, 1]
    else:
        pred_base = base_model.predict(base_dev)

    full_cols = [*baseline, feature_col]
    scaler_full = StandardScaler().fit(train[full_cols])
    full_train = scaler_full.transform(train[full_cols])
    full_dev = scaler_full.transform(dev[full_cols])
    full_model = make_model(candidate)
    full_model.fit(full_train, y_train)
    if candidate["kind"] == "classification":
        pred_full = full_model.predict_proba(full_dev)[:, 1]
    else:
        pred_full = full_model.predict(full_dev)

    loss_base = (y_dev - pred_base) ** 2
    loss_full = (y_dev - pred_full) ** 2
    diff = loss_base - loss_full

    evidence = dev[["token_id", "time"]].copy()
    evidence["loss_improvement"] = diff
    evidence["block"] = evidence["time"].dt.floor("30min")
    blocks = (
        evidence.groupby("block", observed=True)["loss_improvement"]
        .mean()
        .to_numpy(float)
    )

    total_sum = float(np.sum(diff))
    total_n = len(diff)
    market_stats = evidence.groupby("token_id", observed=True)["loss_improvement"].agg(
        ["sum", "count"]
    )
    leave_market: list[float] = []
    for _, row in market_stats.iterrows():
        remaining_n = total_n - int(row["count"])
        if remaining_n > 0:
            leave_market.append((total_sum - float(row["sum"])) / remaining_n)

    block_mean, block_lower, block_upper = bootstrap_blocks(
        blocks,
        f"{candidate['id']}|{label}",
    )
    improvement = float(np.mean(diff))
    p_value = signflip_p(blocks, f"{candidate['id']}|{label}")
    leave_market_min = float(min(leave_market)) if leave_market else float("nan")

    result = {
        "candidate_id": candidate["id"],
        "evaluation": label,
        "feature_column": feature_col,
        "status": "EVALUATED",
        "kind": candidate["kind"],
        "target": target,
        "train_rows": len(train),
        "dev_rows": len(dev),
        "markets": int(dev["token_id"].nunique()),
        "baseline_mse": float(np.mean(loss_base)),
        "challenger_mse": float(np.mean(loss_full)),
        "mean_loss_improvement": improvement,
        "relative_mse_improvement": (
            improvement / float(np.mean(loss_base))
            if float(np.mean(loss_base)) > 0
            else float("nan")
        ),
        "signflip_p": p_value,
        "blocks": len(blocks),
        "positive_blocks": int(np.sum(blocks > 0)),
        "block_bootstrap_mean": block_mean,
        "block_bootstrap_lower": block_lower,
        "block_bootstrap_upper": block_upper,
        "leave_market_min": leave_market_min,
        "leave_market_all_positive": bool(leave_market and min(leave_market) > 0),
        "dev_supports_incremental": bool(
            improvement > 0
            and p_value <= 0.10
            and np.isfinite(block_lower)
            and block_lower > 0
            and leave_market
            and min(leave_market) > 0
        ),
    }
    return result, evidence


def evaluate_candidates(
    clock: pd.DataFrame,
    cfg: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[pd.DataFrame]]:
    primary: list[dict[str, Any]] = []
    placebos: list[dict[str, Any]] = []
    evidence: list[pd.DataFrame] = []

    for candidate in cfg["candidates"]:
        result, rows = evaluate_feature(
            clock,
            candidate,
            str(candidate["feature"]),
            "PRIMARY",
        )
        primary.append(result)
        if not rows.empty:
            tagged = rows.copy()
            tagged["candidate_id"] = candidate["id"]
            tagged["evaluation"] = "PRIMARY"
            evidence.append(tagged)

        feature = str(candidate["feature"])
        if feature == "genuine_age_s":
            alternatives = {
                "FEATURE_DELAY_300S": "genuine_age_delay300",
                "TIME_SHIFT_1800S": "genuine_age_shift1800",
                "FUTURE_FEATURE_DIAGNOSTIC": "genuine_age_future300",
            }
        elif feature == "trade_abs_impact_60":
            alternatives = {
                "FEATURE_DELAY_300S": "trade_abs_impact_delay300",
                "TIME_SHIFT_1800S": "trade_abs_impact_shift1800",
                "FUTURE_FEATURE_DIAGNOSTIC": "trade_abs_impact_future300",
            }
        else:
            alternatives = {}

        for label, column in alternatives.items():
            placebo, _ = evaluate_feature(clock, candidate, column, label)
            placebos.append(placebo)

    return primary, placebos, evidence


def write_csv(path: Path, frames: list[pd.DataFrame]) -> None:
    if not frames:
        path.write_text("status\nEMPTY\n", encoding="utf-8")
        return
    frame = pd.concat(frames, ignore_index=True)
    frame.to_csv(path, index=False, lineterminator="\n")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-manifest", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--slice", choices=sorted(SLICE_CONFIG), required=True)
    args = parser.parse_args()

    cfg = SLICE_CONFIG[args.slice]
    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=True)

    mapping_hash = sha256(MAPPING)
    if mapping_hash != EXPECTED_MAPPING_SHA256:
        raise RuntimeError(
            f"accepted mapping hash changed: {mapping_hash} != {EXPECTED_MAPPING_SHA256}"
        )

    tokens, token_to_condition = representative_tokens()
    inputs = load_inputs(Path(args.input_manifest), cfg)

    start = pd.Timestamp(cfg["start"])
    dev_end = pd.Timestamp(cfg["dev_end"])
    states, trades, state_audit, file_audit = build_states_and_trades(
        inputs,
        tokens,
        start,
        dev_end,
    )
    clock, clock_audit = build_clock(states, trades, cfg)
    primary, placebos, evidence = evaluate_candidates(clock, cfg)

    source_versions = (
        sorted(states["source_version"].dropna().astype(str).unique().tolist())
        if not states.empty
        else []
    )
    audit = {
        "experiment": "EXPERIMENT-005G",
        "lane": "A_STRICT_005F_REPLICATION",
        "slice": args.slice,
        "regime": cfg["regime"],
        "window_id": cfg["window_id"],
        "source_dataset": EXPECTED_DATASET,
        "dataset_version": 1,
        "accepted_mapping_sha256": mapping_hash,
        "representative_conditions": len(token_to_condition),
        "downloaded_source_files": len(inputs),
        "source_versions_seen": source_versions,
        "frozen_start": cfg["start"],
        "frozen_train_end": cfg["train_end"],
        "frozen_dev_end": cfg["dev_end"],
        "frozen_holdout_end": cfg["holdout_end"],
        "holdout_read": False,
        "v3_best_bid_ask_used_in_primary": False,
        "state_audit": state_audit,
        "clock_audit": clock_audit,
        "file_audit": file_audit,
        "real_sig_orders_sent": False,
    }
    (output / "lane_a_audit.json").write_text(
        json.dumps(audit, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (output / "lane_a_dev_results.json").write_text(
        json.dumps(
            {
                "experiment": "EXPERIMENT-005G",
                "slice": args.slice,
                "regime": cfg["regime"],
                "phase": "TRAIN_DEV",
                "holdout_read": False,
                "jump_threshold_abs_logit15": clock_audit[
                    "train_jump_threshold_abs_logit15"
                ],
                "primary": primary,
                "placebos": placebos,
                "interpretation_rule": (
                    "TRAIN/DEV support is not FRESH_REPLICATION. Final replication labels "
                    "require pre-holdout freeze followed by sealed HOLDOUT."
                ),
                "real_sig_orders_sent": False,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    write_csv(output / "lane_a_dev_block_evidence.csv", evidence)

    summary = {
        "slice": args.slice,
        "primary": [
            {
                "candidate_id": row["candidate_id"],
                "status": row["status"],
                "mean_loss_improvement": row.get("mean_loss_improvement"),
                "relative_mse_improvement": row.get("relative_mse_improvement"),
                "signflip_p": row.get("signflip_p"),
                "dev_supports_incremental": row.get("dev_supports_incremental"),
            }
            for row in primary
        ],
        "holdout_read": False,
        "real_sig_orders_sent": False,
    }
    print(json.dumps(summary, indent=2), flush=True)


if __name__ == "__main__":
    main()
