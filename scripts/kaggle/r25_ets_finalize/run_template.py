"""Combine frozen ETS order-book shards into one immutable Kaggle kernel output."""

from __future__ import annotations

import base64
import csv
import gzip
import hashlib
import json
import shutil
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


INPUT = Path("/kaggle/input")
WORK = Path("/kaggle/working/r25_ets_finalize")
EMBEDDED_INPUTS_B64: dict[str, str] = {}
EMBEDDED_KERNEL_ID = ""
EMBEDDED_REPOSITORY_COMMIT = ""


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(8 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def find_one_under(root: Path, name: str) -> Path:
    matches = [path for path in root.rglob(name) if path.is_file()]
    if len(matches) != 1:
        if not matches and name in EMBEDDED_INPUTS_B64:
            embedded = WORK / "embedded_inputs" / name
            embedded.parent.mkdir(parents=True, exist_ok=True)
            embedded.write_bytes(gzip.decompress(base64.b64decode(EMBEDDED_INPUTS_B64[name])))
            return embedded
        raise RuntimeError(f"expected one {name} under {root}, found {len(matches)}")
    return matches[0]


def kernel_sources() -> list[str]:
    return [
        "polyleviathan/r2-5-ets-universe-discovery",
        *[f"polyleviathan/r25-ets-orderbooks-{i:02d}" for i in range(1, 6)],
    ]


def locate_orderbook_source(kernel_id: str) -> tuple[Path, dict[str, Any]]:
    matches = []
    for path in INPUT.rglob("ETS_ORDERBOOK_MANIFEST.json"):
        manifest = read_json(path)
        if manifest.get("kernel_id") == kernel_id:
            matches.append((path, manifest))
    if len(matches) != 1:
        raise RuntimeError(f"expected one order-book manifest for {kernel_id}, found {len(matches)}")
    manifest_path, manifest = matches[0]
    return manifest_path.parent, manifest


def parse_time(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        value = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def write_csv(path: Path, rows: list[dict[str, Any]], columns: list[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore", lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    WORK.mkdir(parents=True, exist_ok=True)
    freeze_path = find_one_under(Path(__file__).resolve().parent, "ETS_UNIVERSE_FREEZE.json")
    inventory_path = find_one_under(Path(__file__).resolve().parent, "ETS_TOKEN_INVENTORY.csv")
    freeze = read_json(freeze_path)
    if sha256(inventory_path) != freeze.get("sha256", {}).get("cid_token_inventory"):
        raise RuntimeError("token inventory does not match the frozen universe checksum")

    source_ids = kernel_sources()
    orderbook_ids = sorted(source for source in source_ids if "/r25-ets-orderbooks-" in source)
    discovery_ids = [source for source in source_ids if source == "polyleviathan/r2-5-ets-universe-discovery"]
    if len(orderbook_ids) != 5 or len(set(orderbook_ids)) != 5 or len(discovery_ids) != 1:
        raise RuntimeError(f"finalizer requires five order-book shards and one discovery source: {source_ids}")

    shards = []
    shard_roots: dict[str, Path] = {}
    for kernel_id in orderbook_ids:
        root, manifest = locate_orderbook_source(kernel_id)
        if manifest.get("universe_freeze_sha256") != sha256(freeze_path):
            raise RuntimeError(f"order-book source {kernel_id} uses a different universe freeze")
        if manifest.get("canonical_mapping_sha256") != freeze.get("canonical_sig_mapping_sha256"):
            raise RuntimeError(f"order-book source {kernel_id} uses a different SIG mapping")
        shards.append(manifest)
        shard_roots[kernel_id] = root
    shards.sort(key=lambda item: item["shard"]["start_inclusive"])

    for previous, current in zip(shards, shards[1:]):
        if previous["shard"]["end_exclusive"] != current["shard"]["start_inclusive"]:
            raise RuntimeError("order-book time shards have a gap or overlap")
    if len({item["shard"]["shard_id"] for item in shards}) != 5:
        raise RuntimeError("order-book shards do not have five unique shard IDs")

    discovery_events = find_one_under(INPUT, "ETS_DISCOVERY_EVENTS.json")
    discovery_audit = find_one_under(INPUT, "ETS_DISCOVERY_AUDIT.json")
    discovery_candidates = find_one_under(INPUT, "ETS_CANDIDATES.csv")
    audit = read_json(discovery_audit)
    if audit.get("canonical_mapping_sha256") != freeze.get("canonical_sig_mapping_sha256"):
        raise RuntimeError("discovery metadata and frozen universe use different canonical mappings")
    if sha256(discovery_events) != audit.get("event_snapshot_sha256"):
        raise RuntimeError("Gamma event metadata checksum does not match discovery audit")
    if sha256(discovery_candidates) != freeze.get("gamma_candidate_snapshot_sha256"):
        raise RuntimeError("Gamma candidate metadata checksum does not match the universe freeze")
    if sha256(discovery_candidates) != audit.get("candidates_sha256"):
        raise RuntimeError("Gamma candidate metadata checksum does not match discovery audit")
    if sha256(discovery_audit) != freeze.get("discovery_audit_sha256"):
        raise RuntimeError("discovery audit checksum does not match the universe freeze")

    inventory_rows = list(csv.DictReader(inventory_path.open(encoding="utf-8-sig", newline="")))
    expected_by_cid: dict[str, dict[str, Any]] = {}
    token_ids: set[str] = set()
    for row in inventory_rows:
        if row["token_id"] in token_ids:
            raise RuntimeError(f"duplicate frozen token ID: {row['token_id']}")
        token_ids.add(row["token_id"])
        expected_by_cid.setdefault(row["condition_id"], row)
    if len(expected_by_cid) != freeze.get("unique_cid_count") or len(token_ids) != freeze.get("unique_token_count"):
        raise RuntimeError("frozen CID/token counts do not match token inventory")

    raw_root = WORK / "ets_raw" / "orderbooks"
    raw_root.mkdir(parents=True, exist_ok=True)
    expected_bytes = sum(int(file_row["bytes"]) for manifest in shards for file_row in manifest.get("files", []))
    available_bytes = shutil.disk_usage(WORK).free
    if available_bytes < expected_bytes + (1 << 30):
        raise RuntimeError(f"insufficient Kaggle working disk for immutable output: need {expected_bytes} bytes plus 1 GiB, have {available_bytes}")

    output_files: list[dict[str, Any]] = []
    seen_paths: set[str] = set()
    for manifest in shards:
        root = shard_roots[manifest["kernel_id"]]
        source_raw_root = root / "ets_raw" / "orderbooks"
        if not source_raw_root.is_dir():
            raise RuntimeError(f"missing raw order-book directory for {manifest['kernel_id']}")
        for record in manifest.get("files", []):
            relative = Path(record["path"]).relative_to("ets_raw/orderbooks")
            if str(relative) in seen_paths:
                raise RuntimeError(f"duplicate order-book output path across time shards: {relative}")
            seen_paths.add(str(relative))
            source = source_raw_root / relative
            destination = raw_root / relative
            if not source.is_file():
                raise RuntimeError(f"manifested order-book file is absent: {source}")
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
            actual_hash = sha256(destination)
            if actual_hash != record["sha256"] or destination.stat().st_size != int(record["bytes"]):
                raise RuntimeError(f"copied order-book file failed hash/size verification: {relative}")
            output_files.append({**record, "path": (Path("ets_raw/orderbooks") / relative).as_posix()})

    # Retain the actual Gamma payload with the immutable output, not only its future API URL.
    gamma_target = WORK / "GAMMA_DISCOVERY_METADATA.json"
    shutil.copyfile(discovery_events, gamma_target)
    candidates_target = WORK / "GAMMA_DISCOVERY_CANDIDATES.csv"
    shutil.copyfile(discovery_candidates, candidates_target)
    audit_target = WORK / "ETS_DISCOVERY_AUDIT.json"
    shutil.copyfile(discovery_audit, audit_target)
    freeze_target = WORK / "ETS_UNIVERSE_FREEZE.json"
    inventory_target = WORK / "ETS_TOKEN_INVENTORY.csv"
    shutil.copyfile(freeze_path, freeze_target)
    shutil.copyfile(inventory_path, inventory_target)

    source_evidence = [row for manifest in shards for row in manifest.get("source_objects", [])]
    source_hours = sorted({row["hour"] for row in source_evidence})
    scanned_hour_total = sum(
        int((parse_time(item["source_scan_bounds"]["shard_end_exclusive"]) -
             parse_time(item["source_scan_bounds"]["shard_start_inclusive"])).total_seconds() // 3600)
        for item in shards
    )
    if len(source_hours) != scanned_hour_total:
        raise RuntimeError(f"source-hour evidence is incomplete or duplicated: {len(source_hours)} unique vs {scanned_hour_total} expected")
    available_hours = sorted({
        hour for manifest in shards for hour in manifest.get("archive_source_bounds", {}).values() if hour
    })
    archive_first = min(available_hours) if available_hours else None
    archive_last = max(available_hours) if available_hours else None
    requested_first = min(item["source_scan_bounds"]["shard_start_inclusive"] for item in shards)
    missing_hours = sorted({hour for manifest in shards for hour in manifest.get("missing_source_hours", [])})

    coverage_inputs = []
    for manifest in shards:
        root = shard_roots[manifest["kernel_id"]]
        coverage_path = root / "ETS_ORDERBOOK_COVERAGE.csv"
        if not coverage_path.is_file():
            raise RuntimeError(f"missing shard coverage file: {coverage_path}")
        with coverage_path.open(encoding="utf-8", newline="") as handle:
            coverage_inputs.extend(csv.DictReader(handle))
    grouped: dict[tuple[str, str, str, str], list[dict[str, str]]] = defaultdict(list)
    for row in coverage_inputs:
        grouped[(row["market_id"], row["condition_id"], row["source"], row["data_type"])].append(row)
    coverage_rows = []
    for key, rows in sorted(grouped.items()):
        market_id, cid, source, data_type = key
        expected_start = min((r["expected_start"] for r in rows if r.get("expected_start")), default="")
        expected_dt = parse_time(expected_start)
        expected_hour = expected_dt.strftime("%Y-%m-%dT%H") if expected_dt else requested_first[:13]
        expected_hour = max(expected_hour, requested_first[:13])
        relevant_missing = [hour for hour in missing_hours if hour >= expected_hour and (not archive_last or hour <= archive_last)]
        row_count = sum(int(r.get("row_count") or 0) for r in rows)
        file_count = sum(int(r.get("file_count") or 0) for r in rows)
        available_starts = [r["available_start"] for r in rows if r.get("available_start")]
        available_ends = [r["available_end"] for r in rows if r.get("available_end")]
        resolved = next((r["resolved_at"] for r in rows if r.get("resolved_at")), "")
        gaps = [{"hour": hour, "reason": "required PMXT source archive missing"} for hour in relevant_missing]
        if not row_count:
            gap_reason = "no matching PMXT rows"
        elif relevant_missing:
            gap_reason = "missing required archive hour(s)"
        else:
            gap_reason = ""
        coverage_rows.append({
            "market_id": market_id, "condition_id": cid, "source": source, "data_type": data_type,
            "expected_start": expected_start, "available_start": min(available_starts, default=""),
            "available_end": max(available_ends, default=""), "resolved_at": resolved,
            "row_count": row_count, "file_count": file_count,
            "complete_to_source_bounds": bool(row_count and not relevant_missing),
            "known_gaps": json.dumps(gaps, separators=(",", ":")) if gaps else "",
            "gap_reason": gap_reason,
            "source_version": "PMXT_V1/V2 routed and spliced per DATA-001",
        })

    coverage_target = WORK / "ETS_ORDERBOOK_COVERAGE.csv"
    coverage_columns = [
        "market_id", "condition_id", "source", "data_type", "expected_start", "available_start",
        "available_end", "resolved_at", "row_count", "file_count", "complete_to_source_bounds",
        "known_gaps", "gap_reason", "source_version",
    ]
    write_csv(coverage_target, coverage_rows, coverage_columns)

    source_evidence_path = WORK / "ETS_ORDERBOOK_SOURCE_EVIDENCE.json.gz"
    with source_evidence_path.open("wb") as raw_handle:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw_handle, mtime=0) as compressed:
            compressed.write((json.dumps(source_evidence, separators=(",", ":"), sort_keys=True) + "\n").encode())
    files = sorted(output_files, key=lambda row: row["path"])
    supporting_files = [gamma_target, candidates_target, audit_target, freeze_target, inventory_target, coverage_target, source_evidence_path]
    supporting_file_records = [
        {"path": path.relative_to(WORK).as_posix(), "bytes": path.stat().st_size, "sha256": sha256(path)}
        for path in supporting_files
    ]
    row_counts: dict[str, int] = defaultdict(int)
    duplicate_counts: dict[str, int] = defaultdict(int)
    for manifest in shards:
        for stream, count in manifest.get("row_counts_by_type", {}).items():
            row_counts[stream] += int(count)
        for stream, count in manifest.get("exact_duplicate_rows_removed_by_type", {}).items():
            duplicate_counts[stream] += int(count)
    markets_with_rows = {
        row["condition_id"] for row in coverage_rows if int(row["row_count"]) > 0
    }
    markets_with_book_rows = {
        row["condition_id"] for row in coverage_rows
        if row["data_type"] in {"depth_snapshots", "book_changes"} and int(row["row_count"]) > 0
    }
    manifest = {
        "schema_version": 1,
        "dataset_id": "polyleviathan/sig-cup-r25-ets-historical-data",
        "dataset_type": "private_kaggle_kernel_output",
        "kernel_id": EMBEDDED_KERNEL_ID,
        "repository_commit": EMBEDDED_REPOSITORY_COMMIT,
        "finalizer_script_sha256": sha256(Path(__file__).resolve()),
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "canonical_sig_mapping_sha256": freeze["canonical_sig_mapping_sha256"],
        "ets_universe_freeze_sha256": sha256(freeze_path),
        "gamma_metadata_sha256": sha256(gamma_target),
        "gamma_candidate_snapshot_sha256": sha256(candidates_target),
        "discovery_audit_sha256": sha256(audit_target),
        "accepted_ets_market_count": freeze["accepted_ets_market_count"],
        "cid_count": freeze["unique_cid_count"], "token_count": freeze["unique_token_count"],
        "orderbook_market_count_with_any_rows": len(markets_with_rows),
        "orderbook_market_count_with_book_rows": len(markets_with_book_rows),
        "source": "PendulumFlow PMXT archives through predictions_cup.historical DATA-001 pipeline",
        "source_versions": ["PMXT_V1", "PMXT_V2"],
        "source_archive_bounds": {"first_available_hour": archive_first, "last_available_hour": archive_last},
        "source_hours_scanned": len(source_hours), "missing_required_archive_hours": len(missing_hours),
        "row_counts_by_type": dict(sorted(row_counts.items())),
        "duplicate_rows_removed_by_type": dict(sorted(duplicate_counts.items())),
        "file_count": len(files), "bytes": sum(int(row["bytes"]) for row in files),
        "supporting_files": supporting_file_records,
        "total_output_bytes_before_manifest_and_quality": sum(int(row["bytes"]) for row in files) + sum(row["bytes"] for row in supporting_file_records),
        "files": files,
        "coverage_path": "ETS_ORDERBOOK_COVERAGE.csv",
        "coverage_sha256": sha256(coverage_target),
        "source_evidence_path": "ETS_ORDERBOOK_SOURCE_EVIDENCE.json.gz",
        "source_evidence_sha256": sha256(source_evidence_path),
        "source_evidence_bytes": source_evidence_path.stat().st_size,
        "gamma_metadata_path": "GAMMA_DISCOVERY_METADATA.json",
        "gamma_metadata_bytes": gamma_target.stat().st_size,
        "gamma_candidate_snapshot_path": "GAMMA_DISCOVERY_CANDIDATES.csv",
        "gamma_candidate_snapshot_bytes": candidates_target.stat().st_size,
        "ordering_semantics": "PMXT observable receive-time normalization; no claim of FULL_EVENT_REPLAY; same-millisecond order is not recoverable",
        "fill_ordering_semantics": "fills are a separate OCI handoff ordered by block_number, log_index; no tx_hash ordering",
        "known_gaps": [
            {"type": "missing_required_pmxt_archive_hours", "hours": missing_hours},
            {"type": "source_coverage", "note": "PMXT is snapshot-grade and does not provide exact queue/cancel sequencing or live receive timestamps for V1"},
        ],
        "shards": [
            {"kernel_id": row["kernel_id"], "created_at": row.get("created_at"), "shard": row["shard"], "archive_source_bounds": row.get("archive_source_bounds"),
             "repository_commit": row.get("repository_commit"), "acquisition_script_sha256": row.get("acquisition_script_sha256"),
             "pipeline_wheel_commit": row.get("pipeline_wheel_commit"), "file_count": row.get("file_count"), "bytes": row.get("bytes")}
            for row in shards
        ],
        "schemas": {
            "raw_orderbooks": "predictions_cup.historical.pmxt.SCHEMAS; daily ZSTD Parquet by stream/date, token_id and condition_id retained",
        },
    }
    manifest_target = WORK / "ETS_ORDERBOOK_MANIFEST.json"
    manifest_target.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    quality = {
        "identity": {
            "frozen_inventory_sha_matches": True,
            "freeze_sha256": sha256(freeze_path),
            "cid_count": len(expected_by_cid),
            "token_count": len(inventory_rows),
            "all_output_file_hashes_verified": True,
        },
        "acquisition": {
            "shards_expected": 5,
            "shards_received": len(shards),
            "shard_time_ranges_contiguous": True,
            "all_source_hours_recorded": len(source_hours) == scanned_hour_total,
            "missing_required_archive_hours": len(missing_hours),
            "accepted_markets_with_any_history": len(markets_with_rows),
            "accepted_markets_with_book_history": len(markets_with_book_rows),
            "accepted_market_count": len(expected_by_cid),
            "no_duplicate_output_paths": len(files) == len(seen_paths),
        },
    }
    quality_target = WORK / "ETS_ORDERBOOK_QUALITY.json"
    quality_target.write_text(json.dumps(quality, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    summary = {
        "status": "COMPLETE" if not missing_hours else "COMPLETE_WITH_SOURCE_GAPS",
        "kernel_id": EMBEDDED_KERNEL_ID, "manifest_path": manifest_target.name,
        "manifest_sha256": sha256(manifest_target), "quality_path": quality_target.name,
        "coverage_path": coverage_target.name, "accepted_ets_markets": freeze["accepted_ets_market_count"],
        "cid_count": len(expected_by_cid), "token_count": len(inventory_rows),
        "orderbook_files": len(files), "orderbook_bytes": sum(int(row["bytes"]) for row in files),
        "row_counts_by_type": dict(sorted(row_counts.items())),
        "markets_with_orderbook_history": len(markets_with_book_rows),
        "source_archive_bounds": {"first": archive_first, "last": archive_last},
        "missing_required_archive_hours": len(missing_hours),
    }
    (WORK / "ETS_ORDERBOOK_FINALIZE_SUMMARY.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(summary, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
