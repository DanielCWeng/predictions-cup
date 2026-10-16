"""Research-only falsification: no real account, host, network or LIVE authority."""

from __future__ import annotations

import multiprocessing as mp
import os
import sqlite3
from dataclasses import replace
from datetime import UTC, datetime, timedelta
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

NOW = datetime(2026, 10, 16, 17, 0, tzinfo=UTC)
ISSUER = "test-fixture-NOT-a-venue-issuer"
KEY_ID = "ephemeral-test-only-key"


def _signed_epoch(
    private: Ed25519PrivateKey, *, epoch: str = "e1", revision: str = "r1",
    published: datetime = NOW, expires: datetime = NOW + timedelta(minutes=3),
    positions: dict[str, int] | None = None, open_ids: tuple[str, ...] = (),
    unknown_ids: tuple[str, ...] = (), senders: tuple[str, ...] = ("west",),
) -> AccountEpochProof:
    ledger = {"36": 99_000} if positions is None else positions
    unsigned = AccountEpochProof(
        account_id="fixture-account", tournament_id="fixture-tournament",
        epoch=epoch, publisher_revision=revision, publisher_at=published,
        expires_at=expires, signed_by_exchange=ledger,
        gross=sum(abs(x) for x in ledger.values()), net=sum(ledger.values()),
        open_order_ids=open_ids, uncertain_order_ids=unknown_ids,
        active_sender_ids=senders, issuer_id=ISSUER, key_id=KEY_ID, signature=b"",
    )
    return replace(unsigned, signature=private.sign(unsigned.signed_content()))


def _signed_terminal(
    private: Ed25519PrivateKey, *, epoch: str = "e1", intent: str = "intent-a",
    order: str = "client-order-a", status: str = "FILLED", cumulative: int = 0,
    published: datetime = NOW + timedelta(seconds=20),
) -> TerminalOrderProof:
    unsigned = TerminalOrderProof(
        account_id="fixture-account", tournament_id="fixture-tournament",
        epoch=epoch, intent_id=intent, venue_order_id=order,
        terminal_status=status, cumulative_filled=cumulative,
        publisher_at=published, issuer_id=ISSUER, key_id=KEY_ID, signature=b"",
    )
    return replace(unsigned, signature=private.sign(unsigned.signed_content()))


@pytest.fixture()
def proven() -> tuple[Ed25519PrivateKey, OfflineIssuerRegistry, AccountEpochProof]:
    # Actual Ed25519 cryptography, but key generated IN TEST, not venue-authenticated.
    private = Ed25519PrivateKey.generate()
    public = private.public_key().public_bytes(
        encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw
    )
    registry = OfflineIssuerRegistry(
        keys={(ISSUER, KEY_ID): public},
        account_id="fixture-account", tournament_id="fixture-tournament",
    )
    return private, registry, _signed_epoch(private)


def _seed(
    path: Path, registry: OfflineIssuerRegistry, proof: AccountEpochProof
) -> OfflineAccountLease:
    lease = OfflineAccountLease(path, registry)
    verdict = lease.seed(proof, NOW)
    assert verdict.status == "SIMULATED_ONLY"
    assert verdict.execution_authorised is False
    assert verdict.current_verified_headroom == "UNKNOWN"
    return lease


def _claim(lease: OfflineAccountLease, *, holder: str = "west") -> int:
    answer = lease.claim(
        epoch="e1", holder=holder, now=NOW, expires=NOW + timedelta(minutes=1)
    )
    assert answer.status == "SIMULATED_ONLY"
    assert not answer.execution_authorised
    assert answer.lease_token is not None
    return answer.lease_token


def _reserve(
    lease: OfflineAccountLease, proof: AccountEpochProof, token: int,
    *, intent: str = "intent-a", order: str = "client-order-a",
    qty: int = 700, now: datetime = NOW, holder: str = "west",
) -> str:
    return lease.reserve(
        proof=proof, holder=holder, token=token, intent_id=intent,
        venue_order_id=order, exchange_id="36", signed_delta=qty, now=now,
    ).status


