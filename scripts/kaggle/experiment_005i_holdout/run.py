# ruff: noqa: E501,I001
from __future__ import annotations

import argparse
import json
import math
import shutil
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from scripts.kaggle.experiment_005i_regimes.run import state_labels  # noqa: E402
from scripts.kaggle.experiment_005i_v3_worker.run import (  # noqa: E402
    aggregate_hour,
    stratified_sample,
)

OFI_FREEZE = ROOT / "data/experiments/experiment_005i/OFI_TRAIN_MODEL_FREEZE.json"
REGIME_FREEZE = ROOT / "data/experiments/experiment_005i/REGIME_DEFINITION_FREEZE.json"


def run(args: list[str]) -> None:
    print("+", " ".join(args), flush=True)
    subprocess.run(args, check=True)


def sigmoid(values: np.ndarray) -> np.ndarray:
    values = np.clip(values, -30.0, 30.0)
    return 1.0 / (1.0 + np.exp(-values))


def frozen_probability(frame: pd.DataFrame, model: dict[str, Any]) -> np.ndarray:
    features = list(model["features"])
    medians = np.asarray([model["medians"][name] for name in features], dtype=float)
    means = np.asarray(model["scaler_mean"], dtype=float)
    scales = np.asarray(model["scaler_scale"], dtype=float)
    coef = np.asarray(model["coef"], dtype=float)
    intercept = float(model["intercept"])
    values = frame[features].to_numpy(dtype=float)
    for j in range(values.shape[1]):
        bad = ~np.isfinite(values[:, j])
        values[bad, j] = medians[j]
    z = (values - means) / scales
    return sigmoid(z @ coef + intercept)


def loss_sum(y: np.ndarray, p: np.ndarray) -> float:
    p = np.clip(p, 1e-12, 1.0 - 1e-12)
    return float(-(y * np.log(p) + (1 - y) * np.log(1 - p)).sum())


def auc(y: list[int], p: list[float]) -> float | None:
    if len(set(y)) < 2:
        return None
    order = np.argsort(np.asarray(p))
    ranks = np.empty(len(order), dtype=float)
    ranks[order] = np.arange(1, len(order) + 1, dtype=float)
    y_arr = np.asarray(y, dtype=int)
    n1 = int(y_arr.sum())
    n0 = len(y_arr) - n1
    if n0 == 0 or n1 == 0:
        return None
    rank_sum = float(ranks[y_arr == 1].sum())
    return (rank_sum - n1 * (n1 + 1) / 2.0) / (n1 * n0)


def price_region(mid: float) -> str:
    if mid <= 0.10:
        return "P00_10"
    if mid <= 0.20:
        return "P10_20"
    if mid <= 0.40:
        return "P20_40"
    if mid <= 0.60:
        return "P40_60"
    if mid <= 0.80:
        return "P60_80"
    if mid <= 0.90:
        return "P80_90"
    return "P90_100"


def move_bucket(value: float) -> str:
    x = abs(value)
    if x <= 0.001:
        return "LE_0.1c"
    if x <= 0.002:
        return "0.1_0.2c"
    if x <= 0.005:
        return "0.2_0.5c"
    if x <= 0.010:
        return "0.5_1c"
    if x <= 0.020:
        return "1_2c"
    if x <= 0.050:
        return "2_5c"
    return "GT_5c"


def rel_spread_bucket(value: float) -> str:
    if value <= 0.01:
        return "RS_LE_0.01"
    if value <= 0.025:
        return "RS_0.01_0.025"
    if value <= 0.065:
        return "RS_0.025_0.065"
    if value <= 0.25:
        return "RS_0.065_0.25"
    if value <= 0.91:
        return "RS_0.25_0.91"
    return "RS_GT_0.91"


