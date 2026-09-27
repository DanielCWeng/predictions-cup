"""Executable lead/lag, relative-value and leave-one-out relationship experiments."""

from __future__ import annotations

import json
import random
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import ROUND_CEILING, Decimal
from enum import StrEnum

from predictions_cup.replay.markouts import Direction, evaluate_markout, executable_entry
from predictions_cup.replay.model import (
    InstrumentView,
    InvalidReason,
    ReplayEvent,
    ReplaySource,
    ReplayState,
)
from predictions_cup.replay.runner import ReplayFrame, ReplayRunner
from predictions_cup.replay.splits import ChronologicalBoundaries


class ExperimentFamily(StrEnum):
    LEADLAG = "LEADLAG"
    RV = "RV"
    LOO_PRICE = "LOO-PRICE"
    LOO_FAMILY = "LOO-FAMILY"


class PriceCoordinate(StrEnum):
    PROBABILITY = "PROBABILITY"
    LOGIT = "LOGIT"


class ReferencePriceMode(StrEnum):
    MIDPOINT = "MIDPOINT"
    MICROPRICE_IF_DEPTH_AVAILABLE = "MICROPRICE_IF_DEPTH_AVAILABLE"
    BID = "BID"
    ASK = "ASK"
    LAST_TRADE = "LAST_TRADE"


class RelationshipDirection(StrEnum):
    SAME = "SAME"
    COMPLEMENT = "COMPLEMENT"


class ReferenceEstimator(StrEnum):
    WEIGHTED_PROBABILITY = "WEIGHTED_PROBABILITY"
    WEIGHTED_LOGIT = "WEIGHTED_LOGIT"


class LeakageClass(StrEnum):
    INDIRECT = "INDIRECT"
    DIRECT_EQUIVALENT = "DIRECT_EQUIVALENT"
    COMPLEMENT = "COMPLEMENT"
    MECHANICAL_SIBLING = "MECHANICAL_SIBLING"


class Regime(StrEnum):
    NORMAL = "NORMAL"
    EVENT = "EVENT"


@dataclass(frozen=True, slots=True, order=True)
class InstrumentKey:
    source: ReplaySource
    instrument_id: str

    def __post_init__(self) -> None:
        if not self.instrument_id:
            raise ValueError("instrument_id must not be blank")

    @property
    def label(self) -> str:
        return f"{self.source.value}:{self.instrument_id}"


@dataclass(frozen=True, slots=True)
class ReferenceSpec:
    instrument: InstrumentKey
    weight: Decimal = Decimal("1")
    relationship: RelationshipDirection = RelationshipDirection.SAME
    leakage_class: LeakageClass = LeakageClass.INDIRECT

    def __post_init__(self) -> None:
        if self.weight <= 0:
            raise ValueError("reference weights must be positive")
        if (
            self.relationship is RelationshipDirection.COMPLEMENT
            and self.leakage_class is LeakageClass.INDIRECT
        ):
            raise ValueError(
                "complement references must be explicitly classified as leakage-sensitive"
            )


@dataclass(frozen=True, slots=True)
class EventWindow:
    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        _require_aware(self.start, "event window start")
        _require_aware(self.end, "event window end")
        if self.end <= self.start:
            raise ValueError("event window end must be after start")

    def contains(self, value: datetime) -> bool:
        return self.start <= value < self.end


DepthCost = Callable[[InstrumentView, Direction], Decimal | None]


DEFAULT_RESPONSE_HORIZONS = (
    timedelta(seconds=1),
    timedelta(seconds=2),
    timedelta(seconds=5),
    timedelta(seconds=10),
    timedelta(seconds=30),
    timedelta(minutes=1),
    timedelta(minutes=5),
)


