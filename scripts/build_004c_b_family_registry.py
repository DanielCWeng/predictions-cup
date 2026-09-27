"""Build the outcome-independent EXPERIMENT-004C-B economic family registry."""
# ruff: noqa: E501
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
MATRIX = ROOT / "data/experiments/experiment_004a2/condition_usability_matrix.csv"
BASE_SHA = "4e094d13b06d9e558cbc404cc4d472316d7d5bbb"
EXPECTED_IDENTITY_SHA256 = "e89fe8e25dcc494dad3ed96fe6672ab9f247c01eb70d4c0270a913f18a200a72"
PRIMARY_REGIMES = ("PRE_ELECTION", "ACTIVE_RESULTS")


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))

HARD_SPECS: tuple[dict[str, Any], ...] = (
    {
        "family_id": "PER_PRESIDENTIAL_WINNER_106520",
        "event_id": "106520",
        "relation_type": "EXHAUSTIVE_PARTITION",
        "formula": "sum_i p_i = 1",
        "permitted_transformation": "UNWEIGHTED_L2_SIMPLEX_PROJECTION",
        "rule_url": "https://polymarket.com/event/peru-presidential-election-winner",
        "rule_summary": "Listed winner outcomes plus an Other/another-candidate outcome; same election and resolution source.",
    },
    {
        "family_id": "COL_PRESIDENTIAL_WINNER_34584",
        "event_id": "34584",
        "relation_type": "EXHAUSTIVE_PARTITION",
        "formula": "sum_i p_i = 1",
        "permitted_transformation": "UNWEIGHTED_L2_SIMPLEX_PROJECTION",
        "rule_url": "https://polymarket.com/event/colombia-presidential-election",
        "rule_summary": "Listed winner outcomes plus Other; potential runoff included; unresolved-by-deadline maps to Other.",
    },
    {
        "family_id": "COL_RUNOFF_PAIR_481843",
        "event_id": "481843",
        "relation_type": "EXHAUSTIVE_PARTITION",
        "formula": "sum_i p_i = 1",
        "permitted_transformation": "UNWEIGHTED_L2_SIMPLEX_PROJECTION",
        "rule_url": "https://polymarket.com/event/colombia-election-who-will-advance-to-2nd-round",
        "rule_summary": "Three named runoff pairs, Other, and 1st Round Outright Winner exhaust the first-round terminal states.",
    },
    {
        "family_id": "HUN_TISZA_SEAT_BUCKETS_263567",
        "event_id": "263567",
        "relation_type": "EXHAUSTIVE_PARTITION",
        "formula": "sum_i p_i = 1",
        "permitted_transformation": "UNWEIGHTED_L2_SIMPLEX_PROJECTION",
        "rule_url": "https://polymarket.com/event/of-seats-won-by-tisza-in-hungary-parliamentary-election",
        "rule_summary": "Integer seat-count brackets <70 through 130+ cover every possible TISZA seat count.",
    },
    {
        "family_id": "HUN_FIDESZ_SEAT_BUCKETS_263762",
        "event_id": "263762",
        "relation_type": "EXHAUSTIVE_PARTITION",
        "formula": "sum_i p_i = 1",
        "permitted_transformation": "UNWEIGHTED_L2_SIMPLEX_PROJECTION",
        "rule_url": "https://polymarket.com/event/of-seats-won-by-fidesz-kdnp-in-hungary-parliamentary-election",
        "rule_summary": "Integer seat-count brackets <70 through 130+ cover every possible Fidesz-KDNP seat count.",
    },
    {
        "family_id": "HUN_TISZA_VOTE_BUCKETS_266168",
        "event_id": "266168",
        "relation_type": "EXHAUSTIVE_PARTITION",
        "formula": "sum_i p_i = 1",
        "permitted_transformation": "UNWEIGHTED_L2_SIMPLEX_PROJECTION",
        "rule_url": "https://polymarket.com/event/hungary-election-tisza-of-popular-vote",
        "rule_summary": "Five national-list-vote brackets cover all shares; exact boundaries resolve to the higher bracket.",
    },
    {
        "family_id": "HUN_FIDESZ_VOTE_BUCKETS_266119",
        "event_id": "266119",
        "relation_type": "EXHAUSTIVE_PARTITION",
        "formula": "sum_i p_i = 1",
        "permitted_transformation": "UNWEIGHTED_L2_SIMPLEX_PROJECTION",
        "rule_url": "https://polymarket.com/event/hungary-election-fidesz-kdnp-of-popular-vote",
        "rule_summary": "Five national-list-vote brackets cover all shares; exact boundaries resolve to the higher bracket.",
    },
    {
        "family_id": "HUN_TISZA_AT_LEAST_SEATS_266366",
        "event_id": "266366",
        "relation_type": "CONDITIONAL_STAGED",
        "formula": "for thresholds a<b: P(seats>=b) <= P(seats>=a)",
        "permitted_transformation": "UNWEIGHTED_L2_NONINCREASING_ISOTONIC_PROJECTION",
        "rule_url": "https://polymarket.com/event/hungary-election-tisza-wins-at-least-seats",
        "rule_summary": "Every contract resolves from the same TISZA seat count using nested at-least thresholds.",
    },
    {
        "family_id": "HUN_FIDESZ_AT_LEAST_SEATS_266367",
        "event_id": "266367",
        "relation_type": "CONDITIONAL_STAGED",
        "formula": "for thresholds a<b: P(seats>=b) <= P(seats>=a)",
        "permitted_transformation": "UNWEIGHTED_L2_NONINCREASING_ISOTONIC_PROJECTION",
        "rule_url": "https://polymarket.com/event/hungary-election-fidesz-kdnp-wins-seats",
        "rule_summary": "Every contract resolves from the same Fidesz-KDNP seat count using nested at-least thresholds.",
    },
)

