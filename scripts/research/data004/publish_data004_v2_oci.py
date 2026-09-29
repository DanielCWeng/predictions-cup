#!/usr/bin/env python3
"""Verify/publish DATA-004 v2 payloads and version its reviewed manifest."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[3]
SONAR = Path("/home/ubuntu/polymarketwhale/Sonar")
LANE = Path("/home/ubuntu/campaigns/data004_20260929")
INBOX = Path("/home/ubuntu/inbox/data004_20260929")
PACKAGE = INBOX / "sig-cup-data-004-ets-p0p1-fills"
V2_MANIFEST_PATH = ROOT / "data/research/data004_ets_p0p1/v2/data004_manifest.json"
V1_MANIFEST_PATH = ROOT / "data/research/data004_ets_p0p1/v1/data004_manifest.json"
V1_QUALITY_PATH = ROOT / "data/research/data004_ets_p0p1/v1/data004_quality.json"
V1_ORIGINAL_MANIFEST_SHA = "048bd5a59642f14c174316590adbdce220f9efabbf571a3a8348da30bc33ce72"
BUCKET = "polymarket-bot-state"
V1_PREFIX = "research/data004_ets_p0p1/v1/"
V2_PREFIX = "research/data004_ets_p0p1/v2/"
MIN_FREE_BYTES = 6 * (1 << 30)
HARD_MIN_FREE_BYTES = 5 * (1 << 30)


def sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def disk_gate(stage: str, reserve_bytes: int = 1 << 20) -> int:
    log = LANE / "data004_a3_disk_check.jsonl"
    started = time.monotonic()
    while True:
        result = subprocess.run(
            ["df", "-B1", "--output=avail", str(INBOX)], check=True, text=True, capture_output=True
        )
        available = int([line.strip() for line in result.stdout.splitlines() if line.strip()][-1])
        with log.open("a", encoding="utf-8") as handle:
            handle.write(
                json.dumps(
                    {
                        "stage": stage,
                        "available_bytes": available,
                        "reserve_bytes": reserve_bytes,
                        "checked_at_utc": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                    }
                )
                + "\n"
            )
        if available < HARD_MIN_FREE_BYTES:
            raise RuntimeError(f"disk below hard 5 GiB floor at {stage}: {available}")
        if available >= MIN_FREE_BYTES + reserve_bytes:
            return available
        if time.monotonic() - started > 2 * 60 * 60:
            raise RuntimeError(f"disk gate timed out at {stage}: {available}")
        print(
            f"PAUSED disk gate stage={stage} free_bytes={available}; rechecking in 5 minutes",
            flush=True,
        )
        time.sleep(300)


def object_body_bytes(response: Any) -> bytes:
    body = response.data.raw
    if hasattr(body, "read"):
        return body.read()
    return bytes(body)


def get_object_bytes(client: Any, namespace: str, object_name: str) -> bytes:
    response = client.get_object(
        namespace_name=namespace, bucket_name=BUCKET, object_name=object_name
    )
    return object_body_bytes(response)


def head_or_none(client: Any, namespace: str, object_name: str) -> Any | None:
    import oci

    try:
        return client.head_object(
            namespace_name=namespace, bucket_name=BUCKET, object_name=object_name
        )
    except oci.exceptions.ServiceError as exc:
        if exc.status == 404:
            return None
        raise


def main() -> None:
    if os.geteuid() == 0:
        raise RuntimeError("run as ubuntu; root must not read Sonar/.env")
    load_dotenv(SONAR / ".env", override=False)
    sys.path.insert(0, str(SONAR))
    import config  # type: ignore[import-not-found]
    import oci  # type: ignore[import-not-found]

    manifest = json.loads(V2_MANIFEST_PATH.read_text(encoding="utf-8"))
    if manifest.get("version") != "v2" or manifest.get("status") not in {
        "BLOCKED_QUALITY_GATE",
        "ACCEPTED_V2",
    }:
        raise RuntimeError("refusing to publish a non-v2 or unreviewed manifest")
    if manifest.get("status") == "ACCEPTED_V2":
        addendum_4 = manifest.get("addendum_4", {})
        if (
            addendum_4.get("status") != "ACCEPTED_V2"
            or addendum_4.get("fill_values_changed") != 0
            or not addendum_4.get("source_quantum_tolerance", {}).get("all_groups_within_tolerance")
        ):
            raise RuntimeError("accepted v2 manifest lacks the Addendum 4 source-quantum evidence")
    v1_manifest = json.loads(V1_MANIFEST_PATH.read_text(encoding="utf-8"))
    if (
        v1_manifest.get("original_manifest_sha256") != V1_ORIGINAL_MANIFEST_SHA
        or v1_manifest.get("status") != "BLOCKED_SUPERSEDED"
    ):
        raise RuntimeError("v1 source manifest lacks the required blocked/superseded annotation")

    signer, config_obj = config.get_oci_signer_and_config()
    client = (
        oci.object_storage.ObjectStorageClient(config_obj, signer=signer)
        if signer
        else oci.object_storage.ObjectStorageClient(config_obj)
    )
    namespace = str(client.get_namespace().data)
    uploaded: list[dict[str, Any]] = []
    for index, entry in enumerate(manifest["files"], 1):
        object_name = str(entry["path"])
        if not object_name.startswith(V2_PREFIX):
            raise RuntimeError(f"non-v2 object path in manifest: {object_name}")
        disk_gate(f"before_v2_object_{index:03d}_{entry.get('date', 'metadata')}")
        if entry.get("kind") == "fills":
            day = str(entry["date"])
            local_dir = PACKAGE / "fills" / f"date={day}"
            matches = sorted(local_dir.glob("*.parquet"))
            if len(matches) != 1:
                raise RuntimeError(f"expected one local Parquet for {day}, found {len(matches)}")
            local_path = matches[0]
            if (
                local_path.stat().st_size != int(entry["bytes"])
                or sha256_file(local_path) != entry["sha256"]
            ):
                raise RuntimeError(f"local fill object hash/size mismatch: {local_path}")
            payload = local_path.read_bytes()
        else:
            relative = object_name.removeprefix(V2_PREFIX)
            old_object = V1_PREFIX + relative
            payload = get_object_bytes(client, namespace, old_object)
            if len(payload) != int(entry["bytes"]) or sha256(payload) != entry["sha256"]:
                raise RuntimeError(f"v1 metadata object differs from the v2 manifest: {old_object}")

        existing = head_or_none(client, namespace, object_name)
        if existing is not None:
            existing_payload = get_object_bytes(client, namespace, object_name)
            if (
                len(existing_payload) != int(entry["bytes"])
                or sha256(existing_payload) != entry["sha256"]
            ):
                raise RuntimeError(
                    f"v2 prefix already contains different bytes at {object_name}; refusing overwrite"
                )
            uploaded.append(
                {
                    "path": object_name,
                    "bytes": len(payload),
                    "sha256": entry["sha256"],
                    "action": "verified_existing",
                }
            )
            continue
        response = client.put_object(
            namespace_name=namespace,
            bucket_name=BUCKET,
            object_name=object_name,
            put_object_body=payload,
        )
        head = client.head_object(
            namespace_name=namespace, bucket_name=BUCKET, object_name=object_name
        )
        response_size = int(head.headers.get("content-length", -1))
        if response_size != int(entry["bytes"]):
            raise RuntimeError(
                f"OCI object size verification failed for {object_name}: {response_size}"
            )
        uploaded.append(
            {
                "path": object_name,
                "bytes": len(payload),
                "sha256": entry["sha256"],
                "etag": response.headers.get("etag"),
                "action": "uploaded",
            }
        )
        if index % 50 == 0 or index == len(manifest["files"]):
            print(f"published v2 objects {index}/{len(manifest['files'])}", flush=True)

    manifest_bytes = V2_MANIFEST_PATH.read_bytes()
    manifest_sha = sha256(manifest_bytes)
    final_name = V2_PREFIX + "MANIFEST.json"
    disk_gate("before_v2_final_manifest", reserve_bytes=len(manifest_bytes) + (1 << 20))
    manifest_transition: dict[str, Any] | None = None
    if head_or_none(client, namespace, final_name) is not None:
        existing = get_object_bytes(client, namespace, final_name)
        if sha256(existing) != manifest_sha:
            old_manifest = json.loads(existing)
            new_manifest = json.loads(manifest_bytes)
            addendum_4 = new_manifest.get("addendum_4", {})
            expected_old_sha = addendum_4.get("previous_manifest_sha256")
            archive_name = addendum_4.get("previous_manifest_object")
            if (
                old_manifest.get("status") != "BLOCKED_QUALITY_GATE"
                or new_manifest.get("status") != "ACCEPTED_V2"
                or expected_old_sha != sha256(existing)
                or old_manifest.get("files") != new_manifest.get("files")
                or not archive_name
            ):
                raise RuntimeError(
                    "refusing v2 manifest transition that is not the Addendum 4 acceptance of unchanged payload objects"
                )
            archived = head_or_none(client, namespace, archive_name)
            if archived is None:
                client.put_object(
                    namespace_name=namespace,
                    bucket_name=BUCKET,
                    object_name=archive_name,
                    put_object_body=existing,
                )
            else:
                archived_payload = get_object_bytes(client, namespace, archive_name)
                if sha256(archived_payload) != sha256(existing):
                    raise RuntimeError(
                        f"blocked v2 manifest archive already exists with different bytes: {archive_name}"
                    )
            client.put_object(
                namespace_name=namespace,
                bucket_name=BUCKET,
                object_name=final_name,
                put_object_body=manifest_bytes,
            )
            manifest_transition = {
                "from_status": old_manifest.get("status"),
                "to_status": new_manifest.get("status"),
                "previous_manifest_object": archive_name,
                "previous_manifest_sha256": sha256(existing),
                "payload_file_list_unchanged": True,
            }
    else:
        client.put_object(
            namespace_name=namespace,
            bucket_name=BUCKET,
            object_name=final_name,
            put_object_body=manifest_bytes,
        )
    final_head = client.head_object(
        namespace_name=namespace, bucket_name=BUCKET, object_name=final_name
    )
    if int(final_head.headers.get("content-length", -1)) != len(manifest_bytes):
        raise RuntimeError("v2 final manifest size verification failed")

    # The v1 payload remains byte-for-byte untouched. Only its rejected lifecycle
    # manifest is replaced to mark that v1 was blocked and superseded by v2.
    v1_bytes = V1_MANIFEST_PATH.read_bytes()
    v1_sha = sha256(v1_bytes)
    disk_gate("before_v1_supersession_manifest", reserve_bytes=len(v1_bytes) + (1 << 20))
    v1_object_name = V1_PREFIX + "MANIFEST.json"
    prior_v1_bytes = get_object_bytes(client, namespace, v1_object_name)
    prior_v1_sha = sha256(prior_v1_bytes)
    if prior_v1_sha == V1_ORIGINAL_MANIFEST_SHA:
        client.put_object(
            namespace_name=namespace,
            bucket_name=BUCKET,
            object_name=v1_object_name,
            put_object_body=v1_bytes,
        )
    elif prior_v1_sha != v1_sha:
        raise RuntimeError(f"v1 manifest changed unexpectedly: {prior_v1_sha}")
    v1_head = client.head_object(
        namespace_name=namespace, bucket_name=BUCKET, object_name=V1_PREFIX + "MANIFEST.json"
    )
    if int(v1_head.headers.get("content-length", -1)) != len(v1_bytes):
        raise RuntimeError("v1 supersession manifest size verification failed")

    record = {
        "dataset_id": "DATA-004",
        "published_utc": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "bucket": BUCKET,
        "v2_prefix": V2_PREFIX,
        "v2_objects": len(uploaded),
        "v2_bytes": sum(int(row["bytes"]) for row in uploaded),
        "v2_object_sha256_from_manifest": manifest_sha,
        "v2_final_manifest_sha256": manifest_sha,
        "v1_prefix": V1_PREFIX,
        "v1_parquet_objects_changed": 0,
        "v1_status_manifest_sha256": v1_sha,
        "v1_original_manifest_sha256": V1_ORIGINAL_MANIFEST_SHA,
        "upload_actions": {
            action: sum(row["action"] == action for row in uploaded)
            for action in {row["action"] for row in uploaded}
        },
        "objects": uploaded,
    }
    path = LANE / (
        "data004_a4_oci_publish.json"
        if json.loads(manifest_bytes).get("status") == "ACCEPTED_V2"
        else "data004_a3_oci_publish.json"
    )
    record["manifest_transition"] = manifest_transition
    write_json(path, record)
    print(
        json.dumps(
            {
                "v2_objects": record["v2_objects"],
                "v2_bytes": record["v2_bytes"],
                "v2_manifest_sha256": manifest_sha,
                "v1_manifest_sha256": v1_sha,
                "v1_parquet_objects_changed": 0,
            },
            sort_keys=True,
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