@dataclass(frozen=True, slots=True)
class RelationshipExperimentSpec:
    id: str
    dataset_id: str
    family: ExperimentFamily
    target: InstrumentKey
    references: tuple[ReferenceSpec, ...]
    threshold: Decimal
    lookback: timedelta = timedelta(seconds=1)
    cooldown: timedelta = timedelta(0)
    coordinate: PriceCoordinate = PriceCoordinate.LOGIT
    reference_price_mode: ReferencePriceMode = ReferencePriceMode.MIDPOINT
    target_price_mode: ReferencePriceMode = ReferencePriceMode.MIDPOINT
    estimator: ReferenceEstimator = ReferenceEstimator.WEIGHTED_LOGIT
    target_horizons: tuple[timedelta, ...] = DEFAULT_RESPONSE_HORIZONS
    reference_freshness: timedelta = timedelta(seconds=5)
    target_freshness: timedelta | None = None
    min_active_references: int = 1
    clamp_epsilon: Decimal = Decimal("0.000001")
    direct_family: tuple[InstrumentKey, ...] = ()
    excluded_family: tuple[InstrumentKey, ...] = ()
    event_windows: tuple[EventWindow, ...] = ()
    fee_per_share: Decimal | None = None
    latency_adjustment: Decimal | None = None
    depth_cost: DepthCost | None = None

    def __post_init__(self) -> None:
        if not self.id or not self.dataset_id:
            raise ValueError("experiment id and dataset id must be non-blank")
        if not self.references:
            raise ValueError("at least one explicit reference is required")
        if self.threshold <= 0:
            raise ValueError("threshold must be positive")
        if self.lookback <= timedelta(0):
            raise ValueError("lookback must be positive")
        if self.cooldown < timedelta(0):
            raise ValueError("cooldown must not be negative")
        if not self.target_horizons or any(h <= timedelta(0) for h in self.target_horizons):
            raise ValueError("target horizons must be positive")
        if self.reference_freshness <= timedelta(0):
            raise ValueError("reference freshness must be positive")
        if self.target_freshness is not None and self.target_freshness <= timedelta(0):
            raise ValueError("target freshness must be positive when configured")
        if self.min_active_references < 1 or self.min_active_references > len(self.references):
            raise ValueError("min_active_references must be within configured reference count")
        if not (Decimal("0") < self.clamp_epsilon < Decimal("0.5")):
            raise ValueError("clamp_epsilon must be between zero and 0.5")
        if self.fee_per_share is not None and self.fee_per_share < 0:
            raise ValueError("fee_per_share must not be negative")
        if self.latency_adjustment is not None and self.latency_adjustment < 0:
            raise ValueError("latency_adjustment must not be negative")
        if any(ref.instrument == self.target for ref in self.references):
            raise ValueError("target quote cannot be part of its predictor set")
        if self.family is ExperimentFamily.LOO_FAMILY:
            banned = set(self.direct_family) | set(self.excluded_family) | {self.target}
            for ref in self.references:
                if ref.instrument in banned or ref.leakage_class is not LeakageClass.INDIRECT:
                    raise ValueError(
                        "LOO-FAMILY predictor set contains direct/complement/mechanical leakage"
                    )


@dataclass(frozen=True, slots=True)
class FeatureVector:
    price_movement: Decimal | None
    logit_movement: Decimal | None
    spread: Decimal | None
    depth: Decimal | None
    quote_age_seconds: Decimal | None
    reference_residual_probability: Decimal | None
    reference_residual_logit: Decimal | None
    number_active_references: int
    logit_clamp_epsilon: Decimal

    def as_mapping(self) -> dict[str, object]:
        return {
            "price_movement": _decimal_text(self.price_movement),
            "logit_movement": _decimal_text(self.logit_movement),
            "spread": _decimal_text(self.spread),
            "depth": _decimal_text(self.depth),
            "quote_age_seconds": _decimal_text(self.quote_age_seconds),
            "reference_residual_probability": _decimal_text(self.reference_residual_probability),
            "reference_residual_logit": _decimal_text(self.reference_residual_logit),
            "number_active_references": self.number_active_references,
            "logit_clamp_epsilon": str(self.logit_clamp_epsilon),
        }


@dataclass(frozen=True, slots=True)
class RelationshipObservation:
    experiment_id: str
    dataset_id: str
    target: str
    reference_set: tuple[str, ...]
    excluded_instruments: tuple[str, ...]
    decision_time: datetime
    regime: Regime
    signal_type: str
    signal_direction: Direction | None
    signal_value: Decimal | None
    threshold: Decimal
    features: FeatureVector
    target_bid: Decimal | None
    target_ask: Decimal | None
    entry_price: Decimal | None
    horizon: timedelta
    future_bid: Decimal | None
    future_ask: Decimal | None
    gross_markout: Decimal | None
    net_markout: Decimal | None
    target_mid_change: Decimal | None
    target_logit_change: Decimal | None
    valid: bool
    invalid_reason: InvalidReason | None

    def as_record(self) -> dict[str, object]:
        return {
            "experiment_id": self.experiment_id,
            "dataset_id": self.dataset_id,
            "target": self.target,
            "reference_set": list(self.reference_set),
            "excluded_instruments": list(self.excluded_instruments),
            "decision_time": self.decision_time.isoformat(),
            "regime": self.regime.value,
            "signal_type": self.signal_type,
            "signal_direction": (
                None if self.signal_direction is None else self.signal_direction.value
            ),
            "signal_value": _decimal_text(self.signal_value),
            "threshold": str(self.threshold),
            "features": self.features.as_mapping(),
            "target_bid": _decimal_text(self.target_bid),
            "target_ask": _decimal_text(self.target_ask),
            "entry_price": _decimal_text(self.entry_price),
            "horizon_seconds": _timedelta_seconds(self.horizon),
            "future_bid": _decimal_text(self.future_bid),
            "future_ask": _decimal_text(self.future_ask),
            "gross_markout": _decimal_text(self.gross_markout),
            "net_markout": _decimal_text(self.net_markout),
            "target_mid_change": _decimal_text(self.target_mid_change),
            "target_logit_change": _decimal_text(self.target_logit_change),
            "valid": self.valid,
            "invalid_reason": None if self.invalid_reason is None else self.invalid_reason.value,
        }


