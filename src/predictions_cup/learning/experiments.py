"""Small generic experiment contract over deterministic replay."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from predictions_cup.replay.markouts import Direction, evaluate_markout, executable_entry
from predictions_cup.replay.model import (
    InstrumentView,
    InvalidReason,
    ReplayEvent,
    ReplaySource,
    ReplayState,
)
from predictions_cup.replay.runner import ReplayFrame, ReplayRunner

FeatureValue = Decimal | int | str | bool | None
ParameterValue = Decimal | int | str | bool
FeatureMap = dict[str, FeatureValue]
FeatureBuilder = Callable[[ReplayFrame, "InstrumentPair"], FeatureMap]
SignalRule = Callable[[Mapping[str, FeatureValue], Mapping[str, ParameterValue]], Direction | None]

DEFAULT_MARKOUT_HORIZONS = (
    timedelta(seconds=1),
    timedelta(seconds=5),
    timedelta(seconds=30),
    timedelta(minutes=1),
    timedelta(minutes=5),
)


@dataclass(frozen=True, slots=True)
class InstrumentPair:
    sig_instrument_id: str
    external_instrument_id: str
    external_source: ReplaySource = ReplaySource.POLYMARKET
    market_id: str | None = None

    def __post_init__(self) -> None:
        if not self.sig_instrument_id or not self.external_instrument_id:
            raise ValueError("instrument pairing must contain both SIG and external identifiers")
        if self.external_source is ReplaySource.SIG:
            raise ValueError("external_source must not be SIG")


@dataclass(frozen=True, slots=True)
class ExperimentSpec:
    id: str
    description: str
    required_inputs: tuple[str, ...]
    parameters: Mapping[str, ParameterValue]
    pairs: tuple[InstrumentPair, ...]
    feature_builder: FeatureBuilder
    signal_rule: SignalRule
    target_horizons: tuple[timedelta, ...] = DEFAULT_MARKOUT_HORIZONS
    external_freshness: timedelta = timedelta(seconds=5)
    sig_freshness: timedelta | None = None
    estimated_cost_per_share: Decimal = Decimal("0")

    def __post_init__(self) -> None:
        if not self.id or not self.description:
            raise ValueError("experiment id and description must be non-blank")
        if not self.pairs:
            raise ValueError("experiment requires an explicit instrument pair specification")
        if not self.target_horizons or any(
            horizon <= timedelta(0) for horizon in self.target_horizons
        ):
            raise ValueError("target horizons must be positive")
        if self.external_freshness <= timedelta(0):
            raise ValueError("external freshness must be positive")
        if self.sig_freshness is not None and self.sig_freshness <= timedelta(0):
            raise ValueError("SIG freshness must be positive when configured")
        if self.estimated_cost_per_share < 0:
            raise ValueError("estimated cost must not be negative")


@dataclass(frozen=True, slots=True)
class ExperimentObservation:
    experiment_id: str
    decision_at: datetime
    instrument: str
    market_id: str | None
    features: Mapping[str, FeatureValue]
    signal: str | None
    direction: Direction | None
    entry_executable_price: Decimal | None
    target_horizon: timedelta
    future_executable_price: Decimal | None
    gross_markout: Decimal | None
    midpoint_markout: Decimal | None
    estimated_cost: Decimal | None
    net_markout: Decimal | None
    valid: bool
    invalid_reason: InvalidReason | None

    def as_record(self) -> dict[str, object]:
        return {
            "experiment_id": self.experiment_id,
            "decision_at": self.decision_at.isoformat(),
            "instrument": self.instrument,
            "market_id": self.market_id,
            "features": {
                key: _json_value(value) for key, value in sorted(self.features.items())
            },
            "signal": self.signal,
            "direction": None if self.direction is None else self.direction.value,
            "entry_executable_price": _decimal_text(self.entry_executable_price),
            "target_horizon_seconds": _timedelta_seconds(self.target_horizon),
            "future_executable_price": _decimal_text(self.future_executable_price),
            "gross_markout": _decimal_text(self.gross_markout),
            "midpoint_markout": _decimal_text(self.midpoint_markout),
            "estimated_cost": _decimal_text(self.estimated_cost),
            "net_markout": _decimal_text(self.net_markout),
            "valid": self.valid,
            "invalid_reason": (
                None if self.invalid_reason is None else self.invalid_reason.value
            ),
        }


@dataclass(slots=True)
class _PendingMarkout:
    spec: ExperimentSpec
    pair: InstrumentPair
    decision_at: datetime
    target_at: datetime
    horizon: timedelta
    features: FeatureMap
    direction: Direction
    entry_quote: InstrumentView
    entry_price: Decimal


class ExperimentRunner:
    """Single-pass experiment runner that resolves targets without looking past them."""

    def run(
        self,
        events: Iterable[ReplayEvent],
        spec: ExperimentSpec,
    ) -> tuple[ExperimentObservation, ...]:
        output: list[ExperimentObservation] = []
        pending: list[_PendingMarkout] = []
        replay = ReplayRunner(events)
        pair_by_external = {
            (pair.external_source, pair.external_instrument_id): pair for pair in spec.pairs
        }

        def resolve_before(next_at: datetime, state: ReplayState) -> None:
            self._resolve_pending(
                pending=pending,
                output=output,
                state=state,
                through=next_at,
                inclusive=False,
            )

        def on_frame(frame: ReplayFrame) -> None:
            self._resolve_pending(
                pending=pending,
                output=output,
                state=frame.state,
                through=frame.observed_at,
                inclusive=True,
            )
            touched = {
                (event.source, event.instrument_id)
                for event in frame.events
                if event.instrument_id != "*"
            }
            for key in sorted(touched, key=lambda item: (item[0].value, item[1])):
                pair = pair_by_external.get(key)
                if pair is None:
                    continue
                self._start_decision(
                    frame=frame,
                    spec=spec,
                    pair=pair,
                    pending=pending,
                    output=output,
                )

        replay.run(on_frame, before_frame=resolve_before)
        for item in sorted(pending, key=lambda pending_item: pending_item.target_at):
            output.append(
                _invalid_from_pending(item, InvalidReason.DATASET_END)
            )
        return tuple(output)

    def _start_decision(
        self,
        *,
        frame: ReplayFrame,
        spec: ExperimentSpec,
        pair: InstrumentPair,
        pending: list[_PendingMarkout],
        output: list[ExperimentObservation],
    ) -> None:
        external, external_reason = frame.state.quote_status(
            source=pair.external_source,
            instrument_id=pair.external_instrument_id,
            at=frame.observed_at,
            max_age=spec.external_freshness,
        )
        sig, sig_reason = frame.state.quote_status(
            source=ReplaySource.SIG,
            instrument_id=pair.sig_instrument_id,
            at=frame.observed_at,
            max_age=spec.sig_freshness,
        )
        reason = external_reason or sig_reason
        if reason is not None or external is None or sig is None:
            invalid_reason = reason or InvalidReason.NO_EXECUTABLE_START
            output.extend(
                _invalid_without_signal(
                    spec=spec,
                    pair=pair,
                    decision_at=frame.observed_at,
                    reason=invalid_reason,
                )
            )
            return

        features = dict(spec.feature_builder(frame, pair))
        direction = spec.signal_rule(features, spec.parameters)
        if direction is None:
            return
        entry = executable_entry(sig, direction)
        if entry is None:
            output.extend(
                _invalid_signal(
                    spec=spec,
                    pair=pair,
                    decision_at=frame.observed_at,
                    features=features,
                    direction=direction,
                    reason=InvalidReason.NO_EXECUTABLE_START,
                )
            )
            return

        for horizon in spec.target_horizons:
            pending.append(
                _PendingMarkout(
                    spec=spec,
                    pair=pair,
                    decision_at=frame.observed_at,
                    target_at=frame.observed_at + horizon,
                    horizon=horizon,
                    features=features,
                    direction=direction,
                    entry_quote=sig,
                    entry_price=entry,
                )
            )

    def _resolve_pending(
        self,
        *,
        pending: list[_PendingMarkout],
        output: list[ExperimentObservation],
        state: ReplayState,
        through: datetime,
        inclusive: bool,
    ) -> None:
        keep: list[_PendingMarkout] = []
        for item in pending:
            ready = item.target_at <= through if inclusive else item.target_at < through
            if not ready:
                keep.append(item)
                continue
            future, reason = state.quote_status(
                source=ReplaySource.SIG,
                instrument_id=item.pair.sig_instrument_id,
                at=item.target_at,
                max_age=item.spec.sig_freshness,
            )
            if reason is not None or future is None:
                mapped_reason = (
                    InvalidReason.NO_VALID_FUTURE_QUOTE
                    if reason is InvalidReason.NO_EXECUTABLE_START
                    else reason or InvalidReason.NO_VALID_FUTURE_QUOTE
                )
                output.append(_invalid_from_pending(item, mapped_reason))
                continue
            markout = evaluate_markout(
                entry_quote=item.entry_quote,
                future_quote=future,
                direction=item.direction,
            )
            if markout is None:
                output.append(
                    _invalid_from_pending(item, InvalidReason.NO_VALID_FUTURE_QUOTE)
                )
                continue
            cost = item.spec.estimated_cost_per_share
            output.append(
                ExperimentObservation(
                    experiment_id=item.spec.id,
                    decision_at=item.decision_at,
                    instrument=item.pair.sig_instrument_id,
                    market_id=item.pair.market_id,
                    features=item.features,
                    signal=item.direction.value,
                    direction=item.direction,
                    entry_executable_price=markout.entry_price,
                    target_horizon=item.horizon,
                    future_executable_price=markout.future_price,
                    gross_markout=markout.gross,
                    midpoint_markout=markout.midpoint,
                    estimated_cost=cost,
                    net_markout=markout.gross - cost,
                    valid=True,
                    invalid_reason=None,
                )
            )
        pending[:] = keep


def serialize_observations(observations: Iterable[ExperimentObservation]) -> bytes:
    records = [observation.as_record() for observation in observations]
    return (json.dumps(records, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _invalid_without_signal(
    *,
    spec: ExperimentSpec,
    pair: InstrumentPair,
    decision_at: datetime,
    reason: InvalidReason,
) -> list[ExperimentObservation]:
    return [
        ExperimentObservation(
            experiment_id=spec.id,
            decision_at=decision_at,
            instrument=pair.sig_instrument_id,
            market_id=pair.market_id,
            features={},
            signal=None,
            direction=None,
            entry_executable_price=None,
            target_horizon=horizon,
            future_executable_price=None,
            gross_markout=None,
            midpoint_markout=None,
            estimated_cost=None,
            net_markout=None,
            valid=False,
            invalid_reason=reason,
        )
        for horizon in spec.target_horizons
    ]


def _invalid_signal(
    *,
    spec: ExperimentSpec,
    pair: InstrumentPair,
    decision_at: datetime,
    features: FeatureMap,
    direction: Direction,
    reason: InvalidReason,
) -> list[ExperimentObservation]:
    return [
        ExperimentObservation(
            experiment_id=spec.id,
            decision_at=decision_at,
            instrument=pair.sig_instrument_id,
            market_id=pair.market_id,
            features=features,
            signal=direction.value,
            direction=direction,
            entry_executable_price=None,
            target_horizon=horizon,
            future_executable_price=None,
            gross_markout=None,
            midpoint_markout=None,
            estimated_cost=None,
            net_markout=None,
            valid=False,
            invalid_reason=reason,
        )
        for horizon in spec.target_horizons
    ]


def _invalid_from_pending(
    item: _PendingMarkout,
    reason: InvalidReason,
) -> ExperimentObservation:
    return ExperimentObservation(
        experiment_id=item.spec.id,
        decision_at=item.decision_at,
        instrument=item.pair.sig_instrument_id,
        market_id=item.pair.market_id,
        features=item.features,
        signal=item.direction.value,
        direction=item.direction,
        entry_executable_price=item.entry_price,
        target_horizon=item.horizon,
        future_executable_price=None,
        gross_markout=None,
        midpoint_markout=None,
        estimated_cost=item.spec.estimated_cost_per_share,
        net_markout=None,
        valid=False,
        invalid_reason=reason,
    )


def _decimal_text(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


def _timedelta_seconds(value: timedelta) -> str:
    whole_seconds = value.days * 86_400 + value.seconds
    exact = Decimal(whole_seconds) + Decimal(value.microseconds) / Decimal(1_000_000)
    return str(exact)


def _json_value(value: FeatureValue) -> object:
    if isinstance(value, Decimal):
        return str(value)
    return value