SOFT_SPECS = (
    ("PER_SENATE_MOST_SEATS_106510", "106510", "same-election Senate most-seats competitors; tie semantics not promoted to identity"),
    ("PER_HOUSE_MOST_SEATS_106511", "106511", "same-election Chamber most-seats competitors; tie semantics not promoted to identity"),
    ("HUN_MOST_SEATS_106614", "106614", "same-election most-seats competitors; tie semantics not promoted to identity"),
    ("HUN_SECOND_MOST_SEATS_291004", "291004", "same-election second-place competitors; tie semantics not promoted to identity"),
    ("HUN_THIRD_MOST_SEATS_291025", "291025", "same-election third-place competitors; tie semantics not promoted to identity"),
    ("COL_FIRST_ROUND_WINNER_34582", "34582", "same-first-round winner competitors; hard exhaustiveness not separately proven"),
    ("HUN_LIST_VOTE_WINNER_246787", "246787", "same-election national-list-vote competitors; tie semantics not promoted to identity"),
)


def canonical_members(identity: list[dict[str, str]], event_id: str) -> list[dict[str, Any]]:
    by_condition: dict[str, dict[str, str]] = {}
    regimes: defaultdict[str, set[str]] = defaultdict(set)
    for row in identity:
        if row.get("event_id") != event_id:
            continue
        regimes[row["condition_id"]].add(row["regime_id"])
        if row.get("outcome") == "Yes":
            by_condition[row["condition_id"]] = row
    members = []
    for cid, row in sorted(by_condition.items()):
        members.append({
            "condition_id": cid,
            "market_id": row["market_id"],
            "member_market_ids": [row["market_id"]],
            "canonical_token_id": row["token_id"],
            "exact_outcome_semantics": row["question"],
            "canonical_outcome": "Yes",
            "market_family": row["market_family"],
            "source_event_id": event_id,
            "present_in_events": sorted(regimes[cid]),
        })
    return members

def scope_rows(members, matrix, hard_type):
    out = []
    events = sorted({e for member in members for e in member["present_in_events"]})
    for event in events:
        for regime in PRIMARY_REGIMES:
            field = "pre_election_usable" if regime == "PRE_ELECTION" else "active_results_usable"
            usable = [m["condition_id"] for m in members if matrix.get((event, m["condition_id"]), {}).get(field) == "true"]
            if hard_type == "EXHAUSTIVE_PARTITION" and len(usable) == len(members) and usable:
                relation, formula = "EXHAUSTIVE_PARTITION", "sum_i p_i = 1"
            elif hard_type == "EXHAUSTIVE_PARTITION" and len(usable) >= 2:
                relation, formula = "MUTUALLY_EXCLUSIVE_NONEXHAUSTIVE", "sum_i p_i <= 1"
            elif hard_type == "CONDITIONAL_STAGED" and len(usable) >= 2:
                relation = "CONDITIONAL_STAGED"
                formula = "higher threshold probability cannot exceed lower threshold probability"
            else:
                relation, formula = "UNSUPPORTED", None
            out.append({
                "event": event,
                "regime": regime,
                "usable_member_count": len(usable),
                "total_member_count": len(members),
                "usable_condition_ids": usable,
                "empirical_relation_type": relation,
                "empirical_formula": formula,
                "empirically_complete": len(usable) == len(members) and bool(members),
                "eligible_for_hard_test": relation != "UNSUPPORTED",
            })
    return out


