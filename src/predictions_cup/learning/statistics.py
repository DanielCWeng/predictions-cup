"""Dependency-light multiple-testing and dependence-aware bootstrap primitives."""

from __future__ import annotations

import hashlib
import random
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from decimal import Decimal

from predictions_cup.learning.research_spec import canonical_json_bytes


@dataclass(frozen=True, slots=True)
class HypothesisTest:
    hypothesis_id: str
    family_id: str
    p_value: Decimal


@dataclass(frozen=True, slots=True)
class FDRResult:
    hypothesis_id: str
    family_id: str
    raw_p_value: Decimal
    rank: int
    threshold: Decimal
    q_value: Decimal
    rejected: bool
    family_size: int


def benjamini_hochberg(
    tests: Iterable[HypothesisTest], alpha: Decimal
) -> tuple[FDRResult, ...]:
    if not (Decimal("0") < alpha <= Decimal("1")):
        raise ValueError("FDR alpha must be in (0, 1]")
    by_family: dict[str, list[HypothesisTest]] = {}
    for test in tests:
        if not test.hypothesis_id or not test.family_id:
            raise ValueError("hypothesis and family IDs must be non-blank")
        if not (Decimal("0") <= test.p_value <= Decimal("1")):
            raise ValueError("p-values must lie in [0, 1]")
        by_family.setdefault(test.family_id, []).append(test)
    output: list[FDRResult] = []
    for family, family_tests in sorted(by_family.items()):
        ordered = sorted(family_tests, key=lambda item: (item.p_value, item.hypothesis_id))
        m = len(ordered)
        cutoff = 0
        raw_q: list[Decimal] = []
        for rank, test in enumerate(ordered, 1):
            threshold = alpha * Decimal(rank) / Decimal(m)
            if test.p_value <= threshold:
                cutoff = rank
            raw_q.append(min(Decimal("1"), test.p_value * Decimal(m) / Decimal(rank)))
        q_values = raw_q[:]
        for index in range(m - 2, -1, -1):
            q_values[index] = min(q_values[index], q_values[index + 1])
        for rank, (test, q_value) in enumerate(zip(ordered, q_values, strict=True), 1):
            output.append(
                FDRResult(
                    hypothesis_id=test.hypothesis_id,
                    family_id=family,
                    raw_p_value=test.p_value,
                    rank=rank,
                    threshold=alpha * Decimal(rank) / Decimal(m),
                    q_value=q_value,
                    rejected=rank <= cutoff,
                    family_size=m,
                )
            )
    return tuple(sorted(output, key=lambda item: (item.family_id, item.hypothesis_id)))


@dataclass(frozen=True, slots=True)
class BootstrapResult:
    method: str
    units: int
    draws: int
    statistic: str
    point_estimate: Decimal
    lower: Decimal
    upper: Decimal
    seed: int


def derived_seed(run_id: str, component_id: str) -> int:
    payload = canonical_json_bytes({"run_id": run_id, "component": component_id})
    digest = hashlib.sha256(payload).digest()
    return int.from_bytes(digest[:8], "big")


def moving_block_bootstrap_mean(
    values: Sequence[Decimal],
    *,
    block_size: int,
    draws: int,
    run_id: str,
    component_id: str,
    seed: int | None = None,
) -> BootstrapResult:
    if not values or block_size < 1 or draws < 1:
        raise ValueError("bootstrap requires values, positive block_size and positive draws")
    actual_seed = derived_seed(run_id, component_id) if seed is None else seed
    rng = random.Random(actual_seed)
    n = len(values)
    starts = list(range(max(1, n - block_size + 1)))
    samples: list[Decimal] = []
    for _ in range(draws):
        sample: list[Decimal] = []
        while len(sample) < n:
            start = rng.choice(starts)
            sample.extend(values[start : min(n, start + block_size)])
        sample = sample[:n]
        samples.append(sum(sample, Decimal("0")) / Decimal(n))
    samples.sort()
    return BootstrapResult(
        method="moving_block",
        units=len(starts),
        draws=draws,
        statistic="mean",
        point_estimate=sum(values, Decimal("0")) / Decimal(n),
        lower=_quantile(samples, Decimal("0.025")),
        upper=_quantile(samples, Decimal("0.975")),
        seed=actual_seed,
    )


def event_bootstrap_mean(
    event_values: dict[str, Sequence[Decimal]],
    *,
    draws: int,
    run_id: str,
    component_id: str,
    seed: int | None = None,
) -> BootstrapResult:
    if not event_values or draws < 1 or any(not values for values in event_values.values()):
        raise ValueError("event bootstrap requires non-empty event units and positive draws")
    actual_seed = derived_seed(run_id, component_id) if seed is None else seed
    rng = random.Random(actual_seed)
    ids = sorted(event_values)
    all_values = [value for event_id in ids for value in event_values[event_id]]
    samples: list[Decimal] = []
    for _ in range(draws):
        picked = [rng.choice(ids) for _ in ids]
        values = [value for event_id in picked for value in event_values[event_id]]
        samples.append(sum(values, Decimal("0")) / Decimal(len(values)))
    samples.sort()
    return BootstrapResult(
        method="event",
        units=len(ids),
        draws=draws,
        statistic="mean",
        point_estimate=sum(all_values, Decimal("0")) / Decimal(len(all_values)),
        lower=_quantile(samples, Decimal("0.025")),
        upper=_quantile(samples, Decimal("0.975")),
        seed=actual_seed,
    )


def _quantile(values: Sequence[Decimal], q: Decimal) -> Decimal:
    index = int((Decimal(len(values) - 1) * q).to_integral_value())
    return values[max(0, min(len(values) - 1, index))]
