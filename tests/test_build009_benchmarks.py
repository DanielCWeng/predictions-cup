from __future__ import annotations

from predictions_cup.benchmarks import benchmark_callable, benchmark_report


def test_benchmark_callable_smoke() -> None:
    stats = benchmark_callable(
        "fixture",
        lambda: 1 + 1,
        calls=50,
        warmup_calls=5,
    )
    assert stats.calls == 50
    assert stats.total_elapsed_ns > 0
    assert stats.throughput_per_second > 0
    assert stats.p99_ns_per_call >= 0


def test_full_benchmark_framework_smoke() -> None:
    report = benchmark_report(
        calls=25,
        warmup_calls=5,
        allocation_profile=False,
        include_journal=True,
        journal_calls=3,
    )
    assert all(result.passed for result in report.correctness)
    names = {result.name for result in report.performance}
    assert "end_to_end_internal_decision_to_null_sink" in names
    assert "sqlite_wal_predispatch" in names
    assert report.metadata["calls"] == 25
