"""First-hours economic diagnostics over existing CAPTURE, OBSERVE and LIVE-LEARN evidence."""

from __future__ import annotations

import bisect
import os
import random
import statistics
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path

import pyarrow as pa
import pyarrow.dataset as ds
import pyarrow.parquet as pq

from predictions_cup.analysis.evidence import (
    AnalysisEvidence,
    AnalysisUncertainty,
    canonical_config_hash,
    sha256_file,
    write_evidence,
)
from predictions_cup.live_learn.contracts import OutcomeStatus, outcome_from_record
from predictions_cup.mapping.crosswalk import load_document
from predictions_cup.mapping.models import MappingDirection

ANALYSIS_VERSION = "live-diag-001-v1"
DEFAULT_THRESHOLDS_TICKS = (1, 2, 3, 5)
DEFAULT_HORIZONS_SECONDS = (1, 5, 15, 30, 60, 300)


class ResearchStatus(StrEnum):
    MONETIZABLE_CANDIDATE = "MONETIZABLE_CANDIDATE"
    DESCRIPTIVE_ONLY = "DESCRIPTIVE_ONLY"
    TOO_FAST_TO_MONETIZE = "TOO_FAST_TO_MONETIZE"
    NO_EXECUTABLE_EDGE = "NO_EXECUTABLE_EDGE"
    NEGATIVE_ECONOMICS = "NEGATIVE_ECONOMICS"
    INSUFFICIENT_DEPTH = "INSUFFICIENT_DEPTH"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    INCONCLUSIVE = "INCONCLUSIVE"


class MarketRecommendation(StrEnum):
    QUOTE_MORE = "QUOTE_MORE"
    QUOTE_NORMAL = "QUOTE_NORMAL"
    WIDEN = "WIDEN"
    ONE_SIDED = "ONE_SIDED"
    PAUSE = "PAUSE"
    NO_DATA = "NO_DATA"


@dataclass(frozen=True, slots=True)
class Quote:
    observed_at: datetime
    best_bid: float
    best_ask: float
    bid_depth: float | None = None
    ask_depth: float | None = None
    trusted: bool = True

    @property
    def midpoint(self) -> float:
        return (self.best_bid + self.best_ask) / 2.0

    @property
    def spread(self) -> float:
        return self.best_ask - self.best_bid


@dataclass(frozen=True, slots=True)
class GapTrigger:
    trigger_id: str
    observed_at: datetime
    threshold_ticks: int
    external_fv: float
    sig_quote: Quote
    signed_gap: float


@dataclass(frozen=True, slots=True)
class SnapbackObservation:
    trigger_id: str
    horizon_seconds: int
    residual_gap: float
    residual_ratio: float
    fraction_closed: float
    sig_move: float
    same_direction_sig_response: bool
    overshoot: bool
    full_close: bool


@dataclass(frozen=True, slots=True)
class LeadLagObservation:
    impulse_id: str
    external_observed_at: datetime
    external_move: float
    lead_seconds: float | None
    sig_response_at: datetime | None
    same_direction: bool | None
    sig_bid_at_trigger: float | None
    sig_ask_at_trigger: float | None
    available_depth: float | None
    gross_executable_edge: float | None
    latency_ms: float
    latency_adjusted_executable_price: float | None
    latency_adjusted_edge: float | None
    signal_half_life: float | None
    status: ResearchStatus
    reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class InventoryPoint:
    observed_at: datetime
    inventory: float
    capital_at_risk: float


@dataclass(frozen=True, slots=True)
class InventorySummary:
    peak_abs_inventory: float
    inventory_time_weighted_abs: float | None
    time_to_flat_distribution: tuple[float, ...]
    inventory_half_life: float | None
    returns_to_flat_count: int
    time_above_25pct_limit: float | None
    time_above_50pct_limit: float | None
    time_above_75pct_limit: float | None
    capital_seconds_consumed: float
    episode_count: int


def align_probability(value: float, direction: MappingDirection) -> float:
    if direction is MappingDirection.SAME:
        return value
    if direction is MappingDirection.COMPLEMENT:
        return 1.0 - value
    raise ValueError("LIVE-DIAG requires SAME or COMPLEMENT direct mapping")


def _times(quotes: Sequence[Quote]) -> list[datetime]:
    return [item.observed_at for item in quotes]


def _asof(quotes: Sequence[Quote], at: datetime) -> Quote | None:
    if not quotes:
        return None
    index = bisect.bisect_right(_times(quotes), at) - 1
    return None if index < 0 else quotes[index]


