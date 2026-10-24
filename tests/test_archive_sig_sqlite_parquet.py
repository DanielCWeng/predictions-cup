"""Offline SQLite-to-Parquet archive regression and no-deletion tests."""

from __future__ import annotations

import base64
from pathlib import Path
import sqlite3

import pyarrow.parquet as pq
import pytest

from scripts.archive_sig_sqlite_parquet import archive, verify


def _fixture_db(path: Path) -> None:
    with sqlite3.connect(path) as db:
        db.execute(
            "CREATE TABLE realtime_deliveries "
            "(topic TEXT, revision INTEGER, details BLOB, price REAL)"
        )
        db.executemany(
            "INSERT INTO realtime_deliveries VALUES (?, ?, ?, ?)",
            [
                ("cup", 1, b"\x00\x01", 0.625),
                ("cup", 2, None, None),
                ("cup", 3, b"", 0.5),
                ("cup", 4, b"\xff", 0.9),
                ("cup", 5, None, 0.13),
            ],
        )
        db.execute("CREATE TABLE empty_events (event TEXT)")
        db.commit()


def test_offline_archive_streams_and_preserves_original(tmp_path: Path) -> None:
    source = tmp_path / "original.sqlite3"
    destination = tmp_path / "archive"
    _fixture_db(source)

    result = archive(
        source,
        destination,
        batch_rows=2,
        min_free_bytes=0,
        max_seconds=20,
        guard=lambda: None,
    )
    state = result["tables"]["realtime_deliveries"]
    assert state["target_rowid"] == 5
    assert state["last_rowid"] == 5
    assert [part["rows"] for part in state["parts"]] == [2, 2, 1]
    assert result["tables"]["empty_events"]["parts"] == []
    assert verify(destination) == {"empty_events": 0, "realtime_deliveries": 5}

    rows = []
    for part in state["parts"]:
        rows.extend(pq.read_table(destination / "realtime_deliveries" / part["file"]).to_pylist())
    assert [item["_sqlite_rowid"] for item in rows] == [1, 2, 3, 4, 5]
    assert [item["revision"] for item in rows] == ["1", "2", "3", "4", "5"]
    assert rows[0]["details"] == "base64:" + base64.b64encode(b"\x00\x01").decode()
    assert rows[1]["details"] is None
    assert rows[2]["details"] == "base64:"
    assert rows[0]["price"] == "0.625"
    assert rows[1]["price"] is None

    # A completed archive is idempotent and does not touch the SQLite source.
    again = archive(
        source,
        destination,
        batch_rows=2,
        min_free_bytes=0,
        max_seconds=20,
        guard=lambda: None,
    )
    assert again["tables"]["realtime_deliveries"]["parts"] == state["parts"]
    with sqlite3.connect(source) as db:
        assert db.execute("SELECT COUNT(*) FROM realtime_deliveries").fetchone()[0] == 5


def test_archive_fails_closed_on_disk_guard(tmp_path: Path) -> None:
    source = tmp_path / "original.sqlite3"
    _fixture_db(source)
    with pytest.raises(RuntimeError, match="Insufficient free space"):
        archive(
            source,
            tmp_path / "output",
            min_free_bytes=10**18,
            guard=lambda: None,
        )
    with sqlite3.connect(source) as db:
        assert db.execute("SELECT COUNT(*) FROM realtime_deliveries").fetchone()[0] == 5


def test_verify_detects_corrupt_archive(tmp_path: Path) -> None:
    source = tmp_path / "original.sqlite3"
    destination = tmp_path / "output"
    _fixture_db(source)
    result = archive(
        source,
        destination,
        min_free_bytes=0,
        guard=lambda: None,
    )
    filename = result["tables"]["realtime_deliveries"]["parts"][0]["file"]
    output = destination / "realtime_deliveries" / filename
    with output.open("ab") as stream:
        stream.write(b"corruption")
    with pytest.raises(RuntimeError, match="SHA256 mismatch"):
        verify(destination)
