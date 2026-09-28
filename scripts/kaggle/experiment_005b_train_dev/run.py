# ruff: noqa: E501
"""EXPERIMENT-005B Stage 3: TRAIN census, redundancy, DEV selection and model freeze."""

from __future__ import annotations

import hashlib
import json
import math
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
OUT = Path("/kaggle/working/005b_historical_predictive_atlas/train_dev")
OUT.mkdir(parents=True, exist_ok=True)
SPEC_PATH = Path(__file__).with_name("screening_model_spec.json")
META = {
    "family", "event_id", "market_id", "condition_id", "timestamp", "tx_hash", "log_index",
    "market_order_us", "raw_split", "train_end_timestamp", "dev_end_timestamp",
}
LABEL_PREFIXES = ("clock_label_end_", "event_label_end_")
EMBARGO = 300


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def locate_files() -> dict[str, Path]:
    found = {}
    for family in FAMILIES:
        matches = sorted(Path("/kaggle/input").rglob(f"feature_target_{family}.parquet"))
        if len(matches) != 1:
            raise RuntimeError(f"expected one feature-target parquet for {family}, found {matches}")
        found[family] = matches[0]
    return found


def label_for_target(target: str) -> str:
    horizon = target.split("_")[-1]
    if target.startswith("target_clock_"):
        return f"clock_label_end_{horizon}"
    if target.startswith("target_event_"):
        return f"event_label_end_{horizon}"
    raise ValueError(target)


def feature_group(name: str) -> str:
    if name.startswith("loo_family_"):
        return "family_context"
    if name.startswith(("loo_event_", "common_event_", "market_minus_event_")):
        return "event_context"
    if name.startswith(("realised_vol_", "absolute_return_", "recent_max_move_", "jump_frequency_", "stasis_fraction_", "direction_change_frequency_")):
        return "movement_state"
    if name.startswith(("trade_count_", "notional_value_", "share_volume_", "mean_trade_size_", "median_trade_size_", "trade_size_variance_", "trade_size_q90_", "max_trade_size_", "large_trade_share_", "last_interarrival_", "mean_interarrival_", "burstiness_", "activity_acceleration_", "activity_vs_train_")):
        return "anonymous_activity"
    return "price_history"


def model_eligible(target: str) -> bool:
    return (
        "price_change" in target
        or target.startswith("target_clock_realised_movement_")
        or "_sign_" in target
    ) and "logit_change" not in target and "absolute_change" not in target


def deterministic_sample(
    con: duckdb.DuckDBPyConnection,
    path: Path,
    split: str,
    limit: int,
) -> pd.DataFrame:
    q = str(path).replace("'", "''")
    if split == "TRAIN":
        where = f"raw_split='TRAIN' AND timestamp < train_end_timestamp-{EMBARGO}"
    elif split == "DEV":
        where = (
            f"raw_split='DEV' AND timestamp >= train_end_timestamp+{EMBARGO} "
            f"AND timestamp < dev_end_timestamp-{EMBARGO}"
        )
    else:
        raise ValueError(split)
    return con.execute(
        f"""SELECT * FROM read_parquet('{q}')
            WHERE {where}
            ORDER BY hash(condition_id,tx_hash,log_index)
            LIMIT {limit}"""
    ).df()


def schema_columns(
    con: duckdb.DuckDBPyConnection,
    path: Path,
) -> tuple[list[str], list[str]]:
    q = str(path).replace("'", "''")
    names = [
        row[0]
        for row in con.execute(
            f"DESCRIBE SELECT * FROM read_parquet('{q}')"
        ).fetchall()
    ]
    targets = [name for name in names if name.startswith("target_")]
    features = [
        name
        for name in names
        if name not in META
        and not name.startswith("target_")
        and not name.startswith(LABEL_PREFIXES)
    ]
    return features, targets


