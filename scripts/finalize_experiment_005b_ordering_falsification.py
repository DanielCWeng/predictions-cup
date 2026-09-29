"""Finalize EXPERIMENT-005B ordering falsification without reselection.

Consumes the immutable parent HOLDOUT result and the corrected post-hoc result.
No feature, target, model, threshold, candidate, hyperparameter or shortlist
selection is performed here.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

MOVEMENT = tuple(
    f"target_clock_realised_movement_{h}"
    for h in (1, 5, 15, 30, 60, 120, 300)
)
EVENT_SIGN = tuple(
    f"target_event_sign_{h}" for h in (1, 2, 5, 10)
)
EVENT_PRICE = (
    "target_event_price_change_5",
    "target_event_price_change_10",
)
FAMILIES = ("US_2024", "CAN_2025", "COL_2026", "HUN_2026", "PER_2026")
BASELINES = (
    "persistence",
    "own_recent_price",
    "current_absolute_movement",
    "recent_activity",
)
PARENT_HOLDOUT_SHA256 = (
    "94db3e2319d376078e9f80f5e28d04215dcade53d76c1ec749af8bcc0fa8972a"
)
FREEZE_SHA256 = (
    "44025aa2c5e5bddd97d88c81e649d86d6bf55d6cccec1d57c2aa7af12a9a7ec6"
)


def by_target(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(row["target"]): row for row in payload["results"]}


def movement_row(
    target: str,
    parent: dict[str, Any],
    corrected: dict[str, Any],
) -> dict[str, Any]:
    p = parent[target]
    c = corrected[target]
    pmodel = p.get("model")
    cmodel = c.get("model")
    if not pmodel or not cmodel:
        return {
            "target": target,
            "status": "falsified_or_missing",
            "reason": "original selected model is absent from corrected evaluation",
        }

    hold = cmodel["holdout_metrics"]
    candidate_mae = float(hold["mae"])
    base = cmodel["baseline_metrics"]
    baseline_mae = {
        name: float(base[name]["mae"])
        for name in BASELINES
    }
    beats_all = all(
        candidate_mae < baseline_mae[name] for name in BASELINES
    )

    per_family = cmodel["per_family_metrics"]
    family_improvement = {
        family: float(
            per_family[family]["mae_improvement_vs_persistence"]
        )
        for family in FAMILIES
    }
    all_families_positive = all(
        value > 0 for value in family_improvement.values()
    )

    boot = cmodel["hierarchical_market_bootstrap"]
    boot_mean = float(boot["mean"])
    boot_lower = float(boot["lower_2_5"])
    boot_upper = float(boot["upper_97_5"])
    boot_mean_positive = boot_mean > 0
    boot_lower_positive = boot_lower > 0

    # The parent finding specifically carried a positive 2.5% hierarchical
    # bootstrap bound. Requiring the corrected lower bound to remain >0 is a
    # conservative falsification rule; it cannot create a new positive claim.
    strict_survival = (
        beats_all
        and all_families_positive
        and boot_lower_positive
    )
    return {
        "target": target,
        "status": (
            "not_falsified_by_ordering_correction"
            if strict_survival
            else "falsified_or_materially_weakened_by_ordering_correction"
        ),
        "corrected_mae": candidate_mae,
        "corrected_baseline_mae": baseline_mae,
        "beats_every_original_named_baseline": beats_all,
        "corrected_family_mae_improvement_vs_persistence": family_improvement,
        "all_five_families_positive": all_families_positive,
        "corrected_hierarchical_bootstrap": {
            "mean": boot_mean,
            "lower_2_5": boot_lower,
            "upper_97_5": boot_upper,
        },
        "bootstrap_mean_positive": boot_mean_positive,
        "bootstrap_lower_2_5_positive": boot_lower_positive,
        "parent_holdout_metrics": pmodel.get("holdout_metrics"),
        "parent_hierarchical_bootstrap": pmodel.get(
            "hierarchical_market_bootstrap"
        ),
    }


def event_row(
    target: str,
    parent: dict[str, Any],
    corrected: dict[str, Any],
) -> dict[str, Any]:
    p = parent.get(target)
    c = corrected.get(target)
    if p is None or c is None:
        return {
            "target": target,
            "status": "not_evaluated_in_parent_or_corrected",
        }

    corrected_scalars = []
    for scalar in c.get("scalar_candidates", []):
        hold = scalar.get("holdout", {})
        train_rho = scalar.get("train_spearman")
        corrected_rho = hold.get("spearman")
        same_sign = (
            train_rho is not None
            and corrected_rho is not None
            and float(train_rho) * float(corrected_rho) > 0
        )
        corrected_scalars.append(
            {
                "feature": scalar.get("feature"),
                "selection_label": scalar.get("selection_label"),
                "parent_train_spearman": train_rho,
                "parent_dev_spearman": scalar.get("dev_spearman"),
                "corrected_holdout_spearman": corrected_rho,
                "same_sign_as_parent_train": same_sign,
                "corrected_holdout": hold,
            }
        )

    model = c.get("model")
    model_report: dict[str, Any] | None = None
    if model:
        hold = model.get("holdout_metrics", {})
        baselines = model.get("baseline_metrics", {})
        boot = model.get("hierarchical_market_bootstrap", {})
        model_report = {
            "corrected_holdout_metrics": hold,
            "corrected_baseline_metrics": baselines,
            "corrected_hierarchical_bootstrap": boot,
            "bootstrap_mean_positive": (
                float(boot["mean"]) > 0
                if boot.get("mean") is not None
                else None
            ),
            "bootstrap_lower_2_5_positive": (
                float(boot["lower_2_5"]) > 0
                if boot.get("lower_2_5") is not None
                else None
            ),
        }
        if "accuracy" in hold:
            acc = float(hold["accuracy"])
            named = [
                float(row["accuracy"])
                for name, row in baselines.items()
                if name in BASELINES and row.get("accuracy") is not None
            ]
            model_report["beats_every_named_accuracy_baseline"] = (
                bool(named) and all(acc > value for value in named)
            )
        if "mae" in hold:
            mae = float(hold["mae"])
            named = [
                float(row["mae"])
                for name, row in baselines.items()
                if name in BASELINES and row.get("mae") is not None
            ]
            model_report["beats_every_named_mae_baseline"] = (
                bool(named) and all(mae < value for value in named)
            )

    any_scalar_same_sign = any(
        row["same_sign_as_parent_train"] for row in corrected_scalars
    )
    return {
        "target": target,
        "classification": "POST_HOC_FALSIFICATION_ONLY",
        "original_scalar_candidates_evaluated": len(corrected_scalars),
        "any_original_scalar_retains_parent_train_sign": any_scalar_same_sign,
        "scalar_candidates": corrected_scalars,
        "model": model_report,
        "parent_model": (
            p.get("model", {}).get("holdout_metrics")
            if p.get("model")
            else None
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--parent-holdout", type=Path, required=True)
    parser.add_argument("--corrected-holdout", type=Path, required=True)
    parser.add_argument("--mapping-summary", type=Path, required=True)
    parser.add_argument("--matrix-diff", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    parent_payload = json.loads(args.parent_holdout.read_text())
    corrected_payload = json.loads(args.corrected_holdout.read_text())
    mapping = json.loads(args.mapping_summary.read_text())
    matrix_diff = json.loads(args.matrix_diff.read_text())

    if corrected_payload.get("classification") != "POST_HOC_FALSIFICATION_ONLY":
        raise RuntimeError("corrected payload is not post-hoc falsification")
    if (
        corrected_payload.get("train_dev_shortlist_sha256")
        != FREEZE_SHA256
    ):
        raise RuntimeError("corrected payload is not bound to parent freeze")
    if (
        corrected_payload.get("parent_holdout_results_sha256")
        != PARENT_HOLDOUT_SHA256
    ):
        raise RuntimeError("corrected payload is not bound to parent HOLDOUT")
    if mapping.get("all_families_pass") is not True:
        raise RuntimeError("actual block-number hard gate did not pass")

    parent = by_target(parent_payload)
    corrected = by_target(corrected_payload)

    movement = [
        movement_row(target, parent, corrected)
        for target in MOVEMENT
    ]
    strict_count = sum(
        row["status"] == "not_falsified_by_ordering_correction"
        for row in movement
    )
    movement_materially_survives = strict_count == len(MOVEMENT)

    event = [
        event_row(target, parent, corrected)
        for target in EVENT_SIGN + EVENT_PRICE
    ]

    result = {
        "schema_version": 1,
        "experiment_id": "EXPERIMENT-005B-ORDERING-FALSIFICATION",
        "classification": "POST_HOC_FALSIFICATION_ONLY",
        "interpretation_constraint": (
            "Corrected evidence can demote/falsify parent findings only; "
            "it cannot promote, reconfirm, or introduce candidates."
        ),
        "parent_holdout_sha256": PARENT_HOLDOUT_SHA256,
        "parent_train_dev_freeze_sha256": FREEZE_SHA256,
        "mapping_gate": mapping,
        "matrix_difference_totals": matrix_diff.get("totals"),
        "movement_findings": movement,
        "movement_horizons_not_falsified": strict_count,
        "movement_horizon_count": len(MOVEMENT),
        "movement_materially_survives": movement_materially_survives,
        "event_time_findings": event,
        "downstream_disposition": (
            "CARRY_UNCHANGED_MOVEMENT_HYPOTHESIS_TO_FRESH_DATA_003"
            if movement_materially_survives
            else "CLOSE_005B_AS_ORDERING_CONTAMINATED_HISTORICAL_DISCOVERY"
        ),
        "new_candidates_introduced": 0,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "movement_horizons_not_falsified": strict_count,
                "movement_horizon_count": len(MOVEMENT),
                "movement_materially_survives": movement_materially_survives,
                "downstream_disposition": result["downstream_disposition"],
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
