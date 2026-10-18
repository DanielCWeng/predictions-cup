
"""Synthetic privacy/security/continuity test; never imports live capture clients."""
from __future__ import annotations

import hashlib
import json
import time
import tracemalloc
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import pytest

from predictions_cup.research.local_private_stream_sink import (
    BRIDGE_PM_TOKENS,
    LocalRunPermit,
    PrivateStreamingSink,
    SinkDenied,
    _whole_vwap,
    verify_sealed_archive,
)

T = datetime.now(UTC).replace(microsecond=0)
P = "a" * 40


def stamp(sec: int) -> str:
    return (T + timedelta(seconds=sec)).isoformat()


def permit(**changes: object) -> LocalRunPermit:
    base = LocalRunPermit(
        run_id="synthetic_run", producer_git_sha=P,
        expires_utc=(T + timedelta(hours=23)).isoformat(),
        purge_utc=(T + timedelta(hours=25)).isoformat(),
        max_rows=20000, max_bytes=1024 * 1024,
        min_disk_free_bytes=1000, min_free_inodes=1,
    )
    return replace(base, **cast(dict[str, Any], changes))


def sig(sec: int = 0, *, revision: int = 1, previous_revision: int = 0,
        source_sequence_from: int = 1, source_sequence_through: int = 1,
        kind: str = "SIG_BOOK") -> dict[str, object]:
    value: dict[str, object] = dict(
        kind=kind, exchange_id="912", market_id="223", producer_git_sha=P,
        producer_run_id="synthetic_run", publisher_utc=stamp(sec),
        received_utc=stamp(sec), clock_uncertainty_ms=0, connection_epoch=1,
        asof_sequence=revision, native_asof_utc=stamp(sec), revision=revision,
        previous_revision=previous_revision,
        source_sequence_from=source_sequence_from,
        source_sequence_through=source_sequence_through,
    )
    if kind == "SIG_BOOK":
        value.update(bids=[["0.40", "5.5"]], asks=[["0.60", "8"]])
    return value


def pm(sec: int = 1, *, kind: str = "PM_SEED", ordinal: int = 0,
       epoch: int = 1, drop_total: int = 0) -> dict[str, object]:
    return dict(kind=kind, exchange_id="912", market_id="223", producer_git_sha=P,
                producer_run_id="synthetic_run", publisher_utc=stamp(sec),
                received_utc=stamp(sec), clock_uncertainty_ms=0,
                connection_epoch=epoch, seed_epoch=epoch, local_ordinal=ordinal,
                drop_total=drop_total, seed_complete=True,
                bids=[["0.43", "7"]], asks=[["0.63", "8"]])


def new(tmp_path: Path, **changes: object) -> PrivateStreamingSink:
    return PrivateStreamingSink(tmp_path / "private", permit(**changes), lambda _: True)


def test_default_off_creates_nothing(tmp_path: Path) -> None:
    s = PrivateStreamingSink(tmp_path / "not_created")
    assert not s.enabled
    assert not s.root.exists()
    with pytest.raises(SinkDenied, match="DEFAULT_OFF"):
        s.append(sig(), b"test")


