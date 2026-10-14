#!/usr/bin/env python3
"""LANE-5 offline recorder A/B microrehearsal. NO LIVE clients or credentials.

This is a synthetic asyncio scheduling/control-latency PROXY, NOT an actual
MakerRuntimeLoop decision, order cancellation or exchange ACK benchmark.
Run on an isolated research machine:
    python scripts/research/lane5_depth_mirror_perf.py --iterations 3000
    python scripts/research/lane5_depth_mirror_perf.py --iterations 3000 --burst
Numbers are environment-specific; no pass/fail latency threshold is hardcoded.
"""
from __future__ import annotations

import argparse
import asyncio
import gc
import json
import queue
import resource
import tempfile
import threading
import time
import tracemalloc
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from predictions_cup.models.market import OrderBook, OrderBookLevel
from predictions_cup.sig.depth_mirror import MakerDepthMirror


def percentiles(samples: list[int]) -> dict[str, float]:
    if not samples:
        return {}
    ordered = sorted(samples)
    def at(frac: float) -> float:
        index = (len(ordered) - 1) * frac
        lo = int(index)
        hi = min(lo + 1, len(ordered) - 1)
        return (ordered[lo] + (ordered[hi] - ordered[lo]) * (index - lo)) / 1000
    return {
        "p50_us": round(at(.5), 3),
        "p95_us": round(at(.95), 3),
        "p99_us": round(at(.99), 3),
        "p99_9_us": round(at(.999), 3),
        "max_us": round(ordered[-1] / 1000, 3),
        "over_10ms": sum(n > 10_000_000 for n in samples),
        "n": len(samples),
    }


def book_with_levels(levels: int) -> OrderBook:
    # Created once, outside every measured hot path.
    bids = tuple(
        OrderBookLevel(
            price=Decimal(4000 - i) / 10000,
            quantity=Decimal(1 + i),
        )
        for i in range(levels)
    )
    asks = tuple(
        OrderBookLevel(
            price=Decimal(6000 + i) / 10000,
            quantity=Decimal(1 + i),
        )
        for i in range(levels)
    )
    return OrderBook(
        exchange_id="lane5-synthetic", bids=bids, asks=asks,
        timestamp=datetime(2026, 10, 14, tzinfo=UTC), source="sig-rest",
    )


