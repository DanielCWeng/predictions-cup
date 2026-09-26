"""As-of-safe common observations and deterministic validation splits."""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum

from predictions_cup.learning.experiments import ExperimentObservation
from predictions_cup.learning.relationships import RelationshipObservation
from predictions_cup.learning.research_spec import canonical_json_bytes
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
        if self.label_end_time != self.decision_time + self.horizon:
            raise ValueError("label_end_time must equal decision_time + horizon")

    @property
    def observation_id(self) -> str:
        payload = {
            "experiment_id": self.experiment_id,
            "run_id": self.run_id,
            "decision_time": self.decision_time.isoformat(),
            "feature_available_at": self.feature_available_at.isoformat(),
            "label_end_time": self.label_end_time.isoformat(),
            "market_id": self.market_id,
            "instrument_id": self.instrument_id,
            "event_id": self.event_id,
            "event_family_id": self.event_family_id,
            "horizon_seconds": str(self.horizon.total_seconds()),
            "signal": self.signal,
            "predictive_result": self.predictive_result,
            "gross_executable_markout": self.gross_executable_markout,
            "net_executable_markout": self.net_executable_markout,
            "valid": self.valid,
            "invalid_reason": self.invalid_reason,
        }
        return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()

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
    market_id: str | None = None,
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
        market_id=market_id,
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
        key=lambda item: (
            item.decision_time,
            item.instrument_id,
            item.horizon,
            item.observation_id,
        ),
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


def purge_roles(
    assignments: Iterable[FoldAssignment],
    evaluation_start: datetime,
    *,
    roles: tuple[FoldRole, ...],
    embargo: timedelta = timedelta(0),
) -> tuple[tuple[FoldAssignment, ...], PurgeEvidence]:
    if evaluation_start.tzinfo is None or evaluation_start.utcoffset() is None:
        raise ValueError("evaluation_start must be timezone-aware")
    if embargo < timedelta(0):
        raise ValueError("embargo must not be negative")
    if not roles:
        raise ValueError("at least one fold role must be supplied for purge")
    materialized = tuple(assignments)
    kept: list[FoldAssignment] = []
    purged = embargoed = 0
    embargo_start = evaluation_start - embargo
    for item in materialized:
        if item.role not in roles:
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
    evidence = PurgeEvidence(len(materialized), purged, embargoed, len(kept))
    return tuple(kept), evidence


def purge_training(
    assignments: Iterable[FoldAssignment],
    evaluation_start: datetime,
    *,
    embargo: timedelta = timedelta(0),
) -> tuple[tuple[FoldAssignment, ...], PurgeEvidence]:
    return purge_roles(
        assignments,
        evaluation_start,
        roles=(FoldRole.TRAIN,),
        embargo=embargo,
    )


def purge_development(
    assignments: Iterable[FoldAssignment],
    holdout_start: datetime,
    *,
    embargo: timedelta = timedelta(0),
) -> tuple[tuple[FoldAssignment, ...], PurgeEvidence]:
    return purge_roles(
        assignments,
        holdout_start,
        roles=(FoldRole.DEVELOPMENT,),
        embargo=embargo,
    )


@dataclass(frozen=True, slots=True)
class WalkForwardConfig:
    training_window: timedelta
    development_window: timedelta
    holdout_window: timedelta
    step: timedelta
    expanding_training: bool = False
    embargo: timedelta = timedelta(0)

    def __post_init__(self) -> None:
        windows = (
            self.training_window,
            self.development_window,
            self.holdout_window,
            self.step,
        )
        if any(value <= timedelta(0) for value in windows):
            raise ValueError("walk-forward windows and step must be positive")
        if self.embargo < timedelta(0):
            raise ValueError("walk-forward embargo must not be negative")


@dataclass(frozen=True, slots=True)
class WalkForwardFold:
    fold_id: str
    train_start: datetime
    train_end: datetime
    development_end: datetime
    holdout_end: datetime
    assignments: tuple[FoldAssignment, ...]
    training_purge_evidence: PurgeEvidence
    development_purge_evidence: PurgeEvidence


