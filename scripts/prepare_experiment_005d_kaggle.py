"""Build the self-contained Kaggle runner for EXPERIMENT-005D.

Only semantic/topology inputs permitted by the 005 prior-work boundary are embedded.
Detailed prior empirical results are never read by this script.
"""
from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
EXP = ROOT / "data/experiments/experiment_005d"
KERNEL = ROOT / "scripts/kaggle/experiment_005d"
PREREG = EXP / "preregistration.json"
PRE_HOLDOUT = EXP / "PRE_HOLDOUT_FREEZE.json"
FAMILY_REGISTRY = ROOT / "data/experiments/experiment_004c_b/family_registry.json"
RELATIONSHIPS = ROOT / "data/experiments/experiment_003/relationship_inventory.csv"
HISTORICAL_CATALOGUE = ROOT / "data/research/historical_elections/market_catalogue.csv"
DATA1_MANIFEST = ROOT / "data/manifests/historical/data_001_corpus_manifest.json"
DATA1_IDENTITY = ROOT / "data/manifests/historical/data_001_market_identity.csv"
DATA2_MANIFEST = ROOT / "data/manifests/fees/data_002_manifest.json"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sanitized_families() -> list[dict[str, Any]]:
    data = json.loads(FAMILY_REGISTRY.read_text(encoding="utf-8"))
    keep_family = (
        "family_id",
        "relation_type",
        "formula",
        "mechanically_enforceable",
        "complete",
        "rule_summary",
        "rule_url",
        "settlement_compatibility",
        "validation_status",
        "permitted_transformation",
        "members",
    )
    keep_member = (
        "condition_id",
        "canonical_token_id",
        "canonical_outcome",
        "exact_outcome_semantics",
        "market_family",
        "market_id",
        "member_market_ids",
        "present_in_events",
        "source_event_id",
    )
    out: list[dict[str, Any]] = []
    for family in data.get("families", []):
        row = {key: family.get(key) for key in keep_family if key != "members"}
        row["members"] = [
            {key: member.get(key) for key in keep_member}
            for member in family.get("members", [])
        ]
        out.append(row)
    return out


def csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def main() -> None:
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    phase = "HOLDOUT" if PRE_HOLDOUT.exists() else "TRAIN_DEV"
    pre_holdout = (
        json.loads(PRE_HOLDOUT.read_text(encoding="utf-8"))
        if PRE_HOLDOUT.exists()
        else None
    )
    historical = [
        row
        for row in csv_rows(HISTORICAL_CATALOGUE)
        if row.get("election_id") in {"USA_2024_GENERAL", "CAN_2025_FEDERAL"}
    ]
    embedded = {
        "phase": phase,
        "preregistration": prereg,
        "preregistration_sha256": sha(PREREG),
        "pre_holdout_freeze": pre_holdout,
        "families": sanitized_families(),
        "relationships": csv_rows(RELATIONSHIPS),
        "historical_catalogue": historical,
        "expected_hashes": {
            "data_001_manifest": sha(DATA1_MANIFEST),
            "data_001_identity": sha(DATA1_IDENTITY),
            "data_002_manifest": sha(DATA2_MANIFEST),
            "relationship_inventory": sha(RELATIONSHIPS),
            "family_registry": sha(FAMILY_REGISTRY),
        },
        "data2_manifest_payload": json.loads(
            DATA2_MANIFEST.read_text(encoding="utf-8")
        ),
        "source_hashes": {
            "historical_catalogue": sha(HISTORICAL_CATALOGUE),
            "run_body": sha(KERNEL / "run_body.py.txt"),
        },
    }
    body = (KERNEL / "run_body.py.txt").read_text(encoding="utf-8")
    future = "from __future__ import annotations\n"
    if not body.startswith(future):
        raise SystemExit("run_body.py.txt must begin with future import")
    generated = future + "EMBEDDED=" + repr(embedded) + "\n" + body[len(future):]
    run_path = KERNEL / "run.py"
    run_path.write_text(generated, encoding="utf-8")
    print(
        json.dumps(
            {
                "phase": phase,
                "run_sha256": sha(run_path),
                "run_bytes": run_path.stat().st_size,
                "preregistration_sha256": sha(PREREG),
                "family_count": len(embedded["families"]),
                "relationship_count": len(embedded["relationships"]),
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
