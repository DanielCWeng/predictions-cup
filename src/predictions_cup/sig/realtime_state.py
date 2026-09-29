"""Authoritative REST reconciliation around best-effort SIG Realtime invalidations."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator, Callable, Iterable, Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Protocol

from pydantic import ValidationError

from predictions_cup.models import OrderBook
from predictions_cup.sig.dto import BulkPricesDto, MarketDto, OrderBookSnapshotDto
from predictions_cup.sig.realtime_models import MarketBatchDto, RealtimeTradeDto
from predictions_cup.sig.realtime_storage import SigRealtimeRecorder
from predictions_cup.sig.rest_governor import RestGovernorSnapshot, RestPriority

logger = logging.getLogger(__name__)
Clock = Callable[[], datetime]
GovernorSnapshotFn = Callable[[], RestGovernorSnapshot]


class SubscriptionReason(StrEnum):
    INITIAL_SUBSCRIBE = "initial_subscribe"
    RECONNECT = "reconnect"
    TOKEN_REFRESH = "token_refresh"
    SOCKET_ERROR = "socket_error"


class DepthState(StrEnum):
    UNTRACKED_DEPTH = "UNTRACKED_DEPTH"
    TRACKED_UNTRUSTED = "TRACKED_UNTRUSTED"
    TRACKED_TRUSTED = "TRACKED_TRUSTED"


class TrustTransition(StrEnum):
    TRUSTED = "TRUSTED"
    UNTRUSTED_INITIAL_SUBSCRIBE = "UNTRUSTED_INITIAL_SUBSCRIBE"
    UNTRUSTED_REVISION_GAP = "UNTRUSTED_REVISION_GAP"
    UNTRUSTED_RECONNECT = "UNTRUSTED_RECONNECT"
    UNTRUSTED_TOKEN_REFRESH = "UNTRUSTED_TOKEN_REFRESH"
    UNTRUSTED_SOCKET_ERROR = "UNTRUSTED_SOCKET_ERROR"
    UNTRUSTED_MALFORMED_PAYLOAD = "UNTRUSTED_MALFORMED_PAYLOAD"
    UNTRUSTED_BOOK_DIRTY = "UNTRUSTED_BOOK_DIRTY"
    UNTRUSTED_SETTLEMENT = "UNTRUSTED_SETTLEMENT"
    UNTRUSTED_PERIODIC_REFRESH = "UNTRUSTED_PERIODIC_REFRESH"
    RECONCILING = "RECONCILING"
    TRUSTED_AFTER_RECONCILIATION = "TRUSTED_AFTER_RECONCILIATION"


class SigStateRest(Protocol):
    def iter_markets(
        self,
        *,
        limit: int = 100,
        tournament_id: str | None = None,
    ) -> AsyncIterator[MarketDto]: ...

    async def get_market(
        self,
        market_id: str,
        *,
        tournament_id: str | None = None,
    ) -> MarketDto: ...

    async def get_orderbook(
        self,
        exchange_id: str,
        *,
        depth: int = 20,
        tournament_id: str | None = None,
    ) -> OrderBookSnapshotDto: ...

    async def get_bulk_prices(
        self,
        exchange_ids: Sequence[str],
        *,
        tournament_id: str | None = None,
    ) -> BulkPricesDto: ...

    def priority(self, priority: RestPriority) -> AbstractAsyncContextManager[None]: ...


@dataclass
class MarketRuntimeState:
    market_id: str
    title: str
    status: str
    settled_with: str | None
    last_rest_observed_at: datetime


@dataclass
class ExchangeRuntimeState:
    exchange_id: str
    market_id: str
    tournament_id: str
    depth_state: DepthState = DepthState.UNTRACKED_DEPTH
    orderbook: OrderBook | None = None
    last_trade: RealtimeTradeDto | None = None
    latest_price: Decimal | None = None
    scalar_best_bid: Decimal | None = None
    scalar_best_ask: Decimal | None = None
    scalar_spread: Decimal | None = None
    last_scalar_observed_at: datetime | None = None
    last_rest_observed_at: datetime | None = None
    last_realtime_observed_at: datetime | None = None
    last_accepted_revision: int | None = None
    last_reconciliation_at: datetime | None = None
    last_reconciliation_attempt_at: datetime | None = None

    @property
    def tracked(self) -> bool:
        return self.depth_state != DepthState.UNTRACKED_DEPTH

    @property
    def trusted(self) -> bool:
        return self.depth_state == DepthState.TRACKED_TRUSTED

    @property
    def best_bid(self) -> Decimal | None:
        if self.trusted:
            if self.orderbook is None or not self.orderbook.bids:
                return None
            return self.orderbook.bids[0].price
        return self.scalar_best_bid

    @property
    def best_ask(self) -> Decimal | None:
        if self.trusted:
            if self.orderbook is None or not self.orderbook.asks:
                return None
            return self.orderbook.asks[0].price
        return self.scalar_best_ask


@dataclass
class RuntimeHealth:
    connected: bool = False
    last_realtime_receive: datetime | None = None
    last_valid_batch: datetime | None = None
    last_rest_reconciliation: datetime | None = None
    revision_gap_count: int = 0
    reconnect_count: int = 0
    reconciliation_failure_count: int = 0
    bounded_book_refresh_count: int = 0
    bulk_price_refresh_count: int = 0
    bulk_price_missing_count: int = 0
    full_book_refresh_count: int = 0


class SigRealtimeStateEngine:
    """Full-universe Realtime plus explicit tracked authoritative depth."""

    def __init__(
        self,
        *,
        rest: SigStateRest,
        recorder: SigRealtimeRecorder,
        tournament_id: str,
        tracked_depth_exchange_ids: Iterable[str] = (),
        book_depth: int = 20,
        open_book_max_trusted_age_seconds: float = 30.0,
        bulk_price_refresh_seconds: float = 10.0,
        governed_rate_per_second: float = 2.0,
        governor_snapshot: GovernorSnapshotFn | None = None,
        clock: Clock = lambda: datetime.now(UTC),
    ) -> None:
        if not tournament_id.strip():
            raise ValueError("tournament_id must not be blank")
        if book_depth < 1 or book_depth > 200:
            raise ValueError("book_depth must be between 1 and 200")
        if open_book_max_trusted_age_seconds <= 0:
            raise ValueError("open_book_max_trusted_age_seconds must be positive")
        if bulk_price_refresh_seconds <= 0:
            raise ValueError("bulk_price_refresh_seconds must be positive")
        if governed_rate_per_second <= 0:
            raise ValueError("governed_rate_per_second must be positive")

        tracked = frozenset(tracked_depth_exchange_ids)
        if any(not exchange_id.strip() for exchange_id in tracked):
            raise ValueError("tracked exchange IDs must not be blank")

        self._rest = rest
        self._recorder = recorder
        self.tournament_id = tournament_id
        self.topic = f"tournament:{tournament_id}"
        self._configured_tracked_exchange_ids = tracked
        self._book_depth = book_depth
        self._open_book_max_trusted_age = timedelta(
            seconds=open_book_max_trusted_age_seconds
        )
        self._bulk_price_refresh_interval = timedelta(seconds=bulk_price_refresh_seconds)
        self._governed_rate_per_second = governed_rate_per_second
        self._governor_snapshot = governor_snapshot
        self._clock = clock

        self.market_states: dict[str, MarketRuntimeState] = {}
        self.states: dict[str, ExchangeRuntimeState] = {}
        self.health = RuntimeHealth()
        self.last_accepted_revision: int | None = None
        self._last_bulk_price_refresh_at: datetime | None = None

        self._reconcile_registry_lock = asyncio.Lock()
        self._reconcile_tasks: dict[str, asyncio.Task[None]] = {}
        self._reconcile_generation: dict[str, int] = {}
        self._reconcile_priority: dict[str, RestPriority] = {}
        self._reconcile_reason: dict[str, str] = {}
        self._reconcile_revision: dict[str, int | None] = {}
        self._background_tasks: set[asyncio.Task[None]] = set()

    @property
    def tracked_depth_exchange_ids(self) -> frozenset[str]:
        return frozenset(
            exchange_id for exchange_id, state in self.states.items() if state.tracked
        )

    async def initialize(self) -> None:
        """Enumerate all markets, cheaply seed BBO, then seed tracked depth only."""
        self.health.connected = False
        self.last_accepted_revision = None
        await self.refresh_universe(
            reason=SubscriptionReason.INITIAL_SUBSCRIBE.value,
            triggering_revision=None,
            priority=RestPriority.NORMAL,
        )
        self._validate_tracked_configuration()
        await self.refresh_bulk_prices(
            reason=SubscriptionReason.INITIAL_SUBSCRIBE.value,
            priority=RestPriority.BACKGROUND,
        )
        tracked = self.tracked_depth_exchange_ids
        self._mark_untrusted(
            tracked,
            TrustTransition.UNTRUSTED_INITIAL_SUBSCRIBE,
            revision=None,
        )
        await self._reconcile_many(
            tracked,
            reason=SubscriptionReason.INITIAL_SUBSCRIBE.value,
            final_transition=TrustTransition.TRUSTED_AFTER_RECONCILIATION,
            triggering_revision=None,
            priority=RestPriority.NORMAL,
        )

    async def refresh_universe(
        self,
        *,
        reason: str,
        triggering_revision: int | None,
        priority: RestPriority = RestPriority.NORMAL,
    ) -> None:
        async with self._rest.priority(priority):
            async for market in self._rest.iter_markets(
                tournament_id=self.tournament_id
            ):
                observed_at = self._clock()
                self._apply_market_snapshot(
                    market,
                    observed_at=observed_at,
                    reason=reason,
                    triggering_revision=triggering_revision,
                )

    async def prepare_subscription(self, reason: SubscriptionReason) -> None:
        """Invalidate tracked depth, recover authoritative state, then resubscribe."""
        self.health.connected = False
        self.last_accepted_revision = None
        transition = {
            SubscriptionReason.INITIAL_SUBSCRIBE: TrustTransition.UNTRUSTED_INITIAL_SUBSCRIBE,
            SubscriptionReason.RECONNECT: TrustTransition.UNTRUSTED_RECONNECT,
            SubscriptionReason.TOKEN_REFRESH: TrustTransition.UNTRUSTED_TOKEN_REFRESH,
            SubscriptionReason.SOCKET_ERROR: TrustTransition.UNTRUSTED_SOCKET_ERROR,
        }[reason]
        if reason == SubscriptionReason.RECONNECT:
            self.health.reconnect_count += 1

        self._mark_untrusted(
            self.tracked_depth_exchange_ids,
            transition,
            revision=None,
        )
        await self.refresh_universe(
            reason=reason.value,
            triggering_revision=None,
            priority=RestPriority.HIGH,
        )
        self._validate_tracked_configuration()
        await self._reconcile_many(
            self.tracked_depth_exchange_ids,
            reason=reason.value,
            final_transition=TrustTransition.TRUSTED_AFTER_RECONCILIATION,
            triggering_revision=None,
            priority=RestPriority.HIGH,
        )
        await self.refresh_bulk_prices(
            reason=reason.value,
            priority=RestPriority.BACKGROUND,
        )

    def mark_connected(self) -> None:
        self.health.connected = True

    def mark_disconnected(self) -> None:
        self.health.connected = False

    async def maintenance(self, observed_at: datetime) -> None:
        """Schedule staggered tracked refreshes and cheap broad-universe BBO work."""
        await self.refresh_stale_open_books(observed_at, wait=False)
        bulk_refresh_due = (
            self._last_bulk_price_refresh_at is None
            or observed_at - self._last_bulk_price_refresh_at
            >= self._bulk_price_refresh_interval
        )
        bulk_refresh_running = any(
            not task.done() and task.get_name() == "sig-bulk-price-refresh"
            for task in self._background_tasks
        )
        if bulk_refresh_due and not bulk_refresh_running:
            task = asyncio.create_task(
                self.refresh_bulk_prices(
                    reason="periodic_bulk_prices",
                    priority=RestPriority.BACKGROUND,
                ),
                name="sig-bulk-price-refresh",
            )
            self._track_background_task(task)

    async def refresh_stale_open_books(
        self,
        observed_at: datetime,
        *,
        wait: bool = True,
    ) -> None:
        """Expire tracked trust by deadline, then reconcile through governed REST."""
        if observed_at.tzinfo is None or observed_at.utcoffset() is None:
            raise ValueError("maintenance timestamp must be timezone aware")

        due_exchange_ids: list[str] = []
        newly_stale: list[str] = []
        for state in self.states.values():
            if not state.tracked:
                continue
            market_state = self.market_states.get(state.market_id)
            if market_state is None or market_state.status != "open":
                continue

            if state.trusted:
                last_rest = state.last_rest_observed_at
                if (
                    last_rest is None
                    or observed_at - last_rest >= self._open_book_max_trusted_age
                ):
                    due_exchange_ids.append(state.exchange_id)
                    newly_stale.append(state.exchange_id)
            else:
                last_attempt = state.last_reconciliation_attempt_at
                if (
                    last_attempt is None
                    or observed_at - last_attempt >= self._open_book_max_trusted_age
                ):
                    due_exchange_ids.append(state.exchange_id)

        if not due_exchange_ids:
            return

        if newly_stale:
            self.health.bounded_book_refresh_count += len(newly_stale)
            self._mark_untrusted(
                newly_stale,
                TrustTransition.UNTRUSTED_PERIODIC_REFRESH,
                revision=self.last_accepted_revision,
            )

        await self._reconcile_many(
            due_exchange_ids,
            reason="expiry_safety_refresh",
            final_transition=TrustTransition.TRUSTED_AFTER_RECONCILIATION,
            triggering_revision=self.last_accepted_revision,
            priority=RestPriority.NORMAL,
            wait=wait,
        )

    async def refresh_bulk_prices(
        self,
        *,
        reason: str,
        priority: RestPriority = RestPriority.BACKGROUND,
    ) -> None:
        exchange_ids = sorted(self.states)
        if not exchange_ids:
            self._last_bulk_price_refresh_at = self._clock()
            return

        for index in range(0, len(exchange_ids), 100):
            requested = tuple(exchange_ids[index : index + 100])
            async with self._rest.priority(priority):
                response = await self._rest.get_bulk_prices(
                    requested,
                    tournament_id=self.tournament_id,
                )
            observed_at = self._clock()
            self.health.bulk_price_refresh_count += 1
            self._apply_bulk_prices(
                response,
                requested=requested,
                observed_at=observed_at,
                reason=reason,
            )
        self._last_bulk_price_refresh_at = self._clock()

    async def handle_raw_batch(
        self,
        topic: str,
        payload: object,
        observed_at: datetime,
    ) -> None:
        if topic != self.topic:
            raise ValueError("received batch for unexpected topic")
        monotonic_receive_ns = time.monotonic_ns()
        self.health.last_realtime_receive = observed_at

        try:
            batch = MarketBatchDto.model_validate(payload)
            self._validate_batch_tournament(batch)
        except (ValidationError, ValueError) as exc:
            parsed_at = self._clock()
            self._recorder.record_raw_batch(
                topic=topic,
                payload=payload,
                observed_at=observed_at,
                monotonic_receive_ns=monotonic_receive_ns,
                parsed_at=parsed_at,
                validation_error=type(exc).__name__,
            )
            logger.warning("SIG Realtime payload rejected: %s", type(exc).__name__)
            await self._full_resync(
                transition=TrustTransition.UNTRUSTED_MALFORMED_PAYLOAD,
                reason="malformed_payload",
                triggering_revision=None,
                refresh_bulk_prices=True,
            )
            return

        delivery = batch.delivery
        self._recorder.record_raw_batch(
            topic=topic,
            payload=payload,
            observed_at=observed_at,
            monotonic_receive_ns=monotonic_receive_ns,
            parsed_at=self._clock(),
            validation_error=None,
        )
        self._recorder.record_delivery(
            topic=topic, delivery=delivery, observed_at=observed_at
        )
        self.health.last_valid_batch = observed_at

        if self.last_accepted_revision == delivery.revision:
            return

        revision_gap_recovered = False
        if (
            self.last_accepted_revision is not None
            and delivery.previous_revision != self.last_accepted_revision
        ):
            self.health.revision_gap_count += 1
            await self._full_resync(
                transition=TrustTransition.UNTRUSTED_REVISION_GAP,
                reason="revision_gap",
                triggering_revision=delivery.revision,
                refresh_bulk_prices=False,
            )
            revision_gap_recovered = True

        self.last_accepted_revision = delivery.revision
        await self._ensure_known_exchanges(batch)
        self._record_trades(batch, observed_at)

        for book_event in batch.book_dirty:
            self._recorder.record_book_dirty(
                topic=self.topic,
                revision=delivery.revision,
                event=book_event,
                observed_at=observed_at,
            )
        for settlement_event in batch.market_settled:
            self._recorder.record_market_settled(
                topic=self.topic,
                revision=delivery.revision,
                event=settlement_event,
                observed_at=observed_at,
            )

        settled_market_ids = {item.market_id for item in batch.market_settled}
        if settled_market_ids:
            settled_exchange_ids = {
                state.exchange_id
                for state in self.states.values()
                if state.market_id in settled_market_ids
            }
            for exchange_id in settled_exchange_ids:
                state = self.states[exchange_id]
                state.last_realtime_observed_at = observed_at
                state.last_accepted_revision = delivery.revision
            self._mark_untrusted(
                settled_exchange_ids,
                TrustTransition.UNTRUSTED_SETTLEMENT,
                revision=delivery.revision,
            )

            successful_markets: set[str] = set()
            for market_id in sorted(settled_market_ids):
                if await self._reconcile_market(
                    market_id,
                    reason="market_settled",
                    triggering_revision=delivery.revision,
                    priority=RestPriority.HIGH,
                ):
                    successful_markets.add(market_id)

            await self._reconcile_many(
                (
                    exchange_id
                    for exchange_id in settled_exchange_ids
                    if self.states[exchange_id].tracked
                    and self.states[exchange_id].market_id in successful_markets
                ),
                reason="market_settled",
                final_transition=TrustTransition.TRUSTED_AFTER_RECONCILIATION,
                triggering_revision=delivery.revision,
                priority=RestPriority.HIGH,
            )

        dirty_exchange_ids = {
            item.exchange_id
            for item in batch.book_dirty
            if self.states[item.exchange_id].market_id not in settled_market_ids
        }
        for exchange_id in dirty_exchange_ids:
            state = self.states[exchange_id]
            state.last_realtime_observed_at = observed_at
            state.last_accepted_revision = delivery.revision

        tracked_dirty_exchange_ids = {
            exchange_id
            for exchange_id in dirty_exchange_ids
            if self.states[exchange_id].tracked
        }
        if tracked_dirty_exchange_ids:
            self._mark_untrusted(
                tracked_dirty_exchange_ids,
                TrustTransition.UNTRUSTED_BOOK_DIRTY,
                revision=delivery.revision,
            )
            await self._reconcile_many(
                tracked_dirty_exchange_ids,
                reason="book_dirty",
                final_transition=TrustTransition.TRUSTED_AFTER_RECONCILIATION,
                triggering_revision=delivery.revision,
                priority=RestPriority.HIGH,
            )

        if revision_gap_recovered:
            await self.refresh_bulk_prices(
                reason="revision_gap",
                priority=RestPriority.BACKGROUND,
            )

    def health_snapshot(self) -> dict[str, object]:
        tracked_trusted = sum(
            1 for state in self.states.values() if state.depth_state == DepthState.TRACKED_TRUSTED
        )
        tracked_untrusted = sum(
            1
            for state in self.states.values()
            if state.depth_state == DepthState.TRACKED_UNTRUSTED
        )
        untracked = sum(
            1
            for state in self.states.values()
            if state.depth_state == DepthState.UNTRACKED_DEPTH
        )

        oldest_age: float | None = None
        now = self._clock()
        ages = [
            max(0.0, (now - state.last_rest_observed_at).total_seconds())
            for state in self.states.values()
            if state.tracked and state.last_rest_observed_at is not None
        ]
        if ages:
            oldest_age = max(ages)

        governor = self._governor_snapshot() if self._governor_snapshot is not None else None
        return {
            "connected": self.health.connected,
            "last_realtime_receive": self.health.last_realtime_receive,
            "last_valid_batch": self.health.last_valid_batch,
            "last_rest_reconciliation": self.health.last_rest_reconciliation,
            "revision_gap_count": self.health.revision_gap_count,
            "reconnect_count": self.health.reconnect_count,
            "reconciliation_failure_count": self.health.reconciliation_failure_count,
            "bounded_book_refresh_count": self.health.bounded_book_refresh_count,
            "known_exchange_count": len(self.states),
            "tracked_depth_exchange_count": tracked_trusted + tracked_untrusted,
            "tracked_trusted_count": tracked_trusted,
            "tracked_untrusted_count": tracked_untrusted,
            "untracked_depth_exchange_count": untracked,
            "market_count": len(self.market_states),
            "trusted_exchange_count": tracked_trusted,
            "untrusted_exchange_count": tracked_untrusted,
            "bulk_price_refresh_count": self.health.bulk_price_refresh_count,
            "bulk_price_missing_count": self.health.bulk_price_missing_count,
            "full_book_refresh_count": self.health.full_book_refresh_count,
            "oldest_tracked_book_age_seconds": oldest_age,
            "rest_governor_rate": (
                governor.rate_per_second
                if governor is not None
                else self._governed_rate_per_second
            ),
            "rest_requests_total": governor.requests_total if governor is not None else None,
            "rest_429_count": governor.rate_limit_count if governor is not None else None,
            "rest_shared_cooldown_count": (
                governor.shared_cooldown_count if governor is not None else None
            ),
            "pending_high_priority_reads": (
                governor.pending_high_priority_reads if governor is not None else None
            ),
            "pending_background_reads": (
                governor.pending_background_reads if governor is not None else None
            ),
        }

    async def _full_resync(
        self,
        *,
        transition: TrustTransition,
        reason: str,
        triggering_revision: int | None,
        refresh_bulk_prices: bool,
    ) -> None:
        self._mark_untrusted(
            self.tracked_depth_exchange_ids,
            transition,
            revision=triggering_revision,
        )
        await self.refresh_universe(
            reason=reason,
            triggering_revision=triggering_revision,
            priority=RestPriority.HIGH,
        )
        self._validate_tracked_configuration()
        await self._reconcile_many(
            self.tracked_depth_exchange_ids,
            reason=reason,
            final_transition=TrustTransition.TRUSTED_AFTER_RECONCILIATION,
            triggering_revision=triggering_revision,
            priority=RestPriority.HIGH,
        )
        if refresh_bulk_prices:
            await self.refresh_bulk_prices(
                reason=reason,
                priority=RestPriority.BACKGROUND,
            )

    async def _ensure_known_exchanges(self, batch: MarketBatchDto) -> None:
        referenced = {trade.exchange_id for trade in batch.trades} | {
            item.exchange_id for item in batch.book_dirty
        }
        unknown = referenced.difference(self.states)
        if not unknown:
            return

        await self.refresh_universe(
            reason="new_exchange",
            triggering_revision=batch.delivery.revision,
            priority=RestPriority.HIGH,
        )
        still_unknown = unknown.difference(self.states)
        if still_unknown:
            raise ValueError(
                f"Realtime referenced unknown exchanges: {sorted(still_unknown)!r}"
            )

        tracked_unknown = {
            exchange_id
            for exchange_id in unknown
            if self.states[exchange_id].tracked
        }
        await self._reconcile_many(
            tracked_unknown,
            reason="new_exchange",
            final_transition=TrustTransition.TRUSTED_AFTER_RECONCILIATION,
            triggering_revision=batch.delivery.revision,
            priority=RestPriority.HIGH,
        )

    def _validate_batch_tournament(self, batch: MarketBatchDto) -> None:
        for trade in batch.trades:
            if trade.tournament_id != self.tournament_id:
                raise ValueError(
                    "tournament channel trade missing/mismatched tournamentId"
                )
        if any(
            item.tournament_id != self.tournament_id for item in batch.book_dirty
        ):
            raise ValueError("bookDirty tournamentId mismatch")
        if any(
            item.tournament_id != self.tournament_id
            for item in batch.market_settled
        ):
            raise ValueError("marketSettled tournamentId mismatch")

    def _record_trades(self, batch: MarketBatchDto, observed_at: datetime) -> None:
        revision = batch.delivery.revision
        for trade in batch.trades:
            state = self.states[trade.exchange_id]
            state.last_trade = trade
            state.last_realtime_observed_at = observed_at
            state.last_accepted_revision = revision
            self._recorder.record_trade(
                topic=self.topic,
                revision=revision,
                trade=trade,
                observed_at=observed_at,
            )

    def _apply_market_snapshot(
        self,
        market: MarketDto,
        *,
        observed_at: datetime,
        reason: str,
        triggering_revision: int | None,
    ) -> None:
        self.market_states[market.id] = MarketRuntimeState(
            market_id=market.id,
            title=market.title,
            status=market.status,
            settled_with=market.settled_with,
            last_rest_observed_at=observed_at,
        )

        for exchange in market.exchanges:
            current = self.states.get(exchange.id)
            if current is None:
                depth_state = (
                    DepthState.TRACKED_UNTRUSTED
                    if exchange.id in self._configured_tracked_exchange_ids
                    else DepthState.UNTRACKED_DEPTH
                )
                current = ExchangeRuntimeState(
                    exchange_id=exchange.id,
                    market_id=market.id,
                    tournament_id=self.tournament_id,
                    depth_state=depth_state,
                )
                self.states[exchange.id] = current
            elif current.market_id != market.id:
                raise ValueError("SIG exchange changed market identity")

            if market.status != "open":
                current.orderbook = None

        self._recorder.record_market(
            tournament_id=self.tournament_id,
            market=market,
            observed_at=observed_at,
            reason=reason,
            triggering_revision=triggering_revision,
        )
        self.health.last_rest_reconciliation = observed_at

    def _apply_bulk_prices(
        self,
        response: BulkPricesDto,
        *,
        requested: tuple[str, ...],
        observed_at: datetime,
        reason: str,
    ) -> None:
        requested_set = set(requested)
        response_ids = [item.exchange_id for item in response.data]
        missing_ids = list(response.missing_ids)
        if any(exchange_id not in requested_set for exchange_id in response_ids):
            raise ValueError("bulk price response contained an unrequested exchange")
        if any(exchange_id not in requested_set for exchange_id in missing_ids):
            raise ValueError("bulk price response reported an unrequested missing exchange")
        if len(response_ids) != len(set(response_ids)):
            raise ValueError("bulk price response duplicated an exchange")
        if len(missing_ids) != len(set(missing_ids)):
            raise ValueError("bulk price response duplicated a missing exchange")
        expected_ids = [
            exchange_id for exchange_id in requested if exchange_id not in set(missing_ids)
        ]
        if response_ids != expected_ids:
            raise ValueError("bulk price response did not preserve requested exchange order")

        for item in response.data:
            state = self.states.get(item.exchange_id)
            if state is None:
                raise ValueError("bulk price response referenced unknown exchange")
            if state.market_id != item.market_id:
                raise ValueError("bulk price response changed exchange market identity")
            state.latest_price = item.latest_price
            state.scalar_best_bid = item.best_bid
            state.scalar_best_ask = item.best_ask
            state.scalar_spread = item.spread
            state.last_scalar_observed_at = observed_at

        if response.missing_ids:
            self.health.bulk_price_missing_count += len(response.missing_ids)
            missing_exchanges: list[tuple[str, str]] = []
            for exchange_id in response.missing_ids:
                state = self.states[exchange_id]
                state.latest_price = None
                state.scalar_best_bid = None
                state.scalar_best_ask = None
                state.scalar_spread = None
                state.last_scalar_observed_at = None
                missing_exchanges.append((state.exchange_id, state.market_id))
            self._recorder.record_missing_prices(
                tournament_id=self.tournament_id,
                exchanges=tuple(missing_exchanges),
                observed_at=observed_at,
                reason=f"{reason}:missing",
            )
            logger.warning(
                "SIG bulk price response missing exchanges count=%s",
                len(response.missing_ids),
            )

        self._recorder.record_prices(
            tournament_id=self.tournament_id,
            prices=response.data,
            observed_at=observed_at,
            reason=reason,
        )

    async def _reconcile_market(
        self,
        market_id: str,
        *,
        reason: str,
        triggering_revision: int | None,
        priority: RestPriority,
    ) -> bool:
        try:
            async with self._rest.priority(priority):
                market = await self._rest.get_market(
                    market_id,
                    tournament_id=self.tournament_id,
                )
            observed_at = self._clock()
            if market.id != market_id:
                raise ValueError("authoritative market identity mismatch")
            self._apply_market_snapshot(
                market,
                observed_at=observed_at,
                reason=reason,
                triggering_revision=triggering_revision,
            )
        except Exception as exc:
            self.health.reconciliation_failure_count += 1
            logger.warning(
                "SIG REST market reconciliation failed market=%s error=%s",
                market_id,
                type(exc).__name__,
            )
            return False
        return True

    def _mark_untrusted(
        self,
        exchange_ids: Iterable[str],
        transition: TrustTransition,
        *,
        revision: int | None,
    ) -> None:
        observed_at = self._clock()
        for exchange_id in tuple(exchange_ids):
            state = self.states.get(exchange_id)
            if state is None or not state.tracked:
                continue
            state.depth_state = DepthState.TRACKED_UNTRUSTED
            self._recorder.record_transition(
                topic=self.topic,
                exchange_id=exchange_id,
                transition=transition.value,
                observed_at=observed_at,
                revision=revision,
            )

    async def _reconcile_many(
        self,
        exchange_ids: Iterable[str],
        *,
        reason: str,
        final_transition: TrustTransition,
        triggering_revision: int | None,
        priority: RestPriority,
        wait: bool = True,
    ) -> None:
        tasks = [
            self._queue_reconciliation(
                exchange_id,
                reason=reason,
                final_transition=final_transition,
                triggering_revision=triggering_revision,
                priority=priority,
                wait=wait,
            )
            for exchange_id in sorted(set(exchange_ids))
            if exchange_id in self.states and self.states[exchange_id].tracked
        ]
        if tasks:
            await asyncio.gather(*tasks)

    async def _queue_reconciliation(
        self,
        exchange_id: str,
        *,
        reason: str,
        final_transition: TrustTransition,
        triggering_revision: int | None,
        priority: RestPriority,
        wait: bool,
    ) -> None:
        async with self._reconcile_registry_lock:
            generation = self._reconcile_generation.get(exchange_id, 0) + 1
            self._reconcile_generation[exchange_id] = generation

            existing_priority = self._reconcile_priority.get(exchange_id)
            if existing_priority is None or priority < existing_priority:
                self._reconcile_priority[exchange_id] = priority
                self._reconcile_reason[exchange_id] = reason
                self._reconcile_revision[exchange_id] = triggering_revision
            elif existing_priority == priority:
                self._reconcile_reason[exchange_id] = reason
                if triggering_revision is not None:
                    self._reconcile_revision[exchange_id] = triggering_revision

            task = self._reconcile_tasks.get(exchange_id)
            if task is None or task.done():
                task = asyncio.create_task(
                    self._reconcile_loop(
                        exchange_id,
                        final_transition=final_transition,
                    ),
                    name=f"sig-book-reconcile-{exchange_id}",
                )
                self._reconcile_tasks[exchange_id] = task

        if wait:
            await task

    async def _reconcile_loop(
        self,
        exchange_id: str,
        *,
        final_transition: TrustTransition,
    ) -> None:
        while True:
            await asyncio.sleep(0)
            async with self._reconcile_registry_lock:
                generation = self._reconcile_generation[exchange_id]
                priority = self._reconcile_priority[exchange_id]
                reason = self._reconcile_reason[exchange_id]
                triggering_revision = self._reconcile_revision[exchange_id]

            await self._reconcile_exchange_once(
                exchange_id,
                reason=reason,
                final_transition=final_transition,
                triggering_revision=triggering_revision,
                priority=priority,
                generation=generation,
            )

            async with self._reconcile_registry_lock:
                if self._reconcile_generation.get(exchange_id) == generation:
                    self._reconcile_tasks.pop(exchange_id, None)
                    self._reconcile_generation.pop(exchange_id, None)
                    self._reconcile_priority.pop(exchange_id, None)
                    self._reconcile_reason.pop(exchange_id, None)
                    self._reconcile_revision.pop(exchange_id, None)
                    return

    async def _reconcile_exchange_once(
        self,
        exchange_id: str,
        *,
        reason: str,
        final_transition: TrustTransition,
        triggering_revision: int | None,
        priority: RestPriority,
        generation: int,
    ) -> None:
        state = self.states[exchange_id]
        if not state.tracked:
            return

        state.depth_state = DepthState.TRACKED_UNTRUSTED
        transition_at = self._clock()
        state.last_reconciliation_attempt_at = transition_at
        self._recorder.record_transition(
            topic=self.topic,
            exchange_id=exchange_id,
            transition=TrustTransition.RECONCILING.value,
            observed_at=transition_at,
            revision=triggering_revision,
            detail=reason,
        )

        market_state = self.market_states.get(state.market_id)
        if market_state is not None and market_state.status != "open":
            observed_at = market_state.last_rest_observed_at
            state.orderbook = None
            state.last_rest_observed_at = observed_at
            state.last_reconciliation_at = observed_at
            if await self._generation_is_current(exchange_id, generation):
                state.depth_state = DepthState.TRACKED_TRUSTED
                if triggering_revision is not None:
                    state.last_accepted_revision = triggering_revision
                self._recorder.record_transition(
                    topic=self.topic,
                    exchange_id=exchange_id,
                    transition=final_transition.value,
                    observed_at=observed_at,
                    revision=triggering_revision,
                    detail=f"{reason}:market_{market_state.status}",
                )
            return

        try:
            self.health.full_book_refresh_count += 1
            async with self._rest.priority(priority):
                snapshot = await self._rest.get_orderbook(
                    exchange_id,
                    depth=self._book_depth,
                    tournament_id=self.tournament_id,
                )
            observed_at = self._clock()
            if (
                snapshot.exchange_id != exchange_id
                or snapshot.market_id != state.market_id
            ):
                raise ValueError("authoritative orderbook identity mismatch")
            book = snapshot.to_canonical(observed_at=observed_at)
        except Exception as exc:
            self.health.reconciliation_failure_count += 1
            logger.warning(
                "SIG REST reconciliation failed exchange=%s error=%s",
                exchange_id,
                type(exc).__name__,
            )
            return

        state.orderbook = book
        state.last_rest_observed_at = observed_at
        state.last_reconciliation_at = observed_at
        self.health.last_rest_reconciliation = observed_at
        self._recorder.record_book(
            market_id=state.market_id,
            tournament_id=self.tournament_id,
            book=book,
            observed_at=observed_at,
            reason=reason,
            triggering_revision=triggering_revision,
        )

        if not await self._generation_is_current(exchange_id, generation):
            return

        state.depth_state = DepthState.TRACKED_TRUSTED
        if triggering_revision is not None:
            state.last_accepted_revision = triggering_revision
        self._recorder.record_transition(
            topic=self.topic,
            exchange_id=exchange_id,
            transition=final_transition.value,
            observed_at=observed_at,
            revision=triggering_revision,
            detail=reason,
        )

    async def _generation_is_current(
        self,
        exchange_id: str,
        generation: int,
    ) -> bool:
        async with self._reconcile_registry_lock:
            return self._reconcile_generation.get(exchange_id) == generation

    def _validate_tracked_configuration(self) -> None:
        unknown = self._configured_tracked_exchange_ids.difference(self.states)
        if unknown:
            raise ValueError(
                "tracked exchange IDs are absent from authoritative tournament universe: "
                f"{sorted(unknown)!r}"
            )

        periodic_demand = (
            len(self._configured_tracked_exchange_ids)
            / self._open_book_max_trusted_age.total_seconds()
        )
        reserved_background_rate = self._governed_rate_per_second * 0.5
        if periodic_demand > reserved_background_rate:
            raise ValueError(
                "tracked-depth freshness configuration is not sustainable: "
                f"periodic demand {periodic_demand:.3f} rps exceeds "
                f"reserved capacity {reserved_background_rate:.3f} rps"
            )

    async def aclose(self) -> None:
        """Cancel engine-owned maintenance/reconciliation work without deadlock."""
        tasks = {
            task
            for task in (*self._background_tasks, *self._reconcile_tasks.values())
            if not task.done()
        }
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._background_tasks.clear()
        self._reconcile_tasks.clear()

    def _track_background_task(self, task: asyncio.Task[None]) -> None:
        self._background_tasks.add(task)

        def done(completed: asyncio.Task[None]) -> None:
            self._background_tasks.discard(completed)
            try:
                completed.result()
            except asyncio.CancelledError:
                return
            except Exception:
                logger.exception("SIG background maintenance task failed")

        task.add_done_callback(done)
