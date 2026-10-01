# ruff: noqa
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

WORK = Path("/kaggle/working")
CLOCK_HORIZONS = (1, 5, 15, 30, 60, 300)
EVENT_HORIZONS = (1, 2, 5, 10, 25, 50)
ARRIVAL_FEATURES = [
    "mid",
    "spread",
    "log_depth_2c",
    "activity_60",
    "mid_vol_60",
    "vpin_60",
]
DIRECTION_FEATURES = [
    "imbalance_touch",
    "microprice_deviation",
    "ofi_60",
    "signed_flow_60",
    "pre_move_30",
    "spread",
    "log_depth_2c",
]


def locate(name: str) -> Path:
    matches = list(Path("/kaggle/input").rglob(name))
    if len(matches) != 1:
        raise RuntimeError(f"expected one {name}, found {matches}")
    return matches[0]


def require_columns(frame: pd.DataFrame, names: list[str]) -> None:
    missing = [name for name in names if name not in frame.columns]
    if missing:
        raise RuntimeError(f"missing required columns {missing}")


def auc_score(y: np.ndarray, score: np.ndarray) -> float:
    y = np.asarray(y, dtype=int)
    score = np.asarray(score, dtype=float)
    pos = y == 1
    neg = y == 0
    n_pos = int(pos.sum())
    n_neg = int(neg.sum())
    if n_pos == 0 or n_neg == 0:
        return math.nan
    ranks = pd.Series(score).rank(method="average").to_numpy(float)
    return float((ranks[pos].sum() - n_pos * (n_pos + 1) / 2.0) / (n_pos * n_neg))


def logistic_fit(
    train: pd.DataFrame,
    test: pd.DataFrame,
    features: list[str],
    target: str,
) -> tuple[dict[str, Any], np.ndarray, np.ndarray]:
    cols = features + [target]
    tr = train[cols].replace([np.inf, -np.inf], np.nan).dropna().copy()
    te = test[cols].replace([np.inf, -np.inf], np.nan).dropna().copy()
    if len(tr) < 100 or len(te) < 50:
        return ({"status": "INSUFFICIENT", "train_n": len(tr), "dev_n": len(te)}, np.array([]), np.array([]))
    ytr = tr[target].astype(int).to_numpy()
    yte = te[target].astype(int).to_numpy()
    if len(np.unique(ytr)) < 2 or len(np.unique(yte)) < 2:
        return ({"status": "ONE_CLASS", "train_n": len(tr), "dev_n": len(te)}, np.array([]), np.array([]))
    xtr0 = tr[features].to_numpy(float)
    xte0 = te[features].to_numpy(float)
    mean = xtr0.mean(axis=0)
    std = xtr0.std(axis=0)
    std[std == 0] = 1.0
    xtr = np.column_stack([np.ones(len(xtr0)), (xtr0 - mean) / std])
    xte = np.column_stack([np.ones(len(xte0)), (xte0 - mean) / std])
    beta = np.zeros(xtr.shape[1], dtype=float)
    ridge = np.eye(xtr.shape[1]) * 1e-4
    ridge[0, 0] = 0.0
    for _ in range(50):
        z = np.clip(xtr @ beta, -30, 30)
        p = 1.0 / (1.0 + np.exp(-z))
        w = np.clip(p * (1.0 - p), 1e-6, None)
        grad = xtr.T @ (ytr - p) - ridge @ beta
        h = (xtr.T * w) @ xtr + ridge
        step = np.linalg.solve(h, grad)
        beta += step
        if float(np.max(np.abs(step))) < 1e-8:
            break
    ptr = 1.0 / (1.0 + np.exp(-np.clip(xtr @ beta, -30, 30)))
    pte = 1.0 / (1.0 + np.exp(-np.clip(xte @ beta, -30, 30)))
    baseline = float(np.mean(ytr))
    result = {
        "status": "OK",
        "features": features,
        "train_n": int(len(tr)),
        "dev_n": int(len(te)),
        "train_positive": int(ytr.sum()),
        "dev_positive": int(yte.sum()),
        "train_auc": auc_score(ytr, ptr),
        "dev_auc": auc_score(yte, pte),
        "train_brier": float(np.mean((ytr - ptr) ** 2)),
        "dev_brier": float(np.mean((yte - pte) ** 2)),
        "dev_baseline_brier": float(np.mean((yte - baseline) ** 2)),
        "coefficients_standardized": [float(value) for value in beta],
    }
    te_out = te.copy()
    te_out["_prediction"] = pte
    te_out["_target"] = yte
    return result, te_out.index.to_numpy(), pte


