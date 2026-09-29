#!/usr/bin/env python3
"""Build the DATA-004 Kaggle upload bundle from its finalized OCI source copy."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
LANE = Path("/home/ubuntu/campaigns/data004_20260929")
SONAR_ROOT = Path("/home/ubuntu/polymarketwhale/Sonar")
INBOX = Path("/home/ubuntu/inbox/data004_20260929")
PACKAGE_NAME = "sig-cup-data-004-ets-p0p1-fills"
DATASET_ID = "polyleviathan/sig-cup-data-004-ets-p0p1-fills"
BUCKET = "polymarket-bot-state"
DATA_PREFIX = "research/data004_ets_p0p1/v1/"
FINAL_MANIFEST_OBJECT = DATA_PREFIX + "MANIFEST.json"
FROZEN_GRAPH_DIR = ROOT / "data/research/r25_ets_math_graph"
FROZEN_UNIVERSE_FILES = [
    "ETS_FILL_ACQUISITION.csv",
    "ETS_GAMMA_SEMANTIC_AUDIT.csv",
    "ETS_MARKET_GRAPH.csv",
    "ETS_SIG_ANCHOR_GRAPH.csv",
    "ETS_RELATIONSHIP_TAXONOMY.json",
    "ETS_COMPONENTS.json",
]
MIN_FREE_BYTES = 6 * (1 << 30)
HARD_MIN_FREE_BYTES = 5 * (1 << 30)
MAX_WAIT_SECONDS = 2 * 60 * 60
CHUNK_BYTES = 1 << 20


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(CHUNK_BYTES), b""):
            digest.update(block)
    return digest.hexdigest()


def json_bytes(value: Any) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(json_bytes(value))


def disk_gate(path: Path, stage: str, reserve_bytes: int = 0) -> int:
    check_log = LANE / "data004_package_disk_check.jsonl"
    started = time.monotonic()
    while True:
        result = subprocess.run(
            ["df", "-B1", "--output=avail", str(path)], check=True, text=True, capture_output=True
        )
        available = int([line.strip() for line in result.stdout.splitlines() if line.strip()][-1])
        entry = {
            "checked_at_utc": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "stage": stage,
            "available_bytes": available,
            "reserve_bytes": reserve_bytes,
            "min_free_required_bytes": MIN_FREE_BYTES,
            "hard_floor_bytes": HARD_MIN_FREE_BYTES,
        }
        with check_log.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        if available < HARD_MIN_FREE_BYTES:
            raise RuntimeError(
                f"disk is already below the 5 GiB hard floor at {stage}: {available}"
            )
        if available >= MIN_FREE_BYTES and available - reserve_bytes >= HARD_MIN_FREE_BYTES:
            return available
        elapsed = time.monotonic() - started
        if elapsed >= MAX_WAIT_SECONDS:
            raise RuntimeError(
                f"disk gate timed out after two hours at {stage}: {available} bytes free"
            )
        print(
            f"PAUSED disk gate stage={stage} free_bytes={available}; recheck in 5 minutes "
            f"(elapsed={int(elapsed)}s, max=7200s)",
            flush=True,
        )
        time.sleep(300)


def atomic_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


def object_body_bytes(response: Any) -> bytes:
    body = response.data.raw
    if hasattr(body, "read"):
        return body.read()
    return bytes(body)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--package-parent", type=Path, default=INBOX)
    parser.add_argument(
        "--repo-output", type=Path, default=ROOT / "data/research/data004_ets_p0p1/v1"
    )
    parser.add_argument("--lane", type=Path, default=LANE)
    args = parser.parse_args()
    if os.geteuid() == 0:
        raise RuntimeError("run as ubuntu; root must not read Sonar/.env")

    package_parent = args.package_parent.resolve()
    package_root = package_parent / PACKAGE_NAME
    archive_path = package_parent / f"{PACKAGE_NAME}.tar.zst"
    repo_output = args.repo_output.resolve()
    lane = args.lane.resolve()
    manifest_path = repo_output / "data004_manifest.json"
    quality_path = repo_output / "data004_quality.json"
    checkpoint_path = lane / "data004_checkpoint.json"
    summary_path = lane / "data004_export_summary.json"
    manifest_payload = manifest_path.read_bytes()
    manifest_sha = sha256_bytes(manifest_payload)
    source_manifest = json.loads(manifest_payload)
    quality = json.loads(quality_path.read_text(encoding="utf-8"))
    checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    export_summary = json.loads(summary_path.read_text(encoding="utf-8"))
    if manifest_sha != export_summary.get("manifest_sha256"):
        raise RuntimeError(
            "checked-in source manifest hash differs from the finalized export summary"
        )
    if source_manifest.get("status") != "BLOCKED_QUALITY_GATE":
        raise RuntimeError("DATA-004 status changed; review package text before using this builder")
    if quality.get("all_gates_pass"):
        raise RuntimeError("quality file disagrees with the blocked source manifest")
    if package_root.exists() or archive_path.exists():
        raise FileExistsError(
            f"refusing to overwrite existing Kaggle package: {package_root} or {archive_path}"
        )

    # Prepare compact source-day and condition-level coverage records in Git as well as the bundle.
    disk_gate(Path("/home/ubuntu/inbox"), "before_local_coverage_outputs", reserve_bytes=1 << 20)
    market_rows = source_manifest["validation"]["coverage_by_market"]
    condition_fields = [
        "condition_id",
        "market_id",
        "event_id",
        "acquisition_class",
        "acquisition_tier",
        "created_at_utc",
        "end_date_utc",
        "fill_rows",
        "tokens_expected",
        "tokens_with_rows",
        "first_fill_utc",
        "last_fill_utc",
        "first_fill_minus_created_seconds",
        "last_fill_minus_end_seconds",
        "missing_block_rows",
        "condition_id_mismatch_rows",
        "zero_fill_reason",
    ]
    condition_rows = [dict(row) for row in market_rows]
    condition_rows.sort(key=lambda row: str(row["condition_id"]))
    atomic_csv(repo_output / "condition_coverage.csv", condition_rows, condition_fields)

    source_days = checkpoint.get("day_coverage", [])
    counts = source_manifest["counts"]
    source_day_fields = [
        "date",
        "trade_object",
        "trade_etag",
        "trade_bytes",
        "custody_object",
        "custody_etag",
        "custody_bytes",
        "raw_matched_fill_rows",
        "deduped_fill_rows",
        "duplicate_rows_removed",
        "duplicate_key_groups",
        "conflicting_duplicate_groups",
        "condition_id_mismatch_rows",
        "block_number_missing_rows",
        "block_number_missing_reason_json",
        "status",
        "fill_objects_json",
    ]
    source_day_rows = []
    for row in sorted(source_days, key=lambda item: item["date"]):
        trade = row.get("source_trade_object") or {}
        custody = row.get("source_custody_object") or {}
        source_day_rows.append(
            {
                "date": row["date"],
                "trade_object": trade.get("object_name", ""),
                "trade_etag": trade.get("etag", ""),
                "trade_bytes": trade.get("size", ""),
                "custody_object": custody.get("object_name", ""),
                "custody_etag": custody.get("etag", ""),
                "custody_bytes": custody.get("size", ""),
                "raw_matched_fill_rows": row.get("raw_matched_fill_rows", 0),
                "deduped_fill_rows": row.get("deduped_fill_rows", 0),
                "duplicate_rows_removed": row.get("duplicate_rows_removed", 0),
                "duplicate_key_groups": row.get("duplicate_key_groups", 0),
                "conflicting_duplicate_groups": row.get("conflicting_duplicate_groups", 0),
                "condition_id_mismatch_rows": row.get("condition_id_mismatch_rows", 0),
                "block_number_missing_rows": row.get("block_number_missing_rows", 0),
                "block_number_missing_reason_json": json.dumps(
                    row.get("block_number_missing_reason", {}), sort_keys=True
                ),
                "status": row.get("status", ""),
                "fill_objects_json": json.dumps(row.get("files", []), separators=(",", ":")),
            }
        )
    atomic_csv(repo_output / "source_day_inventory.csv", source_day_rows, source_day_fields)

    full_file_records: list[dict[str, Any]] = []
    full_acquisition_rows: list[dict[str, str]] = []
    full_graph_rows = 0
    full_anchor_rows = 0
    for name in FROZEN_UNIVERSE_FILES:
        path = FROZEN_GRAPH_DIR / name
        record: dict[str, Any] = {
            "path": str(path.relative_to(ROOT)),
            "bytes": path.stat().st_size,
            "sha256": sha256_file(path),
        }
        if path.suffix == ".csv":
            with path.open(encoding="utf-8-sig", newline="") as handle:
                rows = list(csv.DictReader(handle))
            record["rows"] = len(rows)
            if name == "ETS_FILL_ACQUISITION.csv":
                full_acquisition_rows = rows
            elif name == "ETS_MARKET_GRAPH.csv":
                full_graph_rows = len(rows)
            elif name == "ETS_SIG_ANCHOR_GRAPH.csv":
                full_anchor_rows = len(rows)
        full_file_records.append(record)
    full_market_ids = {str(row["market_id"]) for row in full_acquisition_rows}
    full_condition_ids = {str(row["condition_id"]) for row in full_acquisition_rows}
    full_token_ids = {
        str(row[key])
        for row in full_acquisition_rows
        for key in ("token_yes", "token_no")
        if row.get(key)
    }
    full_book_needed = sum(
        row.get("book_needed_later", "").strip().lower() == "true" for row in full_acquisition_rows
    )
    if (
        len(full_market_ids),
        len(full_condition_ids),
        len(full_token_ids),
        full_graph_rows,
        full_anchor_rows,
        full_book_needed,
    ) != (1279, 1279, 2558, 5422, 231, 797):
        raise RuntimeError(
            "full frozen universe metadata differs from the expected 1,279/2,558/5,422/231/797 freeze"
        )
    full_universe_manifest = {
        "dataset_id": "DATA-004",
        "purpose": "Preserve the full frozen R2.5 ETS candidate metadata while fills are limited to P0/P1.",
        "frozen_candidate_markets": len(full_market_ids),
        "frozen_candidate_conditions": len(full_condition_ids),
        "frozen_candidate_tokens": len(full_token_ids),
        "selected_fill_markets": counts["markets"],
        "book_needed_later_markets": full_book_needed,
        "market_graph_rows": full_graph_rows,
        "sig_anchor_rows": full_anchor_rows,
        "files": full_file_records,
    }
    write_json(repo_output / "full_universe_manifest.json", full_universe_manifest)

    disk_gate(package_parent.parent, "before_package_parent_create")
    package_parent.mkdir(parents=True, exist_ok=True)
    disk_gate(package_parent, "before_package_create", reserve_bytes=300 * (1 << 20))
    package_root.mkdir(parents=True)
    (package_root / "fills").mkdir()

    from dotenv import load_dotenv

    load_dotenv(SONAR_ROOT / ".env", override=False)
    sys.path.insert(0, str(SONAR_ROOT))
    import config
    import oci

    signer, cfg = config.get_oci_signer_and_config()
    client = (
        oci.object_storage.ObjectStorageClient(cfg, signer=signer)
        if signer
        else oci.object_storage.ObjectStorageClient(cfg)
    )
    namespace = client.get_namespace().data

    remote_manifest = object_body_bytes(
        client.get_object(
            namespace_name=namespace, bucket_name=BUCKET, object_name=FINAL_MANIFEST_OBJECT
        )
    )
    if sha256_bytes(remote_manifest) != manifest_sha:
        raise RuntimeError(
            "immutable OCI manifest bytes do not match the reviewed Git/lane manifest"
        )

    fill_files = [row for row in source_manifest["files"] if row.get("kind") == "fills"]
    package_file_rows: list[dict[str, Any]] = []
    for index, row in enumerate(sorted(fill_files, key=lambda item: item["path"])):
        object_name = str(row["path"])
        if int(row["bytes"]) > 200 * (1 << 20):
            raise RuntimeError(f"OCI fill object exceeds the 200 MB local file cap: {object_name}")
        relative = PurePosixPath(object_name).relative_to(PurePosixPath(DATA_PREFIX))
        if relative.parts[0] != "fills" or ".." in relative.parts:
            raise RuntimeError(
                f"unexpected fill object path outside the DATA-004 prefix: {object_name}"
            )
        destination = package_root.joinpath(*relative.parts)
        destination.parent.mkdir(parents=True, exist_ok=True)
        disk_gate(
            package_parent,
            f"before_download:{index + 1}/{len(fill_files)}:{object_name}",
            reserve_bytes=int(row["bytes"]),
        )
        temp_path = destination.with_suffix(destination.suffix + ".partial")
        response = client.get_object(
            namespace_name=namespace, bucket_name=BUCKET, object_name=object_name
        )
        body = response.data.raw
        digest = hashlib.sha256()
        total = 0
        with temp_path.open("wb") as handle:
            if hasattr(body, "read"):
                while True:
                    block = body.read(CHUNK_BYTES)
                    if not block:
                        break
                    handle.write(block)
                    digest.update(block)
                    total += len(block)
            else:
                block = bytes(body)
                handle.write(block)
                digest.update(block)
                total = len(block)
            handle.flush()
            os.fsync(handle.fileno())
        if total != int(row["bytes"]) or digest.hexdigest() != row["sha256"]:
            temp_path.unlink(missing_ok=True)
            raise RuntimeError(
                f"downloaded package chunk failed byte/hash verification: {object_name}"
            )
        os.replace(temp_path, destination)
        package_file_rows.append(
            {
                "path": relative.as_posix(),
                "bytes": total,
                "rows": int(row["rows"]),
                "sha256": digest.hexdigest(),
                "source_oci_object": object_name,
            }
        )
        if (index + 1) % 50 == 0 or index + 1 == len(fill_files):
            print(
                f"downloaded={index + 1}/{len(fill_files)} fill_files bytes={sum(item['bytes'] for item in package_file_rows)}",
                flush=True,
            )

    evidence_files = {
        "OCI_SOURCE_MANIFEST.json": repo_output / "data004_manifest.json",
        "QUALITY.json": repo_output / "data004_quality.json",
        "data004_baseline_pairing.json": repo_output / "data004_baseline_pairing.json",
        "full_universe_manifest.json": repo_output / "full_universe_manifest.json",
        "market_coverage.csv": repo_output / "market_coverage.csv",
        "condition_coverage.csv": repo_output / "condition_coverage.csv",
        "token_coverage.csv": repo_output / "token_coverage.csv",
        "event_coverage.csv": repo_output / "event_coverage.csv",
        "date_coverage.csv": repo_output / "date_coverage.csv",
        "source_day_inventory.csv": repo_output / "source_day_inventory.csv",
        "zero_fill_markets.csv": repo_output / "zero_fill_markets.csv",
        "sample_fills.csv": repo_output / "sample_fills.csv",
    }
    for name, source in evidence_files.items():
        disk_gate(
            package_parent, f"before_copy_evidence:{name}", reserve_bytes=source.stat().st_size
        )
        shutil.copy2(source, package_root / name)

    for name in FROZEN_UNIVERSE_FILES:
        source = FROZEN_GRAPH_DIR / name
        destination = package_root / "full_frozen_universe" / name
        disk_gate(
            package_parent, f"before_copy_full_universe:{name}", reserve_bytes=source.stat().st_size
        )
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)

    validation = source_manifest["validation"]
    failures = source_manifest["failures"]
    failure_names = [row["gate"] for row in failures]
    zero_fill = len(validation["zero_fill_markets"])
    gap_days = source_manifest["source"]["dates_missing_custody_object_in_trade_scan"]
    readme = f"""# DATA-004 — ETS P0/P1 Polymarket fills

