"""Combined official public result + SIG executable quote READ-ONLY watcher.

No trade credential, order sender, fill action or fabricated election forecast.
One official page and two bounded SIG orderbook reads per interval.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from dotenv import load_dotenv

from predictions_cup.analysis.election_official_texas import (
    TexasSenateObservation,
    fetch_texas_senate,
)
from predictions_cup.config.settings import AppSettings
from predictions_cup.sig.client import SigRestClient
from predictions_cup.sig.dto import OrderBookSnapshotDto

TEXAS_MARKETS = (("982", "293", "DEMOCRATIC"), ("983", "294", "REPUBLICAN"))
COMPETITION_CUTOFF = datetime(2026, 11, 4, 16, 59, 55, tzinfo=UTC)


class BookReader(Protocol):
    async def get_orderbook(
        self, exchange_id: str, *, depth: int = 20,
        tournament_id: str | None = None,
    ) -> OrderBookSnapshotDto: ...


async def capture_official_sig_pair(
    reader: BookReader,
    *,
    official: TexasSenateObservation,
    tournament_id: str,
    pause: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> dict[str, object]:
    if not tournament_id.strip():
        raise ValueError("missing configured SIG tournament")
    books: list[dict[str, Any]] = []
    for exchange, market, party in TEXAS_MARKETS:
        await pause(0.75)
        started = datetime.now(UTC)
        book = await reader.get_orderbook(
            exchange, depth=20, tournament_id=tournament_id,
        )
        completed = datetime.now(UTC)
        if book.exchange_id != exchange or book.market_id != market:
            raise ValueError("SIG watch market identity mismatch")
        if book.bids and book.asks and book.bids[0].price >= book.asks[0].price:
            raise ValueError("SIG watch crossed market")
        books.append({
            "exchange_id": exchange, "market_id": market, "party": party,
            "read_started_at": started.isoformat(),
            "read_completed_at": completed.isoformat(),
            "best_bid": None if book.best_bid is None else str(book.best_bid),
            "best_ask": None if book.best_ask is None else str(book.best_ask),
            "bid_shares_at_touch": str(book.bids[0].quantity) if book.bids else "0",
            "ask_shares_at_touch": str(book.asks[0].quantity) if book.asks else "0",
            "verified_exact_cross_venue_mapping": False,
        })
    return {
        "mode": "OFFICIAL_AND_SIG_READONLY_SHADOW",
        "observed_at": datetime.now(UTC).isoformat(),
        "official": official.as_document(),
        "sig_quotes": books,
        "execution_authority": False,
        "probability_generated": False,
        "unverified_contract_mapping": True,
        "status": (
            "OBSERVATION_ONLY_COUNTS_NOT_CALIBRATED"
            if official.status == "UNVERIFIED_COUNTS"
            else "NO_ELECTION_RESULT_SIGNAL"
        ),
    }


async def run(
    *,
    runtime_env: Path, loop: bool, interval_seconds: int,
) -> None:
    if not runtime_env.is_file():
        raise ValueError("runtime environment file missing")
    load_dotenv(runtime_env, override=True)
    settings = AppSettings(
        trading_enabled=False, execution_mode="SHADOW",
        sig_trade_credential=None, global_kill_switch=True,
    )
    if not settings.sig_read_credential or not settings.tournament_id:
        raise ValueError("explicit read-only SIG credential and tournament required")
    async with SigRestClient(settings) as rest:
        last_fingerprint: str | None = None
        while datetime.now(UTC) < COMPETITION_CUTOFF:
            try:
                official = await fetch_texas_senate()
                snapshot = await capture_official_sig_pair(
                    rest, official=official, tournament_id=settings.tournament_id,
                )
                quotes = snapshot["sig_quotes"]
                # Suppress repeated identical source+book status. Timestamps
                # change on every read, so use only the price/depth fields.
                if isinstance(quotes, list):
                    fingerprint = json.dumps([
                        official.body_sha256,
                        [(
                            q["exchange_id"], q["best_bid"], q["best_ask"],
                            q["bid_shares_at_touch"], q["ask_shares_at_touch"],
                        ) for q in quotes],
                    ])
                if fingerprint != last_fingerprint:
                    print(json.dumps(snapshot, sort_keys=True), flush=True)
                    last_fingerprint = fingerprint
            except Exception as exc:
                print(json.dumps({
                    "mode": "OFFICIAL_AND_SIG_READONLY_SHADOW",
                    "status": "ABSTAIN_SOURCE_OR_SIG_BOOK_UNAVAILABLE",
                    "error_type": type(exc).__name__,
                    "execution_authority": False,
                }), flush=True)
            if not loop:
                return
            await asyncio.sleep(interval_seconds)


def main() -> int:
    parser = argparse.ArgumentParser(description="Official Texas returns + SIG quote shadow")
    parser.add_argument("--runtime-env", type=Path, required=True)
    parser.add_argument("--loop", action="store_true")
    parser.add_argument("--interval-seconds", type=int, default=60)
    args = parser.parse_args()
    if not 30 <= args.interval_seconds <= 3600:
        parser.error("interval-seconds must be between 30 and 3600")
    asyncio.run(run(
        runtime_env=args.runtime_env,
        loop=args.loop,
        interval_seconds=args.interval_seconds,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