def test_permission_and_hash_chain(tmp_path: Path) -> None:
    s = new(tmp_path)
    assert s.root.stat().st_mode & 0o777 == 0o700
    assert (s.root / "records.jsonl").stat().st_mode & 0o777 == 0o600
    assert s.append(sig(), b"sig bytes")
    s.append(pm(), b"pm seed")
    s.append(pm(2, kind="PM_CHANGE", ordinal=1), b"pm delta")
    s.close()
    assert verify_sealed_archive(s.root)
    assert s.written_bytes == (s.root / "records.jsonl").stat().st_size
    rows = [json.loads(x) for x in (s.root / "records.jsonl").read_text().splitlines()]
    assert rows[0]["row"]["original_sha256"] == hashlib.sha256(b"sig bytes").hexdigest()
    assert rows[0]["row"]["publisher_utc"] == stamp(0)
    assert json.loads((s.root / "receipt.json").read_text())["real_net"] is None
    assert (s.root / "receipt.json").stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("alter,reason", [
    (lambda row: row.update(account_token="private"), "UNEXPECTED_FIELD_OR_PII"),
    (lambda row: row.update(exchange_id="966"), "NOT_ALLOWLISTED"),
    (lambda row: row.update(publisher_utc=stamp(-3600)), "CLOCK_SOURCE_STALE"),
    (lambda row: row.update(received_utc="2026-10-18T00:00:00"), "NON_UTC_CLOCK"),
    (lambda row: row.update(bids=[["0.75", "4"]]), "CROSSED_BOOK"),
    (lambda row: row.update(bids=[["0.40", "3"], ["0.40", "3"]]), "UNSORTED_DEPTH"),
])
def test_privacy_clock_and_book_abort(tmp_path: Path, alter: Callable[[dict[str, object]], Any],
                                      reason: str) -> None:
    s = new(tmp_path)
    row = sig()
    alter(row)
    with pytest.raises(SinkDenied, match=reason):
        s.append(row, b"raw")
    assert s.aborted and not s.enabled
    assert s.rows == 0
    assert json.loads((s.root / "receipt.json").read_text())["rejected"] == 1
    with pytest.raises(SinkDenied, match="DEFAULT_OFF"):
        s.append(sig(), b"another")


def test_pm_orphan_and_reconnect_are_fatal(tmp_path: Path) -> None:
    s = new(tmp_path)
    with pytest.raises(SinkDenied, match="PM_CHANGE_BEFORE_SEED"):
        s.append(pm(kind="PM_CHANGE", ordinal=1), b"orphan")
    assert s.aborted
    s2 = PrivateStreamingSink(tmp_path / "private2", permit(), lambda _: True)
    s2.append(pm(), b"seed")
    with pytest.raises(SinkDenied, match="PM_GAP_RECONNECT"):
        s2.append(pm(1, kind="PM_CHANGE", ordinal=1, epoch=2), b"reconnect")


def test_sig_gap_and_backdated_native_abort(tmp_path: Path) -> None:
    s = new(tmp_path)
    s.append(sig(), b"sig1")
    with pytest.raises(SinkDenied, match="SIG_GAP_RECONNECT"):
        s.append(sig(1, revision=2, previous_revision=1,
                     source_sequence_from=3, source_sequence_through=3), b"sig3")
    s2 = PrivateStreamingSink(tmp_path / "private2", permit(), lambda _: True)
    s2.append(sig(), b"sig1")
    with pytest.raises(SinkDenied, match="GLOBAL_CLOCK_REGRESSION"):
        s2.append(sig(-1, revision=2, previous_revision=1,
                      source_sequence_from=2, source_sequence_through=2), b"sig2")


def test_byte_and_row_caps_fail_closed(tmp_path: Path) -> None:
    s = new(tmp_path, max_rows=1)
    s.append(sig(), b"first")
    with pytest.raises(SinkDenied, match="ROW_OR_BYTE_CAP"):
        s.append(pm(), b"second")
    s2 = PrivateStreamingSink(tmp_path / "private2", permit(max_bytes=1024), lambda _: True)
    s2.append(sig(), b"first")
    with pytest.raises(SinkDenied, match="ROW_OR_BYTE_CAP"):
        s2.append(pm(), b"second")


def test_corrupt_truncated_and_duplicate_fail(tmp_path: Path) -> None:
    s = new(tmp_path)
    s.append(sig(), b"first")
    with pytest.raises(SinkDenied, match="DUPLICATE_SOURCE_PAYLOAD"):
        s.append(sig(1, revision=2, previous_revision=1,
                     source_sequence_from=2, source_sequence_through=2), b"first")
    assert verify_sealed_archive(s.root)
    path = s.root / "records.jsonl"
    content = path.read_bytes()
    path.write_bytes(content[:-5])
    with pytest.raises(SinkDenied, match="TRUNCATED_OR_OVERSIZED_RECORD"):
        verify_sealed_archive(s.root)
    path.write_bytes(content.replace(b"0.40", b"0.41"))
    with pytest.raises(SinkDenied, match="CORRUPT_CHAIN"):
        verify_sealed_archive(s.root)


