"""Build the frozen EXPERIMENT-004C-D preregistration and metadata-only registry."""

from __future__ import annotations

import csv
import hashlib
import json
import subprocess
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data/experiments/experiment_004c_d"
PROV = OUT / "provenance"
OUT.mkdir(parents=True, exist_ok=True)
PROV.mkdir(parents=True, exist_ok=True)

ASTRA_COMMIT = "5b274cdc487fa5aceb184689c1e6c776aa9f2df5"
ASTRA_PATH = "data/experiments/experiment_004c/research/astra/ASTRA_004C_HYPOTHESIS_SET.md"
ASTRA_SHA256 = "95613edc6acfedd9b614143198366b1b2d62b9f10cbbcb5bd2425588760b6bff"
ASTRA_PRIOR_SHA256 = "3c8403c8a69d46486aa60074a89349afc1625abb02db47f694ad14f183fd7a93"
BASE_MAIN = "4e094d13b06d9e558cbc404cc4d472316d7d5bbb"
DISCOVERY_EVENTS = ("hungary_election", "peru_first_round")
CHALLENGE_EVENTS = ("colombia_first_round", "peru_runoff", "colombia_runoff")
REGIMES = ("PRE_ELECTION", "ACTIVE_RESULTS")
SLOTS = tuple(f"C0{i}-{r}" for i in range(1, 5) for r in ("PRE", "ACTIVE"))

def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()

