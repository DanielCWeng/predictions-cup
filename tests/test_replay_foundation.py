from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from predictions_cup.external.polymarket.health import IngestionHealth
from predictions_cup.external.polymarket.models import (
    BookChangeEvent,
    BookLevel as PolymarketBookLevel,
    BookSnapshot,
    TradeEvent,
)
from predictions_cup.external.polymarket.storage import PolymarketStorage
from predictions_cup.learning.evaluation import summarize
from predictions_cup.learning.experiments import (
    ExperimentObservation,
    ExperimentRunner,
    ExperimentSpec,
    InstrumentPair,
    serialize_observations,
)
from predictions_cup.learning.splits import split_observations
from predictions_cup.models import OrderBook, OrderBookLevel
from predictions_cup.replay.loaders import (
    CaptureSchemaError,
    load_polymarket_capture,
    load_sig_capture,
    summarize_captures,
)
from predictions_cup.replay.markouts import Direction, evaluate_markout
from predictions_cup.replay.model import (
    HealthPayload,
    InstrumentView,
    InvalidReason,
    QuotePayload,
    ReplayEvent,
    ReplayEventType,
    ReplaySource,
    ReplayState,
    TrustPayload,
)
from predictions_cup.replay.runner import ReplayFrame, ReplayRunner
from predictions_cup.replay.splits import ChronologicalBoundaries
from predictions_cup.sig.realtime_models import RealtimeTradeDto
from predictions_cup.sig.realtime_storage import SigRealtimeRecorder


def _quote_event(
    *,
    observed_at: datetime,
    source: ReplaySource,
    instrument: str,
    bid: str,
    ask: str,
    sequence: int,
    source_at: datetime | None = None,
    quote_observed_at: datetime | None = None,
) -> ReplayEvent:
    return ReplayEvent(
        observed_at=observed_at,
        source_at=source_at,
        source=source,
        event_type=ReplayEventType.BOOK_OBSERVATION,
        instrument_id=instrument,
        market_id="market-1",
        sequence=sequence,
        payload=QuotePayload(
            best_bid=Decimal(bid),
            best_ask=Decimal(ask),
            quote_observed_at=quote_observed_at or observed_at,
        ),
    )


def _trust_event(at: datetime, trusted: bool, sequence: int = 1) -> ReplayEvent:
    return ReplayEvent(
        observed_at=at,
        source_at=None,
        source=ReplaySource.SIG,
        event_type=ReplayEventType.TRUST,
        instrument_id="sig-1",
        market_id=None,
        sequence=sequence,
        payload=TrustPayload(
            trusted=trusted,
            transition="TRUSTED" if trusted else "UNTRUSTED_REVISION_GAP",
        ),
    )


def _view(*, bid: str, ask: str) -> InstrumentView:
    at = datetime(2026, 9, 25, 12, tzinfo=UTC)
    return InstrumentView(
        source=ReplaySource.SIG,
        instrument_id="sig-1",
        market_id="market-1",
        best_bid=Decimal(bid),
        best_ask=Decimal(ask),
        bids=(),
        asks=(),
        last_trade=None,
        quote_observed_at=at,
        last_event_observed_at=at,
        trusted=True,
        book_valid=True,
        source_available=True,
    )


def test_replay_orders_by_observed_time_not_source_time_and_ties_stably() -> None:
    base = datetime(2026, 9, 25, 12, tzinfo=UTC)
    future_stamped = _quote_event(
        observed_at=base,
        source=ReplaySource.POLYMARKET,
        instrument="poly-1",
        bid="0.40",
        ask="0.41",
        sequence=2,
        source_at=base + timedelta(hours=1),
    )
    later_observed = _quote_event(
        observed_at=base + timedelta(seconds=1),
        source=ReplaySource.SIG,
        instrument="sig-1",
        bid="0.50",
        ask="0.52",
        sequence=1,
        source_at=base - timedelta(hours=1),
    )
    tie_a = _quote_event(
        observed_at=base,
        source=ReplaySource.POLYMARKET,
        instrument="poly-1",
        bid="0.39",
        ask="0.40",
        sequence=1,
    )

    one = ReplayRunner((later_observed, future_stamped, tie_a)).events
    two = ReplayRunner((tie_a, later_observed, future_stamped)).events

    assert one == two
    assert one[0] is tie_a
    assert one[1] is future_stamped
    assert one[2] is later_observed


