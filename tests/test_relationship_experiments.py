from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from predictions_cup.learning.relationships import (
    EventWindow,
    ExperimentFamily,
    InstrumentKey,
    LeakageClass,
    PriceCoordinate,
    ReferenceEstimator,
    ReferencePriceMode,
    ReferenceSpec,
    Regime,
    RelationshipDirection,
    RelationshipExperimentRunner,
    RelationshipExperimentSpec,
    response_curve,
    serialize_relationship_observations,
    summarize_relationship_observations,
)
from predictions_cup.replay.markouts import Direction
from predictions_cup.replay.model import (
    InvalidReason,
    QuotePayload,
    ReplayEvent,
    ReplayEventType,
    ReplaySource,
    TradePayload,
    TrustPayload,
)


def _quote(
    at: datetime,
    source: ReplaySource,
    instrument: str,
    bid: str,
    ask: str,
    sequence: int,
    *,
    quote_at: datetime | None = None,
) -> ReplayEvent:
    return ReplayEvent(
        observed_at=at,
        source_at=None,
        source=source,
        event_type=ReplayEventType.BOOK_OBSERVATION,
        instrument_id=instrument,
        market_id=f"market-{instrument}",
        sequence=sequence,
        payload=QuotePayload(
            best_bid=Decimal(bid),
            best_ask=Decimal(ask),
            quote_observed_at=quote_at or at,
        ),
    )


def _trade(
    at: datetime,
    source: ReplaySource,
    instrument: str,
    price: str,
    sequence: int,
) -> ReplayEvent:
    return ReplayEvent(
        observed_at=at,
        source_at=None,
        source=source,
        event_type=ReplayEventType.TRADE,
        instrument_id=instrument,
        market_id=f"market-{instrument}",
        sequence=sequence,
        payload=TradePayload(price=Decimal(price), quantity=Decimal("1")),
    )


def _trust(at: datetime, instrument: str = "sig-1", sequence: int = 99) -> ReplayEvent:
    return ReplayEvent(
        observed_at=at,
        source_at=None,
        source=ReplaySource.SIG,
        event_type=ReplayEventType.TRUST,
        instrument_id=instrument,
        market_id=None,
        sequence=sequence,
        payload=TrustPayload(trusted=True, transition="TRUSTED"),
    )


def _leadlag_spec(**overrides: object) -> RelationshipExperimentSpec:
    values: dict[str, object] = {
        "id": "LEADLAG-TEST",
        "dataset_id": "synthetic",
        "family": ExperimentFamily.LEADLAG,
        "target": InstrumentKey(ReplaySource.SIG, "sig-1"),
        "references": (
            ReferenceSpec(InstrumentKey(ReplaySource.POLYMARKET, "poly-1")),
        ),
        "threshold": Decimal("0.05"),
        "lookback": timedelta(seconds=1),
        "coordinate": PriceCoordinate.PROBABILITY,
        "target_horizons": (timedelta(seconds=1), timedelta(seconds=2)),
        "reference_freshness": timedelta(seconds=10),
    }
    values.update(overrides)
    return RelationshipExperimentSpec(**values)  # type: ignore[arg-type]


