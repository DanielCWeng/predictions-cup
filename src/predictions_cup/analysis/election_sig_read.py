"""Read-only SIG election watchlist snapshot; never authorises trading.

Pulls account positions, open orders and executable SIG book ladders using the
repository's existing READ-ONLY SigRestClient. Default watchlist admits only
verified exact, same-direction election mappings. No order sender is imported.
The account and books are not atomic: output requires later reconciliation
before any economic decision. No candidate probability is manufactured.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from predictions_cup.config.settings import AppSettings
from predictions_cup.sig.account_reconciliation import (
    AccountReconciliationRest,
    reconcile_account,
)
from predictions_cup.sig.client import SigRestClient
from predictions_cup.sig.dto import OrderBookSnapshotDto

DEFAULT_RACES = ("Maine", "Kansas", "Iowa", "Texas")
MAX_BOOKS = 20


class ElectionSnapshotRest(AccountReconciliationRest, Protocol):
    async def get_account(self) -> Any: ...
    async def get_orderbook(
        self, exchange_id: str, *, depth: int = 20, tournament_id: str | None = None
    ) -> OrderBookSnapshotDto: ...


def _positive_integer(value: int) -> int:
    if type(value) is not int or value < 1:
        raise ValueError("positive book depth required")
    return value


def select_watchlist(
    mapping: Mapping[str, Any], *,
    races: tuple[str, ...] = DEFAULT_RACES,
    include_derived: bool = False,
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """Select verified exact SAME mappings; report excluded risky mappings."""
    records = mapping.get("records")
    if not isinstance(records, list) or not races or any(not r.strip() for r in races):
        raise ValueError("mapping records and nonempty race names required")
    chosen: list[dict[str, str]] = []
    excluded: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in records:
        if not isinstance(item, dict):
            raise ValueError("invalid market mapping entry")
        title = item.get("sig_market_title", "")
        if not isinstance(title, str) or not any(
            re.search(rf"\b{re.escape(race)} senate\b", title, re.IGNORECASE)
            for race in races
        ):
            continue
        eid, mid = item.get("sig_exchange_id"), item.get("sig_market_id")
        if not isinstance(eid, str) or not eid.isdigit() or not isinstance(mid, str):
            raise ValueError("missing SIG market identity")
        if eid in seen:
            raise ValueError("duplicate exchange mapping")
        seen.add(eid)
        cls = item.get("mapping_class")
        direction = item.get("mapping_direction")
        state = item.get("status")
        row = {
            "exchange_id": eid,
            "market_id": mid,
            "title": title,
            "mapping_class": str(cls),
            "mapping_direction": str(direction),
            "status": str(state),
        }
        if (
            state == "VERIFIED"
            and (
                (cls == "EXACT" and direction == "SAME")
                or (include_derived and cls == "DERIVED")
            )
        ):
            chosen.append(row)
        else:
            excluded.append(row)
    if not chosen:
        raise ValueError("no eligible exchanges in requested race watchlist")
    if len(chosen) > MAX_BOOKS:
        raise ValueError("watchlist too large for bounded rate-limited snapshot")
    return chosen, excluded


async def capture_election_sig_snapshot(
    rest: ElectionSnapshotRest,
    *,
    mapping: Mapping[str, Any],
    tournament_id: str,
    tournament_slug: str,
    races: tuple[str, ...] = DEFAULT_RACES,
    include_derived: bool = False,
    depth: int = 20,
    pause: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> dict[str, object]:
    """Collect one read-only survey and report, WITHOUT inferring executable EV."""
    _positive_integer(depth)
    if depth > 200 or not tournament_id.strip() or not tournament_slug.strip():
        raise ValueError("invalid depth or tournament scope")
    if mapping.get("tournament_id") != tournament_id:
        raise ValueError("requested tournament does not match source mapping")

    selected, excluded = select_watchlist(
        mapping, races=races, include_derived=include_derived
    )
    account = await reconcile_account(
        rest, tournament_id=tournament_id, tournament_slug=tournament_slug,
        cash_balance_reader=rest.get_account,
    )
    positions = {p.exchange_id: p for p in account.positions}
    orders_by_exchange: dict[str, int] = {}
    for order in account.open_orders:
        if order.open:
            orders_by_exchange[order.exchange_id] = (
                orders_by_exchange.get(order.exchange_id, 0) + 1
            )

    books: list[dict[str, object]] = []
    for row in selected:
        # Keep calls below the typical 100/60s participant budget on this
        # standalone utility. Account reconciliation may use a two-read burst.
        await pause(0.75)
        eid = row["exchange_id"]
        begin = datetime.now(UTC)
        quote = await rest.get_orderbook(
            eid, depth=depth, tournament_id=tournament_id
        )
        end = datetime.now(UTC)
        if quote.exchange_id != eid or quote.market_id != row["market_id"]:
            raise ValueError(f"SIG book identity mismatch exchange={eid}")
        if quote.bids and quote.asks and quote.bids[0].price >= quote.asks[0].price:
            raise ValueError(f"crossed SIG book exchange={eid}")
        held = positions.get(eid)
        qty = held.quantity if held is not None else 0
        books.append({
            **row,
            "book_read_started_at": begin.isoformat(),
            "book_read_completed_at": end.isoformat(),
            "bids": [
                {"price": str(level.price), "quantity": str(level.quantity)}
                for level in quote.bids
            ],
            "asks": [
                {"price": str(level.price), "quantity": str(level.quantity)}
                for level in quote.asks
            ],
            "held_yes_shares": str(max(0, qty)),
            "held_no_shares": str(max(0, -qty)),
            "open_orders_in_instrument": orders_by_exchange.get(eid, 0),
            "position_settled": bool(held.settled) if held is not None else False,
            # Maine federal races use ranked-choice voting in November 2026;
            # an election-night first-choice lead is not a settlement call.
            "election_rule_caution": (
                "MAINE_SENATE_RANKED_CHOICE_FIRST_ROUND_NOT_TERMINAL"
                if "maine senate" in row["title"].lower()
                else "VERIFY_OFFICIAL_SETTLEMENT_RULES_INDEPENDENTLY"
            ),
        })

    return {
        "mode": "READ_ONLY_NON_ATOMIC_SIG_SURVEY",
        "execution_authority": False,
        "tournament_id": tournament_id,
        "account_read_started_at": account.observed_at.isoformat(),
        "completed_at": datetime.now(UTC).isoformat(),
        "cash_balance_raw": (
            None if account.cash_balance is None else str(account.cash_balance)
        ),
        "cash_is_verified_spendable": False,
        "total_open_orders": len(account.open_orders),
        "selected_books": books,
        "excluded_mappings": excluded,
        "warning": (
            "API reads are sequential/non-atomic, not reconciled for intervening fills; "
            "cash may already include venue holds; the book can change before execution. "
            "No live trade may be justified from this survey alone."
        ),
    }


async def run_readonly(args: argparse.Namespace) -> dict[str, object]:
    # Force a shadow-only settings object without any trading credential even if
    # the operator's environment normally configures LIVE execution. The client
    # instantiated below has GET-only methods and no order dispatcher.
    settings = AppSettings(
        trading_enabled=False,
        execution_mode="SHADOW",
        sig_trade_credential=None,
        global_kill_switch=True,
    )
    tournament_id = args.tournament_id or settings.tournament_id
    tournament_slug = args.tournament_slug or settings.tournament_slug
    if not tournament_id or not tournament_slug:
        raise ValueError("explicit tournament id and slug required")
    mapping = json.loads(args.mapping.read_text(encoding="utf-8"))
    if not isinstance(mapping, dict):
        raise ValueError("mapping must be a JSON object")
    async with SigRestClient(settings) as rest:
        return await capture_election_sig_snapshot(
            rest, mapping=mapping, tournament_id=tournament_id,
            tournament_slug=tournament_slug,
            races=tuple(args.race), include_derived=args.include_derived,
            depth=args.depth,
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="SIG read-only election watchlist books")
    parser.add_argument(
        "--mapping", type=Path,
        default=Path("data/mappings/sig_polymarket_2026.json"),
    )
    parser.add_argument("--race", action="append", default=None)
    parser.add_argument("--include-derived", action="store_true")
    parser.add_argument("--tournament-id")
    parser.add_argument("--tournament-slug")
    parser.add_argument("--depth", type=int, default=20)
    args = parser.parse_args()
    args.race = args.race or list(DEFAULT_RACES)
    try:
        result = asyncio.run(run_readonly(args))
    except (OSError, ValueError, RuntimeError) as exc:
        parser.error(str(exc))
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
