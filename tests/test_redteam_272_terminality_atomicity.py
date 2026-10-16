"""Red Team #272: additional exact-head terminal semantics and atomic rollback controls.

This file is reviewer-only. Synthetic Ed25519 signing keys and isolated SQLite
do not establish venue custody, sender authority or any LIVE headroom.
"""
from __future__ import annotations

import sqlite3
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from predictions_cup.research.account_terminality_lease import (
    AccountEpochProof,
    OfflineAccountLease,
    OfflineIssuerRegistry,
    TerminalOrderProof,
)
from test_account_terminality_lease import (
    ISSUER,
    KEY_ID,
    NOW,
    _claim,
    _reserve,
    _seed,
    _signed_epoch,
    _signed_terminal,
)


@pytest.fixture()
def signer() -> tuple[Ed25519PrivateKey, OfflineIssuerRegistry, AccountEpochProof]:
    private = Ed25519PrivateKey.generate()
    public = private.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    registry = OfflineIssuerRegistry(
        keys={(ISSUER, KEY_ID): public},
        account_id="fixture-account",
        tournament_id="fixture-tournament",
    )
    return private, registry, _signed_epoch(private)


def _state(path: Path) -> tuple[object, ...]:
    """Capture the actual durable before/after state, not just returned status."""
    with sqlite3.connect(path) as db:
        account = db.execute(
            "SELECT epoch,revision,positions,gross,fence FROM account WHERE slot=1"
        ).fetchone()
        intents = db.execute(
            "SELECT intent_id,venue_order_id,state,quantity "
            "FROM intents ORDER BY intent_id"
        ).fetchall()
        lease = db.execute(
            "SELECT holder,token,expires FROM lease WHERE slot=1"
        ).fetchall()
        epochs = db.execute("SELECT epoch FROM epochs ORDER BY epoch").fetchall()
        wires = db.execute(
            "SELECT intent_id,event_kind FROM send_events"
        ).fetchall()
    return account, intents, lease, epochs, wires


def _pending_case(
    path: Path,
    signer: tuple[Ed25519PrivateKey, OfflineIssuerRegistry, AccountEpochProof],
) -> tuple[Ed25519PrivateKey, OfflineAccountLease, AccountEpochProof]:
    private, registry, prior = signer
    store = _seed(path, registry, prior)
    token = _claim(store)
    assert _reserve(store, prior, token, qty=700) == "SIMULATED_ONLY"
    assert store.simulated_possible_wire("intent-a").status == "SIMULATED_ONLY"
    assert _state(path)[1][0][2] == "UNCERTAIN"
    return private, store, prior


@pytest.mark.parametrize(
    ("status", "quantity"),
    [
        ("FILLED", 0),
        ("FILLED", 1),
        ("FILLED", 699),
        ("REJECTED", 200),
        ("CANCELLED", 200),
        ("EXPIRED", 200),
        ("OPEN", 700),
    ],
)
def test_redteam_272_disputed_terminal_preserves_all_durable_state_across_restart(
    tmp_path: Path,
    signer: tuple[Ed25519PrivateKey, OfflineIssuerRegistry, AccountEpochProof],
    status: str,
    quantity: int,
) -> None:
    private, store, prior = _pending_case(tmp_path / "retention.db", signer)
    before = _state(Path(store.path))
    successor = _signed_epoch(
        private, epoch="e2", revision="r2",
        published=NOW + timedelta(seconds=60),
        positions={"36": 99_000 + quantity},
    )
    terminal = _signed_terminal(private, status=status, cumulative=quantity)
    verdict = store.reconcile(
        prior=prior, successor=successor, terminal=(terminal,),
        now=NOW + timedelta(seconds=61),
    )
    assert verdict.status in {"UNVERIFIED", "NO_CAPACITY"}
    assert verdict.execution_authorised is False
    assert verdict.current_verified_headroom == "UNKNOWN"
    assert _state(Path(store.path)) == before
    restarted = OfflineAccountLease(store.path, store.registry)
    assert _state(Path(restarted.path)) == before
    assert restarted.claim(
        epoch="e1", holder="west", now=NOW + timedelta(seconds=70),
        expires=NOW + timedelta(seconds=90),
    ).status == "NO_CAPACITY"
    assert _state(Path(restarted.path)) == before


