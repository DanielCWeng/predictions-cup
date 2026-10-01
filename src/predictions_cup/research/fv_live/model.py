"""Frozen fair-value components for the FV-LIVE-001 offline replay.

This module has no production wiring. The OFI linear predictor is the frozen
005I challenger; this replay calibrates only its score-to-price conversion on
pre-live data. Inventory skew is deliberately limited to a declared grid.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import exp, isfinite, log

OFI_FEATURES = (
    "mid",
    "relative_spread",
    "depth_total5",
    "quote_events",
    "rv_5m",
    "imbalance1",
    "ret_1m",
    "ret_5m",
    "ofi_depth_norm",
)
OFI_MEAN = (
    0.49926132703457154,
    0.6093899214720409,
    1255.7557112181355,
    46.28957943495199,
    0.00651951604757511,
    0.0050633144068354,
    0.00007383662075523766,
    0.00015296481487162234,
    0.0003953494884767887,
)
OFI_SCALE = (
    0.3614213501807598,
    0.6563555961826829,
    21735.340691848338,
    134.0623967783279,
    0.014815558240286596,
    0.5153045000805079,
    0.01709391729548788,
    0.02461392090531114,
    0.09439555541035766,
)
OFI_COEF = (
    0.09164168425054639,
    -0.052679168227685993,
    0.01419651726981733,
    -0.0049317690810563326,
    0.006798088756926945,
    0.4438542392351359,
    -0.230839809333758,
    -0.27025740362144657,
    -0.2380956402390129,
)
OFI_INTERCEPT = 0.003167796092707855
INVENTORY_SKEW_GRID_TICKS_PER_500 = (0.0, 0.5, 1.0, 2.0)
TICK = 0.01


@dataclass(frozen=True)
class FVParameters:
    """Pre-live calibration constants; never fit on the live TEST window."""

    reversal_beta: float
    ofi_price_beta: float
    hazard_pull_threshold: float


def frozen_ofi_probability(features: tuple[float, ...]) -> float:
    """Apply frozen 005I OFI challenger coefficients to the supplied vector."""
    if len(features) != len(OFI_FEATURES):
        raise ValueError(f"expected {len(OFI_FEATURES)} features, got {len(features)}")
    z = OFI_INTERCEPT
    for value, mean, scale, coef in zip(features, OFI_MEAN, OFI_SCALE, OFI_COEF, strict=True):
        if not isfinite(value):
            raise ValueError("OFI features must be finite")
        z += ((value - mean) / scale) * coef
    if z >= 0:
        return 1.0 / (1.0 + exp(-z))
    exp_z = exp(z)
    return exp_z / (1.0 + exp_z)


def fair_value(
    pm_mid: float,
    ret_5m: float,
    ofi_probability: float,
    parameters: FVParameters,
    *,
    use_reversal: bool = True,
    use_ofi: bool = True,
) -> float:
    """Return PM mid plus independently toggled, pre-live calibrated layers."""
    value = pm_mid
    if use_reversal:
        value += parameters.reversal_beta * ret_5m
    if use_ofi:
        value += parameters.ofi_price_beta * (ofi_probability - 0.5)
    return min(1.0, max(0.0, value))


def quote_center(fv: float, inventory_lots: float, k_ticks_per_500: float) -> float:
    """Skew the center by -k ticks per 500 lots of signed inventory."""
    if k_ticks_per_500 not in INVENTORY_SKEW_GRID_TICKS_PER_500:
        raise ValueError("inventory skew must use the predeclared grid")
    return min(1.0, max(0.0, fv - k_ticks_per_500 * TICK * inventory_lots / 500.0))


def binary_log_loss(probability: float, label: int) -> float:
    """Stable binary log loss, used for the frozen OFI score diagnostic."""
    p = min(1.0 - 1e-15, max(1e-15, probability))
    return -(label * log(p) + (1 - label) * log(1 - p))
