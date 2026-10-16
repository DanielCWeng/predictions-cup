"""Read-only census of SIG versioned books embedded in retained raw_events Parquet.

This is a source-presence audit, NOT a causal depth, fill, or PnL certificate.
It does not use API credentials, networking, execution clients, or active SQLite.
"""
from __future__ import annotations

import argparse
import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
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
    rejected_validation_rows: int = 0
    missing_epoch_rows: int = 0
    wrong_or_unknown_topic_rows: int = 0
    non_market_batch_rows: int = 0
    tournament_filter_active: bool = False
    batches_with_books: int = 0
    resync_required_batches_with_books: int = 0
    missing_delivery_batches_with_books: int = 0
    duplicate_delivery_batches_with_books_within_shard: int = 0
    revision_gap_batches_with_books_within_shard: int = 0
    unscoped_delivery_continuity_batches_with_books: int = 0
    versioned_books: int = 0
    unversioned_or_malformed_books: int = 0
    two_sided_versioned_books: int = 0
    # The immutable writer uses random UUID shard names, so path order is
    # NOT event order across shards. Only consecutive observations retained
    # in a single FIFO Parquet shard support sequence-order diagnostics.
    duplicate_versions_within_shard: int = 0
    backwards_versions_within_shard: int = 0
    _versions: dict[tuple[str, int, str], int] = field(default_factory=dict)
    _delivery_revisions: dict[tuple[str, int, str], int] = field(
        default_factory=dict
    )
    _exchanges: set[str] = field(default_factory=set)
    _sessions: set[str] = field(default_factory=set)
    _first_native_asof: datetime | None = None
    _last_native_asof: datetime | None = None

    def begin_shard(self) -> None:
        """Reset ordering evidence; filename UUIDs cannot order shard events."""
        self._versions.clear()
        self._delivery_revisions.clear()

    def consume(
        self, raw: object, *, session: object, epoch: object,
        validation_error: object, topic: object,
        source: object, event_type: object,
        expected_tournament_id: str | None,
    ) -> None:
        self.raw_rows += 1
        self.tournament_filter_active = expected_tournament_id is not None
        if source != "SIG_REALTIME_DECODED" or event_type != "MARKET_BATCH":
            self.non_market_batch_rows += 1
            return
        # SIG's older CAPTURE-001 book-only rows often have tournament_id
        # NULL. The subscription topic, not an optional payload label, is
        # the scoped identity for each native versioned-book batch.
        if expected_tournament_id is not None:
            expected_prefix = f"tournament:{expected_tournament_id}:market:"
            if not (
                isinstance(topic, str)
                and (
                    topic == f"tournament:{expected_tournament_id}"
                    or topic.startswith(expected_prefix)
                    and len(topic) > len(expected_prefix)
                )
            ):
                self.wrong_or_unknown_topic_rows += 1
                return
        # Scope the census to messages passing initial DTO/identity parsing,
        # not arbitrary rejected JSON. This marker is recorded BEFORE later
        # state changes or REST reconciliation: a null validation_error does
        # NOT prove downstream source or executable-book acceptance.
        if validation_error is not None:
            self.rejected_validation_rows += 1
            return
        if not isinstance(raw, str):
            self.malformed_json_rows += 1
            return
        try:
            payload = json.loads(raw)
        except (ValueError, TypeError):
            self.malformed_json_rows += 1
            return
        epoch_identity_valid = (
            isinstance(session, str) and bool(session.strip())
            and isinstance(epoch, int) and not isinstance(epoch, bool)
            and epoch >= 0
        )
        if not epoch_identity_valid:
            self.missing_epoch_rows += 1
        session_key = session if isinstance(session, str) else "UNKNOWN_SESSION"
        epoch_key = epoch if isinstance(epoch, int) and not isinstance(epoch, bool) else -1
        if epoch_identity_valid:
            self._sessions.add(session_key)
        for batch in _batches(payload):
            books = batch.get("books")
            has_books = isinstance(books, list) and bool(books)

            # Revision continuity belongs to the *topic*, not to books[].
            # Trade-only/settlement-only batches must advance the watermark:
            # otherwise a later book carrying a valid revision looks like a
            # fake gap. Within a FIFO shard we can detect local anomalies,
            # but cannot reconstruct acceptance across unsortable shards.
            delivery = batch.get("delivery")
            if not isinstance(delivery, dict):
                if has_books:
                    self.missing_delivery_batches_with_books += 1
            else:
                revision = delivery.get("revision")
                previous_revision = delivery.get("previousRevision")
                if (
                    type(revision) is not int or revision < 0
                    or type(previous_revision) is not int
                    or previous_revision < 0
                ):
                    if has_books:
                        self.missing_delivery_batches_with_books += 1
                elif (
                    epoch_identity_valid
                    and isinstance(topic, str)
                    and ":market:" in topic
                ):
                    delivery_key = (session_key, epoch_key, topic)
                    old_revision = self._delivery_revisions.get(delivery_key)
                    if has_books and old_revision is not None:
                        if revision <= old_revision:
                            self.duplicate_delivery_batches_with_books_within_shard += 1
                        elif previous_revision > old_revision:
                            self.revision_gap_batches_with_books_within_shard += 1
                    if old_revision is None or revision > old_revision:
                        self._delivery_revisions[delivery_key] = revision
                elif has_books:
                    self.unscoped_delivery_continuity_batches_with_books += 1

            if not has_books:
                continue
            self.batches_with_books += 1
            # The recorder persists raw messages before the state engine
            # applies duplicate/gap/resyncRequired rejection or recovery.
            # These figures count raw appearances, not accepted depth.
            if batch.get("resyncRequired") is True:
                self.resync_required_batches_with_books += 1
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
                source_time = datetime.fromisoformat(
                    version["at"].replace("Z", "+00:00")
                ).astimezone(UTC)
                if self._first_native_asof is None or source_time < self._first_native_asof:
                    self._first_native_asof = source_time
                if self._last_native_asof is None or source_time > self._last_native_asof:
                    self._last_native_asof = source_time
                if (
                    isinstance(item.get("bids"), list)
                    and bool(item["bids"])
                    and isinstance(item.get("asks"), list)
                    and bool(item["asks"])
                ):
                    # Descriptive presence only; levels are NOT executable certified.
                    self.two_sided_versioned_books += 1
                # Sequence anomalies require an identified socket epoch;
                # missing session metadata must not create false continuity.
                if epoch_identity_valid:
                    key = (session_key, epoch_key, exchange_id)
                    sequence = version["sequence"]
                    prior = self._versions.get(key)
                    if prior is not None:
                        if sequence == prior:
                            self.duplicate_versions_within_shard += 1
                        elif sequence < prior:
                            self.backwards_versions_within_shard += 1
                    if prior is None or sequence > prior:
                        self._versions[key] = sequence

    def summary(self) -> dict[str, object]:
        return {
            "status": "SOURCE_PRESENCE_ONLY_NOT_EXECUTION_CERTIFIED",
            "files_read": self.files_read,
            "raw_rows": self.raw_rows,
            "malformed_json_rows": self.malformed_json_rows,
            "rejected_validation_rows": self.rejected_validation_rows,
            "missing_epoch_rows": self.missing_epoch_rows,
            "wrong_or_unknown_topic_rows": self.wrong_or_unknown_topic_rows,
            "non_market_batch_rows": self.non_market_batch_rows,
            "tournament_scope": (
                "SCOPED_BY_SUBSCRIPTION_TOPIC"
                if self.tournament_filter_active else "UNVERIFIED_MIXED_POSSIBLE"
            ),
            "batches_with_books": self.batches_with_books,
            "resync_required_batches_with_books": (
                self.resync_required_batches_with_books
            ),
            "missing_delivery_batches_with_books": (
                self.missing_delivery_batches_with_books
            ),
            "duplicate_delivery_batches_with_books_within_shard": (
                self.duplicate_delivery_batches_with_books_within_shard
            ),
            "revision_gap_batches_with_books_within_shard": (
                self.revision_gap_batches_with_books_within_shard
            ),
            "unscoped_delivery_continuity_batches_with_books": (
                self.unscoped_delivery_continuity_batches_with_books
            ),
            "batch_acceptance": "NOT_RECONSTRUCTED_FROM_RAW_MESSAGES",
            "versioned_books": self.versioned_books,
            "unversioned_or_malformed_books": self.unversioned_or_malformed_books,
            "two_sided_versioned_books": self.two_sided_versioned_books,
            "unique_exchanges": len(self._exchanges),
            "observed_sessions": len(self._sessions),
            "earliest_native_asof": (
                self._first_native_asof.isoformat()
                if self._first_native_asof is not None else None
            ),
            "latest_native_asof": (
                self._last_native_asof.isoformat()
                if self._last_native_asof is not None else None
            ),
            "time_range_does_not_prove_dense_coverage": True,
            "duplicate_versions_within_shard": self.duplicate_versions_within_shard,
            "backwards_versions_within_shard": self.backwards_versions_within_shard,
            "cross_shard_sequence_continuity": "UNKNOWN_UUID_NAMED_SHARDS",
            "coverage_completeness": "UNKNOWN",
            "post_parse_state_acceptance": "UNVERIFIED",
            "source_clock_causality": "UNVERIFIED",
            "paired_executable_opportunities": None,
            "fill_probability": None,
            "after_cost_pnl": None,
        }


