"""Microbenchmark synchronous OBSERVE-001 emission overhead."""

from __future__ import annotations

import argparse
import json
import statistics
import time
from datetime import UTC, datetime

import numpy as np

from predictions_cup.observe import (
    BoundedObservationEmitter,
    CallbackObservationSink,
    NullObservationEmitter,
    ObservationKind,
    VenueObservation,
)


def _sample(emitter: object, observation: VenueObservation, iterations: int) -> list[int]:
    emit = getattr(emitter, "emit")
    values: list[int] = []
    for _ in range(iterations):
        started = time.perf_counter_ns()
        emit(observation)
        values.append(time.perf_counter_ns() - started)
    return values


def _summary(values: list[int]) -> dict[str, float]:
    array = np.asarray(values, dtype=float)
    p95, p99 = np.percentile(array, [95, 99])
    return {
        "median_ns": float(statistics.median(values)),
        "p95_ns": float(p95),
        "p99_ns": float(p99),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--iterations", type=int, default=100_000)
    args = parser.parse_args()
    if args.iterations <= 0:
        raise ValueError("--iterations must be positive")

    observation = VenueObservation(
        kind=ObservationKind.REQUEST_DISPATCHED,
        observed_at=datetime.now(UTC),
        monotonic_ns=time.monotonic_ns(),
        process_instance_id="benchmark",
        source="benchmark",
        source_version="observe-001",
        provenance="local",
        logical_operation_id="benchmark-operation",
    )
    off = NullObservationEmitter()
    off_values = _sample(off, observation, args.iterations)

    on = BoundedObservationEmitter(
        CallbackObservationSink(lambda item: None),
        queue_max=max(1_024, args.iterations + 1),
    )
    on_values = _sample(on, observation, args.iterations)
    health = on.health()
    on.close()

    off_summary = _summary(off_values)
    on_summary = _summary(on_values)
    print(
        json.dumps(
            {
                "iterations": args.iterations,
                "off": off_summary,
                "on": on_summary,
                "incremental_median_ns": (
                    on_summary["median_ns"] - off_summary["median_ns"]
                ),
                "accepted": health.accepted,
                "dropped": health.dropped,
                "sink_failures": health.sink_failures,
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
