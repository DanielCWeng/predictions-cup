"""Event-time scientific-validation controls for election prediction-market research."""

from __future__ import annotations

import csv
import hashlib
import json
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from statistics import median
from typing import Any

from predictions_cup.learning.research_spec import canonical_json_bytes
from predictions_cup.learning.validation import (
    EvaluationObservation,
    FoldAssignment,
    FoldRole,
    GroupHoldoutResult,
    leave_group_out_diagnostic,
    purge_training,
)

EXPECTED_004A_PACKAGE_VERSION = "004A-event-time-v2"
EXPECTED_004A_REGIME_SHA256 = (
    "57a102d64778be7c1460638bb4da63be7eeafe0b9494a4287339674bcad0a741"
)
CONDITION_UNIVERSE_VERSION = "004A2-condition-universe-v1"
VALIDATION_PROTOCOL_VERSION = "004A2-event-time-validation-v1"
LOW_FAMILY_COUNT_FLAG = "LOW_INDEPENDENT_FAMILY_COUNT"


class ClaimRegime(StrEnum):
    PRE_ELECTION = "PRE_ELECTION"
    ELECTION_DAY_PRE_RESULTS = "ELECTION_DAY_PRE_RESULTS"
    ACTIVE_RESULTS = "ACTIVE_RESULTS"
    LATE_COUNT_DIAGNOSTIC = "LATE_COUNT_DIAGNOSTIC"


class ClaimScope(StrEnum):
    WITHIN_EVENT = "WITHIN_EVENT"
    CROSS_ROUND_SAME_FAMILY = "CROSS_ROUND_SAME_FAMILY"
    CROSS_EVENT_FAMILY = "CROSS_EVENT_FAMILY"


class ValidationMethod(StrEnum):
    WITHIN_EVENT_TEMPORAL_OOS = "WITHIN_EVENT_TEMPORAL_OOS"
    FORWARD_EVENT_HOLDOUT = "FORWARD_EVENT_HOLDOUT"
    FORWARD_FAMILY_HOLDOUT = "FORWARD_FAMILY_HOLDOUT"
    CROSS_ROUND_SAME_FAMILY = "CROSS_ROUND_SAME_FAMILY"
    LEAVE_FAMILY_OUT_RETROSPECTIVE_DIAGNOSTIC = (
        "LEAVE_FAMILY_OUT_RETROSPECTIVE_DIAGNOSTIC"
    )


class ValidationEvidenceScope(StrEnum):
    WITHIN_EVENT_SUPPORTED = "WITHIN_EVENT_SUPPORTED"
    SAME_FAMILY_FORWARD_SUPPORTED = "SAME_FAMILY_FORWARD_SUPPORTED"
    CROSS_FAMILY_FORWARD_SUPPORTED = "CROSS_FAMILY_FORWARD_SUPPORTED"
    RETROSPECTIVE_ONLY = "RETROSPECTIVE_ONLY"


@dataclass(frozen=True, slots=True)
class EventTimeWindow:
    regime_id: str
    event_id: str
    event_family: str
    claim_regime: ClaimRegime
    source_regime_name: str
    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        if self.start.tzinfo is None or self.end.tzinfo is None:
            raise ValueError("event-time windows must be timezone-aware")
        if self.start >= self.end:
            raise ValueError("event-time windows must have positive duration")


@dataclass(frozen=True, slots=True)
class ConditionEligibility:
    regime_id: str
    event_family: str
    condition_id: str
    market_id: str | None
    market_family: str | None
    question: str
    canonical_token_id: str | None
    canonical_outcome: str | None
    counterpart_token_id: str | None
    pre_election_usable: bool
    election_day_pre_results_usable: bool
    active_results_usable: bool
    late_count_usable: bool
    canonical_token_classification: str | None
    counterpart_token_classification: str | None
    token_pair_disagreement: bool
    condition_classification: str
    reason_codes: tuple[str, ...]

    def as_csv_row(self) -> dict[str, str]:
        return {
            "regime_id": self.regime_id,
            "event_family": self.event_family,
            "condition_id": self.condition_id,
            "market_id": self.market_id or "",
            "market_family": self.market_family or "",
            "question": self.question,
            "canonical_token_id": self.canonical_token_id or "",
            "canonical_outcome": self.canonical_outcome or "",
            "counterpart_token_id": self.counterpart_token_id or "",
            "pre_election_usable": _bool_text(self.pre_election_usable),
            "election_day_pre_results_usable": _bool_text(
                self.election_day_pre_results_usable
            ),
            "active_results_usable": _bool_text(self.active_results_usable),
            "late_count_usable": _bool_text(self.late_count_usable),
            "canonical_token_classification": self.canonical_token_classification or "",
            "counterpart_token_classification": self.counterpart_token_classification or "",
            "token_pair_disagreement": _bool_text(self.token_pair_disagreement),
            "condition_classification": self.condition_classification,
            "reason_codes": ";".join(self.reason_codes),
        }


