"""Summarize the EXPERIMENT-005B archival block-number hard gate."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

FAMILIES = ("US_2024", "CAN_2025", "COL_2026", "HUN_2026", "PER_2026")
EXPECTED_ROWS = {
    "US_2024": 7_890_463,
    "CAN_2025": 512_901,
    "COL_2026": 412_863,
    "HUN_2026": 505_395,
    "PER_2026": 750_233,
}
EXPECTED_TX = {
    "US_2024": 6_821_998,
    "CAN_2025": 340_791,
    "COL_2026": 279_775,
    "HUN_2026": 333_329,
    "PER_2026": 460_795,
}
EXPECTED_CANONICAL_SHA256 = {
    "US_2024": "207bfbdba42fa2626647044f0907e1522db8c30a12b94260e278d6d6a06cf1ca",
    "CAN_2025": "7160c056190583033c690ed8ff8bf6eb856de47dfa120628256602150014adc4",
    "COL_2026": "991d43f0a7ae5a740c97bc52049341a936fdec5ec0105cc195d68553a4bb37c9",
    "HUN_2026": "39a6f90fbfaa1855dcd7de1804463efc78a32a8a3f628fcb319cfb4e584cd13d",
    "PER_2026": "b3b52a94c313a54d06ca979588b5f64340cdd00eb602abd8d52222e221484005",
}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reports-dir", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    families: list[dict[str, Any]] = []
    failures: list[str] = []
    for family in FAMILIES:
        path = args.reports_dir / f"tx_block_report_{family}.json"
        report = json.loads(path.read_text())
        checks = {
            "economic_rows": report.get("economic_rows") == EXPECTED_ROWS[family],
            "parent_canonical_sha256": (
                report.get("parent_canonical_sha256")
                == EXPECTED_CANONICAL_SHA256[family]
            ),
            "tx_count": report.get("tx_count") == EXPECTED_TX[family],
            "mapped_tx_count": report.get("mapped_tx_count") == EXPECTED_TX[family],
            "mapped_block_count": (
                report.get("mapped_block_count") == report.get("block_count")
            ),
            "missing_block_numbers": report.get("missing_block_numbers") == 0,
            "missing_block_timestamps": (
                report.get("missing_block_timestamps") == 0
            ),
            "timestamp_mismatches": report.get("timestamp_mismatches") == 0,
            "duplicate_block_log_groups": (
                report.get("duplicate_block_log_groups") == 0
            ),
            "distinct_blocks_sharing_timestamp": (
                report.get("distinct_blocks_sharing_timestamp") == 0
            ),
        }
        passed = all(checks.values())
        if not passed:
            failures.append(
                family
                + ":"
                + ",".join(key for key, value in checks.items() if not value)
            )
        families.append(
            {
                "family": family,
                "passed": passed,
                "checks": checks,
                "report_sha256": sha256(path),
                "tx_block_sha256": report.get("tx_block_sha256"),
                "block_timestamp_sha256": report.get(
                    "block_timestamp_sha256"
                ),
                "economic_rows": report.get("economic_rows"),
                "tx_count": report.get("tx_count"),
                "block_count": report.get("block_count"),
            }
        )

    result = {
        "schema_version": 1,
        "experiment_id": "EXPERIMENT-005B-ORDERING-FALSIFICATION",
        "stage": "ACTUAL_BLOCK_NUMBER_HARD_GATE",
        "classification": "POST_HOC_FALSIFICATION_ONLY",
        "source": "Polygon archival JSON-RPC",
        "families": families,
        "totals": {
            "economic_rows": sum(int(row["economic_rows"]) for row in families),
            "tx_count": sum(int(row["tx_count"]) for row in families),
            "block_count": sum(int(row["block_count"]) for row in families),
        },
        "all_families_pass": not failures,
        "failures": failures,
    }
    if result["totals"]["economic_rows"] != 10_071_855:
        failures.append("total economic rows != 10071855")
    if result["totals"]["tx_count"] != 8_236_688:
        failures.append("total tx count != 8236688")
    result["all_families_pass"] = not failures
    result["failures"] = failures

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "all_families_pass": result["all_families_pass"],
                "economic_rows": result["totals"]["economic_rows"],
                "tx_count": result["totals"]["tx_count"],
                "block_count": result["totals"]["block_count"],
            },
            sort_keys=True,
        )
    )
    if failures:
        raise SystemExit("block-number hard gate failed: " + "; ".join(failures))


if __name__ == "__main__":
    main()
