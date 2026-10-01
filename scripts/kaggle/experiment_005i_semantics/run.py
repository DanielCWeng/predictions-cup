from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

EXPERIMENT = "EXPERIMENT-005I"
DATASET_SLUG = "polyleviathan/sig-cup-data003-orderbooks"
MOUNT = "sig-cup-data003-orderbooks"
MAPPING_SHA256 = "9bc3d55317ace5e7e56eb0e4deaf2d36d09421b2861b7104977a96bedfc16df2"

INPUT = Path("/kaggle/input")
OUT = Path("/kaggle/working/005i_semantics")
OUT.mkdir(parents=True, exist_ok=True)

TIME_HINT = re.compile(r"(time|timestamp|observed|captured|sampled|created|updated|recv|receive|event_at|ts)", re.I)
ID_HINT = re.compile(r"(token|market|condition|exchange|event|risk_group|asset).*id|^id$", re.I)
PRICE_HINT = re.compile(r"(price|bid|ask|mid|spread)", re.I)
SIZE_HINT = re.compile(r"(size|qty|quantity|depth|volume)", re.I)

def now_iso() -> str:
    return datetime.now(UTC).isoformat()

def write_json(name: str, value: Any) -> None:
    (OUT / name).write_text(json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")

def root() -> Path:
    exact = INPUT / MOUNT
    if exact.is_dir():
        return exact
    candidates = [p for p in INPUT.iterdir() if p.is_dir() and MOUNT in p.name]
    if len(candidates) != 1:
        raise RuntimeError(f"dataset mount unresolved: exact={exact.exists()} candidates={[p.name for p in candidates]}")
    return candidates[0]

def schema_signature(schema: pa.Schema) -> str:
    return "|".join(f"{f.name}:{f.type}:{int(f.nullable)}" for f in schema)

def family(path: Path, base: Path) -> str:
    rel = path.relative_to(base)
    return rel.parts[0] if len(rel.parts) > 1 else "ROOT"

def safe_scalar(v: Any) -> Any:
    if hasattr(v, "as_py"):
        v = v.as_py()
    if isinstance(v, (bytes, bytearray)):
        return v.hex()
    if isinstance(v, datetime):
        return v.isoformat()
    if isinstance(v, (str, int, float, bool)) or v is None:
        return v
    return str(v)

def sample_table(pf: pq.ParquetFile, limit: int = 5) -> list[dict[str, Any]]:
    if pf.metadata.num_rows == 0:
        return []
    batch = next(pf.iter_batches(batch_size=max(limit, 1), use_threads=False), None)
    if batch is None:
        return []
    table = pa.Table.from_batches([batch]).slice(0, limit)
    out = []
    for row in table.to_pylist():
        out.append({k: safe_scalar(v) for k, v in row.items()})
    return out

def time_bounds_from_metadata(pf: pq.ParquetFile, schema: pa.Schema) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    md = pf.metadata
    for i, name in enumerate(schema.names):
        if not TIME_HINT.search(name):
            continue
        lo = None
        hi = None
        for rg in range(md.num_row_groups):
            stats = md.row_group(rg).column(i).statistics
            if stats is None or not stats.has_min_max:
                continue
            try:
                a, b = stats.min, stats.max
                if lo is None or a < lo:
                    lo = a
                if hi is None or b > hi:
                    hi = b
            except Exception:
                pass
        out[name] = {"min": safe_scalar(lo), "max": safe_scalar(hi), "arrow_type": str(schema.field(name).type)}
    return out

def classify(path: str, names: list[str]) -> dict[str, Any]:
    low_path = path.lower()
    low = [n.lower() for n in names]
    joined = " ".join(low)
    scores = Counter()
    if "depth_snapshot" in low_path or "snapshot" in low_path:
        scores["DEPTH_SNAPSHOT"] += 4
    if "book_change" in low_path or "book_changes" in low_path or "delta" in low_path:
        scores["BOOK_CHANGE"] += 4
    if "trade" in low_path or "fill" in low_path:
        scores["TRADE_OR_FILL"] += 4
    if any(x in joined for x in ("bids", "asks", "bid_levels", "ask_levels", "levels")):
        scores["DEPTH_SNAPSHOT"] += 3
    if any(x in low for x in ("side", "price", "size")) and any("transaction" in x or "trade" in x for x in low):
        scores["TRADE_OR_FILL"] += 3
    if any(x in joined for x in ("old_price", "new_price", "change_type", "book_change")):
        scores["BOOK_CHANGE"] += 3
    if not scores:
        role = "UNKNOWN"
        confidence = "LOW"
    else:
        role, score = scores.most_common(1)[0]
        ties = [k for k, v in scores.items() if v == score]
        confidence = "HIGH" if score >= 5 and len(ties) == 1 else "MEDIUM" if len(ties) == 1 else "LOW"
    return {"role": role, "confidence": confidence, "scores": dict(scores)}

def main() -> None:
    base = root()
    files = sorted(p for p in base.rglob("*") if p.is_file())
    parquets = [p for p in files if p.suffix.lower() == ".parquet"]
    manifests = [p for p in files if "_manifest" in p.name.lower() or "_manifests" in p.parts]
    if not parquets:
        raise RuntimeError("No parquet files found in DATA-003-linked order-book corpus")

    schema_groups: dict[str, dict[str, Any]] = {}
    fam = Counter()
    total_rows = 0
    total_bytes = 0
    global_time_cols = Counter()
    global_id_cols = Counter()
    role_rows = Counter()
    role_files = Counter()
    ambiguous: list[dict[str, Any]] = []
    inventory: list[dict[str, Any]] = []

    for idx, path in enumerate(parquets, 1):
        rel = path.relative_to(base).as_posix()
        pf = pq.ParquetFile(path)
        schema = pf.schema_arrow
        names = schema.names
        rows = int(pf.metadata.num_rows)
        size = path.stat().st_size
        total_rows += rows
        total_bytes += size
        fam[family(path, base)] += 1

        sig = schema_signature(schema)
        if sig not in schema_groups:
            info = classify(rel, names)
            schema_groups[sig] = {
                "schema_signature": sig,
                "columns": [{"name": f.name, "type": str(f.type), "nullable": bool(f.nullable)} for f in schema],
                "sample_source": rel,
                "sample_rows": sample_table(pf),
                "time_bounds_metadata": time_bounds_from_metadata(pf, schema),
                "semantic_classification": info,
                "file_count": 0,
                "row_count": 0,
                "families": Counter(),
                "paths": [],
            }
        g = schema_groups[sig]
        g["file_count"] += 1
        g["row_count"] += rows
        g["families"][family(path, base)] += 1
        if len(g["paths"]) < 25:
            g["paths"].append(rel)

        cls = classify(rel, names)
        role_rows[cls["role"]] += rows
        role_files[cls["role"]] += 1
        if cls["confidence"] == "LOW":
            ambiguous.append({"path": rel, "classification": cls, "columns": names})

        for name in names:
            if TIME_HINT.search(name):
                global_time_cols[name] += 1
            if ID_HINT.search(name):
                global_id_cols[name] += 1

        inventory.append({
            "path": rel,
            "rows": rows,
            "bytes": size,
            "schema_signature": sig,
            "semantic_classification": cls,
        })
        if idx % 100 == 0 or idx == len(parquets):
            print(f"005I_AUDIT {idx}/{len(parquets)} rows={total_rows}", flush=True)

    groups = []
    for g in schema_groups.values():
        groups.append({
            **{k: v for k, v in g.items() if k != "families"},
            "families": dict(g["families"]),
        })
    groups.sort(key=lambda x: (-x["row_count"], x["sample_source"]))

    manifest_samples = []
    for path in manifests[:50]:
        rel = path.relative_to(base).as_posix()
        row = {"path": rel, "bytes": path.stat().st_size}
        if path.suffix.lower() == ".json" and path.stat().st_size < 8_000_000:
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                row["top_level"] = sorted(payload) if isinstance(payload, dict) else type(payload).__name__
                row["sample"] = payload if path.stat().st_size < 100_000 else None
            except Exception as exc:
                row["parse_error"] = f"{type(exc).__name__}: {exc}"
        manifest_samples.append(row)

    required_observable_time = bool(global_time_cols)
    required_identity = bool(global_id_cols)
    depth_like = role_files["DEPTH_SNAPSHOT"] + role_files["BOOK_CHANGE"]
    trade_like = role_files["TRADE_OR_FILL"]
    gate = {
        "experiment": EXPERIMENT,
        "generated_at": now_iso(),
        "dataset_slug": DATASET_SLUG,
        "accepted_mapping_sha256": MAPPING_SHA256,
        "parquet_files": len(parquets),
        "rows": total_rows,
        "bytes": total_bytes,
        "schema_count": len(groups),
        "observable_time_columns_present": required_observable_time,
        "identity_columns_present": required_identity,
        "depth_or_book_change_files": depth_like,
        "trade_or_fill_files": trade_like,
        "ambiguous_file_count": len(ambiguous),
        "scientific_modelling_allowed": required_observable_time and required_identity and depth_like > 0,
        "holdout_read": False,
        "make_modified": False,
        "real_sig_orders_sent": False,
    }

    write_json("INPUT_AUDIT.json", {
        "experiment": EXPERIMENT,
        "generated_at": now_iso(),
        "dataset_slug": DATASET_SLUG,
        "mount": str(base),
        "file_count": len(files),
        "parquet_file_count": len(parquets),
        "manifest_count": len(manifests),
        "total_parquet_rows": total_rows,
        "total_parquet_bytes": total_bytes,
        "families": dict(fam),
        "semantic_role_files": dict(role_files),
        "semantic_role_rows": dict(role_rows),
        "time_columns": dict(global_time_cols),
        "identity_columns": dict(global_id_cols),
        "manifest_samples": manifest_samples,
    })
    write_json("SCHEMA_SAMPLES.json", {"schemas": groups})
    write_json("PARQUET_INVENTORY.json", {"files": inventory})
    write_json("SEMANTIC_GATE.json", gate)
    write_json("AMBIGUOUS_FILES.json", {"files": ambiguous[:500]})
    write_json("AUDIT_HANDOFF.json", {
        "experiment": EXPERIMENT,
        "phase": "INPUT_SEMANTICS",
        "gate": gate,
        "next_action": "BUILD_OBSERVABLE_TIME_PANEL" if gate["scientific_modelling_allowed"] else "RESOLVE_SEMANTICS_BEFORE_MODELLING",
        "expiration_regime_research": "DEFERRED",
        "real_sig_orders_sent": False,
    })
    print(json.dumps(gate, sort_keys=True), flush=True)

if __name__ == "__main__":
    main()