**Status: {source_manifest["status"]} — review evidence; no predictive result.**

This private Kaggle package contains the frozen DATA-004 fill scope and review evidence. The immutable source copy is in OCI at `{BUCKET}/{DATA_PREFIX}`. Its final manifest is `{FINAL_MANIFEST_OBJECT}` with SHA-256 `{manifest_sha}`.

## Scope

- {counts["markets"]} markets: {source_manifest["universe"]["counts"]["p0_markets"]} P0 and {source_manifest["universe"]["counts"]["p1_markets"]} P1.
- {counts["conditions"]} conditions and {counts["tokens"]} Gamma-aligned outcome tokens.
- {counts["deduped_fill_rows"]:,} rows from {counts["earliest_fill_utc"]} through {counts["latest_fill_utc"]}.
- {len(fill_files)} date-partitioned Parquet files under `fills/date=YYYY-MM-DD/`.
- Metadata for all {full_universe_manifest["frozen_candidate_markets"]:,} frozen candidates / {full_universe_manifest["frozen_candidate_conditions"]:,} conditions / {full_universe_manifest["frozen_candidate_tokens"]:,} tokens is retained under `full_frozen_universe/`; only P0/P1 fills are acquired. The full graph has {full_universe_manifest["market_graph_rows"]:,} rows and {full_universe_manifest["book_needed_later_markets"]:,} markets remain flagged for later book work.

