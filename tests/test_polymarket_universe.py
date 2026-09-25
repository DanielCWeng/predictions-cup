from __future__ import annotations

from datetime import UTC, datetime

from predictions_cup.external.polymarket.models import PolymarketMarket
from predictions_cup.external.polymarket.universe import ElectionUniverseSelector


def market(market_id: str, question: str, *, active: bool = True) -> PolymarketMarket:
    return PolymarketMarket(
        market_id=market_id,
        condition_id=f"condition-{market_id}",
        question=question,
        slug=None,
        outcomes=("Yes", "No"),
        token_ids=(f"{market_id}-yes", f"{market_id}-no"),
        active=active,
        closed=False,
        accepting_orders=True,
        start_at=None,
        end_at=datetime(2026, 11, 4, tzinfo=UTC),
        resolution_source=None,
        event_id=None,
        event_slug=None,
        event_title=None,
        neg_risk=False,
        neg_risk_market_id=None,
        market_group=None,
        group_item_title=None,
        group_item_threshold=None,
        parent_event_slug=None,
        min_tick_size=None,
        min_order_size=None,
        liquidity=None,
        volume=None,
    )


def test_election_only_selection_and_manual_overrides_are_stable() -> None:
    senate = market("2", "Who wins the 2026 U.S. Senate race in Example?")
    sports = market("1", "Will the Lions win the 2026 championship?")
    manually_included = market("3", "A market with no election keywords")
    selector = ElectionUniverseSelector(
        include_ids=frozenset({"3"}), exclude_ids=frozenset({"condition-2"})
    )

    first = selector.select((senate, sports, manually_included))
    second = selector.select((manually_included, sports, senate))

    assert [m.market_id for m in first.markets] == ["3"]
    assert first.token_ids == second.token_ids == ("3-no", "3-yes")
