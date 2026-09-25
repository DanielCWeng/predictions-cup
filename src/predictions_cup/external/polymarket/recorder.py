"""EXPERIMENT-001A Polymarket live recorder entry point."""

from __future__ import annotations

import argparse
import asyncio
import logging
from datetime import datetime
from typing import Any

from predictions_cup.config import AppSettings, load_settings
from predictions_cup.external.polymarket.client import ClobMarketDataClient
from predictions_cup.external.polymarket.gamma import GammaClient
from predictions_cup.external.polymarket.health import IngestionHealth
from predictions_cup.external.polymarket.models import JsonObject, TradeEvent, utc_now
from predictions_cup.external.polymarket.orderbook import OrderBookStore, event_type
from predictions_cup.external.polymarket.storage import PolymarketStorage
from predictions_cup.external.polymarket.universe import ElectionUniverseSelector, parse_id_csv
from predictions_cup.external.polymarket.websocket import MarketWebSocket

_LOG = logging.getLogger(__name__)


class PolymarketRecorder:
    """Coordinates public metadata, authoritative books, stream updates, and storage."""

    def __init__(self, settings: AppSettings) -> None:
        self.settings = settings
        self.health = IngestionHealth()
        self.gamma = GammaClient(
            str(settings.polymarket_gamma_base_url),
            page_limit=settings.polymarket_gamma_page_limit,
        )
        self.clob = ClobMarketDataClient(str(settings.polymarket_clob_base_url))
        self.storage = PolymarketStorage(settings.polymarket_storage_path)
        self.books = OrderBookStore()
        self.selector = ElectionUniverseSelector(
            include_ids=parse_id_csv(settings.polymarket_include_ids),
            exclude_ids=parse_id_csv(settings.polymarket_exclude_ids),
        )
        self.websocket = MarketWebSocket(str(settings.polymarket_ws_url), self.health)
        self._token_ids: tuple[str, ...] = ()
        self._market_count = 0

    async def initialize(self) -> None:
        await asyncio.to_thread(self.storage.initialize)
        await self.refresh_universe()

    async def refresh_universe(self) -> None:
        try:
            discovery = await self.gamma.discover_active_markets()
            selection = self.selector.select(discovery.markets)
            if not selection.markets:
                raise RuntimeError("Polymarket universe selector returned no markets")
            refreshed_at = utc_now()
            await asyncio.to_thread(
                self.storage.upsert_markets, selection.markets, refreshed_at.isoformat()
            )

            old_tokens = frozenset(self._token_ids)
            new_tokens = frozenset(selection.token_ids)
            additions = tuple(sorted(new_tokens - old_tokens))
            removals = old_tokens - new_tokens
            if additions and old_tokens:
                await self._seed_books(additions, invalidate=False)
            if removals:
                self.books.invalidate(removals)

            self._token_ids = selection.token_ids
            self._market_count = len(selection.markets)
            self.health.markets_subscribed = self._market_count
            self.health.tokens_subscribed = len(self._token_ids)
            self.health.parse_failures += discovery.parse_failures
            self.health.gamma_last_refresh_at = refreshed_at
            self.health.gamma_last_status = "OK"
            await self.websocket.set_tokens(self._token_ids)
        except Exception as exc:
            self.health.gamma_last_refresh_at = utc_now()
            self.health.gamma_last_status = f"ERROR: {type(exc).__name__}: {exc}"
            raise

    async def _before_websocket_connect(self) -> None:
        self.books.invalidate_all()
        await self._seed_books(self._token_ids, invalidate=False)

    async def _seed_books(self, token_ids: tuple[str, ...], *, invalidate: bool) -> None:
        if invalidate:
            self.books.invalidate(set(token_ids))
        payloads = await self.clob.fetch_books(token_ids)
        observed_at = utc_now()
        for payload in payloads:
            self.books.apply_full_snapshot(payload, observed_at)
        missing = set(token_ids) - set(self.books.initialized_tokens)
        if missing:
            raise RuntimeError(f"CLOB /books did not initialize {len(missing)} subscribed tokens")
        self.health.last_valid_book_update_at = observed_at

    async def handle_message(self, payload: JsonObject, observed_at: datetime) -> None:
        kind = event_type(payload)
        if kind == "book":
            self.books.apply_full_snapshot(payload, observed_at)
            self.health.last_valid_book_update_at = observed_at
            self.health.last_book_change_at = observed_at
            return
        if kind == "price_change":
            result = self.books.apply_price_change(payload, observed_at)
            self.health.book_uninitialized_delta_count += result.uninitialized_deltas
            if result.changed_tokens:
                self.health.last_valid_book_update_at = observed_at
                self.health.last_book_change_at = observed_at
            return
        if kind == "last_trade_price":
            event = _event_payload(payload)
            trade = TradeEvent.from_ws(event, observed_at)
            try:
                await asyncio.to_thread(self.storage.append_trade, trade)
            except Exception:
                self.health.storage_failures += 1
                raise
            self.health.last_trade_at = observed_at
            return
        if kind == "tick_size_change":
            if self.books.apply_tick_size_change(payload, observed_at):
                self.health.last_valid_book_update_at = observed_at
            else:
                self.health.book_uninitialized_delta_count += 1
            return

        self.health.unknown_event_count += 1
        _LOG.info("Skipping unsupported Polymarket market event type=%r", kind)

    async def record_snapshot_once(self) -> int:
        recorded_at = utc_now()
        snapshots = self.books.snapshots(self.settings.polymarket_book_depth)
        try:
            count = await asyncio.to_thread(
                self.storage.append_snapshots, snapshots, recorded_at.isoformat()
            )
        except Exception as exc:
            self.health.storage_failures += 1
            self.health.snapshot_last_at = recorded_at
            self.health.snapshot_last_status = f"ERROR: {type(exc).__name__}: {exc}"
            raise
        self.health.snapshot_last_at = recorded_at
        self.health.snapshot_last_status = f"OK rows={count}"
        return count

    async def run(self) -> None:
        await self.initialize()
        async with asyncio.TaskGroup() as tasks:
            tasks.create_task(
                self.websocket.run(self.handle_message, self._before_websocket_connect)
            )
            tasks.create_task(self._snapshot_loop())
            tasks.create_task(self._refresh_loop())
            tasks.create_task(self._health_loop())

    async def _snapshot_loop(self) -> None:
        while True:
            await self.record_snapshot_once()
            await asyncio.sleep(self.settings.polymarket_snapshot_interval_seconds)

    async def _refresh_loop(self) -> None:
        while True:
            await asyncio.sleep(self.settings.polymarket_gamma_refresh_seconds)
            await self.refresh_universe()

    async def _health_loop(self) -> None:
        while True:
            await asyncio.sleep(10)
            recorded_at = utc_now().isoformat()
            try:
                await asyncio.to_thread(self.storage.append_health, self.health, recorded_at)
            except Exception:
                self.health.storage_failures += 1
                raise
            _LOG.info("Polymarket capture health=%s", self.health.as_record())