def _worker(
    path: str, registry: OfflineIssuerRegistry, proof: AccountEpochProof,
    token: int, intent: str, ready: mp.Queue[str], go: mp.synchronize.Event,
    out: mp.Queue[tuple[str, str]],
) -> None:
    lease = OfflineAccountLease(path, registry)
    ready.put(intent)
    if not go.wait(timeout=15):
        out.put((intent, "TIMEOUT"))
        return
    out.put((intent, _reserve(lease, proof, token, intent=intent, order="order-"+intent)))


def _crash_after_reservation(
    path: str, registry: OfflineIssuerRegistry, proof: AccountEpochProof,
    token: int,
) -> None:
    lease = OfflineAccountLease(path, registry)
    result = _reserve(lease, proof, token, qty=900)
    os._exit(0 if result == "SIMULATED_ONLY" else 17)


@pytest.mark.parametrize("change", (
    "wrong_signature", "no_crypto_issuer", "tampered_signed_positions",
    "stale", "future", "open_order", "unknown_fill", "two_senders",
    "gross_discrepancy", "naive_time", "bad_account",
))
def test_untrusted_partial_stale_and_forged_epoch_returns_unverified(
    tmp_path: Path,
    proven: tuple[Ed25519PrivateKey, OfflineIssuerRegistry, AccountEpochProof],
    change: str,
) -> None:
    _private, registry, original = proven
    proof = original
    if change == "wrong_signature":
        proof = replace(proof, signature=b"z" * 64)
    elif change == "no_crypto_issuer":
        registry = replace(registry, keys={})
    elif change == "tampered_signed_positions":
        proof = replace(proof, signed_by_exchange={"36": 1})
    elif change == "stale":
        proof = replace(proof, expires_at=NOW - timedelta(seconds=1))
    elif change == "future":
        proof = replace(proof, publisher_at=NOW + timedelta(seconds=1))
    elif change == "open_order":
        proof = replace(proof, open_order_ids=("unknown-open-order",))
    elif change == "unknown_fill":
        proof = replace(proof, uncertain_order_ids=("unknown-fill",))
    elif change == "two_senders":
        proof = replace(proof, active_sender_ids=("west", "east"))
    elif change == "gross_discrepancy":
        proof = replace(proof, gross=98_999)
    elif change == "naive_time":
        proof = replace(proof, publisher_at=NOW.replace(tzinfo=None))
    else:
        proof = replace(proof, account_id="another-account")
    result = OfflineAccountLease(tmp_path / "unverified.sqlite", registry).seed(proof, NOW)
    assert result.status == "UNVERIFIED"
    assert result.execution_authorised is False
    assert result.current_verified_headroom == "UNKNOWN"


def test_no_self_asserted_signed_boolean_and_real_signature_binding(
    tmp_path: Path,
    proven: tuple[Ed25519PrivateKey, OfflineIssuerRegistry, AccountEpochProof],
) -> None:
    _private, registry, proof = proven
    assert not hasattr(proof, "signed_positions_verified")
    fake = Ed25519PrivateKey.generate()
    falsely_signed = replace(proof, signature=fake.sign(proof.signed_content()))
    answer = OfflineAccountLease(tmp_path / "invalid-key.sqlite", registry).seed(
        falsely_signed, NOW
    )
    assert answer.status == "UNVERIFIED"
    assert not answer.execution_authorised


