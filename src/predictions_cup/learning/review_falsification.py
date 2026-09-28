"""Post-HOLDOUT falsification statistics for EXPERIMENT-005C.

All functions are data-source agnostic. Real-data execution is Kaggle-only.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

EPS = 1e-15


@dataclass(frozen=True)
class ClusterSeries:
    mean: float
    standard_error: float
    t_stat: float
    n_rows: int
    n_blocks: int
    hours: np.ndarray
    influence: np.ndarray


def utc_hour_cluster_series(
    timestamps: np.ndarray,
    loss_advantage: np.ndarray,
) -> ClusterSeries:
    """Build one-way UTC-hour cluster influence terms for a mean."""
    times = np.asarray(timestamps, dtype=np.int64)
    values = np.asarray(loss_advantage, dtype=np.float64)
    if times.shape != values.shape:
        raise ValueError("timestamps/loss_advantage shape mismatch")

    valid = np.isfinite(values)
    times = times[valid]
    values = values[valid]
    if len(values) < 2:
        raise ValueError("need at least two finite observations")

    hours = times // 3600
    unique = np.unique(hours)
    mean = float(values.mean())
    influence = np.zeros(len(unique), dtype=np.float64)
    for index, hour in enumerate(unique):
        mask = hours == hour
        influence[index] = float(np.sum(values[mask] - mean))

    n = len(values)
    blocks = len(unique)
    if blocks < 2:
        se = float("nan")
        t_stat = float("nan")
    else:
        variance = (blocks / (blocks - 1.0)) * float(np.sum(influence**2))
        se = float(np.sqrt(max(variance, 0.0)) / n)
        t_stat = float(mean / se) if se > EPS else float("nan")

    return ClusterSeries(
        mean=mean,
        standard_error=se,
        t_stat=t_stat,
        n_rows=n,
        n_blocks=blocks,
        hours=unique,
        influence=influence,
    )


@dataclass(frozen=True)
class MaxTResult:
    max_statistics: np.ndarray
    draw_statistics: np.ndarray
    effective_tests: float


def synchronized_hour_wild_max_t(
    series: list[ClusterSeries],
    *,
    draws: int,
    seed: int,
) -> MaxTResult:
    """Correlation-preserving max-t bootstrap with shared UTC-hour signs."""
    if not series:
        raise ValueError("series must be non-empty")
    if draws < 1:
        raise ValueError("draws must be positive")

    all_hours = np.unique(
        np.concatenate([item.hours for item in series])
    )
    hour_to_col = {int(hour): idx for idx, hour in enumerate(all_hours)}

    weights = np.zeros((len(series), len(all_hours)), dtype=np.float64)
    for row, item in enumerate(series):
        if (
            not np.isfinite(item.standard_error)
            or item.standard_error <= EPS
        ):
            continue
        denominator = item.n_rows * item.standard_error
        for hour, influence in zip(
            item.hours,
            item.influence,
            strict=True,
        ):
            weights[row, hour_to_col[int(hour)]] = influence / denominator

    rng = np.random.default_rng(seed)
    draw_statistics = np.empty((draws, len(series)), dtype=np.float64)
    chunk = 1000
    for start in range(0, draws, chunk):
        stop = min(draws, start + chunk)
        signs = rng.choice(
            np.array([-1.0, 1.0]),
            size=(stop - start, len(all_hours)),
        )
        draw_statistics[start:stop] = signs @ weights.T

    max_statistics = np.nanmax(draw_statistics, axis=1)

    corr = np.corrcoef(draw_statistics, rowvar=False)
    corr = np.nan_to_num(corr, nan=0.0)
    corr = (corr + corr.T) / 2.0
    np.fill_diagonal(corr, 1.0)
    eigenvalues = np.linalg.eigvalsh(corr)
    eigenvalues = np.clip(eigenvalues, 0.0, None)
    denominator = float(np.sum(eigenvalues**2))
    effective = (
        float(np.sum(eigenvalues) ** 2 / denominator)
        if denominator > EPS
        else 0.0
    )

    return MaxTResult(
        max_statistics=max_statistics,
        draw_statistics=draw_statistics,
        effective_tests=effective,
    )


def bootstrap_p_value(
    null_statistics: np.ndarray,
    observed: float,
) -> float:
    values = np.asarray(null_statistics, dtype=np.float64)
    finite = values[np.isfinite(values)]
    if not np.isfinite(observed) or len(finite) == 0:
        return float("nan")
    return float((1 + np.sum(finite >= observed)) / (len(finite) + 1))


@dataclass(frozen=True)
class BlockSensitivityResult:
    mean: float
    ci_low: float | None
    ci_high: float | None
    n_rows: int
    block_rows: int
    effective_block_equivalents: float
    status: str


def moving_block_mean_sensitivity(
    values: np.ndarray,
    *,
    block_rows: int,
    draws: int,
    seed: int,
    minimum_effective_blocks: float = 4.0,
) -> BlockSensitivityResult:
    """Moving-block mean bootstrap with an explicit stability threshold."""
    finite = np.asarray(values, dtype=np.float64)
    finite = finite[np.isfinite(finite)]
    n = len(finite)
    if n == 0:
        return BlockSensitivityResult(
            mean=float("nan"),
            ci_low=None,
            ci_high=None,
            n_rows=0,
            block_rows=block_rows,
            effective_block_equivalents=0.0,
            status="NO_VALID_ROWS",
        )
    if block_rows < 1:
        raise ValueError("block_rows must be positive")

    effective = n / block_rows
    mean = float(finite.mean())
    if block_rows > n:
        return BlockSensitivityResult(
            mean=mean,
            ci_low=None,
            ci_high=None,
            n_rows=n,
            block_rows=block_rows,
            effective_block_equivalents=effective,
            status="INSUFFICIENT_DURATION",
        )
    if effective < minimum_effective_blocks:
        return BlockSensitivityResult(
            mean=mean,
            ci_low=None,
            ci_high=None,
            n_rows=n,
            block_rows=block_rows,
            effective_block_equivalents=effective,
            status="UNSTABLE_TOO_FEW_BLOCK_EQUIVALENTS",
        )

    needed = int(np.ceil(n / block_rows))
    max_start = n - block_rows
    rng = np.random.default_rng(seed)
    results = np.empty(draws, dtype=np.float64)
    for draw in range(draws):
        starts = rng.integers(0, max_start + 1, size=needed)
        sampled = np.concatenate(
            [finite[start : start + block_rows] for start in starts]
        )[:n]
        results[draw] = float(sampled.mean())

    return BlockSensitivityResult(
        mean=mean,
        ci_low=float(np.quantile(results, 0.025)),
        ci_high=float(np.quantile(results, 0.975)),
        n_rows=n,
        block_rows=block_rows,
        effective_block_equivalents=effective,
        status="STABLE_ENOUGH_FOR_CI",
    )


def per_time_loss_advantage(
    target: np.ndarray,
    challenger: np.ndarray,
    baseline: np.ndarray,
) -> np.ndarray:
    """Average B2-loss minus challenger-loss over common finite targets."""
    y = np.asarray(target, dtype=np.float64)
    pred = np.asarray(challenger, dtype=np.float64)
    base = np.asarray(baseline, dtype=np.float64)
    if y.shape != pred.shape or y.shape != base.shape:
        raise ValueError("target/prediction shapes must match")

    result = np.full(y.shape[0], np.nan, dtype=np.float64)
    for row in range(y.shape[0]):
        mask = (
            np.isfinite(y[row])
            & np.isfinite(pred[row])
            & np.isfinite(base[row])
        )
        if mask.any():
            base_loss = (y[row, mask] - base[row, mask]) ** 2
            challenger_loss = (y[row, mask] - pred[row, mask]) ** 2
            result[row] = float(np.mean(base_loss - challenger_loss))
    return result


def _pooled_metrics(
    target: np.ndarray,
    challenger: np.ndarray,
    baseline: np.ndarray,
    row_keep: np.ndarray,
) -> tuple[float, float, float]:
    y = target[row_keep]
    pred = challenger[row_keep]
    base = baseline[row_keep]
    mask = np.isfinite(y) & np.isfinite(pred) & np.isfinite(base)
    if not mask.any():
        return float("nan"), float("nan"), float("nan")
    challenger_mse = float(np.mean((y[mask] - pred[mask]) ** 2))
    baseline_mse = float(np.mean((y[mask] - base[mask]) ** 2))
    improvement = (
        (baseline_mse - challenger_mse) / baseline_mse
        if baseline_mse > EPS
        else float("nan")
    )
    return challenger_mse, baseline_mse, improvement


def temporal_hour_concentration(
    timestamps: np.ndarray,
    target: np.ndarray,
    challenger: np.ndarray,
    baseline: np.ndarray,
) -> tuple[list[dict[str, float | int]], dict[str, float | int | None]]:
    """Fixed UTC-hour temporal concentration and removal diagnostics."""
    times = np.asarray(timestamps, dtype=np.int64)
    y = np.asarray(target, dtype=np.float64)
    pred = np.asarray(challenger, dtype=np.float64)
    base = np.asarray(baseline, dtype=np.float64)
    if y.shape != pred.shape or y.shape != base.shape:
        raise ValueError("target/prediction shapes must match")
    if y.shape[0] != len(times):
        raise ValueError("timestamp row count mismatch")

    common = np.isfinite(y) & np.isfinite(pred) & np.isfinite(base)
    active_rows = common.any(axis=1)
    if not active_rows.any():
        return [], {
            "active_blocks": 0,
            "positive_blocks": 0,
            "cumulative_loss_advantage": float("nan"),
            "top1_positive_share": None,
            "top3_positive_share": None,
            "top5_positive_share": None,
            "first_half_loss_advantage": float("nan"),
            "second_half_loss_advantage": float("nan"),
            "improvement_after_remove_top1": float("nan"),
            "improvement_after_remove_top3": float("nan"),
        }

    hours = times // 3600
    rows: list[dict[str, float | int]] = []
    for hour in np.unique(hours[active_rows]):
        row_mask = (hours == hour) & active_rows
        cell_mask = common[row_mask]
        yy = y[row_mask]
        pp = pred[row_mask]
        bb = base[row_mask]
        diff = (yy - bb) ** 2 - (yy - pp) ** 2
        advantage = float(np.sum(diff[cell_mask]))
        rows.append(
            {
                "utc_hour": int(hour),
                "start_timestamp": int(hour * 3600),
                "decision_rows": int(np.sum(row_mask)),
                "valid_target_cells": int(np.sum(cell_mask)),
                "loss_advantage_sum": advantage,
                "loss_advantage_mean": (
                    advantage / int(np.sum(cell_mask))
                    if np.sum(cell_mask)
                    else float("nan")
                ),
            }
        )

    contributions = np.array(
        [float(row["loss_advantage_sum"]) for row in rows],
        dtype=np.float64,
    )
    positive = np.sort(contributions[contributions > 0])[::-1]
    positive_total = float(positive.sum())

    def share(top: int) -> float | None:
        if positive_total <= EPS:
            return None
        return float(positive[:top].sum() / positive_total)

    valid_times = times[active_rows]
    midpoint = int((int(valid_times.min()) + int(valid_times.max())) // 2)
    first_keep = times <= midpoint
    second_keep = times > midpoint

    def total_advantage(row_keep: np.ndarray) -> float:
        mask = common[row_keep]
        yy = y[row_keep]
        pp = pred[row_keep]
        bb = base[row_keep]
        if not mask.any():
            return float("nan")
        diff = (yy - bb) ** 2 - (yy - pp) ** 2
        return float(np.sum(diff[mask]))

    ranked_positive_hours = [
        int(rows[index]["utc_hour"])
        for index in np.argsort(contributions)[::-1]
        if contributions[index] > 0
    ]

    keep_top1 = np.ones(len(times), dtype=bool)
    if ranked_positive_hours:
        keep_top1 &= hours != ranked_positive_hours[0]

    keep_top3 = np.ones(len(times), dtype=bool)
    for hour in ranked_positive_hours[:3]:
        keep_top3 &= hours != hour

    _, _, imp1 = _pooled_metrics(y, pred, base, keep_top1)
    _, _, imp3 = _pooled_metrics(y, pred, base, keep_top3)

    summary: dict[str, float | int | None] = {
        "active_blocks": len(rows),
        "positive_blocks": int(np.sum(contributions > 0)),
        "cumulative_loss_advantage": float(contributions.sum()),
        "top1_positive_share": share(1),
        "top3_positive_share": share(3),
        "top5_positive_share": share(5),
        "midpoint_timestamp": midpoint,
        "first_half_loss_advantage": total_advantage(first_keep),
        "second_half_loss_advantage": total_advantage(second_keep),
        "improvement_after_remove_top1": imp1,
        "improvement_after_remove_top3": imp3,
    }
    return rows, summary