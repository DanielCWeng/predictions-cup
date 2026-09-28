"""Leakage-safe primitives for EXPERIMENT-005E participant ecology."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, cast

import numpy as np

NS = 1_000_000_000
PRICE_EPS = 1e-6


@dataclass(frozen=True)
class EconomicTradeAudit:
    groups: int
    valid_groups: int
    invalid_groups: int
    active_rows: int
    passive_rows: int
    economic_rows: int


@dataclass(frozen=True)
class ChronologicalCuts:
    train_end_ns: int
    dev_end_ns: int


def canonical_yes_price(outcome: object, price: object) -> float:
    """Map a YES/NO outcome-row price onto the canonical YES probability axis."""
    p = float(cast(Any, price))
    side = str(outcome).strip().upper()
    if not np.isfinite(p) or not (0.0 < p < 1.0):
        return float("nan")
    if side == "YES":
        return p
    if side == "NO":
        return 1.0 - p
    return float("nan")


def participant_yes_pressure(outcome: object, owner_side: object) -> float:
    """Return owner-direction YES pressure without claiming aggressor/taker semantics."""
    outcome_text = str(outcome).strip().upper()
    side_text = str(owner_side).strip().upper()
    if outcome_text == "YES" and side_text == "BUY":
        return 1.0
    if outcome_text == "YES" and side_text == "SELL":
        return -1.0
    if outcome_text == "NO" and side_text == "BUY":
        return -1.0
    if outcome_text == "NO" and side_text == "SELL":
        return 1.0
    return float("nan")


def logit_probability(values: np.ndarray) -> np.ndarray:
    """Finite logit transform with only numerical clipping, never label imputation."""
    p = np.asarray(values, dtype=float)
    clipped = np.clip(p, PRICE_EPS, 1.0 - PRICE_EPS)
    return np.log(clipped / (1.0 - clipped))


def chronological_cuts(
    timestamps_ns: np.ndarray,
    *,
    train_fraction: float = 0.70,
    dev_fraction: float = 0.15,
) -> ChronologicalCuts:
    """Timestamp-only split cut points; outcomes cannot influence the boundaries."""
    ts = np.asarray(timestamps_ns, dtype=np.int64)
    if len(ts) < 3:
        raise ValueError("need at least three observations")
    if not (0.0 < train_fraction < 1.0 and 0.0 < dev_fraction < 1.0):
        raise ValueError("invalid split fractions")
    if train_fraction + dev_fraction >= 1.0:
        raise ValueError("TRAIN + DEV must leave HOLDOUT")
    ordered = np.sort(ts)
    train_idx = min(len(ordered) - 2, max(0, int(np.floor(len(ordered) * train_fraction))))
    dev_idx = min(
        len(ordered) - 1,
        max(train_idx + 1, int(np.floor(len(ordered) * (train_fraction + dev_fraction)))),
    )
    return ChronologicalCuts(int(ordered[train_idx]), int(ordered[dev_idx]))


def split_labels(
    timestamps_ns: np.ndarray,
    label_end_ns: np.ndarray,
    cuts: ChronologicalCuts,
    *,
    embargo_seconds: int = 300,
) -> np.ndarray:
    """Assign TRAIN/DEV/HOLDOUT and purge labels crossing the next boundary."""
    ts = np.asarray(timestamps_ns, dtype=np.int64)
    end = np.asarray(label_end_ns, dtype=np.int64)
    if len(ts) != len(end):
        raise ValueError("timestamp and label arrays must align")
    result = np.full(len(ts), "HOLDOUT", dtype=object)
    train = ts < cuts.train_end_ns
    dev = (ts >= cuts.train_end_ns) & (ts < cuts.dev_end_ns)
    result[train] = "TRAIN"
    result[dev] = "DEV"
    embargo = int(embargo_seconds) * NS
    bad_train = train & ((end >= cuts.train_end_ns) | (ts >= cuts.train_end_ns - embargo))
    bad_dev = dev & ((end >= cuts.dev_end_ns) | (ts >= cuts.dev_end_ns - embargo))
    result[bad_train | bad_dev] = "PURGED"
    return result


def prior_count_from_sorted_groups(group_codes: np.ndarray) -> np.ndarray:
    """Prior observation count for rows already ordered by group then time."""
    codes = np.asarray(group_codes, dtype=np.int64)
    out = np.zeros(len(codes), dtype=np.int64)
    if len(codes) == 0:
        return out
    boundaries = np.flatnonzero(np.r_[True, codes[1:] != codes[:-1]])
    ends = np.r_[boundaries[1:], len(codes)]
    for start, end in zip(boundaries, ends, strict=True):
        out[start:end] = np.arange(end - start, dtype=np.int64)
    return out


def prior_cumsum_from_sorted_groups(group_codes: np.ndarray, values: np.ndarray) -> np.ndarray:
    """Strictly-prior cumulative sum for rows ordered by group then time."""
    codes = np.asarray(group_codes, dtype=np.int64)
    x = np.asarray(values, dtype=float)
    if len(codes) != len(x):
        raise ValueError("group and value arrays must align")
    out = np.zeros(len(x), dtype=float)
    if len(x) == 0:
        return out
    boundaries = np.flatnonzero(np.r_[True, codes[1:] != codes[:-1]])
    ends = np.r_[boundaries[1:], len(x)]
    for start, end in zip(boundaries, ends, strict=True):
        cs = np.cumsum(np.nan_to_num(x[start:end], nan=0.0))
        out[start + 1 : end] = cs[:-1]
    return out


def shrunk_historical_score(
    sums: np.ndarray,
    counts: np.ndarray,
    *,
    prior_strength: float = 20.0,
) -> np.ndarray:
    """Empirical-Bayes shrinkage toward neutral zero."""
    s = np.asarray(sums, dtype=float)
    n = np.asarray(counts, dtype=float)
    if len(s) != len(n):
        raise ValueError("sum and count arrays must align")
    if prior_strength <= 0:
        raise ValueError("prior strength must be positive")
    score: np.ndarray = np.divide(s, n + prior_strength, out=np.zeros_like(s), where=np.isfinite(s))
    return score


def ridge_fit(
    x: np.ndarray,
    y: np.ndarray,
    *,
    alpha: float,
    sample_weight: np.ndarray | None = None,
) -> np.ndarray:
    """Fit an intercept plus ridge coefficients without penalising the intercept."""
    x_arr = np.asarray(x, dtype=float)
    y_arr = np.asarray(y, dtype=float)
    if x_arr.ndim != 2 or y_arr.ndim != 1 or len(x_arr) != len(y_arr):
        raise ValueError("invalid ridge inputs")
    design = np.column_stack([np.ones(len(x_arr)), x_arr])
    if sample_weight is None:
        w = np.ones(len(y_arr), dtype=float)
    else:
        w = np.asarray(sample_weight, dtype=float)
        if len(w) != len(y_arr):
            raise ValueError("weight length mismatch")
    root_w = np.sqrt(w)
    weighted_x = design * root_w[:, None]
    weighted_y = y_arr * root_w
    penalty = np.eye(design.shape[1], dtype=float) * float(alpha)
    penalty[0, 0] = 0.0
    beta = np.linalg.solve(weighted_x.T @ weighted_x + penalty, weighted_x.T @ weighted_y)
    return np.asarray(beta, dtype=float)


def ridge_predict(beta: np.ndarray, x: np.ndarray) -> np.ndarray:
    coef = np.asarray(beta, dtype=float)
    x_arr = np.asarray(x, dtype=float)
    if x_arr.ndim != 2 or x_arr.shape[1] + 1 != len(coef):
        raise ValueError("invalid ridge prediction inputs")
    prediction: np.ndarray = coef[0] + x_arr @ coef[1:]
    return prediction


def weighted_mse(
    y: np.ndarray,
    pred: np.ndarray,
    weight: np.ndarray | None = None,
) -> float:
    truth = np.asarray(y, dtype=float)
    estimate = np.asarray(pred, dtype=float)
    if len(truth) != len(estimate):
        raise ValueError("mse arrays must align")
    if weight is None:
        return float(np.mean((truth - estimate) ** 2))
    w = np.asarray(weight, dtype=float)
    return float(np.average((truth - estimate) ** 2, weights=w))


def bh_adjust(
    p_values: dict[str, float],
    *,
    alpha: float = 0.05,
) -> dict[str, dict[str, float | bool | int]]:
    """Deterministic Benjamini-Hochberg adjustment with lexical tie-breaking."""
    if not p_values:
        return {}
    ordered = sorted(p_values.items(), key=lambda item: (item[1], item[0]))
    m = len(ordered)
    q_raw = [min(1.0, float(p) * m / (idx + 1)) for idx, (_, p) in enumerate(ordered)]
    q = q_raw[:]
    for idx in range(m - 2, -1, -1):
        q[idx] = min(q[idx], q[idx + 1])
    out: dict[str, dict[str, float | bool | int]] = {}
    for idx, ((name, p), q_value) in enumerate(zip(ordered, q, strict=True), start=1):
        out[name] = {
            "p": float(p),
            "q": float(q_value),
            "rank": idx,
            "family_size": m,
            "reject": bool(q_value <= alpha),
        }
    return out


def effective_number(shares: Iterable[float]) -> float:
    x = np.asarray(list(shares), dtype=float)
    total = float(np.sum(x))
    if total <= 0:
        return 0.0
    p = x / total
    denom = float(np.sum(p * p))
    return 0.0 if denom <= 0 else 1.0 / denom