def test_leadlag_response_curve_uses_executable_quotes_without_lookahead() -> None:
    base = datetime(2026, 9, 25, 12, tzinfo=UTC)
    events = (
        _quote(base, ReplaySource.POLYMARKET, "poly-1", "0.49", "0.51", 1),
        _quote(base, ReplaySource.SIG, "sig-1", "0.49", "0.51", 1),
        _trust(base),
        _quote(
            base + timedelta(seconds=1),
            ReplaySource.POLYMARKET,
            "poly-1",
            "0.59",
            "0.61",
            2,
        ),
        _quote(
            base + timedelta(seconds=2, milliseconds=100),
            ReplaySource.SIG,
            "sig-1",
            "0.57",
            "0.59",
            2,
        ),
        _quote(
            base + timedelta(seconds=4),
            ReplaySource.POLYMARKET,
            "poly-1",
            "0.59",
            "0.61",
            3,
        ),
    )
    rows = RelationshipExperimentRunner().run(events, (_leadlag_spec(),))

    signal_rows = [row for row in rows if row.signal_direction is Direction.BUY_YES]
    assert len(signal_rows) == 2
    by_horizon = {row.horizon: row for row in signal_rows}
    one_second = by_horizon[timedelta(seconds=1)]
    two_second = by_horizon[timedelta(seconds=2)]

    assert one_second.future_bid == Decimal("0.49")
    assert one_second.gross_markout == Decimal("-0.02")
    assert two_second.future_bid == Decimal("0.57")
    assert two_second.gross_markout == Decimal("0.06")
    assert two_second.entry_price == Decimal("0.51")
    assert two_second.signal_value == Decimal("0.10")
    assert two_second.net_markout is None

    curve = response_curve(rows)
    assert any(key[5] == "2" and summary.n_valid == 1 for key, summary in curve.items())


def test_leadlag_down_signal_maps_to_sell_yes() -> None:
    base = datetime(2026, 9, 25, 12, tzinfo=UTC)
    events = (
        _quote(base, ReplaySource.POLYMARKET, "poly-1", "0.49", "0.51", 1),
        _quote(base, ReplaySource.SIG, "sig-1", "0.49", "0.51", 1),
        _trust(base),
        _quote(
            base + timedelta(seconds=1),
            ReplaySource.POLYMARKET,
            "poly-1",
            "0.39",
            "0.41",
            2,
        ),
        _quote(
            base + timedelta(seconds=2),
            ReplaySource.SIG,
            "sig-1",
            "0.42",
            "0.44",
            2,
        ),
        _quote(
            base + timedelta(seconds=4),
            ReplaySource.POLYMARKET,
            "poly-1",
            "0.39",
            "0.41",
            3,
        ),
    )
    spec = _leadlag_spec(target_horizons=(timedelta(seconds=1),))
    rows = RelationshipExperimentRunner().run(events, (spec,))
    signal = next(row for row in rows if row.signal_direction is not None)
    assert signal.signal_direction is Direction.SELL_YES
    assert signal.entry_price == Decimal("0.49")
    assert signal.future_ask == Decimal("0.44")
    assert signal.gross_markout == Decimal("0.05")


def test_continuous_threshold_episode_is_deduplicated() -> None:
    base = datetime(2026, 9, 25, 12, tzinfo=UTC)
    events = (
        _quote(base, ReplaySource.POLYMARKET, "poly-1", "0.49", "0.51", 1),
        _quote(base, ReplaySource.SIG, "sig-1", "0.49", "0.51", 1),
        _trust(base),
        _quote(base + timedelta(seconds=1), ReplaySource.POLYMARKET, "poly-1", "0.59", "0.61", 2),
        _quote(
            base + timedelta(seconds=1, milliseconds=500),
            ReplaySource.POLYMARKET,
            "poly-1",
            "0.60",
            "0.62",
            3,
        ),
        _quote(base + timedelta(seconds=3), ReplaySource.SIG, "sig-1", "0.55", "0.57", 2),
    )
    spec = _leadlag_spec(target_horizons=(timedelta(seconds=2),))
    rows = RelationshipExperimentRunner().run(events, (spec,))
    assert len([row for row in rows if row.signal_direction is not None]) == 1


