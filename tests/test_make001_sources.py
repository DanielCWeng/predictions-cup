from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from predictions_cup.external.polymarket.models import BookLevel, BookSnapshot
from predictions_cup.maker.service import _mapped_token_ids
from predictions_cup.maker.sources import MakerSourceBridge
from predictions_cup.mapping.models import (
    MappingClass,
    MappingDirection,
    MappingDocument,
    MappingStatus,
    MarketMapping,
    PolymarketContractIdentity,
)
from predictions_cup.models import OrderBook, OrderBookLevel
from predictions_cup.runtime.models import RuntimeOrderState, RuntimePortfolio, RuntimePosition
from predictions_cup.sig.realtime_state import (
    DepthState,
    ExchangeRuntimeState,
    MarketRuntimeState,
    RuntimeHealth,
)


@dataclass
class _SigState:
    tournament_id: str
    states: dict[str, ExchangeRuntimeState]
    market_states: dict[str, MarketRuntimeState]
    health: RuntimeHealth


@dataclass
class _AccountState:
    tournament_id: str
    last_accepted_observed_at: datetime | None
    portfolio: RuntimePortfolio
    last_authoritative_observed_at: datetime | None = None

    def runtime_portfolio(self) -> RuntimePortfolio:
        return self.portfolio


class _PmBooks:
    def __init__(self, snapshots: dict[str, BookSnapshot]) -> None:
        self._snapshots = snapshots

    def snapshot(self, token_id: str, depth: int) -> BookSnapshot | None:
        assert depth == 1
        return self._snapshots.get(token_id)


def _mapping() -> MappingDocument:
    direct = PolymarketContractIdentity(
        market_id="pm1",
        condition_id="cid1",
        event_id="event1",
        question="fixture",
        outcomes=("Yes", "No"),
        token_ids=("yes-token", "no-token"),
        mapped_outcome="Yes",
        mapped_token_id="yes-token",
    )
    record = MarketMapping(
        sig_tournament_id="t1",
        sig_market_id="m1",
        sig_market_title="fixture market",
        sig_exchange_id="36",
        sig_outcome_label="YES",
        mapping_class=MappingClass.EXACT,
        mapping_direction=MappingDirection.SAME,
        mapping_confidence=Decimal("1.0"),
        status=MappingStatus.VERIFIED,
        direct_polymarket=direct,
    )
    return MappingDocument(tournament_id="t1", records=(record,))


def _pm_snapshot(observed_at: datetime) -> BookSnapshot:
    return BookSnapshot(
        market_id="pm1",
        token_id="yes-token",
        source_timestamp=observed_at,
        observed_at=observed_at,
        bids=(BookLevel(price=Decimal("0.49"), size=Decimal("50")),),
        asks=(BookLevel(price=Decimal("0.51"), size=Decimal("40")),),
    )


def test_bridge_builds_bounded_scalar_bbo_snapshot_with_separate_freshness() -> None:
    wall_now = datetime(2026, 9, 29, 14, 0, tzinfo=UTC)
    sig_observed = wall_now - timedelta(milliseconds=20)
    account_observed = wall_now - timedelta(milliseconds=30)
    pm_observed = wall_now - timedelta(milliseconds=10)
    sig = _SigState(
        tournament_id="t1",
        states={
            "36": ExchangeRuntimeState(
                exchange_id="36",
                market_id="m1",
                tournament_id="t1",
                depth_state=DepthState.UNTRACKED_DEPTH,
                scalar_best_bid=Decimal("0.49"),
                scalar_best_ask=Decimal("0.51"),
                last_scalar_observed_at=sig_observed,
            )
        },
        market_states={
            "m1": MarketRuntimeState(
                market_id="m1",
                title="fixture",
                status="open",
                settled_with=None,
                last_rest_observed_at=sig_observed,
            )
        },
        health=RuntimeHealth(connected=True),
    )
    account = _AccountState(
        tournament_id="t1",
        last_accepted_observed_at=account_observed,
        portfolio=RuntimePortfolio(
            positions=(
                RuntimePosition(
                    exchange_id="36",
                    market_id="m1",
                    tournament_id="t1",
                    gross_exposure=3.0,
                    signed_quantity=-3.0,
                ),
            ),
            account_trusted=True,
        ),
    )
    bridge = MakerSourceBridge(
        mapping=_mapping(),
        sig_state=sig,
        account_state=account,
        polymarket_books=_PmBooks({"yes-token": _pm_snapshot(pm_observed)}),
    )

    snapshot = bridge.build(
        "36",
        wall_now=wall_now,
        monotonic_now_ns=1_000_000_000,
        polymarket_feed_trusted=True,
    )

    assert snapshot is not None
    assert snapshot.sig_bbo_trusted is True
    assert snapshot.sig_depth_trusted is False
    assert snapshot.sig_bbo_observed_ns == 980_000_000
    assert snapshot.account_observed_ns == 970_000_000
    assert snapshot.inventory_observed_ns == 970_000_000
    book = snapshot.runtime.book("36")
    assert book is not None
    assert book.trusted_depth is False
    assert book.bids[0].price_ticks == 98
    assert book.asks[0].price_ticks == 102
    assert snapshot.external_quotes["yes-token"].observed_monotonic_ns == 990_000_000
    assert snapshot.runtime.portfolio.positions[0].signed_quantity == -3.0