def fit_ols(train: pd.DataFrame, dev: pd.DataFrame, features: list[str], target: str) -> dict[str, Any]:
    cols = features + [target]
    tr = train[cols].replace([np.inf, -np.inf], np.nan).dropna()
    te = dev[cols].replace([np.inf, -np.inf], np.nan).dropna()
    if len(tr) < max(50, len(features) * 10) or len(te) < 30:
        return {"status": "INSUFFICIENT", "features": features, "train_n": len(tr), "dev_n": len(te)}
    xtr0 = tr[features].to_numpy(float)
    xte0 = te[features].to_numpy(float)
    mean = xtr0.mean(axis=0)
    std = xtr0.std(axis=0)
    std[std == 0] = 1.0
    xtr = np.column_stack([np.ones(len(xtr0)), (xtr0 - mean) / std])
    xte = np.column_stack([np.ones(len(xte0)), (xte0 - mean) / std])
    ytr = tr[target].to_numpy(float)
    yte = te[target].to_numpy(float)
    beta, *_ = np.linalg.lstsq(xtr, ytr, rcond=None)
    pte = xte @ beta
    ptr = xtr @ beta
    def metrics(y: np.ndarray, pred: np.ndarray) -> tuple[float, float]:
        denom = float(np.sum((y - y.mean()) ** 2))
        r2 = 1.0 - float(np.sum((y - pred) ** 2)) / denom if denom > 0 else math.nan
        rmse = float(np.sqrt(np.mean((y - pred) ** 2)))
        return r2, rmse
    tr_r2, tr_rmse = metrics(ytr, ptr)
    dev_r2, dev_rmse = metrics(yte, pte)
    return {
        "status": "OK",
        "features": features,
        "train_n": int(len(tr)),
        "dev_n": int(len(te)),
        "train_r2": tr_r2,
        "dev_r2": dev_r2,
        "train_rmse": tr_rmse,
        "dev_rmse": dev_rmse,
        "coefficients_standardized": [float(value) for value in beta],
    }


def matched_case_control(
    fills: pd.DataFrame,
    controls: pd.DataFrame,
    pairs: pd.DataFrame,
) -> pd.DataFrame:
    fill_by_group = fills.set_index("group_id", drop=False)
    controls_key = controls.copy()
    controls_key["_key"] = (
        controls_key["window_id"].astype(str)
        + "|"
        + controls_key["token_id"].astype(str)
        + "|"
        + controls_key["anchor_ts_ns"].astype("int64").astype(str)
    )
    control_by_key = controls_key.set_index("_key", drop=False)
    rows: list[dict[str, Any]] = []
    for pair in pairs.itertuples(index=False):
        if pair.group_id not in fill_by_group.index:
            continue
        f = fill_by_group.loc[pair.group_id]
        key = f"{pair.window_id}|{pair.token_id}|{int(pair.control_ts_ns)}"
        if key not in control_by_key.index:
            continue
        c = control_by_key.loc[key]
        if isinstance(f, pd.DataFrame):
            f = f.iloc[0]
        if isinstance(c, pd.DataFrame):
            c = c.iloc[0]
        fill_row = {
            "split": f["split"],
            "window_id": f["window_id"],
            "token_id": f["token_id"],
            "anchor_ts_ns": f["anchor_ts_ns"],
            "arrival": 1,
        }
        control_row = {
            "split": c["split"],
            "window_id": c["window_id"],
            "token_id": c["token_id"],
            "anchor_ts_ns": c["anchor_ts_ns"],
            "arrival": 0,
        }
        for feature in ARRIVAL_FEATURES:
            fill_row[feature] = f.get(feature)
            control_row[feature] = c.get(feature)
        rows.extend([fill_row, control_row])
    return pd.DataFrame(rows)


def subgroup_auc(
    frame: pd.DataFrame,
    predictions: pd.Series,
    target: str,
    group: str,
    min_rows: int = 20,
) -> dict[str, Any]:
    values = []
    work = frame.copy()
    work["_prediction"] = predictions
    for key, part in work.groupby(group):
        if len(part) < min_rows or part[target].nunique() < 2:
            continue
        values.append((str(key), auc_score(part[target].to_numpy(int), part["_prediction"].to_numpy(float)), len(part)))
    aucs = [value[1] for value in values if math.isfinite(value[1])]
    return {
        "groups": int(len(values)),
        "median_auc": float(np.median(aucs)) if aucs else math.nan,
        "p10_auc": float(np.quantile(aucs, 0.10)) if aucs else math.nan,
        "positive_over_half_share": float(np.mean(np.asarray(aucs) > 0.5)) if aucs else math.nan,
        "details": [{"group": k, "auc": a, "n": n} for k, a, n in values],
    }


def time_half_auc(frame: pd.DataFrame, predictions: pd.Series, target: str) -> dict[str, Any]:
    work = frame.copy()
    work["_prediction"] = predictions
    work = work.sort_values("anchor_ts_ns")
    midpoint = len(work) // 2
    halves = {"FIRST": work.iloc[:midpoint], "SECOND": work.iloc[midpoint:]}
    out = {}
    for name, part in halves.items():
        out[name] = (
            auc_score(part[target].to_numpy(int), part["_prediction"].to_numpy(float))
            if len(part) and part[target].nunique() >= 2
            else math.nan
        )
    return out


