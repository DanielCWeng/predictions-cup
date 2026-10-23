"""Independent future offline evidence-admission vectors; research TEST ONLY.

The #277 black-box tests below execute the REAL existing schema gate on invented
bytes. FUTURE_VECTORS are deliberately **not** runnable author acceptance tests:
the separate Alpha1/Alpha2/Capital issuer-authenticated consumers do not yet
exist on this branch. They freeze exact negative fixtures/expected dispositions,
NOT a pass for unknown future implementations.

Do not use any of these fixtures as original issuer proof, real trades, cash
flows, ready PAPER or LIVE evidence. #84 and #264 failures remain unresolved.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from predictions_cup.research.offline_paired_tape_gate import validate_offline_tape
from test_offline_paired_tape_gate import _sample


@dataclass(frozen=True)
class FutureNegativeVector:
    producer: str
    vector: str
    fabricated_or_missing_evidence: str
    expected: str


DENY = "NOT_ADMITTED_NULL_ECONOMICS"
BLOCK = "RELEASE_BLOCKED_NO_WIRE_OR_MERGE"

# These are preregistered FUTURE contracts, not presently executed validators.
# Each string identifies a concrete independent synthetic input mutation.
FUTURE_VECTORS: tuple[FutureNegativeVector, ...] = (
    # (1) Alpha1 causal decision/arrival/exit and mapping/lot gates.
    FutureNegativeVector("ALPHA1", "lookahead_six_pm_minutes",
                         "one of six PM minutes arrives after decision", DENY),
    FutureNegativeVector("ALPHA1", "future_arrival_depth_at_decision",
                         "entry qualification uses depth recorded post latency", DENY),
    FutureNegativeVector("ALPHA1", "exit_only_midpoint",
                         "30s exit has midpoint but no native bid/ask cash", DENY),
    FutureNegativeVector("ALPHA1", "wrong_sig_exchange_no_complement",
                         "NO token from another SIG exchange", DENY),
    FutureNegativeVector("ALPHA1", "one_share_fractional_pooling",
                         "two book levels each one share with 50% haircut", DENY),
    FutureNegativeVector("ALPHA1", "insufficient_whole_depth_size20",
                         "one-share depth assumed fillable for 20 shares", DENY),
    FutureNegativeVector("ALPHA1", "split_and_candidate_retrofit",
                         "horizon/model coefficient reselected from sealed holdout", DENY),
    # (2) Alpha2 all-attempt quote, issuer fill and terminality gates.
    FutureNegativeVector("ALPHA2", "overlap_30s_60s_shared_fill",
                         "q@00 and q@30 both consume issuer fill f7 at 00:40", DENY),
    FutureNegativeVector("ALPHA2", "bid_ask_double_hit",
                         "BID and ASK count a single issuer fill f8 twice", DENY),
    FutureNegativeVector("ALPHA2", "fill_event_no_id",
                         "trade price touches quote but native fill ID absent", DENY),
    FutureNegativeVector("ALPHA2", "replayed_identical_fill",
                         "same native fill f9 replayed with different local event IDs", DENY),
    FutureNegativeVector("ALPHA2", "ack_not_venue_terminal",
                         "ACK exists; venue terminal confirmation missing", DENY),
    FutureNegativeVector("ALPHA2", "cancel_request_uncertain_late_fill",
                         "CANCEL_REQUEST with lost ACK and possible late fill", DENY),
    FutureNegativeVector("ALPHA2", "zero_fill_missing_from_denominator",
                         "complete ACK-to-EXPIRED zero-fill maker attempt omitted", DENY),
    FutureNegativeVector("ALPHA2", "one_unknown_eligible_attempt",
                         "censored UNCERTAIN attempt excluded as zero-fill", DENY),
    FutureNegativeVector("ALPHA2", "local_make_label_without_native_role",
                         "strategy MAKE string but issuer liquidity role unproved", DENY),
    FutureNegativeVector("ALPHA2", "fractional_size_as_native_order",
                         "weighted proxy count 67.75 presented as 67.75 SIG trades", DENY),
    # (3) Capital issuer role/fee/lot/namespace/equity gate.
    FutureNegativeVector("CAPITAL", "unknown_fee_interpreted_zero",
                         "fee_rate_bps NULL coerced to native PAID_ZERO", DENY),
    FutureNegativeVector("CAPITAL", "rebate_sign_or_currency_unknown",
                         "rebate sign inverted or issuer currency/unit missing", DENY),
    FutureNegativeVector("CAPITAL", "pre_entry_exit_disguised_post",
                         "exit cash timestamp earlier than original fill", DENY),
    FutureNegativeVector("CAPITAL", "no_basis_as_one_minus_yes",
                         "NO cost inferred as YES complement, not direct NO paid cash", DENY),
    FutureNegativeVector("CAPITAL", "unsettled_or_void_lot",
                         "partial exit/void, no authenticated final settled proceeds", DENY),
    FutureNegativeVector("CAPITAL", "cross_tournament_raw_id_scope_unknown",
                         "same raw fill ID across tournaments, issuer scope UNKNOWN", DENY),
    FutureNegativeVector("CAPITAL", "same_tournament_duplicate_native_id",
                         "distinct logical intents claim identical scoped issuer fill", DENY),
    FutureNegativeVector("CAPITAL", "account_realised_plus31_94_not_maker",
                         "account aggregate +31.94 allocated to unrelated maker attempts", DENY),
    FutureNegativeVector("CAPITAL", "stale_snapshot_9280_executable",
                         "12-Oct 13:37 quotes mixed with 21:22 capital snapshot", DENY),
    FutureNegativeVector("CAPITAL", "orphan_multi_leg_exposure",
                         "leg 1 filled, leg 2 unproved, reservation released", DENY),
    FutureNegativeVector("CAPITAL", "no_independent_day_event_clusters",
                         "positive proxy mean without day/event clustered after-fee CI", DENY),
    # (4) Rights, durable per-wire observer, cutoff, global lock and performance.
    FutureNegativeVector("SAFETY", "self_asserted_rights_no_owner_attestation",
                         "locally hashed bytes with unverified permission scope", BLOCK),
    FutureNegativeVector("SAFETY", "issuer_git_sha_self_authenticates",
                         "metadata git hash treated as publisher identity signature", BLOCK),
    FutureNegativeVector("SAFETY", "queue_emit_true_no_committed_observe",
                         "observer enqueues but no durable pre-POST per-wire receipt", BLOCK),
    FutureNegativeVector("SAFETY", "failed_first_economic_wire_observer",
                         "closed/full writer permits initial economic HTTP POST", BLOCK),
    FutureNegativeVector("SAFETY", "two_wire_429_then_success_one_receipt",
                         "two actual economic wires counted as one observation", BLOCK),
    FutureNegativeVector("SAFETY", "lost_ack_cancellation_erases_uncertainty",
                         "possible wire then cancel releases reservation/UNCERTAIN", BLOCK),
    FutureNegativeVector("SAFETY", "cutoff_x3_x6_scope",
                         "unverified global tournament cutoff/UTC/native lock version", BLOCK),
    FutureNegativeVector("SAFETY", "benchmark_rights_target_unaccepted",
                         "untrusted target-host source/performance promoted by fake CI", BLOCK),
)


@pytest.mark.parametrize(
    ("mutation", "reason"),
    (
        ("sig_wrong_exchange", "SIG_MAPPING_OR_SIDE_MISMATCH"),
        ("sig_receive_after_decision", "NONCAUSAL_CLOCK_ORDER"),
        ("pm_wrong_token", "PM_MAPPING_OR_SIDE_MISMATCH"),
        ("complement_without_no", "MAPPING_ORIENTATION_INVALID"),
        ("pm_ws_reconnect", "PM_WS_CONTINUITY_LOST"),
        ("pm_bad_seed_epoch", "PM_SEED_EPOCH_MISMATCH"),
        ("unverified_use_ref", "MISSING_PERMITTED_USE_EVIDENCE"),
    ),
)
def test_actual_277_black_box_decision_gate_fail_closed(
    tmp_path: Path, mutation: str, reason: str,
) -> None:
    """This portion DOES run the real, pinned #277 schema gate on fake data."""
    def edit(
        mapping: dict[str, Any], sig: dict[str, Any], pm: dict[str, Any],
        manifest: dict[str, Any],
    ) -> None:
        if mutation == "sig_wrong_exchange":
            sig["exchange_id"] = "unrelated-sig-exchange"
        elif mutation == "sig_receive_after_decision":
            sig["received_at"] = "2026-10-16T12:00:00.900Z"
        elif mutation == "pm_wrong_token":
            pm["token_id"] = "unrelated-pm-token"
        elif mutation == "complement_without_no":
            mapping["mapping_direction"] = "COMPLEMENT"
        elif mutation == "pm_ws_reconnect":
            pm["reconnect_gap"] = True
        elif mutation == "pm_bad_seed_epoch":
            pm["seed_generation"] = 4
        elif mutation == "unverified_use_ref":
            manifest["permission_ref"] = ""

    paths, digest = _sample(tmp_path, edit)
    actual = validate_offline_tape(*paths, expected_manifest_sha256=digest)
    assert actual.verdict == "PAIRED_DEPTH_NOT_CERTIFIED"
    assert actual.schema_valid_intervals == 0
    assert reason in [why for _interval, why in actual.rejections]
    assert actual.source_admitted is False
    assert actual.paper_admitted is False
    assert actual.external_issuer_authenticated is False
    assert actual.network_requests == 0


