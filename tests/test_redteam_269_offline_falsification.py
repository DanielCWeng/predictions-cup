"""Independent Red Team offline #269 adversarial lease and terminality tests.

NO venue/host/API access; signing key is generated in tests only and has
NO asserted issuer custody. Reviewed production/research module is unchanged.
"""
from __future__ import annotations

import multiprocessing as mp
import sqlite3
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from predictions_cup.research.account_terminality_lease import (
    AccountEpochProof,
    OfflineAccountLease,
    OfflineIssuerRegistry,
)
from test_account_terminality_lease import (
    NOW,
    _claim,
    _reserve,
    _seed,
    _signed_epoch,
    _signed_terminal,
)


def _racer(
    path: str, registry: OfflineIssuerRegistry, proof: AccountEpochProof,
    token: int, name: str, qty: int, ready: mp.Queue[str],
    go: mp.synchronize.Event, done: mp.Queue[tuple[str, str, bool]],
) -> None:
    account = OfflineAccountLease(path, registry)
    ready.put(name)
    if not go.wait(timeout=15):
        done.put((name, "TIMEOUT", False))
        return
    verdict = account.reserve(
        proof=proof, holder="west", token=token, intent_id=name,
        venue_order_id="venue-" + name, exchange_id="36",
        signed_delta=qty, now=NOW,
    )
    done.put((name, verdict.status, verdict.execution_authorised))


def _spawn_two(
    path: Path, registry: OfflineIssuerRegistry, proof: AccountEpochProof,
    token: int, qty: int,
) -> tuple[tuple[str, str, bool], tuple[str, str, bool]]:
    ctx = mp.get_context("spawn")
    ready: mp.Queue[str] = ctx.Queue()
    done: mp.Queue[tuple[str, str, bool]] = ctx.Queue()
    go = ctx.Event()
    workers = [
        ctx.Process(
            target=_racer,
            args=(str(path), registry, proof, token, f"rival-{i}", qty, ready, go, done),
        )
        for i in range(2)
    ]
    try:
        for proc in workers:
            proc.start()
        assert {ready.get(timeout=20), ready.get(timeout=20)} == {
            "rival-0", "rival-1"
        }
        go.set()
        results = (done.get(timeout=20), done.get(timeout=20))
    finally:
        go.set()
        for proc in workers:
            proc.join(timeout=20)
            if proc.is_alive():
                proc.terminate()
                proc.join(timeout=10)
        assert all(proc.exitcode == 0 for proc in workers)
    return results


@pytest.mark.parametrize("qty,expected", [
    (700, ["NO_CAPACITY", "SIMULATED_ONLY"]),
    (400, ["SIMULATED_ONLY", "SIMULATED_ONLY"]),
])
def test_redteam_269_real_two_os_processes_shared_token_and_gross_conservation(
    tmp_path: Path,
    proven: tuple[Ed25519PrivateKey, OfflineIssuerRegistry, AccountEpochProof],
    qty: int, expected: list[str],
) -> None:
    """Two separate PIDs can COPY holder+token; shared gross CAS still limits sum.

    400+400 is admitted and is a *LIVE authority* failure, not an over-cap
    breach in research, because source explicitly only simulates leases.
    """
    _key, registry, proof = proven
    path = tmp_path / "rivals.sqlite"
    account = _seed(path, registry, proof)
    token = _claim(account)
    results = _spawn_two(path, registry, proof, token, qty)
    assert sorted(r[1] for r in results) == expected
    assert all(r[2] is False for r in results)
    with sqlite3.connect(path) as db:
        used = db.execute(
            "SELECT COALESCE(SUM(quantity),0) FROM intents "
            "WHERE state != 'RECONCILED'"
        ).fetchone()[0]
        rows = db.execute("SELECT COUNT(*) FROM intents").fetchone()[0]
    assert used == qty * expected.count("SIMULATED_ONLY")
    assert 99_000 + used <= 100_000
    assert rows == expected.count("SIMULATED_ONLY")
    assert account.claim(
        epoch="e1", holder="east", now=NOW+timedelta(seconds=70),
        expires=NOW+timedelta(seconds=85),
    ).status == "NO_CAPACITY"


