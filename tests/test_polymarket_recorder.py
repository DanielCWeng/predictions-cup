from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest

from predictions_cup.config import AppSettings
from predictions_cup.external.polymarket.recorder import PolymarketRecorder


def test_snapshot_storage_failure_is_surfaced(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = AppSettings.model_validate(
        {"polymarket_storage_path": tmp_path / "capture.sqlite3"}
    )
    recorder = PolymarketRecorder(settings)
    recorder.storage.initialize()
    recorder.books.apply_full_snapshot(
        {
            "market": "0xmarket",
            "asset_id": "token-1",
            "timestamp": "1782753357257",
            "bids": [{"price": "0.45", "size": "10"}],
            "asks": [{"price": "0.46", "size": "10"}],
        },
        datetime(2026, 9, 25, tzinfo=UTC),
    )

    def fail(*args: object, **kwargs: object) -> int:
        del args, kwargs
        raise RuntimeError("disk full")

    monkeypatch.setattr(recorder.storage, "append_snapshots", fail)

    with pytest.raises(RuntimeError, match="disk full"):
        asyncio.run(recorder.record_snapshot_once())
    assert recorder.health.storage_failures == 1
    assert recorder.health.snapshot_last_status is not None
    assert recorder.health.snapshot_last_status.startswith("ERROR:")
