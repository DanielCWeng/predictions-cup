#!/usr/bin/env python3
"""Summarize the exported DATA-004 fill rows by direction, side, role and tier."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_FILL_DIR = Path("/home/ubuntu/inbox/data004_20260929/sig-cup-data-004-ets-p0p1-fills/fills")
DEFAULT_OUTPUT = ROOT / "data/research/data004_ets_p0p1/v1/fill_composition.json"
DEFAULT_PACKAGE_ROOT = DEFAULT_FILL_DIR.parent
LANE = Path("/home/ubuntu/campaigns/data004_20260929")
DEFAULT_EXPECTED_ROWS = 231_964
MIN_FREE_BYTES = 6 * (1 << 30)
HARD_MIN_FREE_BYTES = 5 * (1 << 30)


def sha256_file(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def disk_gate(path: Path, stage: str, reserve_bytes: int = 0) -> int:
    started = time.monotonic()
    log_path = LANE / "data004_package_disk_check.jsonl"
    while True:
        result = subprocess.run(
            ["df", "-B1", "--output=avail", str(path)], check=True, text=True, capture_output=True
        )
        available = int([line.strip() for line in result.stdout.splitlines() if line.strip()][-1])
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(
                json.dumps(
                    {
                        "checked_at_utc": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                        "stage": stage,
                        "available_bytes": available,
                        "reserve_bytes": reserve_bytes,
                        "min_free_required_bytes": MIN_FREE_BYTES,
                        "hard_floor_bytes": HARD_MIN_FREE_BYTES,
                    },
                    sort_keys=True,
                )
                + "\n"
            )
            handle.flush()
            os.fsync(handle.fileno())
        if available < HARD_MIN_FREE_BYTES:
            raise RuntimeError(
                f"disk is already below the 5 GiB hard floor at {stage}: {available}"
            )
        if available >= MIN_FREE_BYTES and available - reserve_bytes >= HARD_MIN_FREE_BYTES:
            return available
        elapsed = time.monotonic() - started
        if elapsed >= 2 * 60 * 60:
            raise RuntimeError(
                f"disk gate timed out after two hours at {stage}: {available} bytes free"
            )
        print(
            f"PAUSED disk gate stage={stage} free_bytes={available}; recheck in 5 minutes",
            flush=True,
        )
        time.sleep(300)


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def update_package(
    summary: dict[str, Any], summary_path: Path, package_root: Path
) -> dict[str, Any]:
    package_root = package_root.resolve()
    manifest_path = package_root / "MANIFEST.json"
    readme_path = package_root / "README.md"
    coverage_path = package_root / "COVERAGE.md"
    if not all(path.exists() for path in (manifest_path, readme_path, coverage_path)):
        raise RuntimeError(f"completed Kaggle package is absent or incomplete at {package_root}")
    record_path = ROOT / "data/manifests/fills/data_004_kaggle_run.json"
    run = json.loads(record_path.read_text(encoding="utf-8"))
    archive_path = Path(run["source_archive"])
    relative = summary_path.resolve().relative_to(ROOT / "data/research/data004_ets_p0p1/v1")
    package_summary = package_root / relative.name
    summary_bytes = summary_path.read_bytes()
    disk_gate(package_root, "before_write_fill_composition", reserve_bytes=len(summary_bytes) * 2)
    package_summary.write_bytes(summary_bytes)

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["files"] = [row for row in manifest["files"] if row.get("path") != relative.as_posix()]
    manifest["files"].append(
        {
            "path": relative.as_posix(),
            "bytes": package_summary.stat().st_size,
            "sha256": sha256_file(package_summary),
            "source": "summarized from the 343 finalized OCI fill Parquet objects",
        }
    )
    manifest["files"].sort(key=lambda row: row["path"])
    readme_text = readme_path.read_text(encoding="utf-8")
    if "`fill_composition.json`" not in readme_text:
        readme_text += "\n\n`fill_composition.json` gives row counts by economic direction, source side, order role, and P0/P1 tier.\n"
    coverage_text = coverage_path.read_text(encoding="utf-8")
    direction = summary["counts_by"]["economic_direction"]
    role = summary["counts_by"]["order_role"]
    tier = summary["counts_by"]["acquisition_class"]
    section = (
        "\n## Fill composition\n\n"
        f"Economic direction: BUY {direction.get('BUY', 0):,}; SELL {direction.get('SELL', 0):,}; "
        f"UNKNOWN {direction.get('UNKNOWN', 0) + direction.get('NULL', 0):,}. "
        f"Order role: MAKER {role.get('MAKER', 0):,}; TAKER {role.get('TAKER', 0):,}. "
        f"P0/P1 rows: {tier.get('FILLS_P0', 0):,} / {tier.get('FILLS_P1', 0):,}. "
        "Detailed category counts are in `fill_composition.json`.\n"
    )
    if "## Fill composition" not in coverage_text:
        coverage_text += section

    disk_gate(
        package_root,
        "before_refresh_package_manifest",
        reserve_bytes=manifest_path.stat().st_size + 4096,
    )
    readme_path.write_text(readme_text, encoding="utf-8")
    coverage_path.write_text(coverage_text, encoding="utf-8")
    for name in ("README.md", "COVERAGE.md"):
        path = package_root / name
        record = next(row for row in manifest["files"] if row["path"] == name)
        record["bytes"] = path.stat().st_size
        record["sha256"] = sha256_file(path)
    write_json(manifest_path, manifest)
    disk_gate(archive_path.parent, "before_refresh_archive", reserve_bytes=300 * (1 << 20))
    subprocess.run(
        [
            "tar",
            "--zstd",
            "-cf",
            str(archive_path),
            "-C",
            str(archive_path.parent),
            package_root.name,
        ],
        check=True,
        text=True,
        capture_output=True,
    )
    members = subprocess.run(
        ["tar", "--zstd", "-tf", str(archive_path)], check=True, text=True, capture_output=True
    ).stdout.splitlines()
    files = [path for path in package_root.rglob("*") if path.is_file()]
    run.update(
        {
            "source_archive_bytes": archive_path.stat().st_size,
            "source_archive_sha256": sha256_file(archive_path),
            "uncompressed_bytes": sum(path.stat().st_size for path in files),
            "file_count": len(files),
            "archive_member_count": len(members),
            "generated_utc": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "status": "PENDING_MANUAL_UPLOAD",
        }
    )
    write_json(record_path, run)
    return run


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fill-dir", type=Path, default=DEFAULT_FILL_DIR)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--package-root", type=Path, default=DEFAULT_PACKAGE_ROOT)
    parser.add_argument("--expected-rows", type=int, default=DEFAULT_EXPECTED_ROWS)
    args = parser.parse_args()
    if os.geteuid() == 0:
        raise RuntimeError("run as ubuntu")
    os.environ["POLARS_MAX_THREADS"] = "2"
    sys.path.insert(0, "/home/ubuntu/polymarketwhale/Sonar")
    import polars as pl

    pl.Config.set_streaming_chunk_size(4096)
    paths = sorted(args.fill_dir.resolve().rglob("*.parquet"))
    if not paths:
        raise RuntimeError(f"no fill Parquet files found under {args.fill_dir}")
    scan = pl.scan_parquet([str(path) for path in paths], low_memory=True)
    row_count = int(scan.select(pl.len()).collect(engine="streaming").item())
    if row_count != args.expected_rows:
        raise RuntimeError(f"expected {args.expected_rows} fill rows, found {row_count}")

    def counts_for(column: str) -> dict[str, int]:
        result = (
            scan.group_by(column).len().sort(column, nulls_last=True).collect(engine="streaming")
        )
        counts: dict[str, int] = {}
        for row in result.iter_rows(named=True):
            value: Any = row[column]
            label = "NULL" if value is None else str(value)
            counts[label] = int(row["len"])
        return counts

    summary = {
        "dataset_id": "DATA-004",
        "version": "v1",
        "fill_rows": row_count,
        "parquet_files": len(paths),
        "counts_by": {
            column: counts_for(column)
            for column in (
                "economic_direction",
                "economic_direction_status",
                "side",
                "order_role",
                "order_is_match_taker_order",
                "acquisition_class",
            )
        },
    }
    disk_gate(args.output.parent, "before_write_fill_composition", reserve_bytes=16 * 1024)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    package_record = update_package(summary, args.output, args.package_root.resolve())
    summary["kaggle_package"] = {
        "archive_sha256": package_record["source_archive_sha256"],
        "archive_bytes": package_record["source_archive_bytes"],
        "status": package_record["status"],
    }
    print(json.dumps(summary, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
