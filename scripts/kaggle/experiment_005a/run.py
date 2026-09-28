# ruff: noqa: E501
from __future__ import annotations

import csv
import hashlib
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.dataset as pads
import pyarrow.parquet as pq

INPUT = Path(os.environ.get("EXP005A_INPUT", "/kaggle/input"))
WORK = Path(os.environ.get("EXP005A_WORK", "/kaggle/working/005a_fee_role_discovery"))
WORK.mkdir(parents=True, exist_ok=True)

DATA001_SHA = "e3d95735262b0be3e9fe7dd32fb54e53dc7e65b403ef9b5a2261ac107a8ea4c4"
DATA002_REPO_MANIFEST_SHA = "3bcb544fdcf3479f5e8a6973906c8ccfd5b9abd77592629daa70facfdfdd6d5c"
BASE_MAIN_SHA = "69cb1924751515a495bf99556819147ad090d67d"
EXPERIMENT_004C_FREEZE_SHA = "ba938bedcf63f562be8b26c9502e828391123867"
REGIMES = ("PRE_ELECTION", "ACTIVE_RESULTS")
PRIMARY_EVENTS = ("colombia_first_round", "peru_runoff", "colombia_runoff")
EVENT_FAMILY = {
    "colombia_first_round": "COL_2026",
    "peru_runoff": "PER_2026",
    "colombia_runoff": "COL_2026",
}
HORIZONS = (1, 5, 30, 60, 300)
NS = 1_000_000_000
UTC_US = pa.timestamp("us", tz="UTC")

A2_BASE = (
    "own_price_move_t_minus_30_to_t",
    "midpoint_t",
    "spread_t",
    "quote_age_t",
    "abs_own_price_move_300s",
    "genuine_bbo_changes_30s",
    "fill_count_30s",
    "unsigned_fill_value_30s",
    "common_event_move_ex_target",
)
A2_CHALLENGER = ("signed_taker_value_30s", "signed_taker_shares_30s")
A3_BASE = (
    "B_recent_move",
    "B_midpoint",
    "B_spread",
    "B_quote_age",
    "B_activity",
    "A_price_move_t_minus_30_to_t",
    "A_midpoint",
    "A_spread",
    "A_quote_age",
    "A_activity",
    "common_event_move_ex_A_B",
)
A3_CHALLENGER = ("A_signed_taker_value_30s", "A_signed_taker_shares_30s")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_json_sha(payload: Any) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def ns(value: datetime) -> int:
    return int(value.timestamp() * NS)


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    output = rows or [{"status": "EMPTY"}]
    fields = fields or ["status"]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(output)


def one_code_manifest() -> Path:
    matches = sorted(INPUT.rglob("005a_code_manifest.json"))
    if len(matches) != 1:
        raise RuntimeError(f"expected exactly one 005a_code_manifest.json, got {matches}")
    return matches[0]


def load_code_bundle() -> tuple[Path, dict[str, Any]]:
    manifest_path = one_code_manifest()
    root = manifest_path.parent
    manifest = json.loads(manifest_path.read_text())
    if manifest["runner_sha256"] != sha256(Path(__file__)):
        raise RuntimeError("005A Kaggle runner hash mismatch")
    for relative, expected in manifest["files"].items():
        path = root / relative
        if not path.exists() or sha256(path) != expected:
            raise RuntimeError(f"005A code bundle hash mismatch: {relative}")

    gate_path = root / "empirical_gate.json"
    if not gate_path.exists():
        raise RuntimeError("EMPIRICAL BLOCKED: 005A gate file is absent")
    gate = json.loads(gate_path.read_text())
    required = ("data_002_merged", "experiment_004c_fully_frozen", "master_authorized")
    if any(gate.get(key) is not True for key in required):
        raise RuntimeError(f"EMPIRICAL BLOCKED: invalid gate flags {gate}")
    if gate.get("data_002_main_sha") != BASE_MAIN_SHA:
        raise RuntimeError("EMPIRICAL BLOCKED: DATA-002 canonical main SHA mismatch")
    if gate.get("experiment_004c_freeze_sha") != EXPERIMENT_004C_FREEZE_SHA:
        raise RuntimeError("EMPIRICAL BLOCKED: 004C canonical freeze SHA mismatch")

    bundle = root / "predictions_cup_005a.bundle"
    sys.path.insert(0, str(bundle))
    return root, manifest


