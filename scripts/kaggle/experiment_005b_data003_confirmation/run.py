# ruff: noqa: E501
"""Fresh DATA-003 confirmation of the seven frozen 005B movement models.

Protocol was frozen at b42d01a32ed7c4cfa4c5ea24610920b346fe0aba
before any DATA-003 predictive target/performance evaluation.

DATA-003 is evaluation-only. Models and baseline regressions are fit solely on
the corrected historical 005B TRAIN+DEV sample using the exact frozen target
definitions, feature sets, model classes and hyperparameters.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LinearRegression, Ridge
from sklearn.metrics import mean_absolute_error, mean_squared_error
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

PROTOCOL_COMMIT = "b42d01a32ed7c4cfa4c5ea24610920b346fe0aba"
PARENT_VERDICT_COMMIT = "32b79983aa8cbbc544686aefdec8b89f96bea81d"
PARENT_FREEZE_SHA256 = (
    "44025aa2c5e5bddd97d88c81e649d86d6bf55d6cccec1d57c2aa7af12a9a7ec6"
)
SEED = 505005
BOOT_SEED = 505003
BOOT = 1000
EMBARGO = 300
HORIZONS = (1, 5, 15, 30, 60, 120, 300)
HISTORY_FAMILIES = ("US_2024", "CAN_2025", "COL_2026", "HUN_2026", "PER_2026")
OUT = Path("/kaggle/working/005b_data003_confirmation/evaluation")
OUT.mkdir(parents=True, exist_ok=True)
TMP = Path("/kaggle/working/005b_data003_confirmation/tmp")
TMP.mkdir(parents=True, exist_ok=True)

MODEL_SPECS: dict[int, dict[str, Any]] = {
    1: {
        "model": "hist_gradient_boosting",
        "params": {
            "learning_rate": 0.05,
            "max_iter": 100,
            "max_leaf_nodes": 15,
            "l2_regularization": 1.0,
        },
        "features": [
            "realised_vol_5",
            "absolute_return_5",
            "recent_max_move_900",
            "absolute_return_30",
            "absolute_return_15",
            "absolute_return_60",
            "absolute_return_120",
            "absolute_return_300",
        ],
    },
    5: {
        "model": "ridge",
        "params": {"alpha": 10.0},
        "features": [
            "realised_vol_5",
            "recent_max_move_900",
            "absolute_return_5",
            "absolute_return_30",
            "absolute_return_60",
            "absolute_return_15",
            "absolute_return_120",
            "absolute_return_300",
        ],
    },
    15: {
        "model": "hist_gradient_boosting",
        "params": {
            "learning_rate": 0.05,
            "max_iter": 100,
            "max_leaf_nodes": 15,
            "l2_regularization": 1.0,
        },
        "features": [
            "realised_vol_5",
            "recent_max_move_900",
            "absolute_return_300",
            "absolute_return_60",
            "absolute_return_120",
            "absolute_return_30",
            "absolute_return_15",
            "absolute_return_900",
        ],
    },
    30: {
        "model": "hist_gradient_boosting",
        "params": {
            "learning_rate": 0.05,
            "max_iter": 100,
            "max_leaf_nodes": 15,
            "l2_regularization": 1.0,
        },
        "features": [
            "recent_max_move_900",
            "realised_vol_5",
            "absolute_return_300",
            "absolute_return_120",
            "absolute_return_60",
            "absolute_return_900",
            "absolute_return_30",
            "absolute_return_15",
        ],
    },
    60: {
        "model": "hist_gradient_boosting",
        "params": {
            "learning_rate": 0.05,
            "max_iter": 100,
            "max_leaf_nodes": 15,
            "l2_regularization": 1.0,
        },
        "features": [
            "recent_max_move_900",
            "absolute_return_300",
            "realised_vol_5",
            "absolute_return_900",
            "absolute_return_120",
            "absolute_return_60",
            "absolute_return_30",
            "absolute_return_15",
        ],
    },
    120: {
        "model": "hist_gradient_boosting",
        "params": {
            "learning_rate": 0.05,
            "max_iter": 100,
            "max_leaf_nodes": 15,
            "l2_regularization": 1.0,
        },
        "features": [
            "recent_max_move_900",
            "absolute_return_300",
            "absolute_return_900",
            "absolute_return_120",
            "realised_vol_5",
            "absolute_return_60",
            "distance_recent_low_300",
            "absolute_return_30",
        ],
    },
    300: {
        "model": "hist_gradient_boosting",
        "params": {
            "learning_rate": 0.05,
            "max_iter": 100,
            "max_leaf_nodes": 15,
            "l2_regularization": 1.0,
        },
        "features": [
            "recent_max_move_900",
            "absolute_return_900",
            "absolute_return_300",
            "absolute_return_120",
            "absolute_return_60",
            "realised_vol_5",
            "distance_recent_low_300",
            "absolute_return_30",
        ],
    },
}

BASELINE_FEATURES = {
    "own_recent_price": "price_change_30",
    "current_absolute_movement": "absolute_return_30",
    "recent_activity": "trade_count_30",
}

HISTORY_SLUGS = {
    "US_2024": "005b-ordering-falsification-us-merge",
    "CAN_2025": "005b-ordering-falsification-features-can-2025",
    "COL_2026": "005b-ordering-falsification-features-col-2026",
    "HUN_2026": "005b-ordering-falsification-features-hun-2026",
    "PER_2026": "005b-ordering-falsification-features-per-2026",
}


def qpath(path: Path) -> str:
    return str(path).replace("'", "''")


def locate_unique(name: str, fragment: str) -> Path:
    matches = [
        path for path in Path("/kaggle/input").rglob(name)
        if fragment in str(path)
    ]
    if len(matches) != 1:
        raise RuntimeError(
            f"expected one {name} under {fragment}, found {matches}"
        )
    return matches[0]


def locate_history(family: str) -> Path:
    return locate_unique(
        f"feature_target_{family}.parquet",
        HISTORY_SLUGS[family],
    )


def connect() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    con.execute("PRAGMA threads=4")
    con.execute("PRAGMA preserve_insertion_order=false")
    escaped = qpath(TMP)
    con.execute(f"PRAGMA temp_directory='{escaped}'")
    return con


def build_data003_matrix() -> Path:
    economic = locate_unique(
        "economic_fills_DATA003.parquet",
        "005b-data003-block-gate",
    )
    tx_block = locate_unique(
        "tx_block_DATA003.parquet",
        "005b-data003-block-gate",
    )
    block_ts = locate_unique(
        "block_timestamp.parquet",
        "005b-data003-block-gate",
    )
    output = OUT / "data003_movement_matrix.parquet"
    con = connect()
    eq = qpath(economic)
    tq = qpath(tx_block)
    bq = qpath(block_ts)
    oq = qpath(output)

    feature_windows = (5, 15, 30, 60, 120, 300, 900)
    raw_feature_expr: list[str] = []
    for seconds in feature_windows:
        span = seconds * 1_000_000
        frame = (
            "PARTITION BY condition_id ORDER BY market_order_us "
            f"RANGE BETWEEN {span} PRECEDING AND CURRENT ROW"
        )
        raw_feature_expr.extend(
            [
                (
                    f"p_yes-FIRST_VALUE(p_yes) OVER ({frame}) "
                    f"AS price_change_{seconds}"
                ),
                (
                    f"SQRT(SUM(POWER(one_trade_change,2)) OVER ({frame})) "
                    f"AS realised_vol_{seconds}"
                ),
                (
                    f"ABS(p_yes-FIRST_VALUE(p_yes) OVER ({frame})) "
                    f"AS absolute_return_{seconds}"
                ),
                (
                    f"MAX(ABS(one_trade_change)) OVER ({frame}) "
                    f"AS recent_max_move_{seconds}"
                ),
                (
                    f"COUNT(*) OVER ({frame}) "
                    f"AS trade_count_{seconds}"
                ),
            ]
        )
    low_frame = (
        "PARTITION BY condition_id ORDER BY market_order_us "
        "RANGE BETWEEN 300000000 PRECEDING AND CURRENT ROW"
    )
    raw_feature_expr.append(
        f"p_yes-MIN(p_yes) OVER ({low_frame}) AS distance_recent_low_300"
    )

    target_raw: list[str] = []
    target_final: list[str] = []
    for seconds in HORIZONS:
        span = seconds * 1_000_000
        future = (
            "PARTITION BY condition_id ORDER BY market_order_us "
            f"RANGE BETWEEN 1 FOLLOWING AND {span} FOLLOWING"
        )
        target_raw.extend(
            [
                f"COUNT(*) OVER ({future}) AS future_count_{seconds}",
                (
                    f"SQRT(SUM(POWER(one_trade_change,2)) OVER ({future})) "
                    f"AS future_realised_{seconds}"
                ),
                (
                    f"MAX(timestamp) OVER ({future}) "
                    f"AS clock_label_end_{seconds}"
                ),
            ]
        )
        target_final.extend(
            [
                (
                    f"CASE WHEN future_count_{seconds}>0 "
                    f"THEN future_realised_{seconds} END "
                    f"AS target_clock_realised_movement_{seconds}"
                ),
                f"clock_label_end_{seconds}",
            ]
        )

    feature_sql = ",\n                ".join(raw_feature_expr)
    target_raw_sql = ",\n                ".join(target_raw)
    target_final_sql = ",\n            ".join(target_final)

    con.execute(
        f"""
        COPY (
          WITH joined AS (
            SELECT
              e.*,
              t.block_number,
              b.block_timestamp
            FROM read_parquet('{eq}') AS e
            INNER JOIN read_parquet('{tq}') AS t
              ON LOWER(CAST(e.tx_hash AS VARCHAR))=t.tx_hash
            INNER JOIN read_parquet('{bq}') AS b
              USING (block_number)
          ),
          ordered AS (
            SELECT
              *,
              ROW_NUMBER() OVER (
                ORDER BY
                  block_number,
                  log_index,
                  tx_hash,
                  condition_id
              ) AS row_id,
              CAST(timestamp AS BIGINT)*1000000
                + ROW_NUMBER() OVER (
                    PARTITION BY condition_id, timestamp
                    ORDER BY
                      block_number,
                      log_index,
                      tx_hash
                  ) - 1 AS market_order_us
            FROM joined
          ),
          deltaed AS (
            SELECT
              *,
              p_yes-LAG(p_yes) OVER (
                PARTITION BY condition_id
                ORDER BY market_order_us
              ) AS one_trade_change
            FROM ordered
          ),
          features AS (
            SELECT
              *,
              {feature_sql}
            FROM deltaed
          ),
          targets_raw AS (
            SELECT
              *,
              {target_raw_sql}
            FROM features
          )
          SELECT
            row_id,
            condition_id,
            sig_market_id,
            mapping_class,
            mapping_direction,
            timestamp,
            block_number,
            tx_hash,
            log_index,
            market_order_us,
            p_yes,
            {", ".join(sorted(set(
                feature
                for spec in MODEL_SPECS.values()
                for feature in spec["features"]
            ) | set(BASELINE_FEATURES.values())))},
            {target_final_sql}
          FROM targets_raw
          ORDER BY row_id
        )
        TO '{oq}' (
          FORMAT PARQUET,
          COMPRESSION ZSTD,
          ROW_GROUP_SIZE 100000
        )
        """
    )

    rows = int(
        con.execute(
            f"SELECT COUNT(*) FROM read_parquet('{oq}')"
        ).fetchone()[0]
    )
    source_rows = int(
        con.execute(
            f"SELECT COUNT(*) FROM read_parquet('{eq}')"
        ).fetchone()[0]
    )
    con.close()
    if rows != source_rows:
        raise RuntimeError(
            f"DATA-003 matrix rows {rows} != economic rows {source_rows}"
        )
    return output


def history_frame(
    target: str,
    label: str,
    columns: list[str],
) -> pd.DataFrame:
    parts: list[pd.DataFrame] = []
    selected = sorted(
        set(
            [
                "family",
                "condition_id",
                "tx_hash",
                "log_index",
                "timestamp",
                "dev_end_timestamp",
                target,
                label,
            ]
            + columns
            + list(BASELINE_FEATURES.values())
        )
    )
    column_sql = ",".join(selected)
    for family in HISTORY_FAMILIES:
        path = locate_history(family)
        q = qpath(path)
        con = duckdb.connect()
        frame = con.execute(
            f"""
            SELECT {column_sql}
            FROM read_parquet('{q}')
            WHERE timestamp < dev_end_timestamp-{EMBARGO}
              AND {target} IS NOT NULL
              AND {label} IS NOT NULL
              AND {label} < dev_end_timestamp
            ORDER BY hash(condition_id,tx_hash,log_index)
            LIMIT 100000
            """
        ).df()
        con.close()
        frame["family"] = family
        parts.append(frame)
    return pd.concat(parts, ignore_index=True)


def matrix(df: pd.DataFrame, features: list[str]) -> np.ndarray:
    return (
        df[features]
        .apply(pd.to_numeric, errors="coerce")
        .to_numpy(dtype=np.float64, copy=False)
    )


def model_for(spec: dict[str, Any]) -> Any:
    if spec["model"] == "ridge":
        return make_pipeline(
            SimpleImputer(strategy="median"),
            StandardScaler(),
            Ridge(alpha=float(spec["params"]["alpha"])),
        )
    if spec["model"] == "hist_gradient_boosting":
        params = spec["params"]
        return make_pipeline(
            SimpleImputer(strategy="median"),
            HistGradientBoostingRegressor(
                learning_rate=float(params["learning_rate"]),
                max_iter=int(params["max_iter"]),
                max_leaf_nodes=int(params["max_leaf_nodes"]),
                l2_regularization=float(
                    params["l2_regularization"]
                ),
                random_state=SEED,
            ),
        )
    raise ValueError(spec["model"])


def linear_baseline(
    train: pd.DataFrame,
    y: np.ndarray,
    test: pd.DataFrame,
    feature: str,
) -> np.ndarray:
    model = make_pipeline(
        SimpleImputer(strategy="median"),
        StandardScaler(),
        LinearRegression(),
    )
    model.fit(matrix(train, [feature]), y)
    return model.predict(matrix(test, [feature]))


def pearson(x: np.ndarray, y: np.ndarray) -> float:
    valid = np.isfinite(x) & np.isfinite(y)
    if int(valid.sum()) < 3:
        return math.nan
    xv = x[valid]
    yv = y[valid]
    if np.std(xv) <= 0 or np.std(yv) <= 0:
        return math.nan
    return float(np.corrcoef(xv, yv)[0, 1])


def condition_bootstrap(
    conditions: np.ndarray,
    y: np.ndarray,
    pred: np.ndarray,
    persistence: np.ndarray,
) -> dict[str, float | int]:
    frame = pd.DataFrame(
        {
            "condition_id": conditions.astype(str),
            "diff": (
                np.abs(y-persistence)
                - np.abs(y-pred)
            ),
        }
    )
    per_condition = (
        frame.groupby("condition_id", as_index=False)["diff"]
        .mean()
        .sort_values("condition_id")
    )
    values = per_condition["diff"].to_numpy(dtype=float)
    if not len(values):
        raise RuntimeError("no conditions for bootstrap")
    rng = np.random.default_rng(BOOT_SEED)
    reps = np.empty(BOOT, dtype=float)
    for i in range(BOOT):
        sample = values[
            rng.integers(0, len(values), size=len(values))
        ]
        reps[i] = float(np.mean(sample))
    return {
        "conditions": int(len(values)),
        "observed_equal_weight_condition_mean": float(
            np.mean(values)
        ),
        "bootstrap_mean": float(np.mean(reps)),
        "lower_2_5": float(np.quantile(reps, 0.025)),
        "upper_97_5": float(np.quantile(reps, 0.975)),
        "repetitions": BOOT,
        "seed": BOOT_SEED,
    }


def mapping_class_diagnostics(
    frame: pd.DataFrame,
    y: np.ndarray,
    pred: np.ndarray,
    persistence: np.ndarray,
) -> dict[str, Any]:
    output: dict[str, Any] = {}
    classes = frame["mapping_class"].astype(str).to_numpy()
    for mapping_class in ("EXACT", "DERIVED", "NEAR"):
        mask = classes == mapping_class
        if not mask.any():
            output[mapping_class] = {"rows": 0}
            continue
        p_mae = float(mean_absolute_error(y[mask], pred[mask]))
        b_mae = float(
            mean_absolute_error(y[mask], persistence[mask])
        )
        output[mapping_class] = {
            "rows": int(mask.sum()),
            "conditions": int(
                frame.loc[mask, "condition_id"].nunique()
            ),
            "model_mae": p_mae,
            "persistence_mae": b_mae,
            "mae_improvement_vs_persistence": (
                (b_mae-p_mae)/b_mae if b_mae else 0.0
            ),
        }
    return output


def evaluate_horizon(
    horizon: int,
    data003_path: Path,
) -> dict[str, Any]:
    spec = MODEL_SPECS[horizon]
    target = f"target_clock_realised_movement_{horizon}"
    label = f"clock_label_end_{horizon}"
    train = history_frame(
        target,
        label,
        list(spec["features"]),
    )
    y_train = pd.to_numeric(
        train[target],
        errors="coerce",
    ).to_numpy(dtype=float)
    valid_train = np.isfinite(y_train)
    train = train.loc[valid_train].reset_index(drop=True)
    y_train = y_train[valid_train]

    q = qpath(data003_path)
    needed = sorted(
        set(
            [
                "condition_id",
                "sig_market_id",
                "mapping_class",
                "mapping_direction",
                "timestamp",
                "block_number",
                "tx_hash",
                "log_index",
                target,
                label,
            ]
            + list(spec["features"])
            + list(BASELINE_FEATURES.values())
        )
    )
    con = duckdb.connect()
    test = con.execute(
        f"""
        SELECT {",".join(needed)}
        FROM read_parquet('{q}')
        WHERE {target} IS NOT NULL
          AND {label} IS NOT NULL
        ORDER BY block_number,log_index,tx_hash
        """
    ).df()
    con.close()
    y = pd.to_numeric(
        test[target],
        errors="coerce",
    ).to_numpy(dtype=float)
    valid = np.isfinite(y)
    test = test.loc[valid].reset_index(drop=True)
    y = y[valid]
    if len(y) < 100:
        raise RuntimeError(
            f"{target}: insufficient DATA-003 support {len(y)}"
        )

    model = model_for(spec)
    model.fit(matrix(train, spec["features"]), y_train)
    pred = model.predict(matrix(test, spec["features"]))

    persistence_value = float(np.mean(y_train))
    persistence = np.full(len(y), persistence_value)
    baselines = {
        "persistence": persistence,
    }
    for name, feature in BASELINE_FEATURES.items():
        baselines[name] = linear_baseline(
            train,
            y_train,
            test,
            feature,
        )

    model_mae = float(mean_absolute_error(y, pred))
    model_mse = float(mean_squared_error(y, pred))
    baseline_metrics: dict[str, Any] = {}
    for name, values in baselines.items():
        baseline_metrics[name] = {
            "mae": float(mean_absolute_error(y, values)),
            "mse": float(mean_squared_error(y, values)),
            "predictive_ic": pearson(values, y),
        }
    beats_all = all(
        model_mae < float(row["mae"])
        for row in baseline_metrics.values()
    )
    bootstrap = condition_bootstrap(
        test["condition_id"].astype(str).to_numpy(),
        y,
        pred,
        persistence,
    )
    lower_positive = float(bootstrap["lower_2_5"]) > 0
    passed = beats_all and lower_positive

    return {
        "target": target,
        "horizon_seconds": horizon,
        "historical_fit_rows": int(len(train)),
        "historical_fit_family_counts": {
            str(k): int(v)
            for k, v in train["family"].value_counts().sort_index().items()
        },
        "data003_rows": int(len(test)),
        "data003_conditions": int(
            test["condition_id"].nunique()
        ),
        "data003_sig_markets": int(
            test["sig_market_id"].nunique()
        ),
        "frozen_model": spec,
        "model_metrics": {
            "mae": model_mae,
            "mse": model_mse,
            "predictive_ic": pearson(pred, y),
        },
        "baseline_metrics": baseline_metrics,
        "beats_every_frozen_baseline_on_mae": beats_all,
        "condition_cluster_bootstrap_vs_persistence": bootstrap,
        "bootstrap_lower_2_5_positive": lower_positive,
        "mapping_class_diagnostics": mapping_class_diagnostics(
            test,
            y,
            pred,
            persistence,
        ),
        "fresh_confirmation_pass": passed,
    }


def main() -> None:
    gate_report = locate_unique(
        "data003_block_gate_report.json",
        "005b-data003-block-gate",
    )
    gate = json.loads(gate_report.read_text())
    if gate.get("all_hard_gates_pass") is not True:
        raise RuntimeError("DATA-003 block-number hard gate did not pass")
    if gate.get("protocol_commit") != PROTOCOL_COMMIT:
        raise RuntimeError("block gate is not bound to frozen protocol")
    if gate.get("predictive_outcomes_accessed") is not False:
        raise RuntimeError("block gate accessed predictive outcomes")

    data003 = build_data003_matrix()
    results = []
    for horizon in HORIZONS:
        print(f"CONFIRM {horizon}s", flush=True)
        results.append(evaluate_horizon(horizon, data003))

    passed = sum(
        row["fresh_confirmation_pass"]
        for row in results
    )
    full = passed == len(HORIZONS)
    payload = {
        "schema_version": 1,
        "experiment_id": "EXPERIMENT-005B-DATA003-CONFIRMATION",
        "classification": "FRESH_CONFIRMATION_ONLY",
        "protocol_commit": PROTOCOL_COMMIT,
        "parent_verdict_commit": PARENT_VERDICT_COMMIT,
        "parent_train_dev_freeze_sha256": PARENT_FREEZE_SHA256,
        "data003_dataset": "polyleviathan/sig-cup-data-003-sig-actual-fills",
        "data003_training_rows": 0,
        "new_candidates_introduced": 0,
        "horizons": list(HORIZONS),
        "results": results,
        "horizons_passing_frozen_confirmation": int(passed),
        "horizon_count": len(HORIZONS),
        "full_movement_transfer": full,
        "disposition": (
            "MOVEMENT_HYPOTHESIS_FRESHLY_SUPPORTED_ON_DATA_003"
            if full
            else "MOVEMENT_HYPOTHESIS_PARTIAL_OR_FAILED_TRANSFER_TO_DATA_003"
        ),
        "interpretation_constraint": (
            "This confirms only the preregistered historical movement hypothesis "
            "on a disjoint mapped-universe dataset. It is not an execution-alpha "
            "claim and does not justify new candidates or tuning."
        ),
    }
    path = OUT / "data003_confirmation_results.json"
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "horizons_passing": passed,
                "horizon_count": len(HORIZONS),
                "full_movement_transfer": full,
                "disposition": payload["disposition"],
            },
            sort_keys=True,
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
