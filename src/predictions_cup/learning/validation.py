"""As-of-safe common observations and deterministic validation splits."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum

from predictions_cup.learning.experiments import ExperimentObservation
from predictions_cup.learning.relationships import RelationshipObservation
from predictions_cup.replay.splits import ChronologicalBoundaries


class FoldRole(StrEnum):
    TRAIN = "TRAIN"
    DEVELOPMENT = "DEVELOPMENT"
    HOLDOUT = "HOLDOUT"


@dataclass(frozen=True, slots=True)
class EvaluationObservation:
    experiment_id: str
    hypothesis_family: str
    run_id: str
    decision_time: datetime
    feature_available_at: datetime
    label_end_time: datetime
    market_id: str | None
    instrument_id: str
    event_id: str | None
    event_family_id: str | None
    horizon: timedelta
    signal: str | None
    predictive_result: Decimal | None
    gross_executable_markout: Decimal | None
    net_executable_markout: Decimal | None
    valid: bool
    invalid_reason: str | None
    underlying: object

    def __post_init__(self) -> None:
        for name, value in (
            ("decision_time", self.decision_time),
            ("feature_available_at", self.feature_available_at),
            ("label_end_time", self.label_end_time),
        ):
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError(f"{name} must be timezone-aware")
        if self.horizon <= timedelta(0):
            raise ValueError("horizon must be positive")

    def assert_asof_safe(self) -> None:
        if self.feature_available_at > self.decision_time:
            raise ValueError(
                f"future feature rejected: available {self.feature_available_at.isoformat()} "
                f"after decision {self.decision_time.isoformat()}"
            )


def adapt_experiment_observation(
    row: ExperimentObservation,
    *,
    hypothesis_family: str,
    run_id: str,
    feature_available_at: datetime | None = None,
    event_id: str | None = None,
    event_family_id: str | None = None,
    predictive_result: Decimal | None = None,
) -> EvaluationObservation:
    result = EvaluationObservation(
        experiment_id=row.experiment_id,
        hypothesis_family=hypothesis_family,
        run_id=run_id,
        decision_time=row.decision_at,
        feature_available_at=feature_available_at or row.decision_at,
        label_end_time=row.decision_at + row.target_horizon,
        market_id=row.market_id,
        instrument_id=row.instrument,
        event_id=event_id,
        event_family_id=event_family_id,
        horizon=row.target_horizon,
        signal=row.signal,
        predictive_result=predictive_result,
        gross_executable_markout=row.gross_markout,
        net_executable_markout=row.net_markout,
        valid=row.valid,
        invalid_reason=None if row.invalid_reason is None else row.invalid_reason.value,
        underlying=row,
    )
    result.assert_asof_safe()
    return result


def adapt_relationship_observation(
    row: RelationshipObservation,
    *,
    hypothesis_family: str,
    run_id: str,
    feature_available_at: datetime | None = None,
    event_id: str | None = None,
    event_family_id: str | None = None,
    predictive_result: Decimal | None = None,
) -> EvaluationObservation:
    result = EvaluationObservation(
        experiment_id=row.experiment_id,
        hypothesis_family=hypothesis_family,
        run_id=run_id,
        decision_time=row.decision_time,
        feature_available_at=feature_available_at or row.decision_time,
        label_end_time=row.decision_time + row.horizon,
        market_id=None,
        instrument_id=row.target,
        event_id=event_id,
        event_family_id=event_family_id,
        horizon=row.horizon,
        signal=None if row.signal_direction is None else row.signal_direction.value,
        predictive_result=predictive_result,
        gross_executable_markout=row.gross_markout,
        net_executable_markout=row.net_markout,
        valid=row.valid,
        invalid_reason=None if row.invalid_reason is None else row.invalid_reason.value,
        underlying=row,
    )
    result.assert_asof_safe()
    return result


@dataclass(frozen=True, slots=True)
class FoldAssignment:
    observation: EvaluationObservation
    role: FoldRole


@dataclass(frozen=True, slots=True)
class PurgeEvidence:
    rows_before: int
    rows_removed_by_purge: int
    rows_removed_by_embargo: int
    rows_remaining: int


def chronological_split(
    rows: Iterable[EvaluationObservation], boundaries: ChronologicalBoundaries
) -> tuple[FoldAssignment, ...]:
    out: list[FoldAssignment] = []
    ordered = sorted(
        rows,
        key=lambda item: (item.decision_time, item.instrument_id, item.horizon),
    )
    for row in ordered:
        row.assert_asof_safe()
        if row.decision_time < boundaries.train_end:
            role = FoldRole.TRAIN
        elif row.decision_time < boundaries.development_end:
            role = FoldRole.DEVELOPMENT
        else:
            role = FoldRole.HOLDOUT
        out.append(FoldAssignment(row, role))
    return tuple(out)


def purge_training(
    assignments: Iterable[FoldAssignment],
    evaluation_start: datetime,
    *,
    embargo: timedelta = timedelta(0),
) -> tuple[tuple[FoldAssignment, ...], PurgeEvidence]:
    if evaluation_start.tzinfo is None or evaluation_start.utcoffset() is None:
        raise ValueError("evaluation_start must be timezone-aware")
    if embargo < timedelta(0):
        raise ValueError("embargo must not be negative")
    assignments = tuple(assignments)
    kept: list[FoldAssignment] = []
    purged = embargoed = 0
    embargo_start = evaluation_start - embargo
    for item in assignments:
        if item.role is not FoldRole.TRAIN:
            kept.append(item)
            continue
        row = item.observation
        if row.label_end_time > evaluation_start:
            purged += 1
            continue
        if embargo and row.decision_time >= embargo_start:
            embargoed += 1
            continue
        kept.append(item)
    evidence = PurgeEvidence(len(assignments), purged, embargoed, len(kept))
    return tuple(kept), evidence


def chronological_group_holdout(
    rows: Iterable[EvaluationObservation],
    *,
    holdout_id: str,
    level: str,
) -> tuple[FoldAssignment, ...]:
    if level not in {"event", "family"}:
        raise ValueError("level must be 'event' or 'family'")
    rows = tuple(rows)
    getter = (lambda row: row.event_id) if level == "event" else (lambda row: row.event_family_id)
    held = [row for row in rows if getter(row) == holdout_id]
    if not held:
        raise ValueError(f"no rows for held-out {level} {holdout_id}")
    first_holdout = min(row.decision_time for row in held)
    out: list[FoldAssignment] = []
    for row in sorted(rows, key=lambda item: item.decision_time):
        group_id = getter(row)
        if group_id == holdout_id:
            out.append(FoldAssignment(row, FoldRole.HOLDOUT))
        elif row.decision_time < first_holdout:
            out.append(FoldAssignment(row, FoldRole.TRAIN))
    return tuple(out)


def leave_group_out_diagnostic(
    rows: Iterable[EvaluationObservation],
    *,
    holdout_id: str,
    level: str,
) -> tuple[FoldAssignment, ...]:
    if level not in {"event", "family"}:
        raise ValueError("level must be 'event' or 'family'")
    getter = (lambda row: row.event_id) if level == "event" else (lambda row: row.event_family_id)
    return tuple(
        FoldAssignment(row, FoldRole.HOLDOUT if getter(row) == holdout_id else FoldRole.TRAIN)
        for row in sorted(rows, key=lambda item: item.decision_time)
    )