def walk_forward_folds(
    rows: Iterable[EvaluationObservation],
    config: WalkForwardConfig,
) -> tuple[WalkForwardFold, ...]:
    ordered = tuple(sorted(rows, key=lambda row: (row.decision_time, row.observation_id)))
    if not ordered:
        return ()
    first = ordered[0].decision_time
    last = ordered[-1].decision_time
    anchor = first + config.training_window
    output: list[WalkForwardFold] = []
    fold_number = 1
    while True:
        train_end = anchor
        development_end = train_end + config.development_window
        holdout_end = development_end + config.holdout_window
        if development_end > last:
            break
        train_start = first if config.expanding_training else train_end - config.training_window
        fold_rows = tuple(
            row for row in ordered if train_start <= row.decision_time < holdout_end
        )
        assignments = chronological_split(
            fold_rows,
            ChronologicalBoundaries(train_end, development_end),
        )
        assignments, train_evidence = purge_training(
            assignments,
            train_end,
            embargo=config.embargo,
        )
        assignments, development_evidence = purge_development(
            assignments,
            development_end,
            embargo=config.embargo,
        )
        output.append(
            WalkForwardFold(
                fold_id=f"fold-{fold_number:03d}",
                train_start=train_start,
                train_end=train_end,
                development_end=development_end,
                holdout_end=holdout_end,
                assignments=assignments,
                training_purge_evidence=train_evidence,
                development_purge_evidence=development_evidence,
            )
        )
        fold_number += 1
        anchor += config.step
        if anchor > last:
            break
    return tuple(output)


@dataclass(frozen=True, slots=True)
class GroupHoldoutResult:
    assignments: tuple[FoldAssignment, ...]
    purge_evidence: PurgeEvidence
    holdout_start: datetime


def chronological_group_holdout_result(
    rows: Iterable[EvaluationObservation],
    *,
    holdout_id: str,
    level: str,
    embargo: timedelta = timedelta(0),
) -> GroupHoldoutResult:
    if level not in {"event", "family"}:
        raise ValueError("level must be 'event' or 'family'")
    materialized = tuple(rows)
    held = [row for row in materialized if _group_id(row, level) == holdout_id]
    if not held:
        raise ValueError(f"no rows for held-out {level} {holdout_id}")
    first_holdout = min(row.decision_time for row in held)
    assignments: list[FoldAssignment] = []
    for row in sorted(materialized, key=lambda item: (item.decision_time, item.observation_id)):
        group_id = _group_id(row, level)
        if group_id == holdout_id:
            assignments.append(FoldAssignment(row, FoldRole.HOLDOUT))
        elif row.decision_time < first_holdout:
            assignments.append(FoldAssignment(row, FoldRole.TRAIN))
    purged, evidence = purge_training(
        assignments,
        first_holdout,
        embargo=embargo,
    )
    return GroupHoldoutResult(purged, evidence, first_holdout)


def chronological_group_holdout(
    rows: Iterable[EvaluationObservation],
    *,
    holdout_id: str,
    level: str,
    embargo: timedelta = timedelta(0),
) -> tuple[FoldAssignment, ...]:
    return chronological_group_holdout_result(
        rows,
        holdout_id=holdout_id,
        level=level,
        embargo=embargo,
    ).assignments


def leave_group_out_diagnostic(
    rows: Iterable[EvaluationObservation],
    *,
    holdout_id: str,
    level: str,
) -> tuple[FoldAssignment, ...]:
    if level not in {"event", "family"}:
        raise ValueError("level must be 'event' or 'family'")
    return tuple(
        FoldAssignment(
            row,
            FoldRole.HOLDOUT if _group_id(row, level) == holdout_id else FoldRole.TRAIN,
        )
        for row in sorted(rows, key=lambda item: (item.decision_time, item.observation_id))
    )


def _group_id(row: EvaluationObservation, level: str) -> str | None:
    return row.event_id if level == "event" else row.event_family_id