def test_residual_rich_target_generates_sell_signal_and_convergence_markout() -> None:
    base = datetime(2026, 9, 25, 12, tzinfo=UTC)
    spec = RelationshipExperimentSpec(
        id="RV-TEST",
        dataset_id="synthetic",
        family=ExperimentFamily.RV,
        target=InstrumentKey(ReplaySource.SIG, "sig-1"),
        references=(ReferenceSpec(InstrumentKey(ReplaySource.POLYMARKET, "poly-1")),),
        threshold=Decimal("0.05"),
        coordinate=PriceCoordinate.PROBABILITY,
        estimator=ReferenceEstimator.WEIGHTED_PROBABILITY,
        target_horizons=(timedelta(seconds=1),),
        reference_freshness=timedelta(seconds=10),
    )
    events = (
        _quote(base, ReplaySource.POLYMARKET, "poly-1", "0.49", "0.51", 1),
        _quote(base, ReplaySource.SIG, "sig-1", "0.59", "0.61", 1),
        _trust(base),
        _quote(base + timedelta(seconds=1), ReplaySource.SIG, "sig-1", "0.53", "0.55", 2),
    )
    rows = RelationshipExperimentRunner().run(events, (spec,))
    signal = next(row for row in rows if row.signal_direction is not None)
    assert signal.signal_direction is Direction.SELL_YES
    assert signal.features.reference_residual_probability == Decimal("0.10")
    assert signal.gross_markout == Decimal("0.04")


def test_loo_family_rejects_direct_equivalent_leakage_but_loo_price_allows_it() -> None:
    target = InstrumentKey(ReplaySource.SIG, "sig-1")
    direct = InstrumentKey(ReplaySource.POLYMARKET, "direct-1")
    direct_ref = ReferenceSpec(direct, leakage_class=LeakageClass.DIRECT_EQUIVALENT)

    allowed = RelationshipExperimentSpec(
        id="LOO-PRICE",
        dataset_id="synthetic",
        family=ExperimentFamily.LOO_PRICE,
        target=target,
        references=(direct_ref,),
        direct_family=(direct,),
        threshold=Decimal("0.05"),
    )
    assert allowed.family is ExperimentFamily.LOO_PRICE

    with pytest.raises(ValueError, match="LOO-FAMILY"):
        RelationshipExperimentSpec(
            id="LOO-FAMILY",
            dataset_id="synthetic",
            family=ExperimentFamily.LOO_FAMILY,
            target=target,
            references=(direct_ref,),
            direct_family=(direct,),
            threshold=Decimal("0.05"),
        )


def test_loo_price_can_signal_when_indirect_loo_family_has_no_edge() -> None:
    base = datetime(2026, 9, 25, 12, tzinfo=UTC)
    target = InstrumentKey(ReplaySource.SIG, "sig-1")
    direct = InstrumentKey(ReplaySource.POLYMARKET, "direct-1")
    indirect = InstrumentKey(ReplaySource.POLYMARKET, "indirect-1")
    loo_price = RelationshipExperimentSpec(
        id="LOO-PRICE",
        dataset_id="synthetic",
        family=ExperimentFamily.LOO_PRICE,
        target=target,
        references=(ReferenceSpec(direct, leakage_class=LeakageClass.DIRECT_EQUIVALENT),),
        threshold=Decimal("0.05"),
        coordinate=PriceCoordinate.PROBABILITY,
        estimator=ReferenceEstimator.WEIGHTED_PROBABILITY,
        target_horizons=(timedelta(seconds=1),),
        reference_freshness=timedelta(seconds=10),
    )
    loo_family = RelationshipExperimentSpec(
        id="LOO-FAMILY",
        dataset_id="synthetic",
        family=ExperimentFamily.LOO_FAMILY,
        target=target,
        references=(ReferenceSpec(indirect),),
        direct_family=(direct,),
        excluded_family=(direct,),
        threshold=Decimal("0.05"),
        coordinate=PriceCoordinate.PROBABILITY,
        estimator=ReferenceEstimator.WEIGHTED_PROBABILITY,
        target_horizons=(timedelta(seconds=1),),
        reference_freshness=timedelta(seconds=10),
    )
    events = (
        _quote(base, ReplaySource.POLYMARKET, "direct-1", "0.69", "0.71", 1),
        _quote(base, ReplaySource.POLYMARKET, "indirect-1", "0.49", "0.51", 1),
        _quote(base, ReplaySource.SIG, "sig-1", "0.49", "0.51", 1),
        _trust(base),
        _quote(base + timedelta(seconds=1), ReplaySource.SIG, "sig-1", "0.50", "0.52", 2),
    )
    rows = RelationshipExperimentRunner().run(events, (loo_price, loo_family))
    assert any(
        row.experiment_id == "LOO-PRICE" and row.signal_direction is Direction.BUY_YES
        for row in rows
    )
    assert not any(
        row.experiment_id == "LOO-FAMILY" and row.signal_direction is not None
        for row in rows
    )


