"""Synthetic-only negative controls for the offline paired depth schema gate."""

from __future__ import annotations

import hashlib
import json
import socket
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from predictions_cup.research.offline_paired_tape_gate import GateResult, validate_offline_tape

_EDIT = Callable[[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]], None]


def _sample(
    tmp_path: Path, edit: _EDIT | None = None,
) -> tuple[tuple[Path, Path, Path, Path], str]:
    mapping: dict[str, Any] = {
        "mapping_class": "EXACT", "mapping_version": "fixture-v1",
        "mapping_direction": "SAME", "sig_exchange_id": "879",
        "sig_market_id": "m-879", "sig_outcome": "YES",
        "pm_condition_id": "condition-a", "pm_token_id": "pm-yes",
        "pm_outcome": "YES",
    }
    sig: dict[str, Any] = {
        "interval_id": "interval-1", "run_id": "sig-run-1",
        "exchange_id": "879", "market_id": "m-879", "outcome": "YES",
        "full_depth": True, "clock_domain": "UTC", "publisher_clock_attested": True,
        "source_at": "2026-10-16T12:00:00Z", "source_sequence": 51,
        "received_at": "2026-10-16T12:00:00.100Z",
        "decision_at": "2026-10-16T12:00:00.500Z", "clock_uncertainty_ms": 40,
        "bids": [{"price": "0.40", "size": "20"}],
        "asks": [{"price": "0.60", "size": "23"}],
    }
    pm: dict[str, Any] = {
        "interval_id": "interval-1", "run_id": "pm-run-1",
        "token_id": "pm-yes", "condition_id": "condition-a", "outcome": "YES",
        "full_depth": True, "clock_domain": "UTC", "publisher_clock_attested": True,
        "source_at": "2026-10-16T12:00:00.050Z", "ws_sequence": 8,
        "received_at": "2026-10-16T12:00:00.200Z",
        "decision_at": "2026-10-16T12:00:00.500Z", "clock_uncertainty_ms": 40,
        "last_full_book_at": "2026-10-16T12:00:00Z",
        "seed_status": "COMPLETE", "book_initialized": True,
        "seed_generation": 3, "connection_seed_generation": 3,
        "subscription_epoch": 7, "connection_subscription_epoch": 7,
        "ws_contiguous": True, "reconnect_gap": False, "book_hash": "a" * 64,
        "bids": [{"price": "0.43", "size": "11"}],
        "asks": [{"price": "0.57", "size": "14"}],
    }
    manifest: dict[str, Any] = {
        "schema": "offline-paired-depth-schema-v1", "archive_complete": True,
        "source_type": "OPERATOR_EXPORTED_OFFLINE_BYTES",
        "issuer": "SYNTHETIC-TEST-ONLY", "permission_ref": "fixture-only-not-a-real-grant",
        "permitted_use": "OFFLINE_SCHEMA_CHECK_ONLY",
        "producers": {
            "sig": {"source_type": "SIG_FULL_DEPTH_EXPORT", "issuer": "fixture-sig",
                    "producer_git_sha": "1" * 40, "run_id": "sig-run-1"},
            "pm": {"source_type": "PM_FULL_DEPTH_EXPORT", "issuer": "fixture-pm",
                   "producer_git_sha": "2" * 40, "run_id": "pm-run-1"},
        },
        "max_age_ms": 2000, "max_clock_uncertainty_ms": 100, "interval_count": 1,
    }
    if edit is not None:
        edit(mapping, sig, pm, manifest)
    blobs = {
        "mapping": json.dumps(mapping, sort_keys=True).encode(),
        "sig": (json.dumps(sig, sort_keys=True) + "\n").encode(),
        "pm": (json.dumps(pm, sort_keys=True) + "\n").encode(),
    }
    manifest["file_sha256"] = {
        key: hashlib.sha256(value).hexdigest() for key, value in blobs.items()
    }
    mf = tmp_path / "manifest.json"
    mapping_file, sig_file, pm_file = (tmp_path / f"{kind}.jsonl" for kind in blobs)
    mf_bytes = json.dumps(manifest, sort_keys=True).encode()
    mf.write_bytes(mf_bytes)
    for kind, path in zip(blobs, (mapping_file, sig_file, pm_file), strict=True):
        path.write_bytes(blobs[kind])
    return (mf, mapping_file, sig_file, pm_file), hashlib.sha256(mf_bytes).hexdigest()