def test_replay_exposes_no_future_state_early() -> None:
    base = datetime(2026, 9, 25, 12, tzinfo=UTC)
    initial = _quote_event(
        observed_at=base,
        source=ReplaySource.SIG,
        instrument="sig-1",
        bid="0.50",
        ask="0.52",
        sequence=1,
    )
    later = _quote_event(
        observed_at=base + timedelta(seconds=5),
        source=ReplaySource.SIG,
        instrument="sig-1",
        bid="0.58",
        ask="0.60",
        sequence=2,
        source_at=base - timedelta(seconds=10),
    )
    seen: list[tuple[datetime, Decimal | None]] = []

    def on_frame(frame: ReplayFrame) -> None:
        view = frame.state.view(ReplaySource.SIG, "sig-1")
        seen.append((frame.observed_at, None if view is None else view.best_ask))

    ReplayRunner((later, initial)).run(on_frame)
    assert seen == [
        (base, Decimal("0.52")),
        (base + timedelta(seconds=5), Decimal("0.60")),
    ]


def test_state_gates_untrusted_and_stale_quotes() -> None:
    base = datetime(2026, 9, 25, 12, tzinfo=UTC)
    state = ReplayState()
    state.apply(
        _quote_event(
            observed_at=base,
            source=ReplaySource.SIG,
            instrument="sig-1",
            bid="0.50",
            ask="0.52",
            sequence=1,
        )
    )
    _, reason = state.quote_status(
        source=ReplaySource.SIG,
        instrument_id="sig-1",
        at=base,
        max_age=None,
    )
    assert reason is InvalidReason.SIG_UNTRUSTED

    state.apply(_trust_event(base, True, sequence=2))
    _, reason = state.quote_status(
        source=ReplaySource.SIG,
        instrument_id="sig-1",
        at=base,
        max_age=None,
    )
    assert reason is None

    external = _quote_event(
        observed_at=base + timedelta(seconds=10),
        source=ReplaySource.POLYMARKET,
        instrument="poly-1",
        bid="0.40",
        ask="0.41",
        sequence=1,
        quote_observed_at=base,
    )
    state.apply(external)
    _, reason = state.quote_status(
        source=ReplaySource.POLYMARKET,
        instrument_id="poly-1",
        at=base + timedelta(seconds=10),
        max_age=timedelta(seconds=5),
    )
    assert reason is InvalidReason.EXTERNAL_STALE

    state.apply(
        ReplayEvent(
            observed_at=base + timedelta(seconds=11),
            source_at=None,
            source=ReplaySource.POLYMARKET,
            event_type=ReplayEventType.HEALTH,
            instrument_id="*",
            market_id=None,
            sequence=1,
            payload=HealthPayload(available=False, detail="disconnect"),
        )
    )
    _, reason = state.quote_status(
        source=ReplaySource.POLYMARKET,
        instrument_id="poly-1",
        at=base + timedelta(seconds=11),
        max_age=None,
    )
    assert reason is InvalidReason.DATA_GAP


def test_executable_markouts_cross_bid_ask_in_both_directions() -> None:
    entry = _view(bid="0.50", ask="0.52")
    future = _view(bid="0.58", ask="0.60")

    bullish = evaluate_markout(
        entry_quote=entry,
        future_quote=future,
        direction=Direction.BUY_YES,
    )
    opposite = evaluate_markout(
        entry_quote=entry,
        future_quote=future,
        direction=Direction.SELL_YES,
    )

    assert bullish is not None
    assert bullish.entry_price == Decimal("0.52")
    assert bullish.future_price == Decimal("0.58")
    assert bullish.gross == Decimal("0.06")
    assert opposite is not None
    assert opposite.entry_price == Decimal("0.50")
    assert opposite.future_price == Decimal("0.60")
    assert opposite.gross == Decimal("-0.10")