def test_redteam_269_filled_zero_signed_terminal_cannot_retire_risk(
    tmp_path: Path,
    proven: tuple[Ed25519PrivateKey, OfflineIssuerRegistry, AccountEpochProof],
) -> None:
    """FILLED with zero cumulative volume is semantically contradictory.

    Even *cryptographically fixture-signed* same-key, unchanged positions
    should NOT be sufficient to reclaim full reserved gross.
    """
    private, registry, proof = proven
    account = _seed(tmp_path / "filled-zero.sqlite", registry, proof)
    token = _claim(account)
    assert _reserve(account, proof, token, qty=700) == "SIMULATED_ONLY"
    assert account.simulated_possible_wire("intent-a").status == "SIMULATED_ONLY"
    successor = _signed_epoch(
        private, epoch="e2", revision="r2",
        published=NOW+timedelta(seconds=60), positions={"36": 99_000},
    )
    terminal = _signed_terminal(
        private, status="FILLED", cumulative=0,
    )
    verdict = account.reconcile(
        prior=proof, successor=successor,
        terminal=(terminal,), now=NOW+timedelta(seconds=61),
    )
    assert verdict.status in {"NO_CAPACITY", "UNVERIFIED"}, (
        "FILLED with cumulative_filled=0 retired possible-wire gross risk"
    )
    assert verdict.execution_authorised is False
    with sqlite3.connect(account.path) as db:
        assert db.execute("SELECT state FROM intents").fetchone()[0] == "UNCERTAIN"


def test_redteam_269_rejected_positive_fill_needs_semantic_issuer_proof(
    tmp_path: Path,
    proven: tuple[Ed25519PrivateKey, OfflineIssuerRegistry, AccountEpochProof],
) -> None:
    """A REJECTED status with confirmed fill must not silently retire risk.

    If venue rules allow filled partial then rejected remainder, that precise
    contract must be documented before treating this as proof.
    """
    private, registry, proof = proven
    account = _seed(tmp_path / "rejected-positive.sqlite", registry, proof)
    token = _claim(account)
    assert _reserve(account, proof, token, qty=700) == "SIMULATED_ONLY"
    account.simulated_possible_wire("intent-a")
    successor = _signed_epoch(
        private, epoch="e2", revision="r2",
        published=NOW+timedelta(seconds=60), positions={"36": 99_200},
    )
    terminal = _signed_terminal(private, status="REJECTED", cumulative=200)
    verdict = account.reconcile(
        prior=proof, successor=successor,
        terminal=(terminal,), now=NOW+timedelta(seconds=61),
    )
    assert verdict.status in {"NO_CAPACITY", "UNVERIFIED"}, (
        "REJECTED+positive cumulative fill needs authoritative terminal semantics"
    )
    assert verdict.execution_authorised is False


