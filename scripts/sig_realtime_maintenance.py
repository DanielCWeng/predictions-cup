#!/usr/bin/env python3
"""Preview or apply bounded age retention to the SIG realtime SQLite database.

Dry-run is the default. Apply deletes rows older than the selected retention window
and truncates the WAL. It intentionally does not VACUUM the database.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

TABLES: tuple[tuple[str, str], ...] = (
    ("realtime_deliveries", "observed_at"),
    ("realtime_trades", "observed_at"),
    ("book_dirty_events", "observed_at"),
    ("market_settled_events", "observed_at"),
    ("market_observations", "rest_observed_at"),
    ("price_observations", "rest_observed_at"),
    ("book_observations", "rest_observed_at"),
    ("trust_transitions", "observed_at"),
    ("capture_health", "observed_at"),
)
JOURNAL_SIZE_LIMIT_BYTES = 64 * 1024 * 1024
DELETE_BATCH_SIZE = 100_000


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database",
        type=Path,
        default=Path("data/sig_realtime.sqlite3"),
        help="SQLite database path (default: data/sig_realtime.sqlite3)",
    )
    parser.add_argument(
        "--retention-days",
        type=int,
        default=3,
        help="Retention for non-book tables (default: 3)",
    )
    parser.add_argument(
        "--book-days",
        type=int,
        default=1,
        help="Retention for raw book deltas and snapshots (default: 1)",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Delete expired rows and checkpoint; omitted by default for dry-run",
    )
    return parser.parse_args()


def sizes(path: Path) -> None:
    for suffix in ("", "-wal", "-shm"):
        item = Path(f"{path}{suffix}")
        size = item.stat().st_size if item.exists() else 0
        print(f"size {item}: {size} bytes")


def first_id_at_or_after(
    connection: sqlite3.Connection, table: str, column: str, cutoff: str
) -> int | None:
    """Binary-search the INTEGER PRIMARY KEY, assuming capture timestamps are ordered."""
    first = connection.execute(f'SELECT min(id) FROM "{table}"').fetchone()[0]
    last = connection.execute(f'SELECT max(id) FROM "{table}"').fetchone()[0]
    if first is None or last is None:
        return None

    earliest = connection.execute(
        f'SELECT "{column}" FROM "{table}" WHERE id >= ? ORDER BY id LIMIT 1',
        (first,),
    ).fetchone()[0]
    latest = connection.execute(
        f'SELECT "{column}" FROM "{table}" WHERE id <= ? ORDER BY id DESC LIMIT 1',
        (last,),
    ).fetchone()[0]
    if earliest >= cutoff:
        return first
    if latest < cutoff:
        return last + 1

    def timestamp_at_or_after(row_id: int) -> tuple[int, str] | None:
        return connection.execute(
            f'SELECT id, "{column}" FROM "{table}" WHERE id >= ? ORDER BY id LIMIT 1',
            (row_id,),
        ).fetchone()

    while first < last:
        middle = (first + last) // 2
        row = timestamp_at_or_after(middle)
        if row is None:
            last = middle
        elif row[1] < cutoff:
            first = row[0] + 1
        else:
            last = row[0]
    row = timestamp_at_or_after(first)
    return None if row is None else row[0]


def connect(path: Path, *, read_only: bool) -> sqlite3.Connection:
    if read_only:
        return sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True, timeout=2)
    connection = sqlite3.connect(path, timeout=5)
    connection.execute("PRAGMA busy_timeout=5000")
    return connection


def main() -> int:
    args = parse_args()
    if args.retention_days < 1 or args.book_days < 1:
        print("retention periods must be at least one day", file=sys.stderr)
        return 2
    if not args.database.is_file():
        print(f"database not found: {args.database}", file=sys.stderr)
        return 2

    mode = "APPLY" if args.apply else "DRY-RUN"
    now = datetime.now(UTC)
    print(f"mode: {mode}")
    print(f"database: {args.database}")
    print(f"as_of_utc: {now.isoformat()}")
    sizes(args.database)

    connection = connect(args.database, read_only=not args.apply)
    try:
        page_size = connection.execute("PRAGMA page_size").fetchone()[0]
        page_count = connection.execute("PRAGMA page_count").fetchone()[0]
        free_pages = connection.execute("PRAGMA freelist_count").fetchone()[0]
        print(
            f"pages: page_size={page_size} page_count={page_count} "
            f"freelist_count={free_pages}"
        )

        cutoffs: list[tuple[str, int | None]] = []
        for table, column in TABLES:
            days = (
                args.book_days
                if table in ("book_dirty_events", "book_observations")
                else args.retention_days
            )
            exists = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
            ).fetchone()
            if exists is None:
                print(f"table {table}: absent (skipped)")
                continue
            cutoff = (now - timedelta(days=days)).isoformat()
            first_kept = first_id_at_or_after(connection, table, column, cutoff)
            maximum = connection.execute(f'SELECT max(id) FROM "{table}"').fetchone()[0]
            print(
                f"table {table}: retention_days={days} cutoff={cutoff} "
                f"first_kept_id={first_kept} max_id={maximum}"
            )
            cutoffs.append((table, first_kept))

        if not args.apply:
            print("dry-run: no rows changed and no checkpoint requested")
            return 0

        connection.execute(f"PRAGMA journal_size_limit={JOURNAL_SIZE_LIMIT_BYTES}")
        for table, first_kept in cutoffs:
            deleted = 0
            if first_kept is None:
                print(f"deleted {table}: 0 rows (empty at preview)")
                continue
            while True:
                connection.execute("BEGIN IMMEDIATE")
                cursor = connection.execute(
                    f'DELETE FROM "{table}" WHERE id IN '
                    f'(SELECT id FROM "{table}" WHERE id < ? ORDER BY id LIMIT ?)',
                    (first_kept, DELETE_BATCH_SIZE),
                )
                count = cursor.rowcount
                connection.commit()
                deleted += count
                if count == 0:
                    break
                connection.execute("PRAGMA wal_checkpoint(PASSIVE)").fetchone()
            print(f"deleted {table}: {deleted} rows")
        checkpoint = connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        print(
            "wal_checkpoint(TRUNCATE): "
            f"busy={checkpoint[0]} log={checkpoint[1]} checkpointed={checkpoint[2]}"
        )
        if checkpoint[0]:
            print("checkpoint blocked by an active reader or writer", file=sys.stderr)
            return 1
        connection.close()
        connection = connect(args.database, read_only=True)
        page_size = connection.execute("PRAGMA page_size").fetchone()[0]
        page_count = connection.execute("PRAGMA page_count").fetchone()[0]
        free_pages = connection.execute("PRAGMA freelist_count").fetchone()[0]
        print(
            f"pages_after: page_size={page_size} page_count={page_count} "
            f"freelist_count={free_pages}"
        )
        sizes(args.database)
        print("note: main database pages are not compacted; VACUUM is not performed")
        return 0
    finally:
        connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