def canonical_json_bytes(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode()

def git_show(commit: str, path: str) -> bytes:
    return subprocess.check_output(["git", "show", f"{commit}:{path}"], cwd=ROOT)

astra = git_show(ASTRA_COMMIT, ASTRA_PATH)
if sha256_bytes(astra) != ASTRA_SHA256:
    raise SystemExit("Astra source hash mismatch")
(PROV / "ASTRA_004C_HYPOTHESIS_SET_PINNED.md").write_bytes(astra)

conditions = list(csv.DictReader((ROOT / "data/experiments/experiment_004a2/condition_usability_matrix.csv").open()))
regime_defs = json.loads((ROOT / "data/experiments/experiment_004a/regime_definitions.json").read_text())
windows = {
    e["regime_id"]: {
        r["name"]: [r["start_utc"], r["end_utc"]]
        for r in e["regimes"] if r["name"] in REGIMES
    }
    for e in regime_defs["events"]
}
field_by_regime = {
    "PRE_ELECTION": "pre_election_usable",
    "ACTIVE_RESULTS": "active_results_usable",
}
registry = []
for event in DISCOVERY_EVENTS + CHALLENGE_EVENTS:
    for regime in REGIMES:
        for row in conditions:
            if row["regime_id"] != event:
                continue
            exact_yes = row["canonical_outcome"] == "Yes" and bool(row["canonical_token_id"])
            admitted = exact_yes and row[field_by_regime[regime]] == "true"
            registry.append({
                "event": event,
                "event_family": row["event_family"],
                "regime": regime,
                "condition_id": row["condition_id"],
                "market_id": row["market_id"],
                "market_family": row["market_family"],
                "canonical_token_id": row["canonical_token_id"],
                "counterpart_token_id": row["counterpart_token_id"],
                "admitted": "true" if admitted else "false",
                "exclusion_reason": "" if admitted else (
                    "CANONICAL_YES_FAILED_CLOSED" if not exact_yes else f"{regime}_NOT_USABLE"
                ),
            })

fields = list(registry[0])
with (OUT / "market_family_registry.csv").open("w", newline="", encoding="utf-8") as handle:
    writer = csv.DictWriter(handle, fields, lineterminator="\n")
    writer.writeheader()
    writer.writerows(registry)

admitted = [r for r in registry if r["admitted"] == "true"]
counts = defaultdict(lambda: defaultdict(Counter))
for r in admitted:
    counts[r["event"]][r["regime"]][r["market_family"]] += 1
counts_json = {
    event: {regime: dict(fams) for regime, fams in by_reg.items()}
    for event, by_reg in counts.items()
}

c03_reason = (
    "DORMANT_GRAPH_SUPPORT_FAIL: Hungary PRE has <3 family peers; Peru PRE presidential targets "
    "have 8 family peers but only 2 outside-family admitted conditions, so the prespecified "
    "same-degree matched non-neighbour control is unavailable without shrinking the graph."
)

prereg = {
    "experiment_id": "EXPERIMENT-004C-D",
    "version": "004C-D-prereg-v1",
    "branch": "experiment/004c-d-conditional-response-renewal",
    "base_main_commit": BASE_MAIN,
    "scientific_source": {
        "astra_commit": ASTRA_COMMIT,
        "astra_path": ASTRA_PATH,
        "astra_sha256": ASTRA_SHA256,
        "astra_prior_sha256": ASTRA_PRIOR_SHA256,
        "pinned_copy": "data/experiments/experiment_004c_d/provenance/ASTRA_004C_HYPOTHESIS_SET_PINNED.md",
        "hypotheses_rewritten_after_004c": False,
    },
    "partition": {
        "discovery_events": list(DISCOVERY_EVENTS),
        "challenge_events": list(CHALLENGE_EVENTS),
        "challenge_status": "004B_SEALED_PRIOR_EXPERIMENT_EXPOSED",
        "primary_regime": "PRE_ELECTION",
        "secondary_regime": "ACTIVE_RESULTS",
        "pool_pre_and_active": False,
        "excluded_regimes": ["ELECTION_DAY_PRE_RESULTS", "LATE_COUNT", "POST_RESOLUTION_DIAGNOSTIC"],
        "windows": {e: windows[e] for e in DISCOVERY_EVENTS + CHALLENGE_EVENTS},
    },
    "universe": {
        "axis": "exact canonical Yes condition",
        "admission": "canonical Yes token present AND 004A2 regime usable",
        "family_graph": "same exact market_family within event/regime; leave target out; equal weight",
        "target_excluded_from_own_aggregate": True,
        "counterpart_complements_excluded": True,
        "title_inference_for_duplicates": False,
        "direct_duplicates": "exclude only explicit configured equivalent/mechanical duplicate; none inferred from title",
        "registry_path": "data/experiments/experiment_004c_d/market_family_registry.csv",
        "admitted_family_counts": counts_json,
    },
    "clock": {
        "observable_time": "observed_at for book_changes; recorded_at only for depth-snapshot placebo",
        "grid_seconds": 30,
        "asof_rule": "right edge minus 1ns; only observable_time <= query_time",
        "freshness_seconds": 300,
        "record_age": "seconds since latest valid observable BBO record; identical records reset",
        "genuine_bbo_change_age": "seconds since latest genuine valid bid/ask change; identical records do not reset",
        "midpoint_change_age": "seconds since latest genuine midpoint change",
        "unknown_age_policy": "unavailable, never zero",
    },
    "genuine_change": {
        "same_timestamp_identical": "collapse",
        "same_timestamp_conflict": "fail timestamp closed and reset continuity barrier",
        "valid_bbo": "0 < best_bid <= best_ask < 1",
        "event": "bid and/or ask differs from immediately preceding valid observable BBO",
        "repeated_identical": "observation_only_not_renewal",
        "gap_barrier_seconds": 300,
        "known_invalid_or_gap": "reset continuity; first later valid state initializes but is not counted as renewal",
    },
    "exposure": {
        "future_zero_label_requires": [
            "valid target BBO at decision time",
            "target observation envelope extends beyond horizon",
            "next target valid observable record after horizon occurs within 300s",
            "no target invalid/conflict barrier intersects label interval",
            "collector-wide book-change liveness has no >30s gap intersecting label interval",
        ],
        "unknown_exposure": "row unavailable, never label 0",
        "collector_liveness_scope": "all book-change tokens in event corpus, outcome-independent",
        "same_mask_baseline_challenger": True,
    },
    "features": {
        "family_innovation_E": "mean(abs(p_i(t)-p_i(t-30s))) over available same-family peers; target excluded; no zero fill",
        "primary_age_A": "log(1 + genuine_BBO_change_age_at_t_minus_30 / 30)",
        "record_age_diagnostic": "log(1 + record_age_at_t_minus_30 / 30)",
        "family_activity_F": "mean(log(1 + genuine_change_count_i(t-30,t])) over available peers",
        "target_duration_D": "log(1 + seconds_since_last_genuine_target_change_at_t_minus_30 / 30)",
        "spread_S": "ask_j(t-30)-bid_j(t-30)",
        "trailing_windows_seconds": [30, 300],
        "outside_family": "all admitted non-target conditions outside target market_family; equal-weight observed aggregates",
        "outside_match_control": {
            "k": "min(number of valid family peers, number of valid outside-family candidates)",
            "distance": "abs(p-peer_median_p)/0.10 + abs(A-peer_median_A) + abs(log1p(changes300)-peer_median_log1p_changes300)",
            "ties": "condition_id lexical",
            "scales": {"price": 0.10, "age_log": 1.0, "activity_log": 1.0},
            "outcome_matching": False,
        },
    },
    "hypotheses": {
        "D1_C01": {
            "primary_target": "abs(p_target(t+30)-p_target(t))",
            "interaction": "E*A",
            "expected_sign": "positive",
            "state_time": "t-30",
            "shock_interval": "(t-30,t]",
            "response_interval": "(t,t+30]",
            "diagnostics_seconds": [60, 120],
            "first_update_diagnostic_seconds": 120,
        },
        "D2_C02": {
            "primary_target": "any genuine valid target BBO change in (t,t+30]",
            "interaction": "F*D",
            "expected_sign": "positive",
            "diagnostics_seconds": [60, 120],
            "directional_price_claim": False,
        },
        "D3_C03": {
            "status": "DORMANT",
            "reason": c03_reason,
            "reserved_slots_remain": True,
            "p_value_if_dormant": 1.0,
        },
        "D4_C04": {
            "status": "PENDING_NONOUTCOME_SUPPORT_GATE",
            "target": "D2 genuine renewal target",
            "interaction": "E*S",
            "expected_sign": "negative",
            "diagnostics_seconds": [60, 120],
        },
    },
    "models": {
        "D1": "ridge linear regression on raw magnitude target",
        "D2_D4": "ridge logistic discrete hazard",
        "standardization": "discovery-training weighted mean/std; intercept unscaled",
        "ridge_alpha": 1.0,
        "logistic_C": 1.0,
        "logistic_solver": "lbfgs",
        "max_iter": 2000,
        "tolerance": 1e-9,
        "hyperparameter_search": False,
        "fit_scope": "separate by PRE/ACTIVE; discovery only; no market-ID coefficients",
        "discovery_split": "chronological first 2/3 train, last 1/3 validation per event/regime; 300s purge and embargo",
        "final_refit": "same frozen model on all eligible discovery rows after support audit",
        "D1_loss": "weighted squared error",
        "D2_D4_loss": "weighted Brier score",
    },
    "weighting": {
        "hierarchy": "event -> disjoint 300s block -> target -> row",
        "rule": "equal event weight; equal block weight within event; equal target weight within block; equal row weight within target-block",
        "purpose": "prevent high-frequency or many-row markets from dominating",
    },
    "nulls": {
        "attempts": 9999,
        "seed_master": 20260927004,
        "seed_derivation": "first 64 bits SHA256(master|component_id)",
        "N0": "restricted additive baseline with interaction fixed to zero",
        "N1": "joint cross-sectional 300s block residual resampling within event/regime; masks fixed; 600s sensitivity",
        "N2_D1": "restricted no-interaction common-information/asynchronous bootstrap: retain observed E/A/common predictors and masks, jointly resample 300s cross-sectional residual blocks; require moment, age, availability, autocorrelation and common-burst diagnostics before interpretation",
        "N2_D2_D4": "sequential restricted hazard simulation with interaction zero, own duration/count history recomputed from simulated renewals, observed common/outside activity and exposure schedules retained, plus event-block common logit offset resampled from discovery; no family-specific excitation interaction",
        "invalid_draws": "count as exceedances unless a prespecified structural impossibility makes slot untestable",
        "common_null_failure": "INCONCLUSIVE for transmission interpretation",
    },
    "negative_controls": [
        "activity/age/probability-matched outside-family source aggregate",
        "raw book_changes row intensity",
        "repeated unchanged BBO-record intensity",
        "depth-snapshot cadence",
        "exclude same-observable-timestamp source/target renewal",
        "record-age versus genuine-change-age diagnostic",
        "target own-renewal-history ablation explanatory only",
        "D1 bid/ask translation versus one-sided spread repair diagnostic",
    ],
    "support_gates": {
        "residualized_added_feature_full_rank": True,
        "discovery_training_observations_per_fitted_coefficient_min": 20,
        "occupied_disjoint_300s_blocks_per_evaluated_event_regime_min": 40,
        "added_feature_varying_blocks_min": 20,
        "single_block_added_feature_ss_max_fraction": 0.50,
        "hazard_requires_both_classes": True,
        "finite_identifiable_fit": True,
        "no_gate_relaxation_after_challenge": True,
    },
    "multiplicity": {
        "slots": list(SLOTS),
        "alpha": 0.05,
        "slot_p": "conservative intersection-union max of valid expected-sign, predictive-gain, and relevant conditional-null component p-values; for replication also max across predesignated testable challenge events",
        "family_correction": "Holm FWER across all 8 slots",
        "dormant_slots_p": 1.0,
        "diagnostics_can_rescue": False,
    },
    "replication": {
        "eligible_challenge_events": list(CHALLENGE_EVENTS),
        "rule": "same expected interaction sign and positive frozen predictive gain in every predesignated testable event used for claim",
        "pool_rows_as_replication": False,
        "country_generalisation": "requires at least one testable Colombia event and testable Peru runoff; otherwise not claimed",
    },
    "promotion": {
        "D1": "CONDITIONAL_MAGNITUDE_RESPONSE_SUPPORTED only with sign, frozen loss gain, N2 survival, Holm, support and replication",
        "D2": "CONDITIONAL_QUOTE_RENEWAL_SUPPORTED only with sign, Brier gain, N2 survival, genuine-change measurement, Holm, support and replication",
        "observation_explanation": "OBSERVATION_PROCESS_EXPLAINS_RESULT if raw records/same-timestamp/capture null explain effect",
        "no_edge": "NO_CONDITIONAL_EDGE only for adequately supported D1/D2 failures",
        "inconclusive": "INCONCLUSIVE for censoring/support/null/replication failure",
    },
    "scientific_restrictions": {
        "real_order_placement": False,
        "strategy_pnl": False,
        "directional_election_forecast": False,
        "horizon_search": False,
        "threshold_search": False,
        "learned_leader_graph": False,
        "challenge_hyperparameter_tuning": False,
    },
}

prereg_bytes = canonical_json_bytes(prereg)
(OUT / "preregistration.json").write_bytes(prereg_bytes)
provenance = {
    "astra_commit": ASTRA_COMMIT,
    "astra_path": ASTRA_PATH,
    "astra_sha256_expected": ASTRA_SHA256,
    "astra_sha256_verified": sha256_bytes(astra),
    "astra_prior_sha256": ASTRA_PRIOR_SHA256,
    "base_main_commit": BASE_MAIN,
    "preregistration_sha256": sha256_bytes(prereg_bytes),
    "challenge_empirical_access_before_freeze": False,
}
(PROV / "astra_provenance.json").write_bytes(canonical_json_bytes(provenance))
print(json.dumps({
    "preregistration_sha256": provenance["preregistration_sha256"],
    "registry_rows": len(registry),
    "admitted_rows": len(admitted),
    "c03_status": "DORMANT",
}, sort_keys=True))