@pytest.mark.parametrize("variation", [
    "missing_terminal", "reopened", "wrong_order", "wrong_fill",
    "tamper", "expired_account", "wrong_issuer", "prior_epoch_replay",
])
def test_redteam_269_untrusted_terminal_cannot_release_reserve(
    tmp_path: Path,
    proven: tuple[Ed25519PrivateKey, OfflineIssuerRegistry, AccountEpochProof],
    variation: str,
) -> None:
    private, registry, prior = proven
    account = _seed(tmp_path / (variation+".sqlite"), registry, prior)
    token = _claim(account)
    assert _reserve(account, prior, token, qty=700) == "SIMULATED_ONLY"
    account.simulated_possible_wire("intent-a")
    successor = _signed_epoch(
        private, epoch="e2", revision="r2",
        published=NOW+timedelta(seconds=60), positions={"36": 99_300},
    )
    terminal = _signed_terminal(private, status="FILLED", cumulative=300)
    candidates = (terminal,)
    if variation == "missing_terminal":
        candidates = ()
    elif variation == "reopened":
        candidates = (_signed_terminal(private, status="OPEN", cumulative=300),)
    elif variation == "wrong_order":
        candidates = (_signed_terminal(private, order="foreign-order", cumulative=300),)
    elif variation == "wrong_fill":
        candidates = (_signed_terminal(private, cumulative=200),)
    elif variation == "tamper":
        candidates = (replace(terminal, signature=b"x"*64),)
    elif variation == "expired_account":
        successor = _signed_epoch(
            private, epoch="e2", revision="r2",
            published=NOW+timedelta(seconds=60),
            expires=NOW+timedelta(seconds=60, microseconds=100),
            positions={"36": 99_300},
        )
    elif variation == "wrong_issuer":
        candidates = (replace(terminal, issuer_id="not-fixture-issuer"),)
    else:
        successor = _signed_epoch(
            private, epoch="e1", revision="r1-reused",
            published=NOW+timedelta(seconds=60), positions={"36": 99_300},
        )
    verdict = account.reconcile(
        prior=prior, successor=successor, terminal=candidates,
        now=NOW+timedelta(seconds=61),
    )
    assert verdict.status in {"UNVERIFIED", "NO_CAPACITY"}
    assert verdict.execution_authorised is False
    assert verdict.current_verified_headroom == "UNKNOWN"
    with sqlite3.connect(account.path) as db:
        assert db.execute("SELECT state FROM intents").fetchone()[0] == "UNCERTAIN"


def test_redteam_269_foreign_actor_can_only_tighten_possible_wire(
    tmp_path: Path,
    proven: tuple[Ed25519PrivateKey, OfflineIssuerRegistry, AccountEpochProof],
) -> None:
    _private, registry, proof = proven
    account = _seed(tmp_path/"foreign.sqlite", registry, proof)
    token = _claim(account)
    assert _reserve(account, proof, token, qty=700) == "SIMULATED_ONLY"
    # No holder/token requirement for this call; this MUST NOT release risk.
    first = OfflineAccountLease(account.path, registry).simulated_possible_wire("intent-a")
    assert first.status == "SIMULATED_ONLY"
    assert not first.execution_authorised
    assert OfflineAccountLease(
        account.path, registry
    ).simulated_possible_wire("another-nonexistent-operation").status == "NO_CAPACITY"
    with sqlite3.connect(account.path) as db:
        assert db.execute("SELECT state FROM intents").fetchone()[0] == "UNCERTAIN"
        assert db.execute("SELECT COUNT(*) FROM send_events").fetchone()[0] == 1
    assert _reserve(account, proof, token, intent="later", order="later") == "NO_CAPACITY"


def test_redteam_269_full_gross_cap_and_duplicate_venue_id_denied(
    tmp_path: Path,
    proven: tuple[Ed25519PrivateKey, OfflineIssuerRegistry, AccountEpochProof],
) -> None:
    private, registry, proof = proven
    full = _signed_epoch(private, positions={"36": 100_000})
    account = _seed(tmp_path/"full.sqlite", registry, full)
    token = _claim(account)
    verdict = account.reserve(
        proof=full, holder="west", token=token, intent_id="full",
        venue_order_id="full-order", exchange_id="36",
        signed_delta=-1, now=NOW,
    )
    assert verdict.status == "NO_CAPACITY"
    assert not verdict.execution_authorised
    room_account = _seed(tmp_path/"room.sqlite", registry, proof)
    key = _claim(room_account)
    assert _reserve(
        room_account, proof, key, intent="one", order="same-order", qty=100,
    ) == "SIMULATED_ONLY"
    assert _reserve(
        room_account, proof, key, intent="two", order="same-order", qty=100,
    ) == "NO_CAPACITY"