def _economic(quotes: Sequence[Quote]) -> tuple[Quote, ...]:
    result: list[Quote] = []
    for item in sorted(quotes, key=lambda quote: quote.observed_at):
        if not item.trusted or item.best_ask < item.best_bid:
            continue
        if result and (result[-1].best_bid, result[-1].best_ask) == (
            item.best_bid,
            item.best_ask,
        ):
            continue
        result.append(item)
    return tuple(result)


def construct_gap_episodes(
    *,
    sig_quotes: Sequence[Quote],
    external_quotes: Sequence[Quote],
    threshold_ticks: int,
    tick_size: float = 0.01,
) -> tuple[GapTrigger, ...]:
    """Create independent threshold-crossing episodes using only observable-as-of state."""
    if threshold_ticks <= 0 or tick_size <= 0:
        raise ValueError("threshold_ticks and tick_size must be positive")
    sig = _economic(sig_quotes)
    external = _economic(external_quotes)
    threshold = threshold_ticks * tick_size
    timestamps = sorted(
        {item.observed_at for item in sig} | {item.observed_at for item in external}
    )
    active = False
    output: list[GapTrigger] = []
    for at in timestamps:
        sig_quote = _asof(sig, at)
        external_quote = _asof(external, at)
        if sig_quote is None or external_quote is None:
            continue
        gap = external_quote.midpoint - sig_quote.midpoint
        over = abs(gap) >= threshold
        if over and not active:
            output.append(
                GapTrigger(
                    trigger_id=(
                        f"{at.astimezone(UTC).isoformat()}:{threshold_ticks}:{len(output)}"
                    ),
                    observed_at=at,
                    threshold_ticks=threshold_ticks,
                    external_fv=external_quote.midpoint,
                    sig_quote=sig_quote,
                    signed_gap=gap,
                )
            )
        active = over
    return tuple(output)


def observe_snapback(
    *,
    triggers: Sequence[GapTrigger],
    sig_quotes: Sequence[Quote],
    external_quotes: Sequence[Quote],
    horizons_seconds: Sequence[int] = DEFAULT_HORIZONS_SECONDS,
    tick_size: float = 0.01,
) -> tuple[SnapbackObservation, ...]:
    """Score future residual gaps without feeding future evidence into the trigger."""
    sig = _economic(sig_quotes)
    external = _economic(external_quotes)
    output: list[SnapbackObservation] = []
    for trigger in triggers:
        for horizon in horizons_seconds:
            at = trigger.observed_at + timedelta(seconds=horizon)
            future_sig = _asof(sig, at)
            future_external = _asof(external, at)
            if future_sig is None or future_external is None:
                continue
            residual = future_external.midpoint - future_sig.midpoint
            ratio = residual / trigger.signed_gap
            fraction_closed = 1.0 - ratio
            sig_move = future_sig.midpoint - trigger.sig_quote.midpoint
            output.append(
                SnapbackObservation(
                    trigger_id=trigger.trigger_id,
                    horizon_seconds=horizon,
                    residual_gap=residual,
                    residual_ratio=ratio,
                    fraction_closed=fraction_closed,
                    sig_move=sig_move,
                    same_direction_sig_response=sig_move * trigger.signed_gap > 0.0,
                    overshoot=ratio < 0.0,
                    full_close=abs(residual) <= tick_size / 2.0,
                )
            )
    return tuple(output)


def _median(values: Iterable[float]) -> float | None:
    materialized = tuple(values)
    return None if not materialized else float(statistics.median(materialized))


def _bootstrap_median(values: Sequence[float]) -> AnalysisUncertainty:
    if len(values) < 10:
        return AnalysisUncertainty(
            point_estimate=_median(values),
            interval=None,
            method=None,
            cluster_unit="gap_episode",
            support=len(values),
            reason="INSUFFICIENT_INDEPENDENT_EVENTS",
        )
    rng = random.Random(0)
    estimates = sorted(
        float(statistics.median(rng.choices(values, k=len(values)))) for _ in range(500)
    )
    return AnalysisUncertainty(
        point_estimate=float(statistics.median(values)),
        interval=(estimates[11], estimates[487]),
        method="episode_bootstrap_percentile",
        cluster_unit="gap_episode",
        support=len(values),
    )


