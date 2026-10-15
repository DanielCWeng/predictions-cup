"""Read-only census of SIG versioned books embedded in retained raw_events Parquet.

This is a source-presence audit, NOT a causal depth, fill, or PnL certificate.
It does not use API credentials, networking, execution clients, or active SQLite.
"""
from __future__ import annotations

import argparse
import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq


def _batches(payload: object) -> Iterator[dict[str, Any]]:
    if not isinstance(payload, dict):
        return
    markets = payload.get("markets")
    if isinstance(markets, list):
        for entry in markets:
            if isinstance(entry, dict) and isinstance(entry.get("batch"), dict):
                yield entry["batch"]
    else:
        yield payload


def _has_aware_clock(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return stamp.utcoffset() is not None


@dataclass
class RawBookAudit:
    files_read: int = 0
    raw_rows: int = 0
    malformed_json_rows: int = 0
    batches_with_books: int = 0
    versioned_books: int = 0
    unversioned_or_malformed_books: int = 0
    two_sided_versioned_books: int = 0
    duplicate_versions_in_epoch: int = 0
    backwards_versions_in_epoch: int = 0
    _versions: dict[tuple[str, int, str], int] = field(default_factory=dict)
    _exchanges: set[str] = field(default_factory=set)
    _sessions: set[str] = field(default_factory=set)

    def consume(self, raw: object, *, session: object, epoch: object) -> None:
        self.raw_rows += 1
        if not isinstance(raw, str):
            self.malformed_json_rows += 1
            return
        try:
            payload = json.loads(raw)
        except (ValueError, TypeError):
            self.malformed_json_rows += 1
            return
        session_key = str(session) if isinstance(session, str) else "UNKNOWN_SESSION"
        epoch_key = epoch if isinstance(epoch, int) and not isinstance(epoch, bool) else -1
        self._sessions.add(session_key)
        for batch in _batches(payload):
            books = batch.get("books")
            if not isinstance(books, list) or not books:
                continue
            self.batches_with_books += 1
            for item in books:
                if not isinstance(item, dict):
                    self.unversioned_or_malformed_books += 1
                    continue
                native_id = item.get("exchangeId")
                version = item.get("asOf")
                if (
                    isinstance(native_id, bool)
                    or not isinstance(native_id, (str, int))
                    or not str(native_id).strip()
                    or not isinstance(version, dict)
                    or isinstance(version.get("sequence"), bool)
                    or not isinstance(version.get("sequence"), int)
                    or version["sequence"] < 0
                    or not _has_aware_clock(version.get("at"))
                ):
                    self.unversioned_or_malformed_books += 1
                    continue
                exchange_id = str(native_id)
                self._exchanges.add(exchange_id)
                self.versioned_books += 1
                if (
                    isinstance(item.get("bids"), list)
                    and bool(item["bids"])
                    and isinstance(item.get("asks"), list)
                    and bool(item["asks"])
                ):
                    # Descriptive presence only; levels are NOT executable certified.
                    self.two_sided_versioned_books += 1
                key = (session_key, epoch_key, exchange_id)
                sequence = version["sequence"]
                prior = self._versions.get(key)
                if prior is not None:
                    if sequence == prior:
                        self.duplicate_versions_in_epoch += 1
                    elif sequence < prior:
                        self.backwards_versions_in_epoch += 1
                if prior is None or sequence > prior:
                    self._versions[key] = sequence

    def summary(self) -> dict[str, object]:
        return {
            "status": "SOURCE_PRESENCE_ONLY_NOT_EXECUTION_CERTIFIED",
            "files_read": self.files_read,
            "raw_rows": self.raw_rows,
            "malformed_json_rows": self.malformed_json_rows,
            "batches_with_books": self.batches_with_books,
            "versioned_books": self.versioned_books,
            "unversioned_or_malformed_books": self.unversioned_or_malformed_books,
            "two_sided_versioned_books": self.two_sided_versioned_books,
            "unique_exchanges": len(self._exchanges),
            "observed_sessions": len(self._sessions),
            "duplicate_versions_in_epoch": self.duplicate_versions_in_epoch,
            "backwards_versions_in_epoch": self.backwards_versions_in_epoch,
            "coverage_completeness": "UNKNOWN",
            "paired_executable_opportunities": None,
            "fill_probability": None,
            "after_cost_pnl": None,
        }


def scan_raw_events(research_root: Path, *, max_files: int = 100_000) -> RawBookAudit:
    """Stream existing immutable raw shards; fail on absent/incomplete input paths."""
    raw_root = research_root if research_root.name == "raw_events" else research_root / "raw_events"
    if (
        not raw_root.is_dir()
        or raw_root.is_symlink()
        or raw_root.resolve() != raw_root.absolute()
    ):
        raise ValueError("existing non-symlink raw_events directory required")
    paths = sorted(raw_root.rglob("*.parquet"))
    if not paths:
        raise ValueError("no raw_events parquet shards found")
    if len(paths) > max_files:
        raise ValueError("raw_events shard count exceeds declared file limit")
    audit = RawBookAudit()
    for path in paths:
        if path.is_symlink() or not path.is_file():
            raise ValueError("refusing symlink or missing raw_events shard")
        parquet = pq.ParquetFile(path)
        fields = set(parquet.schema_arrow.names)
        if "raw_json" not in fields:
            raise ValueError("raw shard lacks raw_json column")
        columns = [
            name for name in ("raw_json", "session_id", "connection_epoch")
            if name in fields
        ]
        for batch in parquet.iter_batches(batch_size=512, columns=columns):
            for row in batch.to_pylist():
                audit.consume(
                    row["raw_json"],
                    session=row.get("session_id"),
                    epoch=row.get("connection_epoch"),
                )
        audit.files_read += 1
    return audit


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--research-root", type=Path, required=True)
    parser.add_argument("--max-files", type=int, default=100_000)
    args = parser.parse_args()
    if args.max_files < 1:
        parser.error("max-files must be positive")
    try:
        result = scan_raw_events(args.research_root, max_files=args.max_files)
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    print(json.dumps(result.summary(), sort_keys=True))


if __name__ == "__main__":
    main()
