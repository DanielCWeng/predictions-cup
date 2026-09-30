from __future__ import annotations

import asyncio
import math
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from typing import cast

import pytest

from predictions_cup.execution.journal import ExecutionJournal
from predictions_cup.execution.live import SigLiveSink
from predictions_cup.execution.models import ExecutionEvent, ExecutionMode, LifecycleState
from predictions_cup.execution.reservations import ExecutionReservationBook
from predictions_cup.execution.sinks import ExecutionPlan
from predictions_cup.maker import (
    ActiveQuote,
    BinaryCaraInventoryModel,
    ConservativeEligibilityPolicy,
    ConservativeSpreadPolicy,
    DirectPolymarketFairValueProvider,
    ExternalQuoteState,
    GateMode,
    InventoryConfidenceSizePolicy,
    LiveMakerExecutionAdapter,
    MakerConfig,
    MakerCoordinator,
    MakerEngine,
    MakerMarketSnapshot,
    MakerStateChange,
    NullPredictiveAdjuster,
    NullToxicityProvider,
    QuoteLifecycleActionKind,
    QuoteLifecycleConfig,
    QuoteLifecycleManager,
    QuoteRegistry,
    QuoteSide,
    ShadowMakerExecutionAdapter,
)
from predictions_cup.maker.contracts import FairValueResult
from predictions_cup.maker.runtime_loop import MakerRuntimeLoop
from predictions_cup.maker.sources import MakerSourceBridge
from predictions_cup.mapping.models import (
    MappingClass,
    MappingDirection,
    MappingDocument,
    MappingStatus,
    MarketMapping,
    PolymarketContractIdentity,
)
from predictions_cup.observe import (
    BoundedObservationEmitter,
    InMemoryObservationSink,
    ObservationKind,
)
from predictions_cup.risk.core import RiskContext, RiskLimits
from predictions_cup.runtime.models import (
    RuntimeBook,
    RuntimeLevel,
    RuntimeMarket,
    RuntimePortfolio,
    RuntimePosition,
    RuntimeSnapshot,
)

NOW = 1_000_000_000
TOURNAMENT = "t1"


def _identity(token_id: str, *, market_id: str = "pm-1") -> PolymarketContractIdentity:
    return PolymarketContractIdentity(
        market_id=market_id,
        condition_id=f"condition-{market_id}",
        event_id="event-1",
        question=f"question {market_id}",
        outcomes=("Yes", "No"),
        token_ids=(token_id, f"{token_id}-no"),
        mapped_outcome="Yes",
        mapped_token_id=token_id,
    )


def _mapping(
    *,
    mapping_class: MappingClass = MappingClass.EXACT,
    direction: MappingDirection = MappingDirection.SAME,
) -> MappingDocument:
    direct = (
        _identity("token-yes")
        if mapping_class in {MappingClass.EXACT, MappingClass.NEAR}
        else None
    )
    components: tuple[PolymarketContractIdentity, ...] = ()
    semantic_notes: str | None = "reviewed direct mapping"
    resolution_notes: str | None = "same outcome"
    if mapping_class is MappingClass.DERIVED:
        direct = None
        direction = MappingDirection.DERIVED
        components = (
            _identity("bucket-a", market_id="bucket-a-market"),
            _identity("bucket-b", market_id="bucket-b-market"),
        )
        semantic_notes = "mutually exclusive final-election margin partition"
        resolution_notes = "party-win probability is union/sum of all mapped buckets"
    elif mapping_class in {MappingClass.NO_TRADE, MappingClass.MODEL_ONLY}:
        direct = None
        direction = None  # type: ignore[assignment]
        semantic_notes = "fail closed"
        resolution_notes = "no direct source"

    record = MarketMapping(
        sig_tournament_id=TOURNAMENT,
        sig_market_id="m1",
        sig_market_title="fixture",
        sig_exchange_id="36",
        sig_outcome_label="YES",
        mapping_class=mapping_class,
        mapping_direction=direction,
        mapping_confidence=Decimal("0.99"),
        status=MappingStatus.VERIFIED,
        direct_polymarket=direct,
        polymarket_components=components,
        semantic_notes=semantic_notes,
        resolution_notes=resolution_notes,
    )
    return MappingDocument(tournament_id=TOURNAMENT, records=(record,))


def _runtime(
    *,
    signed_inventory: float = 0.0,
    account_trusted: bool = True,
    status: str = "open",
) -> RuntimeSnapshot:
    positions: tuple[RuntimePosition, ...] = ()
    if signed_inventory != 0.0:
        positions = (
            RuntimePosition(
                exchange_id="36",
                market_id="m1",
                tournament_id=TOURNAMENT,
                gross_exposure=abs(signed_inventory),
                signed_quantity=signed_inventory,
            ),
        )
    return RuntimeSnapshot(
        markets=(
            RuntimeMarket(
                market_id="m1",
                status=status,
                exchange_ids=("36",),
                tournament_id=TOURNAMENT,
                mapping_accepted=True,
                tradeable=True,
            ),
        ),
        books=(
            RuntimeBook(
                exchange_id="36",
                market_id="m1",
                tournament_id=TOURNAMENT,
                bids=(RuntimeLevel(price_ticks=98, quantity=20.0),),
                asks=(RuntimeLevel(price_ticks=102, quantity=20.0),),
                trusted_depth=True,
                observed_monotonic_ns=NOW,
            ),
        ),
        portfolio=RuntimePortfolio(
            positions=positions,
            account_trusted=account_trusted,
        ),
        observation_monotonic_ns=NOW,
    )