All rows use the deduplication key `(tx_hash, log_index, token_id)`. Block numbers are joined from custody by transaction hash, with source provenance kept per row. Rows without a custody match remain null; none are imputed.

## Review status

The corpus is complete for the listed trade-object date range, but the full quality gate did not pass. Failures: {", ".join(failure_names)}. The custody lake has no objects for {", ".join(gap_days)}, leaving {validation["missing_block_number_rows"]:,} fills without block numbers. The maker/taker size audit also found {validation["symmetry"]["mismatch_groups"]:,} mismatched and {validation["symmetry"]["one_sided_groups"]:,} one-sided match-equivalent groups; exact match pairing is unavailable in the source schema. See `QUALITY.json`, `COVERAGE.md`, and `OCI_SOURCE_MANIFEST.json` before use.

The bundle includes market, condition, token, event, fill-date and source-day coverage, a zero-fill-market file, a 25-row sample, and the DATA-003 baseline pairing plan. The zero-fill file has no market rows because every selected market has observed fills.

The DATA-003 pairing file is a plan for its accepted anchors. DATA-003 histories were not downloaded again in this lane. This bundle contains no order-book data, no R3 or fair-value result, and no outcome-based graph changes.
"""
    disk_gate(package_parent, "before_package_docs", reserve_bytes=1 << 20)
    (package_root / "README.md").write_text(readme, encoding="utf-8")
    coverage = f"""# DATA-004 coverage and quality

