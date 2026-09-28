"""Explicit registry for pure mathematical kernel implementations."""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass

KernelFn = Callable[..., float]


@dataclass(frozen=True, slots=True)
class KernelImplementation:
    name: str
    version: str
    function: KernelFn
    reference: bool = False


@dataclass(frozen=True, slots=True)
class KernelSpec:
    maths_ledger_id: str
    name: str
    input_contract: str
    output_contract: str
    tolerance_abs: float
    tolerance_rel: float
    implementations: tuple[KernelImplementation, ...]
    enabled: bool = True

    def implementation(self, name: str) -> KernelImplementation:
        for implementation in self.implementations:
            if implementation.name == name:
                return implementation
        raise KeyError(f"unknown implementation {name!r} for {self.maths_ledger_id}")


class KernelRegistry:
    """Small typed registry; registering math never touches execution or SIG code."""

    def __init__(self) -> None:
        self._specs: dict[str, KernelSpec] = {}

    def register(self, spec: KernelSpec) -> None:
        if spec.maths_ledger_id in self._specs:
            raise ValueError(f"kernel already registered: {spec.maths_ledger_id}")
        if not spec.implementations:
            raise ValueError("kernel requires at least one implementation")
        if sum(1 for item in spec.implementations if item.reference) != 1:
            raise ValueError("kernel requires exactly one reference implementation")
        self._specs[spec.maths_ledger_id] = spec

    def spec(self, maths_ledger_id: str) -> KernelSpec:
        return self._specs[maths_ledger_id]

    def run(self, maths_ledger_id: str, implementation: str, *args: float) -> float:
        spec = self.spec(maths_ledger_id)
        if not spec.enabled:
            raise RuntimeError(f"kernel disabled: {maths_ledger_id}")
        return spec.implementation(implementation).function(*args)

    def specs(self) -> Mapping[str, KernelSpec]:
        return self._specs


def identity(value: float) -> float:
    return value


def logit(probability: float) -> float:
    if probability <= 0.0 or probability >= 1.0:
        raise ValueError("logit requires 0 < p < 1")
    return math.log(probability / (1.0 - probability))


def logit_log1p(probability: float) -> float:
    if probability <= 0.0 or probability >= 1.0:
        raise ValueError("logit requires 0 < p < 1")
    return math.log(probability) - math.log1p(-probability)


def inverse_logit(log_odds: float) -> float:
    if log_odds >= 0.0:
        exponent = math.exp(-log_odds)
        return 1.0 / (1.0 + exponent)
    exponent = math.exp(log_odds)
    return exponent / (1.0 + exponent)


def binary_cara_reservation_exact(probability: float, gamma: float, inventory: float) -> float:
    """M-041: sigma(logit(p) - gamma*q)."""
    return inverse_logit(logit(probability) - gamma * inventory)


def binary_cara_reservation_naive(
    probability: float, gamma: float, inventory: float
) -> float:
    value = logit(probability) - gamma * inventory
    return 1.0 / (1.0 + math.exp(-value))


def binary_cara_reservation_first_order(
    probability: float, gamma: float, inventory: float
) -> float:
    """M-042: p - gamma*q*p*(1-p), clipped only to probability support."""
    approximation = probability - gamma * inventory * probability * (1.0 - probability)
    return min(1.0, max(0.0, approximation))


def default_kernel_registry() -> KernelRegistry:
    registry = KernelRegistry()
    registry.register(
        KernelSpec(
            maths_ledger_id="IDENTITY",
            name="Identity",
            input_contract="float",
            output_contract="same float",
            tolerance_abs=0.0,
            tolerance_rel=0.0,
            implementations=(
                KernelImplementation("python", "1", identity, reference=True),
            ),
        )
    )
    registry.register(
        KernelSpec(
            maths_ledger_id="M-038",
            name="Log odds",
            input_contract="0 < p < 1",
            output_contract="real log odds",
            tolerance_abs=1e-15,
            tolerance_rel=1e-15,
            implementations=(
                KernelImplementation("ratio", "1", logit, reference=True),
                KernelImplementation("log1p", "1", logit_log1p),
            ),
        )
    )
    registry.register(
        KernelSpec(
            maths_ledger_id="M-041",
            name="Binary CARA reservation probability",
            input_contract="p, gamma, inventory",
            output_contract="probability",
            tolerance_abs=5e-3,
            tolerance_rel=5e-3,
            implementations=(
                KernelImplementation(
                    "stable",
                    "1",
                    binary_cara_reservation_exact,
                    reference=True,
                ),
                KernelImplementation(
                    "naive",
                    "1",
                    binary_cara_reservation_naive,
                ),
            ),
        )
    )
    registry.register(
        KernelSpec(
            maths_ledger_id="M-042",
            name="Binary CARA first-order reservation approximation",
            input_contract="p, gamma, inventory",
            output_contract="probability approximation",
            tolerance_abs=0.0,
            tolerance_rel=0.0,
            implementations=(
                KernelImplementation(
                    "python",
                    "1",
                    binary_cara_reservation_first_order,
                    reference=True,
                ),
            ),
        )
    )
    return registry
