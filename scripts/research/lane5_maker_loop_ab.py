#!/usr/bin/env python3
"""LANE-5 exact-source offline MakerRuntimeLoop A/B with optional depth mirror.

Uses the repository's proven synthetic MAKE-001 fixture and actual
MakerRuntimeLoop.notify_sig/drain_once/engine.quote paths. No execution adapter,
SigLiveSink, REST client, HTTP, venue credentials or order placement.
Cancellation/ACK dispatch timings are still *asyncio proxy callbacks*, never
exchange cancellation or ACK processing. NOT a West deployment acceptance test.
"""
from __future__ import annotations

import argparse
import asyncio
import gc
import json
import sys
import tempfile
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from predictions_cup.maker.runtime_loop import MakerRuntimeLoop
from predictions_cup.sig.depth_mirror import MakerDepthMirror

from lane5_depth_mirror_perf import book_with_levels, percentiles


def maker_fixture() -> tuple[MakerRuntimeLoop, str]:
    # Internal benchmark fixture is offline and returns decisions without a
    # LIVE sink. Import it from scripts without changing project packaging.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import benchmark_make001 as existing

    path = Path("data/mappings/sig_polymarket_2026.json")
    _document, engine, snapshots = existing._build_fixture(path)
    if not snapshots:
        raise RuntimeError("no synthetic tradeable maker fixture available")
    snapshot = snapshots[sorted(snapshots)[0]]
    runtime = MakerRuntimeLoop(
        bridge=existing.cast(existing.MakerSourceBridge,
                             existing._BenchmarkBridge(snapshot)),
        coordinator=existing.cast(existing.MakerCoordinator,
                                  existing._BenchmarkCoordinator(engine)),
        polymarket_feed_trusted=lambda: True,
    )
    return runtime, snapshot.exchange_id


async def run_case(name: str, n: int, levels: int) -> dict[str, Any]:
    runtime, exchange_id = maker_fixture()
    book = book_with_levels(levels)
    observed_at = datetime(2026, 10, 14, tzinfo=UTC)
    recorder: MakerDepthMirror | None = None
    release = threading.Event()
    blocked = threading.Event()
    pressure_stop = threading.Event()
    cpu_thread: threading.Thread | None = None
    loop = asyncio.get_running_loop()
    mirror_cb_ns: list[int] = []
    maker_cycles_ns: list[int] = []
    cancel_proxy_ns: list[int] = []
    ack_proxy_ns: list[int] = []
    timer_ns: list[int] = []
    with tempfile.TemporaryDirectory(prefix="lane5-maker-ab-") as directory:
        try:
            if name not in ("off", "off_paced", "off_cpu_gc"):
                recorder = MakerDepthMirror(Path(directory) / "mirror.jsonl")
                if name in ("blocked", "blocked_cpu_gc"):
                    orig = recorder._book_row

                    def held(item: object) -> dict[str, Any]:
                        blocked.set()
                        release.wait(timeout=10)
                        return orig(item)  # type: ignore[arg-type]

                    recorder._book_row = held  # type: ignore[method-assign]
                recorder.start()
                if name in ("blocked", "blocked_cpu_gc"):
                    recorder.record(book, "market", "cup", observed_at, 17)
                    assert blocked.wait(timeout=2)
                    for _ in range(128):
                        recorder.record(book, "market", "cup", observed_at, 17)

            if name.endswith("_cpu_gc"):
                def busy() -> None:
                    x = 1
                    while not pressure_stop.is_set():
                        for _ in range(2000):
                            x = (x * 33 + 17) & 65535
                        gc.collect()
                cpu_thread = threading.Thread(target=busy, daemon=True)
                cpu_thread.start()

            stop_timer = asyncio.Event()
            async def timer() -> None:
                due = loop.time() + .002
                while not stop_timer.is_set():
                    await asyncio.sleep(max(0, due - loop.time()))
                    at = loop.time()
                    timer_ns.append(int(max(0, at - due) * 1e9))
                    due = max(due + .002, at + .002)

            heartbeat = asyncio.create_task(timer())
            started_cpu = time.process_time_ns()
            started_wall = time.perf_counter_ns()
            try:
                for _ in range(n):
                    t = time.perf_counter_ns()
                    loop.call_soon(lambda t=t: cancel_proxy_ns.append(time.perf_counter_ns() - t))
                    loop.call_soon(lambda t=t: ack_proxy_ns.append(time.perf_counter_ns() - t))
                    if recorder is not None:
                        e = time.perf_counter_ns()
                        recorder.record(book, "market", "cup", observed_at, 17)
                        mirror_cb_ns.append(time.perf_counter_ns() - e)
                    decision_start = time.perf_counter_ns()
                    runtime.notify_sig({exchange_id}, observed_monotonic_ns=decision_start)
                    await runtime.drain_once()  # ACTUAL maker quote/coordinator cycle
                    maker_cycles_ns.append(time.perf_counter_ns() - decision_start)
                    await asyncio.sleep(0)
                    if name.endswith("_paced"):
                        await asyncio.sleep(.002)
                await asyncio.sleep(.01)
            finally:
                stop_timer.set()
                await heartbeat
            result: dict[str, Any] = {
                "case": name,
                "levels_per_side": levels,
                "n_maker_cycles": n,
                "actual_maker_loop_cycles": percentiles(maker_cycles_ns),
                "capture_callback": percentiles(mirror_cb_ns),
                "synthetic_cancel_dispatch": percentiles(cancel_proxy_ns),
                "synthetic_ack_dispatch": percentiles(ack_proxy_ns),
                "event_loop_timer_lateness": percentiles(timer_ns),
                "process_cpu_ms": round((time.process_time_ns() - started_cpu) / 1e6, 3),
                "wall_ms": round((time.perf_counter_ns() - started_wall) / 1e6, 3),
                "has_actual_exchange_ack_or_cancel": False,
                "does_not_send_orders": True,
                "rest_calls": 0,
            }
            return result
        finally:
            pressure_stop.set()
            if cpu_thread is not None:
                cpu_thread.join(timeout=2)
            release.set()
            if recorder is not None:
                recorder.close(timeout=2)
                if "result" in locals():
                    result["capture_counters"] = recorder.snapshot()


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--iterations", type=int, default=1000)
    parser.add_argument("--burst", action="store_true")
    args = parser.parse_args()
    if args.iterations < 1000:
        parser.error("--iterations must be at least 1000")
    count = args.iterations * (10 if args.burst else 1)
    levels = 200 if args.burst else 20
    print(json.dumps({
        "schema": "lane5-offline-actual-maker-loop-v1", "synthetic_books": True,
        "uses_real_maker_runtime_loop": True,
        "uses_real_exchange_ack_or_cancel": False,
        "mode": "10x-burst" if args.burst else "baseline",
    }), flush=True)
    for name in ("off", "on", "off_paced", "on_paced",
                 "blocked", "off_cpu_gc", "blocked_cpu_gc"):
        iterations = min(count, 1000) if name.endswith("_paced") and args.burst else count
        print(json.dumps(await run_case(name, iterations, levels), sort_keys=True), flush=True)


if __name__ == "__main__":
    asyncio.run(main())
