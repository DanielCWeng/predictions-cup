"""Independent offline safety regressions for the opt-in maker depth mirror."""
from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from threading import Event
from time import perf_counter, sleep

import pytest

from predictions_cup.config.settings import AppSettings
from predictions_cup.models.market import OrderBook, OrderBookLevel
from predictions_cup.sig.depth_mirror import MakerDepthMirror


def book() -> OrderBook:
    return OrderBook(
        exchange_id="947",
        bids=(OrderBookLevel(price=Decimal("0.45"), quantity=Decimal("10")),),
        asks=(OrderBookLevel(price=Decimal("0.55"), quantity=Decimal("20")),),
        timestamp=datetime(2026, 10, 13, tzinfo=UTC),
        source="sig-rest",
    )


def publish(mirror: MakerDepthMirror) -> None:
    mirror.record(book(), "m-947", "t-cup", datetime(2026, 10, 13, tzinfo=UTC), 17)


def test_default_disabled_and_relative_destination_rejected(tmp_path: Path) -> None:
    assert AppSettings(_env_file=None).maker_depth_mirror_output_file is None
    with pytest.raises(ValueError, match="absolute"):
        MakerDepthMirror(Path("capture.jsonl"))
    with pytest.raises(ValueError, match="bounded"):
        MakerDepthMirror(tmp_path / "good.jsonl", queue_size=0)
    with pytest.raises(ValueError, match="active data"):
        MakerDepthMirror(Path.cwd() / "data" / "unsafe.jsonl")


def test_capture_is_full_depth_and_explicitly_untrusted_for_paper(tmp_path: Path) -> None:
    output = tmp_path / "immutable.jsonl"
    mirror = MakerDepthMirror(output)
    mirror.start()
    publish(mirror)
    mirror.close(timeout=2)
    records = [json.loads(s) for s in output.read_text().splitlines()]
    assert len(records) == 1
    row = records[0]
    assert row["exchange_id"] == "947"
    assert row["tournament_id"] == "t-cup"
    assert row["bids"] == [{"price": "0.45", "quantity": "10"}]
    assert row["asks"] == [{"price": "0.55", "quantity": "20"}]
    assert row["triggering_realtime_revision"] == 17
    assert row["venue_timestamp"] is None and row["venue_revision"] is None
    assert not row["trusted_for_paired_paper"]
    assert not row["source_clock_verified"] and not row["source_sequence_verified"]
    assert output.stat().st_mode & 0o777 == 0o600
    assert mirror.snapshot()["written"] == 1


def test_existing_output_never_overwritten_even_if_activated(tmp_path: Path) -> None:
    output = tmp_path / "existing.jsonl"
    output.write_text("ORIGINAL")
    mirror = MakerDepthMirror(output)
    mirror.start()
    publish(mirror)
    mirror.close(timeout=2)
    assert output.read_text() == "ORIGINAL"
    assert mirror.snapshot()["errors"] == 1
    assert mirror.snapshot()["disabled"] is True


def test_queue_saturation_cannot_block_live_producer(tmp_path: Path) -> None:
    entered, release = Event(), Event()
    mirror = MakerDepthMirror(tmp_path / "queue.jsonl", queue_size=1)
    serialize = mirror._book_row

    def held(item: object) -> dict[str, object]:
        entered.set()
        assert release.wait(timeout=3)
        return serialize(item)  # type: ignore[arg-type]

    mirror._book_row = held  # type: ignore[method-assign]
    mirror.start()
    publish(mirror)
    assert entered.wait(timeout=2)
    publish(mirror)  # one pending slot
    immutable_book = book()  # measure enqueue only, not fixture creation
    timestamp = datetime(2026, 10, 13, tzinfo=UTC)
    started = perf_counter()
    for _ in range(1000):
        mirror.record(immutable_book, "m-947", "t-cup", timestamp, 17)
    elapsed = perf_counter() - started
    assert elapsed < 0.25
    assert mirror.snapshot()["dropped"] > 0
    release.set()
    mirror.close(timeout=2)
    assert mirror.snapshot()["written"] <= 2


def test_bounded_file_stops_when_max_bytes_hit(tmp_path: Path) -> None:
    mirror = MakerDepthMirror(tmp_path / "cap.jsonl", max_bytes=1024)
    mirror.start()
    for _ in range(5):
        publish(mirror)
    mirror.close(timeout=2)
    assert (tmp_path / "cap.jsonl").stat().st_size <= 1024
    assert mirror.snapshot()["disabled"] is True


def test_missing_parent_disables_without_affecting_maker(tmp_path: Path) -> None:
    mirror = MakerDepthMirror(tmp_path / "absent" / "cannot-create.jsonl")
    mirror.start()
    sleep(0.1)
    publish(mirror)
    mirror.close(timeout=1)
    assert mirror.snapshot()["disabled"] is True
    assert mirror.snapshot()["errors"] == 1


def test_closed_mirror_drops_new_capture_and_never_restarts(tmp_path: Path) -> None:
    mirror = MakerDepthMirror(tmp_path / "once.jsonl")
    mirror.start()
    mirror.close(timeout=1)
    publish(mirror)
    assert mirror.snapshot()["dropped"] == 1
    with pytest.raises(ValueError, match="twice"):
        mirror.start()
