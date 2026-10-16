"""Inert, durable reduction-reservation prototype. NEVER a LIVE order authority.

An isolated SQLite file atomically serialises per-exchange allocations across
processes. It NEVER calls an execution client, the maker or RISK-002. The
external authority/provenance of SignedInventoryEvidence is NOT established by
this module; no production order sender is wired to this store.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from predictions_cup.research.signed_reduction_screen import (
    SignedInventoryEvidence,
    screen_signed_reduction,
)


class ReservationDenied(ValueError):
    """Untrusted, conflicting or potentially exposure-increasing transition."""


@dataclass(frozen=True, slots=True)
class ReductionReservation:
    intent_id: str
    exchange_id: str
    signed_delta: int
    quantity: int
    cumulative_filled: int
    state: str

    @property
    def execution_authorised(self) -> bool:
        return False


def _ledger_digest(ledger: Mapping[str, int]) -> str:
    encoded = json.dumps(dict(sorted(ledger.items())), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _validate_evidence(evidence: SignedInventoryEvidence, *, cap: int, age: float) -> str:
    """Validate mathematical ledger, not authenticity of its upstream source."""
    if not evidence.signed_by_exchange:
        raise ReservationDenied("empty_ledger")
    exchange, position = next(iter(evidence.signed_by_exchange.items()))
    if (
        not isinstance(exchange, str)
        or not exchange
        or not isinstance(position, int)
        or isinstance(position, bool)
        or not position
    ):
        raise ReservationDenied("invalid_first_position")
    side = "BUY" if position < 0 else "SELL"
    screened = screen_signed_reduction(
        evidence, exchange_id=exchange, outcome_side="YES", action=side,
        quantity=1, gross_limit=cap, max_account_age_seconds=age,
    )
    if not screened.mathematically_reducing:
        raise ReservationDenied(screened.reason)
    if any(not isinstance(k, str) or not k.strip() for k in evidence.signed_by_exchange):
        raise ReservationDenied("invalid_exchange_identity")
    return _ledger_digest(evidence.signed_by_exchange)


class OfflineReductionReservations:
    """Durable simulation of one-account one-epoch reduction-only serialisation.

    All mutations use BEGIN IMMEDIATE + synchronous FULL SQLite transactions.
    Cancellation/ACK/timeout/partial fills never release reserved capacity.
    Release requires terminal order evidence PLUS a new complete authenticated
    account reconciliation exactly matching all cumulative terminal fills.
    The primitive is research-only: no API, authorisation or LIVE integration.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        with self._connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS epochs (
                slot INTEGER PRIMARY KEY CHECK (slot=1), epoch TEXT NOT NULL,
                digest TEXT NOT NULL, ledger TEXT NOT NULL,
                gross INTEGER NOT NULL, net INTEGER NOT NULL)""")
            db.execute("""CREATE TABLE IF NOT EXISTS intents (
                intent_id TEXT PRIMARY KEY, epoch TEXT NOT NULL,
                exchange_id TEXT NOT NULL, signed_delta INTEGER NOT NULL,
                quantity INTEGER NOT NULL, cumulative INTEGER NOT NULL DEFAULT 0,
                state TEXT NOT NULL)""")
            db.execute("""CREATE TABLE IF NOT EXISTS events (
                event_id TEXT PRIMARY KEY, intent_id TEXT NOT NULL,
                kind TEXT NOT NULL, cumulative INTEGER NOT NULL)""")

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, isolation_level=None, timeout=10)
        db.execute("PRAGMA busy_timeout=10000")
        db.execute("PRAGMA synchronous=FULL")
        return db

    @staticmethod
    def _transaction(db: sqlite3.Connection) -> None:
        db.execute("BEGIN IMMEDIATE")

    @staticmethod
    def _epoch(db: sqlite3.Connection) -> tuple[str, str, dict[str, int]]:
        row = db.execute("SELECT epoch,digest,ledger FROM epochs WHERE slot=1").fetchone()
        if row is None:
            raise ReservationDenied("epoch_not_seeded")
        return str(row[0]), str(row[1]), json.loads(row[2])

    def seed(
        self, *, epoch: str, evidence: SignedInventoryEvidence, cap: int, age: float
    ) -> None:
        """One-time bootstrap: independently trusted account data is required."""
        if not epoch or not epoch.strip():
            raise ReservationDenied("invalid_epoch")
        digest = _validate_evidence(evidence, cap=cap, age=age)
        with self._connect() as db:
            self._transaction(db)
            if db.execute("SELECT 1 FROM epochs WHERE slot=1").fetchone():
                raise ReservationDenied("already_seeded_no_implicit_reset")
            db.execute(
                "INSERT INTO epochs(slot,epoch,digest,ledger,gross,net) VALUES(1,?,?,?,?,?)",
                (epoch, digest, json.dumps(dict(evidence.signed_by_exchange), sort_keys=True),
                 evidence.account_gross, evidence.account_net),
            )

    def reserve(
        self, *, epoch: str, evidence: SignedInventoryEvidence, intent_id: str,
        exchange_id: str, outcome_side: str, action: str, quantity: int,
        cap: int, age: float,
    ) -> ReductionReservation:
        if not intent_id or not intent_id.strip():
            raise ReservationDenied("invalid_intent_id")
        digest = _validate_evidence(evidence, cap=cap, age=age)
        verdict = screen_signed_reduction(
            evidence, exchange_id=exchange_id, outcome_side=outcome_side,
            action=action, quantity=quantity, gross_limit=cap,
            max_account_age_seconds=age,
        )
        if not verdict.mathematically_reducing or verdict.signed_delta is None:
            raise ReservationDenied(verdict.reason)
        with self._connect() as db:
            self._transaction(db)
            active, stored_digest, ledger = self._epoch(db)
            if epoch != active or stored_digest != digest or ledger != dict(evidence.signed_by_exchange):
                raise ReservationDenied("stale_or_changed_ledger_epoch")
            row = db.execute(
                "SELECT exchange_id,signed_delta,quantity,cumulative,state FROM intents WHERE intent_id=?",
                (intent_id,),
            ).fetchone()
            if row is not None:
                if (row[0], row[1], row[2]) != (exchange_id, verdict.signed_delta, quantity):
                    raise ReservationDenied("conflicting_logical_intent_replay")
                return ReductionReservation(intent_id, row[0], row[1], row[2], row[3], row[4])
            consumed = int(db.execute(
                "SELECT COALESCE(SUM(quantity),0) FROM intents WHERE epoch=? AND exchange_id=?",
                (active, exchange_id),
            ).fetchone()[0])
            if consumed + quantity > abs(ledger[exchange_id]):
                raise ReservationDenied("inventory_slice_already_reserved")
            db.execute(
                "INSERT INTO intents(intent_id,epoch,exchange_id,signed_delta,quantity,cumulative,state)"
                " VALUES(?,?,?,?,?,0,'RESERVED')",
                (intent_id, epoch, exchange_id, verdict.signed_delta, quantity),
            )
            return ReductionReservation(
                intent_id, exchange_id, verdict.signed_delta, quantity, 0, "RESERVED"
            )

    def observe(
        self, *, intent_id: str, event_id: str, kind: str, cumulative_filled: int = 0,
    ) -> ReductionReservation:
        """Record offline evidence without releasing on ACK, cancel or timeout.

        CUMULATIVE_FILL is an externally observed cumulative number, not an
        additive event. TERMINAL is a claimed venue-confirmed terminal state,
        but only the subsequent signed-account reconcile can free inventory.
        Neither claim is authenticated by this isolated library.
        """
        if kind not in {"ACK", "TIMEOUT", "CANCEL_REQUESTED", "CUMULATIVE_FILL", "TERMINAL"}:
            raise ReservationDenied("unknown_event_kind")
        if (
            not event_id or not event_id.strip()
            or not isinstance(cumulative_filled, int)
            or isinstance(cumulative_filled, bool)
            or cumulative_filled < 0
        ):
            raise ReservationDenied("malformed_event")
        with self._connect() as db:
            self._transaction(db)
            epoch, _, _ = self._epoch(db)
            row = db.execute(
                "SELECT exchange_id,signed_delta,quantity,cumulative,state "
                "FROM intents WHERE intent_id=? AND epoch=?",
                (intent_id, epoch),
            ).fetchone()
            if row is None:
                raise ReservationDenied("unknown_active_intent")
            exchange, delta, qty, old, state = row
            prior = db.execute(
                "SELECT intent_id,kind,cumulative FROM events WHERE event_id=?", (event_id,)
            ).fetchone()
            if prior is not None:
                if prior != (intent_id, kind, cumulative_filled):
                    raise ReservationDenied("conflicting_duplicate_event")
                return ReductionReservation(intent_id, exchange, delta, qty, old, state)
            if state == "TERMINAL":
                raise ReservationDenied("terminal_intent_immutable")
            if kind in {"CUMULATIVE_FILL", "TERMINAL"}:
                if cumulative_filled < old or cumulative_filled > qty:
                    raise ReservationDenied("invalid_cumulative_fill")
                next_fill = cumulative_filled
            else:
                if cumulative_filled != 0:
                    raise ReservationDenied("non_fill_event_cannot_claim_fill")
                next_fill = old
            next_state = "TERMINAL" if kind == "TERMINAL" else (
                "UNCERTAIN" if kind in {"TIMEOUT", "CANCEL_REQUESTED"} else state
            )
            db.execute(
                "INSERT INTO events(event_id,intent_id,kind,cumulative) VALUES(?,?,?,?)",
                (event_id, intent_id, kind, cumulative_filled),
            )
            db.execute(
                "UPDATE intents SET cumulative=?,state=? WHERE intent_id=?",
                (next_fill, next_state, intent_id),
            )
            return ReductionReservation(
                intent_id, exchange, delta, qty, next_fill, next_state
            )

    def reconcile(
        self, *, prior_epoch: str, new_epoch: str, evidence: SignedInventoryEvidence,
        cap: int, age: float,
    ) -> None:
        """Reopen capacity only on exact new signed-account + terminal parity."""
        if not new_epoch or not new_epoch.strip() or new_epoch == prior_epoch:
            raise ReservationDenied("new_account_epoch_required")
        digest = _validate_evidence(evidence, cap=cap, age=age)
        with self._connect() as db:
            self._transaction(db)
            active, _, old_ledger = self._epoch(db)
            if active != prior_epoch:
                raise ReservationDenied("epoch_compare_and_swap_failed")
            rows = db.execute(
                "SELECT exchange_id,signed_delta,quantity,cumulative,state "
                "FROM intents WHERE epoch=?", (active,),
            ).fetchall()
            if not rows or any(row[4] != "TERMINAL" for row in rows):
                raise ReservationDenied("unresolved_venue_order_or_cancel")
            expected = dict(old_ledger)
            for exchange, delta, qty, filled, _ in rows:
                if filled > qty:
                    raise ReservationDenied("fill_exceeds_reservation")
                expected[exchange] += (1 if delta > 0 else -1) * filled
            expected = {k: v for k, v in expected.items() if v != 0}
            if expected != dict(evidence.signed_by_exchange):
                raise ReservationDenied("authoritative_signed_fill_reconciliation_mismatch")
            db.execute(
                "UPDATE epochs SET epoch=?,digest=?,ledger=?,gross=?,net=? WHERE slot=1",
                (new_epoch, digest, json.dumps(expected, sort_keys=True),
                 evidence.account_gross, evidence.account_net),
            )