@dataclass(slots=True)
class _Pending:
    spec: RelationshipExperimentSpec
    decision_time: datetime
    target_time: datetime
    horizon: timedelta
    direction: Direction
    signal_value: Decimal
    features: FeatureVector
    entry_quote: InstrumentView
    entry_price: Decimal
    entry_mid: Decimal | None
    entry_logit: Decimal | None
    regime: Regime


@dataclass(slots=True)
class _SignalState:
    armed: bool = True
    last_direction: Direction | None = None
    last_signal_at: datetime | None = None


class RelationshipExperimentRunner:
    """One deterministic runner for lead/lag, RV and leave-one-out experiments."""

    def run(
        self,
        events: Iterable[ReplayEvent],
        specs: tuple[RelationshipExperimentSpec, ...],
    ) -> tuple[RelationshipObservation, ...]:
        if not specs:
            return ()
        output: list[RelationshipObservation] = []
        pending: list[_Pending] = []
        histories: dict[InstrumentKey, list[tuple[datetime, InstrumentView]]] = {}
        signal_state = {spec.id: _SignalState() for spec in specs}
        replay = ReplayRunner(events)

        def resolve_before(next_at: datetime, state: ReplayState) -> None:
            self._resolve_pending(pending, output, state, through=next_at, inclusive=False)

        def on_frame(frame: ReplayFrame) -> None:
            self._resolve_pending(
                pending, output, frame.state, through=frame.observed_at, inclusive=True
            )
            touched = {
                InstrumentKey(event.source, event.instrument_id)
                for event in frame.events
                if event.instrument_id != "*"
            }
            for spec in sorted(specs, key=lambda item: item.id):
                relevant = {ref.instrument for ref in spec.references}
                if spec.family is not ExperimentFamily.LEADLAG:
                    relevant.add(spec.target)
                if touched.isdisjoint(relevant):
                    continue
                self._consider_signal(
                    frame=frame,
                    spec=spec,
                    histories=histories,
                    state=signal_state[spec.id],
                    pending=pending,
                    output=output,
                )
            for key in sorted(touched):
                view = frame.state.view(key.source, key.instrument_id)
                if view is not None:
                    histories.setdefault(key, []).append((frame.observed_at, view))

        final_state = replay.run(on_frame, before_frame=resolve_before)
        del final_state
        for item in sorted(pending, key=lambda value: (value.target_time, value.spec.id)):
            output.append(self._invalid_from_pending(item, InvalidReason.DATASET_END))
        return tuple(
            sorted(
                output,
                key=lambda row: (
                    row.decision_time,
                    row.experiment_id,
                    row.target,
                    row.horizon,
                    "" if row.signal_direction is None else row.signal_direction.value,
                ),
            )
        )

    def _consider_signal(
        self,
        *,
        frame: ReplayFrame,
        spec: RelationshipExperimentSpec,
        histories: Mapping[InstrumentKey, list[tuple[datetime, InstrumentView]]],
        state: _SignalState,
        pending: list[_Pending],
        output: list[RelationshipObservation],
    ) -> None:
        target, target_reason = _valid_view(
            frame.state, spec.target, frame.observed_at, spec.target_freshness
        )
        if target_reason is not None or target is None:
            output.extend(
                self._invalid_without_signal(
                    spec, frame.observed_at, target_reason or InvalidReason.NO_EXECUTABLE_START
                )
            )
            return
        if (
            target.best_bid is not None
            and target.best_ask is not None
            and target.best_bid > target.best_ask
        ):
            output.extend(
                self._invalid_without_signal(spec, frame.observed_at, InvalidReason.INVALID_SPREAD)
            )
            return

        if (
            spec.family is not ExperimentFamily.LEADLAG
            and spec.target_price_mode is ReferencePriceMode.LAST_TRADE
        ):
            _, target_trade_reason = _valid_feature_view(
                frame.state,
                spec.target,
                frame.observed_at,
                spec.target_freshness,
                ReferencePriceMode.LAST_TRADE,
            )
            if target_trade_reason is not None:
                output.extend(
                    self._invalid_without_signal(
                        spec,
                        frame.observed_at,
                        target_trade_reason,
                    )
                )
                return

        references: list[tuple[ReferenceSpec, InstrumentView]] = []
        first_reason: InvalidReason | None = None
        for ref in spec.references:
            view, reason = _valid_feature_view(
                frame.state,
                ref.instrument,
                frame.observed_at,
                spec.reference_freshness,
                spec.reference_price_mode,
            )
            if reason is not None or view is None:
                first_reason = first_reason or _reference_reason(reason)
                continue
            references.append((ref, view))
        if len(references) < spec.min_active_references:
            output.extend(
                self._invalid_without_signal(
                    spec,
                    frame.observed_at,
                    first_reason or InvalidReason.INSUFFICIENT_PREDICTOR_COVERAGE,
                )
            )
            return

        if spec.family is ExperimentFamily.LEADLAG:
            computed = self._leadlag_signal(spec, frame.observed_at, references, histories, target)
        else:
            computed = self._residual_signal(spec, references, target, frame.observed_at)
        if computed is None:
            output.extend(
                self._invalid_without_signal(
                    spec,
                    frame.observed_at,
                    InvalidReason.INSUFFICIENT_PREDICTOR_COVERAGE,
                )
            )
            return
        signal_value, direction, features = computed
        if abs(signal_value) < spec.threshold:
            state.armed = True
            state.last_direction = None
            return
        if direction is None:
            return
        if state.last_direction is not None and direction is not state.last_direction:
            state.armed = True
        if not state.armed:
            return
        if (
            state.last_signal_at is not None
            and frame.observed_at - state.last_signal_at < spec.cooldown
        ):
            return

        entry = executable_entry(target, direction)
        if entry is None:
            output.extend(
                self._invalid_signal(
                    spec,
                    frame.observed_at,
                    direction,
                    signal_value,
                    features,
                    InvalidReason.NO_EXECUTABLE_START,
                )
            )
            return
        state.armed = False
        state.last_direction = direction
        state.last_signal_at = frame.observed_at
        entry_mid = target.midpoint
        entry_logit = None if entry_mid is None else _logit(entry_mid, spec.clamp_epsilon)
        regime = _regime(spec, frame.observed_at)
        for horizon in spec.target_horizons:
            pending.append(
                _Pending(
                    spec=spec,
                    decision_time=frame.observed_at,
                    target_time=frame.observed_at + horizon,
                    horizon=horizon,
                    direction=direction,
                    signal_value=signal_value,
                    features=features,
                    entry_quote=target,
                    entry_price=entry,
                    entry_mid=entry_mid,
                    entry_logit=entry_logit,
                    regime=regime,
                )
            )

    def _leadlag_signal(
        self,
        spec: RelationshipExperimentSpec,
        decision_time: datetime,
        references: list[tuple[ReferenceSpec, InstrumentView]],
        histories: Mapping[InstrumentKey, list[tuple[datetime, InstrumentView]]],
        target: InstrumentView,
    ) -> tuple[Decimal, Direction | None, FeatureVector] | None:
        cutoff = decision_time - spec.lookback
        weighted_p = Decimal("0")
        weighted_z = Decimal("0")
        active = 0
        for ref, current in references:
            previous = _history_at_or_before(histories.get(ref.instrument, []), cutoff)
            if previous is None:
                continue
            if not _view_is_fresh_for_mode(
                previous,
                cutoff,
                spec.reference_freshness,
                spec.reference_price_mode,
            ):
                continue
            current_p = _relationship_price(
                current, spec.reference_price_mode, ref.relationship
            )
            previous_p = _relationship_price(
                previous, spec.reference_price_mode, ref.relationship
            )
            if current_p is None or previous_p is None:
                continue
            weighted_p += ref.weight * (current_p - previous_p)
            weighted_z += ref.weight * (
                _logit(current_p, spec.clamp_epsilon) - _logit(previous_p, spec.clamp_epsilon)
            )
            active += 1
        if active < spec.min_active_references:
            return None
        signal = weighted_z if spec.coordinate is PriceCoordinate.LOGIT else weighted_p
        direction = Direction.BUY_YES if signal > 0 else Direction.SELL_YES if signal < 0 else None
        features = _feature_vector(
            target=target,
            decision_time=decision_time,
            price_movement=weighted_p,
            logit_movement=weighted_z,
            residual_p=None,
            residual_z=None,
            active=active,
            epsilon=spec.clamp_epsilon,
        )
        return signal, direction, features

    def _residual_signal(
        self,
        spec: RelationshipExperimentSpec,
        references: list[tuple[ReferenceSpec, InstrumentView]],
        target: InstrumentView,
        decision_time: datetime,
    ) -> tuple[Decimal, Direction | None, FeatureVector] | None:
        values: list[tuple[Decimal, Decimal]] = []
        for ref, view in references:
            price = _relationship_price(
                view, spec.reference_price_mode, ref.relationship
            )
            if price is None:
                continue
            values.append((price, ref.weight))
        if len(values) < spec.min_active_references:
            return None
        ref_p = _estimate_reference(values, spec.estimator, spec.clamp_epsilon)
        target_p = _reference_price(target, spec.target_price_mode)
        if target_p is None:
            return None
        residual_p = target_p - ref_p
        residual_z = _logit(target_p, spec.clamp_epsilon) - _logit(ref_p, spec.clamp_epsilon)
        signal = residual_z if spec.coordinate is PriceCoordinate.LOGIT else residual_p
        direction = Direction.SELL_YES if signal > 0 else Direction.BUY_YES if signal < 0 else None
        features = _feature_vector(
            target=target,
            decision_time=decision_time,
            price_movement=None,
            logit_movement=None,
            residual_p=residual_p,
            residual_z=residual_z,
            active=len(values),
            epsilon=spec.clamp_epsilon,
        )
        return signal, direction, features

    def _resolve_pending(
        self,
        pending: list[_Pending],
        output: list[RelationshipObservation],
        state: ReplayState,
        *,
        through: datetime,
        inclusive: bool,
    ) -> None:
        keep: list[_Pending] = []
        for item in pending:
            ready = item.target_time <= through if inclusive else item.target_time < through
            if not ready:
                keep.append(item)
                continue
            future, reason = _valid_view(
                state, item.spec.target, item.target_time, item.spec.target_freshness
            )
            if reason is not None or future is None:
                mapped = (
                    InvalidReason.NO_VALID_FUTURE_QUOTE
                    if reason is InvalidReason.NO_EXECUTABLE_START
                    else reason or InvalidReason.NO_VALID_FUTURE_QUOTE
                )
                output.append(self._invalid_from_pending(item, mapped))
                continue
            markout = evaluate_markout(
                entry_quote=item.entry_quote, future_quote=future, direction=item.direction
            )
            if markout is None:
                output.append(self._invalid_from_pending(item, InvalidReason.NO_VALID_FUTURE_QUOTE))
                continue
            total_cost = _explicit_cost(item.spec, item.entry_quote, item.direction)
            net = None if total_cost is None else markout.gross - total_cost
            future_mid = future.midpoint
            mid_change = (
                None
                if item.entry_mid is None or future_mid is None
                else future_mid - item.entry_mid
            )
            future_logit = (
                None if future_mid is None else _logit(future_mid, item.spec.clamp_epsilon)
            )
            logit_change = (
                None
                if item.entry_logit is None or future_logit is None
                else future_logit - item.entry_logit
            )
            output.append(
                RelationshipObservation(
                    experiment_id=item.spec.id,
                    dataset_id=item.spec.dataset_id,
                    target=item.spec.target.label,
                    reference_set=_reference_labels(item.spec),
                    excluded_instruments=_excluded_labels(item.spec),
                    decision_time=item.decision_time,
                    regime=item.regime,
                    signal_type=item.spec.family.value,
                    signal_direction=item.direction,
                    signal_value=item.signal_value,
                    threshold=item.spec.threshold,
                    features=item.features,
                    target_bid=item.entry_quote.best_bid,
                    target_ask=item.entry_quote.best_ask,
                    entry_price=markout.entry_price,
                    horizon=item.horizon,
                    future_bid=future.best_bid,
                    future_ask=future.best_ask,
                    gross_markout=markout.gross,
                    net_markout=net,
                    target_mid_change=mid_change,
                    target_logit_change=logit_change,
                    valid=True,
                    invalid_reason=None,
                )
            )
        pending[:] = keep

    def _invalid_without_signal(
        self,
        spec: RelationshipExperimentSpec,
        decision_time: datetime,
        reason: InvalidReason,
    ) -> list[RelationshipObservation]:
        features = FeatureVector(None, None, None, None, None, None, None, 0, spec.clamp_epsilon)
        return [
            _invalid_observation(spec, decision_time, horizon, features, reason)
            for horizon in spec.target_horizons
        ]

    def _invalid_signal(
        self,
        spec: RelationshipExperimentSpec,
        decision_time: datetime,
        direction: Direction,
        signal_value: Decimal,
        features: FeatureVector,
        reason: InvalidReason,
    ) -> list[RelationshipObservation]:
        return [
            _invalid_observation(
                spec, decision_time, horizon, features, reason, direction, signal_value
            )
            for horizon in spec.target_horizons
        ]

    def _invalid_from_pending(
        self, item: _Pending, reason: InvalidReason
    ) -> RelationshipObservation:
        return RelationshipObservation(
            experiment_id=item.spec.id,
            dataset_id=item.spec.dataset_id,
            target=item.spec.target.label,
            reference_set=_reference_labels(item.spec),
            excluded_instruments=_excluded_labels(item.spec),
            decision_time=item.decision_time,
            regime=item.regime,
            signal_type=item.spec.family.value,
            signal_direction=item.direction,
            signal_value=item.signal_value,
            threshold=item.spec.threshold,
            features=item.features,
            target_bid=item.entry_quote.best_bid,
            target_ask=item.entry_quote.best_ask,
            entry_price=item.entry_price,
            horizon=item.horizon,
            future_bid=None,
            future_ask=None,
            gross_markout=None,
            net_markout=None,
            target_mid_change=None,
            target_logit_change=None,
            valid=False,
            invalid_reason=reason,
        )


