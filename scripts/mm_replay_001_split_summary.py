#!/usr/bin/env python3
"""Post-run split summary for MM-REPLAY-001.

Consumes the frozen MM_FILL_RESULTS.parquet emitted by the Kaggle replay.
This script is descriptive only: it does not fit, tune, select, or modify
any policy. It exists to keep TRAIN/DEV/FINAL evidence separate.
"""

from __future__ import annotations

import argparse
import csv
import math
from collections import defaultdict
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq

HORIZONS = (1, 5, 15, 30, 60, 300)


def finite(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def weighted_mean(values: list[tuple[float, float]]) -> float | None:
    pairs = [(v, w) for v, w in values if math.isfinite(v) and math.isfinite(w) and w > 0]
    if not pairs:
        return None
    weight = sum(w for _, w in pairs)
    return sum(v * w for v, w in pairs) / weight if weight > 0 else None


def mean(values: list[float]) -> float | None:
    vals = [v for v in values if math.isfinite(v)]
    return sum(vals) / len(vals) if vals else None


def summarize(fill_path: Path) -> list[dict[str, Any]]:
    frame = pq.read_table(fill_path).to_pandas()
    if frame.empty:
        return []

    required = {
        "split",
        "scenario_id",
        "scenario_family",
        "policy_id",
        "fill_assumption",
        "reaction_delay_ms",
        "size",
        "gross_spread_capture",
    }
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"fill file missing required columns: {missing}")

    keys = [
        "split",
        "scenario_family",
        "policy_id",
        "fill_assumption",
        "reaction_delay_ms",
        "scenario_id",
    ]
    rows: list[dict[str, Any]] = []

    for key, group in frame.groupby(keys, dropna=False, sort=True):
        key_values = dict(zip(keys, key, strict=True))
        sizes = [finite(v) or 0.0 for v in group["size"].tolist()]
        gross = [finite(v) for v in group["gross_spread_capture"].tolist()]
        gross_values = [v for v in gross if v is not None]
        gross_weighted = [
            (v, w)
            for v, w in zip(gross, sizes, strict=False)
            if v is not None and w > 0
        ]

        row: dict[str, Any] = {
            **key_values,
            "fills": len(group),
            "traded_size": sum(sizes),
            "markets": int(group["sig_market_id"].astype(str).nunique())
            if "sig_market_id" in group.columns
            else None,
            "mean_gross_spread_capture": mean(gross_values),
            "size_weighted_gross_spread_capture": weighted_mean(gross_weighted),
        }

        for horizon in HORIZONS:
            for reference in ("local", "external"):
                column = f"{reference}_markout_{horizon}s"
                if column not in group.columns:
                    row[f"mean_{column}"] = None
                    row[f"size_weighted_{column}"] = None
                    continue
                values = [finite(v) for v in group[column].tolist()]
                clean = [v for v in values if v is not None]
                weighted = [
                    (v, w)
                    for v, w in zip(values, sizes, strict=False)
                    if v is not None and w > 0
                ]
                row[f"mean_{column}"] = mean(clean)
                row[f"size_weighted_{column}"] = weighted_mean(weighted)

        rows.append(row)

    return rows


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("fill_results", type=Path)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("MM_SPLIT_RESULTS.csv"),
    )
    args = parser.parse_args()
    rows = summarize(args.fill_results)
    write_csv(args.output, rows)
    print(f"wrote {len(rows)} split/scenario rows to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
