#!/usr/bin/env python3
"""Fail-closed preflight for EXPERIMENT-005A."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
BASE_SHA = "69cb1924751515a495bf99556819147ad090d67d"
GATE_PATH = ROOT / "data/experiments/experiment_005a/empirical_gate.json"
EXPECTED_SHA256 = {
    "data/manifests/fees/data_002_manifest.json":
        "3bcb544fdcf3479f5e8a6973906c8ccfd5b9abd77592629daa70facfdfdd6d5c",
    "data/manifests/fees/data_002_quality.json":
        "fc9fae5000c680d8e8aec649fdfa767203f68bcd3f055998ff9b9faa2fc09ba9",
    "data/manifests/fees/data_002_fee_regimes.csv":
        "8ded772f413585914e18a4e34e778f7da7fe55b2647f3799a6e5312abc6a1d5b",
    "data/manifests/historical/data_001_corpus_manifest.json":
        "e3d95735262b0be3e9fe7dd32fb54e53dc7e65b403ef9b5a2261ac107a8ea4c4",
    "data/reference/polymarket_infrastructure/infra_registry.json":
        "839f62b62c6b2e3d3b7a44d200c2d4b949c35de8e7dea006e9a2f2c8426959e8",
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )


def _load_json(path: Path) -> dict[str, Any]:
    raw = json.loads(path.read_text())
    if not isinstance(raw, dict):
        raise RuntimeError(f"{path} must contain a JSON object")
    return raw


def validate_provenance() -> None:
    ancestry = _git("merge-base", "--is-ancestor", BASE_SHA, "HEAD")
    if ancestry.returncode != 0:
        raise RuntimeError(f"005A HEAD is not descended from frozen base {BASE_SHA}")
    for relative, expected in EXPECTED_SHA256.items():
        actual = _sha256(ROOT / relative)
        if actual != expected:
            raise RuntimeError(f"hash mismatch for {relative}: {actual} != {expected}")

    data002 = _load_json(ROOT / "data/manifests/fees/data_002_manifest.json")
    if data002.get("source_commit") is not None or data002.get("pipeline_commit") is not None:
        raise RuntimeError("DATA-002 source/pipeline provenance changed unexpectedly")


def validate_empirical_gate() -> None:
    if not GATE_PATH.exists():
        raise RuntimeError(
            "EMPIRICAL BLOCKED: empirical_gate.json absent; full EXPERIMENT-004C freeze not proven"
        )
    gate = _load_json(GATE_PATH)
    required_true = ("data_002_merged", "experiment_004c_fully_frozen", "master_authorized")
    failed = [field for field in required_true if gate.get(field) is not True]
    if failed:
        raise RuntimeError(f"EMPIRICAL BLOCKED: false/missing gate fields: {failed}")
    if gate.get("data_002_main_sha") != BASE_SHA:
        raise RuntimeError("EMPIRICAL BLOCKED: DATA-002 canonical main SHA mismatch")
    if not gate.get("experiment_004c_freeze_sha"):
        raise RuntimeError("EMPIRICAL BLOCKED: experiment_004c_freeze_sha missing")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--empirical", action="store_true")
    args = parser.parse_args()

    validate_provenance()
    if args.empirical:
        validate_empirical_gate()
    print("005A preflight: provenance PASS")
    print(f"base_sha={BASE_SHA}")
    print(f"empirical={'AUTHORIZED' if args.empirical else 'NOT_REQUESTED'}")


if __name__ == "__main__":
    main()
