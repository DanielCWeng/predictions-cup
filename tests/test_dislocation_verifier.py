"""LANE-4 SHADOW-only, synthetic falsification; no traded profits claimed."""

from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from predictions_cup.analysis.dislocation_verifier import (
    AuditedOpportunity,
    AuditStatus,
    AvailableCapital,
    BookReceipt,
    DislocationPolicy,
    SettlementReview,
    SourceReceipt,
    audit_opportunity,
    project_available_capital,
    state_vector_sha256,
    verify_signature,
)
from predictions_cup.analysis.structural_certificates import (
    BookLevel,
    ExecutableBook,
    LegAction,
    StructuralRelationship,
    complement_relationship,
)
from predictions_cup.mapping.crosswalk import document_json
from predictions_cup.mapping.models import (
    MappingClass,
    MappingDirection,
    MappingDocument,
    MappingStatus,
    MarketMapping,
    PolymarketContractIdentity,
)
from predictions_cup.runtime.models import RuntimeOrderState, RuntimePortfolio, RuntimePosition

NOW = datetime(2026, 10, 12, 15, tzinfo=UTC)
NS = 10_000_000_000
KEY = b"test-only-non-production-secret"
RULE = "Synthetic audited binary event: precisely one YES or NO pays one unit."


def _document() -> MappingDocument:
    records = []
    for label in ("yes", "no"):
        external = PolymarketContractIdentity(
            market_id=f"poly-{label}",
            condition_id=f"condition-{label}",
            event_id="event-A",
            question=f"Test outcome {label}?",
            outcomes=("Yes", "No"),
            token_ids=(f"token-{label}", f"token-{label}-other"),
            mapped_outcome="Yes",
            mapped_token_id=f"token-{label}",
        )
        records.append(MarketMapping(
            sig_tournament_id="cup-2026",
            sig_market_id=f"sig-market-{label}",
            sig_market_title=f"Test outcome {label}?",
            sig_exchange_id=label,
            sig_outcome_label="Yes",
            mapping_class=MappingClass.EXACT,
            mapping_direction=MappingDirection.SAME,
            mapping_confidence=Decimal("1"),
            status=MappingStatus.VERIFIED,
            direct_polymarket=external,
            semantic_notes="Human-reviewed exact event",
            resolution_notes="Synthetic fixture only",
        ))
    return MappingDocument(tournament_id="cup-2026", records=tuple(records))


def _setup() -> tuple[
    StructuralRelationship,
    dict[str, BookReceipt],
    MappingDocument,
    SettlementReview,
    SourceReceipt,
    AvailableCapital,
]:
    document = _document()
    mapping_hash = hashlib.sha256(document_json(document).encode()).hexdigest()
    relationship = complement_relationship(
        relationship_id="complete-outcomes",
        event_group_id="event-A",
        yes_instrument_id="yes",
        no_instrument_id="no",
        semantic_proof_version="v1",
        semantic_proof_hash=hashlib.sha256(RULE.encode()).hexdigest(),
        mapping_hash=mapping_hash,
    )
    receipts = {
        label: BookReceipt(
            book=ExecutableBook(
                instrument_id=label,
                observed_at=NOW - timedelta(milliseconds=80),
                bids=(BookLevel(0.36, 20),),
                asks=(BookLevel(0.40, 2), BookLevel(0.43, 8)),
                trusted=True,
            ),
            received_ns=NS - 80_000_000,
            clock_domain="west-process-1",
        ) for label in ("yes", "no")
    }
    review = SettlementReview(
        source_url="https://example.org/signed-fixture-rules",
        rule_text=RULE,
        reviewer_id="research-reviewer",
        approved=True,
        event_id="event-A",
        state_vector_hash=state_vector_sha256(relationship),
    )
    source = SourceReceipt(
        event_id="event-A",
        source_id="verified-source",
        source_kind="OFFICIAL_RESULT",
        published_at=NOW - timedelta(milliseconds=400),
        received_at=NOW - timedelta(milliseconds=200),
        received_ns=NS - 200_000_000,
        clock_domain="west-process-1",
        verified=True,
    )
    capital = project_available_capital(
        RuntimePortfolio(account_trusted=True),
        cash_available=100, gross_limit_units=100,
        snapshot_id="authoritative-fixture-A",
        observed_ns=NS - 50_000_000,
        clock_domain="west-process-1",
    )
    return relationship, receipts, document, review, source, capital