def test_free_space_and_early_expiry(tmp_path: Path) -> None:
    s = new(tmp_path, min_disk_free_bytes=2**60)
    with pytest.raises(SinkDenied, match="DISK_OR_INODE_GUARD"):
        s.append(sig(), b"first")
    s2 = PrivateStreamingSink(tmp_path / "private2", permit(), lambda _: True)
    assert s2.permit is not None
    s2.permit = replace(s2.permit, expires_utc=(T - timedelta(seconds=1)).isoformat())
    with pytest.raises(SinkDenied, match="PERMIT_EXPIRED"):
        s2.append(sig(), b"first")


def test_purge_requires_deadline(tmp_path: Path) -> None:
    s = new(tmp_path)
    s.append(sig(), b"first")
    s.close()
    with pytest.raises(SinkDenied, match="PURGE_NOT_DUE"):
        s.purge_after_deadline(T)
    assert s.purge_after_deadline(T + timedelta(hours=30))
    assert not s.root.exists()


def test_10k_synthetic_burst_bounded_memory(tmp_path: Path) -> None:
    s = new(tmp_path, max_rows=10001, max_bytes=16 * 1024 * 1024)
    tracemalloc.start()
    started = time.perf_counter()
    for i in range(10000):
        s.append(sig(2 * i, revision=i + 1, previous_revision=i,
                     source_sequence_from=i + 1, source_sequence_through=i + 1),
                 f"invented issuer blob {i}".encode())
    elapsed = time.perf_counter() - started
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    s.close()
    assert verify_sealed_archive(s.root)
    assert s.rows == 10000
    assert peak < 8 * 1024 * 1024, f"peak allocated memory {peak}"
    print(f"SYNTHETIC_BURST rows={s.rows} sec={elapsed:.3f} rows_per_sec={s.rows/elapsed:.1f}"
          f" bytes={s.written_bytes} peak_python_alloc={peak}")


def test_typed_reference_only_economics_are_not_real_net(tmp_path: Path) -> None:
    s = new(tmp_path)
    s.append(pm(0), b"pm0")
    s.append(pm(1, kind="PM_CHANGE", ordinal=1), b"pm1")
    s.append(sig(2), b"sig2")
    rows = s.reference_economic_rows("912")
    assert tuple(x.whole_shares for x in rows) == (1, 5, 10, 20)
    assert rows[0].verdict == "REFERENCE_ONLY_NOT_RESTING_PROOF"
    assert rows[0].yes_reference_gap == "-0.17"
    assert rows[0].no_reference_gap == "-0.23"
    assert rows[1].verdict == "REFERENCE_ONLY_NOT_RESTING_PROOF"
    assert rows[2].yes_reference_gap is None  # 10 whole shares exceed real level qty
    assert rows[3].verdict == "SOURCE_OR_WHOLE_DEPTH_UNKNOWN"
    assert all(x.arrival_250ms_observed is None and x.real_net is None
               and not x.source_admitted and not x.paper_admitted for x in rows)
    s.close()


def test_missing_or_noncausal_pm_reference_abstains(tmp_path: Path) -> None:
    s = new(tmp_path)
    s.append(sig(0), b"sig")
    assert all(x.yes_reference_gap is None for x in s.reference_economic_rows("912"))
    s.append(pm(1), b"seed")
    s.append(pm(2, kind="PM_CHANGE", ordinal=1), b"change")
    assert all(x.no_reference_gap is None for x in s.reference_economic_rows("912"))


def test_fake_verifier_and_unsafe_parent_rejected(tmp_path: Path) -> None:
    with pytest.raises(SinkDenied, match="PERMIT_NOT_INDEPENDENTLY_VERIFIED"):
        PrivateStreamingSink(tmp_path / "blocked", permit(), lambda _: False)
    assert not (tmp_path / "blocked").exists()
    bad = tmp_path / "open_parent"
    bad.mkdir(mode=0o777)
    bad.chmod(0o777)
    with pytest.raises(SinkDenied, match="PARENT_WRITABLE_BY_OTHERS"):
        PrivateStreamingSink(bad / "nope", permit(), lambda _: True)
    assert not (bad / "nope").exists()

# Versioned PR319 SourceBridge.emit(dict) ABI, intentionally invented events.
# These are private-sink shape tests, NOT original issuer packet authentication.

