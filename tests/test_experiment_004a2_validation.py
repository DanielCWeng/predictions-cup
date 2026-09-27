"""Synthetic and accepted-evidence tests for EXPERIMENT-004A.2."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from predictions_cup.learning.event_time_validation import (
    CONDITION_UNIVERSE_VERSION,
    EXPECTED_004A_REGIME_SHA256,
    ClaimRegime,
    ConditionEligibility,
    EventTimeWindow,
    Frozen004APackage,
    RegimeWindowEvidenceSummary,
    ValidationEvidenceScope,
    ValidationMethod,
    assign_observation_to_regime,
    build_fold_inventory,
    canonical_yes_index,
    condition_universe_sha256,
    construct_forward_event_holdout,
    construct_forward_family_holdout,
    construct_leave_family_out_diagnostic,
    construct_same_family_transfer_fold,
    construct_within_event_temporal_fold,
    load_frozen_004a,
    make_validation_protocol,
    project_condition_universe,
    summarise_event_family_evidence,
    validation_protocol_sha256,
)
from predictions_cup.learning.validation import (
    EvaluationObservation,
    FoldRole,
    chronological_split,
    purge_development,
)
from predictions_cup.replay.splits import ChronologicalBoundaries

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "data" / "experiments" / "experiment_004a"
IDENTITY = ROOT / "data" / "manifests" / "historical" / "data_001_market_identity.csv"
BASE = datetime(2026, 1, 1, tzinfo=UTC)


@pytest.fixture(scope="module")
def frozen_package() -> Frozen004APackage:
    return load_frozen_004a(PACKAGE)


@pytest.fixture(scope="module")
def conditions() -> tuple[ConditionEligibility, ...]:
    return project_condition_universe(PACKAGE, IDENTITY)


def _window(
    event: str,
    family: str,
    claim: ClaimRegime,
    start_seconds: int,
    end_seconds: int,
) -> EventTimeWindow:
    return EventTimeWindow(
        regime_id=event,
        event_id=event,
        event_family=family,
        claim_regime=claim,
        source_regime_name=(
            "LATE_COUNT" if claim is ClaimRegime.LATE_COUNT_DIAGNOSTIC else claim.value
        ),
        start=BASE + timedelta(seconds=start_seconds),
        end=BASE + timedelta(seconds=end_seconds),
    )


def _obs(
    seconds: int,
    *,
    horizon: int,
    event: str = "A",
    family: str = "F1",
    feature_offset: int = 0,
    effect: str = "1",
) -> EvaluationObservation:
    decision = BASE + timedelta(seconds=seconds)
    return EvaluationObservation(
        experiment_id="synthetic",
        hypothesis_family="synthetic",
        run_id="run",
        decision_time=decision,
        feature_available_at=decision + timedelta(seconds=feature_offset),
        label_end_time=decision + timedelta(seconds=horizon),
        market_id=f"market-{event}",
        instrument_id=f"token-{event}-{seconds}",
        event_id=event,
        event_family_id=family,
        horizon=timedelta(seconds=horizon),
        signal="UP",
        predictive_result=Decimal(effect),
        gross_executable_markout=None,
        net_executable_markout=None,
        valid=True,
        invalid_reason=None,
        underlying=object(),
    )


def _summary(
    event: str,
    family: str,
    effect: str,
) -> RegimeWindowEvidenceSummary:
    return RegimeWindowEvidenceSummary(
        event_id=event,
        event_family=family,
        claim_regime=ClaimRegime.ACTIVE_RESULTS,
        effect_estimate=Decimal(effect),
        observation_count=10,
        condition_count=2,
        time_coverage_seconds=Decimal("60"),
        invalid_count=0,
        block_level_uncertainty="synthetic",
        direction="POSITIVE" if Decimal(effect) > 0 else "NEGATIVE",
    )


def test_frozen_package_identity(frozen_package: Frozen004APackage) -> None:
    assert frozen_package.package_version == "004A-event-time-v2"
    assert frozen_package.regime_package_sha256 == EXPECTED_004A_REGIME_SHA256
    assert {window.event_family for window in frozen_package.windows} == {
        "COL_2026",
        "PER_2026",
        "HUN_2026",
    }


def test_pre_and_active_results_never_pool() -> None:
    windows = (
        _window("A", "F1", ClaimRegime.PRE_ELECTION, 0, 100),
        _window("A", "F1", ClaimRegime.ACTIVE_RESULTS, 200, 300),
    )
    row = _obs(50, horizon=10)
    assert assign_observation_to_regime(
        row, windows, ClaimRegime.PRE_ELECTION
    ).valid
    active = assign_observation_to_regime(row, windows, ClaimRegime.ACTIVE_RESULTS)
    assert not active.valid
    assert active.reason_code == "DECISION_OUTSIDE_CLAIMED_REGIME"


def test_pre_election_label_crossing_boundary_is_rejected() -> None:
    window = _window("A", "F1", ClaimRegime.PRE_ELECTION, 0, 100)
    result = assign_observation_to_regime(
        _obs(95, horizon=10), (window,), ClaimRegime.PRE_ELECTION
    )
    assert not result.valid
    assert result.reason_code == "LABEL_CROSSES_REGIME_END"


def test_active_results_label_ending_exactly_at_end_is_rejected() -> None:
    window = _window("A", "F1", ClaimRegime.ACTIVE_RESULTS, 200, 300)
    result = assign_observation_to_regime(
        _obs(290, horizon=10), (window,), ClaimRegime.ACTIVE_RESULTS
    )
    assert not result.valid
    assert result.reason_code == "LABEL_CROSSES_REGIME_END"


def test_asof_violation_raises() -> None:
    window = _window("A", "F1", ClaimRegime.PRE_ELECTION, 0, 100)
    row = _obs(50, horizon=10, feature_offset=1)
    with pytest.raises(ValueError, match="future feature rejected"):
        assign_observation_to_regime(row, (window,), ClaimRegime.PRE_ELECTION)


def test_train_labels_crossing_frozen_holdout_boundary_are_purged() -> None:
    windows = (
        _window("A", "F1", ClaimRegime.PRE_ELECTION, 0, 150),
        _window("B", "F2", ClaimRegime.PRE_ELECTION, 100, 200),
    )
    result = construct_forward_event_holdout(
        (_obs(95, horizon=10), _obs(110, horizon=5, event="B", family="F2")),
        windows=windows,
        holdout_event_id="B",
        claim_regime=ClaimRegime.PRE_ELECTION,
        embargo=timedelta(0),
    )
    assert result.holdout_start == BASE + timedelta(seconds=100)
    assert result.purge_evidence.rows_removed_by_purge == 1
    assert all(item.role is FoldRole.HOLDOUT for item in result.assignments)


def test_development_labels_crossing_holdout_are_purged() -> None:
    rows = (_obs(10, horizon=1), _obs(40, horizon=15), _obs(60, horizon=1))
    assignments = chronological_split(
        rows,
        ChronologicalBoundaries(
            BASE + timedelta(seconds=20),
            BASE + timedelta(seconds=50),
        ),
    )
    kept, evidence = purge_development(
        assignments,
        BASE + timedelta(seconds=50),
    )
    assert evidence.rows_removed_by_purge == 1
    assert not any(
        item.role is FoldRole.DEVELOPMENT
        and item.observation.decision_time == BASE + timedelta(seconds=40)
        for item in kept
    )


def test_embargo_is_explicit_and_deterministic() -> None:
    windows = (
        _window("A", "F1", ClaimRegime.PRE_ELECTION, 0, 150),
        _window("B", "F2", ClaimRegime.PRE_ELECTION, 100, 200),
    )
    result = construct_forward_event_holdout(
        (_obs(95, horizon=1), _obs(110, horizon=5, event="B", family="F2")),
        windows=windows,
        holdout_event_id="B",
        claim_regime=ClaimRegime.PRE_ELECTION,
        embargo=timedelta(seconds=10),
    )
    assert result.purge_evidence.rows_removed_by_embargo == 1


def test_entire_heldout_event_is_absent_from_training() -> None:
    windows = (
        _window("A", "F1", ClaimRegime.PRE_ELECTION, 0, 90),
        _window("B", "F2", ClaimRegime.PRE_ELECTION, 100, 200),
    )
    rows = (
        _obs(50, horizon=5),
        _obs(110, horizon=5, event="B", family="F2"),
        _obs(120, horizon=5, event="B", family="F2"),
    )
    result = construct_forward_event_holdout(
        rows,
        windows=windows,
        holdout_event_id="B",
        claim_regime=ClaimRegime.PRE_ELECTION,
        embargo=timedelta(0),
    )
    assert all(
        item.role is FoldRole.HOLDOUT
        for item in result.assignments
        if item.observation.event_id == "B"
    )
    assert not any(
        item.role is FoldRole.TRAIN and item.observation.event_id == "B"
        for item in result.assignments
    )


def test_entire_heldout_family_is_absent_from_training() -> None:
    windows = (
        _window("A", "F1", ClaimRegime.PRE_ELECTION, 0, 90),
        _window("B", "F2", ClaimRegime.PRE_ELECTION, 100, 180),
        _window("C", "F2", ClaimRegime.PRE_ELECTION, 190, 260),
    )
    rows = (
        _obs(50, horizon=5),
        _obs(110, horizon=5, event="B", family="F2"),
        _obs(200, horizon=5, event="C", family="F2"),
    )
    result = construct_forward_family_holdout(
        rows,
        windows=windows,
        holdout_family="F2",
        claim_regime=ClaimRegime.PRE_ELECTION,
        embargo=timedelta(0),
    )
    assert all(
        item.role is FoldRole.HOLDOUT
        for item in result.assignments
        if item.observation.event_family_id == "F2"
    )
    assert not any(
        item.role is FoldRole.TRAIN and item.observation.event_family_id == "F2"
        for item in result.assignments
    )


def test_accepted_rounds_share_only_three_families(
    frozen_package: Frozen004APackage,
) -> None:
    family_by_regime = {
        window.regime_id: window.event_family
        for window in frozen_package.windows
        if window.claim_regime is ClaimRegime.PRE_ELECTION
    }
    assert family_by_regime["colombia_first_round"] == "COL_2026"
    assert family_by_regime["colombia_runoff"] == "COL_2026"
    assert family_by_regime["peru_first_round"] == "PER_2026"
    assert family_by_regime["peru_runoff"] == "PER_2026"
    assert family_by_regime["hungary_election"] == "HUN_2026"
    assert len(set(family_by_regime.values())) == 3


def test_same_family_transfer_is_not_independent_replication(
    frozen_package: Frozen004APackage,
    conditions: tuple[ConditionEligibility, ...],
) -> None:
    folds = build_fold_inventory(frozen_package.windows, conditions)
    transfers = [
        fold
        for fold in folds
        if fold.validation_method is ValidationMethod.CROSS_ROUND_SAME_FAMILY
    ]
    assert len(transfers) == 8
    assert all(not fold.independent_family_holdout for fold in transfers)
    assert all(fold.same_family_training_present for fold in transfers)


def test_retrospective_lofo_is_explicitly_non_temporal(
    frozen_package: Frozen004APackage,
    conditions: tuple[ConditionEligibility, ...],
) -> None:
    folds = build_fold_inventory(frozen_package.windows, conditions)
    diagnostics = [
        fold
        for fold in folds
        if fold.validation_method
        is ValidationMethod.LEAVE_FAMILY_OUT_RETROSPECTIVE_DIAGNOSTIC
    ]
    assert len(diagnostics) == 12
    assert all(not fold.chronological for fold in diagnostics)
    assert all(fold.status == "DIAGNOSTIC_ONLY" for fold in diagnostics)
    assert all(
        fold.evidence_scope is ValidationEvidenceScope.RETROSPECTIVE_ONLY
        for fold in diagnostics
    )
    assert all(
        fold.reason.startswith("NON_TEMPORAL_RETROSPECTIVE_DIAGNOSTIC")
        for fold in diagnostics
    )


def test_canonical_yes_rule_is_exact_case_sensitive() -> None:
    assert canonical_yes_index(("Yes", "No")) == 0
    assert canonical_yes_index(("No", "Yes")) == 1
    assert canonical_yes_index(("YES", "NO")) is None


def test_zero_or_multiple_exact_yes_fail_closed() -> None:
    assert canonical_yes_index(("YES", "NO")) is None
    assert canonical_yes_index(("Yes", "Yes")) is None
    assert canonical_yes_index(("No", "No")) is None


def test_accepted_condition_projection_counts_and_disagreements(
    conditions: tuple[ConditionEligibility, ...],
) -> None:
    assert len(conditions) == 1300
    assert sum(row.canonical_outcome == "Yes" for row in conditions) == 1187
    assert sum(row.condition_classification == "FAILED_CLOSED" for row in conditions) == 113
    failed_by_event: dict[str, int] = {}
    for row in conditions:
        if row.condition_classification == "FAILED_CLOSED":
            failed_by_event[row.regime_id] = failed_by_event.get(row.regime_id, 0) + 1
    assert failed_by_event == {
        "hungary_election": 27,
        "peru_first_round": 43,
        "peru_runoff": 43,
    }
    assert sum(row.token_pair_disagreement for row in conditions) == 25
    eligible_disagreements = [
        row
        for row in conditions
        if row.token_pair_disagreement and row.canonical_token_id is not None
    ]
    assert all(
        row.condition_classification == row.canonical_token_classification
        for row in eligible_disagreements
    )


def test_condition_universe_hash_is_deterministic(
    conditions: tuple[ConditionEligibility, ...],
) -> None:
    assert CONDITION_UNIVERSE_VERSION == "004A2-condition-universe-v1"
    assert condition_universe_sha256(conditions) == condition_universe_sha256(
        tuple(conditions)
    )


def test_family_evidence_collapses_rounds_before_aggregation() -> None:
    result = summarise_event_family_evidence(
        (
            _summary("col-r1", "COL_2026", "1"),
            _summary("col-r2", "COL_2026", "3"),
            _summary("per-r1", "PER_2026", "-1"),
            _summary("per-r2", "PER_2026", "1"),
            _summary("hun", "HUN_2026", "2"),
        )
    )
    assert result.eligible_independent_families == 3
    assert dict(result.family_effects) == {
        "COL_2026": Decimal("2"),
        "HUN_2026": Decimal("2"),
        "PER_2026": Decimal("0"),
    }
    assert result.effect_range == Decimal("2")
    assert result.flags == ("LOW_INDEPENDENT_FAMILY_COUNT",)


def test_five_event_windows_cannot_be_reported_as_five_families() -> None:
    result = summarise_event_family_evidence(
        (
            _summary("col-r1", "COL_2026", "1"),
            _summary("col-r2", "COL_2026", "1"),
            _summary("per-r1", "PER_2026", "1"),
            _summary("per-r2", "PER_2026", "1"),
            _summary("hun", "HUN_2026", "1"),
        )
    )
    assert result.eligible_independent_families == 3


def test_protocol_serialization_and_hash_are_deterministic(
    frozen_package: Frozen004APackage,
    conditions: tuple[ConditionEligibility, ...],
) -> None:
    universe_sha = condition_universe_sha256(conditions)
    left = make_validation_protocol(
        package=frozen_package,
        condition_universe_sha=universe_sha,
    )
    right = make_validation_protocol(
        package=frozen_package,
        condition_universe_sha=universe_sha,
    )
    assert left == right
    assert validation_protocol_sha256(left) == validation_protocol_sha256(right)
    assert left["fdr_lane_policy"]["pool_primary_regimes"] is False


def test_protocol_identity_changes_with_universe_or_package_hash(
    frozen_package: Frozen004APackage,
    conditions: tuple[ConditionEligibility, ...],
) -> None:
    original = make_validation_protocol(
        package=frozen_package,
        condition_universe_sha=condition_universe_sha256(conditions),
    )
    changed_universe = make_validation_protocol(
        package=frozen_package,
        condition_universe_sha="0" * 64,
    )
    changed_package = replace(frozen_package, regime_package_sha256="1" * 64)
    changed_regime = make_validation_protocol(
        package=changed_package,
        condition_universe_sha=condition_universe_sha256(conditions),
    )
    assert validation_protocol_sha256(original) != validation_protocol_sha256(
        changed_universe
    )
    assert validation_protocol_sha256(original) != validation_protocol_sha256(
        changed_regime
    )


def test_forward_family_inventory_fails_closed_without_prior_family(
    frozen_package: Frozen004APackage,
    conditions: tuple[ConditionEligibility, ...],
) -> None:
    folds = build_fold_inventory(frozen_package.windows, conditions)
    family_folds = [
        fold
        for fold in folds
        if fold.validation_method is ValidationMethod.FORWARD_FAMILY_HOLDOUT
    ]
    assert len(family_folds) == 12
    assert sum(fold.status == "INSUFFICIENT_PRIOR_FAMILIES" for fold in family_folds) == 4
    assert sum(fold.status == "FEASIBLE" for fold in family_folds) == 5


def test_inventory_feasibility_requires_nonempty_frozen_condition_universe(
    frozen_package: Frozen004APackage,
    conditions: tuple[ConditionEligibility, ...],
) -> None:
    folds = build_fold_inventory(frozen_package.windows, conditions)
    assert sum(fold.status == "FEASIBLE" for fold in folds) == 36
    newly_data_ineligible = [
        fold
        for fold in folds
        if fold.chronology_status == "CHRONOLOGY_OK"
        and fold.status != "FEASIBLE"
    ]
    assert len(newly_data_ineligible) == 16
    assert all(fold.data_eligibility_status != "DATA_ELIGIBLE" for fold in newly_data_ineligible)


def test_election_day_lane_has_zero_eligible_canonical_conditions(
    frozen_package: Frozen004APackage,
    conditions: tuple[ConditionEligibility, ...],
) -> None:
    folds = build_fold_inventory(frozen_package.windows, conditions)
    election_day = [
        fold for fold in folds if fold.claim_regime is ClaimRegime.ELECTION_DAY_PRE_RESULTS
    ]
    assert len(election_day) == 18
    assert not any(fold.status == "FEASIBLE" for fold in election_day)
    assert all(fold.eligible_holdout_conditions == 0 for fold in election_day)
    assert sum(
        fold.chronology_status == "CHRONOLOGY_OK"
        and fold.status == "NO_ELIGIBLE_TRAIN_OR_HOLDOUT_CONDITIONS"
        for fold in election_day
    ) == 13


def test_hungary_late_count_and_peru_r1_forward_training_fail_closed(
    frozen_package: Frozen004APackage,
    conditions: tuple[ConditionEligibility, ...],
) -> None:
    folds = {
        fold.fold_id: fold
        for fold in build_fold_inventory(frozen_package.windows, conditions)
    }
    hungary_within = folds["LATE_COUNT_DIAGNOSTIC__within__hungary_election"]
    assert hungary_within.eligible_train_conditions == 0
    assert hungary_within.eligible_holdout_conditions == 0
    assert hungary_within.status == "NO_ELIGIBLE_TRAIN_OR_HOLDOUT_CONDITIONS"

    peru_event = folds["LATE_COUNT_DIAGNOSTIC__forward_event__peru_first_round"]
    assert peru_event.chronology_status == "CHRONOLOGY_OK"
    assert peru_event.eligible_train_conditions == 0
    assert peru_event.eligible_holdout_conditions == 40
    assert peru_event.status == "NO_ELIGIBLE_TRAIN_CONDITIONS"

    peru_family = folds["LATE_COUNT_DIAGNOSTIC__forward_family__PER_2026"]
    assert peru_family.chronology_status == "CHRONOLOGY_OK"
    assert peru_family.eligible_train_conditions == 0
    assert peru_family.eligible_holdout_conditions == 81
    assert peru_family.status == "NO_ELIGIBLE_TRAIN_CONDITIONS"


def test_forward_family_holdout_uses_frozen_window_start_not_first_tick() -> None:
    windows = (
        _window("A", "F1", ClaimRegime.PRE_ELECTION, 0, 150),
        _window("B", "F2", ClaimRegime.PRE_ELECTION, 100, 200),
    )
    result = construct_forward_family_holdout(
        (
            _obs(95, horizon=1),
            _obs(105, horizon=1),
            _obs(120, horizon=1, event="B", family="F2"),
        ),
        windows=windows,
        holdout_family="F2",
        claim_regime=ClaimRegime.PRE_ELECTION,
        embargo=timedelta(0),
    )
    assert result.holdout_start == BASE + timedelta(seconds=100)
    assert not any(
        item.role is FoldRole.TRAIN
        and item.observation.decision_time >= BASE + timedelta(seconds=100)
        for item in result.assignments
    )


def test_same_family_transfer_holds_out_only_later_round() -> None:
    windows = (
        _window("R1", "F1", ClaimRegime.ACTIVE_RESULTS, 0, 80),
        _window("R2", "F1", ClaimRegime.ACTIVE_RESULTS, 100, 180),
    )
    result = construct_same_family_transfer_fold(
        (
            _obs(50, horizon=5, event="R1"),
            _obs(120, horizon=5, event="R2"),
        ),
        windows=windows,
        train_event_id="R1",
        holdout_event_id="R2",
        claim_regime=ClaimRegime.ACTIVE_RESULTS,
        embargo=timedelta(0),
    )
    assert [(item.observation.event_id, item.role) for item in result.assignments] == [
        ("R1", FoldRole.TRAIN),
        ("R2", FoldRole.HOLDOUT),
    ]


def test_retrospective_leave_family_out_can_use_later_rows() -> None:
    windows = (
        _window("A", "F1", ClaimRegime.PRE_ELECTION, 0, 90),
        _window("B", "F2", ClaimRegime.PRE_ELECTION, 100, 190),
    )
    assignments = construct_leave_family_out_diagnostic(
        (_obs(50, horizon=5), _obs(120, horizon=5, event="B", family="F2")),
        windows=windows,
        holdout_family="F1",
        claim_regime=ClaimRegime.PRE_ELECTION,
    )
    assert any(
        item.role is FoldRole.TRAIN and item.observation.event_family_id == "F2"
        for item in assignments
    )


def test_within_event_temporal_fold_stays_in_same_regime() -> None:
    window = _window("A", "F1", ClaimRegime.PRE_ELECTION, 0, 100)
    result = construct_within_event_temporal_fold(
        (
            _obs(20, horizon=5),
            _obs(60, horizon=5),
            _obs(95, horizon=10),
        ),
        windows=(window,),
        event_id="A",
        claim_regime=ClaimRegime.PRE_ELECTION,
        holdout_start=BASE + timedelta(seconds=50),
        embargo=timedelta(0),
    )
    assert [(item.observation.decision_time, item.role) for item in result.assignments] == [
        (BASE + timedelta(seconds=20), FoldRole.TRAIN),
        (BASE + timedelta(seconds=60), FoldRole.HOLDOUT),
    ]


def test_protocol_records_separate_primary_fdr_lanes(
    frozen_package: Frozen004APackage,
    conditions: tuple[ConditionEligibility, ...],
) -> None:
    protocol = make_validation_protocol(
        package=frozen_package,
        condition_universe_sha=condition_universe_sha256(conditions),
    )
    lanes = protocol["fdr_lane_policy"]
    assert lanes["PRE_ELECTION"] == "separate_primary_family"
    assert lanes["ACTIVE_RESULTS"] == "separate_primary_family"
    assert lanes["pool_primary_regimes"] is False