def _audit(**changes: object) -> AuditedOpportunity:
    rel, books, doc, review, source, capital = _setup()
    params = {
        "relationship": rel,
        "receipts": books,
        "mapping_document": doc,
        "review": review,
        "source": source,
        "capital": capital,
        "decision_at": NOW,
        "decision_ns": NS,
        "clock_domain": "west-process-1",
        "signing_key": KEY,
    }
    params.update(changes)
    return audit_opportunity(**params)  # type: ignore[arg-type]


def test_signed_candidate_consumes_full_depth_with_separate_orphan_risk() -> None:
    result = _audit(policy=DislocationPolicy(
        slippage_per_share=0.005, latency_loss_per_share=0.005,
        fee_rate=0.002, min_edge_per_bundle=0.0,
    ))
    assert result.status is AuditStatus.SHADOW_CANDIDATE
    assert result.structural is not None
    assert result.structural.executable_prices[0] > 0.40
    assert result.bundles > 2.0
    assert result.conditional_net_edge is not None and result.conditional_net_edge > 0
    assert result.worst_orphan_unwind_loss is not None
    assert result.worst_orphan_unwind_loss > 0
    assert result.realised_pnl is None
    assert 0 < result.ttl_ns <= 5_000_000_000
    assert verify_signature(result, KEY)
    assert not verify_signature(result, b"wrong-key")
    assert not verify_signature(replace(result, bundles=result.bundles + 1), KEY)


@pytest.mark.parametrize(
    ("changed", "expected"),
    [
        ({"clock_domain": "east-host"}, "CLOCK_OR_FUTURE_SOURCE"),
        ({"decision_ns": NS - 300_000_000}, "CLOCK_OR_FUTURE_SOURCE"),
    ],
)
def test_clock_confusion_rejected(changed: dict[str, object], expected: str) -> None:
    result = _audit(**changed)
    assert result.status is AuditStatus.REJECTED
    assert result.reason == expected


def test_wrong_contract_shuffled_event_and_fake_semantics_rejected() -> None:
    rel, books, doc, review, source, capital = _setup()
    wrong_mapping = doc.model_copy(update={"records": doc.records[:1]})
    assert _audit(mapping_document=wrong_mapping).reason == "MAPPING_HASH_MISMATCH"
    assert _audit(source=replace(source, event_id="other")).reason == "UNVERIFIED_OR_SHUFFLED_EVENT"
    assert _audit(review=replace(review, approved=False)).reason == "SETTLEMENT_PROOF_MISMATCH"
    assert _audit(review=replace(review, rule_text="changed")).reason == "SETTLEMENT_PROOF_MISMATCH"
    assert _audit(review=replace(review, state_vector_hash="bad")).reason == (
        "SETTLEMENT_PROOF_MISMATCH"
    )
    sell = replace(rel, legs=(replace(rel.legs[0], action=LegAction.SELL), rel.legs[1]))
    assert _audit(relationship=sell).reason == "SETTLEMENT_PROOF_MISMATCH"
    signed_sell = replace(review, state_vector_hash=state_vector_sha256(sell))
    assert _audit(relationship=sell, review=signed_sell).reason == (
        "SHORT_OR_SELL_REQUIRES_INVENTORY_PROOF"
    )
    assert len(books) == 2 and capital.trusted


def test_future_stale_crossed_cancel_and_partial_fill_scenarios() -> None:
    rel, books, doc, review, source, capital = _setup()
    stale_books = {
        "yes": replace(books["yes"], received_ns=0),
        "no": books["no"],
    }
    assert _audit(receipts=stale_books).reason == "STALE_CROSSED_OR_UNTRUSTED_BOOK"
    future = replace(source, received_at=NOW + timedelta(seconds=1))
    assert _audit(source=future).reason == "STALE_OR_FUTURE_SOURCE"
    stale = replace(source, received_ns=0)
    assert _audit(source=stale).reason == "STALE_OR_FUTURE_SOURCE"
    crossed = replace(books["yes"], book=replace(
        books["yes"].book, bids=(BookLevel(0.41, 20),)
    ))
    assert _audit(receipts={**books, "yes": crossed}).reason == (
        "STALE_CROSSED_OR_UNTRUSTED_BOOK"
    )
    assert _audit(capital=replace(capital, unresolved_orders=True)).reason == (
        "UNTRUSTED_CAPITAL_OR_PENDING_ORDERS"
    )
    # Cancel / partial fill: no atomic fill guarantee; the hypothetical edge
    # remains separately labelled and carries a positive worst-orphan loss.
    candidate = _audit()
    assert candidate.status is AuditStatus.SHADOW_CANDIDATE
    assert candidate.worst_orphan_unwind_loss is not None
    assert candidate.worst_orphan_unwind_loss > 0


