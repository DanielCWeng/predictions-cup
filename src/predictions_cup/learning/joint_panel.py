"""Core numerical primitives for EXPERIMENT-005C joint-panel prediction.

This module is deliberately data-source agnostic. Real historical data is only processed by
the Kaggle runner; local/EC2 tests use synthetic arrays.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

EPS = 1e-12


def canonical_yes_price(outcome: str, price: float) -> float:
    """Map a binary outcome-token price to canonical YES probability."""
    value = outcome.strip().upper()
    if value == "YES":
        return float(price)
    if value == "NO":
        return 1.0 - float(price)
    raise ValueError(f"non-binary outcome: {outcome!r}")


def logit_price(price: np.ndarray, *, epsilon: float = 1e-4) -> np.ndarray:
    """Numerically safe logit while preserving the unclipped probability elsewhere."""
    values = np.asarray(price, dtype=np.float64)
    clipped = np.clip(values, epsilon, 1.0 - epsilon)
    return np.asarray(np.log(clipped) - np.log1p(-clipped), dtype=np.float64)


@dataclass(frozen=True)
class FeatureScaler:
    mean: np.ndarray
    scale: np.ndarray

    def transform(self, values: np.ndarray) -> np.ndarray:
        array = np.asarray(values, dtype=np.float64)
        return np.asarray((array - self.mean) / self.scale, dtype=np.float64)


def fit_feature_scaler(values: np.ndarray) -> FeatureScaler:
    """Fit a finite TRAIN-only standardizer."""
    array = np.asarray(values, dtype=np.float64)
    if array.ndim != 2 or not np.isfinite(array).all():
        raise ValueError("feature matrix must be finite and two-dimensional")
    mean = array.mean(axis=0)
    scale = array.std(axis=0)
    scale = np.where(scale > EPS, scale, 1.0)
    return FeatureScaler(mean=mean, scale=scale)


@dataclass(frozen=True)
class LinearModel:
    coef: np.ndarray
    intercept: np.ndarray
    support: np.ndarray
    alpha: float

    def predict(self, values: np.ndarray) -> np.ndarray:
        x = np.asarray(values, dtype=np.float64)
        return np.asarray(x @ self.coef + self.intercept, dtype=np.float64)


def fit_masked_multioutput(
    x: np.ndarray,
    y: np.ndarray,
    *,
    alpha: float,
    minimum_support: int,
) -> LinearModel:
    """Fit one shared predictor matrix with target-specific missing response masks."""
    features = np.asarray(x, dtype=np.float64)
    targets = np.asarray(y, dtype=np.float64)
    if features.ndim != 2 or targets.ndim != 2:
        raise ValueError("x and y must be matrices")
    if features.shape[0] != targets.shape[0]:
        raise ValueError("x/y row mismatch")
    if not np.isfinite(features).all():
        raise ValueError("x must be finite")
    p = features.shape[1]
    m = targets.shape[1]
    coef = np.zeros((p, m), dtype=np.float64)
    intercept = np.full(m, np.nan, dtype=np.float64)
    support = np.zeros(m, dtype=np.int64)
    eye = np.eye(p, dtype=np.float64)
    for target in range(m):
        mask = np.isfinite(targets[:, target])
        n = int(mask.sum())
        support[target] = n
        if n < minimum_support:
            continue
        xx = features[mask]
        yy = targets[mask, target]
        center = float(yy.mean())
        rhs = xx.T @ (yy - center)
        if alpha == 0.0:
            beta, *_ = np.linalg.lstsq(xx, yy - center, rcond=None)
        else:
            beta = np.linalg.solve(xx.T @ xx + alpha * eye, rhs)
        coef[:, target] = beta
        intercept[target] = center
    return LinearModel(coef=coef, intercept=intercept, support=support, alpha=float(alpha))


def truncate_model(model: LinearModel, rank: int) -> LinearModel:
    """SVD-truncate the fitted predictive coefficient matrix."""
    if rank < 1:
        raise ValueError("rank must be positive")
    u, singular, vt = np.linalg.svd(model.coef, full_matrices=False)
    k = min(rank, len(singular))
    coef = (u[:, :k] * singular[:k]) @ vt[:k]
    return LinearModel(
        coef=coef,
        intercept=model.intercept.copy(),
        support=model.support.copy(),
        alpha=model.alpha,
    )


@dataclass(frozen=True)
class PCAModel:
    mean: np.ndarray
    components: np.ndarray

    def transform(self, values: np.ndarray) -> np.ndarray:
        x = np.asarray(values, dtype=np.float64)
        return np.asarray((x - self.mean) @ self.components.T, dtype=np.float64)


def fit_pca(values: np.ndarray, rank: int) -> PCAModel:
    x = np.asarray(values, dtype=np.float64)
    if x.ndim != 2 or not np.isfinite(x).all():
        raise ValueError("PCA input must be finite 2D")
    mean = x.mean(axis=0)
    _, _, vt = np.linalg.svd(x - mean, full_matrices=False)
    k = min(max(1, rank), vt.shape[0])
    return PCAModel(mean=mean, components=vt[:k])


def strict_asof(
    times: np.ndarray,
    values: np.ndarray,
    query_times: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """As-of state using observations strictly before each query instant."""
    t = np.asarray(times, dtype=np.int64)
    v = np.asarray(values, dtype=np.float64)
    q = np.asarray(query_times, dtype=np.int64)
    if len(t) != len(v):
        raise ValueError("times/values length mismatch")
    idx = np.searchsorted(t, q, side="left") - 1
    out = np.full(len(q), np.nan, dtype=np.float64)
    observed = np.full(len(q), -1, dtype=np.int64)
    valid = idx >= 0
    out[valid] = v[idx[valid]]
    observed[valid] = t[idx[valid]]
    return out, observed


@dataclass(frozen=True)
class MarketGrid:
    returns: np.ndarray
    observed: np.ndarray
    age: np.ndarray
    current: np.ndarray
    current_available: np.ndarray


def market_grid_features(
    times: np.ndarray,
    values: np.ndarray,
    decisions: np.ndarray,
    *,
    grid_seconds: int,
    lag_depth: int,
    age_cap_seconds: int,
) -> MarketGrid:
    """Return/mask/age panel for one market without inventing quiet-period zeros."""
    q = np.asarray(decisions, dtype=np.int64)
    returns = np.full((len(q), lag_depth), np.nan, dtype=np.float64)
    observed = np.zeros((len(q), lag_depth), dtype=np.float64)
    age = np.ones((len(q), lag_depth), dtype=np.float64)
    current, current_time = strict_asof(times, values, q)
    current_age = q - current_time
    current_available = (current_time >= 0) & (current_age <= age_cap_seconds)
    for lag in range(lag_depth):
        end = q - lag * grid_seconds
        start = end - grid_seconds
        end_value, end_time = strict_asof(times, values, end)
        start_value, start_time = strict_asof(times, values, start)
        left = np.searchsorted(times, start, side="left")
        right = np.searchsorted(times, end, side="left")
        has_new = right > left
        valid = (
            has_new
            & (end_time >= 0)
            & (start_time >= 0)
            & ((end - end_time) <= age_cap_seconds)
            & ((start - start_time) <= age_cap_seconds)
        )
        returns[valid, lag] = end_value[valid] - start_value[valid]
        observed[valid, lag] = 1.0
        lag_age = np.where(end_time >= 0, end - end_time, age_cap_seconds)
        age[:, lag] = np.minimum(np.maximum(lag_age, 0), age_cap_seconds) / age_cap_seconds
    return MarketGrid(
        returns=returns,
        observed=observed,
        age=age,
        current=current,
        current_available=current_available,
    )


def future_target(
    times: np.ndarray,
    values: np.ndarray,
    decisions: np.ndarray,
    *,
    horizon_seconds: int,
    age_cap_seconds: int,
) -> np.ndarray:
    """Future change requiring a genuinely new target observation in [t,t+h)."""
    q = np.asarray(decisions, dtype=np.int64)
    future_q = q + horizon_seconds
    base, base_time = strict_asof(times, values, q)
    future, future_time = strict_asof(times, values, future_q)
    left = np.searchsorted(times, q, side="left")
    right = np.searchsorted(times, future_q, side="left")
    has_new = right > left
    valid = (
        has_new
        & (base_time >= 0)
        & (future_time >= 0)
        & ((q - base_time) <= age_cap_seconds)
        & ((future_q - future_time) <= age_cap_seconds)
    )
    result = np.full(len(q), np.nan, dtype=np.float64)
    result[valid] = future[valid] - base[valid]
    return result


def masked_mse(y: np.ndarray, pred: np.ndarray) -> float:
    target = np.asarray(y, dtype=np.float64)
    forecast = np.asarray(pred, dtype=np.float64)
    mask = np.isfinite(target) & np.isfinite(forecast)
    if not mask.any():
        return float("nan")
    return float(np.mean((target[mask] - forecast[mask]) ** 2))


def per_target_mse(y: np.ndarray, pred: np.ndarray) -> np.ndarray:
    target = np.asarray(y, dtype=np.float64)
    forecast = np.asarray(pred, dtype=np.float64)
    result = np.full(target.shape[1], np.nan, dtype=np.float64)
    for col in range(target.shape[1]):
        mask = np.isfinite(target[:, col]) & np.isfinite(forecast[:, col])
        if mask.any():
            result[col] = float(np.mean((target[mask, col] - forecast[mask, col]) ** 2))
    return result


def _rank(values: np.ndarray) -> np.ndarray:
    order = np.argsort(values, kind="mergesort")
    ranks = np.empty(len(values), dtype=np.float64)
    ranks[order] = np.arange(len(values), dtype=np.float64)
    return ranks


def predictive_ic(y: np.ndarray, pred: np.ndarray) -> tuple[float, float]:
    """Mean cross-sectional Spearman IC and median per-target time-series IC."""
    target = np.asarray(y, dtype=np.float64)
    forecast = np.asarray(pred, dtype=np.float64)
    cross: list[float] = []
    for row in range(target.shape[0]):
        mask = np.isfinite(target[row]) & np.isfinite(forecast[row])
        if int(mask.sum()) >= 3:
            a = _rank(target[row, mask])
            b = _rank(forecast[row, mask])
            if a.std() > EPS and b.std() > EPS:
                cross.append(float(np.corrcoef(a, b)[0, 1]))
    temporal: list[float] = []
    for col in range(target.shape[1]):
        mask = np.isfinite(target[:, col]) & np.isfinite(forecast[:, col])
        if int(mask.sum()) >= 3:
            a = _rank(target[mask, col])
            b = _rank(forecast[mask, col])
            if a.std() > EPS and b.std() > EPS:
                temporal.append(float(np.corrcoef(a, b)[0, 1]))
    return (
        float(np.mean(cross)) if cross else float("nan"),
        float(np.median(temporal)) if temporal else float("nan"),
    )


def effective_number(weights: np.ndarray) -> float:
    values = np.abs(np.asarray(weights, dtype=np.float64))
    total = float(values.sum())
    if total <= EPS:
        return 0.0
    shares = values / total
    return float(1.0 / np.sum(shares**2))


def source_target_concentration(
    coef: np.ndarray,
    *,
    source_groups: np.ndarray,
) -> dict[str, float]:
    """Group coefficient mass by source market and summarize concentration."""
    matrix = np.asarray(coef, dtype=np.float64)
    groups = np.asarray(source_groups, dtype=np.int64)
    if matrix.shape[0] != len(groups):
        raise ValueError("source group count does not match coefficient rows")
    source_ids = np.unique(groups)
    source_norm = np.array(
        [np.linalg.norm(matrix[groups == source]) for source in source_ids],
        dtype=np.float64,
    )
    target_norm = np.linalg.norm(matrix, axis=0)
    return {
        "effective_source_markets": effective_number(source_norm),
        "effective_target_markets": effective_number(target_norm),
        "max_source_share": (
            float(source_norm.max() / source_norm.sum()) if source_norm.sum() > EPS else 0.0
        ),
        "max_target_share": (
            float(target_norm.max() / target_norm.sum()) if target_norm.sum() > EPS else 0.0
        ),
    }


def singular_spectrum(matrix: np.ndarray) -> dict[str, object]:
    values = np.linalg.svd(np.asarray(matrix, dtype=np.float64), compute_uv=False)
    energy = values**2
    total = float(energy.sum())
    cumulative = np.cumsum(energy) / total if total > EPS else np.zeros_like(energy)
    effective = effective_number(energy)
    return {
        "singular_values": values.tolist(),
        "cumulative_energy": cumulative.tolist(),
        "effective_rank": effective,
    }