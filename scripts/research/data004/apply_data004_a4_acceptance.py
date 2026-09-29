#!/usr/bin/env python3
"""Apply the DATA-004 Addendum 4 source-quantum conservation disposition."""

from __future__ import annotations

import csv
import hashlib
import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[3]
LANE = Path("/home/ubuntu/campaigns/data004_20260929")
PACKAGE = Path("/home/ubuntu/inbox/data004_20260929/sig-cup-data-004-ets-p0p1-fills")
V2 = ROOT / "data/research/data004_ets_p0p1/v2"
QUANTUM = Decimal("0.0001")
STATUS = "ACCEPTED_V2"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def measure_size_precision() -> dict[str, Any]:
    histogram: dict[int, int] = {}
    rows = 0
    maximum = 0
    for path in sorted((PACKAGE / "fills").rglob("*.parquet")):
        parquet = pq.ParquetFile(path)
        for batch in parquet.iter_batches(columns=["size_shares"], batch_size=8192):
            for value in batch.column(0).to_pylist():
                if value is None:
                    raise ValueError(f"null size_shares value in {path}")
                decimal_value = Decimal(str(value))
                decimal_places = max(0, -decimal_value.as_tuple().exponent)
                histogram[decimal_places] = histogram.get(decimal_places, 0) + 1
                maximum = max(maximum, decimal_places)
                rows += 1
    return {
        "field": "size_shares",
        "rows_scanned": rows,
        "max_decimal_places_observed": maximum,
        "decimal_places_histogram": {str(k): histogram[k] for k in sorted(histogram)},
        "source_precision_claim": "The source lake serializes size_shares at four decimal places; on-chain amounts have six decimal places.",
    }