## Source inventory and selected history

Metadata for the full frozen candidate universe is preserved in `full_frozen_universe/`:
1,279 markets / conditions / 2,558 tokens with their acquisition classifications and graph context.
The fill payload contains P0/P1 only (298 markets). The 797 markets flagged for later book work
remain metadata only in this delivery.

The live OCI inventory contained {source_manifest["source"]["listed_trade_object_count"]:,} trade objects and {source_manifest["source"]["listed_custody_object_count"]:,} custody objects. Trade objects run from {source_manifest["source"]["first_available_trade_object_day"]} through {source_manifest["source"]["latest_available_trade_object_day"]}; custody objects run through {source_manifest["source"]["latest_available_custody_object_day"]}.

The frozen scan covers {source_manifest["source"]["scan_days_with_trade_objects"]} daily trade objects from {source_manifest["source"]["scan_start_day"]} through {source_manifest["source"]["scan_last_day"]}. There are no missing trade object dates in that range. {len(validation["coverage_by_date"])} dates contain selected fills, and {sum(1 for row in source_days if row.get("raw_matched_fill_rows", 0) == 0)} scanned trade dates contain no selected fills. `source_day_inventory.csv` lists every scanned date, both source-object identities, row counts, block gaps, and fill object paths. `date_coverage.csv` has rows only for dates with fills.

