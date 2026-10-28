"""Issuer order-fills evidence must be complete, bounded and identity-matched."""

from __future__ import annotations

from decimal import Decimal

from predictions_cup.analysis.election_order_capacity import issuer_filled_total
from predictions_cup.sig.trading_dto import OrderFillsResponseDto, OrderReadDto


def sample_order() -> OrderReadDto:
    return OrderReadDto.model_validate({
        "id": 1234, "exchangeId": "983", "side": "yes", "action": "buy",
        "quantity": "300", "quantityFilled": None,
        "priceLimit": "0.34", "open": True,
        "createdAt": "2026-10-28T22:00:00Z", "expirationDate": None,
    })


def fill_envelope(
    *,
    order_id: int = 1234,
    exchange_id: str = "983",
    complete: bool = True,
    has_more: bool = False,
    total_filled: str = "25",
) -> OrderFillsResponseDto:
    return OrderFillsResponseDto.model_validate({
        "orderId": order_id, "exchangeId": exchange_id,
        "tournamentId": "synthetic-test",
        "data": [],
        "pagination": {
            "limit": 200, "hasMore": has_more,
            "nextCursor": "next" if has_more else None,
        },
        "coverage": {"complete": complete, "projectedThroughSequence": 12345},
        "totalQuantityFilled": total_filled,
        "avgFillPrice": None,
    })


def test_complete_issuer_report_confirms_remaining_quantity() -> None:
    confirmed = issuer_filled_total(sample_order(), fill_envelope())
    assert confirmed == Decimal("25")
    assert confirmed is not None
    assert sample_order().quantity - confirmed == Decimal("275")


def test_partial_or_paginated_issuer_report_abstains() -> None:
    assert issuer_filled_total(
        sample_order(), fill_envelope(complete=False)
    ) is None
    assert issuer_filled_total(
        sample_order(), fill_envelope(has_more=True)
    ) is None


def test_identity_and_impossible_fills_abstain() -> None:
    assert issuer_filled_total(
        sample_order(), fill_envelope(order_id=1235)
    ) is None
    assert issuer_filled_total(
        sample_order(), fill_envelope(exchange_id="982")
    ) is None
    assert issuer_filled_total(
        sample_order(), fill_envelope(total_filled="301")
    ) is None
