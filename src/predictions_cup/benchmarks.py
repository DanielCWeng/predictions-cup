"""First-class BUILD-009 correctness and latency benchmark harness."""

from __future__ import annotations

import argparse
import json
import os
import platform
import statistics
import subprocess
import sys
import tempfile
import time
import tracemalloc
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from predictions_cup.execution.journal import ExecutionJournal
from predictions_cup.execution.models import (
    ExecutionEnvelope,
    ExecutionMode,
    OperationKind,
    RuntimeOrderIntent,
)
from predictions_cup.execution.sinks import NullSink
from predictions_cup.risk.core import RiskContext
from predictions_cup.runtime import (
    OrderAction,
    OutcomeSide,
    RuntimeMarket,
    RuntimePortfolio,
    RuntimeSnapshot,
    limit_price_to_ticks,
    ticks_to_limit_price,
)
from predictions_cup.runtime.orchestrator import DecisionCore
from predictions_cup.strategy.core import StrategyRegistry, synthetic_threshold_strategy
from predictions_cup.strategy.kernels import (
    KernelRegistry,
    binary_cara_reservation_exact,
    binary_cara_reservation_first_order,
    default_kernel_registry,
    logit,
    logit_log1p,
)

ZeroArgFn = Callable[[], object]


@dataclass(frozen=True, slots=True)
class BenchmarkStats:
    name: str
    calls: int
    warmup_calls: int
    total_elapsed_ns: int
    mean_ns_per_call: float
    median_ns_per_call: float
    p95_ns_per_call: float
    p99_ns_per_call: float
    throughput_per_second: float
    timer_harness_ns_per_call: float
    peak_traced_bytes: int | None = None


@dataclass(frozen=True, slots=True)
class CorrectnessResult:
    name: str
    max_absolute_error: float
    max_relative_error: float
    tolerance_absolute: float
    tolerance_relative: float
    passed: bool


@dataclass(frozen=True, slots=True)
class BenchmarkReport:
    metadata: dict[str, object]
    correctness: tuple[CorrectnessResult, ...]
    performance: tuple[BenchmarkStats, ...]
    notes: tuple[str, ...]


def _percentile(values: Sequence[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int((len(ordered) - 1) * percentile)))
    return ordered[index]


def _empty_harness_ns(batch_size: int, repeats: int = 25) -> float:
    samples: list[float] = []
    for _ in range(repeats):
        started = time.perf_counter_ns()
        for _ in range(batch_size):
            pass
        samples.append((time.perf_counter_ns() - started) / batch_size)
    return statistics.median(samples)


