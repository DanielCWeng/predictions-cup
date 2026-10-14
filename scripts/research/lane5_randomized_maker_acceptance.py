#!/usr/bin/env python3
"""Pre-registered randomized OFF/ON safety screen: real maker, fake sink, no venue.

Actual MakerRuntimeLoop -> MakerCoordinator -> engine/lifecycle/risk and
ShadowMakerExecutionAdapter. Books are SYNTHETIC and never arrive from SIG.
Not a LIVE maker, real cancellation/ACK, host or prospective depth acceptance.
"""
from __future__ import annotations

import argparse
import asyncio
import gc
import hashlib
import json
import random
import resource
import socket
import sys
import tempfile
import threading
import time
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import patch

from predictions_cup.execution.models import ExecutionMode, LifecycleState
from predictions_cup.maker import (
    MakerCoordinator,
    MakerRuntimeLoop,
    QuoteLifecycleManager,
    QuoteRegistry,
    QuoteSide,
    ShadowMakerExecutionAdapter,
)
from predictions_cup.risk.core import RiskContext
from predictions_cup.sig.depth_mirror import MakerDepthMirror

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import benchmark_make001 as make_fixture  # noqa: E402
from lane5_depth_mirror_perf import book_with_levels, percentiles  # noqa: E402

CASES = ("paced20", "paced200", "burst200", "blocked200", "cpu_gc200", "disk_fail20")
THRESHOLD_DELTA_US = {"p50_us": 50.0, "p95_us": 100.0,
                      "p99_us": 250.0, "p99_9_us": 500.0, "max_us": 2000.0}


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _forbid_network(*_args: object, **_kwargs: object) -> None:
    raise AssertionError("OFFLINE SAFETY VIOLATION: attempted socket connection")