def full_support(
    con: duckdb.DuckDBPyConnection,
    path: Path,
    targets: list[str],
) -> dict[str, dict[str, int]]:
    q = str(path).replace("'", "''")
    expr = []
    for target in targets:
        label = label_for_target(target)
        valid = (
            f"raw_split='TRAIN' AND timestamp < train_end_timestamp-{EMBARGO} "
            f"AND {target} IS NOT NULL AND {label} < train_end_timestamp"
        )
        key = target.replace("'", "")
        expr.extend(
            [
                f"SUM(CASE WHEN {valid} THEN 1 ELSE 0 END) AS n__{key}",
                f"COUNT(DISTINCT CASE WHEN {valid} THEN market_id END) AS m__{key}",
                f"COUNT(DISTINCT CASE WHEN {valid} THEN CAST(timestamp/86400 AS BIGINT) END) AS b__{key}",
            ]
        )
    row = con.execute(
        "SELECT " + ",".join(expr) + f" FROM read_parquet('{q}')"
    ).fetchone()
    result = {}
    for i, target in enumerate(targets):
        result[target] = {
            "support": int(row[i * 3] or 0),
            "markets": int(row[i * 3 + 1] or 0),
            "blocks": int(row[i * 3 + 2] or 0),
        }
    return result


def numeric_matrix(df: pd.DataFrame, columns: list[str]) -> np.ndarray:
    return (
        df[columns]
        .apply(pd.to_numeric, errors="coerce")
        .to_numpy(dtype=np.float64, copy=False)
    )


def corr_columns(
    x: np.ndarray,
    y: np.ndarray,
    chunk: int = 32,
) -> tuple[np.ndarray, np.ndarray]:
    p = x.shape[1]
    corr = np.full(p, np.nan)
    count = np.zeros(p, dtype=np.int64)
    yfinite = np.isfinite(y)
    for start in range(0, p, chunk):
        stop = min(p, start + chunk)
        z = x[:, start:stop]
        valid = np.isfinite(z) & yfinite[:, None]
        n = valid.sum(axis=0).astype(np.float64)
        zz = np.where(valid, z, 0.0)
        yy = np.where(valid, y[:, None], 0.0)
        sx = zz.sum(axis=0)
        sy = yy.sum(axis=0)
        sxx = (zz * zz).sum(axis=0)
        syy = (yy * yy).sum(axis=0)
        sxy = (zz * yy).sum(axis=0)
        with np.errstate(invalid="ignore", divide="ignore"):
            cov = sxy - sx * sy / n
            vx = sxx - sx * sx / n
            vy = syy - sy * sy / n
            values = cov / np.sqrt(vx * vy)
        values[(n < 3) | (vx <= 0) | (vy <= 0)] = np.nan
        corr[start:stop] = values
        count[start:stop] = n.astype(np.int64)
    return corr, count