def test_synthetic_end_to_end_lead_lag_is_deterministic() -> None:
    base = datetime(2026, 9, 25, 12, tzinfo=UTC)
    events = (
        _quote_event(
            observed_at=base,
            source=ReplaySource.SIG,
            instrument="sig-1",
            bid="0.50",
            ask="0.52",
            sequence=1,
        ),
        _trust_event(base, True, sequence=2),
        _quote_event(
            observed_at=base,
            source=ReplaySource.POLYMARKET,
            instrument="poly-1",
            bid="0.40",
            ask="0.41",
            sequence=1,
        ),
        _quote_event(
            observed_at=base + timedelta(seconds=1),
            source=ReplaySource.POLYMARKET,
            instrument="poly-1",
            bid="0.55",
            ask="0.56",
            sequence=2,
        ),
        _quote_event(
            observed_at=base + timedelta(seconds=6),
            source=ReplaySource.SIG,
            instrument="sig-1",
            bid="0.58",
            ask="0.60",
            sequence=3,
            source_at=base - timedelta(seconds=30),
        ),
    )

    def features(
        frame: ReplayFrame, pair: InstrumentPair
    ) -> dict[str, Decimal | int | str | bool | None]:
        sig = frame.state.view(ReplaySource.SIG, pair.sig_instrument_id)
        external = frame.state.view(pair.external_source, pair.external_instrument_id)
        assert sig is not None and external is not None
        assert sig.midpoint is not None and external.midpoint is not None
        return {"external_minus_sig": external.midpoint - sig.midpoint}

    def signal(
        feature_map: Mapping[str, Decimal | int | str | bool | None],
        parameters: Mapping[str, Decimal | int | str | bool],
    ) -> Direction | None:
        value = feature_map["external_minus_sig"]
        threshold = parameters["threshold"]
        assert isinstance(value, Decimal)
        assert isinstance(threshold, Decimal)
        return Direction.BUY_YES if value > threshold else None

    spec = ExperimentSpec(
        id="LEADLAG-TOY",
        description="Synthetic external impulse followed by SIG repricing.",
        required_inputs=("external_quote", "sig_quote", "sig_trust"),
        parameters={"threshold": Decimal("0.02")},
        pairs=(InstrumentPair("sig-1", "poly-1", market_id="market-1"),),
        feature_builder=features,
        signal_rule=signal,
        target_horizons=(timedelta(seconds=5),),
        external_freshness=timedelta(seconds=2),
    )

    first = ExperimentRunner().run(events, spec)
    second = ExperimentRunner().run(tuple(reversed(events)), spec)

    assert len(first) == 1
    assert first[0].valid is True
    assert first[0].entry_executable_price == Decimal("0.52")
    assert first[0].future_executable_price == Decimal("0.58")
    assert first[0].gross_markout == Decimal("0.06")
    assert serialize_observations(first) == serialize_observations(second)


def test_missing_future_target_and_untrusted_future_are_explicit() -> None:
    base = datetime(2026, 9, 25, 12, tzinfo=UTC)

    def features(
        frame: ReplayFrame, pair: InstrumentPair
    ) -> dict[str, Decimal | int | str | bool | None]:
        del frame, pair
        return {"signal": True}

    def always_buy(
        feature_map: Mapping[str, Decimal | int | str | bool | None],
        parameters: Mapping[str, Decimal | int | str | bool],
    ) -> Direction:
        del feature_map, parameters
        return Direction.BUY_YES

    spec = ExperimentSpec(
        id="TARGET-VALIDITY",
        description="Target validity regression.",
        required_inputs=("sig_quote", "external_quote"),
        parameters={},
        pairs=(InstrumentPair("sig-1", "poly-1"),),
        feature_builder=features,
        signal_rule=always_buy,
        target_horizons=(timedelta(seconds=5),),
    )
    base_events = (
        _quote_event(
            observed_at=base,
            source=ReplaySource.SIG,
            instrument="sig-1",
            bid="0.50",
            ask="0.52",
            sequence=1,
        ),
        _trust_event(base, True, sequence=2),
        _quote_event(
            observed_at=base,
            source=ReplaySource.POLYMARKET,
            instrument="poly-1",
            bid="0.55",
            ask="0.56",
            sequence=1,
        ),
    )
    dataset_end = ExperimentRunner().run(base_events, spec)
    assert dataset_end[0].invalid_reason is InvalidReason.DATASET_END

    untrusted = ExperimentRunner().run(
        base_events
        + (
            _trust_event(base + timedelta(seconds=4), False, sequence=3),
            _quote_event(
                observed_at=base + timedelta(seconds=6),
                source=ReplaySource.POLYMARKET,
                instrument="other",
                bid="0.1",
                ask="0.2",
                sequence=4,
            ),
        ),
        spec,
    )
    assert untrusted[0].invalid_reason is InvalidReason.SIG_UNTRUSTED


def test_chronological_split_never_shuffles_time() -> None:
    base = datetime(2026, 9, 25, 12, tzinfo=UTC)
    template = ExperimentObservation(
        experiment_id="X",
        decision_at=base,
        instrument="sig-1",
        market_id=None,
        features={},
        signal="BUY_YES",
        direction=Direction.BUY_YES,
        entry_executable_price=Decimal("0.5"),
        target_horizon=timedelta(seconds=1),
        future_executable_price=Decimal("0.6"),
        gross_markout=Decimal("0.1"),
        midpoint_markout=Decimal("0.1"),
        estimated_cost=Decimal("0"),
        net_markout=Decimal("0.1"),
        valid=True,
        invalid_reason=None,
    )
    observations = (
        replace(template, decision_at=base + timedelta(hours=2)),
        replace(template, decision_at=base),
        replace(template, decision_at=base + timedelta(hours=1)),
    )
    split = split_observations(
        observations,
        ChronologicalBoundaries(
            train_end=base + timedelta(minutes=30),
            development_end=base + timedelta(hours=1, minutes=30),
        ),
    )
    assert [item.decision_at for item in split.train] == [base]
    assert [item.decision_at for item in split.development] == [
        base + timedelta(hours=1)
    ]
    assert [item.decision_at for item in split.holdout] == [
        base + timedelta(hours=2)
    ]
    assert summarize(observations).count == 3


