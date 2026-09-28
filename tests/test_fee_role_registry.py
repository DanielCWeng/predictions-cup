from pathlib import Path

from predictions_cup.learning.fee_role_registry import (
    family_pairs,
    load_conditions,
    load_semantic_relationships,
    participant_stratum,
)


def test_registry_builds_only_frozen_usable_regime_rows(tmp_path: Path) -> None:
    usable = tmp_path / "usable.csv"
    usable.write_text(
        "regime_id,event_family,condition_id,market_family,question,canonical_token_id,"
        "pre_election_usable,active_results_usable\n"
        "e,F,c1,M,Q1,t1,true,false\n"
        "e,F,c2,M,Q2,t2,true,true\n"
        "e,F,c3,N,Q3,t3,false,true\n"
    )
    rows = load_conditions(usable)
    assert [(r.condition_id, r.regime) for r in rows] == [
        ("c1", "PRE_ELECTION"),
        ("c2", "PRE_ELECTION"),
        ("c2", "ACTIVE_RESULTS"),
        ("c3", "ACTIVE_RESULTS"),
    ]


def test_family_pairs_are_complete_directed_and_not_performance_selected(tmp_path: Path) -> None:
    usable = tmp_path / "usable.csv"
    usable.write_text(
        "regime_id,event_family,condition_id,market_family,question,canonical_token_id,"
        "pre_election_usable,active_results_usable\n"
        "e,F,c1,M,Q1,t1,true,false\n"
        "e,F,c2,M,Q2,t2,true,false\n"
        "e,F,c3,M,Q3,t3,true,false\n"
    )
    pairs = family_pairs(load_conditions(usable))
    assert len(pairs) == 6
    assert {(p.source_condition_id, p.target_condition_id) for p in pairs} == {
        ("c1", "c2"), ("c1", "c3"), ("c2", "c1"),
        ("c2", "c3"), ("c3", "c1"), ("c3", "c2"),
    }


def test_semantic_inventory_requires_both_ends_usable(tmp_path: Path) -> None:
    usable = tmp_path / "usable.csv"
    usable.write_text(
        "regime_id,event_family,condition_id,market_family,question,canonical_token_id,"
        "pre_election_usable,active_results_usable\n"
        "e,F,c1,M,Q1,t1,true,false\n"
        "e,F,c2,N,Q2,t2,true,true\n"
    )
    inventory = tmp_path / "relationships.csv"
    inventory.write_text(
        "regime_id,target_condition_id,target_token_id,target_market_family,"
        "reference_condition_id,reference_token_id,reference_market_family,"
        "relationship_type,direction,leakage_class,semantic_basis,eligible_families\n"
        "e,c2,t2,N,c1,t1,M,MANUAL_INDIRECT,REFERENCE_TO_TARGET,INDIRECT,basis,LEADLAG\n"
    )
    edges = load_semantic_relationships(inventory, load_conditions(usable))
    assert len(edges) == 1
    assert edges[0].regime == "PRE_ELECTION"
    assert edges[0].source_condition_id == "c1"
    assert edges[0].target_condition_id == "c2"


def test_participant_stratum_is_explicit_and_stable() -> None:
    assert participant_stratum(
        event="e",
        regime="PRE_ELECTION",
        market_family="M",
        time_block=7,
        role="TAKER",
    ) == "e|PRE_ELECTION|M|7|TAKER"
