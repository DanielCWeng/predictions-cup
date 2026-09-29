# ruff: noqa
from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

import pandas as pd
import pyarrow.parquet as pq

DATASET_REF = "polyleviathan/sig-cup-data-004-ets-p0p1-fills"
DATASET_SLUG = "sig-cup-data-004-ets-p0p1-fills"
EXPECTED_SOURCE_MANIFEST_SHA = "f38c54291c96d0a34ed52fb383e51dfbc7273d195c9882dc82d374f6ba5dde53"
EXPECTED = {
    "markets": 298,
    "conditions": 298,
    "tokens": 596,
    "p0_markets": 210,
    "p1_markets": 88,
    "deduped_fill_rows": 231964,
}
OUT = Path("/kaggle/working/r3_fv_001_smoke")
OUT.mkdir(parents=True, exist_ok=True)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def locate_root() -> Path:
    roots = []
    for manifest in Path("/kaggle/input").rglob("MANIFEST.json"):
        if DATASET_SLUG in str(manifest.parent):
            roots.append(manifest.parent)
    roots = sorted(set(roots))
    if len(roots) != 1:
        raise RuntimeError(f"expected one mounted DATA-004 root, found {roots}")
    return roots[0]


def expand_directory_zips(root: Path) -> Path:
    expanded = OUT / "expanded"
    expanded.mkdir(exist_ok=True)
    for zpath in root.glob("*.zip"):
        target = expanded / zpath.stem
        target.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(zpath) as zf:
            zf.extractall(target)
    return expanded


def resolve_manifest_path(root: Path, expanded: Path, rel: str) -> Path:
    direct = root / rel
    if direct.is_file():
        return direct
    materialized = expanded / rel
    if materialized.is_file():
        return materialized
    raise FileNotFoundError(f"manifest member unavailable after zip expansion: {rel}")


