"""OFFLINE ONLY: signed account epoch, terminality and fenced risk-admission experiment.

Builds on #241's BEGIN IMMEDIATE reservation research WITHOUT importing into
production risk, execution, services or maker. Crypto-verifiable fixture inputs
are NOT operationally authenticated venue evidence. Every result denies LIVE.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

GROSS_CAP = 100_000  # RISK-002 remains untouched; research uses its existing ceiling.


@dataclass(frozen=True, slots=True)
class OfflineVerdict:
    status: str  # UNVERIFIED | NO_CAPACITY | SIMULATED_ONLY
    reason: str
    lease_token: int | None = None

    @property
    def execution_authorised(self) -> bool:
        return False

    @property
    def current_verified_headroom(self) -> str:
        return "UNKNOWN"


@dataclass(frozen=True, slots=True)
class AccountEpochProof:
    account_id: str
    tournament_id: str
    epoch: str
    publisher_revision: str
    publisher_at: datetime
    expires_at: datetime
    signed_by_exchange: Mapping[str, int]
    gross: int
    net: int
    open_order_ids: tuple[str, ...]
    uncertain_order_ids: tuple[str, ...]
    active_sender_ids: tuple[str, ...]
    issuer_id: str
    key_id: str
    signature: bytes

    def signed_content(self) -> bytes:
        return _canonical({
            "type": "ACCOUNT_EPOCH_V1", "account": self.account_id,
            "tournament": self.tournament_id, "epoch": self.epoch,
            "revision": self.publisher_revision,
            "published": _time(self.publisher_at), "expires": _time(self.expires_at),
            "positions": dict(self.signed_by_exchange), "gross": self.gross,
            "net": self.net, "open_orders": self.open_order_ids,
            "uncertain_orders": self.uncertain_order_ids,
            "sender_ids": self.active_sender_ids, "issuer": self.issuer_id,
            "key": self.key_id,
        })


@dataclass(frozen=True, slots=True)
class TerminalOrderProof:
    account_id: str
    tournament_id: str
    epoch: str
    intent_id: str
    venue_order_id: str
    terminal_status: str  # FILLED | CANCELLED | EXPIRED | REJECTED
    cumulative_filled: int
    publisher_at: datetime
    issuer_id: str
    key_id: str
    signature: bytes

    def signed_content(self) -> bytes:
        return _canonical({
            "type": "TERMINAL_ORDER_V1", "account": self.account_id,
            "tournament": self.tournament_id, "epoch": self.epoch,
            "intent": self.intent_id, "order": self.venue_order_id,
            "status": self.terminal_status, "cumulative": self.cumulative_filled,
            "published": _time(self.publisher_at), "issuer": self.issuer_id,
            "key": self.key_id,
        })


@dataclass(frozen=True, slots=True)
class OfflineIssuerRegistry:
    """Externally PINNED keys for schema tests; no production trust delegation.

    A test-provided public key proves only signature/identity binding, not that
    a venue or custodian owns the key. No result may confer LIVE capacity.
    """
    keys: Mapping[tuple[str, str], bytes]
    account_id: str
    tournament_id: str

    def check(self, issuer: str, key: str, data: bytes, signature: bytes) -> bool:
        raw = self.keys.get((issuer, key))
        if raw is None or len(raw) != 32 or len(signature) != 64:
            return False
        try:
            Ed25519PublicKey.from_public_bytes(raw).verify(signature, data)
        except (InvalidSignature, ValueError):
            return False
        return True


def _time(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("untrusted_naive_clock")
    return value.astimezone(UTC).isoformat(timespec="microseconds")


def _canonical(payload: object) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()


def _valid_snapshot(
    proof: AccountEpochProof, registry: OfflineIssuerRegistry, now: datetime
) -> str | None:
    try:
        current, published, expires = (
            _time(t) for t in (now, proof.publisher_at, proof.expires_at)
        )
        if not current or not published or not expires:
            return "untrusted_clock"
        if not (proof.publisher_at <= now <= proof.expires_at):
            return "stale_future_or_expired_epoch"
        if proof.expires_at <= proof.publisher_at:
            return "invalid_epoch_window"
        encoded = proof.signed_content()
    except (TypeError, ValueError):
        return "malformed_or_naive_proof"
    if (
        proof.account_id != registry.account_id
        or proof.tournament_id != registry.tournament_id
        or not proof.epoch.strip()
        or not proof.publisher_revision.strip()
    ):
        return "unbound_account_epoch_or_revision"
    signed = proof.signed_by_exchange
    if not signed or any(
        not isinstance(k, str) or not k.strip()
        or not isinstance(v, int) or isinstance(v, bool) or v == 0
        for k, v in signed.items()
    ):
        return "invalid_signed_ledger"
    if (
        not isinstance(proof.gross, int) or isinstance(proof.gross, bool)
        or not isinstance(proof.net, int) or isinstance(proof.net, bool)
        or sum(abs(v) for v in signed.values()) != proof.gross
        or sum(signed.values()) != proof.net or proof.gross > GROSS_CAP
    ):
        return "gross_net_signed_parity_or_cap"
    if proof.open_order_ids or proof.uncertain_order_ids:
        return "incomplete_open_or_unknown_orders"
    if (
        len(proof.active_sender_ids) != 1
        or not proof.active_sender_ids[0].strip()
    ):
        return "unfenced_rival_sender_claim"
    if not registry.check(proof.issuer_id, proof.key_id, encoded, proof.signature):
        return "missing_or_untrusted_crypto_issuer"
    return None


def _valid_terminal(
    item: TerminalOrderProof, registry: OfflineIssuerRegistry,
    prior: AccountEpochProof, successor: AccountEpochProof,
) -> bool:
    try:
        aware = _time(item.publisher_at)
        if not aware:
            return False
        return (
            item.account_id == registry.account_id
            and item.tournament_id == registry.tournament_id
            and item.epoch == prior.epoch
            and item.terminal_status in {"FILLED", "CANCELLED", "EXPIRED", "REJECTED"}
            and item.publisher_at >= prior.publisher_at
            and item.publisher_at <= successor.publisher_at
            and type(item.cumulative_filled) is int
            and item.cumulative_filled >= 0
            and registry.check(
                item.issuer_id, item.key_id, item.signed_content(), item.signature
            )
        )
    except (TypeError, ValueError):
        return False


class OfflineAccountLease:
    """Shared SQLite *simulation* of one account's gross and fence epoch.

    No account connector, HTTP client or production risk imports. A reservation
    is persisted BEFORE a simulated network attempt. Lease expiry, cancellation,
    timeout, unknown order or process death NEVER itself releases risk.
    """

    def __init__(self, path: str | Path, registry: OfflineIssuerRegistry) -> None:
        self.path = str(path)
        self.registry = registry
        with self._db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS account (
                    slot INTEGER PRIMARY KEY CHECK(slot=1),
                    epoch TEXT NOT NULL, revision TEXT NOT NULL,
                    publisher TEXT NOT NULL, expires TEXT NOT NULL,
                    digest TEXT NOT NULL, positions TEXT NOT NULL,
                    gross INTEGER NOT NULL, sender TEXT NOT NULL,
                    fence INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS epochs (epoch TEXT PRIMARY KEY);
                CREATE TABLE IF NOT EXISTS lease (
                    slot INTEGER PRIMARY KEY CHECK(slot=1),
                    holder TEXT NOT NULL, token INTEGER NOT NULL,
                    expires TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS intents (
                    intent_id TEXT PRIMARY KEY, epoch TEXT NOT NULL,
                    token INTEGER NOT NULL, holder TEXT NOT NULL,
                    venue_order_id TEXT NOT NULL UNIQUE,
                    exchange_id TEXT NOT NULL, signed_delta INTEGER NOT NULL,
                    quantity INTEGER NOT NULL, state TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS send_events (
                    intent_id TEXT PRIMARY KEY, event_kind TEXT NOT NULL
                );
            """)

    def _db(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, isolation_level=None, timeout=8)
        db.execute("PRAGMA busy_timeout=8000")
        db.execute("PRAGMA synchronous=FULL")
        return db

    @staticmethod
    def _deny(reason: str, *, unverified: bool = False) -> OfflineVerdict:
        return OfflineVerdict("UNVERIFIED" if unverified else "NO_CAPACITY", reason)

    def seed(self, proof: AccountEpochProof, now: datetime) -> OfflineVerdict:
        error = _valid_snapshot(proof, self.registry, now)
        if error:
            return self._deny(error, unverified=True)
        try:
            with self._db() as db:
                db.execute("BEGIN IMMEDIATE")
                if db.execute("SELECT 1 FROM account WHERE slot=1").fetchone():
                    return self._deny("already_seeded")
                db.execute("INSERT INTO epochs(epoch) VALUES(?)", (proof.epoch,))
                db.execute(
                    "INSERT INTO account(slot,epoch,revision,publisher,expires,digest,"
                    "positions,gross,sender,fence) VALUES(1,?,?,?,?,?,?,?,?,0)",
                    (proof.epoch, proof.publisher_revision, _time(proof.publisher_at),
                     _time(proof.expires_at),
                     hashlib.sha256(proof.signed_content()).hexdigest(),
                     json.dumps(dict(proof.signed_by_exchange), sort_keys=True),
                     proof.gross, proof.active_sender_ids[0]),
                )
                return OfflineVerdict("SIMULATED_ONLY", "synthetic_signed_epoch_seeded")
        except sqlite3.Error:
            return self._deny("sqlite_seed_or_busy")

    def claim(
        self, *, epoch: str, holder: str, now: datetime, expires: datetime
    ) -> OfflineVerdict:
        try:
            clock, deadline = _time(now), _time(expires)
            if not holder or expires <= now:
                return self._deny("invalid_lease_request")
        except (TypeError, ValueError):
            return self._deny("invalid_clock", unverified=True)
        try:
            with self._db() as db:
                db.execute("BEGIN IMMEDIATE")
                acct = db.execute(
                    "SELECT epoch,expires,fence,sender FROM account WHERE slot=1"
                ).fetchone()
                if acct is None or acct[0] != epoch:
                    return self._deny("wrong_or_absent_account_epoch", unverified=True)
                if clock > acct[1]:
                    return self._deny("expired_account_snapshot", unverified=True)
                if holder != acct[3]:
                    return self._deny("unattested_cross_host_sender")
                lease = db.execute("SELECT holder,token,expires FROM lease WHERE slot=1").fetchone()
                if lease is not None and clock <= lease[2]:
                    return self._deny("another_lease_or_owner_active")
                if db.execute(
                    "SELECT 1 FROM intents WHERE state != 'RECONCILED' LIMIT 1"
                ).fetchone():
                    return self._deny("uncertain_reserved_leases_survive_expiry")
                token = int(acct[2]) + 1
                db.execute("UPDATE account SET fence=? WHERE slot=1", (token,))
                db.execute(
                    "INSERT INTO lease(slot,holder,token,expires) VALUES(1,?,?,?)"
                    " ON CONFLICT(slot) DO UPDATE SET holder=excluded.holder,"
                    "token=excluded.token,expires=excluded.expires",
                    (holder, token, deadline),
                )
                return OfflineVerdict("SIMULATED_ONLY", "simulated_fenced_lease", token)
        except sqlite3.Error:
            return self._deny("sqlite_lease_busy")

    def reserve(
        self, *, proof: AccountEpochProof, holder: str, token: int,
        intent_id: str, venue_order_id: str, exchange_id: str,
        signed_delta: int, now: datetime,
    ) -> OfflineVerdict:
        error = _valid_snapshot(proof, self.registry, now)
        if error:
            return self._deny(error, unverified=True)
        if (
            not intent_id or not venue_order_id or not exchange_id
            or type(signed_delta) is not int or signed_delta == 0
        ):
            return self._deny("invalid_intent_or_quantity")
        qty = abs(signed_delta)
        digest = hashlib.sha256(proof.signed_content()).hexdigest()
        try:
            with self._db() as db:
                db.execute("BEGIN IMMEDIATE")
                acct = db.execute(
                    "SELECT epoch,revision,digest,gross,sender FROM account WHERE slot=1"
                ).fetchone()
                if acct is None or (
                    acct[0] != proof.epoch or acct[1] != proof.publisher_revision
                    or acct[2] != digest
                ):
                    return self._deny("stale_epoch_compare_and_reserve", unverified=True)
                lease = db.execute("SELECT holder,token,expires FROM lease WHERE slot=1").fetchone()
                if (
                    holder != acct[4]
                    or lease is None or lease[0] != holder or lease[1] != token
                    or _time(now) > lease[2]
                ):
                    return self._deny("unfenced_or_expired_lease")
                if db.execute(
                    "SELECT 1 FROM intents WHERE intent_id=? OR venue_order_id=?",
                    (intent_id, venue_order_id),
                ).fetchone():
                    return self._deny("duplicate_or_replayed_intent")
                committed = db.execute(
                    "SELECT COALESCE(SUM(quantity),0) FROM intents "
                    "WHERE state != 'RECONCILED'"
                ).fetchone()
                reserved = int(committed[0]) if committed else 0
                # Conservative gross: NEVER assume the requested trade reduces
                # risk. The #241 signed-reduction math stays independently inert.
                if int(acct[3]) + reserved + qty > GROSS_CAP:
                    return self._deny("absolute_gross_cap_or_reservation")
                db.execute(
                    "INSERT INTO intents(intent_id,epoch,token,holder,venue_order_id,"
                    "exchange_id,signed_delta,quantity,state) VALUES(?,?,?,?,?,?,?,?,'RESERVED')",
                    (intent_id, proof.epoch, token, holder, venue_order_id,
                     exchange_id, signed_delta, qty),
                )
                return OfflineVerdict("SIMULATED_ONLY", "durable_intent_before_fake_wire", token)
        except sqlite3.Error:
            return self._deny("sqlite_reservation_busy")

    def simulated_possible_wire(self, intent_id: str) -> OfflineVerdict:
        """Mark prior possibility of a wire durably; NO network call exists."""
        try:
            with self._db() as db:
                db.execute("BEGIN IMMEDIATE")
                row = db.execute(
                    "SELECT state FROM intents WHERE intent_id=?", (intent_id,)
                ).fetchone()
                if row is None or row[0] == "RECONCILED":
                    return self._deny("missing_or_retired_intent")
                db.execute(
                    "INSERT OR IGNORE INTO send_events(intent_id,event_kind)"
                    " VALUES(?,'POSSIBLE_WIRE')", (intent_id,),
                )
                db.execute(
                    "UPDATE intents SET state='UNCERTAIN' WHERE intent_id=?",
                    (intent_id,),
                )
                return OfflineVerdict("SIMULATED_ONLY", "possible_wire_reserve_held")
        except sqlite3.Error:
            return self._deny("sqlite_possible_wire_busy")

    def reconcile(
        self, *, prior: AccountEpochProof, successor: AccountEpochProof,
        terminal: tuple[TerminalOrderProof, ...], now: datetime,
    ) -> OfflineVerdict:
        error = _valid_snapshot(successor, self.registry, now)
        if error:
            return self._deny(error, unverified=True)
        if (
            successor.epoch == prior.epoch
            or successor.publisher_at <= prior.publisher_at
            or len({t.intent_id for t in terminal}) != len(terminal)
        ):
            return self._deny("non_increasing_epoch_or_duplicate_terminal", unverified=True)
        # The prior snapshot must also pass signature verification, though its
        # expiry is allowed here because this is *historical* reconciliation.
        if not self.registry.check(
            prior.issuer_id, prior.key_id, prior.signed_content(), prior.signature
        ):
            return self._deny("untrusted_prior_account", unverified=True)
        try:
            with self._db() as db:
                db.execute("BEGIN IMMEDIATE")
                acct = db.execute(
                    "SELECT epoch,digest,positions FROM account WHERE slot=1"
                ).fetchone()
                if acct is None or acct[0] != prior.epoch or acct[1] != hashlib.sha256(
                    prior.signed_content()
                ).hexdigest():
                    return self._deny("prior_epoch_digest_mismatch", unverified=True)
                if db.execute(
                    "SELECT 1 FROM epochs WHERE epoch=?", (successor.epoch,)
                ).fetchone():
                    return self._deny("reused_epoch", unverified=True)
                rows = db.execute(
                    "SELECT intent_id,venue_order_id,exchange_id,signed_delta,quantity,"
                    "state FROM intents WHERE state != 'RECONCILED'"
                ).fetchall()
                if not rows or len(rows) != len(terminal):
                    return self._deny("missing_venue_terminal_proofs")
                indexed = {p.intent_id: p for p in terminal}
                expected = dict(prior.signed_by_exchange)
                for intent, order, exchange, delta, qty, _state in rows:
                    proof = indexed.get(str(intent))
                    if (
                        proof is None or proof.venue_order_id != order
                        or not _valid_terminal(proof, self.registry, prior, successor)
                        or proof.cumulative_filled > qty
                    ):
                        return self._deny("missing_or_untrusted_terminal_fill", unverified=True)
                    # A test-key signature proves bytes, NOT SIG order-state
                    # semantics. A contradictory or unverified terminal claim
                    # cannot retire an UNCERTAIN reserved intent.
                    if _state not in {"RESERVED", "UNCERTAIN"}:
                        return self._deny("unresolved_intent_state", unverified=True)
                    if proof.terminal_status == "FILLED":
                        if proof.cumulative_filled != qty:
                            return self._deny("filled_requires_full_quantity", unverified=True)
                    elif proof.terminal_status in {"REJECTED", "CANCELLED", "EXPIRED"}:
                        # Venue rules for positive partial terminal fills are not
                        # authenticated. Conservatively deny until attested.
                        if proof.cumulative_filled != 0:
                            return self._deny("partial_terminal_semantics_unknown", unverified=True)
                    else:
                        return self._deny("unsupported_terminal_status", unverified=True)
                    expected[str(exchange)] = expected.get(str(exchange), 0) + (
                        (1 if delta > 0 else -1) * proof.cumulative_filled
                    )
                expected = {k: v for k, v in expected.items() if v}
                if expected != dict(successor.signed_by_exchange):
                    return self._deny("signed_account_terminal_fill_parity", unverified=True)
                db.execute("INSERT INTO epochs(epoch) VALUES(?)", (successor.epoch,))
                db.execute(
                    "UPDATE account SET epoch=?,revision=?,publisher=?,expires=?,"
                    "digest=?,positions=?,gross=?,sender=?,fence=fence+1 WHERE slot=1",
                    (successor.epoch, successor.publisher_revision,
                     _time(successor.publisher_at), _time(successor.expires_at),
                     hashlib.sha256(successor.signed_content()).hexdigest(),
                     json.dumps(expected, sort_keys=True), successor.gross,
                     successor.active_sender_ids[0]),
                )
                db.execute("UPDATE intents SET state='RECONCILED' WHERE state != 'RECONCILED'")
                db.execute("DELETE FROM lease")
                return OfflineVerdict("SIMULATED_ONLY", "fixture_only_terminal_epoch_reconciled")
        except (sqlite3.Error, TypeError, ValueError):
            return self._deny("sqlite_or_invalid_terminal", unverified=True)