def bridge_sig(
    sec: int = 0, *, revision: int = 1, previous_revision: int = 0,
    through: int | None = None, gap: bool = True, native: bool = True,
) -> dict[str, object]:
    books: list[dict[str, object]] = []
    if native:
        books = [{"asof_at": stamp(sec), "asof_sequence": revision,
                  "full_depth": {"bids": [["0.40", "10"]],
                                 "asks": [["0.60", "10"]],
                                 "complete_source_claim": True}}]
    return {
        "schema": "alpha2-source-bridge-v1", "venue": "SIG", "kind": "BATCH",
        "producer_sha": P, "issuer_authenticated": False,
        "source_admitted": False, "paper_admitted": False,
        "market_id": "223", "exchange_id": "912",
        "tournament_id": "bda92870-621e-47b0-bc3c-3602c5c26f55",
        "revision": revision, "previous_revision": previous_revision,
        "source_sequence_from": revision, "source_sequence_through": through or revision,
        "connection_epoch": 1, "received_at": stamp(sec),
        "clock_uncertainty_ms": 0, "raw_frame_sha256": None,
        "wire_bytes_verified": False, "gap_or_unanchored": gap,
        "full_books": books,
    }


def bridge_seed(sec: int = 0) -> dict[str, object]:
    return {
        "schema": "alpha2-source-bridge-v1", "venue": "PM",
        "kind": "REST_SEED_COMPLETE", "producer_sha": P,
        "issuer_authenticated": False, "source_admitted": False,
        "paper_admitted": False, "token_id": BRIDGE_PM_TOKENS["912"],
        "connection_epoch": 1, "received_at": stamp(sec),
        "ws_local_ordinal": 0, "clock_uncertainty_ms": 0,
        "raw_response_sha256": None, "wire_bytes_verified": False,
        "full_depth": {"bids": [["0.43", "10"]],
                       "asks": [["0.63", "10"]],
                       "complete_source_claim": True},
    }


def bridge_ws(sec: int = 1, ordinal: int = 1, *, changed: bool = True) -> dict[str, object]:
    token = BRIDGE_PM_TOKENS["912"]
    return {
        "schema": "alpha2-source-bridge-v1", "venue": "PM",
        "kind": "WS_EVENT", "producer_sha": P,
        "issuer_authenticated": False, "source_admitted": False,
        "paper_admitted": False, "connection_epoch": 1,
        "ws_local_ordinal": ordinal, "received_at": stamp(sec),
        "clock_uncertainty_ms": 0, "raw_frame_sha256": None,
        "wire_bytes_verified": False, "gap_or_unanchored": False,
        "affected_tokens": [token] if changed else [],
        "uninitialized_tokens": [], "abstain_tokens": [],
        "full_books": {token: bridge_seed()["full_depth"]} if changed else {},
    }


def test_bridge_nullable_wire_hash_and_local_hash_never_source_identity(tmp_path: Path) -> None:
    s = new(tmp_path)
    s.append_bridge(bridge_sig())
    s.append_bridge(bridge_seed(1))
    s.append_bridge(bridge_ws(2))
    s.close()
    assert s.rows == 3 and verify_sealed_archive(s.root)
    rows = [json.loads(x)["row"] for x in (s.root / "records.jsonl").read_text().splitlines()]
    assert rows[0]["raw_frame_sha256"] is None
    assert rows[1]["raw_response_sha256"] is None
    assert all(row["issuer_authenticated"] is False and
               row["source_admitted"] is False and row["paper_admitted"] is False
               for row in rows)
    for row in rows:
        local = row.pop("local_canonical_sha256")
        canonical = json.dumps(row, sort_keys=True, separators=(",", ":"),
                               ensure_ascii=True, allow_nan=False).encode("ascii")
        assert hashlib.sha256(canonical).hexdigest() == local
    receipt = json.loads((s.root / "receipt.json").read_text())
    assert receipt["abi_mode"] == "bridge" and receipt["issuer_authenticated"] is False