def locate_data001() -> tuple[Path, dict[str, Any]]:
    matches = [
        path
        for path in INPUT.rglob("corpus_manifest.json")
        if path.parent.name == "schema_version=1"
    ]
    if len(matches) != 1:
        raise RuntimeError(f"expected one DATA-001 corpus manifest, got {matches}")
    manifest_path = matches[0]
    if sha256(manifest_path) != DATA001_SHA:
        raise RuntimeError("DATA-001 manifest hash mismatch")
    return manifest_path.parent, json.loads(manifest_path.read_text())


def locate_data002(code_root: Path) -> tuple[Path, dict[str, Any]]:
    matches = [
        path
        for path in INPUT.rglob("data_002_manifest.json")
        if code_root not in path.parents
    ]
    if len(matches) != 1:
        raise RuntimeError(f"expected one DATA-002 manifest, got {matches}")
    manifest_path = matches[0]
    payload = json.loads(manifest_path.read_text())
    expected_payload = json.loads((code_root / "data_002_manifest_expected.json").read_text())
    if payload != expected_payload:
        raise RuntimeError(
            "DATA-002 manifest semantic mismatch "
            f"actual={canonical_json_sha(payload)} expected={canonical_json_sha(expected_payload)}"
        )
    return manifest_path.parent, payload


def verify_data001(corpus: Path, manifest: dict[str, Any], events: tuple[str, ...]) -> dict[str, Any]:
    records = {row["path"]: row for row in manifest["output_files"]}
    checked = 0
    rows = 0
    for event in events:
        for path in sorted((corpus / event).rglob("*.parquet")):
            rel = str(path.relative_to(corpus))
            rec = records.get(rel)
            if rec is None or sha256(path) != rec["sha256"]:
                raise RuntimeError(f"DATA-001 mismatch: {rel}")
            checked += 1
            rows += int(rec["rows"])
    return {"files_checked": checked, "rows_checked": rows}


