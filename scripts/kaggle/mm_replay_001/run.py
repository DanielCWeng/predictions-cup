# ruff: noqa
"""MM-REPLAY-001 Kaggle execution entrypoint.

The kernel is intentionally manifest-driven and fail-closed. GitHub Actions stages the
current ``predictions_cup`` package and frozen research manifests into this directory
before pushing the kernel, so the 005F feature implementation is not duplicated here.
"""
from __future__ import annotations

import csv
import hashlib
import json
import math
import sys
from bisect import bisect_right
from collections import defaultdict
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

HERE = Path(__file__).resolve().parent
WORK = Path("/kaggle/working/mm_replay_001")
WORK.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(HERE))

from predictions_cup.mm_replay_001 import (  # noqa: E402
    CANCEL_LATENCIES_MS,
    EXPERIMENT_ID,
    MARKOUT_HORIZONS_S,
    BookDelta,
    BookObservation,
    ConservativeTradeFillModel,
    DatasetBinding,
    FillAssumption,
    Frozen005FTransferAdapter,
    InputContractError,
    MakerPolicy,
    QueueAwareFillModel,
    Side,
    TopOfBookReconstructor,
    TradeThroughSensitivityFillModel,
    default_policies,
    fair_value_convergence,
    inspect_input,
    replay_market,
    sha256_file,
    toxicity_bucket,
    write_audit,
)

MANIFEST_PATH = HERE / "input_manifest.json"
FIT_MANIFEST_PATH = HERE / "repo_context" / "fit_freeze_manifest.json"
REQUIRED_005F = {
    "PRE_ELECTION|clock|UPDATE_HAZARD",
    "ACTIVE_RESULTS|clock|UPDATE_HAZARD",
    "ACTIVE_RESULTS|clock|JUMP_HAZARD",
}


def load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError(f"{path} must contain an object")
    return payload


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str] | None = None) -> None:
    if fields is None:
        fields = []
        for row in rows:
            for key in row:
                if key not in fields:
                    fields.append(key)
    if not fields:
        fields = ["status"]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def find_dataset_root(binding: DatasetBinding) -> Path:
    binding.require_bound()
    assert binding.kaggle_dataset_slug is not None
    leaf = binding.kaggle_dataset_slug.rsplit("/", 1)[-1]
    direct = Path("/kaggle/input") / leaf
    if direct.is_dir():
        return direct
    matches = [p for p in Path("/kaggle/input").iterdir() if p.is_dir() and p.name == leaf]
    if len(matches) == 1:
        return matches[0]
    candidates = [p for p in Path("/kaggle/input").iterdir() if p.is_dir() and leaf in p.name]
    if len(candidates) == 1:
        return candidates[0]
    raise InputContractError(
        f"cannot uniquely locate bound Kaggle dataset {binding.kaggle_dataset_slug}: "
        f"{candidates}"
    )


def _timestamp_ns(value: Any) -> int:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        raise ValueError("missing event timestamp")
    if isinstance(value, (pd.Timestamp, np.datetime64)):
        return int(pd.Timestamp(value).value)
    if isinstance(value, str) and not value.strip().isdigit():
        return int(pd.Timestamp(value).value)
    raw = int(value)
    magnitude = abs(raw)
    if magnitude < 100_000_000_000:
        return raw * 1_000_000_000
    if magnitude < 100_000_000_000_000:
        return raw * 1_000_000
    if magnitude < 100_000_000_000_000_000:
        return raw * 1_000
    return raw


def _float_or_none(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _side_or_none(value: Any) -> Side | None:
    text = str(value or "").strip().upper()
    if text in {"BUY", "B", "BID", "TAKER_BUY"}:
        return Side.BUY
    if text in {"SELL", "S", "ASK", "TAKER_SELL"}:
        return Side.SELL
    return None


def load_frame(root: Path, column_map: dict[str, str]) -> pd.DataFrame:
    source_columns = sorted(set(column_map.values()))
    parts: list[pd.DataFrame] = []
    for path in sorted(root.rglob("*.parquet")):
        schema = pq.ParquetFile(path).schema_arrow.names
        available = [col for col in source_columns if col in schema]
        if not available:
            continue
        table = pq.read_table(path, columns=available)
        parts.append(table.to_pandas())
    for path in sorted(root.rglob("*.csv")):
        header = pd.read_csv(path, nrows=0).columns.tolist()
        available = [col for col in source_columns if col in header]
        if not available:
            continue
        parts.append(pd.read_csv(path, usecols=available))
    if not parts:
        raise InputContractError("no mapped parquet/csv columns could be loaded")
    frame = pd.concat(parts, ignore_index=True, sort=False)
    rename = {
        source: canonical
        for canonical, source in column_map.items()
        if source in frame.columns
    }
    return frame.rename(columns=rename)


def canonical_observations(
    frame: pd.DataFrame,
    encoding: str,
) -> dict[str, list[BookObservation]]:
    required = {"market_id", "event_timestamp"}
    missing = required - set(frame.columns)
    if missing:
        raise InputContractError(
            "explicit column_map missing: " + ", ".join(sorted(missing))
        )
    rows: dict[str, list[BookObservation]] = defaultdict(list)
    reconstructor = TopOfBookReconstructor()
    ordered = frame.copy()
    ordered["__timestamp_ns"] = ordered["event_timestamp"].map(_timestamp_ns)
    ordered["market_id"] = ordered["market_id"].astype(str)
    ordered.sort_values(["market_id", "__timestamp_ns"], inplace=True, kind="stable")
    for rec in ordered.to_dict(orient="records"):
        market_id = str(rec["market_id"])
        timestamp_ns = int(rec["__timestamp_ns"])
        if encoding.upper() == "DELTA":
            side = str(rec.get("book_side", "")).strip().upper()
            if side in {"BUY", "BID"}:
                side = "BID"
            elif side in {"SELL", "ASK"}:
                side = "ASK"
            else:
                raise InputContractError(f"invalid delta side {side!r}")
            action = str(rec.get("book_action", "SET")).strip().upper()
            if action in {"REMOVE", "DELETE", "DEL"}:
                action = "DELETE"
            elif action in {"SET", "UPDATE", "UPSERT", "ADD"}:
                action = "SET"
            else:
                raise InputContractError(f"invalid delta action {action!r}")
            reconstructed = reconstructor.apply(
                BookDelta(
                    market_id=market_id,
                    timestamp_ns=timestamp_ns,
                    side=side,
                    price=float(rec["book_price"]),
                    size=float(rec["book_size"]),
                    action=action,
                    event_id=(
                        str(rec.get("event_id"))
                        if rec.get("event_id") is not None
                        else None
                    ),
                )
            )
            best_bid, best_ask = reconstructed.best_bid, reconstructed.best_ask
            bid_depth, ask_depth = reconstructed.bid_depth, reconstructed.ask_depth
        else:
            best_bid = _float_or_none(rec.get("best_bid"))
            best_ask = _float_or_none(rec.get("best_ask"))
            bid_depth = _float_or_none(rec.get("bid_depth"))
            ask_depth = _float_or_none(rec.get("ask_depth"))
        ext = _float_or_none(rec.get("external_fv"))
        ext_ts_raw = rec.get("external_fv_timestamp") or rec.get("source_timestamp")
        ext_ts = (
            _timestamp_ns(ext_ts_raw)
            if ext is not None and ext_ts_raw is not None
            else (timestamp_ns if ext is not None else None)
        )
        rows[market_id].append(
            BookObservation(
                market_id=market_id,
                timestamp_ns=timestamp_ns,
                best_bid=best_bid,
