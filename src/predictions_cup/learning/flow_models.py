"""Frozen regression, weighting, and multiplicity utilities for EXPERIMENT-005A."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class RidgeModel:
    feature_names: tuple[str, ...]
    mean: np.ndarray
    scale: np.ndarray
    coefficient: np.ndarray
    intercept: float
    alpha: float


def hierarchical_equal_weights(levels: list[np.ndarray]) -> np.ndarray:
    """Equal-weight each nested level, then rows inside the terminal group."""

    if not levels:
        raise ValueError("at least one hierarchy level is required")
    length = len(levels[0])
    if any(len(level) != length for level in levels):
        raise ValueError("hierarchy level lengths differ")
    if length == 0:
        return np.zeros(0, dtype=np.float64)

    normalized = [np.asarray(level, dtype=object) for level in levels]
    prefixes = [
        tuple(str(normalized[d][row]) for d in range(len(normalized)))
        for row in range(length)
    ]
    children: list[dict[tuple[str, ...], set[str]]] = []
    for depth, level in enumerate(normalized):
        by_parent: dict[tuple[str, ...], set[str]] = {}
        for row in range(length):
            parent = prefixes[row][:depth]
            by_parent.setdefault(parent, set()).add(str(level[row]))
        children.append(by_parent)

    leaf_counts: dict[tuple[str, ...], int] = {}
    for key in prefixes:
        leaf_counts[key] = leaf_counts.get(key, 0) + 1

    weights = np.ones(length, dtype=np.float64)
    for row, key in enumerate(prefixes):
        weight = 1.0
        for depth in range(len(normalized)):
            parent = key[:depth]
            weight /= len(children[depth][parent])
        weight /= leaf_counts[key]
        weights[row] = weight

    total = float(weights.sum())
    if total <= 0 or not np.isfinite(total):
        raise ValueError("invalid hierarchical weights")
    return weights / total


def _weighted_mean_scale(x: np.ndarray, w: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    wn = np.asarray(w, float)
    wn = wn / wn.sum()
    mean = np.sum(x * wn[:, None], axis=0)
    variance = np.sum((x - mean) ** 2 * wn[:, None], axis=0)
    scale = np.sqrt(np.maximum(variance, 1e-12))
    return mean, scale


def fit_weighted_ridge(
    x: np.ndarray,
    y: np.ndarray,
    w: np.ndarray,
    *,
    feature_names: tuple[str, ...],
    alpha: float = 1.0,
) -> RidgeModel:
    features = np.asarray(x, float)
    target = np.asarray(y, float)
    weights = np.asarray(w, float)
    if features.ndim != 2:
        raise ValueError("x must be two-dimensional")
    if len(features) != len(target) or len(target) != len(weights):
        raise ValueError("ridge input lengths differ")
    if features.shape[1] != len(feature_names):
        raise ValueError("feature_names length differs from x columns")
    if len(target) == 0:
        raise ValueError("cannot fit empty ridge model")
    if alpha < 0:
        raise ValueError("alpha must be non-negative")
    finite = (
        np.all(np.isfinite(features), axis=1)
        & np.isfinite(target)
        & np.isfinite(weights)
        & (weights > 0)
    )
    if not np.all(finite):
        raise ValueError("ridge inputs must be finite with positive weights")

    mean, scale = _weighted_mean_scale(features, weights)
    standardized = (features - mean) / scale
    design = np.column_stack([np.ones(len(standardized)), standardized])
    root_w = np.sqrt(weights)
    weighted_design = design * root_w[:, None]
    weighted_target = target * root_w
    penalty = np.eye(design.shape[1]) * float(alpha)
    penalty[0, 0] = 0.0
    gram = weighted_design.T @ weighted_design + penalty
    rhs = weighted_design.T @ weighted_target
    beta = np.linalg.solve(gram, rhs)
    return RidgeModel(
        feature_names=feature_names,
        mean=mean,
        scale=scale,
        coefficient=beta[1:],
        intercept=float(beta[0]),
        alpha=float(alpha),
    )


def predict_ridge(model: RidgeModel, x: np.ndarray) -> np.ndarray:
    features = np.asarray(x, float)
    if features.ndim != 2 or features.shape[1] != len(model.feature_names):
        raise ValueError("prediction shape does not match model")
    prediction = model.intercept + ((features - model.mean) / model.scale) @ model.coefficient
    return np.asarray(prediction, dtype=np.float64)


def weighted_mse(y: np.ndarray, prediction: np.ndarray, w: np.ndarray) -> float:
    target = np.asarray(y, float)
    pred = np.asarray(prediction, float)
    weights = np.asarray(w, float)
    if not (len(target) == len(pred) == len(weights)):
        raise ValueError("loss input lengths differ")
    return float(np.average((target - pred) ** 2, weights=weights))


def empirical_two_sided_p(observed: float, null_values: np.ndarray) -> float:
    null = np.asarray(null_values, float)
    null = null[np.isfinite(null)]
    if not np.isfinite(observed) or len(null) == 0:
        return 1.0
    exceed = int(np.sum(np.abs(null) >= abs(float(observed))))
    return float((1 + exceed) / (len(null) + 1))


def benjamini_hochberg(p_values: np.ndarray) -> np.ndarray:
    values = np.asarray(p_values, float)
    if values.ndim != 1:
        raise ValueError("p_values must be one-dimensional")
    n = len(values)
    if n == 0:
        return values.copy()
    clean = np.where(np.isfinite(values), np.clip(values, 0.0, 1.0), 1.0)
    order = np.argsort(clean, kind="stable")
    ranked = clean[order]
    adjusted = ranked * n / np.arange(1, n + 1)
    adjusted = np.minimum.accumulate(adjusted[::-1])[::-1]
    adjusted = np.clip(adjusted, 0.0, 1.0)
    result = np.empty(n, dtype=np.float64)
    result[order] = adjusted
    return result


def chronological_split(
    times_ns: np.ndarray,
    *,
    train_fraction: float = 2 / 3,
    embargo_ns: int,
) -> tuple[np.ndarray, np.ndarray]:
    times = np.asarray(times_ns, np.int64)
    if not 0 < train_fraction < 1:
        raise ValueError("train_fraction must be strictly between zero and one")
    if embargo_ns < 0:
        raise ValueError("embargo_ns must be non-negative")
    if len(times) == 0:
        return np.zeros(0, bool), np.zeros(0, bool)
    low = int(times.min())
    high = int(times.max())
    cut = low + int((high - low) * train_fraction)
    return times <= cut - embargo_ns, times >= cut + embargo_ns