async def _public_smoke_test(settings: AppSettings) -> int:
    gamma = GammaClient(
        str(settings.polymarket_gamma_base_url),
        page_limit=min(settings.polymarket_gamma_page_limit, 20),
    )
    discovery = await gamma.discover_active_markets()
    selector = ElectionUniverseSelector(
        include_ids=parse_id_csv(settings.polymarket_include_ids),
        exclude_ids=parse_id_csv(settings.polymarket_exclude_ids),
    )
    selected = selector.select(discovery.markets)
    if not selected.markets:
        raise RuntimeError("live smoke found no selected election markets")
    clob = ClobMarketDataClient(str(settings.polymarket_clob_base_url), batch_size=10)
    books = await clob.fetch_books(selected.token_ids[: min(10, len(selected.token_ids))])
    if not books:
        raise RuntimeError("live smoke received no CLOB books")
    _LOG.info(
        "public smoke ok markets=%d selected=%d books=%d",
        len(discovery.markets),
        len(selected.markets),
        len(books),
    )
    return 0


def _event_payload(payload: JsonObject) -> JsonObject:
    inner: Any = payload.get("payload")
    if payload.get("topic") == "market" and isinstance(inner, dict):
        return inner
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Polymarket public-data recorder")
    parser.add_argument(
        "--smoke-test",
        action="store_true",
        help="perform explicit read-only public Gamma/CLOB checks and exit",
    )
    args = parser.parse_args(argv)
    settings = load_settings()
    logging.basicConfig(
        level=getattr(logging, settings.log_level),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    if args.smoke_test:
        return asyncio.run(_public_smoke_test(settings))
    if not settings.polymarket_capture_enabled:
        _LOG.error(
            "Polymarket capture is disabled; set PREDICTIONS_CUP_POLYMARKET_CAPTURE_ENABLED=true "
            "to run the explicit recorder process"
        )
        return 2
    recorder = PolymarketRecorder(settings)
    try:
        asyncio.run(recorder.run())
    except KeyboardInterrupt:
        _LOG.info("Polymarket recorder stopped by operator")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