def test_bridge_nonbook_sig_and_unchanged_pm_ordinals_are_preserved(tmp_path: Path) -> None:
    s = new(tmp_path)
    s.append_bridge(bridge_sig(native=False))  # unanchored first topic marker
    s.append_bridge(bridge_sig(1, revision=2, previous_revision=1,
                               gap=False, native=False))
    s.append_bridge(bridge_seed(2))
    s.append_bridge(bridge_ws(3, 1, changed=False))
    s.append_bridge(bridge_ws(4, 2, changed=True))
    assert all(v.real_net is None and v.yes_reference_gap is None
               for v in s.reference_economic_rows("912"))
    s.close()
    assert s.rows == 5 and verify_sealed_archive(s.root)
    assert json.loads((s.root / "receipt.json").read_text()) == {
        **json.loads((s.root / "receipt.json").read_text()),
        "bridge_unanchored": 1, "bridge_nonbook": 2, "bridge_unchanged_ws": 1,
    }
    kinds = [json.loads(x)["row"]["kind"] for x in
             (s.root / "records.jsonl").read_text().splitlines()]
    assert kinds == ["BATCH", "BATCH", "REST_SEED_COMPLETE", "WS_EVENT", "WS_EVENT"]


def test_bridge_lost_sig_delivery_is_fatal(tmp_path: Path) -> None:
    s = new(tmp_path)
    s.append_bridge(bridge_sig())
    with pytest.raises(SinkDenied, match="SIG_SOURCE_GAP_OR_RECONNECT"):
        s.append_bridge(bridge_sig(1, revision=3, previous_revision=2, gap=True))
    assert s.aborted and s.rows == 1
    with pytest.raises(SinkDenied, match="DEFAULT_OFF"):
        s.append_bridge(bridge_sig(2, revision=4, previous_revision=3, gap=False))


def test_bridge_regressed_native_clock_is_fatal(tmp_path: Path) -> None:
    s = new(tmp_path)
    s.append_bridge(bridge_sig(1))
    with pytest.raises(SinkDenied, match="SIG_NATIVE_ASOF_REGRESSION"):
        s.append_bridge(bridge_sig(1, revision=2, previous_revision=1, gap=False))
    assert s.aborted


def test_bridge_unseeded_orphan_pm_ws_fails(tmp_path: Path) -> None:
    s = new(tmp_path)
    with pytest.raises(SinkDenied, match="PM_WS_ORDINAL_GAP_OR_RECONNECT"):
        s.append_bridge(bridge_ws())
    assert s.rows == 0 and s.aborted


def test_bridge_pm_missing_ordinal_and_uninitialized_abort(tmp_path: Path) -> None:
    s = new(tmp_path)
    s.append_bridge(bridge_seed())
    with pytest.raises(SinkDenied, match="PM_WS_ORDINAL_GAP_OR_RECONNECT"):
        s.append_bridge(bridge_ws(1, 2))
    assert s.aborted
    s2 = PrivateStreamingSink(tmp_path / "private2", permit(), lambda _: True)
    s2.append_bridge(bridge_seed())
    row = bridge_ws(1, 1)
    row["uninitialized_tokens"] = [BRIDGE_PM_TOKENS["912"]]
    with pytest.raises(SinkDenied, match="PM_UNSEEDED_OR_MISSING_BOOK"):
        s2.append_bridge(row)


def test_bridge_disconnect_recorded_then_poisoned(tmp_path: Path) -> None:
    s = new(tmp_path)
    s.append_bridge(bridge_seed())
    row = {
        "schema": "alpha2-source-bridge-v1", "venue": "PM", "kind": "DISCONNECT",
        "producer_sha": P, "issuer_authenticated": False,
        "source_admitted": False, "paper_admitted": False,
        "connection_epoch": 1, "received_at": stamp(1),
        "ws_local_ordinal": 0, "full_books": {}, "gap_or_unanchored": True,
    }
    with pytest.raises(SinkDenied, match="PM_DISCONNECT_ABORT"):
        s.append_bridge(row)
    assert s.rows == 2 and s.aborted and verify_sealed_archive(s.root)
    final_row = json.loads((s.root / "records.jsonl").read_text().splitlines()[-1])
    assert final_row["row"]["kind"] == "DISCONNECT"