def scan_raw_events(
    research_root: Path, *, max_files: int = 100_000,
    expected_tournament_id: str | None = None,
) -> RawBookAudit:
    """Stream existing immutable raw shards; fail on absent/incomplete input paths."""
    if expected_tournament_id is not None and (
        not expected_tournament_id.strip() or ":" in expected_tournament_id
    ):
        raise ValueError("expected tournament ID must be nonblank without colons")
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
        # Each Parquet writer shard retains FIFO row order, but shards sharing
        # a minute have random filenames. Never compare their version highs
        # unless an independently proven event sort has been performed.
        audit.begin_shard()
        fields = set(parquet.schema_arrow.names)
        required = {
            "raw_json", "session_id", "connection_epoch", "validation_error",
            "topic", "source", "event_type",
        }
        if not required.issubset(fields):
            raise ValueError("raw shard lacks mandatory CAPTURE-001 audit columns")
        columns = [
            "raw_json", "session_id", "connection_epoch", "validation_error",
            "topic", "source", "event_type",
        ]
        for batch in parquet.iter_batches(batch_size=512, columns=columns):
            for row in batch.to_pylist():
                audit.consume(
                    row["raw_json"],
                    session=row.get("session_id"),
                    epoch=row.get("connection_epoch"),
                    validation_error=row.get("validation_error"),
                    topic=row.get("topic"),
                    source=row.get("source"),
                    event_type=row.get("event_type"),
                    expected_tournament_id=expected_tournament_id,
                )
        audit.files_read += 1
    return audit


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--research-root", type=Path, required=True)
    parser.add_argument("--max-files", type=int, default=100_000)
    parser.add_argument(
        "--tournament-id",
        help="Limit source presence to exact tournament topic; without this, "
             "output is explicitly unscoped and not attributable to the cup.",
    )
    args = parser.parse_args()
    if args.max_files < 1:
        parser.error("max-files must be positive")
    try:
        result = scan_raw_events(
            args.research_root, max_files=args.max_files,
            expected_tournament_id=args.tournament_id,
        )
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    print(json.dumps(result.summary(), sort_keys=True))


if __name__ == "__main__":
    main()
