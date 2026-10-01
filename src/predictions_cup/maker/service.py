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
from collections.abc import Iterable, Mapping
from contextlib import suppress
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from time import monotonic_ns
from typing import cast

from pydantic import ValidationError

from predictions_cup.config import AppSettings, load_settings
from predictions_cup.execution.interlocks import (
    assert_live_interlocks,
    assert_live_recovery_interlocks,
)
from predictions_cup.execution.journal import ExecutionJournal
from predictions_cup.execution.live import SigLiveSink
from predictions_cup.execution.models import (
    ExecutionEnvelope,
    ExecutionEvent,
    ExecutionMode,
    LifecycleState,
)
from predictions_cup.execution.recovery import (
    recover_in_session_cancellations,
    recover_startup,
)
from predictions_cup.execution.reservations import ExecutionReservationBook
from predictions_cup.execution.sinks import ExecutionPlan
from predictions_cup.external.polymarket.client import ClobMarketDataClient
from predictions_cup.external.polymarket.health import IngestionHealth
from predictions_cup.external.polymarket.models import JsonObject, PayloadError
from predictions_cup.external.polymarket.orderbook import OrderBookStore, event_type
from predictions_cup.external.polymarket.websocket import MarketWebSocket
from predictions_cup.maker.adapters import (
    LiveMakerExecutionAdapter,
    ShadowMakerExecutionAdapter,
)
from predictions_cup.maker.contracts import MakerMarketSnapshot
from predictions_cup.maker.coordinator import MakerCoordinator, MakerStateChange
from predictions_cup.maker.factory import MakerRuntimeComponents, build_maker_components
from predictions_cup.maker.instance_lock import MakerInstanceLock
from predictions_cup.maker.noop_recorder import NoopSigRealtimeRecorder
from predictions_cup.maker.recovery import reconcile_maker_quote_registry
from predictions_cup.maker.residual_live import ResidualTakerLiveCoordinator
from predictions_cup.maker.residual_taker import resolve_residual_universe
from predictions_cup.maker.runtime_loop import ExecutionObserver, MakerRuntimeLoop
from predictions_cup.maker.sources import MakerSourceBridge
from predictions_cup.mapping.models import MappingDocument
from predictions_cup.models.registry import default_model_registry
from predictions_cup.models.runtime import LiveModelCoordinator, ModelRuntime
from predictions_cup.observe import (
    BoundedObservationEmitter,
    CaptureObservationSink,
    CompetitionContextSampler,
    ObservationHealthProvider,
    ObservationHealthSnapshot,
    ObservationHealthStatusPublisher,
    SigOfficialCompetitionContextProvider,
    default_observation_health_status_path,
)
from predictions_cup.risk import (
    CapitalControlService,
    CapitalRiskState,
    ExposureAttribution,
    ExternalCashFlowScan,
    ReconciliationError,
    RiskContext,
    RiskContextSource,
    SigRealtimeRiskMarkProvider,
    SqliteRiskStateStore,
    apply_external_cash_flow_scan,
    attribute_strategy_exposure,
    fetch_tournament_fills,
    latest_tournament_transaction_id,
    normalize_sig_risk_inputs,
    scan_external_cash_flows,
    validate_restart_preflight,
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
from predictions_cup.sig.errors import SigApiError, SigExecutionUncertainError
from predictions_cup.sig.governed_client import GovernedSigRestClient, build_rest_governor
from predictions_cup.sig.launch_storage import ObservationCaptureRecorder
from predictions_cup.sig.realtime_models import MarketBatchDto
from predictions_cup.sig.realtime_state import SigRealtimeStateEngine, SubscriptionReason
from predictions_cup.sig.realtime_storage import SigRealtimeRecorder
from predictions_cup.sig.realtime_subscriber import (
    SubscriberExit,
    SupabaseTournamentSubscriber,
)
from predictions_cup.sig.rest_governor import RestPriority
from predictions_cup.sig.trading_client import SigTradingClient
from predictions_cup.sig.trading_dto import PortfolioPnlDto

_LOG = logging.getLogger(__name__)
_LIVE_MAX_EXCHANGES = 160


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
        self._live_exchange_ids: frozenset[str] | None = None
        if self.core.risk_context.mode is ExecutionMode.LIVE:
            configured = frozenset(_configured_tracked_exchanges(settings))
            if not configured:
                raise ValueError(
                    "LIVE MAKE requires an explicit sig_realtime_tracked_exchange_ids universe"
                )
            if len(configured) > _LIVE_MAX_EXCHANGES:
                raise ValueError(
                    f"LIVE MAKE launch universe exceeds {_LIVE_MAX_EXCHANGES} exchanges"
                )
            self._live_exchange_ids = configured
        self._pm_token_ids = _mapped_token_ids(
            self.core.mapping,
            exchange_ids=self._live_exchange_ids,
        )
        self._last_health: tuple[bool, bool, bool, datetime | None] | None = None
        self._observation_health_provider: ObservationHealthProvider | None = None
        self._observation_health_publisher: ObservationHealthStatusPublisher | None = None
        # Resolve/validate both model allowlists at service startup.  In
        # particular, ENV live-on + code live-off fails before any market loop.
        self._model_runtime = ModelRuntime.from_allowlists(
            default_model_registry(),
            paper_model_ids=settings.model_paper_ids,
            live_model_ids=settings.model_live_ids,
            platform_live_ready=False,
        )

    def observation_health(self) -> ObservationHealthSnapshot | None:
        provider = self._observation_health_provider
        return None if provider is None else provider.health()

    async def run(self) -> None:
        tournament_id, tournament_slug = self._tournament_context()
        governor = build_rest_governor(self.settings)
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
        self._observation_health_publisher = ObservationHealthStatusPublisher(
            default_observation_health_status_path(self.settings.sig_research_path),
            process_instance_id=observe_recorder.session_id,
            owner="predictions-cup-maker.service",
        )
        self._publish_observation_health(force=True)
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
        risk_store: SqliteRiskStateStore | None = None
        risk_service: CapitalControlService | None = None
        risk_context_source: RiskContextSource | None = None
        authoritative_account: AccountAuthoritativeSnapshot | None = None
        live_sink: SigLiveSink | None = None
        instance_lock: MakerInstanceLock | None = None
        if self.core.risk_context.mode is ExecutionMode.LIVE:
            lock_path = self.settings.execution_journal_path.with_name(
                self.settings.execution_journal_path.name + ".make.lock"
            )
            instance_lock = MakerInstanceLock(lock_path)
            instance_lock.acquire()

        try:
            if self.settings.risk_capital_control_enabled:
                risk_store = SqliteRiskStateStore(self.settings.risk_state_path)
                risk_service = CapitalControlService(
                    store=risk_store,
                    max_account_age_ns=(
                        self.settings.risk_max_account_age_ms * 1_000_000
                    ),
                    max_mark_age_ns=self.settings.risk_max_mark_age_ms * 1_000_000,
                    session_loss_limit=(
                        None
                        if self.settings.risk_session_loss_limit is None
                        else Decimal(str(self.settings.risk_session_loss_limit))
                    ),
                    drawdown_limit=(
                        None
                        if self.settings.risk_drawdown_limit is None
                        else Decimal(str(self.settings.risk_drawdown_limit))
                    ),
                )
                expected_profile_version = (
                    self.core.risk_context.profile.version
                    if self.core.risk_context.profile is not None
                    else self.settings.risk_profile_version
                )
                validate_restart_preflight(
                    risk_store.load(),
                    session_id=tournament_id,
                    profile_version=expected_profile_version,
                    live_recovery=(
                        self.core.risk_context.mode is ExecutionMode.LIVE
                    ),
                )

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
            authoritative_account = initial_account

            tracked = _configured_tracked_exchanges(self.settings)
            if self.settings.maker_require_trusted_depth:
                records = self.core.mapping.normalized().records
                required = (
                    set(self._live_exchange_ids)
                    if self._live_exchange_ids is not None
                    else {
                        record.sig_exchange_id
                        for record in records
                        if record.mapping_class.value not in {"NO_TRADE", "MODEL_ONLY"}
                    }
                )
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
                bulk_prices_tracked_only=True,
                extra_bulk_price_exchange_ids=tuple(
                    value.strip()
                    for value in self.settings.maker_mark_only_exchange_ids.split(",")
                    if value.strip()
                ),
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
                allowed_exchange_ids=self._live_exchange_ids,
            )

            if self.settings.shadow_enabled:
                shadow_runtime = build_live_shadow_runtime(self.settings, self.core)
                await shadow_runtime.start()
                self._observe_005f_books(
                    self._pm_token_ids,
                    shadow_runtime,
                    observed_monotonic_ns=monotonic_ns(),
                    source_version="clob-rest-seed-v1",
                    trusted=True,
                )

            adapter: LiveMakerExecutionAdapter | ShadowMakerExecutionAdapter
            if self.core.risk_context.mode is ExecutionMode.LIVE:
                journal = ExecutionJournal(self.settings.execution_journal_path)
                trading = SigTradingClient(
                    self.settings,
                    governor=governor,
                )
                recovery_permit = assert_live_recovery_interlocks(
                    self.settings,
                    explicit_live_invocation=self.explicit_live_invocation,
                    account_trusted=account_state.trusted,
                )
                live_sink = SigLiveSink(
                    client=trading,
                    journal=journal,
                    permit=recovery_permit,
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
                    # The production service does not replay fresh economic
                    # placements until current risk/market eligibility has been
                    # independently proven. Cancellation recovery still runs.
                    placement_replay_allowed=lambda _envelope: False,
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
                authoritative_account = authoritative
                reconcile_maker_quote_registry(
                    journal=journal,
                    authoritative=authoritative,
                    quotes=self.core.quotes,
                    observed_monotonic_ns=monotonic_ns(),
                )
            else:
                if self.explicit_live_invocation:
                    raise ValueError(
                        "--live was supplied but PREDICTIONS_CUP_EXECUTION_MODE is not LIVE"
                    )
                adapter = ShadowMakerExecutionAdapter()

            risk_context: RiskContext | RiskContextSource = self.core.risk_context
            if self.settings.risk_capital_control_enabled:
                if authoritative_account is None:
                    raise RuntimeError("RISK-002 missing authoritative account state")
                if risk_store is None or risk_service is None:
                    raise RuntimeError("RISK-002 durable service was not initialized")
                risk_context_source = await self._initialize_capital_control(
                    rest=rest,
                    account=authoritative_account,
                    journal=journal,
                    service=risk_service,
                    sig_state=sig_state,
                    tournament_id=tournament_id,
                    tournament_slug=tournament_slug,
                )
                await self._checkpoint_realtime_risk_transition(
                    source=risk_context_source,
                    service=risk_service,
                )
                risk_context = risk_context_source

            if self.core.risk_context.mode is ExecutionMode.LIVE:
                if (
                    journal is None
                    or trading is None
                    or live_sink is None
                    or risk_context_source is None
                ):
                    raise RuntimeError(
                        "LIVE startup missing execution or RISK-002 recovery authority"
                    )
                # Startup reads above can outlast the RISK mark age; refresh
                # held-position marks right before the admission interlock.
                held_exchange_ids = {
                    position.exchange_id
                    for position in (
                        authoritative_account.positions
                        if authoritative_account is not None
                        else ()
                    )
                }.intersection(sig_state.states)
                if held_exchange_ids:
                    await sig_state.refresh_exchange_prices(
                        held_exchange_ids, reason="live_startup_risk_marks"
                    )
                capital = risk_context_source().capital_state
                capital_ready = (
                    capital is not None
                    and capital.reconciliation_complete
                    and capital.account_trusted
                    and capital.exposure.trusted
                    and capital.marks_trusted
                )
                if not capital_ready:
                    # Raise the canonical interlock error and identify the exact
                    # missing admission gate. Recovery/cancel work has already
                    # been allowed above, but fresh economic exposure is not.
                    assert_live_interlocks(
                        self.settings,
                        explicit_live_invocation=self.explicit_live_invocation,
                        account_trusted=account_state.trusted,
                        capital_state_ready=False,
                    )
                    raise AssertionError("unreachable")

                assert capital is not None
                capital_halts_make = (
                    (
                        capital.global_halt is not None
                        and capital.global_halt.active
                    )
                    or capital.strategy_halted(
                        self.core.engine.strategy_id,
                        "MAKE",
                    )
                )
                if not capital_halts_make:
                    permit = assert_live_interlocks(
                        self.settings,
                        explicit_live_invocation=self.explicit_live_invocation,
                        account_trusted=account_state.trusted,
                        capital_state_ready=True,
                    )
                    live_sink = SigLiveSink(
                        client=trading,
                        journal=journal,
                        permit=permit,
                        reservations=self.core.reservations,
                    )
                # Under a durable matching halt retain the recovery-only sink:
                # cancels/reconciliation remain legal, fresh placement is
                # impossible both at central Risk and at the sink capability.
                adapter = LiveMakerExecutionAdapter(
                    live_sink,
                    journal=journal,
                    quotes=self.core.quotes,
                    observation_emitter=observation_emitter,
                    observation_process_instance_id=observe_recorder.session_id,
                )

            model_execution_observer: ExecutionObserver | None = None
            if (
                self.settings.model_live_ids.strip()
                and self.core.risk_context.mode is ExecutionMode.LIVE
            ):
                if (
                    risk_context_source is None
                    or live_sink is None
                    or shadow_runtime is None
                ):
                    raise RuntimeError(
                        "LIVE model runtime requires RISK-002, BUILD-009 and SHADOW"
                    )
                self._model_runtime = ModelRuntime.from_allowlists(
                    default_model_registry(),
                    paper_model_ids=self.settings.model_paper_ids,
                    live_model_ids=self.settings.model_live_ids,
                    platform_live_ready=True,
                )
                model_live_sink = live_sink

                async def dispatch_model_plan(
                    plan: ExecutionPlan,
                    snapshot: MakerMarketSnapshot,
                ) -> ExecutionEvent:
                    del snapshot
                    try:
                        return await model_live_sink.dispatch(plan)
                    except SigExecutionUncertainError:
                        return await model_live_sink.dispatch_recovery(
                            plan.envelope,
                            plan=plan,
                        )

                model_coordinator = LiveModelCoordinator(
                    self._model_runtime,
                    mapping_version=shadow_runtime.mapping_version,
                    risk_context=risk_context,
                    reservations=self.core.reservations,
                    dispatch=dispatch_model_plan,
                    decision_observer=shadow_runtime.persist_model_decision,
                )
                async def observe_live_models(
                    change: MakerStateChange,
                    observed_at: datetime,
                    snapshots: Mapping[str, MakerMarketSnapshot],
                ) -> None:
                    await model_coordinator.on_state_change(
                        change,
                        observed_at,
                        snapshots,
                    )

                model_execution_observer = observe_live_models

            if (
                self.settings.residual_taker_enabled
                and not self.settings.residual_taker_shadow_only
                and self.core.risk_context.mode is ExecutionMode.LIVE
            ):
                if risk_context_source is None or live_sink is None or journal is None:
                    raise RuntimeError(
                        "LIVE residual taker requires RISK-002 and BUILD-009"
                    )
                taker_sink = live_sink

                async def dispatch_taker_plan(plan: ExecutionPlan) -> ExecutionEvent:
                    try:
                        return await taker_sink.dispatch(plan)
                    except SigExecutionUncertainError:
                        return await taker_sink.dispatch_recovery(
                            plan.envelope,
                            plan=plan,
                        )

                taker = ResidualTakerLiveCoordinator(
                    mapping=self.core.mapping,
                    tracked_exchange_ids=resolve_residual_universe(
                        self.core.mapping,
                        self.settings.residual_taker_exchange_ids,
                        self.settings.sig_realtime_tracked_exchange_ids,
                    ),
                    size=self.settings.residual_taker_size,
                    max_pm_book_age_ns=(
                        self.settings.residual_taker_max_pm_book_age_ms * 1_000_000
                    ),
                    risk_context=risk_context,
                    reservations=self.core.reservations,
                    journal=journal,
                    quotes=self.core.quotes,
                    dispatch=dispatch_taker_plan,
                    cancel=taker_sink.cancel,
                    kill_switch=self.core.kill_switch,
                )
                _LOG.warning("RESIDUAL-TAKER-001 LIVE enabled")
                upstream_observer = model_execution_observer

                async def observe_live_taker(
                    change: MakerStateChange,
                    observed_at: datetime,
                    snapshots: Mapping[str, MakerMarketSnapshot],
                ) -> None:
                    if upstream_observer is not None:
                        await upstream_observer(change, observed_at, snapshots)
                    await taker.on_state_change(change, observed_at, snapshots)

                model_execution_observer = observe_live_taker

            coordinator = MakerCoordinator(
                engine=self.core.engine,
                lifecycle=self.core.lifecycle,
                quote_registry=self.core.quotes,
                risk_context=risk_context,
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
                polymarket_feed_trusted=self._polymarket_feed_trusted,
                telemetry=self.telemetry,
                snapshot_observer=(
                    None if shadow_runtime is None else shadow_runtime.observe
                ),
                execution_observer=model_execution_observer,
                # LIVE writes are paced one exchange at a time so the asyncio
                # shell regains control between governed REST operations and
                # snapshots the next market from current state.
                max_exchanges_per_cycle=(
                    1
                    if self.core.risk_context.mode is ExecutionMode.LIVE
                    else None
                ),
            )

            capital_refresh_task: asyncio.Task[object] | None = None

            def _log_capital_refresh_failure(task: asyncio.Task[object]) -> None:
                if not task.cancelled() and task.exception() is not None:
                    # Capital state keeps its last account observation and so
                    # ages out under RISK's max account age (fail closed).
                    _LOG.warning(
                        "RISK-002 capital refresh failed: %s: %s",
                        type(task.exception()).__name__,
                        task.exception(),
                    )

            async def account_resync() -> AccountAuthoritativeSnapshot:
                nonlocal capital_refresh_task
                async with rest.priority(RestPriority.NORMAL):
                    await rest.get_account()
                    authoritative = await reconcile_account(
                        rest,
                        tournament_id=tournament_id,
                        tournament_slug=tournament_slug,
                    )
                if journal is not None and live_sink is not None:
                    resolved_cancels = await recover_in_session_cancellations(
                        journal=journal,
                        rest=rest,
                        live_sink=live_sink,
                        tournament_id=tournament_id,
                    )
                    if resolved_cancels:
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
                if (
                    risk_context_source is not None
                    and risk_service is not None
                    and (capital_refresh_task is None or capital_refresh_task.done())
                ):
                    # RISK's ~5 extra reads run outside the account trust
                    # window: account activity from our own orders during them
                    # otherwise invalidated almost every account resync.
                    capital_refresh_task = asyncio.create_task(
                        self._refresh_capital_control(
                            rest=rest,
                            account=authoritative,
                            journal=journal,
                            service=risk_service,
                            source=risk_context_source,
                            tournament_id=tournament_id,
                            tournament_slug=tournament_slug,
                        )
                    )
                    capital_refresh_task.add_done_callback(_log_capital_refresh_failure)
                return authoritative

            if isinstance(adapter, LiveMakerExecutionAdapter):
                async def reconcile_uncertain_cancels() -> None:
                    assert journal is not None and live_sink is not None
                    # A closed-order 409 needs one order/fill check. A full
                    # account and capital refresh can fail under SIG REST load
                    # before reaching that targeted recovery.
                    await recover_in_session_cancellations(
                        journal=journal,
                        rest=rest,
                        live_sink=live_sink,
                        tournament_id=tournament_id,
                    )

                adapter.set_cancel_uncertainty_resolver(reconcile_uncertain_cancels)

            account_controller = AccountRealtimeController(
                state=account_state,
                mint_token=rest.mint_realtime_token,
                authoritative_resync=account_resync,
                refresh_interval_seconds=(
                    self.settings.maker_account_refresh_interval_seconds
                ),
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
                            shadow_runtime,
                        ),
                        lambda: self._before_pm_connect(runtime, shadow_runtime),
                    ),
                    name="make-polymarket",
                ),
                asyncio.create_task(
                    self._run_sig_market_feed(
                        sig_state,
                        rest,
                        runtime,
                        context_sampler,
                        risk_context_source=risk_context_source,
                        risk_service=risk_service,
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
            if instance_lock is not None:
                instance_lock.close()
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
            if risk_store is not None:
                risk_store.close()
            with suppress(Exception):
                await context_sampler.aclose()
            observation_emitter.close()
            observe_recorder.close()
            await rest.aclose()

    async def _initialize_capital_control(
        self,
        *,
        rest: GovernedSigRestClient,
        account: AccountAuthoritativeSnapshot,
        journal: ExecutionJournal | None,
        service: CapitalControlService,
        sig_state: SigRealtimeStateEngine,
        tournament_id: str,
        tournament_slug: str,
    ) -> RiskContextSource:
        baseline_pnl, baseline_cursor = await self._stable_risk_session_baseline(
            rest=rest,
            tournament_slug=tournament_slug,
        )
        state = service.load_or_initialize(
            session_id=tournament_id,
            start_equity=baseline_pnl.total_account_value,
            start_unrealised_pnl=baseline_pnl.unrealized_pnl,
            profile_version=(
                self.core.risk_context.profile.version
                if self.core.risk_context.profile is not None
                else self.settings.risk_profile_version
            ),
            observed_monotonic_ns=monotonic_ns(),
            realised_pnl_cursor=baseline_cursor,
            external_cash_flow_cursor=baseline_cursor,
        )
        source = RiskContextSource(
            self.core.risk_context,
            state=state,
            mark_provider=SigRealtimeRiskMarkProvider(sig_state),
            halt_checkpoint=lambda halted, reason: service.checkpoint(
                halted,
                event_type="GLOBAL_HALT",
                detail=reason,
            ),
        )
        await self._refresh_capital_control(
            rest=rest,
            account=account,
            journal=journal,
            service=service,
            source=source,
            tournament_id=tournament_id,
            tournament_slug=tournament_slug,
        )
        return source

    async def _refresh_capital_control(
        self,
        *,
        rest: GovernedSigRestClient,
        account: AccountAuthoritativeSnapshot,
        journal: ExecutionJournal | None,
        service: CapitalControlService,
        source: RiskContextSource,
        tournament_id: str,
        tournament_slug: str,
    ) -> CapitalRiskState:
        state = source.state
        if state is None:
            raise RuntimeError("RISK-002 context has no durable session state")

        try:
            scan, pnl = await self._stable_incremental_risk_reads(
                rest=rest,
                tournament_slug=tournament_slug,
                prior_event_id=state.external_cash_flow_cursor,
            )
        except (ReconciliationError, SigApiError) as exc:
            return self._block_risk_refresh(
                state=state,
                service=service,
                source=source,
                detail=f"{type(exc).__name__}:{exc}",
            )
        updated = apply_external_cash_flow_scan(state, scan)
        if updated != state:
            service.checkpoint(
                updated,
                event_type="EXTERNAL_CASH_FLOW_RECONCILIATION",
                detail=(
                    f"delta={scan.delta};events={scan.events_scanned};"
                    f"cursor={updated.external_cash_flow_cursor}"
                ),
            )
        state = updated

        attributions: tuple[ExposureAttribution, ...] = ()
        attribution_complete: bool | None = None
        if self.settings.risk_max_per_strategy_exposure is not None:
            if journal is None:
                attribution_complete = not account.positions and not account.open_orders
            else:
                try:
                    fills = await fetch_tournament_fills(
                        rest,
                        tournament_id=tournament_id,
                    )
                except SigApiError as exc:
                    return self._block_risk_refresh(
                        state=state,
                        service=service,
                        source=source,
                        detail=f"{type(exc).__name__}:{exc}",
                    )
                attribution = attribute_strategy_exposure(
                    journal=journal,
                    account=account,
                    fills=fills,
                    market_by_exchange={
                        record.sig_exchange_id: record.sig_market_id
                        for record in self.core.mapping.records
                    },
                )
                attributions = attribution.attributions
                attribution_complete = attribution.complete
                if not attribution.complete:
                    _LOG.error(
                        "RISK-002 strategy exposure attribution incomplete; "
                        "fresh strategy risk remains blocked: %s",
                        attribution.reason,
                    )

        observed_ns = monotonic_ns()
        inputs = normalize_sig_risk_inputs(
            session_id=tournament_id,
            session_start_equity=state.session_start_equity,
            session_start_unrealised_pnl=state.session_start_unrealised_pnl,
            account=account,
            pnl=pnl,
            observed_monotonic_ns=observed_ns,
            net_external_cash_flow=state.net_external_cash_flow,
            unresolved_operation_ids=_risk_uncertain_operation_ids(
                journal,
                self.core.reservations,
            ),
            realised_pnl_cursor=state.realised_pnl_cursor,
            attributions=attributions,
            memberships=self.core.risk_context.exposure_groups,
            strategy_attribution_complete=attribution_complete,
        )
        if (
            self.settings.risk_max_event_group_exposure is not None
            and not inputs.exposure.group_classification_complete
        ):
            _LOG.error(
                "RISK-002 event-group classification incomplete; "
                "event-group admission remains blocked"
            )

        try:
            reconciled = service.reconcile(
                state=state,
                authoritative=inputs.authoritative,
                reconstruction=inputs.reconstruction,
                exposure=inputs.exposure,
                marks=inputs.marks,
                now_monotonic_ns=observed_ns,
            )
        except ReconciliationError as exc:
            return self._block_risk_refresh(
                state=state,
                service=service,
                source=source,
                detail=f"{type(exc).__name__}:{exc}",
            )
        source.publish(
            reconciled,
            valuation_positions=inputs.valuation_positions,
        )
        return reconciled

    @staticmethod
    def _block_risk_refresh(
        *,
        state: CapitalRiskState,
        service: CapitalControlService,
        source: RiskContextSource,
        detail: str,
    ) -> CapitalRiskState:
        blocked = replace(
            state,
            account_trusted=False,
            marks_trusted=False,
            reconciliation_complete=False,
        )
        service.checkpoint(
            blocked,
            event_type="RECONCILIATION_BLOCKED",
            detail=detail,
        )
        source.publish(blocked)
        return blocked

    async def _stable_risk_session_baseline(
        self,
        *,
        rest: GovernedSigRestClient,
        tournament_slug: str,
    ) -> tuple[PortfolioPnlDto, str | None]:
        for _ in range(3):
            before = await latest_tournament_transaction_id(
                rest,
                tournament_slug=tournament_slug,
            )
            pnl = await rest.get_tournament_pnl(tournament_slug, period="all")
            after = await latest_tournament_transaction_id(
                rest,
                tournament_slug=tournament_slug,
            )
            if before == after:
                return pnl, after
        raise RuntimeError("RISK-002 could not establish a stable session baseline")

    async def _stable_incremental_risk_reads(
        self,
        *,
        rest: GovernedSigRestClient,
        tournament_slug: str,
        prior_event_id: str | None,
    ) -> tuple[ExternalCashFlowScan, PortfolioPnlDto]:
        for _ in range(3):
            scan = await scan_external_cash_flows(
                rest,
                tournament_slug=tournament_slug,
                prior_event_id=prior_event_id,
            )
            pnl = await rest.get_tournament_pnl(tournament_slug, period="all")
            after = await latest_tournament_transaction_id(
                rest,
                tournament_slug=tournament_slug,
            )
            observed_newest = (
                scan.newest_event_id
                if scan.newest_event_id is not None
                else prior_event_id
            )
            if after == observed_newest:
                return scan, pnl
        raise ReconciliationError(
            "RISK-002 authoritative reads did not reach a stable fence"
        )

    async def _checkpoint_realtime_risk_transition(
        self,
        *,
        source: RiskContextSource | None,
        service: CapitalControlService | None,
    ) -> None:
        if source is None or service is None:
            return
        before = source.state
        after = source.refresh(now_ns=monotonic_ns())
        if before is None or after is None:
            return
        new_peak = after.peak_session_equity > before.peak_session_equity
        new_halt = (
            after.global_halt is not None
            and after.global_halt.active
            and (
                before.global_halt is None
                or not before.global_halt.active
            )
        )
        if not new_peak and not new_halt:
            return
        event_type = "GLOBAL_HALT" if new_halt else "PEAK_EQUITY"
        detail = (
            after.global_halt.reason
            if new_halt and after.global_halt is not None
            else f"peak={after.peak_session_equity}"
        )
        await asyncio.to_thread(
            service.checkpoint,
            after,
            event_type=event_type,
            detail=detail,
        )

    async def _run_sig_market_feed(
        self,
        sig_state: SigRealtimeStateEngine,
        rest: GovernedSigRestClient,
        runtime: MakerRuntimeLoop,
        context_sampler: CompetitionContextSampler,
        *,
        risk_context_source: RiskContextSource | None = None,
        risk_service: CapitalControlService | None = None,
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
                    topics=sig_state.subscription_topics(
                        exchange_ids=_mapped_sig_exchange_ids(
                            self.core.mapping,
                            allowed_exchange_ids=self._live_exchange_ids,
                        )
                    ),
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
                        await self._checkpoint_realtime_risk_transition(
                            source=risk_context_source,
                            service=risk_service,
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
                    await self._checkpoint_realtime_risk_transition(
                        source=risk_context_source,
                        service=risk_service,
                    )

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

    def _observe_005f_books(
        self,
        token_ids: Iterable[str],
        shadow_runtime: LiveShadowRuntime | None,
        *,
        observed_monotonic_ns: int,
        source_version: str,
        trusted: bool,
    ) -> None:
        if shadow_runtime is None:
            return
        for token_id in sorted(token_ids):
            book = self.pm_books.snapshot(token_id, 1)
            if book is None:
                continue
            shadow_runtime.observe_polymarket_bbo(
                token_id=token_id,
                observed_at=book.observed_at,
                observed_monotonic_ns=observed_monotonic_ns,
                best_bid=None if book.best_bid is None else float(book.best_bid),
                best_ask=None if book.best_ask is None else float(book.best_ask),
                source_version=source_version,
                trusted=trusted,
            )

    async def _before_pm_connect(
        self,
        runtime: MakerRuntimeLoop,
        shadow_runtime: LiveShadowRuntime | None = None,
    ) -> None:
        self.pm_health.websocket_connected = False
        runtime.notify_global(observed_monotonic_ns=monotonic_ns())
        await self._seed_polymarket_books()
        self._observe_005f_books(
            self._pm_token_ids,
            shadow_runtime,
            observed_monotonic_ns=monotonic_ns(),
            source_version="clob-rest-seed-v1",
            trusted=True,
        )

    async def _handle_pm_message(
        self,
        payload: JsonObject,
        observed_at: datetime,
        runtime: MakerRuntimeLoop,
        shadow_runtime: LiveShadowRuntime | None = None,
    ) -> None:
        try:
            kind = event_type(payload)
            changed_tokens: set[str] = set()
            event_monotonic_ns = monotonic_ns()
            if kind == "book":
                token_id = self.pm_books.apply_full_snapshot(payload, observed_at)
                changed_tokens.add(token_id)
                self._observe_005f_books(
                    {token_id},
                    shadow_runtime,
                    observed_monotonic_ns=event_monotonic_ns,
                    source_version="clob-market-ws-v1",
                    trusted=True,
                )
            elif kind == "price_change":
                result = self.pm_books.apply_price_change(payload, observed_at)
                changed_tokens.update(result.changed_tokens)
                if shadow_runtime is not None:
                    grouped: dict[
                        str,
                        list[tuple[Decimal | None, Decimal | None, datetime]],
                    ] = {}
                    for change in result.changes:
                        grouped.setdefault(change.token_id, []).append(
                            (change.best_bid, change.best_ask, change.observed_at)
                        )
                    for token_id, observations in grouped.items():
                        states: set[tuple[float, float]] = set()
                        invalid = False
                        for bid, ask, _ in observations:
                            if bid is None or ask is None or ask < bid:
                                invalid = True
                                continue
                            states.add((float(bid), float(ask)))
                        if not invalid and len(states) == 1:
                            best_bid, best_ask = next(iter(states))
                            trusted = True
                        else:
                            best_bid = None
                            best_ask = None
                            trusted = False
                        shadow_runtime.observe_polymarket_bbo(
                            token_id=token_id,
                            observed_at=observations[0][2],
                            observed_monotonic_ns=event_monotonic_ns,
                            best_bid=best_bid,
                            best_ask=best_ask,
                            source_version="clob-market-ws-v1",
                            trusted=trusted,
                        )
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
                    observed_monotonic_ns=event_monotonic_ns,
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
            self._publish_observation_health()
            current = (
                self._polymarket_feed_trusted(),
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

    def _polymarket_feed_trusted(self) -> bool:
        if not self.pm_health.websocket_connected:
            return False
        observed_at = self.pm_health.last_message_at
        if observed_at is None:
            return False
        age_seconds = (datetime.now(UTC) - observed_at).total_seconds()
        return (
            0.0 <= age_seconds
            <= self.pm_ws.receive_liveness_timeout_seconds
        )

    def _publish_observation_health(self, *, force: bool = False) -> None:
        provider = self._observation_health_provider
        publisher = self._observation_health_publisher
        if provider is None or publisher is None:
            return
        try:
            publisher.publish(provider.health(), force=force)
        except Exception as exc:
            _LOG.warning(
                "OBSERVE health status publication failed without affecting Risk: %s",
                type(exc).__name__,
            )

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


def _risk_uncertain_operation_ids(
    journal: ExecutionJournal | None,
    reservations: ExecutionReservationBook | None = None,
) -> tuple[str, ...]:
    if journal is None:
        return ()
    # CANCEL_PENDING is deliberately absent. A cancel in flight can only
    # remove exposure; the order stays counted as open/uncertain exposure until
    # SIG confirms, and a fill it races appears in the authoritative positions.
    # Blocking on it made every capital refresh fail while MAKE was cancelling
    # (LIVE 2026-10-01: marks went stale, forcing more cancels).
    uncertain_states = {
        LifecycleState.PENDING,
        LifecycleState.UNCERTAIN,
        LifecycleState.RECONCILING,
    }
    return tuple(
        envelope.logical_operation_id
        for envelope in journal.unresolved()
        if envelope.lifecycle_state in uncertain_states
        and not _placement_in_flight(envelope, reservations)
    )


def _placement_in_flight(
    envelope: ExecutionEnvelope,
    reservations: ExecutionReservationBook | None,
) -> bool:
    """A PENDING placement this process is dispatching right now.

    Its exposure is held in the BUILD-009 reservation book, which RISK
    admission overlays on every limit, so capital reconciliation need not wait
    for SIG to answer (batches can take ~90 s, longer than the mark age).
    A timeout turns it UNCERTAIN, and a leftover from an earlier process has no
    reservation; both still block.
    """
    return (
        reservations is not None
        and envelope.lifecycle_state is LifecycleState.PENDING
        and reservations.contains_operation(
            envelope.logical_operation_id,
            envelope.intent_ids,
        )
    )


def _mapped_token_ids(
    mapping: MappingDocument,
    *,
    exchange_ids: frozenset[str] | None = None,
) -> tuple[str, ...]:
    # Polymarket price_change frames may contain both outcome tokens for a
    # subscribed market even when MAKE only consumes one aligned token for FV.
    # Seed/subscribe the full outcome-token set so every delta has an
    # authoritative snapshot base; MakerSourceBridge still exposes only the
    # mapped/aligned token(s) to the fair-value provider.
    token_ids = {
        token_id
        for record in mapping.records
        if exchange_ids is None or record.sig_exchange_id in exchange_ids
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


def _mapped_sig_exchange_ids(
    mapping: MappingDocument, *, allowed_exchange_ids: frozenset[str] | None
) -> frozenset[str]:
    """Return mapped tradeable SIG exchanges, intersected with the live allowlist."""
    return frozenset(
        record.sig_exchange_id
        for record in mapping.normalized().records
        if record.mapping_class.value not in {"NO_TRADE", "MODEL_ONLY"}
        and (
            allowed_exchange_ids is None
            or record.sig_exchange_id in allowed_exchange_ids
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