def main() -> None:
    manifest_path = V2 / "data004_manifest.json"
    quality_path = V2 / "data004_quality.json"
    conservation_path = V2 / "maker_taker_conservation.json"
    audit_path = V2 / "maker_taker_scope_audit.json"
    residuals_path = V2 / "maker_taker_tx_condition_residuals.csv"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    quality = json.loads(quality_path.read_text(encoding="utf-8"))
    conservation = json.loads(conservation_path.read_text(encoding="utf-8"))
    scope_audit = json.loads(audit_path.read_text(encoding="utf-8"))
    if (
        manifest.get("status") == STATUS
        and quality.get("status") == STATUS
        and quality.get("all_gates_pass")
    ):
        print(
            json.dumps(
                {
                    "status": STATUS,
                    "already_applied": True,
                    "manifest_sha256": sha256_file(manifest_path),
                },
                sort_keys=True,
            )
        )
        return
    if (
        manifest.get("status") != "BLOCKED_QUALITY_GATE"
        or quality.get("status") != "BLOCKED_QUALITY_GATE"
    ):
        raise RuntimeError("Addendum 4 may only transition the existing blocked v2 artifacts")
    old_manifest_sha = sha256_file(manifest_path)

    precision = measure_size_precision()
    expected_rows = int(manifest["counts"]["deduped_fill_rows"])
    if precision["rows_scanned"] != expected_rows or precision["max_decimal_places_observed"] != 4:
        raise RuntimeError(f"source precision evidence does not match Addendum 4: {precision}")

    with residuals_path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        fields = list(reader.fieldnames or [])
        residuals = list(reader)
    if len(residuals) != 43:
        raise RuntimeError(f"expected 43 precision residuals, found {len(residuals)}")
    maker_counts: list[int] = []
    ratios: list[Decimal] = []
    for row in residuals:
        delta = abs(Decimal(row["residual_size_taker_minus_maker"]))
        maker_rows = int(row["maker_rows"])
        tolerance = QUANTUM * max(maker_rows, 1)
        if delta > tolerance or delta != QUANTUM:
            raise RuntimeError(f"residual is not within the authorized source quantum: {row}")
        row["cause_class"] = "PRECISION_ROUNDING_4DP"
        maker_counts.append(maker_rows)
        ratios.append(delta / tolerance)

    with residuals_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(residuals)
    residual_sha = sha256_file(residuals_path)

    source_quantum_tolerance = {
        "source_quantum_shares": str(QUANTUM),
        "pass_condition": "abs(taker_size - maker_size) <= source_quantum_shares * max(maker_rows, 1)",
        "maximum_maker_rows_in_rounding_groups": max(maker_counts),
        "maximum_applicable_tolerance_shares": str(QUANTUM * max(maker_counts)),
        "maximum_observed_absolute_residual_shares": str(
            max(abs(Decimal(row["residual_size_taker_minus_maker"])) for row in residuals)
        ),
        "maximum_residual_to_tolerance_ratio": str(max(ratios)),
        "rounding_group_count": len(residuals),
        "all_groups_within_tolerance": True,
        "justification": "All 43 non-zero residuals are exactly one 4-decimal source quantum. The source lake stores size_shares to four decimal places while on-chain amounts have six decimal places, so each summed maker row may contribute at most one source quantum of rounding. Fill values remain unchanged.",
    }

    for row in residuals:
        row["cause_class"] = "PRECISION_ROUNDING_4DP"
    scope_audit["cause_counts"] = {"PRECISION_ROUNDING_4DP": len(residuals)}
    scope_audit["remaining_unexplained_residuals"] = []
    scope_audit["unexplained_residual_groups"] = 0
    scope_audit["precision_rounding_groups"] = len(residuals)
    scope_audit["precision_rounding_residuals"] = residuals
    scope_audit["precision_tolerance"] = source_quantum_tolerance
    scope_audit["residuals_csv"] = "maker_taker_tx_condition_residuals.csv"
    scope_audit["residuals_sha256"] = residual_sha
    write_json(audit_path, scope_audit)
    audit_sha = sha256_file(audit_path)

    conservation["method"] = (
        "Decimal aggregation by (tx_hash, condition_id), with acceptance at the measured 4-decimal source quantum; fill values are not rounded or changed."
    )
    conservation["pass_at_source_quantum"] = True
    conservation["source_precision_evidence"] = precision
    conservation["source_quantum_tolerance"] = source_quantum_tolerance
    conservation["residual_cause_counts"] = {"PRECISION_ROUNDING_4DP": len(residuals)}
    conservation["residual_buckets_by_cause"] = {
        "exact_groups": 96587,
        "one_sided_groups": 0,
        "outside_scope_explained_residual": 0,
        "PRECISION_ROUNDING_4DP": len(residuals),
        "unexplained_residual_groups": 0,
    }
    conservation["scope_audit"] = {
        "outside_scope_fill_rows": 0,
        "transactions_checked": scope_audit["transactions_checked"],
        "precision_rounding_groups": len(residuals),
        "unexplained_residual_groups": 0,
        "residuals_csv": "maker_taker_tx_condition_residuals.csv",
        "residuals_sha256": residual_sha,
        "scope_audit_json": "maker_taker_scope_audit.json",
        "scope_audit_sha256": audit_sha,
    }
    gate_summary = conservation["per_tx_condition_conservation"]
    gate_summary.update(
        {
            "method": "Group taker-order rows against maker rows by (tx_hash, condition_id), sum size_shares using Decimal(str(value)), then compare at the 4-decimal source quantum.",
            "mismatched_size_groups": len(residuals),
            "precision_rounding_groups": len(residuals),
            "unexplained_residual_groups": 0,
            "pass_before_scope_attribution": True,
            "strict_exact_equality_pass": False,
            "pass_at_source_quantum": True,
            "pass": True,
            "residual_cause_counts": {"PRECISION_ROUNDING_4DP": len(residuals)},
            "residuals_csv": "maker_taker_tx_condition_residuals.csv",
            "residuals_sha256": residual_sha,
            "precision_rounding_residuals": residuals,
            "tolerance": source_quantum_tolerance,
            "source_precision_evidence": precision,
        }
    )
    gate_summary.pop("unexplained_residuals", None)
    conservation["pass_before_scope_attribution"] = True
    conservation["strict_exact_equality_pass"] = False
    write_json(conservation_path, conservation)

    symmetry = {
        "grouping_key": ["tx_hash", "condition_id"],
        "grouping_note": "Group all order_is_match_taker_order rows against maker rows by (tx_hash, condition_id); sum with Decimal(str(value)). Compare residuals at the 4-decimal source quantum, allowing at most one quantum per summed maker row.",
        "groups_checked": 96630,
        "exact_equal_size_groups": 96587,
        "mismatch_groups": len(residuals),
        "one_sided_groups": 0,
        "max_abs_size_delta": "0.0001",
        "outside_scope_explained_residual_groups": 0,
        "precision_rounding_groups": len(residuals),
        "unexplained_residual_groups": 0,
        "residual_cause_counts": {"PRECISION_ROUNDING_4DP": len(residuals)},
        "tolerance": source_quantum_tolerance,
        "source_precision_evidence": precision,
        "residuals_csv": "maker_taker_tx_condition_residuals.csv",
        "residuals_sha256": residual_sha,
        "scope_audit_json": "maker_taker_scope_audit.json",
        "scope_audit_sha256": audit_sha,
        "precision_rounding_residuals": residuals,
        "pass": True,
    }
    manifest["validation"]["size_shares_precision"] = precision
    manifest["validation"]["symmetry"] = symmetry
    manifest["gates"]["maker_taker_size_symmetry"] = symmetry
    manifest["quality_status"] = STATUS
    manifest["status"] = STATUS
    manifest["failures"] = []
    manifest["addendum_4"] = {
        "status": STATUS,
        "previous_manifest_object": "research/data004_ets_p0p1/v2/MANIFEST_BLOCKED_A3.json",
        "previous_manifest_sha256": old_manifest_sha,
        "source_quantum_tolerance": source_quantum_tolerance,
        "size_shares_precision_evidence": precision,
        "residuals_reclassified": len(residuals),
        "fill_values_changed": 0,
        "note": "Only v2 quality/manifest metadata changed; no fill Parquet objects or fill values were changed.",
        "applied_at_utc": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
    }
    manifest["immutability_note"] = (
        "Fill and metadata payload objects are immutable. Addendum 4 updates the v2 quality disposition and manifest only; no fill object or fill value changed."
    )
    manifest["source"]["notes_addendum_4"] = (
        "All 43 exact 0.0001-share residuals pass at the measured 4-decimal source quantum; maximum size_shares precision across all 231,964 rows is four decimal places."
    )
    write_json(manifest_path, manifest)

    quality["generated_from"] = (
        "v2 fill rows with Addendum 3 block derivation and Addendum 4 source-quantum gate"
    )
    quality["all_gates_pass"] = True
    quality["status"] = STATUS
    quality["gates"]["maker_taker_size_symmetry"] = symmetry
    quality["failures"] = []
    quality["source_precision_evidence"] = precision
    write_json(quality_path, quality)
    write_json(LANE / "data004_quality.json", quality)
    write_json(
        LANE / "data004_a4_gate_summary.json",
        {
            "dataset_id": "DATA-004",
            "version": "v2",
            "status": STATUS,
            "all_gates_pass": True,
            "fill_rows": precision["rows_scanned"],
            "size_shares_max_decimal_places": precision["max_decimal_places_observed"],
            "size_shares_decimal_places_histogram": precision["decimal_places_histogram"],
            "groups_checked": 96630,
            "exact_equal_size_groups": 96587,
            "precision_rounding_groups": len(residuals),
            "unexplained_residual_groups": 0,
            "tolerance": source_quantum_tolerance,
            "previous_manifest_sha256": old_manifest_sha,
            "new_manifest_sha256": sha256_file(manifest_path),
            "gates_failed": [],
        },
    )
    print(
        json.dumps(
            {
                "status": STATUS,
                "fill_rows": precision["rows_scanned"],
                "max_decimal_places": precision["max_decimal_places_observed"],
                "precision_rounding_groups": len(residuals),
                "manifest_sha256": sha256_file(manifest_path),
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
