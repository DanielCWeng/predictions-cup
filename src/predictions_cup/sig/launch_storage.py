"""Launch-grade immutable SIG research capture and forward-compatible research events."""

from __future__ import annotations

import json
import os
import queue
import threading
import time
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from predictions_cup.models import OrderBook
from predictions_cup.sig.dto import MarketDto, PriceSnapshotDto
from predictions_cup.sig.realtime_models import (
    BookDirtyDto,
    MarketSettledDto,
    RealtimeDeliveryDto,
    RealtimeTradeDto,
)
from predictions_cup.sig.realtime_storage import SigRealtimeRecorder

SCHEMA_VERSION = "capture-001-v1"
_UTC_TIMESTAMP = pa.timestamp("us", tz="UTC")

_SCHEMAS: dict[str, pa.Schema] = {
    "raw_events": pa.schema(
        [
            ("session_id", pa.string()),
            ("schema_version", pa.string()),
            ("source", pa.string()),
            ("event_type", pa.string()),
            ("topic", pa.string()),
            ("tournament_id", pa.string()),
            ("revision", pa.int64()),
            ("previous_revision", pa.int64()),
            ("source_sequence_from", pa.int64()),
            ("source_sequence_through", pa.int64()),
            ("local_receive_at", _UTC_TIMESTAMP),
            ("monotonic_receive_ns", pa.int64()),
            ("parsed_at", _UTC_TIMESTAMP),
            ("validation_error", pa.string()),
            ("raw_json", pa.string()),
        ]
    ),
    "normalized_events": pa.schema(
        [
            ("session_id", pa.string()),
            ("schema_version", pa.string()),
            ("source", pa.string()),
            ("event_type", pa.string()),
            ("tournament_id", pa.string()),
            ("exchange_id", pa.string()),
            ("market_id", pa.string()),
            ("venue_timestamp", _UTC_TIMESTAMP),
            ("observed_at", _UTC_TIMESTAMP),
            ("monotonic_ns", pa.int64()),
            ("revision", pa.int64()),
            ("trust_state", pa.string()),
            ("provenance", pa.string()),
            ("evidence_label", pa.string()),
            ("price", pa.string()),
            ("quantity", pa.string()),
            ("best_bid", pa.string()),
            ("best_ask", pa.string()),
            ("spread", pa.string()),
            ("reason", pa.string()),
            ("payload_json", pa.string()),
        ]
    ),
    "liquidity_events": pa.schema(
        [
            ("session_id", pa.string()),
            ("schema_version", pa.string()),
            ("tournament_id", pa.string()),
            ("exchange_id", pa.string()),
            ("market_id", pa.string()),
            ("observed_at", _UTC_TIMESTAMP),
            ("monotonic_ns", pa.int64()),
            ("side", pa.string()),
            ("price", pa.string()),
            ("old_quantity", pa.string()),
            ("new_quantity", pa.string()),
            ("change_type", pa.string()),
            ("evidence_label", pa.string()),
            ("reason", pa.string()),
            ("triggering_revision", pa.int64()),
        ]
    ),
    "strategy_events": pa.schema(
        [
            ("session_id", pa.string()),
            ("schema_version", pa.string()),
            ("event_type", pa.string()),
            ("observed_at", _UTC_TIMESTAMP),
            ("monotonic_ns", pa.int64()),
            ("tournament_id", pa.string()),
            ("exchange_id", pa.string()),
            ("market_id", pa.string()),
            ("strategy_id", pa.string()),
            ("strategy_version", pa.string()),
            ("fair_value_provider", pa.string()),
            ("fair_value_version", pa.string()),
            ("signal_provider", pa.string()),
            ("signal_version", pa.string()),
            ("payload_json", pa.string()),
        ]
    ),
    "ets_state": pa.schema(
        [
            ("session_id", pa.string()),
            ("schema_version", pa.string()),
            ("observed_at", _UTC_TIMESTAMP),
            ("monotonic_ns", pa.int64()),
            ("graph_id", pa.string()),
            ("model_version", pa.string()),
            ("composite_fv", pa.string()),
            ("uncertainty", pa.string()),
            ("constituent_freshness_json", pa.string()),
            ("constituent_provenance_json", pa.string()),
            ("components_json", pa.string()),
        ]
    ),
}


