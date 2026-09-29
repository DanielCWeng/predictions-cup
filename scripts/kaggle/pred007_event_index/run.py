from __future__ import annotations

import csv
import hashlib
import json
import math
import subprocess
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np

INPUT = Path("/kaggle/input")
OUT = Path("/kaggle/working/pred007_event_index")
OUT.mkdir(parents=True, exist_ok=True)

EXPECTED_CORPUS = "e3d95735262b0be3e9fe7dd32fb54e53dc7e65b403ef9b5a2261ac107a8ea4c4"
EXPECTED_IDENTITY = "e89fe8e25dcc494dad3ed96fe6672ab9f247c01eb70d4c0270a913f18a200a72"
EXPECTED_PREREG = "64af4c1427afa983a13da6a11d5721b1b6496b0306ce1b07ab6e2f19df9e7e70"
EXPECTED_REL = "e93033568ac3d3045399e6b630d37bb30a829c9c2fad3ef5da8ac31a7516732e"
EXPECTED_CODE_COMMIT = "b2ba5e2c3e63d57bd6e74c0c5861bad67c983e1b"

NS = 1_000_000_000
MIN_SOURCES = 3
FRESHNESS_SECONDS = 120
EMBARGO_SECONDS = 30
TRAIN_FRACTION = 0.60
SHOCK_QUANTILE = 0.90
PRIMARY_HORIZONS = (1, 2, 5, 10)
SECONDARY_HORIZONS = (30,)
HORIZONS = PRIMARY_HORIZONS + SECONDARY_HORIZONS
BOOTSTRAP_DRAWS = 1000
MIN_TRAIN_ROWS = 200
MIN_CELL_EVAL_ROWS = 20
GLOBAL_MIN_EVAL_ROWS = 1000
GLOBAL_MIN_TARGETS = 10


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def one(name: str) -> Path:
    matches = sorted(INPUT.rglob(name))
    if len(matches) != 1:
        raise SystemExit(f"expected exactly one {name}, got {matches}")
    return matches[0]


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def ns(value: datetime) -> int:
    return int(value.timestamp() * NS)


def seed(label: str) -> int:
    return int.from_bytes(hashlib.sha256(label.encode()).digest()[:8], "big")


def mse(y: np.ndarray, pred: np.ndarray) -> float:
    return float(np.mean((y - pred) ** 2))


def sign_accuracy(y: np.ndarray, pred: np.ndarray) -> float | None:
    mask = np.isfinite(y) & np.isfinite(pred) & (y != 0)
    if not np.any(mask):
        return None
    return float(np.mean(np.sign(y[mask]) == np.sign(pred[mask])))


def bootstrap_ci(values: np.ndarray, label: str) -> dict[str, float | None]:
    values = np.asarray(values, dtype=float)
    values = values[np.isfinite(values)]
    if len(values) < 2:
        return {"lower": None, "median": None, "upper": None, "clusters": int(len(values))}
    rng = np.random.default_rng(seed(label))
    draws = np.empty(BOOTSTRAP_DRAWS, dtype=float)
    n = len(values)
    for i in range(BOOTSTRAP_DRAWS):
        draws[i] = float(np.mean(values[rng.integers(0, n, size=n)]))
    q = np.quantile(draws, [0.025, 0.5, 0.975])
    return {
        "lower": float(q[0]),
        "median": float(q[1]),
        "upper": float(q[2]),
        "clusters": int(n),
    }


