"""Research parity checks against existing signed-inventory bounds. No LIVE operations."""

import pytest

from predictions_cup.research.signed_reduction_screen import (
    ConditionalReduction,
    SignedInventoryEvidence,
    screen_signed_reduction,
)
from predictions_cup.runtime.models import RuntimePortfolio, RuntimePosition


@pytest.mark.parametrize(
    ("signed", "side", "action", "delta", "expected"),
    [
        (-2500, "YES", "BUY", 1000, True),
        (-2500, "NO", "SELL", 1000, True),
        (-2500, "YES", "SELL", -1000, False),
        (-2500, "NO", "BUY", -1000, False),
        (2500, "YES", "SELL", -1000, True),
        (2500, "NO", "BUY", -1000, True),
        (2500, "YES", "BUY", 1000, False),
        (2500, "NO", "SELL", 1000, False),
    ],
)
def test_parity_with_runtime_helper(
    signed: int, side: str, action: str, delta: int, expected: bool
) -> None:
    runtime = RuntimePortfolio(
        positions=(
            RuntimePosition(
                exchange_id="x", market_id="m", tournament_id="t",
                gross_exposure=float(abs(signed)),
                signed_quantity=float(signed),
            ),
        ),
        account_trusted=True,
    )
    assert runtime.is_inventory_reducing(
        exchange_id="x", tournament_id="t", signed_delta=float(delta)
    ) is expected
    evidence = SignedInventoryEvidence(
        signed_by_exchange={"x": signed},
        account_gross=abs(signed),
        account_net=signed,
        account_trusted=True,
        reconciliation_complete=True,
        signed_positions_verified=True,
        venue_netting_verified=True,
        market_open_verified=True,
        global_halt_clear=True,
        account_age_seconds=1.0,
    )
    result = screen_signed_reduction(
        evidence, exchange_id="x", outcome_side=side,
        action=action, quantity=1000, gross_limit=5000,
        max_account_age_seconds=5.0,
    )
    assert result.mathematically_reducing is expected
    assert result.signed_delta == (delta if expected else None)
    assert result.execution_authorised is False


def test_repeated_screen_against_same_snapshot_is_not_a_reservation() -> None:
    """Repeated positive mathematical answers MUST NOT become live approvals."""
    evidence = SignedInventoryEvidence(
        signed_by_exchange={"x": -2500},
        account_gross=2500,
        account_net=-2500,
        account_trusted=True,
        reconciliation_complete=True,
        signed_positions_verified=True,
        venue_netting_verified=True,
        market_open_verified=True,
        global_halt_clear=True,
        account_age_seconds=1.0,
    )
    def check() -> ConditionalReduction:
        return screen_signed_reduction(
            evidence, exchange_id="x", outcome_side="YES",
            action="BUY", quantity=2000, gross_limit=5000,
            max_account_age_seconds=5.0,
        )

    first, second = check(), check()
    assert first.mathematically_reducing
    assert second.mathematically_reducing
    assert first.execution_authorised is False
    assert second.execution_authorised is False
    assert -2500 + 2000 + 2000 > 0  # joint intents would flip the position