def summarize_snapback(
    *,
    triggers: Sequence[GapTrigger],
    observations: Sequence[SnapbackObservation],
    horizons_seconds: Sequence[int] = DEFAULT_HORIZONS_SECONDS,
) -> dict[str, object]:
    grouped: dict[int, list[SnapbackObservation]] = defaultdict(list)
    by_trigger: dict[str, list[SnapbackObservation]] = defaultdict(list)
    for item in observations:
        grouped[item.horizon_seconds].append(item)
        by_trigger[item.trigger_id].append(item)
    half_lives: list[float] = []
    for values in by_trigger.values():
        for item in sorted(values, key=lambda value: value.horizon_seconds):
            if abs(item.residual_ratio) <= 0.5:
                half_lives.append(float(item.horizon_seconds))
                break
    summary: dict[str, object] = {
        "trigger_count": len(triggers),
        "independent_event_count": len(triggers),
        "estimated_residual_half_life": _median(half_lives),
    }
    for horizon in horizons_seconds:
        values = grouped[horizon]
        fractions = [item.fraction_closed for item in values]
        summary[f"residual_gap_{horizon}s"] = _median(
            item.residual_gap for item in values
        )
        summary[f"residual_ratio_{horizon}s"] = _median(
            item.residual_ratio for item in values
        )
        summary[f"fraction_gap_closed_{horizon}s"] = _median(fractions)
        summary[f"full_close_rate_{horizon}s"] = (
            None if not values else sum(item.full_close for item in values) / len(values)
        )
        summary[f"same_direction_sig_response_rate_{horizon}s"] = (
            None
            if not values
            else sum(item.same_direction_sig_response for item in values) / len(values)
        )
        summary[f"overshoot_rate_{horizon}s"] = (
            None if not values else sum(item.overshoot for item in values) / len(values)
        )
    return summary


def _active_edge(
    external_fv: float,
    quote: Quote,
) -> tuple[float, float | None, float | None]:
    if external_fv > quote.best_ask:
        return external_fv - quote.best_ask, quote.best_ask, quote.ask_depth
    if external_fv < quote.best_bid:
        return quote.best_bid - external_fv, quote.best_bid, quote.bid_depth
    return 0.0, None, None


def _first_response(
    sig_quotes: Sequence[Quote],
    *,
    at: datetime,
    direction: int,
) -> tuple[datetime | None, bool | None]:
    baseline = _asof(sig_quotes, at)
    if baseline is None:
        return None, None
    for quote in sig_quotes:
        if quote.observed_at < at:
            continue
        move = quote.midpoint - baseline.midpoint
        if move != 0.0:
            return quote.observed_at, move * direction > 0.0
    return None, None


def analyze_lead_lag(
    *,
    sig_quotes: Sequence[Quote],
    external_quotes: Sequence[Quote],
    latency_ms: float,
    minimum_impulse_ticks: int = 1,
    tick_size: float = 0.01,
) -> tuple[LeadLagObservation, ...]:
    """Require the executable edge to survive the declared latency assumption."""
    if latency_ms < 0:
        raise ValueError("latency_ms must be non-negative")
    sig = _economic(sig_quotes)
    external = _economic(external_quotes)
    output: list[LeadLagObservation] = []
    for index, current in enumerate(external[1:], start=1):
        prior = external[index - 1]
        move = current.midpoint - prior.midpoint
        if abs(move) < minimum_impulse_ticks * tick_size:
            continue
        trigger = _asof(sig, current.observed_at)
        impulse_id = f"{current.observed_at.astimezone(UTC).isoformat()}:{index}"
        if trigger is None:
            output.append(
                LeadLagObservation(
                    impulse_id=impulse_id,
                    external_observed_at=current.observed_at,
                    external_move=move,
                    lead_seconds=None,
                    sig_response_at=None,
                    same_direction=None,
                    sig_bid_at_trigger=None,
                    sig_ask_at_trigger=None,
                    available_depth=None,
                    gross_executable_edge=None,
                    latency_ms=latency_ms,
                    latency_adjusted_executable_price=None,
                    latency_adjusted_edge=None,
                    signal_half_life=None,
                    status=ResearchStatus.INSUFFICIENT_EVIDENCE,
                    reasons=("NO_SIG_ASOF_QUOTE",),
                )
            )
            continue

        direction = 1 if move > 0 else -1
        response_at, same_direction = _first_response(
            sig,
            at=current.observed_at,
            direction=direction,
        )
        lead_seconds = (
            None
            if response_at is None
            else (response_at - current.observed_at).total_seconds()
        )
        gross_edge, _, depth = _active_edge(current.midpoint, trigger)
        delayed = _asof(
            sig,
            current.observed_at + timedelta(milliseconds=latency_ms),
        )
        delayed_edge: float | None = None
        delayed_price: float | None = None
        if delayed is not None:
            delayed_edge, delayed_price, _ = _active_edge(current.midpoint, delayed)

        half_life: float | None = None
        if gross_edge > 0:
            for quote in sig:
                if quote.observed_at < current.observed_at:
                    continue
                edge, _, _ = _active_edge(current.midpoint, quote)
                if edge <= gross_edge / 2.0:
                    half_life = (
                        quote.observed_at - current.observed_at
                    ).total_seconds()
                    break

        if response_at is None:
            status = ResearchStatus.INSUFFICIENT_EVIDENCE
            reasons = ("NO_SUBSEQUENT_SIG_RESPONSE",)
        elif gross_edge <= 0:
            status = ResearchStatus.NO_EXECUTABLE_EDGE
            reasons = ("NO_ACTIVE_EDGE_AT_TRIGGER",)
        elif depth is None or depth <= 0:
            status = ResearchStatus.INSUFFICIENT_DEPTH
            reasons = ("EXECUTABLE_DEPTH_UNAVAILABLE",)
        elif delayed_edge is None:
            status = ResearchStatus.INSUFFICIENT_EVIDENCE
            reasons = ("NO_LATENCY_ASOF_BOOK",)
        elif delayed_edge <= 0:
            status = ResearchStatus.TOO_FAST_TO_MONETIZE
            reasons = ("EDGE_GONE_AFTER_LATENCY",)
        else:
            status = ResearchStatus.MONETIZABLE_CANDIDATE
            reasons = ("LATENCY_ADJUSTED_ACTIVE_EDGE_POSITIVE",)

        output.append(
            LeadLagObservation(
                impulse_id=impulse_id,
                external_observed_at=current.observed_at,
                external_move=move,
                lead_seconds=lead_seconds,
                sig_response_at=response_at,
                same_direction=same_direction,
                sig_bid_at_trigger=trigger.best_bid,
                sig_ask_at_trigger=trigger.best_ask,
                available_depth=depth,
                gross_executable_edge=gross_edge,
                latency_ms=latency_ms,
                latency_adjusted_executable_price=delayed_price,
                latency_adjusted_edge=delayed_edge,
                signal_half_life=half_life,
                status=status,
                reasons=reasons,
            )
        )
    return tuple(output)