def add_reversal(
    frame: pd.DataFrame,
    counter_n: Counter[str],
    counter_rev: Counter[str],
    prefix: str,
) -> None:
    rows = frame[
        frame["ret_5m"].notna()
        & frame["future_ret_5m"].notna()
        & frame["ret_5m"].ne(0)
        & frame["future_ret_5m"].ne(0)
    ]
    for row in rows.itertuples(index=False):
        reversed_move = np.sign(row.ret_5m) != np.sign(row.future_ret_5m)
        keys = [
            f"{prefix}|ALL",
            f"{prefix}|PRICE|{price_region(float(row.mid))}",
            f"{prefix}|MOVE|{move_bucket(float(row.ret_5m))}",
        ]
        if pd.notna(row.relative_spread):
            keys.append(f"{prefix}|RELSPREAD|{rel_spread_bucket(float(row.relative_spread))}")
        if hasattr(row, "state"):
            keys.append(f"{prefix}|STATE|{row.state}")
        for key in keys:
            counter_n[key] += 1
            if reversed_move:
                counter_rev[key] += 1


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--spec", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()

    spec = json.loads(Path(args.spec).read_text(encoding="utf-8"))
    if str(spec.get("surface", "")).upper() != "HOLDOUT":
        raise RuntimeError("005I confirmation worker is HOLDOUT-only")
    dataset = str(spec["dataset"])
    files = [str(x) for x in spec["files"]]
    worker_id = str(spec["worker_id"])
    sample_per_hour = int(spec.get("sample_per_hour", 3000))
    seed = int(spec.get("seed", 705009))

    ofi_freeze = json.loads(OFI_FREEZE.read_text(encoding="utf-8"))
    regime_freeze = json.loads(REGIME_FREEZE.read_text(encoding="utf-8"))
    thresholds = {k: float(v) for k, v in regime_freeze["thresholds"].items()}
    baseline_model = ofi_freeze["models"]["baseline"]
    challenger_model = ofi_freeze["models"]["ofi_challenger"]

    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    temp = out / "_downloads"
    temp.mkdir(parents=True, exist_ok=True)

    carry: dict[Any, dict[str, Any]] = {}
    tail = pd.DataFrame()
    failures: list[dict[str, str]] = []
    files_completed = 0

    pred = {
        "sample_n": 0,
        "sample_base_loss": 0.0,
        "sample_ofi_loss": 0.0,
        "full_n": 0,
        "full_base_loss": 0.0,
        "full_ofi_loss": 0.0,
    }
    sample_y: list[int] = []
    sample_pb: list[float] = []
    sample_po: list[float] = []

    reversal_n: Counter[str] = Counter()
    reversal_rev: Counter[str] = Counter()
    occupancy: Counter[str] = Counter()
    transitions: Counter[str] = Counter()
    exits: Counter[str] = Counter()
    dwell: dict[str, list[int]] = defaultdict(list)
    last_state: dict[str, str] = {}
    run_state: dict[str, str] = {}
    run_len: dict[str, int] = {}
    state_ofi_same: Counter[str] = Counter()
    state_ofi_n: Counter[str] = Counter()
    state_future_abs_sum: Counter[str] = Counter()
    state_future_abs_n: Counter[str] = Counter()

    for i, file_name in enumerate(files):
        file_dir = temp / f"{i:04d}"
        file_dir.mkdir(parents=True, exist_ok=True)
        try:
            run(
                [
                    "kaggle",
                    "datasets",
                    "download",
                    dataset,
                    "-f",
                    file_name,
                    "-p",
                    str(file_dir),
                    "--unzip",
                ]
            )
            parquets = sorted(file_dir.rglob("*.parquet"))
            if len(parquets) != 1:
                raise RuntimeError(f"expected one parquet, found {len(parquets)}")
            panel, _, carry, tail, _ = aggregate_hour(parquets[0], carry, tail)
            files_completed += 1
            if panel.empty:
                continue
            panel = panel.sort_values(["asset_id", "minute"]).copy()
            panel["state"] = state_labels(panel, thresholds)

            full_pred = panel[
                panel["future_ret_1m"].notna() & panel["future_ret_1m"].ne(0)
            ].copy()
            if not full_pred.empty:
                y = (full_pred["future_ret_1m"] > 0).astype(int).to_numpy()
                pb = frozen_probability(full_pred, baseline_model)
                po = frozen_probability(full_pred, challenger_model)
                pred["full_n"] += len(y)
                pred["full_base_loss"] += loss_sum(y, pb)
                pred["full_ofi_loss"] += loss_sum(y, po)

            sampled = stratified_sample(panel, sample_per_hour, seed + i * 17)
            sample_pred = sampled[
                sampled["future_ret_1m"].notna() & sampled["future_ret_1m"].ne(0)
            ].copy()
            if not sample_pred.empty:
                y = (sample_pred["future_ret_1m"] > 0).astype(int).to_numpy()
                pb = frozen_probability(sample_pred, baseline_model)
                po = frozen_probability(sample_pred, challenger_model)
                pred["sample_n"] += len(y)
                pred["sample_base_loss"] += loss_sum(y, pb)
                pred["sample_ofi_loss"] += loss_sum(y, po)
                sample_y.extend(y.astype(int).tolist())
                sample_pb.extend(pb.astype(float).tolist())
                sample_po.extend(po.astype(float).tolist())

            add_reversal(panel, reversal_n, reversal_rev, "FULL")
            add_reversal(sampled, reversal_n, reversal_rev, "SAMPLE")

            trusted = panel[panel["state"].ne("UNTRUSTED")]
            for state, n in trusted["state"].value_counts().items():
                occupancy[str(state)] += int(n)

            for asset, group in trusted.groupby("asset_id", sort=False):
                key = str(asset)
                previous = last_state.get(key)
                for row in group.itertuples(index=False):
                    state = str(row.state)
                    if previous is None:
                        run_state[key] = state
                        run_len[key] = 1
                    elif state == previous:
                        run_len[key] = run_len.get(key, 0) + 1
                    else:
                        transitions[f"{previous}->{state}"] += 1
                        exits[previous] += 1
                        if run_state.get(key) == previous:
                            dwell[previous].append(run_len.get(key, 0))
                        run_state[key] = state
                        run_len[key] = 1
                    if (
                        pd.notna(row.ofi_depth_norm)
                        and pd.notna(row.future_ret_1m)
                        and row.ofi_depth_norm != 0
                        and row.future_ret_1m != 0
                    ):
                        state_ofi_n[state] += 1
                        if np.sign(row.ofi_depth_norm) == np.sign(row.future_ret_1m):
                            state_ofi_same[state] += 1
                    if pd.notna(row.future_abs_5m):
                        state_future_abs_sum[state] += float(row.future_abs_5m)
                        state_future_abs_n[state] += 1
                    previous = state
                if previous is not None:
                    last_state[key] = previous

            print(
                f"005I_HOLDOUT worker={worker_id} {i + 1}/{len(files)} "
                f"panel={len(panel)} sample={len(sampled)}",
                flush=True,
            )
        except Exception as exc:
            failures.append({"file": file_name, "error": f"{type(exc).__name__}: {exc}"})
            print(f"005I_HOLDOUT_ERROR file={file_name} error={exc}", flush=True)
        finally:
            shutil.rmtree(file_dir, ignore_errors=True)

    for key, state in run_state.items():
        dwell[state].append(run_len.get(key, 0))

    sample_base_ll = pred["sample_base_loss"] / pred["sample_n"] if pred["sample_n"] else None
    sample_ofi_ll = pred["sample_ofi_loss"] / pred["sample_n"] if pred["sample_n"] else None
    full_base_ll = pred["full_base_loss"] / pred["full_n"] if pred["full_n"] else None
    full_ofi_ll = pred["full_ofi_loss"] / pred["full_n"] if pred["full_n"] else None

    reversal_rows = []
    for key in sorted(reversal_n):
        parts = key.split("|")
        reversal_rows.append(
            {
                "surface": parts[0],
                "slice_type": parts[1] if len(parts) > 1 else "ALL",
                "slice": parts[2] if len(parts) > 2 else "ALL",
                "n": int(reversal_n[key]),
                "reversal_rate": float(reversal_rev[key] / reversal_n[key]),
            }
        )

    regime_rows = []
    for state in sorted(occupancy):
        lengths = np.asarray(dwell.get(state, []), dtype=float)
        regime_rows.append(
            {
                "state": state,
                "occupancy_minutes": int(occupancy[state]),
                "exits": int(exits[state]),
                "exit_hazard_per_minute": (
                    float(exits[state] / occupancy[state]) if occupancy[state] else None
                ),
                "dwell_n": int(len(lengths)),
                "dwell_median_min": float(np.median(lengths)) if len(lengths) else None,
                "dwell_p90_min": float(np.quantile(lengths, 0.9)) if len(lengths) else None,
                "ofi_sign_agreement": (
                    float(state_ofi_same[state] / state_ofi_n[state])
                    if state_ofi_n[state]
                    else None
                ),
                "ofi_sign_n": int(state_ofi_n[state]),
                "future_abs_5m_mean": (
                    float(state_future_abs_sum[state] / state_future_abs_n[state])
                    if state_future_abs_n[state]
                    else None
                ),
            }
        )

    transition_rows = []
    for key, n in transitions.items():
        source, target = key.split("->", 1)
        transition_rows.append(
            {
                "from_state": source,
                "to_state": target,
                "count": int(n),
                "share_of_source_exits": float(n / exits[source]) if exits[source] else None,
            }
        )

    evidence = {
        "schema_version": 1,
        "experiment": "EXPERIMENT-005I",
        "worker_id": worker_id,
        "surface": "HOLDOUT",
        "files_requested": len(files),
        "files_completed": files_completed,
        "files_failed": len(failures),
        "failures": failures,
        "predictive_confirmation": {
            "sample_n": pred["sample_n"],
            "sample_baseline_logloss": sample_base_ll,
            "sample_ofi_logloss": sample_ofi_ll,
            "sample_logloss_improvement": (
                sample_base_ll - sample_ofi_ll
                if sample_base_ll is not None and sample_ofi_ll is not None
                else None
            ),
            "sample_baseline_auc": auc(sample_y, sample_pb),
            "sample_ofi_auc": auc(sample_y, sample_po),
            "full_n": pred["full_n"],
            "full_baseline_logloss": full_base_ll,
            "full_ofi_logloss": full_ofi_ll,
            "full_logloss_improvement": (
                full_base_ll - full_ofi_ll
                if full_base_ll is not None and full_ofi_ll is not None
                else None
            ),
        },
        "transition_count": int(sum(transitions.values())),
        "holdout_read": True,
        "make_modified": False,
        "real_sig_orders_sent": False,
    }
    (out / "HOLDOUT_EVIDENCE.json").write_text(
        json.dumps(evidence, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    pq.write_table(pa.Table.from_pylist(reversal_rows), out / "HOLDOUT_REVERSAL.parquet")
    pq.write_table(pa.Table.from_pylist(regime_rows), out / "HOLDOUT_REGIMES.parquet")
    pq.write_table(pa.Table.from_pylist(transition_rows), out / "HOLDOUT_TRANSITIONS.parquet")
    shutil.rmtree(temp, ignore_errors=True)


if __name__ == "__main__":
    main()
