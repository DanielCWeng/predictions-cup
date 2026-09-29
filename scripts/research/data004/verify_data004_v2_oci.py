#!/usr/bin/env python3
"""Read-only verification of the published DATA-004 v2 manifest and payload."""
from __future__ import annotations

import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[3]
SONAR = Path("/home/ubuntu/polymarketwhale/Sonar")
LANE = Path("/home/ubuntu/campaigns/data004_20260929")
V1_MANIFEST_PATH = ROOT / "data/research/data004_ets_p0p1/v1/data004_manifest.json"
V2_MANIFEST_PATH = ROOT / "data/research/data004_ets_p0p1/v2/data004_manifest.json"
BUCKET = "polymarket-bot-state"
V1_PREFIX = "research/data004_ets_p0p1/v1/"
V2_PREFIX = "research/data004_ets_p0p1/v2/"
V2_MANIFEST_OBJECT = V2_PREFIX + "MANIFEST.json"


def sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def object_body_bytes(response: Any) -> bytes:
    body = response.data.raw
    if hasattr(body, "read"):
        return body.read()
    return bytes(body)


def main() -> None:
    if os.geteuid() == 0:
        raise RuntimeError("run as ubuntu; root must not read Sonar/.env")
    load_dotenv(SONAR / ".env", override=False)
    sys.path.insert(0, str(SONAR))
    import config  # type: ignore[import-not-found]
    import oci  # type: ignore[import-not-found]

    signer, config_obj = config.get_oci_signer_and_config()
    client = oci.object_storage.ObjectStorageClient(config_obj, signer=signer) if signer else oci.object_storage.ObjectStorageClient(config_obj)
    namespace = str(client.get_namespace().data)
    v2_local = json.loads(V2_MANIFEST_PATH.read_text(encoding="utf-8"))
    v1_local = json.loads(V1_MANIFEST_PATH.read_text(encoding="utf-8"))
    v2_bytes = object_body_bytes(client.get_object(namespace_name=namespace, bucket_name=BUCKET, object_name=V2_MANIFEST_OBJECT))
    v1_bytes = object_body_bytes(client.get_object(namespace_name=namespace, bucket_name=BUCKET, object_name=V1_PREFIX + "MANIFEST.json"))
    v2_remote_sha = sha256(v2_bytes)
    v1_remote_sha = sha256(v1_bytes)
    if v2_remote_sha != sha256(V2_MANIFEST_PATH.read_bytes()):
        raise RuntimeError("remote v2 manifest bytes differ from the reviewed local manifest")
    if v1_remote_sha != sha256(V1_MANIFEST_PATH.read_bytes()):
        raise RuntimeError("remote v1 lifecycle manifest differs from its supersession annotation")

    object_sizes_checked = 0
    object_bytes_checked = 0
    for row in v2_local["files"]:
        head = client.head_object(namespace_name=namespace, bucket_name=BUCKET, object_name=row["path"])
        size = int(head.headers.get("content-length", -1))
        if size != int(row["bytes"]):
            raise RuntimeError(f"remote size mismatch for {row['path']}: {size} != {row['bytes']}")
        object_sizes_checked += 1
        object_bytes_checked += size

    sampled: list[dict[str, Any]] = []
    v1_by_day = {row.get("date"): row for row in v1_local["files"] if row.get("kind") == "fills"}
    v2_by_day = {row.get("date"): row for row in v2_local["files"] if row.get("kind") == "fills"}
    for day in ("2026-09-20", "2026-09-21"):
        v2_row, v1_row = v2_by_day[day], v1_by_day[day]
        for version, row in (("v2", v2_row), ("v1", v1_row)):
            payload = object_body_bytes(client.get_object(namespace_name=namespace, bucket_name=BUCKET, object_name=row["path"]))
            actual = sha256(payload)
            expected = row["sha256"]
            if actual != expected:
                raise RuntimeError(f"remote sample hash mismatch for {version} {day}: {actual} != {expected}")
            sampled.append({"version": version, "date": day, "path": row["path"], "bytes": len(payload), "sha256": actual})

    result = {
        "verified_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "bucket": BUCKET,
        "v2_manifest_object": V2_MANIFEST_OBJECT,
        "v2_manifest_sha256": v2_remote_sha,
        "v1_status_manifest_object": V1_PREFIX + "MANIFEST.json",
        "v1_status_manifest_sha256": v1_remote_sha,
        "v1_status": v1_local["status"],
        "v2_status": v2_local["status"],
        "v2_payload_objects_size_checked": object_sizes_checked,
        "v2_payload_bytes_size_checked": object_bytes_checked,
        "sampled_parquet_hashes": sampled,
        "v1_payload_objects_modified": 0,
    }
    path = LANE / "data004_a3_oci_verify.json"
    path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
