"""Aggregate the five EXPERIMENT-005B family feature-build reports."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, cast

FAMILIES = ("US_2024", "CAN_2025", "COL_2026", "HUN_2026", "PER_2026")


def load(path: Path) -> dict[str, Any]:
    return cast(
        dict[str, Any],
        json.loads(path.read_text(encoding="utf-8")),
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--root",
        type=Path,
        default=Path("data/experiments/experiment_005b/results"),
    )
    args = parser.parse_args()
    root = args.root
    reports = [
        load(root / f"feature_target_build_report_{family}.json")
        for family in FAMILIES
    ]

    reference = reports[0]
    for report, family in zip(reports, FAMILIES, strict=True):
        if report["family"] != family:
            raise RuntimeError(
                f"family mismatch: expected {family}, got {report['family']}"
            )
        for key in (
            "feature_target_spec_sha256",
            "feature_count",
            "target_count",
            "target_columns",
        ):
            if report[key] != reference[key]:
                raise RuntimeError(f"schema/spec drift for {family}: {key}")
        reference_features = reference["feature_columns"]
        current_features = report["feature_columns"]
        if (
            len(reference_features) != len(set(reference_features))
            or len(current_features) != len(set(current_features))
            or set(current_features) != set(reference_features)
        ):
            raise RuntimeError(
                f"schema/spec drift for {family}: feature_columns"
            )

    split_counts: dict[str, int] = {}
    for report in reports:
        for split, count in report["split_counts"].items():
            split_counts[split] = split_counts.get(split, 0) + int(count)

    payload = {
        "schema_version": 1,
        "experiment_id": "EXPERIMENT-005B",
        "stage": "features_targets_aggregate",
        "feature_target_spec_sha256": reference[
            "feature_target_spec_sha256"
        ],
        "feature_target_spec_repo_path": reference[
            "feature_target_spec_repo_path"
        ],
        "feature_count": reference["feature_count"],
        "target_count": reference["target_count"],
        "feature_columns": reference["feature_columns"],
        "target_columns": reference["target_columns"],
        "families": reports,
        "totals": {
            "rows": sum(int(report["rows"]) for report in reports),
            "output_bytes": sum(
                int(report["output_bytes"]) for report in reports
            ),
            "split_counts": split_counts,
        },
    }
    target = root / "feature_target_build_report.json"
    target.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "rows": payload["totals"]["rows"],
                "families": len(reports),
                "feature_count": payload["feature_count"],
                "target_count": payload["target_count"],
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
