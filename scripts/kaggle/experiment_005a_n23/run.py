# ruff: noqa: E501
from __future__ import annotations

import csv
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

INPUT = Path("/kaggle/input")
WORK = Path("/kaggle/working/005a_n23_flow_nulls")
WORK.mkdir(parents=True, exist_ok=True)

MASTER_SEED = 20260928005
EXPECTED_STAGE1_IMPLEMENTATION = "a0217eed5f775735d5ad01edf3141963be7b1787"
EXPECTED_004C_FREEZE = "ba938bedcf63f562be8b26c9502e828391123867"
DRAWS = 999
NS = 1_000_000_000
HORIZONS = (1, 5, 30, 60, 300)
REGIMES = ("PRE_ELECTION", "ACTIVE_RESULTS")

A2_BASE = (
    "own_price_move_t_minus_30_to_t", "midpoint_t", "spread_t", "quote_age_t",
    "abs_own_price_move_300s", "genuine_bbo_changes_30s", "fill_count_30s",
    "unsigned_fill_value_30s", "common_event_move_ex_target",
)
A2_CHALLENGER = ("signed_taker_value_30s", "signed_taker_shares_30s")
A3_BASE = (
    "B_recent_move", "B_midpoint", "B_spread", "B_quote_age", "B_activity",
    "A_price_move_t_minus_30_to_t", "A_midpoint", "A_spread", "A_quote_age",
    "A_activity", "common_event_move_ex_A_B",
)
A3_CHALLENGER = ("A_signed_taker_value_30s", "A_signed_taker_shares_30s")


def sha256(path: Path) -> str:
    d = hashlib.sha256()
    with path.open("rb") as h:
        for chunk in iter(lambda: h.read(1 << 20), b""):
            d.update(chunk)
    return d.hexdigest()


def seed(component: str, draw: int) -> int:
    digest = hashlib.sha256(f"{MASTER_SEED}|{component}|{draw}".encode()).digest()
    return int.from_bytes(digest[:8], "big")


def one(name: str) -> Path:
    matches = sorted(INPUT.rglob(name))
    if len(matches) != 1:
        raise RuntimeError(f"expected exactly one {name}, got {matches}")
    return matches[0]


def load_bundle() -> dict[str, Any]:
    p = one("005a_a45_code_manifest.json")
    root = p.parent
    m = json.loads(p.read_text())
    for rel, expected in m["files"].items():
        q = root / rel
        if not q.exists() or sha256(q) != expected:
            raise RuntimeError(f"code bundle mismatch: {rel}")
    sys.path.insert(0, str(root / "predictions_cup_005a.bundle"))
    return m


def locate_stage1() -> tuple[Path, dict[str, Any]]:
    p = one("run_manifest.json")
    # Three upstream kernels can expose run_manifest.json in other null jobs, but this N23
    # kernel has exactly one kernel_source. Fail if that assumption changes.
    m = json.loads(p.read_text())
    if m.get("stage") != "A2_A3_OBSERVED_AND_PANELS":
        raise RuntimeError(f"wrong stage-1 manifest: {m.get('stage')}")
    if m.get("code_manifest", {}).get("implementation_commit") != EXPECTED_STAGE1_IMPLEMENTATION:
        raise RuntimeError("wrong stage-1 implementation")
    if m.get("gate", {}).get("experiment_004c_freeze_sha") != EXPECTED_004C_FREEZE:
        raise RuntimeError("wrong stage-1 004C freeze")
    root = p.parent
    for rel in ("panels/a2.parquet", "panels/a3.parquet", "observed_incremental_models.csv"):
        meta = m["outputs"][rel]
        q = root / rel
        if not q.exists() or q.stat().st_size != int(meta["bytes"]) or sha256(q) != meta["sha256"]:
            raise RuntimeError(f"stage-1 output mismatch: {rel}")
    return root, m


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    with path.open("w", newline="", encoding="utf-8") as h:
        w = csv.DictWriter(h, fieldnames=fields, lineterminator="\\n")
        w.writeheader()
        w.writerows(rows)


def finite_frame(frame: pd.DataFrame, features: tuple[str, ...], y_name: str) -> pd.DataFrame:
    mask = np.isfinite(frame[y_name].to_numpy(float))
    for feature in features:
        mask &= np.isfinite(frame[feature].to_numpy(float))
    return frame.loc[mask].copy()


