# ruff: noqa: E501,I001
from __future__ import annotations

import argparse
import json
import math
import shutil
import subprocess
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pyarrow.parquet as pq


REQUIRED = {
    "timestamp",
    "token_id",
    "condition_id",
    "side",
    "price",
    "size_shares",
    "sig_market_id",
}


def run(args: list[str]) -> None:
    print("+", " ".join(args), flush=True)
    subprocess.run(args, check=True)


def token_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value).hex()
    return str(value)


def lag_corr(values: pd.Series) -> float | None:
    x = pd.to_numeric(values, errors="coerce").dropna()
    if len(x) < 5 or x.std() == 0:
        return None
    y = x.shift(1)
    frame = pd.concat([x, y], axis=1).dropna()
    if len(frame) < 4 or frame.iloc[:, 0].std() == 0 or frame.iloc[:, 1].std() == 0:
        return None
    return float(frame.iloc[:, 0].corr(frame.iloc[:, 1]))


def same_sign_rate(values: pd.Series) -> float | None:
    x = np.sign(pd.to_numeric(values, errors="coerce").dropna().to_numpy(dtype=float))
    x = x[x != 0]
    if len(x) < 2:
        return None
    return float(np.mean(x[1:] == x[:-1]))


def run_lengths(signs: np.ndarray) -> list[int]:
    signs = signs[signs != 0]
    if len(signs) == 0:
        return []
    lengths: list[int] = []
    current = signs[0]
    n = 1
    for value in signs[1:]:
        if value == current:
            n += 1
        else:
            lengths.append(n)
            current = value
            n = 1
    lengths.append(n)
    return lengths


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--spec", required=True)
    p.add_argument("--output-dir", required=True)
    args = p.parse_args()
    spec = json.loads(Path(args.spec).read_text(encoding="utf-8"))
    dataset = str(spec["dataset"])
    files = [str(x) for x in spec["files"]]
    surface = str(spec["surface"])
    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    temp = out / "_downloads"
    temp.mkdir(parents=True, exist_ok=True)

    parts: list[pd.DataFrame] = []
    source_rows: list[dict[str, Any]] = []
    for i, file_name in enumerate(files):
        dest = temp / str(i)
        dest.mkdir(parents=True, exist_ok=True)
        try:
            run(["kaggle", "datasets", "download", dataset, "-f", file_name, "-p", str(dest), "--unzip"])
            parquets = list(dest.rglob("*.parquet"))
            if len(parquets) != 1:
                raise RuntimeError(f"expected one parquet, got {len(parquets)}")
            pf = pq.ParquetFile(parquets[0])
            missing = REQUIRED - set(pf.schema_arrow.names)
            if missing:
                raise RuntimeError(f"fill file missing columns: {sorted(missing)}")
            frame = pf.read(columns=sorted(REQUIRED), use_threads=True).to_pandas()
            frame["source_file"] = file_name
            parts.append(frame)
            source_rows.append({"file": file_name, "rows": int(len(frame))})
        finally:
            shutil.rmtree(dest, ignore_errors=True)

    if not parts:
        raise RuntimeError("no fill rows loaded")
    df = pd.concat(parts, ignore_index=True)
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    df["token_id"] = df["token_id"].map(token_text)
    df["condition_id"] = df["condition_id"].map(token_text)
    df["sig_market_id"] = df["sig_market_id"].map(token_text)
    df["side"] = df["side"].astype(str).str.upper()
    df["size"] = pd.to_numeric(df["size_shares"], errors="coerce").astype(float)
    df["price_f"] = pd.to_numeric(df["price"], errors="coerce").astype(float)
    side_counts = Counter(df["side"].dropna().tolist())
    df["signed_size"] = np.where(
        df["side"].eq("BUY"),
        df["size"],
        np.where(df["side"].eq("SELL"), -df["size"], 0.0),
    )
    df["bucket_5m"] = df["timestamp"].dt.floor("5min")
    df.sort_values(["token_id", "timestamp"], inplace=True)

    bins = (
        df.groupby(["token_id", "sig_market_id", "bucket_5m"], sort=False)
        .agg(
            signed_volume=("signed_size", "sum"),
            absolute_volume=("size", "sum"),
            fill_count=("side", "size"),
            buy_count=("side", lambda x: int(x.eq("BUY").sum())),
            sell_count=("side", lambda x: int(x.eq("SELL").sum())),
            mean_price=("price_f", "mean"),
        )
        .reset_index()
    )

    token_rows: list[dict[str, Any]] = []
    event_runs: dict[str, list[int]] = defaultdict(list)
    interarrivals: dict[str, list[float]] = defaultdict(list)
    for token, g in df.groupby("token_id", sort=False):
        signs = np.sign(g["signed_size"].to_numpy(dtype=float))
        event_runs[token].extend(run_lengths(signs))
        dt = g["timestamp"].diff().dt.total_seconds().dropna()
        interarrivals[token].extend([float(x) for x in dt if math.isfinite(float(x)) and float(x) >= 0])

    for token, g in bins.groupby("token_id", sort=False):
        active = g[g["signed_volume"].ne(0)].copy()
        runs = event_runs.get(token, [])
        arr = np.asarray(interarrivals.get(token, []), dtype=float)
        mean_arr = float(np.mean(arr)) if len(arr) else np.nan
        std_arr = float(np.std(arr, ddof=1)) if len(arr) > 1 else np.nan
        burst = (
            (std_arr - mean_arr) / (std_arr + mean_arr)
            if math.isfinite(mean_arr) and math.isfinite(std_arr) and std_arr + mean_arr > 0
            else np.nan
        )
        token_rows.append(
            {
                "token_id": token,
                "sig_market_id": str(g["sig_market_id"].iloc[0]),
                "fills": int(df["token_id"].eq(token).sum()),
                "active_5m_bins": int(len(active)),
                "signed_flow_lag1_corr": lag_corr(active["signed_volume"]),
                "signed_flow_same_sign_rate": same_sign_rate(active["signed_volume"]),
                "event_same_side_mean_run": float(np.mean(runs)) if runs else np.nan,
                "event_same_side_p90_run": float(np.quantile(runs, 0.9)) if runs else np.nan,
                "mean_interarrival_s": mean_arr,
                "interarrival_cv": std_arr / mean_arr if math.isfinite(mean_arr) and mean_arr > 0 and math.isfinite(std_arr) else np.nan,
                "burstiness": burst,
            }
        )
    token_summary = pd.DataFrame(token_rows)

    market = (
        bins.groupby("sig_market_id", sort=False)
        .agg(
            fill_bins=("fill_count", "size"),
            fills=("fill_count", "sum"),
            absolute_volume=("absolute_volume", "sum"),
            signed_volume=("signed_volume", "sum"),
        )
        .reset_index()
    )

    valid_corr = pd.to_numeric(token_summary["signed_flow_lag1_corr"], errors="coerce").dropna()
    valid_same = pd.to_numeric(token_summary["signed_flow_same_sign_rate"], errors="coerce").dropna()
    valid_run = pd.to_numeric(token_summary["event_same_side_mean_run"], errors="coerce").dropna()
    valid_burst = pd.to_numeric(token_summary["burstiness"], errors="coerce").dropna()
    summary = {
        "schema_version": 1,
        "experiment": "EXPERIMENT-005I",
        "surface": surface,
        "files": source_rows,
        "rows": int(len(df)),
        "tokens": int(df["token_id"].nunique()),
        "markets": int(df["sig_market_id"].nunique()),
        "side_counts": dict(side_counts),
        "active_5m_bins": int(len(bins)),
        "token_lag1_corr": {
            "n": int(len(valid_corr)),
            "mean": float(valid_corr.mean()) if len(valid_corr) else None,
            "median": float(valid_corr.median()) if len(valid_corr) else None,
            "positive_share": float((valid_corr > 0).mean()) if len(valid_corr) else None,
        },
        "token_same_sign_rate": {
            "n": int(len(valid_same)),
            "mean": float(valid_same.mean()) if len(valid_same) else None,
            "median": float(valid_same.median()) if len(valid_same) else None,
        },
        "event_same_side_mean_run": {
            "n": int(len(valid_run)),
            "mean": float(valid_run.mean()) if len(valid_run) else None,
            "median": float(valid_run.median()) if len(valid_run) else None,
        },
        "burstiness": {
            "n": int(len(valid_burst)),
            "mean": float(valid_burst.mean()) if len(valid_burst) else None,
            "median": float(valid_burst.median()) if len(valid_burst) else None,
        },
        "holdout_read": surface.upper() == "HOLDOUT",
        "make_modified": false if False else False,
        "real_sig_orders_sent": False,
    }
    bins.to_csv(out / "FLOW_BINS.csv.gz", index=False, compression="gzip")
    token_summary.to_csv(out / "FLOW_TOKEN_SUMMARY.csv.gz", index=False, compression="gzip")
    market.to_csv(out / "FLOW_MARKET_SUMMARY.csv", index=False)
    (out / "FLOW_PERSISTENCE.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    shutil.rmtree(temp, ignore_errors=True)


if __name__ == "__main__":
    main()