def _external(
    *,
    bid: float = 0.49,
    ask: float = 0.51,
    token_id: str = "token-yes",
    observed_ns: int = NOW,
    trusted: bool = True,
) -> ExternalQuoteState:
    return ExternalQuoteState(
        token_id=token_id,
        best_bid=bid,
        best_ask=ask,
        observed_monotonic_ns=observed_ns,
        trusted=trusted,
        source_version="fixture-v1",
    )


def _maker_snapshot(
    *,
    mapping_class: MappingClass = MappingClass.EXACT,
    signed_inventory: float = 0.0,
    external: dict[str, ExternalQuoteState] | None = None,
    bbo_observed_ns: int = NOW,
    bbo_trusted: bool = True,
    account_observed_ns: int = NOW,
    inventory_observed_ns: int = NOW,
    account_trusted: bool = True,
    volatility: float | None = 0.01,
) -> MakerMarketSnapshot:
    if external is None:
        if mapping_class is MappingClass.DERIVED:
            external = {
                "bucket-a": _external(
                    token_id="bucket-a",
                    bid=0.29,
                    ask=0.31,
                ),
                "bucket-b": _external(
                    token_id="bucket-b",
                    bid=0.19,
                    ask=0.21,
                ),
            }
        else:
            external = {"token-yes": _external()}
    return MakerMarketSnapshot(
        runtime=_runtime(
            signed_inventory=signed_inventory,
            account_trusted=account_trusted,
        ),
        exchange_id="36",
        market_id="m1",
        tournament_id=TOURNAMENT,
        now_monotonic_ns=NOW,
        sig_bbo_observed_ns=bbo_observed_ns,
        sig_bbo_trusted=bbo_trusted,
        sig_depth_observed_ns=NOW,
        sig_depth_trusted=True,
        account_observed_ns=account_observed_ns,
        inventory_observed_ns=inventory_observed_ns,
        external_quotes=external,
        volatility=volatility,
    )


def _engine(
    *,
    mapping_class: MappingClass = MappingClass.EXACT,
    direction: MappingDirection = MappingDirection.SAME,
    max_inventory: float = 10.0,
    max_age_ns: int = 100_000_000,
) -> MakerEngine:
    provider = DirectPolymarketFairValueProvider(
        _mapping(
            mapping_class=mapping_class,
            direction=direction,
        )
    )
    return MakerEngine(
        fair_value=provider,
        predictive=NullPredictiveAdjuster(),
        toxicity=NullToxicityProvider(),
        inventory=BinaryCaraInventoryModel(),
        spread=ConservativeSpreadPolicy(base_half_spread_ticks=1.0),
        size=InventoryConfidenceSizePolicy(base_size=4),
        eligibility=ConservativeEligibilityPolicy(
            max_bbo_age_ns=max_age_ns,
            max_fv_age_ns=max_age_ns,
            max_account_age_ns=max_age_ns,
            max_inventory_age_ns=max_age_ns,
            max_optional_signal_age_ns=max_age_ns,
        ),
        config=MakerConfig(max_abs_inventory=max_inventory),
    )



class _PerIntentBatchLiveSink:
    def __init__(
        self,
        journal: ExecutionJournal,
        *,
        acked_intent_indices: frozenset[int],
        omit_order_id_indices: frozenset[int] = frozenset(),
    ) -> None:
        self.journal = journal
        self.acked_intent_indices = acked_intent_indices
        self.omit_order_id_indices = omit_order_id_indices

    async def dispatch(self, plan: ExecutionPlan) -> ExecutionEvent:
        assert len(plan.intents) == 2
        self.journal.record_before_dispatch(
            plan.envelope,
            plan.intents,
            audit=plan.audit,
            submitted_monotonic_ns=NOW + 10,
        )
        for index, intent in enumerate(plan.intents):
            if index not in self.acked_intent_indices:
                self.journal.record_event(
                    logical_operation_id=plan.envelope.logical_operation_id,
                    tournament_id=intent.tournament_id,
                    logical_intent_id=intent.intent_id,
                    event_type="REJECTED",
                    observed_monotonic_ns=NOW + 20 + index,
                    exchange_id=intent.exchange_id,
                    terminal_status=LifecycleState.REJECTED.value,
                )
                continue
            order_id = None if index in self.omit_order_id_indices else str(91 + index)
            self.journal.record_event(
                logical_operation_id=plan.envelope.logical_operation_id,
                tournament_id=intent.tournament_id,
                logical_intent_id=intent.intent_id,
                event_type="ACK",
                observed_monotonic_ns=NOW + 20 + index,
                exchange_id=intent.exchange_id,
                exchange_order_id=order_id,
                terminal_status=LifecycleState.ACKED.value,
            )
        self.journal.mark_state(
            plan.envelope.logical_operation_id,
            LifecycleState.ACKED,
            NOW + 30,
        )
        return ExecutionEvent(
            logical_operation_id=plan.envelope.logical_operation_id,
            state=LifecycleState.ACKED,
            observed_monotonic_ns=NOW + 30,
            simulated=False,
            detail="fixture_best_effort_batch",
        )


