# ruff: noqa: E501
"""EXPERIMENT-005B Stage 4: sealed HOLDOUT evaluation. Refuses to run without a committed hash gate."""

from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.impute import SimpleImputer
from sklearn.linear_model import ElasticNet, LinearRegression, LogisticRegression, Ridge
from sklearn.metrics import accuracy_score, mean_absolute_error, mean_squared_error
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

FAMILIES = ("US_2024", "CAN_2025", "COL_2026", "HUN_2026", "PER_2026")
OUT = Path("/kaggle/working/005b_historical_predictive_atlas/holdout")
OUT.mkdir(parents=True, exist_ok=True)
HOLDOUT_PROTOCOL_SHA256 = "c6aa34e7e62161932ac9be3555c27c62d8738e549b0f73810e40a09aa2504c70"
HOLDOUT_PROTOCOL_REPO_PATH = "data/experiments/experiment_005b/holdout_protocol.json"
EXPECTED_TRAIN_DEV_SHORTLIST_SHA256 = "0000000000000000000000000000000000000000000000000000000000000000"
HOLDOUT_GATE_COMMIT = "PENDING"
EMBARGO = 300
BOOT = 1000
SEED = 505005
REGIME_FEATURES = (
    "regime_pre_election",
    "regime_election_day_pre_results",
    "regime_active_results",
    "regime_late_count",
    "regime_post_resolution_diagnostic",
)
REGIME_WINDOWS = {
    "COL_2026": (
        ("regime_pre_election", "2026-05-29T00:00:00+00:00", "2026-05-31T13:00:00+00:00"),
        ("regime_election_day_pre_results", "2026-05-31T13:00:00+00:00", "2026-05-31T21:11:00+00:00"),
        ("regime_active_results", "2026-05-31T21:11:00+00:00", "2026-06-01T03:11:00+00:00"),
        ("regime_late_count", "2026-06-01T03:11:00+00:00", "2026-06-05T05:00:00+00:00"),
        ("regime_pre_election", "2026-06-19T00:00:00+00:00", "2026-06-21T13:00:00+00:00"),
        ("regime_election_day_pre_results", "2026-06-21T13:00:00+00:00", "2026-06-21T21:11:00+00:00"),
        ("regime_active_results", "2026-06-21T21:11:00+00:00", "2026-06-22T03:11:00+00:00"),
        ("regime_late_count", "2026-06-22T03:11:00+00:00", "2026-06-25T05:00:00+00:00"),
    ),
    "HUN_2026": (
        ("regime_pre_election", "2026-04-05T00:00:00+00:00", "2026-04-12T04:00:00+00:00"),
        ("regime_election_day_pre_results", "2026-04-12T04:00:00+00:00", "2026-04-12T18:18:00+00:00"),
        ("regime_active_results", "2026-04-12T18:18:00+00:00", "2026-04-13T00:18:00+00:00"),
        ("regime_late_count", "2026-04-13T00:18:00+00:00", "2026-04-18T22:00:00+00:00"),
        ("regime_post_resolution_diagnostic", "2026-04-18T22:00:00+00:00", "2026-05-07T12:00:00+00:00"),
    ),
    "PER_2026": (
        ("regime_pre_election", "2026-04-10T00:00:00+00:00", "2026-04-12T12:00:00+00:00"),
        ("regime_election_day_pre_results", "2026-04-12T12:00:00+00:00", "2026-04-12T23:00:00+00:00"),
        ("regime_active_results", "2026-04-12T23:00:00+00:00", "2026-04-13T05:00:00+00:00"),
        ("regime_late_count", "2026-04-13T05:00:00+00:00", "2026-05-15T15:04:00+00:00"),
        ("regime_post_resolution_diagnostic", "2026-05-15T15:04:00+00:00", "2026-05-17T17:03:00+00:00"),
        ("regime_pre_election", "2026-06-05T00:00:00+00:00", "2026-06-07T12:00:00+00:00"),
        ("regime_election_day_pre_results", "2026-06-07T12:00:00+00:00", "2026-06-07T23:12:00+00:00"),
        ("regime_active_results", "2026-06-07T23:12:00+00:00", "2026-06-08T05:12:00+00:00"),
        ("regime_late_count", "2026-06-08T05:12:00+00:00", "2026-06-29T19:50:00+00:00"),
        ("regime_post_resolution_diagnostic", "2026-06-29T19:50:00+00:00", "2026-07-03T21:05:00+00:00"),
    ),
}