def fit_cell(
    frame: pd.DataFrame,
    *,
    base_features: tuple[str, ...],
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

    features = base_features + challenger_features
    data = finite_frame(frame, features, y_name)
    groups = data["event"].astype(str).to_numpy(object)
    train, validation = chronological_split_by_group(
        data["time_ns"].to_numpy(np.int64),
        groups,
        train_fraction=2 / 3,
        embargo_ns=300 * NS,
    )
    if train.sum() < 5 or validation.sum() < 5:
        return {"status": "COVERAGE_LIMITED", "rows": len(data)}

    tr = data.loc[train].copy()
    va = data.loc[validation].copy()
    train_w = hierarchical_equal_weights([
        tr["event_family"].astype(str).to_numpy(object),
        tr["event"].astype(str).to_numpy(object),
        tr["block"].astype(str).to_numpy(object),
        tr[entity_column].astype(str).to_numpy(object),
    ])
    val_w = hierarchical_equal_weights([
        va["event_family"].astype(str).to_numpy(object),
        va["event"].astype(str).to_numpy(object),
        va["block"].astype(str).to_numpy(object),
        va[entity_column].astype(str).to_numpy(object),
    ])
    x0 = tr.loc[:, list(base_features)].to_numpy(float)
    x1 = tr.loc[:, list(features)].to_numpy(float)
    y = tr[y_name].to_numpy(float)
    base = fit_weighted_ridge(x0, y, train_w, feature_names=base_features, alpha=1.0)
    challenger = fit_weighted_ridge(x1, y, train_w, feature_names=features, alpha=1.0)
    vx0 = va.loc[:, list(base_features)].to_numpy(float)
    vx1 = va.loc[:, list(features)].to_numpy(float)
    vy = va[y_name].to_numpy(float)
    p0 = predict_ridge(base, vx0)
    p1 = predict_ridge(challenger, vx1)
    loss0 = weighted_mse(vy, p0, val_w)
    loss1 = weighted_mse(vy, p1, val_w)
    return {
        "status": "OK",
        "data": data,
        "train": train,
        "validation": validation,
        "validation_frame": va,
        "base_model": base,
        "challenger_model": challenger,
        "val_w": val_w,
        "vy": vy,
        "baseline_prediction": p0,
        "observed_gain": loss0 - loss1,
        "baseline_mse": loss0,
        "challenger_mse": loss1,
    }


def source_index(
    validation: pd.DataFrame,
    *,
    source_column: str,
    feature_columns: tuple[str, ...],
) -> dict[str, Any]:
    keys = ["event", source_column, "time_ns"]
    # Every duplicate source-time row in A3 must carry the same source flow.
    grouped = validation.groupby(keys, sort=False, dropna=False)
    for feature in feature_columns:
        spread = grouped[feature].agg(lambda x: float(np.nanmax(x) - np.nanmin(x))).to_numpy(float)
        if np.any(np.abs(spread) > 1e-12):
            raise RuntimeError(f"inconsistent duplicated source-time values for {feature}")
    unique = validation.drop_duplicates(keys, keep="first").loc[
        :, keys + ["block"] + list(feature_columns)
    ].copy()
    unique = unique.sort_values(keys, kind="stable").reset_index(drop=True)
    key_to_uid = {
        (str(row.event), str(getattr(row, source_column)), int(row.time_ns)): idx
        for idx, row in enumerate(unique.itertuples(index=False))
    }
    uid = np.asarray([
        key_to_uid[(str(e), str(s), int(t))]
        for e, s, t in zip(
            validation["event"], validation[source_column], validation["time_ns"], strict=True
        )
    ], np.int64)
    group_labels = np.asarray(
        [f"{e}|{s}" for e, s in zip(unique["event"], unique[source_column], strict=True)],
        object,
    )
    return {
        "unique": unique,
        "uid": uid,
        "group_labels": group_labels,
        "values": unique.loc[:, list(feature_columns)].to_numpy(float),
    }


def transformed_validation(
    index: dict[str, Any],
    *,
    mode: str,
    component: str,
    draw: int,
) -> tuple[np.ndarray, int]:
    unique = index["unique"]
    values = np.asarray(index["values"], float)
    groups = np.asarray(index["group_labels"], object)
    transformed = values.copy()
    rng = np.random.default_rng(seed(component, draw))
    valid_groups = 0

    for group in sorted(set(map(str, groups))):
        ids = np.flatnonzero(groups == group)
        order = ids[np.argsort(unique.loc[ids, "time_ns"].to_numpy(np.int64), kind="stable")]
        n = len(order)
        if mode == "circular":
            if n <= 20:
                continue
            shift = int(rng.integers(10, n - 9))
        elif mode == "block":
            times = unique.loc[order, "time_ns"].to_numpy(np.int64)
            blocks = unique.loc[order, "block"].to_numpy(np.int64)
            slots = ((times // (30 * NS)) % 10).astype(np.int64)
            patterns: dict[tuple[int, ...], list[np.ndarray]] = {}
            for block in sorted(set(map(int, blocks))):
                local = np.flatnonzero(blocks == block)
                pattern = tuple(map(int, slots[local]))
                patterns.setdefault(pattern, []).append(order[local])
            shifted_any = False
            for pattern in sorted(patterns):
                chunks = patterns[pattern]
                if len(chunks) < 2:
                    continue
                shift_blocks = int(rng.integers(1, len(chunks)))
                for destination, source in zip(
                    chunks, np.roll(np.asarray(chunks, object), shift_blocks), strict=True
                ):
                    source_ids = np.asarray(source, np.int64)
                    transformed[destination] = values[source_ids]
                shifted_any = True
            if shifted_any:
                valid_groups += 1
            continue
        else:
            raise ValueError(mode)
        transformed[order] = np.roll(values[order], shift, axis=0)
        valid_groups += 1

    if valid_groups == 0:
        raise RuntimeError(f"no valid {mode} source groups for {component}")
    return transformed[index["uid"]], valid_groups


def null_gain(fit: dict[str, Any], transformed: np.ndarray, challenger_features: tuple[str, ...]) -> float:
    from predictions_cup.learning.flow_models import predict_ridge, weighted_mse

    va = fit["validation_frame"]
    model = fit["challenger_model"]
    full = va.loc[:, list(model.feature_names)].to_numpy(float)
    positions = [model.feature_names.index(name) for name in challenger_features]
    for column, pos in enumerate(positions):
        full[:, pos] = transformed[:, column]
    prediction = predict_ridge(model, full)
    challenger_loss = weighted_mse(fit["vy"], prediction, fit["val_w"])
    return float(fit["baseline_mse"] - challenger_loss)


def upper_p(observed: float, nulls: list[float]) -> float:
    if observed <= 0:
        return 1.0
    arr = np.asarray(nulls, float)
    return float((1 + np.sum(arr >= observed)) / (len(arr) + 1))


def main() -> None:
    code_manifest = load_bundle()
    root, stage1 = locate_stage1()
    a2 = pq.read_table(root / "panels/a2.parquet").to_pandas()
    a3 = pq.read_table(root / "panels/a3.parquet").to_pandas()

    summaries: list[dict[str, Any]] = []
    draws_out: list[dict[str, Any]] = []

    families = [
        ("A2_OWN_MARKET", a2, None, A2_BASE, A2_CHALLENGER, "condition_id", "condition_id"),
        ("A3_SAME_FAMILY", a3, "SAME_FAMILY", A3_BASE, A3_CHALLENGER, "pair_id", "source_condition_id"),
        ("A3_SEMANTIC", a3, "SEMANTIC", A3_BASE, A3_CHALLENGER, "pair_id", "source_condition_id"),
    ]

    for mechanism, source, pair_family, base_features, challenger_features, entity, source_col in families:
        scoped = source if pair_family is None else source[source["pair_family"] == pair_family]
        cell_rows: list[dict[str, Any]] = []
        for regime in REGIMES:
            regime_frame = scoped[scoped["regime"] == regime]
            for horizon in HORIZONS:
                y_name = f"y_{horizon}s"
                fit = fit_cell(
                    regime_frame,
                    base_features=base_features,
                    challenger_features=challenger_features,
                    y_name=y_name,
                    entity_column=entity,
                )
                if fit["status"] != "OK":
                    row = {
                        "mechanism": mechanism, "regime": regime, "horizon_seconds": horizon,
                        "status": fit["status"], "observed_gain": np.nan,
                        "circular_p": 1.0, "block_p": 1.0, "intersection_p": 1.0,
                    }
                    cell_rows.append(row)
                    continue

                observed = float(fit["observed_gain"])
                # Stage-1 reproduction is a hard gate before stochastic work.
                if observed > 0:
                    index = source_index(
                        fit["validation_frame"],
                        source_column=source_col,
                        feature_columns=challenger_features,
                    )
                    null_by_mode: dict[str, list[float]] = {}
                    groups_by_mode: dict[str, int] = {}
                    for mode in ("circular", "block"):
                        values: list[float] = []
                        min_groups = None
                        for draw in range(DRAWS):
                            transformed, valid_groups = transformed_validation(
                                index,
                                mode=mode,
                                component=f"{mechanism}|{regime}|{horizon}|{mode}",
                                draw=draw,
                            )
                            gain = null_gain(fit, transformed, challenger_features)
                            values.append(gain)
                            min_groups = valid_groups if min_groups is None else min(min_groups, valid_groups)
                            draws_out.append({
                                "mechanism": mechanism,
                                "regime": regime,
                                "horizon_seconds": horizon,
                                "null": mode,
                                "draw": draw,
                                "loss_gain": gain,
                            })
                        null_by_mode[mode] = values
                        groups_by_mode[mode] = int(min_groups or 0)
                    circular_p = upper_p(observed, null_by_mode["circular"])
                    block_p = upper_p(observed, null_by_mode["block"])
                    intersection = max(circular_p, block_p)
                    null_circular_mean = float(np.mean(null_by_mode["circular"]))
                    null_block_mean = float(np.mean(null_by_mode["block"]))
                else:
                    circular_p = block_p = intersection = 1.0
                    groups_by_mode = {"circular": 0, "block": 0}
                    null_circular_mean = null_block_mean = np.nan

                cell_rows.append({
                    "mechanism": mechanism,
                    "regime": regime,
                    "horizon_seconds": horizon,
                    "status": "OK",
                    "rows": len(fit["data"]),
                    "validation_rows": int(np.sum(fit["validation"])),
                    "observed_gain": observed,
                    "relative_gain": observed / float(fit["baseline_mse"]),
                    "circular_p": circular_p,
                    "block_p": block_p,
                    "intersection_p": intersection,
                    "circular_valid_groups_min": groups_by_mode["circular"],
                    "block_valid_groups_min": groups_by_mode["block"],
                    "circular_null_mean": null_circular_mean,
                    "block_null_mean": null_block_mean,
                })

        from predictions_cup.learning.flow_models import benjamini_hochberg
        pvals = np.asarray([float(row["intersection_p"]) for row in cell_rows], float)
        qvals = benjamini_hochberg(pvals)
        for row, q in zip(cell_rows, qvals, strict=True):
            row["bh_q"] = float(q)
            row["bh_reject_5pct"] = bool(q <= 0.05 and float(row["observed_gain"]) > 0)
        summaries.extend(cell_rows)

    write_csv(WORK / "null_summary.csv", summaries)
    write_csv(WORK / "null_draws.csv", draws_out)
    manifest = {
        "experiment_id": "EXPERIMENT-005A",
        "stage": "N23_FLOW_NULLS",
        "master_seed": MASTER_SEED,
        "draws": DRAWS,
        "stage1_manifest_sha256": sha256(root / "run_manifest.json"),
        "stage1_implementation_commit": stage1["code_manifest"]["implementation_commit"],
        "code_bundle_implementation_commit": code_manifest["implementation_commit"],
        "outputs": {
            "null_summary.csv": sha256(WORK / "null_summary.csv"),
            "null_draws.csv": sha256(WORK / "null_draws.csv"),
        },
    }
    (WORK / "run_manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\\n")
    print(json.dumps({
        "status": "COMPLETE",
        "cells": len(summaries),
        "draw_rows": len(draws_out),
        "bh_rejections": sum(bool(row["bh_reject_5pct"]) for row in summaries),
    }, sort_keys=True))


if __name__ == "__main__":
    main()
