"""Invented-data-only WAL backup acceptance tests; never opens operational journals.

All file paths are enforced as exact fixture basenames in pytest's tmp_path.
This is NOT a backup utility and grants no permission for source acquisition.
"""

from __future__ import annotations

import hashlib
import shutil
import sqlite3
import threading
import time
from pathlib import Path

import pytest


SOURCE_NAME = "invented_source.sqlite3"
SNAPSHOT_NAME = "invented_snapshot.sqlite3"


class SnapshotRejected(RuntimeError):
    """Fail-closed simulated preflight or frozen-copy validation rejection."""


def _invented_source(path: Path) -> sqlite3.Connection:
    assert path.name == SOURCE_NAME
    conn = sqlite3.connect(path, isolation_level=None, timeout=0.2)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=FULL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(
        """
        CREATE TABLE operations (
            operation_id TEXT PRIMARY KEY,
            sequence_no INTEGER NOT NULL UNIQUE,
            intent_id TEXT NOT NULL UNIQUE
        );
        CREATE TABLE events (
            event_id INTEGER PRIMARY KEY,
            operation_id TEXT NOT NULL REFERENCES operations(operation_id),
            kind TEXT NOT NULL CHECK (kind IN ('SUBMISSION', 'PARTIAL', 'UNCERTAIN')),
            UNIQUE(operation_id, kind)
        );
        CREATE TABLE fill_dedupe (
            alias TEXT PRIMARY KEY,
            event_id INTEGER NOT NULL REFERENCES events(event_id)
        );
        CREATE TABLE snapshot_clock (
            singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
            last_committed_sequence INTEGER NOT NULL
        );
        INSERT INTO snapshot_clock VALUES (1, 0);
        """
    )
    return conn


def _invented_commit(conn: sqlite3.Connection, number: int) -> None:
    op = f"invented-operation-{number:05d}"
    kind = "PARTIAL" if number % 2 else "UNCERTAIN"
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute(
            "INSERT INTO operations VALUES (?, ?, ?)",
            (op, number, f"invented-intent-{number:05d}"),
        )
        conn.execute("INSERT INTO events(operation_id, kind) VALUES (?, 'SUBMISSION')", (op,))
        cursor = conn.execute(
            "INSERT INTO events(operation_id, kind) VALUES (?, ?)", (op, kind)
        )
        event_id = cursor.lastrowid
        conn.executemany(
            "INSERT INTO fill_dedupe VALUES (?, ?)",
            [(f"invented-alias-{number:05d}-{suffix}", event_id) for suffix in ("a", "b")],
        )
        conn.execute("UPDATE snapshot_clock SET last_committed_sequence=?", (number,))
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise


