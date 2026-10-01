from __future__ import annotations

import csv
import hashlib
import json
import os
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

INPUT = Path("/kaggle/input")
WORK = Path("/kaggle/working/005g_data003_orderbook_audit")
WORK.mkdir(parents=True, exist_ok=True)

EXPECTED_DATASET_SLUG = "polyleviathan/sig-cup-data003-orderbooks"
EXPECTED_MOUNT_NAME = "sig-cup-data003-orderbooks"
EXPECTED_MAPPING_SHA256 = "9bc3d55317ace5e7e56eb0e4deaf2d36d09421b2861b7104977a96bedfc16df2"
EXPERIMENT = "EXPERIMENT-005G"
SCHEMA_VERSION = 1
HASH_CHUNK = 8 * 1024 * 1024
ID_COLUMNS = (
    "token_id",
    "market_id",
    "condition_id",
    "exchange_id",
    "risk_group_id",
    "sig_exchange_id",
)
VERSION_COLUMNS = ("source_version", "acquisition_version", "schema_version")
TIME_RE = re.compile(r"(time|timestamp|observed_at|captured_at|sampled_at|created_at|updated_at)$", re.I)

CONFIG = {
    "experiment": EXPERIMENT,
    "schema_version": SCHEMA_VERSION,
    "dataset_slug": EXPECTED_DATASET_SLUG,
    "expected_mount_name": EXPECTED_MOUNT_NAME,
    "accepted_sig_pm_mapping_sha256": EXPECTED_MAPPING_SHA256,
    "audit_scope": "fresh DATA-003-linked orderbook corpus only",
    "forbidden_substitutes": [
        "DATA-001",
        "Hungary/Peru/Colombia historical tournament corpus",
        "old EXPERIMENT-005F corpus",
        "unrelated Polymarket samples",
    ],
    "scientific_results_allowed": False,
    "real_sig_orders_sent": False,
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(HASH_CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")


def safe_rel(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def resolve_dataset_root() -> Path:
    exact = INPUT / EXPECTED_MOUNT_NAME
    if exact.is_dir():
        return exact
    candidates = sorted(
        p for p in INPUT.iterdir()
        if p.is_dir() and EXPECTED_MOUNT_NAME in p.name
    )
    if len(candidates) == 1:
        return candidates[0]
    raise RuntimeError(
        f"Expected exactly one mount for {EXPECTED_DATASET_SLUG}; "
        f"exact={exact.exists()} candidates={[p.name for p in candidates]}"
    )


def file_family(rel: str) -> str:
    parts = Path(rel).parts
    if not parts:
        return "ROOT"
    if len(parts) == 1:
        return "ROOT"
    if parts[0] == "_manifests":
        return "_manifests"
    if parts[0].lower() == "ev18":
        return "ev18"
    return parts[0]


def parquet_schema_info(path: Path) -> dict[str, Any]:
    pf = pq.ParquetFile(path)
    md = pf.metadata
    schema = pf.schema_arrow
    out: dict[str, Any] = {
        "rows": int(md.num_rows),
        "row_groups": int(md.num_row_groups),
        "columns": [
            {"name": f.name, "type": str(f.type), "nullable": bool(f.nullable)}
            for f in schema
        ],
        "created_by": md.created_by,
        "serialized_size": int(md.serialized_size),
    }

    time_stats: dict[str, dict[str, str | None]] = {}
    names = schema.names
    for col_index, name in enumerate(names):
        if not TIME_RE.search(name):
            continue
        low: Any = None
        high: Any = None
        for rg in range(md.num_row_groups):
            col = md.row_group(rg).column(col_index)
            stats = col.statistics
            if stats is None or not stats.has_min_max:
                continue
            cmin, cmax = stats.min, stats.max
            if low is None or cmin < low:
                low = cmin
            if high is None or cmax > high:
                high = cmax
        time_stats[name] = {
            "min": None if low is None else str(low),
            "max": None if high is None else str(high),
        }
    out["time_stats_from_metadata"] = time_stats
    return out


def iter_batches(path: Path, columns: list[str], batch_size: int = 262_144) -> Iterable[pa.RecordBatch]:
    if not columns:
        return
    pf = pq.ParquetFile(path)
    yield from pf.iter_batches(batch_size=batch_size, columns=columns, use_threads=False)


def normalize_scalar(value: Any) -> str | None:
    if value is None:
        return None
    try:
        if hasattr(value, "as_py"):
            value = value.as_py()
    except Exception:
        pass
    if value is None:
        return None
    return str(value)


def scan_coverage(path: Path, names: list[str]) -> tuple[dict[str, set[str]], dict[str, tuple[str | None, str | None]]]:
    id_sets: dict[str, set[str]] = {name: set() for name in names if name in ID_COLUMNS}
    time_bounds: dict[str, tuple[str | None, str | None]] = {
        name: (None, None) for name in names if TIME_RE.search(name)
    }
    wanted = list(id_sets) + list(time_bounds)
    if not wanted:
        return id_sets, time_bounds

    for batch in iter_batches(path, wanted):
        table = pa.Table.from_batches([batch])
        for name in id_sets:
            arr = table[name]
            for value in arr.to_pylist():
                if value is not None:
                    id_sets[name].add(str(value))
        for name in time_bounds:
            arr = table[name]
            if len(arr) == 0:
                continue
            try:
                mm = pc.min_max(arr)
                lo = normalize_scalar(mm["min"])
                hi = normalize_scalar(mm["max"])
            except Exception:
                values = [normalize_scalar(v) for v in arr if normalize_scalar(v) is not None]
                lo = min(values) if values else None
                hi = max(values) if values else None
            prev_lo, prev_hi = time_bounds[name]
            if lo is not None and (prev_lo is None or lo < prev_lo):
                prev_lo = lo
            if hi is not None and (prev_hi is None or hi > prev_hi):
                prev_hi = hi
            time_bounds[name] = (prev_lo, prev_hi)
    return id_sets, time_bounds


def flatten_json_versions(value: Any, prefix: str = "") -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if isinstance(value, dict):
        for key, child in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            if "version" in str(key).lower() or "slug" in str(key).lower() or "dataset" in str(key).lower():
                if isinstance(child, (str, int, float, bool)) or child is None:
                    rows.append({"path": path, "value": child})
            rows.extend(flatten_json_versions(child, path))
    elif isinstance(value, list):
        for i, child in enumerate(value[:1000]):
            rows.extend(flatten_json_versions(child, f"{prefix}[{i}]"))
    return rows


def parse_small_manifest(path: Path, root: Path) -> dict[str, Any]:
    rel = safe_rel(path, root)
    info: dict[str, Any] = {
        "path": rel,
        "size_bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }
    if path.suffix.lower() == ".json" and path.stat().st_size <= 64 * 1024 * 1024:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            info["json_top_level"] = sorted(payload) if isinstance(payload, dict) else type(payload).__name__
            info["version_like_fields"] = flatten_json_versions(payload)[:200]
        except Exception as exc:
            info["parse_error"] = f"{type(exc).__name__}: {exc}"
    return info


def choose_dataset_version(manifests: list[dict[str, Any]]) -> dict[str, Any]:
    candidates: list[dict[str, Any]] = []
    for manifest in manifests:
        for item in manifest.get("version_like_fields", []):
            key = str(item.get("path", "")).lower()
            if any(term in key for term in ("dataset_version", "kaggle_version", "version_number")):
                candidates.append({"manifest": manifest["path"], **item})
    unique = sorted({str(x["value"]) for x in candidates if x.get("value") not in (None, "")})
    return {
        "status": "RESOLVED" if len(unique) == 1 else "AMBIGUOUS_OR_MISSING",
        "values": unique,
        "evidence": candidates[:50],
    }


def main() -> None:
    root = resolve_dataset_root()
    files = sorted(p for p in root.rglob("*") if p.is_file())
    if not files:
        raise RuntimeError(f"No files found under {root}")

    inventory: list[dict[str, Any]] = []
    schema_groups: dict[str, dict[str, Any]] = {}
    corrupt: list[dict[str, str]] = []
    global_ids: dict[str, set[str]] = defaultdict(set)
    global_time: dict[str, list[str | None]] = {}
    family_counts: Counter[str] = Counter()
    extension_counts: Counter[str] = Counter()
    manifests: list[dict[str, Any]] = []
    total_rows = 0
    total_bytes = 0

    for i, path in enumerate(files, 1):
        rel = safe_rel(path, root)
        fam = file_family(rel)
        ext = path.suffix.lower() or "<none>"
        family_counts[fam] += 1
        extension_counts[ext] += 1
        size = path.stat().st_size
        total_bytes += size

        row: dict[str, Any] = {
            "path": rel,
            "family": fam,
            "extension": ext,
            "size_bytes": size,
            "sha256": sha256_file(path),
        }

        is_manifest = "_manifest" in path.name.lower() or "_manifests" in Path(rel).parts
        if is_manifest:
            manifests.append(parse_small_manifest(path, root))

        if ext == ".parquet":
            try:
                pinfo = parquet_schema_info(path)
                row["rows"] = pinfo["rows"]
                row["row_groups"] = pinfo["row_groups"]
                total_rows += pinfo["rows"]

                schema_signature = sha256_bytes(
                    canonical_json_bytes(pinfo["columns"])
                )
                row["schema_signature"] = schema_signature
                group = schema_groups.setdefault(
                    schema_signature,
                    {
                        "schema_signature": schema_signature,
                        "columns": pinfo["columns"],
                        "files": 0,
                        "rows": 0,
                        "families": Counter(),
                        "created_by": Counter(),
                    },
                )
                group["files"] += 1
                group["rows"] += pinfo["rows"]
                group["families"][fam] += 1
                group["created_by"][str(pinfo.get("created_by"))] += 1

                names = [x["name"] for x in pinfo["columns"]]
                ids, time_bounds = scan_coverage(path, names)
                for name, values in ids.items():
                    global_ids[name].update(values)
                for name, (lo, hi) in time_bounds.items():
                    current = global_time.setdefault(name, [None, None])
                    if lo is not None and (current[0] is None or lo < current[0]):
                        current[0] = lo
                    if hi is not None and (current[1] is None or hi > current[1]):
                        current[1] = hi
            except Exception as exc:
                corrupt.append({"path": rel, "error": f"{type(exc).__name__}: {exc}"})
                row["parquet_error"] = corrupt[-1]["error"]

        inventory.append(row)
        if i % 100 == 0 or i == len(files):
            print(f"AUDIT_PROGRESS files={i}/{len(files)} bytes={total_bytes}", flush=True)

    version_resolution = choose_dataset_version(manifests)
    schema_out: list[dict[str, Any]] = []
    for value in schema_groups.values():
        schema_out.append(
            {
                **{k: v for k, v in value.items() if k not in ("families", "created_by")},
                "families": dict(value["families"]),
                "created_by": dict(value["created_by"]),
            }
        )
    schema_out.sort(key=lambda x: (-x["rows"], x["schema_signature"]))

    config_hash = sha256_bytes(canonical_json_bytes(CONFIG))
    audit = {
        "schema_version": SCHEMA_VERSION,
        "experiment": EXPERIMENT,
        "phase": "INPUT_AUDIT",
        "generated_at": now_iso(),
        "authoritative": True,
        "scientific_result": "NOT_RUN",
        "dataset": {
            "slug": EXPECTED_DATASET_SLUG,
            "mount_name": root.name,
            "mount_path": str(root),
            "version_resolution": version_resolution,
            "file_count": len(files),
            "total_bytes": total_bytes,
            "total_parquet_rows": total_rows,
            "extensions": dict(extension_counts),
            "file_families": dict(family_counts),
            "ev18_present": any(p.lower() == "ev18" for p in family_counts),
            "manifest_count": len(manifests),
            "parquet_corrupt_count": len(corrupt),
        },
        "coverage": {
            name: {
                "count": len(values),
                "sample": sorted(values)[:50],
            }
            for name, values in sorted(global_ids.items())
        },
        "time_ranges": {
            name: {"min": bounds[0], "max": bounds[1]}
            for name, bounds in sorted(global_time.items())
        },
        "schemas": {
            "unique_schema_count": len(schema_out),
            "schema_inventory_path": "SCHEMA_INVENTORY.json",
        },
        "manifests": manifests,
        "corrupt_files": corrupt,
        "requested_audit_dimensions": {
            "schemas": "AUDITED",
            "row_counts": "AUDITED_FOR_PARQUET",
            "market_token_coverage": "AUDITED_WHERE_ID_COLUMNS_EXIST",
            "time_ranges": "AUDITED_WHERE_TIME_COLUMNS_EXIST",
            "acquisition_versions": "MANIFEST_AND_SCHEMA_EVIDENCE_ONLY",
            "source_versions": "MANIFEST_AND_SCHEMA_EVIDENCE_ONLY",
            "timestamp_precision": "SCHEMA_EVIDENCE_ONLY",
            "event_ordering": "NOT_YET_ASSESSED",
            "snapshot_cadence": "NOT_YET_ASSESSED",
            "depth_coverage": "SCHEMA_EVIDENCE_ONLY",
            "snapshot_delta_trade_event_semantics": "REQUIRES_SCHEMA_INTERPRETATION",
            "duplicated_observations": "NOT_YET_ASSESSED",
            "gaps": "NOT_YET_ASSESSED",
            "partial_markets": "NOT_YET_ASSESSED",
            "mapping_coverage": "REQUIRES_CANONICAL_MAPPING_JOIN",
            "relationship_to_DATA_003": "REQUIRES_MANIFEST_INTERPRETATION",
        },
        "provenance": {
            "dataset_slug": EXPECTED_DATASET_SLUG,
            "dataset_version": version_resolution,
            "file_hash_manifest": "FILE_INVENTORY.parquet",
            "accepted_sig_pm_mapping_sha256": EXPECTED_MAPPING_SHA256,
            "experiment_config_hash": config_hash,
            "repository_git_sha": "BOUND_BY_GITHUB_JOB_COMMIT_NOT_IN_KAGGLE_MOUNT",
        },
        "boundaries": {
            "used_DATA_001": False,
            "used_old_005F_corpus": False,
            "used_unrelated_polymarket_samples": False,
            "real_sig_orders_sent": False,
        },
    }

    gate_reasons: list[str] = []
    if root.name != EXPECTED_MOUNT_NAME:
        gate_reasons.append(f"mount name differs: {root.name}")
    if corrupt:
        gate_reasons.append(f"{len(corrupt)} corrupt/unreadable parquet files")
    if version_resolution["status"] != "RESOLVED":
        gate_reasons.append("dataset version unresolved or ambiguous from mounted evidence")
    if not manifests:
        gate_reasons.append("no manifest files discovered")

    provenance_gate = {
        "schema_version": 1,
        "experiment": EXPERIMENT,
        "generated_at": now_iso(),
        "status": "PASS" if not gate_reasons else "BLOCK_SCIENTIFIC_RESULTS",
        "reasons": gate_reasons,
        "dataset_slug": EXPECTED_DATASET_SLUG,
        "accepted_sig_pm_mapping_sha256": EXPECTED_MAPPING_SHA256,
        "config_hash": config_hash,
        "scientific_results_allowed": not gate_reasons,
        "real_sig_orders_sent": False,
    }

    table = pa.Table.from_pylist(inventory)
    pq.write_table(table, WORK / "FILE_INVENTORY.parquet", compression="zstd")
    write_json(WORK / "SCHEMA_INVENTORY.json", schema_out)
    write_json(WORK / "INPUT_AUDIT.json", audit)
    write_json(WORK / "PROVENANCE_GATE.json", provenance_gate)

    md = [
        "# EXPERIMENT-005G — INPUT AUDIT",
        "",
        f"- Generated: {audit['generated_at']}",
        f"- Dataset: {EXPECTED_DATASET_SLUG}",
        f"- Files: {len(files):,}",
        f"- Bytes: {total_bytes:,}",
        f"- Parquet rows (metadata sum): {total_rows:,}",
        f"- Unique parquet schemas: {len(schema_out)}",
        f"- Corrupt/unreadable parquet files: {len(corrupt)}",
        f"- ev18 present: {audit['dataset']['ev18_present']}",
        f"- Provenance gate: {provenance_gate['status']}",
        "",
        "## File families",
        "",
    ]
    for key, value in sorted(family_counts.items()):
        md.append(f"- {key}: {value:,} files")
    md.extend(["", "## Coverage"])
    for key, value in audit["coverage"].items():
        md.append(f"- {key}: {value['count']:,} distinct values")
    md.extend(["", "## Time ranges"])
    for key, value in audit["time_ranges"].items():
        md.append(f"- {key}: {value['min']} -> {value['max']}")
    md.extend(["", "## Gate reasons"])
    if gate_reasons:
        md.extend(f"- {reason}" for reason in gate_reasons)
    else:
        md.append("- none")
    md.extend([
        "",
        "This audit intentionally does not make a scientific 005G claim. "
        "Event ordering, duplicate/gap logic, cadence semantics, exact DATA-003 linkage, "
        "and replication feature semantics are interpreted only after this schema/provenance gate.",
        "",
        "REAL SIG ORDERS SENT: NO",
    ])
    (WORK / "INPUT_AUDIT.md").write_text("\n".join(md) + "\n", encoding="utf-8")

    handoff = {
        "experiment": EXPERIMENT,
        "phase": "INPUT_AUDIT",
        "status": provenance_gate["status"],
        "next_required_step": (
            "Interpret actual schemas/manifests, bind dataset version and DATA-003/mapping linkage, "
            "then implement canonical BBO/depth panels and strict 005F replication."
        ),
        "outputs": [
            "INPUT_AUDIT.json",
            "INPUT_AUDIT.md",
            "FILE_INVENTORY.parquet",
            "SCHEMA_INVENTORY.json",
            "PROVENANCE_GATE.json",
        ],
        "real_sig_orders_sent": False,
    }
    write_json(WORK / "AUDIT_HANDOFF.json", handoff)
    print(json.dumps(handoff, indent=2), flush=True)


if __name__ == "__main__":
    main()