def test_two_independent_os_processes_distinct_intents_cas_under_gross_cap(
    tmp_path: Path,
    proven: tuple[Ed25519PrivateKey, OfflineIssuerRegistry, AccountEpochProof],
) -> None:
    _private, registry, proof = proven
    path = tmp_path / "os-process-shared-account.sqlite"
    lease = _seed(path, registry, proof)
    token = _claim(lease)
    ctx = mp.get_context("spawn")
    ready: mp.Queue[str] = ctx.Queue()
    go = ctx.Event()
    out: mp.Queue[tuple[str, str]] = ctx.Queue()
    workers = [
        ctx.Process(
            target=_worker,
            args=(str(path), registry, proof, token, label, ready, go, out),
        )
        for label in ("different-op-a", "different-op-b")
    ]
    try:
        for process in workers:
            process.start()
        assert {ready.get(timeout=20), ready.get(timeout=20)} == {
            "different-op-a", "different-op-b"
        }
        go.set()
        results = sorted((out.get(timeout=20)[1], out.get(timeout=20)[1]))
        assert results == ["NO_CAPACITY", "SIMULATED_ONLY"]
    finally:
        go.set()
        for process in workers:
            process.join(timeout=20)
            if process.is_alive():
                process.terminate()
                process.join(timeout=10)
    assert all(process.exitcode == 0 for process in workers)
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT COUNT(*) FROM intents").fetchone()[0] == 1
    assert _reserve(lease, proof, token, intent="third", order="third-order") != "SIMULATED_ONLY"


def test_lease_expiry_crash_and_uncertain_do_not_free_reserved_gross(
    tmp_path: Path,
    proven: tuple[Ed25519PrivateKey, OfflineIssuerRegistry, AccountEpochProof],
) -> None:
    _private, registry, proof = proven
    path = tmp_path / "crash.sqlite"
    lease = _seed(path, registry, proof)
    token = _claim(lease)
    ctx = mp.get_context("spawn")
    worker = ctx.Process(
        target=_crash_after_reservation, args=(str(path), registry, proof, token)
    )
    worker.start()
    worker.join(timeout=20)
    if worker.is_alive():
        worker.terminate()
        worker.join(timeout=10)
    assert worker.exitcode == 0
    recovered = OfflineAccountLease(path, registry)
    # Even when no fake HTTP was ever sent the crash creates possible outcome.
    assert recovered.simulated_possible_wire("intent-a").status == "SIMULATED_ONLY"
    with sqlite3.connect(path) as db:
        assert db.execute(
            "SELECT state FROM intents WHERE intent_id='intent-a'"
        ).fetchone()[0] == "UNCERTAIN"
    takeover = recovered.claim(
        epoch="e1", holder="east", now=NOW + timedelta(seconds=70),
        expires=NOW + timedelta(seconds=90),
    )
    assert takeover.status == "NO_CAPACITY"
    assert takeover.reason == "uncertain_reserved_leases_survive_expiry"
    assert _reserve(recovered, proof, token, intent="late", order="late-order") == "NO_CAPACITY"


def test_fencing_rollover_only_without_unresolved_intents(
    tmp_path: Path,
    proven: tuple[Ed25519PrivateKey, OfflineIssuerRegistry, AccountEpochProof],
) -> None:
    _private, registry, proof = proven
    lease = _seed(tmp_path / "lease-fence.sqlite", registry, proof)
    first = _claim(lease)
    assert lease.claim(
        epoch="e1", holder="east", now=NOW,
        expires=NOW + timedelta(seconds=90),
    ).status == "NO_CAPACITY"
    # Even AFTER lease expiry, East cannot hijack an account epoch signed
    # as West's exclusive sender. An epoch attesting a new owner is required.
    assert lease.claim(
        epoch="e1", holder="east", now=NOW + timedelta(seconds=70),
        expires=NOW + timedelta(seconds=85),
    ).status == "NO_CAPACITY"
    second = lease.claim(
        epoch="e1", holder="west", now=NOW + timedelta(seconds=70),
        expires=NOW + timedelta(seconds=85),
    )
    assert second.status == "SIMULATED_ONLY"
    assert second.lease_token == first + 1
    assert _reserve(lease, proof, first) == "NO_CAPACITY"
    assert _reserve(lease, proof, second.lease_token or -1, holder="west") == "SIMULATED_ONLY"


