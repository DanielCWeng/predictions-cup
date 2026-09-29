#!/usr/bin/env python3
"""Fail-closed repository-native Kaggle dataset upload helper."""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

ROOT = Path.cwd().resolve()


def run(args: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    print("+", " ".join(args), flush=True)
    result = subprocess.run(args, cwd=ROOT, text=True, capture_output=True)
    if result.stdout:
        print(result.stdout, end="" if result.stdout.endswith("\n") else "\n")
    if result.stderr:
        print(result.stderr, file=sys.stderr, end="" if result.stderr.endswith("\n") else "\n")
    if check and result.returncode != 0:
        raise RuntimeError(f"Command failed with exit code {result.returncode}: {' '.join(args)}")
    return result


def repo_path(raw: str) -> Path:
    path = (ROOT / raw).resolve()
    path.relative_to(ROOT)
    return path


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def require_equal(label: str, actual: Any, expected: Any) -> None:
    if actual != expected:
        raise RuntimeError(f"{label} mismatch: actual={actual!r} expected={expected!r}")


def verify_package(data: dict[str, Any], work: Path) -> tuple[Path, dict[str, Any]]:
    archive = repo_path(str(data["archive_path"]))
    checksum_path = repo_path(str(data["checksum_path"]))
    expected_archive_sha = str(data["expected_archive_sha256"]).lower()
    actual_archive_sha = sha256_file(archive)
    require_equal("archive sha256", actual_archive_sha, expected_archive_sha)

    checksum_tokens = checksum_path.read_text(encoding="utf-8").strip().split()
    if not checksum_tokens:
        raise RuntimeError("checksum file is empty")
    require_equal("checksum-file sha256", checksum_tokens[0].lower(), expected_archive_sha)

    extract_dir = work / "extracted"
    extract_dir.mkdir()
    run(["tar", "--zstd", "-xf", str(archive), "-C", str(extract_dir)])
    package_root = extract_dir / str(data["package_root"])
    if not package_root.is_dir():
        raise RuntimeError(f"expected package root missing: {package_root}")

    metadata = load_json(package_root / "dataset-metadata.json")
    require_equal("dataset metadata id", metadata.get("id"), data["dataset_ref"])
    require_equal("dataset metadata private", metadata.get("private"), True)

    package_manifest_path = package_root / "MANIFEST.json"
    package_manifest = load_json(package_manifest_path)
    require_equal("package version", package_manifest.get("version"), data["expected_package_version"])
    require_equal("package status", package_manifest.get("status"), data["expected_status"])
    require_equal(
        "package source manifest sha256",
        package_manifest.get("source_oci", {}).get("manifest_sha256"),
        data["expected_oci_manifest_sha256"],
    )
    for key, expected in data.get("expected_scope", {}).items():
        require_equal(f"package scope {key}", package_manifest.get("scope", {}).get(key), expected)

    source_manifest_path = package_root / "OCI_SOURCE_MANIFEST.json"
    require_equal(
        "OCI source manifest file sha256",
        sha256_file(source_manifest_path),
        data["expected_oci_manifest_sha256"],
    )
    source_manifest = load_json(source_manifest_path)
    require_equal("OCI source version", source_manifest.get("version"), data["expected_package_version"])
    require_equal("OCI source status", source_manifest.get("status"), data["expected_status"])
    expected_counts = data.get("expected_counts", {})
    for key, expected in expected_counts.items():
        require_equal(f"OCI source count {key}", source_manifest.get("counts", {}).get(key), expected)

    listed_files = package_manifest.get("files", [])
    require_equal("package manifest file count", len(listed_files), int(data["expected_file_count"]))
    for row in listed_files:
        rel = Path(str(row["path"]))
        candidate = (package_root / rel).resolve()
        candidate.relative_to(package_root.resolve())
        if not candidate.is_file():
            raise RuntimeError(f"manifest file missing: {rel}")
        require_equal(f"size {rel}", candidate.stat().st_size, int(row["bytes"]))
        require_equal(f"sha256 {rel}", sha256_file(candidate), row["sha256"])

    evidence = {
        "archive_path": str(data["archive_path"]),
        "archive_sha256": actual_archive_sha,
        "dataset_ref": data["dataset_ref"],
        "package_root": data["package_root"],
        "package_version": package_manifest.get("version"),
        "package_status": package_manifest.get("status"),
        "package_manifest_sha256": sha256_file(package_manifest_path),
        "oci_source_manifest_sha256": sha256_file(source_manifest_path),
        "verified_manifest_files": len(listed_files),
        "verified_scope": package_manifest.get("scope", {}),
        "verified_counts": {
            key: source_manifest.get("counts", {}).get(key) for key in expected_counts
        },
    }
    return package_root, evidence


def find_one(root: Path, name: str) -> Path:
    matches = list(root.rglob(name))
    if len(matches) != 1:
        raise RuntimeError(
            f"expected exactly one {name} in existing Kaggle download, found {len(matches)}"
        )
    return matches[0]


def existing_is_identical(
    data: dict[str, Any], work: Path
) -> tuple[bool, dict[str, Any]]:
    dest = work / "existing"
    dest.mkdir()
    result = run(
        [
            "kaggle",
            "datasets",
            "download",
            "-d",
            str(data["dataset_ref"]),
            "-p",
            str(dest),
            "--unzip",
        ],
        check=False,
    )
    if result.returncode != 0:
        return False, {"download_error": (result.stderr or result.stdout or "").strip()}
    try:
        package_manifest_path = find_one(dest, "MANIFEST.json")
        source_manifest_path = find_one(dest, "OCI_SOURCE_MANIFEST.json")
        manifest = load_json(package_manifest_path)
        same = (
            manifest.get("version") == data["expected_package_version"]
            and manifest.get("status") == data["expected_status"]
            and manifest.get("source_oci", {}).get("manifest_sha256")
            == data["expected_oci_manifest_sha256"]
            and sha256_file(source_manifest_path) == data["expected_oci_manifest_sha256"]
        )
        return same, {
            "existing_package_manifest_sha256": sha256_file(package_manifest_path),
            "existing_oci_source_manifest_sha256": sha256_file(source_manifest_path),
        }
    except Exception as exc:
        return False, {"inspection_error": str(exc)}


def wait_ready(dataset_ref: str, timeout_seconds: int = 600) -> str:
    deadline = time.monotonic() + timeout_seconds
    last = ""
    while time.monotonic() < deadline:
        result = run(["kaggle", "datasets", "status", dataset_ref], check=False)
        last = ((result.stdout or "") + (result.stderr or "")).strip()
        if result.returncode == 0 and "ready" in last.lower():
            return last
        if (
            any(token in last.lower() for token in ("error", "failed"))
            and "not found" not in last.lower()
        ):
            raise RuntimeError(f"Kaggle dataset entered failure state: {last}")
        time.sleep(10)
    raise TimeoutError(
        f"Kaggle dataset did not become ready within {timeout_seconds}s: {last}"
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()

    manifest_path = repo_path(args.manifest)
    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    data = load_json(manifest_path)
    require_equal("manifest action", data.get("action"), "dataset_upload")
    require_equal(
        "dataset ref",
        data.get("dataset_ref"),
        "polyleviathan/sig-cup-data-004-ets-p0p1-fills",
    )
    require_equal(
        "existing policy", data.get("existing_policy"), "reuse_if_manifest_matches"
    )

    with tempfile.TemporaryDirectory(prefix="kaggle-dataset-upload-") as tmp:
        work = Path(tmp)
        package_root, evidence = verify_package(data, work)

        status_before = run(
            ["kaggle", "datasets", "status", str(data["dataset_ref"])], check=False
        )
        status_text = (
            (status_before.stdout or "") + (status_before.stderr or "")
        ).strip()
        if status_before.returncode == 0:
            identical, existing_evidence = existing_is_identical(data, work)
            evidence.update(existing_evidence)
            if not identical:
                raise RuntimeError(
                    "Kaggle dataset already exists but does not match the frozen "
                    "DATA-004 v2 provenance; refusing overwrite/version mutation"
                )
            evidence["upload_disposition"] = "REUSED_IDENTICAL_EXISTING"
            evidence["kaggle_version"] = None
            evidence["status"] = status_text
        else:
            lowered = status_text.lower()
            not_found = any(
                token in lowered for token in ("404", "not found", "does not exist")
            )
            if not not_found:
                raise RuntimeError(
                    f"Could not safely determine dataset existence: {status_text}"
                )
            run(
                [
                    "kaggle",
                    "datasets",
                    "create",
                    "-p",
                    str(package_root),
                    "--dir-mode",
                    "zip",
                ]
            )
            evidence["upload_disposition"] = "CREATED_NEW_DATASET"
            evidence["kaggle_version"] = 1
            evidence["status"] = wait_ready(str(data["dataset_ref"]))

        evidence["verified_private_metadata"] = True
        (output_dir / "dataset_upload_result.json").write_text(
            json.dumps(evidence, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print(json.dumps(evidence, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