async def run_case(
    case: str, enabled: bool, iterations: int, *, pair: int,
) -> dict[str, Any]:
    """Use independent maker+recorder instances, same prebuilt immutable tape."""
    levels = 200 if "200" in case else 20
    doc, engine, snapshots = make_fixture._build_fixture(
        Path("data/mappings/sig_polymarket_2026.json")
    )
    exchange_id = sorted(snapshots)[0]
    snapshot = snapshots[exchange_id]
    book = replace(book_with_levels(levels), exchange_id=exchange_id)
    # The serialized fixture identity is independent of recorder OFF/ON.
    tape = json.dumps({
        "exchange_id": exchange_id, "levels": levels, "iterations": iterations,
        "bids": [(str(x.price), str(x.quantity)) for x in book.bids],
        "asks": [(str(x.price), str(x.quantity)) for x in book.asks],
    }, sort_keys=True).encode("utf-8")
    tape_sha256 = _sha256(tape)
    assert doc.tournament_id == snapshot.tournament_id

    registry = QuoteRegistry()
    desired = engine.quote(snapshot).desired
    if desired is None or desired.bid_ticks is None or desired.bid_size <= 0:
        raise AssertionError("no valid synthetic maker bid to test fake cancellation")
    # Seed an OPEN quote matching the engine's own desired price.
    registry.apply_authoritative(
        exchange_id=exchange_id, side=QuoteSide.BID,
        price_ticks=desired.bid_ticks, size=desired.bid_size,
        remaining_size=desired.bid_size, logical_operation_id="fake-seeded-bid",
        exchange_order_id=1001, lifecycle_state=LifecycleState.OPEN,
        observed_monotonic_ns=make_fixture.NOW,
    )
    adapter = ShadowMakerExecutionAdapter()
    fake_place_ns: list[int] = []
    fake_cancel_ns: list[int] = []
    fake_cancel_calls = 0
    fake_place_calls = 0

    async def fake_place(plan: Any, state: Any) -> Any:
        nonlocal fake_place_calls
        started = time.perf_counter_ns()
        result = await adapter.place(plan, state)
        fake_place_ns.append(time.perf_counter_ns() - started)
        fake_place_calls += 1
        assert result.simulated
        return result

    async def fake_cancel(active: Any, logical: str, tournament: str) -> Any:
        nonlocal fake_cancel_calls
        started = time.perf_counter_ns()
        result = await adapter.cancel(active, logical, tournament)
        fake_cancel_ns.append(time.perf_counter_ns() - started)
        fake_cancel_calls += 1
        assert result.simulated
        return result

    coordinator = MakerCoordinator(
        engine=engine, lifecycle=QuoteLifecycleManager(),
        quote_registry=registry,
        risk_context=RiskContext(
            mode=ExecutionMode.SHADOW, kill_switch=False, limits=None,
            max_state_age_ns=10_000_000_000,
        ),
        placement_dispatch=fake_place, cancel_dispatch=fake_cancel,
    )
    runtime = MakerRuntimeLoop(
        bridge=make_fixture.cast(make_fixture.MakerSourceBridge,
                                 make_fixture._BenchmarkBridge(snapshot)),
        coordinator=coordinator, polymarket_feed_trusted=lambda: True,
    )

    timestamp = datetime(2026, 10, 14, tzinfo=UTC)
    record_ns: list[int] = []
    maker_ns: list[int] = []
    heartbeat_ns: list[int] = []
    recovery_ns: list[int] = []
    cpu_stop = threading.Event()
    writer_release = threading.Event()
    writer_entered = threading.Event()
    cpu_worker: threading.Thread | None = None
    mirror: MakerDepthMirror | None = None
    process_wall = time.perf_counter_ns()
    process_cpu = time.process_time_ns()
    loop = asyncio.get_running_loop()
    with tempfile.TemporaryDirectory(prefix="lane5-no-network-") as folder:
        try:
            if enabled:
                mirror = MakerDepthMirror(Path(folder) / "maker-book.jsonl")
                if case == "blocked200":
                    original = mirror._book_row

                    def blocked(item: Any) -> dict[str, Any]:
                        writer_entered.set()
                        writer_release.wait(timeout=8)
                        return original(item)

                    mirror._book_row = blocked  # type: ignore[method-assign]
                if case == "disk_fail20":
                    def failed(_item: Any) -> dict[str, Any]:
                        raise OSError("injected offline ENOSPC")
                    mirror._book_row = failed  # type: ignore[method-assign]
                mirror.start()
                if case == "blocked200":
                    mirror.record(
                            book, snapshot.market_id, snapshot.tournament_id, timestamp, 1
                        )
                    if not await asyncio.to_thread(writer_entered.wait, 2):
                        raise AssertionError("writer stall fixture did not enter")
                    for _ in range(128):
                        mirror.record(
                            book, snapshot.market_id, snapshot.tournament_id, timestamp, 1
                        )

            if case == "cpu_gc200":
                def busy() -> None:
                    x = 7
                    while not cpu_stop.is_set():
                        for _ in range(2500):
                            x = (x * 33 + 17) & 65535
                        gc.collect()
                cpu_worker = threading.Thread(target=busy, daemon=True)
                cpu_worker.start()

            heartbeat_stop = asyncio.Event()
            async def heartbeat() -> None:
                target = loop.time() + 0.002
                while not heartbeat_stop.is_set():
                    await asyncio.sleep(max(0, target - loop.time()))
                    now = loop.time()
                    heartbeat_ns.append(int(max(0, now - target) * 1e9))
                    target = max(target + .002, now + .002)

            heartbeat_task = asyncio.create_task(heartbeat())
            try:
                for _ in range(iterations):
                    if mirror is not None:
                        t = time.perf_counter_ns()
                        mirror.record(
                            book, snapshot.market_id, snapshot.tournament_id, timestamp, 1
                        )
                        record_ns.append(time.perf_counter_ns() - t)
                    t = time.perf_counter_ns()
                    runtime.notify_sig({exchange_id}, observed_monotonic_ns=t)
                    result = await runtime.drain_once()
                    if result is None or len(result.decisions) != 1:
                        raise AssertionError("real MakerRuntimeLoop did not decide")
                    maker_ns.append(time.perf_counter_ns() - t)
                    await asyncio.sleep(0)
                    if case.startswith("paced"):
                        await asyncio.sleep(.001)

                # The same actual coordinator must process a kill and a fake
                # cancel, then prohibit fresh action while recovery is blocked.
                t = time.perf_counter_ns()
                runtime.activate_kill_switch("lane5-offline-injected-kill")
                killed = await runtime.drain_once()
                if killed is None or not coordinator.kill_switch.active:
                    raise AssertionError("kill switch did not run actual coordinator")
                recovery_ns.append(time.perf_counter_ns() - t)
                if fake_cancel_calls == 0:
                    raise AssertionError("fake cancel dispatch not exercised")
                before = fake_place_calls
                runtime.set_blocked_exchange_ids({exchange_id})
                await runtime.drain_once()
                runtime.notify_sig({exchange_id})
                assert await runtime.drain_once() is None
                runtime.set_blocked_exchange_ids(())
                await runtime.drain_once()
                if fake_place_calls != before:
                    raise AssertionError("maker placed after latched kill/recovery")
            finally:
                heartbeat_stop.set()
                await heartbeat_task
        finally:
            cpu_stop.set()
            writer_release.set()
            if cpu_worker is not None:
                await asyncio.to_thread(cpu_worker.join, 2)
            if mirror is not None:
                await asyncio.to_thread(mirror.close, timeout=2)
                if mirror.snapshot()["queue_pending"] != 0:
                    raise AssertionError("writer did not drain/mark accepted losses")
                if mirror._thread is not None and mirror._thread.is_alive():
                    raise AssertionError("writer remains after offline teardown")

        counters: dict[str, int | bool] | None = (
            mirror.snapshot() if mirror is not None else None
        )
        if counters is not None:
            if counters["producer_attempted"] != (
                counters["accepted"] + counters["producer_rejected"]
            ):
                raise AssertionError("producer accounting mismatch")
            if counters["dropped"] != (
                counters["producer_rejected"] + counters["writer_lost"]
            ):
                raise AssertionError("loss accounting mismatch")
            if counters["accepted"] != counters["written"] + counters["writer_lost"]:
                raise AssertionError("accepted records not terminally accounted")
        return {
            "pair": pair, "case": case, "recorder": "ON" if enabled else "OFF",
            "levels_per_side": levels, "maker_cycles": iterations,
            "fixture_sha256": tape_sha256,
            "maker_decision": percentiles(maker_ns),
            "producer_callback": percentiles(record_ns),
            "fake_cancel": percentiles(fake_cancel_ns),
            "fake_place": percentiles(fake_place_ns),
            "fake_kill_and_recovery": percentiles(recovery_ns),
            "event_loop_lateness": percentiles(heartbeat_ns),
            "fake_cancel_count": fake_cancel_calls,
            "fake_place_count": fake_place_calls,
            "writer": counters,
            "process_cpu_ms": (time.process_time_ns() - process_cpu) / 1e6,
            "process_wall_ms": (time.perf_counter_ns() - process_wall) / 1e6,
            "max_rss_kb": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
            "network_clients_created": 0, "sig_rest_requests": 0,
            "actual_venue_orders_or_cancels": 0,
            "source_is_synthetic": True,
            "has_real_exchange_ack": False,
        }