def _live_two_sided_cycle(
    tmp_path: Path,
    *,
    acked_intent_indices: frozenset[int],
    omit_order_id_indices: frozenset[int] = frozenset(),
) -> tuple[tuple[object, ...], QuoteRegistry]:
    journal = ExecutionJournal(tmp_path / "live-batch.sqlite3")
    quotes = QuoteRegistry()
    observation_sink = InMemoryObservationSink()
    emitter = BoundedObservationEmitter(observation_sink, queue_max=64)
    live = _PerIntentBatchLiveSink(
        journal,
        acked_intent_indices=acked_intent_indices,
        omit_order_id_indices=omit_order_id_indices,
    )
    adapter = LiveMakerExecutionAdapter(
        cast(SigLiveSink, live),
        journal=journal,
        quotes=quotes,
        observation_emitter=emitter,
        observation_process_instance_id="live-batch-test",
        wall_clock=lambda: datetime(2026, 10, 1, 16, 0, tzinfo=UTC),
    )
    coordinator = MakerCoordinator(
        engine=_engine(),
        lifecycle=QuoteLifecycleManager(),
        quote_registry=quotes,
        risk_context=RiskContext(
            mode=ExecutionMode.LIVE,
            kill_switch=False,
            limits=RiskLimits(
                max_order_size=10,
                max_gross_exposure=100.0,
                max_per_market_exposure=100.0,
                max_open_order_exposure=100.0,
                max_concurrent_open_orders=10,
            ),
            max_state_age_ns=1_000_000_000,
        ),
        placement_dispatch=adapter.place,
        cancel_dispatch=adapter.cancel,
        reservations=ExecutionReservationBook(),
        observation_emitter=emitter,
        observation_process_instance_id="live-batch-test",
        wall_clock=lambda: datetime(2026, 10, 1, 16, 0, tzinfo=UTC),
    )
    try:
        result = asyncio.run(
            coordinator.on_state_change(
                MakerStateChange(
                    event_id="two-sided-live",
                    observed_monotonic_ns=NOW,
                    exchange_ids=frozenset({"36"}),
                ),
                {"36": _maker_snapshot()},
            )
        )
        assert result.execution_events[0].state is LifecycleState.ACKED
        emitter.close()
        return tuple(observation_sink.observations), quotes
    finally:
        emitter.close()
        journal.close()


def test_live_two_sided_batch_two_acks_publish_two_authoritative_quotes(
    tmp_path: Path,
) -> None:
    observations, quotes = _live_two_sided_cycle(
        tmp_path,
        acked_intent_indices=frozenset({0, 1}),
    )
    published = tuple(
        item for item in observations if item.kind is ObservationKind.QUOTE_PUBLISHED
    )
    assert len(published) == 2
    assert {item.logical_intent_id for item in published} == {
        "make-direct-pm:1000000000:36:0",
        "make-direct-pm:1000000000:36:1",
    }
    assert {item.exchange_order_id for item in published} == {"91", "92"}
    assert {dict(item.detail)["side"] for item in published} == {"BID", "ASK"}
    assert all(
        dict(item.detail)["quote_key"].startswith("two-sided-live:make-place:36|36|")
        for item in published
    )
    state = quotes.state("36")
    assert state.bid is not None and state.bid.exchange_order_id == 91
    assert state.ask is not None and state.ask.exchange_order_id == 92


def test_live_two_sided_batch_ack_plus_reject_publishes_only_acknowledged_quote(
    tmp_path: Path,
) -> None:
    observations, quotes = _live_two_sided_cycle(
        tmp_path,
        acked_intent_indices=frozenset({0}),
    )
    published = tuple(
        item for item in observations if item.kind is ObservationKind.QUOTE_PUBLISHED
    )
    assert len(published) == 1
    assert published[0].exchange_order_id == "91"
    assert dict(published[0].detail)["side"] == "BID"
    state = quotes.state("36")
    assert state.bid is not None
    assert state.ask is None


def test_live_two_sided_batch_ack_without_order_identity_is_not_published(
    tmp_path: Path,
) -> None:
    observations, quotes = _live_two_sided_cycle(
        tmp_path,
        acked_intent_indices=frozenset({0, 1}),
        omit_order_id_indices=frozenset({1}),
    )
    published = tuple(
        item for item in observations if item.kind is ObservationKind.QUOTE_PUBLISHED
    )
    assert len(published) == 1
    assert published[0].exchange_order_id == "91"
    state = quotes.state("36")
    assert state.bid is not None
    assert state.ask is not None
    assert state.ask.exchange_order_id is None


def test_direct_pm_preserves_value_orientation_and_source_observation_age() -> None:
    same = DirectPolymarketFairValueProvider(_mapping())
    snapshot = replace(
        _maker_snapshot(),
        now_monotonic_ns=1_000,
        external_quotes={"token-yes": _external(observed_ns=950)},
    )
    result = same.fair_value(snapshot)
    assert result.usable
    assert result.value == pytest.approx(0.5)
    assert result.uncertainty == pytest.approx(0.01)

    complement = DirectPolymarketFairValueProvider(_mapping(direction=MappingDirection.COMPLEMENT))
    snapshot = replace(
        snapshot,
        external_quotes={
            "token-yes": _external(bid=0.29, ask=0.31, observed_ns=950)
        },
    )
    assert complement.fair_value(snapshot).value == pytest.approx(0.7)

    stale = replace(
        snapshot,
        external_quotes={
            "token-yes": _external(bid=0.29, ask=0.31, observed_ns=899)
        },
    )
    stale_result = complement.fair_value(stale)
    assert stale_result.usable is True
    assert stale_result.observed_monotonic_ns == 899


