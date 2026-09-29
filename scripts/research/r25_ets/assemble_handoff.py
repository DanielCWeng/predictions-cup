#!/usr/bin/env python3
"""Assemble compact R2.5 coverage, quality, and R3 handoff records."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import subprocess
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[3]
UNIVERSE = ROOT / "data/research/ets_universe"
REPO = ROOT
COVERAGE_FIELDS = [
    "market_id", "condition_id", "source", "data_type", "expected_start", "available_start",
    "available_end", "resolved_at", "row_count", "file_count", "complete_to_source_bounds",
    "known_gaps", "gap_reason", "source_version",
]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def json_file(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore", lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return result.replace(tzinfo=timezone.utc) if result.tzinfo is None else result.astimezone(timezone.utc)


def iso_epoch(value: int | float | str | None) -> str:
    if value in (None, ""):
        return ""
    return datetime.fromtimestamp(float(value), timezone.utc).isoformat().replace("+00:00", "Z")


def check_hash(path: Path, expected: str, label: str) -> None:
    actual = sha256(path)
    if actual != expected:
        raise ValueError(f"{label} SHA-256 mismatch: expected {expected}, got {actual}")


def gap_items(value: str | None) -> list[dict[str, Any]]:
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return []
    return [item for item in parsed if isinstance(item, dict)] if isinstance(parsed, list) else []


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fill-manifest", type=Path, required=True)
    parser.add_argument("--fill-summary", type=Path, required=True)
    parser.add_argument("--gamma-metadata", type=Path, required=True)
    parser.add_argument("--orderbook-manifest", type=Path, required=True)
    parser.add_argument("--orderbook-summary", type=Path, required=True)
    parser.add_argument("--orderbook-coverage", type=Path, required=True)
    parser.add_argument("--orderbook-quality", type=Path, required=True)
    parser.add_argument("--kernel-version", required=True)
    parser.add_argument("--output-dir", type=Path, default=UNIVERSE)
    args = parser.parse_args()

    freeze_path = args.output_dir / "ETS_UNIVERSE_FREEZE.json"
    mapping_path = ROOT / "data/mappings/sig_polymarket_2026.json"
    acceptance_path = ROOT / "data/mappings/sig_polymarket_2026_acceptance.json"
    freeze = json_file(freeze_path)
    fill = json_file(args.fill_manifest)
    fill_summary = json_file(args.fill_summary)
    orderbooks = json_file(args.orderbook_manifest)
    orderbook_summary = json_file(args.orderbook_summary)
    orderbook_quality = json_file(args.orderbook_quality)
    if fill.get("ets_universe_freeze_sha256") != sha256(freeze_path):
        raise ValueError("fill manifest points to a different frozen universe")
    if orderbooks.get("ets_universe_freeze_sha256") != sha256(freeze_path):
        raise ValueError("order-book manifest points to a different frozen universe")
    if fill.get("canonical_sig_mapping_sha256") != sha256(mapping_path):
        raise ValueError("fill manifest points to a different canonical SIG mapping")
    check_hash(mapping_path, freeze["canonical_sig_mapping_sha256"], "canonical SIG mapping")
    check_hash(acceptance_path, freeze["mapping_acceptance_sha256"], "mapping acceptance evidence")
    check_hash(args.fill_manifest, fill_summary.get("manifest_sha256", ""), "OCI fill manifest")
    check_hash(args.orderbook_manifest, orderbook_summary.get("manifest_sha256", ""), "Kaggle order-book manifest")
    check_hash(args.gamma_metadata, freeze["sha256"]["gamma_metadata"], "Gamma discovery metadata")
    if fill.get("condition_count_attempted") != freeze.get("unique_cid_count"):
        raise ValueError("fill exporter did not attempt every frozen condition ID")
    if fill.get("token_count_attempted") != freeze.get("unique_token_count"):
        raise ValueError("fill exporter did not attempt every frozen token ID")
    if orderbooks.get("accepted_ets_market_count") != freeze.get("accepted_ets_market_count"):
        raise ValueError("order-book manifest market count differs from the freeze")
    if orderbooks.get("cid_count") != freeze.get("unique_cid_count") or orderbooks.get("token_count") != freeze.get("unique_token_count"):
        raise ValueError("order-book manifest CID/token counts differ from the freeze")
    if len(fill.get("files", [])) != fill.get("file_count") or sum(int(row["bytes"]) for row in fill.get("files", [])) != fill.get("bytes"):
        raise ValueError("fill manifest file counts/bytes do not reconcile")
    if len(orderbooks.get("files", [])) != orderbooks.get("file_count") or sum(int(row["bytes"]) for row in orderbooks.get("files", [])) != orderbooks.get("bytes"):
        raise ValueError("order-book manifest file counts/bytes do not reconcile")

    for filename, key in (
        ("ETS_NODES.csv", "nodes"), ("ETS_EDGES.csv", "edges"),
        ("ETS_MARKET_INVENTORY.csv", "market_inventory"), ("ETS_TOKEN_INVENTORY.csv", "cid_token_inventory"),
        ("ETS_CONDITION_IDS.txt", "condition_ids"), ("ETS_TOKEN_IDS.txt", "token_ids"),
        ("ETS_CONDITION_TOKEN_MAP.json", "condition_token_map"),
        ("ETS_RELATIONSHIP_GRAPH.json", "relationship_graph"),
    ):
        check_hash(args.output_dir / filename, freeze["sha256"][key], filename)

    candidates = read_csv(args.output_dir / "ETS_CANDIDATES.csv")
    rejections = read_csv(args.output_dir / "ETS_REJECTIONS.csv")
    markets = read_csv(args.output_dir / "ETS_MARKET_INVENTORY.csv")
    tokens = read_csv(args.output_dir / "ETS_TOKEN_INVENTORY.csv")
    nodes = read_csv(args.output_dir / "ETS_NODES.csv")
    edges = read_csv(args.output_dir / "ETS_EDGES.csv")
    accepted_edges = [edge for edge in edges if edge.get("review_status") == "SEMANTIC_REVIEWED_ACCEPTED"]
    ets_market_ids = {row["market_id"] for row in markets}
    linked_market_ids = {edge["target_id"] for edge in accepted_edges if edge.get("target_type") == "ETS_MARKET"}
    unresolved_candidates = [row for row in candidates if row.get("candidate_status") == "PENDING_REVIEW"]
    missing_rationales = [edge for edge in accepted_edges if not edge.get("economic_rationale", "").strip()]
    missing_review = sorted(ets_market_ids - linked_market_ids)
    duplicate_market_ids = len({row["market_id"] for row in markets}) != len(markets)
    duplicate_cids = len({row["condition_id"] for row in markets}) != len(markets)
    duplicate_tokens = len({row["token_id"] for row in tokens}) != len(tokens)
    identity_unresolved = [row for row in markets if row.get("identity_status") != "VERIFIED"]
    if unresolved_candidates or missing_rationales or missing_review or duplicate_market_ids or duplicate_cids or duplicate_tokens or identity_unresolved:
        raise ValueError("universe quality gates failed; see candidate/edge identity checks")

    market_by_cid = {row["condition_id"]: row for row in markets}
    coverage_rows = read_csv(args.orderbook_coverage)
    if {row["condition_id"] for row in coverage_rows} != set(market_by_cid):
        raise ValueError("order-book coverage does not contain every frozen ETS market")

    for cid, item in fill.get("coverage_by_condition_id", {}).items():
        market = market_by_cid.get(cid)
        if market is None:
            raise ValueError(f"fill coverage contains CID outside frozen universe: {cid}")
        expected_start = item.get("expected_start") or ""
        row_count = int(item.get("row_count") or 0)
        file_count = int(item.get("file_count") or 0)
        gaps = item.get("known_gaps") or []
        gap_reason = "; ".join(sorted({str(g.get("reason", "")) for g in gaps if g.get("reason")}))
        coverage_rows.append({
            "market_id": item.get("market_id") or market["market_id"], "condition_id": cid,
            "source": "POLYLEVIATHAN_OCI_TRADES", "data_type": "fills",
            "expected_start": expected_start, "available_start": iso_epoch(item.get("available_start")),
            "available_end": iso_epoch(item.get("available_end")), "resolved_at": item.get("resolved_at") or "",
            "row_count": row_count, "file_count": file_count,
            "complete_to_source_bounds": bool(item.get("complete_to_source_bounds")),
            "known_gaps": json.dumps(gaps, separators=(",", ":")) if gaps else "",
            "gap_reason": gap_reason, "source_version": "Polyleviathan trades lake + custody tx_hash/block_number join",
        })
    coverage_target = args.output_dir / "ETS_DATA_COVERAGE.csv"
    coverage_rows.sort(key=lambda row: (row["market_id"], row["source"], row["data_type"]))
    write_csv(coverage_target, coverage_rows, COVERAGE_FIELDS)

    history_rows = []
    for market in markets:
        cid = market["condition_id"]
        related = [row for row in coverage_rows if row["condition_id"] == cid]
        fill_rows = [row for row in related if row["data_type"] == "fills"]
        book_rows = [row for row in related if row["source"] != "POLYLEVIATHAN_OCI_TRADES"]
        all_starts = [row["available_start"] for row in related if row.get("available_start")]
        all_ends = [row["available_end"] for row in related if row.get("available_end")]
        history_rows.append({
            "market_id": market["market_id"], "condition_id": cid,
            "earliest_historical_data_at": min(all_starts, default=""),
            "latest_historical_data_at": max(all_ends, default=""),
            "fill_row_count": sum(int(row["row_count"]) for row in fill_rows),
            "fill_available_start": min((row["available_start"] for row in fill_rows if row.get("available_start")), default=""),
            "fill_available_end": max((row["available_end"] for row in fill_rows if row.get("available_end")), default=""),
            "fill_complete_to_source_bounds": all(bool(row["complete_to_source_bounds"]) for row in fill_rows),
            "orderbook_row_count": sum(int(row["row_count"]) for row in book_rows),
            "orderbook_available_start": min((row["available_start"] for row in book_rows if row.get("available_start")), default=""),
            "orderbook_available_end": max((row["available_end"] for row in book_rows if row.get("available_end")), default=""),
        })
    history_target = args.output_dir / "ETS_MARKET_HISTORY_COVERAGE.csv"
    write_csv(history_target, history_rows, list(history_rows[0]) if history_rows else ["market_id", "condition_id"])

    fill_history = {cid: item for cid, item in fill.get("coverage_by_condition_id", {}).items() if int(item.get("row_count") or 0) > 0}
    fill_partial = [cid for cid, item in fill.get("coverage_by_condition_id", {}).items() if item.get("known_gaps")]
    fill_full_with_rows = [
        cid for cid, item in fill_history.items()
        if item.get("complete_to_source_bounds") and not item.get("known_gaps")
    ]
    fill_no_history = [
        cid for cid, item in fill.get("coverage_by_condition_id", {}).items()
        if not int(item.get("row_count") or 0) and not item.get("known_gaps")
    ]
    book_history = {
        row["condition_id"] for row in coverage_rows
        if row["source"] != "POLYLEVIATHAN_OCI_TRADES" and int(row["row_count"]) > 0
        and row["data_type"] in {"depth_snapshots", "book_changes"}
    }
    book_partial = {
        row["condition_id"] for row in coverage_rows
        if row["source"] != "POLYLEVIATHAN_OCI_TRADES" and (
            "missing required archive" in row.get("gap_reason", "").lower()
            or any("archive" in str(gap.get("reason", "")).lower() for gap in gap_items(row.get("known_gaps")))
        )
    }
    orderbook_rows_by_market: dict[str, int] = defaultdict(int)
    for row in coverage_rows:
        if row["source"] != "POLYLEVIATHAN_OCI_TRADES":
            orderbook_rows_by_market[row["condition_id"]] += int(row["row_count"])
    orderbook_any = {cid for cid, count in orderbook_rows_by_market.items() if count > 0}
    orderbook_full = orderbook_any - book_partial
    orderbook_no_history = set(market_by_cid) - orderbook_any - book_partial

    relation_counts = freeze.get("relationship_class_counts", {})
    candidate_count = int(freeze["candidate_ets_market_count"])
    accepted_count = int(freeze["accepted_ets_market_count"])
    rejected_count = int(freeze["rejected_candidate_count"])
    direct_duplicate_count = int(freeze.get("rejected_direct_duplicate_count", 0))
    total_raw_bytes = int(fill.get("bytes", 0)) + int(orderbooks.get("bytes", 0))
    all_fill_files_hashed = all(len(row.get("sha256", "")) == 64 for row in fill.get("files", []))
    all_book_files_hashed = all(len(row.get("sha256", "")) == 64 for row in orderbooks.get("files", []))
    orderbook_checks = orderbook_quality.get("acquisition", {})
    freeze_time = parse_time(freeze.get("freeze_timestamp"))
    fill_time = parse_time(fill.get("created_at"))
    orderbook_times = [parse_time(item.get("created_at")) for item in orderbooks.get("shards", [])]
    orderbook_times = [value for value in orderbook_times if value is not None]
    acquired_after_freeze = bool(freeze_time and fill_time and fill_time > freeze_time and orderbook_times and min(orderbook_times) > freeze_time)

    known_blockers = []
    if fill.get("status") != "COMPLETE":
        known_blockers.append({
            "type": "fill_source_gaps", "detail": f"{len(fill.get('known_gaps', []))} source/block-provenance gaps; see fill manifest",
        })
    known_blockers.append({
        "type": "fill_to_kaggle_handoff",
        "detail": "The repository Kaggle Actions runner has run/status/output/auth_check only; no dataset upload action. Fills remain in the authorized OCI freeze-scoped prefix.",
    })
    if orderbooks.get("missing_required_archive_hours"):
        known_blockers.append({
            "type": "orderbook_archive_gaps", "detail": f"{orderbooks['missing_required_archive_hours']} required PMXT archive hours were absent within source bounds",
        })

    quality = {
        "status": "PARTIAL_WITH_DOCUMENTED_BLOCKERS" if known_blockers else "COMPLETE",
        "identity": {
            "sig_anchors": len(read_csv(args.output_dir / "SIG_ANCHOR_UNIVERSE.csv")),
            "expected_sig_anchors": freeze["sig_anchor_count"],
            "candidate_ets_markets_reviewed": candidate_count,
            "accepted_ets_markets": accepted_count,
            "rejected_candidates": rejected_count,
            "rejected_direct_mapping_duplicates": direct_duplicate_count,
            "unique_cids": len({row["condition_id"] for row in markets}),
            "expected_unique_cids": freeze["unique_cid_count"],
            "unique_tokens": len({row["token_id"] for row in tokens}),
            "expected_unique_tokens": freeze["unique_token_count"],
            "duplicate_market_ids": duplicate_market_ids,
            "duplicate_cids": duplicate_cids,
            "duplicate_tokens": duplicate_tokens,
            "unresolved_accepted_market_identities": len(identity_unresolved),
            "identity_gate_pass": (
                len(markets) == accepted_count and len({row["condition_id"] for row in markets}) == freeze["unique_cid_count"]
                and len({row["token_id"] for row in tokens}) == freeze["unique_token_count"] and not identity_unresolved
            ),
        },
        "relationships": {
            "accepted_edges": len(accepted_edges),
            "relationship_class_counts": relation_counts,
            "accepted_markets_without_sig_anchor_edge": len(missing_review),
            "accepted_edges_without_rationale": len(missing_rationales),
            "pending_candidates": len(unresolved_candidates),
            "title_similarity_only_edges": 0,
            "relationship_gate_pass": not missing_review and not missing_rationales and not unresolved_candidates,
        },
        "freeze": {
            "freeze_sha256": sha256(freeze_path),
            "freeze_timestamp": freeze.get("freeze_timestamp"),
            "acquired_after_freeze": acquired_after_freeze,
            "repository_commit_at_freeze": freeze.get("repository_commit_at_freeze"),
            "freeze_gate_pass": acquired_after_freeze and not freeze.get("pending_candidate_count") and not freeze.get("pending_relationship_review_count"),
        },
        "fills": {
            "status": fill.get("status"), "conditions_attempted": fill.get("condition_count_attempted"),
            "conditions_expected": freeze["unique_cid_count"], "tokens_attempted": fill.get("token_count_attempted"),
            "tokens_expected": freeze["unique_token_count"], "rows_with_source_backed_block_number": fill.get("row_count", 0),
            "files": fill.get("file_count", 0), "bytes": fill.get("bytes", 0),
            "duplicate_rows_removed": fill.get("duplicate_rows_removed", 0),
            "missing_or_unjoinable_source_gaps": len(fill.get("known_gaps", [])),
            "all_output_file_sha256_present": all_fill_files_hashed,
            "markets_with_fill_history": len(fill_history), "markets_with_fill_gaps": len(fill_partial),
            "markets_with_fill_history_and_complete_source_coverage": len(fill_full_with_rows),
            "markets_with_no_fill_rows_and_no_known_gap": len(fill_no_history),
            "dedup_key": fill.get("dedup_key"), "ordering": fill.get("block_number_join", {}).get("ordering"),
            "all_conditions_attempted": fill.get("condition_count_attempted") == freeze["unique_cid_count"],
        },
        "orderbooks": {
            "kernel_id": orderbooks.get("kernel_id"), "kernel_version": args.kernel_version,
            "shards_expected": 5, "shards_received": len(orderbooks.get("shards", [])),
            "all_accepted_markets_attempted": orderbooks.get("cid_count") == freeze["unique_cid_count"]
            and orderbooks.get("token_count") == freeze["unique_token_count"],
            "markets_with_book_history": len(book_history), "markets_with_any_orderbook_rows": len(orderbook_any),
            "files": orderbooks.get("file_count", 0), "bytes": orderbooks.get("bytes", 0),
            "row_counts_by_type": orderbooks.get("row_counts_by_type", {}),
            "required_archive_hours_scanned": orderbooks.get("source_hours_scanned", 0),
            "missing_required_archive_hours": orderbooks.get("missing_required_archive_hours", 0),
            "all_output_file_sha256_present": all_book_files_hashed,
            "all_source_hours_recorded": orderbook_checks.get("all_source_hours_recorded"),
            "ordering_semantics": orderbooks.get("ordering_semantics"),
        },
        "blockers": known_blockers,
    }
    quality_target = args.output_dir / "ETS_QUALITY.json"
    quality_target.write_text(json.dumps(quality, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    minmax: dict[str, dict[str, str]] = {}
    for row in coverage_rows:
        if row.get("available_start"):
            target = minmax.setdefault(row["data_type"], {"minimum": "", "maximum": ""})
            target["minimum"] = min(target["minimum"], row["available_start"]) if target["minimum"] else row["available_start"]
        if row.get("available_end"):
            target = minmax.setdefault(row["data_type"], {"minimum": "", "maximum": ""})
            target["maximum"] = max(target["maximum"], row["available_end"])
    row_counts = dict(orderbooks.get("row_counts_by_type", {}))
    row_counts["fills"] = int(fill.get("row_count", 0))
    byte_counts = {"fills": int(fill.get("bytes", 0)), "orderbooks": int(orderbooks.get("bytes", 0))}
    file_counts = {"fills": int(fill.get("file_count", 0)), "orderbooks": int(orderbooks.get("file_count", 0))}
    repository_commit = subprocess.check_output(["git", "-C", str(REPO), "rev-parse", "HEAD"], text=True).strip()
    manifest = {
        "schema_version": 1,
        "dataset_id": orderbooks.get("dataset_id"),
        "dataset_layout": {
            "orderbooks": {"kind": "private Kaggle kernel output", "kernel_id": orderbooks.get("kernel_id"), "version": args.kernel_version},
            "fills": {"kind": "OCI object prefix", "bucket": fill.get("bucket"), "prefix": fill.get("object_prefix"),
                      "manifest_object": fill_summary.get("manifest_object"), "manifest_sha256": fill_summary.get("manifest_sha256")},
        },
        "repository_commit": repository_commit,
        "repository_commit_at_freeze": freeze.get("repository_commit_at_freeze"),
        "repository_commits_by_source": {
            "fills": fill.get("repository_commit"),
            "orderbook_shards": sorted({item.get("repository_commit") for item in orderbooks.get("shards", []) if item.get("repository_commit")}),
            "finalizer": orderbooks.get("repository_commit"),
        },
        "canonical_sig_mapping_sha256": freeze["canonical_sig_mapping_sha256"],
        "mapping_acceptance_sha256": freeze["mapping_acceptance_sha256"],
        "ets_universe_freeze_sha256": sha256(freeze_path),
        "accepted_ets_market_count": accepted_count,
        "cid_count": freeze["unique_cid_count"], "token_count": freeze["unique_token_count"],
        "source_list": [
            {"source": fill.get("source"), "version": fill.get("source_version"), "data_type": "on_chain_economic_fills"},
            {"source": orderbooks.get("source"), "versions": orderbooks.get("source_versions"), "data_type": "historical_orderbooks"},
            {"source": "Gamma API", "event_snapshot_sha256": freeze["sha256"]["gamma_metadata"],
             "candidate_snapshot_sha256": freeze["gamma_candidate_snapshot_sha256"],
             "data_type": "identity_lifecycle_resolution_metadata"},
        ],
        "source_versions": {
            "fills": fill.get("source_version", "POLYLEVIATHAN_TRADES_LAKE_CANONICAL"),
            "orderbooks": orderbooks.get("source_versions"),
            "pipeline_wheel_commit": sorted({item.get("pipeline_wheel_commit") for item in orderbooks.get("shards", []) if item.get("pipeline_wheel_commit")}),
        },
        "minimum_maximum_timestamps_utc_by_type": minmax,
        "row_counts_by_type": row_counts,
        "file_counts_by_source": file_counts,
        "byte_counts_by_source": byte_counts,
        "total_raw_data_bytes": total_raw_bytes,
        "sha256": {
            "ets_universe_freeze": sha256(freeze_path),
            "gamma_discovery_metadata": sha256(args.gamma_metadata),
            "gamma_candidate_snapshot": freeze["gamma_candidate_snapshot_sha256"],
            "fill_manifest": sha256(args.fill_manifest),
            "orderbook_manifest": sha256(args.orderbook_manifest),
            "orderbook_coverage": sha256(args.orderbook_coverage),
            "combined_coverage": sha256(coverage_target),
            "market_history_coverage": sha256(history_target),
        },
        "coverage_summary": {
            "markets_with_fill_history": len(fill_history),
            "markets_with_fill_gaps": len(fill_partial),
            "markets_with_orderbook_history": len(book_history),
            "markets_with_any_orderbook_rows": len(orderbook_any),
            "markets_with_orderbook_rows_and_no_archive_gap": len(orderbook_full),
            "markets_with_no_orderbook_rows_and_no_archive_gap": len(orderbook_no_history),
            "markets_with_orderbook_archive_gaps": len(book_partial),
            "fill_status": fill.get("status"),
            "orderbook_missing_required_archive_hours": orderbooks.get("missing_required_archive_hours", 0),
        },
        "ordering_semantics": {
            "fills": "block_number, log_index, token_id; timestamp is a one-second UTC block-time field; no tx_hash sorting",
            "orderbooks": orderbooks.get("ordering_semantics"),
        },
        "known_gaps": {
            "fills": fill.get("known_gaps", []),
            "missing_trade_object_days": fill.get("missing_trade_object_days", []),
            "missing_pmxt_archive_hours": [
                gap for gap in orderbooks.get("known_gaps", []) if gap.get("type") == "missing_required_pmxt_archive_hours"
            ],
            "fill_handoff_to_kaggle": "Blocked: no sanctioned dataset upload action exists in the repository Kaggle runner. Fills remain addressable in the freeze-scoped OCI prefix; no ad-hoc credential path was used.",
        },
        "coverage_paths": {
            "market_source_coverage": "ETS_DATA_COVERAGE.csv",
            "market_history_summary": "ETS_MARKET_HISTORY_COVERAGE.csv",
            "orderbook_coverage_in_kaggle_output": orderbooks.get("coverage_path"),
        },
        "fill_timestamp_semantics": fill.get("timestamp_semantics"),
        "fill_side_semantics": fill.get("side_semantics"),
        "gamma_metadata_sha256": freeze["sha256"]["gamma_metadata"],
        "kaggle_artifact": {"kernel_id": orderbooks.get("kernel_id"), "kernel_version": args.kernel_version},
        "quality_path": "ETS_QUALITY.json",
    }
    manifest_target = args.output_dir / "ETS_DATA_MANIFEST.json"
    manifest_target.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    relationship_lines = [f"- `{name}`: {count} accepted edges" for name, count in sorted(relation_counts.items())]
    coverage_md = [
        "# ETS historical coverage",
        "",
        f"Frozen adjacent markets: {accepted_count}; fills with rows: {len(fill_history)}; order-book markets with depth/book rows: {len(book_history)}.",
        "",
        f"Fill export: {fill.get('status')}; {fill.get('row_count', 0)} source-backed rows in {fill.get('file_count', 0)} files, {fill.get('bytes', 0)} bytes.",
        f"Fill dates: {fill.get('minimum_timestamp_utc')} to {fill.get('maximum_timestamp_utc')}; block/log order retained.",
        f"Order-books: {orderbooks.get('row_counts_by_type', {})}; {orderbooks.get('file_count', 0)} files, {orderbooks.get('bytes', 0)} bytes.",
        f"PMXT source bounds: {orderbooks.get('source_archive_bounds')}; missing required archive hours: {orderbooks.get('missing_required_archive_hours', 0)}.",
        "",
        "## Source coverage by market",
        "",
        f"- Fill history rows present: {len(fill_history)} / {accepted_count}.",
        f"- Fill history with complete source coverage: {len(fill_full_with_rows)}; no rows and no known source gap: {len(fill_no_history)}.",
        f"- Fill rows with known source or block-provenance gaps: {len(fill_partial)} markets.",
        f"- Order-book depth or book-change history present: {len(book_history)} / {accepted_count}.",
        f"- Order-book markets with any normalized rows: {len(orderbook_any)} / {accepted_count}.",
        f"- Order-book rows with no archive gap: {len(orderbook_full)}; no rows and no known archive gap: {len(orderbook_no_history)}.",
        f"- Order-book markets affected by missing PMXT archive hours: {len(book_partial)}.",
        "",
        "## Limitations and unresolved gaps",
        "",
        "- PMXT is snapshot-grade. Same-millisecond order, queue position, and exact cancellation sequencing are not recoverable; V1 receive time is not a venue receive time.",
        f"- Fill export status is `{fill.get('status')}`. See `ETS_DATA_COVERAGE.csv` and the fill manifest for missing trade-object dates and transactions without custody block evidence.",
        "- The fill object prefix is separate from the Kaggle kernel output because the repository runner has no dataset upload action.",
        "",
        "## Relationship-class counts",
        "",
        *relationship_lines,
        "",
        f"Manifest: `ETS_DATA_MANIFEST.json` (SHA-256 `{sha256(manifest_target)}`).",
    ]
    (args.output_dir / "ETS_DATA_COVERAGE.md").write_text("\n".join(coverage_md) + "\n", encoding="utf-8")

    family_market_counts = Counter()
    for market in markets:
        text = " ".join(market.get(key, "") for key in ("question", "event_title", "slug", "event_slug")).lower()
        classes = set(json.loads(market.get("relationship_class_set") or "[]"))
        if classes.intersection({"joint_chamber", "joint_outcome"}):
            family_market_counts["Balance of Power / joint outcomes"] += 1
        if "house" in text and classes.intersection({"chamber_control", "joint_chamber", "joint_outcome", "seat_exact", "seat_range", "seat_threshold"}):
            family_market_counts["House control / seat totals"] += 1
        if "senate" in text and classes.intersection({"chamber_control", "joint_chamber", "joint_outcome", "seat_exact", "seat_range", "seat_threshold"}):
            family_market_counts["Senate control / seat totals"] += 1
        if classes.intersection({"SEAT_EXACT", "SEAT_RANGE", "SEAT_THRESHOLD"}):
            family_market_counts["Seat exact/range/threshold"] += 1
        if "pivotal_race" in classes:
            family_market_counts["Pivotal races"] += 1
        if "multi_race_combo" in classes:
            family_market_counts["Multi-race combinations"] += 1
        if "same_state_related" in classes:
            family_market_counts["Same-state related races"] += 1
        if "other_economically_linked" in classes:
            family_market_counts["Other economically linked"] += 1
    family_lines = [f"- {label}: {count} markets" for label, count in sorted(family_market_counts.items())]
    handoff = [
        f"SIG anchors: {freeze['sig_anchor_count']}",
        f"Existing direct PM markets: {freeze['direct_anchor_counts']['direct_market_ids']}",
        f"ETS candidates reviewed: {candidate_count}",
        f"Accepted adjacent ETS markets: {accepted_count}",
        f"Rejected candidates: {rejected_count}",
        f"Unique ETS CIDs: {freeze['unique_cid_count']}",
        f"Unique ETS tokens: {freeze['unique_token_count']}",
        f"Markets with fill history: {len(fill_history)} / {accepted_count}",
        f"Markets with order-book history: {len(book_history)} / {accepted_count}",
        f"Raw data size: {total_raw_bytes} bytes ({fill.get('bytes', 0)} fill + {orderbooks.get('bytes', 0)} order-book bytes)",
        f"Kaggle artifact: {orderbooks.get('kernel_id')} version {args.kernel_version}",
        "",
        "## Universe",
        "",
        "Accepted relationship classes and edge counts:",
        *relationship_lines,
        "",
        "Unique market counts by visible family (categories may overlap):",
        *(family_lines or ["- No accepted markets in those text/class buckets."]),
        "",
        "## Coverage",
        "",
        f"Order-book source: `{orderbooks.get('kernel_id')}` version {args.kernel_version}; final output is a private Kaggle kernel output. Source range: `{orderbooks.get('source_archive_bounds')}`.",
        f"Fill source: `{fill.get('object_prefix')}` in bucket `{fill.get('bucket')}`; manifest `{fill_summary.get('manifest_object')}` with SHA-256 `{fill_summary.get('manifest_sha256')}`.",
        f"Combined raw Parquet: {total_raw_bytes} bytes, {sum(file_counts.values())} files, {sum(row_counts.values())} rows.",
        "Coverage rows are in `ETS_DATA_COVERAGE.csv`; market-level date limits are in `ETS_MARKET_HISTORY_COVERAGE.csv`.",
        "",
        "## Important gaps",
        "",
        *(f"- {item['type']}: {item['detail']}" for item in known_blockers),
        "- Read PMXT evidence as snapshot/recorder evidence. `FULL_EVENT_REPLAY` is not claimed.",
        "",
        "## R3 handoff",
        "",
        "- Graph: `ETS_RELATIONSHIP_GRAPH.json`.",
        "- CID/token universe: `ETS_CONDITION_IDS.txt`, `ETS_TOKEN_IDS.txt`, `ETS_CONDITION_TOKEN_MAP.json`, and `ETS_TOKEN_INVENTORY.csv`.",
        "- Freeze: `ETS_UNIVERSE_FREEZE.json`.",
        "- Data manifest: `ETS_DATA_MANIFEST.json`; coverage: `ETS_DATA_COVERAGE.csv`.",
        f"- Order-books: mount `{orderbooks.get('kernel_id')}` as a Kaggle kernel source at version {args.kernel_version}.",
        f"- Fills: read OCI prefix `{fill.get('object_prefix')}`; not mounted to Kaggle by this runner.",
        "- Fill canonical order is `block_number, log_index`; do not sort by `tx_hash`.",
        "- PMXT ordering/evidence semantics follow DATA-001; same-millisecond events are not totally ordered.",
        "",
        "No predictive modelling, lead/lag testing, feature selection, or alpha evaluation was performed.",
    ]
    (args.output_dir / "MASTER_HANDOFF_R25.md").write_text("\n".join(handoff) + "\n", encoding="utf-8")

    print(json.dumps({
        "status": quality["status"],
        "accepted_ets_markets": accepted_count,
        "cids": freeze["unique_cid_count"], "tokens": freeze["unique_token_count"],
        "fill_rows": fill.get("row_count"), "orderbook_files": orderbooks.get("file_count"),
        "raw_bytes": total_raw_bytes, "manifest_sha256": sha256(manifest_target),
        "quality_path": str(quality_target), "coverage_path": str(coverage_target),
    }, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
