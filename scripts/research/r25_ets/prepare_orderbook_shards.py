#!/usr/bin/env python3
"""Create five contiguous, mechanically time-sharded Kaggle PMXT acquisition jobs."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[3]
UNIVERSE = ROOT / "data/research/ets_universe"
SOURCE_FIRST = datetime(2026, 2, 21, 18, tzinfo=timezone.utc)
CODE_DATASET = "polyleviathan/sig-cup-predictions-cup-code"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--as-of", help="UTC ISO time; defaults to now, rounded up to the next hour")
    args = parser.parse_args()
    freeze_path = UNIVERSE / "ETS_UNIVERSE_FREEZE.json"
    inventory_path = UNIVERSE / "ETS_TOKEN_INVENTORY.csv"
    freeze: dict[str, Any] = json.loads(freeze_path.read_text(encoding="utf-8"))
    inventory_sha = sha256(inventory_path)
    if inventory_sha != freeze.get("sha256", {}).get("cid_token_inventory"):
        raise RuntimeError("token inventory does not match the frozen universe")
    with inventory_path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    created = [parse_time(row.get("created_at")) for row in rows]
    if created and all(value is not None for value in created):
        first_needed = min(value for value in created if value).replace(minute=0, second=0, microsecond=0)
        start = max(SOURCE_FIRST, first_needed)
        start_basis = "earliest frozen Gamma created_at, clamped to first verified PMXT archive hour"
    else:
        start = SOURCE_FIRST
        start_basis = "first verified PMXT archive hour; at least one Gamma created_at is missing"
    if args.as_of:
        end = parse_time(args.as_of)
        if end is None:
            raise ValueError("--as-of must be a UTC ISO datetime")
        end = end.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)
    else:
        now = datetime.now(timezone.utc)
        end = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    if end <= start:
        raise RuntimeError(f"empty PMXT acquisition range: {start.isoformat()} to {end.isoformat()}")
    total_hours = int((end - start).total_seconds() // 3600)
    if total_hours < 5:
        raise RuntimeError(f"cannot make five nonempty time shards from {total_hours} hours")

    shared_run = ROOT / "scripts/kaggle/r25_ets_orderbooks/run.py"
    repository_commit = subprocess.check_output(
        ["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True,
    ).strip()
    jobs_root = ROOT / "kaggle/jobs"
    for index in range(5):
        shard_start = start + timedelta(hours=(index * total_hours) // 5)
        shard_end = start + timedelta(hours=((index + 1) * total_hours) // 5)
        shard_id = f"{index + 1:02d}"
        kernel_id = f"polyleviathan/r25-ets-orderbooks-{shard_id}"
        kernel_dir = ROOT / f"scripts/kaggle/r25_ets_orderbooks_{shard_id}"
        kernel_dir.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(shared_run, kernel_dir / "run.py")
        shutil.copyfile(freeze_path, kernel_dir / "ETS_UNIVERSE_FREEZE.json")
        shutil.copyfile(inventory_path, kernel_dir / "ETS_TOKEN_INVENTORY.csv")
        (kernel_dir / "REPOSITORY_COMMIT.txt").write_text(repository_commit + "\n", encoding="utf-8")
        shard = {
            "shard_id": shard_id,
            "partition_method": "five contiguous, equal-hour UTC time intervals",
            "start_basis": start_basis,
            "start_inclusive": shard_start.isoformat().replace("+00:00", "Z"),
            "end_exclusive": shard_end.isoformat().replace("+00:00", "Z"),
            "universe_freeze_sha256": sha256(freeze_path),
            "cid_token_inventory_sha256": inventory_sha,
        }
        (kernel_dir / "shard.json").write_text(json.dumps(shard, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        metadata = {
            "id": kernel_id,
            "title": f"R2.5 ETS PMXT order-book shard {shard_id}",
            "code_file": "run.py",
            "language": "python",
            "kernel_type": "script",
            "is_private": True,
            "enable_gpu": False,
            "enable_tpu": False,
            "enable_internet": True,
            "dataset_sources": [CODE_DATASET],
            "competition_sources": [],
            "kernel_sources": [],
        }
        (kernel_dir / "kernel-metadata.json").write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        job = {
            "schema_version": 1,
            "action": "run",
            "kernel_dir": kernel_dir.relative_to(ROOT).as_posix(),
            "kernel": kernel_id,
            "poll_seconds": 30,
            "timeout_minutes": 330,
            "download_outputs": True,
            "output_file_pattern": "(ETS_ORDERBOOK_MANIFEST\\.json|ETS_ORDERBOOK_COVERAGE\\.csv|ETS_ORDERBOOK_SHARD_SUMMARY\\.json)$",
        }
        (jobs_root / f"r25-ets-orderbooks-{shard_id}.json").write_text(json.dumps(job, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    finalizer_dir = ROOT / "scripts/kaggle/r25_ets_finalize"
    shutil.copyfile(ROOT / "scripts/kaggle/r25_ets_finalize/run.py", finalizer_dir / "run.py")
    shutil.copyfile(freeze_path, finalizer_dir / "ETS_UNIVERSE_FREEZE.json")
    shutil.copyfile(inventory_path, finalizer_dir / "ETS_TOKEN_INVENTORY.csv")
    (finalizer_dir / "REPOSITORY_COMMIT.txt").write_text(repository_commit + "\n", encoding="utf-8")
    finalizer_metadata = {
        "id": "polyleviathan/r25-ets-finalize",
        "title": "R2.5 ETS immutable historical data output",
        "code_file": "run.py",
        "language": "python",
        "kernel_type": "script",
        "is_private": True,
        "enable_gpu": False,
        "enable_tpu": False,
        "enable_internet": False,
        "dataset_sources": [],
        "competition_sources": [],
        "kernel_sources": [
            "polyleviathan/r25-ets-discovery",
            *[f"polyleviathan/r25-ets-orderbooks-{i:02d}" for i in range(1, 6)],
        ],
    }
    (finalizer_dir / "kernel-metadata.json").write_text(json.dumps(finalizer_metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": "PREPARED",
        "freeze_sha256": sha256(freeze_path),
        "inventory_sha256": inventory_sha,
        "accepted_markets": freeze["accepted_ets_market_count"],
        "cids": freeze["unique_cid_count"],
        "tokens": freeze["unique_token_count"],
        "range": {"start_inclusive": start.isoformat().replace("+00:00", "Z"), "end_exclusive": end.isoformat().replace("+00:00", "Z")},
        "start_basis": start_basis,
        "jobs": [f"kaggle/jobs/r25-ets-orderbooks-{i:02d}.json" for i in range(1, 6)],
    }, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
