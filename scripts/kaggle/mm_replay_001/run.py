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
                best_ask=best_ask,
                external_fv=ext,
                external_fv_timestamp_ns=ext_ts,
                trade_price=_float_or_none(rec.get("trade_price")),
                trade_size=_float_or_none(rec.get("trade_size")),
                aggressor_side=_side_or_none(rec.get("aggressor_side")),
                bid_depth=bid_depth,
                ask_depth=ask_depth,
                queue_ahead_bid=_float_or_none(rec.get("queue_ahead_bid")),
                queue_ahead_ask=_float_or_none(rec.get("queue_ahead_ask")),
                category=(
                    str(rec.get("category"))
                    if rec.get("category") is not None
                    else None
                ),
            )
        )
    return rows


def genuine_change_times(observations: list[BookObservation]) -> list[int]:
    """Exact 005F grouped-BBO transition definition from the frozen build_clock."""
    out: list[int] = []
    previous: BookObservation | None = None
    for current in observations:
        valid = (
            current.best_bid is not None
            and current.best_ask is not None
            and 0.0 < current.best_bid <= current.best_ask < 1.0
        )
        if previous is None:
            previous = current
            continue
        prev_valid = (
            previous.best_bid is not None
            and previous.best_ask is not None
            and 0.0 < previous.best_bid <= previous.best_ask < 1.0
        )
        contiguous = (
            valid
            and prev_valid
            and current.timestamp_ns - previous.timestamp_ns <= 300_000_000_000
        )
        same = (
            contiguous
            and math.isclose(
                float(current.best_bid),
                float(previous.best_bid),
                rel_tol=0.0,
                abs_tol=1e-12,
            )
            and math.isclose(
                float(current.best_ask),
                float(previous.best_ask),
                rel_tol=0.0,
                abs_tol=1e-12,
            )
        )
        if contiguous and not same:
            out.append(current.timestamp_ns)
        previous = current
    return out


def frozen_artifacts() -> dict[str, tuple[Any, Any, dict[str, Any]]]:
    if not FIT_MANIFEST_PATH.is_file():
        return {}
    fit = load_json(FIT_MANIFEST_PATH)
    candidates = fit.get("candidates", [])
    artifact_files = list(Path("/kaggle/input").rglob("*.joblib"))
    by_hash: dict[str, Path] = {
        sha256_file(path): path for path in artifact_files
    }
    loaded: dict[str, tuple[Any, Any, dict[str, Any]]] = {}
    for row in candidates:
        if not isinstance(row, dict) or row.get("candidate_id") not in REQUIRED_005F:
            continue
        hashes = row.get("artifact_sha256", {})
        model_hash = hashes.get("challenger_model.joblib")
        scaler_hash = hashes.get("challenger_scaler.joblib")
        if model_hash in by_hash and scaler_hash in by_hash:
            loaded[str(row["candidate_id"])] = (
                joblib.load(by_hash[model_hash]),
                joblib.load(by_hash[scaler_hash]),
                row,
            )
    return loaded


def score_artifact(
    model: Any,
    scaler: Any,
    row: dict[str, Any],
    features: dict[str, float],
) -> float:
    cols = [str(x) for x in row["challenger_columns"]]
    x = np.asarray([[features[name] for name in cols]], dtype=float)
    if not np.all(np.isfinite(x)):
        return math.nan
    transformed = scaler.transform(x)
    if bool(row.get("classification")):
        return float(model.predict_proba(transformed)[0, 1])
    return float(model.predict(transformed)[0])


