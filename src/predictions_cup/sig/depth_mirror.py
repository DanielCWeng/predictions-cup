"""Optional, best-effort mirror of maker-fetched SIG full-depth REST books.

No REST, execution client, trade, or risk dependency. The hot path only enqueues
an immutable canonical OrderBook reference. A daemon worker serialises to one
exclusive research-only JSONL file. Every book is *indicative*: SIG REST
supplies no exchange event timestamp or source revision.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from queue import Empty, Full, Queue
from threading import Event, Thread
from typing import Any
from uuid import uuid4

from predictions_cup.models.market import OrderBook

_MAX_BYTES = 32 * 1024 * 1024
_STOP = object()


@dataclass(frozen=True)
class _Captured:
    book: OrderBook
    market_id: str
    tournament_id: str
    received_at: datetime
    triggering_revision: int | None


class MakerDepthMirror:
    """Fail-open maker, fail-closed research: no producer I/O or blocking puts."""

    def __init__(
        self, output: Path, *, queue_size: int = 128,
        max_bytes: int = _MAX_BYTES,
    ) -> None:
        if not output.is_absolute() or not output.name.endswith(".jsonl"):
            raise ValueError("explicit absolute research .jsonl path required")
        # Never target any active DB, WAL, or capture shard under the checkout's data root.
        if output.resolve().is_relative_to((Path.cwd() / "data").resolve()):
            raise ValueError("research mirror cannot write into the active data directory")
        if not 1 <= queue_size <= 4096 or not 1024 <= max_bytes <= _MAX_BYTES:
            raise ValueError("invalid bounded research limits")
        self.output = output
        self.max_bytes = max_bytes
        self.run_id = uuid4().hex
        self._queue: Queue[_Captured | object] = Queue(maxsize=queue_size)
        self._stop = Event()
        self._thread: Thread | None = None
        self._disabled = False
        self.accepted = 0
        self.written = 0
        self.dropped = 0
        self.producer_rejected = 0
        self.writer_lost = 0
        self.errors = 0

    def start(self) -> None:
        if self._thread is not None:
            raise ValueError("mirror cannot start twice")
        self._thread = Thread(target=self._write_loop, name="sig-depth-mirror", daemon=True)
        try:
            self._thread.start()
        except Exception:
            # An optional research thread must not prevent LIVE maker startup.
            self.errors += 1
            self._disabled = True
            self._thread = None

    def record(
        self,
        book: OrderBook,
        market_id: str,
        tournament_id: str,
        received_at: datetime,
        triggering_revision: int | None,
    ) -> None:
        """Maker-safe O(1) enqueue; never serialize, sleep, await or touch disk."""
        if (self._disabled or self._stop.is_set() or self._thread is None
                or received_at.utcoffset() is None):
            self.dropped += 1
            self.producer_rejected += 1
            return
        try:
            self._queue.put_nowait(_Captured(
                book, market_id, tournament_id, received_at, triggering_revision,
            ))
            self.accepted += 1
        except Full:
            self.dropped += 1
            self.producer_rejected += 1

    def _book_row(self, item: _Captured) -> dict[str, Any]:
        book = item.book
        return {
            "schema": "maker-depth-mirror-v1",
            "event": "SIG_MAKER_REST_RECONCILED_BOOK",
            "run_id": self.run_id,
            "exchange_id": book.exchange_id,
            "market_id": item.market_id,
            "tournament_id": item.tournament_id,
            "received_at": item.received_at.astimezone(UTC).isoformat(),
            "venue_timestamp": None,
            "venue_revision": None,
            "triggering_realtime_revision": item.triggering_revision,
            "source": "MAKER_ALREADY_FETCHED_SIG_REST",
            "source_clock_verified": False,
            "source_sequence_verified": False,
            "trusted_for_paired_paper": False,
            "snapshot_at_reconciliation": True,
            "bids": [{"price": str(x.price), "quantity": str(x.quantity)} for x in book.bids],
            "asks": [{"price": str(x.price), "quantity": str(x.quantity)} for x in book.asks],
        }

    def _write_loop(self) -> None:
        try:
            # Do not follow an existing file or symlink; never mutate source capture.
            fd = os.open(
                self.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                0o600,
            )
            with os.fdopen(fd, "w", encoding="utf-8", buffering=1) as stream:
                written_bytes = 0
                while not self._stop.is_set() or not self._queue.empty():
                    try:
                        event = self._queue.get(timeout=0.05)
                    except Empty:
                        continue
                    if event is _STOP:
                        break
                    assert isinstance(event, _Captured)
                    try:
                        row = self._book_row(event)
                        encoded = json.dumps(row, separators=(",", ":"), sort_keys=True) + "\n"
                        size = len(encoded.encode())
                        if written_bytes + size > self.max_bytes:
                            self.dropped += 1
                            self.writer_lost += 1
                            self._disabled = True
                            break
                        stream.write(encoded)
                        written_bytes += size
                        self.written += 1
                    except Exception:
                        # A dequeued item is no longer in the queue drain below.
                        self.dropped += 1
                        self.writer_lost += 1
                        raise
                    finally:
                        self._queue.task_done()
                # No unbounded flush/fsync on shutdown: writer is daemonised.
        except Exception:
            # Research loss must never interrupt maker's execution path.
            self.errors += 1
            self._disabled = True
        finally:
            self._disabled = True
            # When the output file fails/caps, account for accepted but unwritten
            # queued evidence; never allow an unknown gap to appear complete.
            while True:
                try:
                    self._queue.get_nowait()
                except Empty:
                    break
                else:
                    self.dropped += 1
                    self.writer_lost += 1
                    self._queue.task_done()

    def close(self, *, timeout: float = 0.2) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=max(0.0, timeout))

    def snapshot(self) -> dict[str, int | bool]:
        return {
            "accepted": self.accepted,
            "written": self.written,
            "dropped": self.dropped,
            "producer_attempted": self.accepted + self.producer_rejected,
            "producer_rejected": self.producer_rejected,
            "writer_lost": self.writer_lost,
            "errors": self.errors,
            "disabled": self._disabled,
            "queue_pending": self._queue.qsize(),
        }