def impact_curves(fills: pd.DataFrame) -> pd.DataFrame:
    train = fills[fills["split"] == "TRAIN"]
    rows: list[dict[str, Any]] = []
    features = [
        "venue_size_over_touch",
        "episode_size_over_touch",
        "signed_imbalance_touch",
        "signed_microprice_deviation",
        "signed_ofi_60",
        "vpin_60",
        "book_age_ms",
        "activity_60",
    ]
    for feature in features:
        source = pd.to_numeric(train[feature], errors="coerce").replace([np.inf, -np.inf], np.nan).dropna()
        if len(source) < 50:
            continue
        edges = np.unique(np.quantile(source, [0, .2, .4, .6, .8, 1]))
        if len(edges) < 3:
            continue
        work = fills.copy()
        work["_bin"] = pd.cut(pd.to_numeric(work[feature], errors="coerce"), bins=edges, include_lowest=True, duplicates="drop")
        for (split, bucket), group in work.groupby(["split", "_bin"], observed=True):
            rec: dict[str, Any] = {
                "feature": feature,
                "split": split,
                "bucket": str(bucket),
                "fills": int(len(group)),
                "tokens": int(group["token_id"].nunique()),
            }
            for horizon in CLOCK_HORIZONS:
                col = f"signed_move_{horizon}s"
                rec[f"mean_{col}"] = float(pd.to_numeric(group[col], errors="coerce").mean())
            for horizon in EVENT_HORIZONS:
                col = f"signed_move_e{horizon}"
                rec[f"mean_{col}"] = float(pd.to_numeric(group[col], errors="coerce").mean())
            rows.append(rec)
    return pd.DataFrame(rows)