def test_missing_leadlag_lookback_history_is_reported_as_coverage_invalidity() -> None:
    base = datetime(2026, 9, 25, 12, tzinfo=UTC)
    events = (
        _quote(base, ReplaySource.POLYMARKET, "poly-1", "0.49", "0.51", 1),
        _quote(base, ReplaySource.SIG, "sig-1", "0.49", "0.51", 1),
        _trust(base),
    )
    spec = _leadlag_spec(target_horizons=(timedelta(seconds=1),))
    rows = RelationshipExperimentRunner().run(events, (spec,))
    assert any(
        row.invalid_reason is InvalidReason.INSUFFICIENT_PREDICTOR_COVERAGE for row in rows
    )


def test_stale_reference_is_reported_not_silently_dropped() -> None:
    base = datetime(2026, 9, 25, 12, tzinfo=UTC)
    spec = RelationshipExperimentSpec(
        id="RV-STALE",
        dataset_id="synthetic",
        family=ExperimentFamily.RV,
        target=InstrumentKey(ReplaySource.SIG, "sig-1"),
        references=(ReferenceSpec(InstrumentKey(ReplaySource.POLYMARKET, "poly-1")),),
        threshold=Decimal("0.05"),
        coordinate=PriceCoordinate.PROBABILITY,
        target_horizons=(timedelta(seconds=1),),
        reference_freshness=timedelta(seconds=2),
    )
    events = (
        _quote(base, ReplaySource.POLYMARKET, "poly-1", "0.49", "0.51", 1),
        _quote(base, ReplaySource.SIG, "sig-1", "0.49", "0.51", 1),
        _trust(base),
        _quote(base + timedelta(seconds=10), ReplaySource.SIG, "sig-1", "0.59", "0.61", 2),
    )
    rows = RelationshipExperimentRunner().run(events, (spec,))
    stale = [row for row in rows if row.invalid_reason is InvalidReason.EXTERNAL_STALE]
    assert stale
    summary = summarize_relationship_observations(rows)
    assert summary.invalid_breakdown[InvalidReason.EXTERNAL_STALE.value] >= 1


def test_multi_reference_equal_weight_and_event_regime() -> None:
    base = datetime(2026, 9, 25, 12, tzinfo=UTC)
    spec = _leadlag_spec(
        references=(
            ReferenceSpec(InstrumentKey(ReplaySource.POLYMARKET, "poly-a")),
            ReferenceSpec(InstrumentKey(ReplaySource.POLYMARKET, "poly-b")),
        ),
        min_active_references=2,
        target_horizons=(timedelta(seconds=1),),
        event_windows=(
            EventWindow(base + timedelta(milliseconds=500), base + timedelta(seconds=2)),
        ),
    )
    events = (
        _quote(base, ReplaySource.POLYMARKET, "poly-a", "0.49", "0.51", 1),
        _quote(base, ReplaySource.POLYMARKET, "poly-b", "0.49", "0.51", 1),
        _quote(base, ReplaySource.SIG, "sig-1", "0.49", "0.51", 1),
        _trust(base),
        _quote(base + timedelta(seconds=1), ReplaySource.POLYMARKET, "poly-a", "0.59", "0.61", 2),
        _quote(base + timedelta(seconds=1), ReplaySource.POLYMARKET, "poly-b", "0.59", "0.61", 2),
        _quote(base + timedelta(seconds=2), ReplaySource.SIG, "sig-1", "0.55", "0.57", 2),
    )
    rows = RelationshipExperimentRunner().run(events, (spec,))
    signal = next(row for row in rows if row.signal_direction is not None)
    assert signal.signal_value == Decimal("0.20")
    assert signal.features.number_active_references == 2
    assert signal.regime is Regime.EVENT


