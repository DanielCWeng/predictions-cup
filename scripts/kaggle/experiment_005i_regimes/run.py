# ruff: noqa: E501,I001
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from scripts.kaggle.experiment_005i_v3_worker.run import aggregate_hour  # noqa: E402


def run(args: list[str]) -> None:
    print("+", " ".join(args), flush=True)
    subprocess.run(args, check=True)


def safe_mean(total: float, n: int) -> float | None:
    return None if n <= 0 else total / n


def state_labels(panel: pd.DataFrame, t: dict[str, float]) -> pd.Series:
    labels = np.full(len(panel), "OTHER", dtype=object)
    valid = panel["mid"].notna() & panel["depth_trusted"].fillna(0).gt(0)

    conditions = [
        (
            panel["relative_spread"].ge(t["liquidity_stress_relative_spread"])
            & panel["depth_total5"].le(t["liquidity_stress_depth_total5_max"])
        ),
        (
            panel["ret_5m"].abs().ge(t["discovery_abs_ret_5m"])
            & panel["quote_events"].ge(t["active_quote_events"])
        ),
        panel["ret_5m"].abs().ge(t["discovery_abs_ret_5m"]),
        (
            panel["ofi_depth_norm"].abs().ge(t["directional_abs_ofi_depth_norm"])
            & panel["quote_events"].ge(6)
        ),
        panel["replenishment_1m"].ge(t["replenishment_1m"]),
        (
            panel["age_quote_s"].ge(t["stale_age_quote_s"])
            & panel["quote_events"].le(t["quiet_quote_events_max"])
        ),
        panel["quote_events"].ge(t["active_quote_events"]),
        (
            panel["quote_events"].le(t["quiet_quote_events_max"])
            & panel["rv_5m"].le(t["quiet_rv_5m_max"])
        ),
    ]
    names = [
        "LIQUIDITY_STRESS",
        "PRICE_DISCOVERY",
        "POST_SHOCK",
        "DIRECTIONAL_PRESSURE",
        "REPLENISHMENT",
        "STALE",
        "ACTIVE",
        "QUIET",
    ]
    remaining = valid.copy()
    for cond, name in zip(conditions, names, strict=True):
        hit = remaining & cond.fillna(False)
        labels[hit.to_numpy()] = name
        remaining &= ~hit
    labels[~valid.to_numpy()] = "UNTRUSTED"
    return pd.Series(labels, index=panel.index, dtype="string")


