"""Durable columnar research storage for high-frequency Polymarket capture."""

from __future__ import annotations

import os
import sys
import threading
import uuid
from collections.abc import Iterable
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from predictions_cup.external.polymarket.models import (
    BookChangeEvent,
    BookSnapshot,
    TradeEvent,
    hashed_trade_event_id,
)

_UTC_TIMESTAMP = pa.timestamp("us", tz="UTC")
_LEVELS = pa.list_(
    pa.struct(
        [
            pa.field("price", pa.string(), nullable=False),
            pa.field("size", pa.string(), nullable=False),
        ]
    )
)

_SCHEMAS: dict[str, pa.Schema] = {
    "observations": pa.schema(
        [
            ("token_id", pa.string()),
            ("market_id", pa.string()),
            ("source_timestamp", _UTC_TIMESTAMP),
            ("state_observed_at", _UTC_TIMESTAMP),
            ("observed_at", _UTC_TIMESTAMP),
            ("best_bid", pa.string()),
            ("best_ask", pa.string()),
            ("midpoint", pa.string()),
            ("spread", pa.string()),
            ("last_trade_price", pa.string()),
            ("book_valid", pa.bool_()),
        ]
    ),
    "book_changes": pa.schema(
        [
            ("token_id", pa.string()),
            ("market_id", pa.string()),
            ("side", pa.string()),
            ("price", pa.string()),
            ("size", pa.string()),
            ("source_timestamp", _UTC_TIMESTAMP),
            ("observed_at", _UTC_TIMESTAMP),
            ("best_bid", pa.string()),
            ("best_ask", pa.string()),
            ("book_hash", pa.string()),
        ]
    ),
    "trades": pa.schema(
        [
            ("event_id", pa.string()),
            ("token_id", pa.string()),
            ("market_id", pa.string()),
            ("price", pa.string()),
            ("size", pa.string()),
            ("side", pa.string()),
            ("source_timestamp", _UTC_TIMESTAMP),
            ("observed_at", _UTC_TIMESTAMP),
            ("transaction_hash", pa.string()),
            ("fee_rate_bps", pa.string()),
        ]
    ),
    "depth_snapshots": pa.schema(
        [
            ("token_id", pa.string()),
            ("market_id", pa.string()),
            ("source_timestamp", _UTC_TIMESTAMP),
            ("state_observed_at", _UTC_TIMESTAMP),
            ("recorded_at", _UTC_TIMESTAMP),
            ("best_bid", pa.string()),
            ("best_ask", pa.string()),
            ("midpoint", pa.string()),
            ("spread", pa.string()),
            ("bids", _LEVELS),
            ("asks", _LEVELS),
            ("book_hash", pa.string()),
            ("min_order_size", pa.string()),
            ("tick_size", pa.string()),
            ("neg_risk", pa.bool_()),
            ("last_trade_price", pa.string()),
        ]
    ),
}