def summarize_inventory(
    points: Sequence[InventoryPoint],
    *,
    inventory_limit: float | None,
    flat_epsilon: float = 1e-12,
) -> InventorySummary:
    """Measure flat-to-flat inventory episodes and explicit capital-seconds."""
    ordered = tuple(sorted(points, key=lambda item: item.observed_at))
    if not ordered:
        return InventorySummary(0.0, None, (), None, 0, None, None, None, 0.0, 0)
    total_seconds = 0.0
    weighted_inventory = 0.0
    capital_seconds = 0.0
    above = {0.25: 0.0, 0.50: 0.0, 0.75: 0.0}
    for left, right in zip(ordered, ordered[1:], strict=False):
        duration = max(0.0, (right.observed_at - left.observed_at).total_seconds())
        total_seconds += duration
        weighted_inventory += abs(left.inventory) * duration
        capital_seconds += max(0.0, left.capital_at_risk) * duration
        if inventory_limit is not None and inventory_limit > 0:
            ratio = abs(left.inventory) / inventory_limit
            for threshold in above:
                if ratio >= threshold:
                    above[threshold] += duration

    time_to_flat: list[float] = []
    half_lives: list[float] = []
    in_episode = False
    episode_start: datetime | None = None
    episode_peak = 0.0
    half_recorded = False
    for item in ordered:
        non_flat = abs(item.inventory) > flat_epsilon
        if non_flat and not in_episode:
            in_episode = True
            episode_start = item.observed_at
            episode_peak = abs(item.inventory)
            half_recorded = False
        elif non_flat and in_episode:
            episode_peak = max(episode_peak, abs(item.inventory))
            if (
                not half_recorded
                and episode_start is not None
                and abs(item.inventory) <= episode_peak / 2.0
            ):
                half_lives.append((item.observed_at - episode_start).total_seconds())
                half_recorded = True
        elif not non_flat and in_episode:
            assert episode_start is not None
            time_to_flat.append((item.observed_at - episode_start).total_seconds())
            in_episode = False
            episode_start = None
            episode_peak = 0.0
            half_recorded = False

    def threshold_time(value: float) -> float | None:
        if inventory_limit is None or inventory_limit <= 0:
            return None
        return above[value]

    return InventorySummary(
        peak_abs_inventory=max(abs(item.inventory) for item in ordered),
        inventory_time_weighted_abs=(
            None if total_seconds <= 0 else weighted_inventory / total_seconds
        ),
        time_to_flat_distribution=tuple(time_to_flat),
        inventory_half_life=_median(half_lives),
        returns_to_flat_count=len(time_to_flat),
        time_above_25pct_limit=threshold_time(0.25),
        time_above_50pct_limit=threshold_time(0.50),
        time_above_75pct_limit=threshold_time(0.75),
        capital_seconds_consumed=capital_seconds,
        episode_count=len(time_to_flat) + int(in_episode),
    )


