"""LANE-4: fail-closed, signed SHADOW audit around STRUCT-SCAN-001.

Consumes already captured books; does not subscribe to feeds, register strategies,
place orders or change execution/risk settings. All economic edges are conditional
on *every leg* filling at the recorded depth, never realised profit.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum

from predictions_cup.analysis.structural_certificates import (
    BookLevel,
    ExecutableBook,
    LegAction,
    StructuralCertificate,
    StructuralRelationship,
    StructuralStatus,
    evaluate_relationship,
)
from predictions_cup.mapping.crosswalk import document_json
from predictions_cup.mapping.models import MappingClass, MappingDocument, MappingStatus


CUP_2026_TRADE_LOCK = datetime(2026, 11, 4, 17, tzinfo=UTC)


class AuditStatus(StrEnum):
    REJECTED = "REJECTED"
    NO_EDGE = "NO_EDGE"
    SHADOW_CANDIDATE = "SHADOW_CANDIDATE"


@dataclass(frozen=True, slots=True)
class SourceReceipt:
    """Independent source/event receipt on the SAME local monotonic clock."""

    event_id: str
    source_id: str
    source_kind: str
    published_at: datetime
    received_at: datetime
    received_ns: int
    clock_domain: str
    verified: bool


@dataclass(frozen=True, slots=True)
class BookReceipt:
    book: ExecutableBook
    received_ns: int
    clock_domain: str


@dataclass(frozen=True, slots=True)
class SettlementReview:
    """Human-attested terminal-state proof, not an inferred relationship."""

    source_url: str
    rule_text: str
    reviewer_id: str
    approved: bool
    event_id: str
    state_vector_hash: str


@dataclass(frozen=True, slots=True)
class AvailableCapital:
    cash: float
    gross_units: float
    pending_reserved: float
    trusted: bool
    unresolved_orders: bool


@dataclass(frozen=True, slots=True)
class DislocationPolicy:
    max_quote_age_ns: int = 1_000_000_000
    max_source_age_ns: int = 5_000_000_000
    max_publication_delay_s: float = 10.0
    maximum_bundles: float = 100.0
    fee_rate: float = 0.0
    slippage_per_share: float = 0.02
    latency_loss_per_share: float = 0.02
    min_edge_per_bundle: float = 0.01

    def __post_init__(self) -> None:
        for value in (
            self.max_publication_delay_s,
            self.maximum_bundles,
            self.fee_rate,
            self.slippage_per_share,
            self.latency_loss_per_share,
            self.min_edge_per_bundle,
        ):
            if not math.isfinite(value) or value < 0:
                raise ValueError("policy amounts must be finite nonnegative")
        if (
            self.max_quote_age_ns <= 0
            or self.max_source_age_ns <= 0
            or self.maximum_bundles <= 0
        ):
            raise ValueError("policy ages and maximum_bundles must be positive")


@dataclass(frozen=True, slots=True)
class AuditedOpportunity:
    status: AuditStatus
    reason: str
    relationship_id: str
    source_event_id: str
    mapping_sha256: str
    evidence_sha256: str
    structural: StructuralCertificate | None
    bundles: float
    conditional_net_edge: float | None
    worst_orphan_unwind_loss: float | None
    ttl_ns: int
    signature: str

    @property
    def realised_pnl(self) -> None:
        """No fills are observed or simulated in this read-only evaluator."""
        return None


def state_vector_sha256(relationship: StructuralRelationship) -> str:
    """Pin the entire independently reviewed payoff vector and trade direction."""
    payload = {
        "relationship_id": relationship.relationship_id,
        "event_group_id": relationship.event_group_id,
        "states": relationship.payoff_state_ids,
        "legs": [
            (leg.instrument_id, leg.action.value, leg.units_per_bundle, leg.payoff_by_state)
            for leg in relationship.legs
        ],
    }
    return hashlib.sha256(_canonical(payload)).hexdigest()


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def verify_signature(opportunity: AuditedOpportunity, signing_key: bytes) -> bool:
    if not signing_key:
        return False
    return hmac.compare_digest(
        opportunity.signature,
        _sign(opportunity, signing_key),
    )


def _sign(opportunity: AuditedOpportunity, key: bytes) -> str:
    payload = {
        "status": opportunity.status.value,
        "reason": opportunity.reason,
        "relationship_id": opportunity.relationship_id,
        "source_event_id": opportunity.source_event_id,
        "mapping_sha256": opportunity.mapping_sha256,
        "evidence_sha256": opportunity.evidence_sha256,
        "structural": (
            opportunity.structural.to_dict() if opportunity.structural else None
        ),
        "bundles": opportunity.bundles,
        "conditional_net_edge": opportunity.conditional_net_edge,
        "worst_orphan_unwind_loss": opportunity.worst_orphan_unwind_loss,
        "ttl_ns": opportunity.ttl_ns,
    }
    return hmac.new(key, _canonical(payload), hashlib.sha256).hexdigest()


def _walk(levels: Sequence[BookLevel], amount: float) -> float | None:
    remaining = amount
    notional = 0.0
    for level in levels:
        taken = min(remaining, level.size)
        notional += taken * level.price
        remaining -= taken
        if remaining <= 1e-9:
            return notional
    return None


def _orphan_loss(
    relationship: StructuralRelationship,
    receipts: Mapping[str, BookReceipt],
    bundles: float,
    policy: DislocationPolicy,
) -> float:
    """Stress total committed cash if any subset fills and ALL exit depth cancels.

    Credit zero recovery rather than today's bid: a future unwind book is not
    known when the event is observed. All BUY terminal payoffs must be nonnegative.
    """
    risk = 0.0
    for leg in relationship.legs:
        amount = bundles * leg.units_per_bundle
        book = receipts[leg.instrument_id].book
        entry = _walk(book.asks, amount)
        if entry is None:
            raise ValueError("full depth disappeared before orphan stress")
        risk += entry * (1 + policy.fee_rate) + amount * (
            policy.latency_loss_per_share + policy.slippage_per_share
        )
    return risk


def audit_opportunity(
    relationship: StructuralRelationship,
    receipts: Mapping[str, BookReceipt],
    mapping_document: MappingDocument,
    review: SettlementReview,
    source: SourceReceipt,
    capital: AvailableCapital,
    *,
    decision_at: datetime,
    decision_ns: int,
    clock_domain: str,
    signing_key: bytes,
    policy: DislocationPolicy | None = None,
) -> AuditedOpportunity:
    """Evaluate one already-observed event using FULL DEPTH; never place orders."""
    if not signing_key:
        raise ValueError("nonempty local certificate signing key required")
    p = policy or DislocationPolicy()
    mapping_hash = hashlib.sha256(document_json(mapping_document).encode()).hexdigest()
    book_evidence = {
        instrument: {
            "observed_at": receipt.book.observed_at.astimezone(UTC).isoformat(),
            "received_ns": receipt.received_ns,
            "clock_domain": receipt.clock_domain,
            "trusted": receipt.book.trusted,
            "bids": [(v.price, v.size) for v in receipt.book.bids],
            "asks": [(v.price, v.size) for v in receipt.book.asks],
        }
        for instrument, receipt in sorted(receipts.items())
    }
    evidence_hash = hashlib.sha256(_canonical({
        "source": (source.event_id, source.source_id, source.source_kind,
                   source.published_at.isoformat(), source.received_at.isoformat(),
                   source.received_ns, source.clock_domain, source.verified),
        "decision": (decision_at.isoformat(), decision_ns, clock_domain),
        "review": (review.source_url, review.rule_text, review.reviewer_id,
                   review.approved, review.event_id, review.state_vector_hash),
        "capital": (capital.cash, capital.gross_units, capital.pending_reserved,
                    capital.trusted, capital.unresolved_orders),
        "policy": vars_policy(p),
        "books": book_evidence,
    })).hexdigest()
    ttl_ns = 0
    if source.received_ns <= decision_ns:
        ttl_ns = max(0, p.max_source_age_ns - (decision_ns - source.received_ns))

    def emit(
        status: AuditStatus,
        reason: str,
        structural: StructuralCertificate | None = None,
        orphan: float | None = None,
    ) -> AuditedOpportunity:
        opportunity = AuditedOpportunity(
            status=status,
            reason=reason,
            relationship_id=relationship.relationship_id,
            source_event_id=source.event_id,
            mapping_sha256=mapping_hash,
            evidence_sha256=evidence_hash,
            structural=structural,
            bundles=structural.available_size if structural else 0.0,
            conditional_net_edge=structural.net_edge if structural else None,
            worst_orphan_unwind_loss=orphan,
            ttl_ns=ttl_ns,
            signature="",
        )
        return replace(opportunity, signature=_sign(opportunity, signing_key))

    if (
        decision_at.tzinfo is None
        or decision_at.utcoffset() is None
        or source.published_at.tzinfo is None
        or source.published_at.utcoffset() is None
        or source.received_at.tzinfo is None
        or source.received_at.utcoffset() is None
    ):
        return emit(AuditStatus.REJECTED, "NAIVE_CLOCK")
    if decision_at >= CUP_2026_TRADE_LOCK:
        return emit(AuditStatus.REJECTED, "AFTER_2026_TRADE_LOCK")
    if (
        not clock_domain.strip()
        or source.clock_domain != clock_domain
        or decision_ns < source.received_ns
        or source.received_ns < 0
        or decision_ns < 0
    ):
        return emit(AuditStatus.REJECTED, "CLOCK_OR_FUTURE_SOURCE")
    if (
        not source.verified
        or not source.source_id.strip()
        or not source.event_id.strip()
        or not source.source_kind.strip()
        or source.event_id != relationship.event_group_id
    ):
        return emit(AuditStatus.REJECTED, "UNVERIFIED_OR_SHUFFLED_EVENT")
    publication_delay = (source.received_at - source.published_at).total_seconds()
    if (
        publication_delay < 0
        or publication_delay > p.max_publication_delay_s
        or source.received_at > decision_at
        or decision_ns - source.received_ns > p.max_source_age_ns
    ):
        return emit(AuditStatus.REJECTED, "STALE_OR_FUTURE_SOURCE")
    if (
        not capital.trusted
        or capital.unresolved_orders
        or any(
            not math.isfinite(v) or v < 0
            for v in (capital.cash, capital.gross_units, capital.pending_reserved)
        )
    ):
        return emit(AuditStatus.REJECTED, "UNTRUSTED_CAPITAL_OR_PENDING_ORDERS")
    if (
        not review.approved
        or not review.reviewer_id.strip()
        or not review.source_url.startswith("https://")
        or not review.rule_text.strip()
        or review.event_id != source.event_id
        or review.state_vector_hash != state_vector_sha256(relationship)
        or hashlib.sha256(review.rule_text.encode()).hexdigest()
        != relationship.semantic_proof_hash
        or not relationship.semantics_verified
    ):
        return emit(AuditStatus.REJECTED, "SETTLEMENT_PROOF_MISMATCH")
    if relationship.mapping_hash != mapping_hash:
        return emit(AuditStatus.REJECTED, "MAPPING_HASH_MISMATCH")
    if len({leg.instrument_id for leg in relationship.legs}) != len(relationship.legs):
        return emit(AuditStatus.REJECTED, "DUPLICATE_LEG")
    if any(
        any(value not in (0.0, 1.0) for value in leg.payoff_by_state)
        for leg in relationship.legs
    ):
        return emit(AuditStatus.REJECTED, "NON_BINARY_OR_NEGATIVE_PAYOFF")
    if any(leg.action is not LegAction.BUY for leg in relationship.legs):
        # Existing STRUCT-SCAN SELL math does not establish owned inventory.
        return emit(AuditStatus.REJECTED, "SHORT_OR_SELL_REQUIRES_INVENTORY_PROOF")

    pm_ids: set[str] = set()
    for leg in relationship.legs:
        try:
            record = mapping_document.mapping_for_sig_exchange(leg.instrument_id)
        except KeyError:
            return emit(AuditStatus.REJECTED, "UNMAPPED_SIG_LEG")
        external = record.direct_polymarket
        if (
            record.status is not MappingStatus.VERIFIED
            or record.mapping_class is not MappingClass.EXACT
            or external is None
            or external.event_id != source.event_id
            or external.mapped_token_id in pm_ids
        ):
            return emit(AuditStatus.REJECTED, "NOT_SAME_EVENT_VERIFIED_EXACT")
        pm_ids.add(external.mapped_token_id)

    for leg in relationship.legs:
        receipt = receipts.get(leg.instrument_id)
        if receipt is None:
            return emit(AuditStatus.REJECTED, "MISSING_BOOK")
        book = receipt.book
        if (
            book.instrument_id != leg.instrument_id
            or not book.trusted
            or receipt.clock_domain != clock_domain
            or receipt.received_ns < 0
            or receipt.received_ns > decision_ns
            or decision_ns - receipt.received_ns > p.max_quote_age_ns
            or book.observed_at > decision_at
            or not book.asks
            or not book.bids
            or book.bids[0].price >= book.asks[0].price
        ):
            return emit(AuditStatus.REJECTED, "STALE_CROSSED_OR_UNTRUSTED_BOOK")

    ttl_ns = min(
        ttl_ns,
        *(p.max_quote_age_ns - (decision_ns - receipts[leg.instrument_id].received_ns)
          for leg in relationship.legs),
    )
    shares_per_bundle = sum(leg.units_per_bundle for leg in relationship.legs)
    # Conservative capital: reserve the MAX $1/share cost, not midpoint pricing.
    worst_cost_per_bundle = shares_per_bundle * (
        1 + p.fee_rate + p.slippage_per_share + p.latency_loss_per_share
    )
    free_cash = max(0.0, capital.cash - capital.pending_reserved)
    free_gross = max(0.0, capital.gross_units - capital.pending_reserved)
    budget = min(
        p.maximum_bundles,
        free_cash / worst_cost_per_bundle,
        free_gross / shares_per_bundle,
    )
    if budget <= 1e-9:
        return emit(AuditStatus.REJECTED, "CAPITAL_EXHAUSTED")

    # Trim asks to the allowed bundle size. Retain actual bids to stress a
    # partially filled bundle with a forced immediate unwind.
    trimmed: dict[str, ExecutableBook] = {}
    for leg in relationship.legs:
        b = receipts[leg.instrument_id].book
        remaining = budget * leg.units_per_bundle
        asks = []
        for level in b.asks:
            if remaining <= 0:
                break
            taken = min(remaining, level.size)
            if taken > 0:
                asks.append(BookLevel(level.price, taken))
                remaining -= taken
        trimmed[leg.instrument_id] = replace(b, asks=tuple(asks))
    certificate = evaluate_relationship(
        relationship, trimmed, observed_at=decision_at,
        mapping_valid=True,
        max_book_age_seconds=p.max_quote_age_ns / 1e9,
        fee_rate=p.fee_rate,
        slippage_per_unit=(
            p.slippage_per_share + p.latency_loss_per_share
        ) * shares_per_bundle,
        minimum_net_edge=p.min_edge_per_bundle,
    )
    if (
        certificate.certificate_status is not StructuralStatus.EXECUTABLE_VIOLATION
        or certificate.available_size <= 1e-9
    ):
        return emit(AuditStatus.NO_EDGE, certificate.certificate_status.value, certificate)
    risk = _orphan_loss(relationship, receipts, certificate.available_size, p)
    return emit(AuditStatus.SHADOW_CANDIDATE, "CONDITIONAL_ALL_LEGS_FILL_ONLY", certificate, risk)


def vars_policy(policy: DislocationPolicy) -> dict[str, float | int]:
    return {
        "max_quote_age_ns": policy.max_quote_age_ns,
        "max_source_age_ns": policy.max_source_age_ns,
        "max_publication_delay_s": policy.max_publication_delay_s,
        "maximum_bundles": policy.maximum_bundles,
        "fee_rate": policy.fee_rate,
        "slippage_per_share": policy.slippage_per_share,
        "latency_loss_per_share": policy.latency_loss_per_share,
        "min_edge_per_bundle": policy.min_edge_per_bundle,
    }
