"""Dependence-aware HOLDOUT evidence primitives for EXPERIMENT-005F.

These functions are outcome-agnostic. They operate on already-frozen predictions and
never select features, horizons, candidates, or models.
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np

NS = 1_000_000_000
DEFAULT_BLOCK_SECONDS = 30 * 60


def squared_loss_improvement(
    y: Sequence[float] | np.ndarray,
    baseline_prediction: Sequence[float] | np.ndarray,
    challenger_prediction: Sequence[float] | np.ndarray,
) -> np.ndarray:
    y_a = np.asarray(y, dtype=float)
    b_a = np.asarray(baseline_prediction, dtype=float)
    c_a = np.asarray(challenger_prediction, dtype=float)
    if not (len(y_a) == len(b_a) == len(c_a)):
        raise ValueError("prediction arrays must align")
    result: np.ndarray = (y_a - b_a) ** 2 - (y_a - c_a) ** 2
    return result


def absolute_loss_improvement(
    y: Sequence[float] | np.ndarray,
    baseline_prediction: Sequence[float] | np.ndarray,
    challenger_prediction: Sequence[float] | np.ndarray,
) -> np.ndarray:
    y_a = np.asarray(y, dtype=float)
    b_a = np.asarray(baseline_prediction, dtype=float)
    c_a = np.asarray(challenger_prediction, dtype=float)
    if not (len(y_a) == len(b_a) == len(c_a)):
        raise ValueError("prediction arrays must align")
    result: np.ndarray = np.abs(y_a - b_a) - np.abs(y_a - c_a)
    return result


def block_means(
    times_ns: Sequence[int],
    values: Sequence[float],
    *,
    block_seconds: int = DEFAULT_BLOCK_SECONDS,
) -> tuple[np.ndarray, np.ndarray]:
    t = np.asarray(times_ns, dtype=np.int64)
    x = np.asarray(values, dtype=float)
    if len(t) != len(x):
        raise ValueError("times/values mismatch")
    if block_seconds <= 0:
        raise ValueError("block_seconds must be positive")
    valid = np.isfinite(x)
    t = t[valid]
    x = x[valid]
    if len(x) == 0:
        return np.zeros(0, dtype=np.int64), np.zeros(0, dtype=float)
    block = t // (block_seconds * NS)
    unique, inverse = np.unique(block, return_inverse=True)
    sums = np.bincount(inverse, weights=x)
    counts = np.bincount(inverse)
    return unique.astype(np.int64), sums / counts


def one_sided_signflip_p(
    block_values: Sequence[float],
    *,
    draws: int = 4096,
    seed: int = 0,
) -> float:
    x = np.asarray(block_values, dtype=float)
    x = x[np.isfinite(x)]
    if len(x) < 3:
        return 1.0
    observed = float(np.mean(x))
    if observed <= 0.0:
        return 1.0
    if len(x) <= 18:
        total = 1 << len(x)
        ge = 0
        for mask in range(total):
            signs = np.fromiter(
                (1.0 if (mask >> j) & 1 else -1.0 for j in range(len(x))),
                dtype=float,
                count=len(x),
            )
            if float(np.mean(x * signs)) >= observed - 1e-15:
                ge += 1
        return ge / total
    if draws <= 0:
        raise ValueError("draws must be positive")
    rng = np.random.default_rng(seed)
    ge = 0
    for _ in range(draws):
        signs = rng.choice(np.array([-1.0, 1.0]), size=len(x))
        if float(np.mean(x * signs)) >= observed - 1e-15:
            ge += 1
    return (ge + 1) / (draws + 1)


def moving_block_bootstrap_mean(
    values: Sequence[float],
    *,
    block_length: int,
    draws: int = 2000,
    seed: int = 0,
    alpha: float = 0.05,
) -> dict[str, float]:
    x = np.asarray(values, dtype=float)
    x = x[np.isfinite(x)]
    if len(x) == 0:
        return {"mean": np.nan, "lower": np.nan, "upper": np.nan}
    if block_length <= 0 or draws <= 0:
        raise ValueError("block_length and draws must be positive")
    if not 0.0 < alpha < 1.0:
        raise ValueError("alpha must be in (0,1)")
    block_length = min(int(block_length), len(x))
    starts_max = max(1, len(x) - block_length + 1)
    rng = np.random.default_rng(seed)
    means = np.empty(draws, dtype=float)
    for draw in range(draws):
        sample: list[float] = []
        while len(sample) < len(x):
            start = int(rng.integers(0, starts_max))
            sample.extend(x[start : start + block_length].tolist())
        means[draw] = float(np.mean(sample[: len(x)]))
    return {
        "mean": float(np.mean(x)),
        "lower": float(np.quantile(means, alpha / 2)),
        "upper": float(np.quantile(means, 1 - alpha / 2)),
    }


def leave_group_out_means(
    groups: Sequence[object],
    values: Sequence[float],
) -> dict[str, float]:
    g = np.asarray(groups, dtype=object)
    x = np.asarray(values, dtype=float)
    if len(g) != len(x):
        raise ValueError("groups/values mismatch")
    out: dict[str, float] = {}
    for value in sorted({str(v) for v in g}):
        keep = np.asarray([str(v) != value for v in g], dtype=bool)
        finite = keep & np.isfinite(x)
        out[value] = float(np.mean(x[finite])) if np.any(finite) else np.nan
    return out


def stability_summary(
    groups: Sequence[object],
    values: Sequence[float],
) -> dict[str, float | int | bool]:
    loo = leave_group_out_means(groups, values)
    finite = np.asarray([v for v in loo.values() if np.isfinite(v)], dtype=float)
    return {
        "groups": len(loo),
        "positive_groups": int(np.sum(finite > 0.0)),
        "all_positive": bool(len(finite) > 0 and np.all(finite > 0.0)),
        "min_leave_one_out": float(np.min(finite)) if len(finite) else np.nan,
        "max_leave_one_out": float(np.max(finite)) if len(finite) else np.nan,
    }

def event_block_means(
    events: Sequence[object],
    times_ns: Sequence[int],
    values: Sequence[float],
    *,
    block_seconds: int = DEFAULT_BLOCK_SECONDS,
) -> np.ndarray:
    """Mean values in event-local wall-clock blocks for sign-flip inference."""
    e = np.asarray(events, dtype=object)
    t = np.asarray(times_ns, dtype=np.int64)
    x = np.asarray(values, dtype=float)
    if not (len(e) == len(t) == len(x)):
        raise ValueError("events/times/values mismatch")
    if block_seconds <= 0:
        raise ValueError("block_seconds must be positive")
    buckets: dict[tuple[str, int], list[float]] = {}
    width = block_seconds * NS
    for event, ts, value in zip(e, t, x, strict=True):
        if not np.isfinite(value):
            continue
        key = (str(event), int(ts) // width)
        buckets.setdefault(key, []).append(float(value))
    return np.asarray(
        [float(np.mean(buckets[key])) for key in sorted(buckets)],
        dtype=float,
    )


def equal_event_time_block_bootstrap(
    events: Sequence[object],
    times_ns: Sequence[int],
    values: Sequence[float],
    *,
    block_length_minutes: int,
    draws: int = 2000,
    seed: int = 0,
    alpha: float = 0.05,
) -> dict[str, float | int]:
    """Moving-block bootstrap on one-minute event grids with equal event weighting.

    Each event is expanded to a one-minute wall-clock grid. Missing minutes remain
    missing and are carried through block resampling, so a block never jumps across
    an unobserved wall-clock gap. Event means are weighted equally per draw.
    """
    e = np.asarray(events, dtype=object)
    t = np.asarray(times_ns, dtype=np.int64)
    x = np.asarray(values, dtype=float)
    if not (len(e) == len(t) == len(x)):
        raise ValueError("events/times/values mismatch")
    if block_length_minutes <= 0 or draws <= 0:
        raise ValueError("block length and draws must be positive")
    if not 0.0 < alpha < 1.0:
        raise ValueError("alpha must be in (0,1)")

    minute_ns = 60 * NS
    grids: list[np.ndarray] = []
    event_means: list[float] = []
    for event in sorted({str(v) for v in e}):
        mask = np.asarray([str(v) == event for v in e], dtype=bool) & np.isfinite(x)
        if not np.any(mask):
            continue
        mins = t[mask] // minute_ns
        vals = x[mask]
        unique = np.unique(mins)
        lo = int(unique.min())
        hi = int(unique.max())
        grid = np.full(hi - lo + 1, np.nan, dtype=float)
        for minute in unique:
            m = mins == minute
            grid[int(minute) - lo] = float(np.mean(vals[m]))
        grids.append(grid)
        event_means.append(float(np.nanmean(grid)))

    finite_raw = x[np.isfinite(x)]
    if not grids:
        return {
            "event_equal_mean": np.nan,
            "raw_observation_mean": np.nan,
            "lower": np.nan,
            "upper": np.nan,
            "events": 0,
            "draws": draws,
        }

    rng = np.random.default_rng(seed)
    draw_means = np.empty(draws, dtype=float)
    for draw in range(draws):
        sampled_event_means: list[float] = []
        for grid in grids:
            n = len(grid)
            block = min(int(block_length_minutes), n)
            max_start = max(1, n - block + 1)
            sampled: list[float] = []
            while len(sampled) < n:
                start = int(rng.integers(0, max_start))
                sampled.extend(grid[start : start + block].tolist())
            sample = np.asarray(sampled[:n], dtype=float)
            if np.any(np.isfinite(sample)):
                sampled_event_means.append(float(np.nanmean(sample)))
        draw_means[draw] = (
            float(np.mean(sampled_event_means))
            if sampled_event_means
            else np.nan
        )

    finite_draws = draw_means[np.isfinite(draw_means)]
    return {
        "event_equal_mean": float(np.mean(event_means)),
        "raw_observation_mean": (
            float(np.mean(finite_raw)) if len(finite_raw) else np.nan
        ),
        "lower": (
            float(np.quantile(finite_draws, alpha / 2))
            if len(finite_draws)
            else np.nan
        ),
        "upper": (
            float(np.quantile(finite_draws, 1 - alpha / 2))
            if len(finite_draws)
            else np.nan
        ),
        "events": len(grids),
        "draws": draws,
    }

