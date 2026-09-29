#!/usr/bin/env python3
"""Build the compressed DATA-004 v2 Kaggle handoff and record its checksum."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
INBOX = Path("/home/ubuntu/inbox/data004_20260929")
PACKAGE_NAME = "sig-cup-data-004-ets-p0p1-fills"
PACKAGE = INBOX / PACKAGE_NAME
ARCHIVE = INBOX / f"{PACKAGE_NAME}.tar.zst"
SHA_FILE = INBOX / f"{PACKAGE_NAME}.tar.zst.sha256"
RUN_RECORD = ROOT / "data/manifests/fills/data_004_kaggle_run.json"
MANIFEST = ROOT / "data/research/data004_ets_p0p1/v2/data004_manifest.json"
MIN_FREE_BYTES = 6 * (1 << 30)
HARD_MIN_FREE_BYTES = 5 * (1 << 30)
MAX_TEMP_BYTES = 200 * (1 << 20)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def disk_gate(stage: str, reserve_bytes: int) -> int:
    log = Path("/home/ubuntu/campaigns/data004_20260929/data004_a3_disk_check.jsonl")
    started = time.monotonic()
    while True:
        result = subprocess.run(["df", "-B1", "--output=avail", str(INBOX)], check=True, text=True, capture_output=True)
        available = int([line.strip() for line in result.stdout.splitlines() if line.strip()][-1])
        with log.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"stage": stage, "available_bytes": available, "reserve_bytes": reserve_bytes,
                                     "checked_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")}) + "\n")
        if available < HARD_MIN_FREE_BYTES:
            raise RuntimeError(f"disk below hard 5 GiB floor at {stage}: {available}")
        if available >= MIN_FREE_BYTES + reserve_bytes:
            return available
        if time.monotonic() - started > 2 * 60 * 60:
            raise RuntimeError(f"disk gate timed out at {stage}: {available}")
        print(f"PAUSED disk gate stage={stage} free_bytes={available}; rechecking in 5 minutes", flush=True)
        time.sleep(300)


def main() -> None:
    if os.geteuid() == 0:
        raise RuntimeError("run as ubuntu")
    package_manifest = json.loads((PACKAGE / "MANIFEST.json").read_text(encoding="utf-8"))
    source_manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    if package_manifest.get("version") != "v2" or source_manifest.get("version") != "v2":
        raise RuntimeError("refusing to archive a non-v2 package")
    if package_manifest.get("source_oci", {}).get("manifest_sha256") != sha256_file(MANIFEST):
        raise RuntimeError("Kaggle package points at a stale OCI source manifest hash")
    expected_uncompressed = sum(row["bytes"] for row in package_manifest["files"])
    disk_gate("before_v2_tar_zst", reserve_bytes=64 * (1 << 20))
    temp = ARCHIVE.with_name(ARCHIVE.name + ".tmp")
    temp.unlink(missing_ok=True)
    subprocess.run(["tar", "--zstd", "-cf", str(temp), "-C", str(INBOX), PACKAGE_NAME], check=True)
    temp_bytes = temp.stat().st_size
    if temp_bytes > MAX_TEMP_BYTES:
        temp.unlink(missing_ok=True)
        raise RuntimeError(f"temporary archive exceeded 200 MiB: {temp_bytes}")
    subprocess.run(["tar", "--zstd", "-tf", str(temp)], check=True, stdout=subprocess.DEVNULL)
    digest = sha256_file(temp)
    archive_bytes = temp.stat().st_size
    os.replace(temp, ARCHIVE)
    SHA_FILE.write_text(f"{digest}  {ARCHIVE.name}\n", encoding="utf-8")
    members = subprocess.run(["tar", "--zstd", "-tf", str(ARCHIVE)], check=True, text=True, capture_output=True).stdout.splitlines()
    run = json.loads(RUN_RECORD.read_text(encoding="utf-8")) if RUN_RECORD.exists() else {}
    run.update({
        "dataset_ref": "polyleviathan/sig-cup-data-004-ets-p0p1-fills",
        "dataset_metadata_path": f"{PACKAGE_NAME}/dataset-metadata.json",
        "visibility": "private",
        "version": "v2",
        "status": "PENDING_TEAM_UPLOAD",
        "source_archive": str(ARCHIVE),
        "source_archive_bytes": archive_bytes,
        "source_archive_sha256": digest,
        "source_archive_sha256_file": str(SHA_FILE),
        "uncompressed_bytes": expected_uncompressed,
        "file_count": len(package_manifest["files"]),
        "archive_member_count": len(members),
        "oci_manifest_object": "research/data004_ets_p0p1/v2/MANIFEST.json",
        "oci_manifest_sha256": sha256_file(MANIFEST),
        "original_v1_oci_manifest_sha256": "048bd5a59642f14c174316590adbdce220f9efabbf571a3a8348da30bc33ce72",
        "superseded_v1_manifest_sha256": "73bbb42db50b9fa3ece571e1e9a7f246f0b1dcdc82479679e9d74e9ac0249ff9",
        "upload_command": "kaggle datasets create -p sig-cup-data-004-ets-p0p1-fills --dir-mode zip",
        "upload_command_cwd": str(INBOX),
        "note": "Prepared for the team to upload manually from the GitHub handoff. This host has no Kaggle CLI or credentials; no upload was attempted.",
        "github_handoff_repo_path": "data/kaggle_handoff/data004/",
        "generated_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    })
    RUN_RECORD.write_text(json.dumps(run, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"archive": str(ARCHIVE), "bytes": archive_bytes, "sha256": digest,
                      "uncompressed_bytes": expected_uncompressed, "files": len(package_manifest["files"]),
                      "members": len(members), "status": run["status"]}, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
