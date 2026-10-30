"""One-shot GET-only SIG election inventory and executable-depth snapshot.

No forecasts, writes, collectors, execution credentials, or automatic orders.
The data can be completed with independently evidenced *joint* settlement
scenarios and passed to scripts/ops/election_terminal_score.py.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from datetime import UTC, datetime
from decimal import Decimal, ROUND_FLOOR
from typing import Any

from predictions_cup.analysis.election_capital import estimate_reducing_exit
from predictions_cup.config.settings import AppSettings
from predictions_cup.sig.client import SigRestClient
from predictions_cup.sig.dto import OrderBookSnapshotDto
from predictions_cup.sig.trading_dto import PositionReadDto

ZERO = Decimal("0")
ONE = Decimal("1")


def _cash_ask_from_book(
    book: OrderBookSnapshotDto, quantity: int,
) -> tuple[int, Decimal]:
    """Return only the displayed, actually fillable BUY YES quantity and VWAP."""
    if not 1 <= quantity <= 10000:
        raise ValueError("bounded snapshot quantity required")
    filled = 0
    total = ZERO
    for level in book.asks:
        if filled >= quantity:
            break
        available = int(Decimal(level.quantity).to_integral_value(rounding=ROUND_FLOOR))
        price = Decimal(level.price)
        if available < 0 or not ZERO < price < ONE:
            raise ValueError("invalid executable SIG ask")
        take = min(quantity - filled, available)
        total += take * price
        filled += take
    return filled, ZERO if filled == 0 else total / filled


def _full_holdings(
    positions: tuple[PositionReadDto, ...],
) -> tuple[list[dict[str, int | str]], frozenset[str]]:
    by_market: defaultdict[str, list[int]] = defaultdict(lambda: [0, 0])
    for position in positions:
        if position.settled or position.quantity == 0:
            continue
        qty = Decimal(position.quantity)
        if qty != qty.to_integral_value():
            raise ValueError("fractional holding prevents complete portfolio proof")
        if not position.market_id or not position.exchange_id:
            raise ValueError("portfolio holding lacks exact market identity")
        if qty > 0:
            by_market[position.market_id][0] += int(qty)
        else:
            by_market[position.market_id][1] += -int(qty)
    holdings = [
        {"market_id": market_id, "yes_shares": amounts[0], "no_shares": amounts[1]}
        for market_id, amounts in sorted(by_market.items())
    ]
    if not holdings:
        raise ValueError("no unsettled holdings to model")
    return holdings, frozenset(by_market)


async def snapshot_election_opportunities(
    *, max_exit_markets: int = 4, size_limit: int = 2500,
    depth: int = 20, entry_exchange_ids: tuple[str, ...] = (),
) -> dict[str, Any]:
    """Capture read-only inputs, with NULL instead of invented order certainty."""
    if (
        not 1 <= max_exit_markets <= 8
        or not 1 <= size_limit <= 10000
        or not 1 <= depth <= 100
        or len(entry_exchange_ids) > 8
        or len(set(entry_exchange_ids)) != len(entry_exchange_ids)
        or any(not item.isdigit() for item in entry_exchange_ids)
    ):
        raise ValueError("bounded and unique SIG market IDs are required")
    settings = AppSettings(
        trading_enabled=False, execution_mode="SHADOW",
        sig_trade_credential=None, global_kill_switch=True,
    )
    if not settings.tournament_id or not settings.tournament_slug:
        raise ValueError("tournament identity unavailable")

    async with SigRestClient(settings) as api:
        account = await api.get_account()
        if account.balance is None:
            raise ValueError("authoritative cash unavailable")
        portfolio = await api.get_tournament_positions(settings.tournament_slug)
        standings = await api.get_tournament_leaderboard(
            settings.tournament_slug, period="all", limit=100,
        )
        tournaments = await api.list_tournaments(limit=100)
        tournament = next(
            (t for t in tournaments.data if t.id == settings.tournament_id), None,
        )
        if tournament is None or len(standings.leaderboard) < 3:
            raise ValueError("leaderboard threshold or starting balance unavailable")
        by_rank = {entry.rank: entry for entry in standings.leaderboard}
        if 3 not in by_rank:
            raise ValueError("third place not returned by official leaderboard")
        initial = Decimal(tournament.initial_balance)
        milestones = {
            str(rank): float(initial + Decimal(by_rank[rank].pnl))
            for rank in (3, 10, 50, 100) if rank in by_rank
        }
        holdings, authoritative_ids = _full_holdings(tuple(portfolio.positions))
        held_positions = sorted(
            (
                p for p in portfolio.positions
                if not p.settled and p.quantity != 0
            ),
            key=lambda p: abs(Decimal(p.market_value)),
            reverse=True,
        )
        quotes: list[dict[str, Any]] = []
        covered_ids: set[str] = set()
        for position in held_positions[:max_exit_markets]:
            book = await api.get_orderbook(
                position.exchange_id, depth=depth,
                tournament_id=settings.tournament_id,
            )
            captured_at = datetime.now(UTC)
            estimate = estimate_reducing_exit(
                position, book, size_limit=size_limit,
            )
            quantity = estimate.executable_shares
            if quantity > 0:
                effective_price = Decimal(estimate.gross_proceeds) / quantity
                if ZERO < effective_price < ONE:
                    quotes.append({
                        "id": f"exit-{position.exchange_id}",
                        "market_id": position.market_id,
                        "exchange_id": position.exchange_id,
                        "title": position.market_title,
                        "action": "SELL", "side": estimate.held_side,
                        "quantity": quantity,
                        "displayed_shares": quantity,
                        "executable_price": float(effective_price),
                        "extra_cost_per_share": 0.02,
                        "quote_observed_at": captured_at.isoformat(),
                        "source_reference": (
                            f"SIG GET orderbook exchange={position.exchange_id} "
                            f"depth={depth} observed={captured_at.isoformat()}"
                        ),
                    })
            covered_ids.add(position.exchange_id)
            await asyncio.sleep(0.5)
        for eid in entry_exchange_ids:
            if eid in covered_ids:
                continue
            book = await api.get_orderbook(
                eid, depth=depth, tournament_id=settings.tournament_id,
            )
            captured_at = datetime.now(UTC)
            filled, price = _cash_ask_from_book(book, size_limit)
            if filled > 0 and ZERO < price < ONE:
                quotes.append({
                    "id": f"entry-{eid}",
                    "market_id": book.market_id,
                    "exchange_id": eid,
                    "action": "BUY", "side": "YES",
                    "quantity": filled,
                    "displayed_shares": filled,
                    "executable_price": float(price),
                    "extra_cost_per_share": 0.02,
                    "quote_observed_at": captured_at.isoformat(),
                    "source_reference": (
                        f"SIG GET orderbook exchange={eid} "
                        f"depth={depth} observed={captured_at.isoformat()}"
                    ),
                })
            await asyncio.sleep(0.5)

    return {
        "mode": "GET_ONLY_SIG_ELECTION_SNAPSHOT",
        "execution_authority": False,
        "as_of": datetime.now(UTC).isoformat(),
        "cash": float(Decimal(account.balance)),
        "target_balance": milestones["3"],
        "rank_milestones": milestones,
        "leaderboard_my_rank": standings.my_rank,
        "portfolio_complete": True,
        "account_reconciled": False,
        "uncertain_order_exposure": None,
        "authoritative_position_market_ids": sorted(authoritative_ids),
        "holdings": holdings,
        "candidate_quotes": quotes,
        "scenarios": [],
        "probability_views": [],
        "warning": (
            "This is non-atomic GET-only input, not a model or a fresh trading "
            "decision. No official settlement rule has been verified here. "
            "Scenario support must include ALL held and candidate market IDs; "
            "probabilities and contract mappings require separate independent "
            "evidence. Unknown live order exposure remains NULL, never zero."
        ),
    }
