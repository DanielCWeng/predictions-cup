"""Combined OBSERVE-001 emitter + CAPTURE persistence benchmark."""

from __future__ import annotations

import argparse
import json
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

from predictions_cup.observe import (
    BoundedObservationEmitter,
    CaptureObservationSink,
    ObservationHealthProvider,
    ObservationKind,
    VenueObservation,
)
from predictions_cup.sig.launch_storage import ObservationCaptureRecorder


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--iterations", type=int, default=10_000)
    args = parser.parse_args()
    if args.iterations <= 0:
        raise ValueError("--iterations must be positive")

    with tempfile.TemporaryDirectory(prefix="observe001-") as directory:
        root = Path(directory)
        queue_max = max(1_024, args.iterations + 1)
        recorder = ObservationCaptureRecorder(
            root,
            queue_max=queue_max,
            shard_seconds=60,
            max_rows_per_shard=max(100_000, args.iterations + 1),
            session_id="observe-combined-benchmark",
        )
        emitter = BoundedObservationEmitter(
            CaptureObservationSink(recorder),
            queue_max=queue_max,
        )
        provider = ObservationHealthProvider(emitter, recorder)
        observation = VenueObservation(
            kind=ObservationKind.REQUEST_DISPATCHED,
            observed_at=datetime.now(UTC),
            monotonic_ns=time.monotonic_ns(),
            process_instance_id="observe-combined-benchmark",
            source="benchmark",
            source_version="observe-001",
            provenance="local",
            logical_operation_id="benchmark-operation",
        )

        started = time.perf_counter()
        for _ in range(args.iterations):
            if not emitter.emit(observation):
                raise RuntimeError("combined benchmark dropped an OBSERVE emission")
        produced = time.perf_counter()
        runtime_health = provider.health()
        emitter.close()
        recorder.close()
        finished = time.perf_counter()

        parquet_files = tuple((root / "venue_observations").rglob("*.parquet"))
        print(
            json.dumps(
                {
                    "iterations": args.iterations,
                    "producer_seconds": produced - started,
                    "producer_events_per_second": (
                        args.iterations / max(produced - started, 1e-12)
                    ),
                    "end_to_end_drain_seconds": finished - started,
                    "runtime_health": runtime_health.to_dict(),
                    "parquet_shards": len(parquet_files),
                },
                sort_keys=True,
            )
        )


if __name__ == "__main__":
    main()