@dataclass(frozen=True, slots=True)
class RelationshipSummary:
    n_total: int
    n_valid: int
    invalid_breakdown: Mapping[str, int]
    mean_executable_markout: Decimal | None
    median_executable_markout: Decimal | None
    hit_rate: Decimal | None
    p25: Decimal | None
    p75: Decimal | None
    total_gross_markout: Decimal
    total_net_markout: Decimal | None
    mean_ci_low: Decimal | None
    mean_ci_high: Decimal | None


def summarize_relationship_observations(
    observations: tuple[RelationshipObservation, ...],
    *,
    bootstrap_block_size: int = 5,
    bootstrap_samples: int = 400,
    bootstrap_seed: int = 0,
) -> RelationshipSummary:
    valid = [row for row in observations if row.valid and row.gross_markout is not None]
    invalid: dict[str, int] = {}
    for row in observations:
        if row.valid or row.invalid_reason is None:
            continue
        key = row.invalid_reason.value
        invalid[key] = invalid.get(key, 0) + 1
    gross = sorted(row.gross_markout for row in valid if row.gross_markout is not None)
    if not gross:
        return RelationshipSummary(
            len(observations), 0, dict(sorted(invalid.items())), None, None, None, None, None,
            Decimal("0"), None, None, None
        )
    total = sum(gross, Decimal("0"))
    net = [row.net_markout for row in valid if row.net_markout is not None]
    ci_low, ci_high = _block_bootstrap_mean_ci(
        [row.gross_markout for row in valid if row.gross_markout is not None],
        block_size=bootstrap_block_size,
        samples=bootstrap_samples,
        seed=bootstrap_seed,
    )
    return RelationshipSummary(
        n_total=len(observations),
        n_valid=len(gross),
        invalid_breakdown=dict(sorted(invalid.items())),
        mean_executable_markout=total / Decimal(len(gross)),
        median_executable_markout=_median(gross),
        hit_rate=Decimal(sum(1 for value in gross if value > 0)) / Decimal(len(gross)),
        p25=_nearest_rank(gross, Decimal("0.25")),
        p75=_nearest_rank(gross, Decimal("0.75")),
        total_gross_markout=total,
        total_net_markout=sum(net, Decimal("0")) if len(net) == len(gross) else None,
        mean_ci_low=ci_low,
        mean_ci_high=ci_high,
    )


