"""Machine-readable OBSERVE-001 summaries and lifecycle replay."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable

import numpy as np

from predictions_cup.observe.contracts import ObservationKind, VenueObservation
from predictions_cup.observe.cross_venue import CrossVenueResponse
from predictions_cup.observe.spans import VenueSpanCollector


def _percentiles(values: list[float]) -> dict[str, float | None]:
    if not values:
        return {"p50": None, "p95": None, "p99": None}
    array = np.asarray(values, dtype=float)
    p50, p95, p99 = np.percentile(array, [50, 95, 99])
    return {"p50": float(p50), "p95": float(p95), "p99": float(p99)}


def replay_operation(
    observations: Iterable[VenueObservation],
    logical_operation_id: str,
) -> tuple[VenueObservation, ...]:
    return tuple(
        sorted(
            (
                item
                for item in observations
                if item.logical_operation_id == logical_operation_id
            ),
            key=lambda item: (
                item.observed_at_utc,
                item.process_instance_id,
                item.monotonic_ns,
            ),
        )
    )


def summarize_observations(
    observations: Iterable[VenueObservation],
) -> dict[str, object]:
    rows = tuple(observations)
    counts = Counter(item.kind.value for item in rows)
    spans = VenueSpanCollector().collect(rows)
    span_values: dict[str, list[float]] = {}
    for span in spans:
        span_values.setdefault(span.name.value, []).append(span.duration_ns / 1_000_000.0)

    by_operation: dict[str, Counter[ObservationKind]] = {}
    for item in rows:
        if item.logical_operation_id is None:
            continue
        by_operation.setdefault(item.logical_operation_id, Counter())[item.kind] += 1
    duplicate_ack = sum(
        max(0, counter[ObservationKind.ACK] - 1) for counter in by_operation.values()
    )
    duplicate_fill = sum(
        max(0, counter[ObservationKind.FILL] - 1) for counter in by_operation.values()
    )
    dispatches = counts[ObservationKind.REQUEST_DISPATCHED.value]
    uncertainty = counts[ObservationKind.UNCERTAIN.value]
    return {
        "event_counts": dict(sorted(counts.items())),
        "operations": len(by_operation),
        "latency_ms": {
            name: _percentiles(values) for name, values in sorted(span_values.items())
        },
        "rate_limit_429_count": counts[ObservationKind.RATE_LIMIT.value],
        "rate_limit_429_rate": (
            None
            if dispatches == 0
            else counts[ObservationKind.RATE_LIMIT.value] / dispatches
        ),
        "server_5xx_count": counts[ObservationKind.SERVER_ERROR.value],
        "server_5xx_rate": (
            None
            if dispatches == 0
            else counts[ObservationKind.SERVER_ERROR.value] / dispatches
        ),
        "transport_exception_count": counts[ObservationKind.TRANSPORT_EXCEPTION.value],
        "uncertainty_count": uncertainty,
        "uncertainty_rate": None if dispatches == 0 else uncertainty / dispatches,
        "reconnect_count": counts[ObservationKind.RECONNECT_RESOLVED.value],
        "realtime_revision_gap_count": counts[ObservationKind.REALTIME_REVISION_GAP.value],
        "replenishment_observation_count": counts[ObservationKind.QUOTE_REPLENISHED.value],
        "duplicate_ack_evidence": duplicate_ack,
        "duplicate_fill_evidence": duplicate_fill,
    }


def summarize_cross_venue(
    responses: Iterable[CrossVenueResponse],
) -> dict[str, object]:
    rows = tuple(responses)
    return {
        "matches": len(rows),
        "pm_to_sig_observed_response_seconds": _percentiles(
            [row.elapsed_seconds for row in rows]
        ),
        "same_direction_rate": (
            None if not rows else sum(row.same_direction for row in rows) / len(rows)
        ),
        "interpretation": (
            "Nearest subsequent mapped SIG economic change after a PM economic change; "
            "descriptive observable-time timing only, not causal lead-lag."
        ),
    }