def response_metrics(
    x: np.ndarray,
    y: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    p = x.shape[1]
    median = np.nanmedian(x, axis=0)
    q10 = np.nanquantile(x, 0.10, axis=0)
    q90 = np.nanquantile(x, 0.90, axis=0)
    direction = np.full(p, np.nan)
    decile = np.full(p, np.nan)
    effect = np.full(p, np.nan)
    monotonicity = np.full(p, np.nan)
    yfinite = np.isfinite(y)
    ystd = float(np.nanstd(y))
    for j in range(p):
        xv = x[:, j]
        valid = np.isfinite(xv) & yfinite
        if valid.sum() < 20:
            continue
        high = valid & (xv >= median[j])
        low = valid & (xv < median[j])
        if high.sum() and low.sum():
            direction[j] = float(np.nanmean(y[high]) - np.nanmean(y[low]))
            if ystd > 0:
                effect[j] = direction[j] / ystd
        top = valid & (xv >= q90[j])
        bottom = valid & (xv <= q10[j])
        if top.sum() and bottom.sum():
            decile[j] = float(np.nanmean(y[top]) - np.nanmean(y[bottom]))
        ranks = pd.Series(xv[valid]).rank(pct=True, method="average").to_numpy()
        bins = np.minimum(9, (ranks * 10).astype(int))
        sums = np.bincount(bins, weights=y[valid], minlength=10)
        counts = np.bincount(bins, minlength=10)
        means = np.divide(
            sums,
            counts,
            out=np.full(10, np.nan),
            where=counts > 0,
        )
        ok = np.isfinite(means)
        if ok.sum() >= 4:
            monotonicity[j] = float(
                np.corrcoef(np.arange(10)[ok], means[ok])[0, 1]
            )
    return direction, decile, effect, monotonicity


def market_concentration(
    df: pd.DataFrame,
    target: str,
) -> tuple[float, float]:
    label = label_for_target(target)
    valid = (
        df[target].notna()
        & df[label].notna()
        & (df[label].to_numpy() < df["train_end_timestamp"].to_numpy())
    )
    counts = df.loc[valid, "market_id"].value_counts().to_numpy(dtype=float)
    if not len(counts):
        return math.nan, math.nan
    shares = counts / counts.sum()
    return float(np.sum(shares * shares)), float(shares.max())


def rank_matrix(df: pd.DataFrame, features: list[str]) -> np.ndarray:
    ranked = (
        df[features]
        .apply(pd.to_numeric, errors="coerce")
        .rank(pct=True, method="average")
    )
    return ranked.to_numpy(dtype=np.float64, copy=False)


def feature_market_concentration(
    markets: np.ndarray,
    feature_values: np.ndarray,
    target_values: np.ndarray,
) -> tuple[float, float]:
    valid = (
        np.isfinite(feature_values)
        & np.isfinite(target_values)
    )
    if not valid.any():
        return math.nan, math.nan
    _, counts = np.unique(markets[valid], return_counts=True)
    shares = counts.astype(float) / counts.sum()
    return float(np.sum(shares * shares)), float(shares.max())


def union_find_clusters(
    corr: np.ndarray,
    features: list[str],
    threshold: float,
) -> list[list[str]]:
    parent = list(range(len(features)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    for i in range(len(features)):
        for j in range(i):
            if np.isfinite(corr[i, j]) and abs(corr[i, j]) >= threshold:
                union(i, j)
    groups: dict[int, list[str]] = {}
    for i, name in enumerate(features):
        groups.setdefault(find(i), []).append(name)
    return [
        sorted(value)
        for _, value in sorted(groups.items(), key=lambda item: min(item[1]))
    ]


def fit_regression_models(
    x_train: np.ndarray,
    y_train: np.ndarray,
    x_dev: np.ndarray,
    y_dev: np.ndarray,
    features: list[str],
    target: str,
) -> dict[str, Any]:
    train_ok = np.isfinite(y_train)
    dev_ok = np.isfinite(y_dev)
    xtr = x_train[train_ok]
    ytr = y_train[train_ok]
    xdv = x_dev[dev_ok]
    ydv = y_dev[dev_ok]
    if len(ytr) < 500 or len(ydv) < 200:
        return {"status": "INSUFFICIENT_SUPPORT"}

    baseline_value = 0.0 if "price_change" in target else float(np.nanmean(ytr))
    baseline = np.full(len(ydv), baseline_value)
    base_mae = mean_absolute_error(ydv, baseline)
    base_mse = mean_squared_error(ydv, baseline)
    results: list[dict[str, Any]] = [
        {
            "name": "persistence_baseline",
            "params": {},
            "mae": base_mae,
            "mse": base_mse,
            "mae_improvement": 0.0,
            "mse_improvement": 0.0,
        }
    ]
    candidates: list[tuple[str, list[int], Any, dict[str, Any]]] = []
    own_feature = (
        "price_change_30"
        if "price_change" in target
        else "realised_vol_30"
    )
    if own_feature in features:
        candidates.append(
            (
                "own_history_linear",
                [features.index(own_feature)],
                make_pipeline(
                    SimpleImputer(strategy="median"),
                    StandardScaler(),
                    LinearRegression(),
                ),
                {},
            )
        )
    for j, name in enumerate(features):
        candidates.append(
            (
                "univariate_linear",
                [j],
                make_pipeline(
                    SimpleImputer(strategy="median"),
                    StandardScaler(),
                    LinearRegression(),
                ),
                {"feature": name},
            )
        )
    candidates.append(
        (
            "ols",
            list(range(len(features))),
            make_pipeline(
                SimpleImputer(strategy="median"),
                StandardScaler(),
                LinearRegression(),
            ),
            {},
        )
    )
    for alpha in (0.1, 1.0, 10.0):
        candidates.append(
            (
                "ridge",
                list(range(len(features))),
                make_pipeline(
                    SimpleImputer(strategy="median"),
                    StandardScaler(),
                    Ridge(alpha=alpha),
                ),
                {"alpha": alpha},
            )
        )
    for alpha in (0.001, 0.01, 0.1):
        for l1 in (0.1, 0.5, 0.9):
            candidates.append(
                (
                    "elastic_net",
                    list(range(len(features))),
                    make_pipeline(
                        SimpleImputer(strategy="median"),
                        StandardScaler(),
                        ElasticNet(
                            alpha=alpha,
                            l1_ratio=l1,
                            max_iter=2000,
                        ),
                    ),
                    {"alpha": alpha, "l1_ratio": l1},
                )
            )
    candidates.append(
        (
            "hist_gradient_boosting",
            list(range(len(features))),
            make_pipeline(
                SimpleImputer(strategy="median"),
                HistGradientBoostingRegressor(
                    learning_rate=0.05,
                    max_iter=100,
                    max_leaf_nodes=15,
                    l2_regularization=1.0,
                    random_state=505005,
                ),
            ),
            {
                "learning_rate": 0.05,
                "max_iter": 100,
                "max_leaf_nodes": 15,
                "l2_regularization": 1.0,
            },
        )
    )

    for name, idx, model, params in candidates:
        try:
            model.fit(xtr[:, idx], ytr)
            pred = model.predict(xdv[:, idx])
            mae = mean_absolute_error(ydv, pred)
            mse = mean_squared_error(ydv, pred)
            results.append(
                {
                    "name": name,
                    "params": params,
                    "mae": mae,
                    "mse": mse,
                    "mae_improvement": (
                        (base_mae - mae) / base_mae if base_mae else 0.0
                    ),
                    "mse_improvement": (
                        (base_mse - mse) / base_mse if base_mse else 0.0
                    ),
                }
            )
        except Exception as exc:
            results.append(
                {"name": name, "params": params, "error": repr(exc)}
            )
    valid = [row for row in results if "mae" in row]
    best = max(
        valid,
        key=lambda row: (
            row["mae_improvement"],
            row["mse_improvement"],
            -row["mae"],
        ),
    )
    return {
        "status": "OK",
        "baseline": {"mae": base_mae, "mse": base_mse},
        "best": best,
        "all": results,
    }


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


def fit_classification_models(
    x_train: np.ndarray,
    y_train: np.ndarray,
    x_dev: np.ndarray,
    y_dev: np.ndarray,
) -> dict[str, Any]:
    train_ok = np.isfinite(y_train)
    dev_ok = np.isfinite(y_dev)
    xtr = x_train[train_ok]
    ytr = y_train[train_ok].astype(int)
    xdv = x_dev[dev_ok]
    ydv = y_dev[dev_ok].astype(int)
    if len(ytr) < 500 or len(ydv) < 200 or len(np.unique(ytr)) < 2:
        return {"status": "INSUFFICIENT_SUPPORT"}

    classes = np.array(sorted(set(ytr) | set(ydv)))
    counts = pd.Series(ytr).value_counts()
    majority = int(counts.index[0])
    pred = np.full(len(ydv), majority)
    prior = np.array([np.mean(ytr == c) for c in classes])
    base_proba = np.repeat(prior[None, :], len(ydv), axis=0)
    results: list[dict[str, Any]] = [
        {
            "name": "majority_baseline",
            "params": {},
            "accuracy": accuracy_score(ydv, pred),
            "brier": multiclass_brier(ydv, base_proba, classes),
        }
    ]
    for c in (0.1, 1.0, 10.0):
        try:
            model = make_pipeline(
                SimpleImputer(strategy="median"),
                StandardScaler(),
                LogisticRegression(
                    C=c,
                    max_iter=1000,
                    random_state=505005,
                ),
            )
            model.fit(xtr, ytr)
            pred = model.predict(xdv)
            raw = model.predict_proba(xdv)
            model_classes = model[-1].classes_
            proba = np.zeros((len(ydv), len(classes)))
            for j, value in enumerate(model_classes):
                proba[:, np.where(classes == value)[0][0]] = raw[:, j]
            results.append(
                {
                    "name": "logistic_l2",
                    "params": {"C": c},
                    "accuracy": accuracy_score(ydv, pred),
                    "brier": multiclass_brier(ydv, proba, classes),
                }
            )
        except Exception as exc:
            results.append(
                {
                    "name": "logistic_l2",
                    "params": {"C": c},
                    "error": repr(exc),
                }
            )
    valid = [row for row in results if "brier" in row]
    best = min(valid, key=lambda row: (row["brier"], -row["accuracy"]))
    return {
        "status": "OK",
        "classes": classes.tolist(),
        "best": best,
        "all": results,
    }


def main() -> None:
    files = locate_files()
    con = duckdb.connect()
    features, targets = schema_columns(con, files[FAMILIES[0]])
    for family in FAMILIES[1:]:
        current_features, current_targets = schema_columns(con, files[family])
        if current_features != features or current_targets != targets:
            raise RuntimeError(f"schema drift for {family}")

    support_by_family = {}
    train_parts = []
    dev_parts = []
    for family in FAMILIES:
        support_by_family[family] = full_support(con, files[family], targets)
        train_parts.append(
            deterministic_sample(con, files[family], "TRAIN", 80000)
        )
        dev_parts.append(
            deterministic_sample(con, files[family], "DEV", 50000)
        )
    con.close()
    train = pd.concat(train_parts, ignore_index=True)
    dev = pd.concat(dev_parts, ignore_index=True)

    x_train = numeric_matrix(train, features)
    x_dev = numeric_matrix(dev, features)
    rank_train = (
        train.groupby("family", sort=False, group_keys=False)
        .head(40000)
        .copy()
    )
    bucket_train = (
        train.groupby("family", sort=False, group_keys=False)
        .head(10000)
        .copy()
    )
    redundancy_train = (
        train.groupby("family", sort=False, group_keys=False)
        .head(5000)
        .copy()
    )
    x_rank = rank_matrix(rank_train, features)
    x_bucket = numeric_matrix(bucket_train, features)

    per_target: dict[str, list[dict[str, Any]]] = {}
    pooled_spearman = np.full((len(features), len(targets)), np.nan)

    for ti, target in enumerate(targets):
        label = label_for_target(target)
        train_valid = (
            train[label].notna().to_numpy()
            & (
                train[label].to_numpy()
                < train["train_end_timestamp"].to_numpy()
            )
        )
        y = pd.to_numeric(
            train[target],
            errors="coerce",
        ).to_numpy(dtype=float)
        y[~train_valid] = np.nan
        pearson, sample_n = corr_columns(x_train, y)

        rank_valid = (
            rank_train[label].notna().to_numpy()
            & (
                rank_train[label].to_numpy()
                < rank_train["train_end_timestamp"].to_numpy()
            )
        )
        yr = pd.to_numeric(rank_train[target], errors="coerce")
        yr[~rank_valid] = np.nan
        yrank = yr.rank(pct=True, method="average").to_numpy(dtype=float)
        spearman, _ = corr_columns(x_rank, yrank)
        pooled_spearman[:, ti] = spearman

        bucket_valid = (
            bucket_train[label].notna().to_numpy()
            & (
                bucket_train[label].to_numpy()
                < bucket_train["train_end_timestamp"].to_numpy()
            )
        )
        yb = pd.to_numeric(
            bucket_train[target],
            errors="coerce",
        ).to_numpy(dtype=float)
        yb[~bucket_valid] = np.nan
        direction, decile, effect, monotonicity = response_metrics(
            x_bucket,
            yb,
        )

        family_corrs = []
        for family in FAMILIES:
            idx = train["family"].to_numpy() == family
            fp, _ = corr_columns(x_train[idx], y[idx])
            family_corrs.append(fp)
        family_stack = np.vstack(family_corrs)
        pooled_sign = np.sign(pearson)
        valid_family = np.isfinite(family_stack)
        family_matches = (
            (np.sign(family_stack) == pooled_sign[None, :])
            & valid_family
        )
        family_denominator = valid_family.sum(axis=0)
        consistency = np.divide(
            family_matches.sum(axis=0),
            family_denominator,
            out=np.full(len(features), np.nan),
            where=family_denominator > 0,
        )

        hhi, max_share = market_concentration(train, target)
        rows = []
        for fi, name in enumerate(features):
            full_support_total = sum(
                support_by_family[f][target]["support"]
                for f in FAMILIES
            )
            full_markets = sum(
                support_by_family[f][target]["markets"]
                for f in FAMILIES
            )
            full_blocks = sum(
                support_by_family[f][target]["blocks"]
                for f in FAMILIES
            )
            rows.append(
                {
                    "feature": name,
                    "feature_group": feature_group(name),
                    "pearson": (
                        None
                        if not np.isfinite(pearson[fi])
                        else float(pearson[fi])
                    ),
                    "spearman": (
                        None
                        if not np.isfinite(spearman[fi])
                        else float(spearman[fi])
                    ),
                    "directional_response": (
                        None
                        if not np.isfinite(direction[fi])
                        else float(direction[fi])
                    ),
                    "decile_response": (
                        None
                        if not np.isfinite(decile[fi])
                        else float(decile[fi])
                    ),
                    "monotonicity": (
                        None
                        if not np.isfinite(monotonicity[fi])
                        else float(monotonicity[fi])
                    ),
                    "standardized_effect": (
                        None
                        if not np.isfinite(effect[fi])
                        else float(effect[fi])
                    ),
                    "sample_support": int(sample_n[fi]),
                    "full_target_support": int(full_support_total),
                    "full_market_count_sum": int(full_markets),
                    "full_time_block_count_sum": int(full_blocks),
                    "market_concentration_hhi_sample": hhi,
                    "max_market_share_sample": max_share,
                    "family_sign_consistency": (
                        None
                        if not np.isfinite(consistency[fi])
                        else float(consistency[fi])
                    ),
                }
            )
        per_target[target] = rows

    red_rank_frame = (
        redundancy_train[features]
        .apply(pd.to_numeric, errors="coerce")
        .rank(pct=True, method="average")
    )
    red_corr = red_rank_frame.corr(
        method="pearson",
        min_periods=100,
    ).to_numpy(dtype=np.float64)
    clusters = union_find_clusters(red_corr, features, 0.95)
    median_abs = np.nanmedian(np.abs(pooled_spearman), axis=1)
    representative: dict[str, str] = {}
    reps: set[str] = set()
    for cluster in clusters:
        scored = []
        for name in cluster:
            value = median_abs[features.index(name)]
            score = float(value) if np.isfinite(value) else -math.inf
            scored.append((-score, name))
        chosen = sorted(scored)[0][1]
        reps.add(chosen)
        for name in cluster:
            representative[name] = chosen

    dev_metrics: dict[str, dict[str, float | None]] = {}
    for target in targets:
        label = label_for_target(target)
        valid = (
            dev[label].notna().to_numpy()
            & (
                dev[label].to_numpy()
                < dev["dev_end_timestamp"].to_numpy()
            )
        )
        y = pd.to_numeric(
            dev[target],
            errors="coerce",
        ).to_numpy(dtype=float)
        y[~valid] = np.nan
        p, _ = corr_columns(x_dev, y)
        dev_metrics[target] = {
            features[i]: (
                None if not np.isfinite(p[i]) else float(p[i])
            )
            for i in range(len(features))
        }

    shortlist: dict[str, list[dict[str, Any]]] = {}
    model_selection: dict[str, dict[str, Any]] = {}
    train_model = (
        train.groupby("family", sort=False, group_keys=False)
        .head(30000)
        .copy()
    )
    dev_model = (
        dev.groupby("family", sort=False, group_keys=False)
        .head(20000)
        .copy()
    )

    for target in targets:
        rows = per_target[target]
        eligible = [
            row
            for row in rows
            if row["feature"] in reps
            and row["full_target_support"] >= 1000
            and row["full_market_count_sum"] >= 3
            and row["full_time_block_count_sum"] >= 4
            and row["spearman"] is not None
        ]
        eligible.sort(
            key=lambda row: (
                -abs(row["spearman"]),
                row["feature"],
            )
        )
        chosen = []
        target_label = label_for_target(target)
        target_train = pd.to_numeric(
            train[target],
            errors="coerce",
        ).to_numpy(dtype=float)
        target_valid = (
            train[target_label].notna().to_numpy()
            & (
                train[target_label].to_numpy()
                < train["train_end_timestamp"].to_numpy()
            )
        )
        target_train[~target_valid] = np.nan
        train_markets = train["market_id"].astype(str).to_numpy()
        for row in eligible[:8]:
            dev_r = dev_metrics[target].get(row["feature"])
            train_r = row["spearman"]
            feature_index = features.index(row["feature"])
            feature_hhi, feature_max_share = feature_market_concentration(
                train_markets,
                x_train[:, feature_index],
                target_train,
            )
            stable = (
                dev_r is not None
                and train_r * dev_r > 0
                and (
                    not np.isfinite(feature_max_share)
                    or feature_max_share <= 0.50
                )
            )
            label_name = "DISCOVERY_ONLY"
            if stable:
                label_name = (
                    "CROSS_FAMILY_CANDIDATE"
                    if (row["family_sign_consistency"] or 0) >= 0.8
                    else "WITHIN_FAMILY_STABLE"
                )
            chosen.append(
                {
                    **row,
                    "dev_pearson": dev_r,
                    "candidate_market_concentration_hhi_sample": (
                        None
                        if not np.isfinite(feature_hhi)
                        else feature_hhi
                    ),
                    "candidate_max_market_share_sample": (
                        None
                        if not np.isfinite(feature_max_share)
                        else feature_max_share
                    ),
                    "selection_label": label_name,
                    "stable_train_dev": stable,
                }
            )
        shortlist[target] = chosen[:12]

        model_features = [
            row["feature"]
            for row in chosen
            if row["stable_train_dev"]
            and abs(row["spearman"]) >= 0.01
        ][:12]
        if not model_eligible(target) or not model_features:
            model_selection[target] = {
                "status": "NO_MODEL_CANDIDATE",
                "features": model_features,
            }
            continue

        xtr = numeric_matrix(train_model, model_features)
        xdv = numeric_matrix(dev_model, model_features)
        ytr = pd.to_numeric(
            train_model[target],
            errors="coerce",
        ).to_numpy(dtype=float)
        ydv = pd.to_numeric(
            dev_model[target],
            errors="coerce",
        ).to_numpy(dtype=float)
        label = label_for_target(target)
        train_target_valid = (
            train_model[label].notna().to_numpy()
            & (
                train_model[label].to_numpy()
                < train_model["train_end_timestamp"].to_numpy()
            )
        )
        dev_target_valid = (
            dev_model[label].notna().to_numpy()
            & (
                dev_model[label].to_numpy()
                < dev_model["dev_end_timestamp"].to_numpy()
            )
        )
        ytr[~train_target_valid] = np.nan
        ydv[~dev_target_valid] = np.nan

        if "_sign_" in target:
            fitted = fit_classification_models(
                xtr,
                ytr,
                xdv,
                ydv,
            )
        else:
            fitted = fit_regression_models(
                xtr,
                ytr,
                xdv,
                ydv,
                model_features,
                target,
            )
        model_selection[target] = {
            "features": model_features,
            **fitted,
        }

    atlas_rows = []
    for target, rows in per_target.items():
        for row in rows:
            atlas_rows.append(
                {
                    "target": target,
                    **row,
                    "dev_pearson": dev_metrics[target].get(
                        row["feature"]
                    ),
                    "representative": representative[row["feature"]],
                }
            )
    pd.DataFrame(atlas_rows).to_csv(
        OUT / "train_feature_target_atlas.csv",
        index=False,
    )
    pd.DataFrame(
        [
            {
                "cluster_id": i,
                "representative": representative[cluster[0]],
                "feature": feature,
            }
            for i, cluster in enumerate(clusters)
            for feature in cluster
        ]
    ).to_csv(
        OUT / "feature_redundancy_clusters.csv",
        index=False,
    )

    freeze = {
        "schema_version": 1,
        "experiment_id": "EXPERIMENT-005B",
        "stage": "TRAIN_DEV_FREEZE",
        "screening_model_spec_sha256": sha256(SPEC_PATH),
        "feature_count": len(features),
        "target_count": len(targets),
        "redundancy_cluster_count": len(clusters),
        "representative_feature_count": len(reps),
        "shortlist": shortlist,
        "model_selection": model_selection,
        "holdout_touched": False,
    }
    freeze_path = OUT / "train_dev_shortlist_freeze.json"
    freeze_path.write_text(
        json.dumps(freeze, indent=2, sort_keys=True) + "\n"
    )
    summary = {
        "targets_with_stable_candidate": sum(
            any(row["stable_train_dev"] for row in rows)
            for rows in shortlist.values()
        ),
        "targets_with_model_candidate": sum(
            value.get("status") == "OK"
            for value in model_selection.values()
        ),
        "targets_without_model_candidate": sum(
            value.get("status") != "OK"
            for value in model_selection.values()
        ),
    }
    (OUT / "train_dev_summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n"
    )
    print(json.dumps(summary, sort_keys=True))


if __name__ == "__main__":
    main()
