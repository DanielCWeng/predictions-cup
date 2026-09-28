#!/usr/bin/env python3
"""Fail-closed verifier for the canonical EXPERIMENT-005A result package."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path
from typing import Any

EXPECTED_OBSERVED_IMPLEMENTATION = "a0217eed5f775735d5ad01edf3141963be7b1787"
EXPECTED_004C_FREEZE = "ba938bedcf63f562be8b26c9502e828391123867"

OBSERVED_STAGES = {
    "a23": "A2_A3_OBSERVED_AND_PANELS",
    "a4": "A4_PARTICIPANT_OBSERVED_AND_PANELS",
    "a5": "A5_MAKER_LIQUIDITY_OBSERVED_AND_PANELS",
}
NULL_STAGES = {
    "n23": "N23_FLOW_NULLS",
    "n4": "N4_PARTICIPANT_IDENTITY_NULL",
    "n5": "N5_MAKER_LIQUIDITY_NULLS",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    raw = json.loads(path.read_text())
    if not isinstance(raw, dict):
        raise RuntimeError(f"{path} is not a JSON object")
    return raw


def find_stage(root: Path, stage: str) -> tuple[Path, dict[str, Any]]:
    hits: list[tuple[Path, dict[str, Any]]] = []
    for path in sorted(root.rglob("run_manifest.json")):
        payload = load_json(path)
        if payload.get("stage") == stage:
            hits.append((path, payload))
    if len(hits) != 1:
        raise RuntimeError(f"{root}: expected one {stage} manifest, found {len(hits)}")
    return hits[0]


def verify_output_map(manifest_path: Path, outputs: dict[str, Any]) -> None:
    root = manifest_path.parent
    for relative, spec in outputs.items():
        path = root / relative
        if not path.is_file():
            raise RuntimeError(f"{manifest_path}: missing output {relative}")
        if isinstance(spec, dict):
            expected_hash = spec.get("sha256")
            expected_bytes = spec.get("bytes")
            if expected_bytes is not None and path.stat().st_size != int(expected_bytes):
                raise RuntimeError(f"{manifest_path}: size mismatch {relative}")
        else:
            expected_hash = spec
        if sha256(path) != expected_hash:
            raise RuntimeError(f"{manifest_path}: hash mismatch {relative}")


def verify_observed(
    root: Path, stage: str
) -> tuple[Path, dict[str, Any]]:
    path, manifest = find_stage(root, stage)
    code = manifest.get("code_manifest", {})
    if code.get("implementation_commit") != EXPECTED_OBSERVED_IMPLEMENTATION:
        raise RuntimeError(f"{stage}: wrong implementation commit")
    gate = manifest.get("gate", {})
    if gate.get("experiment_004c_freeze_sha") != EXPECTED_004C_FREEZE:
        raise RuntimeError(f"{stage}: wrong 004C freeze")

    maps: tuple[str, ...]
    required_csv: tuple[str, ...]
    if stage == "A2_A3_OBSERVED_AND_PANELS":
        maps = ("outputs",)
        required_csv = ("coverage.csv", "observed_incremental_models.csv")
    elif stage == "A4_PARTICIPANT_OBSERVED_AND_PANELS":
        maps = ("panel_outputs",)
        required_csv = ("coverage.csv", "history_audit.csv", "observed_incremental_models.csv")
    elif stage == "A5_MAKER_LIQUIDITY_OBSERVED_AND_PANELS":
        maps = ("maker_panels", "liquidity_panels")
        required_csv = (
            "coverage.csv",
            "maker_observed_models.csv",
            "liquidity_observed_models.csv",
            "liquidity_binary_descriptive.csv",
        )
    else:
        raise RuntimeError(f"unsupported observed stage {stage}")

    for key in maps:
        outputs = manifest.get(key)
        if not isinstance(outputs, dict):
            raise RuntimeError(f"{path}: {key} missing")
        verify_output_map(path, outputs)
    for relative in required_csv:
        if not (path.parent / relative).is_file():
            raise RuntimeError(f"{path}: required summary missing {relative}")
    return path, manifest


def verify_null(
    root: Path, stage: str
) -> tuple[Path, dict[str, Any]]:
    path, manifest = find_stage(root, stage)
    if manifest.get("code_bundle_implementation_commit") != EXPECTED_OBSERVED_IMPLEMENTATION:
        raise RuntimeError(f"{stage}: wrong code-bundle implementation")
    outputs = manifest.get("outputs")
    if not isinstance(outputs, dict):
        raise RuntimeError(f"{path}: outputs missing")
    verify_output_map(path, outputs)
    return path, manifest


def parse_bool(value: str) -> bool:
    lowered = value.strip().lower()
    if lowered in {"true", "1"}:
        return True
    if lowered in {"false", "0"}:
        return False
    raise RuntimeError(f"invalid boolean {value!r}")


def audit_bh(path: Path, *, expected_rows: int | None = None) -> dict[str, Any]:
    with path.open(newline="") as handle:
        rows = list(csv.DictReader(handle))
    if expected_rows is not None and len(rows) != expected_rows:
        raise RuntimeError(
            f"{path}: expected {expected_rows} summary rows, found {len(rows)}"
        )
    rejects = 0
    for row in rows:
        if "bh_q" not in row or "bh_reject_5pct" not in row:
            continue
        q = float(row["bh_q"])
        if not 0.0 <= q <= 1.0:
            raise RuntimeError(f"{path}: invalid BH q={q}")
        for key in ("circular_p", "block_p", "intersection_p", "identity_p"):
            raw = row.get(key)
            if raw not in (None, "", "NONPOSITIVE_STAGE1"):
                p = float(str(raw))
                if not 0.0 <= p <= 1.0:
                    raise RuntimeError(f"{path}: invalid {key}={p}")
        reject = parse_bool(row["bh_reject_5pct"])
        if reject and q > 0.05:
            raise RuntimeError(f"{path}: reject with q={q}")
        rejects += int(reject)
    return {"rows": len(rows), "bh_rejections": rejects, "sha256": sha256(path)}


def main() -> None:
    parser = argparse.ArgumentParser()
    for name in (*OBSERVED_STAGES, *NULL_STAGES):
        parser.add_argument(f"--{name}", type=Path, required=True)
    args = parser.parse_args()

    observed: dict[str, tuple[Path, dict[str, Any]]] = {}
    for name, stage in OBSERVED_STAGES.items():
        observed[name] = verify_observed(getattr(args, name), stage)

    nulls: dict[str, tuple[Path, dict[str, Any]]] = {}
    for name, stage in NULL_STAGES.items():
        nulls[name] = verify_null(getattr(args, name), stage)

    a23_path, _ = observed["a23"]
    a4_path, _ = observed["a4"]
    a5_path, _ = observed["a5"]
    _, n23 = nulls["n23"]
    _, n4 = nulls["n4"]
    _, n5 = nulls["n5"]

    expected_links = {
        "n23_stage1": (n23.get("stage1_manifest_sha256"), sha256(a23_path)),
        "n4_main": (n4.get("main_stage1_manifest_sha256"), sha256(a23_path)),
        "n4_a4": (n4.get("a4_stage1_manifest_sha256"), sha256(a4_path)),
        "n5_stage1": (n5.get("stage1_manifest_sha256"), sha256(a5_path)),
    }
    for label, (actual, expected) in expected_links.items():
        if actual != expected:
            raise RuntimeError(f"{label}: upstream manifest linkage mismatch")

    if n23.get("stage1_implementation_commit") != EXPECTED_OBSERVED_IMPLEMENTATION:
        raise RuntimeError("N23: wrong stage-1 implementation")
    if n4.get("a4_stage1_implementation_commit") != EXPECTED_OBSERVED_IMPLEMENTATION:
        raise RuntimeError("N4: wrong A4 implementation")
    if n5.get("stage1_implementation_commit") != EXPECTED_OBSERVED_IMPLEMENTATION:
        raise RuntimeError("N5: wrong A5 implementation")

    summaries = {
        "n23": audit_bh(
            nulls["n23"][0].parent / "null_summary.csv", expected_rows=30
        ),
        "n4": audit_bh(
            nulls["n4"][0].parent / "participant_null_summary.csv", expected_rows=10
        ),
        "n5_maker": audit_bh(
            nulls["n5"][0].parent / "maker_null_summary.csv", expected_rows=10
        ),
        "n5_liquidity": audit_bh(
            nulls["n5"][0].parent / "liquidity_primary_null_summary.csv",
            expected_rows=30,
        ),
        "n5_capture_placebo": audit_bh(
            nulls["n5"][0].parent / "capture_placebo_null_summary.csv",
            expected_rows=30,
        ),
    }

    result = {
        "experiment_id": "EXPERIMENT-005A",
        "status": "CANONICAL_RESULT_PACKAGE_VERIFIED",
        "observed_implementation_commit": EXPECTED_OBSERVED_IMPLEMENTATION,
        "experiment_004c_freeze_sha": EXPECTED_004C_FREEZE,
        "manifest_sha256": {
            name: sha256(value[0]) for name, value in {**observed, **nulls}.items()
        },
        "observed_summary_sha256": {
            "a23": sha256(observed["a23"][0].parent / "observed_incremental_models.csv"),
            "a4": sha256(observed["a4"][0].parent / "observed_incremental_models.csv"),
            "a5_maker": sha256(observed["a5"][0].parent / "maker_observed_models.csv"),
            "a5_liquidity": sha256(
                observed["a5"][0].parent / "liquidity_observed_models.csv"
            ),
        },
        "null_summaries": summaries,
        "total_primary_bh_rejections": (
            summaries["n23"]["bh_rejections"]
            + summaries["n4"]["bh_rejections"]
            + summaries["n5_maker"]["bh_rejections"]
            + summaries["n5_liquidity"]["bh_rejections"]
        ),
        "capture_placebo_bh_rejections": summaries["n5_capture_placebo"][
            "bh_rejections"
        ],
    }
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
