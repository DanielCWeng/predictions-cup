"""STRUCT-SCAN-001: depth-aware executable hard-relationship certificates."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from predictions_cup.analysis.evidence import canonical_config_hash, sha256_file


class StructuralStatus(StrEnum):
    EXECUTABLE_VIOLATION = "EXECUTABLE_VIOLATION"
    NO_VIOLATION = "NO_VIOLATION"
    INSUFFICIENT_DEPTH = "INSUFFICIENT_DEPTH"
    STALE_BOOK = "STALE_BOOK"
    UNTRUSTED_BOOK = "UNTRUSTED_BOOK"
    SEMANTICS_UNVERIFIED = "SEMANTICS_UNVERIFIED"
    MAPPING_INVALID = "MAPPING_INVALID"


class LegAction(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


@dataclass(frozen=True, slots=True)
class BookLevel:
    price: float
    size: float

    def __post_init__(self) -> None:
        if not math.isfinite(self.price) or not 0.0 <= self.price <= 1.0:
            raise ValueError("book price must be finite within [0,1]")
        if not math.isfinite(self.size) or self.size <= 0.0:
            raise ValueError("book size must be positive and finite")


@dataclass(frozen=True, slots=True)
class ExecutableBook:
    instrument_id: str
    observed_at: datetime
    bids: tuple[BookLevel, ...]
    asks: tuple[BookLevel, ...]
    trusted: bool = True

    def __post_init__(self) -> None:
        if not self.instrument_id.strip():
            raise ValueError("instrument_id must not be blank")
        if self.observed_at.tzinfo is None or self.observed_at.utcoffset() is None:
            raise ValueError("book timestamp must be timezone-aware")
        if any(
            left.price < right.price
            for left, right in zip(self.bids, self.bids[1:], strict=False)
        ):
            raise ValueError("bids must be sorted best-to-worst descending")
        if any(
            left.price > right.price
            for left, right in zip(self.asks, self.asks[1:], strict=False)
        ):
            raise ValueError("asks must be sorted best-to-worst ascending")


@dataclass(frozen=True, slots=True)
class StructuralLeg:
    instrument_id: str
    action: LegAction
    units_per_bundle: float
    payoff_by_state: tuple[float, ...]

    def __post_init__(self) -> None:
        if not self.instrument_id.strip():
            raise ValueError("leg instrument_id must not be blank")
        if not math.isfinite(self.units_per_bundle) or self.units_per_bundle <= 0.0:
            raise ValueError("units_per_bundle must be positive and finite")
        if not self.payoff_by_state:
            raise ValueError("payoff_by_state must not be empty")
        if any(not math.isfinite(value) for value in self.payoff_by_state):
            raise ValueError("payoffs must be finite")


@dataclass(frozen=True, slots=True)
class StructuralRelationship:
    relationship_id: str
    relationship_type: str
    legs: tuple[StructuralLeg, ...]
    payoff_state_ids: tuple[str, ...]
    semantic_proof_version: str
    semantic_proof_hash: str
    mapping_hash: str
    semantics_verified: bool

    def __post_init__(self) -> None:
        if not self.relationship_id.strip() or not self.relationship_type.strip():
            raise ValueError("relationship identity/type must not be blank")
        if len(self.legs) < 2:
            raise ValueError("structural relationship requires at least two legs")
        if len(self.payoff_state_ids) < 2:
            raise ValueError("at least two terminal states are required")
        if len(self.payoff_state_ids) != len(set(self.payoff_state_ids)):
            raise ValueError("payoff state IDs must be unique")
        if any(
            len(leg.payoff_by_state) != len(self.payoff_state_ids)
            for leg in self.legs
        ):
            raise ValueError("every leg must define every terminal payoff state")
        if (
            not self.semantic_proof_version.strip()
            or not self.semantic_proof_hash.strip()
        ):
            raise ValueError("semantic proof provenance must not be blank")
        if not self.mapping_hash.strip():
            raise ValueError("mapping_hash must not be blank")


@dataclass(frozen=True, slots=True)
class StructuralCertificate:
    certificate_id: str
    relationship_id: str
    relationship_type: str
    observed_at: datetime
    leg_ids: tuple[str, ...]
    semantic_proof_version: str
    semantic_proof_hash: str
    mapping_hash: str
    executable_prices: tuple[float, ...]
    available_size: float
    payoff_states: tuple[tuple[str, float], ...]
    worst_case_payoff: float | None
    gross_cost: float | None
    gross_edge: float | None
    fees: float | None
    slippage_assumption: float
    net_edge: float | None
    certificate_status: StructuralStatus
    reasons: tuple[str, ...]
    evidence_refs: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "certificate_id": self.certificate_id,
            "relationship_id": self.relationship_id,
            "relationship_type": self.relationship_type,
            "observed_at": self.observed_at.astimezone(UTC).isoformat(),
            "legs": list(self.leg_ids),
            "semantic_proof_version": self.semantic_proof_version,
            "semantic_proof_hash": self.semantic_proof_hash,
            "mapping_hash": self.mapping_hash,
            "executable_prices": list(self.executable_prices),
            "available_size": self.available_size,
            "payoff_states": [
                {"state_id": state_id, "portfolio_payoff": payoff}
                for state_id, payoff in self.payoff_states
            ],
            "worst_case_payoff": self.worst_case_payoff,
            "gross_cost": self.gross_cost,
            "gross_edge": self.gross_edge,
            "fees": self.fees,
            "slippage_assumption": self.slippage_assumption,
            "net_edge": self.net_edge,
            "certificate_status": self.certificate_status.value,
            "reasons": list(self.reasons),
            "evidence_refs": list(self.evidence_refs),
        }


def _consume(
    levels: Sequence[BookLevel],
    quantity: float,
) -> tuple[float, float] | None:
    if quantity <= 0:
        raise ValueError("quantity must be positive")
    remaining = quantity
    notional = 0.0
    filled = 0.0
    for level in levels:
        take = min(remaining, level.size)
        notional += take * level.price
        filled += take
        remaining -= take
        if remaining <= 1e-12:
            return notional, notional / filled
    return None


def _max_leg_quantity(book: ExecutableBook, action: LegAction) -> float:
    levels = book.asks if action is LegAction.BUY else book.bids
    return sum(level.size for level in levels)


def _state_payoffs(
    relationship: StructuralRelationship,
    bundle_size: float,
) -> tuple[tuple[str, float], ...]:
    output: list[tuple[str, float]] = []
    for index, state_id in enumerate(relationship.payoff_state_ids):
        payoff = 0.0
        for leg in relationship.legs:
            signed = 1.0 if leg.action is LegAction.BUY else -1.0
            payoff += (
                signed
                * leg.units_per_bundle
                * bundle_size
                * leg.payoff_by_state[index]
            )
        output.append((state_id, payoff))
    return tuple(output)


def _economics(
    relationship: StructuralRelationship,
    books: Mapping[str, ExecutableBook],
    *,
    bundle_size: float,
    fee_rate: float,
    slippage_per_unit: float,
) -> tuple[
    tuple[float, ...],
    float,
    float,
    float,
    float,
    tuple[tuple[str, float], ...],
]:
    prices: list[float] = []
    gross_cost = 0.0
    fee_notional = 0.0
    for leg in relationship.legs:
        book = books[leg.instrument_id]
        quantity = leg.units_per_bundle * bundle_size
        levels = book.asks if leg.action is LegAction.BUY else book.bids
        consumed = _consume(levels, quantity)
        if consumed is None:
            raise ValueError("insufficient depth for proposed bundle size")
        notional, vwap = consumed
        prices.append(vwap)
        fee_notional += abs(notional)
        if leg.action is LegAction.BUY:
            gross_cost += notional
        else:
            gross_cost -= notional
    payoff_states = _state_payoffs(relationship, bundle_size)
    worst_case = min(value for _, value in payoff_states)
    gross_edge = worst_case - gross_cost
    fees = fee_notional * fee_rate
    slippage = slippage_per_unit * bundle_size
    net_edge = gross_edge - fees - slippage
    return (
        tuple(prices),
        gross_cost,
        gross_edge,
        fees,
        net_edge,
        payoff_states,
    )


def _max_bundle_depth(
    relationship: StructuralRelationship,
    books: Mapping[str, ExecutableBook],
) -> float:
    sizes = []
    for leg in relationship.legs:
        book = books[leg.instrument_id]
        sizes.append(
            _max_leg_quantity(book, leg.action) / leg.units_per_bundle
        )
    return min(sizes, default=0.0)


def _certificate_id(
    relationship: StructuralRelationship,
    observed_at: datetime,
    available_size: float,
    net_edge: float | None,
) -> str:
    payload = {
        "relationship_id": relationship.relationship_id,
        "observed_at": observed_at.astimezone(UTC).isoformat(),
        "available_size": round(available_size, 12),
        "net_edge": None if net_edge is None else round(net_edge, 12),
        "semantic_proof_hash": relationship.semantic_proof_hash,
        "mapping_hash": relationship.mapping_hash,
    }
    raw = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return "stc_" + hashlib.sha256(raw).hexdigest()[:32]


def _empty_certificate(
    relationship: StructuralRelationship,
    *,
    observed_at: datetime,
    status: StructuralStatus,
    reasons: tuple[str, ...],
    slippage_per_unit: float,
) -> StructuralCertificate:
    return StructuralCertificate(
        certificate_id=_certificate_id(
            relationship,
            observed_at,
            0.0,
            None,
        ),
        relationship_id=relationship.relationship_id,
        relationship_type=relationship.relationship_type,
        observed_at=observed_at,
        leg_ids=tuple(leg.instrument_id for leg in relationship.legs),
        semantic_proof_version=relationship.semantic_proof_version,
        semantic_proof_hash=relationship.semantic_proof_hash,
        mapping_hash=relationship.mapping_hash,
        executable_prices=(),
        available_size=0.0,
        payoff_states=(),
        worst_case_payoff=None,
        gross_cost=None,
        gross_edge=None,
        fees=None,
        slippage_assumption=slippage_per_unit,
        net_edge=None,
        certificate_status=status,
        reasons=reasons,
        evidence_refs=tuple(
            f"book:{leg.instrument_id}" for leg in relationship.legs
        ),
    )


def evaluate_relationship(
    relationship: StructuralRelationship,
    books: Mapping[str, ExecutableBook],
    *,
    observed_at: datetime,
    mapping_valid: bool = True,
    max_book_age_seconds: float = 2.0,
    fee_rate: float = 0.0,
    slippage_per_unit: float = 0.0,
    minimum_net_edge: float = 0.0,
) -> StructuralCertificate:
    """Prove or reject one executable structural violation from full depth."""
    if observed_at.tzinfo is None or observed_at.utcoffset() is None:
        raise ValueError("observed_at must be timezone-aware")
    if max_book_age_seconds <= 0:
        raise ValueError("max_book_age_seconds must be positive")
    if min(fee_rate, slippage_per_unit, minimum_net_edge) < 0:
        raise ValueError("fees, slippage and minimum edge must be non-negative")

    missing = [
        leg.instrument_id
        for leg in relationship.legs
        if leg.instrument_id not in books
    ]
    if not relationship.semantics_verified:
        return _empty_certificate(
            relationship,
            observed_at=observed_at,
            status=StructuralStatus.SEMANTICS_UNVERIFIED,
            reasons=("SEMANTIC_PROOF_NOT_VERIFIED",),
            slippage_per_unit=slippage_per_unit,
        )
    if not mapping_valid:
        return _empty_certificate(
            relationship,
            observed_at=observed_at,
            status=StructuralStatus.MAPPING_INVALID,
            reasons=("MAPPING_INVALID",),
            slippage_per_unit=slippage_per_unit,
        )
    if missing:
        return _empty_certificate(
            relationship,
            observed_at=observed_at,
            status=StructuralStatus.INSUFFICIENT_DEPTH,
            reasons=("MISSING_LEG_BOOK:" + ",".join(sorted(missing)),),
            slippage_per_unit=slippage_per_unit,
        )

    selected = [books[leg.instrument_id] for leg in relationship.legs]
    if any(not book.trusted for book in selected):
        return _empty_certificate(
            relationship,
            observed_at=observed_at,
            status=StructuralStatus.UNTRUSTED_BOOK,
            reasons=("UNTRUSTED_LEG_BOOK",),
            slippage_per_unit=slippage_per_unit,
        )
    if any(
        (observed_at - book.observed_at.astimezone(UTC)).total_seconds()
        > max_book_age_seconds
        or book.observed_at > observed_at
        for book in selected
    ):
        return _empty_certificate(
            relationship,
            observed_at=observed_at,
            status=StructuralStatus.STALE_BOOK,
            reasons=("STALE_OR_FUTURE_LEG_BOOK",),
            slippage_per_unit=slippage_per_unit,
        )

    max_size = _max_bundle_depth(relationship, books)
    if max_size <= 0:
        return _empty_certificate(
            relationship,
            observed_at=observed_at,
            status=StructuralStatus.INSUFFICIENT_DEPTH,
            reasons=("ZERO_EXECUTABLE_DEPTH",),
            slippage_per_unit=slippage_per_unit,
        )

    tiny = min(max_size, 1e-6)
    _, _, _, _, tiny_edge, _ = _economics(
        relationship,
        books,
        bundle_size=tiny,
        fee_rate=fee_rate,
        slippage_per_unit=slippage_per_unit,
    )
    if tiny_edge <= minimum_net_edge * tiny:
        prices, gross_cost, gross_edge, fees, net_edge, states = _economics(
            relationship,
            books,
            bundle_size=max_size,
            fee_rate=fee_rate,
            slippage_per_unit=slippage_per_unit,
        )
        return StructuralCertificate(
            certificate_id=_certificate_id(
                relationship,
                observed_at,
                max_size,
                net_edge,
            ),
            relationship_id=relationship.relationship_id,
            relationship_type=relationship.relationship_type,
            observed_at=observed_at,
            leg_ids=tuple(
                leg.instrument_id for leg in relationship.legs
            ),
            semantic_proof_version=relationship.semantic_proof_version,
            semantic_proof_hash=relationship.semantic_proof_hash,
            mapping_hash=relationship.mapping_hash,
            executable_prices=prices,
            available_size=max_size,
            payoff_states=states,
            worst_case_payoff=min(value for _, value in states),
            gross_cost=gross_cost,
            gross_edge=gross_edge,
            fees=fees,
            slippage_assumption=slippage_per_unit,
            net_edge=net_edge,
            certificate_status=StructuralStatus.NO_VIOLATION,
            reasons=("NO_POSITIVE_NET_EDGE_AT_TOP_OF_BOOK",),
            evidence_refs=tuple(
                f"book:{leg.instrument_id}" for leg in relationship.legs
            ),
        )

    _, _, _, _, max_edge, _ = _economics(
        relationship,
        books,
        bundle_size=max_size,
        fee_rate=fee_rate,
        slippage_per_unit=slippage_per_unit,
    )
    executable_size = max_size
    if max_edge <= minimum_net_edge * max_size:
        low = 0.0
        high = max_size
        for _ in range(64):
            mid = (low + high) / 2.0
            _, _, _, _, edge, _ = _economics(
                relationship,
                books,
                bundle_size=mid,
                fee_rate=fee_rate,
                slippage_per_unit=slippage_per_unit,
            )
            if edge > minimum_net_edge * mid:
                low = mid
            else:
                high = mid
        executable_size = low

    prices, gross_cost, gross_edge, fees, net_edge, states = _economics(
        relationship,
        books,
        bundle_size=executable_size,
        fee_rate=fee_rate,
        slippage_per_unit=slippage_per_unit,
    )
    if (
        executable_size <= 1e-9
        or net_edge <= minimum_net_edge * executable_size
    ):
        status = StructuralStatus.NO_VIOLATION
        reasons = ("FEES_SLIPPAGE_OR_DEPTH_ERASE_EDGE",)
    else:
        status = StructuralStatus.EXECUTABLE_VIOLATION
        reasons = (
            "WORST_CASE_TERMINAL_PAYOFF_EXCEEDS_EXECUTABLE_COST",
        )
    return StructuralCertificate(
        certificate_id=_certificate_id(
            relationship,
            observed_at,
            executable_size,
            net_edge,
        ),
        relationship_id=relationship.relationship_id,
        relationship_type=relationship.relationship_type,
        observed_at=observed_at,
        leg_ids=tuple(leg.instrument_id for leg in relationship.legs),
        semantic_proof_version=relationship.semantic_proof_version,
        semantic_proof_hash=relationship.semantic_proof_hash,
        mapping_hash=relationship.mapping_hash,
        executable_prices=prices,
        available_size=executable_size,
        payoff_states=states,
        worst_case_payoff=min(value for _, value in states),
        gross_cost=gross_cost,
        gross_edge=gross_edge,
        fees=fees,
        slippage_assumption=slippage_per_unit,
        net_edge=net_edge,
        certificate_status=status,
        reasons=reasons,
        evidence_refs=tuple(
            f"book:{leg.instrument_id}" for leg in relationship.legs
        ),
    )


def complement_relationship(
    *,
    relationship_id: str,
    yes_instrument_id: str,
    no_instrument_id: str,
    semantic_proof_version: str,
    semantic_proof_hash: str,
    mapping_hash: str,
    action: LegAction = LegAction.BUY,
) -> StructuralRelationship:
    """Build an explicitly verified binary complement portfolio."""
    return StructuralRelationship(
        relationship_id=relationship_id,
        relationship_type="COMPLEMENT",
        legs=(
            StructuralLeg(
                yes_instrument_id,
                action,
                1.0,
                (1.0, 0.0),
            ),
            StructuralLeg(
                no_instrument_id,
                action,
                1.0,
                (0.0, 1.0),
            ),
        ),
        payoff_state_ids=("YES", "NO"),
        semantic_proof_version=semantic_proof_version,
        semantic_proof_hash=semantic_proof_hash,
        mapping_hash=mapping_hash,
        semantics_verified=True,
    )


def exhaustive_partition_relationship(
    *,
    relationship_id: str,
    instrument_ids: Sequence[str],
    semantic_proof_version: str,
    semantic_proof_hash: str,
    mapping_hash: str,
    relationship_type: str = "EXHAUSTIVE_PARTITION",
) -> StructuralRelationship:
    """Build a mutually-exclusive exhaustive long portfolio from audited semantics."""
    if len(instrument_ids) < 2:
        raise ValueError("partition requires at least two instruments")
    states = tuple(
        f"STATE_{index}" for index in range(len(instrument_ids))
    )
    legs = []
    for index, instrument_id in enumerate(instrument_ids):
        payoff = tuple(
            1.0 if state == index else 0.0
            for state in range(len(states))
        )
        legs.append(
            StructuralLeg(
                instrument_id,
                LegAction.BUY,
                1.0,
                payoff,
            )
        )
    return StructuralRelationship(
        relationship_id=relationship_id,
        relationship_type=relationship_type,
        legs=tuple(legs),
        payoff_state_ids=states,
        semantic_proof_version=semantic_proof_version,
        semantic_proof_hash=semantic_proof_hash,
        mapping_hash=mapping_hash,
        semantics_verified=True,
    )


def write_certificates(
    *,
    output_root: Path,
    certificates: Sequence[StructuralCertificate],
    config: Mapping[str, object],
) -> None:
    """Persist append-only certificates plus a current machine-readable snapshot."""
    output_root.mkdir(parents=True, exist_ok=True)
    rows = [certificate.to_dict() for certificate in certificates]
    if rows:
        pq.write_table(
            pa.Table.from_pylist(rows),
            output_root / "certificates.parquet",
        )
        with (
            output_root / "certificates.jsonl"
        ).open("a", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(row, sort_keys=True) + "\n")
    current = {
        "analysis_id": "STRUCT-SCAN-001",
        "analysis_version": "struct-scan-001-v1",
        "generated_at": datetime.now(UTC).isoformat(),
        "config_hash": canonical_config_hash(config),
        "certificates": rows,
        "execution_authority": "SHADOW_ONLY",
    }
    (output_root / "current.json").write_text(
        json.dumps(current, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def semantic_artifact_identity(path: Path) -> str:
    """Expose the exact audited semantic artifact hash consumed by a scanner."""
    return sha256_file(path)
