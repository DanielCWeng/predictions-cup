from __future__ import annotations

import csv
import hashlib
import json
import shutil
from collections import Counter
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq

ROOT = Path("/kaggle/input")
OUT = Path("/kaggle/working")
TARGET_HINT = "sig-cup-data003-orderbooks"


def sha256_file(path: Path, chunk_size: int = 8 * 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def jsonish(path: Path) -> bool:
    return path.suffix.lower() in {".json", ".jsonl", ".yaml", ".yml", ".txt", ".csv"}


def dataset_root() -> Path:
    candidates = [p for p in ROOT.iterdir() if p.is_dir()]
    exact = [p for p in candidates if TARGET_HINT in p.name]
    if len(exact) == 1:
        return exact[0]
    if len(candidates) == 1:
        return candidates[0]
    raise RuntimeError(f"cannot identify dataset root from {[p.name for p in candidates]}")


def main() -> None:
    root = dataset_root()
    files = sorted(p for p in root.rglob("*") if p.is_file())
    rows: list[dict[str, Any]] = []
    parquet_schemas: Counter[str] = Counter()
    parquet_examples: dict[str, dict[str, Any]] = {}
    manifest_records: list[dict[str, Any]] = []

    manifest_out = OUT / "probe_manifests"
    manifest_out.mkdir(parents=True, exist_ok=True)

    for path in files:
        rel = path.relative_to(root)
        size = path.stat().st_size
        record: dict[str, Any] = {
            "path": str(rel),
            "size_bytes": size,
            "suffix": path.suffix.lower(),
        }
        parts = set(rel.parts)
        is_manifest = "_manifests" in parts or rel.name.startswith("_manifest") or rel.name.startswith("manifest")
        if is_manifest:
            dest = manifest_out / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, dest)
            record["sha256"] = sha256_file(path)
            if jsonish(path) and size <= 5_000_000:
                try:
                    text = path.read_text(encoding="utf-8")
                    if path.suffix.lower() == ".json":
                        payload = json.loads(text)
                        manifest_records.append({"path": str(rel), "json": payload})
                    else:
                        manifest_records.append({"path": str(rel), "text_preview": text[:20000]})
                except Exception as exc:
                    manifest_records.append({"path": str(rel), "read_error": repr(exc)})

        if path.suffix.lower() == ".parquet":
            try:
                pf = pq.ParquetFile(path)
                schema_text = str(pf.schema_arrow)
                parquet_schemas[schema_text] += 1
                if schema_text not in parquet_examples:
                    parquet_examples[schema_text] = {
                        "path": str(rel),
                        "rows": pf.metadata.num_rows,
                        "row_groups": pf.metadata.num_row_groups,
                        "columns": pf.schema_arrow.names,
                    }
                record["rows"] = pf.metadata.num_rows
                record["row_groups"] = pf.metadata.num_row_groups
                record["columns"] = "|".join(pf.schema_arrow.names)
            except Exception as exc:
                record["parquet_error"] = repr(exc)
        rows.append(record)

    with (OUT / "PROBE_FILES.csv").open("w", encoding="utf-8", newline="") as handle:
        fieldnames = sorted({key for row in rows for key in row})
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    schemas = []
    for schema_text, count in parquet_schemas.most_common():
        example = parquet_examples[schema_text]
        schemas.append({
            "count_files": count,
            "schema": schema_text,
            "example": example,
        })

    summary = {
        "dataset_root": str(root),
        "dataset_dir_name": root.name,
        "file_count": len(files),
        "total_bytes": sum(int(r["size_bytes"]) for r in rows),
        "parquet_file_count": sum(1 for r in rows if r["suffix"] == ".parquet"),
        "manifest_file_count": len(manifest_records),
        "ev18_matches": [r["path"] for r in rows if "ev18" in str(r["path"]).lower()],
        "schema_signatures": schemas,
        "manifest_records": manifest_records,
    }
    (OUT / "PROBE_SUMMARY.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({
        "dataset_root": summary["dataset_root"],
        "file_count": summary["file_count"],
        "total_bytes": summary["total_bytes"],
        "parquet_file_count": summary["parquet_file_count"],
        "manifest_file_count": summary["manifest_file_count"],
        "ev18_matches": summary["ev18_matches"][:20],
        "schema_signature_count": len(schemas),
    }, indent=2))


if __name__ == "__main__":
    main()