async def run_case(
    name: str, iterations: int, levels: int, *, pressure: bool = False,
) -> dict[str, object]:
    mirror: MakerDepthMirror | None = None
    release = threading.Event()
    writer_entered = threading.Event()
    pressure_stop = threading.Event()
    thread: threading.Thread | None = None
    book = book_with_levels(levels)
    observed_at = datetime(2026, 10, 14, tzinfo=UTC)
    loop = asyncio.get_running_loop()
    control_ns: list[int] = []
    ack_ns: list[int] = []
    cancel_ns: list[int] = []
    decisions_ns: list[int] = []
    event_loop_ns: list[int] = []
    temp_dir = tempfile.TemporaryDirectory(prefix="lane5-perf-")
    try:
        if name not in ("disabled", "queue_only"):
            mirror = MakerDepthMirror(Path(temp_dir.name) / "isolated.jsonl", queue_size=128)
            if name in ("blocked_writer", "saturated", "blocked_pressure"):
                original = mirror._book_row
                def blocked(item: object) -> dict[str, object]:
                    writer_entered.set()
                    release.wait(timeout=10.0)
                    return original(item)  # type: ignore[arg-type]
                mirror._book_row = blocked  # type: ignore[method-assign]
            elif name == "disk_failure":
                def failed(_item: object) -> dict[str, object]:
                    raise OSError("LANE5 simulated ENOSPC")
                mirror._book_row = failed  # type: ignore[method-assign]
            mirror.start()
            if name in ("blocked_writer", "saturated", "blocked_pressure"):
                mirror.record(book, "market", "cup", observed_at, 42)
                assert writer_entered.wait(timeout=2), "writer did not enter fixture"
                # Permanently fill the producer queue while worker is blocked.
                for _ in range(128):
                    mirror.record(book, "market", "cup", observed_at, 42)
                assert mirror.snapshot()["queue_pending"] == 128

        full_queue: queue.Queue[None] | None = None
        if name == "queue_only":
            full_queue = queue.Queue(maxsize=1)
            full_queue.put_nowait(None)

        if pressure:
            def busy() -> None:
                x = 1
                while not pressure_stop.is_set():
                    for _ in range(4000):
                        x = (x * 33 + 17) & 0xFFFF
                    gc.collect()
            thread = threading.Thread(target=busy, daemon=True)
            thread.start()

        # Real event-loop periodic timer, independent of producer callback.
        stop_heartbeat = asyncio.Event()
        async def heartbeat() -> None:
            target = loop.time() + .002
            while not stop_heartbeat.is_set():
                await asyncio.sleep(max(0.0, target - loop.time()))
                t = loop.time()
                event_loop_ns.append(int(max(0, t - target) * 1_000_000_000))
                target = max(target + .002, t + .002)
        beat = asyncio.create_task(heartbeat())
        try:
            tracemalloc.start()
            tracemalloc.reset_peak()
            cpu_start = time.process_time_ns()
            wall_start = time.perf_counter_ns()
            for _ in range(iterations):
                # Scheduled callbacks act as synthetic cancellation/ACK
                # event-loop proxies; they do NOT hit an execution sink.
                target_ns = time.perf_counter_ns()
                loop.call_soon(lambda t=target_ns: cancel_ns.append(time.perf_counter_ns() - t))
                loop.call_soon(lambda t=target_ns: ack_ns.append(time.perf_counter_ns() - t))
                decision_start = time.perf_counter_ns()
                now = time.perf_counter_ns()
                if mirror is not None:
                    mirror.record(book, "market", "cup", observed_at, 42)
                elif full_queue is not None:
                    try:
                        full_queue.put_nowait(None)
                    except queue.Full:
                        pass
                control_ns.append(time.perf_counter_ns() - now)
                # Fixed synthetic decision work, identical across conditions.
                checksum = (17 * 31 + 19 * 37) % 997
                if checksum != 233:  # keep the work non-inert
                    raise AssertionError("unreachable synthetic workload check")
                decisions_ns.append(time.perf_counter_ns() - decision_start)
                await asyncio.sleep(0)
            await asyncio.sleep(.01)
            wall_ns = time.perf_counter_ns() - wall_start
            cpu_ns = time.process_time_ns() - cpu_start
            _, peak_bytes = tracemalloc.get_traced_memory()
            tracemalloc.stop()
        finally:
            stop_heartbeat.set()
            await beat
        out: dict[str, object] = {
            "case": name, "iterations": iterations, "levels_per_side": levels,
            "capture_callback": percentiles(control_ns),
            "synthetic_decision": percentiles(decisions_ns),
            "synthetic_cancel_dispatch": percentiles(cancel_ns),
            "synthetic_ack_dispatch": percentiles(ack_ns),
            "event_loop_timer_lateness": percentiles(event_loop_ns),
            "process_cpu_ms": round(cpu_ns / 1e6, 2),
            "wall_ms": round(wall_ns / 1e6, 2),
            "python_alloc_peak_bytes": peak_bytes,
            "process_peak_rss_kb": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        }
        return out
    finally:
        pressure_stop.set()
        if thread is not None:
            thread.join(timeout=2)
        release.set()
        if mirror is not None:
            mirror.close(timeout=2)
            if "out" in locals():
                out["capture_counters"] = mirror.snapshot()
        temp_dir.cleanup()


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--iterations", type=int, default=3000)
    parser.add_argument("--burst", action="store_true", help="10x volume, 200 levels per side")
    args = parser.parse_args()
    if args.iterations < 1000:
        parser.error("--iterations must be >=1000 for a meaningful p99.9")
    n = args.iterations * (10 if args.burst else 1)
    levels = 200 if args.burst else 20
    print(json.dumps({
        "schema": "lane5-offline-perf-v1", "python": __import__("sys").version.split()[0],
        "burst": args.burst, "synthetic_only": True,
        "source_clock_verified": False, "is_live_measurement": False,
    }, sort_keys=True))
    for name in ("disabled", "enabled", "queue_only", "saturated",
                 "blocked_writer", "disk_failure", "blocked_pressure"):
        case = await run_case(name, n, levels, pressure=name == "blocked_pressure")
        print(json.dumps(case, sort_keys=True, default=str), flush=True)


if __name__ == "__main__":
    asyncio.run(main())
