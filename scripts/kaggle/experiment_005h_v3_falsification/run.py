# ruff: noqa
from __future__ import annotations

import bisect
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path("/kaggle/input")
WORK = Path("/kaggle/working")
BOOTSTRAPS = 500
SEED = 50053


def locate(name: str) -> Path:
    matches = sorted(ROOT.rglob(name))
    if len(matches) != 1:
        raise RuntimeError(f"expected one {name}, found {matches}")
    return matches[0]


def load_inputs() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    fills = pd.read_parquet(locate("FILL_EVENTS.parquet"))
    controls = pd.read_parquet(locate("NONFILL_CONTROLS.parquet"))
    pairs = pd.read_csv(locate("MATCHED_CONTROLS.csv"))
    freeze = json.loads(locate("V3_CORE_SHORTLIST_FREEZE.json").read_text())
    if freeze.get("b0_opened") is not False:
        raise RuntimeError("V3 core source does not prove B0 remained sealed")
    if set(fills["split"].astype(str)) - {"TRAIN", "DEV"}:
        raise RuntimeError("unexpected split in V3 fill source")
    if set(fills["window_id"].astype(str)) - {"W17", "W18"}:
        raise RuntimeError("unexpected window in V3 fill source")
    return fills, controls, pairs, freeze


def clean(frame: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
    return frame[cols].replace([np.inf, -np.inf], np.nan).dropna()


def fit_fixed(
    train: pd.DataFrame,
    features: list[str],
    target: str,
) -> dict[str, Any]:
    tr = clean(train, features + [target])
    if len(tr) < max(50, len(features) * 10):
        return {"status": "INSUFFICIENT", "n": int(len(tr))}
    x = tr[features].to_numpy(float)
    y = tr[target].to_numpy(float)
    mean = x.mean(axis=0)
    std = x.std(axis=0)
    std[std == 0] = 1.0
    z = (x - mean) / std
    X = np.column_stack([np.ones(len(z)), z])
    beta = np.linalg.lstsq(X, y, rcond=None)[0]
    return {
        "status": "OK",
        "features": features,
        "mean": mean,
        "std": std,
        "beta": beta,
        "n": int(len(tr)),
    }


def predict_fixed(model: dict[str, Any], frame: pd.DataFrame) -> pd.DataFrame:
    features = list(model["features"])
    work = frame.copy()
    valid = (
        work[features]
        .replace([np.inf, -np.inf], np.nan)
        .notna()
        .all(axis=1)
    )
    out = work.loc[valid].copy()
    x = out[features].to_numpy(float)
    z = (x - model["mean"]) / model["std"]
    X = np.column_stack([np.ones(len(z)), z])
    out["prediction"] = X @ model["beta"]
    return out


def r2(y: np.ndarray, pred: np.ndarray) -> float:
    if len(y) < 2:
        return math.nan
    denom = float(np.sum((y - np.mean(y)) ** 2))
    if denom <= 0:
        return math.nan
    return 1.0 - float(np.sum((y - pred) ** 2)) / denom


def model_compare(
    train: pd.DataFrame,
    dev: pd.DataFrame,
    base: list[str],
    abs_feature: str,
    rel_feature: str,
    target: str,
) -> dict[str, Any]:
    abs_model = fit_fixed(train, base + [abs_feature], target)
    rel_model = fit_fixed(train, base + [rel_feature], target)
    if abs_model.get("status") != "OK" or rel_model.get("status") != "OK":
        return {"status": "INSUFFICIENT"}
    abs_pred = predict_fixed(abs_model, dev)
    rel_pred = predict_fixed(rel_model, dev)
    common = abs_pred[["group_id", target, "token_id", "anchor_ts_ns", "book_age_ms", "prediction"]].rename(
        columns={"prediction": "abs_pred"}
    ).merge(
        rel_pred[["group_id", "prediction"]].rename(columns={"prediction": "rel_pred"}),
        on="group_id",
        how="inner",
    )
    if len(common) < 30:
        return {"status": "INSUFFICIENT", "dev_n": int(len(common))}
    y = common[target].to_numpy(float)
    abs_r2 = r2(y, common["abs_pred"].to_numpy(float))
    rel_r2 = r2(y, common["rel_pred"].to_numpy(float))
    common["abs_sqerr"] = (common[target] - common["abs_pred"]) ** 2
    common["rel_sqerr"] = (common[target] - common["rel_pred"]) ** 2
    token_mse = common.groupby("token_id")[["abs_sqerr", "rel_sqerr"]].mean()
    token_win = float((token_mse["rel_sqerr"] < token_mse["abs_sqerr"]).mean())
    return {
        "status": "OK",
        "abs_model": abs_model,
        "rel_model": rel_model,
        "common": common,
        "abs_r2": abs_r2,
        "rel_r2": rel_r2,
        "r2_diff": rel_r2 - abs_r2,
        "token_mse_win_fraction": token_win,
    }


def bootstrap_r2_diff(common: pd.DataFrame, seed: int = SEED) -> dict[str, Any]:
    if common.empty:
        return {"n": 0}
    by_token = {key: g for key, g in common.groupby("token_id")}
    keys = list(by_token)
    if len(keys) < 2:
        return {"n": int(len(common)), "clusters": len(keys)}
    rng = np.random.default_rng(seed)
    diffs: list[float] = []
    for _ in range(BOOTSTRAPS):
        sampled = rng.choice(keys, size=len(keys), replace=True)
        sample = pd.concat([by_token[k] for k in sampled], ignore_index=True)
        y = sample["signed_move_30s"].to_numpy(float)
        a = r2(y, sample["abs_pred"].to_numpy(float))
        b = r2(y, sample["rel_pred"].to_numpy(float))
        if math.isfinite(a) and math.isfinite(b):
            diffs.append(b - a)
    if not diffs:
        return {"n": int(len(common)), "clusters": len(keys)}
    return {
        "n": int(len(common)),
        "clusters": int(len(keys)),
        "point": float(
            r2(common["signed_move_30s"].to_numpy(float), common["rel_pred"].to_numpy(float))
            - r2(common["signed_move_30s"].to_numpy(float), common["abs_pred"].to_numpy(float))
        ),
        "ci_2_5": float(np.quantile(diffs, 0.025)),
        "ci_97_5": float(np.quantile(diffs, 0.975)),
        "positive_fraction": float(np.mean(np.asarray(diffs) > 0)),
    }


def mean_gap(
    frame: pd.DataFrame,
    group_col: str,
    value_col: str,
) -> dict[str, Any]:
    work = clean(frame, [group_col, value_col, "token_id"])
    yes = work[work[group_col].astype(bool)][value_col]
    no = work[~work[group_col].astype(bool)][value_col]
    if yes.empty or no.empty:
        return {"n": int(len(work)), "status": "INSUFFICIENT"}
    return {
        "n": int(len(work)),
        "true_n": int(len(yes)),
        "false_n": int(len(no)),
        "true_mean": float(yes.mean()),
        "false_mean": float(no.mean()),
        "gap": float(yes.mean() - no.mean()),
        "status": "OK",
    }


def bootstrap_gap(
    frame: pd.DataFrame,
    group_col: str,
    value_col: str,
    seed: int = SEED + 1,
) -> dict[str, Any]:
    work = clean(frame, [group_col, value_col, "token_id"])
    by_token = {key: g for key, g in work.groupby("token_id")}
    keys = list(by_token)
    if len(keys) < 2:
        return {"n": int(len(work)), "clusters": int(len(keys))}
    rng = np.random.default_rng(seed)
    gaps: list[float] = []
    for _ in range(BOOTSTRAPS):
        sampled = rng.choice(keys, size=len(keys), replace=True)
        sample = pd.concat([by_token[k] for k in sampled], ignore_index=True)
        yes = sample[sample[group_col].astype(bool)][value_col]
        no = sample[~sample[group_col].astype(bool)][value_col]
        if not yes.empty and not no.empty:
            gaps.append(float(yes.mean() - no.mean()))
    point = mean_gap(work, group_col, value_col)
    if not gaps:
        return {**point, "clusters": int(len(keys))}
    return {
        **point,
        "clusters": int(len(keys)),
        "ci_2_5": float(np.quantile(gaps, 0.025)),
        "ci_97_5": float(np.quantile(gaps, 0.975)),
        "positive_fraction": float(np.mean(np.asarray(gaps) > 0)),
    }


def bootstrap_mean(
    frame: pd.DataFrame,
    value_col: str,
    seed: int = SEED + 2,
) -> dict[str, Any]:
    work = clean(frame, [value_col, "token_id"])
    by_token = {key: g[value_col].to_numpy(float) for key, g in work.groupby("token_id")}
    keys = list(by_token)
    if not keys:
        return {"n": 0}
    rng = np.random.default_rng(seed)
    vals: list[float] = []
    for _ in range(BOOTSTRAPS):
        sampled = rng.choice(keys, size=len(keys), replace=True)
        arr = np.concatenate([by_token[k] for k in sampled])
        vals.append(float(arr.mean()))
    return {
        "n": int(len(work)),
        "clusters": int(len(keys)),
        "mean": float(work[value_col].mean()),
        "ci_2_5": float(np.quantile(vals, 0.025)),
        "ci_97_5": float(np.quantile(vals, 0.975)),
        "positive_fraction": float(np.mean(np.asarray(vals) > 0)),
    }


def split_halves(frame: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    ordered = frame.sort_values("anchor_ts_ns")
    cut = len(ordered) // 2
    return ordered.iloc[:cut].copy(), ordered.iloc[cut:].copy()


def nearest_fill_distance(ts: int, times: list[int]) -> int:
    if not times:
        return 10**30
    pos = bisect.bisect_left(times, ts)
    vals = []
    if pos < len(times):
        vals.append(abs(times[pos] - ts))
    if pos > 0:
        vals.append(abs(times[pos - 1] - ts))
    return min(vals) if vals else 10**30


def strict_rematch(
    fills: pd.DataFrame,
    controls: pd.DataFrame,
    split: str,
) -> pd.DataFrame:
    f = fills[fills["split"] == split].copy().sort_values("anchor_ts_ns")
    c = controls[controls["split"] == split].copy()
    fill_times = {
        str(token): sorted(g["anchor_ts_ns"].astype("int64").tolist())
        for token, g in f.groupby("token_id")
    }
    c = c[
        [
            nearest_fill_distance(
                int(row.anchor_ts_ns),
                fill_times.get(str(row.token_id), []),
            ) > 120_000_000_000
            for row in c.itertuples(index=False)
        ]
    ].copy()
    grouped = {str(token): g.copy() for token, g in c.groupby("token_id")}
    used: set[int] = set()
    rows: list[dict[str, Any]] = []
    for row in f.itertuples(index=False):
        cand = grouped.get(str(row.token_id))
        if cand is None or cand.empty:
            continue
        cand = cand[~cand.index.isin(used)].copy()
        required = [
            "mid","spread","log_depth_2c","activity_60",
            "mid_vol_60","pre_move_30","mid_30s",
        ]
        cand = cand.replace([np.inf, -np.inf], np.nan).dropna(subset=required)
        if cand.empty or any(pd.isna(getattr(row, col)) for col in required[:-1]):
            continue
        dt_hours = (cand["anchor_ts_ns"].astype("int64") - int(row.anchor_ts_ns)).abs() / 3.6e12
        score = (
            ((cand["mid"] - float(row.mid)) / 0.05) ** 2
            + ((cand["spread"] - float(row.spread)) / 0.02) ** 2
            + ((cand["log_depth_2c"] - float(row.log_depth_2c)) / 2.0) ** 2
            + ((cand["activity_60"] - float(row.activity_60)) / 25.0) ** 2
            + ((cand["mid_vol_60"] - float(row.mid_vol_60)) / 0.01) ** 2
            + ((cand["pre_move_30"] - float(row.pre_move_30)) / 0.05) ** 2
            + (dt_hours / 6.0) ** 2
        )
        idx = int(score.idxmin())
        control = cand.loc[idx]
        used.add(idx)
        q = int(row.q)
        fill_raw = float(row.mid_30s) - float(row.mid)
        control_raw = float(control["mid_30s"]) - float(control["mid"])
        rows.append(
            {
                "group_id": row.group_id,
                "token_id": row.token_id,
                "fill_ts_ns": int(row.anchor_ts_ns),
                "control_ts_ns": int(control["anchor_ts_ns"]),
                "q": q,
                "match_score": float(score.loc[idx]),
                "fill_raw_move_30s": fill_raw,
                "control_raw_move_30s": control_raw,
                "fill_signed_move_30s": q * fill_raw,
                "control_signed_move_30s": q * control_raw,
                "delta_signed_move_30s": q * (fill_raw - control_raw),
            }
        )
    return pd.DataFrame(rows)


def permutation_placebo(matches: pd.DataFrame, seed: int = SEED + 3) -> dict[str, Any]:
    if matches.empty:
        return {"n": 0}
    observed = float(matches["delta_signed_move_30s"].mean())
    by_token = {key: g.copy() for key, g in matches.groupby("token_id")}
    rng = np.random.default_rng(seed)
    null: list[float] = []
    for _ in range(BOOTSTRAPS):
        pieces = []
        for g in by_token.values():
            control_raw = g["control_raw_move_30s"].to_numpy(float).copy()
            rng.shuffle(control_raw)
            q = g["q"].to_numpy(float)
            fill_raw = g["fill_raw_move_30s"].to_numpy(float)
            pieces.append(q * (fill_raw - control_raw))
        if pieces:
            null.append(float(np.concatenate(pieces).mean()))
    if not null:
        return {"n": int(len(matches)), "observed": observed}
    arr = np.asarray(null)
    return {
        "n": int(len(matches)),
        "observed": observed,
        "null_mean": float(arr.mean()),
        "null_2_5": float(np.quantile(arr, 0.025)),
        "null_97_5": float(np.quantile(arr, 0.975)),
        "one_sided_p": float((1 + np.sum(arr >= observed)) / (1 + len(arr))),
    }


def c01_tests(fills: pd.DataFrame) -> dict[str, Any]:
    base = ["mid","spread","log_depth_2c","activity_60","signed_pre_move_30"]
    train = fills[(fills["split"] == "TRAIN") & fills["economic_exact"].astype(bool)].copy()
    dev = fills[(fills["split"] == "DEV") & fills["economic_exact"].astype(bool)].copy()
    result = model_compare(
        train, dev, base, "log_episode_size", "log_episode_size_over_touch", "signed_move_30s"
    )
    if result.get("status") != "OK":
        return result
    common = result.pop("common")
    abs_model = result.pop("abs_model")
    rel_model = result.pop("rel_model")
    boot = bootstrap_r2_diff(common)
    halves = {}
    for label, subset in zip(("DEV_H1","DEV_H2"), split_halves(dev), strict=True):
        cmp = model_compare(
            train, subset, base, "log_episode_size", "log_episode_size_over_touch", "signed_move_30s"
        )
        halves[label] = {
            k: v for k, v in cmp.items()
            if k not in {"common","abs_model","rel_model"}
        }
    stale = {}
    for limit in (1000.0, 5000.0):
        subset = dev[pd.to_numeric(dev["book_age_ms"], errors="coerce") <= limit]
        cmp = model_compare(
            train, subset, base, "log_episode_size", "log_episode_size_over_touch", "signed_move_30s"
        )
        stale[f"book_age_le_{int(limit)}ms"] = {
            k: v for k, v in cmp.items()
            if k not in {"common","abs_model","rel_model"}
        }
    half_ok = all(
        x.get("status") == "OK" and float(x.get("r2_diff", -999)) > 0
        for x in halves.values()
    )
    stale5 = stale["book_age_le_5000ms"]
    robust = (
        float(result["r2_diff"]) > 0
        and float(boot.get("ci_2_5", -999)) > 0
        and half_ok
        and float(result["token_mse_win_fraction"]) >= 0.55
        and (
            stale5.get("status") != "OK"
            or float(stale5.get("r2_diff", -999)) > 0
        )
    )
    label = (
        "ROBUST_V3_SURVIVOR"
        if robust
        else "PROVISIONAL_V3_ONLY"
        if float(result["r2_diff"]) > 0
        else "REJECTED_V3"
    )
    return {
        **result,
        "bootstrap": boot,
        "halves": halves,
        "stale_exclusions": stale,
        "label": label,
    }


def c02_tests(fills: pd.DataFrame) -> dict[str, Any]:
    train = fills[fills["split"] == "TRAIN"].copy()
    dev = fills[fills["split"] == "DEV"].copy()
    train_gap = mean_gap(train, "failed_replenish_80_30s", "signed_move_30s")
    dev_gap = mean_gap(dev, "failed_replenish_80_30s", "signed_move_30s")
    boot = bootstrap_gap(dev, "failed_replenish_80_30s", "signed_move_30s")
    halves = {
        label: mean_gap(subset, "failed_replenish_80_30s", "signed_move_30s")
        for label, subset in zip(("DEV_H1","DEV_H2"), split_halves(dev), strict=True)
    }
    stale = {}
    for limit in (1000.0, 5000.0):
        subset = dev[pd.to_numeric(dev["book_age_ms"], errors="coerce") <= limit]
        stale[f"book_age_le_{int(limit)}ms"] = mean_gap(
            subset, "failed_replenish_80_30s", "signed_move_30s"
        )
    exact = mean_gap(
        dev[dev["economic_exact"].astype(bool)],
        "failed_replenish_80_30s",
        "signed_move_30s",
    )
    robust = (
        train_gap.get("status") == "OK"
        and dev_gap.get("status") == "OK"
        and float(train_gap.get("gap", -999)) > 0
        and float(dev_gap.get("gap", -999)) > 0
        and float(boot.get("ci_2_5", -999)) > 0
        and all(
            x.get("status") == "OK" and float(x.get("gap", -999)) > 0
            for x in halves.values()
        )
        and (
            stale["book_age_le_5000ms"].get("status") != "OK"
            or float(stale["book_age_le_5000ms"].get("gap", -999)) > 0
        )
    )
    label = (
        "ROBUST_V3_SURVIVOR"
        if robust
        else "PROVISIONAL_V3_ONLY"
        if float(dev_gap.get("gap", -999)) > 0
        else "REJECTED_V3"
    )
    return {
        "train": train_gap,
        "dev": dev_gap,
        "dev_bootstrap": boot,
        "halves": halves,
        "stale_exclusions": stale,
        "exact_economics_dev": exact,
        "label": label,
    }


def c03_tests(
    fills: pd.DataFrame,
    controls: pd.DataFrame,
    pairs: pd.DataFrame,
) -> tuple[dict[str, Any], pd.DataFrame]:
    dev_pairs = pairs[pairs["split"] == "DEV"].copy()
    canonical = bootstrap_mean(dev_pairs, "delta_signed_move_30s")
    halves = {}
    if not dev_pairs.empty:
        ordered = dev_pairs.sort_values("fill_ts_ns")
        cut = len(ordered) // 2
        halves["DEV_H1"] = bootstrap_mean(ordered.iloc[:cut], "delta_signed_move_30s")
        halves["DEV_H2"] = bootstrap_mean(ordered.iloc[cut:], "delta_signed_move_30s")
    if not dev_pairs.empty and "match_score" in dev_pairs:
        cutoff = float(dev_pairs["match_score"].quantile(0.75))
        good = dev_pairs[dev_pairs["match_score"] <= cutoff]
        score_sensitivity = bootstrap_mean(good, "delta_signed_move_30s")
    else:
        score_sensitivity = {"n": 0}
    strict = strict_rematch(fills, controls, "DEV")
    strict_boot = bootstrap_mean(strict, "delta_signed_move_30s")
    strict_halves = {}
    if not strict.empty:
        ordered = strict.sort_values("fill_ts_ns")
        cut = len(ordered) // 2
        strict_halves["DEV_H1"] = bootstrap_mean(
            ordered.iloc[:cut], "delta_signed_move_30s"
        )
        strict_halves["DEV_H2"] = bootstrap_mean(
            ordered.iloc[cut:], "delta_signed_move_30s"
        )
    placebo = permutation_placebo(strict)
    robust = (
        int(canonical.get("n", 0)) >= 50
        and float(canonical.get("ci_2_5", -999)) > 0
        and int(strict_boot.get("n", 0)) >= 50
        and float(strict_boot.get("ci_2_5", -999)) > 0
        and all(
            int(x.get("n", 0)) >= 20 and float(x.get("mean", -999)) > 0
            for x in strict_halves.values()
        )
        and float(score_sensitivity.get("mean", -999)) > 0
        and float(placebo.get("one_sided_p", 1.0)) < 0.05
    )
    label = (
        "ROBUST_V3_SURVIVOR"
        if robust
        else "PROVISIONAL_V3_ONLY"
        if float(canonical.get("mean", -999)) > 0
        else "REJECTED_V3"
    )
    return {
        "canonical": canonical,
        "canonical_halves": halves,
        "best_75pct_match_scores": score_sensitivity,
        "strict_unique_control": strict_boot,
        "strict_halves": strict_halves,
        "permutation_placebo": placebo,
        "label": label,
    }, strict


def write_rows(results: dict[str, dict[str, Any]]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for candidate, payload in results.items():
        rows.append(
            {
                "candidate_id": candidate,
                "robustness_label": payload.get("label"),
                "payload_json": json.dumps(payload, sort_keys=True, default=str),
            }
        )
    return pd.DataFrame(rows)


def main() -> None:
    fills, controls, pairs, core_freeze = load_inputs()
    c01 = c01_tests(fills)
    c02 = c02_tests(fills)
    c03, strict_pairs = c03_tests(fills, controls, pairs)
    results = {
        "005H-C01-RELATIVE-SIZE": c01,
        "005H-C02-FAILED-REPLENISHMENT": c02,
        "005H-C03-FILL-BEYOND-STATE": c03,
    }
    rows = write_rows(results)
    rows.to_csv(WORK / "FALSIFICATION_RESULTS.csv", index=False)
    rows.to_parquet(WORK / "FALSIFICATION_RESULTS.parquet", index=False)
    strict_pairs.to_csv(WORK / "STRICT_MATCHED_CONTROLS.csv", index=False)

    robust = [
        candidate
        for candidate, payload in results.items()
        if payload.get("label") == "ROBUST_V3_SURVIVOR"
    ]
    provisional = [
        candidate
        for candidate, payload in results.items()
        if payload.get("label") == "PROVISIONAL_V3_ONLY"
    ]
    rejected = [
        candidate
        for candidate, payload in results.items()
        if payload.get("label") == "REJECTED_V3"
    ]
    summary = {
        "schema_version": 1,
        "experiment": "EXPERIMENT-005H",
        "stage": "V3_HOSTILE_FALSIFICATION",
        "source_core_stage": core_freeze.get("stage"),
        "source_core_survivors": [
            x.get("candidate_id")
            for x in core_freeze.get("survivors", [])
        ],
        "robust_v3_survivors": robust,
        "provisional_v3_only": provisional,
        "rejected_v3": rejected,
        "candidate_results": results,
        "b0_opened": False,
        "experiment_wide_pre_holdout_freeze_written": False,
        "real_sig_orders_sent": False,
    }
    (WORK / "V3_FALSIFICATION_SUMMARY.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True, default=str) + "\n"
    )
    lines = [
        "# EXPERIMENT-005H — V3 Hostile Falsification",
        "",
        "Inputs are the frozen W17 TRAIN / W18 DEV V3 core. B0 is not mounted or read.",
        "",
        f"- Robust V3 survivors: {', '.join(robust) if robust else 'NONE'}",
        f"- Provisional V3-only: {', '.join(provisional) if provisional else 'NONE'}",
        f"- Rejected on V3: {', '.join(rejected) if rejected else 'NONE'}",
        "",
        "V3 survival alone does not authorize the experiment-wide holdout freeze.",
        "",
        "REAL SIG ORDERS SENT: NO",
    ]
    (WORK / "V3_FALSIFICATION_REPORT.md").write_text("\n".join(lines) + "\n")
    print(json.dumps(summary, sort_keys=True, default=str), flush=True)


if __name__ == "__main__":
    main()