def region(mid: float) -> str:
    if mid <= 0.1:
        return "P00_10"
    if mid <= 0.2:
        return "P10_20"
    if mid <= 0.4:
        return "P20_40"
    if mid <= 0.6:
        return "P40_60"
    if mid <= 0.8:
        return "P60_80"
    if mid <= 0.9:
        return "P80_90"
    return "P90_100"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--spec", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()

    spec = json.loads(Path(args.spec).read_text(encoding="utf-8"))
    dataset = str(spec["dataset"])
    files = [str(x) for x in spec["files"]]
    worker_id = str(spec["worker_id"])
    surface = str(spec["surface"])
    t = {k: float(v) for k, v in spec["thresholds"].items()}

    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    temp = out / "_downloads"
    temp.mkdir(parents=True, exist_ok=True)

    carry: dict[Any, dict[str, Any]] = {}
    tail = pd.DataFrame()
    last_state: dict[str, str] = {}
    run_state: dict[str, str] = {}
    run_len: dict[str, int] = {}
    occupancy: Counter[str] = Counter()
    transitions: Counter[str] = Counter()
    exits: Counter[str] = Counter()
    entries: Counter[str] = Counter()
    dwell: dict[str, list[int]] = defaultdict(list)
    reversal: Counter[str] = Counter()
    reversal_n: Counter[str] = Counter()
    ofi_sign: Counter[str] = Counter()
    ofi_sign_n: Counter[str] = Counter()
    quote_next_positive: Counter[str] = Counter()
    quote_next_n: Counter[str] = Counter()
    stress_future_abs_sum: Counter[str] = Counter()
    stress_future_abs_n: Counter[str] = Counter()
    entry_feature_sum: dict[str, Counter[str]] = defaultdict(Counter)
    entry_feature_n: dict[str, Counter[str]] = defaultdict(Counter)
    recovery_outcome_sum: Counter[str] = Counter()
    recovery_outcome_n: Counter[str] = Counter()
    files_completed = 0
    failures: list[dict[str, str]] = []

    entry_features = [
        "quote_events",
        "relative_spread",
        "depth_total5",
        "age_quote_s",
        "rv_5m",
        "ofi_depth_norm",
        "common_quote_events",
        "common_median_depth",
    ]

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
            path = parquets[0]
            panel, _, carry, tail, _ = aggregate_hour(path, carry, tail)
            files_completed += 1
            if panel.empty:
                continue

            panel = panel.sort_values(["asset_id", "minute"]).copy()
            panel["state"] = state_labels(panel, t)

            gp = panel.groupby("asset_id", sort=False)
            panel["next_replenishment_1m"] = gp["replenishment_1m"].shift(-1)
            panel["next_future_abs_5m"] = gp["future_abs_5m"].shift(-1)
            panel["next_quote_events"] = gp["quote_events"].shift(-1)

            for state, n in panel["state"].value_counts(dropna=False).items():
                occupancy[str(state)] += int(n)

            trusted = panel[panel["state"].ne("UNTRUSTED")].copy()
            for asset, g in trusted.groupby("asset_id", sort=False):
                asset_key = str(asset)
                prev = last_state.get(asset_key)
                prev_row: dict[str, Any] | None = None
                for row in g.itertuples(index=False):
                    state = str(row.state)
                    if prev is not None and state != prev:
                        transitions[f"{prev}->{state}"] += 1
                        exits[prev] += 1
                        entries[state] += 1
                        if run_state.get(asset_key) == prev:
                            dwell[prev].append(run_len.get(asset_key, 0))
                        run_state[asset_key] = state
                        run_len[asset_key] = 1
                        if prev_row is not None:
                            for feature in entry_features:
                                value = prev_row.get(feature)
                                if value is not None and pd.notna(value) and np.isfinite(float(value)):
                                    entry_feature_sum[state][feature] += float(value)
                                    entry_feature_n[state][feature] += 1
                    elif prev is None:
                        run_state[asset_key] = state
                        run_len[asset_key] = 1
                    else:
                        run_len[asset_key] = run_len.get(asset_key, 0) + 1

                    mid = float(row.mid)
                    if pd.notna(row.ret_5m) and pd.notna(row.future_ret_5m) and row.ret_5m != 0 and row.future_ret_5m != 0:
                        key = f"{state}|{region(mid)}"
                        reversal_n[key] += 1
                        if np.sign(row.ret_5m) != np.sign(row.future_ret_5m):
                            reversal[key] += 1

                    if pd.notna(row.ofi_depth_norm) and pd.notna(row.future_ret_1m) and row.ofi_depth_norm != 0 and row.future_ret_1m != 0:
                        ofi_sign_n[state] += 1
                        if np.sign(row.ofi_depth_norm) == np.sign(row.future_ret_1m):
                            ofi_sign[state] += 1

                    if pd.notna(row.next_quote_events):
                        quote_next_n[state] += 1
                        if row.next_quote_events > 0:
                            quote_next_positive[state] += 1

                    if pd.notna(row.future_abs_5m):
                        stress_future_abs_sum[state] += float(row.future_abs_5m)
                        stress_future_abs_n[state] += 1

                    if row.withdrawal_1m >= t["withdrawal_1m"] and pd.notna(row.next_replenishment_1m) and pd.notna(row.next_future_abs_5m):
                        recovery = "RECOVERED_NEXT_MINUTE" if row.next_replenishment_1m >= t["replenishment_1m"] else "NOT_RECOVERED_NEXT_MINUTE"
                        recovery_outcome_sum[recovery] += float(row.next_future_abs_5m)
                        recovery_outcome_n[recovery] += 1

                    prev = state
                    prev_row = {feature: getattr(row, feature) for feature in entry_features}

                last_state[asset_key] = prev if prev is not None else last_state.get(asset_key, "OTHER")

            print(
                f"005I_REGIME worker={worker_id} {i + 1}/{len(files)} rows={pq.ParquetFile(path).metadata.num_rows}",
                flush=True,
            )
        except Exception as exc:
            failures.append({"file": file_name, "error": f"{type(exc).__name__}: {exc}"})
            print(f"005I_REGIME_ERROR file={file_name} error={exc}", flush=True)
        finally:
            shutil.rmtree(file_dir, ignore_errors=True)

    for asset_key, state in run_state.items():
        dwell[state].append(run_len.get(asset_key, 0))

    state_rows = []
    for state in sorted(occupancy):
        ds = np.asarray(dwell.get(state, []), dtype=float)
        state_rows.append(
            {
                "state": state,
                "occupancy_minutes": int(occupancy[state]),
                "exits": int(exits[state]),
                "entries": int(entries[state]),
                "exit_hazard_per_minute": safe_mean(exits[state], occupancy[state]),
                "dwell_n": int(len(ds)),
                "dwell_median_min": None if len(ds) == 0 else float(np.median(ds)),
                "dwell_p90_min": None if len(ds) == 0 else float(np.quantile(ds, 0.9)),
                "next_quote_positive_rate": safe_mean(quote_next_positive[state], quote_next_n[state]),
                "future_abs_5m_mean": safe_mean(stress_future_abs_sum[state], stress_future_abs_n[state]),
                "ofi_sign_agreement": safe_mean(ofi_sign[state], ofi_sign_n[state]),
                "ofi_sign_n": int(ofi_sign_n[state]),
            }
        )

    transition_rows = []
    for key, n in transitions.most_common():
        src, dst = key.split("->", 1)
        transition_rows.append(
            {
                "from_state": src,
                "to_state": dst,
                "count": int(n),
                "share_of_source_exits": safe_mean(n, exits[src]),
            }
        )

    reversal_rows = []
    for key, n in sorted(reversal_n.items()):
        state, price_region = key.split("|", 1)
        reversal_rows.append(
            {
                "state": state,
                "price_region": price_region,
                "n": int(n),
                "reversal_rate": safe_mean(reversal[key], n),
            }
        )

    entry_rows = []
    for state, sums in entry_feature_sum.items():
        row: dict[str, Any] = {"entered_state": state}
        for feature in entry_features:
            row[f"pre_{feature}_mean"] = safe_mean(sums[feature], entry_feature_n[state][feature])
            row[f"pre_{feature}_n"] = int(entry_feature_n[state][feature])
        entry_rows.append(row)

    recovery_rows = []
    for key in sorted(recovery_outcome_n):
        recovery_rows.append(
            {
                "recovery_class": key,
                "n": int(recovery_outcome_n[key]),
                "subsequent_abs_5m_mean": safe_mean(recovery_outcome_sum[key], recovery_outcome_n[key]),
            }
        )

    pd.DataFrame(state_rows).to_csv(out / "REGIME_SUMMARY.csv", index=False)
    pd.DataFrame(transition_rows).to_csv(out / "TRANSITION_MATRIX_LONG.csv", index=False)
    pd.DataFrame(reversal_rows).to_csv(out / "REGIME_REVERSAL.csv", index=False)
    pd.DataFrame(entry_rows).to_csv(out / "ENTRY_PRECONDITIONS.csv", index=False)
    pd.DataFrame(recovery_rows).to_csv(out / "RESILIENCE_EPISODES.csv", index=False)

    summary = {
        "schema_version": 1,
        "experiment": "EXPERIMENT-005I",
        "worker_id": worker_id,
        "surface": surface,
        "files_requested": len(files),
        "files_completed": files_completed,
        "files_failed": len(failures),
        "failures": failures,
        "occupancy_minutes": dict(occupancy),
        "transition_count": int(sum(transitions.values())),
        "reversal_episode_count": int(sum(reversal_n.values())),
        "ofi_direction_episode_count": int(sum(ofi_sign_n.values())),
        "recovery_episode_count": int(sum(recovery_outcome_n.values())),
        "thresholds": t,
        "holdout_read": surface.upper() == "HOLDOUT",
        "make_modified": False,
        "real_sig_orders_sent": False,
    }
    (out / "REGIME_WORKER_SUMMARY.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    shutil.rmtree(temp, ignore_errors=True)


if __name__ == "__main__":
    main()