@pytest.mark.parametrize("field,value,reason", [
    ("account_id", "private", "UNEXPECTED_FIELD_OR_PII"),
    ("wire_bytes_verified", True, "WIRE_VERIFICATION_UNPROVEN"),
    ("issuer_authenticated", True, "UNSUPPORTED_TRUST_PROMOTION"),
    ("raw_frame_sha256", "raw-not-original", "UNVERIFIED_RAW_DIGEST_FORMAT"),
    ("full_books", [{"asof_at": stamp(-100), "asof_sequence": 1,
                     "full_depth": (
                         cast(list[dict[str, object]], bridge_sig()["full_books"])[0]["full_depth"]
                     )}],
     "SIG_NATIVE_SOURCE_CLOCK_STALE_OR_FUTURE"),
])
def test_bridge_strict_privacy_wire_clock(tmp_path: Path, field: str, value: object,
                                          reason: str) -> None:
    s = new(tmp_path)
    row = bridge_sig()
    row[field] = value
    with pytest.raises(SinkDenied, match=reason):
        s.append_bridge(row)
    assert s.aborted and s.rows == 0


def test_bridge_cannot_mix_legacy_and_native_abi(tmp_path: Path) -> None:
    s = new(tmp_path)
    s.append_bridge(bridge_sig())
    with pytest.raises(SinkDenied, match="MIXED_ABI_DENIED"):
        s.append(sig(1), b"invented")
    assert s.aborted


def test_bridge_sink_write_guard_error_is_irreversible(tmp_path: Path,
                                                        monkeypatch: pytest.MonkeyPatch) -> None:
    s = new(tmp_path)

    def deny_space(_: int) -> None:
        raise OSError("INVENTED_DISK_FAULT")

    monkeypatch.setattr(s, "_resources", deny_space)
    with pytest.raises(SinkDenied, match="BRIDGE_IO_OR_INVALID_RECORD"):
        s.append_bridge(bridge_sig())
    assert s.aborted and s.rows == 0


def test_bridge_bounded_burst_synthetic(tmp_path: Path) -> None:
    s = new(tmp_path, max_rows=5000, max_bytes=8 * 1024 * 1024)
    start = time.perf_counter()
    tracemalloc.start()
    for i in range(4000):
        s.append_bridge(bridge_sig(i, revision=i + 1,
                                   previous_revision=i, gap=(i == 0), native=False))
    elapsed = time.perf_counter() - start
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    s.close()
    assert verify_sealed_archive(s.root)
    assert s.rows == 4000 and peak < 8 * 1024 * 1024
    print(f"SYNTH_BRIDGE_BURST count={s.rows} bytes={s.written_bytes} "
          f"elapsed_s={elapsed:.3f} throughput={s.rows/elapsed:.1f} peak_py={peak}")