def test_same_epoch_replay_unknown_terminal_cancel_ack_cannot_recycle(
    tmp_path: Path,
    proven: tuple[Ed25519PrivateKey, OfflineIssuerRegistry, AccountEpochProof],
) -> None:
    private, registry, prior = proven
    lease = _seed(tmp_path / "inflight.sqlite", registry, prior)
    token = _claim(lease)
    assert _reserve(lease, prior, token) == "SIMULATED_ONLY"
    assert _reserve(lease, prior, token) == "NO_CAPACITY"
    assert lease.simulated_possible_wire("intent-a").status == "SIMULATED_ONLY"
    next_epoch = _signed_epoch(
        private, epoch="e2", revision="r2", published=NOW + timedelta(seconds=60),
        positions={"36": 99_000},
    )
    assert lease.reconcile(
        prior=prior, successor=next_epoch, terminal=(), now=NOW + timedelta(seconds=61),
    ).status == "NO_CAPACITY"
    for terminal in (
        _signed_terminal(private, status="OPEN"),
        _signed_terminal(private, order="different-order"),
        _signed_terminal(private, cumulative=701),
        _signed_terminal(private, cumulative=0, status="CANCELLED"),
    ):
        verdict = lease.reconcile(
            prior=prior, successor=next_epoch, terminal=(terminal,),
            now=NOW + timedelta(seconds=61),
        )
        if terminal.terminal_status == "CANCELLED" and terminal.venue_order_id == (
            "client-order-a"
        ):
            assert verdict.status == "SIMULATED_ONLY"
        else:
            assert verdict.status in {"UNVERIFIED", "NO_CAPACITY"}


def test_crypto_terminal_and_full_signed_position_parity_required(
    tmp_path: Path,
    proven: tuple[Ed25519PrivateKey, OfflineIssuerRegistry, AccountEpochProof],
) -> None:
    private, registry, prior = proven
    lease = _seed(tmp_path / "terminal.sqlite", registry, prior)
    token = _claim(lease)
    assert _reserve(lease, prior, token, qty=500) == "SIMULATED_ONLY"
    lease.simulated_possible_wire("intent-a")
    successor = _signed_epoch(
        private, epoch="e2", revision="r2", published=NOW + timedelta(seconds=60),
        positions={"36": 99_500},
    )
    terminal = _signed_terminal(private, cumulative=500)
    wrong = _signed_terminal(private, cumulative=200)
    assert lease.reconcile(
        prior=prior, successor=successor, terminal=(wrong,),
        now=NOW + timedelta(seconds=61),
    ).status == "UNVERIFIED"
    assert lease.reconcile(
        prior=prior, successor=successor,
        terminal=(replace(terminal, signature=b"x" * 64),),
        now=NOW + timedelta(seconds=61),
    ).status == "UNVERIFIED"
    result = lease.reconcile(
        prior=prior, successor=successor, terminal=(terminal,),
        now=NOW + timedelta(seconds=61),
    )
    assert result.status == "SIMULATED_ONLY"
    assert result.execution_authorised is False
    assert result.current_verified_headroom == "UNKNOWN"
    assert lease.reconcile(
        prior=prior, successor=successor, terminal=(terminal,),
        now=NOW + timedelta(seconds=61),
    ).status == "UNVERIFIED"
    with sqlite3.connect(lease.path) as db:
        assert db.execute("SELECT state FROM intents").fetchone()[0] == "RECONCILED"


def test_risk_002_gross_is_absolute_even_when_intent_claims_reduction(
    tmp_path: Path,
    proven: tuple[Ed25519PrivateKey, OfflineIssuerRegistry, AccountEpochProof],
) -> None:
    private, registry, _proof = proven
    gross_full = _signed_epoch(private, positions={"36": 100_000})
    lease = _seed(tmp_path / "full-cap.sqlite", registry, gross_full)
    token = _claim(lease)
    # Even if mathematically reducing: #241's maths is not venue capacity.
    answer = lease.reserve(
        proof=gross_full, holder="west", token=token, intent_id="sell",
        venue_order_id="sell-id", exchange_id="36", signed_delta=-1,
        now=NOW,
    )
    assert answer.status == "NO_CAPACITY"
    assert answer.reason == "absolute_gross_cap_or_reservation"
    assert answer.execution_authorised is False
