"""Bridge existing live in-memory state into explicit MAKE snapshots.

This module performs no network/filesystem/database access. It converts the already
maintained BUILD-004/006 SIG state, BUILD-009 account state and Polymarket
OrderBookStore into bounded per-exchange maker snapshots.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime
from decimal import Decimal
from typing import Protocol

from predictions_cup.external.polymarket.models import BookSnapshot
from predictions_cup.maker.contracts import ExternalQuoteState, MakerMarketSnapshot
from predictions_cup.mapping.models import (
    MappingClass,
    MappingDocument,
    MappingStatus,
    MarketMapping,
)
from predictions_cup.runtime.models import (
    RuntimeBook,
    RuntimeLevel,
    RuntimeMarket,
    RuntimeOrderState,
    RuntimePortfolio,
    RuntimeSnapshot,
    limit_price_to_ticks,
)
from predictions_cup.sig.realtime_state import (
    ExchangeRuntimeState,
    MarketRuntimeState,
    RuntimeHealth,
)


class SigMakerState(Protocol):
    tournament_id: str
    states: dict[str, ExchangeRuntimeState]
    market_states: dict[str, MarketRuntimeState]
    health: RuntimeHealth


class AccountMakerState(Protocol):
    tournament_id: str
    last_accepted_observed_at: datetime | None
    last_authoritative_observed_at: datetime | None

    def runtime_portfolio(self) -> RuntimePortfolio: ...


class PolymarketBookSource(Protocol):
    def snapshot(self, token_id: str, depth: int) -> BookSnapshot | None: ...



class MakerSourceBridge:
    """Indexed bounded bridge from accepted live state into maker snapshots."""

    def __init__(
        self,
        *,
        mapping: MappingDocument,
        sig_state: SigMakerState,
        account_state: AccountMakerState,
        polymarket_books: PolymarketBookSource,
        polymarket_source_version: str = "clob-market-ws-v1",
    ) -> None:
        if mapping.tournament_id != sig_state.tournament_id:
            raise ValueError("mapping/SIG tournament mismatch")
        if mapping.tournament_id != account_state.tournament_id:
            raise ValueError("mapping/account tournament mismatch")
        if not polymarket_source_version.strip():
            raise ValueError("Polymarket source version must not be blank")
        self._mapping = mapping
        self._sig = sig_state
        self._account = account_state
        self._pm_books = polymarket_books
        self._pm_source_version = polymarket_source_version
        self._records = {
            record.sig_exchange_id: record for record in mapping.records
        }
        if len(self._records) != len(mapping.records):
            raise ValueError("mapping contains duplicate SIG exchanges")

        token_to_exchanges: dict[str, set[str]] = defaultdict(set)
        for record in mapping.records:
            for token_id in self._token_ids(record):
                token_to_exchanges[token_id].add(record.sig_exchange_id)
        self._token_to_exchanges = {
            token_id: frozenset(exchange_ids)
            for token_id, exchange_ids in token_to_exchanges.items()
        }
        market_to_exchanges: dict[str, list[str]] = defaultdict(list)
        for record in mapping.records:
            market_to_exchanges[record.sig_market_id].append(record.sig_exchange_id)
        self._market_exchange_ids = {
            market_id: tuple(sorted(exchange_ids))
            for market_id, exchange_ids in market_to_exchanges.items()
        }

    @property
    def tradeable_exchange_ids(self) -> frozenset[str]:
        return frozenset(
            record.sig_exchange_id
            for record in self._mapping.records
            if self._record_tradeable(record)
        )

    def sig_exchanges_for_polymarket_token(self, token_id: str) -> frozenset[str]:
        return self._token_to_exchanges.get(token_id, frozenset())

    def build_many(
        self,
        exchange_ids: frozenset[str] | set[str] | tuple[str, ...],
        *,
        wall_now: datetime,
        monotonic_now_ns: int,
        polymarket_feed_trusted: bool,
        volatility_by_exchange: dict[str, float] | None = None,
    ) -> dict[str, MakerMarketSnapshot]:
        return {
            exchange_id: snapshot
            for exchange_id in sorted(set(exchange_ids))
            if (
                snapshot := self.build(
                    exchange_id,
                    wall_now=wall_now,
                    monotonic_now_ns=monotonic_now_ns,
                    polymarket_feed_trusted=polymarket_feed_trusted,
                    volatility=(
                        None
                        if volatility_by_exchange is None
                        else volatility_by_exchange.get(exchange_id)
                    ),
                )
            )
            is not None
        }

    def build(
        self,
        exchange_id: str,
        *,
        wall_now: datetime,
        monotonic_now_ns: int,
        polymarket_feed_trusted: bool,
        volatility: float | None = None,
    ) -> MakerMarketSnapshot | None:
        if wall_now.tzinfo is None or wall_now.utcoffset() is None:
            raise ValueError("wall_now must be timezone-aware")
        if monotonic_now_ns < 0:
            raise ValueError("monotonic_now_ns must be non-negative")

        record = self._records.get(exchange_id)
        if record is None:
            return None
        sig_exchange = self._sig.states.get(exchange_id)
        if (
            sig_exchange is not None
            and record.sig_market_id != sig_exchange.market_id
        ):
            raise ValueError("mapping/SIG market identity mismatch")

        sig_market = self._sig.market_states.get(record.sig_market_id)
        market_exchange_ids = self._market_exchange_ids[record.sig_market_id]
        runtime_market = RuntimeMarket(
            market_id=record.sig_market_id,
            status="unknown" if sig_market is None else sig_market.status,
            exchange_ids=market_exchange_ids,
            tournament_id=self._mapping.tournament_id,
            mapping_accepted=record.status is MappingStatus.VERIFIED,
            tradeable=self._record_tradeable(record),
        )

        runtime_book: RuntimeBook | None = None
        bbo_observed_ns = 0
        bbo_trusted = False
        depth_observed_ns: int | None = None
        if sig_exchange is not None:
            try:
                runtime_book, bbo_observed_ns, bbo_trusted, depth_observed_ns = (
                    self._runtime_book(
                        sig_exchange,
                        wall_now=wall_now,
                        monotonic_now_ns=monotonic_now_ns,
                    )
                )
            except ValueError:
                runtime_book = None
                bbo_observed_ns = 0
                bbo_trusted = False
                depth_observed_ns = None
        if runtime_book is None:
            # Missing BBO still produces a snapshot. The eligibility policy sees
            # sig_bbo_trusted=False and cancels/fails closed.
            runtime_book = RuntimeBook(
                exchange_id=exchange_id,
                market_id=record.sig_market_id,
                tournament_id=self._mapping.tournament_id,
                bids=(),
                asks=(),
                trusted_depth=False,
                observed_monotonic_ns=0,
            )

        portfolio = self._canonical_portfolio()
        account_observed = (
            self._account.last_accepted_observed_at
            or self._account.last_authoritative_observed_at
        )
        account_observed_ns = self._to_monotonic(
            account_observed,
            wall_now=wall_now,
            monotonic_now_ns=monotonic_now_ns,
        )

        external_quotes: dict[str, ExternalQuoteState] = {}
        for token_id in self._token_ids(record):
            book = self._pm_books.snapshot(token_id, 1)
            if book is None:
                continue
            external_quotes[token_id] = ExternalQuoteState(
                token_id=token_id,
                best_bid=(
                    None if book.best_bid is None else float(book.best_bid)
                ),
                best_ask=(
                    None if book.best_ask is None else float(book.best_ask)
                ),
                observed_monotonic_ns=self._to_monotonic(
                    book.observed_at,
                    wall_now=wall_now,
                    monotonic_now_ns=monotonic_now_ns,
                ),
                trusted=polymarket_feed_trusted,
                source_version=self._pm_source_version,
            )

        runtime = RuntimeSnapshot(
            markets=(runtime_market,),
            books=(runtime_book,),
            portfolio=portfolio,
            observation_monotonic_ns=monotonic_now_ns,
        )
        sig_connected = self._sig.health.connected
        return MakerMarketSnapshot(
            runtime=runtime,
            exchange_id=exchange_id,
            market_id=record.sig_market_id,
            tournament_id=self._mapping.tournament_id,
            now_monotonic_ns=monotonic_now_ns,
            sig_bbo_observed_ns=bbo_observed_ns,
            sig_bbo_trusted=bbo_trusted and sig_connected,
            sig_depth_observed_ns=depth_observed_ns,
            sig_depth_trusted=(
                sig_exchange is not None
                and sig_exchange.trusted
                and sig_connected
            ),
            account_observed_ns=account_observed_ns,
            inventory_observed_ns=account_observed_ns,
            external_quotes=external_quotes,
            volatility=volatility,
        )

    def _canonical_portfolio(self) -> RuntimePortfolio:
        portfolio = self._account.runtime_portfolio()
        orders: list[RuntimeOrderState] = []
        for order in portfolio.orders:
            record = self._records.get(order.exchange_id)
            if (
                record is None
                or order.tournament_id != self._mapping.tournament_id
            ):
                orders.append(order)
                continue
            if order.market_id not in {"UNKNOWN", record.sig_market_id}:
                raise ValueError(
                    "account open-order market identity conflicts with canonical mapping"
                )
            orders.append(
                RuntimeOrderState(
                    logical_intent_id=order.logical_intent_id,
                    exchange_id=order.exchange_id,
                    market_id=record.sig_market_id,
                    tournament_id=order.tournament_id,
                    reserved_exposure=order.reserved_exposure,
                    open=order.open,
                    uncertain=order.uncertain,
                )
            )
        return RuntimePortfolio(
            positions=portfolio.positions,
            orders=tuple(orders),
            account_trusted=portfolio.account_trusted,
        )

    def _runtime_book(
        self,
        state: ExchangeRuntimeState,
        *,
        wall_now: datetime,
        monotonic_now_ns: int,
    ) -> tuple[RuntimeBook | None, int, bool, int | None]:
        exchange_id = state.exchange_id
        market_id = state.market_id
        tournament_id = state.tournament_id
        trusted_depth = state.trusted
        orderbook = state.orderbook
        last_rest = state.last_rest_observed_at
        last_scalar = state.last_scalar_observed_at

        if trusted_depth and orderbook is not None:
            bids = tuple(
                RuntimeLevel(
                    price_ticks=limit_price_to_ticks(level.price),
                    quantity=float(level.quantity),
                )
                for level in orderbook.bids
            )
            asks = tuple(
                RuntimeLevel(
                    price_ticks=limit_price_to_ticks(level.price),
                    quantity=float(level.quantity),
                )
                for level in orderbook.asks
            )
            observed_ns = self._to_monotonic(
                last_rest,
                wall_now=wall_now,
                monotonic_now_ns=monotonic_now_ns,
            )
            return (
                RuntimeBook(
                    exchange_id=exchange_id,
                    market_id=market_id,
                    tournament_id=tournament_id,
                    bids=bids,
                    asks=asks,
                    trusted_depth=True,
                    observed_monotonic_ns=observed_ns,
                ),
                observed_ns,
                self._sig.health.connected and bool(bids) and bool(asks),
                observed_ns,
            )

        best_bid: Decimal | None = state.scalar_best_bid
        best_ask: Decimal | None = state.scalar_best_ask
        observed_ns = self._to_monotonic(
            last_scalar,
            wall_now=wall_now,
            monotonic_now_ns=monotonic_now_ns,
        )
        bids = (
            ()
            if best_bid is None
            else (
                RuntimeLevel(
                    price_ticks=limit_price_to_ticks(best_bid),
                    quantity=0.0,
                ),
            )
        )
        asks = (
            ()
            if best_ask is None
            else (
                RuntimeLevel(
                    price_ticks=limit_price_to_ticks(best_ask),
                    quantity=0.0,
                ),
            )
        )
        return (
            RuntimeBook(
                exchange_id=exchange_id,
                market_id=market_id,
                tournament_id=tournament_id,
                bids=bids,
                asks=asks,
                trusted_depth=False,
                observed_monotonic_ns=observed_ns,
            ),
            observed_ns,
            (
                self._sig.health.connected
                and best_bid is not None
                and best_ask is not None
            ),
            None,
        )

    @staticmethod
    def _record_tradeable(record: MarketMapping) -> bool:
        return (
            record.status is MappingStatus.VERIFIED
            and record.mapping_class
            not in {MappingClass.NO_TRADE, MappingClass.MODEL_ONLY}
        )

    @staticmethod
    def _token_ids(record: MarketMapping) -> tuple[str, ...]:
        if record.direct_polymarket is not None:
            return (record.direct_polymarket.mapped_token_id,)
        return tuple(
            component.mapped_token_id
            for component in record.polymarket_components
        )

    @staticmethod
    def _to_monotonic(
        observed_at: datetime | None,
        *,
        wall_now: datetime,
        monotonic_now_ns: int,
    ) -> int:
        if observed_at is None:
            return 0
        if observed_at.tzinfo is None or observed_at.utcoffset() is None:
            raise ValueError("source observation timestamp must be timezone-aware")
        age_seconds = (
            wall_now.astimezone(UTC) - observed_at.astimezone(UTC)
        ).total_seconds()
        # A future wall-clock observation maps past monotonic_now_ns so the
        # eligibility policy sees a negative age and fails closed. Very old
        # observations clamp to zero and therefore remain stale.
        return max(
            0,
            monotonic_now_ns - int(age_seconds * 1_000_000_000),
        )