def reversal_table(fills: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for initial in ["signed_move_e1", "signed_move_5s"]:
        if initial not in fills.columns:
            continue
        for later in ["signed_move_30s", "signed_move_60s", "signed_move_300s"]:
            a = pd.to_numeric(fills[initial], errors="coerce")
            b = pd.to_numeric(fills[later], errors="coerce")
            valid = a.notna() & b.notna()
            work = fills.loc[valid, ["split", "token_id", "episode_size_over_touch", "failed_replenish_80_30s"]].copy()
            work["a"] = a[valid]
            work["b"] = b[valid]
            eps = 0.005
            work["class"] = np.select(
                [
                    (work["a"] > eps) & (work["b"] >= work["a"]),
                    (work["a"] > eps) & (work["b"] > eps) & (work["b"] < work["a"]),
                    (work["a"] > eps) & (work["b"] <= eps),
                    work["a"].abs() <= eps,
                ],
                ["CONTINUE", "PARTIAL_REVERSE", "FULL_REVERSE_OR_OVERSHOOT", "FLAT"],
                default="OTHER",
            )
            for (split, cls), group in work.groupby(["split", "class"]):
                rows.append(
                    {
                        "initial": initial,
                        "later": later,
                        "split": split,
                        "class": cls,
                        "fills": int(len(group)),
                        "share": float(len(group) / max(1, len(work[work["split"] == split]))),
                        "mean_size_depth": float(pd.to_numeric(group["episode_size_over_touch"], errors="coerce").mean()),
                        "failed_replenish_rate": float(group["failed_replenish_80_30s"].mean()),
                    }
                )
    return pd.DataFrame(rows)


def resilience_table(fills: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for split, group in fills.groupby("split"):
        for level in (25, 50, 80, 90, 100):
            column = f"replenish_{level}_ms"
            if column not in group.columns:
                continue
            value = pd.to_numeric(group[column], errors="coerce")
            rows.append(
                {
                    "split": split,
                    "recovery_level_pct": level,
                    "fills": int(len(group)),
                    "recovered_30s_share": float((value <= 30000).mean()),
                    "recovered_60s_share": float((value <= 60000).mean()),
                    "median_ms_uncensored": float(value.median()),
                    "p90_ms_uncensored": float(value.quantile(0.9)),
                }
            )
        for failed, part in group.groupby("failed_replenish_80_30s"):
            rows.append(
                {
                    "split": split,
                    "recovery_level_pct": 80,
                    "condition": f"failed_30s={bool(failed)}",
                    "fills": int(len(part)),
                    "mean_signed_move_30s": float(part["signed_move_30s"].mean()),
                    "mean_signed_move_60s": float(part["signed_move_60s"].mean()),
                    "mean_signed_move_300s": float(part["signed_move_300s"].mean()),
                    "mean_flow_persistence_30s": float(part["flow_persistence_30s"].mean()),
                }
            )
    return pd.DataFrame(rows)


def flow_table(fills: pd.DataFrame, episodes: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for split, group in fills.groupby("split"):
        rows.append(
            {
                "split": split,
                "metric": "next_30s_linked_fill_persistence",
                "fills": int(len(group)),
                "mean_same_minus_opp": float(group["flow_persistence_30s"].mean()),
                "positive_share": float((group["flow_persistence_30s"] > 0).mean()),
            }
        )
        for position, part in group.groupby("episode_position"):
            rows.append(
                {
                    "split": split,
                    "metric": "episode_position",
                    "bucket": str(position),
                    "fills": int(len(part)),
                    "mean_signed_move_30s": float(part["signed_move_30s"].mean()),
                    "mean_markout_30s": float(part["aggressor_markout_30s"].mean()),
                }
            )
    for split, group in episodes.groupby("split"):
        rows.append(
            {
                "split": split,
                "metric": "episode_summary",
                "episodes": int(len(group)),
                "multi_fill_share": float((group["fills"] > 1).mean()),
                "mean_fills": float(group["fills"].mean()),
                "mean_duration_s": float(group["duration_s"].mean()),
            }
        )
    return pd.DataFrame(rows)


def toxicity_table(fills: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for split, group in fills.groupby("split"):
        for horizon in (5, 15, 60, 300):
            mark = pd.to_numeric(group[f"aggressor_markout_{horizon}s"], errors="coerce")
            rows.append(
                {
                    "split": split,
                    "horizon_s": horizon,
                    "fills": int(mark.notna().sum()),
                    "mean_aggressor_markout": float(mark.mean()),
                    "adverse_gt_1pp_share": float((mark > 0.01).mean()),
                    "adverse_gt_2pp_share": float((mark > 0.02).mean()),
                }
            )
    return pd.DataFrame(rows)


def spread_table(fills: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for split, group in fills.groupby("split"):
        for horizon in CLOCK_HORIZONS:
            realized = pd.to_numeric(group[f"realized_spread_{horizon}s"], errors="coerce")
            impact = pd.to_numeric(group[f"signed_move_{horizon}s"], errors="coerce")
            effective = pd.to_numeric(group["effective_spread"], errors="coerce")
            residual = effective - realized - impact
            rows.append(
                {
                    "split": split,
                    "horizon_s": horizon,
                    "fills": int(residual.notna().sum()),
                    "mean_effective_spread": float(effective.mean()),
                    "mean_realized_spread": float(realized.mean()),
                    "mean_adverse_movement": float(impact.mean()),
                    "decomposition_residual_abs_mean": float(residual.abs().mean()),
                }
            )
    return pd.DataFrame(rows)


def permutation_test_relative_size(fills: pd.DataFrame, seed: int = 5006) -> dict[str, Any]:
    work = fills[
        fills["economic_exact"].astype(bool)
        & fills["log_episode_size_over_touch"].notna()
        & fills["signed_move_30s"].notna()
    ].copy()
    if len(work) < 100:
        return {"status": "INSUFFICIENT", "n": int(len(work))}
    observed = float(np.corrcoef(work["log_episode_size_over_touch"], work["signed_move_30s"])[0, 1])
    rng = np.random.default_rng(seed)
    null = []
    groups = list(work.groupby("token_id").groups.values())
    values = work["log_episode_size_over_touch"].to_numpy(float).copy()
    for _ in range(500):
        perm = values.copy()
        for idx in groups:
            pos = work.index.get_indexer(idx)
            perm[pos] = rng.permutation(perm[pos])
        null.append(float(np.corrcoef(perm, work["signed_move_30s"].to_numpy(float))[0, 1]))
    p = float((1 + np.sum(np.abs(null) >= abs(observed))) / (1 + len(null)))
    return {
        "status": "OK",
        "n": int(len(work)),
        "observed_correlation": observed,
        "null_mean": float(np.mean(null)),
        "empirical_two_sided_p": p,
    }


def cluster_bootstrap_mean(
    frame: pd.DataFrame,
    value: str,
    cluster: str = "token_id",
    seed: int = 5007,
) -> dict[str, Any]:
    work = frame[[cluster, value]].replace([np.inf, -np.inf], np.nan).dropna()
    if work.empty:
        return {"status": "INSUFFICIENT", "n": 0}
    grouped = {
        str(key): part[value].to_numpy(float)
        for key, part in work.groupby(cluster, sort=False)
    }
    if len(grouped) < 2:
        return {
            "status": "INSUFFICIENT",
            "n": int(len(work)),
            "clusters": int(len(grouped)),
        }
    rng = np.random.default_rng(seed)
    keys = list(grouped)
    draws: list[float] = []
    for _ in range(1000):
        sampled = rng.choice(keys, size=len(keys), replace=True)
        values = np.concatenate([grouped[str(key)] for key in sampled])
        draws.append(float(np.mean(values)))
    return {
        "status": "OK",
        "n": int(len(work)),
        "clusters": int(len(grouped)),
        "mean": float(work[value].mean()),
        "median": float(work[value].median()),
        "ci_2_5": float(np.quantile(draws, 0.025)),
        "ci_97_5": float(np.quantile(draws, 0.975)),
    }


def replenishment_gap(
    fills: pd.DataFrame,
    mask: pd.Series,
) -> dict[str, Any]:
    work = fills.loc[mask].copy()
    work = work[
        work["signed_move_30s"].notna()
        & work["failed_replenish_80_30s"].notna()
    ]
    failed = work[work["failed_replenish_80_30s"].astype(bool)]
    recovered = work[~work["failed_replenish_80_30s"].astype(bool)]
    if len(failed) < 20 or len(recovered) < 20:
        return {
            "status": "INSUFFICIENT",
            "failed_n": int(len(failed)),
            "recovered_n": int(len(recovered)),
        }
    return {
        "status": "OK",
        "failed_n": int(len(failed)),
        "recovered_n": int(len(recovered)),
        "failed_mean": float(failed["signed_move_30s"].mean()),
        "recovered_mean": float(recovered["signed_move_30s"].mean()),
        "gap": float(
            failed["signed_move_30s"].mean()
            - recovered["signed_move_30s"].mean()
        ),
    }


def replenishment_dev_falsifications(fills: pd.DataFrame) -> dict[str, Any]:
    train_p90 = float(
        fills.loc[fills["split"] == "TRAIN", "activity_60"].quantile(0.90)
    )
    dev = fills[fills["split"] == "DEV"].copy()
    dev = dev.sort_values("anchor_ts_ns")
    midpoint = len(dev) // 2
    return {
        "FULL": replenishment_gap(dev, pd.Series(True, index=dev.index)),
        "FIRST_HALF": replenishment_gap(
            dev,
            pd.Series(dev.index.isin(dev.iloc[:midpoint].index), index=dev.index),
        ),
        "SECOND_HALF": replenishment_gap(
            dev,
            pd.Series(dev.index.isin(dev.iloc[midpoint:].index), index=dev.index),
        ),
        "STALE_EXCLUDED": replenishment_gap(
            dev,
            pd.to_numeric(dev["book_age_ms"], errors="coerce") <= 5000,
        ),
        "HIGH_ACTIVITY_EXCLUDED": replenishment_gap(
            dev,
            pd.to_numeric(dev["activity_60"], errors="coerce") <= train_p90,
        ),
    }


def filtered_models(fills: pd.DataFrame, mask: pd.Series) -> dict[str, Any]:
    work = fills.loc[mask].copy()
    train = work[work["split"] == "TRAIN"]
    dev = work[work["split"] == "DEV"]
    base = ["mid", "spread", "log_depth_2c", "activity_60", "signed_pre_move_30"]
    exact_train = train[train["economic_exact"].astype(bool)]
    exact_dev = dev[dev["economic_exact"].astype(bool)]
    return {
        "abs_size": fit_ols(exact_train, exact_dev, base + ["log_episode_size"], "signed_move_30s"),
        "rel_size": fit_ols(exact_train, exact_dev, base + ["log_episode_size_over_touch"], "signed_move_30s"),
        "baseline": fit_ols(train, dev, base, "signed_move_30s"),
        "ofi": fit_ols(train, dev, base + ["signed_ofi_60"], "signed_move_30s"),
        "microprice": fit_ols(train, dev, base + ["signed_microprice_deviation"], "signed_move_30s"),
    }


def main() -> None:
    fills = pd.read_parquet(locate("FILL_EVENTS.parquet"))
    episodes = pd.read_parquet(locate("FILL_EPISODES.parquet"))
    controls = pd.read_parquet(locate("NONFILL_CONTROLS.parquet"))
    pairs = pd.read_csv(locate("MATCHED_CONTROLS.csv"))
    require_columns(
        fills,
        [
            "split", "window_id", "group_id", "token_id", "anchor_ts_ns", "q",
            "mid", "spread", "log_depth_2c", "activity_60", "mid_vol_60", "vpin_60",
            "imbalance_touch", "microprice_deviation", "ofi_60", "signed_flow_60",
            "pre_move_30", "book_age_ms", "economic_exact", "episode_position",
            "failed_replenish_80_30s",
        ]
        + [f"signed_move_{h}s" for h in CLOCK_HORIZONS]
        + [f"signed_move_e{h}" for h in EVENT_HORIZONS],
    )

    case_control = matched_case_control(fills, controls, pairs)
    arrival_train = case_control[case_control["split"] == "TRAIN"]
    arrival_dev = case_control[case_control["split"] == "DEV"]
    arrival_result, arrival_idx, arrival_pred = logistic_fit(
        arrival_train, arrival_dev, ARRIVAL_FEATURES, "arrival"
    )
    arrival_dev_scored = arrival_dev.loc[arrival_idx].copy() if len(arrival_idx) else pd.DataFrame()
    if not arrival_dev_scored.empty:
        arrival_dev_scored["_prediction"] = arrival_pred
        arrival_result["token_leaveout"] = subgroup_auc(
            arrival_dev_scored,
            arrival_dev_scored["_prediction"],
            "arrival",
            "token_id",
            min_rows=20,
        )
        arrival_result["time_halves"] = time_half_auc(
            arrival_dev_scored,
            arrival_dev_scored["_prediction"],
            "arrival",
        )

    direction = fills.copy()
    direction["buy"] = (direction["q"].astype(int) > 0).astype(int)
    direction_train = direction[direction["split"] == "TRAIN"]
    direction_dev = direction[direction["split"] == "DEV"]
    direction_result, direction_idx, direction_pred = logistic_fit(
        direction_train, direction_dev, DIRECTION_FEATURES, "buy"
    )
    direction_dev_scored = direction_dev.loc[direction_idx].copy() if len(direction_idx) else pd.DataFrame()
    if not direction_dev_scored.empty:
        direction_dev_scored["_prediction"] = direction_pred
        direction_result["time_halves"] = time_half_auc(
            direction_dev_scored,
            direction_dev_scored["_prediction"],
            "buy",
        )

    arrival_rows = pd.DataFrame(
        [{"model": "ARRIVAL_STATE", **arrival_result}]
    )
    direction_rows = pd.DataFrame(
        [{"model": "DIRECTION_STATE", **direction_result}]
    )
    arrival_rows.to_parquet(WORK / "FILL_ARRIVAL_RESULTS.parquet", index=False)
    direction_rows.to_parquet(WORK / "FILL_DIRECTION_RESULTS.parquet", index=False)

    impacts = impact_curves(fills)
    reversals = reversal_table(fills)
    resilience = resilience_table(fills)
    flow = flow_table(fills, episodes)
    toxicity = toxicity_table(fills)
    spreads = spread_table(fills)
    impacts.to_parquet(WORK / "IMPACT_CURVES.parquet", index=False)
    reversals.to_parquet(WORK / "REVERSAL_CONTINUATION.parquet", index=False)
    resilience.to_parquet(WORK / "RESILIENCE_RESULTS.parquet", index=False)
    flow.to_parquet(WORK / "FLOW_PERSISTENCE.parquet", index=False)
    toxicity.to_parquet(WORK / "TOXICITY_RESULTS.parquet", index=False)
    spreads.to_parquet(WORK / "SPREAD_DECOMPOSITION.parquet", index=False)

    train_activity_p90 = float(
        fills.loc[fills["split"] == "TRAIN", "activity_60"].quantile(0.90)
    )
    stale_mask = pd.to_numeric(fills["book_age_ms"], errors="coerce") <= 5000
    activity_mask = pd.to_numeric(fills["activity_60"], errors="coerce") <= train_activity_p90
    full_models = filtered_models(fills, pd.Series(True, index=fills.index))
    stale_models = filtered_models(fills, stale_mask.fillna(False))
    quiet_models = filtered_models(fills, activity_mask.fillna(False))
    permutation = permutation_test_relative_size(fills)

    falsification_rows = []
    for name, payload in [
        ("relative_size_randomized_alignment", permutation),
        ("stale_book_exclusion", stale_models),
        ("top_decile_activity_exclusion", quiet_models),
        ("full_sample_models", full_models),
    ]:
        falsification_rows.append(
            {"test": name, "payload_json": json.dumps(payload, sort_keys=True, default=str)}
        )
    if "delta_signed_move_30s" in pairs.columns:
        for split, group in pairs.groupby("split"):
            vals = pd.to_numeric(group["delta_signed_move_30s"], errors="coerce").dropna()
            falsification_rows.append(
                {
                    "test": "matched_nonfill_30s",
                    "split": split,
                    "n": int(len(vals)),
                    "mean": float(vals.mean()) if len(vals) else math.nan,
                    "median": float(vals.median()) if len(vals) else math.nan,
                }
            )
    for split, group in fills.groupby("split"):
        signed = pd.to_numeric(group["signed_move_30s"], errors="coerce").dropna()
        falsification_rows.append(
            {
                "test": "side_sign_flip_placebo",
                "split": split,
                "n": int(len(signed)),
                "original_mean": float(signed.mean()),
                "flipped_mean": float((-signed).mean()),
            }
        )
    falsifications = pd.DataFrame(falsification_rows)
    falsifications.to_parquet(WORK / "FALSIFICATION_RESULTS.parquet", index=False)

    tradfi_rows = []
    for stratum, models in [
        ("FULL", full_models),
        ("STALE_EXCLUDED", stale_models),
        ("HIGH_ACTIVITY_EXCLUDED", quiet_models),
    ]:
        for model, payload in models.items():
            tradfi_rows.append(
                {
                    "stratum": stratum,
                    "model": model,
                    "payload_json": json.dumps(payload, sort_keys=True, default=str),
                }
            )
    tradfi = pd.DataFrame(tradfi_rows)
    tradfi.to_parquet(WORK / "TRADFI_CHALLENGERS.parquet", index=False)

    dev = fills[fills["split"] == "DEV"]
    replenishment_checks = replenishment_dev_falsifications(fills)
    matched_control_dev = (
        cluster_bootstrap_mean(
            pairs[pairs["split"] == "DEV"],
            "delta_signed_move_30s",
            cluster="token_id",
        )
        if "delta_signed_move_30s" in pairs.columns
        else {"status": "INSUFFICIENT", "n": 0}
    )

    candidates: list[dict[str, Any]] = []
    rel = full_models["rel_size"]
    abs_model = full_models["abs_size"]
    rel_stale = stale_models["rel_size"]
    rel_quiet = quiet_models["rel_size"]
    if (
        rel.get("status") == "OK"
        and abs_model.get("status") == "OK"
        and float(rel.get("dev_r2", -999)) > float(abs_model.get("dev_r2", -999))
        and float(rel.get("dev_r2", -999)) > 0
        and permutation.get("status") == "OK"
        and float(permutation.get("empirical_two_sided_p", 1)) <= 0.05
        and rel_stale.get("status") == "OK"
        and rel_quiet.get("status") == "OK"
        and float(rel_stale.get("dev_r2", -999)) > 0
        and float(rel_quiet.get("dev_r2", -999)) > 0
    ):
        candidates.append(
            {
                "candidate_id": "005H-C01-RELATIVE-SIZE",
                "mechanism": "episode size normalized by visible touch liquidity",
                "classification": ["SEND_TO_MM_REPLAY", "SEND_TO_005I", "FUTURE_CONFIRMATION_REQUIRED"],
            }
        )
    repl_required = [
        replenishment_checks.get(name, {})
        for name in [
            "FULL",
            "FIRST_HALF",
            "SECOND_HALF",
            "STALE_EXCLUDED",
            "HIGH_ACTIVITY_EXCLUDED",
        ]
    ]
    if (
        all(check.get("status") == "OK" for check in repl_required)
        and all(float(check.get("gap", -999)) > 0 for check in repl_required)
    ):
        candidates.append(
            {
                "candidate_id": "005H-C02-FAILED-REPLENISHMENT",
                "mechanism": "failed 80% consumed-side 2c recovery within 30s",
                "dev_falsifications": replenishment_checks,
                "classification": [
                    "SEND_TO_MM_REPLAY",
                    "SEND_TO_LIVE_DIAG",
                    "SEND_TO_SHADOW",
                    "FUTURE_CONFIRMATION_REQUIRED",
                ],
            }
        )
    if (
        matched_control_dev.get("status") == "OK"
        and int(matched_control_dev.get("n", 0)) >= 50
        and float(matched_control_dev.get("ci_2_5", -999)) > 0
    ):
        candidates.append(
            {
                "candidate_id": "005H-C03-FILL-BEYOND-STATE",
                "mechanism": (
                    "linked fill episodes move further in aggressor direction "
                    "than same-token matched non-fill states"
                ),
                "matched_control_dev": matched_control_dev,
                "classification": [
                    "SEND_TO_005I",
                    "SEND_TO_LIVE_DIAG",
                    "FUTURE_CONFIRMATION_REQUIRED",
                ],
            }
        )
    if arrival_result.get("status") == "OK":
        token_auc = arrival_result.get("token_leaveout", {})
        halves = arrival_result.get("time_halves", {})
        if (
            float(arrival_result.get("dev_auc", 0)) >= 0.60
            and float(token_auc.get("median_auc", 0)) >= 0.55
            and min(float(halves.get("FIRST", 0)), float(halves.get("SECOND", 0))) >= 0.55
        ):
            candidates.append(
                {
                    "candidate_id": "005H-C04-ARRIVAL-STATE",
                    "mechanism": "book state distinguishes fill moments from matched non-fill states",
                    "classification": ["SEND_TO_LIVE_DIAG", "SEND_TO_005I", "FUTURE_CONFIRMATION_REQUIRED"],
                }
            )
    if direction_result.get("status") == "OK":
        halves = direction_result.get("time_halves", {})
        dev_buy = int(direction_result.get("dev_positive", 0))
        dev_n = int(direction_result.get("dev_n", 0))
        if (
            float(direction_result.get("dev_auc", 0)) >= 0.58
            and dev_buy >= 100
            and dev_n - dev_buy >= 100
            and min(float(halves.get("FIRST", 0)), float(halves.get("SECOND", 0))) >= 0.52
        ):
            candidates.append(
                {
                    "candidate_id": "005H-C05-DIRECTION-STATE",
                    "mechanism": "pre-fill book state predicts aggressor direction conditional on a fill",
                    "classification": ["SEND_TO_005I", "SEND_TO_LIVE_DIAG", "FUTURE_CONFIRMATION_REQUIRED"],
                }
            )
    baseline = full_models["baseline"]
    for challenger_name in ["ofi", "microprice"]:
        challenger = full_models[challenger_name]
        stale_ch = stale_models[challenger_name]
        quiet_ch = quiet_models[challenger_name]
        if (
            challenger.get("status") == "OK"
            and baseline.get("status") == "OK"
            and float(challenger.get("dev_r2", -999)) - float(baseline.get("dev_r2", -999)) >= 0.005
            and stale_ch.get("status") == "OK"
            and quiet_ch.get("status") == "OK"
            and float(stale_ch.get("dev_r2", -999)) > float(stale_models["baseline"].get("dev_r2", -999))
            and float(quiet_ch.get("dev_r2", -999)) > float(quiet_models["baseline"].get("dev_r2", -999))
        ):
            candidates.append(
                {
                    "candidate_id": "005H-C06-BOOK-MICROSTRUCTURE-CHALLENGER",
                    "mechanism": challenger_name,
                    "classification": ["SEND_TO_005I", "FUTURE_CONFIRMATION_REQUIRED"],
                }
            )
            break

    discovery_rows = [
        {
            "candidate_id": candidate["candidate_id"],
            "mechanism": candidate["mechanism"],
            "classification_json": json.dumps(candidate["classification"]),
        }
        for candidate in candidates
    ]
    discovery = pd.DataFrame(
        discovery_rows,
        columns=["candidate_id", "mechanism", "classification_json"],
    )
    discovery.to_parquet(WORK / "DISCOVERY_RESULTS.parquet", index=False)

    freeze = {
        "schema_version": 1,
        "experiment": "EXPERIMENT-005H",
        "stage": "PRE_HOLDOUT_FREEZE",
        "train_window": "W17",
        "dev_window": "W18",
        "final_holdout": "B0",
        "b0_opened": False,
        "candidates": candidates,
        "arrival_result": arrival_result,
        "direction_result": direction_result,
        "matched_control_dev": matched_control_dev,
        "replenishment_dev_falsifications": replenishment_checks,
        "full_models": full_models,
        "stale_excluded_models": stale_models,
        "high_activity_excluded_models": quiet_models,
        "relative_size_permutation": permutation,
        "rule": "Only candidates listed here may receive one-shot B0 evaluation. No post-B0 retuning, candidate addition, threshold change, feature change, or rescue.",
        "real_sig_orders_sent": False,
    }
    freeze["stage"] = "EXTENDED_V3_SHORTLIST"
    freeze["rule"] = (
        "This is a V3 shortlist only. It does not authorize B0. "
        "Experiment-wide PRE_HOLDOUT_FREEZE.json may be written only after "
        "hostile V3 falsification and source-version robustness are complete."
    )
    (WORK / "EXTENDED_V3_SHORTLIST.json").write_text(
        json.dumps(freeze, indent=2, sort_keys=True, default=str) + "\n"
    )
    summary = {
        "experiment": "EXPERIMENT-005H",
        "stage": "EXTENDED_V3_DISCOVERY_FALSIFICATION",
        "fill_events": int(len(fills)),
        "episodes": int(len(episodes)),
        "matched_pairs": int(len(pairs)),
        "arrival": arrival_result,
        "direction": direction_result,
        "candidates": candidates,
        "b0_opened": False,
        "real_sig_orders_sent": False,
    }
    (WORK / "EXTENDED_DISCOVERY_SUMMARY.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True, default=str) + "\n"
    )
    (WORK / "EXTENDED_DISCOVERY_REPORT.md").write_text(
        "# EXPERIMENT-005H — Extended V3 Discovery / Falsification\n\n"
        f"- Fill events: **{len(fills):,}**\n"
        f"- 10-second episodes: **{len(episodes):,}**\n"
        f"- Matched fill/non-fill pairs: **{len(pairs):,}**\n"
        f"- Provisional V3 shortlist: **{len(candidates)}**\n"
        "- B0 opened: **NO**\n\n"
        "This is not the experiment-wide holdout freeze. B0 remains sealed.\n\n"
        "REAL SIG ORDERS SENT: NO\n"
    )
    print(json.dumps(summary, sort_keys=True, default=str), flush=True)


if __name__ == "__main__":
    main()
