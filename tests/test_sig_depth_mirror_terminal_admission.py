"""Deterministic offline race regression for terminal writer loss accounting."""
from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from threading import Event, Lock, Thread, current_thread

import pytest

from predictions_cup.models.market import OrderBook, OrderBookLevel
from predictions_cup.sig.depth_mirror import MakerDepthMirror


def test_writer_exit_cannot_leave_a_late_accepted_item_stranded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    writer_entered, writer_continue = Event(), Event()
    producer_paused, producer_continue = Event(), Event()
    worker_final_gate = Event()
    stamp = datetime(2026, 10, 14, tzinfo=UTC)
    book = OrderBook(
        exchange_id="947",
        bids=(OrderBookLevel(price=Decimal("0.45"), quantity=Decimal("10")),),
        asks=(OrderBookLevel(price=Decimal("0.55"), quantity=Decimal("10")),),
        timestamp=stamp, source="sig-rest",
    )
    mirror = MakerDepthMirror(tmp_path / "terminal-race.jsonl", queue_size=4)

    class ObservedGate:
        def __init__(self) -> None:
            self.lock = Lock()

        def __enter__(self) -> ObservedGate:
            if current_thread().name == "sig-depth-mirror":
                worker_final_gate.set()
            self.lock.acquire()
            return self

        def __exit__(self, *_args: object) -> None:
            self.lock.release()

    monkeypatch.setattr(mirror, "_admission_lock", ObservedGate())

    def writer_fails(_captured: object) -> dict[str, object]:
        writer_entered.set()
        assert writer_continue.wait(timeout=3.0)
        raise OSError("injected permanent writer failure")

    mirror._book_row = writer_fails  # type: ignore[method-assign]
    mirror.start()
    mirror.record(book, "77", "cup", stamp, 5)
    assert writer_entered.wait(timeout=2.0)

    original_put = mirror._queue.put_nowait

    def paused_admission(item: object) -> None:
        producer_paused.set()
        assert producer_continue.wait(timeout=3.0)
        original_put(item)

    monkeypatch.setattr(mirror._queue, "put_nowait", paused_admission)
    producer = Thread(
        target=lambda: mirror.record(book, "77", "cup", stamp, 6),
        name="isolated-synthetic-producer",
    )
    producer.start()
    try:
        assert producer_paused.wait(timeout=2.0)
        writer_continue.set()
        # The dying worker MUST wait for the in-progress producer admission
        # before it drains the queue. Old implementation did not coordinate.
        assert worker_final_gate.wait(timeout=2.0)
    finally:
        producer_continue.set()
        writer_continue.set()
        producer.join(timeout=2.0)
        if mirror._thread is not None:
            mirror._thread.join(timeout=2.0)

    assert not producer.is_alive()
    assert mirror._thread is not None and not mirror._thread.is_alive()
    result = mirror.snapshot()
    assert result["producer_attempted"] == 2
    assert result["accepted"] == 2
    assert result["producer_rejected"] == 0
    assert result["written"] == 0
    assert result["writer_lost"] == result["dropped"] == 2
    assert result["queue_pending"] == 0
    assert result["errors"] == 1