def ecology_summary(quotes: Sequence[Quote]) -> dict[str, object]:
    """Describe observable BBO state behaviour without inventing trader identities."""
    economic = _economic(quotes)
    lifetimes = [
        (right.observed_at - left.observed_at).total_seconds()
        for left, right in zip(economic, economic[1:], strict=False)
    ]
    spreads = [item.spread for item in economic]
    active_seconds = (
        0.0
        if len(economic) < 2
        else (economic[-1].observed_at - economic[0].observed_at).total_seconds()
    )
    return {
        "bbo_renewal_count": max(0, len(economic) - 1),
        "bbo_renewal_rate_per_second": (
            None
            if active_seconds <= 0
            else max(0, len(economic) - 1) / active_seconds
        ),
        "bbo_state_lifetime_seconds_p50": _median(lifetimes),
        "spread_p50": _median(spreads),
        "spread_min": None if not spreads else min(spreads),
        "spread_max": None if not spreads else max(spreads),
        "terminology": (
            "BBO state lifetime; no anonymous participant identity is inferred."
        ),
    }


def recommend_market(
    *,
    sample_count: int,
    independent_event_count: int,
    expected_edge: float | None,
    adverse_selection: float | None,
    capital_time_efficiency: float | None,
) -> tuple[MarketRecommendation, tuple[str, ...]]:
    """Create a research-only recommendation with explicit support gates."""
    if sample_count < 5 or independent_event_count < 5:
        return MarketRecommendation.NO_DATA, ("INSUFFICIENT_INDEPENDENT_EVENTS",)
    if (
        adverse_selection is not None
        and expected_edge is not None
        and adverse_selection > max(0.0, expected_edge)
    ):
        return MarketRecommendation.WIDEN, ("ADVERSE_SELECTION_EXCEEDS_EDGE",)
    if expected_edge is not None and expected_edge < 0:
        return MarketRecommendation.PAUSE, ("NEGATIVE_EXPECTED_EDGE",)
    if (
        expected_edge is not None
        and expected_edge > 0
        and capital_time_efficiency is not None
        and capital_time_efficiency > 0
    ):
        return MarketRecommendation.QUOTE_MORE, ("POSITIVE_EDGE_AND_CAPITAL_EFFICIENCY",)
    return MarketRecommendation.QUOTE_NORMAL, ("NO_MATERIAL_NEGATIVE_EVIDENCE",)


def _load_quotes(
    root: Path,
    *,
    stream: str,
    identity_column: str,
    identities: set[str],
    sig: bool,
) -> dict[str, tuple[Quote, ...]]:
    files = sorted((root / stream).rglob("*.parquet"))
    if not files or not identities:
        return {}
    dataset = ds.dataset([str(path) for path in files], format="parquet")
    required = {identity_column, "observed_at", "best_bid", "best_ask"}
    if not required <= set(dataset.schema.names):
        return {}
    columns = [identity_column, "observed_at", "best_bid", "best_ask"]
    for optional in ("bid_depth", "ask_depth", "book_valid", "trust_state"):
        if optional in dataset.schema.names:
            columns.append(optional)
    filter_expression = ds.field(identity_column).isin(sorted(identities))
    if sig and "event_type" in dataset.schema.names:
        columns.append("event_type")
        filter_expression &= ds.field("event_type") == "BBO_SNAPSHOT"
    result: dict[str, list[Quote]] = defaultdict(list)
    scanner = dataset.scanner(
        columns=columns,
        filter=filter_expression,
        batch_size=65_536,
    )
    for batch in scanner.to_batches():
        for row in batch.to_pylist():
            if row.get("book_valid") is False:
                continue
            identity = row.get(identity_column)
            observed = row.get("observed_at")
            bid = row.get("best_bid")
            ask = row.get("best_ask")
            if not isinstance(identity, str) or not isinstance(observed, datetime):
                continue
            try:
                bid_value = float(str(bid))
                ask_value = float(str(ask))
            except (TypeError, ValueError):
                continue
            if ask_value < bid_value:
                continue
            trust = row.get("trust_state")
            result[identity].append(
                Quote(
                    observed_at=observed.astimezone(UTC),
                    best_bid=bid_value,
                    best_ask=ask_value,
                    bid_depth=(
                        None
                        if row.get("bid_depth") is None
                        else float(str(row["bid_depth"]))
                    ),
                    ask_depth=(
                        None
                        if row.get("ask_depth") is None
                        else float(str(row["ask_depth"]))
                    ),
                    trusted=trust not in {"UNTRUSTED", "INVALID", "STALE"},
                )
            )
    return {
        key: tuple(sorted(values, key=lambda item: item.observed_at))
        for key, values in result.items()
    }