def _decimal_text(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Parquet timestamps must be timezone-aware")
    return value.astimezone(UTC)


def _parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    normalized = _utc(parsed)
    assert normalized is not None
    return normalized


class PolymarketResearchStorage:
    """Write immutable short ZSTD Parquet shards using atomic publication."""

    def __init__(
        self,
        root: Path,
        *,
        shard_seconds: int = 60,
        max_rows_per_shard: int = 100_000,
        max_buffer_bytes: int = 8 * 1024 * 1024,
    ) -> None:
        if shard_seconds <= 0:
            raise ValueError("shard_seconds must be positive")
        if max_rows_per_shard <= 0:
            raise ValueError("max_rows_per_shard must be positive")
        if max_buffer_bytes <= 0:
            raise ValueError("max_buffer_bytes must be positive")
        self.root = root
        self.shard_seconds = shard_seconds
        self.max_rows_per_shard = max_rows_per_shard
        self.max_buffer_bytes = max_buffer_bytes
        self._buffers: dict[str, dict[int, list[dict[str, Any]]]] = {
            stream: {} for stream in _SCHEMAS
        }
        self._buffer_sizes: dict[str, dict[int, list[int]]] = {
            stream: {} for stream in _SCHEMAS
        }
        self._buffer_bytes: dict[str, dict[int, int]] = {
            stream: {} for stream in _SCHEMAS
        }
        self._lock = threading.Lock()

    def initialize(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        for stream in _SCHEMAS:
            (self.root / stream).mkdir(parents=True, exist_ok=True)

    def append_observations(
        self,
        snapshots: Iterable[BookSnapshot],
        observed_at: str,
    ) -> int:
        sampled_at = _parse_utc(observed_at)
        rows = [
            {
                "token_id": snapshot.token_id,
                "market_id": snapshot.market_id,
                "source_timestamp": _utc(snapshot.source_timestamp),
                "state_observed_at": _utc(snapshot.observed_at),
                "observed_at": sampled_at,
                "best_bid": _decimal_text(snapshot.best_bid),
                "best_ask": _decimal_text(snapshot.best_ask),
                "midpoint": _decimal_text(snapshot.midpoint),
                "spread": _decimal_text(snapshot.spread),
                "last_trade_price": _decimal_text(snapshot.last_trade_price),
                "book_valid": True,
            }
            for snapshot in snapshots
        ]
        return self._append("observations", rows, "observed_at")

    def append_book_changes(self, changes: Iterable[BookChangeEvent]) -> int:
        rows = [
            {
                "token_id": change.token_id,
                "market_id": change.market_id,
                "side": change.side,
                "price": str(change.price),
                "size": str(change.size),
                "source_timestamp": _utc(change.source_timestamp),
                "observed_at": _utc(change.observed_at),
                "best_bid": _decimal_text(change.best_bid),
                "best_ask": _decimal_text(change.best_ask),
                "book_hash": change.book_hash,
            }
            for change in changes
        ]
        return self._append("book_changes", rows, "observed_at")

    def append_trade(self, trade: TradeEvent) -> None:
        row = {
            "event_id": hashed_trade_event_id(
                trade.token_id,
                trade.transaction_hash,
            ),
            "token_id": trade.token_id,
            "market_id": trade.market_id,
            "price": str(trade.price),
            "size": _decimal_text(trade.size),
            "side": trade.side,
            "source_timestamp": _utc(trade.source_timestamp),
            "observed_at": _utc(trade.observed_at),
            "transaction_hash": trade.transaction_hash,
            "fee_rate_bps": _decimal_text(trade.fee_rate_bps),
        }
        self._append("trades", [row], "observed_at")

    def append_snapshots(
        self,
        snapshots: Iterable[BookSnapshot],
        recorded_at: str,
    ) -> int:
        sampled_at = _parse_utc(recorded_at)
        rows = [
            {
                "token_id": snapshot.token_id,
                "market_id": snapshot.market_id,
                "source_timestamp": _utc(snapshot.source_timestamp),
                "state_observed_at": _utc(snapshot.observed_at),
                "recorded_at": sampled_at,
                "best_bid": _decimal_text(snapshot.best_bid),
                "best_ask": _decimal_text(snapshot.best_ask),
                "midpoint": _decimal_text(snapshot.midpoint),
                "spread": _decimal_text(snapshot.spread),
                "bids": [
                    {"price": str(level.price), "size": str(level.size)}
                    for level in snapshot.bids
                ],
                "asks": [
                    {"price": str(level.price), "size": str(level.size)}
                    for level in snapshot.asks
                ],
                "book_hash": snapshot.book_hash,
                "min_order_size": _decimal_text(snapshot.min_order_size),
                "tick_size": _decimal_text(snapshot.tick_size),
                "neg_risk": snapshot.neg_risk,
                "last_trade_price": _decimal_text(snapshot.last_trade_price),
            }
            for snapshot in snapshots
        ]
        return self._append("depth_snapshots", rows, "recorded_at")

    def flush_due(self, now: datetime) -> int:
        normalized = _utc(now)
        assert normalized is not None
        current_epoch = int(normalized.timestamp())
        with self._lock:
            written = 0
            for stream in _SCHEMAS:
                for bucket in sorted(tuple(self._buffers[stream])):
                    if bucket + self.shard_seconds <= current_epoch:
                        written += self._flush_bucket(stream, bucket, force=True)
            return written

    def flush_all(self) -> int:
        with self._lock:
            written = 0
            for stream in _SCHEMAS:
                for bucket in sorted(tuple(self._buffers[stream])):
                    written += self._flush_bucket(stream, bucket, force=True)
            return written

    def _append(
        self,
        stream: str,
        rows: list[dict[str, Any]],
        time_field: str,
    ) -> int:
        if not rows:
            return 0
        with self._lock:
            buckets = self._buffers[stream]
            touched: set[int] = set()
            for row in rows:
                value = row[time_field]
                if not isinstance(value, datetime):
                    raise TypeError(f"{stream}.{time_field} must be datetime")
                bucket = self._bucket(value)
                buckets.setdefault(bucket, []).append(row)
                row_size = _estimated_object_size(row)
                self._buffer_sizes[stream].setdefault(bucket, []).append(row_size)
                stream_bytes = self._buffer_bytes[stream]
                stream_bytes[bucket] = stream_bytes.get(bucket, 0) + row_size
                touched.add(bucket)

            newest = max(touched)
            for bucket in sorted(tuple(buckets)):
                if bucket < newest:
                    self._flush_bucket(stream, bucket, force=True)
                elif (
                    len(buckets[bucket]) >= self.max_rows_per_shard
                    or self._buffer_bytes[stream][bucket] >= self.max_buffer_bytes
                ):
                    self._flush_bucket(stream, bucket, force=False)
        return len(rows)

    def _bucket(self, observed_at: datetime) -> int:
        epoch = int(observed_at.timestamp())
        return epoch - (epoch % self.shard_seconds)

    def _flush_bucket(self, stream: str, bucket: int, *, force: bool) -> int:
        rows = self._buffers[stream].get(bucket)
        if not rows:
            self._buffers[stream].pop(bucket, None)
            self._buffer_sizes[stream].pop(bucket, None)
            self._buffer_bytes[stream].pop(bucket, None)
            return 0

        sizes = self._buffer_sizes[stream][bucket]
        buffered_bytes = self._buffer_bytes[stream][bucket]
        written = 0
        while rows and (
            force
            or len(rows) >= self.max_rows_per_shard
            or buffered_bytes >= self.max_buffer_bytes
        ):
            take = min(len(rows), self.max_rows_per_shard)
            chunk_bytes = 0
            for index, size in enumerate(sizes[:take]):
                if index > 0 and chunk_bytes + size > self.max_buffer_bytes:
                    take = index
                    break
                chunk_bytes += size
            chunk = rows[:take]
            del rows[:take]
            del sizes[:take]
            buffered_bytes -= chunk_bytes
            self._buffer_bytes[stream][bucket] = buffered_bytes
            self._write_atomic(stream, bucket, chunk)
            written += len(chunk)

        if not rows:
            self._buffers[stream].pop(bucket, None)
            self._buffer_sizes[stream].pop(bucket, None)
            self._buffer_bytes[stream].pop(bucket, None)
        return written

    def _write_atomic(
        self,
        stream: str,
        bucket: int,
        rows: list[dict[str, Any]],
    ) -> None:
        bucket_at = datetime.fromtimestamp(bucket, tz=UTC)
        directory = (
            self.root
            / stream
            / bucket_at.strftime("%Y")
            / bucket_at.strftime("%m")
            / bucket_at.strftime("%d")
            / bucket_at.strftime("%H")
        )
        directory.mkdir(parents=True, exist_ok=True)
        stem = bucket_at.strftime("%Y%m%dT%H%M%SZ")
        unique = uuid.uuid4().hex
        final_path = directory / f"{stem}_part-{unique}.parquet"
        temporary_path = directory / f".{stem}_part-{unique}.tmp"

        table = pa.Table.from_pylist(rows, schema=_SCHEMAS[stream])
        try:
            pq.write_table(
                table,
                temporary_path,
                compression="zstd",
                use_dictionary=True,
            )
            with temporary_path.open("rb") as handle:
                os.fsync(handle.fileno())
            os.replace(temporary_path, final_path)
            _fsync_directory(directory)
        except Exception:
            temporary_path.unlink(missing_ok=True)
            raise


def _fsync_directory(path: Path) -> None:
    directory_flag = getattr(os, "O_DIRECTORY", 0)
    descriptor = os.open(path, os.O_RDONLY | directory_flag)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _estimated_object_size(value: object, seen: set[int] | None = None) -> int:
    """Conservatively estimate the Python object graph retained by a buffer row."""
    if seen is None:
        seen = set()
    identity = id(value)
    if identity in seen:
        return 0
    seen.add(identity)
    size = sys.getsizeof(value)
    if isinstance(value, dict):
        size += sum(
            _estimated_object_size(key, seen) + _estimated_object_size(item, seen)
            for key, item in value.items()
        )
    elif isinstance(value, (list, tuple, set, frozenset)):
        size += sum(_estimated_object_size(item, seen) for item in value)
    return size
