"""Frozen mathematical helpers for EXPERIMENT-004C-B.

This module contains no empirical family discovery. Scientific definitions live in the
immutable 004C-B registry/preregistration; these helpers only implement their declared math.
"""
from __future__ import annotations

from collections import defaultdict

import numpy as np


def project_simplex(values: np.ndarray, total: float = 1.0) -> np.ndarray:
    """Unweighted Euclidean projection onto {x>=0, sum(x)=total}."""
    v = np.asarray(values, dtype=float)
    if v.ndim != 1 or not np.all(np.isfinite(v)):
        raise ValueError("simplex projection requires a finite 1-D vector")
    if total <= 0:
        raise ValueError("total must be positive")
    u = np.sort(v)[::-1]
    cssv = np.cumsum(u) - total
    idx = np.arange(1, len(v) + 1)
    keep = u - cssv / idx > 0
    if not np.any(keep):
        return np.full_like(v, total / len(v))
    rho = np.flatnonzero(keep)[-1]
    theta = cssv[rho] / (rho + 1.0)
    return np.maximum(v - theta, 0.0)


def project_capped_simplex(values: np.ndarray, total: float = 1.0) -> np.ndarray:
    """Projection onto {0<=x<=1, sum(x)<=total}; market inputs already lie in [0,1]."""
    v = np.asarray(values, dtype=float)
    if v.ndim != 1 or not np.all(np.isfinite(v)):
        raise ValueError("capped-simplex projection requires a finite 1-D vector")
    if np.any(v < 0) or np.any(v > 1):
        raise ValueError("probability vector must lie in [0,1]")
    return v.copy() if float(v.sum()) <= total + 1e-15 else project_simplex(v, total)


def project_nonincreasing(values: np.ndarray) -> np.ndarray:
    """Unweighted L2 isotonic projection enforcing x[0]>=x[1]>=... in [0,1]."""
    v = np.asarray(values, dtype=float)
    if v.ndim != 1 or not np.all(np.isfinite(v)):
        raise ValueError("isotonic projection requires a finite 1-D vector")
    y = -np.clip(v, 0.0, 1.0)
    means: list[float] = []
    weights: list[int] = []
    for val in y:
        means.append(float(val))
        weights.append(1)
        while len(means) >= 2 and means[-2] > means[-1]:
            w = weights[-2] + weights[-1]
            m = (means[-2] * weights[-2] + means[-1] * weights[-1]) / w
            means[-2:] = [m]
            weights[-2:] = [w]
    out = np.empty(len(v), dtype=float)
    pos = 0
    for m, w in zip(means, weights, strict=True):
        out[pos : pos + w] = -m
        pos += w
    return np.clip(out, 0.0, 1.0)


def structural_residual(values: np.ndarray, relation_type: str) -> np.ndarray:
    if relation_type == "EXHAUSTIVE_PARTITION":
        projected = project_simplex(values)
    elif relation_type == "MUTUALLY_EXCLUSIVE_NONEXHAUSTIVE":
        projected = project_capped_simplex(values)
    elif relation_type == "CONDITIONAL_STAGED":
        projected = project_nonincreasing(values)
    else:
        raise ValueError(f"unsupported hard relation type: {relation_type}")
    return np.asarray(values, dtype=float) - projected


def bh_adjust(rows: list[dict], family_key: str = "fdr_family") -> list[dict]:
    """BH with unavailable preregistered cells retained in the planned family size."""
    out = [dict(row) for row in rows]
    grouped: dict[str, list[int]] = defaultdict(list)
    for i, row in enumerate(out):
        grouped[str(row[family_key])].append(i)
    for _, indices in grouped.items():
        m_total = len(indices)
        available = [
            (i, float(out[i]["p_value"]))
            for i in indices
            if out[i].get("p_value") is not None
        ]
        available.sort(key=lambda pair: (pair[1], str(out[pair[0]].get("hypothesis_id", ""))))
        running = 1.0
        adjusted = [1.0] * len(available)
        for rank0 in range(len(available) - 1, -1, -1):
            _, p = available[rank0]
            running = min(running, p * m_total / (rank0 + 1))
            adjusted[rank0] = running
        for (idx, _), q in zip(available, adjusted, strict=True):
            out[idx]["q_value"] = min(1.0, float(q))
            out[idx]["fdr_family_size"] = m_total
        for idx in indices:
            out[idx].setdefault("q_value", None)
            out[idx].setdefault("fdr_family_size", m_total)
    return out