def _snapshot(
    source: Path,
    destination: Path,
    *,
    available_bytes: int = 1_000_000,
    minimum_bytes: int = 100_000,
    lock_guard_ok: bool = True,
    deadline_seconds: float = 3.0,
) -> None:
    """Test-local call to sqlite3 backup; hard guard rejects every non-fixture path."""
    if (
        source.name != SOURCE_NAME
        or destination.name != SNAPSHOT_NAME
        or source.parent != destination.parent
        or source == destination
    ):
        raise SnapshotRejected("not an invented test fixture")
    if destination.exists():
        raise SnapshotRejected("destination already exists")
    if not lock_guard_ok or available_bytes < minimum_bytes or deadline_seconds <= 0:
        raise SnapshotRejected("failed simulated lock, space, or deadline guard")

    start = time.monotonic()

    def progress(_status: int, _remaining: int, _total: int) -> None:
        if time.monotonic() - start >= deadline_seconds:
            raise SnapshotRejected("backup deadline exceeded")

    try:
        with sqlite3.connect(source, timeout=0.2) as reader:
            reader.execute("PRAGMA query_only=ON")
            with sqlite3.connect(destination, timeout=0.2) as copy:
                reader.backup(copy, pages=4, progress=progress, sleep=0.001)
    except BaseException:
        destination.unlink(missing_ok=True)
        raise
    if time.monotonic() - start >= deadline_seconds:
        destination.unlink(missing_ok=True)
        raise SnapshotRejected("backup deadline exceeded")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _validate_frozen_snapshot(path: Path, expected_sha: str) -> dict[str, int | str]:
    """Only a closed, standalone frozen copy may be validated."""
    if path.name == SOURCE_NAME or not path.is_file() or _sha(path) != expected_sha:
        raise SnapshotRejected("missing frozen snapshot or SHA256 mismatch")
    try:
        with sqlite3.connect(f"{path.as_uri()}?mode=ro&immutable=1", uri=True) as conn:
            if conn.execute("PRAGMA integrity_check").fetchone() != ("ok",):
                raise SnapshotRejected("SQLite integrity check failed")
            if conn.execute("PRAGMA foreign_key_check").fetchall():
                raise SnapshotRejected("orphan event or alias")
            clock = int(
                conn.execute("SELECT last_committed_sequence FROM snapshot_clock").fetchone()[0]
            )
            operations = int(conn.execute("SELECT COUNT(*) FROM operations").fetchone()[0])
            events = int(conn.execute("SELECT COUNT(*) FROM events").fetchone()[0])
            aliases = int(conn.execute("SELECT COUNT(*) FROM fill_dedupe").fetchone()[0])
            max_sequence = int(
                conn.execute("SELECT COALESCE(MAX(sequence_no), 0) FROM operations").fetchone()[0]
            )
            if (operations, events, aliases, max_sequence) != (
                clock,
                2 * clock,
                2 * clock,
                clock,
            ):
                raise SnapshotRejected("non-atomic generation, missing rows, or alias cardinality")
            invalid_lifecycle = conn.execute(
                """SELECT COUNT(*) FROM operations o
                WHERE (SELECT COUNT(*) FROM events e WHERE e.operation_id=o.operation_id) != 2
                   OR NOT EXISTS (
                     SELECT 1 FROM events e WHERE e.operation_id=o.operation_id
                     AND e.kind='SUBMISSION')"""
            ).fetchone()[0]
            if invalid_lifecycle:
                raise SnapshotRejected("invalid event lifecycle")
    except sqlite3.DatabaseError as exc:
        raise SnapshotRejected("invalid SQLite snapshot") from exc
    return {
        "snapshot_clock": clock,
        "operations": operations,
        "events": events,
        "aliases": aliases,
        "sha256": expected_sha,
    }


def test_concurrent_writer_delayed_checkpoint_consistent_snapshot(tmp_path: Path) -> None:
    source = tmp_path / SOURCE_NAME
    frozen = tmp_path / SNAPSHOT_NAME
    connection = _invented_source(source)
    for number in range(1, 21):
        _invented_commit(connection, number)

    # Pin a historical WAL read transaction so PASSIVE checkpoint cannot erase
    # frames needed by that reader. This is an invented database only.
    pinned = sqlite3.connect(source, isolation_level=None, timeout=0.2)
    pinned.execute("BEGIN")
    assert pinned.execute("SELECT COUNT(*) FROM operations").fetchone() == (20,)

    ten_more = threading.Event()
    failures: list[Exception] = []

    def writer() -> None:
        try:
            with sqlite3.connect(source, isolation_level=None, timeout=0.2) as writable:
                writable.execute("PRAGMA foreign_keys=ON")
                for number in range(21, 141):
                    _invented_commit(writable, number)
                    if number == 30:
                        ten_more.set()
                    time.sleep(0.002)
        except Exception as exc:
            failures.append(exc)
            ten_more.set()

    thread = threading.Thread(target=writer, name="synthetic-wal-writer")
    thread.start()
    try:
        assert ten_more.wait(timeout=3)
        assert not failures
        # Another connection's PASSIVE checkpoint can leave frames uncheckpointed
        # while the pinned reader still owns its old generation.
        checkpoint = connection.execute("PRAGMA wal_checkpoint(PASSIVE)").fetchone()
        assert checkpoint is not None
        assert checkpoint[1] > checkpoint[2]
        _snapshot(source, frozen)
        receipt = _validate_frozen_snapshot(frozen, _sha(frozen))
        assert 30 <= receipt["snapshot_clock"] <= 140
        assert receipt["operations"] == receipt["snapshot_clock"]
        assert receipt["events"] == 2 * receipt["snapshot_clock"]
    finally:
        pinned.execute("ROLLBACK")
        pinned.close()
        thread.join(timeout=4)
        connection.close()
    assert not thread.is_alive()
    assert failures == []
    # Frozen snapshot must remain bytewise identical and internally coherent
    # after 110 more synthetic writer transactions and connection shutdown.
    assert _validate_frozen_snapshot(frozen, str(receipt["sha256"])) == receipt
    with sqlite3.connect(source) as live_synthetic:
        assert live_synthetic.execute("SELECT COUNT(*) FROM operations").fetchone() == (140,)