@dataclass(frozen=True, slots=True)
class ValidationFold:
    claim_regime: ClaimRegime
    claim_scope: ClaimScope
    validation_method: ValidationMethod
    evidence_scope: ValidationEvidenceScope
    fold_id: str
    train_events: tuple[str, ...]
    development_events: tuple[str, ...]
    holdout_event: str | None
    holdout_family: str | None
    holdout_window_start: datetime | None
    holdout_window_end: datetime | None
    chronological: bool
    independent_family_holdout: bool
    same_family_training_present: bool
    chronology_status: str
    data_eligibility_status: str
    eligible_train_conditions: int
    eligible_holdout_conditions: int
    status: str
    reason: str

    def as_csv_row(self) -> dict[str, str]:
        return {
            "claim_regime": self.claim_regime.value,
            "claim_scope": self.claim_scope.value,
            "validation_method": self.validation_method.value,
            "evidence_scope": self.evidence_scope.value,
            "fold_id": self.fold_id,
            "train_events": ";".join(self.train_events),
            "development_events": ";".join(self.development_events),
            "holdout_event": self.holdout_event or "",
            "holdout_family": self.holdout_family or "",
            "holdout_window_start": _iso(self.holdout_window_start),
            "holdout_window_end": _iso(self.holdout_window_end),
            "chronological": _bool_text(self.chronological),
            "independent_family_holdout": _bool_text(self.independent_family_holdout),
            "same_family_training_present": _bool_text(
                self.same_family_training_present
            ),
            "chronology_status": self.chronology_status,
            "data_eligibility_status": self.data_eligibility_status,
            "eligible_train_conditions": str(self.eligible_train_conditions),
            "eligible_holdout_conditions": str(self.eligible_holdout_conditions),
            "status": self.status,
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class RegimeAssignment:
    observation: EvaluationObservation
    window: EventTimeWindow | None
    valid: bool
    reason_code: str | None


@dataclass(frozen=True, slots=True)
class Frozen004APackage:
    package_version: str
    regime_package_sha256: str
    windows: tuple[EventTimeWindow, ...]
    timeline: Mapping[str, Any]
    regime_definitions: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class RegimeEvidenceObservation:
    observation: EvaluationObservation
    condition_id: str


@dataclass(frozen=True, slots=True)
class RegimeWindowEvidenceSummary:
    event_id: str
    event_family: str
    claim_regime: ClaimRegime
    effect_estimate: Decimal | None
    observation_count: int
    condition_count: int
    time_coverage_seconds: Decimal
    invalid_count: int
    block_level_uncertainty: str | None
    direction: str


@dataclass(frozen=True, slots=True)
class FamilyEvidenceSummary:
    family_effects: tuple[tuple[str, Decimal], ...]
    eligible_independent_families: int
    same_effect_direction: int
    equal_family_mean: Decimal | None
    equal_family_median: Decimal | None
    minimum: Decimal | None
    maximum: Decimal | None
    effect_range: Decimal | None
    flags: tuple[str, ...]


def canonical_yes_index(outcomes: Sequence[str]) -> int | None:
    matches = [index for index, outcome in enumerate(outcomes) if outcome == "Yes"]
    return matches[0] if len(matches) == 1 else None


def _bool_text(value: bool) -> str:
    return "true" if value else "false"


def _parse_bool(value: str) -> bool:
    if value not in {"true", "false"}:
        raise ValueError(f"expected lowercase boolean, got {value!r}")
    return value == "true"


def _dt(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"timestamp must be timezone-aware: {value}")
    return parsed


def _iso(value: datetime | None) -> str:
    if value is None:
        return ""
    return value.isoformat().replace("+00:00", "Z")


def _regime_package_sha(timeline_path: Path, definitions_path: Path) -> str:
    digest = hashlib.sha256()
    for path in (timeline_path, definitions_path):
        digest.update(path.read_bytes())
        digest.update(b"\n")
    return digest.hexdigest()


def _claim_for_source_regime(name: str) -> ClaimRegime | None:
    if name == "LATE_COUNT":
        return ClaimRegime.LATE_COUNT_DIAGNOSTIC
    try:
        return ClaimRegime(name)
    except ValueError:
        return None


def load_frozen_004a(package_dir: Path) -> Frozen004APackage:
    timeline_path = package_dir / "event_timeline.json"
    definitions_path = package_dir / "regime_definitions.json"
    timeline = json.loads(timeline_path.read_text(encoding="utf-8"))
    definitions = json.loads(definitions_path.read_text(encoding="utf-8"))
    versions = {timeline["package_version"], definitions["package_version"]}
    if versions != {EXPECTED_004A_PACKAGE_VERSION}:
        raise ValueError(f"unexpected 004A package versions: {sorted(versions)}")
    digest = _regime_package_sha(timeline_path, definitions_path)
    if digest != EXPECTED_004A_REGIME_SHA256:
        raise ValueError(f"004A regime package hash mismatch: {digest}")

    event_ids = {event["regime_id"]: event["event_id"] for event in timeline["events"]}
    windows: list[EventTimeWindow] = []
    for event in definitions["events"]:
        regime_id = str(event["regime_id"])
        event_family = str(event["event_family"])
        for raw in event["regimes"]:
            if not raw.get("enabled", True):
                continue
            claim = _claim_for_source_regime(str(raw["name"]))
            if claim is None:
                continue
            windows.append(
                EventTimeWindow(
                    regime_id=regime_id,
                    event_id=str(event_ids[regime_id]),
                    event_family=event_family,
                    claim_regime=claim,
                    source_regime_name=str(raw["name"]),
                    start=_dt(str(raw["start_utc"])),
                    end=_dt(str(raw["end_utc"])),
                )
            )
    return Frozen004APackage(
        package_version=EXPECTED_004A_PACKAGE_VERSION,
        regime_package_sha256=digest,
        windows=tuple(
            sorted(windows, key=lambda item: (item.start, item.regime_id, item.claim_regime))
        ),
        timeline=timeline,
        regime_definitions=definitions,
    )


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def project_condition_universe(
    package_dir: Path,
    identity_path: Path,
) -> tuple[ConditionEligibility, ...]:
    usability = _read_csv(package_dir / "usability_matrix.csv")
    market_coverage = _read_csv(package_dir / "market_coverage.csv")
    window_coverage = _read_csv(package_dir / "event_window_coverage.csv")
    identity = _read_csv(identity_path)

    if len(usability) != 2600 or len(identity) != 2600 or len(market_coverage) != 2600:
        raise ValueError("accepted 004A/DATA-001 token universe must contain exactly 2,600 rows")

    identity_by_token = {
        (row["regime_id"], row["token_id"]): row
        for row in identity
    }
    market_by_token = {
        (row["regime_id"], row["token_id"]): row
        for row in market_coverage
    }
    usable_by_window = {
        (row["coverage_id"], row["regime"]): _parse_bool(row["usable"])
        for row in window_coverage
    }
    grouped: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
    for row in usability:
        grouped[(row["regime_id"], row["condition_id"])].append(row)
    if len(grouped) != 1300:
        raise ValueError(f"expected 1,300 event-scoped binary conditions, got {len(grouped)}")

    output: list[ConditionEligibility] = []
    for (regime_id, condition_id), pair in sorted(grouped.items()):
        if len(pair) != 2:
            raise ValueError(
                f"{regime_id}/{condition_id} must have exactly two outcome tokens, got {len(pair)}"
            )
        identities = [identity_by_token[(regime_id, row["token_id"])] for row in pair]
        if any(item["condition_id"] != condition_id for item in identities):
            raise ValueError(f"identity condition mismatch for {regime_id}/{condition_id}")
        pair_disagreement = pair[0]["classification"] != pair[1]["classification"]
        exact_yes_indexes = [
            index for index, item in enumerate(identities) if item["outcome"] == "Yes"
        ]
        yes_index = canonical_yes_index([item["outcome"] for item in identities])
        if yes_index is None:
            output.append(
                ConditionEligibility(
                    regime_id=regime_id,
                    event_family=pair[0]["event_family"],
                    condition_id=condition_id,
                    market_id=None,
                    market_family=None,
                    question=pair[0]["market/question"],
                    canonical_token_id=None,
                    canonical_outcome=None,
                    counterpart_token_id=None,
                    pre_election_usable=False,
                    election_day_pre_results_usable=False,
                    active_results_usable=False,
                    late_count_usable=False,
                    canonical_token_classification=None,
                    counterpart_token_classification=None,
                    token_pair_disagreement=pair_disagreement,
                    condition_classification="FAILED_CLOSED",
                    reason_codes=(f"CANONICAL_YES_TOKEN_COUNT_{len(exact_yes_indexes)}",),
                )
            )
            continue

        other_index = 1 - yes_index
        canonical = pair[yes_index]
        counterpart = pair[other_index]
        canonical_identity = identities[yes_index]
        market_row = market_by_token[(regime_id, canonical["token_id"])]
        coverage_id = market_row["coverage_id"]
        reasons = tuple(
            part
            for field in (canonical["reason_codes"], canonical["late_count_reason_codes"])
            for part in field.split(";")
            if part
        )
        output.append(
            ConditionEligibility(
                regime_id=regime_id,
                event_family=canonical["event_family"],
                condition_id=condition_id,
                market_id=canonical_identity["market_id"] or None,
                market_family=canonical_identity["market_family"] or None,
                question=canonical_identity["question"],
                canonical_token_id=canonical["token_id"],
                canonical_outcome=canonical_identity["outcome"],
                counterpart_token_id=counterpart["token_id"],
                pre_election_usable=usable_by_window[(coverage_id, "PRE_ELECTION")],
                election_day_pre_results_usable=usable_by_window[
                    (coverage_id, "ELECTION_DAY_PRE_RESULTS")
                ],
                active_results_usable=usable_by_window[(coverage_id, "ACTIVE_RESULTS")],
                late_count_usable=usable_by_window[(coverage_id, "LATE_COUNT")],
                canonical_token_classification=canonical["classification"],
                counterpart_token_classification=counterpart["classification"],
                token_pair_disagreement=pair_disagreement,
                condition_classification=canonical["classification"],
                reason_codes=reasons,
            )
        )
    return tuple(output)


def condition_universe_sha256(rows: Sequence[ConditionEligibility]) -> str:
    payload = [row.as_csv_row() for row in rows]
    return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


def assign_observation_to_regime(
    observation: EvaluationObservation,
    windows: Sequence[EventTimeWindow],
    claim_regime: ClaimRegime,
) -> RegimeAssignment:
    observation.assert_asof_safe()
    candidates = [
        window
        for window in windows
        if window.claim_regime is claim_regime
        and observation.event_id in {window.event_id, window.regime_id}
    ]
    if len(candidates) != 1:
        return RegimeAssignment(observation, None, False, "NO_EVENT_TIME_WINDOW")
    window = candidates[0]
    if not (window.start <= observation.decision_time < window.end):
        return RegimeAssignment(
            observation, window, False, "DECISION_OUTSIDE_CLAIMED_REGIME"
        )
    if observation.label_end_time >= window.end:
        return RegimeAssignment(observation, window, False, "LABEL_CROSSES_REGIME_END")
    return RegimeAssignment(observation, window, True, None)


def _matches_event(window: EventTimeWindow, event_identifier: str) -> bool:
    return event_identifier in {window.event_id, window.regime_id}


def _resolve_event_window(
    windows: Sequence[EventTimeWindow],
    claim_regime: ClaimRegime,
    event_identifier: str,
) -> EventTimeWindow:
    matched = [
        window
        for window in windows
        if window.claim_regime is claim_regime and _matches_event(window, event_identifier)
    ]
    if len(matched) != 1:
        raise ValueError(
            f"expected exactly one frozen window for {event_identifier}/{claim_regime.value}"
        )
    return matched[0]


def _valid_regime_rows(
    rows: Iterable[EvaluationObservation],
    windows: Sequence[EventTimeWindow],
    claim_regime: ClaimRegime,
) -> tuple[EvaluationObservation, ...]:
    assigned = (assign_observation_to_regime(row, windows, claim_regime) for row in rows)
    return tuple(item.observation for item in assigned if item.valid)


def construct_within_event_temporal_fold(
    rows: Iterable[EvaluationObservation],
    *,
    windows: Sequence[EventTimeWindow],
    event_id: str,
    claim_regime: ClaimRegime,
    holdout_start: datetime,
    embargo: timedelta,
) -> GroupHoldoutResult:
    event_window = _resolve_event_window(windows, claim_regime, event_id)
    event_aliases = {event_window.event_id, event_window.regime_id}
    valid = tuple(
        row
        for row in _valid_regime_rows(rows, windows, claim_regime)
        if row.event_id in event_aliases
    )
    if not valid:
        raise ValueError(f"no valid rows for {event_id}/{claim_regime.value}")
    assignments = tuple(
        FoldAssignment(
            row,
            FoldRole.TRAIN if row.decision_time < holdout_start else FoldRole.HOLDOUT,
        )
        for row in sorted(valid, key=lambda item: (item.decision_time, item.observation_id))
    )
    if not any(item.role is FoldRole.HOLDOUT for item in assignments):
        raise ValueError("within-event temporal fold requires later holdout observations")
    kept, evidence = purge_training(assignments, holdout_start, embargo=embargo)
    return GroupHoldoutResult(kept, evidence, holdout_start)


def construct_forward_event_holdout(
    rows: Iterable[EvaluationObservation],
    *,
    windows: Sequence[EventTimeWindow],
    holdout_event_id: str,
    claim_regime: ClaimRegime,
    embargo: timedelta,
) -> GroupHoldoutResult:
    held_window = _resolve_event_window(windows, claim_regime, holdout_event_id)
    held_aliases = {held_window.event_id, held_window.regime_id}
    boundary = held_window.start
    valid = _valid_regime_rows(rows, windows, claim_regime)
    assignments = tuple(
        FoldAssignment(
            row,
            FoldRole.HOLDOUT if row.event_id in held_aliases else FoldRole.TRAIN,
        )
        for row in sorted(valid, key=lambda item: (item.decision_time, item.observation_id))
        if row.event_id in held_aliases or row.decision_time < boundary
    )
    if not any(item.role is FoldRole.HOLDOUT for item in assignments):
        raise ValueError(f"no held-out rows for event {holdout_event_id}")
    kept, evidence = purge_training(assignments, boundary, embargo=embargo)
    return GroupHoldoutResult(kept, evidence, boundary)


def construct_forward_family_holdout(
    rows: Iterable[EvaluationObservation],
    *,
    windows: Sequence[EventTimeWindow],
    holdout_family: str,
    claim_regime: ClaimRegime,
    embargo: timedelta,
) -> GroupHoldoutResult:
    held_windows = [
        window
        for window in windows
        if window.claim_regime is claim_regime and window.event_family == holdout_family
    ]
    if not held_windows:
        raise ValueError(f"no frozen held-out windows for family {holdout_family}")
    boundary = min(window.start for window in held_windows)
    valid = _valid_regime_rows(rows, windows, claim_regime)
    assignments = tuple(
        FoldAssignment(
            row,
            FoldRole.HOLDOUT if row.event_family_id == holdout_family else FoldRole.TRAIN,
        )
        for row in sorted(valid, key=lambda item: (item.decision_time, item.observation_id))
        if row.event_family_id == holdout_family or row.decision_time < boundary
    )
    if not any(item.role is FoldRole.HOLDOUT for item in assignments):
        raise ValueError(f"no held-out rows for family {holdout_family}")
    kept, evidence = purge_training(assignments, boundary, embargo=embargo)
    return GroupHoldoutResult(kept, evidence, boundary)


def construct_same_family_transfer_fold(
    rows: Iterable[EvaluationObservation],
    *,
    windows: Sequence[EventTimeWindow],
    train_event_id: str,
    holdout_event_id: str,
    claim_regime: ClaimRegime,
    embargo: timedelta,
) -> GroupHoldoutResult:
    train_window = _resolve_event_window(windows, claim_regime, train_event_id)
    holdout_window = _resolve_event_window(windows, claim_regime, holdout_event_id)
    if train_window == holdout_window:
        raise ValueError("same-family transfer requires two distinct frozen event windows")
    if train_window.event_family != holdout_window.event_family:
        raise ValueError("same-family transfer events must share an event family")
    if train_window.start >= holdout_window.start:
        raise ValueError("same-family transfer must run from earlier to later event")

    train_aliases = {train_window.event_id, train_window.regime_id}
    holdout_aliases = {holdout_window.event_id, holdout_window.regime_id}
    valid = _valid_regime_rows(rows, windows, claim_regime)
    assignments = tuple(
        FoldAssignment(
            row,
            FoldRole.HOLDOUT if row.event_id in holdout_aliases else FoldRole.TRAIN,
        )
        for row in sorted(valid, key=lambda item: (item.decision_time, item.observation_id))
        if row.event_id in train_aliases | holdout_aliases
        and (row.event_id in holdout_aliases or row.decision_time < holdout_window.start)
    )
    kept, evidence = purge_training(
        assignments, holdout_window.start, embargo=embargo
    )
    return GroupHoldoutResult(kept, evidence, holdout_window.start)


def construct_leave_family_out_diagnostic(
    rows: Iterable[EvaluationObservation],
    *,
    windows: Sequence[EventTimeWindow],
    holdout_family: str,
    claim_regime: ClaimRegime,
) -> tuple[FoldAssignment, ...]:
    valid = _valid_regime_rows(rows, windows, claim_regime)
    return leave_group_out_diagnostic(valid, holdout_id=holdout_family, level="family")


def _windows_by_claim(
    windows: Sequence[EventTimeWindow],
) -> dict[ClaimRegime, list[EventTimeWindow]]:
    grouped: dict[ClaimRegime, list[EventTimeWindow]] = defaultdict(list)
    for window in windows:
        grouped[window.claim_regime].append(window)
    for values in grouped.values():
        values.sort(key=lambda item: (item.start, item.regime_id))
    return grouped


def _condition_usable_for_claim(
    row: ConditionEligibility, claim_regime: ClaimRegime
) -> bool:
    if row.canonical_outcome != "Yes":
        return False
    if claim_regime is ClaimRegime.PRE_ELECTION:
        return row.pre_election_usable
    if claim_regime is ClaimRegime.ELECTION_DAY_PRE_RESULTS:
        return row.election_day_pre_results_usable
    if claim_regime is ClaimRegime.ACTIVE_RESULTS:
        return row.active_results_usable
    if claim_regime is ClaimRegime.LATE_COUNT_DIAGNOSTIC:
        return row.late_count_usable
    raise AssertionError(f"unhandled claim regime: {claim_regime}")


def _eligible_condition_count(
    conditions: Sequence[ConditionEligibility],
    *,
    event_ids: Iterable[str],
    claim_regime: ClaimRegime,
) -> int:
    events = set(event_ids)
    return sum(
        row.regime_id in events and _condition_usable_for_claim(row, claim_regime)
        for row in conditions
    )


def _data_eligibility_status(
    *, eligible_train_conditions: int, eligible_holdout_conditions: int
) -> str:
    reasons: list[str] = []
    if eligible_train_conditions == 0:
        reasons.append("NO_ELIGIBLE_TRAIN_CONDITIONS")
    if eligible_holdout_conditions == 0:
        reasons.append("NO_ELIGIBLE_HOLDOUT_CONDITIONS")
    return ";".join(reasons) if reasons else "DATA_ELIGIBLE"


def _fold_status(*, chronology_status: str, data_eligibility_status: str) -> str:
    if chronology_status != "CHRONOLOGY_OK":
        return chronology_status
    if data_eligibility_status == "DATA_ELIGIBLE":
        return "FEASIBLE"
    reasons = set(data_eligibility_status.split(";"))
    if reasons == {
        "NO_ELIGIBLE_TRAIN_CONDITIONS",
        "NO_ELIGIBLE_HOLDOUT_CONDITIONS",
    }:
        return "NO_ELIGIBLE_TRAIN_OR_HOLDOUT_CONDITIONS"
    return data_eligibility_status


def _reason_with_data(base_reason: str, data_eligibility_status: str) -> str:
    if data_eligibility_status == "DATA_ELIGIBLE":
        return base_reason
    return f"{base_reason} Data eligibility: {data_eligibility_status}."


def build_fold_inventory(
    windows: Sequence[EventTimeWindow],
    conditions: Sequence[ConditionEligibility],
) -> tuple[ValidationFold, ...]:
    output: list[ValidationFold] = []
    grouped = _windows_by_claim(windows)
    same_family_pairs = (
        ("peru_first_round", "peru_runoff"),
        ("colombia_first_round", "colombia_runoff"),
    )
    for claim in ClaimRegime:
        claim_windows = grouped.get(claim, [])
        by_regime = {window.regime_id: window for window in claim_windows}

        for window in claim_windows:
            eligible = _eligible_condition_count(
                conditions, event_ids=(window.regime_id,), claim_regime=claim
            )
            data_status = _data_eligibility_status(
                eligible_train_conditions=eligible,
                eligible_holdout_conditions=eligible,
            )
            chronology_status = "CHRONOLOGY_OK"
            output.append(
                ValidationFold(
                    claim_regime=claim,
                    claim_scope=ClaimScope.WITHIN_EVENT,
                    validation_method=ValidationMethod.WITHIN_EVENT_TEMPORAL_OOS,
                    evidence_scope=ValidationEvidenceScope.WITHIN_EVENT_SUPPORTED,
                    fold_id=f"{claim.value}__within__{window.regime_id}",
                    train_events=(window.regime_id,),
                    development_events=(window.regime_id,),
                    holdout_event=window.regime_id,
                    holdout_family=window.event_family,
                    holdout_window_start=window.start,
                    holdout_window_end=window.end,
                    chronological=True,
                    independent_family_holdout=False,
                    same_family_training_present=False,
                    chronology_status=chronology_status,
                    data_eligibility_status=data_status,
                    eligible_train_conditions=eligible,
                    eligible_holdout_conditions=eligible,
                    status=_fold_status(
                        chronology_status=chronology_status,
                        data_eligibility_status=data_status,
                    ),
                    reason=_reason_with_data(
                        "Downstream preregistration must freeze the within-window temporal "
                        "split and explicit embargo before outcomes are inspected.",
                        data_status,
                    ),
                )
            )

        for index, holdout in enumerate(claim_windows):
            prior = [item for item in claim_windows[:index] if item.start < holdout.start]
            same_family = any(item.event_family == holdout.event_family for item in prior)
            scope = (
                ClaimScope.CROSS_ROUND_SAME_FAMILY
                if same_family
                else ClaimScope.CROSS_EVENT_FAMILY
            )
            evidence_scope = (
                ValidationEvidenceScope.SAME_FAMILY_FORWARD_SUPPORTED
                if same_family
                else ValidationEvidenceScope.CROSS_FAMILY_FORWARD_SUPPORTED
            )
            chronology_status = (
                "CHRONOLOGY_OK" if prior else "INSUFFICIENT_PRIOR_EVENTS"
            )
            train_events = tuple(item.regime_id for item in prior)
            eligible_train = _eligible_condition_count(
                conditions, event_ids=train_events, claim_regime=claim
            )
            eligible_holdout = _eligible_condition_count(
                conditions, event_ids=(holdout.regime_id,), claim_regime=claim
            )
            data_status = _data_eligibility_status(
                eligible_train_conditions=eligible_train,
                eligible_holdout_conditions=eligible_holdout,
            )
            base_reason = (
                "Only observations strictly before the held-out window start may train; "
                "purge and explicit embargo apply."
                if prior
                else "No earlier same-regime event observations exist."
            )
            output.append(
                ValidationFold(
                    claim_regime=claim,
                    claim_scope=scope,
                    validation_method=ValidationMethod.FORWARD_EVENT_HOLDOUT,
                    evidence_scope=evidence_scope,
                    fold_id=f"{claim.value}__forward_event__{holdout.regime_id}",
                    train_events=train_events,
                    development_events=(),
                    holdout_event=holdout.regime_id,
                    holdout_family=holdout.event_family,
                    holdout_window_start=holdout.start,
                    holdout_window_end=holdout.end,
                    chronological=True,
                    independent_family_holdout=not same_family and bool(prior),
                    same_family_training_present=same_family,
                    chronology_status=chronology_status,
                    data_eligibility_status=data_status,
                    eligible_train_conditions=eligible_train,
                    eligible_holdout_conditions=eligible_holdout,
                    status=_fold_status(
                        chronology_status=chronology_status,
                        data_eligibility_status=data_status,
                    ),
                    reason=_reason_with_data(base_reason, data_status),
                )
            )

        families = sorted(
            {window.event_family for window in claim_windows},
            key=lambda family: min(
                item.start for item in claim_windows if item.event_family == family
            ),
        )
        for family in families:
            family_windows = [
                item for item in claim_windows if item.event_family == family
            ]
            boundary = min(item.start for item in family_windows)
            prior = [
                item
                for item in claim_windows
                if item.event_family != family and item.start < boundary
            ]
            prior_families = {item.event_family for item in prior}
            chronology_status = (
                "CHRONOLOGY_OK"
                if prior_families
                else "INSUFFICIENT_PRIOR_FAMILIES"
            )
            train_events = tuple(item.regime_id for item in prior)
            holdout_events = tuple(item.regime_id for item in family_windows)
            eligible_train = _eligible_condition_count(
                conditions, event_ids=train_events, claim_regime=claim
            )
            eligible_holdout = _eligible_condition_count(
                conditions, event_ids=holdout_events, claim_regime=claim
            )
            data_status = _data_eligibility_status(
                eligible_train_conditions=eligible_train,
                eligible_holdout_conditions=eligible_holdout,
            )
            base_reason = (
                f"{len(prior_families)} prior independent family/families; "
                f"{LOW_FAMILY_COUNT_FLAG}."
                if prior_families
                else "No independent family has observations strictly before this family boundary."
            )
            output.append(
                ValidationFold(
                    claim_regime=claim,
                    claim_scope=ClaimScope.CROSS_EVENT_FAMILY,
                    validation_method=ValidationMethod.FORWARD_FAMILY_HOLDOUT,
                    evidence_scope=ValidationEvidenceScope.CROSS_FAMILY_FORWARD_SUPPORTED,
                    fold_id=f"{claim.value}__forward_family__{family}",
                    train_events=train_events,
                    development_events=(),
                    holdout_event=";".join(holdout_events),
                    holdout_family=family,
                    holdout_window_start=boundary,
                    holdout_window_end=max(item.end for item in family_windows),
                    chronological=True,
                    independent_family_holdout=True,
                    same_family_training_present=False,
                    chronology_status=chronology_status,
                    data_eligibility_status=data_status,
                    eligible_train_conditions=eligible_train,
                    eligible_holdout_conditions=eligible_holdout,
                    status=_fold_status(
                        chronology_status=chronology_status,
                        data_eligibility_status=data_status,
                    ),
                    reason=_reason_with_data(base_reason, data_status),
                )
            )

        for first_round, runoff in same_family_pairs:
            if first_round not in by_regime or runoff not in by_regime:
                continue
            first = by_regime[first_round]
            later = by_regime[runoff]
            chronology_status = (
                "CHRONOLOGY_OK"
                if first.start < later.start
                else "INFEASIBLE_CHRONOLOGY"
            )
            eligible_train = _eligible_condition_count(
                conditions, event_ids=(first_round,), claim_regime=claim
            )
            eligible_holdout = _eligible_condition_count(
                conditions, event_ids=(runoff,), claim_regime=claim
            )
            data_status = _data_eligibility_status(
                eligible_train_conditions=eligible_train,
                eligible_holdout_conditions=eligible_holdout,
            )
            output.append(
                ValidationFold(
                    claim_regime=claim,
                    claim_scope=ClaimScope.CROSS_ROUND_SAME_FAMILY,
                    validation_method=ValidationMethod.CROSS_ROUND_SAME_FAMILY,
                    evidence_scope=ValidationEvidenceScope.SAME_FAMILY_FORWARD_SUPPORTED,
                    fold_id=f"{claim.value}__same_family__{first_round}__to__{runoff}",
                    train_events=(first_round,),
                    development_events=(),
                    holdout_event=runoff,
                    holdout_family=later.event_family,
                    holdout_window_start=later.start,
                    holdout_window_end=later.end,
                    chronological=True,
                    independent_family_holdout=False,
                    same_family_training_present=True,
                    chronology_status=chronology_status,
                    data_eligibility_status=data_status,
                    eligible_train_conditions=eligible_train,
                    eligible_holdout_conditions=eligible_holdout,
                    status=_fold_status(
                        chronology_status=chronology_status,
                        data_eligibility_status=data_status,
                    ),
                    reason=_reason_with_data(
                        "Later round is genuinely unseen but is not an independent "
                        "election-family replication.",
                        data_status,
                    ),
                )
            )

        for family in families:
            held = [item for item in claim_windows if item.event_family == family]
            training = [item for item in claim_windows if item.event_family != family]
            train_events = tuple(item.regime_id for item in training)
            holdout_events = tuple(item.regime_id for item in held)
            eligible_train = _eligible_condition_count(
                conditions, event_ids=train_events, claim_regime=claim
            )
            eligible_holdout = _eligible_condition_count(
                conditions, event_ids=holdout_events, claim_regime=claim
            )
            data_status = _data_eligibility_status(
                eligible_train_conditions=eligible_train,
                eligible_holdout_conditions=eligible_holdout,
            )
            output.append(
                ValidationFold(
                    claim_regime=claim,
                    claim_scope=ClaimScope.CROSS_EVENT_FAMILY,
                    validation_method=(
                        ValidationMethod.LEAVE_FAMILY_OUT_RETROSPECTIVE_DIAGNOSTIC
                    ),
                    evidence_scope=ValidationEvidenceScope.RETROSPECTIVE_ONLY,
                    fold_id=f"{claim.value}__retrospective_lofo__{family}",
                    train_events=train_events,
                    development_events=(),
                    holdout_event=";".join(holdout_events),
                    holdout_family=family,
                    holdout_window_start=min(item.start for item in held),
                    holdout_window_end=max(item.end for item in held),
                    chronological=False,
                    independent_family_holdout=True,
                    same_family_training_present=False,
                    chronology_status="NON_TEMPORAL_DIAGNOSTIC",
                    data_eligibility_status=data_status,
                    eligible_train_conditions=eligible_train,
                    eligible_holdout_conditions=eligible_holdout,
                    status="DIAGNOSTIC_ONLY",
                    reason=_reason_with_data(
                        "NON_TEMPORAL_RETROSPECTIVE_DIAGNOSTIC", data_status
                    ),
                )
            )
    return tuple(sorted(output, key=lambda item: item.fold_id))

def make_validation_protocol(
    *,
    package: Frozen004APackage,
    condition_universe_sha: str,
) -> dict[str, Any]:
    return {
        "validation_protocol_version": VALIDATION_PROTOCOL_VERSION,
        "004a_regime_package_version": package.package_version,
        "004a_regime_package_sha256": package.regime_package_sha256,
        "condition_universe_version": CONDITION_UNIVERSE_VERSION,
        "condition_universe_sha256": condition_universe_sha,
        "canonical_token_rule": {
            "outcome_label": "Yes",
            "match": "exact_case_sensitive",
            "required_matches_per_condition": 1,
            "zero_or_multiple": "FAIL_CLOSED",
            "counterpart_may_not_promote_condition": True,
            "complements_are_not_independent": True,
        },
        "regime_membership_rule": (
            "regime_start <= decision_time < regime_end AND label_end_time < regime_end"
        ),
        "cross_boundary_label_rule": {
            "policy": "INVALID_FOR_CLAIMED_REGIME",
            "reason_code": "LABEL_CROSSES_REGIME_END",
            "half_open_intervals": True,
            "clip_or_reassign": False,
        },
        "as_of_rule": "feature_available_at <= decision_time",
        "label_horizon_rule": "label_end_time == decision_time + horizon",
        "learned_state_policy": {
            "fit_sources": ["TRAIN", "DEVELOPMENT"],
            "heldout_observations_may_influence_fit": False,
            "applies_to": [
                "normalization",
                "model_fitting",
                "participant_scoring",
                "PCA_SVD_FACTORS",
                "parameter_selection",
                "feature_selection",
                "relationship_discovery",
            ],
        },
        "claim_scopes": [value.value for value in ClaimScope],
        "claim_regimes": [value.value for value in ClaimRegime],
        "fold_data_eligibility_rule": {
            "source": "frozen canonical ConditionEligibility universe",
            "feasible_requires_nonempty_train": True,
            "feasible_requires_nonempty_holdout": True,
            "reason_codes": [
                "NO_ELIGIBLE_TRAIN_CONDITIONS",
                "NO_ELIGIBLE_HOLDOUT_CONDITIONS",
            ],
            "chronology_status_preserved_separately": True,
        },
        "fold_construction_rules": {
            "WITHIN_EVENT_TEMPORAL_OOS": "chronological same-event same-regime only",
            "FORWARD_EVENT_HOLDOUT": "whole held-out event; training strictly earlier",
            "FORWARD_FAMILY_HOLDOUT": "whole held-out family; training strictly earlier",
            "CROSS_ROUND_SAME_FAMILY": "first round to later runoff only",
            "LEAVE_FAMILY_OUT_RETROSPECTIVE_DIAGNOSTIC": (
                "all other families may train; never prospective evidence"
            ),
        },
        "purge_semantics": {
            "closed_label_rule": "label_end_time >= evaluation_start is purged",
            "train_to_development": True,
            "development_to_holdout": True,
            "event_holdouts": True,
            "family_holdouts": True,
            "regime_boundaries": True,
        },
        "embargo_semantics": {
            "must_be_explicit_in_downstream_research_spec": True,
            "must_be_non_negative": True,
            "must_be_hash_bound": True,
            "hidden_result_dependent_default": False,
        },
        "family_grouping": {
            "COL_2026": ["colombia_first_round", "colombia_runoff"],
            "PER_2026": ["peru_first_round", "peru_runoff"],
            "HUN_2026": ["hungary_election"],
        },
        "uncertainty_hierarchy": {
            "within_window": "contiguous temporal block",
            "cross_family": "family -> event/round -> contiguous temporal block",
            "independent_family_unit": "event_family",
            "small_n_flag": LOW_FAMILY_COUNT_FLAG,
        },
        "fdr_lane_policy": {
            "PRE_ELECTION": "separate_primary_family",
            "ACTIVE_RESULTS": "separate_primary_family",
            "ELECTION_DAY_PRE_RESULTS": "separate_if_preregistered",
            "LATE_COUNT_DIAGNOSTIC": "diagnostic_by_default",
            "pool_primary_regimes": False,
            "retain_unavailable_cells_in_search_space": True,
        },
        "evidence_scope_policy": {
            "WITHIN_EVENT_SUPPORTED": "within event/regime only",
            "SAME_FAMILY_FORWARD_SUPPORTED": "unseen later round; not independent family",
            "CROSS_FAMILY_FORWARD_SUPPORTED": "requires genuine forward family holdout",
            "RETROSPECTIVE_ONLY": "transportability diagnostic; not prospective OOS",
            "cross_family_without_holdout": "INCONCLUSIVE",
        },
    }


def validation_protocol_sha256(protocol: Mapping[str, Any]) -> str:
    payload = dict(protocol)
    payload.pop("validation_protocol_sha256", None)
    return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


def summarise_regime_window_evidence(
    rows: Iterable[RegimeEvidenceObservation],
    *,
    windows: Sequence[EventTimeWindow],
    event_id: str,
    claim_regime: ClaimRegime,
    block_level_uncertainty: str | None = None,
) -> RegimeWindowEvidenceSummary:
    relevant = tuple(item for item in rows if item.observation.event_id == event_id)
    if not relevant:
        raise ValueError(f"no evidence rows for event {event_id}")
    assignments = [
        assign_observation_to_regime(item.observation, windows, claim_regime)
        for item in relevant
    ]
    valid_indexes = [index for index, item in enumerate(assignments) if item.valid]
    valid = [relevant[index] for index in valid_indexes]
    effects = [
        item.observation.predictive_result
        for item in valid
        if item.observation.predictive_result is not None
    ]
    effect = sum(effects, Decimal(0)) / Decimal(len(effects)) if effects else None
    times = [item.observation.decision_time for item in valid]
    coverage = (
        Decimal(str((max(times) - min(times)).total_seconds()))
        if len(times) >= 2
        else Decimal(0)
    )
    if effect is None:
        direction = "UNAVAILABLE"
    elif effect > 0:
        direction = "POSITIVE"
    elif effect < 0:
        direction = "NEGATIVE"
    else:
        direction = "ZERO"
    family = next(
        window.event_family
        for window in windows
        if window.claim_regime is claim_regime
        and event_id in {window.event_id, window.regime_id}
    )
    return RegimeWindowEvidenceSummary(
        event_id=event_id,
        event_family=family,
        claim_regime=claim_regime,
        effect_estimate=effect,
        observation_count=len(valid),
        condition_count=len({item.condition_id for item in valid}),
        time_coverage_seconds=coverage,
        invalid_count=len(relevant) - len(valid),
        block_level_uncertainty=block_level_uncertainty,
        direction=direction,
    )


def summarise_event_family_evidence(
    windows: Iterable[RegimeWindowEvidenceSummary],
) -> FamilyEvidenceSummary:
    grouped: dict[str, list[Decimal]] = defaultdict(list)
    for window in windows:
        if window.effect_estimate is not None:
            grouped[window.event_family].append(window.effect_estimate)
    family_effects = tuple(
        (
            family,
            sum(values, Decimal(0)) / Decimal(len(values)),
        )
        for family, values in sorted(grouped.items())
        if values
    )
    values = [value for _, value in family_effects]
    if not values:
        return FamilyEvidenceSummary(
            family_effects=(),
            eligible_independent_families=0,
            same_effect_direction=0,
            equal_family_mean=None,
            equal_family_median=None,
            minimum=None,
            maximum=None,
            effect_range=None,
            flags=(),
        )
    positive = sum(value > 0 for value in values)
    negative = sum(value < 0 for value in values)
    same_direction = max(positive, negative)
    flags = (LOW_FAMILY_COUNT_FLAG,) if len(values) <= 3 else ()
    return FamilyEvidenceSummary(
        family_effects=family_effects,
        eligible_independent_families=len(values),
        same_effect_direction=same_direction,
        equal_family_mean=sum(values, Decimal(0)) / Decimal(len(values)),
        equal_family_median=median(values),
        minimum=min(values),
        maximum=max(values),
        effect_range=max(values) - min(values),
        flags=flags,
    )