def test_derived_partition_sum_and_no_trade_are_explicit() -> None:
    provider = DirectPolymarketFairValueProvider(_mapping(mapping_class=MappingClass.DERIVED))
    result = provider.fair_value(
        _maker_snapshot(mapping_class=MappingClass.DERIVED)
    )
    assert result.usable
    assert result.value == pytest.approx(0.5)
    assert result.mapping_class == "DERIVED"

    no_trade = DirectPolymarketFairValueProvider(_mapping(mapping_class=MappingClass.NO_TRADE))
    result = no_trade.fair_value(_maker_snapshot())
    assert result.usable is False
    assert result.reason == "mapping_not_directly_tradeable"


def test_inventory_skews_reservation_price_and_hard_boundary_is_one_sided() -> None:
    flat = _engine().quote(_maker_snapshot())
    long = _engine().quote(_maker_snapshot(signed_inventory=8.0))
    boundary = _engine(max_inventory=10.0).quote(
        _maker_snapshot(signed_inventory=10.0)
    )

    assert flat.desired is not None
    assert long.desired is not None
    assert flat.trace.reservation_price is not None
    assert long.trace.reservation_price is not None
    assert long.trace.reservation_price < flat.trace.reservation_price
    assert boundary.gate.mode is GateMode.ASK_ONLY
    assert boundary.desired is not None
    assert boundary.desired.bid_ticks is None
    assert boundary.desired.ask_ticks is not None


def test_fractional_inventory_headroom_never_rounds_up_past_hard_boundary() -> None:
    long = _engine(max_inventory=10.0).quote(
        _maker_snapshot(signed_inventory=9.5)
    )
    short = _engine(max_inventory=10.0).quote(
        _maker_snapshot(signed_inventory=-9.5)
    )

    assert long.desired is not None
    assert long.desired.bid_ticks is None
    assert long.desired.bid_size == 0
    assert long.desired.ask_ticks is not None

    assert short.desired is not None
    assert short.desired.ask_ticks is None
    assert short.desired.ask_size == 0
    assert short.desired.bid_ticks is not None


def test_quote_ticks_are_passive_valid_and_never_cross() -> None:
    engine = _engine()
    for bid, ask in (
        (0.05, 0.07),
        (0.20, 0.22),
        (0.49, 0.51),
        (0.80, 0.82),
        (0.93, 0.95),
    ):
        snapshot = _maker_snapshot(
            external={
                "token-yes": _external(bid=bid, ask=ask)
            }
        )
        decision = engine.quote(snapshot)
        assert decision.desired is not None
        quote = decision.desired
        if quote.bid_ticks is not None:
            assert 1 <= quote.bid_ticks <= 199
            assert quote.bid_ticks < 102
        if quote.ask_ticks is not None:
            assert 1 <= quote.ask_ticks <= 199
            assert quote.ask_ticks > 98
        if quote.bid_ticks is not None and quote.ask_ticks is not None:
            assert quote.bid_ticks < quote.ask_ticks


def test_probability_boundary_drops_side_instead_of_narrowing_required_spread() -> None:
    high = _engine().quote(
        _maker_snapshot(
            external={"token-yes": _external(bid=0.97, ask=0.99)}
        )
    )
    low = _engine().quote(
        _maker_snapshot(
            external={"token-yes": _external(bid=0.01, ask=0.03)}
        )
    )

    assert high.desired is not None
    assert high.desired.ask_ticks is None
    assert high.desired.ask_size == 0
    assert high.desired.bid_ticks is not None

    assert low.desired is not None
    assert low.desired.bid_ticks is None
    assert low.desired.bid_size == 0
    assert low.desired.ask_ticks is not None


@pytest.mark.parametrize(
    ("field", "reason"),
    (
        ("bbo", "sig_bbo_stale"),
        ("account", "account_stale"),
        ("inventory", "inventory_stale"),
    ),
)
def test_stale_internal_sources_cancel_quotes(field: str, reason: str) -> None:
    if field == "bbo":
        snapshot = _maker_snapshot(bbo_observed_ns=NOW - 100_000_001)
    elif field == "account":
        snapshot = _maker_snapshot(account_observed_ns=NOW - 100_000_001)
    else:
        snapshot = _maker_snapshot(inventory_observed_ns=NOW - 100_000_001)
    decision = _engine().quote(snapshot)
    assert decision.desired is None
    assert decision.gate.mode is GateMode.CANCEL
    assert decision.gate.reason == reason


def test_trusted_but_stale_sources_fail_closed_at_exact_deadline() -> None:
    max_age_ns = 100_000_000
    engine = _engine(max_age_ns=max_age_ns)

    stale_pm = engine.quote(
        _maker_snapshot(
            external={
                "token-yes": _external(
                    observed_ns=NOW - max_age_ns,
                    trusted=True,
                )
            }
        )
    )
    assert stale_pm.desired is None
    assert stale_pm.gate.mode is GateMode.CANCEL
    assert stale_pm.gate.reason == "fv_stale"

    stale_account = engine.quote(
        _maker_snapshot(
            account_observed_ns=NOW - max_age_ns,
            inventory_observed_ns=NOW - max_age_ns,
        )
    )
    assert stale_account.desired is None
    assert stale_account.gate.mode is GateMode.CANCEL
    assert stale_account.gate.reason == "account_stale"

    fresh = engine.quote(
        _maker_snapshot(
            bbo_observed_ns=NOW - max_age_ns + 1,
            account_observed_ns=NOW - max_age_ns + 1,
            inventory_observed_ns=NOW - max_age_ns + 1,
            external={
                "token-yes": _external(
                    observed_ns=NOW - max_age_ns + 1,
                    trusted=True,
                )
            },
        )
    )
    assert fresh.desired is not None
    assert fresh.gate.mode in {GateMode.NORMAL, GateMode.WIDER}