def response_curve(
    observations: tuple[RelationshipObservation, ...],
) -> dict[tuple[str, str, str, str, str, str, str], RelationshipSummary]:
    groups: dict[
        tuple[str, str, str, str, str, str, str], list[RelationshipObservation]
    ] = {}
    for row in observations:
        direction = "NONE" if row.signal_direction is None else row.signal_direction.value
        key = (
            row.experiment_id,
            row.dataset_id,
            row.target,
            direction,
            str(row.threshold),
            _timedelta_seconds(row.horizon),
            row.regime.value,
        )
        groups.setdefault(key, []).append(row)
    return {
        key: summarize_relationship_observations(tuple(rows))
        for key, rows in sorted(groups.items(), key=lambda item: item[0])
    }


@dataclass(frozen=True, slots=True)
class RelationshipChronologicalSplit:
    train: tuple[RelationshipObservation, ...]
    development: tuple[RelationshipObservation, ...]
    holdout: tuple[RelationshipObservation, ...]


def split_relationship_observations(
    observations: tuple[RelationshipObservation, ...],
    boundaries: ChronologicalBoundaries,
) -> RelationshipChronologicalSplit:
    train: list[RelationshipObservation] = []
    development: list[RelationshipObservation] = []
    holdout: list[RelationshipObservation] = []
    for row in sorted(observations, key=lambda item: item.decision_time):
        _require_aware(row.decision_time, "decision_time")
        if row.decision_time < boundaries.train_end:
            train.append(row)
        elif row.decision_time < boundaries.development_end:
            development.append(row)
        else:
            holdout.append(row)
    return RelationshipChronologicalSplit(tuple(train), tuple(development), tuple(holdout))