def test_capital_and_pending_reservations_limit_size_and_reject() -> None:
    _, _, _, _, _, capital = _setup()
    ample = _audit()
    capped = _audit(capital=replace(
        capital, cash_available=4, gross_headroom_units=3,
    ))
    assert ample.status is AuditStatus.SHADOW_CANDIDATE
    assert capped.bundles < ample.bundles
    assert capped.bundles == 1.0
    assert _audit(capital=replace(capital, gross_headroom_units=0)).reason == (
        "CAPITAL_EXHAUSTED"
    )


def test_spread_fees_and_adverse_latency_erase_midpoint_edge() -> None:
    _, books, _, _, _, _ = _setup()
    thinner = {
        name: replace(receipt, book=replace(
            receipt.book,
            asks=(BookLevel(0.51, 10),),
            bids=(BookLevel(0.49, 20),),
        ))
        for name, receipt in books.items()
    }
    decision = _audit(receipts=thinner)
    assert decision.status is AuditStatus.NO_EDGE
    assert decision.conditional_net_edge is not None
    assert decision.conditional_net_edge < 0


def test_reject_duplicate_leg_and_invalid_policy() -> None:
    rel, _, _, review, _, _ = _setup()
    duplicate = replace(rel, legs=(rel.legs[0], rel.legs[0]))
    review = replace(review, state_vector_hash=state_vector_sha256(duplicate))
    assert _audit(relationship=duplicate, review=review).reason == "DUPLICATE_LEG"
    with pytest.raises(ValueError, match="finite"):
        DislocationPolicy(fee_rate=float("nan"))
    with pytest.raises(ValueError, match="positive"):
        DislocationPolicy(max_quote_age_ns=0)


def test_trade_lock_negative_payoff_and_signature_tamper() -> None:
    assert _audit(decision_at=datetime(2026, 11, 4, 16, 59, 55, tzinfo=UTC)).reason == (
        "AFTER_2026_WIRE_SAFETY_CUTOFF"
    )
    rel, _, _, review, _, _ = _setup()
    negative = replace(
        rel,
        legs=(replace(rel.legs[0], payoff_by_state=(-0.2, 0.0)), rel.legs[1]),
    )
    attested = replace(review, state_vector_hash=state_vector_sha256(negative))
    assert _audit(relationship=negative, review=attested).reason == (
        "NON_BINARY_OR_NEGATIVE_PAYOFF"
    )
    candidate = _audit()
    assert candidate.structural is not None
    tampered = replace(
        candidate,
        structural=replace(candidate.structural, fee_rate=0.5),
    )
    assert not verify_signature(tampered, KEY)


def test_integer_share_rounding_and_insufficient_one_lot() -> None:
    _, books, _, _, _, capital = _setup()
    # 2-leg bundle: budget=1.5 used to generate 1.5 shares on each leg.
    result = _audit(capital=replace(
        capital, cash_available=100, gross_headroom_units=3,
    ))
    assert result.status is AuditStatus.SHADOW_CANDIDATE
    assert result.bundles == 1.0
    assert result.executable_leg_shares == (("yes", 1), ("no", 1))
    assert all(isinstance(shares, int) for _, shares in result.executable_leg_shares)
    assert result.structural is not None
    assert result.structural.gross_cost == pytest.approx(0.80)

    subshare = _audit(capital=replace(capital, gross_headroom_units=1.9))
    assert subshare.status is AuditStatus.REJECTED
    assert subshare.reason == "NO_WHOLE_SHARE_BUNDLE"
    assert subshare.executable_leg_shares == ()


def test_fractional_ratio_requires_exact_integer_orders() -> None:
    rel, books, doc, review, source, capital = _setup()
    # 0.5 units per leg per bundle: two bundles = one whole share on each leg.
    rat = replace(rel, legs=(
        replace(rel.legs[0], units_per_bundle=0.5),
        replace(rel.legs[1], units_per_bundle=0.5),
    ))
    attested = replace(review, state_vector_hash=state_vector_sha256(rat))
    valid = _audit(relationship=rat, review=attested)
    assert valid.status is AuditStatus.SHADOW_CANDIDATE
    assert valid.bundles % 2 == 0
    assert all(
        isinstance(quantity, int) and quantity > 0
        for _, quantity in valid.executable_leg_shares
    )
    insufficient = _audit(
        relationship=rat, review=attested,
        capital=replace(capital, gross_headroom_units=0.9),
    )
    assert insufficient.reason == "NO_WHOLE_SHARE_BUNDLE"

    # Nearly-rational float cannot be silently represented as exactly 1/3.
    irrational = replace(rel, legs=(
        replace(rel.legs[0], units_per_bundle=0.333333312),
        rel.legs[1],
    ))
    attested = replace(review, state_vector_hash=state_vector_sha256(irrational))
    assert _audit(relationship=irrational, review=attested).reason == (
        "INCOMPATIBLE_FRACTIONAL_LEG_RATIOS"
    )


