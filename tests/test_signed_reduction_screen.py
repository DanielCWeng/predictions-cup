"""Synthetic fail-closed tests. Never exercise SIG, maker or actual risk gate."""

from dataclasses import replace

import pytest

from predictions_cup.research.signed_reduction_screen import (
    SignedInventoryEvidence,
    screen_signed_reduction,
)


def _case() -> SignedInventoryEvidence:
    return SignedInventoryEvidence(
        signed_by_exchange={"held_no": -2500, "held_yes": 97500},
        account_gross=100000,
        account_net=95000,
        account_trusted=True,
        reconciliation_complete=True,
        signed_positions_verified=True,
        venue_netting_verified=True,
        market_open_verified=True,
        global_halt_clear=True,
        account_age_seconds=1.0,
    )


def _screen(
    data: SignedInventoryEvidence | None = None,
    *,
    exchange_id: str = "held_no",
    outcome_side: str = "YES",
    action: str = "BUY",
    quantity: int = 1000,
):
    return screen_signed_reduction(
        data or _case(),
        exchange_id=exchange_id,
        outcome_side=outcome_side,
        action=action,
        quantity=quantity,
        gross_limit=100000,
        max_account_age_seconds=10.0,
    )


def test_held_no_buy_yes_conditionally_reduces_math_only() -> None:
    result = _screen()
    assert result.mathematically_reducing
    assert result.projected_gross == 99000
    assert result.signed_delta == 1000
    assert not result.execution_authorised


def test_held_no_sell_no_reduces_same_canonical_sign() -> None:
    result = _screen(outcome_side="NO", action="SELL")
    assert result.mathematically_reducing
    assert result.projected_gross == 99000


def test_held_yes_sell_yes_reduces() -> None:
    result = _screen(exchange_id="held_yes", action="SELL", quantity=500)
    assert result.mathematically_reducing
    assert result.projected_gross == 99500


@pytest.mark.parametrize(
    ("side", "action"),
    [("YES", "SELL"), ("NO", "BUY")],
)
def test_held_no_wrong_direction_never_bypasses_cap(
    side: str, action: str
) -> None:
    result = _screen(outcome_side=side, action=action)
    assert not result.mathematically_reducing
    assert result.reason == "intent_not_opposite_to_existing_inventory"


def test_overshoot_can_never_flip_a_position() -> None:
    result = _screen(quantity=2501)
    assert not result.mathematically_reducing
    assert result.reason == "possible_overshoot_and_side_flip"


@pytest.mark.parametrize("amount", [0, -1, 0.5, True])
def test_requires_positive_integral_lots(amount: int | float) -> None:
    assert not _screen(quantity=amount).mathematically_reducing  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "field",
    [
        "account_trusted",
        "reconciliation_complete",
        "signed_positions_verified",
        "venue_netting_verified",
        "market_open_verified",
        "global_halt_clear",
    ],
)
def test_missing_independent_proof_denies(field: str) -> None:
    assert not _screen(replace(_case(), **{field: False})).mathematically_reducing


@pytest.mark.parametrize(
    "field",
    ["open_order_exposure", "uncertain_order_exposure", "reservation_exposure"],
)
def test_any_pending_exposure_denies(field: str) -> None:
    assert not _screen(replace(_case(), **{field: 1})).mathematically_reducing


@pytest.mark.parametrize("age", [None, float("nan"), -1.0, 11.0])
def test_unknown_or_stale_account_denies(age: float | None) -> None:
    assert not _screen(replace(_case(), account_age_seconds=age)).mathematically_reducing


def test_risk_absolute_bucket_is_not_signed_position_evidence() -> None:
    data = replace(_case(), signed_positions_verified=False)
    result = _screen(data)
    assert result.reason == "signed_position_provenance_missing"


def test_reconciles_gross_and_net_independently() -> None:
    data = replace(_case(), account_net=100000)
    result = _screen(data)
    assert result.reason == "signed_ledger_not_reconciled_to_gross_and_net"


def test_rejects_missing_exchange_and_inventory() -> None:
    assert _screen(exchange_id="unknown").reason == "no_authoritative_existing_position"
    assert _screen(exchange_id="").reason == "unknown_exchange"


def test_incorrect_market_side_is_rejected() -> None:
    assert _screen(outcome_side="MAYBE").reason == "unknown_venue_action_or_outcome"


def test_screen_default_denies_everything_without_signed_provenance() -> None:
    default = SignedInventoryEvidence(
        signed_by_exchange={"x": -10},
        account_gross=10,
        account_net=-10,
    )
    assert not _screen(default, exchange_id="x", quantity=1).mathematically_reducing


def test_no_implicit_live_authorisation_even_after_math_passes() -> None:
    assert _screen().reason == "CONDITIONAL_MATH_ONLY_NOT_EXECUTION_AUTHORITY"
    assert not _screen().execution_authorised