def _align_quotes(
    quotes: Sequence[Quote],
    direction: MappingDirection,
) -> tuple[Quote, ...]:
    if direction is MappingDirection.SAME:
        return tuple(quotes)
    if direction is not MappingDirection.COMPLEMENT:
        raise ValueError("direct quote alignment requires SAME or COMPLEMENT")
    return tuple(
        Quote(
            observed_at=item.observed_at,
            best_bid=1.0 - item.best_ask,
            best_ask=1.0 - item.best_bid,
            bid_depth=item.ask_depth,
            ask_depth=item.bid_depth,
            trusted=item.trusted,
        )
        for item in quotes
    )


def analyze_maker_outcomes(
    path: Path | None,
) -> tuple[dict[str, object], list[dict[str, object]]]:
    """Aggregate LIVE-LEARN quote economics already backed by BUILD-009 fill evidence."""
    if path is None or not path.exists():
        return {"available": False, "reason": "LIVE_LEARN_OUTCOMES_UNAVAILABLE"}, []
    rows: list[dict[str, object]] = []
    import json

    with path.open("r", encoding="utf-8") as handle:
        for raw in handle:
            if not raw.strip():
                continue
            value = json.loads(raw)
            if not isinstance(value, dict):
                continue
            outcome = outcome_from_record(value)
            if outcome.strategy_family != "MAKE":
                continue
            if outcome.outcome_status is not OutcomeStatus.MATURED_SCORED:
                continue
            metrics = dict(outcome.metric_values)
            if not any(
                name in metrics
                for name in (
                    "fill_rate",
                    "spread_capture",
                    "post_fill_markout",
                    "adverse_selection",
                )
            ):
                continue
            rows.append(
                {
                    "decision_id": outcome.decision_id,
                    "candidate_id": outcome.candidate_id,
                    "market_id": outcome.dimensions.get("market"),
                    "horizon_seconds": outcome.horizon_seconds,
                    "fill_rate": metrics.get("fill_rate"),
                    "spread_capture": metrics.get("spread_capture"),
                    "post_fill_markout": metrics.get("post_fill_markout"),
                    "adverse_selection": metrics.get("adverse_selection"),
                    "fill_latency_seconds": metrics.get("fill_latency_seconds"),
                    "evidence_refs": list(outcome.evidence_source_ids),
                    "status": outcome.outcome_status.value,
                }
            )
    decisions = {str(row["decision_id"]) for row in rows}
    filled = [
        float(str(row["fill_rate"]))
        for row in rows
        if row["fill_rate"] is not None
    ]
    adverse = [
        float(str(row["adverse_selection"]))
        for row in rows
        if row["adverse_selection"] is not None
    ]
    spread = [
        float(str(row["spread_capture"]))
        for row in rows
        if row["spread_capture"] is not None
    ]
    summary = {
        "available": True,
        "sample_count": len(rows),
        "independent_event_count": len(decisions),
        "fill_rate_p50": _median(filled),
        "spread_capture_p50": _median(spread),
        "adverse_selection_p50": _median(adverse),
        "accounting_boundary": (
            "Diagnostic quote economics only; authoritative realised/unrealised "
            "P&L remains RISK-002."
        ),
    }
    return summary, rows


def _write_rows(path: Path, rows: list[dict[str, object]]) -> None:
    if rows:
        path.parent.mkdir(parents=True, exist_ok=True)
        pq.write_table(pa.Table.from_pylist(rows), path)