def main() -> None:
    root = locate_root()
    package_manifest_path = root / "MANIFEST.json"
    source_manifest_path = root / "OCI_SOURCE_MANIFEST.json"
    quality_path = root / "QUALITY.json"

    package = json.loads(package_manifest_path.read_text(encoding="utf-8"))
    source = json.loads(source_manifest_path.read_text(encoding="utf-8"))
    quality = json.loads(quality_path.read_text(encoding="utf-8"))

    if package.get("dataset_id") != "DATA-004":
        raise RuntimeError("wrong package dataset_id")
    if package.get("version") != "v2" or source.get("version") != "v2":
        raise RuntimeError("DATA-004 package/source is not v2")
    if package.get("status") != "ACCEPTED_V2" or source.get("status") != "ACCEPTED_V2":
        raise RuntimeError("DATA-004 v2 is not accepted")
    if quality.get("all_gates_pass") is not True:
        raise RuntimeError("DATA-004 quality gate is not PASS")

    source_sha = sha256_file(source_manifest_path)
    if source_sha != EXPECTED_SOURCE_MANIFEST_SHA:
        raise RuntimeError(f"source manifest sha mismatch: {source_sha}")
    if package.get("source_oci", {}).get("manifest_sha256") != source_sha:
        raise RuntimeError("package source_oci manifest hash does not bind to OCI_SOURCE_MANIFEST.json")

    scope = package.get("scope", {})
    for key in ("markets", "conditions", "tokens", "p0_markets", "p1_markets"):
        if int(scope.get(key, -1)) != EXPECTED[key]:
            raise RuntimeError(f"scope mismatch {key}: {scope.get(key)}")

    counts = source.get("counts", {})
    for key in ("markets", "conditions", "tokens", "deduped_fill_rows"):
        if int(counts.get(key, -1)) != EXPECTED[key]:
            raise RuntimeError(f"source count mismatch {key}: {counts.get(key)}")

    expanded = expand_directory_zips(root)
    listed = package.get("files", [])
    if len(listed) != 369:
        raise RuntimeError(f"package manifest file count mismatch: {len(listed)}")

    verified_bytes = 0
    fill_paths = []
    fill_manifest_rows = 0
    for row in listed:
        rel = str(row["path"])
        path = resolve_manifest_path(root, expanded, rel)
        if path.stat().st_size != int(row["bytes"]):
            raise RuntimeError(f"byte-size mismatch for {rel}")
        actual_sha = sha256_file(path)
        if actual_sha != row["sha256"]:
            raise RuntimeError(f"sha256 mismatch for {rel}")
        verified_bytes += path.stat().st_size
        if rel.startswith("fills/") and rel.endswith(".parquet"):
            fill_paths.append(path)
            fill_manifest_rows += int(row.get("rows", 0))

    fill_paths = sorted(fill_paths)
    if not fill_paths:
        raise RuntimeError("no fill Parquet partitions found")
    if fill_manifest_rows != EXPECTED["deduped_fill_rows"]:
        raise RuntimeError(f"fill manifest row sum mismatch: {fill_manifest_rows}")

    representative = []
    for idx in sorted(set([0, len(fill_paths)//2, len(fill_paths)-1])):
        path = fill_paths[idx]
        pf = pq.ParquetFile(path)
        schema_names = pf.schema_arrow.names
        required = {"block_number", "log_index", "token_id"}
        missing = sorted(required - set(schema_names))
        if missing:
            raise RuntimeError(f"representative parquet missing {missing}: {path}")
        representative.append({
            "path": str(path),
            "rows": pf.metadata.num_rows,
            "columns": schema_names,
            "sha256": sha256_file(path),
        })

    chronology_parts = []
    for path in fill_paths:
        table = pq.read_table(path, columns=["token_id", "block_number", "log_index"])
        chronology_parts.append(table.to_pandas())
    chronology = pd.concat(chronology_parts, ignore_index=True)
    if len(chronology) != EXPECTED["deduped_fill_rows"]:
        raise RuntimeError(f"chronology row mismatch: {len(chronology)}")
    if chronology[["block_number", "log_index"]].isna().any().any():
        raise RuntimeError("missing block_number/log_index in DATA-004")
    duplicate_token_order = int(
        chronology.duplicated(["token_id", "block_number", "log_index"]).sum()
    )
    if duplicate_token_order:
        raise RuntimeError(
            f"strict token chronology has duplicate (token,block,log) keys: {duplicate_token_order}"
        )
    canonical = chronology.sort_values(
        ["token_id", "block_number", "log_index"], kind="mergesort"
    )
    bad_steps = 0
    for _, group in canonical.groupby("token_id", sort=False):
        b = group["block_number"].to_numpy()
        l = group["log_index"].to_numpy()
        if len(group) > 1:
            non_strict = (b[1:] < b[:-1]) | ((b[1:] == b[:-1]) & (l[1:] <= l[:-1]))
            bad_steps += int(non_strict.sum())
    if bad_steps:
        raise RuntimeError(f"strict (block_number,log_index) reconstruction failed: {bad_steps}")

    result = {
        "schema_version": 1,
        "stage": "R3-FV-001_DATA004_KAGGLE_SMOKE",
        "dataset_ref": DATASET_REF,
        "package_version": package.get("version"),
        "package_status": package.get("status"),
        "quality_all_gates_pass": quality.get("all_gates_pass"),
        "source_manifest_sha256": source_sha,
        "package_manifest_sha256": sha256_file(package_manifest_path),
        "manifest_file_count": len(listed),
        "manifest_files_hash_verified": len(listed),
        "verified_bytes": verified_bytes,
        "fill_parquet_partitions": len(fill_paths),
        "fill_rows": len(chronology),
        "fill_manifest_rows": fill_manifest_rows,
        "scope": {k: scope.get(k) for k in ("markets","conditions","tokens","p0_markets","p1_markets")},
        "block_number_present": True,
        "log_index_present": True,
        "missing_order_keys": 0,
        "duplicate_token_order_keys": duplicate_token_order,
        "strict_block_log_order_reconstructable": bad_steps == 0,
        "representative_parquet": representative,
        "dataset_hashes_manifest_agree": True,
        "pass": True,
    }
    out_path = OUT / "smoke_result.json"
    out_path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print("R3_DATA004_SMOKE_RESULT=" + json.dumps(result, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
