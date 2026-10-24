"""Read-only, resumable archival export of operational SIG SQLite into Parquet.

Run ONLY while predictions-cup-sig-capture is stopped for a planned maintenance
window. No SQLite DELETE/VACUUM/checkpoint operation is performed by this tool.
Existing SQLite/WAL and existing Parquet files are never removed or replaced.

Each row is exported with SQLite rowid and original columns represented as
nullable UTF-8 strings. BLOBs use a base64: prefix; column declarations are
recorded in the manifest. This is an *analytical* archive, not proof that the
SQLite database can be discarded. An independent coverage/fidelity audit is
required before any retention or cleanup decision.
"""

from __future__ import annotations

import argparse
import base64
from collections.abc import Callable
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import time
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

_CAPTURE_SERVICE = "predictions-cup-sig-capture"
_GIB = 1024**3


def _quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _encode(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, bytes):
        return "base64:" + base64.b64encode(value).decode("ascii")
    return str(value)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _save_manifest(path: Path, data: dict[str, Any]) -> None:
    pending = path.with_suffix(".pending")
    with pending.open("w", encoding="utf-8") as out:
        json.dump(data, out, sort_keys=True, indent=2)
        out.flush()
        os.fsync(out.fileno())
    os.replace(pending, path)


def check_capture_stopped() -> None:
    """Refuse to pin a live SQLite WAL or compete with the live capture writer."""
    result = subprocess.run(
        ["systemctl", "is-active", _CAPTURE_SERVICE],
        capture_output=True,
        text=True,
        check=False,
        timeout=4,
    )
    if result.returncode != 3 or result.stdout.strip() != "inactive":
        raise RuntimeError(
            "Capture must be explicitly stopped and verified inactive before export; "
            f"systemd status={result.stdout.strip() or 'unknown'}"
        )


def archive(
    source: Path,
    destination: Path,
    *,
    batch_rows: int = 1_000,
    min_free_bytes: int = 8 * _GIB,
    max_seconds: float = 480.0,
    guard: Callable[[], None] = check_capture_stopped,
) -> dict[str, Any]:
    """Export rowid-bounded chunks; resume safely from an atomic manifest.

    The fixed high-water rowid is captured per table on its first run. Repeated
    invocations continue the same bounded archive even if newer rows exist.
    No existing final Parquet shard is overwritten.
    """
    if batch_rows < 1 or batch_rows > 10_000:
        raise ValueError("batch_rows must be between 1 and 10000")
    if min_free_bytes < 0 or max_seconds <= 0:
        raise ValueError("invalid storage or duration limit")
    if not source.is_file():
        raise FileNotFoundError(source)
    guard()
    destination.mkdir(parents=True, exist_ok=True)
    if source.resolve() == destination.resolve():
        raise ValueError("source and destination must differ")
    manifest_path = destination / "manifest.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("version") != 1 or manifest.get("source") != str(source.resolve()):
            raise RuntimeError("Archive manifest belongs to a different source/version")
    else:
        if shutil.disk_usage(destination).free < min_free_bytes:
            raise RuntimeError("Insufficient free space to start a safe archive")
        manifest = {"version": 1, "source": str(source.resolve()), "tables": {}}
        _save_manifest(manifest_path, manifest)

    started = time.monotonic()
    conn = sqlite3.connect(f"file:{source.resolve()}?mode=ro", uri=True, timeout=2)
    try:
        conn.execute("PRAGMA query_only=ON")
        tables = conn.execute(
            "SELECT name, sql FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()
        for name, create_sql in tables:
            guard()
            if "WITHOUT ROWID" in (create_sql or "").upper():
                raise RuntimeError(f"Table {name!r} needs a separate key export strategy")
            info = conn.execute(f"PRAGMA table_info({_quote(name)})").fetchall()
            columns = [{"name": str(row[1]), "type": str(row[2])} for row in info]
            schema_hash = hashlib.sha256(
                json.dumps(columns, sort_keys=True).encode("utf-8")
            ).hexdigest()
            state = manifest["tables"].get(name)
            if state is None:
                target = conn.execute(
                    f"SELECT max(rowid) FROM {_quote(name)}"
                ).fetchone()[0]
                state = {
                    "schema_sha256": schema_hash,
                    "columns": columns,
                    "target_rowid": int(target or 0),
                    "last_rowid": 0,
                    "parts": [],
                }
                manifest["tables"][name] = state
                _save_manifest(manifest_path, manifest)
            if state["schema_sha256"] != schema_hash:
                raise RuntimeError(f"Schema changed for {name!r}; refusing resume")

            table_dir = destination / name
            table_dir.mkdir(exist_ok=True)
            while state["last_rowid"] < state["target_rowid"]:
                if time.monotonic() - started >= max_seconds:
                    return manifest
                guard()
                free = shutil.disk_usage(destination).free
                if free < min_free_bytes:
                    raise RuntimeError(
                        f"Free-space guard tripped ({free} bytes); existing files preserved"
                    )

                fetched = conn.execute(
                    f"SELECT rowid, * FROM {_quote(name)} "
                    "WHERE rowid > ? AND rowid <= ? ORDER BY rowid LIMIT ?",
                    (state["last_rowid"], state["target_rowid"], batch_rows),
                ).fetchall()
                if not fetched:
                    state["last_rowid"] = state["target_rowid"]
                    _save_manifest(manifest_path, manifest)
                    break

                first_id = int(fetched[0][0])
                last_id = int(fetched[-1][0])
                filename = f"rowid-{first_id:014d}-{last_id:014d}.parquet"
                output = table_dir / filename
                pending = table_dir / (filename + ".pending")
                if output.exists() or pending.exists():
                    raise RuntimeError(
                        f"Unmanifested existing shard {filename}; manual review required"
                    )

                fields: dict[str, pa.Array] = {
                    "_sqlite_rowid": pa.array(
                        [int(row[0]) for row in fetched], type=pa.int64()
                    )
                }
                for index, column in enumerate(columns, start=1):
                    fields[column["name"]] = pa.array(
                        [_encode(row[index]) for row in fetched], type=pa.string()
                    )
                table = pa.table(fields)
                pq.write_table(table, pending, compression="zstd")
                if pq.read_metadata(pending).num_rows != len(fetched):
                    raise RuntimeError(f"Parquet count mismatch for {filename}")
                with pending.open("rb") as handle:
                    os.fsync(handle.fileno())
                digest = _sha256(pending)
                os.replace(pending, output)
                state["parts"].append(
                    {
                        "file": filename,
                        "rows": len(fetched),
                        "first_rowid": first_id,
                        "last_rowid": last_id,
                        "sha256": digest,
                        "bytes": output.stat().st_size,
                    }
                )
                state["last_rowid"] = last_id
                _save_manifest(manifest_path, manifest)
    finally:
        conn.close()
    return manifest


def verify(destination: Path) -> dict[str, int]:
    """Recheck shard checksums, physical Parquet row counts and manifest ranges."""
    manifest = json.loads((destination / "manifest.json").read_text(encoding="utf-8"))
    counts: dict[str, int] = {}
    for name, state in manifest["tables"].items():
        count = 0
        previous = 0
        for part in state["parts"]:
            if part["first_rowid"] <= previous:
                raise RuntimeError(f"Overlapping rowid ranges for {name}")
            path = destination / name / part["file"]
            if _sha256(path) != part["sha256"]:
                raise RuntimeError(f"SHA256 mismatch: {path}")
            if pq.read_metadata(path).num_rows != part["rows"]:
                raise RuntimeError(f"Row count mismatch: {path}")
            count += part["rows"]
            previous = part["last_rowid"]
        counts[name] = count
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--destination", required=True, type=Path)
    parser.add_argument("--batch-rows", type=int, default=1_000)
    parser.add_argument("--min-free-gib", type=float, default=8.0)
    parser.add_argument("--max-seconds", type=float, default=480.0)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    if args.verify_only:
        print(json.dumps({"verified_rows": verify(args.destination)}, sort_keys=True))
        return
    result = archive(
        args.source,
        args.destination,
        batch_rows=args.batch_rows,
        min_free_bytes=int(args.min_free_gib * _GIB),
        max_seconds=args.max_seconds,
    )
    summary = {
        name: {
            "exported_rows": sum(item["rows"] for item in state["parts"]),
            "complete": state["last_rowid"] >= state["target_rowid"],
        }
        for name, state in result["tables"].items()
    }
    print(json.dumps(summary, sort_keys=True))


if __name__ == "__main__":
    main()
