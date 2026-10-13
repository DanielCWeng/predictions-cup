"""Lane-5 isolated fail-closed safety regressions; no network or LIVE runtime."""
from __future__ import annotations

import ast
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from threading import Thread

from predictions_cup.models.market import OrderBook, OrderBookLevel
from predictions_cup.sig.depth_mirror import MakerDepthMirror


def _book() -> OrderBook:
    return OrderBook(
        exchange_id="lane5-1",
        bids=(OrderBookLevel(price=Decimal("0.4"), quantity=Decimal("10")),),
        asks=(OrderBookLevel(price=Decimal("0.6"), quantity=Decimal("10")),),
        timestamp=datetime(2026, 10, 14, tzinfo=UTC),
        source="sig-rest",
    )


def _record(mirror: MakerDepthMirror) -> None:
    mirror.record(_book(), "market-1", "cup", datetime(2026, 10, 14, tzinfo=UTC), 23)


def test_inflight_serialization_failure_counts_lost_snapshot(tmp_path: Path) -> None:
    mirror = MakerDepthMirror(tmp_path / "write-failed.jsonl")

    def fail(_item: object) -> dict[str, object]:
        raise OSError("injected ENOSPC while serialising/writing")

    mirror._book_row = fail  # type: ignore[method-assign]
    mirror.start()
    _record(mirror)
    assert mirror._thread is not None
    mirror._thread.join(timeout=2.0)
    assert not mirror._thread.is_alive()
    stats = mirror.snapshot()
    assert stats["accepted"] == 1
    assert stats["written"] == 0
    assert stats["dropped"] == 1, "in-flight writer failure must count as lost evidence"
    assert stats["errors"] == 1
    assert stats["queue_pending"] == 0
    assert stats["disabled"] is True


def test_research_thread_start_failure_must_not_abort_maker(
    tmp_path: Path, monkeypatch,
) -> None:
    mirror = MakerDepthMirror(tmp_path / "thread-failure.jsonl")

    def fail_start(_self: Thread) -> None:
        raise RuntimeError("injected thread allocation failure")

    monkeypatch.setattr(Thread, "start", fail_start)
    mirror.start()  # Optional research failure is not a LIVE maker startup failure.
    stats = mirror.snapshot()
    assert stats["errors"] == 1
    assert stats["disabled"] is True
    _record(mirror)
    assert mirror.snapshot()["dropped"] == 1


def test_recorder_close_survives_unrelated_maker_cleanup_exception() -> None:
    """Structural guard: a preceding cleanup await must not bypass mirror.close()."""
    path = Path(__file__).resolve().parents[1] / "src/predictions_cup/maker/service.py"
    module = ast.parse(path.read_text(encoding="utf-8"))
    service = next(n for n in module.body if isinstance(n, ast.ClassDef) and n.name == "MakerService")
    run = next(n for n in service.body if isinstance(n, ast.AsyncFunctionDef) and n.name == "run")
    outer = next(n for n in run.body if isinstance(n, ast.Try))

    def mirror_close(nodes: list[ast.stmt]) -> bool:
        return any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "depth_mirror"
            and node.func.attr == "close"
            for stmt in nodes for node in ast.walk(stmt)
        )

    # Either a first, synchronous unconditional close or a nested finally
    # encloses it, guaranteeing it executes when any prior cleanup await fails.
    nested_finally = any(
        isinstance(stmt, ast.Try) and mirror_close(stmt.finalbody)
        for stmt in outer.finalbody
    )
    starts_with_close = bool(outer.finalbody) and mirror_close(outer.finalbody[:1])
    assert nested_finally or starts_with_close
