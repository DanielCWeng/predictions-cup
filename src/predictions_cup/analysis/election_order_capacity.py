"""Read-only analysis of unfilled SIG orders and indicative buy notional.

This module DOES NOT cancel orders. The displayed notional is NOT proof of
collateral reserved or newly spendable balance after cancellation.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from predictions_cup.config.settings import AppSettings
from predictions_cup.sig.client import SigRestClient
from predictions_cup.sig.trading_dto import OrderFillsResponseDto, OrderReadDto

ZERO = Decimal("0")


@dataclass(frozen=True, slots=True)
class OrderNotional:
    exchange_id: str
    side: str
    action: str
    open_orders: int
    unfilled_shares: str
    indicative_buy_notional: str
    buy_notional_upper_bound: str
    missing_fill_counts: int
    missing_price_limits: int


def summarize_open_orders(orders: tuple[OrderReadDto, ...]) -> tuple[OrderNotional, ...]:
    groups: dict[tuple[str, str, str], dict[str, Decimal | int]] = defaultdict(
        lambda: {
            "count": 0, "shares": ZERO, "buy_notional": ZERO,
            "buy_upper": ZERO,
            "missing_fills": 0, "missing_prices": 0,
        }
    )
    for order in orders:
        if not order.open:
            continue
        if order.quantity < ZERO:
            raise ValueError("negative open-order quantity")
        key = (order.exchange_id, order.side, order.action)
        row = groups[key]
        row["count"] += 1
        if order.quantity_filled is None:
            row["missing_fills"] += 1
            if order.action == "buy" and order.price_limit is not None:
                row["buy_upper"] += order.quantity * order.price_limit
            # Do not claim these shares are unfilled or unencumbered.
            continue
        if not ZERO <= order.quantity_filled <= order.quantity:
            raise ValueError("invalid filled quantity in active order")
        outstanding = order.quantity - order.quantity_filled
        row["shares"] += outstanding
        if order.action == "buy":
            if order.price_limit is None:
                row["missing_prices"] += 1
            else:
                row["buy_notional"] += outstanding * order.price_limit
                row["buy_upper"] += outstanding * order.price_limit
    return tuple(
        OrderNotional(
            exchange_id=eid, side=side, action=action,
            open_orders=int(row["count"]),
            unfilled_shares=str(row["shares"]),
            indicative_buy_notional=str(row["buy_notional"]),
            buy_notional_upper_bound=str(row["buy_upper"]),
            missing_fill_counts=int(row["missing_fills"]),
            missing_price_limits=int(row["missing_prices"]),
        )
        for (eid, side, action), row in sorted(groups.items())
    )


def issuer_filled_total(
    order: OrderReadDto,
    fills: OrderFillsResponseDto,
) -> Decimal | None:
    """Admit only complete and identity-matched issuer fill evidence."""
    if (
        fills.order_id != order.id
        or fills.exchange_id != order.exchange_id
        or not fills.coverage.complete
        or fills.pagination.has_more
        or fills.total_quantity_filled < ZERO
        or fills.total_quantity_filled > order.quantity
    ):
        return None
    return fills.total_quantity_filled


async def scan_orders(*, limit: int = 2000, reconcile_top_buys: int = 0) -> dict[str, object]:

    if not 1 <= limit <= 4000 or not 0 <= reconcile_top_buys <= 12:
        raise ValueError("invalid bounded audit limits")
    settings = AppSettings(
        trading_enabled=False, execution_mode="SHADOW",
        sig_trade_credential=None, global_kill_switch=True,
    )
    if not settings.tournament_id:
        raise ValueError("tournament ID missing")
    async with SigRestClient(settings) as rest:
        account = await rest.get_account()
        orders: list[OrderReadDto] = []
        async for order in rest.iter_orders(
            status="open", tournament_id=settings.tournament_id, limit=200,
        ):
            orders.append(order)
            if len(orders) > limit:
                raise ValueError("too many orders to audit safely")
        issuer_checked = 0
        issuer_confirmed = 0
        if reconcile_top_buys:
            ranked = sorted(
                (
                    (idx, order) for idx, order in enumerate(orders)
                    if order.open and order.action == "buy"
                    and order.price_limit is not None
                    and order.quantity_filled is None
                ),
                key=lambda item: item[1].quantity * item[1].price_limit,
                reverse=True,
            )[:reconcile_top_buys]
            for idx, order in ranked:
                await asyncio.sleep(0.75)
                fills = await rest.get_order_fills(order.id, limit=200)
                issuer_checked += 1
                confirmed = issuer_filled_total(order, fills)
                if confirmed is not None:
                    orders[idx] = order.model_copy(
                        update={"quantity_filled": confirmed}
                    )
                    issuer_confirmed += 1
    rows = summarize_open_orders(tuple(orders))
    return {
        "mode": "READ_ONLY_ORDER_CAPACITY_SURVEY",
        "execution_authority": False,
        "observed_at": datetime.now(UTC).isoformat(),
        "raw_cash": str(account.balance),
        "cash_is_spendable": False,
        "open_orders": sum(x.open_orders for x in rows),
        "issuer_fill_endpoints_checked": issuer_checked,
        "issuer_fill_endpoints_confirmed": issuer_confirmed,
        "indicative_open_buy_notional": str(sum(
            (Decimal(x.indicative_buy_notional) for x in rows), ZERO
        )),
        "open_buy_notional_upper_bound": str(sum(
            (Decimal(x.buy_notional_upper_bound) for x in rows), ZERO
        )),
        "missing_fill_counts": sum(x.missing_fill_counts for x in rows),
        "missing_price_limits": sum(x.missing_price_limits for x in rows),
        "markets": [asdict(x) for x in rows],
        "warning": (
            "Non-atomic read; priceLimit may use outcome-side price units. "
            "Gross buy price*quantity is not an issuer-confirmed hold. "
            "Never cancel merely to free nominal notional: orders can reduce "
            "inventory or be in uncertain execution state. Reconcile with "
            "execution journal, risk and issuer account before any action."
        ),
    }


def main() -> int:
    p = argparse.ArgumentParser(description="SIG read-only open order capital")
    p.add_argument("--runtime-env", type=Path, required=True)
    p.add_argument("--reconcile-top-buys", type=int, default=0)
    args = p.parse_args()
    from dotenv import load_dotenv

    if not args.runtime_env.is_file():
        p.error("runtime environment file missing")
    load_dotenv(args.runtime_env, override=True)
    result = asyncio.run(scan_orders(reconcile_top_buys=args.reconcile_top_buys))
    print(json.dumps(result, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