def test_bridge_preserves_trusted_sig_depth_and_quantity() -> None:
    wall_now = datetime(2026, 9, 29, 14, 0, tzinfo=UTC)
    observed = wall_now - timedelta(milliseconds=5)
    orderbook = OrderBook(
        exchange_id="36",
        bids=(
            OrderBookLevel(price=Decimal("0.49"), quantity=Decimal("7")),
            OrderBookLevel(price=Decimal("0.485"), quantity=Decimal("4")),
        ),
        asks=(
            OrderBookLevel(price=Decimal("0.51"), quantity=Decimal("8")),
        ),
        timestamp=observed,
        source="sig-rest",
    )
    sig = _SigState(
        tournament_id="t1",
        states={
            "36": ExchangeRuntimeState(
                exchange_id="36",
                market_id="m1",
                tournament_id="t1",
                depth_state=DepthState.TRACKED_TRUSTED,
                orderbook=orderbook,
                last_rest_observed_at=observed,
            )
        },
        market_states={
            "m1": MarketRuntimeState(
                market_id="m1",
                title="fixture",
                status="open",
                settled_with=None,
                last_rest_observed_at=observed,
            )
        },
        health=RuntimeHealth(connected=True),
    )
    account = _AccountState(
        tournament_id="t1",
        last_accepted_observed_at=observed,
        portfolio=RuntimePortfolio(account_trusted=True),
    )
    bridge = MakerSourceBridge(
        mapping=_mapping(),
        sig_state=sig,
        account_state=account,
        polymarket_books=_PmBooks({"yes-token": _pm_snapshot(observed)}),
    )

    snapshot = bridge.build(
        "36",
        wall_now=wall_now,
        monotonic_now_ns=2_000_000_000,
        polymarket_feed_trusted=True,
    )

    assert snapshot is not None
    assert snapshot.sig_depth_trusted is True
    assert snapshot.sig_depth_observed_ns == 1_995_000_000
    book = snapshot.runtime.book("36")
    assert book is not None
    assert book.trusted_depth
    assert tuple((level.price_ticks, level.quantity) for level in book.bids) == (
        (98, 7.0),
        (97, 4.0),
    )
    assert tuple((level.price_ticks, level.quantity) for level in book.asks) == (
        (102, 8.0),
    )


def test_bridge_indexes_polymarket_token_to_affected_sig_exchange() -> None:
    wall_now = datetime(2026, 9, 29, 14, 0, tzinfo=UTC)
    state = ExchangeRuntimeState(
        exchange_id="36",
        market_id="m1",
        tournament_id="t1",
        scalar_best_bid=Decimal("0.49"),
        scalar_best_ask=Decimal("0.51"),
        last_scalar_observed_at=wall_now,
    )
    bridge = MakerSourceBridge(
        mapping=_mapping(),
        sig_state=_SigState(
            tournament_id="t1",
            states={"36": state},
            market_states={
                "m1": MarketRuntimeState(
                    market_id="m1",
                    title="fixture",
                    status="open",
                    settled_with=None,
                    last_rest_observed_at=wall_now,
                )
            },
            health=RuntimeHealth(connected=True),
        ),
        account_state=_AccountState(
            tournament_id="t1",
            last_accepted_observed_at=wall_now,
            portfolio=RuntimePortfolio(account_trusted=True),
        ),
        polymarket_books=_PmBooks({"yes-token": _pm_snapshot(wall_now)}),
    )

    assert bridge.tradeable_exchange_ids == frozenset({"36"})
    assert bridge.sig_exchanges_for_polymarket_token("yes-token") == frozenset({"36"})
    assert bridge.sig_exchanges_for_polymarket_token("unknown") == frozenset()


