from __future__ import annotations

from pathlib import Path

import pytest

from predictions_cup.learning.election_market_structure import (
    DISCOVERY_EVENTS,
    EXPECTED_004A2_PROTOCOL_SHA256,
    EXPECTED_004A2_UNIVERSE_SHA256,
    EXPECTED_004A_REGIME_SHA256,
    PRIMARY_REGIMES,
    RESPONSE_HORIZONS_SECONDS,
    SAMPLING_RESOLUTIONS_SECONDS,
    SEALED_EVENTS,
    derived_seed,
    discovery_spec_sha256,
    empirical_event_root,
    frozen_discovery_manifest,
    require_discovery_event,
    verify_upstream,
)

ROOT = Path(__file__).resolve().parents[1]


def test_upstream_hashes_are_frozen() -> None:
    actual = verify_upstream(ROOT)
    assert actual["004a_regime_sha256"] == EXPECTED_004A_REGIME_SHA256
    assert actual["004a2_condition_universe_sha256"] == EXPECTED_004A2_UNIVERSE_SHA256
    assert actual["004a2_validation_protocol_sha256"] == EXPECTED_004A2_PROTOCOL_SHA256


def test_discovery_manifest_is_deterministic() -> None:
    left = frozen_discovery_manifest()
    right = frozen_discovery_manifest()
    assert left == right
    assert left["discovery_spec_sha256"] == discovery_spec_sha256(left)


def test_partition_is_exact_and_fail_closed() -> None:
    assert DISCOVERY_EVENTS == ("hungary_election", "peru_first_round")
    assert SEALED_EVENTS == ("colombia_first_round", "peru_runoff", "colombia_runoff")
    for event in DISCOVERY_EVENTS:
        require_discovery_event(event)
    for event in SEALED_EVENTS:
        with pytest.raises(PermissionError):
            require_discovery_event(event)
        with pytest.raises(PermissionError):
            empirical_event_root(Path("/corpus"), event)


def test_primary_lanes_and_grids_are_fixed() -> None:
    assert PRIMARY_REGIMES == ("PRE_ELECTION", "ACTIVE_RESULTS")
    assert SAMPLING_RESOLUTIONS_SECONDS == (1, 5, 30, 60, 300)
    assert RESPONSE_HORIZONS_SECONDS == (1, 5, 15, 30, 60, 120, 300)
    manifest = frozen_discovery_manifest()
    assert manifest["excluded_regimes"]["ELECTION_DAY_PRE_RESULTS"] == "ELIGIBILITY_NOT_ASSESSED"


def test_null_seeds_are_deterministic_and_component_specific() -> None:
    assert derived_seed("a") == derived_seed("a")
    assert derived_seed("a") != derived_seed("b")


def test_no_aggressor_or_participant_overreach_in_spec() -> None:
    features = frozen_discovery_manifest()["feature_definitions"]
    assert features["source_side_policy"] == "never interpreted as aggressor direction"
    assert features["participant_policy"] == "no participant identity/reputation/skill features"


def test_discovery_market_universe_respects_canonical_yes_and_regime_usability() -> None:
    import csv

    path = ROOT / "data/experiments/experiment_004b/market_universe.csv"
    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    admitted = [row for row in rows if row["admitted"] == "true"]
    assert len(admitted) == 55
    assert all(row["event"] in DISCOVERY_EVENTS for row in admitted)
    assert all(row["regime"] in PRIMARY_REGIMES for row in admitted)
    assert all(row["canonical_outcome"] == "Yes" for row in admitted)
    assert all(row["canonical_token_id"] for row in admitted)
    assert len({(row["event"], row["regime"], row["condition_id"]) for row in admitted}) == 55


def test_pre_and_active_are_distinct_universes() -> None:
    import csv
    from collections import Counter

    with (ROOT / "data/experiments/experiment_004b/market_universe.csv").open(
        newline="", encoding="utf-8"
    ) as handle:
        rows = list(csv.DictReader(handle))
    counts = Counter((row["event"], row["regime"]) for row in rows if row["admitted"] == "true")
    assert counts == {
        ("hungary_election", "PRE_ELECTION"): 2,
        ("hungary_election", "ACTIVE_RESULTS"): 2,
        ("peru_first_round", "PRE_ELECTION"): 11,
        ("peru_first_round", "ACTIVE_RESULTS"): 40,
    }


