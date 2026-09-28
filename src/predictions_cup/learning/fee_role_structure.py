"""Role semantics for EXPERIMENT-005A.

Classification/instrumentation primitives only. This module does not load outcomes,
fit models, search thresholds, compute strategy P&L, or place orders.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum

FEE_START_UTC = datetime(2026, 1, 5, tzinfo=UTC)
V2_START_UTC = datetime(2026, 4, 28, tzinfo=UTC)


class FeeRegime(StrEnum):
    PRE_FEE = "PRE_FEE"
    V1 = "V1"
    V2 = "V2"


class RoleClass(StrEnum):
    TAKER_HIGH_CONFIDENCE = "TAKER_HIGH_CONFIDENCE"
    MAKER_HIGH_CONFIDENCE = "MAKER_HIGH_CONFIDENCE"
    TAKER_SUPPORTIVE = "TAKER_SUPPORTIVE"
    MAKER_SUPPORTIVE = "MAKER_SUPPORTIVE"
    AMBIGUOUS = "AMBIGUOUS"
    UNKNOWN = "UNKNOWN"


class RoleEvidenceSource(StrEnum):
    FEE_PLUS_ORDER_ROLE = "FEE_PLUS_ORDER_ROLE"
    NET_FEE_PLUS_ORDER_ROLE = "NET_FEE_PLUS_ORDER_ROLE"
    ORDER_ROLE_ONLY = "ORDER_ROLE_ONLY"
    ORDER_ROLE_ONLY_PRE_FEE = "ORDER_ROLE_ONLY_PRE_FEE"
    FEE_ONLY_SUPPORTIVE = "FEE_ONLY_SUPPORTIVE"
    CONTRADICTORY = "CONTRADICTORY"
    NONE = "NONE"


@dataclass(frozen=True)
class RoleEvidence:
    timestamp: datetime
    order_is_match_taker_order: bool | None
    fee_evidence: str | None
    fee_net_usd_equiv: Decimal | None
    flag_attribution_ambiguous: bool = False
    flag_multiple_fee_records: bool = False
    flag_fee_on_fee_disabled_market: bool = False
    flag_fee_on_non_taker_order: bool = False


@dataclass(frozen=True)
class RoleDecision:
    role_class: RoleClass
    regime: FeeRegime
    evidence_source: RoleEvidenceSource
    reason: str

    @property
    def is_taker(self) -> bool:
        return self.role_class in {
            RoleClass.TAKER_HIGH_CONFIDENCE,
            RoleClass.TAKER_SUPPORTIVE,
        }

    @property
    def is_high_confidence(self) -> bool:
        return self.role_class in {
            RoleClass.TAKER_HIGH_CONFIDENCE,
            RoleClass.MAKER_HIGH_CONFIDENCE,
        }


def fee_regime(timestamp: datetime) -> FeeRegime:
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    utc_timestamp = timestamp.astimezone(UTC)
    if utc_timestamp < FEE_START_UTC:
        return FeeRegime.PRE_FEE
    if utc_timestamp < V2_START_UTC:
        return FeeRegime.V1
    return FeeRegime.V2


def _has_charge(fee_evidence: str | None) -> bool:
    return bool(fee_evidence and fee_evidence.startswith("fee_charged"))


def _positive_net_fee(value: Decimal | None) -> bool:
    return value is not None and value > Decimal("0")


def classify_role(evidence: RoleEvidence) -> RoleDecision:
    regime = fee_regime(evidence.timestamp)
    has_charge = _has_charge(evidence.fee_evidence)
    positive_net = _positive_net_fee(evidence.fee_net_usd_equiv)

    if evidence.flag_attribution_ambiguous or evidence.flag_fee_on_fee_disabled_market:
        return RoleDecision(
            RoleClass.AMBIGUOUS,
            regime,
            RoleEvidenceSource.CONTRADICTORY,
            "attribution or fee-enablement ambiguity flag",
        )

    if regime is FeeRegime.PRE_FEE:
        if has_charge or positive_net:
            return RoleDecision(
                RoleClass.AMBIGUOUS,
                regime,
                RoleEvidenceSource.CONTRADICTORY,
                "fee evidence appears before the fee regime",
            )
        if evidence.order_is_match_taker_order is True:
            return RoleDecision(
                RoleClass.TAKER_SUPPORTIVE,
                regime,
                RoleEvidenceSource.ORDER_ROLE_ONLY_PRE_FEE,
                "chain order-role semantic only; fee absence is uninformative",
            )
        if evidence.order_is_match_taker_order is False:
            return RoleDecision(
                RoleClass.MAKER_SUPPORTIVE,
                regime,
                RoleEvidenceSource.ORDER_ROLE_ONLY_PRE_FEE,
                "chain order-role semantic only; fee absence is uninformative",
            )
        return RoleDecision(
            RoleClass.UNKNOWN,
            regime,
            RoleEvidenceSource.NONE,
            "no independently identifying order-role evidence",
        )

    if regime is FeeRegime.V1:
        if evidence.order_is_match_taker_order is True:
            if positive_net and not evidence.flag_multiple_fee_records:
                return RoleDecision(
                    RoleClass.TAKER_HIGH_CONFIDENCE,
                    regime,
                    RoleEvidenceSource.NET_FEE_PLUS_ORDER_ROLE,
                    "active order role agrees with positive realised V1 fee",
                )
            return RoleDecision(
                RoleClass.TAKER_SUPPORTIVE,
                regime,
                RoleEvidenceSource.ORDER_ROLE_ONLY,
                "V1 gross fee/zero fee is not independently decisive",
            )
        if evidence.order_is_match_taker_order is False:
            if positive_net:
                return RoleDecision(
                    RoleClass.AMBIGUOUS,
                    regime,
                    RoleEvidenceSource.CONTRADICTORY,
                    "positive realised V1 fee conflicts with passive order-role evidence",
                )
            return RoleDecision(
                RoleClass.MAKER_SUPPORTIVE,
                regime,
                RoleEvidenceSource.ORDER_ROLE_ONLY,
                "passive order role; V1 gross/refund mechanics prevent fee-based upgrade",
            )
        if positive_net:
            return RoleDecision(
                RoleClass.TAKER_SUPPORTIVE,
                regime,
                RoleEvidenceSource.FEE_ONLY_SUPPORTIVE,
                "positive realised V1 fee without order-role evidence",
            )
        return RoleDecision(
            RoleClass.UNKNOWN,
            regime,
            RoleEvidenceSource.NONE,
            "V1 gross/zero fee without order-role evidence is not classifying",
        )

    if evidence.order_is_match_taker_order is True:
        if (
            has_charge
            and positive_net
            and not evidence.flag_multiple_fee_records
            and not evidence.flag_fee_on_non_taker_order
        ):
            return RoleDecision(
                RoleClass.TAKER_HIGH_CONFIDENCE,
                regime,
                RoleEvidenceSource.FEE_PLUS_ORDER_ROLE,
                "V2 active order role agrees with positive taker-only fee",
            )
        return RoleDecision(
            RoleClass.TAKER_SUPPORTIVE,
            regime,
            RoleEvidenceSource.ORDER_ROLE_ONLY,
            "V2 active order role present but independent fee confirmation is absent/noisy",
        )

    if evidence.order_is_match_taker_order is False:
        if has_charge or positive_net or evidence.flag_fee_on_non_taker_order:
            return RoleDecision(
                RoleClass.AMBIGUOUS,
                regime,
                RoleEvidenceSource.CONTRADICTORY,
                "V2 fee evidence conflicts with passive order-role evidence",
            )
        if (
            evidence.fee_evidence == "no_fee_leg_observed"
            and not evidence.flag_multiple_fee_records
        ):
            return RoleDecision(
                RoleClass.MAKER_HIGH_CONFIDENCE,
                regime,
                RoleEvidenceSource.FEE_PLUS_ORDER_ROLE,
                "V2 passive order role agrees with scanned zero-fee evidence",
            )
        return RoleDecision(
            RoleClass.MAKER_SUPPORTIVE,
            regime,
            RoleEvidenceSource.ORDER_ROLE_ONLY,
            "V2 passive order role without clean independent zero-fee confirmation",
        )

    if has_charge and positive_net:
        return RoleDecision(
            RoleClass.TAKER_SUPPORTIVE,
            regime,
            RoleEvidenceSource.FEE_ONLY_SUPPORTIVE,
            "V2 taker-only fee evidence without order-role field",
        )
    return RoleDecision(
        RoleClass.UNKNOWN,
        regime,
        RoleEvidenceSource.NONE,
        "V2 zero/no fee without order-role evidence is not enough to call maker",
    )


def canonical_yes_pressure_sign(outcome_side: str, participant_side: str) -> int:
    """Map the signed order owner's token action onto canonical YES-probability pressure."""

    outcome = outcome_side.strip().upper()
    side = participant_side.strip().lower()
    if outcome not in {"YES", "NO"}:
        raise ValueError(f"unsupported outcome_side: {outcome_side!r}")
    if side not in {"buy", "sell"}:
        raise ValueError(f"unsupported participant_side: {participant_side!r}")
    if (outcome, side) in {("YES", "buy"), ("NO", "sell")}:
        return 1
    return -1


def signed_aggressor_amount(
    decision: RoleDecision,
    *,
    outcome_side: str,
    participant_side: str,
    amount: Decimal,
    high_confidence_only: bool = True,
) -> Decimal | None:
    if not decision.is_taker:
        return None
    if high_confidence_only and not decision.is_high_confidence:
        return None
    return amount * canonical_yes_pressure_sign(outcome_side, participant_side)