def test_writer_restart_keeps_complete_atomic_generation(tmp_path: Path) -> None:
    source = tmp_path / SOURCE_NAME
    frozen = tmp_path / SNAPSHOT_NAME
    first = _invented_source(source)
    for number in range(1, 6):
        _invented_commit(first, number)
    first.close()
    with sqlite3.connect(source, isolation_level=None) as restarted:
        restarted.execute("PRAGMA foreign_keys=ON")
        for number in range(6, 10):
            _invented_commit(restarted, number)
    _snapshot(source, frozen)
    assert _validate_frozen_snapshot(frozen, _sha(frozen))["snapshot_clock"] == 9


def test_frozen_copy_sha_corruption_and_logical_incompleteness_rejected(tmp_path: Path) -> None:
    source = tmp_path / SOURCE_NAME
    frozen = tmp_path / SNAPSHOT_NAME
    with _invented_source(source) as writer:
        for number in range(1, 7):
            _invented_commit(writer, number)
    _snapshot(source, frozen)
    digest = _sha(frozen)
    assert _validate_frozen_snapshot(frozen, digest)["operations"] == 6

    copied = tmp_path / "invented_frozen_copy.sqlite3"
    shutil.copyfile(frozen, copied)  # Only copy a CLOSED synthetic backup, never live SQLite.
    assert _validate_frozen_snapshot(copied, digest)["sha256"] == digest
    with copied.open("r+b") as handle:
        handle.seek(105)
        one = handle.read(1)
        handle.seek(105)
        handle.write(bytes([one[0] ^ 255]))
    with pytest.raises(SnapshotRejected, match="SHA256"):
        _validate_frozen_snapshot(copied, digest)

    recalculated_corrupt = tmp_path / "invented_corrupt_rehashed.sqlite3"
    shutil.copyfile(frozen, recalculated_corrupt)
    with recalculated_corrupt.open("r+b") as handle:
        handle.write(b"X")  # Invalid SQLite header; attacker can recompute SHA.
    with pytest.raises(SnapshotRejected, match="invalid SQLite"):
        _validate_frozen_snapshot(recalculated_corrupt, _sha(recalculated_corrupt))

    incomplete = tmp_path / "invented_incomplete_copy.sqlite3"
    shutil.copyfile(frozen, incomplete)
    with sqlite3.connect(incomplete) as changed:
        changed.execute("PRAGMA journal_mode=DELETE")
        changed.execute("DELETE FROM fill_dedupe WHERE alias='invented-alias-00001-a'")
        assert changed.execute("PRAGMA integrity_check").fetchone() == ("ok",)
    # Recompute SHA to model a newly rehashed but logically incomplete copy.
    with pytest.raises(SnapshotRejected, match="missing rows"):
        _validate_frozen_snapshot(incomplete, _sha(incomplete))


def test_simulated_lock_disk_deadline_and_target_guard_fail_closed(tmp_path: Path) -> None:
    source = tmp_path / SOURCE_NAME
    frozen = tmp_path / SNAPSHOT_NAME
    with _invented_source(source) as writer:
        _invented_commit(writer, 1)
    for guard in (
        {"lock_guard_ok": False},
        {"available_bytes": 100, "minimum_bytes": 1000},
        {"deadline_seconds": 0.0},
    ):
        with pytest.raises(SnapshotRejected, match="guard"):
            _snapshot(source, frozen, **guard)  # type: ignore[arg-type]
        assert not frozen.exists()
    with pytest.raises(SnapshotRejected, match="not an invented"):
        _snapshot(tmp_path / "execution_journal.sqlite3", frozen)
    assert not frozen.exists()
    _snapshot(source, frozen)
    with pytest.raises(SnapshotRejected, match="already exists"):
        _snapshot(source, frozen)
