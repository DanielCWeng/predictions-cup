"""Operational single-process service for MAKE-001.

LIVE is impossible without an explicit invocation flag plus every BUILD-009
interlock. The service does not persist Polymarket analytics; it consumes the
accepted mapped token set directly into an in-memory OrderBookStore.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
from contextlib import suppress
from datetime import UTC, datetime
from time import monotonic_ns
from typing import cast

from pydantic import ValidationError

from predictions_cup.config import AppSettings, load_settings
from predictions_cup.execution.interlocks import assert_live_interlocks
from predictions_cup.execution.journal import ExecutionJournal
from predictions_cup.execution.live import SigLiveSink
from predictions_cup.execution.models import ExecutionMode
from predictions_cup.execution.recovery import recover_startup
from predictions_cup.external.polymarket.client import ClobMarketDataClient
from predictions_cup.external.polymarket.health import IngestionHealth
from predictions_cup.external.polymarket.models import JsonObject, PayloadError
from predictions_cup.external.polymarket.orderbook import OrderBookStore, event_type
from predictions_cup.external.polymarket.websocket import MarketWebSocket
from predictions_cup.maker.adapters import (
    LiveMakerExecutionAdapter,
    ShadowMakerExecutionAdapter,
)
from predictions_cup.maker.coordinator import MakerCoordinator
from predictions_cup.maker.factory import MakerRuntimeComponents, build_maker_components
from predictions_cup.maker.noop_recorder import NoopSigRealtimeRecorder
from predictions_cup.maker.recovery import reconcile_maker_quote_registry
from predictions_cup.maker.runtime_loop import MakerRuntimeLoop
from predictions_cup.maker.sources import MakerSourceBridge
from predictions_cup.mapping.models import MappingDocument
from predictions_cup.observe import (
    BoundedObservationEmitter,
    CaptureObservationSink,
    CompetitionContextSampler,
    ObservationHealthProvider,
    ObservationHealthSnapshot,
    SigOfficialCompetitionContextProvider,
)
from predictions_cup.runtime.telemetry import HotPathTelemetry
from predictions_cup.shadow.live import LiveShadowRuntime, build_live_shadow_runtime
from predictions_cup.sig.account_reconciliation import (
    AccountAuthoritativeSnapshot,
    reconcile_account,
)
from predictions_cup.sig.account_runtime import AccountRealtimeController
from predictions_cup.sig.account_state import (
    AccountRealtimeStateEngine,
    AccountTrustTransition,
)
from predictions_cup.sig.errors import SigApiError
from predictions_cup.sig.governed_client import GovernedSigRestClient
from predictions_cup.sig.launch_storage import ObservationCaptureRecorder
from predictions_cup.sig.realtime_models import MarketBatchDto
from predictions_cup.sig.realtime_state import SigRealtimeStateEngine, SubscriptionReason
from predictions_cup.sig.realtime_storage import SigRealtimeRecorder
from predictions_cup.sig.realtime_subscriber import (
    SubscriberExit,
    SupabaseTournamentSubscriber,
)
from predictions_cup.sig.rest_governor import RestPriority, SigRestGovernor
from predictions_cup.sig.trading_client import SigTradingClient

_LOG = logging.getLogger(__name__)


class MakerService:
    def __init__(
        self,
        settings: AppSettings,
        *,
        explicit_live_invocation: bool,
    ) -> None:
        if not settings.maker_enabled:
            raise ValueError("MAKE service requires PREDICTIONS_CUP_MAKER_ENABLED=true")
        self.settings = settings
        self.explicit_live_invocation = explicit_live_invocation
        self.core: MakerRuntimeComponents = build_maker_components(settings)
        self.stop_event = asyncio.Event()
        self.telemetry = HotPathTelemetry()
        self.pm_health = IngestionHealth()
        self.pm_books = OrderBookStore()
        self.pm_clob = ClobMarketDataClient(str(settings.polymarket_clob_base_url))
        self.pm_ws = MarketWebSocket(str(settings.polymarket_ws_url), self.pm_health)
        self._pm_token_ids = _mapped_token_ids(self.core.mapping)
        self._last_health: tuple[bool, bool, bool, datetime | None] | None = None
        self._observation_health_provider: ObservationHealthProvider | None = None

    def observation_health(self) -> ObservationHealthSnapshot | None:
        provider = self._observation_health_provider
        return None if provider is None else provider.health()

    async def run(self) -> None:
        tournament_id, tournament_slug = self._tournament_context()
        governor = SigRestGovernor(
            rate_per_second=self.settings.sig_rest_governor_rate_per_second,
            max_shared_cooldown_seconds=(
                self.settings.sig_rest_shared_cooldown_max_seconds
            ),
        )
        rest = GovernedSigRestClient(self.settings, governor=governor)
        sig_recorder = cast(SigRealtimeRecorder, NoopSigRealtimeRecorder())
        observe_recorder = ObservationCaptureRecorder(
            self.settings.sig_research_path,
            queue_max=max(1_024, min(self.settings.sig_capture_queue_max, 65_536)),
            shard_seconds=self.settings.sig_capture_parquet_shard_seconds,
            max_rows_per_shard=self.settings.sig_capture_parquet_max_rows_per_shard,
        )
        observation_emitter = BoundedObservationEmitter(
            CaptureObservationSink(observe_recorder),
            queue_max=max(1_024, min(self.settings.sig_capture_queue_max, 65_536)),
        )
        self._observation_health_provider = ObservationHealthProvider(
            observation_emitter,
            observe_recorder,
        )
        context_sampler = CompetitionContextSampler(
            SigOfficialCompetitionContextProvider(rest, tournament_id=tournament_id),
            observe_recorder.record_competition_context,
            interval_seconds=60.0,
        )
        journal: ExecutionJournal | None = None
        trading: SigTradingClient | None = None
        sig_state: SigRealtimeStateEngine | None = None
        runtime: MakerRuntimeLoop | None = None
        shadow_runtime: LiveShadowRuntime | None = None

        try:
            account_state = AccountRealtimeStateEngine(
                tournament_id=tournament_id,
                reservations=self.core.reservations,
            )
            initial_account = await reconcile_account(
                rest,
                tournament_id=tournament_id,
                tournament_slug=tournament_slug,
            )
            account_state.apply_authoritative(initial_account)

            tracked = _configured_tracked_exchanges(self.settings)
            if self.settings.maker_require_trusted_depth:
                missing = self.core.mapping.normalized().records
                required = {
                    record.sig_exchange_id
                    for record in missing
                    if record.mapping_class.value not in {"NO_TRADE", "MODEL_ONLY"}
                }
                if not required.issubset(set(tracked)):
                    raise ValueError(
                        "maker_require_trusted_depth requires every tradeable "
                        "SIG exchange in sig_realtime_tracked_exchange_ids"
                    )

            sig_state = SigRealtimeStateEngine(
                rest=rest,
                recorder=sig_recorder,
                tournament_id=tournament_id,
                tracked_depth_exchange_ids=tracked,
                book_depth=self.settings.sig_realtime_book_depth,
                open_book_max_trusted_age_seconds=(
                    self.settings.sig_realtime_open_book_refresh_seconds
                ),
                bulk_price_refresh_seconds=(
                    self.settings.sig_realtime_bulk_price_refresh_seconds
                ),
                governed_rate_per_second=(
                    self.settings.sig_rest_governor_rate_per_second
                ),
                governor_snapshot=rest.governor_snapshot,
                observation_emitter=observation_emitter,
                observation_process_instance_id=observe_recorder.session_id,
            )
            await sig_state.initialize()
            context_sampler.maybe_schedule(datetime.now(UTC), force=True)
            await self._seed_polymarket_books()
            await self.pm_ws.set_tokens(self._pm_token_ids)

            bridge = MakerSourceBridge(
                mapping=self.core.mapping,
                sig_state=sig_state,
                account_state=account_state,
                polymarket_books=self.pm_books,
            )

            if self.settings.shadow_enabled:
                shadow_runtime = build_live_shadow_runtime(self.settings, self.core)
                await shadow_runtime.start()

            adapter: LiveMakerExecutionAdapter | ShadowMakerExecutionAdapter
            if self.core.risk_context.mode is ExecutionMode.LIVE:
                journal = ExecutionJournal(self.settings.execution_journal_path)
                trading = SigTradingClient(
                    self.settings,
                    governor=governor,
                )
                permit = assert_live_interlocks(
                    self.settings,
                    explicit_live_invocation=self.explicit_live_invocation,
                    account_trusted=account_state.trusted,
                )
                live_sink = SigLiveSink(
                    client=trading,
                    journal=journal,
                    permit=permit,
                    reservations=self.core.reservations,
                    observation_emitter=observation_emitter,
                    observation_process_instance_id=observe_recorder.session_id,
                )
                recovery = await recover_startup(
                    journal=journal,
                    rest=rest,
                    live_sink=live_sink,
                    tournament_id=tournament_id,
                    tournament_slug=tournament_slug,
                    observation_emitter=observation_emitter,
                    observation_process_instance_id=observe_recorder.session_id,
                )
                if not recovery.safe_to_resume_live:
                    raise RuntimeError(
                        "LIVE startup blocked by unresolved execution operations: "
                        + ",".join(recovery.unresolved_operation_ids)
                    )
                authoritative = await reconcile_account(
                    rest,
                    tournament_id=tournament_id,
                    tournament_slug=tournament_slug,
                )
                account_state.apply_authoritative(authoritative)
                reconcile_maker_quote_registry(
                    journal=journal,
                    authoritative=authoritative,
                    quotes=self.core.quotes,
                    observed_monotonic_ns=monotonic_ns(),
                )
                adapter = LiveMakerExecutionAdapter(
                    live_sink,
                    journal=journal,
                    quotes=self.core.quotes,
                )
            else:
                if self.explicit_live_invocation:
                    raise ValueError(
                        "--live was supplied but PREDICTIONS_CUP_EXECUTION_MODE is not LIVE"
                    )
                adapter = ShadowMakerExecutionAdapter()

            coordinator = MakerCoordinator(
                engine=self.core.engine,
                lifecycle=self.core.lifecycle,
                quote_registry=self.core.quotes,
                risk_context=self.core.risk_context,
                placement_dispatch=adapter.place,
                cancel_dispatch=adapter.cancel,
                reservations=(
                    self.core.reservations
                    if self.core.risk_context.mode is ExecutionMode.LIVE
                    else None
                ),
                kill_switch=self.core.kill_switch,
                observation_emitter=observation_emitter,
                observation_process_instance_id=observe_recorder.session_id,
            )
            runtime = MakerRuntimeLoop(
                bridge=bridge,
                coordinator=coordinator,
                polymarket_feed_trusted=lambda: self.pm_health.websocket_connected,
                telemetry=self.telemetry,
                snapshot_observer=(
                    None if shadow_runtime is None else shadow_runtime.observe
                ),
                # LIVE writes are paced one exchange at a time so the asyncio
                # shell regains control between governed REST operations and
                # snapshots the next market from current state.
                max_exchanges_per_cycle=(
                    1
                    if self.core.risk_context.mode is ExecutionMode.LIVE
                    else None
                ),
            )

            async def account_resync() -> AccountAuthoritativeSnapshot:
                authoritative = await reconcile_account(
                    rest,
                    tournament_id=tournament_id,
                    tournament_slug=tournament_slug,
                )
                if journal is not None:
                    reconcile_maker_quote_registry(
                        journal=journal,
                        authoritative=authoritative,
                        quotes=self.core.quotes,
                        observed_monotonic_ns=monotonic_ns(),
                    )
                return authoritative

            account_controller = AccountRealtimeController(
                state=account_state,
                mint_token=rest.mint_realtime_token,
                authoritative_resync=account_resync,
                execution_journal=journal,
                observation_emitter=observation_emitter,
                observation_process_instance_id=observe_recorder.session_id,
            )
            # The startup REST snapshot was sufficient for LIVE permit/recovery,
            # but new maker placements must wait for a subscribed account socket
            # plus the controller's while-subscribed authoritative reconciliation.
            account_state.mark_untrusted(AccountTrustTransition.INITIAL)

            self._install_signal_handlers()
            runtime.notify_global(observed_monotonic_ns=monotonic_ns())
            tasks = (
                asyncio.create_task(
                    runtime.run(stop_event=self.stop_event),
                    name="make-runtime",
                ),
                asyncio.create_task(
                    self.pm_ws.run(
                        lambda payload, observed_at: self._handle_pm_message(
                            payload,
                            observed_at,
                            runtime,
                        ),
                        lambda: self._before_pm_connect(runtime),
                    ),
                    name="make-polymarket",
                ),
                asyncio.create_task(
                    self._run_sig_market_feed(
                        sig_state,
                        rest,
                        runtime,
                        context_sampler,
                    ),
                    name="make-sig-market",
                ),
                asyncio.create_task(
                    account_controller.run(stop_event=self.stop_event),
                    name="make-account",
                ),
                asyncio.create_task(
                    self._health_watchdog(sig_state, account_state, runtime),
                    name="make-health-watchdog",
                ),
            )
            stop_waiter = asyncio.create_task(
                self.stop_event.wait(),
                name="make-stop-waiter",
            )
            done, _ = await asyncio.wait(
                {*tasks, stop_waiter},
                return_when=asyncio.FIRST_COMPLETED,
            )
            failure = next(
                (
                    task
                    for task in tasks
                    if task in done and not task.cancelled() and task.exception() is not None
                ),
                None,
            )
            if failure is not None:
                runtime.activate_kill_switch(
                    f"service_task_failure:{failure.get_name()}"
                )
                await self._best_effort_kill_drain(runtime)
                failure_exc = failure.exception()
                if failure_exc is None:
                    raise RuntimeError("MAKE service task failed without exception")
                raise failure_exc

            if stop_waiter in done:
                runtime.activate_kill_switch("operator_shutdown")
                await self._best_effort_kill_drain(runtime)

            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            stop_waiter.cancel()
            await asyncio.gather(stop_waiter, return_exceptions=True)
        finally:
            self.pm_ws.stop()
            if shadow_runtime is not None:
                with suppress(Exception):
                    await shadow_runtime.close()
            if sig_state is not None:
                await sig_state.aclose()
            if trading is not None:
                await trading.aclose()
            if journal is not None:
                journal.close()
            with suppress(Exception):
                await context_sampler.aclose()
            observation_emitter.close()
            observe_recorder.close()
            await rest.aclose()

    async def _run_sig_market_feed(
        self,
        sig_state: SigRealtimeStateEngine,
        rest: GovernedSigRestClient,
        runtime: MakerRuntimeLoop,
        context_sampler: CompetitionContextSampler,
    ) -> None:
        reason = SubscriptionReason.INITIAL_SUBSCRIBE
        while not self.stop_event.is_set():
            try:
                token = await rest.mint_realtime_token()
                if reason is not SubscriptionReason.INITIAL_SUBSCRIBE:
                    await sig_state.prepare_subscription(reason)

                def connected() -> None:
                    sig_state.mark_connected()
                    runtime.notify_global(observed_monotonic_ns=monotonic_ns())

                subscriber = SupabaseTournamentSubscriber(
                    topic=sig_state.topic,
                    token=token,
                    token_refresh_margin_seconds=(
                        self.settings.sig_realtime_token_refresh_margin_seconds
                    ),
                )

                async def on_batch(
                    topic: str,
                    payload: object,
                    observed_at: datetime,
                ) -> None:
                    try:
                        parsed = MarketBatchDto.model_validate(payload)
                    except ValidationError:
                        parsed = None
                    try:
                        await sig_state.handle_raw_batch(topic, payload, observed_at)
                        if parsed is None:
                            runtime.notify_global(
                                observed_monotonic_ns=monotonic_ns()
                            )
                            return
                        dirty = {
                            item.exchange_id for item in parsed.book_dirty
                        }
                        if dirty:
                            await sig_state.refresh_exchange_prices(
                                dirty,
                                reason="maker_book_dirty",
                                priority=RestPriority.HIGH,
                            )
                        affected = dirty | {
                            trade.exchange_id for trade in parsed.trades
                        }
                        if parsed.market_settled:
                            settled = {
                                item.market_id for item in parsed.market_settled
                            }
                            affected.update(
                                state.exchange_id
                                for state in sig_state.states.values()
                                if state.market_id in settled
                            )
                        runtime.notify_sig(
                            affected,
                            observed_monotonic_ns=monotonic_ns(),
                        )
                    except BaseException:
                        runtime.activate_kill_switch("sig_market_state_failure")
                        with suppress(Exception):
                            await runtime.drain_once()
                        raise

                async def maintenance(observed_at: datetime) -> None:
                    await sig_state.maintenance(observed_at)
                    context_sampler.maybe_schedule(observed_at)

                outcome = await subscriber.run(
                    on_batch=on_batch,
                    on_connected=connected,
                    stop_event=self.stop_event,
                    on_maintenance=maintenance,
                )
            except SigApiError:
                sig_state.mark_disconnected()
                runtime.notify_global(observed_monotonic_ns=monotonic_ns())
                await asyncio.sleep(1.0)
                continue

            sig_state.mark_disconnected()
            runtime.notify_global(observed_monotonic_ns=monotonic_ns())
            if outcome is SubscriberExit.STOPPED:
                return
            if outcome is SubscriberExit.TOKEN_REFRESH:
                reason = SubscriptionReason.TOKEN_REFRESH
            elif outcome is SubscriberExit.SOCKET_ERROR:
                reason = SubscriptionReason.SOCKET_ERROR
            else:
                reason = SubscriptionReason.RECONNECT
            await asyncio.sleep(1.0)

    async def _seed_polymarket_books(self) -> None:
        self.pm_books.invalidate_all()
        batches = await self.pm_clob.fetch_book_batches(self._pm_token_ids)
        for batch in batches:
            for payload in batch.books:
                self.pm_books.apply_full_snapshot(payload, batch.observed_at)
        missing = set(self._pm_token_ids).difference(self.pm_books.initialized_tokens)
        if missing:
            raise RuntimeError(
                f"Polymarket CLOB seed missing {len(missing)} mapped tokens"
            )

    async def _before_pm_connect(self, runtime: MakerRuntimeLoop) -> None:
        self.pm_health.websocket_connected = False
        runtime.notify_global(observed_monotonic_ns=monotonic_ns())
        await self._seed_polymarket_books()

    async def _handle_pm_message(
        self,
        payload: JsonObject,
        observed_at: datetime,
        runtime: MakerRuntimeLoop,
    ) -> None:
        try:
            kind = event_type(payload)
            changed_tokens: set[str] = set()
            if kind == "book":
                changed_tokens.add(
                    self.pm_books.apply_full_snapshot(payload, observed_at)
                )
            elif kind == "price_change":
                result = self.pm_books.apply_price_change(payload, observed_at)
                changed_tokens.update(result.changed_tokens)
                self.pm_health.book_uninitialized_delta_count += (
                    result.uninitialized_deltas
                )
                missing_required = result.uninitialized_token_ids.intersection(
                    self._pm_token_ids
                )
                if missing_required:
                    raise RuntimeError(
                        "Polymarket delta arrived before authoritative seed "
                        f"for {len(missing_required)} required mapped token(s)"
                    )
            elif kind == "last_trade_price":
                token_id = _pm_single_token(payload)
                if self.pm_books.apply_last_trade_price(payload, observed_at):
                    changed_tokens.add(token_id)
            elif kind == "tick_size_change":
                token_id = _pm_single_token(payload)
                if self.pm_books.apply_tick_size_change(payload, observed_at):
                    changed_tokens.add(token_id)
            else:
                return
            if changed_tokens:
                self.pm_health.last_valid_book_update_at = observed_at
                runtime.notify_polymarket(
                    changed_tokens,
                    observed_monotonic_ns=monotonic_ns(),
                )
        except (PayloadError, ValueError, KeyError, TypeError):
            runtime.activate_kill_switch("polymarket_state_failure")
            with suppress(Exception):
                await runtime.drain_once()
            raise

    async def _health_watchdog(
        self,
        sig_state: SigRealtimeStateEngine,
        account_state: AccountRealtimeStateEngine,
        runtime: MakerRuntimeLoop,
    ) -> None:
        while not self.stop_event.is_set():
            current = (
                self.pm_health.websocket_connected,
                sig_state.health.connected,
                account_state.trusted,
                account_state.last_accepted_observed_at,
            )
            if current != self._last_health:
                previous = self._last_health
                self._last_health = current
                if previous is None or current[:3] != previous[:3]:
                    runtime.notify_global(observed_monotonic_ns=monotonic_ns())
                if previous is None or current[3] != previous[3]:
                    runtime.notify_account(observed_monotonic_ns=monotonic_ns())
            await asyncio.sleep(0.05)

    async def _best_effort_kill_drain(
        self,
        runtime: MakerRuntimeLoop,
    ) -> None:
        """Drain paced maker cancellations without one failure hiding later quotes."""
        failures = 0
        max_cycles = len(self.core.mapping.records) + 1
        for _ in range(max_cycles):
            try:
                result = await runtime.drain_once()
            except Exception as exc:
                failures += 1
                _LOG.error(
                    "MAKE kill-drain cycle failed closed: %s",
                    type(exc).__name__,
                )
                continue
            if result is None:
                break
        else:
            _LOG.error("MAKE kill-drain did not reach idle within bounded universe")

        if failures:
            _LOG.error(
                "MAKE kill-drain completed with %d failed/uncertain cycles; "
                "authoritative reconciliation is required before any LIVE resume",
                failures,
            )

    def _tournament_context(self) -> tuple[str, str]:
        tournament_id = self.settings.tournament_id or self.core.mapping.tournament_id
        tournament_slug = self.settings.tournament_slug
        if tournament_slug is None:
            raise ValueError("MAKE service requires explicit tournament_slug")
        if tournament_id != self.core.mapping.tournament_id:
            raise ValueError("MAKE service tournament does not match accepted mapping")
        return tournament_id, tournament_slug

    def _install_signal_handlers(self) -> None:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            with suppress(NotImplementedError):
                loop.add_signal_handler(sig, self.stop_event.set)


def _mapped_token_ids(mapping: MappingDocument) -> tuple[str, ...]:
    # Polymarket price_change frames may contain both outcome tokens for a
    # subscribed market even when MAKE only consumes one aligned token for FV.
    # Seed/subscribe the full outcome-token set so every delta has an
    # authoritative snapshot base; MakerSourceBridge still exposes only the
    # mapped/aligned token(s) to the fair-value provider.
    token_ids = {
        token_id
        for record in mapping.records
        for identity in (
            (record.direct_polymarket,)
            if record.direct_polymarket is not None
            else record.polymarket_components
        )
        if identity is not None
        for token_id in identity.token_ids
    }
    return tuple(sorted(token_ids))


def _configured_tracked_exchanges(settings: AppSettings) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            value.strip()
            for value in settings.sig_realtime_tracked_exchange_ids.split(",")
            if value.strip()
        )
    )


def _pm_single_token(payload: JsonObject) -> str:
    event: object = payload
    if payload.get("topic") == "market" and isinstance(payload.get("payload"), dict):
        event = payload["payload"]
    if not isinstance(event, dict):
        raise PayloadError("Polymarket event is not an object")
    token_id = event.get("asset_id") or event.get("tokenId")
    if not isinstance(token_id, str) or not token_id.strip():
        raise PayloadError("Polymarket event is missing asset_id")
    return token_id.strip()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run MAKE-001 market maker")
    parser.add_argument(
        "--runtime-env-only",
        action="store_true",
        help="disable local .env loading",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="explicitly authorize evaluation of BUILD-009 LIVE interlocks",
    )
    parser.add_argument(
        "--smoke-test",
        action="store_true",
        help="validate configuration/mapping and exit without network I/O",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    settings = load_settings(use_dotenv=not args.runtime_env_only)
    logging.basicConfig(
        level=getattr(logging, settings.log_level),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        force=True,
    )
    if args.smoke_test:
        if not settings.maker_enabled:
            _LOG.info("MAKE smoke: maker disabled by default")
        core = build_maker_components(settings)
        _LOG.info(
            "MAKE smoke ok mode=%s records=%d kill=%s",
            core.risk_context.mode.value,
            len(core.mapping.records),
            core.kill_switch.active,
        )
        return 0
    try:
        asyncio.run(
            MakerService(
                settings,
                explicit_live_invocation=args.live,
            ).run()
        )
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