def test_full_schema_and_forged_trust_labels_still_never_admit(tmp_path: Path) -> None:
    """Self-labeled issuer/cash/permission cannot promote even a valid schema."""
    def edit(
        _mapping: dict[str, Any], sig: dict[str, Any],
        _pm: dict[str, Any], manifest: dict[str, Any],
    ) -> None:
        manifest["independent_issuer_authenticated"] = True
        manifest["source_admitted"] = True
        manifest["paper_admitted"] = True
        manifest["real_alpha"] = 123456
        sig["alleged_venue_native_fill"] = "fabricated-fill-ID"
        sig["alleged_paid_maker_cash"] = "+31.94"

    paths, digest = _sample(tmp_path, edit)
    actual = validate_offline_tape(*paths, expected_manifest_sha256=digest)
    assert actual.verdict == "SCHEMA_ONLY/PAPER_NOT_ADMITTED"
    assert actual.schema_valid_intervals == 1
    assert actual.source_admitted is False
    assert actual.paper_admitted is False
    assert actual.external_issuer_authenticated is False
    assert actual.network_requests == 0
    assert getattr(actual, "real_alpha", None) is None
    assert getattr(actual, "live_admitted", None) is None


def test_preregistered_future_matrix_is_explicitly_unexecuted_not_admission() -> None:
    """Spec integrity only: NOT a passing test of unwritten future consumers."""
    groups = Counter(v.producer for v in FUTURE_VECTORS)
    assert groups == {"ALPHA1": 7, "ALPHA2": 10, "CAPITAL": 11, "SAFETY": 8}
    assert len(FUTURE_VECTORS) == 36
    assert len({(v.producer, v.vector) for v in FUTURE_VECTORS}) == 36
    assert all(v.fabricated_or_missing_evidence for v in FUTURE_VECTORS)
    assert all(
        v.expected == (BLOCK if v.producer == "SAFETY" else DENY)
        for v in FUTURE_VECTORS
    )
    # Independent external issuer/rights/live release proofs deliberately
    # cannot be produced or certified by this test-only negative matrix.