def _write_real_capture_fixtures(tmp_path: Path) -> tuple[Path, Path]:
    at = datetime(2026, 9, 25, 12, tzinfo=UTC)
    sig_path = tmp_path / "sig.sqlite3"
    sig = SigRealtimeRecorder(sig_path)
    sig_book = OrderBook(
        exchange_id="sig-1",
        bids=(OrderBookLevel(price=Decimal("0.50"), quantity=Decimal("10")),),
        asks=(OrderBookLevel(price=Decimal("0.52"), quantity=Decimal("12")),),
        timestamp=at - timedelta(milliseconds=10),
        source="sig-rest",
        revision=None,
    )
    sig.record_book(
        market_id="sig-market",
        tournament_id="cup",
        book=sig_book,
        observed_at=at,
        reason="test",
        triggering_revision=1,
    )
    sig.record_transition(
        topic="tournament:cup",
        exchange_id="sig-1",
        transition="TRUSTED_AFTER_RECONCILIATION",
        observed_at=at,
        revision=1,
    )
    trade = RealtimeTradeDto.model_validate(
        {
            "exchangeId": "sig-1",
            "marketId": "sig-market",
            "price": "0.51",
            "quantity": "2",
            "executedAt": (at + timedelta(milliseconds=5)).isoformat(),
            "tournamentId": "cup",
        }
    )
    sig.record_trade(
        topic="tournament:cup",
        revision=2,
        trade=trade,
        observed_at=at + timedelta(milliseconds=20),
    )
    sig.close()

    poly_path = tmp_path / "polymarket.sqlite3"
    storage = PolymarketStorage(poly_path)
    storage.initialize()
    poly_book = BookSnapshot(
        market_id="poly-market",
        token_id="poly-1",
        source_timestamp=at - timedelta(milliseconds=20),
        observed_at=at,
        bids=(PolymarketBookLevel(Decimal("0.45"), Decimal("10")),),
        asks=(PolymarketBookLevel(Decimal("0.46"), Decimal("12")),),
        last_trade_price=Decimal("0.455"),
    )
    storage.append_observations((poly_book,), at.isoformat())
    storage.append_snapshots((poly_book,), at.isoformat())
    storage.append_book_changes(
        (
            BookChangeEvent(
                market_id="poly-market",
                token_id="poly-1",
                side="BUY",
                price=Decimal("0.45"),
                size=Decimal("10"),
                source_timestamp=at,
                observed_at=at + timedelta(milliseconds=25),
                best_bid=Decimal("0.45"),
                best_ask=Decimal("0.46"),
                book_hash="hash-1",
            ),
        )
    )
    storage.append_trade(
        TradeEvent(
            market_id="poly-market",
            token_id="poly-1",
            price=Decimal("0.455"),
            size=Decimal("1"),
            side="BUY",
            source_timestamp=at,
            observed_at=at + timedelta(milliseconds=30),
            transaction_hash="tx-1",
            fee_rate_bps=None,
        )
    )
    storage.append_health(
        IngestionHealth(websocket_connected=True),
        (at + timedelta(milliseconds=40)).isoformat(),
    )
    return sig_path, poly_path


def test_actual_accepted_sqlite_loaders_and_offline_summary(tmp_path: Path) -> None:
    sig_path, poly_path = _write_real_capture_fixtures(tmp_path)
    sig_events = load_sig_capture(sig_path)
    poly_events = load_polymarket_capture(poly_path)

    assert {event.event_type for event in sig_events} >= {
        ReplayEventType.BOOK_OBSERVATION,
        ReplayEventType.TRADE,
        ReplayEventType.TRUST,
    }
    assert {event.event_type for event in poly_events} >= {
        ReplayEventType.BOOK_OBSERVATION,
        ReplayEventType.BOOK_CHANGE,
        ReplayEventType.DEPTH_SNAPSHOT,
        ReplayEventType.TRADE,
        ReplayEventType.HEALTH,
    }
    summary = summarize_captures(sig_path=sig_path, polymarket_path=poly_path)
    assert summary.records_loaded == len(sig_events) + len(poly_events)
    assert summary.instruments == ("polymarket:poly-1", "sig:sig-1")
    assert summary.trusted_sig_observations == 1
    assert summary.external_observations == 3


def test_malformed_capture_schema_fails_clearly(tmp_path: Path) -> None:
    malformed = tmp_path / "bad.sqlite3"
    with sqlite3.connect(malformed) as connection:
        connection.execute("CREATE TABLE book_observations (id INTEGER PRIMARY KEY)")

    with pytest.raises(CaptureSchemaError, match="missing required tables"):
        load_sig_capture(malformed)
    with pytest.raises(CaptureSchemaError, match="missing required tables"):
        load_polymarket_capture(malformed)
