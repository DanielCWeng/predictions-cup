"""Zero-I/O bridge from existing live state engines into MAKE snapshots."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from predictions_cup.external.polymarket.orderbook import OrderBookStore
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
    RuntimeSnapshot,
    limit_price_to_ticks,
)
from predictions_cup.sig.account_state import AccountRealtimeStateEngine
from predictions_cup.sig.realtime_state import DepthState, SigRealtimeStateEngine


class MakerSnapshotAssembler:
    """Build bounded one-market snapshots from existing in-memory live engines.

    No network, database or filesystem work occurs here. The global portfolio is
    retained for central Risk, while market/book tuples contain only the affected
    exchange so quote calculation does not scan the full Cup universe.
    """

    version = "make-001-v1"

    def __init__(
        self,
        *,
        mapping: MappingDocument,
        sig_state: SigRealtimeStateEngine,
        polymarket_books: OrderBookStore,
        account_state: AccountRealtimeStateEngine,
    ) -> None:
        if sig_state.tournament_id != mapping.tournament_id:
            raise ValueError("SIG state and mapping tournament mismatch")
        if account_state.tournament_id != mapping.tournament_id:
            raise ValueError("account state and mapping tournament mismatch")
        self._records = {
            record.sig_exchange_id: record
            for record in mapping.records
        }
        if len(self._records) != len(mapping.records):
            raise ValueError("mapping contains duplicate SIG exchange IDs")
        self._sig = sig_state
        self._pm = polymarket_books
        self._account = account_state

    @property
    def exchange_ids(self) -> frozenset[str]:
        return frozenset(self._records)

    def snapshot_for(
        self,
        exchange_id: str,
        *,
        now_monotonic_ns: int,
        now_utc: datetime,
        polymarket_connected: bool,
        volatility: float | None = None,
    ) -> MakerMarketSnapshot:
        if now_monotonic_ns < 0:
            raise ValueError("now_monotonic_ns must be non-negative")
        if now_utc.tzinfo is None or now_utc.utcoffset() is None:
            raise ValueError("now_utc must be timezone aware")
        now_utc = now_utc.astimezone(UTC)

        record = self._records.get(exchange_id)
        if record is None:
            raise KeyError(f"unknown maker exchange: {exchange_id}")
        sig_exchange = self._sig.states.get(exchange_id)
        sig_market = self._sig.market_states.get(record.sig_market_id)

        market_status = "unknown" if sig_market is None else sig_market.status
        mapping_accepted = (
            record.status is MappingStatus.VERIFIED
            and record.mapping_class
            not in {MappingClass.NO_TRADE, MappingClass.MODEL_ONLY}
        )
        market = RuntimeMarket(
            market_id=record.sig_market_id,
            status=market_status,
            exchange_ids=(exchange_id,),
            tournament_id=record.sig_tournament_id,
            mapping_accepted=mapping_accepted,
            tradeable=mapping_accepted and market_status == "open",
        )

        book, bbo_observed_ns, bbo_trusted, depth_observed_ns, depth_trusted = (
            self._sig_book(
                record=record,
                now_monotonic_ns=now_monotonic_ns,
                now_utc=now_utc,
            )
        )
        portfolio = self._account.runtime_portfolio()
        runtime = RuntimeSnapshot(
            markets=(market,),
            books=() if book is None else (book,),
            portfolio=portfolio,
            observation_monotonic_ns=now_monotonic_ns,
        )

        account_observed_at = (
            self._account.last_accepted_observed_at
            or self._account.last_authoritative_observed_at
        )
        account_observed_ns = self._monotonic_observed_ns(
            account_observed_at,
            now_monotonic_ns=now_monotonic_ns,
            now_utc=now_utc,
        )

        external_quotes = self._external_quotes(
            record,
            now_monotonic_ns=now_monotonic_ns,
            now_utc=now_utc,
            connected=polymarket_connected,
        )
        return MakerMarketSnapshot(
            runtime=runtime,
            exchange_id=record.sig_exchange_id,
            market_id=record.sig_market_id,
            tournament_id=record.sig_tournament_id,
            now_monotonic_ns=now_monotonic_ns,
            sig_bbo_observed_ns=bbo_observed_ns,
            sig_bbo_trusted=bbo_trusted,
            sig_depth_observed_ns=depth_observed_ns,
            sig_depth_trusted=depth_trusted,
            account_observed_ns=account_observed_ns,
            inventory_observed_ns=account_observed_ns,
            external_quotes=external_quotes,
            volatility=volatility,
        )

    def _sig_book(
        self,
        *,
        record: MarketMapping,
        now_monotonic_ns: int,
        now_utc: datetime,
    ) -> tuple[RuntimeBook | None, int, bool, int | None, bool]:
        state = self._sig.states.get(record.sig_exchange_id)
        if state is None:
            return None, 0, False, None, False
        if state.market_id != record.sig_market_id:
            return None, 0, False, None, False

        depth_trusted = (
            state.depth_state is DepthState.TRACKED_TRUSTED
            and state.orderbook is not None
        )
        bbo_observed_at = (
            state.last_rest_observed_at
            if depth_trusted
            else state.last_scalar_observed_at
        )
        bbo_observed_ns = self._monotonic_observed_ns(
            bbo_observed_at,
            now_monotonic_ns=now_monotonic_ns,
            now_utc=now_utc,
        )
        depth_observed_ns = (
            self._monotonic_observed_ns(
                state.last_rest_observed_at,
                now_monotonic_ns=now_monotonic_ns,
                now_utc=now_utc,
            )
            if depth_trusted
            else None
        )

        try:
            if depth_trusted:
                assert state.orderbook is not None
                bids = tuple(
                    RuntimeLevel(
                        price_ticks=limit_price_to_ticks(level.price),
                        quantity=float(level.quantity),
                    )
                    for level in state.orderbook.bids
                )
                asks = tuple(
                    RuntimeLevel(
                        price_ticks=limit_price_to_ticks(level.price),
                        quantity=float(level.quantity),
                    )
                    for level in state.orderbook.asks
                )
            else:
                bids = self._scalar_level(state.best_bid)
                asks = self._scalar_level(state.best_ask)
        except ValueError:
            return None, bbo_observed_ns, False, depth_observed_ns, False

        bbo_trusted = (
            self._sig.health.connected
            and bool(bids)
            and bool(asks)
            and bbo_observed_at is not None
        )
        return (
            RuntimeBook(
                exchange_id=record.sig_exchange_id,
                market_id=record.sig_market_id,
                tournament_id=record.sig_tournament_id,
                bids=bids,
                asks=asks,
                trusted_depth=depth_trusted,
                observed_monotonic_ns=(
                    depth_observed_ns
                    if depth_observed_ns is not None
                    else bbo_observed_ns
                ),
            ),
            bbo_observed_ns,
            bbo_trusted,
            depth_observed_ns,
            depth_trusted,
        )

    @staticmethod
    def _scalar_level(price: Decimal | None) -> tuple[RuntimeLevel, ...]:
        if price is None:
            return ()
        return (
            RuntimeLevel(
                price_ticks=limit_price_to_ticks(price),
                # Scalar BBO does not carry trusted size. Zero prevents SHADOW
                # from inventing executable depth while still exposing price.
                quantity=0.0,
            ),
        )

    def _external_quotes(
        self,
        record: MarketMapping,
        *,
        now_monotonic_ns: int,
        now_utc: datetime,
        connected: bool,
    ) -> dict[str, ExternalQuoteState]:
        identities = (
            (record.direct_polymarket,)
            if record.direct_polymarket is not None
            else record.polymarket_components
        )
        result: dict[str, ExternalQuoteState] = {}
        for identity in identities:
            if identity is None:
                continue
            token_id = identity.mapped_token_id
            snapshot = self._pm.snapshot(token_id, depth=1)
            if snapshot is None:
                continue
            observed_ns = self._monotonic_observed_ns(
                snapshot.observed_at,
                now_monotonic_ns=now_monotonic_ns,
                now_utc=now_utc,
            )
            result[token_id] = ExternalQuoteState(
                token_id=token_id,
                best_bid=(
                    None
                    if snapshot.best_bid is None
                    else float(snapshot.best_bid)
                ),
                best_ask=(
                    None
                    if snapshot.best_ask is None
                    else float(snapshot.best_ask)
                ),
                observed_monotonic_ns=observed_ns,
                trusted=connected,
                source_version="polymarket-orderbook-store-v1",
            )
        return result

    @staticmethod
    def _monotonic_observed_ns(
        observed_at: datetime | None,
        *,
        now_monotonic_ns: int,
        now_utc: datetime,
    ) -> int:
        if observed_at is None:
            return 0
        if observed_at.tzinfo is None or observed_at.utcoffset() is None:
            return 0
        age_seconds = (now_utc - observed_at.astimezone(UTC)).total_seconds()
        # Future wall-clock observations are intentionally represented as
        # future monotonic observations. Eligibility sees negative age and fails.
        return now_monotonic_ns - int(age_seconds * 1_000_000_000)
