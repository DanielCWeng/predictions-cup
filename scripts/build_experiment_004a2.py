"""Materialize the EXPERIMENT-004A.2 validation protocol from committed evidence."""

from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path
from typing import Any

from predictions_cup.learning.event_time_validation import (
    CONDITION_UNIVERSE_VERSION,
    VALIDATION_PROTOCOL_VERSION,
    ValidationMethod,
    build_fold_inventory,
    condition_universe_sha256,
    load_frozen_004a,
    make_validation_protocol,
    project_condition_universe,
    validation_protocol_sha256,
)

ROOT = Path(__file__).resolve().parents[1]
PACKAGE_004A = ROOT / "data" / "experiments" / "experiment_004a"
OUTPUT = ROOT / "data" / "experiments" / "experiment_004a2"
IDENTITY = ROOT / "data" / "manifests" / "historical" / "data_001_market_identity.csv"

CONDITION_FIELDS = (
    "regime_id",
    "event_family",
    "condition_id",
    "market_id",
    "market_family",
    "question",
    "canonical_token_id",
    "canonical_outcome",
    "counterpart_token_id",
    "pre_election_usable",
    "election_day_pre_results_usable",
    "election_day_pre_results_eligibility_status",
    "active_results_usable",
    "late_count_usable",
    "canonical_token_classification",
    "counterpart_token_classification",
    "token_pair_disagreement",
    "condition_classification",
    "reason_codes",
)

FOLD_FIELDS = (
    "claim_regime",
    "claim_scope",
    "validation_method",
    "evidence_scope",
    "fold_id",
    "train_events",
    "development_events",
    "holdout_event",
    "holdout_family",
    "holdout_window_start",
    "holdout_window_end",
    "chronological",
    "independent_family_holdout",
    "same_family_training_present",
    "chronology_status",
    "data_eligibility_status",
    "eligible_train_conditions",
    "eligible_holdout_conditions",
    "unassessed_train_conditions",
    "unassessed_holdout_conditions",
    "status",
    "reason",
)


