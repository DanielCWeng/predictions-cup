"""Build the frozen EXPERIMENT-004C-A metadata-only pair and market registries."""

from __future__ import annotations

import csv
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "experiments" / "experiment_004c" / "a"
REL = ROOT / "data" / "experiments" / "experiment_003" / "relationship_inventory.csv"
COND = ROOT / "data" / "experiments" / "experiment_004a2" / "condition_usability_matrix.csv"
B_UNIVERSE = ROOT / "data" / "experiments" / "experiment_004b" / "market_universe.csv"

DISCOVERY = {"hungary_election", "peru_first_round"}
CHALLENGE = {"colombia_first_round", "peru_runoff", "colombia_runoff"}
REGIMES = (
    ("PRE_ELECTION", "pre_election_usable"),
    ("ACTIVE_RESULTS", "active_results_usable"),
)


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: list[dict[str, str]], fields: tuple[str, ...]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


conditions = read_csv(COND)
relationships = read_csv(REL)
b_rows = read_csv(B_UNIVERSE)

condition_by_key = {(r["regime_id"], r["condition_id"]): r for r in conditions}
b_admitted = {
    (r["event"], r["regime"], r["condition_id"])
    for r in b_rows
    if r["admitted"] == "true"
}

universe: list[dict[str, str]] = []
for row in conditions:
    event = row["regime_id"]
    if event not in DISCOVERY | CHALLENGE:
        continue
    for regime, usable_col in REGIMES:
        if row[usable_col] != "true":
            continue
        if row["canonical_outcome"] != "Yes" or not row["canonical_token_id"]:
            continue
        if event in DISCOVERY and (event, regime, row["condition_id"]) not in b_admitted:
            continue
        universe.append(
            {
                "event": event,
                "event_family": row["event_family"],
                "regime": regime,
                "evidence_scope": (
                    "DISCOVERY"
                    if event in DISCOVERY
                    else "004B_SEALED_PRIOR_EXPERIMENT_EXPOSED"
                ),
                "condition_id": row["condition_id"],
                "market_id": row["market_id"],
                "market_family": row["market_family"],
                "canonical_token_id": row["canonical_token_id"],
                "question": row["question"],
            }
        )

universe.sort(key=lambda r: (r["event"], r["regime"], r["condition_id"]))
u_keys = {(r["event"], r["regime"], r["condition_id"]) for r in universe}

pairs: list[dict[str, str]] = []
seen: set[tuple[str, str, str, str]] = set()
for rel in relationships:
    event = rel["regime_id"]
    if event not in DISCOVERY | CHALLENGE:
        continue
    if rel["relationship_type"] != "MANUAL_INDIRECT":
        continue
    if rel["leakage_class"] != "INDIRECT":
        continue
    if "LEADLAG" not in rel["eligible_families"].split(";"):
        continue
    source = condition_by_key.get((event, rel["reference_condition_id"]))
    target = condition_by_key.get((event, rel["target_condition_id"]))
    if source is None or target is None:
        continue
    for regime, _ in REGIMES:
        if (event, regime, source["condition_id"]) not in u_keys:
            continue
        if (event, regime, target["condition_id"]) not in u_keys:
            continue
        key = (event, regime, source["condition_id"], target["condition_id"])
        if key in seen:
            continue
        seen.add(key)
        pairs.append(
            {
                "event": event,
                "event_family": target["event_family"],
                "regime": regime,
                "evidence_scope": (
                    "DISCOVERY"
                    if event in DISCOVERY
                    else "004B_SEALED_PRIOR_EXPERIMENT_EXPOSED"
                ),
                "source_condition_id": source["condition_id"],
                "source_token_id": source["canonical_token_id"],
                "source_market_family": source["market_family"],
                "target_condition_id": target["condition_id"],
                "target_token_id": target["canonical_token_id"],
                "target_market_family": target["market_family"],
                "semantic_basis": rel["semantic_basis"],
            }
        )

pairs.sort(
    key=lambda r: (
        r["event"],
        r["regime"],
        r["source_condition_id"],
        r["target_condition_id"],
    )
)

write_csv(
    OUT / "universe.csv",
    universe,
    (
        "event",
        "event_family",
        "regime",
        "evidence_scope",
        "condition_id",
        "market_id",
        "market_family",
        "canonical_token_id",
        "question",
    ),
)
write_csv(
    OUT / "pair_registry.csv",
    pairs,
    (
        "event",
        "event_family",
        "regime",
        "evidence_scope",
        "source_condition_id",
        "source_token_id",
        "source_market_family",
        "target_condition_id",
        "target_token_id",
        "target_market_family",
        "semantic_basis",
    ),
)

counts: dict[str, int] = {}
for row in pairs:
    key = f'{row["event"]}|{row["regime"]}'
    counts[key] = counts.get(key, 0) + 1

expected = {
    "peru_first_round|PRE_ELECTION": 2,
    "peru_first_round|ACTIVE_RESULTS": 18,
    "colombia_first_round|PRE_ELECTION": 6,
    "colombia_first_round|ACTIVE_RESULTS": 4,
    "peru_runoff|ACTIVE_RESULTS": 2,
    "colombia_runoff|PRE_ELECTION": 2,
    "colombia_runoff|ACTIVE_RESULTS": 4,
}
if counts != expected:
    raise SystemExit(f"frozen pair-registry counts changed: {counts!r}")

print(f"wrote {len(universe)} universe rows and {len(pairs)} source-target rows")
