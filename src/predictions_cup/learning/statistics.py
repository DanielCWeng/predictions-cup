"""Dependency-light multiple-testing and dependence-aware bootstrap primitives."""

from __future__ import annotations

import hashlib
import random
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal

from predictions_cup.learning.research_spec import (
    EventBootstrapWeighting,
    FDRProtocol,
    canonical_json_bytes,
)


@dataclass(frozen=True, slots=True)
class HypothesisTest:
    hypothesis_id: str
    family_id: str
    p_value: Decimal
    test_name: str
    null_hypothesis: str
    test_statistic: str
    dependence_assumption: str

    def __post_init__(self) -> None:
        required = (
            self.hypothesis_id,
            self.family_id,
            self.test_name,
            self.null_hypothesis,
            self.test_statistic,
            self.dependence_assumption,
        )
        if any(not value for value in required):
            raise ValueError("statistical-test metadata must be non-blank")
        if not (Decimal("0") <= self.p_value <= Decimal("1")):
            raise ValueError("p-values must lie in [0, 1]")


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
    tests: Iterable[HypothesisTest],
    protocol: FDRProtocol,
) -> tuple[FDRResult, ...]:
    materialized = tuple(tests)
    if len({test.hypothesis_id for test in materialized}) != len(materialized):
        raise ValueError("duplicate hypothesis IDs are not allowed in an FDR family")
    supplied = {test.hypothesis_id for test in materialized}
    declared = set(protocol.hypothesis_ids)
    if supplied != declared:
        raise ValueError("supplied hypothesis IDs must equal the predeclared FDR search space")
    if any(test.family_id != protocol.family_id for test in materialized):
        raise ValueError("statistical-test FDR family must match the declared protocol")

    ordered = sorted(materialized, key=lambda item: (item.p_value, item.hypothesis_id))
    m = len(ordered)
    cutoff = 0
    raw_q: list[Decimal] = []
    for rank, test in enumerate(ordered, 1):
        threshold = protocol.alpha * Decimal(rank) / Decimal(m)
        if test.p_value <= threshold:
            cutoff = rank
        raw_q.append(min(Decimal("1"), test.p_value * Decimal(m) / Decimal(rank)))
    q_values = raw_q[:]
    for index in range(m - 2, -1, -1):
        q_values[index] = min(q_values[index], q_values[index + 1])

    output = tuple(
        FDRResult(
            hypothesis_id=test.hypothesis_id,
            family_id=protocol.family_id,
            raw_p_value=test.p_value,
            rank=rank,
            threshold=protocol.alpha * Decimal(rank) / Decimal(m),
            q_value=q_value,
            rejected=rank <= cutoff,
            family_size=m,
        )
        for rank, (test, q_value) in enumerate(zip(ordered, q_values, strict=True), 1)
    )
    return tuple(sorted(output, key=lambda item: item.hypothesis_id))


@dataclass(frozen=True, slots=True)
class BootstrapResult:
    method: str
    weighting: str | None
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
        weighting=None,
        units=len(starts),
        draws=draws,
        statistic="mean",
        point_estimate=sum(values, Decimal("0")) / Decimal(n),
        lower=_quantile(samples, Decimal("0.025")),
        upper=_quantile(samples, Decimal("0.975")),
        seed=actual_seed,
    )


def event_bootstrap_mean(
    event_values: Mapping[str, Sequence[Decimal]],
    *,
    draws: int,
    run_id: str,
    component_id: str,
    weighting: EventBootstrapWeighting,
    seed: int | None = None,
) -> BootstrapResult:
    if not event_values or draws < 1 or any(not values for values in event_values.values()):
        raise ValueError("event bootstrap requires non-empty event units and positive draws")
    actual_seed = derived_seed(run_id, component_id) if seed is None else seed
    rng = random.Random(actual_seed)
    ids = sorted(event_values)
    event_means = {
        event_id: sum(values, Decimal("0")) / Decimal(len(values))
        for event_id, values in event_values.items()
    }
    all_values = [value for event_id in ids for value in event_values[event_id]]
    samples: list[Decimal] = []
    for _ in range(draws):
        picked = [rng.choice(ids) for _ in ids]
        if weighting is EventBootstrapWeighting.OBSERVATION_WEIGHTED_CLUSTER:
            sampled_values = [
                value for event_id in picked for value in event_values[event_id]
            ]
            estimate = sum(sampled_values, Decimal("0")) / Decimal(len(sampled_values))
        else:
            sampled_means = [event_means[event_id] for event_id in picked]
            estimate = sum(sampled_means, Decimal("0")) / Decimal(len(sampled_means))
        samples.append(estimate)
    samples.sort()

    if weighting is EventBootstrapWeighting.OBSERVATION_WEIGHTED_CLUSTER:
        point_estimate = sum(all_values, Decimal("0")) / Decimal(len(all_values))
        method = "event_cluster"
    else:
        point_estimate = sum(event_means.values(), Decimal("0")) / Decimal(len(event_means))
        method = "event_equal_weight"

    return BootstrapResult(
        method=method,
        weighting=weighting.value,
        units=len(ids),
        draws=draws,
        statistic="mean",
        point_estimate=point_estimate,
        lower=_quantile(samples, Decimal("0.025")),
        upper=_quantile(samples, Decimal("0.975")),
        seed=actual_seed,
    )


def _quantile(values: Sequence[Decimal], q: Decimal) -> Decimal:
    index = int((Decimal(len(values) - 1) * q).to_integral_value())
    return values[max(0, min(len(values) - 1, index))]