def _write_csv(path: Path, rows: list[dict[str, str]], fields: tuple[str, ...]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def main() -> None:
    package = load_frozen_004a(PACKAGE_004A)
    conditions = project_condition_universe(PACKAGE_004A, IDENTITY)
    if len(conditions) != 1300:
        raise ValueError(f"expected 1,300 conditions, got {len(conditions)}")

    condition_rows = [row.as_csv_row() for row in conditions]
    _write_csv(OUTPUT / "condition_usability_matrix.csv", condition_rows, CONDITION_FIELDS)
    universe_sha = condition_universe_sha256(conditions)

    canonical_yes = sum(row.canonical_outcome == "Yes" for row in conditions)
    failed_closed = sum(row.condition_classification == "FAILED_CLOSED" for row in conditions)
    disagreements = sum(row.token_pair_disagreement for row in conditions)
    if disagreements != 25:
        raise ValueError(
            f"accepted 004A must contain 25 token-pair disagreements, got {disagreements}"
        )

    classification_counts = Counter(row.condition_classification for row in conditions)
    failed_reasons = Counter(
        reason
        for row in conditions
        if row.condition_classification == "FAILED_CLOSED"
        for reason in row.reason_codes
    )
    failed_by_event = Counter(
        row.regime_id
        for row in conditions
        if row.condition_classification == "FAILED_CLOSED"
    )
    condition_summary: dict[str, Any] = {
        "experiment_id": "EXPERIMENT-004A.2",
        "condition_universe_version": CONDITION_UNIVERSE_VERSION,
        "condition_universe_sha256": universe_sha,
        "source_004a_package_version": package.package_version,
        "source_004a_regime_sha256": package.regime_package_sha256,
        "canonical_token_rule": "exact outcome label == 'Yes'; zero/multiple fail closed",
        "conditions_total": len(conditions),
        "canonical_yes_conditions": canonical_yes,
        "failed_closed_conditions": failed_closed,
        "failed_closed_conditions_by_event": dict(sorted(failed_by_event.items())),
        "token_pair_disagreements": disagreements,
        "token_pair_disagreement_cases": [
            {
                "regime_id": row.regime_id,
                "condition_id": row.condition_id,
                "canonical_token_id": row.canonical_token_id,
                "canonical_token_classification": row.canonical_token_classification,
                "counterpart_token_id": row.counterpart_token_id,
                "counterpart_token_classification": row.counterpart_token_classification,
            }
            for row in conditions
            if row.token_pair_disagreement
        ],
        "classification_counts": dict(sorted(classification_counts.items())),
        "failed_closed_reason_counts": dict(sorted(failed_reasons.items())),
        "election_day_pre_results_eligibility_status_counts": dict(
            sorted(
                Counter(
                    row.election_day_pre_results_eligibility_status
                    for row in conditions
                ).items()
            )
        ),
        "event_families": ["COL_2026", "PER_2026", "HUN_2026"],
        "alpha_searched": False,
    }
    _write_json(OUTPUT / "condition_universe_summary.json", condition_summary)

    protocol = make_validation_protocol(
        package=package,
        condition_universe_sha=universe_sha,
    )
    protocol_sha = validation_protocol_sha256(protocol)
    protocol["validation_protocol_sha256"] = protocol_sha
    _write_json(OUTPUT / "validation_protocol.json", protocol)

    folds = build_fold_inventory(package.windows, conditions)
    fold_rows = [fold.as_csv_row() for fold in folds]
    _write_csv(OUTPUT / "validation_fold_inventory.csv", fold_rows, FOLD_FIELDS)

    method_counts = Counter(fold.validation_method.value for fold in folds)
    status_counts = Counter(fold.status for fold in folds)
    chronology_status_counts = Counter(fold.chronology_status for fold in folds)
    data_eligibility_status_counts = Counter(
        fold.data_eligibility_status for fold in folds
    )
    feasible_by_method = Counter(
        fold.validation_method.value
        for fold in folds
        if fold.status == "FEASIBLE"
    )
    forward_family = [
        fold
        for fold in folds
        if fold.validation_method is ValidationMethod.FORWARD_FAMILY_HOLDOUT
    ]
    summary: dict[str, Any] = {
        "experiment_id": "EXPERIMENT-004A.2",
        "validation_protocol_version": VALIDATION_PROTOCOL_VERSION,
        "validation_protocol_sha256": protocol_sha,
        "condition_universe_version": CONDITION_UNIVERSE_VERSION,
        "condition_universe_sha256": universe_sha,
        "004a_package_version": package.package_version,
        "004a_regime_sha256": package.regime_package_sha256,
        "regime_lanes": [
            "PRE_ELECTION",
            "ELECTION_DAY_PRE_RESULTS",
            "ACTIVE_RESULTS",
            "LATE_COUNT_DIAGNOSTIC",
        ],
        "claim_lane_eligibility": {
            "ELECTION_DAY_PRE_RESULTS": "ELIGIBILITY_NOT_ASSESSED",
            "upstream_004a_usable_consumed": False,
            "upstream_004a_false_semantics": "NOT_A_PREDICTIVE_REGIME",
        },
        "folds_total": len(folds),
        "fold_counts_by_method": dict(sorted(method_counts.items())),
        "feasible_fold_counts_by_method": dict(sorted(feasible_by_method.items())),
        "fold_status_counts": dict(sorted(status_counts.items())),
        "fold_chronology_status_counts": dict(sorted(chronology_status_counts.items())),
        "fold_data_eligibility_status_counts": dict(
            sorted(data_eligibility_status_counts.items())
        ),
        "forward_family_holdouts": {
            f"{fold.claim_regime.value}__{fold.holdout_family or ''}": fold.status
            for fold in forward_family
        },
        "independent_event_families": {
            "COL_2026": ["colombia_first_round", "colombia_runoff"],
            "PER_2026": ["peru_first_round", "peru_runoff"],
            "HUN_2026": ["hungary_election"],
        },
        "small_n_flag": "LOW_INDEPENDENT_FAMILY_COUNT",
        "canonical_yes_conditions": canonical_yes,
        "failed_closed_conditions": failed_closed,
        "failed_closed_conditions_by_event": dict(sorted(failed_by_event.items())),
        "token_pair_disagreements": disagreements,
        "alpha_searched": False,
        "data001_rescanned": False,
        "limitations": [
            "Only three independent election families exist in DATA-001.",
            (
                "Exact Yes-token selection fails closed where accepted DATA-001 outcome labels "
                "are not exactly 'Yes'; no case normalization is permitted."
            ),
            "Within-window tick count does not increase the independent election-family count.",
            "Retrospective leave-family-out diagnostics are not prospective OOS evidence.",
            (
                "Fold FEASIBLE status requires non-empty assessed eligible training and "
                "holdout condition universes in addition to chronology."
            ),
            (
                "ELECTION_DAY_PRE_RESULTS eligibility was not assessed by accepted 004A; "
                "the upstream NOT_A_PREDICTIVE_REGIME false sentinel is not empirical absence."
            ),
        ],
    }
    _write_json(OUTPUT / "validation_summary.json", summary)

    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