def test_complement_relationship_flips_impulse_direction() -> None:
    base = datetime(2026, 9, 25, 12, tzinfo=UTC)
    spec = _leadlag_spec(
        references=(
            ReferenceSpec(
                InstrumentKey(ReplaySource.POLYMARKET, "poly-1"),
                relationship=RelationshipDirection.COMPLEMENT,
                leakage_class=LeakageClass.COMPLEMENT,
            ),
        ),
        target_horizons=(timedelta(seconds=1),),
    )
    events = (
        _quote(base, ReplaySource.POLYMARKET, "poly-1", "0.39", "0.41", 1),
        _quote(base, ReplaySource.SIG, "sig-1", "0.59", "0.61", 1),
        _trust(base),
        _quote(base + timedelta(seconds=1), ReplaySource.POLYMARKET, "poly-1", "0.49", "0.51", 2),
        _quote(base + timedelta(seconds=2), ReplaySource.SIG, "sig-1", "0.53", "0.55", 2),
    )
    rows = RelationshipExperimentRunner().run(events, (spec,))
    signal = next(row for row in rows if row.signal_direction is not None)
    assert signal.signal_direction is Direction.SELL_YES
    assert signal.signal_value == Decimal("-0.10")


def test_complement_bid_and_ask_are_side_aware() -> None:
    base = datetime(2026, 9, 25, 12, tzinfo=UTC)
    target = InstrumentKey(ReplaySource.SIG, "sig-1")
    complement = ReferenceSpec(
        InstrumentKey(ReplaySource.POLYMARKET, "poly-1"),
        relationship=RelationshipDirection.COMPLEMENT,
        leakage_class=LeakageClass.COMPLEMENT,
    )
    bid_spec = RelationshipExperimentSpec(
        id="RV-COMPLEMENT-BID",
        dataset_id="synthetic",
        family=ExperimentFamily.RV,
        target=target,
        references=(complement,),
        threshold=Decimal("0.05"),
        coordinate=PriceCoordinate.PROBABILITY,
        estimator=ReferenceEstimator.WEIGHTED_PROBABILITY,
        reference_price_mode=ReferencePriceMode.BID,
        target_horizons=(timedelta(seconds=1),),
        reference_freshness=timedelta(seconds=10),
    )
    ask_spec = RelationshipExperimentSpec(
        id="RV-COMPLEMENT-ASK",
        dataset_id="synthetic",
        family=ExperimentFamily.RV,
        target=target,
        references=(complement,),
        threshold=Decimal("0.05"),
        coordinate=PriceCoordinate.PROBABILITY,
        estimator=ReferenceEstimator.WEIGHTED_PROBABILITY,
        reference_price_mode=ReferencePriceMode.ASK,
        target_horizons=(timedelta(seconds=1),),
        reference_freshness=timedelta(seconds=10),
    )
    events = (
        _quote(base, ReplaySource.POLYMARKET, "poly-1", "0.20", "0.30", 1),
        _quote(base, ReplaySource.SIG, "sig-1", "0.59", "0.61", 1),
        _trust(base),
        _quote(base + timedelta(seconds=1), ReplaySource.SIG, "sig-1", "0.64", "0.66", 2),
    )

    rows = RelationshipExperimentRunner().run(events, (bid_spec, ask_spec))
    bid_row = next(
        row
        for row in rows
        if row.experiment_id == "RV-COMPLEMENT-BID" and row.signal_direction is not None
    )
    ask_row = next(
        row
        for row in rows
        if row.experiment_id == "RV-COMPLEMENT-ASK" and row.signal_direction is not None
    )

    assert bid_row.features.reference_residual_probability == Decimal("-0.10")
    assert ask_row.features.reference_residual_probability == Decimal("-0.20")
    assert bid_row.signal_direction is Direction.BUY_YES
    assert ask_row.signal_direction is Direction.BUY_YES