def _run(tmp_path: Path, edit: _EDIT | None = None) -> GateResult:
    paths, expected = _sample(tmp_path, edit)
    return validate_offline_tape(*paths, expected_manifest_sha256=expected)


def _blocked(result: GateResult, reason: str) -> None:
    assert result.verdict == "PAIRED_DEPTH_NOT_CERTIFIED"
    assert result.schema_valid_intervals == 0
    assert result.source_admitted is False
    assert result.paper_admitted is False
    assert result.network_requests == 0
    assert reason in [item[1] for item in result.rejections]


def test_complete_synthetic_shape_never_promotes_paper_or_source(tmp_path: Path) -> None:
    result = _run(tmp_path)
    assert result.verdict == "SCHEMA_ONLY/PAPER_NOT_ADMITTED"
    assert result.schema_valid_intervals == 1
    assert not result.rejections
    assert result.external_issuer_authenticated is False
    assert result.source_admitted is False
    assert result.paper_admitted is False
    assert result.network_requests == 0


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("source_at", None, "MIXED_OR_MISSING_UTC_CLOCK"),
        ("source_sequence", None, "MISSING_SIG_NATIVE_REVISION"),
        ("publisher_clock_attested", False, "PUBLISHER_CLOCK_UNATTESTED"),
        ("full_depth", False, "MISSING_FULL_DEPTH"),
        ("bids", [], "MISSING_FULL_DEPTH"),
        ("clock_domain", "UTC-MONOTONIC", "MIXED_OR_MISSING_UTC_CLOCK"),
        ("source_at", "2026-10-16T13:00:00Z", "NONCAUSAL_CLOCK_ORDER"),
        ("source_at", "2026-10-16T11:59:00Z", "STALE_OR_UNAVAILABLE_BOOK"),
        ("decision_at", "2026-10-16T12:00:01+01:00", "MIXED_OR_MISSING_UTC_CLOCK"),
        ("clock_uncertainty_ms", 500, "CLOCK_UNCERTAINTY_BREACH"),
        ("outcome", "NO", "SIG_MAPPING_OR_SIDE_MISMATCH"),
    ],
)
def test_sig_missing_clocks_depth_and_wrong_side_are_denied(
    tmp_path: Path, field: str, value: Any, reason: str,
) -> None:
    def edit(_mapping: dict[str, Any], sig: dict[str, Any],
             _pm: dict[str, Any], _manifest: dict[str, Any]) -> None:
        sig[field] = value

    _blocked(_run(tmp_path, edit), reason)


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("seed_status", "FAILED", "PM_SEED_NOT_COMPLETE"),
        ("book_initialized", False, "PM_SEED_NOT_COMPLETE"),
        ("seed_generation", 8, "PM_SEED_EPOCH_MISMATCH"),
        ("connection_subscription_epoch", 9, "PM_SEED_EPOCH_MISMATCH"),
        ("ws_contiguous", False, "PM_WS_CONTINUITY_LOST"),
        ("reconnect_gap", True, "PM_WS_CONTINUITY_LOST"),
        ("ws_sequence", None, "MISSING_PM_WS_SEQUENCE"),
        ("publisher_clock_attested", False, "PUBLISHER_CLOCK_UNATTESTED"),
        ("book_hash", None, "PM_BOOK_HASH_MISSING"),
        ("source_at", None, "MIXED_OR_MISSING_UTC_CLOCK"),
        ("decision_at", "2026-10-16T12:00:00.800Z", "NO_SAME_CLOCK_ASOF_OVERLAP"),
        ("outcome", "NO", "PM_MAPPING_OR_SIDE_MISMATCH"),
        ("last_full_book_at", "2026-10-16T12:01:00Z", "PM_FULL_SEED_IN_FUTURE"),
    ],
)
def test_pm_seed_continuity_clock_book_hash_and_side_are_denied(
    tmp_path: Path, field: str, value: Any, reason: str,
) -> None:
    def edit(_mapping: dict[str, Any], _sig: dict[str, Any],
             pm: dict[str, Any], _manifest: dict[str, Any]) -> None:
        pm[field] = value

    _blocked(_run(tmp_path, edit), reason)


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("mapping_class", "SOFT", "INVALID_MAPPING"),
        ("mapping_direction", "COMPLEMENT", "MAPPING_ORIENTATION_INVALID"),
        ("pm_condition_id", "", "INVALID_MAPPING"),
        ("pm_token_id", "different-pm-token", "PM_MAPPING_OR_SIDE_MISMATCH"),
    ],
)
def test_mapping_is_exact_stable_and_orientation_checked(
    tmp_path: Path, field: str, value: Any, reason: str,
) -> None:
    def edit(mapping: dict[str, Any], _sig: dict[str, Any],
             _pm: dict[str, Any], _manifest: dict[str, Any]) -> None:
        mapping[field] = value

    _blocked(_run(tmp_path, edit), reason)


