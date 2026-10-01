# ruff: noqa
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path("/kaggle/input")
WORK = Path("/kaggle/working")
BOOTSTRAPS = 1000
SEED = 50057


def locate(name: str) -> Path:
    matches = sorted(ROOT.rglob(name))
    if len(matches) != 1:
        raise RuntimeError(f"expected one {name}, found {matches}")
    return matches[0]


def load_json(name: str) -> dict[str, Any]:
    return json.loads(locate(name).read_text(encoding="utf-8"))


def finite_frame(frame: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    return (
        frame[columns]
        .replace([np.inf, -np.inf], np.nan)
        .dropna()
    )


def auc_score(y: np.ndarray, score: np.ndarray) -> float:
    y = np.asarray(y, dtype=int)
    score = np.asarray(score, dtype=float)
    pos = int(np.sum(y == 1))
    neg = int(np.sum(y == 0))
    if pos == 0 or neg == 0:
        return math.nan
    order = np.argsort(score, kind="stable")
    ranks = np.empty(len(score), dtype=float)
    i = 0
    while i < len(score):
        j = i + 1
        while j < len(score) and score[order[j]] == score[order[i]]:
            j += 1
        average_rank = (i + 1 + j) / 2.0
        ranks[order[i:j]] = average_rank
        i = j
    return float(
        (
            ranks[y == 1].sum()
            - pos * (pos + 1) / 2.0
        )
        / (pos * neg)
    )


def r2_score(y: np.ndarray, prediction: np.ndarray) -> float:
    y = np.asarray(y, dtype=float)
    prediction = np.asarray(prediction, dtype=float)
    if len(y) < 2:
        return math.nan
    denom = float(np.sum((y - np.mean(y)) ** 2))
    if denom <= 0:
        return math.nan
    return 1.0 - float(np.sum((y - prediction) ** 2)) / denom


def apply_linear_model(
    frame: pd.DataFrame,
    spec: dict[str, Any],
    target: str,
) -> pd.DataFrame:
    if spec.get("status") != "OK":
        return pd.DataFrame()
    features = [str(value) for value in spec["features"]]
    required = features + [target, "token_id", "anchor_ts_ns"]
    work = frame[required].replace(
        [np.inf, -np.inf],
        np.nan,
    ).dropna().copy()
    if work.empty:
        return work
    mean = np.asarray(spec["feature_mean"], dtype=float)
    std = np.asarray(spec["feature_std"], dtype=float)
    beta = np.asarray(
        spec["coefficients_standardized"],
        dtype=float,
    )
    if len(mean) != len(features) or len(std) != len(features):
        raise RuntimeError("frozen linear scaling width mismatch")
    if len(beta) != len(features) + 1:
        raise RuntimeError("frozen linear coefficient width mismatch")
    work["_row_id"] = work.index.astype(str)
    x = work[features].to_numpy(float)
    z = (x - mean) / std
    design = np.column_stack([np.ones(len(z)), z])
    work["prediction"] = design @ beta
    return work


def apply_logistic_model(
    frame: pd.DataFrame,
    spec: dict[str, Any],
    target: str,
) -> pd.DataFrame:
    scored = apply_linear_model(frame, spec, target)
    if scored.empty:
        return scored
    linear = scored["prediction"].to_numpy(float)
    scored["prediction"] = (
        1.0 / (1.0 + np.exp(-np.clip(linear, -30, 30)))
    )
    return scored


def time_halves(
    frame: pd.DataFrame,
    metric: str,
    target: str,
) -> dict[str, float]:
    ordered = frame.sort_values("anchor_ts_ns")
    cut = len(ordered) // 2
    output: dict[str, float] = {}
    for label, part in {
        "FIRST": ordered.iloc[:cut],
        "SECOND": ordered.iloc[cut:],
    }.items():
        if metric == "auc":
            output[label] = (
                auc_score(
                    part[target].to_numpy(int),
                    part["prediction"].to_numpy(float),
                )
                if len(part) and part[target].nunique() >= 2
                else math.nan
            )
        else:
            output[label] = r2_score(
                part[target].to_numpy(float),
                part["prediction"].to_numpy(float),
            )
    return output


def bootstrap_mean_by_token(
    frame: pd.DataFrame,
    value: str,
    seed: int,
) -> dict[str, Any]:
    work = finite_frame(frame, ["token_id", value])
    grouped = {
        str(token): group[value].to_numpy(float)
        for token, group in work.groupby("token_id")
    }
    keys = list(grouped)
    if not keys:
        return {"n": 0, "clusters": 0}
    rng = np.random.default_rng(seed)
    draws: list[float] = []
    for _ in range(BOOTSTRAPS):
        sample_keys = rng.choice(
            keys,
            size=len(keys),
            replace=True,
        )
        sample = np.concatenate(
            [grouped[str(key)] for key in sample_keys]
        )
        draws.append(float(sample.mean()))
    arr = np.asarray(draws, dtype=float)
    return {
        "n": int(len(work)),
        "clusters": int(len(keys)),
        "mean": float(work[value].mean()),
        "ci_2_5": float(np.quantile(arr, 0.025)),
        "ci_97_5": float(np.quantile(arr, 0.975)),
        "positive_fraction": float(np.mean(arr > 0)),
    }


def bootstrap_auc_by_token(
    frame: pd.DataFrame,
    target: str,
    seed: int,
) -> dict[str, Any]:
    work = finite_frame(
        frame,
        ["token_id", target, "prediction"],
    )
    grouped = {
        str(token): group.copy()
        for token, group in work.groupby("token_id")
    }
    keys = list(grouped)
    if not keys:
        return {"n": 0, "clusters": 0}
    rng = np.random.default_rng(seed)
    draws: list[float] = []
    for _ in range(BOOTSTRAPS):
        sample_keys = rng.choice(
            keys,
            size=len(keys),
            replace=True,
        )
        sample = pd.concat(
            [grouped[str(key)] for key in sample_keys],
            ignore_index=True,
        )
        if sample[target].nunique() < 2:
            continue
        value = auc_score(
            sample[target].to_numpy(int),
            sample["prediction"].to_numpy(float),
        )
        if math.isfinite(value):
            draws.append(value)
    point = (
        auc_score(
            work[target].to_numpy(int),
            work["prediction"].to_numpy(float),
        )
        if work[target].nunique() >= 2
        else math.nan
    )
    if not draws:
        return {
            "n": int(len(work)),
            "clusters": int(len(keys)),
            "auc": point,
        }
    arr = np.asarray(draws, dtype=float)
    return {
        "n": int(len(work)),
        "clusters": int(len(keys)),
        "auc": point,
        "ci_2_5": float(np.quantile(arr, 0.025)),
        "ci_97_5": float(np.quantile(arr, 0.975)),
    }


def common_linear_scores(
    frame: pd.DataFrame,
    left: dict[str, Any],
    right: dict[str, Any],
    target: str,
) -> pd.DataFrame:
    left_scored = apply_linear_model(frame, left, target)
    right_scored = apply_linear_model(frame, right, target)
    if left_scored.empty or right_scored.empty:
        return pd.DataFrame()
    left_out = left_scored[
        [
            "_row_id",
            "token_id",
            "anchor_ts_ns",
            target,
            "prediction",
        ]
    ].rename(columns={"prediction": "left_prediction"})
    right_out = right_scored[
        ["_row_id", "prediction"]
    ].rename(columns={"prediction": "right_prediction"})
    return left_out.merge(
        right_out,
        on="_row_id",
        how="inner",
        validate="one_to_one",
    )


def bootstrap_r2_difference(
    common: pd.DataFrame,
    target: str,
    left_prediction: str,
    right_prediction: str,
    seed: int,
) -> dict[str, Any]:
    if common.empty:
        return {"n": 0, "clusters": 0}
    grouped = {
        str(token): group.copy()
        for token, group in common.groupby("token_id")
    }
    keys = list(grouped)
    if not keys:
        return {"n": 0, "clusters": 0}
    y = common[target].to_numpy(float)
    left_r2 = r2_score(
        y,
        common[left_prediction].to_numpy(float),
    )
    right_r2 = r2_score(
        y,
        common[right_prediction].to_numpy(float),
    )
    rng = np.random.default_rng(seed)
    draws: list[float] = []
    for _ in range(BOOTSTRAPS):
        sample_keys = rng.choice(
            keys,
            size=len(keys),
            replace=True,
        )
        sample = pd.concat(
            [grouped[str(key)] for key in sample_keys],
            ignore_index=True,
        )
        sy = sample[target].to_numpy(float)
        l = r2_score(
            sy,
            sample[left_prediction].to_numpy(float),
        )
        r = r2_score(
            sy,
            sample[right_prediction].to_numpy(float),
        )
        if math.isfinite(l) and math.isfinite(r):
            draws.append(r - l)
    result = {
        "n": int(len(common)),
        "clusters": int(len(keys)),
        "left_r2": left_r2,
        "right_r2": right_r2,
        "r2_difference": right_r2 - left_r2,
    }
    if draws:
        arr = np.asarray(draws, dtype=float)
        result.update(
            {
                "ci_2_5": float(np.quantile(arr, 0.025)),
                "ci_97_5": float(np.quantile(arr, 0.975)),
                "positive_fraction": float(np.mean(arr > 0)),
            }
        )
    return result


def matched_case_control(
    fills: pd.DataFrame,
    controls: pd.DataFrame,
    pairs: pd.DataFrame,
    features: list[str],
) -> pd.DataFrame:
    fill_by_group = fills.set_index("group_id", drop=False)
    control_frame = controls.copy()
    control_frame["_key"] = (
        control_frame["token_id"].astype(str)
        + "|"
        + control_frame["anchor_ts_ns"].astype("int64").astype(str)
    )
    control_by_key = control_frame.set_index("_key", drop=False)
    rows: list[dict[str, Any]] = []
    for pair in pairs.itertuples(index=False):
        group_id = str(pair.group_id)
        if group_id not in fill_by_group.index:
            continue
        fill = fill_by_group.loc[group_id]
        if isinstance(fill, pd.DataFrame):
            fill = fill.iloc[0]
        key = (
            str(pair.token_id)
            + "|"
            + str(int(pair.control_ts_ns))
        )
        if key not in control_by_key.index:
            continue
        control = control_by_key.loc[key]
        if isinstance(control, pd.DataFrame):
            control = control.iloc[0]
        fill_row: dict[str, Any] = {
            "token_id": str(fill["token_id"]),
            "anchor_ts_ns": int(fill["anchor_ts_ns"]),
            "arrival": 1,
        }
        control_row: dict[str, Any] = {
            "token_id": str(control["token_id"]),
            "anchor_ts_ns": int(control["anchor_ts_ns"]),
            "arrival": 0,
        }
        for feature in features:
            fill_row[feature] = fill.get(feature)
            control_row[feature] = control.get(feature)
        rows.extend([fill_row, control_row])
    return pd.DataFrame(rows)


def evaluate_c01(
    candidate: dict[str, Any],
    fills: pd.DataFrame,
) -> dict[str, Any]:
    spec = candidate["frozen_spec"]
    exact = fills[fills["economic_exact"].astype(bool)].copy()
    common = common_linear_scores(
        exact,
        spec["absolute_model"],
        spec["relative_model"],
        "signed_move_30s",
    )
    boot = bootstrap_r2_difference(
        common,
        "signed_move_30s",
        "left_prediction",
        "right_prediction",
        SEED + 1,
    )
    passed = (
        int(boot.get("n", 0)) >= 50
        and float(boot.get("right_r2", -999)) > 0
        and float(boot.get("r2_difference", -999)) > 0
        and float(boot.get("ci_2_5", -999)) > 0
    )
    return {
        "candidate_id": candidate["candidate_id"],
        "status": "PASS" if passed else "FAIL",
        "metrics": boot,
    }


def evaluate_c02(
    candidate: dict[str, Any],
    fills: pd.DataFrame,
) -> dict[str, Any]:
    work = finite_frame(
        fills,
        [
            "token_id",
            "failed_replenish_80_30s",
            "signed_move_30s",
        ],
    )
    failed = work[
        work["failed_replenish_80_30s"].astype(bool)
    ]
    recovered = work[
        ~work["failed_replenish_80_30s"].astype(bool)
    ]
    joined = work.copy()
    failed_mean = float(failed["signed_move_30s"].mean())
    recovered_mean = float(
        recovered["signed_move_30s"].mean()
    )
    joined["_gap_value"] = np.where(
        joined["failed_replenish_80_30s"].astype(bool),
        joined["signed_move_30s"],
        -joined["signed_move_30s"],
    )
    grouped = {
        str(token): group.copy()
        for token, group in work.groupby("token_id")
    }
    keys = list(grouped)
    rng = np.random.default_rng(SEED + 2)
    draws: list[float] = []
    for _ in range(BOOTSTRAPS):
        sample_keys = rng.choice(
            keys,
            size=len(keys),
            replace=True,
        ) if keys else []
        if len(sample_keys) == 0:
            break
        sample = pd.concat(
            [grouped[str(key)] for key in sample_keys],
            ignore_index=True,
        )
        f = sample[
            sample["failed_replenish_80_30s"].astype(bool)
        ]["signed_move_30s"]
        r = sample[
            ~sample["failed_replenish_80_30s"].astype(bool)
        ]["signed_move_30s"]
        if len(f) and len(r):
            draws.append(float(f.mean() - r.mean()))
    gap = failed_mean - recovered_mean
    metrics: dict[str, Any] = {
        "n": int(len(work)),
        "failed_n": int(len(failed)),
        "recovered_n": int(len(recovered)),
        "failed_mean": failed_mean,
        "recovered_mean": recovered_mean,
        "gap": gap,
        "clusters": int(len(keys)),
    }
    if draws:
        arr = np.asarray(draws, dtype=float)
        metrics.update(
            {
                "ci_2_5": float(np.quantile(arr, 0.025)),
                "ci_97_5": float(np.quantile(arr, 0.975)),
            }
        )
    passed = (
        len(failed) >= 20
        and len(recovered) >= 20
        and gap > 0
        and float(metrics.get("ci_2_5", -999)) > 0
    )
    return {
        "candidate_id": candidate["candidate_id"],
        "status": "PASS" if passed else "FAIL",
        "metrics": metrics,
    }


def evaluate_c03(
    candidate: dict[str, Any],
    pairs: pd.DataFrame,
) -> dict[str, Any]:
    boot = bootstrap_mean_by_token(
        pairs,
        "delta_signed_move_30s",
        SEED + 3,
    )
    passed = (
        int(boot.get("n", 0)) >= 50
        and float(boot.get("mean", -999)) > 0
        and float(boot.get("ci_2_5", -999)) > 0
    )
    return {
        "candidate_id": candidate["candidate_id"],
        "status": "PASS" if passed else "FAIL",
        "metrics": boot,
    }


def evaluate_c04(
    candidate: dict[str, Any],
    fills: pd.DataFrame,
    controls: pd.DataFrame,
    pairs: pd.DataFrame,
) -> dict[str, Any]:
    model = candidate["frozen_spec"]["arrival_model"]
    features = [str(x) for x in model["features"]]
    case_control = matched_case_control(
        fills,
        controls,
        pairs,
        features,
    )
    scored = apply_logistic_model(
        case_control,
        model,
        "arrival",
    )
    boot = bootstrap_auc_by_token(
        scored,
        "arrival",
        SEED + 4,
    )
    halves = time_halves(
        scored,
        "auc",
        "arrival",
    )
    passed = (
        int(boot.get("n", 0)) >= 100
        and float(boot.get("auc", -999)) >= 0.60
        and float(boot.get("ci_2_5", -999)) > 0.50
        and min(
            float(halves.get("FIRST", -999)),
            float(halves.get("SECOND", -999)),
        ) >= 0.55
    )
    return {
        "candidate_id": candidate["candidate_id"],
        "status": "PASS" if passed else "FAIL",
        "metrics": {
            **boot,
            "time_halves": halves,
            "positive_rows": int(
                scored["arrival"].sum()
            ) if not scored.empty else 0,
            "negative_rows": int(
                len(scored) - scored["arrival"].sum()
            ) if not scored.empty else 0,
        },
    }


def evaluate_c05(
    candidate: dict[str, Any],
    fills: pd.DataFrame,
) -> dict[str, Any]:
    model = candidate["frozen_spec"]["direction_model"]
    work = fills.copy()
    work["buy"] = (work["q"].astype(int) > 0).astype(int)
    scored = apply_logistic_model(
        work,
        model,
        "buy",
    )
    boot = bootstrap_auc_by_token(
        scored,
        "buy",
        SEED + 5,
    )
    halves = time_halves(
        scored,
        "auc",
        "buy",
    )
    buy = int(scored["buy"].sum()) if not scored.empty else 0
    sell = int(len(scored) - buy)
    passed = (
        buy >= 100
        and sell >= 100
        and float(boot.get("auc", -999)) >= 0.58
        and float(boot.get("ci_2_5", -999)) > 0.50
        and min(
            float(halves.get("FIRST", -999)),
            float(halves.get("SECOND", -999)),
        ) >= 0.52
    )
    return {
        "candidate_id": candidate["candidate_id"],
        "status": "PASS" if passed else "FAIL",
        "metrics": {
            **boot,
            "time_halves": halves,
            "buy_rows": buy,
            "sell_rows": sell,
        },
    }


def c06_stratum(
    frame: pd.DataFrame,
    baseline: dict[str, Any],
    challenger: dict[str, Any],
) -> dict[str, Any]:
    common = common_linear_scores(
        frame,
        baseline,
        challenger,
        "signed_move_30s",
    )
    return bootstrap_r2_difference(
        common,
        "signed_move_30s",
        "left_prediction",
        "right_prediction",
        SEED + 6,
    )


def evaluate_c06(
    candidate: dict[str, Any],
    fills: pd.DataFrame,
) -> dict[str, Any]:
    spec = candidate["frozen_spec"]
    baseline = spec["baseline_model"]
    challenger = spec["challenger_model"]
    full = c06_stratum(fills, baseline, challenger)
    stale_limit = float(spec["stale_book_max_ms"])
    activity_limit = float(spec["train_activity_p90"])
    stale = c06_stratum(
        fills[
            pd.to_numeric(
                fills["book_age_ms"],
                errors="coerce",
            ) <= stale_limit
        ],
        baseline,
        challenger,
    )
    quiet = c06_stratum(
        fills[
            pd.to_numeric(
                fills["activity_60"],
                errors="coerce",
            ) <= activity_limit
        ],
        baseline,
        challenger,
    )
    passed = (
        int(full.get("n", 0)) >= 50
        and float(full.get("r2_difference", -999)) >= 0.005
        and float(full.get("ci_2_5", -999)) > 0
        and float(stale.get("r2_difference", -999)) > 0
        and float(quiet.get("r2_difference", -999)) > 0
    )
    return {
        "candidate_id": candidate["candidate_id"],
        "status": "PASS" if passed else "FAIL",
        "metrics": {
            "challenger_name": spec["challenger_name"],
            "full": full,
            "stale_excluded": stale,
            "high_activity_excluded": quiet,
            "stale_book_max_ms": stale_limit,
            "train_activity_p90": activity_limit,
        },
    }


def main() -> None:
    freeze = load_json("PRE_HOLDOUT_FREEZE.json")
    build = load_json("HOLDOUT_BUILD_SUMMARY.json")
    if freeze.get("stage") != "PRE_HOLDOUT_FREEZE":
        raise RuntimeError("wrong pre-holdout freeze stage")
    if freeze.get("b0_authorized") is not True:
        raise RuntimeError("B0 scoring not authorized")
    if freeze.get("b0_opened") is not False:
        raise RuntimeError("freeze says B0 was opened early")
    if build.get("stage") != "ONE_SHOT_B0_BUILD":
        raise RuntimeError("wrong B0 build stage")
    if build.get("b0_opened") is not True:
        raise RuntimeError("B0 build does not prove opening")
    if build.get("model_scoring_performed") is not False:
        raise RuntimeError("B0 build mixed state construction with scoring")

    fills = pd.read_parquet(
        locate("HOLDOUT_FILL_EVENTS.parquet")
    )
    controls = pd.read_parquet(
        locate("HOLDOUT_NONFILL_CONTROLS.parquet")
    )
    pairs = pd.read_csv(
        locate("HOLDOUT_MATCHED_CONTROLS.csv")
    )
    candidate_ids = [
        str(value)
        for value in freeze.get("candidate_ids", [])
    ]
    candidates = {
        str(row["candidate_id"]): row
        for row in freeze.get("candidates", [])
    }
    if sorted(candidate_ids) != sorted(candidates):
        raise RuntimeError(
            "candidate ids and frozen candidate objects disagree"
        )

    evaluators = {
        "005H-C01-RELATIVE-SIZE": lambda row: evaluate_c01(
            row,
            fills,
        ),
        "005H-C02-FAILED-REPLENISHMENT": lambda row: evaluate_c02(
            row,
            fills,
        ),
        "005H-C03-FILL-BEYOND-STATE": lambda row: evaluate_c03(
            row,
            pairs,
        ),
        "005H-C04-ARRIVAL-STATE": lambda row: evaluate_c04(
            row,
            fills,
            controls,
            pairs,
        ),
        "005H-C05-DIRECTION-STATE": lambda row: evaluate_c05(
            row,
            fills,
        ),
        "005H-C06-BOOK-MICROSTRUCTURE-CHALLENGER": lambda row: evaluate_c06(
            row,
            fills,
        ),
    }
    results: list[dict[str, Any]] = []
    for candidate_id in candidate_ids:
        evaluator = evaluators.get(candidate_id)
        if evaluator is None:
            raise RuntimeError(
                f"no frozen B0 evaluator for {candidate_id}"
            )
        result = evaluator(candidates[candidate_id])
        result["mechanism"] = candidates[candidate_id].get(
            "mechanism"
        )
        result["holdout_rule"] = candidates[candidate_id].get(
            "holdout_rule"
        )
        result["source_robustness"] = candidates[candidate_id].get(
            "source_robustness"
        )
        results.append(result)

    result_frame = pd.DataFrame(
        [
            {
                "candidate_id": row["candidate_id"],
                "mechanism": row.get("mechanism"),
                "status": row["status"],
                "metrics_json": json.dumps(
                    row["metrics"],
                    sort_keys=True,
                    default=str,
                ),
                "source_robustness_json": json.dumps(
                    row.get("source_robustness"),
                    sort_keys=True,
                    default=str,
                ),
            }
            for row in results
        ]
    )
    result_frame.to_parquet(
        WORK / "HOLDOUT_RESULTS.parquet",
        index=False,
    )
    passed = [
        row["candidate_id"]
        for row in results
        if row["status"] == "PASS"
    ]
    failed = [
        row["candidate_id"]
        for row in results
        if row["status"] != "PASS"
    ]
    summary = {
        "schema_version": 1,
        "experiment": "EXPERIMENT-005H",
        "stage": "ONE_SHOT_B0_EVALUATION",
        "candidate_ids": candidate_ids,
        "passed": passed,
        "failed": failed,
        "results": results,
        "scoring_policy": (
            "exact frozen W17 parameters / frozen nonparametric rules; "
            "no B0 fitting, search, threshold changes, or rescue"
        ),
        "b0_opened": True,
        "b0_scored_once": True,
        "real_sig_orders_sent": False,
    }
    (WORK / "HOLDOUT_EVALUATION_SUMMARY.json").write_text(
        json.dumps(
            summary,
            indent=2,
            sort_keys=True,
            default=str,
        )
        + "\n",
        encoding="utf-8",
    )
    lines = [
        "# EXPERIMENT-005H — One-Shot B0 Evaluation",
        "",
        f"- Frozen candidates evaluated: **{len(results)}**",
        f"- Passed frozen gate: **{len(passed)}**",
        f"- Failed frozen gate: **{len(failed)}**",
        "- Parameter refit on B0: **NO**",
        "- Candidate search on B0: **NO**",
        "- Rescue subsets / threshold changes: **NO**",
        "",
    ]
    for row in results:
        lines.append(
            f"- {row['candidate_id']}: **{row['status']}**"
        )
    lines.extend(
        [
            "",
            "Every pass remains FUTURE_CONFIRMATION_REQUIRED.",
            "",
            "REAL SIG ORDERS SENT: NO",
        ]
    )
    (WORK / "HOLDOUT_EVALUATION_REPORT.md").write_text(
        "\n".join(lines) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "candidate_ids": candidate_ids,
                "passed": passed,
                "failed": failed,
                "b0_scored_once": True,
            },
            sort_keys=True,
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
