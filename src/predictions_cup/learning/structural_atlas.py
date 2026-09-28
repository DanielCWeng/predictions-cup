"""Structural probability / relative-value primitives for EXPERIMENT-005D.

The module is intentionally outcome-agnostic. It contains numerical building
blocks only; semantic classification and empirical selection live in the 005D
preregistration/runner.
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum

import numpy as np
from numpy.typing import NDArray

EPS = 1e-6


class StructuralClass(StrEnum):
    HARD = "HARD"
    SOFT = "SOFT"
    UNKNOWN = "UNKNOWN"


class LeakageMode(StrEnum):
    LOO_PRICE = "LOO-PRICE"
    LOO_FAMILY = "LOO-FAMILY"


@dataclass(frozen=True, slots=True)
class AffineFit:
    intercept: float
    slope: float
    ridge: float = 0.0

    def predict(self, x: NDArray[np.float64]) -> NDArray[np.float64]:
        return self.intercept + self.slope * x


def clip_probability(
    value: NDArray[np.float64] | float, eps: float = EPS
) -> NDArray[np.float64]:
    return np.asarray(np.clip(value, eps, 1.0 - eps), dtype=np.float64)


def logit(
    value: NDArray[np.float64] | float, eps: float = EPS
) -> NDArray[np.float64]:
    p = clip_probability(value, eps)
    return np.asarray(np.log(p / (1.0 - p)), dtype=np.float64)


def logistic(value: NDArray[np.float64] | float) -> NDArray[np.float64]:
    x = np.asarray(value, dtype=float)
    out = np.empty_like(x)
    positive = x >= 0
    out[positive] = 1.0 / (1.0 + np.exp(-x[positive]))
    ex = np.exp(x[~positive])
    out[~positive] = ex / (1.0 + ex)
    return out


def _require_vector(values: Iterable[float]) -> NDArray[np.float64]:
    x = np.asarray(tuple(values), dtype=float)
    if x.ndim != 1 or len(x) == 0:
        raise ValueError("values must be a non-empty one-dimensional vector")
    if not np.all(np.isfinite(x)):
        raise ValueError("values must be finite")
    return x


def project_simplex(values: Iterable[float]) -> NDArray[np.float64]:
    """Euclidean projection onto p>=0, sum(p)=1."""
    v = _require_vector(values)
    u = np.sort(v)[::-1]
    cssv = np.cumsum(u) - 1.0
    rho = np.flatnonzero(u - cssv / (np.arange(len(u)) + 1.0) > 0.0)
    if len(rho) == 0:
        return np.full_like(v, 1.0 / len(v))
    j = int(rho[-1])
    theta = cssv[j] / (j + 1.0)
    return np.asarray(np.maximum(v - theta, 0.0), dtype=np.float64)


def project_capped_simplex(values: Iterable[float]) -> NDArray[np.float64]:
    """Euclidean projection onto 0<=p<=1, sum(p)<=1."""
    v = np.clip(_require_vector(values), 0.0, 1.0)
    if float(v.sum()) <= 1.0:
        return v
    return project_simplex(v)


def weighted_partition_projection(
    values: Iterable[float], weights: Iterable[float]
) -> NDArray[np.float64]:
    """Weighted quadratic projection onto the probability simplex."""
    v = _require_vector(values)
    w = _require_vector(weights)
    if len(v) != len(w) or np.any(w <= 0.0):
        raise ValueError("positive weights must match values")
    lo, hi = -4.0 * float(w.max()), 4.0 * float(w.max())
    for _ in range(100):
        lam = (lo + hi) / 2.0
        p = np.clip(v - lam / (2.0 * w), 0.0, 1.0)
        if float(p.sum()) > 1.0:
            lo = lam
        else:
            hi = lam
    p = np.clip(v - hi / (2.0 * w), 0.0, 1.0)
    total = float(p.sum())
    if total <= 0.0:
        return np.full_like(v, 1.0 / len(v))
    return p / total


def kl_partition_projection(
    values: Iterable[float], weights: Iterable[float], eps: float = EPS
) -> NDArray[np.float64]:
    """Weighted Bernoulli-KL projection onto sum(p)=1."""
    q = clip_probability(_require_vector(values), eps)
    w = _require_vector(weights)
    if len(q) != len(w) or np.any(w <= 0.0):
        raise ValueError("positive weights must match values")
    z = logit(q, eps)
    lo, hi = -80.0 * float(w.max()), 80.0 * float(w.max())
    for _ in range(120):
        lam = (lo + hi) / 2.0
        p = logistic(z - lam / w)
        if float(p.sum()) > 1.0:
            lo = lam
        else:
            hi = lam
    p = logistic(z - hi / w)
    return np.asarray(p / float(p.sum()), dtype=np.float64)


def weighted_isotonic_nonincreasing(
    values: Iterable[float], weights: Iterable[float] | None = None
) -> NDArray[np.float64]:
    """Weighted PAV projection with 1>=x_1>=...>=x_n>=0."""
    y = np.clip(_require_vector(values), 0.0, 1.0)
    w = np.ones(len(y), dtype=float) if weights is None else _require_vector(weights)
    if len(w) != len(y) or np.any(w <= 0.0):
        raise ValueError("positive weights must match values")
    means: list[float] = []
    masses: list[float] = []
    counts: list[int] = []
    for value, mass in zip(-y, w, strict=True):
        means.append(float(value))
        masses.append(float(mass))
        counts.append(1)
        while len(means) >= 2 and means[-2] > means[-1]:
            new_mass = masses[-2] + masses[-1]
            new_mean = (means[-2] * masses[-2] + means[-1] * masses[-1]) / new_mass
            new_count = counts[-2] + counts[-1]
            means[-2:] = [new_mean]
            masses[-2:] = [new_mass]
            counts[-2:] = [new_count]
    out = np.empty(len(y), dtype=float)
    pos = 0
    for mean, count in zip(means, counts, strict=True):
        out[pos : pos + count] = -mean
        pos += count
    return np.clip(out, 0.0, 1.0)


def implied_pmf_from_thresholds(thresholds: Iterable[float]) -> NDArray[np.float64]:
    s = _require_vector(thresholds)
    if np.any(np.diff(s) > 1e-10):
        raise ValueError("threshold surface must be non-increasing")
    return np.maximum(s[:-1] - s[1:], 0.0)


def leave_one_out_mean(
    values: Iterable[float], target_index: int, weights: Iterable[float] | None = None
) -> float:
    v = _require_vector(values)
    if not 0 <= target_index < len(v) or len(v) < 2:
        raise ValueError("target_index invalid or family too small")
    mask = np.ones(len(v), dtype=bool)
    mask[target_index] = False
    if weights is None:
        return float(v[mask].mean())
    w = _require_vector(weights)
    if len(w) != len(v) or np.any(w <= 0.0):
        raise ValueError("positive weights must match values")
    return float(np.average(v[mask], weights=w[mask]))


def fit_affine(x: Iterable[float], y: Iterable[float], ridge: float = 0.0) -> AffineFit:
    xx, yy = _require_vector(x), _require_vector(y)
    if len(xx) != len(yy) or len(xx) < 3 or ridge < 0.0:
        raise ValueError("invalid affine training sample")
    xc, yc = xx - xx.mean(), yy - yy.mean()
    denom = float(xc @ xc) + ridge
    slope = 0.0 if denom <= 0.0 else float((xc @ yc) / denom)
    intercept = float(yy.mean() - slope * xx.mean())
    return AffineFit(intercept, slope, ridge)


def fit_huber_affine(
    x: Iterable[float],
    y: Iterable[float],
    delta: float = 1.5,
    iterations: int = 20,
) -> AffineFit:
    """Small deterministic Huber IRLS fit; scale is MAD from TRAIN only."""
    xx, yy = _require_vector(x), _require_vector(y)
    if len(xx) != len(yy) or len(xx) < 5 or delta <= 0.0:
        raise ValueError("invalid Huber training sample")
    fit = fit_affine(xx, yy)
    for _ in range(iterations):
        resid = yy - fit.predict(xx)
        med = float(np.median(resid))
        mad = float(np.median(np.abs(resid - med)))
        scale = max(1.4826 * mad, 1e-8)
        u = np.abs(resid) / (delta * scale)
        weight = np.ones_like(u)
        mask = u > 1.0
        weight[mask] = 1.0 / u[mask]
        xbar = float(np.average(xx, weights=weight))
        ybar = float(np.average(yy, weights=weight))
        xc, yc = xx - xbar, yy - ybar
        denom = float(np.sum(weight * xc * xc))
        slope = 0.0 if denom <= 0.0 else float(np.sum(weight * xc * yc) / denom)
        fit = AffineFit(ybar - slope * xbar, slope)
    return fit


def common_factor_residual(panel: NDArray[np.float64], target_index: int) -> NDArray[np.float64]:
    x = np.asarray(panel, dtype=float)
    if x.ndim != 2 or not 0 <= target_index < x.shape[1] or x.shape[1] < 2:
        raise ValueError("invalid panel/target")
    refs = np.delete(x, target_index, axis=1)
    return np.asarray(
        x[:, target_index] - np.nanmean(refs, axis=1), dtype=np.float64
    )


def structural_residual(
    observed: Iterable[float], coherent: Iterable[float]
) -> NDArray[np.float64]:
    a, b = _require_vector(observed), _require_vector(coherent)
    if len(a) != len(b):
        raise ValueError("observed/coherent vectors must match")
    return np.asarray(logit(a) - logit(b), dtype=np.float64)
