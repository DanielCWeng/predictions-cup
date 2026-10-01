# ruff: noqa
"""EXPERIMENT-005G exact sequential-family discovery.

Covers the two families intentionally blocked on the sampled broad panel:
1) native-BBO spread-state dwell -> future transition hazard;
2) depth-shock state -> future half-replenishment hazard.

All state clocks are computed from the full observable event sequence before sampling.
HOLDOUT files are rejected in TRAIN/DEV mode.
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
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler

import experiment_005g_lane_a as lane_a
import experiment_005g_lane_b as lane_b

SEED = 20261001008
SKLEARN_SEED = SEED % (2**32 - 1)
NS = 1_000_000_000
MAX_ROWS = 250_000
FDR_Q = 0.10
CFG = lane_b.CFG


def thin(frame: pd.DataFrame, n: int = MAX_ROWS) -> pd.DataFrame:
    if len(frame) <= n:
        return frame
    step = int(math.ceil(len(frame) / n))
    return frame.iloc[::step].head(n).copy()


def split_label(times: pd.DatetimeIndex) -> np.ndarray:
    return lane_a.split_label(
        times,
        pd.Timestamp(CFG["start"]),
        pd.Timestamp(CFG["train_end"]),
        pd.Timestamp(CFG["dev_end"]),
    )


def age_since(event_ns: np.ndarray, query_ns: np.ndarray) -> np.ndarray:
    index = np.searchsorted(event_ns, query_ns, side="right") - 1
    out = np.full(len(query_ns), np.nan, float)
    ok = index >= 0
    out[ok] = (query_ns[ok] - event_ns[index[ok]]) / NS
    return out


def state_transition_panel(bbo: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, Any]]:
    parts: list[pd.DataFrame] = []
    skipped = 0
    start = pd.Timestamp(CFG["start"])
    dev_end = pd.Timestamp(CFG["dev_end"])
    train_end = pd.Timestamp(CFG["train_end"])
    sample_mod = int(CFG["sample_mod"])

    for token, raw in bbo.groupby("token_id", sort=False):
        events = raw.sort_values("bbo_time").copy().reset_index(drop=True)
        train = events[
            (events["bbo_time"] >= start)
            & (events["bbo_time"] < train_end)
        ]
        if len(train) < 30:
            skipped += 1
            continue
        q1, q2 = train["spread"].quantile([1 / 3, 2 / 3]).tolist()
        if not np.isfinite(q1) or not np.isfinite(q2) or q1 >= q2:
            skipped += 1
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
        transition_mask = np.r_[False, states[1:] != states[:-1]]
        transition_ns = event_ns[transition_mask]

        grid = pd.date_range(
            start,
            dev_end,
            freq="15s",
            inclusive="left",
            tz="UTC",
        )
        query_ns = lane_a.datetime_ns(grid)
        index = np.searchsorted(event_ns, query_ns, side="right") - 1
        valid = index >= 0
        current_state = np.full(len(grid), np.nan, float)
        current_spread = np.full(len(grid), np.nan, float)
        bbo_source_ns = np.full(len(grid), -1, np.int64)
        current_state[valid] = states[index[valid]]
        current_spread[valid] = events["spread"].to_numpy(float)[index[valid]]
        bbo_source_ns[valid] = event_ns[index[valid]]
        bbo_age = np.where(
            bbo_source_ns >= 0,
            (query_ns - bbo_source_ns) / NS,
            np.nan,
        )
        dwell = age_since(transition_ns, query_ns)
        # Before the first transition, dwell since the first observed BBO is the
        # observable lower bound for current-state dwell.
        no_transition = ~np.isfinite(dwell) & valid
        dwell[no_transition] = (
            query_ns[no_transition] - event_ns[0]
        ) / NS

        frame = pd.DataFrame(
            {
                "token_id": str(token),
                "time": grid,
                "spread_state": current_state,
                "spread": current_spread,
                "bbo_age_s": bbo_age,
                "state_dwell_s": dwell,
                "bbo_updates_60": lane_a.rolling_counts(event_ns, query_ns, 60),
                "bbo_updates_300": lane_a.rolling_counts(event_ns, query_ns, 300),
            }
        )
        labels = split_label(grid)
        frame["split"] = labels

        for horizon in (60, 300):
            future_ns = query_ns + horizon * NS
            left = np.searchsorted(transition_ns, query_ns, side="right")
            right = np.searchsorted(transition_ns, future_ns, side="right")
            target = (right > left).astype(float)
            future_labels = split_label(
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
            & lane_a.stable_keep(str(token), grid, sample_mod)
        )
        if np.any(keep):
            parts.append(frame.loc[keep].copy())

    panel = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()
    audit = {
        "rows": len(panel),
        "tokens": int(panel["token_id"].nunique()) if not panel.empty else 0,
        "tokens_skipped": skipped,
        "sample_mod": int(CFG["sample_mod"]),
        "state_definition": "per-token TRAIN-only spread tertiles",
    }
    return panel, audit


def attach_bbo_context(depth: pd.DataFrame, bbo: pd.DataFrame) -> pd.DataFrame:
    if depth.empty:
        return depth
    parts: list[pd.DataFrame] = []
    for token, raw in depth.groupby("token_id", sort=False):
        d = raw.sort_values("depth_time").copy()
        b = bbo[bbo["token_id"].astype(str) == str(token)].sort_values("bbo_time")
        qns = lane_a.datetime_ns(d["depth_time"])
        if b.empty:
            d["bbo_age_s"] = np.nan
            d["bbo_updates_60"] = 0.0
        else:
            times = lane_a.datetime_ns(b["bbo_time"])
            d["bbo_age_s"] = age_since(times, qns)
            d["bbo_updates_60"] = lane_a.rolling_counts(times, qns, 60)
        parts.append(d)
    return pd.concat(parts, ignore_index=True) if parts else depth


def resilience_panel(
    depth: pd.DataFrame,
    bbo: pd.DataFrame,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    depth = attach_bbo_context(depth, bbo)
    rows: list[pd.DataFrame] = []
    depth_shocks = 0
    spread_shocks = 0

    for token, raw in depth.groupby("token_id", sort=False):
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
        previous_spread = np.r_[np.nan, spread[:-1]]
        interval = np.r_[np.nan, np.diff(times) / NS]
        continuity = np.isfinite(interval) & (interval <= 300)

        depth_loss = previous_depth - depth5
        shock_fraction = depth_loss / np.where(
            previous_depth > 0,
            previous_depth,
            np.nan,
        )
        depth_shock = (
            continuity
            & np.isfinite(shock_fraction)
            & (shock_fraction >= 0.25)
            & (depth_loss > 0)
        )
        spread_widen = (
            continuity
            & np.isfinite(previous_spread)
            & np.isfinite(spread)
            & ((spread - previous_spread) >= 0.02)
        )
        depth_shocks += int(depth_shock.sum())
        spread_shocks += int(spread_widen.sum())

        idx = np.flatnonzero(depth_shock)
        if not len(idx):
            continue
        shock = d.loc[idx].copy().reset_index(drop=True)
        shock["shock_size"] = shock_fraction[idx]
        shock["pre_depth_5c"] = previous_depth[idx]
        shock["post_depth_5c"] = depth5[idx]
        shock["spread"] = spread[idx]
        shock["snapshot_interval_s"] = interval[idx]
        shock["split"] = split_label(pd.DatetimeIndex(shock["depth_time"]))

        for horizon in (60, 300):
            target = np.full(len(idx), np.nan, float)
            horizon_ns = times[idx] + horizon * NS
            source_index = np.searchsorted(times, horizon_ns, side="right") - 1
            source_index = np.maximum(source_index, idx)
            source_time = times[source_index]
            age_at_horizon = (horizon_ns - source_time) / NS
            future_depth = depth5[source_index]
            denom = previous_depth[idx] - depth5[idx]
            recovery = (future_depth - depth5[idx]) / denom
            future_labels = split_label(
                pd.DatetimeIndex(pd.to_datetime(horizon_ns, utc=True))
            )
            current_labels = shock["split"].astype(str).to_numpy()
            valid = (
                (current_labels != "")
                & (future_labels == current_labels)
                & (age_at_horizon <= 60)
                & (source_index > idx)
                & np.isfinite(recovery)
            )
            target[valid] = (recovery[valid] >= 0.5).astype(float)
            shock[f"depth_half_recovered_h{horizon}"] = target

        rows.append(shock)

    panel = pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()
    audit = {
        "shock_rows": len(panel),
        "tokens": int(panel["token_id"].nunique()) if not panel.empty else 0,
        "depth_shocks_25pct": depth_shocks,
        "spread_widens_2c": spread_shocks,
        "depth_target_future_snapshot_max_age_s": 60,
    }
    return panel, audit


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
        draws[i] = float(np.mean(rng.choice(x, size=len(x), replace=True)))
    return float(np.quantile(draws, 0.025))


def evaluate(
    panel: pd.DataFrame,
    *,
    target: str,
    feature: str,
    baseline: list[str],
    target_family: str,
    feature_family: str,
) -> dict[str, Any]:
    needed = ["split", "token_id", "time", target, *baseline, feature]
    needed = list(dict.fromkeys(needed))
    frame = panel[needed].replace([np.inf, -np.inf], np.nan).dropna()
    train = thin(frame[frame["split"] == "TRAIN"].sort_values(["time", "token_id"]))
    dev = thin(frame[frame["split"] == "DEV"].sort_values(["time", "token_id"]))
    out: dict[str, Any] = {
        "target": target,
        "feature": feature,
        "target_family": target_family,
        "feature_family": feature_family,
        "baseline": baseline,
        "kind": "classification",
        "model": "LOGIT_C1.0",
        "train_rows": len(train),
        "dev_rows": len(dev),
        "markets": int(dev["token_id"].nunique()),
    }
    if len(train) < 500 or len(dev) < 200 or dev["token_id"].nunique() < 3:
        out["status"] = "INSUFFICIENT_SUPPORT"
        return out
    ytr = train[target].to_numpy(int)
    ydv = dev[target].to_numpy(int)
    if len(np.unique(ytr)) < 2 or len(np.unique(ydv)) < 2:
        out["status"] = "ONE_CLASS_TARGET"
        return out

    sb = StandardScaler().fit(train[baseline])
    xbtr = sb.transform(train[baseline])
    xbdv = sb.transform(dev[baseline])
    base = LogisticRegression(
        C=1.0,
        max_iter=500,
        random_state=SKLEARN_SEED,
    ).fit(xbtr, ytr)
    pred_b = base.predict_proba(xbdv)[:, 1]

    full_cols = [*baseline, feature]
    sf = StandardScaler().fit(train[full_cols])
    xftr = sf.transform(train[full_cols])
    xfdv = sf.transform(dev[full_cols])
    full = LogisticRegression(
        C=1.0,
        max_iter=500,
        random_state=SKLEARN_SEED,
    ).fit(xftr, ytr)
    pred_f = full.predict_proba(xfdv)[:, 1]

    lb = (ydv - pred_b) ** 2
    lf = (ydv - pred_f) ** 2
    diff = lb - lf
    evidence = dev[["token_id", "time"]].copy()
    evidence["value"] = diff
    evidence["block"] = evidence["time"].dt.floor("30min")
    blocks = (
        evidence.groupby("block", observed=True)["value"].mean().to_numpy(float)
    )
    market = evidence.groupby("token_id", observed=True)["value"].agg(["sum", "count"])
    total_sum = float(np.sum(diff))
    total_n = len(diff)
    leave = [
        (total_sum - float(row["sum"])) / (total_n - int(row["count"]))
        for _, row in market.iterrows()
        if total_n - int(row["count"]) > 0
    ]
    improvement = float(np.mean(diff))
    out.update(
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
            "signflip_p": signflip_p(blocks, f"{target}|{feature}"),
            "block_bootstrap_lower": bootstrap_lower(
                blocks, f"{target}|{feature}"
            ),
            "leave_market_min": float(min(leave)) if leave else np.nan,
        }
    )
    return out


def apply_bh(rows: list[dict[str, Any]]) -> None:
    groups: defaultdict[tuple[str, str], list[int]] = defaultdict(list)
    for index, row in enumerate(rows):
        if row.get("status") == "EVALUATED":
            groups[
                (str(row["target_family"]), str(row["feature_family"]))
            ].append(index)
    for indexes in groups.values():
        ordered = sorted(indexes, key=lambda i: float(rows[i]["signflip_p"]))
        total = len(ordered)
        adjusted = [1.0] * total
        running = 1.0
        max_rank = 0
        for rank, index in enumerate(ordered, 1):
            raw = float(rows[index]["signflip_p"])
            if raw <= FDR_Q * rank / total:
                max_rank = rank
        for offset in range(total - 1, -1, -1):
            rank = offset + 1
            raw = float(rows[ordered[offset]]["signflip_p"])
            running = min(running, raw * total / rank)
            adjusted[offset] = min(1.0, running)
        for rank, (index, p_adj) in enumerate(
            zip(ordered, adjusted, strict=True),
            1,
        ):
            row = rows[index]
            row["p_bh"] = p_adj
            row["fdr_pass"] = rank <= max_rank
            row["promotion_gate_pass"] = bool(
                row["fdr_pass"]
                and float(row["mean_loss_improvement"]) > 0
                and np.isfinite(row["block_bootstrap_lower"])
                and float(row["block_bootstrap_lower"]) > 0
                and np.isfinite(row["leave_market_min"])
                and float(row["leave_market_min"]) > 0
            )


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
    depth, bbo, extras_audit = lane_b.load_v3_extras(
        inputs,
        tokens,
        start,
        dev_end,
    )

    transition, transition_audit = state_transition_panel(bbo)
    resilience, resilience_audit = resilience_panel(depth, bbo)

    rows: list[dict[str, Any]] = []
    if not transition.empty:
        for target in ("state_transition_h60", "state_transition_h300"):
            rows.append(
                evaluate(
                    transition,
                    target=target,
                    feature="state_dwell_s",
                    baseline=[
                        "spread_state",
                        "bbo_age_s",
                        "bbo_updates_60",
                        "bbo_updates_300",
                    ],
                    target_family="REGIME_TRANSITION",
                    feature_family="STATE_TRANSITIONS",
                )
            )

    if not resilience.empty:
        for target in (
            "depth_half_recovered_h60",
            "depth_half_recovered_h300",
        ):
            for feature in (
                "top_imbalance",
                "microprice_disp_over_spread",
                "bbo_age_s",
                "bbo_updates_60",
            ):
                rows.append(
                    evaluate(
                        resilience.rename(columns={"depth_time": "time"}),
                        target=target,
                        feature=feature,
                        baseline=[
                            "shock_size",
                            "post_depth_5c",
                            "spread",
                            "snapshot_interval_s",
                        ],
                        target_family="RESILIENCE",
                        feature_family="RESILIENCE",
                    )
                )

    apply_bh(rows)
    promoted = [row for row in rows if row.get("promotion_gate_pass")]
    payload = {
        "schema_version": 1,
        "experiment": "EXPERIMENT-005G",
        "lane": "B_SEQUENTIAL_FAMILIES",
        "phase": "TRAIN_DEV",
        "holdout_read": False,
        "source_dataset": lane_a.EXPECTED_DATASET,
        "dataset_version": 1,
        "screen": rows,
        "promoted_train_dev": promoted,
        "transition_audit": transition_audit,
        "resilience_audit": resilience_audit,
        "extras_audit": extras_audit,
        "labels": {
            "promoted_train_dev": "DISCOVERY_ONLY",
            "not_promoted": "REJECTED_DISCOVERY",
        },
        "make_modified": False,
        "real_sig_orders_sent": False,
    }
    (output / "LANE_B_SEQUENTIAL_RESULTS.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "screen_cells": len(rows),
                "promoted_train_dev": len(promoted),
                "transition_rows": len(transition),
                "resilience_rows": len(resilience),
                "holdout_read": False,
                "real_sig_orders_sent": False,
            },
            indent=2,
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