def test_offline_source_to_sink_smoke(tmp_path: Path) -> None:
    """Synthetic PR319-v1 emit(dict)->sink contract and zero-fee sized references.

    This intentionally uses the PR319-shaped event factories because PR319 is
    a separate unmerged PR, NOT an import available on PR320's base branch.
    It is an ABI smoke, not an issuer-authenticated live bridge integration.
    """
    sink = new(tmp_path)
    emit: Callable[[dict[str, object]], None] = sink.append_bridge
    sig_book = {
        "bids": [["0.49", "9"], ["0.47", "20"]],
        "asks": [["0.51", "9"], ["0.53", "20"]],
        "complete_source_claim": True,
    }
    pm_book = {
        "bids": [["0.55", "9"], ["0.54", "20"]],
        "asks": [["0.57", "9"], ["0.58", "20"]],
        "complete_source_claim": True,
    }
    seed = bridge_seed(0)
    seed["full_depth"] = pm_book
    emit(seed)
    change = bridge_ws(1)
    change["full_books"] = {BRIDGE_PM_TOKENS["912"]: pm_book}
    emit(change)
    emit(bridge_ws(2, 2, changed=False))
    emit(bridge_sig(3, native=False))
    sig_full = bridge_sig(4, revision=2, previous_revision=1, gap=False)
    cast(list[dict[str, object]], sig_full["full_books"])[0]["full_depth"] = sig_book
    emit(sig_full)
    # Synthetic reference gaps are diagnostic only, NOT executable at-arrival
    # venue observations. Source/PAPER/real-net remain unadmitted and NULL.
    assert all(row.real_net is None and row.yes_reference_gap is None
               for row in sink.reference_economic_rows("912"))
    sink.close()
    assert verify_sealed_archive(sink.root)
    receipt = json.loads((sink.root / "receipt.json").read_text())
    stored = [json.loads(line)["row"] for line in
              (sink.root / "records.jsonl").read_text().splitlines()]
    assert receipt["rows"] == 5 and receipt["rejected"] == 0
    assert receipt["bridge_unanchored"] == 1
    assert receipt["bridge_nonbook"] == 1 and receipt["bridge_unchanged_ws"] == 1
    assert all(not row["issuer_authenticated"] and not row["source_admitted"]
               and not row["paper_admitted"] for row in stored)
    observed_pm = stored[1]["full_books"][BRIDGE_PM_TOKENS["912"]]
    observed_sig = stored[4]["full_books"][0]["full_depth"]
    expected = {1: ("0.04", "-0.08"), 5: ("0.04", "-0.08"),
                10: ("0.037", "-0.083"), 20: ("0.0235", "-0.0965")}
    print(f"OFFLINE_SOURCE_SINK rows={receipt['rows']} rejected={receipt['rejected']} "
          f"gap={receipt['bridge_unanchored']} unchanging_ws={receipt['bridge_unchanged_ws']}")
    print("shares yes_bid_minus_sig_ask sig_bid_minus_pm_ask sig_fee real_net")
    for n, expected_pair in expected.items():
        sig_bid = _whole_vwap(observed_sig["bids"], n)
        sig_ask = _whole_vwap(observed_sig["asks"], n)
        pm_bid = _whole_vwap(observed_pm["bids"], n)
        pm_ask = _whole_vwap(observed_pm["asks"], n)
        assert sig_bid is not None and sig_ask is not None
        assert pm_bid is not None and pm_ask is not None
        yes = str(pm_bid - sig_ask)
        no = str(sig_bid - pm_ask)
        assert (yes, no) == expected_pair
        print(f"{n} {yes} {no} 0 NULL")


def test_bridge_pm_equal_or_ambiguous_receipt_must_abort(tmp_path: Path) -> None:
    s = new(tmp_path)
    seed = bridge_seed(0)
    seed["clock_uncertainty_ms"] = 100
    unchanged = bridge_ws(0, 1, changed=False)
    unchanged["clock_uncertainty_ms"] = 100
    s.append_bridge(seed)
    with pytest.raises(SinkDenied, match="PM_RECEIPT_NONMONOTONIC_OR_AMBIGUOUS"):
        s.append_bridge(unchanged)
    assert s.aborted and s.rows == 1
    assert verify_sealed_archive(s.root)
    receipt = json.loads((s.root / "receipt.json").read_text())
    assert receipt["abort_reason"] == "PM_RECEIPT_NONMONOTONIC_OR_AMBIGUOUS"


@pytest.mark.parametrize("changed", [False, True])
def test_bridge_pm_equal_receipt_changed_or_unchanged_fails(
    tmp_path: Path, changed: bool
) -> None:
    s = new(tmp_path)
    seed = bridge_seed(0)
    seed["clock_uncertainty_ms"] = 100
    s.append_bridge(seed)
    event = bridge_ws(0, 1, changed=changed)
    event["clock_uncertainty_ms"] = 100
    with pytest.raises(SinkDenied, match="PM_RECEIPT_NONMONOTONIC_OR_AMBIGUOUS"):
        s.append_bridge(event)
    assert s.aborted and s.rows == 1 and verify_sealed_archive(s.root)


def test_bridge_pm_overlap_increasing_timestamp_fails(tmp_path: Path) -> None:
    s = new(tmp_path)
    seed = bridge_seed(0)
    seed["clock_uncertainty_ms"] = 100
    s.append_bridge(seed)
    event = bridge_ws(1)
    event["clock_uncertainty_ms"] = 100
    event["received_at"] = (T + timedelta(milliseconds=150)).isoformat()
    with pytest.raises(SinkDenied, match="PM_RECEIPT_NONMONOTONIC_OR_AMBIGUOUS"):
        s.append_bridge(event)
    assert s.rows == 1 and s.aborted and verify_sealed_archive(s.root)