Selected fills range from {counts["earliest_fill_utc"]} to {counts["latest_fill_utc"]}. The full market/condition/token/event/date coverage is in the corresponding CSV files.

## Market coverage

All {counts["markets"]} markets have fills. Zero-fill markets: {zero_fill}. Thus no zero-fill market needed a new-market, no-trading, or source-gap classification. {sum(1 for row in validation["coverage_by_market"] if row["missing_block_rows"] > 0)} markets have at least one fill with missing block provenance; see `market_coverage.csv` and `condition_coverage.csv` for exact counts per market and condition.

## Gates

- Universe: {counts["markets"]} markets / {counts["conditions"]} conditions / {counts["tokens"]} tokens; {source_manifest["universe"]["counts"]["p0_markets"]} P0 and {source_manifest["universe"]["counts"]["p1_markets"]} P1 — pass.
- Gamma token/outcome alignment, graph/anchor coverage, fill-key completeness, deduplication, price bounds, condition identity, core-field missingness, lifecycle bounds, source trade-date coverage, and per-market coverage — pass.
- Raw and deduplicated rows: {counts["raw_source_fill_rows"]:,} / {counts["deduped_fill_rows"]:,}; duplicate key groups and removed duplicate rows: {validation["duplicate_key_groups"]:,} / 0.
- Block provenance: {validation["block_number_provenance_mix"].get("CUSTODY_TX_HASH_JOIN", 0):,} rows joined from custody; {validation["missing_block_number_rows"]:,} have no block number and no imputation. Missing custody objects: {", ".join(gap_days)}.
- Ordering: zero inversions among rows with block numbers; {validation["missing_block_number_rows"]:,} rows cannot be ordered because their block number is absent.
- Maker/taker size audit: {validation["symmetry"]["groups_checked"]:,} paired match-equivalent buckets, {validation["symmetry"]["mismatch_groups"]:,} mismatches, and {validation["symmetry"]["one_sided_groups"]:,} one-sided buckets. The grouping key is `(tx_hash, token_id, timestamp, price)`. The lake lacks a common participant-order match ID, so the bucket audit is approximate and its gate is failed pending a defensible reconciliation.