def test_future_source_timestamp_fails_closed() -> None:
    decision = _engine().quote(
        _maker_snapshot(
            external={
                "token-yes": _external(
                    observed_ns=NOW + 1,
                    trusted=True,
                )
            }
        )
    )
    assert decision.desired is None


def test_untrusted_account_and_lost_pm_feed_fail_closed() -> None:
    untrusted = _engine().quote(_maker_snapshot(account_trusted=False))
    assert untrusted.desired is None
    assert untrusted.gate.reason == "account_untrusted"

    lost = _engine().quote(
        _maker_snapshot(
            external={"token-yes": _external(trusted=False)}
        )
    )
    assert lost.desired is None
    assert "fair_value_unusable" in lost.gate.reason


class _BrokenFairValue:
    provider_id = "broken"
    version = "v1"

    def fair_value(self, snapshot: MakerMarketSnapshot) -> FairValueResult:
        del snapshot
        raise RuntimeError("boom")


class _NanFairValue:
    provider_id = "nan"
    version = "v1"

    def fair_value(self, snapshot: MakerMarketSnapshot) -> FairValueResult:
        return FairValueResult(
            value=math.nan,
            uncertainty=0.0,
            confidence=1.0,
            observed_monotonic_ns=snapshot.now_monotonic_ns,
            trusted=True,
            source_id=self.provider_id,
            source_version=self.version,
            mapping_class="EXACT",
            reason="bad",
        )


def _engine_with_provider(provider: object) -> MakerEngine:
    return MakerEngine(
        fair_value=provider,  # type: ignore[arg-type]
        predictive=NullPredictiveAdjuster(),
        toxicity=NullToxicityProvider(),
        inventory=BinaryCaraInventoryModel(),
        spread=ConservativeSpreadPolicy(),
        size=InventoryConfidenceSizePolicy(),
        eligibility=ConservativeEligibilityPolicy(
            max_bbo_age_ns=100_000_000,
            max_fv_age_ns=100_000_000,
            max_account_age_ns=100_000_000,
            max_inventory_age_ns=100_000_000,
            max_optional_signal_age_ns=100_000_000,
        ),
    )


def test_plugin_exception_and_nan_output_suspend() -> None:
    broken = _engine_with_provider(_BrokenFairValue()).quote(_maker_snapshot())
    assert broken.desired is None
    assert broken.gate.reason == "fair_value_plugin_exception"

    nan = _engine_with_provider(_NanFairValue()).quote(_maker_snapshot())
    assert nan.desired is None
    assert "fair_value_unusable" in nan.gate.reason


def _active(
    side: QuoteSide,
    *,
    ticks: int,
    size: int = 2,
    state: LifecycleState = LifecycleState.OPEN,
    order_id: int | None = 91,
) -> ActiveQuote:
    return ActiveQuote(
        side=side,
        price_ticks=ticks,
        size=size,
        remaining_size=size,
        logical_operation_id=f"old-{side.value}",
        exchange_order_id=order_id,
        lifecycle_state=state,
        observed_monotonic_ns=NOW - 1,
    )


def test_lifecycle_suppresses_churn_and_cancel_replace_is_two_phase() -> None:
    manager = QuoteLifecycleManager(
        QuoteLifecycleConfig(
            min_replace_ticks=2,
            min_replace_size=2,
        )
    )
    registry = QuoteRegistry()
    registry.apply_authoritative(
        exchange_id="36",
        side=QuoteSide.BID,
        price_ticks=99,
        size=2,
        remaining_size=2,
        logical_operation_id="old-bid",
        exchange_order_id=91,
        lifecycle_state=LifecycleState.OPEN,
        observed_monotonic_ns=NOW - 1,
    )
    desired = _engine().quote(_maker_snapshot()).desired
    assert desired is not None

    near = replace(desired, bid_ticks=100, bid_size=3, ask_ticks=None, ask_size=0)
    actions = manager.decide(
        desired=near,
        current=registry.state("36"),
        now_monotonic_ns=NOW,
    )
    bid = next(action for action in actions if action.side is QuoteSide.BID)
    assert bid.kind is QuoteLifecycleActionKind.KEEP

    far = replace(near, bid_ticks=102)
    actions = manager.decide(
        desired=far,
        current=registry.state("36"),
        now_monotonic_ns=NOW,
    )
    bid = next(action for action in actions if action.side is QuoteSide.BID)
    assert bid.kind is QuoteLifecycleActionKind.CANCEL
    assert bid.reason == "cancel_for_material_replace"


def test_unresolved_quote_blocks_replacement_exposure() -> None:
    manager = QuoteLifecycleManager()
    state = replace(
        QuoteRegistry().state("36"),
        bid=_active(
            QuoteSide.BID,
            ticks=99,
            state=LifecycleState.UNCERTAIN,
            order_id=None,
        ),
    )
    desired = _engine().quote(_maker_snapshot()).desired
    assert desired is not None
    actions = manager.decide(
        desired=desired,
        current=state,
        now_monotonic_ns=NOW,
    )
    bid = next(action for action in actions if action.side is QuoteSide.BID)
    assert bid.kind is QuoteLifecycleActionKind.WAIT_RECONCILIATION