def test_fractional_depth_stale_capital_and_open_reservations_fail_closed() -> None:
    _, books, _, _, _, cap = _setup()
    fractional = replace(books["yes"], book=replace(
        books["yes"].book,
        asks=(BookLevel(0.40, 0.7), BookLevel(0.43, 8)),
    ))
    assert _audit(receipts={**books, "yes": fractional}).reason == (
        "FRACTIONAL_SIG_BOOK_DEPTH"
    )
    assert _audit(capital=replace(cap, clock_domain="wrong-process")).reason == (
        "UNTRUSTED_CAPITAL_OR_PENDING_ORDERS"
    )
    assert _audit(capital=replace(cap, observed_ns=NS - 2_000_000_000)).reason == (
        "UNTRUSTED_CAPITAL_OR_PENDING_ORDERS"
    )
    portfolio = RuntimePortfolio(
        account_trusted=True,
        positions=(RuntimePosition(
            exchange_id="yes", market_id="market-yes", tournament_id="cup",
            gross_exposure=97.0,
        ),),
    )
    proj = project_available_capital(
        portfolio, cash_available=4, gross_limit_units=100,
        snapshot_id="account-v1", observed_ns=NS - 10_000_000,
        clock_domain="west-process-1",
    )
    assert proj.gross_headroom_units == 3.0
    assert _audit(capital=proj).bundles == 1.0

    pending = RuntimePortfolio(
        account_trusted=True,
        orders=(RuntimeOrderState(
            logical_intent_id="intent-1", exchange_id="yes",
            market_id="market-yes", tournament_id="cup",
            reserved_exposure=2.0, open=True, uncertain=False,
        ),),
    )
    reservation = project_available_capital(
        pending, cash_available=100, gross_limit_units=100,
        snapshot_id="account-with-open", observed_ns=NS - 10_000_000,
        clock_domain="west-process-1",
    )
    assert reservation.gross_headroom_units == 98.0
    assert reservation.unresolved_orders
    assert _audit(capital=reservation).reason == "UNTRUSTED_CAPITAL_OR_PENDING_ORDERS"

    bad = project_available_capital(
        RuntimePortfolio(account_trusted=False), cash_available=100,
        gross_limit_units=100, snapshot_id="untrusted",
        observed_ns=NS, clock_domain="west-process-1",
    )
    assert _audit(capital=bad).status is AuditStatus.REJECTED


def test_near_threshold_fee_edge_and_cutoff_headroom() -> None:
    _, books, _, _, _, _ = _setup()
    neutral = {
        name: replace(receipt, book=replace(
            receipt.book, asks=(BookLevel(0.495, 10),),
        )) for name, receipt in books.items()
    }
    # 1.0 payout - (0.495 + 0.495) - 0.010 haircuts = exactly zero.
    zero = _audit(
        receipts=neutral,
        policy=DislocationPolicy(
            fee_rate=0.0, slippage_per_share=0.0025,
            latency_loss_per_share=0.0025, min_edge_per_bundle=0.0,
        ),
    )
    assert zero.status is AuditStatus.NO_EDGE
    fees = _audit(
        receipts=neutral,
        policy=DislocationPolicy(
            fee_rate=0.005, slippage_per_share=0.0,
            latency_loss_per_share=0.0, min_edge_per_bundle=0.005,
        ),
    )
    assert fees.status is AuditStatus.NO_EDGE
    near = datetime(2026, 11, 4, 16, 59, 54, 950000, tzinfo=UTC)
    rel, quotes, doc, review, event, capital = _setup()
    fresh = {name: replace(receipt, book=replace(
        receipt.book, observed_at=near - timedelta(milliseconds=80),
    )) for name, receipt in quotes.items()}
    event = replace(
        event, published_at=near - timedelta(milliseconds=400),
        received_at=near - timedelta(milliseconds=200),
    )
    kwargs = dict(
        relationship=rel, receipts=fresh, mapping_document=doc,
        review=review, source=event, capital=capital, signing_key=KEY,
        decision_ns=NS, clock_domain="west-process-1",
    )
    denied = audit_opportunity(**kwargs, decision_at=near)
    assert denied.reason == "INSUFFICIENT_ACTION_TTL"
    allowed = audit_opportunity(
        **kwargs, decision_at=near - timedelta(milliseconds=100),
    )
    assert allowed.status is AuditStatus.SHADOW_CANDIDATE
    assert allowed.ttl_ns <= 150_000_000