async def run(seed: int, pairs: int, iterations: int) -> dict[str, Any]:
    if not 1 <= pairs <= 100 or not 2 <= iterations <= 100_000:
        raise ValueError("invalid bounded trial")
    rng = random.Random(seed)
    plan = [
        {"pair": i, "case": CASES[i % len(CASES)],
         "order": list(("OFF", "ON") if rng.randrange(2) == 0 else ("ON", "OFF"))}
        for i in range(pairs)
    ]
    # This plan/threshold is generated and printed BEFORE measuring a case.
    print(json.dumps({
        "schema": "lane5-preregistered-offline-maker-v1", "seed": seed,
        "pairs": plan, "iterations_per_condition": iterations,
        "max_on_minus_off_microseconds": THRESHOLD_DELTA_US,
        "scientific_label": "synthetic_OFFLINE_not_LIVE_acceptance",
    }, sort_keys=True), flush=True)
    results: list[dict[str, Any]] = []
    with patch.object(socket.socket, "connect", _forbid_network), patch.object(
        socket, "create_connection", _forbid_network
    ):
        for entry in plan:
            for value in entry["order"]:
                result = await run_case(
                    entry["case"], value == "ON", iterations, pair=entry["pair"]
                )
                results.append(result)
                print(json.dumps(result, sort_keys=True), flush=True)

    comparisons = []
    for i in range(pairs):
        off = next(x for x in results if x["pair"] == i and x["recorder"] == "OFF")
        on = next(x for x in results if x["pair"] == i and x["recorder"] == "ON")
        assert off["fixture_sha256"] == on["fixture_sha256"]
        delta = {
            k: round(on["maker_decision"][k] - off["maker_decision"][k], 3)
            for k in THRESHOLD_DELTA_US
        }
        comparisons.append({
            "pair": i, "case": off["case"], "maker_on_minus_off_us": delta,
            "meets_strict_absolute_delta": all(
                delta[k] <= limit for k, limit in THRESHOLD_DELTA_US.items()
            ),
        })
    sufficient = pairs >= 12 and iterations >= 1000
    out = {
        "schema": "lane5-offline-decision-v1", "seed": seed, "pairs": comparisons,
        "sufficient_repeats_for_proposed_screen": sufficient,
        "strict_thresholds_met": all(
            x["meets_strict_absolute_delta"] for x in comparisons
        ),
        "verdict": "SCREEN_ONLY" if sufficient else "INSUFFICIENT_SAMPLE",
        "live_activation_verdict": "BLOCKED",
        "real_venue_capture_verdict": "NOT_RUN",
    }
    print(json.dumps(out, sort_keys=True), flush=True)
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=int, default=207220)
    parser.add_argument("--pairs", type=int, default=12)
    parser.add_argument("--iterations", type=int, default=1000)
    args = parser.parse_args()
    asyncio.run(run(args.seed, args.pairs, args.iterations))


if __name__ == "__main__":
    main()