def epoch(value: str) -> int:
    return int(datetime.fromisoformat(value).timestamp())


REGIME_WINDOWS_EPOCH = {
    family: tuple(
        (name, epoch(start), epoch(end))
        for name, start, end in windows
    )
    for family, windows in REGIME_WINDOWS.items()
}


def apply_documented_regimes(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    for feature in REGIME_FEATURES:
        result[feature] = np.nan
    families = result["family"].astype(str).to_numpy()
    timestamps = result["timestamp"].to_numpy(dtype=np.int64)
    for family, windows in REGIME_WINDOWS_EPOCH.items():
        family_mask = families == family
        documented = np.zeros(len(result), dtype=bool)
        for feature, start, end in windows:
            mask = family_mask & (timestamps >= start) & (timestamps < end)
            if mask.any():
                result.loc[mask, feature] = 1.0
                documented |= mask
        for feature in REGIME_FEATURES:
            missing = documented & result[feature].isna().to_numpy()
            if missing.any():
                result.loc[missing, feature] = 0.0
    return result


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def locate_unique(pattern: str) -> Path:
    matches = sorted(Path("/kaggle/input").rglob(pattern))
    if len(matches) != 1:
        raise RuntimeError(f"expected one {pattern}, found {matches}")
    return matches[0]


def locate_family(family: str) -> Path:
    return locate_unique(f"feature_target_{family}.parquet")


def label_for_target(target: str) -> str:
    horizon = target.split("_")[-1]
    if target.startswith("target_clock_"):
        return f"clock_label_end_{horizon}"
    if target.startswith("target_event_"):
        return f"event_label_end_{horizon}"
    raise ValueError(target)


def load_gate() -> tuple[dict[str, Any], dict[str, Any], str]:
    expected = EXPECTED_TRAIN_DEV_SHORTLIST_SHA256
    if len(expected) != 64 or set(expected) == {"0"}:
        raise RuntimeError("HOLDOUT executable still carries the closed placeholder gate")
    if HOLDOUT_GATE_COMMIT in ("", "PENDING"):
        raise RuntimeError("HOLDOUT executable lacks its pre-HOLDOUT gate commit")
    freeze_path = locate_unique("train_dev_shortlist_freeze.json")
    actual = sha256(freeze_path)
    if actual != expected:
        raise RuntimeError(
            f"shortlist hash mismatch expected={expected} actual={actual}"
        )
    freeze = json.loads(freeze_path.read_text())
    if freeze.get("holdout_touched") is not False:
        raise RuntimeError("TRAIN/DEV freeze does not attest holdout_touched=false")
    gate = {
        "expected_train_dev_shortlist_sha256": expected,
        "gate_commit": HOLDOUT_GATE_COMMIT,
    }
    return gate, freeze, actual


def selected_columns(freeze: dict[str, Any]) -> dict[str, set[str]]:
    by_target: dict[str, set[str]] = {}
    for target, rows in freeze["shortlist"].items():
        cols = {target, label_for_target(target)}
        stable = [row for row in rows if row.get("stable_train_dev")]
        if stable:
            cols.add(stable[0]["feature"])
        model = freeze["model_selection"].get(target, {})
        cols.update(model.get("features", []))
        cols.update({"price_change_30", "absolute_return_30", "trade_count_30", "realised_vol_30"})
        by_target[target] = cols
    return by_target


def sql_columns(columns: set[str]) -> str:
    required = {
        "family", "market_id", "condition_id", "timestamp", "tx_hash", "log_index",
        "raw_split", "train_end_timestamp", "dev_end_timestamp",
    }
    physical = {
        column
        for column in columns
        if column not in REGIME_FEATURES
    }
    return ",".join(sorted(required | physical))


def load_target_frames(
    target: str,
    columns: set[str],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    label = label_for_target(target)
    train_parts = []
    hold_parts = []
    for family in FAMILIES:
        path = locate_family(family)
        q = str(path).replace("'", "''")
        con = duckdb.connect()
        cols = sql_columns(columns)
        train_parts.append(
            con.execute(
                f"""SELECT {cols} FROM read_parquet('{q}')
                    WHERE timestamp < dev_end_timestamp-{EMBARGO}
                      AND {target} IS NOT NULL
                      AND {label} IS NOT NULL
                      AND {label} < dev_end_timestamp
                    ORDER BY hash(condition_id,tx_hash,log_index)
                    LIMIT 100000"""
            ).df()
        )
        hold_parts.append(
            con.execute(
                f"""SELECT {cols} FROM read_parquet('{q}')
                    WHERE raw_split='HOLDOUT'
                      AND timestamp >= dev_end_timestamp+{EMBARGO}
                      AND {target} IS NOT NULL
                      AND {label} IS NOT NULL"""
            ).df()
        )
        con.close()
    return (
        apply_documented_regimes(
            pd.concat(train_parts, ignore_index=True)
        ),
        apply_documented_regimes(
            pd.concat(hold_parts, ignore_index=True)
        ),
    )


def matrix(df: pd.DataFrame, features: list[str]) -> np.ndarray:
    return (
        df[features]
        .apply(pd.to_numeric, errors="coerce")
        .to_numpy(dtype=np.float64, copy=False)
    )


def pearson(x: np.ndarray, y: np.ndarray) -> float:
    valid = np.isfinite(x) & np.isfinite(y)
    if valid.sum() < 3:
        return math.nan
    xv = x[valid]
    yv = y[valid]
    if np.std(xv) <= 0 or np.std(yv) <= 0:
        return math.nan
    return float(np.corrcoef(xv, yv)[0, 1])


def spearman(x: np.ndarray, y: np.ndarray) -> float:
    valid = np.isfinite(x) & np.isfinite(y)
    if valid.sum() < 3:
        return math.nan
    xr = pd.Series(x[valid]).rank(method="average").to_numpy()
    yr = pd.Series(y[valid]).rank(method="average").to_numpy()
    return pearson(xr, yr)


def scalar_metrics(x: np.ndarray, y: np.ndarray) -> dict[str, Any]:
    valid = np.isfinite(x) & np.isfinite(y)
    x = x[valid]
    y = y[valid]
    if len(y) < 20:
        return {"support": int(len(y))}
    median = float(np.median(x))
    q10 = float(np.quantile(x, 0.10))
    q90 = float(np.quantile(x, 0.90))
    high = y[x >= median]
    low = y[x < median]
    top = y[x >= q90]
    bottom = y[x <= q10]
    direction = float(high.mean() - low.mean()) if len(high) and len(low) else math.nan
    decile = float(top.mean() - bottom.mean()) if len(top) and len(bottom) else math.nan
    ystd = float(np.std(y))
    return {
        "support": int(len(y)),
        "pearson": pearson(x, y),
        "spearman": spearman(x, y),
        "directional_response": direction,
        "decile_response": decile,
        "standardized_effect": direction / ystd if ystd > 0 else math.nan,
    }


def regression_model(name: str, params: dict[str, Any]) -> Any:
    if name in {"own_history_linear", "univariate_linear", "ols"}:
        return make_pipeline(
            SimpleImputer(strategy="median"),
            StandardScaler(),
            LinearRegression(),
        )
    if name == "ridge":
        return make_pipeline(
            SimpleImputer(strategy="median"),
            StandardScaler(),
            Ridge(alpha=float(params["alpha"])),
        )
    if name == "elastic_net":
        return make_pipeline(
            SimpleImputer(strategy="median"),
            StandardScaler(),
            ElasticNet(
                alpha=float(params["alpha"]),
                l1_ratio=float(params["l1_ratio"]),
                max_iter=2000,
            ),
        )
    if name == "hist_gradient_boosting":
        return make_pipeline(
            SimpleImputer(strategy="median"),
            HistGradientBoostingRegressor(
                learning_rate=float(params["learning_rate"]),
                max_iter=int(params["max_iter"]),
                max_leaf_nodes=int(params["max_leaf_nodes"]),
                l2_regularization=float(params["l2_regularization"]),
                random_state=SEED,
            ),
        )
    raise ValueError(name)


def regression_feature_subset(
    target: str,
    model: dict[str, Any],
) -> list[str]:
    best = model["best"]
    name = best["name"]
    features = list(model["features"])
    if name == "univariate_linear":
        return [str(best["params"]["feature"])]
    if name == "own_history_linear":
        return [
            "price_change_30"
            if "price_change" in target
            else "realised_vol_30"
        ]
    return features


def classification_model(params: dict[str, Any]) -> Any:
    return make_pipeline(
        SimpleImputer(strategy="median"),
        StandardScaler(),
        LogisticRegression(
            C=float(params["C"]),
            max_iter=1000,
            random_state=SEED,
        ),
    )


def multiclass_brier(
    y_true: np.ndarray,
    proba: np.ndarray,
    classes: np.ndarray,
) -> float:
    onehot = np.zeros((len(y_true), len(classes)), dtype=float)
    mapping = {value: i for i, value in enumerate(classes)}
    for i, value in enumerate(y_true):
        onehot[i, mapping[value]] = 1.0
    return float(np.mean(np.sum((proba - onehot) ** 2, axis=1)))


def ece(
    y_true: np.ndarray,
    proba: np.ndarray,
    classes: np.ndarray,
    bins: int = 10,
) -> float:
    confidence = proba.max(axis=1)
    predicted = classes[proba.argmax(axis=1)]
    correct = (predicted == y_true).astype(float)
    edges = np.linspace(0.0, 1.0, bins + 1)
    total = len(y_true)
    result = 0.0
    for i in range(bins):
        mask = (
            (confidence >= edges[i])
            & (confidence < edges[i + 1] if i < bins - 1 else confidence <= edges[i + 1])
        )
        if mask.any():
            result += (
                mask.mean()
                * abs(float(correct[mask].mean()) - float(confidence[mask].mean()))
            )
    return float(result if total else math.nan)


def comparator_predictions(
    train: pd.DataFrame,
    hold: pd.DataFrame,
    target: str,
    classification: bool,
) -> dict[str, np.ndarray]:
    result: dict[str, np.ndarray] = {}
    ytr = pd.to_numeric(train[target], errors="coerce").to_numpy(dtype=float)
    valid = np.isfinite(ytr)
    if classification:
        counts = pd.Series(ytr[valid].astype(int)).value_counts()
        majority = int(counts.index[0])
        result["persistence"] = np.full(len(hold), majority, dtype=float)
    else:
        base = 0.0 if "price_change" in target else float(np.nanmean(ytr))
        result["persistence"] = np.full(len(hold), base, dtype=float)
    for name, feature in (
        ("own_recent_price", "price_change_30"),
        ("current_absolute_movement", "absolute_return_30"),
        ("recent_activity", "trade_count_30"),
    ):
        xtr = matrix(train, [feature])
        xh = matrix(hold, [feature])
        try:
            if classification:
                model = make_pipeline(
                    SimpleImputer(strategy="median"),
                    StandardScaler(),
                    LogisticRegression(
                        C=1.0,
                        max_iter=1000,
                        random_state=SEED,
                    ),
                )
            else:
                model = make_pipeline(
                    SimpleImputer(strategy="median"),
                    StandardScaler(),
                    LinearRegression(),
                )
            model.fit(xtr[valid], ytr[valid].astype(int) if classification else ytr[valid])
            result[name] = model.predict(xh).astype(float)
        except Exception:
            result[name] = np.full(len(hold), np.nan)
    return result


def market_level_differences(
    hold: pd.DataFrame,
    y: np.ndarray,
    candidate: np.ndarray,
    baseline: np.ndarray,
    classification: bool,
) -> pd.DataFrame:
    frame = hold[["family", "market_id", "timestamp"]].copy()
    if classification:
        frame["diff"] = (
            (baseline != y).astype(float)
            - (candidate != y).astype(float)
        )
    else:
        frame["diff"] = np.abs(y - baseline) - np.abs(y - candidate)
    frame["day"] = (frame["timestamp"] // 86400).astype(int)
    return frame


def hierarchical_bootstrap(frame: pd.DataFrame) -> dict[str, float]:
    rng = np.random.default_rng(SEED)
    market = (
        frame.groupby(["family", "market_id"], as_index=False)["diff"]
        .mean()
    )
    families = sorted(market["family"].unique())
    values = []
    for _ in range(BOOT):
        sampled_families = rng.choice(families, size=len(families), replace=True)
        family_values = []
        for family in sampled_families:
            rows = market[market["family"] == family]
            sampled = rows.iloc[
                rng.integers(0, len(rows), size=len(rows))
            ]
            family_values.append(float(sampled["diff"].mean()))
        values.append(float(np.mean(family_values)))
    return {
        "mean": float(np.mean(values)),
        "lower_2_5": float(np.quantile(values, 0.025)),
        "upper_97_5": float(np.quantile(values, 0.975)),
    }


def calendar_block_bootstrap(frame: pd.DataFrame) -> dict[str, float]:
    rng = np.random.default_rng(SEED + 1)
    daily = frame.groupby("day", as_index=False)["diff"].mean().sort_values("day")
    values = daily["diff"].to_numpy(dtype=float)
    if len(values) < 3:
        return {"mean": math.nan, "lower_2_5": math.nan, "upper_97_5": math.nan}
    reps = []
    block = 3
    starts = np.arange(len(values))
    for _ in range(BOOT):
        sample = []
        while len(sample) < len(values):
            start = int(rng.choice(starts))
            sample.extend(values[(start + np.arange(block)) % len(values)].tolist())
        reps.append(float(np.mean(sample[: len(values)])))
    return {
        "mean": float(np.mean(reps)),
        "lower_2_5": float(np.quantile(reps, 0.025)),
        "upper_97_5": float(np.quantile(reps, 0.975)),
    }


def evaluate_target(
    target: str,
    freeze: dict[str, Any],
    columns: set[str],
) -> dict[str, Any]:
    train, hold = load_target_frames(target, columns)
    y_train = pd.to_numeric(train[target], errors="coerce").to_numpy(dtype=float)
    y_hold = pd.to_numeric(hold[target], errors="coerce").to_numpy(dtype=float)
    valid_train = np.isfinite(y_train)
    valid_hold = np.isfinite(y_hold)
    train = train.loc[valid_train].reset_index(drop=True)
    hold = hold.loc[valid_hold].reset_index(drop=True)
    y_train = y_train[valid_train]
    y_hold = y_hold[valid_hold]

    result: dict[str, Any] = {
        "target": target,
        "train_dev_fit_rows": int(len(train)),
        "holdout_rows": int(len(hold)),
        "holdout_market_count": int(hold["market_id"].nunique()),
        "holdout_family_counts": {
            str(key): int(value)
            for key, value in hold["family"].value_counts().sort_index().items()
        },
    }

    stable = [
        row
        for row in freeze["shortlist"].get(target, [])
        if row.get("stable_train_dev")
    ]
    if stable:
        scalar = stable[0]
        feature = scalar["feature"]
        result["scalar_candidate"] = {
            "feature": feature,
            "train_spearman": scalar.get("spearman"),
            "dev_pearson": scalar.get("dev_pearson"),
            "selection_label": scalar.get("selection_label"),
            "holdout": scalar_metrics(
                pd.to_numeric(hold[feature], errors="coerce").to_numpy(dtype=float),
                y_hold,
            ),
        }
    else:
        result["scalar_candidate"] = None

    selection = freeze["model_selection"].get(target, {})
    if selection.get("status") != "OK" or not len(hold):
        result["model"] = None
        return result

    classification = "_sign_" in target
    best = selection["best"]
    if classification:
        if best["name"] == "majority_baseline":
            counts = pd.Series(y_train.astype(int)).value_counts()
            majority = int(counts.index[0])
            pred = np.full(len(y_hold), majority)
            classes = np.array(sorted(set(y_train.astype(int)) | set(y_hold.astype(int))))
            prior = np.array([np.mean(y_train.astype(int) == c) for c in classes])
            proba = np.repeat(prior[None, :], len(y_hold), axis=0)
        else:
            features = list(selection["features"])
            model = classification_model(best["params"])
            model.fit(matrix(train, features), y_train.astype(int))
            pred = model.predict(matrix(hold, features))
            raw = model.predict_proba(matrix(hold, features))
            classes = np.array(sorted(set(y_train.astype(int)) | set(y_hold.astype(int))))
            proba = np.zeros((len(y_hold), len(classes)))
            for j, value in enumerate(model[-1].classes_):
                proba[:, np.where(classes == value)[0][0]] = raw[:, j]
        metrics = {
            "accuracy": float(accuracy_score(y_hold.astype(int), pred.astype(int))),
            "multiclass_brier": multiclass_brier(y_hold.astype(int), proba, classes),
            "expected_calibration_error": ece(y_hold.astype(int), proba, classes),
        }
        comps = comparator_predictions(train, hold, target, True)
        comp_metrics = {
            name: {
                "accuracy": float(accuracy_score(y_hold.astype(int), values.astype(int)))
            }
            for name, values in comps.items()
            if np.isfinite(values).all()
        }
        base = comps["persistence"]
        uncertainty_frame = market_level_differences(
            hold,
            y_hold.astype(int),
            pred.astype(int),
            base.astype(int),
            True,
        )
    else:
        if best["name"] == "persistence_baseline":
            base_value = 0.0 if "price_change" in target else float(np.mean(y_train))
            pred = np.full(len(y_hold), base_value)
            fitted_features: list[str] = []
        else:
            fitted_features = regression_feature_subset(target, selection)
            model = regression_model(best["name"], best["params"])
            model.fit(matrix(train, fitted_features), y_train)
            pred = model.predict(matrix(hold, fitted_features))
        comps = comparator_predictions(train, hold, target, False)
        persistence = comps["persistence"]
        mae = float(mean_absolute_error(y_hold, pred))
        mse = float(mean_squared_error(y_hold, pred))
        base_mae = float(mean_absolute_error(y_hold, persistence))
        base_mse = float(mean_squared_error(y_hold, persistence))
        metrics = {
            "mae": mae,
            "mse": mse,
            "mae_improvement_vs_persistence": (
                (base_mae - mae) / base_mae if base_mae else 0.0
            ),
            "mse_improvement_vs_persistence": (
                (base_mse - mse) / base_mse if base_mse else 0.0
            ),
            "predictive_ic": pearson(pred, y_hold),
            "directional_accuracy": float(np.mean(np.sign(pred) == np.sign(y_hold))),
        }
        comp_metrics = {}
        for name, values in comps.items():
            if not np.isfinite(values).all():
                continue
            comp_metrics[name] = {
                "mae": float(mean_absolute_error(y_hold, values)),
                "mse": float(mean_squared_error(y_hold, values)),
                "predictive_ic": pearson(values, y_hold),
            }
        uncertainty_frame = market_level_differences(
            hold,
            y_hold,
            pred,
            persistence,
            False,
        )

    family_metric = {}
    for family in FAMILIES:
        mask = hold["family"].to_numpy() == family
        if not mask.any():
            continue
        if classification:
            family_metric[family] = {
                "accuracy": float(
                    accuracy_score(
                        y_hold[mask].astype(int),
                        pred[mask].astype(int),
                    )
                )
            }
        else:
            family_metric[family] = {
                "mae_improvement_vs_persistence": float(
                    (
                        mean_absolute_error(y_hold[mask], comps["persistence"][mask])
                        - mean_absolute_error(y_hold[mask], pred[mask])
                    )
                    / mean_absolute_error(y_hold[mask], comps["persistence"][mask])
                )
                if mean_absolute_error(y_hold[mask], comps["persistence"][mask]) > 0
                else 0.0
            }

    result["model"] = {
        "dev_selected": best,
        "features": selection.get("features", []),
        "holdout_metrics": metrics,
        "baseline_metrics": comp_metrics,
        "per_family_metrics": family_metric,
        "hierarchical_market_bootstrap": hierarchical_bootstrap(uncertainty_frame),
        "calendar_3day_block_bootstrap": calendar_block_bootstrap(uncertainty_frame),
    }
    return result


def main() -> None:
    gate, freeze, freeze_sha = load_gate()
    columns = selected_columns(freeze)
    results = []
    for target in sorted(columns):
        has_scalar = any(
            row.get("stable_train_dev")
            for row in freeze["shortlist"].get(target, [])
        )
        has_model = freeze["model_selection"].get(target, {}).get("status") == "OK"
        if not (has_scalar or has_model):
            continue
        print(f"HOLDOUT {target}", flush=True)
        results.append(evaluate_target(target, freeze, columns[target]))

    payload = {
        "schema_version": 1,
        "experiment_id": "EXPERIMENT-005B",
        "stage": "SEALED_HOLDOUT",
        "gate": gate,
        "holdout_protocol_sha256": HOLDOUT_PROTOCOL_SHA256,
        "holdout_protocol_repo_path": HOLDOUT_PROTOCOL_REPO_PATH,
        "train_dev_shortlist_sha256": freeze_sha,
        "targets_evaluated": len(results),
        "results": results,
    }
    path = OUT / "holdout_results.json"
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"targets_evaluated": len(results)}, sort_keys=True))


if __name__ == "__main__":
    main()
