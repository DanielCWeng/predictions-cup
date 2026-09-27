"""Scientific controls and compact analytics for EXPERIMENT-004B.

004B is structure discovery, not an alpha or execution study.  Empirical loading is fail-closed to
the frozen discovery-event whitelist so sealed confirmatory event paths cannot be opened through
this module.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from predictions_cup.learning.research_spec import canonical_json_bytes

EXPERIMENT_ID = "EXPERIMENT-004B"
DISCOVERY_SPEC_VERSION = "004B-discovery-v1"
PACKAGE_VERSION = "004B-structure-v1"
OUTPUT_SCHEMA_VERSION = "004B-v1"
EXPECTED_BASE_COMMIT = "3601bea0e5df9018377da7b6df5f302f774c89f1"
EXPECTED_DATA001_MANIFEST_SHA256 = (
    "e3d95735262b0be3e9fe7dd32fb54e53dc7e65b403ef9b5a2261ac107a8ea4c4"
)
EXPECTED_004A_PACKAGE_VERSION = "004A-event-time-v2"
EXPECTED_004A_REGIME_SHA256 = "57a102d64778be7c1460638bb4da63be7eeafe0b9494a4287339674bcad0a741"
EXPECTED_004A2_UNIVERSE_VERSION = "004A2-condition-universe-v1"
EXPECTED_004A2_UNIVERSE_SHA256 = "f3a8aa611944f16514e1668f9023361c8eab90c336dcf330648d4f502153a892"
EXPECTED_004A2_PROTOCOL_VERSION = "004A2-event-time-validation-v1"
EXPECTED_004A2_PROTOCOL_SHA256 = "052eefc3ad242ffe5499956da9e5ff05d7a9a3fcd01da3522be82be2cfc34a41"
DISCOVERY_EVENTS = ("hungary_election", "peru_first_round")
SEALED_EVENTS = ("colombia_first_round", "peru_runoff", "colombia_runoff")
EVENT_FAMILY = {
    "hungary_election": "HUN_2026",
    "peru_first_round": "PER_2026",
    "colombia_first_round": "COL_2026",
    "peru_runoff": "PER_2026",
    "colombia_runoff": "COL_2026",
}
PRIMARY_REGIMES = ("PRE_ELECTION", "ACTIVE_RESULTS")
SAMPLING_RESOLUTIONS_SECONDS = (1, 5, 30, 60, 300)
RESPONSE_HORIZONS_SECONDS = (1, 5, 15, 30, 60, 120, 300)
PERMUTATION_COUNT = 1000
MASTER_SEED = 20260927004
QUOTE_FRESHNESS_SECONDS = 300
MIN_PAIR_OVERLAP = 30
MIN_FACTOR_BINS = 30
MIN_FACTOR_MARKETS = 3


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def discovery_spec_payload() -> dict[str, Any]:
    """Return the preregistered, result-independent 004B discovery specification."""
    return {
        "experiment_id": EXPERIMENT_ID,
        "discovery_spec_version": DISCOVERY_SPEC_VERSION,
        "base_commit": EXPECTED_BASE_COMMIT,
        "upstream": {
            "data001_manifest_sha256": EXPECTED_DATA001_MANIFEST_SHA256,
            "004a_package_version": EXPECTED_004A_PACKAGE_VERSION,
            "004a_regime_sha256": EXPECTED_004A_REGIME_SHA256,
            "004a2_condition_universe_version": EXPECTED_004A2_UNIVERSE_VERSION,
            "004a2_condition_universe_sha256": EXPECTED_004A2_UNIVERSE_SHA256,
            "004a2_validation_protocol_version": EXPECTED_004A2_PROTOCOL_VERSION,
            "004a2_validation_protocol_sha256": EXPECTED_004A2_PROTOCOL_SHA256,
        },
        "partition": {
            "discovery_events": list(DISCOVERY_EVENTS),
            "sealed_events": list(SEALED_EVENTS),
            "discovery_event_families": sorted({EVENT_FAMILY[e] for e in DISCOVERY_EVENTS}),
            "sealed_event_families": sorted({EVENT_FAMILY[e] for e in SEALED_EVENTS}),
            "immutable_after_results": True,
        },
        "primary_regimes": list(PRIMARY_REGIMES),
        "excluded_regimes": {
            "ELECTION_DAY_PRE_RESULTS": "ELIGIBILITY_NOT_ASSESSED",
            "LATE_COUNT_DIAGNOSTIC": "NON_PRIMARY_DIAGNOSTIC_NOT_USED",
        },
        "sampling_resolutions_seconds": list(SAMPLING_RESOLUTIONS_SECONDS),
        "response_horizons_seconds": list(RESPONSE_HORIZONS_SECONDS),
        "feature_definitions": {
            "observable_clock": "observed_at; depth snapshots use recorded_at",
            "asof_rule": "information_time <= panel_time",
            "quote_freshness_seconds": QUOTE_FRESHNESS_SECONDS,
            "quote_batch_rule": (
                "same-observed_at conflicting BBO states are invalid until a later unambiguous BBO"
            ),
            "midpoint": "(best_bid + best_ask) / 2 for 0 < bid <= ask < 1",
            "logit_midpoint": "log(midpoint/(1-midpoint)); exact 0/1 unavailable; no clipping",
            "price_movement": "current midpoint minus one-resolution-prior midpoint",
            "logit_movement": "current logit midpoint minus one-resolution-prior logit midpoint",
            "spread": "best_ask - best_bid",
            "depth_policy": (
                "full-depth metrics only from valid depth_snapshots; last snapshot inside current "
                "panel bin; no cross-bin depth carry; BBO changes never treated as full depth"
            ),
            "top_depth": "top bid level size and top ask level size from full snapshot",
            "normalized_depth_imbalance": "(top_bid_depth-top_ask_depth)/(sum top depth)",
            "microprice": "(ask*top_bid_depth + bid*top_ask_depth)/(sum top depth)",
            "depth_imbalance_change": "current-bin imbalance minus prior-bin imbalance",
            "bbo_update_intensity": "book-change row count per panel bin",
            "depth_update_intensity": "full-depth snapshot count per panel bin",
            "venue_trade_activity": "unsigned count and price*size notional per panel bin",
            "fill_activity": "unsigned count and source value_usd per panel bin",
            "source_side_policy": "never interpreted as aggressor direction",
            "participant_policy": "no participant identity/reputation/skill features",
            "large_residual_change": (
                "absolute semantic residual change >= 2.0 standard deviations using the "
                "same discovery event/regime/resolution residual-change scale; threshold "
                "is fixed and not searched"
            ),
        },
        "null_calibration_statistic": {
            "resolution_seconds": 30,
            "statistics": [
                "mean absolute off-diagonal contemporaneous price-change correlation",
                "mean absolute directed next-30s price-change correlation",
            ],
            "null_a_minimum_shift_seconds": 300,
            "null_b_block_seconds": 300,
            "reason": (
                "predeclared canonical null-calibration slice; full descriptive structure "
                "still reports every supported resolution"
            ),
        },
        "stability_resolution_seconds": 30,
        "null_definitions": {
            "NULL_A_CIRCULAR_SHIFT": {
                "scope": "independent market series within one event/regime/resolution",
                "preserves": "marginal series and serial order",
                "breaks": "cross-market timing",
                "minimum_shift_seconds": 300,
                "never_cross_regime_boundary": True,
            },
            "NULL_B_BLOCK_PERMUTATION": {
                "scope": "one event/regime/market",
                "block_seconds": 300,
                "preserves": "within-block order and local clustering",
                "never_cross_regime_boundary": True,
            },
            "NULL_C_MATCHED_NON_EDGE": {
                "scope": "same event and regime",
                "matching_priority": [
                    "same unordered market-family pair where possible",
                    "pair coverage within 20 percent where possible",
                    "geometric-mean activity within factor 2 where possible",
                    "deterministic nearest coverage/activity distance then condition IDs",
                ],
                "outcome_dependent_matching": False,
            },
        },
        "permutation_count": PERMUTATION_COUNT,
        "random_seeds": {
            "master": MASTER_SEED,
            "derivation": "first 64 bits of SHA256(master|component_id)",
        },
        "minimum_overlap_rules": {
            "pairwise_correlation_bins": MIN_PAIR_OVERLAP,
            "directed_response_bins": MIN_PAIR_OVERLAP,
            "structural_residual_bins": MIN_PAIR_OVERLAP,
            "factor_complete_bins": MIN_FACTOR_BINS,
            "factor_markets": MIN_FACTOR_MARKETS,
        },
        "factor_interpretation": {
            "minimum_markets": MIN_FACTOR_MARKETS,
            "minimum_complete_bins": MIN_FACTOR_BINS,
            "one_factor_like_rank1_explained_threshold": 0.70,
            "otherwise": "MULTI_FACTOR_LIKE",
            "below_minimum_markets": "INSUFFICIENT_CROSS_SECTION",
        },
        "multiple_comparisons": {
            "method": "Benjamini-Hochberg",
            "alpha": 0.05,
            "family": "event x regime x metric family",
            "retain_all_tested_cells": True,
        },
        "structural_edge_rules": {
            "hard_identity_requires": "explicit verified mechanical relation type",
            "accepted_manual_indirect": "SEMANTIC_BUT_NON_MECHANICAL",
            "title_similarity_alone": "UNVERIFIED",
            "unknown": "UNVERIFIED",
            "allowed_relation_types": [
                "MUTUALLY_EXCLUSIVE_PARTITION",
                "EXHAUSTIVE_PARTITION",
                "SUBSET",
                "SUPERSET",
                "QUALIFICATION_TO_WINNER",
                "FIRST_ROUND_TO_FINAL",
                "SEAT_BIN_PARTITION",
                "RELATED_NON_MECHANICAL",
                "UNVERIFIED",
            ],
        },
        "propagation_network_rule": (
            "retain all available directed pair responses; node source/sink diagnostics use mean "
            "absolute outgoing/incoming price-response strength without P&L or "
            "significance filtering"
        ),
        "large_response_artifact_rule": (
            "full directed response cells are written externally as deterministic CSV; "
            "committed response_matrix.csv is a deterministic group summary plus "
            "predeclared strongest cells"
        ),
        "panel_output_policy": "compact diagnostics in Git; large panel/intermediates external",
        "output_schema_version": OUTPUT_SCHEMA_VERSION,
        "scientific_restrictions": {
            "alpha_generation": False,
            "profitability_optimization": False,
            "execution_simulation": False,
            "strategy_pnl": False,
            "trade_recommendations": False,
            "sealed_event_empirical_inspection": False,
        },
    }


def discovery_spec_sha256(payload: Mapping[str, Any]) -> str:
    clean = dict(payload)
    clean.pop("discovery_spec_sha256", None)
    return hashlib.sha256(canonical_json_bytes(clean)).hexdigest()


def frozen_discovery_manifest() -> dict[str, Any]:
    payload = discovery_spec_payload()
    payload["discovery_spec_sha256"] = discovery_spec_sha256(payload)
    return payload


def derived_seed(component_id: str) -> int:
    material = f"{MASTER_SEED}|{component_id}".encode()
    return int.from_bytes(hashlib.sha256(material).digest()[:8], "big")


def require_discovery_event(event_id: str) -> None:
    if event_id in SEALED_EVENTS:
        raise PermissionError(f"sealed confirmatory event cannot be loaded empirically: {event_id}")
    if event_id not in DISCOVERY_EVENTS:
        raise ValueError(f"event is not in frozen 004B discovery whitelist: {event_id}")


def empirical_event_root(corpus_root: Path, event_id: str) -> Path:
    require_discovery_event(event_id)
    return corpus_root / "schema_version=1" / event_id


def verify_upstream(repo_root: Path) -> dict[str, str]:
    data_manifest = repo_root / "data/manifests/historical/data_001_corpus_manifest.json"
    summary = json.loads(
        (repo_root / "data/experiments/experiment_004a2/validation_summary.json").read_text()
    )
    actual = {
        "data001_manifest_sha256": sha256_file(data_manifest),
        "004a_regime_sha256": str(summary["004a_regime_sha256"]),
        "004a2_condition_universe_sha256": str(summary["condition_universe_sha256"]),
        "004a2_validation_protocol_sha256": str(summary["validation_protocol_sha256"]),
    }
    expected = {
        "data001_manifest_sha256": EXPECTED_DATA001_MANIFEST_SHA256,
        "004a_regime_sha256": EXPECTED_004A_REGIME_SHA256,
        "004a2_condition_universe_sha256": EXPECTED_004A2_UNIVERSE_SHA256,
        "004a2_validation_protocol_sha256": EXPECTED_004A2_PROTOCOL_SHA256,
    }
    if actual != expected:
        raise ValueError(f"004B upstream identity mismatch: {actual!r}")
    return actual


def temporal_bin_times_ns(start_ns: int, end_ns: int, resolution_seconds: int) -> tuple[int, ...]:
    """Deterministic right-edge-minus-1ns observation times for half-open [start,end)."""
    if resolution_seconds <= 0 or end_ns <= start_ns:
        raise ValueError("invalid temporal grid")
    step = resolution_seconds * 1_000_000_000
    return tuple(range(start_ns + step - 1, end_ns, step))


def asof_position(observation_times_ns: tuple[int, ...], query_ns: int) -> int | None:
    """Last observation not later than query_ns; never returns future state."""
    from bisect import bisect_right

    index = bisect_right(observation_times_ns, query_ns) - 1
    return None if index < 0 else index


def response_horizon_supported(resolution_seconds: int, horizon_seconds: int) -> bool:
    return horizon_seconds >= resolution_seconds and horizon_seconds % resolution_seconds == 0


def block_permutation_indices(length: int, block_bins: int, component_id: str) -> tuple[int, ...]:
    """Seeded contiguous-block permutation over one already-isolated regime sequence."""
    import random

    if length < 0 or block_bins <= 0:
        raise ValueError("invalid block permutation dimensions")
    blocks = [
        tuple(range(start, min(length, start + block_bins)))
        for start in range(0, length, block_bins)
    ]
    rng = random.Random(derived_seed(component_id))
    rng.shuffle(blocks)
    return tuple(index for block in blocks for index in block)


def pair_cell_keys(
    condition_ids: tuple[str, ...], *, directed: bool
) -> tuple[tuple[str, str], ...]:
    """Complete deterministic pair universe; cells are retained even if later unavailable."""
    if len(set(condition_ids)) != len(condition_ids):
        raise ValueError("condition IDs must be unique")
    ordered = tuple(sorted(condition_ids))
    if directed:
        return tuple((left, right) for left in ordered for right in ordered if left != right)
    return tuple(
        (left, right) for index, left in enumerate(ordered) for right in ordered[index + 1 :]
    )


def fdr_family_id(event_id: str, regime: str, metric_family: str) -> str:
    if event_id not in DISCOVERY_EVENTS or regime not in PRIMARY_REGIMES or not metric_family:
        raise ValueError("invalid 004B FDR family")
    return f"{event_id}|{regime}|{metric_family}"


def hard_identity_allowed(verification_status: str, relation_type: str) -> bool:
    mechanical = {
        "MUTUALLY_EXCLUSIVE_PARTITION",
        "EXHAUSTIVE_PARTITION",
        "SUBSET",
        "SUPERSET",
        "QUALIFICATION_TO_WINNER",
        "FIRST_ROUND_TO_FINAL",
        "SEAT_BIN_PARTITION",
    }
    return verification_status == "MECHANICAL" and relation_type in mechanical


def factor_eligibility(markets: int, complete_bins: int) -> str:
    if markets < MIN_FACTOR_MARKETS:
        return "INSUFFICIENT_CROSS_SECTION"
    if complete_bins < MIN_FACTOR_BINS:
        return "INSUFFICIENT_COMPLETE_BINS"
    return "ELIGIBLE"


def evidence_package_sha256(package: Mapping[str, Any]) -> str:
    clean = dict(package)
    clean.pop("package_sha256", None)
    return hashlib.sha256(canonical_json_bytes(clean)).hexdigest()


def assert_no_sealed_empirical_evidence(package: Mapping[str, Any]) -> None:
    """Reject sealed event identities from empirical evidence sections.

    The sealed-holdout declaration itself is intentionally exempt because it must name the held-out
    events.  This guard applies to the evidence passed onward to hypothesis-generation agents.
    """
    empirical_sections = (
        "eligible_market_metadata",
        "structural_graph",
        "contemporaneous_summary",
        "response_summary",
        "information_map",
        "factor_evidence",
        "null_adjusted_evidence",
        "stability_evidence",
        "structure_summary",
    )

    def walk(value: object) -> None:
        if isinstance(value, Mapping):
            event = value.get("event")
            if isinstance(event, str) and event in SEALED_EVENTS:
                raise ValueError(f"sealed event leaked into empirical package: {event}")
            for child in value.values():
                walk(child)
        elif isinstance(value, list | tuple):
            for child in value:
                walk(child)

    for section in empirical_sections:
        if section in package:
            walk(package[section])
