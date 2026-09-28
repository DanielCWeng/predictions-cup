"""Pure strategy and mathematical-kernel contracts for BUILD-009."""

from predictions_cup.strategy.core import (
    CandidateLeg,
    NoTrade,
    Opportunity,
    StrategyFamily,
    StrategyRegistry,
    StrategyResult,
)
from predictions_cup.strategy.kernels import (
    KernelImplementation,
    KernelRegistry,
    KernelSpec,
    default_kernel_registry,
)

__all__ = [
    "CandidateLeg",
    "KernelImplementation",
    "KernelRegistry",
    "KernelSpec",
    "NoTrade",
    "Opportunity",
    "StrategyFamily",
    "StrategyRegistry",
    "StrategyResult",
    "default_kernel_registry",
]