def test_shadow_coordinator_uses_central_risk_and_does_not_duplicate_quote() -> None:
    engine = _engine()
    registry = QuoteRegistry()
    adapter = ShadowMakerExecutionAdapter()
    coordinator = MakerCoordinator(
        engine=engine,
        lifecycle=QuoteLifecycleManager(),
        quote_registry=registry,
        risk_context=RiskContext(
            mode=ExecutionMode.SHADOW,
            kill_switch=False,
            limits=None,
            max_state_age_ns=100_000_000,
        ),
        placement_dispatch=adapter.place,
        cancel_dispatch=adapter.cancel,
    )
    snapshot = _maker_snapshot()
    change = MakerStateChange(
        event_id="pm-1",
        observed_monotonic_ns=NOW,
        exchange_ids=frozenset({"36"}),
    )

    first = asyncio.run(coordinator.on_state_change(change, {"36": snapshot}))
    assert len(first.risk_decisions) == 1
    assert first.risk_decisions[0].approved
    assert len(first.execution_events) == 1
    assert first.execution_events[0].simulated
    state = registry.state("36")
    assert state.bid is not None
    assert state.ask is not None

    second = asyncio.run(
        coordinator.on_state_change(
            MakerStateChange(
                event_id="pm-2",
                observed_monotonic_ns=NOW + 1,
                exchange_ids=frozenset({"36"}),
            ),
            {"36": replace(snapshot, now_monotonic_ns=NOW + 1)},
        )
    )
    assert second.risk_decisions == ()
    assert second.execution_events == ()
    assert all(
        action.kind is QuoteLifecycleActionKind.KEEP
        for action in second.lifecycle_actions
    )


class _DeadlineBridge:
    tradeable_exchange_ids = frozenset({"36"})

    def __init__(self, base: MakerMarketSnapshot) -> None:
        self._base = base

    def sig_exchanges_for_polymarket_token(self, token_id: str) -> frozenset[str]:
        del token_id
        return frozenset({"36"})

    def build_many(
        self,
        exchange_ids: frozenset[str] | set[str] | tuple[str, ...],
        *,
        monotonic_now_ns: int,
        **kwargs: object,
    ) -> dict[str, MakerMarketSnapshot]:
        del kwargs
        if "36" not in exchange_ids:
            return {}
        return {
            "36": replace(
                self._base,
                now_monotonic_ns=monotonic_now_ns,
                runtime=replace(
                    self._base.runtime,
                    observation_monotonic_ns=monotonic_now_ns,
                ),
            )
        }


class _DeadlineClock:
    def __init__(self, value: int) -> None:
        self.value = value

    def __call__(self) -> int:
        return self.value


def test_freshness_deadline_cancels_resting_quote_without_feed_event() -> None:
    max_age_ns = 100_000_000
    snapshot = _maker_snapshot()
    registry = QuoteRegistry()
    adapter = ShadowMakerExecutionAdapter()
    coordinator = MakerCoordinator(
        engine=_engine(max_age_ns=max_age_ns),
        lifecycle=QuoteLifecycleManager(),
        quote_registry=registry,
        risk_context=RiskContext(
            mode=ExecutionMode.SHADOW,
            kill_switch=False,
            limits=None,
            max_state_age_ns=max_age_ns,
        ),
        placement_dispatch=adapter.place,
        cancel_dispatch=adapter.cancel,
    )
    clock = _DeadlineClock(NOW)
    runtime = MakerRuntimeLoop(
        bridge=cast(MakerSourceBridge, _DeadlineBridge(snapshot)),
        coordinator=coordinator,
        polymarket_feed_trusted=lambda: True,
        wall_clock=lambda: datetime(2026, 9, 29, 14, 0, tzinfo=UTC),
        mono_clock=clock,
        runtime_session_id="freshness-expiry",
    )

    runtime.notify_sig({"36"}, observed_monotonic_ns=NOW)
    first = asyncio.run(runtime.drain_once())
    assert first is not None
    assert registry.state("36").bid is not None
    assert registry.state("36").ask is not None

    # No source notification occurs here. The deadline itself must wake MAKE.
    clock.value = NOW + max_age_ns - 1
    assert asyncio.run(runtime.drain_once()) is None
    assert registry.state("36").bid is not None
    assert registry.state("36").ask is not None

    clock.value = NOW + max_age_ns
    expired = asyncio.run(runtime.drain_once())
    assert expired is not None
    assert any(
        decision.gate.reason in {
            "sig_bbo_stale",
            "account_stale",
            "inventory_stale",
            "fv_stale",
        }
        for decision in expired.decisions
    )
    assert registry.state("36").bid is None
    assert registry.state("36").ask is None


def test_global_kill_cancels_resting_quotes_even_in_shadow() -> None:
    engine = _engine()
    registry = QuoteRegistry()
    registry.apply_authoritative(
        exchange_id="36",
        side=QuoteSide.BID,
        price_ticks=99,
        size=2,
        remaining_size=2,
        logical_operation_id="old-bid",
        exchange_order_id=91,
        lifecycle_state=LifecycleState.OPEN,
        observed_monotonic_ns=NOW - 1,
    )
    registry.apply_authoritative(
        exchange_id="36",
        side=QuoteSide.ASK,
        price_ticks=101,
        size=2,
        remaining_size=2,
        logical_operation_id="old-ask",
        exchange_order_id=92,
        lifecycle_state=LifecycleState.OPEN,
        observed_monotonic_ns=NOW - 1,
    )
    adapter = ShadowMakerExecutionAdapter()
    coordinator = MakerCoordinator(
        engine=engine,
        lifecycle=QuoteLifecycleManager(),
        quote_registry=registry,
        risk_context=RiskContext(
            mode=ExecutionMode.SHADOW,
            kill_switch=True,
            limits=None,
            max_state_age_ns=100_000_000,
        ),
        placement_dispatch=adapter.place,
        cancel_dispatch=adapter.cancel,
    )
    result = asyncio.run(
        coordinator.on_state_change(
            MakerStateChange(
                event_id="kill",
                observed_monotonic_ns=NOW,
                exchange_ids=frozenset({"36"}),
            ),
            {"36": _maker_snapshot()},
        )
    )
    assert len(result.execution_events) == 2
    assert registry.state("36").bid is None
    assert registry.state("36").ask is None