def test_last_trade_mode_uses_trade_timestamp_not_quote_timestamp() -> None:
    base = datetime(2026, 9, 25, 12, tzinfo=UTC)
    spec = RelationshipExperimentSpec(
        id="RV-LAST-TRADE",
        dataset_id="synthetic",
        family=ExperimentFamily.RV,
        target=InstrumentKey(ReplaySource.SIG, "sig-1"),
        references=(ReferenceSpec(InstrumentKey(ReplaySource.POLYMARKET, "poly-1")),),
        threshold=Decimal("0.05"),
        coordinate=PriceCoordinate.PROBABILITY,
        estimator=ReferenceEstimator.WEIGHTED_PROBABILITY,
        reference_price_mode=ReferencePriceMode.LAST_TRADE,
        target_horizons=(timedelta(seconds=1),),
        reference_freshness=timedelta(seconds=2),
    )

    fresh_trade_only = (
        _quote(base, ReplaySource.SIG, "sig-1", "0.49", "0.51", 1),
        _trust(base),
        _trade(base + timedelta(seconds=1), ReplaySource.POLYMARKET, "poly-1", "0.70", 1),
        _quote(base + timedelta(seconds=2), ReplaySource.SIG, "sig-1", "0.55", "0.57", 2),
    )
    fresh_rows = RelationshipExperimentRunner().run(fresh_trade_only, (spec,))
    fresh_signal = next(row for row in fresh_rows if row.signal_direction is not None)
    assert fresh_signal.signal_direction is Direction.BUY_YES
    assert fresh_signal.features.reference_residual_probability == Decimal("-0.20")
    assert fresh_signal.gross_markout == Decimal("0.04")

    stale_trade_with_fresh_quote = (
        _trade(base, ReplaySource.POLYMARKET, "poly-1", "0.70", 1),
        _quote(
            base + timedelta(seconds=10),
            ReplaySource.POLYMARKET,
            "poly-1",
            "0.69",
            "0.71",
            2,
        ),
        _quote(base + timedelta(seconds=10), ReplaySource.SIG, "sig-1", "0.49", "0.51", 1),
        _trust(base + timedelta(seconds=10)),
    )
    stale_rows = RelationshipExperimentRunner().run(stale_trade_with_fresh_quote, (spec,))
    assert any(
        row.invalid_reason is InvalidReason.EXTERNAL_STALE
        for row in stale_rows
        if row.decision_time == base + timedelta(seconds=10)
    )


def test_serialization_is_deterministic() -> None:
    base = datetime(2026, 9, 25, 12, tzinfo=UTC)
    events = (
        _quote(base, ReplaySource.POLYMARKET, "poly-1", "0.49", "0.51", 1),
        _quote(base, ReplaySource.SIG, "sig-1", "0.49", "0.51", 1),
        _trust(base),
        _quote(base + timedelta(seconds=1), ReplaySource.POLYMARKET, "poly-1", "0.59", "0.61", 2),
        _quote(base + timedelta(seconds=3), ReplaySource.SIG, "sig-1", "0.57", "0.59", 2),
    )
    spec = _leadlag_spec(target_horizons=(timedelta(seconds=2),))
    runner = RelationshipExperimentRunner()
    first = runner.run(events, (spec,))
    second = runner.run(tuple(reversed(events)), (spec,))
    assert serialize_relationship_observations(first) == serialize_relationship_observations(second)
    reversed_bytes = serialize_relationship_observations(reversed(first))
    assert reversed_bytes == serialize_relationship_observations(first)
