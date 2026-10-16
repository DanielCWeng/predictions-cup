"""Independent strict offline-only reviewer falsifiers for draft #277.

These tests use an already synthetic author fixture, never a venue or host.
Do not edit the original author tests or schema gate to manufacture acceptance.
"""

from __future__ import annotations

import hashlib
import json
import socket
from pathlib import Path
from typing import Any

import pytest

from predictions_cup.research.offline_paired_tape_gate import validate_offline_tape
from test_offline_paired_tape_gate import _sample


def _replace_archive(
    paths: tuple[Path, Path, Path, Path],
    *,
    sig_rows: list[dict[str, Any]] | None = None,
    pm_rows: list[dict[str, Any]] | None = None,
) -> str:
    manifest_path, _, sig_path, pm_path = paths
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if sig_rows is not None:
        contents = "".join(json.dumps(row, sort_keys=True) + "\n" for row in sig_rows)
        sig_path.write_text(contents, encoding="utf-8")
    if pm_rows is not None:
        contents = "".join(json.dumps(row, sort_keys=True) + "\n" for row in pm_rows)
        pm_path.write_text(contents, encoding="utf-8")
    sig_count = len(sig_path.read_text(encoding="utf-8").splitlines())
    pm_count = len(pm_path.read_text(encoding="utf-8").splitlines())
    assert sig_count == pm_count
    manifest["interval_count"] = sig_count
    for name, path in (("sig", sig_path), ("pm", pm_path)):
        manifest["file_sha256"][name] = hashlib.sha256(path.read_bytes()).hexdigest()
    new_manifest = json.dumps(manifest, sort_keys=True).encode()
    manifest_path.write_bytes(new_manifest)
    return hashlib.sha256(new_manifest).hexdigest()


def _two_intervals(
    paths: tuple[Path, Path, Path, Path],
    *,
    sig_next_sequence: int,
    pm_next_sequence: int,
) -> str:
    _, _, sig_path, pm_path = paths
    sig_first = json.loads(sig_path.read_text(encoding="utf-8"))
    pm_first = json.loads(pm_path.read_text(encoding="utf-8"))
    sig_second = dict(sig_first)
    pm_second = dict(pm_first)
    sig_second.update({
        "interval_id": "interval-2",
        "source_at": "2026-10-16T12:00:00.600Z",
        "received_at": "2026-10-16T12:00:00.700Z",
        "decision_at": "2026-10-16T12:00:01.000Z",
        "source_sequence": sig_next_sequence,
    })
    pm_second.update({
        "interval_id": "interval-2",
        "source_at": "2026-10-16T12:00:00.650Z",
        "received_at": "2026-10-16T12:00:00.800Z",
        "decision_at": "2026-10-16T12:00:01.000Z",
        "ws_sequence": pm_next_sequence,
    })
    return _replace_archive(
        paths, sig_rows=[sig_first, sig_second], pm_rows=[pm_first, pm_second],
    )


def _assert_no_admission(result: Any) -> None:
    assert result.source_admitted is False
    assert result.paper_admitted is False
    assert result.external_issuer_authenticated is False
    assert result.network_requests == 0


@pytest.mark.parametrize(
    ("next_sig_sequence", "next_pm_sequence"),
    [(52, 7), (50, 9)],
    ids=["pm-ws-regresses-without-reconnect", "sig-native-revision-regresses"],
)
def test_same_run_sequence_regression_is_not_schema_certified(
    tmp_path: Path, next_sig_sequence: int, next_pm_sequence: int,
) -> None:
    """A positive per-row sequence and asserted contiguous=True are insufficient.

    Both rows have distinct interval IDs, monotone UTC clocks, good hashes,
    unchanged producer run/PM connection/subscription epochs, valid books.
    A native revision decreasing in one uninterrupted run must fail schema.
    """
    paths, _ = _sample(tmp_path)
    digest = _two_intervals(
        paths, sig_next_sequence=next_sig_sequence, pm_next_sequence=next_pm_sequence,
    )
    result = validate_offline_tape(*paths, expected_manifest_sha256=digest)
    _assert_no_admission(result)
    assert result.verdict == "PAIRED_DEPTH_NOT_CERTIFIED"
    assert result.schema_valid_intervals == 0


def test_nonstandard_nan_jsonl_field_is_not_schema_certified(tmp_path: Path) -> None:
    """JSON's NaN literal is not valid JSON even if it is in extra metadata."""
    paths, _ = _sample(tmp_path)
    pm_path = paths[3]
    row = json.loads(pm_path.read_text(encoding="utf-8"))
    row["source_health_score"] = float("nan")
    digest = _replace_archive(paths, pm_rows=[row])
    assert b"NaN" in pm_path.read_bytes()
    result = validate_offline_tape(*paths, expected_manifest_sha256=digest)
    _assert_no_admission(result)
    assert result.verdict == "PAIRED_DEPTH_NOT_CERTIFIED"
    assert result.schema_valid_intervals == 0


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("price", "NaN"), ("price", "Infinity"), ("price", "0.0"),
        ("price", "1.0"), ("size", "NaN"), ("size", "Infinity"),
        ("size", "-1"), ("size", "0"),
    ],
)
def test_nonfinite_or_out_of_range_book_level_rejected(
    tmp_path: Path, field: str, value: str,
) -> None:
    paths, _ = _sample(tmp_path)
    pm_path = paths[3]
    row = json.loads(pm_path.read_text(encoding="utf-8"))
    row["bids"][0][field] = value
    digest = _replace_archive(paths, pm_rows=[row])
    result = validate_offline_tape(*paths, expected_manifest_sha256=digest)
    _assert_no_admission(result)
    assert result.verdict == "PAIRED_DEPTH_NOT_CERTIFIED"
    assert result.schema_valid_intervals == 0


def test_direct_symlink_and_socket_denied(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    paths, digest = _sample(tmp_path)
    pm_path = paths[3]
    retained = tmp_path / "pm-retained.jsonl"
    pm_path.rename(retained)
    pm_path.symlink_to(retained)

    def forbid_network(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("unexpected socket call")

    monkeypatch.setattr(socket.socket, "connect", forbid_network)
    monkeypatch.setattr(socket, "create_connection", forbid_network)
    result = validate_offline_tape(*paths, expected_manifest_sha256=digest)
    _assert_no_admission(result)
    assert result.verdict == "PAIRED_DEPTH_NOT_CERTIFIED"
    assert ("ALL", "INPUT_NOT_REGULAR_IMMUTABLE_FILE") in result.rejections


def test_self_claimed_permission_and_manifest_digest_never_authenticate(
    tmp_path: Path,
) -> None:
    paths, _ = _sample(tmp_path)
    manifest_path = paths[0]
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["issuer"] = "self-stated-venue-custodian"
    manifest["permission_ref"] = "self-stated-commercial-use-grant"
    for producer in manifest["producers"].values():
        producer["issuer"] = "self-claimed-signatory"
    new_manifest = json.dumps(manifest, sort_keys=True).encode()
    manifest_path.write_bytes(new_manifest)
    result = validate_offline_tape(
        *paths, expected_manifest_sha256=hashlib.sha256(new_manifest).hexdigest(),
    )
    _assert_no_admission(result)
    assert result.verdict == "SCHEMA_ONLY/PAPER_NOT_ADMITTED"
