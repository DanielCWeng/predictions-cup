"""Bounded synthetic burst benchmark for CAPTURE-001 research persistence."""

from __future__ import annotations

import argparse
import json
import shutil
import time
import tracemalloc
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow.dataset as ds

from predictions_cup.sig.launch_storage import LaunchSigRecorder


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Benchmark CAPTURE-001 persistence")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--rows", type=int, default=50_000)
    parser.add_argument("--queue-max", type=int, default=200_000)
    parser.add_argument("--max-rows-per-shard", type=int, default=5_000)
    parser.add_argument("--shard-seconds", type=int, default=60)
    return parser.parse_args()


def _percentiles(values: list[float]) -> dict[str, float]:
    array = np.asarray(values, dtype=float)
    p50, p95, p99 = np.percentile(array, [50, 95, 99])
    return {"p50": float(p50), "p95": float(p95), "p99": float(p99)}


def run_benchmark(
    *,
    output: Path,
    rows: int,
    queue_max: int,
    max_rows_per_shard: int,
    shard_seconds: int,
) -> dict[str, Any]:
    if rows <= 0:
        raise ValueError("rows must be positive")
    if output.exists():
        shutil.rmtree(output)
    output.mkdir(parents=True)

    research = output / "sig_research"
    recorder = LaunchSigRecorder(
        output / "sig.sqlite3",
        research_root=research,
        queue_max=queue_max,
        max_rows_per_shard=max_rows_per_shard,
        shard_seconds=shard_seconds,
        session_id="capture-001-benchmark",
    )
    base = datetime.now(UTC)
    recorder.record_connection_boundary(
        observed_at=base,
        reason="benchmark",
    )

    emit_us: list[float] = []
    tracemalloc.start()
    started = time.perf_counter()
    for index in range(rows):
        observed_at = base + timedelta(microseconds=index)
        payload = {
            "trades": [],
            "bookDirty": [],
            "marketSettled": [],
            "delivery": {
                "revision": index + 1,
                "previousRevision": index,
                "correlationId": f"bench-{index}",
                "sourceSequenceFrom": index,
                "sourceSequenceThrough": index,
            },
        }
        before = time.perf_counter_ns()
        recorder.record_raw_batch(
            topic="tournament:benchmark",
            payload=payload,
            observed_at=observed_at,
            monotonic_receive_ns=time.monotonic_ns(),
            parsed_at=observed_at,
            validation_error=None,
        )
        emit_us.append((time.perf_counter_ns() - before) / 1_000.0)
    producer_seconds = time.perf_counter() - started
    health_before_close = recorder.capture_health_snapshot()
    recorder.close()
    end_to_end_seconds = time.perf_counter() - started
    _, peak_python_bytes = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    parquet_files = sorted(research.rglob("*.parquet"))
    parquet_bytes = sum(path.stat().st_size for path in parquet_files)
    raw_files = sorted((research / "raw_events").rglob("*.parquet"))
    read_started = time.perf_counter()
    raw_rows = (
        0
        if not raw_files
        else ds.dataset(
            [str(path) for path in raw_files],
            format="parquet",
        ).count_rows()
    )
    read_seconds = time.perf_counter() - read_started

    result: dict[str, Any] = {
        "rows_emitted": rows,
        "raw_rows_read_back": raw_rows,
        "emit_latency_us": _percentiles(emit_us),
        "producer_seconds": producer_seconds,
        "producer_rows_per_second": rows / producer_seconds,
        "end_to_end_seconds": end_to_end_seconds,
        "end_to_end_rows_per_second": rows / end_to_end_seconds,
        "readback_seconds": read_seconds,
        "published_parquet_files": len(parquet_files),
        "published_parquet_bytes": parquet_bytes,
        "python_peak_tracemalloc_bytes": peak_python_bytes,
        "queue_depth_before_close": health_before_close["queue_depth"],
        "queue_capacity": health_before_close["queue_capacity"],
        "queue_high_water": health_before_close["queue_high_water"],
        "dropped_rows": health_before_close["dropped_rows"],
        "storage_failures": health_before_close["storage_failures"],
        "shard_seconds": shard_seconds,
        "max_rows_per_shard": max_rows_per_shard,
    }
    (output / "benchmark.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return result


def main() -> int:
    args = parse_args()
    result = run_benchmark(
        output=args.output,
        rows=args.rows,
        queue_max=args.queue_max,
        max_rows_per_shard=args.max_rows_per_shard,
        shard_seconds=args.shard_seconds,
    )
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