def test_bridge_marks_external_quotes_untrusted_when_pm_feed_is_down() -> None:
    wall_now = datetime(2026, 9, 29, 14, 0, tzinfo=UTC)
    sig = _SigState(
        tournament_id="t1",
        states={
            "36": ExchangeRuntimeState(
                exchange_id="36",
                market_id="m1",
                tournament_id="t1",
                scalar_best_bid=Decimal("0.49"),
                scalar_best_ask=Decimal("0.51"),
                last_scalar_observed_at=wall_now,
            )
        },
        market_states={
            "m1": MarketRuntimeState(
                market_id="m1",
                title="fixture",
                status="open",
                settled_with=None,
                last_rest_observed_at=wall_now,
            )
        },
        health=RuntimeHealth(connected=True),
    )
    bridge = MakerSourceBridge(
        mapping=_mapping(),
        sig_state=sig,
        account_state=_AccountState(
            tournament_id="t1",
            last_accepted_observed_at=wall_now,
            portfolio=RuntimePortfolio(account_trusted=True),
        ),
        polymarket_books=_PmBooks({"yes-token": _pm_snapshot(wall_now)}),
    )

    snapshot = bridge.build(
        "36",
        wall_now=wall_now,
        monotonic_now_ns=1_000,
        polymarket_feed_trusted=False,
    )

    assert snapshot is not None
    assert snapshot.external_quotes["yes-token"].trusted is False

def test_bridge_keeps_cancellable_snapshot_when_sig_exchange_state_disappears() -> None:
    wall_now = datetime(2026, 9, 29, 14, 0, tzinfo=UTC)
    bridge = MakerSourceBridge(
        mapping=_mapping(),
        sig_state=_SigState(
            tournament_id="t1",
            states={},
            market_states={},
            health=RuntimeHealth(connected=True),
        ),
        account_state=_AccountState(
            tournament_id="t1",
            last_accepted_observed_at=wall_now,
            portfolio=RuntimePortfolio(account_trusted=True),
        ),
        polymarket_books=_PmBooks({"yes-token": _pm_snapshot(wall_now)}),
    )

    snapshot = bridge.build(
        "36",
        wall_now=wall_now,
        monotonic_now_ns=1_000_000,
        polymarket_feed_trusted=True,
    )

    assert snapshot is not None
    assert snapshot.runtime.markets[0].status == "unknown"
    assert snapshot.sig_bbo_trusted is False
    assert snapshot.sig_depth_trusted is False
    book = snapshot.runtime.book("36")
    assert book is not None
    assert book.bids == ()
    assert book.asks == ()


def test_bridge_revokes_sig_bbo_and_depth_trust_when_realtime_disconnects() -> None:
    wall_now = datetime(2026, 9, 29, 14, 0, tzinfo=UTC)
    observed = wall_now
    sig = _SigState(
        tournament_id="t1",
        states={
            "36": ExchangeRuntimeState(
                exchange_id="36",
                market_id="m1",
                tournament_id="t1",
                depth_state=DepthState.TRACKED_TRUSTED,
                orderbook=OrderBook(
                    exchange_id="36",
                    bids=(OrderBookLevel(price=Decimal("0.49"), quantity=Decimal("5")),),
                    asks=(OrderBookLevel(price=Decimal("0.51"), quantity=Decimal("5")),),
                    timestamp=observed,
                    source="sig-rest",
                ),
                last_rest_observed_at=observed,
            )
        },
        market_states={
            "m1": MarketRuntimeState(
                market_id="m1",
                title="fixture",
                status="open",
                settled_with=None,
                last_rest_observed_at=observed,
            )
        },
        health=RuntimeHealth(connected=False),
    )
    bridge = MakerSourceBridge(
        mapping=_mapping(),
        sig_state=sig,
        account_state=_AccountState(
            tournament_id="t1",
            last_accepted_observed_at=observed,
            portfolio=RuntimePortfolio(account_trusted=True),
        ),
        polymarket_books=_PmBooks({"yes-token": _pm_snapshot(observed)}),
    )

    snapshot = bridge.build(
        "36",
        wall_now=wall_now,
        monotonic_now_ns=1_000,
        polymarket_feed_trusted=True,
    )

    assert snapshot is not None
    assert snapshot.sig_bbo_trusted is False
    assert snapshot.sig_depth_trusted is False

