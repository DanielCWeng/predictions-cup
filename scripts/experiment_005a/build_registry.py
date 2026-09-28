#!/usr/bin/env python3
"""Build deterministic 005A condition and relationship registries from frozen upstream metadata."""

from __future__ import annotations

import csv
import hashlib
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from predictions_cup.learning.fee_role_registry import (
    family_pairs,
    load_conditions,
    load_semantic_relationships,
)

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "data/experiments/experiment_005a/registry"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fields = list(rows[0]) if rows else ["status"]
    output = rows or [{"status": "EMPTY"}]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(output)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    usability = ROOT / "data/experiments/experiment_004a2/condition_usability_matrix.csv"
    inventory = ROOT / "data/experiments/experiment_003/relationship_inventory.csv"

    conditions = load_conditions(usability)
    same_family = family_pairs(conditions)
    semantic = load_semantic_relationships(inventory, conditions)

    write_csv(OUT / "conditions.csv", [asdict(row) for row in conditions])
    write_csv(OUT / "same_family_pairs.csv", [asdict(row) for row in same_family])
    write_csv(OUT / "semantic_pairs.csv", [asdict(row) for row in semantic])

    manifest: dict[str, Any] = {
        "experiment_id": "EXPERIMENT-005A",
        "selection_rule": "FROZEN_METADATA_ONLY_NO_005A_PERFORMANCE",
        "inputs": {
            str(usability.relative_to(ROOT)): sha256(usability),
            str(inventory.relative_to(ROOT)): sha256(inventory),
        },
        "counts": {
            "condition_regime_rows": len(conditions),
            "same_family_directed_pairs": len(same_family),
            "semantic_directed_pairs": len(semantic),
        },
        "outputs": {},
    }
    for path in sorted(OUT.glob("*.csv")):
        manifest["outputs"][path.name] = {
            "sha256": sha256(path),
            "bytes": path.stat().st_size,
        }
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    print(json.dumps(manifest["counts"], sort_keys=True))


if __name__ == "__main__":
    main()
