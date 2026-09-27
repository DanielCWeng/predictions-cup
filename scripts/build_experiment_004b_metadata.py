"""Build discovery-only market metadata and audited semantic graph for EXPERIMENT-004B."""

from __future__ import annotations

import csv
from itertools import combinations
from pathlib import Path

from predictions_cup.learning.election_market_structure import DISCOVERY_EVENTS, PRIMARY_REGIMES

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data/experiments/experiment_004b"
COND = ROOT / "data/experiments/experiment_004a2/condition_usability_matrix.csv"
REL = ROOT / "data/experiments/experiment_003/relationship_inventory.csv"
with COND.open(newline="", encoding="utf-8") as h:
    conditions = list(csv.DictReader(h))
with REL.open(newline="", encoding="utf-8") as h:
    relationships = list(csv.DictReader(h))
field_by_regime = {"PRE_ELECTION": "pre_election_usable", "ACTIVE_RESULTS": "active_results_usable"}
market_rows = []
eligible = {}
for event in DISCOVERY_EVENTS:
    event_rows = [r for r in conditions if r["regime_id"] == event]
    for regime in PRIMARY_REGIMES:
        field = field_by_regime[regime]
        admitted = []
        for r in event_rows:
            exact_yes = r["canonical_outcome"] == "Yes" and bool(r["canonical_token_id"])
            usable = exact_yes and r[field] == "true"
            reason = (
                ""
                if usable
                else (
                    "CANONICAL_YES_FAILED_CLOSED"
                    if not exact_yes
                    else f"{regime}_NOT_ASSESSED_USABLE"
                )
            )
            row = {
                "event": event,
                "event_family": r["event_family"],
                "regime": regime,
                "condition_id": r["condition_id"],
                "market_id": r["market_id"],
                "market_family": r["market_family"],
                "question": r["question"],
                "canonical_token_id": r["canonical_token_id"],
                "canonical_outcome": r["canonical_outcome"],
                "admitted": "true" if usable else "false",
                "exclusion_reason": reason,
            }
            market_rows.append(row)
            if usable:
                admitted.append(row)
        eligible[(event, regime)] = admitted
fields = list(market_rows[0])
with (OUT / "market_universe.csv").open("w", newline="", encoding="utf-8") as h:
    w = csv.DictWriter(h, fieldnames=fields, lineterminator="\n")
    w.writeheader()
    w.writerows(market_rows)
# Accepted EXP-003 manual relationships are metadata-only and treated as semantic, never mechanical.
rel_keys = {}
for r in relationships:
    if r["regime_id"] not in DISCOVERY_EVENTS:
        continue
    key = (r["regime_id"], frozenset((r["target_condition_id"], r["reference_condition_id"])))
    rel_keys.setdefault(key, []).append(r)
edge_rows = []
for (event, regime), rows in eligible.items():
    by = {r["condition_id"]: r for r in rows}
    for a, b in combinations(sorted(by), 2):
        related = rel_keys.get((event, frozenset((a, b))), [])
        semantic = " | ".join(sorted({r["semantic_basis"] for r in related}))
        edge_rows.append(
            {
                "event": event,
                "regime": regime,
                "condition_i": a,
                "condition_j": b,
                "token_i": by[a]["canonical_token_id"],
                "token_j": by[b]["canonical_token_id"],
                "market_family_i": by[a]["market_family"],
                "market_family_j": by[b]["market_family"],
                "relation_type": "RELATED_NON_MECHANICAL" if related else "UNVERIFIED",
                "verification_status": "SEMANTIC_BUT_NON_MECHANICAL" if related else "UNVERIFIED",
                "hard_probability_identity": "false",
                "semantic_basis": semantic,
            }
        )
edge_fields = list(edge_rows[0]) if edge_rows else ["event", "regime", "condition_i", "condition_j"]
with (OUT / "structural_edges.csv").open("w", newline="", encoding="utf-8") as h:
    w = csv.DictWriter(h, fieldnames=edge_fields, lineterminator="\n")
    w.writeheader()
    w.writerows(edge_rows)
print(
    "market_rows",
    len(market_rows),
    "admitted",
    sum(r["admitted"] == "true" for r in market_rows),
    "edges",
    len(edge_rows),
    "semantic",
    sum(r.get("verification_status") == "SEMANTIC_BUT_NON_MECHANICAL" for r in edge_rows),
)
