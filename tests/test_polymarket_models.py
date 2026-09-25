from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from predictions_cup.external.polymarket.models import PayloadError, PolymarketMarket


def gamma_payload() -> dict[str, object]:
    return {
        "id": "123",
        "conditionId": "0xcondition",
        "question": "Will Example win a 2026 U.S. Senate race?",
        "slug": "example-2026-senate",
        "outcomes": '["Yes", "No"]',
        "clobTokenIds": '["yes-token", "no-token"]',
        "active": True,
        "closed": False,
        "acceptingOrders": True,
        "startDate": "2026-01-01T00:00:00Z",
        "endDate": "2026-11-04T00:00:00Z",
        "resolutionSource": "official results",
        "orderPriceMinTickSize": "0.01",
        "orderMinSize": "5",
        "liquidityNum": "123.45",
        "volumeNum": "456.78",
        "events": [
            {
                "id": "event-1",
                "slug": "2026-senate-example",
                "title": "2026 Senate Example",
                "negRisk": True,
                "negRiskMarketID": "neg-risk-group",
            }
        ],
        "marketGroup": "race-group",
        "groupItemTitle": "Example",
        "groupItemThreshold": "50",
    }


def test_gamma_market_parsing_preserves_identity_and_grouping() -> None:
    market = PolymarketMarket.from_gamma(gamma_payload())

    assert market.market_id == "123"
    assert market.condition_id == "0xcondition"
    assert market.neg_risk is True
    assert market.neg_risk_market_id == "neg-risk-group"
    assert market.market_group == "race-group"
    assert market.group_item_threshold == Decimal("50")
    assert market.min_tick_size == Decimal("0.01")
    assert market.end_at == datetime(2026, 11, 4, tzinfo=UTC)
    assert [(token.token_id, token.outcome) for token in market.tokens()] == [
        ("yes-token", "Yes"),
        ("no-token", "No"),
    ]


def test_gamma_market_rejects_misaligned_token_identity() -> None:
    payload = gamma_payload()
    payload["clobTokenIds"] = '["only-one"]'

    with pytest.raises(PayloadError, match="aligned arrays"):
        PolymarketMarket.from_gamma(payload)
