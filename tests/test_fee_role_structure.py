from datetime import UTC, datetime
from decimal import Decimal

import pytest

from predictions_cup.learning.fee_role_structure import (
    FeeRegime,
    RoleClass,
    RoleEvidence,
    RoleEvidenceSource,
    canonical_yes_pressure_sign,
    classify_role,
    fee_regime,
    signed_aggressor_amount,
)


def _evidence(
    when: datetime,
    order_role: bool | None,
    fee_evidence: str | None,
    net_fee: str | None,
    **flags: bool,
) -> RoleEvidence:
    return RoleEvidence(
        timestamp=when,
        order_is_match_taker_order=order_role,
        fee_evidence=fee_evidence,
        fee_net_usd_equiv=None if net_fee is None else Decimal(net_fee),
        **flags,
    )


def test_fee_regime_boundaries_are_frozen() -> None:
    assert fee_regime(datetime(2026, 1, 4, 23, 59, 59, tzinfo=UTC)) is FeeRegime.PRE_FEE
    assert fee_regime(datetime(2026, 1, 5, tzinfo=UTC)) is FeeRegime.V1
    assert fee_regime(datetime(2026, 4, 27, 23, 59, 59, tzinfo=UTC)) is FeeRegime.V1
    assert fee_regime(datetime(2026, 4, 28, tzinfo=UTC)) is FeeRegime.V2


def test_v2_clean_taker_and_maker_require_agreeing_evidence() -> None:
    when = datetime(2026, 5, 1, tzinfo=UTC)
    taker = classify_role(_evidence(when, True, "fee_charged", "0.02"))
    maker = classify_role(_evidence(when, False, "no_fee_leg_observed", "0"))
    assert taker.role_class is RoleClass.TAKER_HIGH_CONFIDENCE
    assert taker.evidence_source is RoleEvidenceSource.FEE_PLUS_ORDER_ROLE
    assert maker.role_class is RoleClass.MAKER_HIGH_CONFIDENCE


def test_v2_known_taker_without_fee_is_kept_supportive_not_dropped() -> None:
    decision = classify_role(
        _evidence(datetime(2026, 5, 1, tzinfo=UTC), True, "no_fee_leg_observed", "0")
    )
    assert decision.role_class is RoleClass.TAKER_SUPPORTIVE


def test_v2_fee_on_passive_order_fails_closed() -> None:
    decision = classify_role(
        _evidence(
            datetime(2026, 5, 1, tzinfo=UTC),
            False,
            "fee_charged",
            "0.01",
            flag_fee_on_non_taker_order=True,
        )
    )
    assert decision.role_class is RoleClass.AMBIGUOUS


def test_v1_uses_net_fee_not_gross_charge_for_high_confidence_taker() -> None:
    when = datetime(2026, 4, 1, tzinfo=UTC)
    refunded = classify_role(_evidence(when, True, "fee_charged_fully_refunded", "0"))
    realised = classify_role(_evidence(when, True, "fee_charged_partly_refunded", "0.01"))
    assert refunded.role_class is RoleClass.TAKER_SUPPORTIVE
    assert realised.role_class is RoleClass.TAKER_HIGH_CONFIDENCE


def test_v1_passive_positive_net_fee_is_ambiguous() -> None:
    decision = classify_role(
        _evidence(
            datetime(2026, 4, 1, tzinfo=UTC),
            False,
            "fee_charged_partly_refunded",
            "0.01",
        )
    )
    assert decision.role_class is RoleClass.AMBIGUOUS


def test_pre_fee_zero_fee_never_manufactures_maker_label() -> None:
    when = datetime(2025, 12, 1, tzinfo=UTC)
    unknown = classify_role(_evidence(when, None, "no_fee_leg_observed", "0"))
    semantic_maker = classify_role(_evidence(when, False, "no_fee_leg_observed", "0"))
    assert unknown.role_class is RoleClass.UNKNOWN
    assert semantic_maker.role_class is RoleClass.MAKER_SUPPORTIVE
    assert semantic_maker.evidence_source is RoleEvidenceSource.ORDER_ROLE_ONLY_PRE_FEE


@pytest.mark.parametrize(
    ("outcome", "side", "expected"),
    [
        ("YES", "buy", 1),
        ("YES", "sell", -1),
        ("NO", "buy", -1),
        ("NO", "sell", 1),
    ],
)
def test_canonical_yes_pressure_truth_table(outcome: str, side: str, expected: int) -> None:
    assert canonical_yes_pressure_sign(outcome, side) == expected


def test_canonical_yes_pressure_rejects_unverified_other_outcomes() -> None:
    with pytest.raises(ValueError, match="unsupported outcome_side"):
        canonical_yes_pressure_sign("OTHER", "buy")


def test_signed_aggressor_amount_defaults_to_high_confidence_only() -> None:
    when = datetime(2026, 5, 1, tzinfo=UTC)
    high = classify_role(_evidence(when, True, "fee_charged", "0.02"))
    supportive = classify_role(_evidence(when, True, "no_fee_leg_observed", "0"))
    maker = classify_role(_evidence(when, False, "no_fee_leg_observed", "0"))

    assert signed_aggressor_amount(
        high,
        outcome_side="NO",
        participant_side="buy",
        amount=Decimal("10"),
    ) == Decimal("-10")
    assert (
        signed_aggressor_amount(
            supportive,
            outcome_side="YES",
            participant_side="buy",
            amount=Decimal("10"),
        )
        is None
    )
    assert (
        signed_aggressor_amount(
            maker,
            outcome_side="YES",
            participant_side="buy",
            amount=Decimal("10"),
        )
        is None
    )


def test_ambiguous_flag_overrides_other_evidence() -> None:
    decision = classify_role(
        _evidence(
            datetime(2026, 5, 1, tzinfo=UTC),
            True,
            "fee_charged",
            "0.02",
            flag_attribution_ambiguous=True,
        )
    )
    assert decision.role_class is RoleClass.AMBIGUOUS
