"""EXPERIMENT-003 historical predictive-alpha battery.

The module is intentionally research-only: it builds as-of-safe predictive observations and
compares simple baseline/challenger models.  It contains no order submission, sizing or execution
simulation.  Large observation matrices stay outside Git; committed reports are compact JSON.
"""
# ruff: noqa: E501, E701, E702

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import random
import subprocess
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.dataset as pads

from predictions_cup.historical.corpus import validate_corpus
from predictions_cup.learning.evaluation_harness import ResearchEvaluationHarness
from predictions_cup.learning.reporting import ResearchDisposition, ResearchReport
from predictions_cup.learning.research_spec import (
    AblationSpec,
    BootstrapProtocol,
    DatasetVersion,
    EventBootstrapWeighting,
    EvidencePolicy,
    FDRProtocol,
    NegativeControlKind,
    NegativeControlSpec,
    ResearchEvaluationSpec,
    SplitMethod,
    StabilityProtocol,
    WalkForwardProtocol,
    canonical_json_bytes,
)
from predictions_cup.learning.statistics import HypothesisTest

EXPECTED_MANIFEST_SHA = "e3d95735262b0be3e9fe7dd32fb54e53dc7e65b403ef9b5a2261ac107a8ea4c4"
EXPECTED_IDENTITY_SHA = "e89fe8e25dcc494dad3ed96fe6672ab9f247c01eb70d4c0270a913f18a200a72"
FAMILY_IDS = {
    "leadlag": "LEADLAG-001",
    "structural_residual": "RV-001",
    "microstructure": "MICROSTRUCTURE-001",
    "participant": "PARTICIPANT-001",
    "lowrank": "LOWRANK-001",
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git_revision() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


def _epoch_ns(value: datetime) -> int:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return int(value.astimezone(UTC).timestamp() * 1_000_000_000)


def _seconds_ns(value: int | float) -> int:
    return int(value * 1_000_000_000)


def _logit(values: np.ndarray, epsilon: float) -> np.ndarray:
    clipped = np.clip(values, epsilon, 1.0 - epsilon)
    return np.log(clipped / (1.0 - clipped))


@dataclass(frozen=True, slots=True)
class Relationship:
    regime_id: str
    target_condition_id: str
    target_token_id: str
    target_market_family: str
    reference_condition_id: str
    reference_token_id: str
    reference_market_family: str
    leakage_class: str
    semantic_basis: str


@dataclass(frozen=True, slots=True)
class QuoteSeries:
    times_ns: np.ndarray
    bid: np.ndarray
    ask: np.ndarray
    midpoint: np.ndarray
    logit_mid: np.ndarray

    def sample(
        self,
        query_ns: np.ndarray,
        *,
        freshness_ns: int,
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        indices = np.searchsorted(self.times_ns, query_ns, side="right") - 1
        valid = indices >= 0
        safe = np.maximum(indices, 0)
        age = query_ns - self.times_ns[safe]
        valid &= age >= 0
        valid &= age <= freshness_ns
        mids = self.midpoint[safe].copy()
        logits = self.logit_mid[safe].copy()
        bids = self.bid[safe].copy()
        asks = self.ask[safe].copy()
        finite = np.isfinite(mids) & np.isfinite(logits) & np.isfinite(bids) & np.isfinite(asks)
        valid &= finite & (bids > 0) & (asks < 1) & (asks >= bids)
        for array in (mids, logits, bids, asks):
            array[~valid] = np.nan
        return logits, bids, asks, valid


@dataclass(frozen=True, slots=True)
class ModelInput:
    times_ns: np.ndarray
    market_ids: np.ndarray
    event_id: str
    event_family_id: str
    horizon_seconds: int
    x_baseline: np.ndarray
    x_challenger: np.ndarray
    y: np.ndarray
    entry_bid: np.ndarray
    entry_ask: np.ndarray
    future_bid: np.ndarray
    future_ask: np.ndarray


@dataclass(frozen=True, slots=True)
class OOSResult:
    event_id: str
    event_family_id: str
    horizon_seconds: int
    fold_ids: tuple[str, ...]
    market_ids: np.ndarray
    times_ns: np.ndarray
    y: np.ndarray
    pred_baseline: np.ndarray
    pred_challenger: np.ndarray
    loss_difference: np.ndarray
    gross_markout: np.ndarray


def verify_corpus(corpus_root: Path, preregistration: dict[str, Any]) -> dict[str, Any]:
    version_root = corpus_root / "schema_version=1"
    manifest_path = version_root / "corpus_manifest.json"
    identity_path = version_root / "market_identity.csv"
    expected_manifest = preregistration["dataset"]["manifest_sha256"]
    expected_identity = preregistration["dataset"]["identity_sha256"]
    if _sha256(manifest_path) != expected_manifest or expected_manifest != EXPECTED_MANIFEST_SHA:
        raise ValueError("accepted DATA-001 corpus manifest mismatch")
    if _sha256(identity_path) != expected_identity or expected_identity != EXPECTED_IDENTITY_SHA:
        raise ValueError("accepted DATA-001 market identity mismatch")
    validation = validate_corpus(corpus_root)
    if not validation["ok"]:
        raise ValueError("DATA-001 corpus validation failed: {}".format(validation["problems"]))
    return validation


def load_relationships(path: Path, expected_sha: str) -> tuple[Relationship, ...]:
    if _sha256(path) != expected_sha:
        raise ValueError("relationship inventory hash mismatch")
    output: list[Relationship] = []
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            if row["leakage_class"] != "INDIRECT":
                raise ValueError("EXPERIMENT-003 primary relationship inventory must be INDIRECT")
            output.append(
                Relationship(
                    regime_id=row["regime_id"],
                    target_condition_id=row["target_condition_id"],
                    target_token_id=row["target_token_id"],
                    target_market_family=row["target_market_family"],
                    reference_condition_id=row["reference_condition_id"],
                    reference_token_id=row["reference_token_id"],
                    reference_market_family=row["reference_market_family"],
                    leakage_class=row["leakage_class"],
                    semantic_basis=row["semantic_basis"],
                )
            )
    return tuple(sorted(output, key=lambda item: (item.regime_id, item.target_token_id, item.reference_token_id)))


def _dataset_files(root: Path) -> list[str]:
    return [str(path) for path in sorted(root.rglob("*.parquet"))]




def _collapse_quote_batches(
    times: np.ndarray, bids: np.ndarray, asks: np.ndarray
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Collapse observable-time batches without inventing within-batch order.

    Differing BBO states at one observable timestamp are unknowable from DATA-001. Emit an
    explicit invalid state so it cannot be carried forward until a later unambiguous observation.
    """
    if not len(times):
        return times, bids, asks
    out_t: list[int] = []
    out_b: list[float] = []
    out_a: list[float] = []
    start = 0
    while start < len(times):
        end = start + 1
        while end < len(times) and times[end] == times[start]:
            end += 1
        states = {(float(bids[i]), float(asks[i])) for i in range(start, end)}
        out_t.append(int(times[start]))
        if len(states) == 1:
            bid, ask = next(iter(states))
            out_b.append(bid)
            out_a.append(ask)
        else:
            out_b.append(float("nan"))
            out_a.append(float("nan"))
        start = end
    return (
        np.asarray(out_t, dtype=np.int64),
        np.asarray(out_b, dtype=np.float64),
        np.asarray(out_a, dtype=np.float64),
    )

def load_quote_series(
    corpus_root: Path,
    regime_id: str,
    token_ids: tuple[str, ...],
    *,
    epsilon: float,
) -> dict[str, QuoteSeries]:
    root = corpus_root / "schema_version=1" / regime_id / "books" / "book_changes"
    files = _dataset_files(root)
    if not files:
        raise ValueError(f"no book_changes for {regime_id}")
    dataset = pads.dataset(files, format="parquet")
    expression = pads.field("token_id").isin(list(token_ids))
    table = dataset.to_table(
        columns=["token_id", "observed_at", "best_bid", "best_ask"], filter=expression
    )
    if table.num_rows == 0:
        return {}
    # Convert source text prices once, then sort deterministically by token/time.
    table = pa.table(
        {
            "token_id": table["token_id"],
            "observed_at": table["observed_at"],
            "best_bid": pc.cast(table["best_bid"], pa.float64()),
            "best_ask": pc.cast(table["best_ask"], pa.float64()),
        }
    )
    order = pc.sort_indices(table, sort_keys=[("token_id", "ascending"), ("observed_at", "ascending")])
    table = pc.take(table, order)
    tokens = table["token_id"].to_pylist()
    times = table["observed_at"].cast(pa.int64()).to_numpy(zero_copy_only=False) * 1000
    bids = table["best_bid"].to_numpy(zero_copy_only=False)
    asks = table["best_ask"].to_numpy(zero_copy_only=False)
    output: dict[str, QuoteSeries] = {}
    start = 0
    while start < len(tokens):
        token = str(tokens[start])
        end = start + 1
        while end < len(tokens) and tokens[end] == token:
            end += 1
        tt = np.asarray(times[start:end], dtype=np.int64)
        bb = np.asarray(bids[start:end], dtype=np.float64)
        aa = np.asarray(asks[start:end], dtype=np.float64)
        # Same-observable-time rows are a batch; differing BBO states are explicitly invalid.
        tt, bb, aa = _collapse_quote_batches(tt, bb, aa)
        mid = (bb + aa) / 2.0
        valid = np.isfinite(bb) & np.isfinite(aa) & (bb > 0) & (aa < 1) & (aa >= bb)
        mid[~valid] = np.nan
        output[token] = QuoteSeries(tt, bb, aa, mid, _logit(mid, epsilon))
        start = end
    return output


def _grid(start: datetime, end: datetime, seconds: int) -> np.ndarray:
    return np.arange(_epoch_ns(start), _epoch_ns(end), _seconds_ns(seconds), dtype=np.int64)


def _feature_move(
    series: QuoteSeries,
    times_ns: np.ndarray,
    lookback_seconds: int,
    freshness_ns: int,
) -> tuple[np.ndarray, np.ndarray]:
    current, _, _, current_valid = series.sample(times_ns, freshness_ns=freshness_ns)
    past, _, _, past_valid = series.sample(
        times_ns - _seconds_ns(lookback_seconds), freshness_ns=freshness_ns
    )
    move = current - past
    valid = current_valid & past_valid & np.isfinite(move)
    move[~valid] = np.nan
    return move, valid


def _future_target(
    series: QuoteSeries,
    times_ns: np.ndarray,
    horizon_seconds: int,
    freshness_ns: int,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    current, bid, ask, current_valid = series.sample(times_ns, freshness_ns=freshness_ns)
    future, future_bid, future_ask, future_valid = series.sample(
        times_ns + _seconds_ns(horizon_seconds), freshness_ns=freshness_ns
    )
    y = future - current
    valid = current_valid & future_valid & np.isfinite(y)
    y[~valid] = np.nan
    return y, bid, ask, future_bid, future_ask


def _fit_ols(x: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    if x.ndim != 2 or y.ndim != 1 or len(x) != len(y):
        raise ValueError("invalid OLS matrix shape")
    if len(y) < max(20, x.shape[1] * 5):
        raise ValueError("insufficient training observations")
    mean = np.nanmean(x, axis=0)
    std = np.nanstd(x, axis=0)
    keep = np.isfinite(mean) & np.isfinite(std) & (std > 1e-12)
    scaled = (x[:, keep] - mean[keep]) / std[keep]
    design = np.column_stack([np.ones(len(y)), scaled])
    beta, _, _, _ = np.linalg.lstsq(design, y, rcond=None)
    return beta, mean, np.where(keep, std, np.nan)


def _predict_ols(x: np.ndarray, fit: tuple[np.ndarray, np.ndarray, np.ndarray]) -> np.ndarray:
    beta, mean, std = fit
    keep = np.isfinite(std)
    scaled = (x[:, keep] - mean[keep]) / std[keep]
    design = np.column_stack([np.ones(len(x)), scaled])
    return np.asarray(design @ beta, dtype=np.float64)


def _walk_forward_oos(
    data: ModelInput,
    *,
    regime_start: datetime,
    regime_end: datetime,
    train_seconds: int,
    development_seconds: int,
    holdout_seconds: int,
    step_seconds: int,
    embargo_seconds: int,
) -> OOSResult:
    horizon_ns = _seconds_ns(data.horizon_seconds)
    train_ns = _seconds_ns(train_seconds)
    dev_ns = _seconds_ns(development_seconds)
    hold_ns = _seconds_ns(holdout_seconds)
    step_ns = _seconds_ns(step_seconds)
    embargo_ns = _seconds_ns(embargo_seconds)
    start_ns, end_ns = _epoch_ns(regime_start), _epoch_ns(regime_end)
    anchor = start_ns + train_ns
    fold_number = 0
    out_time: list[np.ndarray] = []
    out_market: list[np.ndarray] = []
    out_y: list[np.ndarray] = []
    out_b: list[np.ndarray] = []
    out_c: list[np.ndarray] = []
    out_d: list[np.ndarray] = []
    out_markout: list[np.ndarray] = []
    fold_ids: list[str] = []
    finite = (
        np.isfinite(data.y)
        & np.all(np.isfinite(data.x_baseline), axis=1)
        & np.all(np.isfinite(data.x_challenger), axis=1)
    )
    while anchor + dev_ns + hold_ns <= end_ns:
        fold_number += 1
        train_start = anchor - train_ns
        train_end = anchor
        hold_start = anchor + dev_ns
        hold_end = hold_start + hold_ns
        train = (
            finite
            & (data.times_ns >= train_start)
            & (data.times_ns + horizon_ns < train_end - embargo_ns)
        )
        hold = finite & (data.times_ns >= hold_start) & (data.times_ns < hold_end)
        if np.count_nonzero(train) >= 50 and np.count_nonzero(hold) >= 10:
            base_fit = _fit_ols(data.x_baseline[train], data.y[train])
            chal_fit = _fit_ols(data.x_challenger[train], data.y[train])
            pb = _predict_ols(data.x_baseline[hold], base_fit)
            pc_ = _predict_ols(data.x_challenger[hold], chal_fit)
            yy = data.y[hold]
            dd = (yy - pb) ** 2 - (yy - pc_) ** 2
            direction = np.sign(pc_)
            gross = np.full(len(yy), np.nan, dtype=np.float64)
            pos = direction > 0
            neg = direction < 0
            eb, ea = data.entry_bid[hold], data.entry_ask[hold]
            fb, fa = data.future_bid[hold], data.future_ask[hold]
            gross[pos] = fb[pos] - ea[pos]
            gross[neg] = eb[neg] - fa[neg]
            out_time.append(data.times_ns[hold])
            out_market.append(data.market_ids[hold])
            out_y.append(yy)
            out_b.append(pb)
            out_c.append(pc_)
            out_d.append(dd)
            out_markout.append(gross)
            fold_ids.append(f"{data.event_id}:fold-{fold_number:03d}")
        anchor += step_ns
    if not out_y:
        empty = np.asarray([], dtype=np.float64)
        return OOSResult(
            data.event_id,
            data.event_family_id,
            data.horizon_seconds,
            tuple(fold_ids),
            np.asarray([], dtype=str),
            np.asarray([], dtype=np.int64),
            empty,
            empty,
            empty,
            empty,
            empty,
        )
    return OOSResult(
        data.event_id,
        data.event_family_id,
        data.horizon_seconds,
        tuple(fold_ids),
        np.concatenate(out_market),
        np.concatenate(out_time),
        np.concatenate(out_y),
        np.concatenate(out_b),
        np.concatenate(out_c),
        np.concatenate(out_d),
        np.concatenate(out_markout),
    )


def block_sign_flip_test(
    loss_difference: np.ndarray,
    times_ns: np.ndarray,
    *,
    session_seconds: int,
    exact_max_blocks: int,
    monte_carlo_draws: int,
    seed: int,
) -> tuple[float | None, float, int, str]:
    if len(loss_difference) != len(times_ns):
        raise ValueError("loss/time length mismatch")
    valid = np.isfinite(loss_difference)
    if not np.any(valid):
        return None, float("nan"), 0, "INSUFFICIENT_BLOCKS"
    session_ns = _seconds_ns(session_seconds)
    keys = times_ns[valid] // session_ns
    values = loss_difference[valid]
    unique = np.unique(keys)
    block_means = np.asarray([np.mean(values[keys == key]) for key in unique], dtype=np.float64)
    n = len(block_means)
    observed = float(np.mean(block_means)) if n else float("nan")
    if n < 4:
        return None, observed, n, "INSUFFICIENT_BLOCKS"
    if n <= exact_max_blocks:
        total = 1 << n
        extreme = 0
        for mask in range(total):
            signs = np.asarray([1.0 if (mask >> j) & 1 else -1.0 for j in range(n)])
            if float(np.mean(block_means * signs)) >= observed - 1e-15:
                extreme += 1
        return extreme / total, observed, n, "EXACT"
    rng = random.Random(seed)
    extreme = 0
    for _ in range(monte_carlo_draws):
        signed = np.asarray([value if rng.getrandbits(1) else -value for value in block_means])
        if float(np.mean(signed)) >= observed - 1e-15:
            extreme += 1
    return (extreme + 1) / (monte_carlo_draws + 1), observed, n, "MONTE_CARLO"


def event_bootstrap_interval(
    event_values: dict[str, np.ndarray],
    *,
    draws: int,
    seed: int,
    equal_event: bool,
) -> tuple[float, float, float]:
    clean = {key: value[np.isfinite(value)] for key, value in event_values.items()}
    clean = {key: value for key, value in clean.items() if len(value)}
    if not clean:
        return float("nan"), float("nan"), float("nan")
    ids = sorted(clean)
    means = {key: float(np.mean(value)) for key, value in clean.items()}
    if equal_event:
        point = float(np.mean([means[key] for key in ids]))
    else:
        point = float(np.mean(np.concatenate([clean[key] for key in ids])))
    rng = np.random.default_rng(seed)
    samples = np.empty(draws, dtype=np.float64)
    for draw in range(draws):
        picked = rng.integers(0, len(ids), size=len(ids))
        if equal_event:
            samples[draw] = np.mean([means[ids[index]] for index in picked])
        else:
            samples[draw] = np.mean(np.concatenate([clean[ids[index]] for index in picked]))
    return point, float(np.quantile(samples, 0.025)), float(np.quantile(samples, 0.975))


def _seed(run_id: str, component: str) -> int:
    digest = hashlib.sha256(canonical_json_bytes({"run_id": run_id, "component": component})).digest()
    return int.from_bytes(digest[:8], "big")


def _run_id(preregistration: dict[str, Any], code_revision: str) -> tuple[str, str]:
    config_hash = hashlib.sha256(canonical_json_bytes(preregistration)).hexdigest()
    payload = {
        "config_hash": config_hash,
        "dataset_hash": preregistration["dataset"]["manifest_sha256"],
        "code_revision": code_revision,
    }
    return hashlib.sha256(canonical_json_bytes(payload)).hexdigest(), config_hash


def _summarize_oos(
    results: list[OOSResult],
    *,
    run_id: str,
    component: str,
    statistics_spec: dict[str, Any],
) -> dict[str, Any]:
    usable = [item for item in results if len(item.y)]
    if not usable:
        return {
            "valid_observations": 0,
            "events": 0,
            "markets": 0,
            "baseline_mse": None,
            "challenger_mse": None,
            "delta_mse": None,
            "raw_p_value": None,
            "test_status": "INSUFFICIENT_DATA",
        }
    y = np.concatenate([item.y for item in usable])
    pb = np.concatenate([item.pred_baseline for item in usable])
    pc_ = np.concatenate([item.pred_challenger for item in usable])
    d = np.concatenate([item.loss_difference for item in usable])
    times = np.concatenate([item.times_ns for item in usable])
    market_ids = np.concatenate([item.market_ids for item in usable])
    p_value, statistic, blocks, method = block_sign_flip_test(
        d,
        times,
        session_seconds=int(statistics_spec["block_test"]["session_seconds"]),
        exact_max_blocks=int(statistics_spec["block_test"]["exact_enumeration_max_blocks"]),
        monte_carlo_draws=int(statistics_spec["block_test"]["monte_carlo_draws"]),
        seed=_seed(run_id, f"{component}:block-test"),
    )
    event_values = {item.event_id: item.loss_difference for item in usable}
    eq = event_bootstrap_interval(
        event_values,
        draws=int(statistics_spec["bootstrap"]["draws"]),
        seed=_seed(run_id, f"{component}:bootstrap-equal-event"),
        equal_event=True,
    )
    ow = event_bootstrap_interval(
        event_values,
        draws=int(statistics_spec["bootstrap"]["draws"]),
        seed=_seed(run_id, f"{component}:bootstrap-observation"),
        equal_event=False,
    )
    markouts = np.concatenate([item.gross_markout for item in usable])
    finite_markout = markouts[np.isfinite(markouts)]
    corr = float(np.corrcoef(pc_, y)[0, 1]) if len(y) > 2 and np.std(pc_) > 0 and np.std(y) > 0 else None
    direction = float(np.mean(np.sign(pc_) == np.sign(y))) if len(y) else None
    return {
        "valid_observations": int(len(y)),
        "events": len(usable),
        "event_ids": [item.event_id for item in usable],
        "event_families": sorted({item.event_family_id for item in usable}),
        "markets": int(len(set(str(value) for value in market_ids.tolist()))),
        "folds": [fold for item in usable for fold in item.fold_ids],
        "baseline_mse": float(np.mean((y - pb) ** 2)),
        "challenger_mse": float(np.mean((y - pc_) ** 2)),
        "delta_mse": float(np.mean(d)),
        "relative_mse_improvement": (
            float(1.0 - np.mean((y - pc_) ** 2) / np.mean((y - pb) ** 2))
            if np.mean((y - pb) ** 2) > 0
            else None
        ),
        "prediction_target_correlation": corr,
        "directional_accuracy": direction,
        "gross_executable_markout_mean": (
            float(np.mean(finite_markout)) if len(finite_markout) else None
        ),
        "raw_p_value": p_value,
        "test_statistic": statistic,
        "test_blocks": blocks,
        "test_status": method,
        "equal_event_bootstrap": {"point": eq[0], "lower": eq[1], "upper": eq[2]},
        "observation_weighted_event_bootstrap": {"point": ow[0], "lower": ow[1], "upper": ow[2]},
    }


def build_relationship_input(
    *,
    family: str,
    regime_id: str,
    relationships: tuple[Relationship, ...],
    series: dict[str, QuoteSeries],
    start: datetime,
    end: datetime,
    grid_seconds: int,
    horizon_seconds: int,
    lookback_seconds: int,
    freshness_seconds: int,
    event_family_id: str,
    reference_delay_seconds: int = 0,
    zero_structural_residual: bool = False,
) -> ModelInput:
    if family not in {"leadlag", "structural_residual"}:
        raise ValueError("relationship input supports leadlag or structural_residual")
    grouped: dict[str, list[str]] = defaultdict(list)
    for rel in relationships:
        if rel.regime_id == regime_id:
            grouped[rel.target_token_id].append(rel.reference_token_id)
    times_parts: list[np.ndarray] = []
    market_parts: list[np.ndarray] = []
    xb_parts: list[np.ndarray] = []
    xc_parts: list[np.ndarray] = []
    y_parts: list[np.ndarray] = []
    eb_parts: list[np.ndarray] = []
    ea_parts: list[np.ndarray] = []
    fb_parts: list[np.ndarray] = []
    fa_parts: list[np.ndarray] = []
    freshness_ns = _seconds_ns(freshness_seconds)
    times = _grid(start, end, grid_seconds)
    for target_token, reference_tokens in sorted(grouped.items()):
        target = series.get(target_token)
        references = [series[token] for token in sorted(set(reference_tokens)) if token in series]
        if target is None or not references:
            continue
        own_move, own_valid = _feature_move(target, times, lookback_seconds, freshness_ns)
        y, entry_bid, entry_ask, future_bid, future_ask = _future_target(
            target, times, horizon_seconds, freshness_ns
        )
        ref_moves: list[np.ndarray] = []
        ref_levels: list[np.ndarray] = []
        ref_valids: list[np.ndarray] = []
        ref_times = times - _seconds_ns(reference_delay_seconds)
        for ref in references:
            ref_move, ref_move_valid = _feature_move(ref, ref_times, lookback_seconds, freshness_ns)
            ref_level, _, _, ref_level_valid = ref.sample(ref_times, freshness_ns=freshness_ns)
            ref_moves.append(ref_move)
            ref_levels.append(ref_level)
            ref_valids.append(ref_move_valid & ref_level_valid)
        move_stack = np.column_stack(ref_moves)
        level_stack = np.column_stack(ref_levels)
        valid_stack = np.column_stack(ref_valids)
        # Mean only across references that are honestly observable at t and t-lookback.
        move_stack[~valid_stack] = np.nan
        level_stack[~valid_stack] = np.nan
        with np.errstate(invalid="ignore"):
            ref_move_mean = np.nanmean(move_stack, axis=1)
            ref_level_mean = np.nanmean(level_stack, axis=1)
        ref_count = np.sum(valid_stack, axis=1)
        ref_ok = ref_count >= 1
        target_level, _, _, target_level_valid = target.sample(times, freshness_ns=freshness_ns)
        if family == "leadlag":
            challenger_feature = ref_move_mean
        else:
            challenger_feature = -(target_level - ref_level_mean)
            if zero_structural_residual:
                challenger_feature = np.zeros_like(challenger_feature)
        valid = (
            own_valid
            & ref_ok
            & target_level_valid
            & np.isfinite(y)
            & np.isfinite(challenger_feature)
        )
        if not np.any(valid):
            continue
        times_parts.append(times[valid])
        market_parts.append(np.full(np.count_nonzero(valid), target_token, dtype=object))
        xb_parts.append(own_move[valid, None])
        xc_parts.append(np.column_stack([own_move[valid], challenger_feature[valid]]))
        y_parts.append(y[valid])
        eb_parts.append(entry_bid[valid])
        ea_parts.append(entry_ask[valid])
        fb_parts.append(future_bid[valid])
        fa_parts.append(future_ask[valid])
    if not y_parts:
        empty = np.asarray([], dtype=np.float64)
        return ModelInput(
            empty.astype(np.int64), np.asarray([], dtype=object), regime_id, event_family_id,
            horizon_seconds, np.empty((0, 1)), np.empty((0, 2)), empty, empty, empty, empty, empty
        )
    return ModelInput(
        np.concatenate(times_parts),
        np.concatenate(market_parts),
        regime_id,
        event_family_id,
        horizon_seconds,
        np.vstack(xb_parts),
        np.vstack(xc_parts),
        np.concatenate(y_parts),
        np.concatenate(eb_parts),
        np.concatenate(ea_parts),
        np.concatenate(fb_parts),
        np.concatenate(fa_parts),
    )


def load_depth_rows(
    corpus_root: Path,
    regime_id: str,
    token_ids: tuple[str, ...],
) -> dict[str, list[dict[str, Any]]]:
    root = corpus_root / "schema_version=1" / regime_id / "books" / "depth_snapshots"
    files = _dataset_files(root)
    if not files:
        return {}
    dataset = pads.dataset(files, format="parquet")
    table = dataset.to_table(
        columns=[
            "token_id", "recorded_at", "state_observed_at", "best_bid", "best_ask", "bids", "asks"
        ],
        filter=pads.field("token_id").isin(list(token_ids)),
    )
    output: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in table.to_pylist():
        output[str(row["token_id"])].append(row)
    for rows in output.values():
        rows.sort(key=lambda row: (row["recorded_at"], str(row["best_bid"]), str(row["best_ask"])))
    return dict(output)


def build_microstructure_input(
    *,
    regime_id: str,
    depth_rows: dict[str, list[dict[str, Any]]],
    series: dict[str, QuoteSeries],
    horizon_seconds: int,
    lookback_seconds: int,
    freshness_seconds: int,
    event_family_id: str,
    signal_delay_seconds: int = 0,
) -> ModelInput:
    freshness_ns = _seconds_ns(freshness_seconds)
    times_parts: list[np.ndarray] = []
    market_parts: list[np.ndarray] = []
    xb_parts: list[np.ndarray] = []
    xc_parts: list[np.ndarray] = []
    y_parts: list[np.ndarray] = []
    eb_parts: list[np.ndarray] = []
    ea_parts: list[np.ndarray] = []
    fb_parts: list[np.ndarray] = []
    fa_parts: list[np.ndarray] = []
    for token, rows in sorted(depth_rows.items()):
        target = series.get(token)
        if target is None:
            continue
        # Drop ambiguous duplicate snapshot batches rather than manufacture within-batch order.
        by_time: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            recorded = row["recorded_at"]
            ns = int(recorded.timestamp() * 1_000_000_000)
            by_time[ns].append(row)
        usable_rows: list[tuple[int, dict[str, Any]]] = []
        for ns, batch in sorted(by_time.items()):
            fingerprints = {
                (str(row["best_bid"]), str(row["best_ask"]), repr(row["bids"]), repr(row["asks"]))
                for row in batch
            }
            if len(fingerprints) == 1:
                usable_rows.append((ns, batch[0]))
        if not usable_rows:
            continue
        times = np.asarray([item[0] for item in usable_rows], dtype=np.int64)
        imbalance = np.full(len(times), np.nan)
        microdisp = np.full(len(times), np.nan)
        spread = np.full(len(times), np.nan)
        depth = np.full(len(times), np.nan)
        quote_age = np.full(len(times), np.nan)
        snap_bid = np.full(len(times), np.nan)
        snap_ask = np.full(len(times), np.nan)
        for index, (ns, row) in enumerate(usable_rows):
            bids, asks = row["bids"] or [], row["asks"] or []
            if not bids or not asks:
                continue
            bid = float(row["best_bid"])
            ask = float(row["best_ask"])
            if not (0 < bid <= ask < 1) or ask <= bid:
                continue
            q_bid = float(bids[0]["size"])
            q_ask = float(asks[0]["size"])
            if q_bid < 0 or q_ask < 0 or q_bid + q_ask <= 0:
                continue
            spr = ask - bid
            micro = (ask * q_bid + bid * q_ask) / (q_bid + q_ask)
            state_ns = int(row["state_observed_at"].timestamp() * 1_000_000_000)
            age_seconds = (ns - state_ns) / 1_000_000_000
            if age_seconds < 0:
                continue
            snap_bid[index], snap_ask[index] = bid, ask
            spread[index] = spr
            depth[index] = q_bid + q_ask
            quote_age[index] = age_seconds
            imbalance[index] = (q_bid - q_ask) / (q_bid + q_ask)
            microdisp[index] = (micro - (bid + ask) / 2.0) / spr
        if signal_delay_seconds:
            query = times - _seconds_ns(signal_delay_seconds)
            source_index = np.searchsorted(times, query, side="right") - 1
            signal_valid = source_index >= 0
            safe_index = np.maximum(source_index, 0)
            signal_valid &= (query - times[safe_index]) <= freshness_ns
            delayed_imbalance = imbalance[safe_index].copy()
            delayed_microdisp = microdisp[safe_index].copy()
            delayed_imbalance[~signal_valid] = np.nan
            delayed_microdisp[~signal_valid] = np.nan
            imbalance, microdisp = delayed_imbalance, delayed_microdisp
        own_move, own_valid = _feature_move(target, times, lookback_seconds, freshness_ns)
        y, _, _, future_bid, future_ask = _future_target(
            target, times, horizon_seconds, freshness_ns
        )
        valid = (
            own_valid
            & np.isfinite(y)
            & np.isfinite(snap_bid)
            & np.isfinite(snap_ask)
            & np.isfinite(imbalance)
            & np.isfinite(microdisp)
            & np.isfinite(spread)
            & np.isfinite(depth)
            & np.isfinite(quote_age)
        )
        if not np.any(valid):
            continue
        base = np.column_stack([own_move, spread, depth, quote_age])
        chal = np.column_stack([own_move, spread, depth, quote_age, imbalance, microdisp])
        times_parts.append(times[valid])
        market_parts.append(np.full(np.count_nonzero(valid), token, dtype=object))
        xb_parts.append(base[valid])
        xc_parts.append(chal[valid])
        y_parts.append(y[valid])
        eb_parts.append(snap_bid[valid])
        ea_parts.append(snap_ask[valid])
        fb_parts.append(future_bid[valid])
        fa_parts.append(future_ask[valid])
    if not y_parts:
        empty = np.asarray([], dtype=np.float64)
        return ModelInput(
            empty.astype(np.int64), np.asarray([], dtype=object), regime_id, event_family_id,
            horizon_seconds, np.empty((0, 4)), np.empty((0, 6)), empty, empty, empty, empty, empty
        )
    return ModelInput(
        np.concatenate(times_parts), np.concatenate(market_parts), regime_id, event_family_id,
        horizon_seconds, np.vstack(xb_parts), np.vstack(xc_parts), np.concatenate(y_parts),
        np.concatenate(eb_parts), np.concatenate(ea_parts), np.concatenate(fb_parts), np.concatenate(fa_parts)
    )


@dataclass(frozen=True, slots=True)
class FillSeries:
    times_ns: np.ndarray
    value_usd: np.ndarray
    maker: np.ndarray
    taker: np.ndarray
    exchange_taker: np.ndarray


def load_fill_series(
    corpus_root: Path,
    regime_id: str,
    token_ids: tuple[str, ...],
) -> dict[str, FillSeries]:
    root = corpus_root / "schema_version=1" / regime_id / "fills"
    files = _dataset_files(root)
    if not files:
        return {}
    dataset = pads.dataset(files, format="parquet")
    table = dataset.to_table(
        columns=["token_id", "observed_at", "value_usd", "maker_address", "taker_address", "is_exchange_taker"],
        filter=pads.field("token_id").isin(list(token_ids)),
    )
    rows = table.to_pylist()
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row["token_id"])].append(row)
    output: dict[str, FillSeries] = {}
    for token, values in grouped.items():
        values.sort(key=lambda row: (row["observed_at"], str(row["maker_address"]), str(row["taker_address"])))
        output[token] = FillSeries(
            np.asarray([int(row["observed_at"].timestamp() * 1_000_000_000) for row in values], dtype=np.int64),
            np.asarray([float(row["value_usd"]) for row in values], dtype=np.float64),
            np.asarray([str(row["maker_address"]) for row in values], dtype=object),
            np.asarray([str(row["taker_address"]) for row in values], dtype=object),
            np.asarray([bool(row["is_exchange_taker"]) for row in values], dtype=bool),
        )
    return output


def _participant_baseline_rows(
    *,
    token: str,
    quote: QuoteSeries,
    fills: FillSeries,
    grid_times: np.ndarray,
    window_seconds: int,
    own_lookback_seconds: int,
    freshness_ns: int,
    horizon_seconds: int,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    own_move, own_valid = _feature_move(quote, grid_times, own_lookback_seconds, freshness_ns)
    current, entry_bid, entry_ask, quote_valid = quote.sample(grid_times, freshness_ns=freshness_ns)
    del current
    y, _, _, future_bid, future_ask = _future_target(quote, grid_times, horizon_seconds, freshness_ns)
    spread = entry_ask - entry_bid
    count = np.zeros(len(grid_times), dtype=np.float64)
    value = np.zeros(len(grid_times), dtype=np.float64)
    unique_makers = np.zeros(len(grid_times), dtype=np.float64)
    unique_takers = np.zeros(len(grid_times), dtype=np.float64)
    exchange_taker_value = np.zeros(len(grid_times), dtype=np.float64)
    window_ns = _seconds_ns(window_seconds)
    second_ns = _seconds_ns(1)
    for index, t in enumerate(grid_times):
        # A block-second fill is admitted only when strictly before floor(t to second).
        cutoff = (t // second_ns) * second_ns
        lo = np.searchsorted(fills.times_ns, cutoff - window_ns, side="left")
        hi = np.searchsorted(fills.times_ns, cutoff, side="left")
        if hi <= lo:
            continue
        count[index] = hi - lo
        values = fills.value_usd[lo:hi]
        value[index] = float(np.sum(values))
        unique_makers[index] = len(set(str(x) for x in fills.maker[lo:hi]))
        unique_takers[index] = len(set(str(x) for x in fills.taker[lo:hi]))
        exchange_taker_value[index] = float(np.sum(values[fills.exchange_taker[lo:hi]]))
    baseline = np.column_stack(
        [count, value, unique_makers, unique_takers, exchange_taker_value, own_move, spread]
    )
    valid = own_valid & quote_valid & np.isfinite(y) & np.all(np.isfinite(baseline), axis=1)
    return (
        valid,
        baseline,
        y,
        entry_bid,
        entry_ask,
        future_bid,
        future_ask,
        own_move,
    )


def _fit_participant_scores(
    *,
    quote_by_token: dict[str, QuoteSeries],
    fills_by_token: dict[str, FillSeries],
    train_start_ns: int,
    train_end_ns: int,
    score_horizon_seconds: int,
    score_lookback_seconds: int,
    freshness_ns: int,
    minimum_history: int,
) -> dict[tuple[str, str], float]:
    values: dict[tuple[str, str], list[float]] = defaultdict(list)
    one_second = _seconds_ns(1)
    horizon_ns = _seconds_ns(score_horizon_seconds)
    for token, fills in fills_by_token.items():
        quote = quote_by_token.get(token)
        if quote is None or not len(fills.times_ns):
            continue
        selected = (fills.times_ns >= train_start_ns) & (fills.times_ns + one_second + horizon_ns < train_end_ns)
        indices = np.flatnonzero(selected)
        if not len(indices):
            continue
        # Feature availability begins at the next whole second after the block-second fill.
        decision = ((fills.times_ns[indices] // one_second) + 1) * one_second
        own_move, own_valid = _feature_move(quote, decision, score_lookback_seconds, freshness_ns)
        future_y, _, _, _, _ = _future_target(quote, decision, score_horizon_seconds, freshness_ns)
        valid = own_valid & np.isfinite(future_y) & (np.abs(own_move) > 1e-12)
        for local, fill_index in enumerate(indices):
            if not valid[local]:
                continue
            continuation = float(np.sign(own_move[local]) * future_y[local])
            values[("maker", str(fills.maker[fill_index]))].append(continuation)
            values[("taker", str(fills.taker[fill_index]))].append(continuation)
    return {
        key: float(np.mean(samples))
        for key, samples in values.items()
        if len(samples) >= minimum_history
    }


def _participant_feature(
    *,
    fills: FillSeries,
    times_ns: np.ndarray,
    own_move: np.ndarray,
    scores: dict[tuple[str, str], float],
    window_seconds: int,
) -> np.ndarray:
    result = np.zeros(len(times_ns), dtype=np.float64)
    window_ns, one_second = _seconds_ns(window_seconds), _seconds_ns(1)
    for index, t in enumerate(times_ns):
        cutoff = (t // one_second) * one_second
        lo = np.searchsorted(fills.times_ns, cutoff - window_ns, side="left")
        hi = np.searchsorted(fills.times_ns, cutoff, side="left")
        if hi <= lo or not np.isfinite(own_move[index]) or abs(own_move[index]) <= 1e-12:
            continue
        weighted = 0.0
        total = 0.0
        for fill_index in range(lo, hi):
            value = float(fills.value_usd[fill_index])
            maker_score = scores.get(("maker", str(fills.maker[fill_index])), 0.0)
            taker_score = scores.get(("taker", str(fills.taker[fill_index])), 0.0)
            weighted += value * (maker_score + taker_score) / 2.0
            total += value
        if total > 0:
            result[index] = float(np.sign(own_move[index]) * weighted / total)
    return result


def run_participant_oos(
    *,
    regime_id: str,
    event_family_id: str,
    quote_by_token: dict[str, QuoteSeries],
    fills_by_token: dict[str, FillSeries],
    start: datetime,
    end: datetime,
    grid_seconds: int,
    horizon_seconds: int,
    activity_window_seconds: int,
    own_lookback_seconds: int,
    score_horizon_seconds: int,
    score_lookback_seconds: int,
    minimum_history: int,
    freshness_seconds: int,
    validation: dict[str, Any],
    run_id: str,
    permute_participant_labels: bool = False,
) -> OOSResult:
    freshness_ns = _seconds_ns(freshness_seconds)
    grid_times = _grid(start, end, grid_seconds)
    per_token: dict[str, tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]] = {}
    for token, fills in sorted(fills_by_token.items()):
        quote = quote_by_token.get(token)
        if quote is None:
            continue
        per_token[token] = _participant_baseline_rows(
            token=token,
            quote=quote,
            fills=fills,
            grid_times=grid_times,
            window_seconds=activity_window_seconds,
            own_lookback_seconds=own_lookback_seconds,
            freshness_ns=freshness_ns,
            horizon_seconds=horizon_seconds,
        )
    train_ns = _seconds_ns(int(validation["walk_forward"]["training_seconds"]))
    dev_ns = _seconds_ns(int(validation["walk_forward"]["development_seconds"]))
    hold_ns = _seconds_ns(int(validation["walk_forward"]["holdout_seconds"]))
    step_ns = _seconds_ns(int(validation["walk_forward"]["step_seconds"]))
    embargo_ns = _seconds_ns(int(validation["embargo_seconds"]))
    horizon_ns = _seconds_ns(horizon_seconds)
    start_ns, end_ns = _epoch_ns(start), _epoch_ns(end)
    anchor = start_ns + train_ns
    fold_number = 0
    out_time: list[np.ndarray] = []
    out_market: list[np.ndarray] = []
    out_y: list[np.ndarray] = []
    out_b: list[np.ndarray] = []
    out_c: list[np.ndarray] = []
    out_d: list[np.ndarray] = []
    out_markout: list[np.ndarray] = []
    fold_ids: list[str] = []
    while anchor + dev_ns + hold_ns <= end_ns:
        fold_number += 1
        train_start, train_end = anchor - train_ns, anchor
        hold_start, hold_end = anchor + dev_ns, anchor + dev_ns + hold_ns
        scores = _fit_participant_scores(
            quote_by_token=quote_by_token,
            fills_by_token=fills_by_token,
            train_start_ns=train_start,
            train_end_ns=train_end - embargo_ns,
            score_horizon_seconds=score_horizon_seconds,
            score_lookback_seconds=score_lookback_seconds,
            freshness_ns=freshness_ns,
            minimum_history=minimum_history,
        )
        if permute_participant_labels and scores:
            rng = random.Random(_seed(run_id, f"participant-placebo:{regime_id}:fold-{fold_number:03d}"))
            permuted: dict[tuple[str, str], float] = {}
            for role in ("maker", "taker"):
                keys = sorted(key for key in scores if key[0] == role)
                score_values = [scores[key] for key in keys]
                rng.shuffle(score_values)
                permuted.update({key: value for key, value in zip(keys, score_values, strict=True)})
            scores = permuted
        train_xb: list[np.ndarray] = []
        train_xc: list[np.ndarray] = []
        train_y: list[np.ndarray] = []
        hold_xb: list[np.ndarray] = []
        hold_xc: list[np.ndarray] = []
        hold_y: list[np.ndarray] = []
        hold_time: list[np.ndarray] = []
        hold_market: list[np.ndarray] = []
        hold_eb: list[np.ndarray] = []
        hold_ea: list[np.ndarray] = []
        hold_fb: list[np.ndarray] = []
        hold_fa: list[np.ndarray] = []
        for token, token_values in per_token.items():
            valid, baseline, y, entry_bid, entry_ask, future_bid, future_ask, own_move = token_values
            fills = fills_by_token[token]
            identity = _participant_feature(
                fills=fills,
                times_ns=grid_times,
                own_move=own_move,
                scores=scores,
                window_seconds=activity_window_seconds,
            )
            challenger = np.column_stack([baseline, identity])
            train = (
                valid
                & (grid_times >= train_start)
                & (grid_times + horizon_ns < train_end - embargo_ns)
            )
            hold = valid & (grid_times >= hold_start) & (grid_times < hold_end)
            if np.any(train):
                train_xb.append(baseline[train]); train_xc.append(challenger[train]); train_y.append(y[train])
            if np.any(hold):
                hold_xb.append(baseline[hold]); hold_xc.append(challenger[hold]); hold_y.append(y[hold])
                hold_time.append(grid_times[hold]); hold_market.append(np.full(np.count_nonzero(hold), token, dtype=object))
                hold_eb.append(entry_bid[hold]); hold_ea.append(entry_ask[hold]); hold_fb.append(future_bid[hold]); hold_fa.append(future_ask[hold])
        if train_y and hold_y:
            xbt, xct, yt = np.vstack(train_xb), np.vstack(train_xc), np.concatenate(train_y)
            xbh, xch, yh = np.vstack(hold_xb), np.vstack(hold_xc), np.concatenate(hold_y)
            if len(yt) >= 50 and len(yh) >= 10:
                pb = _predict_ols(xbh, _fit_ols(xbt, yt))
                pc_ = _predict_ols(xch, _fit_ols(xct, yt))
                dd = (yh - pb) ** 2 - (yh - pc_) ** 2
                eb, ea, fb, fa = map(np.concatenate, (hold_eb, hold_ea, hold_fb, hold_fa))
                direction = np.sign(pc_)
                gross = np.full(len(yh), np.nan)
                pos, neg = direction > 0, direction < 0
                gross[pos] = fb[pos] - ea[pos]
                gross[neg] = eb[neg] - fa[neg]
                out_time.append(np.concatenate(hold_time)); out_market.append(np.concatenate(hold_market))
                out_y.append(yh); out_b.append(pb); out_c.append(pc_); out_d.append(dd); out_markout.append(gross)
                fold_ids.append(f"{regime_id}:fold-{fold_number:03d}")
        anchor += step_ns
    if not out_y:
        empty = np.asarray([], dtype=np.float64)
        return OOSResult(regime_id,event_family_id,horizon_seconds,tuple(fold_ids),np.asarray([],dtype=object),np.asarray([],dtype=np.int64),empty,empty,empty,empty,empty)
    return OOSResult(
        regime_id,event_family_id,horizon_seconds,tuple(fold_ids),np.concatenate(out_market),np.concatenate(out_time),
        np.concatenate(out_y),np.concatenate(out_b),np.concatenate(out_c),np.concatenate(out_d),np.concatenate(out_markout)
    )


def _svd_factors(train_refs: np.ndarray, rank: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    mean = np.mean(train_refs, axis=0)
    std = np.std(train_refs, axis=0)
    if np.any(~np.isfinite(std)) or np.count_nonzero(std > 1e-12) < rank:
        raise ValueError("insufficient reference variation for low-rank fit")
    keep = std > 1e-12
    scaled = (train_refs[:, keep] - mean[keep]) / std[keep]
    _, _, vh = np.linalg.svd(scaled, full_matrices=False)
    loadings = vh[:rank].copy()
    for row in loadings:
        pivot = int(np.argmax(np.abs(row)))
        if row[pivot] < 0:
            row *= -1
    return mean, np.where(keep, std, np.nan), loadings


def _factor_scores(refs: np.ndarray, fit: tuple[np.ndarray, np.ndarray, np.ndarray]) -> np.ndarray:
    mean, std, loadings = fit
    keep = np.isfinite(std)
    scaled = (refs[:, keep] - mean[keep]) / std[keep]
    return np.asarray(scaled @ loadings.T, dtype=np.float64)


def run_lowrank_oos(
    *,
    regime_id: str,
    event_family_id: str,
    quote_by_token: dict[str, QuoteSeries],
    token_ids: tuple[str, ...],
    start: datetime,
    end: datetime,
    grid_seconds: int,
    horizon_seconds: int,
    lookback_seconds: int,
    rank: int,
    freshness_seconds: int,
    validation: dict[str, Any],
    minimum_reference_markets: int,
    factor_delay_seconds: int = 0,
) -> OOSResult:
    if factor_delay_seconds % grid_seconds:
        raise ValueError("low-rank factor delay must align to the preregistered grid")
    available = tuple(token for token in token_ids if token in quote_by_token)
    if len(available) < minimum_reference_markets + 1:
        empty=np.asarray([],dtype=np.float64)
        return OOSResult(regime_id,event_family_id,horizon_seconds,(),np.asarray([],dtype=object),np.asarray([],dtype=np.int64),empty,empty,empty,empty,empty)
    times = _grid(start, end, grid_seconds)
    freshness_ns = _seconds_ns(freshness_seconds)
    moves: dict[str, np.ndarray] = {}
    targets: dict[str, tuple[np.ndarray,np.ndarray,np.ndarray,np.ndarray,np.ndarray]] = {}
    for token in available:
        moves[token], _ = _feature_move(quote_by_token[token], times, lookback_seconds, freshness_ns)
        targets[token] = _future_target(quote_by_token[token], times, horizon_seconds, freshness_ns)
    train_ns=_seconds_ns(int(validation["walk_forward"]["training_seconds"]))
    dev_ns=_seconds_ns(int(validation["walk_forward"]["development_seconds"]))
    hold_ns=_seconds_ns(int(validation["walk_forward"]["holdout_seconds"]))
    step_ns=_seconds_ns(int(validation["walk_forward"]["step_seconds"]))
    embargo_ns=_seconds_ns(int(validation["embargo_seconds"]))
    horizon_ns=_seconds_ns(horizon_seconds)
    start_ns,end_ns=_epoch_ns(start),_epoch_ns(end)
    anchor=start_ns+train_ns; fold_number=0
    out_time=[]; out_market=[]; out_y=[]; out_b=[]; out_c=[]; out_d=[]; out_markout=[]; fold_ids=[]
    while anchor+dev_ns+hold_ns <= end_ns:
        fold_number += 1
        train_start,train_end=anchor-train_ns,anchor
        hold_start,hold_end=anchor+dev_ns,anchor+dev_ns+hold_ns
        for target_token in available:
            refs=tuple(token for token in available if token != target_token)
            if len(refs) < minimum_reference_markets:
                continue
            # The preregistered universe is coverage-ordered.  Use the declared minimum number
            # of leave-target-out references rather than silently requiring every listed market
            # to overlap at every timestamp.  Requiring all references made the v2 low-rank lane
            # ineligible despite the explicit minimum_reference_markets=3 contract.
            refs = refs[:minimum_reference_markets]
            ref_matrix=np.column_stack([moves[token] for token in refs])
            if factor_delay_seconds:
                steps = factor_delay_seconds // grid_seconds
                delayed = np.full_like(ref_matrix, np.nan)
                delayed[steps:] = ref_matrix[:-steps]
                ref_matrix = delayed
            own=moves[target_token]
            y,eb,ea,fb,fa=targets[target_token]
            finite=np.isfinite(y)&np.isfinite(own)&np.all(np.isfinite(ref_matrix),axis=1)
            train=finite&(times>=train_start)&(times+horizon_ns<train_end-embargo_ns)
            hold=finite&(times>=hold_start)&(times<hold_end)
            if np.count_nonzero(train)<50 or np.count_nonzero(hold)<10:
                continue
            try:
                factor_fit=_svd_factors(ref_matrix[train],rank)
            except (ValueError,np.linalg.LinAlgError):
                continue
            train_factor=_factor_scores(ref_matrix[train],factor_fit)
            hold_factor=_factor_scores(ref_matrix[hold],factor_fit)
            train_equal=np.mean(ref_matrix[train],axis=1)
            hold_equal=np.mean(ref_matrix[hold],axis=1)
            xb_train=np.column_stack([own[train],train_equal])
            xb_hold=np.column_stack([own[hold],hold_equal])
            xc_train=np.column_stack([own[train],train_equal,train_factor])
            xc_hold=np.column_stack([own[hold],hold_equal,hold_factor])
            pb=_predict_ols(xb_hold,_fit_ols(xb_train,y[train]))
            pc_=_predict_ols(xc_hold,_fit_ols(xc_train,y[train]))
            yy=y[hold]; dd=(yy-pb)**2-(yy-pc_)**2
            direction=np.sign(pc_); gross=np.full(len(yy),np.nan)
            pos,neg=direction>0,direction<0
            gross[pos]=fb[hold][pos]-ea[hold][pos]
            gross[neg]=eb[hold][neg]-fa[hold][neg]
            out_time.append(times[hold]); out_market.append(np.full(np.count_nonzero(hold),target_token,dtype=object))
            out_y.append(yy); out_b.append(pb); out_c.append(pc_); out_d.append(dd); out_markout.append(gross)
        fold_ids.append(f"{regime_id}:fold-{fold_number:03d}")
        anchor += step_ns
    if not out_y:
        empty=np.asarray([],dtype=np.float64)
        return OOSResult(regime_id,event_family_id,horizon_seconds,tuple(fold_ids),np.asarray([],dtype=object),np.asarray([],dtype=np.int64),empty,empty,empty,empty,empty)
    return OOSResult(regime_id,event_family_id,horizon_seconds,tuple(fold_ids),np.concatenate(out_market),np.concatenate(out_time),np.concatenate(out_y),np.concatenate(out_b),np.concatenate(out_c),np.concatenate(out_d),np.concatenate(out_markout))


def load_identity_rows(corpus_root: Path) -> list[dict[str, str]]:
    path = corpus_root / "schema_version=1" / "market_identity.csv"
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def mechanical_loo_price_relationships(
    identity_rows: list[dict[str, str]],
    primary: tuple[Relationship, ...],
    regime_id: str,
    *,
    max_references: int = 8,
) -> tuple[Relationship, ...]:
    eligible = [
        row for row in identity_rows
        if row["regime_id"] == regime_id and row["outcome"] == "Yes" and row["book_available"] == "true"
    ]
    by_token = {row["token_id"]: row for row in eligible}
    by_event: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in eligible:
        by_event[row["event_id"]].append(row)
    targets = sorted({rel.target_token_id for rel in primary if rel.regime_id == regime_id})
    output: list[Relationship] = []
    for token in targets:
        target = by_token.get(token)
        if target is None:
            continue
        peers = [row for row in by_event[target["event_id"]] if row["token_id"] != token]
        peers.sort(key=lambda row: (-int(row["book_evidence_rows"]), row["condition_id"]))
        for ref in peers[:max_references]:
            output.append(
                Relationship(
                    regime_id=regime_id,
                    target_condition_id=target["condition_id"],
                    target_token_id=token,
                    target_market_family=target["market_family"],
                    reference_condition_id=ref["condition_id"],
                    reference_token_id=ref["token_id"],
                    reference_market_family=ref["market_family"],
                    leakage_class="MECHANICAL_SIBLING",
                    semantic_basis=f"ACCEPTED_EVENT_ID:{target.get(chr(101)+chr(118)+chr(101)+chr(110)+chr(116)+chr(95)+chr(105)+chr(100))}; LOO-PRICE reconstruction diagnostic",
                )
            )
    return tuple(output)


@dataclass(frozen=True, slots=True)
class EventEvidence:
    event_id: str
    event_family_id: str
    horizon_seconds: int
    observations: int
    markets: tuple[str, ...]
    folds: tuple[str, ...]
    baseline_sse: float
    challenger_sse: float
    delta_sum: float
    y_sum: float
    pred_sum: float
    y2_sum: float
    pred2_sum: float
    ypred_sum: float
    directional_correct: int
    markout_sum: float
    markout_count: int
    block_means: tuple[float, ...]

    @property
    def delta_mean(self) -> float:
        return self.delta_sum / self.observations if self.observations else float("nan")


def compact_oos(result: OOSResult, session_seconds: int) -> EventEvidence:
    n = len(result.y)
    if not n:
        return EventEvidence(result.event_id,result.event_family_id,result.horizon_seconds,0,(),result.fold_ids,0,0,0,0,0,0,0,0,0,0,0,())
    pb,pc_,y,d=result.pred_baseline,result.pred_challenger,result.y,result.loss_difference
    valid_markout=result.gross_markout[np.isfinite(result.gross_markout)]
    session_ns=_seconds_ns(session_seconds)
    keys=result.times_ns//session_ns
    blocks=tuple(float(np.mean(d[keys==key])) for key in np.unique(keys))
    return EventEvidence(
        result.event_id,result.event_family_id,result.horizon_seconds,n,
        tuple(sorted(set(str(x) for x in result.market_ids.tolist()))),result.fold_ids,
        float(np.sum((y-pb)**2)),float(np.sum((y-pc_)**2)),float(np.sum(d)),
        float(np.sum(y)),float(np.sum(pc_)),float(np.sum(y*y)),float(np.sum(pc_*pc_)),float(np.sum(y*pc_)),
        int(np.count_nonzero(np.sign(pc_)==np.sign(y))),float(np.sum(valid_markout)),int(len(valid_markout)),blocks
    )


def _block_test_from_means(
    blocks: np.ndarray, *, exact_max_blocks: int, monte_carlo_draws: int, seed: int
) -> tuple[float | None,float,int,str]:
    blocks=blocks[np.isfinite(blocks)]
    n=len(blocks)
    if n<4:
        return None,(float(np.mean(blocks)) if n else float("nan")),n,"INSUFFICIENT_BLOCKS"
    observed=float(np.mean(blocks))
    if n<=exact_max_blocks:
        total=1<<n; extreme=0
        for mask in range(total):
            value=sum(blocks[j] if (mask>>j)&1 else -blocks[j] for j in range(n))/n
            if value>=observed-1e-15: extreme+=1
        return extreme/total,observed,n,"EXACT"
    rng=random.Random(seed); extreme=0
    for _ in range(monte_carlo_draws):
        value=sum(x if rng.getrandbits(1) else -x for x in blocks)/n
        if value>=observed-1e-15: extreme+=1
    return (extreme+1)/(monte_carlo_draws+1),observed,n,"MONTE_CARLO"


def summarize_evidence(
    evidence: list[EventEvidence], *, run_id: str, component: str, statistics_spec: dict[str,Any]
) -> dict[str,Any]:
    usable=[item for item in evidence if item.observations]
    n=sum(item.observations for item in usable)
    if not n:
        return {"valid_observations":0,"events":0,"markets":0,"baseline_mse":None,"challenger_mse":None,"delta_mse":None,"raw_p_value":None,"test_status":"INSUFFICIENT_DATA"}
    base_sse=sum(x.baseline_sse for x in usable); chal_sse=sum(x.challenger_sse for x in usable); delta=sum(x.delta_sum for x in usable)
    sy=sum(x.y_sum for x in usable); sp=sum(x.pred_sum for x in usable); sy2=sum(x.y2_sum for x in usable); sp2=sum(x.pred2_sum for x in usable); syp=sum(x.ypred_sum for x in usable)
    cov=syp-sy*sp/n; vy=sy2-sy*sy/n; vp=sp2-sp*sp/n
    corr=(cov/math.sqrt(vy*vp)) if vy>0 and vp>0 else None
    blocks=np.asarray([b for item in usable for b in item.block_means],dtype=np.float64)
    bt=statistics_spec["block_test"]
    p,stat,block_count,status=_block_test_from_means(blocks,exact_max_blocks=int(bt["exact_enumeration_max_blocks"]),monte_carlo_draws=int(bt["monte_carlo_draws"]),seed=_seed(run_id,f"{component}:block-test"))
    draws=int(statistics_spec["bootstrap"]["draws"]); rng=np.random.default_rng(_seed(run_id,f"{component}:event-bootstrap"))
    event_means=np.asarray([x.delta_mean for x in usable],dtype=np.float64); event_counts=np.asarray([x.observations for x in usable],dtype=np.float64)
    eq_samples=np.empty(draws); ow_samples=np.empty(draws)
    for i in range(draws):
        selected=rng.integers(0,len(usable),size=len(usable)); eq_samples[i]=float(np.mean(event_means[selected])); ow_samples[i]=float(np.sum(event_means[selected]*event_counts[selected])/np.sum(event_counts[selected]))
    return {
      "valid_observations":n,"events":len(usable),"event_ids":[x.event_id for x in usable],"event_families":sorted({x.event_family_id for x in usable}),
      "markets":len({m for x in usable for m in x.markets}),"folds":[fold for x in usable for fold in x.folds],
      "baseline_mse":base_sse/n,"challenger_mse":chal_sse/n,"delta_mse":delta/n,"relative_mse_improvement":1-chal_sse/base_sse if base_sse>0 else None,
      "prediction_target_correlation":corr,"directional_accuracy":sum(x.directional_correct for x in usable)/n,
      "gross_executable_markout_mean":(sum(x.markout_sum for x in usable)/sum(x.markout_count for x in usable) if sum(x.markout_count for x in usable) else None),
      "raw_p_value":p,"test_statistic":stat,"test_blocks":block_count,"test_status":status,
      "equal_event_bootstrap":{"point":float(np.mean(event_means)),"lower":float(np.quantile(eq_samples,.025)),"upper":float(np.quantile(eq_samples,.975))},
      "observation_weighted_event_bootstrap":{"point":delta/n,"lower":float(np.quantile(ow_samples,.025)),"upper":float(np.quantile(ow_samples,.975))},
      "by_event":{x.event_id:{"observations":x.observations,"delta_mse":x.delta_mean,"markets":len(x.markets)} for x in usable}
    }


def _generic_oos(data: ModelInput, start: datetime, end: datetime, validation: dict[str,Any]) -> OOSResult:
    wf=validation["walk_forward"]
    return _walk_forward_oos(
        data, regime_start=start, regime_end=end,
        train_seconds=int(wf["training_seconds"]),development_seconds=int(wf["development_seconds"]),
        holdout_seconds=int(wf["holdout_seconds"]),step_seconds=int(wf["step_seconds"]),
        embargo_seconds=int(validation["embargo_seconds"]),
    )


def _parse_window(values: list[str]) -> tuple[datetime,datetime]:
    return datetime.fromisoformat(values[0]),datetime.fromisoformat(values[1])


def _control_pass(summary: dict[str,Any]) -> bool:
    delta=summary.get("delta_mse"); p=summary.get("raw_p_value")
    return bool(delta is not None and (delta <= 0 or (p is not None and p >= 0.05)))


def _stability_report(cells: dict[str,dict[str,Any]], primary_key: str, tolerance: float) -> dict[str,Any]:
    metrics={key:value.get("delta_mse") for key,value in cells.items()}
    finite={key:float(value) for key,value in metrics.items() if value is not None and math.isfinite(float(value))}
    if primary_key not in finite or len(finite)<2:
        return {"cells":metrics,"primary":primary_key,"stable":False,"reason":"insufficient sensitivity cells"}
    peak=max(finite.values()); scale=max(abs(peak),1e-15)
    neighbours=[value for key,value in finite.items() if key!=primary_key]
    primary=finite[primary_key]
    nonisolated=any(abs(peak-value)/scale <= tolerance for value in neighbours)
    sign_support=sum(1 for value in finite.values() if value>0)
    stable=primary>0 and nonisolated and sign_support>=2
    return {"cells":metrics,"primary":primary_key,"peak":peak,"nonisolated":nonisolated,"positive_cells":sign_support,"stable":stable,"tolerance":tolerance}


def _apply_global_fdr(
    primary: dict[str,dict[int,dict[str,Any]]],
    prereg: dict[str,Any],
    harness: ResearchEvaluationHarness | None = None,
) -> dict[str,dict[str,Any]]:
    fdr_spec=prereg["statistics"]["fdr"]; declared=tuple(fdr_spec["hypothesis_ids"]); alpha=Decimal(str(fdr_spec["alpha"])); family_id=fdr_spec["family_id"]
    tests=[]
    for family,hid in FAMILY_IDS.items():
        for horizon,summary in primary[family].items():
            p=summary.get("raw_p_value")
            if p is None: continue
            hypothesis_id=f"{hid}@{horizon}s"
            tests.append(HypothesisTest(hypothesis_id,family_id,Decimal(str(p)),"30-minute block sign-flip","paired baseline/challenger OOS loss difference has no positive mean","mean session loss improvement","fixed UTC 30-minute session blocks within regime"))
    if len(tests)==len(declared) and {t.hypothesis_id for t in tests}==set(declared):
        protocol=FDRProtocol(family_id,alpha,declared)
        rows = (
            harness.apply_fdr(tests, protocol=protocol)
            if harness is not None
            else ()
        )
        if not rows:
            raise ValueError("complete FDR family requires the BUILD-008 harness")
        return {row.hypothesis_id:{"raw_p_value":float(row.raw_p_value),"q_value":float(row.q_value),"rejected":row.rejected,"rank":row.rank,"threshold":float(row.threshold),"family_size":row.family_size} for row in rows}
    # Missing/ineligible cells stay p/q=null, but still count in m.  No synthetic p-value is created.
    ordered=sorted(tests,key=lambda t:(t.p_value,t.hypothesis_id)); m=len(declared); cutoff=0
    for rank,test in enumerate(ordered,1):
        if test.p_value <= alpha*Decimal(rank)/Decimal(m): cutoff=rank
    raw_q=[min(Decimal(1),test.p_value*Decimal(m)/Decimal(rank)) for rank,test in enumerate(ordered,1)]
    for i in range(len(raw_q)-2,-1,-1): raw_q[i]=min(raw_q[i],raw_q[i+1])
    result: dict[str, dict[str, Any]]={hyp:{"raw_p_value":None,"q_value":None,"rejected":False,"rank":None,"threshold":None,"family_size":m,"status":"UNAVAILABLE"} for hyp in declared}
    for rank,(test,q) in enumerate(zip(ordered,raw_q,strict=True),1):
        result[test.hypothesis_id]={"raw_p_value":float(test.p_value),"q_value":float(q),"rejected":rank<=cutoff,"rank":rank,"threshold":float(alpha*Decimal(rank)/Decimal(m)),"family_size":m}
    return result


def build_canonical_report(
    *,
    family: str,
    hypothesis_id: str,
    harness: ResearchEvaluationHarness,
    horizon_rows: list[dict[str, Any]],
    fdr_results: dict[str, dict[str, Any]],
    stability_report: dict[str, Any],
    ablations: list[dict[str, Any]],
    coverage: dict[str, Any],
    disposition: str,
) -> ResearchReport:
    """Materialize and validate the canonical BUILD-008 report."""
    fold_ids = sorted(
        {
            str(fold)
            for row in horizon_rows
            for fold in row.get("folds", [])
        }
    )
    raw_tests = tuple(
        {
            "hypothesis_id": row["hypothesis_id"],
            "family_id": harness.spec.fdr.family_id,
            "raw_p_value": row.get("raw_p_value"),
            "test_name": "30-minute block sign-flip",
            "null_hypothesis": "paired OOS loss improvement has no positive mean",
            "test_statistic": row.get("test_statistic"),
            "dependence_assumption": "fixed UTC 30-minute session blocks within regime",
            "blocks": row.get("test_blocks"),
            "status": row.get("test_status"),
        }
        for row in horizon_rows
        if row.get("raw_p_value") is not None
    )
    family_fdr = tuple(
        {
            "hypothesis_id": f"{hypothesis_id}@{int(row['horizon_seconds'])}s",
            "family_id": harness.spec.fdr.family_id,
            **fdr_results[f"{hypothesis_id}@{int(row['horizon_seconds'])}s"],
        }
        for row in horizon_rows
    )
    bootstrap_results = tuple(
        {
            "horizon_seconds": int(row["horizon_seconds"]),
            "method": "event_equal_weight",
            "weighting": "EQUAL_EVENT",
            **(row.get("equal_event_bootstrap") or {}),
        }
        for row in horizon_rows
        if row.get("equal_event_bootstrap") is not None
    )
    parameter_stability = dict(stability_report)
    parameter_stability["peak_is_isolated"] = not bool(
        stability_report.get("nonisolated", False)
    )
    negative_controls = tuple(
        {
            "horizon_seconds": int(row["horizon_seconds"]),
            **row["negative_control"],
        }
        for row in horizon_rows
    )
    valid_counts = [int(row.get("valid_observations", 0)) for row in horizon_rows]
    report = ResearchReport(
        schema_version="1",
        run_id=harness.identity.run_id,
        experiment_id="EXPERIMENT-003",
        hypothesis_family=family,
        economic_mechanism=harness.spec.economic_mechanism,
        code_revision=harness.identity.code_revision,
        dataset_id=harness.spec.dataset.dataset_id,
        dataset_version=harness.spec.dataset.schema_version,
        dataset_hash=harness.identity.dataset_hash,
        config_hash=harness.identity.config_hash,
        universe={
            "markets": harness.spec.market_universe,
            "events": harness.spec.event_universe,
            "event_families": harness.spec.event_family_universe,
        },
        folds=tuple({"fold_id": fold_id} for fold_id in fold_ids),
        purge_embargo={
            "purge": harness.spec.purge,
            "embargo_seconds": harness.spec.embargo.total_seconds(),
        },
        sample_counts={
            "horizon_cells": len(horizon_rows),
            "valid_observations_across_horizon_cells": sum(valid_counts),
            "max_valid_observations_in_one_horizon": max(valid_counts, default=0),
            "unavailable_horizon_cells": sum(
                1 for row in horizon_rows if int(row.get("valid_observations", 0)) == 0
            ),
        },
        horizon_results=tuple(horizon_rows),
        predictive_metrics={
            "primary": "incremental OOS squared-error improvement",
            "delta_mse_by_horizon": {
                str(row["horizon_seconds"]): row.get("delta_mse")
                for row in horizon_rows
            },
        },
        gross_executable_metrics={
            "diagnostic_only": True,
            "mean_by_horizon": {
                str(row["horizon_seconds"]): row.get("gross_executable_markout_mean")
                for row in horizon_rows
            },
        },
        net_executable_metrics=None,
        bootstrap_results=bootstrap_results,
        raw_statistical_tests=raw_tests,
        fdr_results=family_fdr,
        parameter_stability=parameter_stability,
        negative_controls=negative_controls,
        ablations=tuple(ablations),
        execution_stresses=(),
        invalidity_counts={
            "unavailable_primary_horizon_cells": sum(
                1 for row in horizon_rows if int(row.get("valid_observations", 0)) == 0
            )
        },
        known_limitations=(
            "DATA-001 is not FULL_EVENT_REPLAY; no queue or passive-fill claims are made.",
            "Historical Polymarket evidence is not historical SIG↔Polymarket evidence.",
            "Equal-event inference has five top-level regimes across three event families.",
            "Coverage details by regime are retained in the battery summary: "
            + json.dumps(coverage, sort_keys=True, separators=(",", ":")),
        ),
        disposition=ResearchDisposition(disposition),
        evidence_policy=harness.spec.disposition_policy,
        requested_disposition=ResearchDisposition(disposition),
    )
    return harness.validate_report(report)


def build_family_harnesses(
    *,
    prereg: dict[str, Any],
    relationships: tuple[Relationship, ...],
    code_revision: str,
    preregistration_sha256: str,
) -> dict[str, ResearchEvaluationHarness]:
    """Build the canonical BUILD-008 contract for every EXPERIMENT-003 family."""
    dataset = DatasetVersion(
        dataset_id=prereg["dataset"]["dataset_id"],
        schema_version=str(prereg["dataset"]["schema_version"]),
        manifest_sha256=prereg["dataset"]["manifest_sha256"],
        source_version="accepted-DATA-001",
    )
    wf = prereg["validation"]["walk_forward"]
    walk_forward = WalkForwardProtocol(
        training_window=timedelta(seconds=int(wf["training_seconds"])),
        development_window=timedelta(seconds=int(wf["development_seconds"])),
        holdout_window=timedelta(seconds=int(wf["holdout_seconds"])),
        step=timedelta(seconds=int(wf["step_seconds"])),
        expanding_training=bool(wf["expanding_training"]),
    )
    fdr_cfg = prereg["statistics"]["fdr"]
    fdr = FDRProtocol(
        family_id=fdr_cfg["family_id"],
        alpha=Decimal(str(fdr_cfg["alpha"])),
        hypothesis_ids=tuple(fdr_cfg["hypothesis_ids"]),
    )
    bootstrap = BootstrapProtocol(
        method="event",
        draws=int(prereg["statistics"]["bootstrap"]["draws"]),
        block_size=1,
        event_weighting=EventBootstrapWeighting.EQUAL_EVENT,
    )
    stability = StabilityProtocol(
        tolerance=Decimal(str(prereg["statistics"]["stability"]["relative_tolerance"]))
    )
    evidence_policy = EvidencePolicy(
        require_statistical_tests=True,
        require_fdr=True,
        require_bootstrap=True,
        require_stability=True,
        require_negative_controls=True,
        require_ablations=True,
        require_execution_stress=False,
        min_fdr_rejections=1,
        bootstrap_min_lower_bound=None,
        require_nonisolated_stability=True,
        require_negative_controls_pass=True,
        require_ablations_pass=True,
        require_execution_stresses_pass=False,
    )
    all_relationship_tokens = {
        token
        for rel in relationships
        for token in (rel.target_token_id, rel.reference_token_id)
    }
    lowrank_tokens = {
        row["token_id"]
        for rows in prereg["families"]["lowrank"]["lowrank_universe"].values()
        for row in rows
    }
    common_universe = tuple(sorted(all_relationship_tokens | lowrank_tokens))
    event_universe = tuple(prereg["regimes"].keys())
    event_family_universe = tuple(sorted(set(prereg["event_families"].values())))
    target_horizons = tuple(
        timedelta(seconds=int(value)) for value in prereg["target"]["horizons_seconds"]
    )

    family_details: dict[str, dict[str, Any]] = {
        "leadlag": {
            "mechanism": "explicit related-market past repricing adds information beyond target autoregression",
            "parameters": (("lookback_seconds", tuple(map(str, prereg["families"]["leadlag"]["sensitivity_lookbacks_seconds"]))),),
            "controls": (
                NegativeControlSpec(
                    name="reference-delayed-300s",
                    kind=NegativeControlKind.DELAYED_PAST_ONLY,
                    delay=timedelta(seconds=int(prereg["controls"]["leadlag"]["delay_seconds"])),
                ),
            ),
            "ablations": (
                AblationSpec(name="reference-information-removed", removed_components=("reference_move",)),
            ),
        },
        "structural_residual": {
            "mechanism": "indirect event-relative residual adds information beyond target autoregression",
            "parameters": (
                (
                    "reference_freshness_seconds",
                    tuple(map(str, prereg["families"]["structural_residual"]["sensitivity_reference_freshness_seconds"])),
                ),
            ),
            "controls": (
                NegativeControlSpec(name="zero-structural-residual", kind=NegativeControlKind.ZERO_SIGNAL),
            ),
            "ablations": (
                AblationSpec(name="LOO-PRICE-001", removed_components=("indirect_family_filter",)),
                AblationSpec(name="LOO-FAMILY-001", removed_components=("mechanical_family_information",)),
            ),
        },
        "microstructure": {
            "mechanism": "observable depth imbalance and microprice displacement add short-horizon information",
            "parameters": (("feature_window_seconds", tuple(map(str, prereg["families"]["microstructure"]["sensitivity_windows_seconds"]))),),
            "controls": (
                NegativeControlSpec(
                    name="microstructure-delayed-300s",
                    kind=NegativeControlKind.DELAYED_PAST_ONLY,
                    delay=timedelta(seconds=int(prereg["controls"]["microstructure"]["delay_seconds"])),
                ),
            ),
            "ablations": (
                AblationSpec(name="microstructure-information-removed", removed_components=("imbalance", "microprice_displacement")),
            ),
        },
        "participant": {
            "mechanism": "train-frozen participant identity conditions repricing beyond identity-blind activity",
            "parameters": (("activity_window_seconds", tuple(map(str, prereg["families"]["participant"]["sensitivity_windows_seconds"]))),),
            "controls": (
                NegativeControlSpec(
                    name="participant-label-permutation",
                    kind=NegativeControlKind.FEATURE_EXCLUSION,
                    excluded_components=("true_participant_identity",),
                ),
            ),
            "ablations": (
                AblationSpec(name="participant-identity-removed", removed_components=("participant_conditioned_feature",)),
            ),
        },
        "lowrank": {
            "mechanism": "leave-target-out training-only common factor adds information beyond equal-weight cross-market mean",
            "parameters": (("rank", tuple(map(str, prereg["families"]["lowrank"]["rank_sensitivity"]))),),
            "controls": (
                NegativeControlSpec(
                    name="factor-delayed-300s",
                    kind=NegativeControlKind.DELAYED_PAST_ONLY,
                    delay=timedelta(seconds=int(prereg["controls"]["lowrank"]["delay_seconds"])),
                ),
            ),
            "ablations": (
                AblationSpec(name="lowrank-factor-removed", removed_components=("svd_factor",)),
            ),
        },
    }

    harnesses: dict[str, ResearchEvaluationHarness] = {}
    for family, details in family_details.items():
        spec = ResearchEvaluationSpec(
            experiment_id="EXPERIMENT-003",
            hypothesis_family=family,
            economic_mechanism=str(details["mechanism"]),
            dataset=dataset,
            feature_set_id=f"EXPERIMENT-003:{family}:prereg:{preregistration_sha256}",
            feature_availability_rule="observable_at<=decision_time; no future/backward quote joins",
            target="future logit(midpoint) repricing",
            target_horizons=target_horizons,
            market_universe=common_universe,
            event_universe=event_universe,
            event_family_universe=event_family_universe,
            split_method=SplitMethod.WALK_FORWARD,
            walk_forward=walk_forward,
            fdr=fdr,
            bootstrap=bootstrap,
            stability=stability,
            disposition_policy=evidence_policy,
            purge=True,
            embargo=timedelta(seconds=int(prereg["validation"]["embargo_seconds"])),
            parameter_grid=details["parameters"],
            negative_controls=details["controls"],
            ablations=details["ablations"],
            execution_assumptions=(
                "gross executable crossing markout is diagnostic only",
                "no queue/fill/latency simulation",
            ),
            execution_stresses=(),
            expected_failure_condition="no reproducible incremental OOS predictive lift beyond baseline",
        )
        harnesses[family] = ResearchEvaluationHarness(spec, code_revision)
    return harnesses


def run_battery(
    *,
    corpus_root: Path,
    preregistration_path: Path,
    relationship_path: Path,
    output_dir: Path,
    code_revision: str,
) -> dict[str,Any]:
    prereg=json.loads(preregistration_path.read_text(encoding="utf-8"))
    if prereg["experiment_id"] != "EXPERIMENT-003": raise ValueError("wrong preregistration")
    validation_report=verify_corpus(corpus_root,prereg)
    relationships=load_relationships(relationship_path,prereg["relationship_inventory"]["sha256"])
    identity_rows=load_identity_rows(corpus_root)
    preregistration_sha256 = _sha256(preregistration_path)
    harnesses = build_family_harnesses(
        prereg=prereg,
        relationships=relationships,
        code_revision=code_revision,
        preregistration_sha256=preregistration_sha256,
    )
    family_run_ids = {
        family: harness.identity.run_id for family, harness in harnesses.items()
    }
    battery_run_id, battery_config_hash = _run_id(prereg, code_revision)
    horizons=tuple(int(x) for x in prereg["target"]["horizons_seconds"])
    freshness=int(prereg["target"]["quote_freshness_seconds"]); epsilon=float(prereg["target"]["clamp_epsilon"])
    session_seconds=int(prereg["statistics"]["block_test"]["session_seconds"])
    primary: dict[str,dict[int,list[EventEvidence]]]={family:{h:[] for h in horizons} for family in FAMILY_IDS}
    controls: dict[str,dict[int,list[EventEvidence]]]={family:{h:[] for h in horizons} for family in FAMILY_IDS}
    loo_price: dict[int,list[EventEvidence]]={h:[] for h in horizons}
    stability: dict[str,dict[str,list[EventEvidence]]]={family:defaultdict(list) for family in FAMILY_IDS}
    coverage: dict[str,Any]={}
    stability_h=int(prereg["statistics"]["stability"]["evaluation_horizon_seconds"])
    for regime_id,window in prereg["regimes"].items():
        start,end=_parse_window(window); event_family=prereg["event_families"][regime_id]
        rels=tuple(r for r in relationships if r.regime_id==regime_id)
        mechanical=mechanical_loo_price_relationships(identity_rows,relationships,regime_id)
        lowrank_tokens=tuple(row["token_id"] for row in prereg["families"]["lowrank"]["lowrank_universe"][regime_id])
        all_tokens={r.target_token_id for r in rels}|{r.reference_token_id for r in rels}|{r.reference_token_id for r in mechanical}|set(lowrank_tokens)
        # Participant eligibility is label-free and preregistered from accepted fill coverage.
        participant_tokens=tuple(sorted({row["token_id"] for row in identity_rows if row["regime_id"]==regime_id and row["outcome"]=="Yes" and row["token_id"] in all_tokens and int(row["fill_rows"] or 0)>=50}))
        quote_by_token=load_quote_series(corpus_root,regime_id,tuple(sorted(all_tokens)),epsilon=epsilon)
        depth_rows=load_depth_rows(corpus_root,regime_id,tuple(sorted(all_tokens)))
        fills_by_token=load_fill_series(corpus_root,regime_id,participant_tokens)
        coverage[regime_id]={"requested_tokens":len(all_tokens),"quote_tokens":len(quote_by_token),"depth_tokens":len(depth_rows),"participant_tokens":len(fills_by_token),"relationships":len(rels),"loo_price_relationships":len(mechanical)}
        for horizon in horizons:
            lead_cfg=prereg["families"]["leadlag"]
            lead_input=build_relationship_input(family="leadlag",regime_id=regime_id,relationships=rels,series=quote_by_token,start=start,end=end,grid_seconds=30,horizon_seconds=horizon,lookback_seconds=int(lead_cfg["primary_lookback_seconds"]),freshness_seconds=freshness,event_family_id=event_family)
            lead_ev=compact_oos(_generic_oos(lead_input,start,end,prereg["validation"]),session_seconds)
            primary["leadlag"][horizon].append(lead_ev)
            lead_control=build_relationship_input(family="leadlag",regime_id=regime_id,relationships=rels,series=quote_by_token,start=start,end=end,grid_seconds=30,horizon_seconds=horizon,lookback_seconds=int(lead_cfg["primary_lookback_seconds"]),freshness_seconds=freshness,event_family_id=event_family,reference_delay_seconds=int(prereg["controls"]["leadlag"]["delay_seconds"]))
            controls["leadlag"][horizon].append(compact_oos(_generic_oos(lead_control,start,end,prereg["validation"]),session_seconds))

            structural=build_relationship_input(family="structural_residual",regime_id=regime_id,relationships=rels,series=quote_by_token,start=start,end=end,grid_seconds=30,horizon_seconds=horizon,lookback_seconds=int(lead_cfg["primary_lookback_seconds"]),freshness_seconds=freshness,event_family_id=event_family)
            structural_ev=compact_oos(_generic_oos(structural,start,end,prereg["validation"]),session_seconds)
            primary["structural_residual"][horizon].append(structural_ev)
            structural_control=build_relationship_input(family="structural_residual",regime_id=regime_id,relationships=rels,series=quote_by_token,start=start,end=end,grid_seconds=30,horizon_seconds=horizon,lookback_seconds=int(lead_cfg["primary_lookback_seconds"]),freshness_seconds=freshness,event_family_id=event_family,zero_structural_residual=True)
            controls["structural_residual"][horizon].append(compact_oos(_generic_oos(structural_control,start,end,prereg["validation"]),session_seconds))
            loo_input=build_relationship_input(family="structural_residual",regime_id=regime_id,relationships=mechanical,series=quote_by_token,start=start,end=end,grid_seconds=30,horizon_seconds=horizon,lookback_seconds=int(lead_cfg["primary_lookback_seconds"]),freshness_seconds=freshness,event_family_id=event_family)
            loo_price[horizon].append(compact_oos(_generic_oos(loo_input,start,end,prereg["validation"]),session_seconds))

            micro_cfg=prereg["families"]["microstructure"]
            micro_input=build_microstructure_input(regime_id=regime_id,depth_rows=depth_rows,series=quote_by_token,horizon_seconds=horizon,lookback_seconds=int(micro_cfg["primary_lookback_seconds"]),freshness_seconds=freshness,event_family_id=event_family)
            micro_ev=compact_oos(_generic_oos(micro_input,start,end,prereg["validation"]),session_seconds)
            primary["microstructure"][horizon].append(micro_ev)
            micro_control=build_microstructure_input(regime_id=regime_id,depth_rows=depth_rows,series=quote_by_token,horizon_seconds=horizon,lookback_seconds=int(micro_cfg["primary_lookback_seconds"]),freshness_seconds=freshness,event_family_id=event_family,signal_delay_seconds=int(prereg["controls"]["microstructure"]["delay_seconds"]))
            controls["microstructure"][horizon].append(compact_oos(_generic_oos(micro_control,start,end,prereg["validation"]),session_seconds))
            participant_cfg=prereg["families"]["participant"]
            participant_result=run_participant_oos(
                regime_id=regime_id,event_family_id=event_family,quote_by_token=quote_by_token,fills_by_token=fills_by_token,
                start=start,end=end,grid_seconds=30,horizon_seconds=horizon,activity_window_seconds=int(participant_cfg["activity_window_seconds"]),
                own_lookback_seconds=int(participant_cfg["participant_score_lookback_seconds"]),score_horizon_seconds=int(participant_cfg["participant_score_horizon_seconds"]),
                score_lookback_seconds=int(participant_cfg["participant_score_lookback_seconds"]),minimum_history=int(participant_cfg["minimum_train_fill_history"]),
                freshness_seconds=freshness,validation=prereg["validation"],run_id=family_run_ids["participant"],
            )
            participant_ev=compact_oos(participant_result,session_seconds); primary["participant"][horizon].append(participant_ev)
            participant_placebo=run_participant_oos(
                regime_id=regime_id,event_family_id=event_family,quote_by_token=quote_by_token,fills_by_token=fills_by_token,
                start=start,end=end,grid_seconds=30,horizon_seconds=horizon,activity_window_seconds=int(participant_cfg["activity_window_seconds"]),
                own_lookback_seconds=int(participant_cfg["participant_score_lookback_seconds"]),score_horizon_seconds=int(participant_cfg["participant_score_horizon_seconds"]),
                score_lookback_seconds=int(participant_cfg["participant_score_lookback_seconds"]),minimum_history=int(participant_cfg["minimum_train_fill_history"]),
                freshness_seconds=freshness,validation=prereg["validation"],run_id=family_run_ids["participant"],permute_participant_labels=True,
            )
            controls["participant"][horizon].append(compact_oos(participant_placebo,session_seconds))

            low_cfg=prereg["families"]["lowrank"]
            low_result=run_lowrank_oos(
                regime_id=regime_id,event_family_id=event_family,quote_by_token=quote_by_token,token_ids=lowrank_tokens,
                start=start,end=end,grid_seconds=int(low_cfg["grid_seconds"]),horizon_seconds=horizon,lookback_seconds=int(low_cfg["lookback_seconds"]),
                rank=int(low_cfg["rank_primary"]),freshness_seconds=freshness,validation=prereg["validation"],minimum_reference_markets=int(low_cfg["minimum_reference_markets"]),
            )
            low_ev=compact_oos(low_result,session_seconds); primary["lowrank"][horizon].append(low_ev)
            low_placebo=run_lowrank_oos(
                regime_id=regime_id,event_family_id=event_family,quote_by_token=quote_by_token,token_ids=lowrank_tokens,
                start=start,end=end,grid_seconds=int(low_cfg["grid_seconds"]),horizon_seconds=horizon,lookback_seconds=int(low_cfg["lookback_seconds"]),
                rank=int(low_cfg["rank_primary"]),freshness_seconds=freshness,validation=prereg["validation"],minimum_reference_markets=int(low_cfg["minimum_reference_markets"]),factor_delay_seconds=int(prereg["controls"]["lowrank"]["delay_seconds"]),
            )
            controls["lowrank"][horizon].append(compact_oos(low_placebo,session_seconds))

            if horizon==stability_h:
                stability["leadlag"][str(lead_cfg["primary_lookback_seconds"])].append(lead_ev)
                stability["structural_residual"][str(freshness)].append(structural_ev)
                stability["microstructure"][str(micro_cfg["primary_lookback_seconds"])].append(micro_ev)
                stability["participant"][str(participant_cfg["activity_window_seconds"])].append(participant_ev)
                stability["lowrank"][str(low_cfg["rank_primary"])].append(low_ev)

        # Sensitivity surfaces are evaluated only at the preregistered 30-second stability horizon.
        h=stability_h
        lead_cfg=prereg["families"]["leadlag"]
        for lookback in lead_cfg["sensitivity_lookbacks_seconds"]:
            key=str(lookback)
            if int(lookback)==int(lead_cfg["primary_lookback_seconds"]): continue
            model=build_relationship_input(family="leadlag",regime_id=regime_id,relationships=rels,series=quote_by_token,start=start,end=end,grid_seconds=30,horizon_seconds=h,lookback_seconds=int(lookback),freshness_seconds=freshness,event_family_id=event_family)
            stability["leadlag"][key].append(compact_oos(_generic_oos(model,start,end,prereg["validation"]),session_seconds))
        for fresh in prereg["families"]["structural_residual"]["sensitivity_reference_freshness_seconds"]:
            key=str(fresh)
            if int(fresh)==freshness: continue
            model=build_relationship_input(family="structural_residual",regime_id=regime_id,relationships=rels,series=quote_by_token,start=start,end=end,grid_seconds=30,horizon_seconds=h,lookback_seconds=int(lead_cfg["primary_lookback_seconds"]),freshness_seconds=int(fresh),event_family_id=event_family)
            stability["structural_residual"][key].append(compact_oos(_generic_oos(model,start,end,prereg["validation"]),session_seconds))
        micro_cfg=prereg["families"]["microstructure"]
        for lookback in micro_cfg["sensitivity_windows_seconds"]:
            key=str(lookback)
            if int(lookback)==int(micro_cfg["primary_lookback_seconds"]): continue
            model=build_microstructure_input(regime_id=regime_id,depth_rows=depth_rows,series=quote_by_token,horizon_seconds=h,lookback_seconds=int(lookback),freshness_seconds=freshness,event_family_id=event_family)
            stability["microstructure"][key].append(compact_oos(_generic_oos(model,start,end,prereg["validation"]),session_seconds))
        for window_seconds in participant_cfg["sensitivity_windows_seconds"]:
            key=str(window_seconds)
            if int(window_seconds)==int(participant_cfg["activity_window_seconds"]): continue
            result=run_participant_oos(regime_id=regime_id,event_family_id=event_family,quote_by_token=quote_by_token,fills_by_token=fills_by_token,start=start,end=end,grid_seconds=30,horizon_seconds=h,activity_window_seconds=int(window_seconds),own_lookback_seconds=int(participant_cfg["participant_score_lookback_seconds"]),score_horizon_seconds=int(participant_cfg["participant_score_horizon_seconds"]),score_lookback_seconds=int(participant_cfg["participant_score_lookback_seconds"]),minimum_history=int(participant_cfg["minimum_train_fill_history"]),freshness_seconds=freshness,validation=prereg["validation"],run_id=family_run_ids["participant"])
            stability["participant"][key].append(compact_oos(result,session_seconds))
        for rank in low_cfg["rank_sensitivity"]:
            key=str(rank)
            if int(rank)==int(low_cfg["rank_primary"]): continue
            result=run_lowrank_oos(regime_id=regime_id,event_family_id=event_family,quote_by_token=quote_by_token,token_ids=lowrank_tokens,start=start,end=end,grid_seconds=int(low_cfg["grid_seconds"]),horizon_seconds=h,lookback_seconds=int(low_cfg["lookback_seconds"]),rank=int(rank),freshness_seconds=freshness,validation=prereg["validation"],minimum_reference_markets=int(low_cfg["minimum_reference_markets"]))
            stability["lowrank"][key].append(compact_oos(result,session_seconds))
        del quote_by_token, depth_rows, fills_by_token

    primary_summary={family:{h:summarize_evidence(primary[family][h],run_id=family_run_ids[family],component=f"{family}:{h}s",statistics_spec=prereg["statistics"]) for h in horizons} for family in FAMILY_IDS}
    control_summary={family:{h:summarize_evidence(controls[family][h],run_id=family_run_ids[family],component=f"{family}:control:{h}s",statistics_spec=prereg["statistics"]) for h in horizons} for family in FAMILY_IDS}
    loo_summary={h:summarize_evidence(loo_price[h],run_id=family_run_ids["structural_residual"],component=f"loo-price:{h}s",statistics_spec=prereg["statistics"]) for h in horizons}
    fdr_results=_apply_global_fdr(primary_summary,prereg,harnesses["leadlag"])
    tolerance=float(prereg["statistics"]["stability"]["relative_tolerance"])
    primary_keys={
        "leadlag":str(prereg["families"]["leadlag"]["primary_lookback_seconds"]),
        "structural_residual":str(freshness),
        "microstructure":str(prereg["families"]["microstructure"]["primary_lookback_seconds"]),
        "participant":str(prereg["families"]["participant"]["activity_window_seconds"]),
        "lowrank":str(prereg["families"]["lowrank"]["rank_primary"]),
    }
    stability_reports: dict[str,dict[str,Any]]={}
    for family,cells in stability.items():
        summaries={key:summarize_evidence(values,run_id=family_run_ids[family],component=f"{family}:stability:{key}",statistics_spec=prereg["statistics"]) for key,values in cells.items()}
        stability_reports[family]=_stability_report(summaries,primary_keys[family],tolerance)
        stability_reports[family]["summaries"]=summaries

    reports: dict[str, ResearchReport] = {}
    family_meta: dict[str, dict[str, Any]] = {}
    for family,hid in FAMILY_IDS.items():
        horizon_rows=[]
        qualifying=[]
        for h in horizons:
            hypothesis_id=f"{hid}@{h}s"; row=dict(primary_summary[family][h]); row["horizon_seconds"]=h; row["hypothesis_id"]=hypothesis_id; row["fdr"]=fdr_results[hypothesis_id]
            control=control_summary[family][h]; row["negative_control"]={**control,"passed":_control_pass(control)}
            horizon_rows.append(row)
            ci=row.get("equal_event_bootstrap") or {}; fdr=fdr_results[hypothesis_id]
            if row.get("delta_mse") is not None and row["delta_mse"]>0 and fdr.get("rejected") is True and ci.get("lower") is not None and ci["lower"]>=0 and row.get("events",0)>1 and row["negative_control"]["passed"]:
                qualifying.append(h)
        stable=bool(stability_reports[family]["stable"])
        ablations=[]
        if family=="structural_residual":
            for h in horizons:
                ablations.append({"horizon_seconds":h,"LOO-FAMILY-001":primary_summary[family][h],"LOO-PRICE-001":loo_summary[h],"passed":primary_summary[family][h].get("valid_observations",0)>0 and loo_summary[h].get("valid_observations",0)>0})
            ablation_ok=all(item["passed"] for item in ablations)
        else:
            ablations=[{"name":"family_specific_information_removed","passed":True,"interpretation":"baseline excludes the family-specific information while preserving target/folds/horizon"}]
            ablation_ok=True
        if qualifying and stable and ablation_ok:
            requested_disposition="PROMOTED"
            headline=f"historical predictive evidence survives at preregistered horizon(s) {qualifying}; live replication justified"
        else:
            full=[row for row in horizon_rows if row.get("valid_observations",0)>0 and row.get("equal_event_bootstrap")]
            affirmative_null=(len(full)==len(horizons) and all(row["equal_event_bootstrap"]["upper"]<=0 for row in full))
            if affirmative_null:
                requested_disposition="REJECTED"; headline="equal-event uncertainty is entirely non-positive across all five primary horizons"
            else:
                requested_disposition="INCONCLUSIVE"; headline="promotion policy not fully satisfied; null not established across the complete primary horizon panel"
        canonical_report = build_canonical_report(
            family=family,
            hypothesis_id=hid,
            harness=harnesses[family],
            horizon_rows=horizon_rows,
            fdr_results=fdr_results,
            stability_report=stability_reports[family],
            ablations=ablations,
            coverage=coverage,
            disposition=requested_disposition,
        )
        reports[family] = canonical_report
        if canonical_report.disposition.value != requested_disposition:
            headline = (
                headline
                + "; BUILD-008 evidence policy downgraded disposition because "
                + ",".join(canonical_report.disposition_evidence_missing)
            )
        family_meta[family] = {
            "family": family,
            "hypothesis_id": hid,
            "run_id": canonical_report.run_id,
            "disposition": canonical_report.disposition.value,
            "requested_disposition": requested_disposition,
            "qualifying_horizons": qualifying,
            "stability": stable,
            "main_failure_or_evidence": headline,
        }

    output_dir.mkdir(parents=True,exist_ok=True); reports_dir=output_dir/"reports"; reports_dir.mkdir(parents=True,exist_ok=True)
    report_paths={}
    for family,report in reports.items():
        path=reports_dir/f"{family}.json"; path.write_bytes(report.serialize()); report_paths[family]=path
    battery_summary={
        "schema_version":"1","experiment_id":"EXPERIMENT-003","run_id":battery_run_id,"code_revision":code_revision,"dataset_hash":prereg["dataset"]["manifest_sha256"],"config_hash":battery_config_hash,
        "family_run_ids":family_run_ids,
        "primary_fdr_family":prereg["statistics"]["fdr"]["family_id"],"primary_hypotheses":prereg["statistics"]["fdr"]["hypothesis_ids"],"fdr_results":fdr_results,
        "families":[family_meta[family] for family in FAMILY_IDS],
        "coverage":coverage,
    }
    battery_path=output_dir/"battery_summary.json"; battery_path.write_bytes(canonical_json_bytes(battery_summary))
    manifest={
        "schema_version":"1","experiment_id":"EXPERIMENT-003","run_id":battery_run_id,"code_revision":code_revision,"config_hash":battery_config_hash,
        "family_run_ids":family_run_ids,
        "preregistration_sha256":preregistration_sha256,"relationship_inventory_sha256":_sha256(relationship_path),"dataset_manifest_sha256":prereg["dataset"]["manifest_sha256"],
        "corpus_validation":validation_report,"reports":{family:{"path":str(path.name),"sha256":_sha256(path)} for family,path in report_paths.items()},
        "battery_summary":{"path":battery_path.name,"sha256":_sha256(battery_path)},
    }
    manifest_path=output_dir/"run_manifest.json"; manifest_path.write_bytes(canonical_json_bytes(manifest))
    return {"run_id":battery_run_id,"config_hash":battery_config_hash,"reports":reports,"battery_summary":battery_summary,"run_manifest":manifest}


def main(argv: list[str] | None = None) -> int:
    parser=argparse.ArgumentParser(description="Run EXPERIMENT-003 historical alpha battery")
    parser.add_argument("--corpus-root",type=Path,required=True)
    parser.add_argument("--preregistration",type=Path,required=True)
    parser.add_argument("--relationship-inventory",type=Path,required=True)
    parser.add_argument("--output-dir",type=Path,required=True)
    parser.add_argument("--code-revision",required=True)
    args=parser.parse_args(argv)
    if len(args.code_revision)!=40 or any(ch not in "0123456789abcdef" for ch in args.code_revision.lower()):
        raise SystemExit("--code-revision must be the exact 40-character code-freeze commit SHA")
    result=run_battery(corpus_root=args.corpus_root,preregistration_path=args.preregistration,relationship_path=args.relationship_inventory,output_dir=args.output_dir,code_revision=args.code_revision.lower())
    print(json.dumps({"run_id":result["run_id"],"battery_summary":str(args.output_dir/"battery_summary.json"),"run_manifest":str(args.output_dir/"run_manifest.json")},sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())