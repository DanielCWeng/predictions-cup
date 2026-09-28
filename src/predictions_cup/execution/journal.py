"""Crash-safe local journal for LIVE execution identity and recovery."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from predictions_cup.execution.models import (
    ExecutionEnvelope,
    ExecutionMode,
    LifecycleState,
    OperationKind,
    lifecycle_transition_allowed,
)


class ExecutionJournal:
    """SQLite/WAL journal. It is deliberately outside the calculation hot path."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(path)
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA synchronous=FULL")
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS execution_envelopes (
                logical_operation_id TEXT PRIMARY KEY,
                idempotency_key TEXT,
                operation_kind TEXT NOT NULL,
                sink_mode TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                payload_sha256 TEXT NOT NULL,
                intent_ids_json TEXT NOT NULL,
                lifecycle_state TEXT NOT NULL,
                created_monotonic_ns INTEGER NOT NULL,
                updated_monotonic_ns INTEGER NOT NULL,
                relationship_constraint TEXT,
                response_json TEXT
            )
            """
        )
        self._connection.commit()

    def close(self) -> None:
        self._connection.close()

    def record_before_dispatch(self, envelope: ExecutionEnvelope) -> None:
        existing = self._connection.execute(
            """
            SELECT idempotency_key, operation_kind, sink_mode, payload_sha256, payload_json
            FROM execution_envelopes WHERE logical_operation_id = ?
            """,
            (envelope.logical_operation_id,),
        ).fetchone()
        if existing is not None:
            expected = (
                envelope.idempotency_key,
                envelope.operation_kind.value,
                envelope.sink_mode.value,
                envelope.payload_sha256,
                envelope.payload_json,
            )
            if tuple(existing) != expected:
                raise ValueError("logical operation identity cannot be reused with changed payload")
            return

        with self._connection:
            self._connection.execute(
                """
                INSERT INTO execution_envelopes (
                    logical_operation_id, idempotency_key, operation_kind, sink_mode,
                    payload_json, payload_sha256, intent_ids_json, lifecycle_state,
                    created_monotonic_ns, updated_monotonic_ns, relationship_constraint
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    envelope.logical_operation_id,
                    envelope.idempotency_key,
                    envelope.operation_kind.value,
                    envelope.sink_mode.value,
                    envelope.payload_json,
                    envelope.payload_sha256,
                    json.dumps(envelope.intent_ids, separators=(",", ":")),
                    envelope.lifecycle_state.value,
                    envelope.created_monotonic_ns,
                    envelope.created_monotonic_ns,
                    envelope.relationship_constraint,
                ),
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
            raise ValueError(
                f"invalid lifecycle transition {current.value} -> {state.value}"
            )
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

    def unresolved(self) -> tuple[ExecutionEnvelope, ...]:
        terminal = (
            LifecycleState.FILLED.value,
            LifecycleState.CANCELLED.value,
            LifecycleState.RECONCILED.value,
            LifecycleState.REJECTED.value,
        )
        rows = self._connection.execute(
            """
            SELECT logical_operation_id, idempotency_key, operation_kind, sink_mode,
                   payload_json, payload_sha256, intent_ids_json, lifecycle_state,
                   created_monotonic_ns, relationship_constraint
            FROM execution_envelopes
            WHERE lifecycle_state NOT IN (?, ?, ?, ?)
            ORDER BY created_monotonic_ns, logical_operation_id
            """,
            terminal,
        ).fetchall()
        result: list[ExecutionEnvelope] = []
        for row in rows:
            intent_ids_raw = json.loads(row[6])
            if not isinstance(intent_ids_raw, list) or not all(
                isinstance(value, str) for value in intent_ids_raw
            ):
                raise RuntimeError("journal contains malformed intent identity list")
            result.append(
                ExecutionEnvelope.persisted(
                    logical_operation_id=str(row[0]),
                    idempotency_key=None if row[1] is None else str(row[1]),
                    operation_kind=OperationKind(str(row[2])),
                    sink_mode=ExecutionMode(str(row[3])),
                    payload_json=str(row[4]),
                    payload_sha256=str(row[5]),
                    intent_ids=tuple(intent_ids_raw),
                    lifecycle_state=LifecycleState(str(row[7])),
                    created_monotonic_ns=int(row[8]),
                    relationship_constraint=None if row[9] is None else str(row[9]),
                )
            )
        return tuple(result)
