"""EXPERIMENT-001A Polymarket live recorder entry point."""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
from contextlib import suppress
from datetime import datetime
from typing import Any

from predictions_cup.config import AppSettings, load_settings
from predictions_cup.external.polymarket.client import ClobMarketDataClient
from predictions_cup.external.polymarket.gamma import GammaClient
from predictions_cup.external.polymarket.health import IngestionHealth
from predictions_cup.external.polymarket.models import JsonObject, TradeEvent, utc_now
from predictions_cup.external.polymarket.orderbook import OrderBookStore, event_type
from predictions_cup.external.polymarket.parquet_storage import PolymarketResearchStorage
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
        self.research_storage = PolymarketResearchStorage(
            settings.polymarket_research_path,
            shard_seconds=settings.polymarket_parquet_shard_seconds,
            max_rows_per_shard=settings.polymarket_parquet_max_rows_per_shard,
        )
        self.books = OrderBookStore()
        self.selector = ElectionUniverseSelector(
            include_ids=parse_id_csv(settings.polymarket_include_ids),
            exclude_ids=parse_id_csv(settings.polymarket_exclude_ids),
            strict_ids=parse_id_csv(settings.polymarket_supervised_ids),
        )
        self.websocket = MarketWebSocket(str(settings.polymarket_ws_url), self.health)
        self._token_ids: tuple[str, ...] = ()
        self._market_count = 0
        self._last_depth_snapshot_at: datetime | None = None

    async def initialize(self) -> None:
        await asyncio.to_thread(self.storage.initialize)
        await asyncio.to_thread(self.research_storage.initialize)
        await self.refresh_universe()

    async def refresh_universe(self, *, fail_soft_if_initialized: bool = False) -> bool:
        try:
            discovery = await self.gamma.discover_active_markets()
            selection = self.selector.select(discovery.markets)
            if not selection.markets:
                raise RuntimeError("Polymarket universe selector returned no markets")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.health.gamma_last_refresh_at = utc_now()
            self.health.gamma_last_status = f"ERROR: {type(exc).__name__}: {exc}"
            if fail_soft_if_initialized and self._token_ids:
                _LOG.warning(
                    "Polymarket periodic Gamma refresh failed; keeping existing universe: %s: %s",
                    type(exc).__name__,
                    exc,
                )
                return False
            raise

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
        return True

    async def _before_websocket_connect(self) -> None:
        self.books.invalidate_all()
        await self._seed_books(self._token_ids, invalidate=False)

    async def _seed_books(self, token_ids: tuple[str, ...], *, invalidate: bool) -> None:
        if invalidate:
            self.books.invalidate(set(token_ids))
        batches = await self.clob.fetch_book_batches(token_ids)
        latest_observed_at: datetime | None = None
        for batch in batches:
            for payload in batch.books:
                self.books.apply_full_snapshot(payload, batch.observed_at)
            latest_observed_at = batch.observed_at
        missing = set(token_ids) - set(self.books.initialized_tokens)
        if missing:
            raise RuntimeError(f"CLOB /books did not initialize {len(missing)} subscribed tokens")
        if latest_observed_at is not None:
            self.health.last_valid_book_update_at = latest_observed_at

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
            if result.changes:
                try:
                    await asyncio.to_thread(
                        self.research_storage.append_book_changes,
                        result.changes,
                    )
                except Exception:
                    self.health.storage_failures += 1
                    raise
            if result.changed_tokens:
                self.health.last_valid_book_update_at = observed_at
                self.health.last_book_change_at = observed_at
            return
        if kind == "last_trade_price":
            event = _event_payload(payload)
            trade = TradeEvent.from_ws(event, observed_at)
            self.books.apply_last_trade_price(payload, observed_at)
            try:
                await asyncio.to_thread(self.research_storage.append_trade, trade)
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
        depth_rows = 0
        try:
            panel_rows = await asyncio.to_thread(
                self.research_storage.append_observations, snapshots, recorded_at.isoformat()
            )
            depth_due = (
                self._last_depth_snapshot_at is None
                or (recorded_at - self._last_depth_snapshot_at).total_seconds()
                >= self.settings.polymarket_depth_snapshot_interval_seconds
            )
            if depth_due:
                depth_rows = await asyncio.to_thread(
                    self.research_storage.append_snapshots,
                    snapshots,
                    recorded_at.isoformat(),
                )
                self._last_depth_snapshot_at = recorded_at
            await asyncio.to_thread(self.research_storage.flush_due, recorded_at)
        except Exception as exc:
            self.health.storage_failures += 1
            self.health.snapshot_last_at = recorded_at
            self.health.snapshot_last_status = f"ERROR: {type(exc).__name__}: {exc}"
            raise
        self.health.snapshot_last_at = recorded_at
        self.health.snapshot_last_status = (
            f"OK panel_rows={panel_rows} depth_rows={depth_rows}"
        )
        return panel_rows

    async def run(self) -> None:
        await self.initialize()
        try:
            async with asyncio.TaskGroup() as tasks:
                tasks.create_task(
                    self.websocket.run(self.handle_message, self._before_websocket_connect)
                )
                tasks.create_task(self._snapshot_loop())
                tasks.create_task(self._refresh_loop())
                tasks.create_task(self._health_loop())
        finally:
            await asyncio.to_thread(self.research_storage.flush_all)

    async def _snapshot_loop(self) -> None:
        while True:
            await self.record_snapshot_once()
            await asyncio.sleep(self.settings.polymarket_snapshot_interval_seconds)

    async def _refresh_loop(self) -> None:
        while True:
            await asyncio.sleep(self.settings.polymarket_gamma_refresh_seconds)
            await self.refresh_universe(fail_soft_if_initialized=True)

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