def benchmark_callable(
    name: str,
    function: ZeroArgFn,
    *,
    calls: int,
    warmup_calls: int,
    allocation_profile: bool = False,
) -> BenchmarkStats:
    if calls <= 0 or warmup_calls < 0:
        raise ValueError("benchmark call counts must be valid")

    for _ in range(warmup_calls):
        function()

    started = time.perf_counter_ns()
    for _ in range(calls):
        function()
    total_elapsed_ns = time.perf_counter_ns() - started

    batch_size = min(100, calls)
    sample_batches = min(1000, max(1, calls // batch_size))
    harness_ns = _empty_harness_ns(batch_size)
    samples: list[float] = []
    for _ in range(sample_batches):
        batch_started = time.perf_counter_ns()
        for _ in range(batch_size):
            function()
        batch_elapsed = time.perf_counter_ns() - batch_started
        adjusted = max(0.0, (batch_elapsed / batch_size) - harness_ns)
        samples.append(adjusted)

    peak_traced_bytes: int | None = None
    if allocation_profile:
        tracemalloc.start()
        for _ in range(min(calls, 10_000)):
            function()
        _, peak_traced_bytes = tracemalloc.get_traced_memory()
        tracemalloc.stop()

    mean = total_elapsed_ns / calls
    return BenchmarkStats(
        name=name,
        calls=calls,
        warmup_calls=warmup_calls,
        total_elapsed_ns=total_elapsed_ns,
        mean_ns_per_call=mean,
        median_ns_per_call=statistics.median(samples),
        p95_ns_per_call=_percentile(samples, 0.95),
        p99_ns_per_call=_percentile(samples, 0.99),
        throughput_per_second=(1_000_000_000.0 / mean) if mean else float("inf"),
        timer_harness_ns_per_call=harness_ns,
        peak_traced_bytes=peak_traced_bytes,
    )


def compare_float_functions(
    name: str,
    reference: Callable[..., float],
    challenger: Callable[..., float],
    corpus: Sequence[tuple[float, ...]],
    *,
    tolerance_absolute: float,
    tolerance_relative: float,
) -> CorrectnessResult:
    max_absolute = 0.0
    max_relative = 0.0
    passed = True
    for arguments in corpus:
        expected = reference(*arguments)
        actual = challenger(*arguments)
        absolute = abs(actual - expected)
        relative = absolute / max(abs(expected), 1e-300)
        max_absolute = max(max_absolute, absolute)
        max_relative = max(max_relative, relative)
        if absolute > tolerance_absolute and relative > tolerance_relative:
            passed = False
    return CorrectnessResult(
        name=name,
        max_absolute_error=max_absolute,
        max_relative_error=max_relative,
        tolerance_absolute=tolerance_absolute,
        tolerance_relative=tolerance_relative,
        passed=passed,
    )


def _tick_correctness() -> CorrectnessResult:
    for ticks in range(1, 200):
        price = ticks_to_limit_price(ticks)
        if limit_price_to_ticks(price) != ticks:
            return CorrectnessResult(
                name="sig_tick_roundtrip",
                max_absolute_error=1.0,
                max_relative_error=1.0,
                tolerance_absolute=0.0,
                tolerance_relative=0.0,
                passed=False,
            )
    return CorrectnessResult(
        name="sig_tick_roundtrip",
        max_absolute_error=0.0,
        max_relative_error=0.0,
        tolerance_absolute=0.0,
        tolerance_relative=0.0,
        passed=True,
    )


def _decision_benchmark_function(registry: KernelRegistry) -> ZeroArgFn:
    strategies = StrategyRegistry()
    strategies.register("synthetic-threshold", synthetic_threshold_strategy)
    snapshot = RuntimeSnapshot(
        markets=(
            RuntimeMarket(
                market_id="m1",
                status="open",
                exchange_ids=("36",),
                tournament_id="t1",
                mapping_accepted=True,
                tradeable=True,
            ),
        ),
        books=(),
        portfolio=RuntimePortfolio(account_trusted=False),
        observation_monotonic_ns=1_000_000,
    )
    core = DecisionCore(
        strategies=strategies,
        kernels=registry,
        risk_context=RiskContext(
            mode=ExecutionMode.SHADOW,
            kill_switch=False,
            limits=None,
            max_state_age_ns=1_000_000_000,
        ),
    )
    sink = NullSink()

    def run() -> object:
        prepared = core.prepare(
            strategy_id="synthetic-threshold",
            snapshot=snapshot,
            strategy_config={"threshold": 0.01, "edge": 0.02},
            mode=ExecutionMode.SHADOW,
            logical_operation_id="bench-op",
            idempotency_key="bench-key",
        )
        if prepared.plan is None:
            raise AssertionError("synthetic benchmark unexpectedly returned NO_TRADE")
        return sink.dispatch(prepared.plan, snapshot)

    return run


def _journal_benchmark(count: int) -> BenchmarkStats:
    if count <= 0:
        raise ValueError("journal benchmark count must be positive")
    with tempfile.TemporaryDirectory() as directory:
        journal = ExecutionJournal(Path(directory) / "execution.sqlite3")
        try:
            started = time.perf_counter_ns()
            samples: list[float] = []
            for index in range(count):
                intent = RuntimeOrderIntent(
                    intent_id=f"intent-{index}",
                    exchange_id="36",
                    market_id="m1",
                    tournament_id="t1",
                    outcome_side=OutcomeSide.YES,
                    action=OrderAction.BUY,
                    quantity=1,
                    limit_price_ticks=100,
                    strategy_id="journal-benchmark",
                    decision_observation_ns=index + 1,
                )
                envelope = ExecutionEnvelope.placement(
                    logical_operation_id=f"journal-{index}",
                    operation_kind=OperationKind.SINGLE_PLACEMENT,
                    sink_mode=ExecutionMode.LIVE,
                    idempotency_key=f"journal-key-{index}",
                    intents=(intent,),
                    created_monotonic_ns=index + 1,
                )
                call_started = time.perf_counter_ns()
                journal.record_before_dispatch(envelope)
                samples.append(float(time.perf_counter_ns() - call_started))
            elapsed = time.perf_counter_ns() - started
        finally:
            journal.close()

    mean = elapsed / count
    return BenchmarkStats(
        name="sqlite_wal_predispatch",
        calls=count,
        warmup_calls=0,
        total_elapsed_ns=elapsed,
        mean_ns_per_call=mean,
        median_ns_per_call=statistics.median(samples),
        p95_ns_per_call=_percentile(samples, 0.95),
        p99_ns_per_call=_percentile(samples, 0.99),
        throughput_per_second=1_000_000_000.0 / mean,
        timer_harness_ns_per_call=0.0,
    )


def _git_sha() -> str:
    env_sha = os.environ.get("GITHUB_SHA")
    if env_sha:
        return env_sha
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def _cpu_model() -> str:
    candidate = platform.processor().strip()
    if candidate:
        return candidate
    cpuinfo = Path("/proc/cpuinfo")
    if cpuinfo.exists():
        for line in cpuinfo.read_text(encoding="utf-8", errors="ignore").splitlines():
            if line.lower().startswith("model name"):
                return line.split(":", 1)[-1].strip()
    return "unknown"


def benchmark_report(
    *,
    calls: int,
    warmup_calls: int,
    allocation_profile: bool,
    include_journal: bool,
    journal_calls: int,
) -> BenchmarkReport:
    registry = default_kernel_registry()

    correctness = (
        _tick_correctness(),
        compare_float_functions(
            "M-038 logit reference vs log1p",
            logit,
            logit_log1p,
            ((1e-9,), (0.001,), (0.01,), (0.5,), (0.99,), (0.999,), (1.0 - 1e-9,)),
            tolerance_absolute=1e-12,
            tolerance_relative=1e-12,
        ),
        compare_float_functions(
            "M-041 exact vs M-042 first-order",
            binary_cara_reservation_exact,
            binary_cara_reservation_first_order,
            (
                (0.01, 0.01, 0.0),
                (0.1, 0.05, 1.0),
                (0.5, 0.1, 1.0),
                (0.9, 0.05, -1.0),
                (0.99, 0.01, 0.0),
            ),
            tolerance_absolute=0.01,
            tolerance_relative=0.05,
        ),
    )

    decimal_price = Decimal("0.500")
    tick_value = 100
    performance: list[BenchmarkStats] = [
        benchmark_callable(
            "decimal_tick_roundtrip",
            lambda: limit_price_to_ticks(decimal_price),
            calls=calls,
            warmup_calls=warmup_calls,
            allocation_profile=allocation_profile,
        ),
        benchmark_callable(
            "integer_tick_increment",
            lambda: tick_value + 1,
            calls=calls,
            warmup_calls=warmup_calls,
            allocation_profile=allocation_profile,
        ),
        benchmark_callable(
            "M-038 logit_reference",
            lambda: logit(0.61),
            calls=calls,
            warmup_calls=warmup_calls,
            allocation_profile=allocation_profile,
        ),
        benchmark_callable(
            "M-038 logit_log1p",
            lambda: logit_log1p(0.61),
            calls=calls,
            warmup_calls=warmup_calls,
            allocation_profile=allocation_profile,
        ),
        benchmark_callable(
            "M-041 cara_exact",
            lambda: binary_cara_reservation_exact(0.61, 0.1, 1.0),
            calls=calls,
            warmup_calls=warmup_calls,
            allocation_profile=allocation_profile,
        ),
        benchmark_callable(
            "M-042 cara_first_order",
            lambda: binary_cara_reservation_first_order(0.61, 0.1, 1.0),
            calls=calls,
            warmup_calls=warmup_calls,
            allocation_profile=allocation_profile,
        ),
        benchmark_callable(
            "end_to_end_internal_decision_to_null_sink",
            _decision_benchmark_function(registry),
            calls=calls,
            warmup_calls=warmup_calls,
            allocation_profile=allocation_profile,
        ),
    ]
    if include_journal:
        performance.append(_journal_benchmark(journal_calls))

    metadata: dict[str, object] = {
        "git_sha": _git_sha(),
        "python_version": sys.version,
        "os": platform.platform(),
        "architecture": platform.machine(),
        "cpu_model": _cpu_model(),
        "host_class": os.environ.get("BUILD009_BENCHMARK_HOST_CLASS", "unknown"),
        "process_affinity": (
            sorted(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else None
        ),
        "gc_mode": "default",
        "calls": calls,
        "warmup_calls": warmup_calls,
        "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    notes = (
        "Aggregate throughput uses one timer around N calls.",
        (
            "Percentiles use timed batches of up to 100 calls with empty-loop "
            "harness overhead measured separately."
        ),
        (
            "Allocation profiling, when enabled, is a separate tracemalloc pass "
            "and is not mixed into speed timing."
        ),
        (
            "SIG's documented 250 ms Realtime coalescing is upstream feed behaviour, "
            "not internal processing latency."
        ),
    )
    return BenchmarkReport(
        metadata=metadata,
        correctness=correctness,
        performance=tuple(performance),
        notes=notes,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run BUILD-009 latency/correctness benchmarks.")
    parser.add_argument("--calls", type=int, default=100_000)
    parser.add_argument("--warmup", type=int, default=3_000)
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--allocations", action="store_true")
    parser.add_argument("--include-journal", action="store_true")
    parser.add_argument("--journal-calls", type=int, default=1_000)
    parser.add_argument("--smoke", action="store_true")
    parser.add_argument("--output", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    calls = 250 if args.smoke else args.calls
    warmup = 25 if args.smoke else args.warmup
    repeats = 1 if args.smoke else args.repeats
    reports: list[dict[str, Any]] = []
    for _ in range(repeats):
        report = benchmark_report(
            calls=calls,
            warmup_calls=warmup,
            allocation_profile=args.allocations,
            include_journal=args.include_journal,
            journal_calls=min(args.journal_calls, 25) if args.smoke else args.journal_calls,
        )
        reports.append(asdict(report))
        if not all(result.passed for result in report.correctness):
            return 2

    rendered = json.dumps(reports, indent=2, sort_keys=True)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    else:
        print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