def test_complement_orientation_can_be_schema_only(tmp_path: Path) -> None:
    def edit(mapping: dict[str, Any], _sig: dict[str, Any], pm: dict[str, Any],
             _manifest: dict[str, Any]) -> None:
        mapping["mapping_direction"] = "COMPLEMENT"
        mapping["pm_outcome"] = "NO"
        pm["outcome"] = "NO"

    result = _run(tmp_path, edit)
    assert result.verdict == "SCHEMA_ONLY/PAPER_NOT_ADMITTED"
    assert result.source_admitted is False


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("archive_complete", False, "PARTIAL_ARCHIVE"),
        ("interval_count", 2, "PARTIAL_ARCHIVE"),
        ("permission_ref", "", "MISSING_PERMITTED_USE_EVIDENCE"),
        ("permitted_use", "LIVE_TRADE", "MISSING_PERMITTED_USE_EVIDENCE"),
        ("issuer", "", "MISSING_ISSUER"),
        ("source_type", "LIVE_FEED", "BAD_SOURCE_TYPE"),
    ],
)
def test_manifest_incomplete_permission_and_issuer_block_whole_tape(
    tmp_path: Path, field: str, value: Any, reason: str,
) -> None:
    def edit(_mapping: dict[str, Any], _sig: dict[str, Any],
             _pm: dict[str, Any], manifest: dict[str, Any]) -> None:
        manifest[field] = value

    _blocked(_run(tmp_path, edit), reason)


def test_unpinned_or_changed_input_bytes_fail_closed(tmp_path: Path) -> None:
    paths, manifest_hash = _sample(tmp_path)
    _blocked(validate_offline_tape(*paths, expected_manifest_sha256="0" * 64),
             "MANIFEST_HASH_MISMATCH")
    paths[2].write_bytes(paths[2].read_bytes() + b"corrupted\n")
    _blocked(validate_offline_tape(*paths, expected_manifest_sha256=manifest_hash),
             "SIG_INPUT_HASH_MISMATCH")


def test_truncated_archive_rejects_all_even_with_updated_hash(tmp_path: Path) -> None:
    paths, _hash = _sample(tmp_path)
    pm_bytes = paths[3].read_bytes().rstrip(b"\n")
    paths[3].write_bytes(pm_bytes)
    manifest = json.loads(paths[0].read_text())
    manifest["file_sha256"]["pm"] = hashlib.sha256(pm_bytes).hexdigest()
    mf_bytes = json.dumps(manifest, sort_keys=True).encode()
    paths[0].write_bytes(mf_bytes)
    _blocked(validate_offline_tape(*paths,
                                   expected_manifest_sha256=hashlib.sha256(mf_bytes).hexdigest()),
             "PARTIAL_ARCHIVE")