def test_signed_inventory_is_additive_to_existing_gross_risk_state() -> None:
    position = RuntimePosition(
        exchange_id="36",
        market_id="m1",
        tournament_id=TOURNAMENT,
        gross_exposure=7.0,
        signed_quantity=-7.0,
    )
    portfolio = RuntimePortfolio(positions=(position,), account_trusted=True)
    assert portfolio.gross_exposure == pytest.approx(7.0)
    assert position.signed_quantity == pytest.approx(-7.0)

def test_binary_cara_reservation_matches_math_ledger_formula() -> None:
    engine = _engine(max_inventory=10.0)
    decision = engine.quote(_maker_snapshot(signed_inventory=5.0))
    assert decision.trace.adjusted_fv is not None
    assert decision.trace.reservation_price is not None
    p = decision.trace.adjusted_fv
    gamma = 0.02
    expected = 1.0 / (1.0 + math.exp(-(math.log(p / (1.0 - p)) - gamma * 5.0)))
    assert decision.trace.reservation_price == pytest.approx(expected)


@pytest.mark.parametrize(
    "state",
    (
        LifecycleState.PENDING,
        LifecycleState.ACKED,
        LifecycleState.CANCEL_PENDING,
        LifecycleState.UNCERTAIN,
        LifecycleState.RECONCILING,
    ),
)
def test_unresolved_quote_states_block_replacement(state: LifecycleState) -> None:
    manager = QuoteLifecycleManager()
    current = replace(
        QuoteRegistry().state("36"),
        bid=_active(
            QuoteSide.BID,
            ticks=99,
            state=state,
            order_id=91,
        ),
    )
    desired = _engine().quote(_maker_snapshot()).desired
    assert desired is not None
    desired = replace(desired, bid_ticks=101, bid_size=4, ask_ticks=None, ask_size=0)
    actions = manager.decide(
        desired=desired,
        current=current,
        now_monotonic_ns=NOW,
    )
    bid = next(action for action in actions if action.side is QuoteSide.BID)
    assert bid.kind is QuoteLifecycleActionKind.WAIT_RECONCILIATION


def test_partial_fill_can_cancel_but_never_place_replacement_same_cycle() -> None:
    manager = QuoteLifecycleManager()
    current = replace(
        QuoteRegistry().state("36"),
        bid=_active(
            QuoteSide.BID,
            ticks=99,
            size=4,
            state=LifecycleState.PARTIALLY_FILLED,
            order_id=91,
        ),
    )
    desired = _engine().quote(_maker_snapshot()).desired
    assert desired is not None
    desired = replace(desired, bid_ticks=101, bid_size=4, ask_ticks=None, ask_size=0)
    actions = manager.decide(
        desired=desired,
        current=current,
        now_monotonic_ns=NOW,
    )
    bid = next(action for action in actions if action.side is QuoteSide.BID)
    assert bid.kind is QuoteLifecycleActionKind.CANCEL
    assert not any(
        action.kind is QuoteLifecycleActionKind.PLACE
        for action in actions
        if action.side is QuoteSide.BID
    )


def test_full_fill_allows_later_refill() -> None:
    manager = QuoteLifecycleManager()
    current = replace(
        QuoteRegistry().state("36"),
        bid=_active(
            QuoteSide.BID,
            ticks=99,
            size=2,
            state=LifecycleState.FILLED,
            order_id=91,
        ),
    )
    desired = _engine().quote(_maker_snapshot()).desired
    assert desired is not None
    desired = replace(desired, ask_ticks=None, ask_size=0)
    actions = manager.decide(
        desired=desired,
        current=current,
        now_monotonic_ns=NOW,
    )
    bid = next(action for action in actions if action.side is QuoteSide.BID)
    assert bid.kind is QuoteLifecycleActionKind.PLACE
    assert bid.reason == "terminal_quote_refill"


def test_settled_market_cancels_quote_generation() -> None:
    snapshot = _maker_snapshot()
    settled_runtime = replace(
        snapshot.runtime,
        markets=(replace(snapshot.runtime.markets[0], status="settled"),),
    )
    decision = _engine().quote(replace(snapshot, runtime=settled_runtime))
    assert decision.desired is None
    assert decision.gate.mode is GateMode.CANCEL
    assert decision.gate.reason == "market_not_open"


def test_quote_invariants_over_probability_inventory_grid() -> None:
    engine = _engine(max_inventory=10.0)
    for midpoint in (0.02, 0.05, 0.10, 0.25, 0.50, 0.75, 0.90, 0.95, 0.98):
        bid = max(0.0, midpoint - 0.005)
        ask = min(1.0, midpoint + 0.005)
        for inventory in (-10.0, -8.0, -2.0, 0.0, 2.0, 8.0, 10.0):
            decision = engine.quote(
                _maker_snapshot(
                    signed_inventory=inventory,
                    external={
                        "token-yes": _external(bid=bid, ask=ask),
                    },
                )
            )
            if decision.desired is None:
                assert decision.gate.mode in {
                    GateMode.CANCEL,
                    GateMode.SUSPEND,
                    GateMode.NO_TRADE,
                }
                continue
            quote = decision.desired
            assert quote.bid_size >= 0
            assert quote.ask_size >= 0
            if quote.bid_ticks is not None:
                assert 1 <= quote.bid_ticks <= 199
            if quote.ask_ticks is not None:
                assert 1 <= quote.ask_ticks <= 199
            if quote.bid_ticks is not None and quote.ask_ticks is not None:
                assert quote.bid_ticks < quote.ask_ticks
            if inventory >= 10.0:
                assert quote.bid_ticks is None
            if inventory <= -10.0:
                assert quote.ask_ticks is None

