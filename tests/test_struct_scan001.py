from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from predictions_cup.analysis.structural_certificates import (
    BookLevel,
    ExecutableBook,
    LegAction,
    StructuralStatus,
    complement_relationship,
    evaluate_relationship,
    exhaustive_partition_relationship,
)


NOW = datetime(2026, 10, 1, 16, 0, tzinfo=UTC)


def _book(
    instrument_id: str,
    *,
    bid: float,
    ask: float,
    bid_size: float = 10.0,
    ask_size: float = 10.0,
    trusted: bool = True,
    age_seconds: float = 0.0,
) -> ExecutableBook:
    return ExecutableBook(
        instrument_id=instrument_id,
        observed_at=NOW - timedelta(seconds=age_seconds),
        bids=(BookLevel(bid, bid_size),),
        asks=(BookLevel(ask, ask_size),),
        trusted=trusted,
    )


def _complement(*, action: LegAction = LegAction.BUY):
    return complement_relationship(
        relationship_id="rel-complement",
        yes_instrument_id="yes",
        no_instrument_id="no",
        semantic_proof_version="proof-v1",
        semantic_proof_hash="semantic-hash",
        mapping_hash="mapping-hash",
        action=action,
    )


def test_valid_complement_is_depth_aware_executable_violation() -> None:
    certificate = evaluate_relationship(
        _complement(),
        {
            "yes": _book("yes", bid=0.47, ask=0.48, ask_size=4.0),
            "no": _book("no", bid=0.48, ask=0.49, ask_size=3.0),
        },
        observed_at=NOW,
    )
    assert certificate.certificate_status is StructuralStatus.EXECUTABLE_VIOLATION
    assert certificate.available_size == pytest.approx(3.0)
    assert certificate.gross_cost == pytest.approx(2.91)
    assert certificate.worst_case_payoff == pytest.approx(3.0)
    assert certificate.net_edge == pytest.approx(0.09)


def test_sell_complement_violation_uses_bids_not_midpoint() -> None:
    certificate = evaluate_relationship(
        _complement(action=LegAction.SELL),
        {
            "yes": _book("yes", bid=0.52, ask=0.54),
            "no": _book("no", bid=0.51, ask=0.53),
        },
        observed_at=NOW,
    )
    assert certificate.certificate_status is StructuralStatus.EXECUTABLE_VIOLATION
    assert certificate.net_edge is not None and certificate.net_edge > 0.0


def test_valid_exhaustive_partition_and_negrisk_alias() -> None:
    relationship = exhaustive_partition_relationship(
        relationship_id="partition",
        instrument_ids=("a", "b", "c"),
        semantic_proof_version="proof-v1",
        semantic_proof_hash="proof",
        mapping_hash="mapping",
        relationship_type="NEGRISK",
    )
    certificate = evaluate_relationship(
        relationship,
        {
            "a": _book("a", bid=0.29, ask=0.30),
            "b": _book("b", bid=0.30, ask=0.31),
            "c": _book("c", bid=0.31, ask=0.32),
        },
        observed_at=NOW,
    )
    assert certificate.relationship_type == "NEGRISK"
    assert certificate.certificate_status is StructuralStatus.EXECUTABLE_VIOLATION
    assert certificate.net_edge == pytest.approx(0.7)


def test_midpoint_only_apparent_edge_does_not_survive_executable_asks() -> None:
    certificate = evaluate_relationship(
        _complement(),
        {
            "yes": _book("yes", bid=0.45, ask=0.52),
            "no": _book("no", bid=0.45, ask=0.52),
        },
        observed_at=NOW,
    )
    assert certificate.certificate_status is StructuralStatus.NO_VIOLATION
    assert certificate.net_edge is not None and certificate.net_edge < 0.0


def test_missing_depth_fails_closed() -> None:
    certificate = evaluate_relationship(
        _complement(),
        {"yes": _book("yes", bid=0.47, ask=0.48)},
        observed_at=NOW,
    )
    assert certificate.certificate_status is StructuralStatus.INSUFFICIENT_DEPTH


def test_tiny_executable_size_is_reported_as_tiny_not_scaled_up() -> None:
    certificate = evaluate_relationship(
        _complement(),
        {
            "yes": _book(
                "yes",
                bid=0.47,
                ask=0.48,
                ask_size=0.1,
            ),
            "no": _book(
                "no",
                bid=0.48,
                ask=0.49,
                ask_size=0.1,
            ),
        },
        observed_at=NOW,
    )
    assert certificate.certificate_status is StructuralStatus.EXECUTABLE_VIOLATION
    assert certificate.available_size == pytest.approx(0.1)


def test_stale_untrusted_mapping_and_semantics_fail_closed() -> None:
    stale = evaluate_relationship(
        _complement(),
        {
            "yes": _book("yes", bid=0.47, ask=0.48, age_seconds=3.0),
            "no": _book("no", bid=0.48, ask=0.49),
        },
        observed_at=NOW,
        max_book_age_seconds=2.0,
    )
    assert stale.certificate_status is StructuralStatus.STALE_BOOK

    untrusted = evaluate_relationship(
        _complement(),
        {
            "yes": _book("yes", bid=0.47, ask=0.48, trusted=False),
            "no": _book("no", bid=0.48, ask=0.49),
        },
        observed_at=NOW,
    )
    assert untrusted.certificate_status is StructuralStatus.UNTRUSTED_BOOK

    mapping = evaluate_relationship(
        _complement(),
        {
            "yes": _book("yes", bid=0.47, ask=0.48),
            "no": _book("no", bid=0.48, ask=0.49),
        },
        observed_at=NOW,
        mapping_valid=False,
    )
    assert mapping.certificate_status is StructuralStatus.MAPPING_INVALID

    relationship = replace(_complement(), semantics_verified=False)
    semantic = evaluate_relationship(
        relationship,
        {
            "yes": _book("yes", bid=0.47, ask=0.48),
            "no": _book("no", bid=0.48, ask=0.49),
        },
        observed_at=NOW,
    )
    assert semantic.certificate_status is StructuralStatus.SEMANTICS_UNVERIFIED


def test_fees_erase_apparent_edge() -> None:
    certificate = evaluate_relationship(
        _complement(),
        {
            "yes": _book("yes", bid=0.48, ask=0.49),
            "no": _book("no", bid=0.49, ask=0.50),
        },
        observed_at=NOW,
        fee_rate=0.02,
    )
    assert certificate.certificate_status is StructuralStatus.NO_VIOLATION


def test_multi_level_vwap_limits_maximum_economic_size() -> None:
    yes = ExecutableBook(
        instrument_id="yes",
        observed_at=NOW,
        bids=(BookLevel(0.47, 10.0),),
        asks=(
            BookLevel(0.48, 1.0),
            BookLevel(0.55, 9.0),
        ),
    )
    no = ExecutableBook(
        instrument_id="no",
        observed_at=NOW,
        bids=(BookLevel(0.48, 10.0),),
        asks=(
            BookLevel(0.49, 1.0),
            BookLevel(0.55, 9.0),
        ),
    )
    certificate = evaluate_relationship(
        _complement(),
        {"yes": yes, "no": no},
        observed_at=NOW,
    )
    assert certificate.certificate_status is StructuralStatus.EXECUTABLE_VIOLATION
    assert 1.0 < certificate.available_size < 2.0
    assert certificate.available_size == pytest.approx(1.3, abs=1e-6)
