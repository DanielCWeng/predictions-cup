# ruff: noqa
"""Sealed HOLDOUT evaluator for EXPERIMENT-005G.

This runner is inert until a PRE_HOLDOUT_FREEZE.json exists and its SHA-256 is supplied
explicitly. It refits frozen candidates on TRAIN+DEV, scores HOLDOUT only, and cannot
select new coordinates from HOLDOUT.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier, HistGradientBoostingRegressor
from sklearn.linear_model import LogisticRegression, Ridge
from sklearn.preprocessing import StandardScaler

import experiment_005g_lane_a as lane_a
import experiment_005g_lane_b as lane_b
import experiment_005g_sequential as sequential

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_FREEZE = ROOT / "data/experiments/experiment_005g/PRE_HOLDOUT_FREEZE.json"
START = pd.Timestamp("2026-09-01T00:00:00Z")
TRAIN_END = pd.Timestamp("2026-09-04T00:00:00Z")
DEV_END = pd.Timestamp("2026-09-05T00:00:00Z")
HOLDOUT_END = pd.Timestamp("2026-09-06T00:00:00Z")
SAMPLE_MOD = 16
NS = 1_000_000_000
MAX_ROWS = 250_000
SEED = 20261001007
SKLEARN_SEED = SEED % (2**32 - 1)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_freeze(path: Path, expected_sha256: str) -> dict[str, Any]:
    actual = sha256(path)
    if actual != expected_sha256:
        raise RuntimeError(f"pre-holdout freeze hash mismatch: {actual} != {expected_sha256}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("experiment") != "EXPERIMENT-005G":
        raise RuntimeError("wrong experiment freeze")
    if payload.get("phase") != "PRE_HOLDOUT_FREEZE":
        raise RuntimeError("wrong freeze phase")
    if payload.get("holdout_read") is not False:
        raise RuntimeError("freeze must assert holdout_read=false")
    if payload.get("status") != "FROZEN":
        raise RuntimeError("pre-holdout freeze is not FROZEN")
    return payload


def load_inputs(path: Path) -> list[dict[str, str]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("dataset") != lane_a.EXPECTED_DATASET:
        raise RuntimeError("wrong source dataset")
    files = payload.get("files")
    if not isinstance(files, list) or not files:
        raise RuntimeError("holdout input manifest has no files")
    out: list[dict[str, str]] = []
    for item in files:
        remote = str(item["remote"])
        if not remote.startswith("baseline_sep/"):
            raise RuntimeError(f"non-baseline source in holdout job: {remote}")
        date_part = remote.split("date=", 1)[1].split("/", 1)[0]
        hour_part = remote.split("hour=", 1)[1][:2]
        hour = pd.Timestamp(f"{date_part}T{hour_part}:00:00Z")
        if hour < START or hour >= HOLDOUT_END:
            raise RuntimeError(f"source file outside frozen five-day window: {remote}")
        out.append({"remote": remote, "local": str(item["local"])})
    expected = 5 * 24
    if len(out) != expected:
        raise RuntimeError(f"expected {expected} hourly files, got {len(out)}")
    return sorted(out, key=lambda row: row["remote"])


def full_split(times: pd.DatetimeIndex) -> np.ndarray:
    labels = np.full(len(times), "", dtype=object)
    values = times.as_unit("ns").asi8
    purge = 300 * NS
    labels[(values >= START.value) & (values < TRAIN_END.value - purge)] = "TRAIN"
    labels[(values >= TRAIN_END.value) & (values < DEV_END.value - purge)] = "DEV"
    labels[(values >= DEV_END.value) & (values < HOLDOUT_END.value - purge)] = "HOLDOUT"
    return labels


def asof_depth(
    frame: pd.DataFrame,
    query_ns: np.ndarray,
    columns: list[str],
) -> tuple[dict[str, np.ndarray], np.ndarray]:
    return lane_b.asof_frame(frame, "depth_time", query_ns, columns)


def build_full_panel(
    states: pd.DataFrame,
    trades: pd.DataFrame,
    depth: pd.DataFrame,
    bbo: pd.DataFrame,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    states = lane_b.add_ofi(states)
    trade_features = lane_a.trade_features(trades, states)
    parts: list[pd.DataFrame] = []

    for token, state in states.groupby("token_id", sort=False):
        state = state.sort_values("observed_at").reset_index(drop=True)
        if len(state) < 2:
            continue
        grid = pd.date_range(
            START,
            HOLDOUT_END,
            freq="15s",
            inclusive="left",
            tz="UTC",
        )
        qns = lane_a.datetime_ns(grid)
        midpoint, segment, state_ns = lane_a.asof_from_states(state, qns, "midpoint")
        spread, _, _ = lane_a.asof_from_states(state, qns, "spread")
        qbid, _, _ = lane_a.asof_from_states(state, qns, "qbid")
        qask, _, _ = lane_a.asof_from_states(state, qns, "qask")
        last_genuine, _, _ = lane_a.asof_from_states(state, qns, "last_genuine_ns")
        logmid = lane_a.logit_array(midpoint)
        genuine_age = np.where(
            last_genuine >= 0,
            (qns - last_genuine) / NS,
            np.nan,
        )

        frame = pd.DataFrame(
            {
                "token_id": str(token),
                "time": grid,
                "midpoint": midpoint,
                "spread": spread,
                "segment": segment,
                "state_age_s": (qns - state_ns) / NS,
                "genuine_age_s": genuine_age,
                "price_change_age_s": genuine_age,
                "top_imbalance_pc": (qbid - qask) / np.where(
                    qbid + qask > 0,
                    qbid + qask,
                    np.nan,
                ),
            }
        )

        for horizon in (15, 30, 60, 300):
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

        genuine_times = lane_a.datetime_ns(
            state.loc[state["genuine_bbo"], "observed_at"]
        )
        for horizon in (15, 60, 300):
            frame[f"genuine_{horizon}"] = lane_a.rolling_counts(
                genuine_times,
                qns,
                horizon,
            )
            frame[f"price_updates_{horizon}"] = frame[f"genuine_{horizon}"]

        state_times = lane_a.datetime_ns(state["observed_at"])
        ofi = state["ofi"].to_numpy(float)
        frame["ofi_15"] = lane_b.irregular_sum(state_times, ofi, qns, 15)
        frame["ofi_60"] = lane_b.irregular_sum(state_times, ofi, qns, 60)
        frame["ofi_acceleration"] = frame["ofi_15"] / 15.0 - frame["ofi_60"] / 60.0

        tbbo = bbo[bbo["token_id"].astype(str) == str(token)].sort_values("bbo_time")
        if tbbo.empty:
            bbo_times = np.array([], dtype=np.int64)
            frame["bbo_age_s"] = np.nan
            for horizon in (15, 60, 300):
                frame[f"bbo_updates_{horizon}"] = 0.0
        else:
            bbo_times = lane_a.datetime_ns(tbbo["bbo_time"])
            frame["bbo_age_s"] = lane_b.age_since_events(bbo_times, qns)
            for horizon in (15, 60, 300):
                frame[f"bbo_updates_{horizon}"] = lane_a.rolling_counts(
                    bbo_times,
                    qns,
                    horizon,
                )
        frame["renewal_acceleration"] = (
            frame["bbo_updates_15"] / 15.0 - frame["bbo_updates_60"] / 60.0
        )
        frame["bbo_price_age_gap_s"] = frame["bbo_age_s"] - frame["price_change_age_s"]

        tdepth = depth[depth["token_id"].astype(str) == str(token)].sort_values(
            "depth_time"
        )
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
        dvals, dns = asof_depth(tdepth, qns, depth_cols)
        frame["snapshot_age_s"] = np.where(dns >= 0, (qns - dns) / NS, np.nan)
        fresh = frame["snapshot_age_s"].to_numpy(float) <= lane_b.DEPTH_MAX_AGE_S
        for col in depth_cols:
            values = dvals[col]
            values[~fresh] = np.nan
            frame[col] = values

        token_trades = trades[
            trades["token_id"].astype(str) == str(token)
        ].sort_values("observed_at")
        if token_trades.empty:
            frame["trade_count_60"] = 0.0
        else:
            trade_ns = lane_a.datetime_ns(token_trades["observed_at"])
            frame["trade_count_60"] = lane_a.rolling_counts(
                trade_ns,
                qns,
                60,
            )

        tf = trade_features[
            trade_features["token_id"].astype(str) == str(token)
        ].sort_values("bin_time")
        if tf.empty:
            frame["trade_abs_impact_60"] = np.nan
        else:
            tvals, _ = lane_b.asof_frame(
                tf,
                "bin_time",
                qns,
                ["trade_abs_impact_60"],
            )
            frame["trade_abs_impact_60"] = tvals["trade_abs_impact_60"]

        frame["freshness_x_spread"] = frame["bbo_age_s"] * frame["spread"]
        frame["imbalance_x_ofi"] = frame["top_imbalance"] * frame["ofi_60"]
        frame["volatility_x_liquidity"] = frame["rv_60"] * frame["spread"]
        frame["spread_x_distance"] = frame["spread"] * frame["distance_from_0_5"]

        labels = full_split(grid)
        frame["split"] = labels

        for horizon in (15, 60, 300):
            future_ns = qns + horizon * NS
            fmid, fsegment, _ = lane_a.asof_from_states(
                state,
                future_ns,
                "midpoint",
            )
            fspread, _, _ = lane_a.asof_from_states(
                state,
                future_ns,
                "spread",
            )
            future_labels = full_split(
                pd.DatetimeIndex(pd.to_datetime(future_ns, utc=True))
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

            left_native = np.searchsorted(bbo_times, qns, side="right")
            right_native = np.searchsorted(bbo_times, future_ns, side="right")
            native_update = (right_native > left_native).astype(float)
            native_update[(future_labels != labels) | (labels == "")] = np.nan
            frame[f"bbo_update_h{horizon}"] = native_update

            left_core = np.searchsorted(genuine_times, qns, side="right")
            right_core = np.searchsorted(genuine_times, future_ns, side="right")
            core_update = (right_core > left_core).astype(float)
            core_update[(future_labels != labels) | (labels == "")] = np.nan
            frame[f"update_h{horizon}"] = core_update

        if not tdepth.empty:
            future_depth, future_depth_ns = asof_depth(
                tdepth,
                qns + 60 * NS,
                ["depth_5c"],
            )
            future_age = np.where(
                future_depth_ns >= 0,
                (qns + 60 * NS - future_depth_ns) / NS,
                np.nan,
            )
            change = future_depth["depth_5c"] - frame["depth_5c"].to_numpy(float)
            future_labels = full_split(
                pd.DatetimeIndex(pd.to_datetime(qns + 60 * NS, utc=True))
            )
            invalid = (
                ~(future_age <= lane_b.DEPTH_MAX_AGE_S)
                | ~np.isfinite(frame["depth_5c"].to_numpy(float))
                | (future_labels != labels)
                | (labels == "")
            )
            change[invalid] = np.nan
            frame["depth_h60"] = change
        else:
            frame["depth_h60"] = np.nan

        keep = (
            (frame["split"] != "")
            & np.isfinite(frame["midpoint"])
            & lane_a.stable_keep(str(token), grid, SAMPLE_MOD)
        )
        if np.any(keep):
            parts.append(frame.loc[keep].copy())

    panel = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()
    if panel.empty:
        raise RuntimeError("holdout panel is empty")
    panel = panel.sort_values(["token_id", "time"], kind="stable").reset_index(drop=True)
    audit = {
        "rows": len(panel),
        "tokens": int(panel["token_id"].nunique()),
        "train_rows": int((panel["split"] == "TRAIN").sum()),
        "dev_rows": int((panel["split"] == "DEV").sum()),
        "holdout_rows": int((panel["split"] == "HOLDOUT").sum()),
        "sample_mod": SAMPLE_MOD,
    }
    return panel, audit


def build_transition_holdout_panel(bbo: pd.DataFrame) -> pd.DataFrame:
    parts: list[pd.DataFrame] = []
    for token, raw in bbo.groupby("token_id", sort=False):
        events = raw.sort_values("bbo_time").copy().reset_index(drop=True)
        train = events[
            (events["bbo_time"] >= START)
            & (events["bbo_time"] < TRAIN_END)
        ]
        if len(train) < 30:
            continue
        q1, q2 = train["spread"].quantile([1 / 3, 2 / 3]).tolist()
        if not np.isfinite(q1) or not np.isfinite(q2) or q1 >= q2:
            continue
        events["spread_state"] = np.select(
            [
                events["spread"] <= q1,
                events["spread"] <= q2,
            ],
            [0, 1],
            default=2,
        ).astype(int)
        event_ns = lane_a.datetime_ns(events["bbo_time"])
        states = events["spread_state"].to_numpy(int)
        transitions = np.r_[False, states[1:] != states[:-1]]
        transition_ns = event_ns[transitions]

        grid = pd.date_range(
            START,
            HOLDOUT_END,
            freq="15s",
            inclusive="left",
            tz="UTC",
        )
        query_ns = lane_a.datetime_ns(grid)
        index = np.searchsorted(event_ns, query_ns, side="right") - 1
        valid = index >= 0
        current_state = np.full(len(grid), np.nan, float)
        bbo_source_ns = np.full(len(grid), -1, np.int64)
        current_state[valid] = states[index[valid]]
        bbo_source_ns[valid] = event_ns[index[valid]]
        bbo_age = np.where(
            bbo_source_ns >= 0,
            (query_ns - bbo_source_ns) / NS,
            np.nan,
        )
        dwell = sequential.age_since(transition_ns, query_ns)
        no_transition = ~np.isfinite(dwell) & valid
        dwell[no_transition] = (
            query_ns[no_transition] - event_ns[0]
        ) / NS

        frame = pd.DataFrame(
            {
                "token_id": str(token),
                "time": grid,
                "spread_state": current_state,
                "bbo_age_s": bbo_age,
                "state_dwell_s": dwell,
                "bbo_updates_60": lane_a.rolling_counts(
                    event_ns,
                    query_ns,
                    60,
                ),
                "bbo_updates_300": lane_a.rolling_counts(
                    event_ns,
                    query_ns,
                    300,
                ),
            }
        )
        labels = full_split(grid)
        frame["split"] = labels
        for horizon in (60, 300):
            future_ns = query_ns + horizon * NS
            left = np.searchsorted(transition_ns, query_ns, side="right")
            right = np.searchsorted(transition_ns, future_ns, side="right")
            target = (right > left).astype(float)
            future_labels = full_split(
                pd.DatetimeIndex(pd.to_datetime(future_ns, utc=True))
            )
            invalid = (
                (labels == "")
                | (future_labels != labels)
                | ~np.isfinite(current_state)
                | (bbo_age > 300)
            )
            target[invalid] = np.nan
            frame[f"state_transition_h{horizon}"] = target
        keep = (
            (frame["split"] != "")
            & np.isfinite(frame["spread_state"])
            & (frame["bbo_age_s"] <= 300)
            & lane_a.stable_keep(str(token), grid, SAMPLE_MOD)
        )
        if np.any(keep):
            parts.append(frame.loc[keep].copy())
    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()


def build_resilience_holdout_panel(
    depth: pd.DataFrame,
    bbo: pd.DataFrame,
) -> pd.DataFrame:
    depth = sequential.attach_bbo_context(depth, bbo)
    parts: list[pd.DataFrame] = []
    for _, raw in depth.groupby("token_id", sort=False):
        d = raw.sort_values("depth_time").copy().reset_index(drop=True)
        if len(d) < 2:
            continue
        times = lane_a.datetime_ns(d["depth_time"])
        depth5 = d["depth_5c"].to_numpy(float)
        spread = (
            d["depth_best_ask"].to_numpy(float)
            - d["depth_best_bid"].to_numpy(float)
        )
        previous_depth = np.r_[np.nan, depth5[:-1]]
        interval = np.r_[np.nan, np.diff(times) / NS]
        continuity = np.isfinite(interval) & (interval <= 300)
        depth_loss = previous_depth - depth5
        shock_fraction = depth_loss / np.where(
            previous_depth > 0,
            previous_depth,
            np.nan,
        )
        shock_mask = (
            continuity
            & np.isfinite(shock_fraction)
            & (shock_fraction >= 0.25)
            & (depth_loss > 0)
        )
        indexes = np.flatnonzero(shock_mask)
        if not len(indexes):
            continue
        shock = d.loc[indexes].copy().reset_index(drop=True)
        shock["time"] = shock["depth_time"]
        shock["shock_size"] = shock_fraction[indexes]
        shock["pre_depth_5c"] = previous_depth[indexes]
        shock["post_depth_5c"] = depth5[indexes]
        shock["spread"] = spread[indexes]
        shock["snapshot_interval_s"] = interval[indexes]
        shock["split"] = full_split(pd.DatetimeIndex(shock["depth_time"]))

        for horizon in (60, 300):
            target = np.full(len(indexes), np.nan, float)
            horizon_ns = times[indexes] + horizon * NS
            source_index = np.searchsorted(times, horizon_ns, side="right") - 1
            source_index = np.maximum(source_index, indexes)
            source_time = times[source_index]
            age_at_horizon = (horizon_ns - source_time) / NS
            future_depth = depth5[source_index]
            denom = previous_depth[indexes] - depth5[indexes]
            recovery = (future_depth - depth5[indexes]) / denom
            future_labels = full_split(
                pd.DatetimeIndex(pd.to_datetime(horizon_ns, utc=True))
            )
            current_labels = shock["split"].astype(str).to_numpy()
            valid = (
                (current_labels != "")
                & (future_labels == current_labels)
                & (age_at_horizon <= 60)
                & (source_index > indexes)
                & np.isfinite(recovery)
            )
            target[valid] = (recovery[valid] >= 0.5).astype(float)
            shock[f"depth_half_recovered_h{horizon}"] = target
        parts.append(shock)
    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()


def thin(frame: pd.DataFrame, n: int = MAX_ROWS) -> pd.DataFrame:
    if len(frame) <= n:
        return frame
    step = int(math.ceil(len(frame) / n))
    return frame.iloc[::step].head(n).copy()


def make_model(candidate: dict[str, Any], classification: bool) -> Any:
    model = str(candidate["model"])
    if model.startswith("HGB_D"):
        tail = model.removeprefix("HGB_D")
        depth_s, lr_s = tail.split("_LR", 1)
        kwargs = {
            "max_depth": int(depth_s),
            "learning_rate": float(lr_s),
            "max_iter": 200,
            "random_state": SKLEARN_SEED,
        }
        if classification:
            return HistGradientBoostingClassifier(**kwargs)
        return HistGradientBoostingRegressor(**kwargs)
    if model == "LOGIT_C1.0":
        if not classification:
            raise ValueError("LOGIT_C1.0 requested for regression")
        return LogisticRegression(C=1.0, max_iter=500, random_state=SKLEARN_SEED)
    if model == "RIDGE_1.0":
        if classification:
            raise ValueError("RIDGE_1.0 requested for classification")
        return Ridge(alpha=1.0)
    raise ValueError(f"unsupported frozen model: {model}")


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
    draws = 8192
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
        hashlib.sha256(f"{SEED}|bootstrap|{key}".encode()).digest()[:8],
        "big",
    )
    rng = np.random.default_rng(seed)
    draws = np.empty(4000, float)
    for i in range(len(draws)):
        draws[i] = np.mean(rng.choice(x, size=len(x), replace=True))
    return float(np.quantile(draws, 0.025))


def score_candidate(panel: pd.DataFrame, candidate: dict[str, Any]) -> dict[str, Any]:
    target = str(candidate["target"])
    baseline = [str(value) for value in candidate["baseline"]]
    feature = str(candidate["feature"])
    kind = str(candidate["kind"])
    columns = ["split", "token_id", "time", target, *baseline, feature]
    columns = list(dict.fromkeys(columns))
    frame = panel[columns].replace([np.inf, -np.inf], np.nan).dropna()
    train = thin(frame[frame["split"] == "TRAIN"].sort_values(["time", "token_id"]))
    dev = thin(frame[frame["split"] == "DEV"].sort_values(["time", "token_id"]))
    holdout = frame[frame["split"] == "HOLDOUT"].sort_values(["time", "token_id"])
    result: dict[str, Any] = {
        "candidate_id": candidate["candidate_id"],
        "lane": candidate["lane"],
        "target": target,
        "feature": feature,
        "feature_family": candidate.get("feature_family"),
        "target_family": candidate.get("target_family"),
        "model": candidate["model"],
        "train_rows": len(train),
        "dev_rows": len(dev),
        "holdout_rows": len(holdout),
        "holdout_markets": int(holdout["token_id"].nunique()),
    }
    if len(train) < 500 or len(dev) < 200 or len(holdout) < 200:
        result["status"] = "INSUFFICIENT_HOLDOUT_SUPPORT"
        return result
    classification = kind == "classification"
    y_train = train[target].to_numpy(int if classification else float)
    y_dev = dev[target].to_numpy(int if classification else float)
    y_fit = np.concatenate([y_train, y_dev])
    y_holdout = holdout[target].to_numpy(int if classification else float)
    if classification and (
        len(np.unique(y_fit)) < 2 or len(np.unique(y_holdout)) < 2
    ):
        result["status"] = "ONE_CLASS_HOLDOUT"
        return result

    fit = pd.concat([train, dev], ignore_index=True)
    base_scaler = StandardScaler().fit(fit[baseline])
    full_cols = [*baseline, feature]
    full_scaler = StandardScaler().fit(fit[full_cols])
    base_model = make_model(candidate, classification)
    full_model = make_model(candidate, classification)
    base_model.fit(base_scaler.transform(fit[baseline]), y_fit)
    full_model.fit(full_scaler.transform(fit[full_cols]), y_fit)
    if classification:
        pred_base = base_model.predict_proba(base_scaler.transform(holdout[baseline]))[:, 1]
        pred_full = full_model.predict_proba(full_scaler.transform(holdout[full_cols]))[:, 1]
    else:
        pred_base = base_model.predict(base_scaler.transform(holdout[baseline]))
        pred_full = full_model.predict(full_scaler.transform(holdout[full_cols]))

    loss_base = (y_holdout - pred_base) ** 2
    loss_full = (y_holdout - pred_full) ** 2
    diff = loss_base - loss_full
    evidence = holdout[["token_id", "time"]].copy()
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
    for _, row in evidence.groupby("token_id", observed=True)["loss_improvement"].agg(
        ["sum", "count"]
    ).iterrows():
        remaining = total_n - int(row["count"])
        if remaining > 0:
            leave.append((total_sum - float(row["sum"])) / remaining)

    improvement = float(np.mean(diff))
    p_value = signflip_p(blocks, str(candidate["candidate_id"]))
    lower = bootstrap_lower(blocks, str(candidate["candidate_id"]))
    leave_min = float(min(leave)) if leave else np.nan
    gate = bool(
        improvement > 0
        and p_value <= 0.10
        and np.isfinite(lower)
        and lower > 0
        and np.isfinite(leave_min)
        and leave_min > 0
    )
    if candidate["lane"] == "A_STRICT_005F_REPLICATION":
        disposition = "FRESH_REPLICATION" if gate else "REPLICATION_FAILED"
    else:
        disposition = (
            "INDEPENDENTLY_SUPPORTED_DISCOVERY"
            if gate
            else "DISCOVERY_NOT_CONFIRMED"
        )
    result.update(
        {
            "status": "EVALUATED",
            "baseline_mse": float(np.mean(loss_base)),
            "challenger_mse": float(np.mean(loss_full)),
            "mean_loss_improvement": improvement,
            "relative_mse_improvement": (
                improvement / float(np.mean(loss_base))
                if float(np.mean(loss_base)) > 0
                else np.nan
            ),
            "blocks": len(blocks),
            "positive_blocks": int(np.sum(blocks > 0)),
            "signflip_p": p_value,
            "block_bootstrap_lower": lower,
            "leave_market_min": leave_min,
            "holdout_gate_pass": gate,
            "disposition": disposition,
        }
    )
    return result


def apply_discovery_holdout_fdr(results: list[dict[str, Any]]) -> None:
    groups: dict[tuple[str, str], list[int]] = {}
    for index, row in enumerate(results):
        if (
            row.get("lane") != "B_OPEN_DISCOVERY"
            or row.get("status") != "EVALUATED"
        ):
            continue
        key = (
            str(row.get("target_family", "")),
            str(row.get("feature_family", "")),
        )
        groups.setdefault(key, []).append(index)

    for indexes in groups.values():
        ordered = sorted(indexes, key=lambda idx: float(results[idx]["signflip_p"]))
        total = len(ordered)
        adjusted = [1.0] * total
        running = 1.0
        max_rank = 0
        for rank, index in enumerate(ordered, 1):
            raw = float(results[index]["signflip_p"])
            if raw <= 0.10 * rank / total:
                max_rank = rank
        for offset in range(total - 1, -1, -1):
            rank = offset + 1
            raw = float(results[ordered[offset]]["signflip_p"])
            running = min(running, raw * total / rank)
            adjusted[offset] = min(1.0, running)
        for rank, (index, adjusted_p) in enumerate(
            zip(ordered, adjusted, strict=True),
            1,
        ):
            row = results[index]
            row["holdout_p_bh"] = adjusted_p
            row["holdout_fdr_pass"] = rank <= max_rank
            gate = bool(
                row.get("holdout_gate_pass")
                and row["holdout_fdr_pass"]
            )
            row["holdout_gate_pass"] = gate
            row["disposition"] = (
                "INDEPENDENTLY_SUPPORTED_DISCOVERY"
                if gate
                else "DISCOVERY_NOT_CONFIRMED"
            )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-manifest", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--freeze-path", default=str(DEFAULT_FREEZE))
    parser.add_argument("--freeze-sha256", required=True)
    args = parser.parse_args()

    freeze_path = Path(args.freeze_path)
    freeze = load_freeze(freeze_path, args.freeze_sha256)
    candidates = freeze.get("holdout_candidates")
    if not isinstance(candidates, list) or not candidates:
        raise RuntimeError("freeze contains no holdout candidates")

    if sha256(lane_a.MAPPING) != lane_a.EXPECTED_MAPPING_SHA256:
        raise RuntimeError("accepted mapping changed since freeze")

    inputs = load_inputs(Path(args.input_manifest))
    tokens, _ = lane_a.representative_tokens()
    states, trades, state_audit, file_audit = lane_a.build_states_and_trades(
        inputs,
        tokens,
        START,
        HOLDOUT_END,
    )
    depth, bbo, extras_audit = lane_b.load_v3_extras(
        inputs,
        tokens,
        START,
        HOLDOUT_END,
    )
    panel, panel_audit = build_full_panel(states, trades, depth, bbo)

    transition_panel = build_transition_holdout_panel(bbo)
    resilience_panel = build_resilience_holdout_panel(depth, bbo)
    results: list[dict[str, Any]] = []
    for candidate in candidates:
        if candidate.get("sequential_family"):
            target = str(candidate["target"])
            if target.startswith("state_transition_"):
                source_panel = transition_panel
            elif target.startswith("depth_half_recovered_"):
                source_panel = resilience_panel
            else:
                raise RuntimeError(
                    f"unsupported sequential HOLDOUT target: {target}"
                )
        else:
            source_panel = panel
        results.append(score_candidate(source_panel, candidate))
    apply_discovery_holdout_fdr(results)
    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 1,
        "experiment": "EXPERIMENT-005G",
        "phase": "HOLDOUT",
        "pre_holdout_freeze_sha256": args.freeze_sha256,
        "holdout_window": [DEV_END.isoformat(), HOLDOUT_END.isoformat()],
        "holdout_files_read": [
            item["remote"] for item in inputs
            if "date=2026-09-05" in item["remote"]
        ],
        "results": results,
        "selection_or_tuning_after_holdout": False,
        "discovery_holdout_fdr": "BH q=0.10 within frozen target_family x feature_family cells",
        "make_modified": False,
        "real_sig_orders_sent": False,
    }
    (out / "HOLDOUT_RESULTS.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    audit = {
        "schema_version": 1,
        "experiment": "EXPERIMENT-005G",
        "phase": "HOLDOUT",
        "pre_holdout_freeze_sha256": args.freeze_sha256,
        "dataset": lane_a.EXPECTED_DATASET,
        "dataset_version": 1,
        "state_audit": state_audit,
        "extras_audit": extras_audit,
        "panel_audit": panel_audit,
        "sequential_panel_audit": {
            "transition_rows": len(transition_panel),
            "transition_tokens": (
                int(transition_panel["token_id"].nunique())
                if not transition_panel.empty
                else 0
            ),
            "resilience_rows": len(resilience_panel),
            "resilience_tokens": (
                int(resilience_panel["token_id"].nunique())
                if not resilience_panel.empty
                else 0
            ),
        },
        "file_audit": file_audit,
        "holdout_read": True,
        "selection_or_tuning_after_holdout": False,
        "real_sig_orders_sent": False,
    }
    (out / "HOLDOUT_AUDIT.json").write_text(
        json.dumps(audit, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "candidates": len(candidates),
                "fresh_replications": sum(
                    row.get("disposition") == "FRESH_REPLICATION"
                    for row in results
                ),
                "supported_discoveries": sum(
                    row.get("disposition") == "INDEPENDENTLY_SUPPORTED_DISCOVERY"
                    for row in results
                ),
                "real_sig_orders_sent": False,
            },
            indent=2,
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