def serialize_relationship_observations(
    observations: Iterable[RelationshipObservation],
) -> bytes:
    ordered = sorted(observations, key=_observation_sort_key)
    records = [row.as_record() for row in ordered]
    return (json.dumps(records, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _observation_sort_key(
    row: RelationshipObservation,
) -> tuple[datetime, str, str, timedelta, str]:
    return (
        row.decision_time,
        row.experiment_id,
        row.target,
        row.horizon,
        "" if row.signal_direction is None else row.signal_direction.value,
    )


def _valid_view(
    state: ReplayState,
    instrument: InstrumentKey,
    at: datetime,
    max_age: timedelta | None,
) -> tuple[InstrumentView | None, InvalidReason | None]:
    return state.quote_status(
        source=instrument.source,
        instrument_id=instrument.instrument_id,
        at=at,
        max_age=max_age,
    )


def _valid_feature_view(
    state: ReplayState,
    instrument: InstrumentKey,
    at: datetime,
    max_age: timedelta | None,
    mode: ReferencePriceMode,
) -> tuple[InstrumentView | None, InvalidReason | None]:
    if mode is ReferencePriceMode.LAST_TRADE:
        return state.trade_status(
            source=instrument.source,
            instrument_id=instrument.instrument_id,
            at=at,
            max_age=max_age,
        )
    return _valid_view(state, instrument, at, max_age)


def _view_is_fresh_for_mode(
    view: InstrumentView,
    at: datetime,
    max_age: timedelta | None,
    mode: ReferencePriceMode,
) -> bool:
    observed_at = (
        view.last_trade_observed_at
        if mode is ReferencePriceMode.LAST_TRADE
        else view.quote_observed_at
    )
    if observed_at is None:
        return False
    return max_age is None or at - observed_at <= max_age


def _reference_reason(reason: InvalidReason | None) -> InvalidReason:
    if reason is None or reason is InvalidReason.NO_EXECUTABLE_START:
        return InvalidReason.REFERENCE_UNAVAILABLE
    return reason


def _reference_price(view: InstrumentView, mode: ReferencePriceMode) -> Decimal | None:
    if mode is ReferencePriceMode.MIDPOINT:
        return view.midpoint
    if mode is ReferencePriceMode.BID:
        return view.best_bid
    if mode is ReferencePriceMode.ASK:
        return view.best_ask
    if mode is ReferencePriceMode.LAST_TRADE:
        return view.last_trade
    if view.bids and view.asks:
        bid = view.bids[0]
        ask = view.asks[0]
        total = bid.quantity + ask.quantity
        if total > 0:
            return (ask.price * bid.quantity + bid.price * ask.quantity) / total
    return view.midpoint


def _relationship_price(
    view: InstrumentView,
    mode: ReferencePriceMode,
    relationship: RelationshipDirection,
) -> Decimal | None:
    if relationship is RelationshipDirection.SAME:
        return _reference_price(view, mode)
    if mode is ReferencePriceMode.BID:
        return None if view.best_ask is None else Decimal("1") - view.best_ask
    if mode is ReferencePriceMode.ASK:
        return None if view.best_bid is None else Decimal("1") - view.best_bid
    price = _reference_price(view, mode)
    return None if price is None else Decimal("1") - price


def _estimate_reference(
    values: list[tuple[Decimal, Decimal]],
    estimator: ReferenceEstimator,
    epsilon: Decimal,
) -> Decimal:
    total_weight = sum((weight for _, weight in values), Decimal("0"))
    if estimator is ReferenceEstimator.WEIGHTED_PROBABILITY:
        return sum((price * weight for price, weight in values), Decimal("0")) / total_weight
    z = (
        sum((_logit(price, epsilon) * weight for price, weight in values), Decimal("0"))
        / total_weight
    )
    return _sigmoid(z)


def _feature_vector(
    *,
    target: InstrumentView,
    decision_time: datetime,
    price_movement: Decimal | None,
    logit_movement: Decimal | None,
    residual_p: Decimal | None,
    residual_z: Decimal | None,
    active: int,
    epsilon: Decimal,
) -> FeatureVector:
    spread = (
        None
        if target.best_bid is None or target.best_ask is None
        else target.best_ask - target.best_bid
    )
    depth = None
    if target.bids and target.asks:
        depth = target.bids[0].quantity + target.asks[0].quantity
    quote_age = (
        None
        if target.quote_observed_at is None
        else _seconds_decimal(decision_time - target.quote_observed_at)
    )
    return FeatureVector(
        price_movement,
        logit_movement,
        spread,
        depth,
        quote_age,
        residual_p,
        residual_z,
        active,
        epsilon,
    )


def _history_at_or_before(
    history: list[tuple[datetime, InstrumentView]], cutoff: datetime
) -> InstrumentView | None:
    for at, view in reversed(history):
        if at <= cutoff:
            return view
    return None


def _logit(value: Decimal, epsilon: Decimal) -> Decimal:
    clamped = min(Decimal("1") - epsilon, max(epsilon, value))
    return (clamped / (Decimal("1") - clamped)).ln()


def _sigmoid(value: Decimal) -> Decimal:
    return Decimal("1") / (Decimal("1") + (-value).exp())


def _explicit_cost(
    spec: RelationshipExperimentSpec,
    entry_quote: InstrumentView,
    direction: Direction,
) -> Decimal | None:
    components: list[Decimal] = []
    if spec.fee_per_share is not None:
        components.append(spec.fee_per_share)
    if spec.latency_adjustment is not None:
        components.append(spec.latency_adjustment)
    if spec.depth_cost is not None:
        depth = spec.depth_cost(entry_quote, direction)
        if depth is None:
            return None
        components.append(depth)
    return None if not components else sum(components, Decimal("0"))


def _regime(spec: RelationshipExperimentSpec, at: datetime) -> Regime:
    return (
        Regime.EVENT
        if any(window.contains(at) for window in spec.event_windows)
        else Regime.NORMAL
    )


def _reference_labels(spec: RelationshipExperimentSpec) -> tuple[str, ...]:
    return tuple(ref.instrument.label for ref in spec.references)


def _excluded_labels(spec: RelationshipExperimentSpec) -> tuple[str, ...]:
    excluded = set(spec.excluded_family)
    if spec.family is ExperimentFamily.LOO_FAMILY:
        excluded.update(spec.direct_family)
        excluded.add(spec.target)
    elif spec.family is ExperimentFamily.LOO_PRICE:
        excluded.add(spec.target)
    return tuple(sorted(key.label for key in excluded))


def _invalid_observation(
    spec: RelationshipExperimentSpec,
    decision_time: datetime,
    horizon: timedelta,
    features: FeatureVector,
    reason: InvalidReason,
    direction: Direction | None = None,
    signal_value: Decimal | None = None,
) -> RelationshipObservation:
    return RelationshipObservation(
        experiment_id=spec.id,
        dataset_id=spec.dataset_id,
        target=spec.target.label,
        reference_set=_reference_labels(spec),
        excluded_instruments=_excluded_labels(spec),
        decision_time=decision_time,
        regime=_regime(spec, decision_time),
        signal_type=spec.family.value,
        signal_direction=direction,
        signal_value=signal_value,
        threshold=spec.threshold,
        features=features,
        target_bid=None,
        target_ask=None,
        entry_price=None,
        horizon=horizon,
        future_bid=None,
        future_ask=None,
        gross_markout=None,
        net_markout=None,
        target_mid_change=None,
        target_logit_change=None,
        valid=False,
        invalid_reason=reason,
    )


def _block_bootstrap_mean_ci(
    values: list[Decimal], *, block_size: int, samples: int, seed: int
) -> tuple[Decimal | None, Decimal | None]:
    if block_size < 1 or samples < 1 or len(values) < block_size * 2:
        return None, None
    blocks = [
        values[index : index + block_size]
        for index in range(0, len(values) - block_size + 1)
    ]
    if not blocks:
        return None, None
    rng = random.Random(seed)
    means: list[Decimal] = []
    for _ in range(samples):
        sample: list[Decimal] = []
        while len(sample) < len(values):
            sample.extend(blocks[rng.randrange(len(blocks))])
        sample = sample[: len(values)]
        means.append(sum(sample, Decimal("0")) / Decimal(len(sample)))
    means.sort()
    return _nearest_rank(means, Decimal("0.025")), _nearest_rank(means, Decimal("0.975"))


def _median(values: list[Decimal]) -> Decimal:
    midpoint = len(values) // 2
    if len(values) % 2:
        return values[midpoint]
    return (values[midpoint - 1] + values[midpoint]) / Decimal("2")


def _nearest_rank(values: list[Decimal], quantile: Decimal) -> Decimal:
    raw = (Decimal(len(values)) * quantile).to_integral_value(rounding=ROUND_CEILING)
    return values[max(0, int(raw) - 1)]


def _seconds_decimal(value: timedelta) -> Decimal:
    return Decimal(value.days * 86_400 + value.seconds) + (
        Decimal(value.microseconds) / Decimal(1_000_000)
    )


def _timedelta_seconds(value: timedelta) -> str:
    return str(_seconds_decimal(value))


def _decimal_text(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


def _require_aware(value: datetime, field: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")