def verify_data002(root: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    checked = 0
    rows = 0
    for rec in manifest["files"]:
        rel = str(rec["path"])
        candidates = [root / rel]
        prefix = "schema_version=1/"
        if rel.startswith(prefix):
            candidates.append(root / rel[len(prefix):])
        path = next((candidate for candidate in candidates if candidate.exists()), None)
        if path is None:
            raise RuntimeError(f"DATA-002 file missing: {rel}")
        if sha256(path) != rec["sha256"] or path.stat().st_size != int(rec["bytes"]):
            raise RuntimeError(f"DATA-002 file mismatch: {rel}")
        checked += 1
        rows += int(rec.get("rows") or 0)
    return {"files_checked": checked, "rows_checked": rows}


def parquet_files(root: Path) -> list[str]:
    return [str(path) for path in sorted(root.rglob("*.parquet"))]


def table_for(
    root: Path,
    columns: list[str],
    *,
    tokens: list[str] | None,
    time_col: str,
    start: datetime,
    end: datetime,
) -> pa.Table:
    files = parquet_files(root)
    if not files:
        return pa.table({name: [] for name in columns})
    dataset = pads.dataset(files, format="parquet")
    expr = (pads.field(time_col) >= pa.scalar(start, UTC_US)) & (
        pads.field(time_col) < pa.scalar(end, UTC_US)
    )
    if tokens is not None:
        expr = expr & pads.field("token_id").isin(tokens)
    return dataset.to_table(columns=columns, filter=expr)


def load_bbo_assets(
    corpus: Path,
    event: str,
    tokens: list[str],
    start: datetime,
    end: datetime,
) -> tuple[dict[str, Any], np.ndarray]:
    from predictions_cup.learning.flow_response import reconstruct_genuine_bbo

    book_root = corpus / event / "books" / "book_changes"
    table = table_for(
        book_root,
        ["token_id", "observed_at", "best_bid", "best_ask"],
        tokens=tokens,
        time_col="observed_at",
        start=start,
        end=end,
    )
    grouped: dict[str, list[dict[str, object]]] = defaultdict(list)
    for row in table.to_pylist():
        grouped[str(row["token_id"])].append(row)
    recon = {token: reconstruct_genuine_bbo(grouped.get(token, [])) for token in tokens}

    collector_table = table_for(
        book_root,
        ["observed_at"],
        tokens=None,
        time_col="observed_at",
        start=start,
        end=end,
    )
    if collector_table.num_rows:
        collector = pc.cast(collector_table["observed_at"], pa.int64()).to_numpy(
            zero_copy_only=False
        )
        collector = np.unique(np.asarray(collector, np.int64) * 1000)
    else:
        collector = np.zeros(0, np.int64)
    return recon, collector


def load_windows(code_root: Path) -> dict[str, dict[str, tuple[datetime, datetime]]]:
    payload = json.loads((code_root / "regime_definitions.json").read_text())
    result: dict[str, dict[str, tuple[datetime, datetime]]] = {}
    for event in payload["events"]:
        event_id = str(event["regime_id"])
        if event_id not in PRIMARY_EVENTS:
            continue
        result[event_id] = {}
        for regime in event["regimes"]:
            name = str(regime["name"])
            if name in REGIMES:
                result[event_id][name] = (dt(regime["start_utc"]), dt(regime["end_utc"]))
    return result


def decision_grid(start: datetime, end: datetime) -> np.ndarray:
    return np.arange(ns(start), ns(end), 30 * NS, dtype=np.int64)


def finite_frame(
    frame: pd.DataFrame,
    features: tuple[str, ...],
    y_name: str,
) -> pd.DataFrame:
    if frame.empty:
        return frame.copy()
    mask = np.isfinite(frame[y_name].to_numpy(float))
    for feature in features:
        mask &= np.isfinite(frame[feature].to_numpy(float))
    return frame.loc[mask].copy()


def evaluate_incremental(
    frame: pd.DataFrame,
    *,
    baseline_features: tuple[str, ...],
    challenger_features: tuple[str, ...],
    y_name: str,
    entity_column: str,
) -> dict[str, Any]:
    from predictions_cup.learning.flow_models import (
        chronological_split_by_group,
        fit_weighted_ridge,
        hierarchical_equal_weights,
        predict_ridge,
        weighted_mse,
    )

    features = baseline_features + challenger_features
    data = finite_frame(frame, features, y_name)
    if len(data) < 10:
        return {"status": "COVERAGE_LIMITED", "rows": len(data)}

    train, validation = chronological_split_by_group(
        data["time_ns"].to_numpy(np.int64),
        data["event"].astype(str).to_numpy(object),
        train_fraction=2 / 3,
        embargo_ns=300 * NS,
    )
    if train.sum() < 5 or validation.sum() < 5:
        return {
            "status": "COVERAGE_LIMITED",
            "rows": len(data),
            "train_rows": int(train.sum()),
            "validation_rows": int(validation.sum()),
        }

    train_rows = data.loc[train]
    val_rows = data.loc[validation]
    train_w = hierarchical_equal_weights(
        [
            train_rows["event_family"].astype(str).to_numpy(object),
            train_rows["event"].astype(str).to_numpy(object),
            train_rows["block"].astype(str).to_numpy(object),
            train_rows[entity_column].astype(str).to_numpy(object),
        ]
    )
    val_w = hierarchical_equal_weights(
        [
            val_rows["event_family"].astype(str).to_numpy(object),
            val_rows["event"].astype(str).to_numpy(object),
            val_rows["block"].astype(str).to_numpy(object),
            val_rows[entity_column].astype(str).to_numpy(object),
        ]
    )
    x0 = train_rows.loc[:, list(baseline_features)].to_numpy(float)
    x1 = train_rows.loc[:, list(features)].to_numpy(float)
    y = train_rows[y_name].to_numpy(float)
    base = fit_weighted_ridge(
        x0, y, train_w, feature_names=baseline_features, alpha=1.0
    )
    challenger = fit_weighted_ridge(
        x1, y, train_w, feature_names=features, alpha=1.0
    )

    vx0 = val_rows.loc[:, list(baseline_features)].to_numpy(float)
    vx1 = val_rows.loc[:, list(features)].to_numpy(float)
    vy = val_rows[y_name].to_numpy(float)
    p0 = predict_ridge(base, vx0)
    p1 = predict_ridge(challenger, vx1)
    loss0 = weighted_mse(vy, p0, val_w)
    loss1 = weighted_mse(vy, p1, val_w)
    return {
        "status": "OK",
        "rows": len(data),
        "train_rows": int(train.sum()),
        "validation_rows": int(validation.sum()),
        "events": int(data["event"].nunique()),
        "election_families": int(data["event_family"].nunique()),
        "baseline_mse": loss0,
        "challenger_mse": loss1,
        "loss_gain": loss0 - loss1,
        "signed_value_coefficient": float(
            challenger.coefficient[features.index(challenger_features[0])]
        ),
        "signed_shares_coefficient": float(
            challenger.coefficient[features.index(challenger_features[1])]
        ),
    }


def role_enriched_for_window(
    corpus: Path,
    fee_root: Path,
    code_root: Path,
    *,
    event: str,
    family: str,
    start: datetime,
    end: datetime,
) -> pa.Table:
    from predictions_cup.learning.fee_role_data import (
        ROLE_EVIDENCE_COLUMNS,
        append_role_classification,
        enrich_fills_with_roles,
        load_event_fills,
        load_fee_family,
        load_infrastructure_addresses,
    )

    fill_columns = [
        "fill_id",
        "token_id",
        "market_id",
        "event_id",
        "outcome",
        "observed_at",
        "price",
        "size",
        "value_usd",
        "source_side",
        "maker_address",
        "taker_address",
        "transaction_hash",
        "log_index",
    ]
    fills = load_event_fills(corpus, event, start=start, end=end, columns=fill_columns)
    fee_rows = load_fee_family(
        fee_root,
        family,
        start=start,
        end=end,
        columns=ROLE_EVIDENCE_COLUMNS,
    )
    infra = load_infrastructure_addresses(code_root / "infra_registry.json")
    joined, audit = enrich_fills_with_roles(
        fills,
        fee_rows,
        infrastructure_addresses=infra,
    )
    if audit.unmatched_rows or audit.duplicate_right_keys or audit.outcome_disagreements:
        raise RuntimeError(f"role join failed closed: {event} {start} {end} {audit}")
    return append_role_classification(joined)


def build_a2_frame(
    *,
    event: str,
    regime: str,
    event_family: str,
    conditions: list[dict[str, str]],
    grid: np.ndarray,
    role_table: pa.Table,
    recon: dict[str, Any],
    collector: np.ndarray,
    start: datetime,
    end: datetime,
) -> pd.DataFrame:
    from predictions_cup.learning.flow_panel import (
        aggregate_flow_grid,
        build_quote_grid,
        build_role_flow_series,
        common_event_move,
        future_moves,
    )

    quote = {
        row["token_id"]: build_quote_grid(recon[row["token_id"]], grid)
        for row in conditions
        if row["token_id"] in recon
    }
    parts: list[pd.DataFrame] = []
    for row in conditions:
        token = row["token_id"]
        if token not in quote:
            continue
        flow = aggregate_flow_grid(
            build_role_flow_series(role_table, condition_id=row["condition_id"]),
            grid,
            lookback_seconds=30,
        )
        q = quote[token]
        common = common_event_move(quote, exclude_tokens=frozenset({token}))
        frame = pd.DataFrame(
            {
                "event": event,
                "event_family": event_family,
                "regime": regime,
                "condition_id": row["condition_id"],
                "market_family": row["market_family"],
                "time_ns": grid,
                "block": ((grid - ns(start)) // (300 * NS)).astype(np.int64),
                "own_price_move_t_minus_30_to_t": q.recent_move_30s,
                "midpoint_t": q.midpoint,
                "spread_t": q.spread,
                "quote_age_t": q.quote_age_seconds,
                "abs_own_price_move_300s": q.abs_move_300s,
                "genuine_bbo_changes_30s": q.genuine_changes_30s,
                "fill_count_30s": flow.fill_count,
                "unsigned_fill_value_30s": flow.unsigned_value,
                "common_event_move_ex_target": common,
                "signed_taker_value_30s": flow.signed_value,
                "signed_taker_shares_30s": flow.signed_shares,
                "raw_records_30s": q.raw_records_30s,
                "repeated_unchanged_30s": q.repeated_unchanged_30s,
            }
        )
        for horizon in HORIZONS:
            frame[f"y_{horizon}s"] = future_moves(
                recon[token],
                grid,
                collector,
                horizon_seconds=horizon,
            )
            frame.loc[
                frame["time_ns"] + horizon * NS >= ns(end),
                f"y_{horizon}s",
            ] = np.nan
        parts.append(frame)
    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()


def build_a3_frame(
    *,
    event: str,
    regime: str,
    event_family: str,
    conditions: list[dict[str, str]],
    relationships: list[dict[str, str]],
    grid: np.ndarray,
    role_table: pa.Table,
    recon: dict[str, Any],
    collector: np.ndarray,
    start: datetime,
    end: datetime,
) -> pd.DataFrame:
    from predictions_cup.learning.flow_panel import (
        aggregate_flow_grid,
        build_quote_grid,
        build_role_flow_series,
        common_event_move,
        future_moves,
    )

    by_condition = {row["condition_id"]: row for row in conditions}
    quote = {
        row["token_id"]: build_quote_grid(recon[row["token_id"]], grid)
        for row in conditions
        if row["token_id"] in recon
    }
    flow = {
        row["condition_id"]: aggregate_flow_grid(
            build_role_flow_series(role_table, condition_id=row["condition_id"]),
            grid,
            lookback_seconds=30,
        )
        for row in conditions
    }
    parts: list[pd.DataFrame] = []
    for relationship in relationships:
        source = by_condition.get(relationship["source_condition_id"])
        target = by_condition.get(relationship["target_condition_id"])
        if source is None or target is None:
            continue
        source_token = source["token_id"]
        target_token = target["token_id"]
        if source_token not in quote or target_token not in quote:
            continue
        qa = quote[source_token]
        qb = quote[target_token]
        fa = flow[source["condition_id"]]
        common = common_event_move(
            quote,
            exclude_tokens=frozenset({source_token, target_token}),
        )
        frame = pd.DataFrame(
            {
                "event": event,
                "event_family": event_family,
                "regime": regime,
                "relationship_class": relationship["relationship_class"],
                "source_condition_id": source["condition_id"],
                "target_condition_id": target["condition_id"],
                "pair_id": source["condition_id"] + "->" + target["condition_id"],
                "time_ns": grid,
                "block": ((grid - ns(start)) // (300 * NS)).astype(np.int64),
                "B_recent_move": qb.recent_move_30s,
                "B_midpoint": qb.midpoint,
                "B_spread": qb.spread,
                "B_quote_age": qb.quote_age_seconds,
                "B_activity": qb.genuine_changes_30s,
                "A_price_move_t_minus_30_to_t": qa.recent_move_30s,
                "A_midpoint": qa.midpoint,
                "A_spread": qa.spread,
                "A_quote_age": qa.quote_age_seconds,
                "A_activity": qa.genuine_changes_30s,
                "common_event_move_ex_A_B": common,
                "A_signed_taker_value_30s": fa.signed_value,
                "A_signed_taker_shares_30s": fa.signed_shares,
                "A_unsigned_fill_value_30s": fa.unsigned_value,
                "A_fill_count_30s": fa.fill_count,
            }
        )
        for horizon in HORIZONS:
            frame[f"y_{horizon}s"] = future_moves(
                recon[target_token],
                grid,
                collector,
                horizon_seconds=horizon,
            )
            frame.loc[
                frame["time_ns"] + horizon * NS >= ns(end),
                f"y_{horizon}s",
            ] = np.nan
        parts.append(frame)
    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()


def main() -> None:
    code_root, code_manifest = load_code_bundle()

    # Heavy analytic dependencies are imported only after the empirical gate passes.
    global pd
    import pandas as pd

    corpus, data001 = locate_data001()
    fee_root, data002 = locate_data002(code_root)
    data_checks = {
        "data001": verify_data001(corpus, data001, PRIMARY_EVENTS),
        "data002": verify_data002(fee_root, data002),
    }

    conditions_all = read_csv(code_root / "conditions.csv")
    family_pairs = read_csv(code_root / "same_family_pairs.csv")
    semantic_pairs = read_csv(code_root / "semantic_pairs.csv")
    windows = load_windows(code_root)

    panel_dir = WORK / "panels"
    role_dir = WORK / "role_enriched"
    panel_dir.mkdir(parents=True, exist_ok=True)
    role_dir.mkdir(parents=True, exist_ok=True)

    a2_frames: list[pd.DataFrame] = []
    a3_frames: list[pd.DataFrame] = []
    coverage: list[dict[str, Any]] = []

    for event in PRIMARY_EVENTS:
        event_conditions = [row for row in conditions_all if row["event"] == event]
        tokens = sorted({row["token_id"] for row in event_conditions})
        event_start = min(windows[event][regime][0] for regime in REGIMES) - timedelta(seconds=301)
        event_end = max(windows[event][regime][1] for regime in REGIMES) + timedelta(seconds=601)
        recon, collector = load_bbo_assets(corpus, event, tokens, event_start, event_end)

        for regime in REGIMES:
            start, end = windows[event][regime]
            conditions = [
                row
                for row in event_conditions
                if row["regime"] == regime
            ]
            if not conditions:
                continue
            role_table = role_enriched_for_window(
                corpus,
                fee_root,
                code_root,
                event=event,
                family=EVENT_FAMILY[event],
                start=start,
                end=end,
            )
            role_path = role_dir / f"{event}__{regime}.parquet"
            pq.write_table(role_table, role_path, compression="zstd")

            grid = decision_grid(start, end)
            a2 = build_a2_frame(
                event=event,
                regime=regime,
                event_family=EVENT_FAMILY[event],
                conditions=conditions,
                grid=grid,
                role_table=role_table,
                recon=recon,
                collector=collector,
                start=start,
                end=end,
            )
            same = [
                row
                for row in family_pairs
                if row["event"] == event and row["regime"] == regime
            ]
            semantic = [
                row
                for row in semantic_pairs
                if row["event"] == event and row["regime"] == regime
            ]
            a3_same = build_a3_frame(
                event=event,
                regime=regime,
                event_family=EVENT_FAMILY[event],
                conditions=conditions,
                relationships=same,
                grid=grid,
                role_table=role_table,
                recon=recon,
                collector=collector,
                start=start,
                end=end,
            )
            if not a3_same.empty:
                a3_same["pair_family"] = "SAME_FAMILY"
            a3_semantic = build_a3_frame(
                event=event,
                regime=regime,
                event_family=EVENT_FAMILY[event],
                conditions=conditions,
                relationships=semantic,
                grid=grid,
                role_table=role_table,
                recon=recon,
                collector=collector,
                start=start,
                end=end,
            )
            if not a3_semantic.empty:
                a3_semantic["pair_family"] = "SEMANTIC"

            if not a2.empty:
                a2_frames.append(a2)
            if not a3_same.empty:
                a3_frames.append(a3_same)
            if not a3_semantic.empty:
                a3_frames.append(a3_semantic)
            coverage.append(
                {
                    "event": event,
                    "regime": regime,
                    "conditions": len(conditions),
                    "decision_grid_rows": len(grid),
                    "role_rows": role_table.num_rows,
                    "a2_rows": len(a2),
                    "a3_same_family_rows": len(a3_same),
                    "a3_semantic_rows": len(a3_semantic),
                    "collector_instants": len(collector),
                }
            )

    a2_all = pd.concat(a2_frames, ignore_index=True) if a2_frames else pd.DataFrame()
    a3_all = pd.concat(a3_frames, ignore_index=True) if a3_frames else pd.DataFrame()
    if not a2_all.empty:
        pq.write_table(pa.Table.from_pandas(a2_all, preserve_index=False), panel_dir / "a2.parquet", compression="zstd")
    if not a3_all.empty:
        pq.write_table(pa.Table.from_pandas(a3_all, preserve_index=False), panel_dir / "a3.parquet", compression="zstd")

    observed: list[dict[str, Any]] = []
    for regime in REGIMES:
        a2_regime = a2_all[a2_all["regime"] == regime] if not a2_all.empty else pd.DataFrame()
        for horizon in HORIZONS:
            result = evaluate_incremental(
                a2_regime,
                baseline_features=A2_BASE,
                challenger_features=A2_CHALLENGER,
                y_name=f"y_{horizon}s",
                entity_column="condition_id",
            )
            observed.append(
                {"mechanism": "A2_OWN_MARKET", "regime": regime, "horizon_seconds": horizon, **result}
            )

        for pair_family in ("SAME_FAMILY", "SEMANTIC"):
            if a3_all.empty:
                subset = pd.DataFrame()
            else:
                subset = a3_all[
                    (a3_all["regime"] == regime) & (a3_all["pair_family"] == pair_family)
                ]
            for horizon in HORIZONS:
                result = evaluate_incremental(
                    subset,
                    baseline_features=A3_BASE,
                    challenger_features=A3_CHALLENGER,
                    y_name=f"y_{horizon}s",
                    entity_column="pair_id",
                )
                observed.append(
                    {
                        "mechanism": f"A3_{pair_family}",
                        "regime": regime,
                        "horizon_seconds": horizon,
                        **result,
                    }
                )

    write_csv(WORK / "coverage.csv", coverage)
    write_csv(WORK / "observed_incremental_models.csv", observed)

    outputs: dict[str, Any] = {}
    for path in sorted(WORK.rglob("*")):
        if path.is_file():
            outputs[str(path.relative_to(WORK))] = {
                "sha256": sha256(path),
                "bytes": path.stat().st_size,
            }
    run_manifest = {
        "experiment_id": "EXPERIMENT-005A",
        "stage": "A2_A3_OBSERVED_AND_PANELS",
        "gate": json.loads((code_root / "empirical_gate.json").read_text()),
        "code_manifest": code_manifest,
        "data_checks": data_checks,
        "execution_spec_sha256": sha256(code_root / "execution_spec.json"),
        "outputs": outputs,
    }
    (WORK / "run_manifest.json").write_text(
        json.dumps(run_manifest, indent=2, sort_keys=True) + "\n"
    )
    print(json.dumps({"status": "COMPLETE", "work": str(WORK), "observed_rows": len(observed)}, sort_keys=True))


if __name__ == "__main__":
    main()