def test_bridge_pm_two_seeds_overlapping_rejected(tmp_path: Path) -> None:
    s = new(tmp_path)
    seed = bridge_seed(0)
    seed["clock_uncertainty_ms"] = 100
    s.append_bridge(seed)
    second = bridge_seed(0)
    second["token_id"] = BRIDGE_PM_TOKENS["913"]
    second["clock_uncertainty_ms"] = 100
    with pytest.raises(SinkDenied, match="PM_RECEIPT_NONMONOTONIC_OR_AMBIGUOUS"):
        s.append_bridge(second)
    assert s.rows == 1 and s.aborted and verify_sealed_archive(s.root)


def test_bridge_pm_ws_to_ws_ambiguous_receipt_rejected(tmp_path: Path) -> None:
    s = new(tmp_path)
    seed = bridge_seed(0)
    seed["clock_uncertainty_ms"] = 100
    s.append_bridge(seed)
    first = bridge_ws(1, 1, changed=False)
    first["clock_uncertainty_ms"] = 100
    s.append_bridge(first)
    second = bridge_ws(1, 2, changed=False)
    second["clock_uncertainty_ms"] = 100
    with pytest.raises(SinkDenied, match="PM_RECEIPT_NONMONOTONIC_OR_AMBIGUOUS"):
        s.append_bridge(second)
    assert s.rows == 2 and s.aborted and verify_sealed_archive(s.root)


def test_bridge_pm_ambiguous_disconnect_fails_before_terminal_write(tmp_path: Path) -> None:
    s = new(tmp_path)
    seed = bridge_seed(0)
    seed["clock_uncertainty_ms"] = 100
    s.append_bridge(seed)
    disconnect = {
        "schema": "alpha2-source-bridge-v1", "venue": "PM", "kind": "DISCONNECT",
        "producer_sha": P, "issuer_authenticated": False,
        "source_admitted": False, "paper_admitted": False,
        "connection_epoch": 1, "received_at": stamp(0),
        "ws_local_ordinal": 0, "full_books": {}, "gap_or_unanchored": True,
    }
    with pytest.raises(SinkDenied, match="PM_RECEIPT_NONMONOTONIC_OR_AMBIGUOUS"):
        s.append_bridge(disconnect)
    assert s.rows == 1 and s.aborted and verify_sealed_archive(s.root)
    assert "DISCONNECT" not in (s.root / "records.jsonl").read_text()


def test_bridge_pm_strict_separation_all_events_and_equal_sig_allowed(tmp_path: Path) -> None:
    s = new(tmp_path)
    first = bridge_seed(0)
    first["clock_uncertainty_ms"] = 100
    s.append_bridge(first)
    # SIG equal global timestamp is allowed; PM strictness is independent.
    s.append_bridge(bridge_sig(0, native=False))
    second = bridge_seed(1)
    second["token_id"] = BRIDGE_PM_TOKENS["913"]
    second["clock_uncertainty_ms"] = 100
    s.append_bridge(second)
    unchanged = bridge_ws(2, 1, changed=False)
    unchanged["clock_uncertainty_ms"] = 100
    s.append_bridge(unchanged)
    changed = bridge_ws(3, 2)
    changed["clock_uncertainty_ms"] = 100
    s.append_bridge(changed)
    disconnect = {
        "schema": "alpha2-source-bridge-v1", "venue": "PM", "kind": "DISCONNECT",
        "producer_sha": P, "issuer_authenticated": False,
        "source_admitted": False, "paper_admitted": False,
        "connection_epoch": 1, "received_at": stamp(4),
        "ws_local_ordinal": 2, "full_books": {}, "gap_or_unanchored": True,
    }
    with pytest.raises(SinkDenied, match="PM_DISCONNECT_ABORT"):
        s.append_bridge(disconnect)
    assert s.rows == 6 and s.aborted and verify_sealed_archive(s.root)
    receipt = json.loads((s.root / "receipt.json").read_text())
    assert receipt["bridge_unchanged_ws"] == 1


def test_bridge_pm_zero_uncertainty_requires_strict_order(tmp_path: Path) -> None:
    s = new(tmp_path)
    s.append_bridge(bridge_seed(0))
    s.append_bridge(bridge_ws(1, 1, changed=False))
    s.close()
    assert s.rows == 2 and verify_sealed_archive(s.root)
