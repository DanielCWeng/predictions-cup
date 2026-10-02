"""Crash-safe local journal for LIVE execution identity and recovery."""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from predictions_cup.execution.models import (
    ExecutionAudit,
    ExecutionEnvelope,
    ExecutionMode,
    LifecycleState,
    OperationKind,
    RuntimeOrderIntent,
    lifecycle_transition_allowed,
)

logger = logging.getLogger(__name__)
_FILL_EVENT_TYPES = ("REALTIME_FILL", "AUTHORITATIVE_FILL")


def classify_fill_source(
    *,
    logical_operation_id: str,
    strategy_family: str | None,
    strategy_id: str | None,
) -> str:
    """Classify the two live SIG execution sources for P&L attribution."""
    values = (logical_operation_id, strategy_family or "", strategy_id or "")
    if any("take" in value.lower() for value in values):
        return "TAKE"
    return "MAKE"


def fill_identity_keys(
    *,
    tournament_id: str,
    exchange_id: str | None,
    exchange_order_id: str,
    source_timestamp: str,
    quantity: str,
    price: str | None,
    fill_id: str | None,
) -> tuple[str, ...]:
    """Build stable aliases across SIG's realtime and portfolio fill formats.

    SIG's timestamps differ below millisecond precision, quantities may be
    signed only in portfolio responses, and NO-side prices can use a different
    convention. The content key therefore uses order, millisecond, and absolute
    quantity; an official SIG fill ID adds a stronger alias when available.
    """
    try:
        parsed_at = datetime.fromisoformat(source_timestamp.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("fill timestamp must be ISO formatted") from exc
    if parsed_at.tzinfo is None or parsed_at.utcoffset() is None:
        raise ValueError("fill timestamp must include a timezone")

    def normalise_decimal(value: str | None) -> str | None:
        if value is None:
            return None
        try:
            parsed = Decimal(value)
        except InvalidOperation as exc:
            raise ValueError("fill quantity and price must be decimals") from exc
        if not parsed.is_finite():
            raise ValueError("fill quantity and price must be finite")
        if parsed == 0:
            return "0"
        normalised = format(parsed, "f")
        if "." in normalised:
            normalised = normalised.rstrip("0").rstrip(".")
        return normalised

    quantity_value = normalise_decimal(quantity)
    if quantity_value is None:
        raise ValueError("fill quantity is required")
    normalise_decimal(price)
    occurred_at = parsed_at.astimezone(UTC)
    occurred_at = occurred_at.replace(microsecond=(occurred_at.microsecond // 1_000) * 1_000)
    canonical = json.dumps(
        (
            tournament_id,
            exchange_id,
            exchange_order_id,
            occurred_at.isoformat(timespec="milliseconds"),
            normalise_decimal(str(abs(Decimal(quantity_value)))),
        ),
        separators=(",", ":"),
    )
    content_key = "content:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    keys = [content_key]
    if fill_id is not None and fill_id.strip():
        identity = json.dumps((tournament_id, fill_id), separators=(",", ":"))
        keys.append("sig:" + hashlib.sha256(identity.encode("utf-8")).hexdigest())
    return tuple(keys)


@dataclass(frozen=True, slots=True)
class ExecutionJournalEvent:
    event_id: int
    logical_operation_id: str
    tournament_id: str
    logical_intent_id: str | None
    event_type: str
    observed_monotonic_ns: int
    source_timestamp: str | None
    decision_observation_ns: int | None
    decision_monotonic_ns: int | None
    strategy_family: str | None
    strategy_id: str | None
    signal_value: float | None
    fair_value: float | None
    exchange_id: str | None
    exchange_order_id: str | None
    fill_id: str | None
    quantity: str | None
    price: str | None
    terminal_status: str | None
    detail_json: str | None


class ExecutionJournal:
    """SQLite/WAL journal. It is deliberately outside the calculation hot path."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._operation_listeners: list[Callable[[str], None]] = []
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(path)
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA synchronous=FULL")
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS execution_envelopes (
                logical_operation_id TEXT PRIMARY KEY,
                tournament_id TEXT NOT NULL,
                idempotency_key TEXT,
                operation_kind TEXT NOT NULL,
                sink_mode TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                payload_sha256 TEXT NOT NULL,
                intent_ids_json TEXT NOT NULL,
                lifecycle_state TEXT NOT NULL,
                created_monotonic_ns INTEGER NOT NULL,
                created_at_utc TEXT,
                updated_monotonic_ns INTEGER NOT NULL,
                relationship_constraint TEXT,
                response_json TEXT
            )
            """
        )
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS execution_events (
                event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                logical_operation_id TEXT NOT NULL,
                tournament_id TEXT NOT NULL,
                logical_intent_id TEXT,
                event_type TEXT NOT NULL,
                observed_monotonic_ns INTEGER NOT NULL,
                source_timestamp TEXT,
                decision_observation_ns INTEGER,
                decision_monotonic_ns INTEGER,
                strategy_family TEXT,
                strategy_id TEXT,
                signal_value REAL,
                fair_value REAL,
                exchange_id TEXT,
                exchange_order_id TEXT,
                fill_id TEXT,
                quantity TEXT,
                price TEXT,
                terminal_status TEXT,
                detail_json TEXT,
                FOREIGN KEY(logical_operation_id)
                    REFERENCES execution_envelopes(logical_operation_id)
            )
            """
        )
        self._ensure_execution_envelope_columns()
        self._ensure_execution_event_columns()
        self._ensure_fill_dedupe_table()
        self._backfill_tournament_ids()
        self._connection.execute(
            """
            CREATE INDEX IF NOT EXISTS execution_events_operation_idx
            ON execution_events(logical_operation_id, event_id)
            """
        )
        self._connection.execute(
            """
            CREATE INDEX IF NOT EXISTS execution_events_strategy_idx
            ON execution_events(strategy_id, event_type, logical_operation_id)
            """
        )
        self._connection.execute(
            """
            CREATE INDEX IF NOT EXISTS execution_events_exchange_order_idx
            ON execution_events(exchange_order_id, event_type, event_id)
            """
        )
        self._connection.commit()
        self._seed_fill_dedupe_keys()

    def _ensure_execution_envelope_columns(self) -> None:
        existing = {
            str(row[1])
            for row in self._connection.execute("PRAGMA table_info(execution_envelopes)").fetchall()
        }
        if "tournament_id" not in existing:
            self._connection.execute(
                "ALTER TABLE execution_envelopes ADD COLUMN tournament_id TEXT"
            )
        if "created_at_utc" not in existing:
            self._connection.execute(
                "ALTER TABLE execution_envelopes ADD COLUMN created_at_utc TEXT"
            )

    def _ensure_execution_event_columns(self) -> None:
        existing = {
            str(row[1])
            for row in self._connection.execute("PRAGMA table_info(execution_events)").fetchall()
        }
        additions = (
            ("tournament_id", "TEXT"),
            ("decision_monotonic_ns", "INTEGER"),
            ("strategy_family", "TEXT"),
            ("strategy_id", "TEXT"),
            ("signal_value", "REAL"),
            ("fair_value", "REAL"),
        )
        for name, sql_type in additions:
            if name not in existing:
                self._connection.execute(
                    f"ALTER TABLE execution_events ADD COLUMN {name} {sql_type}"
                )

    def _ensure_fill_dedupe_table(self) -> None:
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS execution_fill_dedupe (
                dedupe_key TEXT PRIMARY KEY,
                event_id INTEGER NOT NULL,
                FOREIGN KEY(event_id) REFERENCES execution_events(event_id)
            )
            """
        )

    def _seed_fill_dedupe_keys(self) -> None:
        rows = self._connection.execute(
            """
            SELECT event_id, tournament_id, exchange_id, exchange_order_id,
                   source_timestamp, quantity, price, fill_id
            FROM execution_events
            WHERE event_type IN ('REALTIME_FILL', 'AUTHORITATIVE_FILL')
              AND exchange_order_id IS NOT NULL
              AND source_timestamp IS NOT NULL
              AND quantity IS NOT NULL
            ORDER BY event_id
            """
        ).fetchall()
        with self._connection:
            for row in rows:
                for key in fill_identity_keys(
                    tournament_id=str(row[1]),
                    exchange_id=None if row[2] is None else str(row[2]),
                    exchange_order_id=str(row[3]),
                    source_timestamp=str(row[4]),
                    quantity=str(row[5]),
                    price=None if row[6] is None else str(row[6]),
                    fill_id=None if row[7] is None else str(row[7]),
                ):
                    self._connection.execute(
                        "INSERT OR IGNORE INTO execution_fill_dedupe(dedupe_key, event_id) "
                        "VALUES (?, ?)",
                        (key, int(row[0])),
                    )

    @staticmethod
    def _extract_tournament_id(payload_json: str) -> str | None:
        raw = json.loads(payload_json)
        if not isinstance(raw, dict):
            return None
        direct = raw.get("tournamentId")
        if isinstance(direct, str) and direct.strip():
            return direct
        for collection_name in ("orders", "legs"):
            collection = raw.get(collection_name)
            if not isinstance(collection, list) or not collection:
                continue
            values = {
                item.get("tournamentId")
                for item in collection
                if isinstance(item, dict)
                and isinstance(item.get("tournamentId"), str)
                and str(item.get("tournamentId")).strip()
            }
            if len(values) == 1:
                value = next(iter(values))
                if isinstance(value, str):
                    return value
        return None

    def _backfill_tournament_ids(self) -> None:
        rows = self._connection.execute(
            """
            SELECT logical_operation_id, payload_json, tournament_id
            FROM execution_envelopes
            """
        ).fetchall()
        for logical_operation_id, payload_json, tournament_id in rows:
            if tournament_id is not None and str(tournament_id).strip():
                continue
            derived = self._extract_tournament_id(str(payload_json))
            if derived is None:
                continue
            self._connection.execute(
                """
                UPDATE execution_envelopes
                SET tournament_id = ?
                WHERE logical_operation_id = ?
                """,
                (derived, str(logical_operation_id)),
            )
        self._connection.execute(
            """
            UPDATE execution_events
            SET tournament_id = (
                SELECT execution_envelopes.tournament_id
                FROM execution_envelopes
                WHERE execution_envelopes.logical_operation_id =
                      execution_events.logical_operation_id
            )
            WHERE tournament_id IS NULL
            """
        )

    @staticmethod
    def _required_tournament_id(value: object) -> str:
        if value is None or not str(value).strip():
            raise RuntimeError("execution journal row is missing tournament identity")
        return str(value)

    def close(self) -> None:
        self._connection.close()

    def add_operation_listener(self, listener: Callable[[str], None]) -> None:
        """Subscribe to committed journal updates without affecting dispatch."""
        self._operation_listeners.append(listener)

    def latest_event_id(self) -> int:
        row = self._connection.execute(
            "SELECT COALESCE(MAX(event_id), 0) FROM execution_events"
        ).fetchone()
        return 0 if row is None else int(row[0])

    def _notify_operation_listeners(self, logical_operation_id: str) -> None:
        for listener in tuple(self._operation_listeners):
            try:
                listener(logical_operation_id)
            except Exception:
                # A projection is never allowed to turn an accepted venue order
                # into an execution exception. It records its own fail-closed
                # discrepancy and the ordinary journal remains authoritative.
                logger.exception(
                    "execution journal projection listener failed op=%s",
                    logical_operation_id,
                )

    def record_before_dispatch(
        self,
        envelope: ExecutionEnvelope,
        intents: tuple[RuntimeOrderIntent, ...] = (),
        audit: ExecutionAudit | None = None,
        submitted_monotonic_ns: int | None = None,
    ) -> None:
        submission_ns = (
            envelope.created_monotonic_ns
            if submitted_monotonic_ns is None
            else submitted_monotonic_ns
        )
        try:
            with self._connection:
                self._connection.execute(
                    """
                    INSERT INTO execution_envelopes (
                        logical_operation_id, tournament_id, idempotency_key,
                        operation_kind, sink_mode, payload_json, payload_sha256,
                        intent_ids_json, lifecycle_state, created_monotonic_ns,
                        created_at_utc, updated_monotonic_ns, relationship_constraint
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        envelope.logical_operation_id,
                        envelope.tournament_id,
                        envelope.idempotency_key,
                        envelope.operation_kind.value,
                        envelope.sink_mode.value,
                        envelope.payload_json,
                        envelope.payload_sha256,
                        json.dumps(envelope.intent_ids, separators=(",", ":")),
                        envelope.lifecycle_state.value,
                        envelope.created_monotonic_ns,
                        datetime.now(UTC).isoformat(),
                        envelope.created_monotonic_ns,
                        envelope.relationship_constraint,
                    ),
                )
                if intents:
                    for intent in intents:
                        self._insert_event(
                            logical_operation_id=envelope.logical_operation_id,
                            tournament_id=envelope.tournament_id,
                            logical_intent_id=intent.intent_id,
                            event_type="SUBMISSION",
                            observed_monotonic_ns=submission_ns,
                            decision_observation_ns=intent.decision_observation_ns,
                            decision_monotonic_ns=(
                                None if audit is None else audit.decision_monotonic_ns
                            ),
                            strategy_family=(None if audit is None else audit.strategy_family),
                            strategy_id=(
                                intent.strategy_id if audit is None else audit.strategy_id
                            ),
                            signal_value=None if audit is None else audit.signal_value,
                            fair_value=None if audit is None else audit.fair_value,
                            exchange_id=intent.exchange_id,
                            quantity=str(intent.quantity),
                            detail_json=json.dumps(
                                {
                                    "action": intent.action.value,
                                    "outcome_side": intent.outcome_side.value,
                                    "market_id": intent.market_id,
                                },
                                separators=(",", ":"),
                            ),
                        )
                else:
                    self._insert_event(
                        logical_operation_id=envelope.logical_operation_id,
                        tournament_id=envelope.tournament_id,
                        logical_intent_id=None,
                        event_type="SUBMISSION",
                        observed_monotonic_ns=submission_ns,
                        decision_observation_ns=(
                            None if audit is None else audit.decision_observation_ns
                        ),
                        decision_monotonic_ns=(
                            None if audit is None else audit.decision_monotonic_ns
                        ),
                        strategy_family=(None if audit is None else audit.strategy_family),
                        strategy_id=None if audit is None else audit.strategy_id,
                        signal_value=None if audit is None else audit.signal_value,
                        fair_value=None if audit is None else audit.fair_value,
                    )
            self._notify_operation_listeners(envelope.logical_operation_id)
            return
        except sqlite3.IntegrityError:
            # The hot path optimistically INSERTs. Only an idempotent retry pays
            # the read needed to prove the logical operation is byte-identical.
            existing = self._connection.execute(
                """
                SELECT tournament_id, idempotency_key, operation_kind, sink_mode,
                       payload_sha256, payload_json
                FROM execution_envelopes WHERE logical_operation_id = ?
                """,
                (envelope.logical_operation_id,),
            ).fetchone()
            if existing is None:
                raise

        expected = (
            envelope.tournament_id,
            envelope.idempotency_key,
            envelope.operation_kind.value,
            envelope.sink_mode.value,
            envelope.payload_sha256,
            envelope.payload_json,
        )
        if tuple(existing) != expected:
            raise ValueError("logical operation identity cannot be reused with changed payload")
        if submitted_monotonic_ns is not None:
            self.record_event(
                logical_operation_id=envelope.logical_operation_id,
                tournament_id=envelope.tournament_id,
                event_type="RESUBMISSION",
                observed_monotonic_ns=submitted_monotonic_ns,
            )

    def operation_created_at(self, logical_operation_id: str) -> datetime | None:
        row = self._connection.execute(
            """
            SELECT created_at_utc
            FROM execution_envelopes
            WHERE logical_operation_id = ?
            """,
            (logical_operation_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"unknown logical operation: {logical_operation_id}")
        if row[0] is None:
            return None
        value = datetime.fromisoformat(str(row[0]))
        if value.tzinfo is None or value.utcoffset() is None:
            raise RuntimeError("execution journal created_at_utc must be timezone-aware")
        return value.astimezone(UTC)

    def record_event(
        self,
        *,
        logical_operation_id: str,
        event_type: str,
        observed_monotonic_ns: int,
        tournament_id: str | None = None,
        logical_intent_id: str | None = None,
        source_timestamp: str | None = None,
        decision_observation_ns: int | None = None,
        decision_monotonic_ns: int | None = None,
        strategy_family: str | None = None,
        strategy_id: str | None = None,
        signal_value: float | None = None,
        fair_value: float | None = None,
        exchange_id: str | None = None,
        exchange_order_id: str | None = None,
        fill_id: str | None = None,
        quantity: str | None = None,
        price: str | None = None,
        terminal_status: str | None = None,
        detail_json: str | None = None,
    ) -> None:
        if not event_type.strip():
            raise ValueError("event_type must not be blank")
        if observed_monotonic_ns < 0:
            raise ValueError("observed_monotonic_ns must be non-negative")
        with self._connection:
            self._insert_event(
                logical_operation_id=logical_operation_id,
                tournament_id=tournament_id,
                logical_intent_id=logical_intent_id,
                event_type=event_type,
                observed_monotonic_ns=observed_monotonic_ns,
                source_timestamp=source_timestamp,
                decision_observation_ns=decision_observation_ns,
                decision_monotonic_ns=decision_monotonic_ns,
                strategy_family=strategy_family,
                strategy_id=strategy_id,
                signal_value=signal_value,
                fair_value=fair_value,
                exchange_id=exchange_id,
                exchange_order_id=exchange_order_id,
                fill_id=fill_id,
                quantity=quantity,
                price=price,
                terminal_status=terminal_status,
                detail_json=detail_json,
            )
        self._notify_operation_listeners(logical_operation_id)

    def record_fill_event_once(
        self,
        *,
        logical_operation_id: str,
        logical_intent_id: str | None,
        event_type: str,
        observed_monotonic_ns: int,
        source_timestamp: str,
        exchange_id: str | None,
        exchange_order_id: str,
        quantity: str,
        price: str | None,
        fill_id: str | None = None,
        strategy_family: str | None = None,
        strategy_id: str | None = None,
        detail_json: str | None = None,
    ) -> bool:
        """Persist one fill across repeat realtime batches and later REST backfill."""
        if event_type not in _FILL_EVENT_TYPES:
            raise ValueError("fill event type must be REALTIME_FILL or AUTHORITATIVE_FILL")
        if not exchange_order_id.strip():
            raise ValueError("fill exchange_order_id must not be blank")
        if observed_monotonic_ns < 0:
            raise ValueError("observed_monotonic_ns must be non-negative")
        envelope = self._connection.execute(
            "SELECT tournament_id FROM execution_envelopes "
            "WHERE logical_operation_id = ?",
            (logical_operation_id,),
        ).fetchone()
        if envelope is None:
            raise KeyError(f"unknown logical operation: {logical_operation_id}")
        tournament_id = self._required_tournament_id(envelope[0])
        keys = fill_identity_keys(
            tournament_id=tournament_id,
            exchange_id=exchange_id,
            exchange_order_id=exchange_order_id,
            source_timestamp=source_timestamp,
            quantity=quantity,
            price=price,
            fill_id=fill_id,
        )
        placeholders = ",".join("?" for _ in keys)
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            existing = self._connection.execute(
                f"SELECT 1 FROM execution_fill_dedupe WHERE dedupe_key IN ({placeholders}) LIMIT 1",
                keys,
            ).fetchone()
            if existing is not None:
                self._connection.rollback()
                return False
            event_id = self._insert_event(
                logical_operation_id=logical_operation_id,
                tournament_id=tournament_id,
                logical_intent_id=logical_intent_id,
                event_type=event_type,
                observed_monotonic_ns=observed_monotonic_ns,
                source_timestamp=source_timestamp,
                strategy_family=strategy_family,
                strategy_id=strategy_id,
                exchange_id=exchange_id,
                exchange_order_id=exchange_order_id,
                fill_id=fill_id,
                quantity=quantity,
                price=price,
                detail_json=detail_json,
            )
            self._connection.executemany(
                "INSERT INTO execution_fill_dedupe(dedupe_key, event_id) VALUES (?, ?)",
                ((key, event_id) for key in keys),
            )
            self._connection.commit()
        except BaseException:
            self._connection.rollback()
            raise
        self._notify_operation_listeners(logical_operation_id)
        return True

    def _insert_event(
        self,
        *,
        logical_operation_id: str,
        event_type: str,
        observed_monotonic_ns: int,
        tournament_id: str | None = None,
        logical_intent_id: str | None = None,
        source_timestamp: str | None = None,
        decision_observation_ns: int | None = None,
        decision_monotonic_ns: int | None = None,
        strategy_family: str | None = None,
        strategy_id: str | None = None,
        signal_value: float | None = None,
        fair_value: float | None = None,
        exchange_id: str | None = None,
        exchange_order_id: str | None = None,
        fill_id: str | None = None,
        quantity: str | None = None,
        price: str | None = None,
        terminal_status: str | None = None,
        detail_json: str | None = None,
    ) -> int:
        if tournament_id is None:
            row = self._connection.execute(
                """
                SELECT tournament_id
                FROM execution_envelopes
                WHERE logical_operation_id = ?
                """,
                (logical_operation_id,),
            ).fetchone()
            tournament_id = None if row is None else self._required_tournament_id(row[0])
        tournament_id = self._required_tournament_id(tournament_id)
        cursor = self._connection.execute(
            """
            INSERT INTO execution_events (
                logical_operation_id, tournament_id, logical_intent_id, event_type,
                observed_monotonic_ns, source_timestamp, decision_observation_ns,
                decision_monotonic_ns, strategy_family, strategy_id, signal_value, fair_value,
                exchange_id, exchange_order_id, fill_id, quantity, price,
                terminal_status, detail_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                logical_operation_id,
                tournament_id,
                logical_intent_id,
                event_type,
                observed_monotonic_ns,
                source_timestamp,
                decision_observation_ns,
                decision_monotonic_ns,
                strategy_family,
                strategy_id,
                signal_value,
                fair_value,
                exchange_id,
                exchange_order_id,
                fill_id,
                quantity,
                price,
                terminal_status,
                detail_json,
            ),
        )
        if cursor.lastrowid is None:
            raise RuntimeError("execution event insert did not return an event id")
        return cursor.lastrowid

    def events(self, logical_operation_id: str) -> tuple[ExecutionJournalEvent, ...]:
        rows = self._connection.execute(
            """
            SELECT event_id, logical_operation_id, tournament_id,
                   logical_intent_id, event_type, observed_monotonic_ns,
                   source_timestamp, decision_observation_ns,
                   decision_monotonic_ns, strategy_family, strategy_id, signal_value, fair_value,
                   exchange_id, exchange_order_id, fill_id, quantity, price,
                   terminal_status, detail_json
            FROM execution_events
            WHERE logical_operation_id = ?
            ORDER BY event_id
            """,
            (logical_operation_id,),
        ).fetchall()
        return tuple(
            ExecutionJournalEvent(
                event_id=int(row[0]),
                logical_operation_id=str(row[1]),
                tournament_id=self._required_tournament_id(row[2]),
                logical_intent_id=None if row[3] is None else str(row[3]),
                event_type=str(row[4]),
                observed_monotonic_ns=int(row[5]),
                source_timestamp=None if row[6] is None else str(row[6]),
                decision_observation_ns=(None if row[7] is None else int(row[7])),
                decision_monotonic_ns=(None if row[8] is None else int(row[8])),
                strategy_family=None if row[9] is None else str(row[9]),
                strategy_id=None if row[10] is None else str(row[10]),
                signal_value=None if row[11] is None else float(row[11]),
                fair_value=None if row[12] is None else float(row[12]),
                exchange_id=None if row[13] is None else str(row[13]),
                exchange_order_id=None if row[14] is None else str(row[14]),
                fill_id=None if row[15] is None else str(row[15]),
                quantity=None if row[16] is None else str(row[16]),
                price=None if row[17] is None else str(row[17]),
                terminal_status=None if row[18] is None else str(row[18]),
                detail_json=None if row[19] is None else str(row[19]),
            )
            for row in rows
        )

    def mark_state(
        self,
        logical_operation_id: str,
        state: LifecycleState,
        observed_monotonic_ns: int,
        *,
        response_json: str | None = None,
    ) -> None:
        row = self._connection.execute(
            """
            SELECT lifecycle_state FROM execution_envelopes
            WHERE logical_operation_id = ?
            """,
            (logical_operation_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"unknown logical operation: {logical_operation_id}")
        current = LifecycleState(str(row[0]))
        if not lifecycle_transition_allowed(current, state):
            raise ValueError(f"invalid lifecycle transition {current.value} -> {state.value}")
        with self._connection:
            self._connection.execute(
                """
                UPDATE execution_envelopes
                SET lifecycle_state = ?, updated_monotonic_ns = ?,
                    response_json = COALESCE(?, response_json)
                WHERE logical_operation_id = ?
                """,
                (
                    state.value,
                    observed_monotonic_ns,
                    response_json,
                    logical_operation_id,
                ),
            )
        self._notify_operation_listeners(logical_operation_id)

    def placement_identity_for_exchange_order_id(
        self,
        exchange_order_id: str,
    ) -> tuple[str, str | None] | None:
        """Resolve venue order identity back to its original placement/intent."""
        attribution = self.placement_attribution_for_exchange_order_id(exchange_order_id)
        return None if attribution is None else (attribution[0], attribution[1])

    def placement_attribution_for_exchange_order_id(
        self,
        exchange_order_id: str,
    ) -> tuple[str, str | None, str | None, str | None] | None:
        """Resolve a venue order to operation, intent, and strategy attribution."""
        if not exchange_order_id.strip():
            raise ValueError("exchange_order_id must not be blank")
        rows = self._connection.execute(
            """
            SELECT ack.logical_operation_id, ack.logical_intent_id,
                   submission.strategy_family, submission.strategy_id
            FROM execution_events AS ack
            JOIN execution_envelopes AS envelope
              ON envelope.logical_operation_id = ack.logical_operation_id
            LEFT JOIN execution_events AS submission
              ON submission.logical_operation_id = ack.logical_operation_id
             AND submission.logical_intent_id = ack.logical_intent_id
             AND submission.event_type = 'SUBMISSION'
            WHERE ack.exchange_order_id = ?
              AND ack.event_type = 'ACK'
              AND envelope.operation_kind IN (?, ?, ?)
            ORDER BY ack.event_id
            """,
            (
                exchange_order_id,
                OperationKind.SINGLE_PLACEMENT.value,
                OperationKind.BEST_EFFORT_BATCH.value,
                OperationKind.ATOMIC_MULTI_LEG.value,
            ),
        ).fetchall()
        identities = tuple(
            dict.fromkeys(
                (
                    str(row[0]),
                    None if row[1] is None else str(row[1]),
                    None if row[2] is None else str(row[2]),
                    None if row[3] is None else str(row[3]),
                )
                for row in rows
            )
        )
        if not identities:
            return None
        if len(identities) != 1:
            raise RuntimeError("venue order identity maps to multiple placement operations")
        return identities[0]

    def logical_operation_for_exchange_order_id(
        self,
        exchange_order_id: str,
    ) -> str | None:
        placement = self.placement_identity_for_exchange_order_id(exchange_order_id)
        if placement is not None:
            return placement[0]
        row = self._connection.execute(
            """
            SELECT logical_operation_id
            FROM execution_events
            WHERE exchange_order_id = ?
            ORDER BY event_id DESC
            LIMIT 1
            """,
            (exchange_order_id,),
        ).fetchone()
        return None if row is None else str(row[0])

    def envelopes_for_strategy(
        self,
        strategy_id: str,
    ) -> tuple[ExecutionEnvelope, ...]:
        """Return durable envelopes attributed to one strategy submission."""
        if not strategy_id.strip():
            raise ValueError("strategy_id must not be blank")
        rows = self._connection.execute(
            """
            SELECT DISTINCT
                   envelope.logical_operation_id,
                   envelope.tournament_id,
                   envelope.idempotency_key,
                   envelope.operation_kind,
                   envelope.sink_mode,
                   envelope.payload_json,
                   envelope.payload_sha256,
                   envelope.intent_ids_json,
                   envelope.lifecycle_state,
                   envelope.created_monotonic_ns,
                   envelope.relationship_constraint
            FROM execution_envelopes AS envelope
            JOIN execution_events AS event
              ON event.logical_operation_id = envelope.logical_operation_id
            WHERE event.event_type = 'SUBMISSION'
              AND event.strategy_id = ?
            ORDER BY envelope.created_monotonic_ns,
                     envelope.logical_operation_id
            """,
            (strategy_id,),
        ).fetchall()
        return self._decode_envelopes(rows)

    def envelopes(self) -> tuple[ExecutionEnvelope, ...]:
        """Return all durable execution envelopes, including terminal history."""
        rows = self._connection.execute(
            """
            SELECT logical_operation_id, tournament_id, idempotency_key,
                   operation_kind, sink_mode, payload_json, payload_sha256,
                   intent_ids_json, lifecycle_state, created_monotonic_ns,
                   relationship_constraint
            FROM execution_envelopes
            ORDER BY created_monotonic_ns, logical_operation_id
            """
        ).fetchall()
        return self._decode_envelopes(rows)

    def unresolved(self) -> tuple[ExecutionEnvelope, ...]:
        terminal = (
            LifecycleState.FILLED.value,
            LifecycleState.CANCELLED.value,
            LifecycleState.RECONCILED.value,
            LifecycleState.REJECTED.value,
        )
        rows = self._connection.execute(
            """
            SELECT logical_operation_id, tournament_id, idempotency_key,
                   operation_kind, sink_mode, payload_json, payload_sha256,
                   intent_ids_json, lifecycle_state, created_monotonic_ns,
                   relationship_constraint
            FROM execution_envelopes
            WHERE lifecycle_state NOT IN (?, ?, ?, ?)
            ORDER BY created_monotonic_ns, logical_operation_id
            """,
            terminal,
        ).fetchall()
        return self._decode_envelopes(rows)

    def _decode_envelopes(
        self,
        rows: list[tuple[object, ...]],
    ) -> tuple[ExecutionEnvelope, ...]:
        result: list[ExecutionEnvelope] = []
        for row in rows:
            intent_ids_raw = json.loads(str(row[7]))
            if not isinstance(intent_ids_raw, list) or not all(
                isinstance(value, str) for value in intent_ids_raw
            ):
                raise RuntimeError("journal contains malformed intent identity list")
            result.append(
                ExecutionEnvelope.persisted(
                    logical_operation_id=str(row[0]),
                    tournament_id=self._required_tournament_id(row[1]),
                    idempotency_key=None if row[2] is None else str(row[2]),
                    operation_kind=OperationKind(str(row[3])),
                    sink_mode=ExecutionMode(str(row[4])),
                    payload_json=str(row[5]),
                    payload_sha256=str(row[6]),
                    intent_ids=tuple(intent_ids_raw),
                    lifecycle_state=LifecycleState(str(row[8])),
                    created_monotonic_ns=int(str(row[9])),
                    relationship_constraint=(None if row[10] is None else str(row[10])),
                )
            )
        return tuple(result)