def test_bridge_restores_canonical_market_identity_for_open_order_risk() -> None:
    wall_now = datetime(2026, 9, 29, 14, 0, tzinfo=UTC)
    sig = _SigState(
        tournament_id="t1",
        states={
            "36": ExchangeRuntimeState(
                exchange_id="36",
                market_id="m1",
                tournament_id="t1",
                scalar_best_bid=Decimal("0.49"),
                scalar_best_ask=Decimal("0.51"),
                last_scalar_observed_at=wall_now,
            )
        },
        market_states={
            "m1": MarketRuntimeState(
                market_id="m1",
                title="fixture",
                status="open",
                settled_with=None,
                last_rest_observed_at=wall_now,
            )
        },
        health=RuntimeHealth(connected=True),
    )
    account = _AccountState(
        tournament_id="t1",
        last_accepted_observed_at=wall_now,
        portfolio=RuntimePortfolio(
            orders=(
                RuntimeOrderState(
                    logical_intent_id="sig-order-91",
                    exchange_id="36",
                    market_id="UNKNOWN",
                    tournament_id="t1",
                    reserved_exposure=4.0,
                    open=True,
                    uncertain=False,
                ),
            ),
            account_trusted=True,
        ),
    )
    bridge = MakerSourceBridge(
        mapping=_mapping(),
        sig_state=sig,
        account_state=account,
        polymarket_books=_PmBooks({"yes-token": _pm_snapshot(wall_now)}),
    )

    snapshot = bridge.build(
        "36",
        wall_now=wall_now,
        monotonic_now_ns=1_000_000,
        polymarket_feed_trusted=True,
    )

    assert snapshot is not None
    assert len(snapshot.runtime.portfolio.orders) == 1
    order = snapshot.runtime.portfolio.orders[0]
    assert order.exchange_id == "36"
    assert order.market_id == "m1"
    assert order.reserved_exposure == 4.0

def test_trusted_live_sources_use_effective_current_freshness_when_quiet() -> None:
    wall_now = datetime(2026, 9, 29, 14, 1, tzinfo=UTC)
    old = wall_now - timedelta(minutes=5)
    sig = _SigState(
        tournament_id="t1",
        states={
            "36": ExchangeRuntimeState(
                exchange_id="36",
                market_id="m1",
                tournament_id="t1",
                scalar_best_bid=Decimal("0.49"),
                scalar_best_ask=Decimal("0.51"),
                last_scalar_observed_at=wall_now - timedelta(seconds=1),
            )
        },
        market_states={
            "m1": MarketRuntimeState(
                market_id="m1",
                title="fixture",
                status="open",
                settled_with=None,
                last_rest_observed_at=wall_now - timedelta(seconds=1),
            )
        },
        health=RuntimeHealth(connected=True),
    )
    bridge = MakerSourceBridge(
        mapping=_mapping(),
        sig_state=sig,
        account_state=_AccountState(
            tournament_id="t1",
            last_accepted_observed_at=old,
            last_authoritative_observed_at=old,
            portfolio=RuntimePortfolio(account_trusted=True),
        ),
        polymarket_books=_PmBooks({"yes-token": _pm_snapshot(old)}),
    )

    snapshot = bridge.build(
        "36",
        wall_now=wall_now,
        monotonic_now_ns=600_000_000_000,
        polymarket_feed_trusted=True,
    )

    assert snapshot is not None
    # Account trust is restored only after a subscribed socket plus authoritative
    # reconciliation; a quiet trusted channel therefore keeps inventory current.
    assert snapshot.account_observed_ns == 600_000_000_000
    assert snapshot.inventory_observed_ns == 600_000_000_000
    # Polymarket reconnect performs a fresh CLOB seed and websocket liveness is
    # monitored separately, so no economic mutation is required to keep FV fresh.
    assert (
        snapshot.external_quotes["yes-token"].observed_monotonic_ns
        == 600_000_000_000
    )
    # SIG scalar BBO still preserves its actual periodic REST refresh age.
    assert snapshot.sig_bbo_observed_ns == 599_000_000_000
    assert snapshot.runtime.portfolio.account_trusted is True
    assert snapshot.external_quotes["yes-token"].trusted is True

def test_polymarket_service_seeds_all_outcome_tokens_not_only_aligned_fv_token() -> None:
    tokens = _mapped_token_ids(_mapping())
    assert tokens == ("no-token", "yes-token")