**Overall status: {source_manifest["status"]}.** Failed gate names: {", ".join(failure_names)}. Do not expand the acquisition scope or run R3 predictive/fair-value work until the custody provenance gap and symmetry audit are resolved.
"""
    (package_root / "COVERAGE.md").write_text(coverage, encoding="utf-8")

    dataset_metadata = {
        "title": "SIG Cup DATA-004 ETS P0/P1 Fills",
        "id": DATASET_ID,
        "private": True,
        "licenses": [{"name": "CC0-1.0"}],
    }
    disk_gate(package_parent, "before_dataset_metadata", reserve_bytes=1 << 20)
    write_json(package_root / "dataset-metadata.json", dataset_metadata)

    all_package_files = []
    source_rows = {row["path"]: row for row in package_file_rows}
    for path in sorted(package_root.rglob("*")):
        if not path.is_file() or path.name == "MANIFEST.json":
            continue
        relative = path.relative_to(package_root).as_posix()
        record: dict[str, Any] = {
            "path": relative,
            "bytes": path.stat().st_size,
            "sha256": sha256_file(path),
        }
        source_row = source_rows.get(relative)
        if source_row:
            record["rows"] = source_row["rows"]
            record["source_oci_object"] = source_row["source_oci_object"]
        all_package_files.append(record)
    package_manifest = {
        "dataset_id": "DATA-004",
        "version": "v1",
        "status": source_manifest["status"],
        "scope": {
            "markets": counts["markets"],
            "conditions": counts["conditions"],
            "tokens": counts["tokens"],
            "p0_markets": source_manifest["universe"]["counts"]["p0_markets"],
            "p1_markets": source_manifest["universe"]["counts"]["p1_markets"],
            "fill_rows": counts["deduped_fill_rows"],
            "frozen_candidate_markets": full_universe_manifest["frozen_candidate_markets"],
            "frozen_candidate_conditions": full_universe_manifest["frozen_candidate_conditions"],
            "frozen_candidate_tokens": full_universe_manifest["frozen_candidate_tokens"],
            "book_needed_later_markets": full_universe_manifest["book_needed_later_markets"],
        },
        "full_universe_manifest": "full_universe_manifest.json",
        "source_oci": {
            "bucket": BUCKET,
            "object_prefix": DATA_PREFIX,
            "manifest_object": FINAL_MANIFEST_OBJECT,
            "manifest_sha256": manifest_sha,
        },
        "files": all_package_files,
    }
    disk_gate(package_parent, "before_package_manifest", reserve_bytes=1 << 20)
    write_json(package_root / "MANIFEST.json", package_manifest)

    disk_gate(package_parent, "before_archive", reserve_bytes=300 * (1 << 20))
    result = subprocess.run(
        ["tar", "--zstd", "-cf", str(archive_path), "-C", str(package_parent), PACKAGE_NAME],
        check=True,
        text=True,
        capture_output=True,
    )
    del result
    archive_bytes = archive_path.stat().st_size
    archive_sha = sha256_file(archive_path)
    listed = subprocess.run(
        ["tar", "--zstd", "-tf", str(archive_path)], check=True, text=True, capture_output=True
    ).stdout.splitlines()
    if not listed or not all(name.startswith(PACKAGE_NAME + "/") for name in listed):
        raise RuntimeError("archive contents do not have the expected package root")
    uncompressed_bytes = sum(
        path.stat().st_size for path in package_root.rglob("*") if path.is_file()
    )
    kaggle_record = {
        "dataset_ref": DATASET_ID,
        "visibility": "private",
        "dataset_metadata_path": f"{PACKAGE_NAME}/dataset-metadata.json",
        "upload_command": f"kaggle datasets create -p {PACKAGE_NAME} --dir-mode zip",
        "upload_command_cwd": str(package_parent),
        "note": "Prepared for manual upload; this host has no Kaggle CLI or credentials. The OCI v1 copy remains the immutable source copy.",
        "source_archive": str(archive_path),
        "source_archive_bytes": archive_bytes,
        "source_archive_sha256": archive_sha,
        "uncompressed_bytes": uncompressed_bytes,
        "file_count": len([path for path in package_root.rglob("*") if path.is_file()]),
        "archive_member_count": len(listed),
        "oci_manifest_object": FINAL_MANIFEST_OBJECT,
        "oci_manifest_sha256": manifest_sha,
        "status": "PENDING_MANUAL_UPLOAD",
        "generated_utc": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
    }
    write_json(ROOT / "data/manifests/fills/data_004_kaggle_run.json", kaggle_record)
    print(
        json.dumps(
            {
                "package_root": str(package_root),
                "archive": str(archive_path),
                "archive_bytes": archive_bytes,
                "archive_sha256": archive_sha,
                "uncompressed_bytes": uncompressed_bytes,
                "file_count": kaggle_record["file_count"],
                "fill_file_count": len(fill_files),
                "fill_rows": counts["deduped_fill_rows"],
                "oci_manifest_sha256": manifest_sha,
                "status": kaggle_record["status"],
            },
            sort_keys=True,
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