def build_005f_transfer(
    markets: dict[str, list[BookObservation]],
    manifest: dict[str, Any],
) -> tuple[
    list[dict[str, Any]],
    dict[str, Any],
    dict[str, list[tuple[int, float]]],
]:
    origins_raw = manifest.get("grid_origin_ns_by_market") or {}
    default_regime = str(manifest.get("005f_regime_default") or "").strip().upper()
    artifacts = frozen_artifacts()
    rows: list[dict[str, Any]] = []
    scores: dict[str, list[tuple[int, float]]] = defaultdict(list)
    skipped_origin = 0

    for market_id, observations in markets.items():
        origin_raw = origins_raw.get(market_id)
        if origin_raw is None:
            skipped_origin += 1
            continue
        origin = int(origin_raw)
        adapter = Frozen005FTransferAdapter(scope_id=market_id, grid_origin_ns=origin)
        for obs in observations:
            adapter.observe(
                timestamp_ns=obs.timestamp_ns,
                best_bid=obs.best_bid,
                best_ask=obs.best_ask,
            )
        genuine = genuine_change_times(observations)
        if not observations:
            continue
        last = observations[-1].timestamp_ns
        query = origin
        if query < observations[0].timestamp_ns:
            steps = math.ceil(
                (observations[0].timestamp_ns - query) / 15_000_000_000
            )
            query += steps * 15_000_000_000

        while query + 300_000_000_000 <= last:
            features = adapter.features(query_timestamp_ns=query)
            if features is not None:
                a = bisect_right(genuine, query)
                b = bisect_right(genuine, query + 300_000_000_000)
                regime = (
                    default_regime
                    if default_regime in {"PRE_ELECTION", "ACTIVE_RESULTS"}
                    else "UNASSIGNED"
                )
                record: dict[str, Any] = {
                    "market_id": market_id,
                    "grid_time_ns": query,
                    "regime": regime,
                    "update_h300": float(b > a),
                    **features,
                }
                update_key = f"{regime}|clock|UPDATE_HAZARD"
                if update_key in artifacts:
                    model, scaler, artifact_row = artifacts[update_key]
                    score = score_artifact(
                        model,
                        scaler,
                        artifact_row,
                        dict(features),
                    )
                    record["frozen_update_hazard_score"] = score
                    if math.isfinite(score):
                        scores[market_id].append((query, score))
                jump_key = "ACTIVE_RESULTS|clock|JUMP_HAZARD"
                if regime == "ACTIVE_RESULTS" and jump_key in artifacts:
                    model, scaler, artifact_row = artifacts[jump_key]
                    record["frozen_jump_hazard_score"] = score_artifact(
                        model,
                        scaler,
                        artifact_row,
                        dict(features),
                    )
                rows.append(record)
            query += 15_000_000_000

    scored = [
        row
        for row in rows
        if math.isfinite(
            float(row.get("frozen_update_hazard_score", math.nan))
        )
    ]
    summary: dict[str, Any] = {
        "experiment": EXPERIMENT_ID,
        "feature_definition": "existing IncrementalHazard005FState",
        "target_horizon_seconds": 300,
        "rows": len(rows),
        "markets": len({str(row["market_id"]) for row in rows}),
        "markets_missing_explicit_grid_origin": skipped_origin,
        "frozen_artifacts_found": sorted(artifacts),
        "scored_rows": len(scored),
        "status": "SCORED" if scored else ("FEATURES_ONLY" if rows else "NOT_RUN"),
        "production_threshold_selected": False,
    }
    if scored:
        y = np.asarray([float(row["update_h300"]) for row in scored])
        p = np.asarray(
            [float(row["frozen_update_hazard_score"]) for row in scored]
        )
        summary["brier"] = float(np.mean((p - y) ** 2))
        summary["mean_score_when_update"] = (
            float(np.mean(p[y == 1])) if np.any(y == 1) else None
        )
        summary["mean_score_when_no_update"] = (
            float(np.mean(p[y == 0])) if np.any(y == 0) else None
        )
        order = np.argsort(p)
        size = max(1, len(order) // 4)
        lo = order[:size]
        hi = order[-size:]
        summary["update_rate_low_score_quartile"] = float(np.mean(y[lo]))
        summary["update_rate_high_score_quartile"] = float(np.mean(y[hi]))
    return rows, summary, scores


def attach_scores(
    markets: dict[str, list[BookObservation]],
    scores: dict[str, list[tuple[int, float]]],
) -> None:
    for market_id, observations in list(markets.items()):
        series = scores.get(market_id, [])
        if not series:
            continue
        times = [item[0] for item in series]
        revised: list[BookObservation] = []
        for obs in observations:
            index = bisect_right(times, obs.timestamp_ns) - 1
            score = series[index][1] if index >= 0 else None
            revised.append(replace(obs, update_hazard=score))
        markets[market_id] = revised


def split_for(timestamp_ns: int, start: int, end: int) -> str:
    span = max(1, end - start)
    fraction = (timestamp_ns - start) / span
    if fraction < 0.60:
        return "TRAIN"
    if fraction < 0.80:
        return "DEV"
    return "FINAL"


def edge_grid() -> list[MakerPolicy]:
    b1, b2, b3 = default_policies()[1:]
    out: list[MakerPolicy] = []
    for base in (b1, b2, b3):
        for ticks in (0.0, 1.0, 2.0, 3.0, 4.0):
            out.append(
                replace(
                    base,
                    policy_id=f"{base.policy_id}-edge{ticks:g}",
                    min_external_edge_ticks=ticks,
                )
            )
    return out


def run_mm(
    markets: dict[str, list[BookObservation]],
) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
]:
    policy_rows: list[dict[str, Any]] = []
    fill_rows: list[dict[str, Any]] = []
    markout_rows: list[dict[str, Any]] = []
    breakdown: list[dict[str, Any]] = []
    models = [ConservativeTradeFillModel(), TradeThroughSensitivityFillModel()]
    queue_supported = any(
        any(
            obs.queue_ahead_bid is not None or obs.queue_ahead_ask is not None
            for obs in observations
        )
        for observations in markets.values()
    )
    if queue_supported:
        models.append(QueueAwareFillModel())
    policies = list(default_policies()) + edge_grid()
    for market_id, observations in markets.items():
        if not observations:
            continue
        start, end = observations[0].timestamp_ns, observations[-1].timestamp_ns
        for policy in policies:
            for model in models:
