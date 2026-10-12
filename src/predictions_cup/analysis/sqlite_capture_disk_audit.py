"""Read-only capture disk, WAL-size and archived-input integrity preflight.

No SQLite connection, checkpoint, vacuum, data deletion, network call or runtime
registration. WAL visibility during active capture is only a point-in-time hint.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import stat
from pathlib import Path
from typing import Any

_SQLITE_MAGIC = b"SQLite format 3\x00"
_WAL_MAGICS = {0x377F0682, 0x377F0683}
_ARCHIVE_SCHEMA = "sig-capture-archive-manifest-v1"


def _regular_file(path: Path) -> None:
    metadata = path.lstat()
    if not stat.S_ISREG(metadata.st_mode):
        raise ValueError(f"expected non-symlink regular file: {path}")


def _sqlite_page_size(path: Path) -> int:
    _regular_file(path)
    with path.open("rb") as stream:
        header = stream.read(100)
    if len(header) != 100 or header[:16] != _SQLITE_MAGIC:
        raise ValueError("not a complete SQLite database header")
    encoded = int.from_bytes(header[16:18], "big")
    page_size = 65536 if encoded == 1 else encoded
    if page_size < 512 or page_size > 65536 or page_size & (page_size - 1):
        raise ValueError("invalid SQLite page size")
    return page_size


def _wal_observation(wal_path: Path, page_size: int) -> dict[str, Any]:
    if not wal_path.exists() and not wal_path.is_symlink():
        return {"file_bytes": 0, "frame_count_estimate": 0,
                "status": "ABSENT", "checksum_verified": False}
    _regular_file(wal_path)
    size = wal_path.stat().st_size
    if size == 0:
        status, frames = "EMPTY", 0
    elif size < 32:
        status, frames = "INCOMPLETE_HEADER", None
    else:
        with wal_path.open("rb") as stream:
            header = stream.read(32)
        magic = int.from_bytes(header[0:4], "big")
        declared = int.from_bytes(header[8:12], "big")
        if magic not in _WAL_MAGICS:
            status, frames = "BAD_MAGIC", None
        elif declared != page_size:
            status, frames = "PAGE_SIZE_MISMATCH", None
        else:
            stride = 24 + page_size
            frames, remainder = divmod(size - 32, stride)
            status = "FRAME_ALIGNED_UNVERIFIED" if remainder == 0 else "PARTIAL_FRAME_UNVERIFIED"
    return {
        "file_bytes": size,
        "frame_count_estimate": frames,
        "status": status,
        # A header/size check does not validate frame checksums or transaction
        # completeness, and cannot prove reader checkpoint lag.
        "checksum_verified": False,
    }


def verify_archive_manifest(manifest_path: Path, archive_root: Path) -> dict[str, Any]:
    """Hash explicit archived copies; never delete or move them or source data."""
    _regular_file(manifest_path)
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("schema_version") != _ARCHIVE_SCHEMA:
        raise ValueError("unrecognized archive manifest schema")
    entries = payload.get("archives")
    if not isinstance(entries, list) or not entries:
        raise ValueError("manifest has no archives")
    root = archive_root.resolve(strict=True)
    if not root.is_dir():
        raise ValueError("archive root must be a directory")
    results: list[dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("invalid archive record")
        relative, expected, size = (
            entry.get("path"), entry.get("sha256"), entry.get("size_bytes")
        )
        if (
            not isinstance(relative, str) or not relative or
            not isinstance(expected, str) or len(expected) != 64 or
            any(x not in "0123456789abcdef" for x in expected) or
            not isinstance(size, int) or isinstance(size, bool) or size < 0
        ):
            raise ValueError("invalid archive path/digest/size")
        path = Path(relative)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError("archive path traversal prohibited")
        resolved = (root / path).resolve(strict=True)
        if not resolved.is_relative_to(root):
            raise ValueError("archive escapes archive root")
        _regular_file(root / path)
        if (root / path).stat().st_size != size:
            raise ValueError(f"archive size mismatch: {relative}")
        digest = hashlib.sha256()
        with (root / path).open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
        if digest.hexdigest() != expected:
            raise ValueError(f"archive digest mismatch: {relative}")
        results.append({"path": relative, "size_bytes": size, "sha256": expected})
    return {"manifest_verified": True, "verified_archives": results}


def inspect_capture_disk(
    db_path: Path,
    *,
    minimum_free_bytes: int,
    archive_manifest: Path | None = None,
    archive_root: Path | None = None,
) -> dict[str, Any]:
    """Return filesystem evidence, never infer data safety from a checkpoint guess."""
    if minimum_free_bytes < 0:
        raise ValueError("minimum_free_bytes must be nonnegative")
    if (archive_manifest is None) != (archive_root is None):
        raise ValueError("manifest and archive_root must be supplied together")
    page_size = _sqlite_page_size(db_path)
    database_bytes = db_path.stat().st_size
    usage = shutil.disk_usage(db_path.parent)
    wal = _wal_observation(Path(f"{db_path}-wal"), page_size)
    sufficient = usage.free >= minimum_free_bytes
    result: dict[str, Any] = {
        "status": "OBSERVATION_ONLY_NOT_RETENTION_AUTHORITY",
        "db_bytes": database_bytes,
        "db_page_size": page_size,
        "db_file_page_aligned": database_bytes % page_size == 0,
        "wal": wal,
        "filesystem_total_bytes": usage.total,
        "filesystem_free_bytes": usage.free,
        "minimum_free_bytes": minimum_free_bytes,
        "free_space_gate": "PASS" if sufficient else "FAIL",
        "database_live_consistency": "NOT_VERIFIED",
        "checkpoint_progress": "NOT_MEASURED_NO_WRITES",
        "archive_proof": "NOT_PROVIDED",
        "safe_to_delete_source_data": False,
    }
    if archive_manifest is not None and archive_root is not None:
        result["archive_proof"] = verify_archive_manifest(
            archive_manifest, archive_root
        )
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument("--minimum-free-bytes", type=int, default=1_073_741_824)
    parser.add_argument("--archive-manifest", type=Path)
    parser.add_argument("--archive-root", type=Path)
    args = parser.parse_args(argv)
    try:
        result = inspect_capture_disk(
            args.db, minimum_free_bytes=args.minimum_free_bytes,
            archive_manifest=args.archive_manifest,
            archive_root=args.archive_root,
        )
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        # Do not dump environmental or source file content into error logs.
        parser.exit(2, f"capture disk audit failed: {type(exc).__name__}\\n")
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0 if result["free_space_gate"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