def test_temporal_grid_and_asof_never_use_future_state() -> None:
    from predictions_cup.learning.election_market_structure import (
        asof_position,
        temporal_bin_times_ns,
    )

    grid = temporal_bin_times_ns(0, 10_000_000_000, 1)
    assert grid[0] == 999_999_999
    assert grid[-1] == 9_999_999_999
    observations = (2_000_000_000, 4_000_000_000, 8_000_000_000)
    assert asof_position(observations, 1_999_999_999) is None
    assert asof_position(observations, 2_000_000_000) == 0
    assert asof_position(observations, 7_000_000_000) == 1


def test_fixed_horizon_support_rule() -> None:
    from predictions_cup.learning.election_market_structure import response_horizon_supported

    assert response_horizon_supported(1, 15)
    assert response_horizon_supported(5, 15)
    assert response_horizon_supported(30, 60)
    assert not response_horizon_supported(30, 15)
    assert not response_horizon_supported(300, 120)


def test_block_permutation_is_deterministic_and_regime_local() -> None:
    from predictions_cup.learning.election_market_structure import block_permutation_indices

    first = block_permutation_indices(17, 4, "event|PRE|NULL_B")
    second = block_permutation_indices(17, 4, "event|PRE|NULL_B")
    assert first == second
    assert sorted(first) == list(range(17))
    assert all(0 <= index < 17 for index in first)


def test_complete_pair_universe_retains_all_cells() -> None:
    from predictions_cup.learning.election_market_structure import pair_cell_keys

    ids = ("c", "a", "b")
    assert pair_cell_keys(ids, directed=False) == (("a", "b"), ("a", "c"), ("b", "c"))
    assert len(pair_cell_keys(ids, directed=True)) == 6


def test_fdr_family_membership_is_deterministic() -> None:
    from predictions_cup.learning.election_market_structure import fdr_family_id

    expected = "peru_first_round|ACTIVE_RESULTS|PRICE_STRUCTURE"
    assert fdr_family_id("peru_first_round", "ACTIVE_RESULTS", "PRICE_STRUCTURE") == expected
    assert fdr_family_id("peru_first_round", "ACTIVE_RESULTS", "PRICE_STRUCTURE") == expected


def test_hard_probability_identity_requires_verified_mechanics() -> None:
    from predictions_cup.learning.election_market_structure import hard_identity_allowed

    assert hard_identity_allowed("MECHANICAL", "EXHAUSTIVE_PARTITION")
    assert not hard_identity_allowed("SEMANTIC_BUT_NON_MECHANICAL", "EXHAUSTIVE_PARTITION")
    assert not hard_identity_allowed("UNVERIFIED", "EXHAUSTIVE_PARTITION")
    assert not hard_identity_allowed("MECHANICAL", "RELATED_NON_MECHANICAL")


def test_factor_guard_fails_cleanly_on_sparse_cross_section() -> None:
    from predictions_cup.learning.election_market_structure import factor_eligibility

    assert factor_eligibility(2, 1000) == "INSUFFICIENT_CROSS_SECTION"
    assert factor_eligibility(5, 10) == "INSUFFICIENT_COMPLETE_BINS"
    assert factor_eligibility(5, 100) == "ELIGIBLE"


def test_structural_graph_contains_no_unverified_hard_identity() -> None:
    import csv

    with (ROOT / "data/experiments/experiment_004b/structural_edges.csv").open(
        newline="", encoding="utf-8"
    ) as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 837
    assert all(row["event"] in DISCOVERY_EVENTS for row in rows)
    assert all(row["hard_probability_identity"] == "false" for row in rows)
    assert not any(row["verification_status"] == "MECHANICAL" for row in rows)


def test_evidence_package_hash_is_deterministic_and_upstream_sensitive() -> None:
    from predictions_cup.learning.election_market_structure import evidence_package_sha256

    package = {
        "package_version": "004B-structure-v1",
        "upstream": {"004a2": EXPECTED_004A2_PROTOCOL_SHA256},
        "response_summary": [{"event": "hungary_election", "effect": 1}],
    }
    first = evidence_package_sha256(package)
    assert first == evidence_package_sha256({**package, "package_sha256": first})
    changed = {
        **package,
        "upstream": {"004a2": "different"},
    }
    assert evidence_package_sha256(changed) != first