class CaptureBackpressureError(RuntimeError):
    """Raised immediately when lossless research persistence can no longer keep up."""


class CaptureStorageError(RuntimeError):
    """Raised when the background immutable writer has failed."""


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("capture timestamps must be timezone-aware")
    return value.astimezone(UTC)


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def _decimal(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


class ImmutableCaptureSink:
    """Bounded non-blocking producer plus a dedicated immutable Parquet writer thread."""

    def __init__(
        self,
        root: Path,
        *,
        shard_seconds: int = 60,
        max_rows_per_shard: int = 100_000,
        queue_max: int = 200_000,
    ) -> None:
        if shard_seconds <= 0 or max_rows_per_shard <= 0 or queue_max <= 0:
            raise ValueError("capture storage limits must be positive")
        self.root = root
        self.shard_seconds = shard_seconds
        self.max_rows_per_shard = max_rows_per_shard
        self._queue: queue.Queue[tuple[str, dict[str, Any]] | None] = queue.Queue(
            maxsize=queue_max
        )
        self._buffers: dict[str, dict[int, list[dict[str, Any]]]] = {
            stream: {} for stream in _SCHEMAS
        }
        self._thread = threading.Thread(
            target=self._run,
            name="capture-001-parquet-writer",
            daemon=True,
        )
        self._error: BaseException | None = None
        self._closed = False
        self._written_rows = 0
        self._written_shards = 0
        self._dropped_rows = 0
        self._last_write_at: datetime | None = None
        self._initialize()
        self._thread.start()

    def _initialize(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        for stream in _SCHEMAS:
            (self.root / stream).mkdir(parents=True, exist_ok=True)

    def emit(self, stream: str, row: dict[str, Any]) -> None:
        if stream not in _SCHEMAS:
            raise KeyError(f"unknown capture stream: {stream}")
        if self._closed:
            raise CaptureStorageError("capture sink is closed")
        if self._error is not None:
            raise CaptureStorageError("capture writer previously failed") from self._error
        try:
            self._queue.put_nowait((stream, row))
        except queue.Full as exc:
            self._dropped_rows += 1
            raise CaptureBackpressureError(
                "capture queue exhausted; refusing silent research-data loss"
            ) from exc

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._queue.put(None)
        self._thread.join()
        if self._error is not None:
            raise CaptureStorageError("capture writer failed") from self._error

    def health_snapshot(self) -> dict[str, object]:
        return {
            "schema_version": SCHEMA_VERSION,
            "research_root": str(self.root),
            "writer_alive": self._thread.is_alive(),
            "queue_depth": self._queue.qsize(),
            "queue_capacity": self._queue.maxsize,
            "written_rows": self._written_rows,
            "written_shards": self._written_shards,
            "dropped_rows": self._dropped_rows,
            "storage_failures": 0 if self._error is None else 1,
            "last_write_at": self._last_write_at,
        }

    def _run(self) -> None:
        try:
            while True:
                item = self._queue.get()
                if item is None:
                    break
                stream, row = item
                self._append_now(stream, row)
            self._flush_all()
        except BaseException as exc:
            self._error = exc

    def _append_now(self, stream: str, row: dict[str, Any]) -> None:
        observed = _event_time(stream, row)
        bucket = int(observed.timestamp())
        bucket -= bucket % self.shard_seconds
        buffers = self._buffers[stream]
        buffers.setdefault(bucket, []).append(row)
        for old_bucket in sorted(tuple(buffers)):
            if old_bucket < bucket:
                self._flush_bucket(stream, old_bucket, force=True)
            elif len(buffers[old_bucket]) >= self.max_rows_per_shard:
                self._flush_bucket(stream, old_bucket, force=False)

    def _flush_all(self) -> None:
        for stream in _SCHEMAS:
            for bucket in sorted(tuple(self._buffers[stream])):
                self._flush_bucket(stream, bucket, force=True)

    def _flush_bucket(self, stream: str, bucket: int, *, force: bool) -> None:
        rows = self._buffers[stream].get(bucket)
        if not rows:
            self._buffers[stream].pop(bucket, None)
            return
        while len(rows) >= self.max_rows_per_shard or (force and rows):
            take = min(len(rows), self.max_rows_per_shard)
            chunk = rows[:take]
            del rows[:take]
            self._write_atomic(stream, bucket, chunk)
        if not rows:
            self._buffers[stream].pop(bucket, None)

    def _write_atomic(
        self,
        stream: str,
        bucket: int,
        rows: list[dict[str, Any]],
    ) -> None:
        at = datetime.fromtimestamp(bucket, tz=UTC)
        directory = (
            self.root
            / stream
            / at.strftime("%Y")
            / at.strftime("%m")
            / at.strftime("%d")
            / at.strftime("%H")
        )
        directory.mkdir(parents=True, exist_ok=True)
        stem = at.strftime("%Y%m%dT%H%M%SZ")
        unique = uuid.uuid4().hex
        final_path = directory / f"{stem}_part-{unique}.parquet"
        temporary_path = directory / f".{stem}_part-{unique}.tmp"
        table = pa.Table.from_pylist(rows, schema=_SCHEMAS[stream])
        try:
            pq.write_table(table, temporary_path, compression="zstd", use_dictionary=True)
            with temporary_path.open("rb") as handle:
                os.fsync(handle.fileno())
            os.replace(temporary_path, final_path)
            _fsync_directory(directory)
        except Exception:
            temporary_path.unlink(missing_ok=True)
            raise
        self._written_rows += len(rows)
        self._written_shards += 1
        self._last_write_at = datetime.now(UTC)


def _event_time(stream: str, row: dict[str, Any]) -> datetime:
    field = "local_receive_at" if stream == "raw_events" else "observed_at"
    value = row.get(field)
    if not isinstance(value, datetime):
        raise TypeError(f"{stream}.{field} must be datetime")
    normalized = _utc(value)
    assert normalized is not None
    return normalized


def _fsync_directory(path: Path) -> None:
    flags = getattr(os, "O_DIRECTORY", 0)
    descriptor = os.open(path, os.O_RDONLY | flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


class LaunchSigRecorder(SigRealtimeRecorder):
    """Accepted operational SQLite plus lossless-as-permitted immutable research capture."""

    def __init__(
        self,
        path: Path,
        *,
        research_root: Path,
        queue_max: int = 200_000,
        shard_seconds: int = 60,
        max_rows_per_shard: int = 100_000,
        session_id: str | None = None,
    ) -> None:
        super().__init__(path)
        self.session_id = session_id or uuid.uuid4().hex
        self._sink = ImmutableCaptureSink(
            research_root,
            shard_seconds=shard_seconds,
            max_rows_per_shard=max_rows_per_shard,
            queue_max=queue_max,
        )
        self._last_books: dict[str, OrderBook] = {}

    def close(self) -> None:
        try:
            self._sink.close()
        finally:
            super().close()

    def capture_health_snapshot(self) -> dict[str, object]:
        return self._sink.health_snapshot()

    def record_raw_batch(
        self,
        *,
        topic: str,
        payload: object,
        observed_at: datetime,
        monotonic_receive_ns: int,
        parsed_at: datetime,
        validation_error: str | None,
    ) -> None:
        delivery: Mapping[str, object] = {}
        tournament_id: str | None = None
        if isinstance(payload, Mapping):
            candidate = payload.get("delivery")
            if isinstance(candidate, Mapping):
                delivery = candidate
            for collection in ("trades", "bookDirty", "marketSettled"):
                values = payload.get(collection)
                if isinstance(values, list) and values and isinstance(values[0], Mapping):
                    candidate_id = values[0].get("tournamentId")
                    if isinstance(candidate_id, str):
                        tournament_id = candidate_id
                        break
        self._sink.emit(
            "raw_events",
            {
                "session_id": self.session_id,
                "schema_version": SCHEMA_VERSION,
                "source": "SIG_REALTIME_DECODED",
                "event_type": "MARKET_BATCH",
                "topic": topic,
                "tournament_id": tournament_id,
                "revision": _int_or_none(delivery.get("revision")),
                "previous_revision": _int_or_none(delivery.get("previousRevision")),
                "source_sequence_from": _int_or_none(delivery.get("sourceSequenceFrom")),
                "source_sequence_through": _int_or_none(delivery.get("sourceSequenceThrough")),
                "local_receive_at": _utc(observed_at),
                "monotonic_receive_ns": monotonic_receive_ns,
                "parsed_at": _utc(parsed_at),
                "validation_error": validation_error,
                "raw_json": _json(payload),
            },
        )

    def record_delivery(
        self,
        *,
        topic: str,
        delivery: RealtimeDeliveryDto,
        observed_at: datetime,
    ) -> None:
        super().record_delivery(topic=topic, delivery=delivery, observed_at=observed_at)
        self._emit_normalized(
            event_type="DELIVERY",
            observed_at=observed_at,
            revision=delivery.revision,
            provenance="SIG_REALTIME",
            payload=delivery.model_dump(mode="json", by_alias=True),
        )

    def record_trade(
        self,
        *,
        topic: str,
        revision: int,
        trade: RealtimeTradeDto,
        observed_at: datetime,
    ) -> None:
        super().record_trade(
            topic=topic, revision=revision, trade=trade, observed_at=observed_at
        )
        self._emit_normalized(
            event_type="TRADE",
            tournament_id=trade.tournament_id,
            exchange_id=trade.exchange_id,
            market_id=trade.market_id,
            venue_timestamp=trade.executed_at,
            observed_at=observed_at,
            revision=revision,
            provenance="SIG_REALTIME",
            evidence_label="OBSERVED_TRADE",
            price=str(trade.price),
            quantity=str(trade.quantity),
            payload=trade.model_dump(mode="json", by_alias=True),
        )

    def record_book_dirty(
        self,
        *,
        topic: str,
        revision: int,
        event: BookDirtyDto,
        observed_at: datetime,
    ) -> None:
        super().record_book_dirty(
            topic=topic, revision=revision, event=event, observed_at=observed_at
        )
        self._emit_normalized(
            event_type="BOOK_DIRTY",
            tournament_id=event.tournament_id,
            exchange_id=event.exchange_id,
            market_id=event.market_id,
            venue_timestamp=event.at,
            observed_at=observed_at,
            revision=revision,
            provenance="SIG_REALTIME",
            evidence_label="OBSERVED_INVALIDATION",
            payload=event.model_dump(mode="json", by_alias=True),
        )

    def record_market_settled(
        self,
        *,
        topic: str,
        revision: int,
        event: MarketSettledDto,
        observed_at: datetime,
    ) -> None:
        super().record_market_settled(
            topic=topic, revision=revision, event=event, observed_at=observed_at
        )
        self._emit_normalized(
            event_type="MARKET_SETTLED",
            tournament_id=event.tournament_id,
            market_id=event.market_id,
            venue_timestamp=event.at,
            observed_at=observed_at,
            revision=revision,
            provenance="SIG_REALTIME",
            evidence_label="OBSERVED_SETTLEMENT_SIGNAL",
            payload=event.model_dump(mode="json", by_alias=True),
        )

    def record_market(
        self,
        *,
        tournament_id: str,
        market: MarketDto,
        observed_at: datetime,
        reason: str,
        triggering_revision: int | None,
    ) -> None:
        super().record_market(
            tournament_id=tournament_id,
            market=market,
            observed_at=observed_at,
            reason=reason,
            triggering_revision=triggering_revision,
        )
        self._emit_normalized(
            event_type="MARKET_SNAPSHOT",
            tournament_id=tournament_id,
            market_id=market.id,
            observed_at=observed_at,
            revision=triggering_revision,
            provenance="SIG_REST_AUTHORITATIVE",
            reason=reason,
            payload=market.model_dump(mode="json", by_alias=True),
        )

    def record_prices(
        self,
        *,
        tournament_id: str,
        prices: tuple[PriceSnapshotDto, ...],
        observed_at: datetime,
        reason: str,
    ) -> None:
        super().record_prices(
            tournament_id=tournament_id,
            prices=prices,
            observed_at=observed_at,
            reason=reason,
        )
        for item in prices:
            self._emit_normalized(
                event_type="BBO_SNAPSHOT",
                tournament_id=tournament_id,
                exchange_id=item.exchange_id,
                market_id=item.market_id,
                observed_at=observed_at,
                provenance="SIG_REST_AUTHORITATIVE",
                evidence_label="AUTHORITATIVE_SCALAR",
                price=_decimal(item.latest_price),
                best_bid=_decimal(item.best_bid),
                best_ask=_decimal(item.best_ask),
                spread=_decimal(item.spread),
                reason=reason,
                payload=item.model_dump(mode="json", by_alias=True),
            )

    def record_missing_prices(
        self,
        *,
        tournament_id: str,
        exchanges: tuple[tuple[str, str], ...],
        observed_at: datetime,
        reason: str,
    ) -> None:
        super().record_missing_prices(
            tournament_id=tournament_id,
            exchanges=exchanges,
            observed_at=observed_at,
            reason=reason,
        )
        for exchange_id, market_id in exchanges:
            self._emit_normalized(
                event_type="BBO_MISSING",
                tournament_id=tournament_id,
                exchange_id=exchange_id,
                market_id=market_id,
                observed_at=observed_at,
                provenance="SIG_REST_AUTHORITATIVE",
                evidence_label="AUTHORITATIVE_MISSING",
                reason=reason,
                payload={},
            )

    def record_book(
        self,
        *,
        market_id: str,
        tournament_id: str,
        book: OrderBook,
        observed_at: datetime,
        reason: str,
        triggering_revision: int | None,
    ) -> None:
        previous = self._last_books.get(book.exchange_id)
        super().record_book(
            market_id=market_id,
            tournament_id=tournament_id,
            book=book,
            observed_at=observed_at,
            reason=reason,
            triggering_revision=triggering_revision,
        )
        best_bid = book.bids[0].price if book.bids else None
        best_ask = book.asks[0].price if book.asks else None
        spread = None if best_bid is None or best_ask is None else best_ask - best_bid
        self._emit_normalized(
            event_type="DEPTH_SNAPSHOT",
            tournament_id=tournament_id,
            exchange_id=book.exchange_id,
            market_id=market_id,
            venue_timestamp=book.timestamp,
            observed_at=observed_at,
            revision=triggering_revision,
            provenance="SIG_REST_AUTHORITATIVE",
            evidence_label="AUTHORITATIVE_DEPTH",
            best_bid=_decimal(best_bid),
            best_ask=_decimal(best_ask),
            spread=_decimal(spread),
            reason=reason,
            payload=book.model_dump(mode="json"),
        )
        if previous is not None:
            self._emit_liquidity_changes(
                previous=previous,
                current=book,
                market_id=market_id,
                tournament_id=tournament_id,
                observed_at=observed_at,
                reason=reason,
                triggering_revision=triggering_revision,
            )
        self._last_books[book.exchange_id] = book

    def record_transition(
        self,
        *,
        topic: str,
        transition: str,
        observed_at: datetime,
        exchange_id: str | None = None,
        revision: int | None = None,
        detail: str | None = None,
    ) -> None:
        super().record_transition(
            topic=topic,
            transition=transition,
            observed_at=observed_at,
            exchange_id=exchange_id,
            revision=revision,
            detail=detail,
        )
        self._emit_normalized(
            event_type="TRUST_TRANSITION",
            exchange_id=exchange_id,
            observed_at=observed_at,
            revision=revision,
            trust_state=transition,
            provenance="LOCAL_STATE_ENGINE",
            evidence_label="LOCAL_TRUST_STATE",
            reason=detail,
            payload={"topic": topic},
        )

    def record_shadow_make(
        self,
        *,
        observed_at: datetime,
        tournament_id: str,
        exchange_id: str,
        market_id: str,
        strategy_id: str,
        strategy_version: str,
        fair_value_provider: str,
        fair_value_version: str,
        signal_provider: str | None,
        signal_version: str | None,
        payload: Mapping[str, object],
    ) -> None:
        """Forward-compatible MAKE/shadow surface with no dependency on MAKE internals."""
        self._sink.emit(
            "strategy_events",
            {
                "session_id": self.session_id,
                "schema_version": SCHEMA_VERSION,
                "event_type": "SHADOW_MAKE",
                "observed_at": _utc(observed_at),
                "monotonic_ns": time.monotonic_ns(),
                "tournament_id": tournament_id,
                "exchange_id": exchange_id,
                "market_id": market_id,
                "strategy_id": strategy_id,
                "strategy_version": strategy_version,
                "fair_value_provider": fair_value_provider,
                "fair_value_version": fair_value_version,
                "signal_provider": signal_provider,
                "signal_version": signal_version,
                "payload_json": _json(dict(payload)),
            },
        )

    def record_ets_state(
        self,
        *,
        observed_at: datetime,
        graph_id: str,
        model_version: str,
        composite_fv: Decimal | None,
        uncertainty: Decimal | None,
        component_values: Mapping[str, object],
        constituent_freshness: Mapping[str, object],
        constituent_provenance: Mapping[str, object],
    ) -> None:
        """Optional aggregate/ETS state; the research/model lane owns its semantics."""
        self._sink.emit(
            "ets_state",
            {
                "session_id": self.session_id,
                "schema_version": SCHEMA_VERSION,
                "observed_at": _utc(observed_at),
                "monotonic_ns": time.monotonic_ns(),
                "graph_id": graph_id,
                "model_version": model_version,
                "composite_fv": _decimal(composite_fv),
                "uncertainty": _decimal(uncertainty),
                "constituent_freshness_json": _json(dict(constituent_freshness)),
                "constituent_provenance_json": _json(dict(constituent_provenance)),
                "components_json": _json(dict(component_values)),
            },
        )

    def _emit_normalized(
        self,
        *,
        event_type: str,
        observed_at: datetime,
        tournament_id: str | None = None,
        exchange_id: str | None = None,
        market_id: str | None = None,
        venue_timestamp: datetime | None = None,
        revision: int | None = None,
        trust_state: str | None = None,
        provenance: str,
        evidence_label: str | None = None,
        price: str | None = None,
        quantity: str | None = None,
        best_bid: str | None = None,
        best_ask: str | None = None,
        spread: str | None = None,
        reason: str | None = None,
        payload: object,
    ) -> None:
        self._sink.emit(
            "normalized_events",
            {
                "session_id": self.session_id,
                "schema_version": SCHEMA_VERSION,
                "source": "SIG",
                "event_type": event_type,
                "tournament_id": tournament_id,
                "exchange_id": exchange_id,
                "market_id": market_id,
                "venue_timestamp": _utc(venue_timestamp),
                "observed_at": _utc(observed_at),
                "monotonic_ns": time.monotonic_ns(),
                "revision": revision,
                "trust_state": trust_state,
                "provenance": provenance,
                "evidence_label": evidence_label,
                "price": price,
                "quantity": quantity,
                "best_bid": best_bid,
                "best_ask": best_ask,
                "spread": spread,
                "reason": reason,
                "payload_json": _json(payload),
            },
        )

    def _emit_liquidity_changes(
        self,
        *,
        previous: OrderBook,
        current: OrderBook,
        market_id: str,
        tournament_id: str,
        observed_at: datetime,
        reason: str,
        triggering_revision: int | None,
    ) -> None:
        for side_name, old_levels, new_levels in (
            ("BID", previous.bids, current.bids),
            ("ASK", previous.asks, current.asks),
        ):
            old = {level.price: level.quantity for level in old_levels}
            new = {level.price: level.quantity for level in new_levels}
            for price in sorted(old.keys() | new.keys()):
                old_quantity = old.get(price)
                new_quantity = new.get(price)
                if old_quantity == new_quantity:
                    continue
                if old_quantity is None:
                    change_type = "LEVEL_APPEARED"
                    evidence = "OBSERVED_LEVEL_INCREASE"
                elif new_quantity is None:
                    change_type = "LEVEL_DISAPPEARED"
                    evidence = "AMBIGUOUS_DEPTH_DECREASE"
                elif new_quantity > old_quantity:
                    change_type = "LEVEL_INCREASED"
                    evidence = "OBSERVED_LEVEL_INCREASE"
                else:
                    change_type = "LEVEL_DECREASED"
                    evidence = "AMBIGUOUS_DEPTH_DECREASE"
                self._sink.emit(
                    "liquidity_events",
                    {
                        "session_id": self.session_id,
                        "schema_version": SCHEMA_VERSION,
                        "tournament_id": tournament_id,
                        "exchange_id": current.exchange_id,
                        "market_id": market_id,
                        "observed_at": _utc(observed_at),
                        "monotonic_ns": time.monotonic_ns(),
                        "side": side_name,
                        "price": str(price),
                        "old_quantity": _decimal(old_quantity),
                        "new_quantity": _decimal(new_quantity),
                        "change_type": change_type,
                        "evidence_label": evidence,
                        "reason": reason,
                        "triggering_revision": triggering_revision,
                    },
                )

        old_bid = previous.bids[0].price if previous.bids else None
        new_bid = current.bids[0].price if current.bids else None
        old_ask = previous.asks[0].price if previous.asks else None
        new_ask = current.asks[0].price if current.asks else None
        if old_bid != new_bid or old_ask != new_ask:
            self._emit_book_state_change(
                current=current,
                market_id=market_id,
                tournament_id=tournament_id,
                observed_at=observed_at,
                reason=reason,
                triggering_revision=triggering_revision,
                change_type="BBO_MOVED",
            )
        old_spread = None if old_bid is None or old_ask is None else old_ask - old_bid
        new_spread = None if new_bid is None or new_ask is None else new_ask - new_bid
        if old_spread is not None and new_spread is not None and old_spread != new_spread:
            self._emit_book_state_change(
                current=current,
                market_id=market_id,
                tournament_id=tournament_id,
                observed_at=observed_at,
                reason=reason,
                triggering_revision=triggering_revision,
                change_type=(
                    "SPREAD_WIDENED" if new_spread > old_spread else "SPREAD_NARROWED"
                ),
            )

    def _emit_book_state_change(
        self,
        *,
        current: OrderBook,
        market_id: str,
        tournament_id: str,
        observed_at: datetime,
        reason: str,
        triggering_revision: int | None,
        change_type: str,
    ) -> None:
        self._sink.emit(
            "liquidity_events",
            {
                "session_id": self.session_id,
                "schema_version": SCHEMA_VERSION,
                "tournament_id": tournament_id,
                "exchange_id": current.exchange_id,
                "market_id": market_id,
                "observed_at": _utc(observed_at),
                "monotonic_ns": time.monotonic_ns(),
                "side": None,
                "price": None,
                "old_quantity": None,
                "new_quantity": None,
                "change_type": change_type,
                "evidence_label": "OBSERVED_STATE_TRANSITION",
                "reason": reason,
                "triggering_revision": triggering_revision,
            },
        )


def _int_or_none(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    return value if isinstance(value, int) else None
