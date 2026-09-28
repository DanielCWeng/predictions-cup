"""Full-range PMXT acquisition for one deterministic time shard of frozen ETS tokens.

Runs on Kaggle. Normalization, source routing, splice semantics, output schemas, and time
ordering are imported from the accepted DATA-001 predictions_cup wheel.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


INPUT = Path("/kaggle/input")
WORK = Path("/kaggle/working/r25_ets_orderbooks")
TMP = Path("/kaggle/tmp/r25_ets_orderbooks")
USER_AGENT = "predictions-cup-r25-ets-orderbooks/1.0"


def find_one(name: str) -> Path:
    matches = [p for p in INPUT.rglob(name) if p.is_file()]
    if len(matches) == 1:
        return matches[0]
    local = Path(__file__).resolve().parent / name
    if local.is_file() and not matches:
        return local
    raise RuntimeError(f"expected one {name} under Kaggle inputs or kernel files, found {len(matches)}")


def install_pipeline() -> str:
    wheels = list(INPUT.rglob("predictions_cup-*.whl"))
    if len(wheels) != 1:
        raise RuntimeError(f"expected the accepted DATA-001 code wheel, found {len(wheels)}")
    wheel = wheels[0]
    code_commit = (wheel.parent / "COMMIT.txt").read_text(encoding="utf-8").strip()
    subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", str(wheel)])
    return code_commit


def parse_time(raw: str | None) -> datetime | None:
    if not raw:
        return None
    value = raw.strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(8 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def request_head(url: str) -> dict[str, Any] | None:
    request = urllib.request.Request(url, method="HEAD", headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            return {
                "url": url,
                "bytes": int(response.headers.get("Content-Length", "0")),
                "etag": response.headers.get("ETag"),
                "last_modified": response.headers.get("Last-Modified"),
            }
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise


def load_inventory(path: Path) -> tuple[list[dict[str, str]], dict[str, str], dict[str, str]]:
    rows = list(csv.DictReader(path.open(encoding="utf-8-sig", newline="")))
    token_condition: dict[str, str] = {}
    token_outcome: dict[str, str] = {}
    for row in rows:
        token, condition = row.get("token_id", "").strip(), row.get("condition_id", "").strip()
        if not token or not condition:
            raise RuntimeError("frozen token inventory contains a blank token/condition ID")
        if token in token_condition and token_condition[token] != condition:
            raise RuntimeError(f"frozen token maps to multiple CIDs: {token}")
        token_condition[token] = condition
        token_outcome[token] = row.get("outcome", "")
    if not token_condition:
        raise RuntimeError("frozen ETS token inventory is empty")
    return rows, token_condition, token_outcome


def load_shard(path: Path) -> dict[str, str]:
    shard = json.loads(path.read_text(encoding="utf-8"))
    for key in ("shard_id", "start_inclusive", "end_exclusive"):
        if not shard.get(key):
            raise ValueError(f"shard configuration lacks {key}")
    return shard


def _all_empty(pmxt: Any) -> dict[str, Any]:
    return {stream: pmxt.empty_table(stream) for stream in ("depth_snapshots", "book_changes", "trades", "tick_size_changes", "rejects")}


def normalize_extract(path: Path | None, version: str, hour: datetime, tokens: frozenset[str], pmxt: Any, corpus: Any) -> dict[str, Any]:
    if path is None or not path.exists():
        return _all_empty(pmxt)
    import pyarrow.parquet as pq
    columns = corpus._V2_COLUMNS if version == "PMXT_V2" else corpus._V1_COLUMNS
    table = pq.ParquetFile(path).read(columns=columns)
    return pmxt.normalize_extract(
        table,
        source_version=version,
        token_ids=tokens,
        window_start=hour,
        window_end=hour + timedelta(hours=1),
    ).tables


def scan_token_stats(table: Any, time_field: str) -> list[dict[str, Any]]:
    if not table.num_rows:
        return []
    grouped = table.group_by("token_id").aggregate([
        (time_field, "min"), (time_field, "max"), (time_field, "count"),
    ])
    return grouped.to_pylist()


def add_token_stats(coverage: dict[str, dict[str, Any]], stream: str, table: Any, time_field: str, day: str) -> None:
    for row in scan_token_stats(table, time_field):
        token = str(row["token_id"])
        item = coverage.setdefault(token, {}).setdefault(stream, {"row_count": 0, "available_start": None, "available_end": None, "output_days": set()})
        item["row_count"] += int(row[f"{time_field}_count"] or 0)
        item["output_days"].add(day)
        start = row[f"{time_field}_min"]
        end = row[f"{time_field}_max"]
        if start and (item["available_start"] is None or start < item["available_start"]):
            item["available_start"] = start
        if end and (item["available_end"] is None or end > item["available_end"]):
            item["available_end"] = end


def download_and_extract(
    *, hour: datetime, version: str, extract_name: str, family_root: Path,
    tokens: frozenset[str], conditions: frozenset[str], acquire: Any, pa: Any, pq: Any,
) -> tuple[dict[str, Any], Path | None]:
    from predictions_cup.historical.sources import pmxt_archive_url
    key = f"{hour:%Y-%m-%dT%H}"
    url = pmxt_archive_url(key, version)
    head = request_head(url)
    if head is None:
        return {"hour": key, "version": version, "status": "SOURCE_MISSING", "url": url}, None
    scratch = TMP / "archives"
    scratch.mkdir(parents=True, exist_ok=True)
    local, downloaded_bytes = acquire._download(url, scratch)
    try:
        digest = sha256(local)
        metadata = pq.read_metadata(local)
        item = {"hour": key, "source_version": version, "extract": extract_name, "families": ["ETS"]}
        stats = acquire._filter_hour(
            local,
            item,
            {"ETS": {"tokens": pa.array(sorted(tokens)), "conditions": pa.array(sorted(conditions))}},
            family_root,
        )
        output = family_root / "ETS" / f"date={hour:%Y-%m-%d}" / f"hour={hour:%H}" / extract_name
        evidence = {
            "hour": key, "version": version, "status": "SOURCE_AVAILABLE",
            "url": url, "bytes": downloaded_bytes, "etag": head.get("etag"),
            "last_modified": head.get("last_modified"), "sha256": digest,
            "rows": metadata.num_rows, "row_groups": metadata.num_row_groups,
            "matched_rows": stats.get("ETS", {}).get("rows", 0),
            "extract_sha256": stats.get("ETS", {}).get("sha256"),
        }
        return evidence, output if output.exists() else None
    finally:
        local.unlink(missing_ok=True)


def iso(value: Any) -> str | None:
    return value.isoformat().replace("+00:00", "Z") if value is not None else None


def main() -> None:
    os.environ.setdefault("POLARS_MAX_THREADS", "2")
    WORK.mkdir(parents=True, exist_ok=True)
    TMP.mkdir(parents=True, exist_ok=True)
    code_commit = install_pipeline()
    import pyarrow as pa
    import pyarrow.parquet as pq
    from predictions_cup.historical import acquire, corpus, pmxt
    from predictions_cup.historical.regimes import (
        PMXT_V1_FIRST_HOUR,
        pmxt_supplement_for_hour,
        pmxt_version_for_hour,
    )

    inventory_path = find_one("ETS_TOKEN_INVENTORY.csv")
    freeze_path = find_one("ETS_UNIVERSE_FREEZE.json")
    shard_path = find_one("shard.json")
    freeze = json.loads(freeze_path.read_text(encoding="utf-8"))
    if sha256(inventory_path) != freeze.get("sha256", {}).get("cid_token_inventory"):
        raise RuntimeError("frozen token inventory checksum does not match ETS_UNIVERSE_FREEZE.json")
    rows, token_condition, _ = load_inventory(inventory_path)
    shard = load_shard(shard_path)
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    start = datetime.fromisoformat(shard["start_inclusive"].replace("Z", "+00:00")).astimezone(timezone.utc)
    end = datetime.fromisoformat(shard["end_exclusive"].replace("Z", "+00:00")).astimezone(timezone.utc)
    end = min(end, now + timedelta(hours=1))
    # DATA-001 records this as the first verified PMXT V1 archive hour. Avoid walking
    # older empty dates that cannot contribute source history to any frozen token.
    source_first_hour = PMXT_V1_FIRST_HOUR
    start = max(start, source_first_hour)
    creation = [parse_time(row.get("created_at")) for row in rows]
    if creation and all(value is not None for value in creation):
        start = max(start, min(value for value in creation if value).replace(minute=0, second=0, microsecond=0))
    if end <= start:
        raise RuntimeError(f"empty shard after intersecting expected creation and source horizon: {shard}")

    start = start.replace(minute=0, second=0, microsecond=0)
    family_root = TMP / "raw_extracts"
    out_root = WORK / "ets_raw" / "orderbooks"
    out_root.mkdir(parents=True, exist_ok=True)
    source_evidence: list[dict[str, Any]] = []
    missing_source_hours: list[str] = []
    observed_coverage: dict[str, dict[str, Any]] = {}
    row_counts: dict[str, int] = defaultdict(int)
    duplicate_rows: dict[str, int] = defaultdict(int)
    splice_reports: list[dict[str, Any]] = []
    archive_available_hours: list[str] = []
    tokens = frozenset(token_condition)
    conditions = frozenset(token_condition.values())

    with corpus._DailyWriters(out_root, pmxt.SCHEMAS) as writers:
        hour = start
        while hour < end:
            key = f"{hour:%Y-%m-%dT%H}"
            version = pmxt_version_for_hour(hour)
            primary_name = "events.parquet"
            primary_evidence, primary_path = download_and_extract(
                hour=hour, version=version, extract_name=primary_name, family_root=family_root,
                tokens=tokens, conditions=conditions, acquire=acquire, pa=pa, pq=pq,
            )
            source_evidence.append(primary_evidence)
            primary_tables = normalize_extract(primary_path, version, hour, tokens, pmxt, corpus)
            normalized = primary_tables
            supplement_version = pmxt_supplement_for_hour(hour)
            supplement_evidence = None
            if supplement_version:
                supplement_evidence, supplement_path = download_and_extract(
                    hour=hour, version=supplement_version, extract_name="events_v2.parquet",
                    family_root=family_root, tokens=tokens, conditions=conditions,
                    acquire=acquire, pa=pa, pq=pq,
                )
                source_evidence.append(supplement_evidence)
                supplement_tables = normalize_extract(supplement_path, supplement_version, hour, tokens, pmxt, corpus)
                normalized, splice = corpus.splice_primary_with_supplement(primary_tables, supplement_tables)
                splice_reports.append({"hour": key, **splice})
                if supplement_path:
                    supplement_path.unlink(missing_ok=True)
            if primary_evidence["status"] == "SOURCE_AVAILABLE" or (
                supplement_evidence and supplement_evidence["status"] == "SOURCE_AVAILABLE"
            ):
                archive_available_hours.append(key)
            required_sources_available = primary_evidence["status"] == "SOURCE_AVAILABLE" and (
                not supplement_version or supplement_evidence["status"] == "SOURCE_AVAILABLE"
            )
            if not required_sources_available:
                missing_source_hours.append(key)

            for stream, table in normalized.items():
                table, dropped = pmxt.sort_and_dedupe(stream, table)
                duplicate_rows[stream] += dropped
                row_counts[stream] += table.num_rows
                time_field = pmxt.STREAM_TIME_FIELD[stream]
                add_token_stats(observed_coverage, stream, table, time_field, f"{hour:%Y-%m-%d}")
                if table.num_rows:
                    writers.write(stream, hour, table)
            if primary_path:
                primary_path.unlink(missing_ok=True)
            # The accepted acquire helper leaves only exact-identity raw extracts. Keep none
            # after normalized, provenance-graded rows have been written.
            extract_dir = family_root / "ETS" / f"date={hour:%Y-%m-%d}" / f"hour={hour:%H}"
            if extract_dir.exists():
                shutil.rmtree(extract_dir)
            if hour.hour % 6 == 0:
                print(f"shard={shard['shard_id']} hour={key} normalized_rows={sum(row_counts.values())}", flush=True)
            hour += timedelta(hours=1)

    archive_first = min(archive_available_hours) if archive_available_hours else None
    archive_last = max(archive_available_hours) if archive_available_hours else None
    market_by_condition: dict[str, dict[str, Any]] = {}
    for row in rows:
        cid = row["condition_id"]
        market_by_condition.setdefault(cid, row)
    coverage_rows = []
    for cid, market in sorted(market_by_condition.items()):
        expected_times = [parse_time(market.get("created_at")), parse_time(market.get("start_date"))]
        expected_times = [value for value in expected_times if value]
        expected_time = min(expected_times) if expected_times else None
        expected_hour = f"{expected_time:%Y-%m-%dT%H}" if expected_time else (archive_first or "")
        market_gaps = [
            key for key in missing_source_hours
            if archive_first and archive_last and archive_first <= key <= archive_last and key >= expected_hour
        ]
        market_tokens = sorted(token for token, condition in token_condition.items() if condition == cid)
        for stream in ("depth_snapshots", "book_changes", "trades", "tick_size_changes", "rejects"):
            starts, ends = [], []
            count = 0
            for token in market_tokens:
                stats = observed_coverage.get(token, {}).get(stream, {})
                count += int(stats.get("row_count", 0))
                if stats.get("available_start"):
                    starts.append(stats["available_start"])
                if stats.get("available_end"):
                    ends.append(stats["available_end"])
            coverage_rows.append({
                "market_id": market.get("market_id", ""), "condition_id": cid,
                "source": "PENDULUMFLOW_PMXT_V1_V2", "data_type": stream,
                "expected_start": iso(expected_time) if expected_time else "",
                "available_start": iso(min(starts)) if starts else "",
                "available_end": iso(max(ends)) if ends else "",
                "resolved_at": market.get("resolution_time", ""),
                "row_count": count,
                "file_count": len({day for token in market_tokens for day in observed_coverage.get(token, {}).get(stream, {}).get("output_days", set())}),
                "complete_to_source_bounds": bool(count and not market_gaps),
                "known_gaps": json.dumps(market_gaps, separators=(",", ":")) if market_gaps else "",
                "gap_reason": "no matching PMXT rows" if not count else ("missing archive hour(s)" if market_gaps else ""),
                "source_version": "PMXT_V1,PMXT_V2 with DATA-001 2026-04-13T19 splice",
            })

    coverage_path = WORK / "ETS_ORDERBOOK_COVERAGE.csv"
    with coverage_path.open("w", encoding="utf-8", newline="") as f:
        cols = [
            "market_id", "condition_id", "source", "data_type", "expected_start", "available_start",
            "available_end", "resolved_at", "row_count", "file_count", "complete_to_source_bounds",
            "known_gaps", "gap_reason", "source_version",
        ]
        writer = csv.DictWriter(f, fieldnames=cols, lineterminator="\n")
        writer.writeheader()
        writer.writerows(coverage_rows)

    def file_records(root: Path) -> list[dict[str, Any]]:
        out = []
        for path in sorted(root.rglob("*.parquet")):
            rel = path.relative_to(WORK).as_posix()
            out.append({"path": rel, "bytes": path.stat().st_size, "rows": pq.ParquetFile(path).metadata.num_rows, "sha256": sha256(path)})
        return out

    files = file_records(out_root)
    source_bounds = {"first_available_hour": archive_first, "last_available_hour": archive_last}
    manifest = {
        "schema_version": 1,
        "dataset_id": "POLYLEVIATHAN_R25_ETS_ORDERBOOKS",
        "kernel_id": json.loads((Path(__file__).resolve().parent / "kernel-metadata.json").read_text())["id"],
        "repository_commit": (Path(__file__).resolve().parent / "REPOSITORY_COMMIT.txt").read_text(encoding="utf-8").strip(),
        "acquisition_script_sha256": sha256(Path(__file__).resolve()),
        "shard": shard,
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "universe_freeze_sha256": sha256(freeze_path),
        "canonical_mapping_sha256": freeze.get("canonical_sig_mapping_sha256"),
        "discovery_freeze_timestamp": freeze.get("freeze_timestamp"),
        "pipeline_wheel_commit": code_commit,
        "canonical_pipeline": "predictions_cup.historical.pmxt, acquire._filter_hour, corpus._DailyWriters, corpus.splice_primary_with_supplement",
        "source_scan_bounds": {
            "first_verified_pmxt_archive_hour": source_first_hour.isoformat().replace("+00:00", "Z"),
            "shard_start_inclusive": start.isoformat().replace("+00:00", "Z"),
            "shard_end_exclusive": end.isoformat().replace("+00:00", "Z"),
            "latest_scan_hour_capped_at": now.isoformat().replace("+00:00", "Z"),
        },
        "frozen_condition_count": len(set(token_condition.values())),
        "frozen_token_count": len(token_condition),
        "requested_start": iso(start), "requested_end_exclusive": iso(end),
        "archive_source_bounds": source_bounds,
        "archive_hours_available": len(archive_available_hours),
        "archive_hours_missing": len(missing_source_hours),
        "missing_source_hours": missing_source_hours,
        "source_objects": source_evidence,
        "source_splices": splice_reports,
        "row_counts_by_type": dict(sorted(row_counts.items())),
        "exact_duplicate_rows_removed_by_type": dict(sorted(duplicate_rows.items())),
        "file_count": len(files), "bytes": sum(x["bytes"] for x in files),
        "min_observed_at": iso(min((s.get("available_start") for rows_by_stream in observed_coverage.values() for s in rows_by_stream.values() if s.get("available_start")), default=None)),
        "max_observed_at": iso(max((s.get("available_end") for rows_by_stream in observed_coverage.values() for s in rows_by_stream.values() if s.get("available_end")), default=None)),
        "files": files,
        "token_coverage": {
            token: {
                stream: {**stats, "available_start": iso(stats.get("available_start")), "available_end": iso(stats.get("available_end")), "output_days": sorted(stats.get("output_days", set()))}
                for stream, stats in streams.items()
            }
            for token, streams in observed_coverage.items()
        },
        "coverage_csv_sha256": sha256(coverage_path),
        "ordering_semantics": {
            "observable_time": "PMXT timestamp_received archive-receive proxy; not live possession time",
            "venue_time": "PMXT V2 timestamp preserved as source_timestamp; PMXT V1 source_timestamp null",
            "canonical_sort": "accepted DATA-001 PMXT sort_and_dedupe rules: observable time then token/content; archive export order is not used",
            "evidence_grades": {"depth_snapshots": "BOOK_SNAPSHOT", "book_changes": "PRICE_ONLY", "trades": "TRADE_FILL"},
        },
        "known_gaps": ([{"type": "missing_required_pmxt_archive_hours", "hours": missing_source_hours}] if missing_source_hours else []),
    }
    manifest_path = WORK / "ETS_ORDERBOOK_MANIFEST.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    summary = {
        "status": "COMPLETE_SHARD_WITH_SOURCE_GAPS" if missing_source_hours else "COMPLETE_SHARD",
        "shard": shard["shard_id"], "kernel": manifest["kernel_id"],
        "requested_hours": int((end - start).total_seconds() // 3600),
        "archive_hours_available": len(archive_available_hours), "archive_hours_missing": len(missing_source_hours),
        "condition_count": manifest["frozen_condition_count"], "token_count": manifest["frozen_token_count"],
        "rows": dict(row_counts), "bytes": manifest["bytes"], "files": len(files),
    }
    (WORK / "ETS_ORDERBOOK_SHARD_SUMMARY.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(summary, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