async def _run_recorder_until_stopped(
    recorder: PolymarketRecorder,
    stop_event: asyncio.Event,
) -> None:
    recorder_task = asyncio.create_task(recorder.run(), name="polymarket-recorder")
    stop_task = asyncio.create_task(stop_event.wait(), name="polymarket-stop-waiter")
    done, _ = await asyncio.wait(
        {recorder_task, stop_task},
        return_when=asyncio.FIRST_COMPLETED,
    )

    if recorder_task in done:
        stop_task.cancel()
        await asyncio.gather(stop_task, return_exceptions=True)
        await recorder_task
        return

    _LOG.info("Polymarket recorder received shutdown signal")
    recorder.websocket.stop()
    recorder_task.cancel()
    with suppress(asyncio.CancelledError):
        await recorder_task


def _install_signal_handlers(stop_event: asyncio.Event) -> None:
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop_event.set)


async def _public_smoke_test(settings: AppSettings) -> int:
    strict_ids = parse_id_csv(settings.polymarket_supervised_ids)
    gamma = GammaClient(
        str(settings.polymarket_gamma_base_url),
        page_limit=(
            settings.polymarket_gamma_page_limit
            if strict_ids
            else min(settings.polymarket_gamma_page_limit, 20)
        ),
    )
    discovery = await gamma.discover_active_markets()
    selector = ElectionUniverseSelector(
        include_ids=parse_id_csv(settings.polymarket_include_ids),
        exclude_ids=parse_id_csv(settings.polymarket_exclude_ids),
        strict_ids=strict_ids,
    )
    selected = selector.select(discovery.markets)
    if not selected.markets:
        raise RuntimeError("live smoke found no selected election markets")
    clob = ClobMarketDataClient(str(settings.polymarket_clob_base_url), batch_size=10)
    probe_tokens = (
        selected.token_ids
        if strict_ids and len(selected.token_ids) <= 25
        else selected.token_ids[: min(10, len(selected.token_ids))]
    )
    books = await clob.fetch_books(probe_tokens)
    if len(books) != len(probe_tokens):
        raise RuntimeError(
            "live smoke did not receive one CLOB book per probed token: "
            f"requested={len(probe_tokens)} received={len(books)}"
        )
    _LOG.info(
        "public smoke ok markets=%d selected=%d selected_tokens=%d "
        "probed_tokens=%d books=%d strict=%s",
        len(discovery.markets),
        len(selected.markets),
        len(selected.token_ids),
        len(probe_tokens),
        len(books),
        bool(strict_ids),
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
        "--runtime-env-only",
        action="store_true",
        help="Disable local .env loading; intended for supervised runtime services.",
    )
    parser.add_argument(
        "--require-explicit-universe",
        action="store_true",
        help=(
            "Fail closed unless PREDICTIONS_CUP_POLYMARKET_SUPERVISED_IDS supplies "
            "a strict external market/condition/token universe."
        ),
    )
    parser.add_argument(
        "--smoke-test",
        action="store_true",
        help="perform explicit read-only public Gamma/CLOB checks and exit",
    )
    args = parser.parse_args(argv)
    settings = load_settings(use_dotenv=not args.runtime_env_only)
    logging.basicConfig(
        level=getattr(logging, settings.log_level),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    if args.smoke_test:
        return asyncio.run(_public_smoke_test(settings))
    if args.require_explicit_universe and not parse_id_csv(settings.polymarket_supervised_ids):
        _LOG.error(
            "Supervised Polymarket capture requires non-empty "
            "PREDICTIONS_CUP_POLYMARKET_SUPERVISED_IDS"
        )
        return 2
    if not settings.polymarket_capture_enabled:
        _LOG.error(
            "Polymarket capture is disabled; set PREDICTIONS_CUP_POLYMARKET_CAPTURE_ENABLED=true "
            "to run the explicit recorder process"
        )
        return 2
    recorder = PolymarketRecorder(settings)

    async def run_until_stopped() -> None:
        stop_event = asyncio.Event()
        _install_signal_handlers(stop_event)
        await _run_recorder_until_stopped(recorder, stop_event)

    try:
        asyncio.run(run_until_stopped())
    except KeyboardInterrupt:
        _LOG.info("Polymarket recorder stopped by operator")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
