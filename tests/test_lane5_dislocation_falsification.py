"""LANE-5 independent challenge to LANE-4 fractional SIG sizing and order lock.

Tests target PR #210 without modifying its economics, models, risk, live or feed code.
The fixtures come from the frozen author test suite; the falsification criteria
are independently defined, and now run as non-XFAIL acceptance regressions.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from predictions_cup.analysis.dislocation_verifier import (
    AuditStatus,
    DislocationPolicy,
    verify_signature,
)
from predictions_cup.analysis.structural_certificates import BookLevel
from test_dislocation_verifier import KEY, NS, _audit, _setup


@pytest.mark.parametrize("available_gross", [0.5, 1.0, 1.5])
def test_not_enough_gross_for_two_whole_shares_abstains(
    available_gross: float,
) -> None:
    _relationship, _receipts, _mapping, _review, _source, capital = _setup()
    candidate = _audit(capital=replace(capital, gross_headroom_units=available_gross))
    assert candidate.status is not AuditStatus.SHADOW_CANDIDATE
    assert candidate.bundles == 0.0


def test_fractional_policy_maximum_cannot_certify_nonplaceable_order() -> None:
    candidate = _audit(policy=DislocationPolicy(maximum_bundles=0.4))
    assert candidate.status is not AuditStatus.SHADOW_CANDIDATE


def test_shadow_actionable_candidate_must_expire_by_order_safety_buffer() -> None:
    relationship, books, mapping, review, source, capital = _setup()
    now = datetime(2026, 11, 4, 16, 59, 55, tzinfo=UTC)
    updated_books = {
        instrument: replace(
            receipt,
            book=replace(
                receipt.book,
                observed_at=now - timedelta(milliseconds=80),
            ),
        )
        for instrument, receipt in books.items()
    }
    updated_source = replace(
        source,
        published_at=now - timedelta(milliseconds=400),
        received_at=now - timedelta(milliseconds=200),
    )
    candidate = _audit(
        relationship=relationship,
        receipts=updated_books,
        mapping_document=mapping,
        review=review,
        source=updated_source,
        capital=capital,
        decision_at=now,
        decision_ns=NS,
    )
    assert candidate.status is AuditStatus.REJECTED
    assert candidate.reason == "AFTER_2026_WIRE_SAFETY_CUTOFF"


def test_existing_candidate_still_authenticates_signed_evidence() -> None:
    candidate = _audit()
    assert candidate.status is AuditStatus.SHADOW_CANDIDATE
    assert candidate.structural is not None
    assert candidate.conditional_net_edge is not None
    assert candidate.worst_orphan_unwind_loss is not None
    assert candidate.realised_pnl is None
    assert verify_signature(candidate, KEY)
    assert not verify_signature(
        replace(candidate, worst_orphan_unwind_loss=0), KEY
    )


def test_fractional_sig_book_depth_abstains_before_sizing() -> None:
    _rel, books, _map, _review, _source, _cap = _setup()
    short = replace(
        books["yes"],
        book=replace(books["yes"].book, asks=(BookLevel(0.40, 1.4),)),
    )
    outcome = _audit(receipts={**books, "yes": short})
    assert outcome.status is AuditStatus.REJECTED
    assert outcome.reason == "FRACTIONAL_SIG_BOOK_DEPTH"


def test_gross_headroom_and_cash_after_snapshot_staleness_rejected() -> None:
    _rel, _books, _map, _review, _source, capital = _setup()
    expired = replace(capital, observed_ns=0)
    result = _audit(capital=expired)
    assert result.status is AuditStatus.REJECTED
    assert result.reason == "UNTRUSTED_CAPITAL_OR_PENDING_ORDERS"
