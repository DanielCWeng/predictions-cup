"""EXPERIMENT-005C post-HOLDOUT independent review falsification.

This runner is deliberately bounded. It reproduces only the 35 already-frozen
HOLDOUT selections and may retain, weaken, downgrade, or reject the existing
005C claim. It cannot select a new candidate.
"""

from __future__ import annotations

import csv
import hashlib
import importlib.util
import json
import math
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType
from typing import Any

import duckdb
import numpy as np

INPUT = Path("/kaggle/input")
WORK = Path("/kaggle/working/005c_review_falsification")

REVIEW_CLASSIFICATION = "POST_HOC_INDEPENDENT_REVIEW_FALSIFICATION"
REVIEW_PREREG_SHA256 = "0567e3b5114d527dc8b67079c35e0dcb04ebdecc4889fc8367a24e9caa8f3181"
ORIGINAL_RUNNER_SHA256 = "ae4468a18d1a980c5db7bb8c683f4580ba91aad903cd49ee3e7dccc2756df40f"
PRE_HOLDOUT_FREEZE_SHA256 = "20f977ffb64a56ecfe230ba45d9d0021dcc97e5218b13eb307db896d14c87933"
ORIGINAL_HOLDOUT_SHA256 = "69ded820bdb336aa4cda16d432c79d51aae017c89fcdb71ed5afe344a60114a9"
REVIEW_MASTER_SEED = 20260928051
MULTIPLICITY_DRAWS = 20_000
BLOCK_DRAWS = 5_000
FAMILIES = ("US_2024", "CAN_2025", "COL_2026", "HUN_2026", "PER_2026")
US_PANEL = "US_2024_event_6e91434eb80f"
US_PRIMARY_GRID = 15
US_PRIMARY_HORIZON = 15
ELECTION_DAY_UTC = datetime(2024, 11, 5, tzinfo=UTC)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def locate_unique(name: str) -> Path:
    matches = sorted(INPUT.rglob(name))
    if len(matches) != 1:
        raise RuntimeError(f"expected exactly one {name}, got {matches}")
    return matches[0]


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    if not rows:
        rows = [{"status": "EMPTY"}]
        fields = ["status"]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def jsonable(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [jsonable(v) for v in value]
    if hasattr(value, "isoformat"):
        try:
            return value.isoformat()
        except Exception:
            pass
    return str(value)


def iso_utc(epoch_seconds: int | float | None) -> str | None:
    if epoch_seconds is None:
        return None
    if not np.isfinite(float(epoch_seconds)):
        return None
    return datetime.fromtimestamp(float(epoch_seconds), UTC).isoformat()


def load_review_bundle() -> tuple[dict[str, Any], dict[str, Any], list[dict[str, str]], ModuleType]:
    manifest_path = locate_unique("review_code_manifest.json")
    manifest = json.loads(manifest_path.read_text())
    if manifest["review_preregistration_sha256"] != REVIEW_PREREG_SHA256:
        raise RuntimeError("review preregistration hash mismatch in manifest")
    if manifest["followup_runner_sha256"] != sha256(Path(__file__)):
        raise RuntimeError("follow-up runner hash mismatch")
    for name, expected in manifest["files"].items():
        path = manifest_path.parent / name
        if sha256(path) != expected:
            raise RuntimeError(f"review bundle mismatch: {name}")

    prereg_path = manifest_path.parent / "review_preregistration.json"
    if sha256(prereg_path) != REVIEW_PREREG_SHA256:
        raise RuntimeError("review preregistration changed")
    prereg = json.loads(prereg_path.read_text())

    freeze_path = manifest_path.parent / "PRE_HOLDOUT_FREEZE.json"
    if sha256(freeze_path) != PRE_HOLDOUT_FREEZE_SHA256:
        raise RuntimeError("PRE_HOLDOUT_FREEZE changed")
    freeze = json.loads(freeze_path.read_text())
    if len(freeze["selections"]) != 35:
        raise RuntimeError("expected exactly 35 frozen selections")

    holdout_path = manifest_path.parent / "holdout_results.csv"
    if sha256(holdout_path) != ORIGINAL_HOLDOUT_SHA256:
        raise RuntimeError("original holdout results changed")
    with holdout_path.open(newline="", encoding="utf-8") as handle:
        original_holdout = list(csv.DictReader(handle))

    original_runner_path = manifest_path.parent / "original_runner.py"
    if sha256(original_runner_path) != ORIGINAL_RUNNER_SHA256:
        raise RuntimeError("original runner changed")
    spec = importlib.util.spec_from_file_location("original_005c_runner", original_runner_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("unable to load original 005C runner")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

    return prereg, freeze, original_holdout, module


def panel_from_selection(runner: ModuleType, selection: dict[str, Any]) -> Any:
    return runner.Panel(
        panel_id=str(selection["panel_id"]),
        family=str(selection["family"]),
        scope=str(selection["panel_scope"]),
        scope_value=str(selection["panel_scope_value"]),
        markets=tuple(str(x) for x in selection["panel_markets"]),
    )


def selection_key(selection: dict[str, Any]) -> tuple[str, str, int, int, str]:
    return (
        str(selection["family"]),
        str(selection["panel_id"]),
        int(selection["grid_seconds"]),
        int(selection["horizon_seconds"]),
        str(selection["model"]),
    )


def fit_frozen_b1(
    runner: ModuleType,
    selection: dict[str, Any],
    panel: Any,
    series: dict[str, Any],
    state: dict[str, Any],
) -> np.ndarray:
    grid = int(selection["grid_seconds"])
    lag = int(selection["baseline"]["B1"]["lag_depth"])
    alpha = float(selection["baseline"]["B1"]["alpha"])
    raw_train = runner.raw_cube(
        panel,
        series,
        state["train_q"],
        grid,
        lag,
        "logit",
    )
    raw_hold = runner.raw_cube(
        panel,
        series,
        state["hold_q"],
        grid,
        lag,
        "logit",
    )
    prepared = runner.prepare_features(raw_train, raw_hold)
    return runner.baseline_predictions(
        prepared,
        state["y_train"],
        list(state["target_indices"]),
        kind="B1",
        alpha=alpha,
    )


def compare_vs_baseline(
    runner: ModuleType,
    y: np.ndarray,
    pred: np.ndarray,
    baseline: np.ndarray,
) -> dict[str, Any]:
    mse = float(runner.masked_mse(y, pred))
    base_mse = float(runner.masked_mse(y, baseline))
    per = np.asarray(runner.per_target_mse(y, pred), dtype=np.float64)
    base_per = np.asarray(runner.per_target_mse(y, baseline), dtype=np.float64)
    valid = np.isfinite(per) & np.isfinite(base_per) & (base_per > 0)
    improvements = np.full(len(per), np.nan, dtype=np.float64)
    improvements[valid] = (base_per[valid] - per[valid]) / base_per[valid]
    return {
        "mse": mse,
        "baseline_mse": base_mse,
        "pooled_improvement": (
            (base_mse - mse) / base_mse
            if np.isfinite(base_mse) and base_mse > 0
            else float("nan")
        ),
        "median_target_improvement": (
            float(np.nanmedian(improvements)) if valid.any() else float("nan")
        ),
        "fraction_targets_helped": (
            float(np.sum(improvements[valid] > 0) / np.sum(valid))
            if valid.any()
            else float("nan")
        ),
    }


def target_time_losses(
    y: np.ndarray,
    challenger: np.ndarray,
    baseline: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    valid = np.isfinite(y) & np.isfinite(challenger) & np.isfinite(baseline)
    base_loss = np.where(valid, (y - baseline) ** 2, np.nan)
    challenge_loss = np.where(valid, (y - challenger) ** 2, np.nan)
    advantage = base_loss - challenge_loss
    return advantage, base_loss, valid


def aggregate_hourly(
    times: np.ndarray,
    values: np.ndarray,
    valid: np.ndarray,
) -> dict[int, tuple[float, int]]:
    out: dict[int, tuple[float, int]] = {}
    for row, timestamp in enumerate(np.asarray(times, dtype=np.int64)):
        mask = valid[row]
        if not mask.any():
            continue
        hour = int(timestamp // 3600 * 3600)
        value_sum = float(np.nansum(values[row, mask]))
        count = int(np.sum(mask))
        prev_sum, prev_count = out.get(hour, (0.0, 0))
        out[hour] = (prev_sum + value_sum, prev_count + count)
    return out


def compute_multiplicity(
    states: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    n_cells = len(states)
    observed_pct = np.array(
        [
            100.0 * item["loss_advantage_sum"] / item["b2_loss_sum"]
            for item in states
        ],
        dtype=np.float64,
    )
    null_stats = np.zeros((MULTIPLICITY_DRAWS, n_cells), dtype=np.float64)

    for family_index, family in enumerate(FAMILIES):
        indices = [i for i, item in enumerate(states) if item["family"] == family]
        if not indices:
            continue
        hours = sorted(
            {
                hour
                for i in indices
                for hour in states[i]["hourly_centered"]
            }
        )
        if not hours:
            raise RuntimeError(f"{family}: no active hours for multiplicity")
        hour_to_pos = {hour: pos for pos, hour in enumerate(hours)}
        block_sums = np.zeros((len(hours), len(indices)), dtype=np.float64)
        denominators = np.zeros(len(indices), dtype=np.float64)
        for local_col, cell_index in enumerate(indices):
            item = states[cell_index]
            denominators[local_col] = float(item["b2_loss_sum"])
            for hour, (value_sum, _count) in item["hourly_centered"].items():
                block_sums[hour_to_pos[hour], local_col] = value_sum

        family_seed = REVIEW_MASTER_SEED + 1000 * (family_index + 1)
        rng = np.random.default_rng(family_seed)
        signs = rng.integers(
            0,
            2,
            size=(MULTIPLICITY_DRAWS, len(hours)),
            dtype=np.int8,
        ).astype(np.float64)
        signs = signs * 2.0 - 1.0
        null_family = signs @ block_sums
        null_family = 100.0 * null_family / denominators[None, :]
        null_stats[:, indices] = null_family

    max_null = np.max(null_stats, axis=1)
    rows: list[dict[str, Any]] = []
    for i, item in enumerate(states):
        obs = float(observed_pct[i])
        unadjusted = float(
            (1 + np.sum(null_stats[:, i] >= obs)) / (MULTIPLICITY_DRAWS + 1)
        )
        adjusted = float(
            (1 + np.sum(max_null >= obs)) / (MULTIPLICITY_DRAWS + 1)
        )
        rows.append(
            {
                "classification": REVIEW_CLASSIFICATION,
                "family": item["family"],
                "panel_id": item["panel_id"],
                "grid_seconds": item["grid_seconds"],
                "horizon_seconds": item["horizon_seconds"],
                "model": item["model"],
                "observed_pooled_improvement_pct_vs_b2": obs,
                "unadjusted_block_wild_p": unadjusted,
                "familywise_max_stat_p": adjusted,
                "null_q95_cell_pct": float(np.quantile(null_stats[:, i], 0.95)),
                "null_q99_cell_pct": float(np.quantile(null_stats[:, i], 0.99)),
                "global_max_null_q95_pct": float(np.quantile(max_null, 0.95)),
                "global_max_null_q99_pct": float(np.quantile(max_null, 0.99)),
                "draws": MULTIPLICITY_DRAWS,
                "block_minutes": 60,
            }
        )

    corr = np.corrcoef(null_stats, rowvar=False)
    corr = np.nan_to_num(corr, nan=0.0, posinf=0.0, neginf=0.0)
    np.fill_diagonal(corr, 1.0)
    eigvals = np.linalg.eigvalsh((corr + corr.T) / 2.0)
    eigvals = np.clip(eigvals, 0.0, None)
    effective = (
        float((eigvals.sum() ** 2) / np.sum(eigvals**2))
        if np.sum(eigvals**2) > 0
        else float("nan")
    )
    strongest = int(np.argmax(observed_pct))
    summary = {
        "classification": REVIEW_CLASSIFICATION,
        "procedure": (
            "20,000-draw one-sided synchronized 1-hour block-wild max statistic; "
            "loss-advantage residuals centered within frozen cell; family-hour "
            "Rademacher sign shared by all related cells; families randomized independently"
        ),
        "draws": MULTIPLICITY_DRAWS,
        "master_seed": REVIEW_MASTER_SEED,
        "cell_count": n_cells,
        "effective_test_count_participation_ratio": effective,
        "null_correlation_eigenvalues": eigvals.tolist(),
        "global_max_q95_pct": float(np.quantile(max_null, 0.95)),
        "global_max_q99_pct": float(np.quantile(max_null, 0.99)),
        "strongest_observed_index": strongest,
        "strongest_observed_pct": float(observed_pct[strongest]),
        "strongest_unadjusted_p": rows[strongest]["unadjusted_block_wild_p"],
        "strongest_familywise_p": rows[strongest]["familywise_max_stat_p"],
        "strongest_survives_fwer_5pct": bool(
            rows[strongest]["familywise_max_stat_p"] <= 0.05
        ),
    }
    return rows, summary


def block_seed(component: str) -> int:
    return int.from_bytes(
        hashlib.sha256(f"{REVIEW_MASTER_SEED}|{component}".encode()).digest()[:8],
        "big",
    )


def circular_block_bootstrap(
    values: np.ndarray,
    block_rows: int,
    component: str,
) -> dict[str, Any]:
    clean = np.asarray(values, dtype=np.float64)
    clean = clean[np.isfinite(clean)]
    n = len(clean)
    effective = n / block_rows if block_rows > 0 else float("nan")
    result: dict[str, Any] = {
        "mean": float(clean.mean()) if n else float("nan"),
        "n_valid_times": n,
        "block_rows": block_rows,
        "effective_blocks": effective,
        "draws": BLOCK_DRAWS,
    }
    if n < 5 or block_rows < 1 or effective < 2.0:
        result.update(
            {
                "ci_low": None,
                "ci_high": None,
                "status": "INSUFFICIENT_EFFECTIVE_BLOCKS",
            }
        )
        return result

    rng = np.random.default_rng(block_seed(component))
    needed = math.ceil(n / block_rows)
    offsets = np.arange(block_rows, dtype=np.int64)
    draws = np.empty(BLOCK_DRAWS, dtype=np.float64)
    for draw in range(BLOCK_DRAWS):
        starts = rng.integers(0, n, size=needed)
        indices = (
            starts[:, None] + offsets[None, :]
        ) % n
        sample = clean[indices.ravel()[:n]]
        draws[draw] = float(sample.mean())
    result.update(
        {
            "ci_low": float(np.quantile(draws, 0.025)),
            "ci_high": float(np.quantile(draws, 0.975)),
            "status": "OK",
        }
    )
    return result


def block_sensitivity(states: list[dict[str, Any]]) -> list[dict[str, Any]]:
    wanted = [
        item
        for item in states
        if item["panel_id"] == US_PANEL
        and (
            (item["grid_seconds"], item["horizon_seconds"], item["model"])
            in {(5, 15, "M2"), (5, 30, "M4"), (15, 15, "M2")}
        )
    ]
    rows: list[dict[str, Any]] = []
    for item in wanted:
        grid = int(item["grid_seconds"])
        values = np.asarray(item["per_time_loss_advantage"], dtype=np.float64)
        for minutes in (5, 15, 30, 60):
            block_rows = int(math.ceil(minutes * 60 / grid))
            summary = circular_block_bootstrap(
                values,
                block_rows,
                (
                    f"{item['panel_id']}|g{grid}|h{item['horizon_seconds']}|"
                    f"{item['model']}|{minutes}m"
                ),
            )
            rows.append(
                {
                    "classification": REVIEW_CLASSIFICATION,
                    "family": item["family"],
                    "panel_id": item["panel_id"],
                    "grid_seconds": grid,
                    "horizon_seconds": item["horizon_seconds"],
                    "model": item["model"],
                    "requested_block_minutes": minutes,
                    **summary,
                }
            )
        optional_rows = int(math.ceil(120 * 60 / grid))
        optional_effective = int(np.isfinite(values).sum()) / optional_rows
        if optional_effective >= 2.0:
            summary = circular_block_bootstrap(
                values,
                optional_rows,
                (
                    f"{item['panel_id']}|g{grid}|h{item['horizon_seconds']}|"
                    f"{item['model']}|120m"
                ),
            )
            rows.append(
                {
                    "classification": REVIEW_CLASSIFICATION,
                    "family": item["family"],
                    "panel_id": item["panel_id"],
                    "grid_seconds": grid,
                    "horizon_seconds": item["horizon_seconds"],
                    "model": item["model"],
                    "requested_block_minutes": 120,
                    **summary,
                }
            )
    return rows


def temporal_concentration(primary: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    advantage = np.asarray(primary["loss_advantage_matrix"], dtype=np.float64)
    b2_loss = np.asarray(primary["b2_loss_matrix"], dtype=np.float64)
    valid = np.asarray(primary["valid_loss_matrix"], dtype=bool)
    times = np.asarray(primary["hold_q"], dtype=np.int64)

    by_hour: dict[int, dict[str, float | int]] = {}
    for row, timestamp in enumerate(times):
        mask = valid[row]
        if not mask.any():
            continue
        hour = int(timestamp // 3600 * 3600)
        bucket = by_hour.setdefault(
            hour,
            {
                "loss_advantage_sum": 0.0,
                "b2_loss_sum": 0.0,
                "observations": 0,
            },
        )
        bucket["loss_advantage_sum"] = float(bucket["loss_advantage_sum"]) + float(
            np.nansum(advantage[row, mask])
        )
        bucket["b2_loss_sum"] = float(bucket["b2_loss_sum"]) + float(
            np.nansum(b2_loss[row, mask])
        )
        bucket["observations"] = int(bucket["observations"]) + int(np.sum(mask))

    hours = sorted(by_hour)
    positives = sorted(
        (
            (hour, float(by_hour[hour]["loss_advantage_sum"]))
            for hour in hours
            if float(by_hour[hour]["loss_advantage_sum"]) > 0
        ),
        key=lambda pair: (-pair[1], pair[0]),
    )
    positive_total = float(sum(value for _hour, value in positives))
    rank_map = {hour: rank + 1 for rank, (hour, _value) in enumerate(positives)}

    rows: list[dict[str, Any]] = []
    for hour in hours:
        item = by_hour[hour]
        advantage_sum = float(item["loss_advantage_sum"])
        base_sum = float(item["b2_loss_sum"])
        rows.append(
            {
                "classification": REVIEW_CLASSIFICATION,
                "hour_utc": iso_utc(hour),
                "observations": int(item["observations"]),
                "loss_advantage_sum": advantage_sum,
                "b2_loss_sum": base_sum,
                "relative_improvement_pct_vs_b2": (
                    100.0 * advantage_sum / base_sum if base_sum > 0 else float("nan")
                ),
                "positive": advantage_sum > 0,
                "positive_rank": rank_map.get(hour),
            }
        )

    def share(k: int) -> float | None:
        if positive_total <= 0:
            return None
        return float(sum(value for _hour, value in positives[:k]) / positive_total)

    midpoint = len(hours) // 2
    first_hours = set(hours[:midpoint])
    second_hours = set(hours[midpoint:])

    def aggregate(selected: set[int]) -> dict[str, Any]:
        adv = float(
            sum(float(by_hour[h]["loss_advantage_sum"]) for h in selected)
        )
        base = float(sum(float(by_hour[h]["b2_loss_sum"]) for h in selected))
        obs = int(sum(int(by_hour[h]["observations"]) for h in selected))
        return {
            "loss_advantage_sum": adv,
            "b2_loss_sum": base,
            "observations": obs,
            "relative_improvement_pct_vs_b2": (
                100.0 * adv / base if base > 0 else float("nan")
            ),
        }

    remove_one = {hour for hour, _value in positives[:1]}
    remove_three = {hour for hour, _value in positives[:3]}
    all_hours = set(hours)
    summary = {
        "classification": REVIEW_CLASSIFICATION,
        "aggregation": "UTC 1-hour active blocks",
        "active_blocks": len(hours),
        "positive_blocks": len(positives),
        "cumulative_loss_advantage": float(
            sum(float(by_hour[h]["loss_advantage_sum"]) for h in hours)
        ),
        "positive_advantage_total": positive_total,
        "share_positive_advantage_top_1": share(1),
        "share_positive_advantage_top_3": share(3),
        "share_positive_advantage_top_5": share(5),
        "first_half": aggregate(first_hours),
        "second_half": aggregate(second_hours),
        "after_removing_strongest_1_positive_block": aggregate(all_hours - remove_one),
        "after_removing_strongest_3_positive_blocks": aggregate(all_hours - remove_three),
        "strongest_positive_hours_utc": [iso_utc(hour) for hour, _value in positives[:5]],
    }
    return rows, summary


def normalize_column(name: str) -> str:
    return "".join(ch for ch in name.lower() if ch.isalnum())


def extract_event_titles(value: Any) -> set[str]:
    titles: set[str] = set()
    if value is None:
        return titles
    if isinstance(value, str):
        text = value.strip()
        if not text or text.lower() == "nan":
            return titles
        try:
            parsed = json.loads(text)
        except (json.JSONDecodeError, TypeError):
            return titles
        return extract_event_titles(parsed)
    if isinstance(value, dict):
        for key, item in value.items():
            norm = normalize_column(str(key))
            if norm in {"title", "name", "question"} and item:
                text = str(item).strip()
                if text and len(text) < 500:
                    titles.add(text)
            if "event" in norm or isinstance(item, (dict, list, tuple)):
                titles.update(extract_event_titles(item))
        return titles
    if isinstance(value, (list, tuple, np.ndarray)):
        for item in value:
            titles.update(extract_event_titles(item))
    return titles


def metadata_semantics(
    selection: dict[str, Any],
    panel: Any,
    series: dict[str, Any],
    runner: ModuleType,
) -> tuple[dict[str, Any], dict[str, str]]:
    meta_path = locate_unique("markets.parquet")
    con = duckdb.connect()
    schema_rows = con.execute(
        f"DESCRIBE SELECT * FROM read_parquet('{str(meta_path).replace(chr(39), chr(39)*2)}')"
    ).fetchall()
    columns = [str(row[0]) for row in schema_rows]
    normalized = {normalize_column(col): col for col in columns}

    condition_col = None
    for candidate in ("conditionid", "condition"):
        if candidate in normalized:
            condition_col = normalized[candidate]
            break
    if condition_col is None:
        con.close()
        raise RuntimeError(f"market metadata lacks condition id column: {columns}")

    interesting = [
        col
        for col in columns
        if any(
            token in normalize_column(col)
            for token in (
                "condition",
                "question",
                "title",
                "event",
                "marketid",
                "slug",
                "start",
                "end",
                "close",
                "date",
                "time",
            )
        )
    ]
    if condition_col not in interesting:
        interesting.insert(0, condition_col)

    ids = list(panel.markets)
    placeholders = ",".join("?" for _ in ids)
    select_sql = ", ".join(f'"{col}"' for col in interesting)
    escaped = str(meta_path).replace("'", "''")
    query = (
        f"SELECT {select_sql} FROM read_parquet('{escaped}') "
        f'WHERE CAST("{condition_col}" AS VARCHAR) IN ({placeholders})'
    )
    cursor = con.execute(query, ids)
    result_columns = [str(item[0]) for item in cursor.description]
    result_records = [dict(zip(result_columns, row, strict=True)) for row in cursor.fetchall()]
    con.close()

    raw_rows: list[dict[str, Any]] = []
    question_map: dict[str, str] = {}
    event_title_candidates: set[str] = set()
    for record in result_records:
        clean = {key: jsonable(value) for key, value in record.items()}
        raw_rows.append(clean)
        cid = str(record[condition_col])

        question = ""
        for col in interesting:
            norm = normalize_column(col)
            value = record.get(col)
            if value is None:
                continue
            text = str(value)
            if norm == "question" and text and text.lower() != "nan":
                question = text
                break
        if not question:
            for col in interesting:
                norm = normalize_column(col)
                value = record.get(col)
                if value is None:
                    continue
                text = str(value)
                if "title" in norm and "event" not in norm and text.lower() != "nan":
                    question = text
                    break
        question_map[cid] = question

        for col in interesting:
            norm = normalize_column(col)
            if "event" not in norm:
                continue
            value = record.get(col)
            if value is None:
                continue
            if "title" in norm or "name" in norm:
                text = str(value).strip()
                if text and text.lower() != "nan" and len(text) < 500:
                    event_title_candidates.add(text)
            event_title_candidates.update(extract_event_titles(value))

    market_rows: list[dict[str, Any]] = []
    for cid in panel.markets:
        market = series[cid]
        market_rows.append(
            {
                "condition_id": cid,
                "question": question_map.get(cid, ""),
                "economic_observation_start_utc": iso_utc(int(market.times[0])),
                "economic_observation_end_utc": iso_utc(int(market.times[-1])),
                "economic_observation_count_seconds": int(len(market.times)),
                "selected_as_target": cid in set(selection["target_ids"]),
            }
        )

    grid = int(selection["grid_seconds"])
    q = runner.decision_times(panel, series, grid)
    train_mask, dev_mask, hold_mask, boundaries = runner.split_masks(q)

    def span(mask: np.ndarray) -> dict[str, Any]:
        selected = q[mask]
        return {
            "count": int(len(selected)),
            "start_utc": iso_utc(int(selected[0])) if len(selected) else None,
            "end_utc": iso_utc(int(selected[-1])) if len(selected) else None,
        }

    hold_times = q[hold_mask]
    hold_start = (
        datetime.fromtimestamp(int(hold_times[0]), UTC)
        if len(hold_times)
        else None
    )
    hold_end = (
        datetime.fromtimestamp(int(hold_times[-1]), UTC)
        if len(hold_times)
        else None
    )
    if hold_start is None or hold_end is None:
        calendar_relation = "UNKNOWN"
    elif hold_end < ELECTION_DAY_UTC:
        delta_days = (ELECTION_DAY_UTC - hold_end).total_seconds() / 86400
        calendar_relation = (
            "PRE_ELECTION_WEEKS"
            if delta_days >= 7
            else "PRE_ELECTION_DAYS"
        )
    elif hold_start >= ELECTION_DAY_UTC + timedelta(days=1):
        calendar_relation = "POST_ELECTION"
    elif hold_start < ELECTION_DAY_UTC and hold_end >= ELECTION_DAY_UTC:
        calendar_relation = "ELECTION_EVE_THROUGH_ELECTION_DAY_OR_LATER"
    else:
        calendar_relation = "ELECTION_DAY_OR_NIGHT"

    semantics = {
        "classification": REVIEW_CLASSIFICATION,
        "canonical_metadata_dataset": "polyleviathan/polyleviathan-market-meta",
        "metadata_file": meta_path.name,
        "metadata_schema": columns,
        "event_id": str(selection["panel_scope_value"]),
        "event_title_candidates_from_metadata": sorted(event_title_candidates),
        "panel_id": selection["panel_id"],
        "market_count": len(panel.markets),
        "markets": market_rows,
        "raw_selected_metadata": raw_rows,
        "raw_split_boundaries_epoch_seconds": {
            "train_dev": int(boundaries[0]),
            "dev_holdout": int(boundaries[1]),
        },
        "raw_split_boundaries_utc": {
            "train_dev": iso_utc(int(boundaries[0])),
            "dev_holdout": iso_utc(int(boundaries[1])),
        },
        "purge_embargo_seconds": int(runner.EMBARGO),
        "train_decision_times": span(train_mask),
        "dev_decision_times": span(dev_mask),
        "holdout_decision_times": span(hold_mask),
        "us_2024_general_election_calendar_anchor_utc": ELECTION_DAY_UTC.isoformat(),
        "holdout_calendar_relation": calendar_relation,
    }
    return semantics, question_map


def source_target_map(
    selection: dict[str, Any],
    state: dict[str, Any],
    question_map: dict[str, str],
) -> list[dict[str, Any]]:
    coef = np.asarray(state["coef"], dtype=np.float64)
    prepared = state["prepared"]
    lag = int(selection["lag_depth"])
    m = len(selection["panel_markets"])
    target_indices = list(state["target_indices"])
    groups = np.asarray(prepared.source_groups, dtype=np.int64)
    return_feature_count = m * lag
    rows: list[dict[str, Any]] = []

    for output_col, target_panel_index in enumerate(target_indices):
        target_id = str(selection["panel_markets"][target_panel_index])
        for source_index, source_id_raw in enumerate(selection["panel_markets"]):
            source_id = str(source_id_raw)
            group_mask = groups == source_index
            group_norm = float(np.linalg.norm(coef[group_mask, output_col]))
            return_positions = [
                source_index * lag + lag_index
                for lag_index in range(lag)
            ]
            return_coefficients = [
                float(coef[position, output_col])
                for position in return_positions
                if position < return_feature_count
            ]
            dominant_index = int(np.argmax(np.abs(return_coefficients)))
            dominant_value = return_coefficients[dominant_index]
            rows.append(
                {
                    "classification": REVIEW_CLASSIFICATION,
                    "target_condition_id": target_id,
                    "target_question": question_map.get(target_id, ""),
                    "source_condition_id": source_id,
                    "source_question": question_map.get(source_id, ""),
                    "is_own_history": source_index == target_panel_index,
                    "source_group_l2_norm": group_norm,
                    "return_coefficient_lag0": (
                        return_coefficients[0] if len(return_coefficients) > 0 else None
                    ),
                    "return_coefficient_lag1": (
                        return_coefficients[1] if len(return_coefficients) > 1 else None
                    ),
                    "return_coefficient_lag2": (
                        return_coefficients[2] if len(return_coefficients) > 2 else None
                    ),
                    "dominant_return_lag": dominant_index,
                    "dominant_return_coefficient": dominant_value,
                    "dominant_return_sign": (
                        "positive"
                        if dominant_value > 0
                        else "negative"
                        if dominant_value < 0
                        else "zero"
                    ),
                }
            )
    return rows


def verify_original_metrics(
    original: list[dict[str, str]],
    selection: dict[str, Any],
    challenger_mse: float,
    b2_mse: float,
) -> None:
    key = selection_key(selection)
    matches = [
        row
        for row in original
        if (
            str(row["family"]),
            str(row["panel_id"]),
            int(row["grid_seconds"]),
            int(row["horizon_seconds"]),
            str(row["model"]),
        )
        == key
    ]
    if len(matches) != 1:
        raise RuntimeError(f"original holdout match count {len(matches)} for {key}")
    row = matches[0]
    if not math.isclose(
        challenger_mse,
        float(row["holdout_mse"]),
        rel_tol=1e-11,
        abs_tol=1e-13,
    ):
        raise RuntimeError(f"challenger reproduction mismatch for {key}")
    if not math.isclose(
        b2_mse,
        float(row["b2_holdout_mse"]),
        rel_tol=1e-11,
        abs_tol=1e-13,
    ):
        raise RuntimeError(f"B2 reproduction mismatch for {key}")


def final_disposition(
    primary_b1_improvement: float,
    multiplicity_summary: dict[str, Any],
    block_rows: list[dict[str, Any]],
    temporal_summary: dict[str, Any],
) -> str:
    if not np.isfinite(primary_b1_improvement) or primary_b1_improvement <= 0:
        return "DOWNGRADE — DOES NOT BEAT B1"
    if not bool(multiplicity_summary["strongest_survives_fwer_5pct"]):
        return "DOWNGRADE — FAMILYWISE NULL NOT REJECTED"

    primary_sensitivity = [
        row
        for row in block_rows
        if row["panel_id"] == US_PANEL
        and int(row["grid_seconds"]) == US_PRIMARY_GRID
        and int(row["horizon_seconds"]) == US_PRIMARY_HORIZON
    ]
    stable_rows = [row for row in primary_sensitivity if row["status"] == "OK"]
    if stable_rows and any(float(row["ci_low"]) <= 0 for row in stable_rows):
        return "DOWNGRADE — DEPENDENCE-SENSITIVE"

    top_one = temporal_summary.get("share_positive_advantage_top_1")
    if top_one is not None and float(top_one) >= 0.50:
        return "RETAIN — TEMPORALLY CONCENTRATED"
    return "RETAIN — ROBUST WITHIN-EVENT JOINT CANDIDATE"


def main() -> None:
    WORK.mkdir(parents=True, exist_ok=True)
    prereg, freeze, original_holdout, runner = load_review_bundle()
    if prereg["classification"] != REVIEW_CLASSIFICATION:
        raise RuntimeError("wrong review classification")

    selections = list(freeze["selections"])
    family_series: dict[str, dict[str, Any]] = {}
    for family in FAMILIES:
        print(f"RECONSTRUCT {family}", flush=True)
        series, _meta, _audit = runner.reconstruction_family(family)
        family_series[family] = series

    b1_rows: list[dict[str, Any]] = []
    cell_states: list[dict[str, Any]] = []
    primary_selection: dict[str, Any] | None = None
    primary_state: dict[str, Any] | None = None

    for index, selection in enumerate(selections, start=1):
        family = str(selection["family"])
        panel = panel_from_selection(runner, selection)
        series = family_series[family]
        print(
            f"REVIEW CELL {index}/35 {selection['panel_id']} "
            f"g={selection['grid_seconds']} h={selection['horizon_seconds']} "
            f"{selection['model']}",
            flush=True,
        )
        metrics, state = runner.model_for_holdout(selection, panel, series, "logit")
        b1_pred = fit_frozen_b1(runner, selection, panel, series, state)
        b1_cmp = compare_vs_baseline(
            runner,
            state["y_hold"],
            state["pred"],
            b1_pred,
        )
        b2_cmp = compare_vs_baseline(
            runner,
            state["y_hold"],
            state["pred"],
            state["b2_pred"],
        )
        challenger_mse = float(metrics["mse"])
        b2_mse = float(metrics["baseline_mse"])
        verify_original_metrics(original_holdout, selection, challenger_mse, b2_mse)

        b1_mse = float(runner.masked_mse(state["y_hold"], b1_pred))
        b1_rows.append(
            {
                "classification": REVIEW_CLASSIFICATION,
                "family": family,
                "panel_id": selection["panel_id"],
                "panel_scope": selection["panel_scope"],
                "grid_seconds": selection["grid_seconds"],                "horizon_seconds": selection["horizon_seconds"],
                "model": selection["model"],
                "lag_depth": selection["lag_depth"],
                "alpha": selection["alpha"],
                "rank": selection["rank"],
                "frozen_b1_lag_depth": selection["baseline"]["B1"]["lag_depth"],
                "frozen_b1_alpha": selection["baseline"]["B1"]["alpha"],
                "frozen_b2_lag_depth": selection["baseline"]["B2"]["lag_depth"],
                "frozen_b2_alpha": selection["baseline"]["B2"]["alpha"],
                "b1_holdout_mse": b1_mse,
                "b2_holdout_mse": b2_mse,
                "challenger_holdout_mse": challenger_mse,
                "challenger_improvement_vs_b1": b1_cmp["pooled_improvement"],
                "challenger_improvement_vs_b2": b2_cmp["pooled_improvement"],
                "median_target_improvement_vs_b1": b1_cmp[
                    "median_target_improvement"
                ],
                "fraction_targets_helped_vs_b1": b1_cmp["fraction_targets_helped"],
            }
        )

        advantage, b2_loss, valid = target_time_losses(
            state["y_hold"],
            state["pred"],
            state["b2_pred"],
        )
        valid_count = int(np.sum(valid))
        if valid_count == 0:
            raise RuntimeError("no valid loss observations")
        raw_advantage_sum = float(np.nansum(advantage[valid]))
        b2_loss_sum = float(np.nansum(b2_loss[valid]))
        pooled_mean = raw_advantage_sum / valid_count
        centered = np.where(valid, advantage - pooled_mean, np.nan)
        hourly_centered = aggregate_hourly(
            state["hold_q"],
            centered,
            valid,
        )
        per_time = runner.per_time_loss_diff(
            state["y_hold"],
            state["pred"],
            state["b2_pred"],
        )
        cell_item = {
            "family": family,
            "panel_id": str(selection["panel_id"]),
            "grid_seconds": int(selection["grid_seconds"]),
            "horizon_seconds": int(selection["horizon_seconds"]),
            "model": str(selection["model"]),
            "loss_advantage_sum": raw_advantage_sum,
            "b2_loss_sum": b2_loss_sum,
            "valid_loss_cells": valid_count,
            "hourly_centered": hourly_centered,
            "per_time_loss_advantage": per_time,
            "loss_advantage_matrix": advantage,
            "b2_loss_matrix": b2_loss,
            "valid_loss_matrix": valid,
            "hold_q": state["hold_q"],
        }
        cell_states.append(cell_item)

        if (
            selection["panel_id"] == US_PANEL
            and int(selection["grid_seconds"]) == US_PRIMARY_GRID
            and int(selection["horizon_seconds"]) == US_PRIMARY_HORIZON
            and str(selection["model"]) == "M2"
        ):
            primary_selection = selection
            primary_state = state

    if primary_selection is None or primary_state is None:
        raise RuntimeError("strongest frozen US selection not found")

    write_csv(WORK / "b1_holdout_comparison.csv", b1_rows)

    multiplicity_rows, multiplicity_summary = compute_multiplicity(cell_states)
    write_csv(WORK / "multiplicity_falsification.csv", multiplicity_rows)
    (WORK / "multiplicity_summary.json").write_text(
        json.dumps(multiplicity_summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    sensitivity_rows = block_sensitivity(cell_states)
    write_csv(WORK / "block_sensitivity.csv", sensitivity_rows)

    primary_item = next(
        item
        for item in cell_states
        if item["panel_id"] == US_PANEL
        and item["grid_seconds"] == US_PRIMARY_GRID
        and item["horizon_seconds"] == US_PRIMARY_HORIZON
        and item["model"] == "M2"
    )
    temporal_rows, temporal_summary = temporal_concentration(primary_item)
    write_csv(WORK / "temporal_concentration.csv", temporal_rows)
    (WORK / "temporal_concentration_summary.json").write_text(
        json.dumps(temporal_summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    primary_panel = panel_from_selection(runner, primary_selection)
    semantics, question_map = metadata_semantics(
        primary_selection,
        primary_panel,
        family_series["US_2024"],
        runner,
    )
    (WORK / "us_event_semantics.json").write_text(
        json.dumps(semantics, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    coefficient_rows = source_target_map(
        primary_selection,
        primary_state,
        question_map,
    )
    write_csv(WORK / "source_target_map.csv", coefficient_rows)
    (WORK / "source_target_map.json").write_text(
        json.dumps(coefficient_rows, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    primary_key = (
        "US_2024",
        US_PANEL,
        US_PRIMARY_GRID,
        US_PRIMARY_HORIZON,
        "M2",
    )
    primary_b1_row = next(
        row
        for row in b1_rows
        if (
            row["family"],
            row["panel_id"],
            int(row["grid_seconds"]),
            int(row["horizon_seconds"]),
            row["model"],
        )
        == primary_key
    )
    original_primary = next(
        row
        for row in original_holdout
        if (
            row["family"] == "US_2024"
            and row["panel_id"] == US_PANEL
            and int(row["grid_seconds"]) == US_PRIMARY_GRID
            and int(row["horizon_seconds"]) == US_PRIMARY_HORIZON
            and row["model"] == "M2"
        )
    )

    interpretation = {
        "classification": REVIEW_CLASSIFICATION,
        "cell": {
            "family": "US_2024",
            "panel_id": US_PANEL,
            "grid_seconds": US_PRIMARY_GRID,
            "horizon_seconds": US_PRIMARY_HORIZON,
            "model": "M2",
        },
        "cross_sectional_ic": float(original_primary["cross_sectional_ic"]),
        "median_time_series_ic": float(original_primary["median_time_series_ic"]),
        "sign_accuracy": float(original_primary["sign_accuracy"]),
        "interpretation": (
            "Evidence is primarily per-market time-series forecasting through time, "
            "not contemporaneous cross-sectional ranking, if the reported IC signs "
            "and magnitudes remain as frozen."
        ),
    }
    (WORK / "interpretation_metrics.json").write_text(
        json.dumps(interpretation, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    disposition = final_disposition(
        float(primary_b1_row["challenger_improvement_vs_b1"]),
        multiplicity_summary,
        sensitivity_rows,
        temporal_summary,
    )
    summary = {
        "classification": REVIEW_CLASSIFICATION,
        "experiment_id": "EXPERIMENT-005C",
        "review_preregistration_sha256": REVIEW_PREREG_SHA256,
        "original_runner_sha256": ORIGINAL_RUNNER_SHA256,
        "pre_holdout_freeze_sha256": PRE_HOLDOUT_FREEZE_SHA256,
        "original_holdout_results_sha256": ORIGINAL_HOLDOUT_SHA256,
        "frozen_selection_count": len(selections),
        "primary_cell": {
            "family": "US_2024",
            "panel_id": US_PANEL,
            "grid_seconds": US_PRIMARY_GRID,
            "horizon_seconds": US_PRIMARY_HORIZON,
            "model": "M2",
        },
        "primary_challenger_improvement_vs_b1": primary_b1_row[
            "challenger_improvement_vs_b1"
        ],
        "primary_challenger_improvement_vs_b2": primary_b1_row[
            "challenger_improvement_vs_b2"
        ],
        "multiplicity": multiplicity_summary,
        "temporal_concentration": temporal_summary,
        "event_semantics": {
            "event_id": semantics["event_id"],
            "event_title_candidates_from_metadata": semantics[
                "event_title_candidates_from_metadata"
            ],
            "holdout_calendar_relation": semantics["holdout_calendar_relation"],
            "holdout_decision_times": semantics["holdout_decision_times"],
        },
        "interpretation": interpretation,
        "final_disposition": disposition,
        "programme_rule": (
            "This review may only retain, weaken, downgrade or reject "
            "existing 005C conclusions."
        ),
        "original_005c_outputs_modified": False,
    }
    (WORK / "review_summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    manifest = {
        "classification": REVIEW_CLASSIFICATION,
        "experiment_id": "EXPERIMENT-005C",
        "review_preregistration_sha256": REVIEW_PREREG_SHA256,
        "original_runner_sha256": ORIGINAL_RUNNER_SHA256,
        "pre_holdout_freeze_sha256": PRE_HOLDOUT_FREEZE_SHA256,
        "original_holdout_results_sha256": ORIGINAL_HOLDOUT_SHA256,
        "outputs": {},
    }
    for path in sorted(WORK.iterdir()):
        if path.is_file() and path.name != "review_run_manifest.json":
            manifest["outputs"][path.name] = {
                "bytes": path.stat().st_size,
                "sha256": sha256(path),
            }
    (WORK / "review_run_manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    print(json.dumps(summary, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()