def test_no_socket_or_network_even_for_well_formed_synthetic_fixture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    def deny(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("NETWORK_CALL_FORBIDDEN")

    monkeypatch.setattr(socket.socket, "connect", deny)
    monkeypatch.setattr(socket, "create_connection", deny)
    assert _run(tmp_path).verdict == "SCHEMA_ONLY/PAPER_NOT_ADMITTED"


def _two_interval_schema(
    tmp_path: Path, *, second_sig_sequence: int, second_pm_sequence: int,
) -> GateResult:
    paths, _ = _sample(tmp_path)
    manifest_path, _, sig_path, pm_path = paths
    sig_first = json.loads(sig_path.read_text(encoding="utf-8"))
    pm_first = json.loads(pm_path.read_text(encoding="utf-8"))
    sig_next = dict(sig_first)
    pm_next = dict(pm_first)
    sig_next.update({
        "interval_id": "interval-2", "source_at": "2026-10-16T12:00:00.600Z",
        "received_at": "2026-10-16T12:00:00.700Z",
        "decision_at": "2026-10-16T12:00:01.000Z",
        "source_sequence": second_sig_sequence,
    })
    pm_next.update({
        "interval_id": "interval-2", "source_at": "2026-10-16T12:00:00.650Z",
        "received_at": "2026-10-16T12:00:00.800Z",
        "decision_at": "2026-10-16T12:00:01.000Z",
        "ws_sequence": second_pm_sequence,
    })
    sig_path.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in (sig_first, sig_next)),
        encoding="utf-8",
    )
    pm_path.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in (pm_first, pm_next)),
        encoding="utf-8",
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["interval_count"] = 2
    manifest["file_sha256"]["sig"] = hashlib.sha256(sig_path.read_bytes()).hexdigest()
    manifest["file_sha256"]["pm"] = hashlib.sha256(pm_path.read_bytes()).hexdigest()
    raw = json.dumps(manifest, sort_keys=True).encode()
    manifest_path.write_bytes(raw)
    return validate_offline_tape(
        *paths, expected_manifest_sha256=hashlib.sha256(raw).hexdigest(),
    )


@pytest.mark.parametrize(
    ("sig_sequence", "pm_sequence", "reason"),
    [
        (52, 7, "PM_WS_SEQUENCE_NOT_INCREASING"),
        (50, 9, "SIG_NATIVE_SEQUENCE_NOT_INCREASING"),
        (51, 9, "SIG_NATIVE_SEQUENCE_NOT_INCREASING"),
        (52, 8, "PM_WS_SEQUENCE_NOT_INCREASING"),
    ],
)
def test_same_run_native_and_ws_sequences_never_reset_or_repeat(
    tmp_path: Path, sig_sequence: int, pm_sequence: int, reason: str,
) -> None:
    _blocked(
        _two_interval_schema(
            tmp_path, second_sig_sequence=sig_sequence,
            second_pm_sequence=pm_sequence,
        ),
        reason,
    )


def test_two_causally_ordered_synthetic_intervals_remain_schema_only(tmp_path: Path) -> None:
    result = _two_interval_schema(
        tmp_path, second_sig_sequence=52, second_pm_sequence=9,
    )
    assert result.verdict == "SCHEMA_ONLY/PAPER_NOT_ADMITTED"
    assert result.schema_valid_intervals == 2
    assert result.source_admitted is False
    assert result.paper_admitted is False
    assert result.network_requests == 0


@pytest.mark.parametrize("file_index", [0, 1, 2, 3])
@pytest.mark.parametrize("bad_number", [float("nan"), float("inf"), float("-inf")])
def test_all_json_inputs_reject_nonstandard_numeric_literals_even_after_rehash(
    tmp_path: Path, file_index: int, bad_number: float,
) -> None:
    paths, _ = _sample(tmp_path)
    manifest_path = paths[0]
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if file_index == 0:
        manifest["extra_field"] = bad_number
    else:
        target = paths[file_index]
        payload = json.loads(target.read_text(encoding="utf-8"))
        payload["unrecognised_metadata"] = bad_number
        suffix = "\n" if file_index in (2, 3) else ""
        target.write_text(json.dumps(payload, sort_keys=True) + suffix, encoding="utf-8")
        key = ("mapping", "sig", "pm")[file_index - 1]
        manifest["file_sha256"][key] = hashlib.sha256(target.read_bytes()).hexdigest()
    raw = json.dumps(manifest, sort_keys=True).encode()
    manifest_path.write_bytes(raw)
    _blocked(
        validate_offline_tape(
            *paths, expected_manifest_sha256=hashlib.sha256(raw).hexdigest(),
        ),
        "NONSTANDARD_JSON_CONSTANT",
    )
