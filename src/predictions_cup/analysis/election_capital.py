"""Depth-aware, reduce-only capital recycling analysis for existing SIG positions.

No writes, no order dispatch, no forecast and no automatic instruction to exit.
All NO positions use the *complement of the YES ask* for executable sale
proceeds; valuing a NO sale at the YES bid is a serious economic error.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from predictions_cup.config.settings import AppSettings
from predictions_cup.sig.client import SigRestClient
from predictions_cup.sig.dto import OrderBookSnapshotDto
from predictions_cup.sig.trading_dto import PositionReadDto

ONE = Decimal("1")
ZERO = Decimal("0")


@dataclass(frozen=True, slots=True)
class LiquidationEstimate:
    exchange_id: str
    market_id: str
    title: str
    held_side: str
    held_shares: int
    evaluated_shares: int
    executable_shares: int
    gross_proceeds: str
    fee_reserve: str
    net_proceeds: str
    mid_mark_proceeds: str
    net_minus_mark: str
    last_fill_price: str | None
    full_evaluated_size_fillable: bool
    authority: bool = False


def estimate_reducing_exit(
    position: PositionReadDto,
    book: OrderBookSnapshotDto,
    *,
    size_limit: int = 2500,
    cost_per_share: Decimal = Decimal("0.02"),
) -> LiquidationEstimate:
    if size_limit < 1 or cost_per_share < 0 or cost_per_share >= ONE:
        raise ValueError("invalid risk and fee reserve")
    if position.exchange_id != book.exchange_id or position.market_id != book.market_id:
        raise ValueError("position/order book exchange identity mismatch")
    if position.settled or position.quantity == 0:
        raise ValueError("no live un-settled inventory to reduce")
    if position.quantity != position.quantity.to_integral_value():
        raise ValueError("fractional SIG position requires reconciliation")
    negative = position.quantity < 0
    total = int(abs(position.quantity))
    target = min(size_limit, total)
    # Selling a NO share hits 1 - the YES ask, not the YES bid.
    levels = book.asks if negative else book.bids
    gross = ZERO
    filled = 0
    last: Decimal | None = None
    for level in levels:
        if filled >= target:
            break
        price = ONE - Decimal(level.price) if negative else Decimal(level.price)
        qty = int(Decimal(level.quantity).to_integral_value(rounding="ROUND_FLOOR"))
        if qty < 0 or not ZERO <= price <= ONE:
            raise ValueError("invalid executable book level")
        take = min(target - filled, qty)
        gross += price * take
        filled += take
        if take:
            last = price
    fees = Decimal(filled) * cost_per_share
    net = max(ZERO, gross - fees)
    # The API marketValue is a mark estimate, not an executable price.
    mark = Decimal(position.market_value) * Decimal(filled) / Decimal(total)
    return LiquidationEstimate(
        exchange_id=position.exchange_id,
        market_id=position.market_id,
        title=position.market_title,
        held_side="NO" if negative else "YES",
        held_shares=total,
        evaluated_shares=target,
        executable_shares=filled,
        gross_proceeds=str(gross),
        fee_reserve=str(fees),
        net_proceeds=str(net),
        mid_mark_proceeds=str(mark),
        net_minus_mark=str(net - mark),
        last_fill_price=None if last is None else str(last),
        full_evaluated_size_fillable=filled == target,
    )


async def scan_capital(*, max_positions: int, size_limit: int, depth: int) -> dict[str, object]:
    if not 1 <= max_positions <= 20 or not 1 <= depth <= 200:
        raise ValueError("bounded survey limits required")
    if not 1 <= size_limit <= 10_000:
        raise ValueError("invalid per-position analysis size")
    settings = AppSettings(
        trading_enabled=False, execution_mode="SHADOW",
        sig_trade_credential=None, global_kill_switch=True,
    )
    if not settings.tournament_id or not settings.tournament_slug:
        raise ValueError("tournament scope must be configured")
    async with SigRestClient(settings) as api:
        account = await api.get_account()
        portfolio = await api.get_tournament_positions(settings.tournament_slug)
        standings = await api.get_tournament_leaderboard(
            settings.tournament_slug, period="all", limit=1
        )
        tournaments = await api.list_tournaments(limit=100)
        tournament = next(
            (x for x in tournaments.data if x.id == settings.tournament_id), None
        )
        if tournament is None or not standings.leaderboard:
            raise ValueError("tournament initial balance or leaderboard unavailable")
        first_target = (
            Decimal(tournament.initial_balance) + Decimal(standings.leaderboard[0].pnl)
        )
        if account.balance is None:
            raise ValueError("missing account balance")
        unsettled = tuple(
            p for p in portfolio.positions if not p.settled and p.quantity != 0
        )
        all_shares_payout_ceiling = (
            Decimal(account.balance)
            + sum((abs(Decimal(p.quantity)) for p in unsettled), ZERO)
        )
        positions = sorted(
            unsettled,
            key=lambda p: abs(Decimal(p.market_value)), reverse=True,
        )[:max_positions]
        estimates: list[dict[str, object]] = []
        for p in positions:
            await asyncio.sleep(0.75)
            book = await api.get_orderbook(
                p.exchange_id, depth=depth, tournament_id=settings.tournament_id
            )
            estimates.append(asdict(estimate_reducing_exit(
                p, book, size_limit=size_limit,
            )))
    return {
        "mode": "READ_ONLY_POTENTIAL_CAPITAL_RELEASE",
        "observed_at": datetime.now(UTC).isoformat(),
        "execution_authority": False,
        "cash_balance_raw": str(account.balance),
        "portfolio_market_value": str(portfolio.summary.total_market_value),
        "gross_marked_equity_approximate": str(
            Decimal(account.balance) + Decimal(portfolio.summary.total_market_value)
        ),
        "capital_is_spendable": False,
        "leaderboard_my_rank": standings.my_rank,
        "leaderboard_target_marked_balance": str(first_target),
        "all_current_holdings_win_theoretical_ceiling": str(
            all_shares_payout_ceiling
        ),
        "all_current_holdings_win_is_not_independently_possible_verified": True,
        "current_holdings_cannot_reach_present_leader_even_if_all_win": (
            all_shares_payout_ceiling < first_target
        ),
        "approximate_target_gap": str(
            first_target - Decimal(account.balance)
            - Decimal(portfolio.summary.total_market_value)
        ),
        "leaderboard_is_provisional": True,
        "fee_reserve_per_share": "0.02",
        "max_shares_evaluated_per_position": size_limit,
        "estimates": estimates,
        "warning": (
            "Non-atomic quotes/account; open orders not reconciled; "
            "uncommitted spendable collateral unverified. Exiting at these "
            "quotes can destroy option value. This is NOT an exit ranking or order."
        ),
    }


def main() -> int:
    p = argparse.ArgumentParser(description="Depth-aware read-only inventory rotation economics")
    p.add_argument("--max-positions", type=int, default=12)
    p.add_argument("--size-limit", type=int, default=2500)
    p.add_argument("--depth", type=int, default=20)
    p.add_argument("--runtime-env", type=Path, default=None)
    args = p.parse_args()
    if args.runtime_env is not None:
        from dotenv import load_dotenv

        if not args.runtime_env.is_file():
            p.error("runtime environment file not found")
        load_dotenv(args.runtime_env, override=True)
    try:
        result = asyncio.run(scan_capital(
            max_positions=args.max_positions,
            size_limit=args.size_limit,
            depth=args.depth,
        ))
    except (ValueError, OSError, RuntimeError) as exc:
        p.error(str(exc))
    print(json.dumps(result, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
