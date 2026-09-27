"""Dependency-light evaluation summaries for valid executable opportunities."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from decimal import ROUND_CEILING, Decimal

from predictions_cup.learning.experiments import ExperimentObservation


@dataclass(frozen=True, slots=True)
class EvaluationSummary:
    count: int
    mean_executable_markout: Decimal | None
    median_executable_markout: Decimal | None
    hit_rate: Decimal | None
    total_gross_markout: Decimal
    total_net_markout: Decimal | None
    p25: Decimal | None
    p75: Decimal | None


def summarize(observations: tuple[ExperimentObservation, ...]) -> EvaluationSummary:
    valid = [
        observation
        for observation in observations
        if observation.valid and observation.gross_markout is not None
    ]
    if not valid:
        return EvaluationSummary(
            count=0,
            mean_executable_markout=None,
            median_executable_markout=None,
            hit_rate=None,
            total_gross_markout=Decimal("0"),
            total_net_markout=None,
            p25=None,
            p75=None,
        )
    gross = sorted(
        observation.gross_markout
        for observation in valid
        if observation.gross_markout is not None
    )
    count = len(gross)
    total = sum(gross, Decimal("0"))
    hits = sum(1 for value in gross if value > 0)
    net_values = [
        observation.net_markout
        for observation in valid
        if observation.net_markout is not None
    ]
    return EvaluationSummary(
        count=count,
        mean_executable_markout=total / Decimal(count),
        median_executable_markout=_median(gross),
        hit_rate=Decimal(hits) / Decimal(count),
        total_gross_markout=total,
        total_net_markout=(
            sum(net_values, Decimal("0")) if len(net_values) == count else None
        ),
        p25=_nearest_rank(gross, Decimal("0.25")),
        p75=_nearest_rank(gross, Decimal("0.75")),
    )


def summarize_by_market_direction_horizon(
    observations: tuple[ExperimentObservation, ...],
) -> dict[tuple[str, str, str], EvaluationSummary]:
    groups: dict[tuple[str, str, str], list[ExperimentObservation]] = {}
    for observation in observations:
        direction = "NONE" if observation.direction is None else observation.direction.value
        horizon = _timedelta_text(observation.target_horizon)
        key = (observation.instrument, direction, horizon)
        groups.setdefault(key, []).append(observation)
    return {
        key: summarize(tuple(group))
        for key, group in sorted(groups.items(), key=lambda item: item[0])
    }


def _median(values: list[Decimal]) -> Decimal:
    midpoint = len(values) // 2
    if len(values) % 2:
        return values[midpoint]
    return (values[midpoint - 1] + values[midpoint]) / Decimal(2)


def _nearest_rank(values: list[Decimal], quantile: Decimal) -> Decimal:
    raw = (Decimal(len(values)) * quantile).to_integral_value(rounding=ROUND_CEILING)
    index = max(0, int(raw) - 1)
    return values[index]


def _timedelta_text(value: timedelta) -> str:
    whole_seconds = value.days * 86_400 + value.seconds
    exact = Decimal(whole_seconds) + Decimal(value.microseconds) / Decimal(1_000_000)
    return str(exact)
