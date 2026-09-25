"""Authoritative REST reconciliation around best-effort SIG Realtime invalidations."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Protocol

from pydantic import ValidationError

from predictions_cup.models import OrderBook
from predictions_cup.sig.dto import MarketDto, OrderBookSnapshotDto
from predictions_cup.sig.realtime_models import MarketBatchDto, RealtimeTradeDto
from predictions_cup.sig.realtime_storage import SigRealtimeRecorder

logger = logging.getLogger(__name__)
Clock = Callable[[], datetime]


class SubscriptionReason(StrEnum):
    INITIAL_SUBSCRIBE = "initial_subscribe"
    RECONNECT = "reconnect"
    TOKEN_REFRESH = "token_refresh"
    SOCKET_ERROR = "socket_error"


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
    trusted: bool = False
    orderbook: OrderBook | None = None
    last_trade: RealtimeTradeDto | None = None
    last_rest_observed_at: datetime | None = None
    last_realtime_observed_at: datetime | None = None
    last_accepted_revision: int | None = None
    last_reconciliation_at: datetime | None = None

    @property
    def best_bid(self) -> Decimal | None:
        return self.orderbook.bids[0].price if self.orderbook and self.orderbook.bids else None

    @property
    def best_ask(self) -> Decimal | None:
        return self.orderbook.asks[0].price if self.orderbook and self.orderbook.asks else None


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


class SigRealtimeStateEngine:
    """State engine that never treats Realtime depth as authoritative."""

    def __init__(
        self,
        *,
        rest: SigStateRest,
        recorder: SigRealtimeRecorder,
        tournament_id: str,
        book_depth: int = 20,
        open_book_max_trusted_age_seconds: float = 30.0,
        max_reconciliation_concurrency: int = 8,
        clock: Clock = lambda: datetime.now(UTC),
    ) -> None:
        if not tournament_id.strip():
            raise ValueError("tournament_id must not be blank")
        if book_depth < 1 or book_depth > 200:
            raise ValueError("book_depth must be between 1 and 200")
        if open_book_max_trusted_age_seconds <= 0:
            raise ValueError("open_book_max_trusted_age_seconds must be positive")
        if max_reconciliation_concurrency < 1:
            raise ValueError("max_reconciliation_concurrency must be positive")
        self._rest = rest
        self._recorder = recorder
        self.tournament_id = tournament_id
        self.topic = f"tournament:{tournament_id}"
        self._book_depth = book_depth
        self._open_book_max_trusted_age = timedelta(
            seconds=open_book_max_trusted_age_seconds
        )
        self._semaphore = asyncio.Semaphore(max_reconciliation_concurrency)
        self._clock = clock
        self.market_states: dict[str, MarketRuntimeState] = {}
        self.states: dict[str, ExchangeRuntimeState] = {}
        self.health = RuntimeHealth()
        self.last_accepted_revision: int | None = None

    async def initialize(self) -> None:
        """Perform the single authoritative seed required before first subscription."""
        self.health.connected = False
        self.last_accepted_revision = None
        await self.refresh_universe(
            reason=SubscriptionReason.INITIAL_SUBSCRIBE.value,
            triggering_revision=None,
        )
        self._mark_untrusted(
            self.states.keys(),
            TrustTransition.UNTRUSTED_INITIAL_SUBSCRIBE,
            revision=None,
        )
        await self._reconcile_many(
            self.states.keys(),
            reason=SubscriptionReason.INITIAL_SUBSCRIBE.value,
            final_transition=TrustTransition.TRUSTED_AFTER_RECONCILIATION,
            triggering_revision=None,
        )

    async def refresh_universe(
        self,
        *,
        reason: str,
        triggering_revision: int | None,
    ) -> None:
        async for market in self._rest.iter_markets(tournament_id=self.tournament_id):
            observed_at = self._clock()
            self._apply_market_snapshot(
                market,
                observed_at=observed_at,
                reason=reason,
                triggering_revision=triggering_revision,
            )

    async def prepare_subscription(self, reason: SubscriptionReason) -> None:
        """Invalidate, refresh authoritative market state, then reseed exchange books."""
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
        self._mark_untrusted(self.states.keys(), transition, revision=None)
        await self.refresh_universe(reason=reason.value, triggering_revision=None)
        await self._reconcile_many(
            self.states.keys(),
            reason=reason.value,
            final_transition=TrustTransition.TRUSTED_AFTER_RECONCILIATION,
            triggering_revision=None,
        )

    def mark_connected(self) -> None:
        self.health.connected = True

    def mark_disconnected(self) -> None:
        self.health.connected = False

    async def refresh_stale_open_books(self, observed_at: datetime) -> None:
        """Bound trusted open-book age when silent order expiry emits no event."""
        if observed_at.tzinfo is None or observed_at.utcoffset() is None:
            raise ValueError("maintenance timestamp must be timezone aware")

        due_exchange_ids: list[str] = []
        for state in self.states.values():
            market_state = self.market_states.get(state.market_id)
            if (
                not state.trusted
                or market_state is None
                or market_state.status != "open"
            ):
                continue
            last_rest = state.last_rest_observed_at
            if (
                last_rest is None
                or observed_at - last_rest >= self._open_book_max_trusted_age
            ):
                due_exchange_ids.append(state.exchange_id)

        if not due_exchange_ids:
            return

        # Expiry produces no Realtime invalidation. Stop claiming these books are
        # trusted before issuing any potentially rate-limited REST reads.
        self.health.bounded_book_refresh_count += len(due_exchange_ids)
        self._mark_untrusted(
            due_exchange_ids,
            TrustTransition.UNTRUSTED_PERIODIC_REFRESH,
            revision=self.last_accepted_revision,
        )
        await self._reconcile_many(
            due_exchange_ids,
            reason="expiry_safety_refresh",
            final_transition=TrustTransition.TRUSTED_AFTER_RECONCILIATION,
            triggering_revision=self.last_accepted_revision,
        )

    async def handle_raw_batch(
        self,
        topic: str,
        payload: object,
        observed_at: datetime,
    ) -> None:
        if topic != self.topic:
            raise ValueError("received batch for unexpected topic")
        self.health.last_realtime_receive = observed_at
        try:
            batch = MarketBatchDto.model_validate(payload)
            self._validate_batch_tournament(batch)
        except (ValidationError, ValueError) as exc:
            logger.warning("SIG Realtime payload rejected: %s", type(exc).__name__)
            await self._full_resync(
                transition=TrustTransition.UNTRUSTED_MALFORMED_PAYLOAD,
                reason="malformed_payload",
                triggering_revision=None,
            )
            return

        delivery = batch.delivery
        self._recorder.record_delivery(topic=topic, delivery=delivery, observed_at=observed_at)
        self.health.last_valid_batch = observed_at

        if self.last_accepted_revision == delivery.revision:
            return

        if (
            self.last_accepted_revision is not None
            and delivery.previous_revision != self.last_accepted_revision
        ):
            self.health.revision_gap_count += 1
            await self._full_resync(
                transition=TrustTransition.UNTRUSTED_REVISION_GAP,
                reason="revision_gap",
                triggering_revision=delivery.revision,
            )

        # The authoritative subscription seed already ran before the socket joined.
        # The first valid topic revision becomes the delivery baseline.
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
                self.states[exchange_id].last_realtime_observed_at = observed_at
                self.states[exchange_id].last_accepted_revision = delivery.revision
            self._mark_untrusted(
                settled_exchange_ids,
                TrustTransition.UNTRUSTED_SETTLEMENT,
                revision=delivery.revision,
            )
            successful_markets: set[str] = set()
            for market_id in settled_market_ids:
                if await self._reconcile_market(
                    market_id,
                    reason="market_settled",
                    triggering_revision=delivery.revision,
                ):
                    successful_markets.add(market_id)
            await self._reconcile_many(
                (
                    exchange_id
                    for exchange_id in settled_exchange_ids
                    if self.states[exchange_id].market_id in successful_markets
                ),
                reason="market_settled",
                final_transition=TrustTransition.TRUSTED_AFTER_RECONCILIATION,
                triggering_revision=delivery.revision,
            )

        dirty_exchange_ids = {
            item.exchange_id
            for item in batch.book_dirty
            if self.states[item.exchange_id].market_id not in settled_market_ids
        }
        for exchange_id in dirty_exchange_ids:
            self.states[exchange_id].last_realtime_observed_at = observed_at
            self.states[exchange_id].last_accepted_revision = delivery.revision
        if dirty_exchange_ids:
            self._mark_untrusted(
                dirty_exchange_ids,
                TrustTransition.UNTRUSTED_BOOK_DIRTY,
                revision=delivery.revision,
            )
            await self._reconcile_many(
                dirty_exchange_ids,
                reason="book_dirty",
                final_transition=TrustTransition.TRUSTED_AFTER_RECONCILIATION,
                triggering_revision=delivery.revision,
            )

    def health_snapshot(self) -> dict[str, object]:
        trusted = sum(1 for state in self.states.values() if state.trusted)
        return {
            "connected": self.health.connected,
            "last_realtime_receive": self.health.last_realtime_receive,
            "last_valid_batch": self.health.last_valid_batch,
            "last_rest_reconciliation": self.health.last_rest_reconciliation,
            "revision_gap_count": self.health.revision_gap_count,
            "reconnect_count": self.health.reconnect_count,
            "reconciliation_failure_count": self.health.reconciliation_failure_count,
            "bounded_book_refresh_count": self.health.bounded_book_refresh_count,
            "market_count": len(self.market_states),
            "trusted_exchange_count": trusted,
            "untrusted_exchange_count": len(self.states) - trusted,
        }

    async def _full_resync(
        self,
        *,
        transition: TrustTransition,
        reason: str,
        triggering_revision: int | None,
    ) -> None:
        self._mark_untrusted(self.states.keys(), transition, revision=triggering_revision)
        await self.refresh_universe(
            reason=reason,
            triggering_revision=triggering_revision,
        )
        await self._reconcile_many(
            self.states.keys(),
            reason=reason,
            final_transition=TrustTransition.TRUSTED_AFTER_RECONCILIATION,
            triggering_revision=triggering_revision,
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
        )
        still_unknown = unknown.difference(self.states)
        if still_unknown:
            raise ValueError(f"Realtime referenced unknown exchanges: {sorted(still_unknown)!r}")
        await self._reconcile_many(
            unknown,
            reason="new_exchange",
            final_transition=TrustTransition.TRUSTED_AFTER_RECONCILIATION,
            triggering_revision=batch.delivery.revision,
        )

    def _validate_batch_tournament(self, batch: MarketBatchDto) -> None:
        for trade in batch.trades:
            if trade.tournament_id != self.tournament_id:
                raise ValueError("tournament channel trade missing/mismatched tournamentId")
        if any(item.tournament_id != self.tournament_id for item in batch.book_dirty):
            raise ValueError("bookDirty tournamentId mismatch")
        if any(item.tournament_id != self.tournament_id for item in batch.market_settled):
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
                self.states[exchange.id] = ExchangeRuntimeState(
                    exchange_id=exchange.id,
                    market_id=market.id,
                    tournament_id=self.tournament_id,
                )
            elif current.market_id != market.id:
                raise ValueError("SIG exchange changed market identity")
        self._recorder.record_market(
            tournament_id=self.tournament_id,
            market=market,
            observed_at=observed_at,
            reason=reason,
            triggering_revision=triggering_revision,
        )
        self.health.last_rest_reconciliation = observed_at

    async def _reconcile_market(
        self,
        market_id: str,
        *,
        reason: str,
        triggering_revision: int | None,
    ) -> bool:
        try:
            async with self._semaphore:
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
            if state is None:
                continue
            state.trusted = False
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
    ) -> None:
        tasks = [
            self._reconcile_exchange(
                exchange_id,
                reason=reason,
                final_transition=final_transition,
                triggering_revision=triggering_revision,
            )
            for exchange_id in tuple(exchange_ids)
            if exchange_id in self.states
        ]
        if tasks:
            await asyncio.gather(*tasks)

    async def _reconcile_exchange(
        self,
        exchange_id: str,
        *,
        reason: str,
        final_transition: TrustTransition,
        triggering_revision: int | None,
    ) -> None:
        state = self.states[exchange_id]
        state.trusted = False
        transition_at = self._clock()
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
            state.trusted = True
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
            async with self._semaphore:
                snapshot = await self._rest.get_orderbook(
                    exchange_id,
                    depth=self._book_depth,
                    tournament_id=self.tournament_id,
                )
            observed_at = self._clock()
            if snapshot.exchange_id != exchange_id or snapshot.market_id != state.market_id:
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
        state.trusted = True
        if triggering_revision is not None:
            state.last_accepted_revision = triggering_revision
        self.health.last_rest_reconciliation = observed_at
        self._recorder.record_book(
            market_id=state.market_id,
            tournament_id=self.tournament_id,
            book=book,
            observed_at=observed_at,
            reason=reason,
            triggering_revision=triggering_revision,
        )
        self._recorder.record_transition(
            topic=self.topic,
            exchange_id=exchange_id,
            transition=final_transition.value,
            observed_at=observed_at,
            revision=triggering_revision,
            detail=reason,
        )
