"""Independent #316 exact-head source-binding/chronology falsifiers.

All inputs are invented. Three STRICT negative tests deliberately challenge the
new #316 author contract; any failure is an authentic red review result, not
an approval to alter or weaken the original #316 tests or #310 frozen oracle.
"""
from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

from predictions_cup.research.offline_causal_alpha_label import (
    Level,
    SettlementRecord,
)
from test_offline_causal_alpha_label import T, _candidate, _run

D = Decimal

FROZEN_316_SOURCE_SHA = "3c55017d8b1fb1630ee46f70ad624ce6ccdddae6"
FROZEN_316_TESTS_SHA = "ae5549b8a7093517a1f4dcffd8c114ae1553ecc3"


def _git_blob_sha(raw: bytes) -> str:
    return hashlib.sha1(
        b"blob " + str(len(raw)).encode() + b"\x00" + raw
    ).hexdigest()


def test_exact_author_source_and_original_tests_frozen() -> None:
    root = Path(__file__).resolve().parents[1]
    assert _git_blob_sha(
        (root / "src/predictions_cup/research/offline_causal_alpha_label.py").read_bytes()
    ) == FROZEN_316_SOURCE_SHA
    assert _git_blob_sha(
        (root / "tests/test_offline_causal_alpha_label.py").read_bytes()
    ) == FROZEN_316_TESTS_SHA


def test_unbound_candidate_interval_cannot_be_labeled_from_unrelated_gate(
    tmp_path: Path,
) -> None:
    # The pinned #277 gate fixture contains ONLY "interval-1".
    # The projected candidate claims an ID not in the source archive.
    candidate = replace(_candidate(), paired_interval_id="never-in-gate-archive")
    result = _run(tmp_path, candidate)
    label = result.candidates[0]
    assert result.tape_gate.schema_valid_intervals == 1
    assert label.status == "REJECTED/PAPER_NOT_ADMITTED"
    assert label.rejection == "CANDIDATE_SOURCE_INTERVAL_UNBOUND"
    assert result.admitted_real_net_susqies is None


def test_self_consistent_foreign_exchange_cannot_borrow_gate_provenance(
    tmp_path: Path,
) -> None:
    # The actual #277 fixture maps SIG exchange "879", market "m-879".
    # These self-consistent foreign candidate books have no such pinned join.
    original = _candidate()
    foreign = replace(
        original,
        sig_exchange_id="fictional-unmapped-exchange",
        sig_market_id="fictional-unmapped-market",
        decision_book=replace(
            original.decision_book,
            exchange_id="fictional-unmapped-exchange",
            market_id="fictional-unmapped-market",
        ),
        arrival_book=replace(
            original.arrival_book,
            exchange_id="fictional-unmapped-exchange",
            market_id="fictional-unmapped-market",
        ),
        exit_book=replace(
            original.exit_book,
            exchange_id="fictional-unmapped-exchange",
            market_id="fictional-unmapped-market",
        ) if original.exit_book is not None else None,
    )
    label = _run(tmp_path, foreign).candidates[0]
    assert label.status == "REJECTED/PAPER_NOT_ADMITTED"
    assert label.rejection == "CANDIDATE_MAPPING_NOT_BOUND_TO_TAPE"
    assert label.real_after_cost_susqies is None


def test_pre_entry_settlement_cannot_close_a_later_hypothetical_lot(
    tmp_path: Path,
) -> None:
    original = _candidate()
    # Both claimed issuer and receive times PRECEDE the hypothetical entry.
    forged_early_settlement = SettlementRecord(
        "OPERATOR_RECORDED",
        T - timedelta(minutes=2),
        T - timedelta(minutes=1),
        "SETTLED",
        D("0.80"),
    )
    candidate = replace(
        original, exit_book=None, settlement=forged_early_settlement
    )
    label = _run(tmp_path, candidate).candidates[0]
    assert label.status == "REJECTED/PAPER_NOT_ADMITTED"
    assert label.rejection == "SETTLEMENT_PRE_ENTRY_OR_ARRIVAL"
    assert label.real_after_cost_susqies is None


def test_falsified_original_cash_claim_never_mints_real_after_cost(
    tmp_path: Path,
) -> None:
    original = _candidate()
    settlement = SettlementRecord(
        "OPERATOR_RECORDED",
        T + timedelta(seconds=6),
        T + timedelta(seconds=6, milliseconds=20),
        "SETTLED",
        D("1"),
    )
    candidate = replace(
        original,
        exit_at=T + timedelta(seconds=7),
        exit_book=None,
        settlement=settlement,
        fee_record=replace(
            original.fee_record, source_class="OPERATOR_RECORDED",
            native_fee_id="claimed-issuer-fee", paid_susqies=D("0"),
        ) if original.fee_record is not None else None,
    )
    result = _run(tmp_path, candidate)
    label = result.candidates[0]
    assert label.status == "SCHEMA_ONLY/PAPER_NOT_ADMITTED"
    assert "SOURCE_NOT_EXTERNALLY_AUTHENTICATED" in label.economic_rejections
    assert "FEE_PROOF_NOT_EXTERNALLY_ADMITTED" in label.economic_rejections
    assert label.source_admitted is False and label.paper_admitted is False
    assert result.source_admitted is False and result.paper_admitted is False
    assert label.real_after_cost_susqies is None
    assert result.admitted_real_net_susqies is None


def test_fractional_ladder_cannot_pool_two_not_whole_after_haircut(
    tmp_path: Path,
) -> None:
    original = _candidate()
    thin = (
        Level(D("0.70"), D("1.99")),
        Level(D("0.75"), D("1.99")),
    )
    label = _run(
        tmp_path,
        replace(
            original,
            decision_book=replace(original.decision_book, asks=thin),
            arrival_book=replace(original.arrival_book, asks=thin),
        ),
    ).candidates[0]
    assert label.status == "REJECTED/PAPER_NOT_ADMITTED"
    assert label.rejection == "WHOLE_LOT_DEPTH_UNAVAILABLE"
    assert label.real_after_cost_susqies is None


def test_bad_upstream_clock_rejects_even_self_claimed_positive_label(
    tmp_path: Path,
) -> None:
    result = _run(tmp_path, _candidate(), bad_gate=True)
    assert result.tape_gate.verdict == "PAIRED_DEPTH_NOT_CERTIFIED"
    assert result.candidates[0].status == "REJECTED/PAPER_NOT_ADMITTED"
    assert result.candidates[0].rejection is not None
    assert result.candidates[0].rejection.startswith("UPSTREAM_PAIRED_TAPE:")
    assert result.admitted_real_net_susqies is None
