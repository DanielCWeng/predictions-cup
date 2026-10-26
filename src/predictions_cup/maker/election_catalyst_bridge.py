"""Read-only conversion of live maker state to catalyst economics inputs.

Used on independently verified, rare catalyst events only. Never queries REST,
touches orders or changes RISK-002.
"""
from __future__ import annotations

import math

from predictions_cup.execution.reservations import ExecutionReservationBook
from predictions_cup.maker.contracts import MakerMarketSnapshot
from predictions_cup.maker.election_catalyst import Direction, MarketView
from predictions_cup.runtime.models import RuntimeLevel


def market_view_from_snapshot(
    snapshot: MakerMarketSnapshot,
    reservations: ExecutionReservationBook,
    direction: Direction,
    *,
    max_bbo_age_ns: int = 15_000_000_000,
) -> MarketView:
    """Reuse authoritative snapshot, overlay open orders, prove reducible shares."""
    runtime = snapshot.runtime
    book = runtime.book(snapshot.exchange_id)
    market = runtime.market(snapshot.market_id)
    portfolio = reservations.overlay_portfolio(runtime.portfolio)
    low, high = portfolio.worst_case_inventory_bounds(
        snapshot.exchange_id, snapshot.tournament_id
    )
    reducible = math.floor(max(0.0, -high if direction == "BUY" else low))
    bbo_age_ns = snapshot.now_monotonic_ns - snapshot.sig_bbo_observed_ns
    bbo_trusted = (
        snapshot.sig_bbo_trusted
        and 0 <= bbo_age_ns <= max_bbo_age_ns
    )
    return MarketView(
        exchange_id=snapshot.exchange_id,
        market_open=bool(
            market is not None
            and market.status == "open"
            and market.tournament_id == snapshot.tournament_id
            and market.tradeable
            and market.mapping_accepted
        ),
        account_trusted=portfolio.account_trusted,
        bbo_trusted=bbo_trusted,
        depth_trusted=bool(
            snapshot.sig_depth_trusted
            and book is not None
            and book.trusted_depth
        ),
        depth_observed_ns=snapshot.sig_depth_observed_ns,
        now_monotonic_ns=snapshot.now_monotonic_ns,
        bids=() if book is None else book.bids,
        asks=() if book is None else book.asks,
        available_cash=(
            None if portfolio.account_proxy_cash_balance is None
            else float(portfolio.account_proxy_cash_balance)
        ),
        reducible_shares=reducible,
    )
