"""Chronological experiment split helpers; never randomize time-series rows."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from predictions_cup.learning.experiments import ExperimentObservation
from predictions_cup.replay.splits import ChronologicalBoundaries


@dataclass(frozen=True, slots=True)
class ChronologicalSplit:
    train: tuple[ExperimentObservation, ...]
    development: tuple[ExperimentObservation, ...]
    holdout: tuple[ExperimentObservation, ...]


def split_observations(
    observations: tuple[ExperimentObservation, ...],
    boundaries: ChronologicalBoundaries,
) -> ChronologicalSplit:
    ordered = sorted(observations, key=lambda observation: observation.decision_at)
    train: list[ExperimentObservation] = []
    development: list[ExperimentObservation] = []
    holdout: list[ExperimentObservation] = []
    for observation in ordered:
        _require_aware(observation.decision_at)
        if observation.decision_at < boundaries.train_end:
            train.append(observation)
        elif observation.decision_at < boundaries.development_end:
            development.append(observation)
        else:
            holdout.append(observation)
    return ChronologicalSplit(
        train=tuple(train),
        development=tuple(development),
        holdout=tuple(holdout),
    )


def _require_aware(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("decision_at must be timezone-aware")
