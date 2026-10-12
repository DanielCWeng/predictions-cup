"""Read-only capture storage audit must never prune or checkpoint source data."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from predictions_cup.analysis.sqlite_capture_disk_audit import (
    inspect_capture_disk,
    main,
    verify_archive_manifest,
)


def _db(tmp_path: Path) -> Path:
    path = tmp_path / "capture.sqlite3"
    connection = sqlite3.connect(path)
    try:
        connection.execute("CREATE TABLE evidence (id INTEGER PRIMARY KEY, text TEXT)")
        connection.execute("INSERT INTO evidence (text) VALUES ('immutable replay')")
        connection.commit()
    finally:
        connection.close()
    return path


def _manifest(tmp_path: Path, *, payload: bytes = b"sample archived depth") -> tuple[Path, Path]:
    archive_dir = tmp_path / "archives"
    archive_dir.mkdir()
    (archive_dir / "source.arrow").write_bytes(payload)
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({
        "schema_version": "sig-capture-archive-manifest-v1",
        "archives": [{
            "path": "source.arrow", "size_bytes": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
        }],
    }), encoding="utf-8")
    return manifest, archive_dir


def test_inspect_reports_disk_capacity_without_writing_db_or_wal(tmp_path: Path) -> None:
    db = _db(tmp_path)
    original = db.read_bytes()
    mtime = db.stat().st_mtime_ns
    wal = Path(f"{db}-wal")
    assert not wal.exists()

    result = inspect_capture_disk(db, minimum_free_bytes=0)
    assert result["free_space_gate"] == "PASS"
    assert result["database_live_consistency"] == "NOT_VERIFIED"
    assert result["checkpoint_progress"] == "NOT_MEASURED_NO_WRITES"
    assert result["archive_proof"] == "NOT_PROVIDED"
    assert result["safe_to_delete_source_data"] is False
    assert result["wal"]["status"] == "ABSENT"
    assert result["db_file_page_aligned"]
    assert db.read_bytes() == original
    assert db.stat().st_mtime_ns == mtime
    assert not wal.exists()


def test_readonly_wal_frame_observation_is_never_checksum_proof(tmp_path: Path) -> None:
    db = _db(tmp_path)
    ps = int.from_bytes(db.read_bytes()[16:18], "big")
    wal = Path(f"{db}-wal")
    header = bytearray(32)
    header[:4] = (0x377F0682).to_bytes(4, "big")
    header[8:12] = ps.to_bytes(4, "big")
    wal.write_bytes(bytes(header) + b"\x00" * (24 + ps))
    before = wal.read_bytes()
    result = inspect_capture_disk(db, minimum_free_bytes=0)
    assert result["wal"]["status"] == "FRAME_ALIGNED_UNVERIFIED"
    assert result["wal"]["frame_count_estimate"] == 1
    assert result["wal"]["checksum_verified"] is False
    assert wal.read_bytes() == before


def test_free_space_threshold_fails_closed_but_does_not_delete(tmp_path: Path) -> None:
    db = _db(tmp_path)
    before = db.read_bytes()
    result = inspect_capture_disk(db, minimum_free_bytes=10 ** 50)
    assert result["free_space_gate"] == "FAIL"
    assert result["safe_to_delete_source_data"] is False
    assert db.read_bytes() == before


def test_archive_hashes_validated_before_claim_of_verified_copy(tmp_path: Path) -> None:
    db = _db(tmp_path)
    manifest, root = _manifest(tmp_path)
    result = inspect_capture_disk(
        db, minimum_free_bytes=0, archive_manifest=manifest, archive_root=root
    )
    assert result["archive_proof"]["manifest_verified"]
    (root / "source.arrow").write_bytes(b"tampered archive source")
    with pytest.raises(ValueError, match="archive size mismatch|archive digest mismatch"):
        inspect_capture_disk(
            db, minimum_free_bytes=0, archive_manifest=manifest, archive_root=root
        )


@pytest.mark.parametrize("bad", ["../source.arrow", "/tmp/elsewhere"])
def test_manifest_rejects_out_of_root_archive_paths(
    tmp_path: Path, bad: str
) -> None:
    manifest, root = _manifest(tmp_path)
    data = json.loads(manifest.read_text(encoding="utf-8"))
    data["archives"][0]["path"] = bad
    manifest.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="traversal"):
        verify_archive_manifest(manifest, root)


def test_corrupt_or_missing_files_never_produce_safe_retention_flag(tmp_path: Path) -> None:
    wrong = tmp_path / "capture.sqlite3"
    wrong.write_bytes(b"not a db")
    with pytest.raises(ValueError, match="SQLite"):
        inspect_capture_disk(wrong, minimum_free_bytes=0)
    with pytest.raises(ValueError, match="supplied together"):
        inspect_capture_disk(wrong, minimum_free_bytes=0, archive_manifest=tmp_path)


def test_cli_is_readonly_and_outputs_machine_parseable_status(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    db = _db(tmp_path)
    assert main(["--db", str(db), "--minimum-free-bytes", "0"]) == 0
    obj = json.loads(capsys.readouterr().out)
    assert obj["status"] == "OBSERVATION_ONLY_NOT_RETENTION_AUTHORITY"
    assert obj["safe_to_delete_source_data"] is False