def test_placement_uncertainty_retains_reservation_and_blocks_duplicate_exposure() -> None:
    registry = QuoteRegistry()
    reservations = ExecutionReservationBook()
    dispatch_calls = 0

    async def uncertain_place(
        plan: ExecutionPlan,
        snapshot: MakerMarketSnapshot,
    ) -> ExecutionEvent:
        nonlocal dispatch_calls
        del plan, snapshot
        dispatch_calls += 1
        raise RuntimeError("simulated transport uncertainty")

    async def unused_cancel(
        active: ActiveQuote,
        logical_operation_id: str,
        tournament_id: str,
    ) -> ExecutionEvent:
        del active, logical_operation_id, tournament_id
        raise AssertionError("cancel should not run")

    coordinator = MakerCoordinator(
        engine=_engine(),
        lifecycle=QuoteLifecycleManager(),
        quote_registry=registry,
        risk_context=RiskContext(
            mode=ExecutionMode.LIVE,
            kill_switch=False,
            limits=RiskLimits(
                max_order_size=10,
                max_gross_exposure=100.0,
                max_per_market_exposure=100.0,
                max_open_order_exposure=100.0,
                max_concurrent_open_orders=100,
            ),
            max_state_age_ns=100_000_000,
        ),
        placement_dispatch=uncertain_place,
        cancel_dispatch=unused_cancel,
        reservations=reservations,
    )
    snapshot = _maker_snapshot()

    with pytest.raises(RuntimeError, match="transport uncertainty"):
        asyncio.run(
            coordinator.on_state_change(
                MakerStateChange(
                    event_id="uncertain-place-1",
                    observed_monotonic_ns=NOW,
                    exchange_ids=frozenset({"36"}),
                ),
                {"36": snapshot},
            )
        )

    state = registry.state("36")
    assert state.bid is not None
    assert state.ask is not None
    assert state.bid.lifecycle_state is LifecycleState.UNCERTAIN
    assert state.ask.lifecycle_state is LifecycleState.UNCERTAIN
    assert reservations.intent_ids()
    assert dispatch_calls == 1

    second = asyncio.run(
        coordinator.on_state_change(
            MakerStateChange(
                event_id="uncertain-place-2",
                observed_monotonic_ns=NOW + 1,
                exchange_ids=frozenset({"36"}),
            ),
            {"36": replace(snapshot, now_monotonic_ns=NOW + 1)},
        )
    )
    assert dispatch_calls == 1
    assert second.risk_decisions == ()
    assert second.execution_events == ()
    assert all(
        action.kind is QuoteLifecycleActionKind.WAIT_RECONCILIATION
        for action in second.lifecycle_actions
    )


def test_cancel_uncertainty_blocks_replacement_until_reconciliation() -> None:
    registry = QuoteRegistry()
    registry.apply_authoritative(
        exchange_id="36",
        side=QuoteSide.BID,
        price_ticks=90,
        size=2,
        remaining_size=2,
        logical_operation_id="old-bid",
        exchange_order_id=91,
        lifecycle_state=LifecycleState.OPEN,
        observed_monotonic_ns=NOW - 1,
    )

    async def unused_place(
        plan: ExecutionPlan,
        snapshot: MakerMarketSnapshot,
    ) -> ExecutionEvent:
        del plan, snapshot
        raise AssertionError("replacement must not place in cancel cycle")

    async def uncertain_cancel(
        active: ActiveQuote,
        logical_operation_id: str,
        tournament_id: str,
    ) -> ExecutionEvent:
        del active, logical_operation_id, tournament_id
        raise RuntimeError("simulated cancel uncertainty")

    coordinator = MakerCoordinator(
        engine=_engine(),
        lifecycle=QuoteLifecycleManager(),
        quote_registry=registry,
        risk_context=RiskContext(
            mode=ExecutionMode.SHADOW,
            kill_switch=False,
            limits=None,
            max_state_age_ns=100_000_000,
        ),
        placement_dispatch=unused_place,
        cancel_dispatch=uncertain_cancel,
    )
    snapshot = _maker_snapshot(
        external={"token-yes": _external(bid=0.59, ask=0.61)}
    )

    with pytest.raises(RuntimeError, match="cancel uncertainty"):
        asyncio.run(
            coordinator.on_state_change(
                MakerStateChange(
                    event_id="uncertain-cancel-1",
                    observed_monotonic_ns=NOW,
                    exchange_ids=frozenset({"36"}),
                ),
                {"36": snapshot},
            )
        )

    active = registry.state("36").bid
    assert active is not None
    assert active.lifecycle_state is LifecycleState.UNCERTAIN

    actions = QuoteLifecycleManager().decide(
        desired=_engine().quote(snapshot).desired,
        current=registry.state("36"),
        now_monotonic_ns=NOW + 1,
    )
    bid = next(action for action in actions if action.side is QuoteSide.BID)
    assert bid.kind is QuoteLifecycleActionKind.WAIT_RECONCILIATION