@pytest.mark.parametrize(
    ("status", "quantity"),
    [
        ("FILLED", 700),
        ("REJECTED", 0),
        ("CANCELLED", 0),
        ("EXPIRED", 0),
    ],
)
def test_redteam_272_compatible_terminal_only_advances_synthetic_epoch(
    tmp_path: Path,
    signer: tuple[Ed25519PrivateKey, OfflineIssuerRegistry, AccountEpochProof],
    status: str,
    quantity: int,
) -> None:
    private, store, prior = _pending_case(tmp_path / "positive.db", signer)
    successor = _signed_epoch(
        private, epoch="e2", revision="r2",
        published=NOW + timedelta(seconds=60),
        positions={"36": 99_000 + quantity},
    )
    terminal = _signed_terminal(private, status=status, cumulative=quantity)
    result = store.reconcile(
        prior=prior, successor=successor, terminal=(terminal,),
        now=NOW + timedelta(seconds=61),
    )
    assert result.status == "SIMULATED_ONLY"
    assert not result.execution_authorised
    assert result.current_verified_headroom == "UNKNOWN"
    with sqlite3.connect(store.path) as db:
        assert db.execute("SELECT epoch FROM account").fetchone()[0] == "e2"
        assert db.execute("SELECT state FROM intents").fetchone()[0] == "RECONCILED"
        assert db.execute("SELECT COUNT(*) FROM lease").fetchone()[0] == 0
    assert store.reconcile(
        prior=prior, successor=successor, terminal=(terminal,),
        now=NOW + timedelta(seconds=61),
    ).status == "UNVERIFIED"


def test_redteam_272_transaction_failure_before_account_update_retains_uncertain(
    tmp_path: Path,
    signer: tuple[Ed25519PrivateKey, OfflineIssuerRegistry, AccountEpochProof],
) -> None:
    private, store, prior = _pending_case(tmp_path / "atomicity.db", signer)
    before = _state(Path(store.path))
    successor = _signed_epoch(
        private, epoch="e2", revision="r2",
        published=NOW + timedelta(seconds=60), positions={"36": 99_700},
    )
    terminal = _signed_terminal(private, status="FILLED", cumulative=700)
    with sqlite3.connect(store.path) as db:
        db.execute(
            "CREATE TRIGGER prevent_epoch_write BEFORE UPDATE ON account "
            "BEGIN SELECT RAISE(ABORT, 'synthetic disk failure'); END"
        )
    result = store.reconcile(
        prior=prior, successor=successor, terminal=(terminal,),
        now=NOW + timedelta(seconds=61),
    )
    assert result.status in {"UNVERIFIED", "NO_CAPACITY"}
    assert result.execution_authorised is False
    assert _state(Path(store.path)) == before, (
        "Failed account epoch commit must not retire intent or lease"
    )
    with sqlite3.connect(store.path) as db:
        db.execute("DROP TRIGGER prevent_epoch_write")
    positive = store.reconcile(
        prior=prior, successor=successor, terminal=(terminal,),
        now=NOW + timedelta(seconds=61),
    )
    assert positive.status == "SIMULATED_ONLY"
    assert not positive.execution_authorised


def test_redteam_272_unknown_internal_intent_state_cannot_retire(
    tmp_path: Path,
    signer: tuple[Ed25519PrivateKey, OfflineIssuerRegistry, AccountEpochProof],
) -> None:
    private, store, prior = _pending_case(tmp_path / "state.db", signer)
    with sqlite3.connect(store.path) as db:
        db.execute(
            "UPDATE intents SET state='UNRECOGNISED' WHERE intent_id='intent-a'"
        )
    before = _state(Path(store.path))
    successor = _signed_epoch(
        private, epoch="e2", revision="r2",
        published=NOW + timedelta(seconds=60),
        positions={"36": 99_700},
    )
    terminal = _signed_terminal(private, status="FILLED", cumulative=700)
    result = store.reconcile(
        prior=prior, successor=successor, terminal=(terminal,),
        now=NOW + timedelta(seconds=61),
    )
    assert result.status in {"UNVERIFIED", "NO_CAPACITY"}
    assert _state(Path(store.path)) == before


def test_redteam_272_signed_terminal_tamper_does_not_mutate_epoch_or_risk(
    tmp_path: Path,
    signer: tuple[Ed25519PrivateKey, OfflineIssuerRegistry, AccountEpochProof],
) -> None:
    private, store, prior = _pending_case(tmp_path / "tamper.db", signer)
    before = _state(Path(store.path))
    successor = _signed_epoch(
        private, epoch="e2", revision="r2",
        published=NOW + timedelta(seconds=60), positions={"36": 99_700},
    )
    original: TerminalOrderProof = _signed_terminal(
        private, status="FILLED", cumulative=700
    )
    tampered = replace(original, cumulative_filled=699)
    result = store.reconcile(
        prior=prior, successor=successor, terminal=(tampered,),
        now=NOW + timedelta(seconds=61),
    )
    assert result.status in {"UNVERIFIED", "NO_CAPACITY"}
    assert not result.execution_authorised
    assert _state(Path(store.path)) == before