def coalesced_event_times(series_by_token: dict[str, Any], source_tokens: tuple[str, ...], start_ns: int, end_ns: int) -> np.ndarray:
    chunks: list[np.ndarray] = []
    for token in source_tokens:
        series = series_by_token.get(token)
        if series is None or len(series.times_ns) == 0:
            continue
        raw = series.times_ns[(series.times_ns >= start_ns) & (series.times_ns < end_ns)]
        if len(raw) == 0:
            continue
        bucket_end = ((raw + NS - 1) // NS) * NS
        chunks.append(bucket_end)
    if not chunks:
        return np.asarray([], dtype=np.int64)
    times = np.unique(np.concatenate(chunks))
    return times[(times >= start_ns) & (times < end_ns)]


def aggregate_features(
    series_by_token: dict[str, Any],
    source_tokens: tuple[str, ...],
    times: np.ndarray,
    train_cutoff: int,
    feature_move: Any,
    seconds_ns: Any,
) -> dict[str, Any]:
    if len(times) == 0:
        return {}
    counts = {}
    for token in source_tokens:
        series = series_by_token[token]
        counts[token] = int(np.count_nonzero(series.times_ns < train_cutoff))
    top_source = sorted(source_tokens, key=lambda tok: (-counts[tok], tok))[0]
    raw_w = {tok: math.sqrt(1.0 + counts[tok]) for tok in source_tokens}

    n = len(times)
    sums = {name: np.zeros(n, dtype=float) for name in ("ew1", "ew5", "attn1", "attn5", "delay1", "delay5")}
    den = {name: np.zeros(n, dtype=float) for name in ("ew1", "ew5", "attn1", "attn5", "delay1", "delay5")}
    top1 = np.full(n, np.nan)
    top5 = np.full(n, np.nan)
    freshness_ns = seconds_ns(FRESHNESS_SECONDS)

    for token in source_tokens:
        series = series_by_token[token]
        move1, valid1 = feature_move(series, times, 1, freshness_ns)
        move5, valid5 = feature_move(series, times, 5, freshness_ns)
        delayed_times = times - seconds_ns(300)
        delay1, dvalid1 = feature_move(series, delayed_times, 1, freshness_ns)
        delay5, dvalid5 = feature_move(series, delayed_times, 5, freshness_ns)
        weight = raw_w[token]

        sums["ew1"][valid1] += move1[valid1]
        den["ew1"][valid1] += 1.0
        sums["ew5"][valid5] += move5[valid5]
        den["ew5"][valid5] += 1.0
        sums["attn1"][valid1] += weight * move1[valid1]
        den["attn1"][valid1] += weight
        sums["attn5"][valid5] += weight * move5[valid5]
        den["attn5"][valid5] += weight
        sums["delay1"][dvalid1] += weight * delay1[dvalid1]
        den["delay1"][dvalid1] += weight
        sums["delay5"][dvalid5] += weight * delay5[dvalid5]
        den["delay5"][dvalid5] += weight

        if token == top_source:
            top1 = move1
            top5 = move5

    out: dict[str, Any] = {"top_source": top_source, "source_update_counts_train": counts}
    for name in sums:
        values = np.full(n, np.nan)
        need = MIN_SOURCES if name != "top1" and name != "top5" else 1
        mask = den[name] > 0
        values[mask] = sums[name][mask] / den[name][mask]
        out[name] = values
    out["source_support_1s"] = den["ew1"]
    out["source_support_5s"] = den["ew5"]
    return out


def fit_predict(
    fit_ols: Any,
    predict_ols: Any,
    x: np.ndarray,
    y: np.ndarray,
    train: np.ndarray,
    evaluation: np.ndarray,
) -> np.ndarray:
    fit = fit_ols(x[train], y[train])
    return predict_ols(x[evaluation], fit)


def main() -> None:
    wheel = one("predictions_cup-0.1.0-py3-none-any.whl")
    commit = one("COMMIT.txt")
    prereg_path = one("preregistration.json")
    rel_path = one("relationship_inventory.csv")
    manifest_path = one("corpus_manifest.json")

    if commit.read_text(encoding="utf-8").strip() != EXPECTED_CODE_COMMIT:
        raise RuntimeError("EXP003 code marker mismatch")
    if sha256(prereg_path) != EXPECTED_PREREG:
        raise RuntimeError("EXP003 preregistration hash mismatch")
    if sha256(rel_path) != EXPECTED_REL:
        raise RuntimeError("EXP003 relationship inventory hash mismatch")
    if sha256(manifest_path) != EXPECTED_CORPUS:
        raise RuntimeError("DATA-001 corpus manifest hash mismatch")

    corpus = manifest_path.parent.parent
    identity_path = corpus / "schema_version=1" / "market_identity.csv"
    if sha256(identity_path) != EXPECTED_IDENTITY:
        raise RuntimeError("DATA-001 market identity hash mismatch")

    subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", "--no-deps", str(wheel)])
    from predictions_cup.learning.historical_alpha import (
        _feature_move,
        _fit_ols,
        _future_target,
        _predict_ols,
        _seconds_ns,
        load_quote_series,
    )

    prereg = json.loads(prereg_path.read_text(encoding="utf-8"))
    relationships = read_csv(rel_path)
    identities = read_csv(identity_path)
    identity = {
        (row["regime_id"], row["token_id"]): row
        for row in identities
        if row.get("outcome") == "Yes"
    }

    declared: dict[str, set[str]] = defaultdict(set)
    for row in relationships:
        declared[row["regime_id"]].add(row["target_token_id"])
        declared[row["regime_id"]].add(row["reference_token_id"])
    for regime, rows in prereg["families"]["lowrank"]["lowrank_universe"].items():
        declared[regime].update(row["token_id"] for row in rows)

    coverage: list[dict[str, Any]] = []
    cells: list[dict[str, Any]] = []
    blocks: dict[tuple[int, str, int], dict[str, Any]] = {}
    participating_targets: dict[int, set[str]] = defaultdict(set)

    for regime in sorted(prereg["regimes"]):
        start, end = map(dt, prereg["regimes"][regime])
        start_ns, end_ns = ns(start), ns(end)
        split_ns = start_ns + int((end_ns - start_ns) * TRAIN_FRACTION)
        train_cutoff = split_ns - EMBARGO_SECONDS * NS
        eval_start = split_ns + EMBARGO_SECONDS * NS
        event_family = prereg["event_families"][regime]

        frozen_tokens = tuple(
            sorted(
                token
                for token in declared[regime]
                if (regime, token) in identity and identity[(regime, token)].get("event_id")
            )
        )
        if not frozen_tokens:
            coverage.append({"regime_id": regime, "status": "NO_FROZEN_TOKENS"})
            continue

        quote_by_token = load_quote_series(corpus, regime, frozen_tokens, epsilon=1e-6)
        available = tuple(token for token in frozen_tokens if token in quote_by_token)
        by_target_event: dict[str, tuple[str, ...]] = {}
        for target in available:
            target_event = identity[(regime, target)]["event_id"]
            sources = tuple(
                token
                for token in available
                if token != target and identity[(regime, token)]["event_id"] != target_event
            )
            if len(sources) >= MIN_SOURCES:
                by_target_event.setdefault(target_event, sources)

        feature_cache: dict[str, tuple[np.ndarray, dict[str, Any], float]] = {}
        for target_event, sources in sorted(by_target_event.items()):
            times = coalesced_event_times(quote_by_token, sources, start_ns, end_ns)
            if len(times) == 0:
                continue
            features = aggregate_features(
                quote_by_token,
                sources,
                times,
                train_cutoff,
                _feature_move,
                _seconds_ns,
            )
            source_ok = (features["source_support_1s"] >= MIN_SOURCES) & (features["source_support_5s"] >= MIN_SOURCES)
            threshold_sample = features["attn1"][
                source_ok & (times < train_cutoff) & np.isfinite(features["attn1"])
            ]
            if len(threshold_sample) < MIN_TRAIN_ROWS:
                continue
            threshold = float(np.quantile(np.abs(threshold_sample), SHOCK_QUANTILE))
            feature_cache[target_event] = (times, features, threshold)

        for target in available:
            target_meta = identity[(regime, target)]
            target_event = target_meta["event_id"]
            cached = feature_cache.get(target_event)
            if cached is None:
                coverage.append(
                    {
                        "regime_id": regime,
                        "event_family": event_family,
                        "target_token_id": target,
                        "target_event_id": target_event,
                        "status": "INSUFFICIENT_CROSS_EVENT_SOURCE_SUPPORT",
                    }
                )
                continue
            times, features, threshold = cached
            sources = by_target_event[target_event]
            own1, own1_valid = _feature_move(
                quote_by_token[target], times, 1, _seconds_ns(FRESHNESS_SECONDS)
            )
            own5, own5_valid = _feature_move(
                quote_by_token[target], times, 5, _seconds_ns(FRESHNESS_SECONDS)
            )
            common_feature_valid = (
                own1_valid
                & own5_valid
                & (features["source_support_1s"] >= MIN_SOURCES)
                & (features["source_support_5s"] >= MIN_SOURCES)
                & np.isfinite(features["top1"])
                & np.isfinite(features["top5"])
                & np.isfinite(features["ew1"])
                & np.isfinite(features["ew5"])
                & np.isfinite(features["attn1"])
                & np.isfinite(features["attn5"])
                & np.isfinite(features["delay1"])
                & np.isfinite(features["delay5"])
            )
            shock = np.abs(features["attn1"]) >= threshold

            x_own = np.column_stack([own1, own5])
            x_top = np.column_stack([own1, own5, features["top1"], features["top5"]])
            x_ew = np.column_stack([own1, own5, features["ew1"], features["ew5"]])
            x_attn = np.column_stack([own1, own5, features["attn1"], features["attn5"]])
            x_delay = np.column_stack([own1, own5, features["delay1"], features["delay5"]])

            coverage_row = {
                "regime_id": regime,
                "event_family": event_family,
                "target_token_id": target,
                "target_condition_id": target_meta.get("condition_id", ""),
                "target_event_id": target_event,
                "target_market_family": target_meta.get("market_family", ""),
                "source_count": len(sources),
                "top_source_token_id": features["top_source"],
                "shock_threshold_abs_logit_1s": threshold,
                "decision_event_rows": int(len(times)),
                "status": "EVALUATED_OR_PARTIAL",
            }
            target_cell_count = 0

            for horizon in HORIZONS:
                y, _, _, _, _ = _future_target(
                    quote_by_token[target],
                    times,
                    horizon,
                    _seconds_ns(FRESHNESS_SECONDS),
                )
                finite = common_feature_valid & np.isfinite(y)
                train = finite & (times < train_cutoff)
                evaluation = finite & (times >= eval_start) & shock & (times + horizon * NS < end_ns)
                n_train = int(np.count_nonzero(train))
                n_eval = int(np.count_nonzero(evaluation))
                if n_train < MIN_TRAIN_ROWS or n_eval < MIN_CELL_EVAL_ROWS:
                    cells.append(
                        {
                            "regime_id": regime,
                            "event_family": event_family,
                            "target_token_id": target,
                            "target_event_id": target_event,
                            "horizon_seconds": horizon,
                            "status": "INSUFFICIENT_ROWS",
                            "train_rows": n_train,
                            "eval_rows": n_eval,
                            "source_count": len(sources),
                            "shock_threshold_abs_logit_1s": threshold,
                        }
                    )
                    continue
                try:
                    pred_own = fit_predict(_fit_ols, _predict_ols, x_own, y, train, evaluation)
                    pred_top = fit_predict(_fit_ols, _predict_ols, x_top, y, train, evaluation)
                    pred_ew = fit_predict(_fit_ols, _predict_ols, x_ew, y, train, evaluation)
                    pred_attn = fit_predict(_fit_ols, _predict_ols, x_attn, y, train, evaluation)
                    pred_delay = fit_predict(_fit_ols, _predict_ols, x_delay, y, train, evaluation)
                except (ValueError, np.linalg.LinAlgError):
                    cells.append(
                        {
                            "regime_id": regime,
                            "event_family": event_family,
                            "target_token_id": target,
                            "target_event_id": target_event,
                            "horizon_seconds": horizon,
                            "status": "MODEL_FIT_FAILED",
                            "train_rows": n_train,
                            "eval_rows": n_eval,
                        }
                    )
                    continue

                yy = y[evaluation]
                tt = times[evaluation]
                own_loss = (yy - pred_own) ** 2
                top_loss = (yy - pred_top) ** 2
                ew_loss = (yy - pred_ew) ** 2
                attn_loss = (yy - pred_attn) ** 2
                delay_loss = (yy - pred_delay) ** 2
                baseline_mse = float(np.mean(own_loss))
                attn_mse = float(np.mean(attn_loss))

                cells.append(
                    {
                        "regime_id": regime,
                        "event_family": event_family,
                        "target_token_id": target,
                        "target_event_id": target_event,
                        "horizon_seconds": horizon,
                        "status": "EVALUATED",
                        "train_rows": n_train,
                        "eval_rows": n_eval,
                        "source_count": len(sources),
                        "top_source_token_id": features["top_source"],
                        "shock_threshold_abs_logit_1s": threshold,
                        "own_mse": baseline_mse,
                        "top_source_mse": float(np.mean(top_loss)),
                        "ew_index_mse": float(np.mean(ew_loss)),
                        "attn_index_mse": attn_mse,
                        "delayed_attn_mse": float(np.mean(delay_loss)),
                        "attn_relative_improvement_vs_own": (
                            (baseline_mse - attn_mse) / baseline_mse if baseline_mse > 0 else None
                        ),
                        "attn_delta_mse_vs_top_source": float(np.mean(top_loss - attn_loss)),
                        "ew_delta_mse_vs_top_source": float(np.mean(top_loss - ew_loss)),
                        "attn_prediction_target_corr": (
                            float(np.corrcoef(pred_attn, yy)[0, 1])
                            if len(yy) > 2 and np.std(pred_attn) > 0 and np.std(yy) > 0
                            else None
                        ),
                        "attn_sign_accuracy": sign_accuracy(yy, pred_attn),
                    }
                )
                target_cell_count += 1
                participating_targets[horizon].add(f"{regime}|{target}")

                minute = tt // (60 * NS)
                for m in np.unique(minute):
                    mask = minute == m
                    key = (horizon, regime, int(m))
                    rec = blocks.setdefault(
                        key,
                        {
                            "event_family": event_family,
                            "n": 0,
                            "own_attn_sum": 0.0,
                            "top_attn_sum": 0.0,
                            "top_ew_sum": 0.0,
                            "delay_attn_sum": 0.0,
                            "own_loss_sum": 0.0,
                            "attn_loss_sum": 0.0,
                        },
                    )
                    rec["n"] += int(np.count_nonzero(mask))
                    rec["own_attn_sum"] += float(np.sum(own_loss[mask] - attn_loss[mask]))
                    rec["top_attn_sum"] += float(np.sum(top_loss[mask] - attn_loss[mask]))
                    rec["top_ew_sum"] += float(np.sum(top_loss[mask] - ew_loss[mask]))
                    rec["delay_attn_sum"] += float(np.sum(delay_loss[mask] - attn_loss[mask]))
                    rec["own_loss_sum"] += float(np.sum(own_loss[mask]))
                    rec["attn_loss_sum"] += float(np.sum(attn_loss[mask]))

            coverage_row["evaluated_horizon_cells"] = target_cell_count
            coverage.append(coverage_row)

    summaries: list[dict[str, Any]] = []
    primary_survivors: list[int] = []
    slow_only = False

    for horizon in HORIZONS:
        recs = [
            (regime, minute, rec)
            for (h, regime, minute), rec in blocks.items()
            if h == horizon and rec["n"] > 0
        ]
        if not recs:
            summaries.append({"horizon_seconds": horizon, "status": "NO_EVALUATED_BLOCKS"})
            continue
        own_attn = np.asarray([rec["own_attn_sum"] / rec["n"] for _, _, rec in recs])
        top_attn = np.asarray([rec["top_attn_sum"] / rec["n"] for _, _, rec in recs])
        top_ew = np.asarray([rec["top_ew_sum"] / rec["n"] for _, _, rec in recs])
        delay_attn = np.asarray([rec["delay_attn_sum"] / rec["n"] for _, _, rec in recs])
        own_loss = np.asarray([rec["own_loss_sum"] / rec["n"] for _, _, rec in recs])
        attn_loss = np.asarray([rec["attn_loss_sum"] / rec["n"] for _, _, rec in recs])
        total_rows = int(sum(rec["n"] for _, _, rec in recs))
        target_count = len(participating_targets[horizon])

        by_family: dict[str, list[float]] = defaultdict(list)
        for _, _, rec in recs:
            by_family[rec["event_family"]].append(rec["own_attn_sum"] / rec["n"])
        family_mean = {fam: float(np.mean(vals)) for fam, vals in sorted(by_family.items())}
        positive_families = sum(value > 0 for value in family_mean.values())

        ci_own = bootstrap_ci(own_attn, f"PRED007|{horizon}|OWN_ATTN")
        ci_top = bootstrap_ci(top_attn, f"PRED007|{horizon}|TOP_ATTN")
        ci_ew = bootstrap_ci(top_ew, f"PRED007|{horizon}|TOP_EW")
        ci_delay = bootstrap_ci(delay_attn, f"PRED007|{horizon}|DELAY_ATTN")

        mean_own = float(np.mean(own_loss))
        mean_attn = float(np.mean(attn_loss))
        relative = (mean_own - mean_attn) / mean_own if mean_own > 0 else None
        passed = bool(
            relative is not None
            and relative > 0
            and ci_own["lower"] is not None
            and ci_own["lower"] > 0
            and ci_top["lower"] is not None
            and ci_top["lower"] > 0
            and positive_families >= 2
            and total_rows >= GLOBAL_MIN_EVAL_ROWS
            and target_count >= GLOBAL_MIN_TARGETS
            and float(np.mean(delay_attn)) > 0
        )
        if horizon in PRIMARY_HORIZONS and passed:
            primary_survivors.append(horizon)
        if horizon in SECONDARY_HORIZONS and passed:
            slow_only = True

        summaries.append(
            {
                "horizon_seconds": horizon,
                "status": "EVALUATED",
                "shock_eval_rows": total_rows,
                "target_markets": target_count,
                "event_minute_clusters": len(recs),
                "own_mse_cluster_mean": mean_own,
                "attn_mse_cluster_mean": mean_attn,
                "attn_relative_mse_improvement_vs_own": relative,
                "attn_delta_mse_vs_own": float(np.mean(own_attn)),
                "attn_delta_mse_vs_top_source": float(np.mean(top_attn)),
                "ew_delta_mse_vs_top_source": float(np.mean(top_ew)),
                "attn_delta_mse_vs_delayed_attn": float(np.mean(delay_attn)),
                "attn_vs_own_bootstrap": ci_own,
                "attn_vs_top_source_bootstrap": ci_top,
                "ew_vs_top_source_bootstrap": ci_ew,
                "attn_vs_delayed_bootstrap": ci_delay,
                "family_mean_delta_mse_vs_own": family_mean,
                "positive_event_families": positive_families,
                "primary_gate_pass": passed,
            }
        )

    if primary_survivors:
        disposition = "HISTORICAL_INDEX_MECHANISM_CANDIDATE_REQUIRES_FUTURE_LIVE_CONFIRMATION"
    elif slow_only:
        disposition = "SLOW_INDEX_EFFECT_ONLY"
    else:
        disposition = "NO_MACHINE_SPEED_INDEX_SIGNAL"

    result = {
        "schema_version": 1,
        "experiment_id": "PRED-007",
        "classification": "HISTORICAL_MECHANISM_EXPLORATION_ONLY",
        "prior_events_previously_exposed": True,
        "fresh_confirmation": False,
        "same_event_siblings_excluded": True,
        "decision_clock": "ONE_SECOND_BUCKET_ENDS_WITH_AT_LEAST_ONE_CROSS_EVENT_SOURCE_BBO_CHANGE",
        "train_fraction": TRAIN_FRACTION,
        "embargo_seconds": EMBARGO_SECONDS,
        "shock_quantile_train_abs_attn_1s": SHOCK_QUANTILE,
        "primary_horizons_seconds": list(PRIMARY_HORIZONS),
        "secondary_horizons_seconds": list(SECONDARY_HORIZONS),
        "bootstrap_draws": BOOTSTRAP_DRAWS,
        "summaries": summaries,
        "primary_survivor_horizons_seconds": primary_survivors,
        "disposition": disposition,
    }

    with (OUT / "CELL_RESULTS.csv").open("w", newline="", encoding="utf-8") as handle:
        fields: list[str] = []
        for row in cells:
            for key in row:
                if key not in fields:
                    fields.append(key)
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(cells)

    with (OUT / "COVERAGE.csv").open("w", newline="", encoding="utf-8") as handle:
        fields = []
        for row in coverage:
            for key in row:
                if key not in fields:
                    fields.append(key)
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(coverage)

    (OUT / "PRED007_RESULTS.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (OUT / "PRED007_VERDICT.json").write_text(
        json.dumps(
            {
                "experiment_id": "PRED-007",
                "disposition": disposition,
                "primary_survivor_horizons_seconds": primary_survivors,
                "historical_only": True,
                "future_live_confirmation_required": bool(primary_survivors),
                "post_result_rescue_permitted": False,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    print("PRED007_RESULT=" + json.dumps(result, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