def test_sealed_empirical_evidence_is_rejected_but_holdout_declaration_is_allowed() -> None:
    from predictions_cup.learning.election_market_structure import (
        assert_no_sealed_empirical_evidence,
    )

    clean = {
        "response_summary": [{"event": "hungary_election"}],
        "sealed_holdout_declaration": {"sealed_events": list(SEALED_EVENTS)},
    }
    assert_no_sealed_empirical_evidence(clean)
    contaminated = {
        **clean,
        "response_summary": [{"event": "colombia_first_round"}],
    }
    with pytest.raises(ValueError, match="sealed event leaked"):
        assert_no_sealed_empirical_evidence(contaminated)


def test_depth_policy_forbids_state_before_valid_initialization() -> None:
    policy = frozen_discovery_manifest()["feature_definitions"]["depth_policy"]
    assert "full-depth metrics only from valid depth_snapshots" in policy
    assert "no cross-bin depth carry" in policy
    assert "BBO changes never treated as full depth" in policy


def test_source_side_policy_is_explicitly_unsigned() -> None:
    features = frozen_discovery_manifest()["feature_definitions"]
    assert features["source_side_policy"] == "never interpreted as aggressor direction"
    assert "unsigned" in features["venue_trade_activity"]
    assert "unsigned" in features["fill_activity"]


def test_null_count_and_block_scope_are_frozen() -> None:
    manifest = frozen_discovery_manifest()
    assert manifest["permutation_count"] == 1000
    assert manifest["null_definitions"]["NULL_A_CIRCULAR_SHIFT"][
        "never_cross_regime_boundary"
    ]
    assert manifest["null_definitions"]["NULL_B_BLOCK_PERMUTATION"][
        "block_seconds"
    ] == 300


def test_factor_interpretation_is_frozen_before_results() -> None:
    factor = frozen_discovery_manifest()["factor_interpretation"]
    assert factor["minimum_markets"] == 3
    assert factor["minimum_complete_bins"] == 30
    assert factor["one_factor_like_rank1_explained_threshold"] == 0.70


def test_hard_edge_rule_does_not_use_title_similarity() -> None:
    rules = frozen_discovery_manifest()["structural_edge_rules"]
    assert rules["title_similarity_alone"] == "UNVERIFIED"
    assert rules["unknown"] == "UNVERIFIED"
    assert rules["hard_identity_requires"] == "explicit verified mechanical relation type"


def test_alpha_execution_and_pnl_are_disabled_in_frozen_spec() -> None:
    restrictions = frozen_discovery_manifest()["scientific_restrictions"]
    assert restrictions["alpha_generation"] is False
    assert restrictions["profitability_optimization"] is False
    assert restrictions["execution_simulation"] is False
    assert restrictions["strategy_pnl"] is False
    assert restrictions["trade_recommendations"] is False


def test_market_universe_never_admits_complementary_token_twice() -> None:
    import csv

    with (ROOT / "data/experiments/experiment_004b/market_universe.csv").open(
        newline="", encoding="utf-8"
    ) as handle:
        rows = list(csv.DictReader(handle))
    admitted = [row for row in rows if row["admitted"] == "true"]
    keys = [
        (row["event"], row["regime"], row["condition_id"])
        for row in admitted
    ]
    assert len(keys) == len(set(keys))
    assert all(row["canonical_outcome"] == "Yes" for row in admitted)


def test_election_day_is_not_silently_reintroduced() -> None:
    manifest = frozen_discovery_manifest()
    assert "ELECTION_DAY_PRE_RESULTS" not in manifest["primary_regimes"]
    assert manifest["excluded_regimes"]["ELECTION_DAY_PRE_RESULTS"] == (
        "ELIGIBILITY_NOT_ASSESSED"
    )


def test_null_statistic_and_residual_shock_are_preregistered() -> None:
    manifest = frozen_discovery_manifest()
    null_stat = manifest["null_calibration_statistic"]
    assert null_stat["resolution_seconds"] == 30
    assert null_stat["null_a_minimum_shift_seconds"] == 300
    assert null_stat["null_b_block_seconds"] == 300
    assert len(null_stat["statistics"]) == 2
    residual = manifest["feature_definitions"]["large_residual_change"]
    assert "2.0 standard deviations" in residual
    assert "not searched" in residual


def test_response_artifact_rule_retains_full_external_matrix() -> None:
    rule = frozen_discovery_manifest()["large_response_artifact_rule"]
    assert "full directed response cells" in rule
    assert "deterministic CSV" in rule
    assert "response_matrix.csv" in rule