def soft_scope_rows(members, matrix):
    out = []
    events = sorted({e for member in members for e in member["present_in_events"]})
    for event in events:
        for regime in PRIMARY_REGIMES:
            field = "pre_election_usable" if regime == "PRE_ELECTION" else "active_results_usable"
            usable = [m["condition_id"] for m in members if matrix.get((event, m["condition_id"]), {}).get(field) == "true"]
            out.append({
                "event": event, "regime": regime, "usable_member_count": len(usable),
                "usable_condition_ids": usable, "eligible_for_soft_test": len(usable) >= 3,
            })
    return out

def build(identity_path: Path) -> dict[str, Any]:
    if sha256(identity_path) != EXPECTED_IDENTITY_SHA256:
        raise SystemExit("accepted DATA-001 identity hash mismatch")
    identity = read_csv(identity_path)
    matrix_rows = read_csv(MATRIX)
    matrix = {(r["regime_id"], r["condition_id"]): r for r in matrix_rows}
    families = []
    for spec in HARD_SPECS:
        members = canonical_members(identity, spec["event_id"])
        raw_conditions = {r["condition_id"] for r in identity if r.get("event_id") == spec["event_id"]}
        exact_yes = {m["condition_id"] for m in members}
        label_complete = raw_conditions == exact_yes
        family = dict(spec)
        family.update({
            "mechanically_enforceable": True,
            "complete": spec["relation_type"] == "EXHAUSTIVE_PARTITION",
            "settlement_compatibility": "SAME_POLYMARKET_EVENT_SHARED_RULES",
            "source_provenance": ["DATA-001 accepted market_identity.csv", spec["rule_url"]],
            "validation_status": "MECHANICALLY_PROVEN" if label_complete else "UNSUPPORTED",
            "canonical_label_complete": label_complete,
            "member_count": len(members),
            "members": members,
            "evidence_scopes": scope_rows(members, matrix, spec["relation_type"]) if label_complete else [],
        })
        families.append(family)
    for family_id, event_id, basis in SOFT_SPECS:
        members = canonical_members(identity, event_id)
        families.append({
            "family_id": family_id, "event_id": event_id,
            "relation_type": "SEMANTIC_COMPETITION_ONLY", "mechanically_enforceable": False,
            "complete": False, "settlement_compatibility": "NOT_PROVEN_FOR_HARD_IDENTITY",
            "permitted_transformation": "LEAVE_TARGET_OUT_EQUAL_WEIGHT_SIBLING_SHOCK",
            "source_provenance": ["DATA-001 accepted market_identity.csv", "event grouping and contract wording"],
            "validation_status": "SEMANTIC_ONLY", "semantic_basis": basis,
            "member_count": len(members), "members": members,
            "evidence_scopes": soft_scope_rows(members, matrix),
        })
    families.append({
        "family_id": "STAGED_FIRSTROUND_TO_OVERALL_UNPROVEN",
        "event_id": None,
        "relation_type": "UNSUPPORTED",
        "mechanically_enforceable": False,
        "complete": False,
        "settlement_compatibility": "INSUFFICIENT_DIRECT_CONDITIONAL_CONTRACTS",
        "permitted_transformation": None,
        "source_provenance": ["EXPERIMENT-003 relationship inventory", "Opus 004C staged hypothesis"],
        "validation_status": "UNSUPPORTED",
        "semantic_basis": "First-round, runoff-pair and overall winner markets are related, but no exact candidate-level conditional identity is available without estimated conditionals.",
        "member_count": 0,
        "members": [],
        "evidence_scopes": [],
    })
    return {
        "experiment_id": "EXPERIMENT-004C-B",
        "registry_version": "004c-b-family-registry-v1",
        "base_sha": BASE_SHA,
        "construction_policy": "contract wording + settlement rules + frozen identity metadata only; no price-behaviour inference",
        "accepted_identity_sha256": EXPECTED_IDENTITY_SHA256,
        "condition_universe_sha256": "f3a8aa611944f16514e1668f9023361c8eab90c336dcf330648d4f502153a892",
        "validation_protocol_sha256": "052eefc3ad242ffe5499956da9e5ff05d7a9a3fcd01da3522be82be2cfc34a41",
        "families": families,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--identity", type=Path, required=True)
    ap.add_argument("--output", type=Path, default=ROOT / "data/experiments/experiment_004c_b/family_registry.json")
    args = ap.parse_args()
    payload = build(args.identity)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"families": len(payload["families"]), "sha256": sha256(args.output)}, sort_keys=True))


if __name__ == "__main__":
    main()
