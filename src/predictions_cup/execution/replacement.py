"""Conservative quote-replacement gate after cancellation or uncertain execution."""

from __future__ import annotations

from predictions_cup.runtime import RuntimePortfolio


def quote_replacement_allowed(
    portfolio: RuntimePortfolio,
    *,
    exchange_id: str | None = None,
    market_id: str | None = None,
) -> bool:
    """Allow replacement only from trusted state with no matching open/uncertain order."""
    if not portfolio.account_trusted:
        return False
    if exchange_id is None and market_id is None:
        raise ValueError("exchange_id or market_id is required")
    for order in portfolio.orders:
        if not (order.open or order.uncertain):
            continue
        exchange_matches = exchange_id is None or order.exchange_id == exchange_id
        market_matches = market_id is None or order.market_id == market_id
        if exchange_matches and market_matches:
            return False
    return True