def analyze_live_diagnostics(
    *,
    sig_root: Path,
    polymarket_root: Path | None,
    mapping_path: Path | None,
    output_root: Path | None = None,
    live_learn_outcomes: Path | None = None,
    latency_ms: float = 100.0,
    latency_assumption_source: str = "fixed_sensitivity",
    thresholds_ticks: Sequence[int] = DEFAULT_THRESHOLDS_TICKS,
    horizons_seconds: Sequence[int] = DEFAULT_HORIZONS_SECONDS,
) -> dict[str, object]:
    """Run the first-hours economic layer without creating a new market-data process."""
    generated_at = datetime.now(UTC)
    config = {
        "analysis_version": ANALYSIS_VERSION,
        "latency_ms": latency_ms,
        "latency_assumption_source": latency_assumption_source,
        "thresholds_ticks": list(thresholds_ticks),
        "horizons_seconds": list(horizons_seconds),
        "tick_size": 0.01,
        "trigger_rule": "threshold_crossing_rearms_only_below_threshold",
    }
    config_hash = canonical_config_hash(config)
    git_sha = os.environ.get("PREDICTIONS_CUP_GIT_SHA", "UNKNOWN")
    maker_summary, maker_rows = analyze_maker_outcomes(live_learn_outcomes)
    if (
        polymarket_root is None
        or mapping_path is None
        or not polymarket_root.exists()
        or not mapping_path.exists()
    ):
        snapshot: dict[str, object] = {
            "analysis_id": "LIVE-DIAG-001",
            "analysis_version": ANALYSIS_VERSION,
            "generated_at": generated_at.isoformat(),
            "git_sha": git_sha,
            "config_hash": config_hash,
            "status": ResearchStatus.INSUFFICIENT_EVIDENCE.value,
            "reasons": ["MISSING_POLYMARKET_OR_MAPPING_INPUT"],
            "maker_economics": maker_summary,
        }
        if output_root is not None:
            write_evidence(output_root=output_root, snapshot=snapshot, records=())
            _write_rows(output_root / "maker_economics.parquet", maker_rows)
        return snapshot

    document = load_document(mapping_path)
    direct = [
        record
        for record in document.records
        if record.direct_polymarket is not None
        and record.mapping_direction
        in {MappingDirection.SAME, MappingDirection.COMPLEMENT}
    ]
    exchange_ids = {record.sig_exchange_id for record in direct}
    token_ids = {
        record.direct_polymarket.mapped_token_id
        for record in direct
        if record.direct_polymarket is not None
    }
    sig_quotes = _load_quotes(
        sig_root,
        stream="normalized_events",
        identity_column="exchange_id",
        identities=exchange_ids,
        sig=True,
    )
    pm_quotes = _load_quotes(
        polymarket_root,
        stream="observations",
        identity_column="token_id",
        identities=token_ids,
        sig=False,
    )
    mapping_hash = sha256_file(mapping_path)
    snapback_rows: list[dict[str, object]] = []
    lead_lag_rows: list[dict[str, object]] = []
    ecology_rows: list[dict[str, object]] = []
    evidence: list[AnalysisEvidence] = []

    for record in direct:
        identity = record.direct_polymarket
        direction = record.mapping_direction
        assert identity is not None and direction is not None
        sig = sig_quotes.get(record.sig_exchange_id, ())
        pm = _align_quotes(pm_quotes.get(identity.mapped_token_id, ()), direction)
        ecology_rows.append(
            {
                "market_id": record.sig_market_id,
                "exchange_id": record.sig_exchange_id,
                **ecology_summary(sig),
            }
        )
        for threshold in thresholds_ticks:
            triggers = construct_gap_episodes(
                sig_quotes=sig,
                external_quotes=pm,
                threshold_ticks=threshold,
            )
            observations = observe_snapback(
                triggers=triggers,
                sig_quotes=sig,
                external_quotes=pm,
                horizons_seconds=horizons_seconds,
            )
            summary = summarize_snapback(
                triggers=triggers,
                observations=observations,
                horizons_seconds=horizons_seconds,
            )
            snapback_rows.append(
                {
                    "market_id": record.sig_market_id,
                    "exchange_id": record.sig_exchange_id,
                    "polymarket_token_id": identity.mapped_token_id,
                    "mapping_class": record.mapping_class.value,
                    "mapping_direction": direction.value,
                    "threshold_ticks": threshold,
                    **summary,
                }
            )
            for horizon in horizons_seconds:
                values = [
                    item.fraction_closed
                    for item in observations
                    if item.horizon_seconds == horizon
                ]
                metric = _median(values)
                event_count = len(triggers)
                evidence.append(
                    AnalysisEvidence(
                        analysis_id="LIVE-DIAG-001:SNAPBACK",
                        analysis_version=ANALYSIS_VERSION,
                        generated_at=generated_at,
                        window_start=(
                            None
                            if not sig
                            else min(item.observed_at for item in sig)
                        ),
                        window_end=(
                            None
                            if not sig
                            else max(item.observed_at for item in sig)
                        ),
                        git_sha=git_sha,
                        config_hash=config_hash,
                        mapping_hash=mapping_hash,
                        source_watermarks=tuple(
                            (
                                source,
                                series[-1].observed_at.isoformat(),
                            )
                            for source, series in (("sig", sig), ("polymarket", pm))
                            if series
                        ),
                        source_health=(
                            ("sig", "AVAILABLE" if sig else "MISSING"),
                            ("polymarket", "AVAILABLE" if pm else "MISSING"),
                        ),
                        market_id=record.sig_market_id,
                        exchange_id=record.sig_exchange_id,
                        risk_group_ids=(),
                        sample_count=len(values),
                        independent_event_count=event_count,
                        metric_name=f"fraction_gap_closed_{horizon}s",
                        metric_value=metric,
                        uncertainty=_bootstrap_median(values),
                        denominator="independent_gap_episodes",
                        status=(
                            ResearchStatus.INSUFFICIENT_EVIDENCE.value
                            if event_count < 5
                            else ResearchStatus.DESCRIPTIVE_ONLY.value
                        ),
                        reasons=(
                            "RISK_GROUP_PROVIDER_UNAVAILABLE",
                            *(
                                ("INSUFFICIENT_INDEPENDENT_EVENTS",)
                                if event_count < 5
                                else ()
                            ),
                        ),
                        evidence_refs=(
                            f"sig:{record.sig_exchange_id}",
                            f"pm:{identity.mapped_token_id}",
                        ),
                        horizon_seconds=horizon,
                        metric_unit="fraction",
                        method_version="snapback-threshold-episodes-v1",
                        source_versions=(
                            ("mapping", str(document.schema_version)),
                        ),
                    )
                )

        for item in analyze_lead_lag(
            sig_quotes=sig,
            external_quotes=pm,
            latency_ms=latency_ms,
        ):
            lead_lag_rows.append(
                {
                    "market_id": record.sig_market_id,
                    "exchange_id": record.sig_exchange_id,
                    "polymarket_token_id": identity.mapped_token_id,
                    "mapping_direction": direction.value,
                    "impulse_id": item.impulse_id,
                    "external_observed_at": item.external_observed_at.isoformat(),
                    "external_move": item.external_move,
                    "lead_seconds": item.lead_seconds,
                    "sig_response_at": (
                        None
                        if item.sig_response_at is None
                        else item.sig_response_at.isoformat()
                    ),
                    "sig_response_definition": "first_material_midpoint_move",
                    "same_direction": item.same_direction,
                    "sig_bid_at_trigger": item.sig_bid_at_trigger,
                    "sig_ask_at_trigger": item.sig_ask_at_trigger,
                    "available_depth": item.available_depth,
                    "gross_executable_edge": item.gross_executable_edge,
                    "latency_assumption_source": latency_assumption_source,
                    "latency_ms": item.latency_ms,
                    "latency_adjusted_executable_price": (
                        item.latency_adjusted_executable_price
                    ),
                    "latency_adjusted_edge": item.latency_adjusted_edge,
                    "signal_half_life": item.signal_half_life,
                    "status": item.status.value,
                    "reasons": list(item.reasons),
                }
            )

    snapshot = {
        "analysis_id": "LIVE-DIAG-001",
        "analysis_version": ANALYSIS_VERSION,
        "generated_at": generated_at.isoformat(),
        "git_sha": git_sha,
        "config_hash": config_hash,
        "mapping_hash": mapping_hash,
        "source_health": {
            "sig_markets_with_bbo": len(sig_quotes),
            "pm_tokens_with_bbo": len(pm_quotes),
        },
        "latency": {
            "source": latency_assumption_source,
            "latency_ms": latency_ms,
        },
        "snapback": snapback_rows,
        "lead_lag": lead_lag_rows,
        "maker_economics": maker_summary,
        "opponent_venue_ecology": ecology_rows,
        "inventory_recycling": {
            "status": "AWAITING_CANONICAL_INVENTORY_HISTORY",
            "reason": (
                "RISK-002 current state is authoritative; "
                "no separate history is inferred here."
            ),
        },
        "risk_group_status": "UNAVAILABLE_NOT_INFERRED",
        "limitations": [
            "Passive touch is not treated as a fill.",
            "Depth-unavailable active opportunities fail as INSUFFICIENT_DEPTH.",
            "No participant identity is inferred from aggregate BBO behaviour.",
            (
                "Maker economics use LIVE-LEARN diagnostics backed by BUILD-009 "
                "fill evidence; authoritative accounting P&L remains RISK-002."
            ),
        ],
    }
    if output_root is not None:
        write_evidence(
            output_root=output_root,
            snapshot=snapshot,
            records=tuple(evidence),
        )
        _write_rows(output_root / "snapback.parquet", snapback_rows)
        _write_rows(output_root / "lead_lag.parquet", lead_lag_rows)
        _write_rows(output_root / "maker_economics.parquet", maker_rows)
        _write_rows(
            output_root / "opponent_venue_ecology.parquet",
            ecology_rows,
        )
    return snapshot
