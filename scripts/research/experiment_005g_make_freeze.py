#!/usr/bin/env python3
"""Build EXPERIMENT-005G PRE_HOLDOUT_FREEZE from already committed TRAIN/DEV evidence.

This script intentionally contains the candidate-admission logic before Lane B DEV is read:
- Lane A admits only 005F_REPL_PRE_UPDATE.
- Lane B admits only rows with promotion_gate_pass=true.
No manual substitution path exists.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
LANE_A_ACTIVE = ROOT / (
    "data/experiments/experiment_005g/lane_a/"
    "ACTIVE_EV18_TRAIN_DEV_EVIDENCE.json"
)
LANE_A_PRE = ROOT / (
    "data/experiments/experiment_005g/lane_a/"
    "PRE_BASELINE_TRAIN_DEV_EVIDENCE.json"
)
DEFAULT_LANE_B = ROOT / (
    "data/experiments/experiment_005g/lane_b/"
    "SEP_TRAIN_DEV_EVIDENCE.json"
)
DEFAULT_LANE_B_SEQUENTIAL = ROOT / (
    "data/experiments/experiment_005g/lane_b/"
    "SEQUENTIAL_TRAIN_DEV_EVIDENCE.json"
)

DATASET = "polyleviathan/sig-cup-data003-orderbooks"
DATASET_VERSION = 1
ACQUISITION_SHA256 = (
    "d25f07e450f57647f198a9e2730889a7846b78a2c4d6f84474b5b45bc33f8532"
)
MAPPING_SHA256 = (
    "9bc3d55317ace5e7e56eb0e4deaf2d36d09421b2861b7104977a96bedfc16df2"
)
ALLOWED_LANE_A = frozenset({"005F_REPL_PRE_UPDATE"})
FORBIDDEN_LANE_A = frozenset(
    {
        "005F_REPL_ACTIVE_UPDATE",
        "005F_REPL_ACTIVE_JUMP",
        "005F_REPL_ACTIVE_PRICE_NEGATIVE",
        "005F_REPL_PRE_LIQUIDITY",
    }
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def lane_a_candidate(pre: dict[str, Any]) -> dict[str, Any]:
    rows = {
        str(row["candidate_id"]): row
        for row in pre.get("candidates", [])
    }
    if set(rows) != {"005F_REPL_PRE_UPDATE", "005F_REPL_PRE_LIQUIDITY"}:
        raise RuntimeError(f"unexpected PRE Lane A coordinates: {sorted(rows)}")
    selected = rows["005F_REPL_PRE_UPDATE"]
    if selected.get("dev_disposition") != "HOLDOUT_ELIGIBLE":
        raise RuntimeError("PRE update did not retain its frozen HOLDOUT_ELIGIBLE label")
    rejected = rows["005F_REPL_PRE_LIQUIDITY"]
    if rejected.get("dev_disposition") == "HOLDOUT_ELIGIBLE":
        raise RuntimeError("PRE liquidity unexpectedly became HOLDOUT eligible")

    return {
        "candidate_id": "005F_REPL_PRE_UPDATE",
        "lane": "A_STRICT_005F_REPLICATION",
        "target": "update_h300",
        "target_family": "UPDATE_HAZARD",
        "feature": "genuine_age_s",
        "feature_family": "BBO_AGE",
        "baseline": ["genuine_15", "genuine_60"],
        "kind": "classification",
        "model": "HGB_D3_LR0.03",
        "dev_mean_loss_improvement": selected["mean_loss_improvement"],
        "dev_relative_mse_improvement": selected["relative_mse_improvement"],
        "dev_signflip_p": selected["signflip_p"],
        "dev_block_bootstrap_lower": selected["block_bootstrap_lower"],
        "dev_leave_market_min": selected["leave_market_min"],
    }


def lane_b_candidates(evidence: dict[str, Any]) -> list[dict[str, Any]]:
    promoted = evidence.get("promoted_train_dev")
    if not isinstance(promoted, list):
        raise RuntimeError("Lane B evidence missing promoted_train_dev list")

    output: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for row in promoted:
        if row.get("promotion_gate_pass") is not True:
            raise RuntimeError(
                "Lane B promoted list contains a row without promotion_gate_pass=true"
            )
        target = str(row["target"])
        feature = str(row["feature"])
        key = (target, feature)
        if key in seen:
            raise RuntimeError(f"duplicate Lane B promoted coordinate: {key}")
        seen.add(key)

        target_spec = evidence.get("target_specs", {}).get(target)
        if not isinstance(target_spec, dict):
            raise RuntimeError(f"Lane B target spec missing for {target}")
        kind = str(target_spec["kind"])
        baseline = [str(value) for value in target_spec["baseline"]]
        model = "LOGIT_C1.0" if kind == "classification" else "RIDGE_1.0"

        output.append(
            {
                "candidate_id": f"005G_DISCOVERY|{target}|{feature}",
                "lane": "B_OPEN_DISCOVERY",
                "target": target,
                "target_family": str(row["target_family"]),
                "feature": feature,
                "feature_family": str(row["feature_family"]),
                "baseline": baseline,
                "kind": kind,
                "model": model,
                "dev_mean_loss_improvement": row["mean_loss_improvement"],
                "dev_relative_mse_improvement": row["relative_mse_improvement"],
                "dev_signflip_p": row["signflip_p"],
                "dev_p_bh": row.get("p_bh"),
                "dev_block_bootstrap_lower": row["block_bootstrap_lower"],
                "dev_leave_market_min": row["leave_market_min"],
            }
        )
    return sorted(output, key=lambda row: str(row["candidate_id"]))


def sequential_candidates(evidence: dict[str, Any]) -> list[dict[str, Any]]:
    promoted = evidence.get("promoted_train_dev")
    if not isinstance(promoted, list):
        raise RuntimeError("Sequential Lane B evidence missing promoted_train_dev list")
    output: list[dict[str, Any]] = []
    for row in promoted:
        if row.get("promotion_gate_pass") is not True:
            raise RuntimeError(
                "Sequential promoted list contains a row without promotion_gate_pass=true"
            )
        target = str(row["target"])
        feature = str(row["feature"])
        output.append(
            {
                "candidate_id": f"005G_DISCOVERY_SEQ|{target}|{feature}",
                "lane": "B_OPEN_DISCOVERY",
                "target": target,
                "target_family": str(row["target_family"]),
                "feature": feature,
                "feature_family": str(row["feature_family"]),
                "baseline": [str(value) for value in row["baseline"]],
                "kind": str(row["kind"]),
                "model": str(row["model"]),
                "dev_mean_loss_improvement": row["mean_loss_improvement"],
                "dev_relative_mse_improvement": row["relative_mse_improvement"],
                "dev_signflip_p": row["signflip_p"],
                "dev_p_bh": row.get("p_bh"),
                "dev_block_bootstrap_lower": row["block_bootstrap_lower"],
                "dev_leave_market_min": row["leave_market_min"],
                "sequential_family": True,
            }
        )
    return sorted(output, key=lambda row: str(row["candidate_id"]))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lane-b", default=str(DEFAULT_LANE_B))
    parser.add_argument(
        "--lane-b-sequential",
        default=str(DEFAULT_LANE_B_SEQUENTIAL),
    )
    parser.add_argument("--output", required=True)
    parser.add_argument("--lane-a-runner-commit", required=True)
    parser.add_argument("--lane-b-runner-commit", required=True)
    parser.add_argument("--holdout-runner-commit", required=True)
    args = parser.parse_args()

    active = load_json(LANE_A_ACTIVE)
    pre = load_json(LANE_A_PRE)
    lane_b_path = Path(args.lane_b)
    lane_b = load_json(lane_b_path)
    lane_b_sequential_path = Path(args.lane_b_sequential)
    lane_b_sequential = load_json(lane_b_sequential_path)

    if active.get("lane") != "A_STRICT_005F_REPLICATION":
        raise RuntimeError("wrong active Lane A evidence")
    if pre.get("lane") != "A_STRICT_005F_REPLICATION":
        raise RuntimeError("wrong PRE Lane A evidence")
    if lane_b.get("lane") != "B_OPEN_DISCOVERY":
        raise RuntimeError("wrong Lane B evidence")
    if lane_b_sequential.get("lane") != "B_SEQUENTIAL_FAMILIES":
        raise RuntimeError("wrong sequential Lane B evidence")
    if active.get("provenance", {}).get("holdout_read") is not False:
        raise RuntimeError("active evidence indicates holdout read")
    if pre.get("provenance", {}).get("holdout_read") is not False:
        raise RuntimeError("PRE evidence indicates holdout read")
    if lane_b.get("holdout_read") is not False:
        raise RuntimeError("Lane B evidence indicates holdout read")
    if lane_b_sequential.get("holdout_read") is not False:
        raise RuntimeError("Sequential Lane B evidence indicates holdout read")

    candidates = [
        lane_a_candidate(pre),
        *lane_b_candidates(lane_b),
        *sequential_candidates(lane_b_sequential),
    ]
    ids = {str(row["candidate_id"]) for row in candidates}
    if ids & FORBIDDEN_LANE_A:
        raise RuntimeError("forbidden Lane A candidate entered freeze")
    if not ids >= ALLOWED_LANE_A:
        raise RuntimeError("required PRE update replication candidate missing")

    holdout_files = [
        f"baseline_sep/date=2026-09-05/hour={hour:02d}/events.parquet"
        for hour in range(24)
    ]
    payload = {
        "schema_version": 1,
        "experiment": "EXPERIMENT-005G",
        "phase": "PRE_HOLDOUT_FREEZE",
        "status": "FROZEN",
        "holdout_read": False,
        "dataset": {
            "slug": DATASET,
            "version": DATASET_VERSION,
            "effective_acquisition_artifact_sha256": ACQUISITION_SHA256,
            "accepted_mapping_sha256": MAPPING_SHA256,
        },
        "evidence": {
            "lane_a_active_path": str(LANE_A_ACTIVE.relative_to(ROOT)),
            "lane_a_active_sha256": sha256(LANE_A_ACTIVE),
            "lane_a_pre_path": str(LANE_A_PRE.relative_to(ROOT)),
            "lane_a_pre_sha256": sha256(LANE_A_PRE),
            "lane_b_path": str(lane_b_path.relative_to(ROOT)),
            "lane_b_sha256": sha256(lane_b_path),
            "lane_b_sequential_path": str(
                lane_b_sequential_path.relative_to(ROOT)
            ),
            "lane_b_sequential_sha256": sha256(lane_b_sequential_path),
        },
        "code": {
            "lane_a_runner_commit": args.lane_a_runner_commit,
            "lane_b_runner_commit": args.lane_b_runner_commit,
            "holdout_runner_commit": args.holdout_runner_commit,
        },
        "candidate_admission": {
            "lane_a_rule": "only 005F_REPL_PRE_UPDATE; all other Lane A coordinates frozen out",
            "lane_b_rule": (
                "only promotion_gate_pass=true rows from frozen broad "
                "and sequential Lane B screens"
            ),
            "manual_replacements_allowed": False,
            "post_holdout_tuning_allowed": False,
        },
        "holdout_window": [
            "2026-09-05T00:00:00Z",
            "2026-09-06T00:00:00Z",
        ],
        "holdout_files": holdout_files,
        "holdout_candidates": candidates,
        "candidate_count": len(candidates),
        "make_modified": False,
        "real_sig_orders_sent": False,
    }

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "output": str(output),
                "sha256": sha256(output),
                "candidate_count": len(candidates),
                "lane_a_candidates": 1,
                "lane_b_candidates": len(candidates) - 1,
                "holdout_read": False,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
