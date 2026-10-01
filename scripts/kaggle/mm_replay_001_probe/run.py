# ruff: noqa
from __future__ import annotations

import csv
import hashlib
import json
import shutil
from collections import Counter
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq
import pandas as pd

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

    samples: list[dict[str, Any]] = []
    seen_source_schema: set[tuple[str, tuple[str, ...]]] = set()
    for path in files:
        if path.suffix.lower() != ".parquet":
            continue
        try:
            pf = pq.ParquetFile(path)
            names = tuple(pf.schema_arrow.names)
            if pf.metadata.num_row_groups == 0:
                continue
            table = pf.read_row_group(0)
            if table.num_rows == 0:
                continue
            frame = table.slice(0, min(20, table.num_rows)).to_pylist()
            source_values = {
                str(row.get("source_version") or "")
                for row in frame
                if isinstance(row, dict)
            }
            source_value = sorted(source_values)[0] if source_values else ""
            key = (source_value, names)
            if key in seen_source_schema:
                continue
            seen_source_schema.add(key)
            normalized = []
            for row in frame[:8]:
                rec: dict[str, Any] = {}
                for k, v in row.items():
                    if isinstance(v, (bytes, bytearray)):
                        rec[k] = "0x" + bytes(v).hex()
                    else:
                        rec[k] = v
                normalized.append(rec)
            samples.append({
                "path": str(path.relative_to(root)),
                "source_version": source_value,
                "columns": list(names),
                "rows": normalized,
            })
        except Exception as exc:
            samples.append({
                "path": str(path.relative_to(root)),
                "sample_error": repr(exc),
            })
        if len(samples) >= 8:
            break

    (OUT / "PROBE_SAMPLES.json").write_text(
        json.dumps(samples, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )

    sample_specs = [
        "ev06_nj11_special/date=2026-04-15/hour=00/events.parquet",
        "baseline_sep/date=2026-09-01/hour=00/events.parquet",
        "ev01_tx_ar_nc_primary/date=2026-02-28/hour=00/events.parquet",
        "ev17_ak_fl_wy/date=2026-08-18/hour=06/events.parquet",
    ]
    samples: list[dict[str, Any]] = []
    for suffix in sample_specs:
        matches = [p for p in files if str(p.relative_to(root)).endswith(suffix)]
        if len(matches) != 1:
            samples.append({"suffix": suffix, "error": f"matches={len(matches)}"})
            continue
        path = matches[0]
        pf = pq.ParquetFile(path)
        table = next(pf.iter_batches(batch_size=2000)).to_pandas()
        event_col = "event_type" if "event_type" in table.columns else (
            "update_type" if "update_type" in table.columns else None
        )
        counts = (
            table[event_col].astype(str).value_counts(dropna=False).head(30).to_dict()
            if event_col is not None
            else {}
        )
        rows_preview = []
        for rec in table.head(8).to_dict(orient="records"):
            clean = {}
            for key, value in rec.items():
                if hasattr(value, "item"):
                    try:
                        value = value.item()
                    except Exception:
                        pass
                text_value = repr(value)
                clean[str(key)] = text_value[:4000]
            rows_preview.append(clean)
        samples.append({
            "path": str(path.relative_to(root)),
            "columns": list(table.columns),
            "event_column": event_col,
            "event_counts_first_2000": counts,
            "rows": rows_preview,
        })
    (OUT / "SAMPLE_ROWS.json").write_text(
        json.dumps(samples, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )

    event_type_audit: list[dict[str, Any]] = []
    for suffix in sample_specs:
        matches = [p for p in files if str(p.relative_to(root)).endswith(suffix)]
        if len(matches) != 1:
            continue
        path = matches[0]
        pf = pq.ParquetFile(path)
        names = set(pf.schema_arrow.names)
        type_col = "event_type" if "event_type" in names else ("update_type" if "update_type" in names else None)
        columns = [c for c in [
            type_col, "timestamp", "timestamp_received", "timestamp_created_at",
            "market", "market_id", "asset_id", "data", "price", "size", "side",
            "best_bid", "best_ask", "transaction_hash", "source_version", "window_id"
        ] if c and c in names]
        counts: Counter[str] = Counter()
        examples: dict[str, list[dict[str, str]]] = {}
        for batch in pf.iter_batches(batch_size=250_000, columns=columns):
            df = batch.to_pandas()
            if type_col is None:
                continue
            vals = df[type_col].astype(str)
            counts.update(vals.tolist())
            for event_name in vals.unique().tolist():
                if len(examples.get(event_name, [])) >= 3:
                    continue
                subset = df[vals == event_name].head(3 - len(examples.get(event_name, [])))
                bucket = examples.setdefault(event_name, [])
                for rec in subset.to_dict(orient="records"):
                    clean: dict[str, str] = {}
                    for key, value in rec.items():
                        text_value = repr(value)
                        clean[str(key)] = text_value[:5000]
                    bucket.append(clean)
        event_type_audit.append({
            "path": str(path.relative_to(root)),
            "counts": dict(counts.most_common()),
            "examples": examples,
        })
    (OUT / "EVENT_TYPE_AUDIT.json").write_text(
        json.dumps(event_type_audit, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    side_semantics: list[dict[str, Any]] = []
    for suffix in (sample_specs[0], sample_specs[3]):
        matches = [p for p in files if str(p.relative_to(root)).endswith(suffix)]
        if len(matches) != 1:
            continue
        path = matches[0]
        pf = pq.ParquetFile(path)
        names = set(pf.schema_arrow.names)
        needed = [c for c in ["event_type","timestamp","asset_id","best_bid","best_ask","price","size","side"] if c in names]
        last_bbo: dict[str, tuple[float,float,int]] = {}
        counts = Counter()
        examples_out: list[dict[str, Any]] = []
        for batch in pf.iter_batches(batch_size=250_000, columns=needed):
            df = batch.to_pandas()
            if "event_type" not in df:
                continue
            df = df[df["event_type"].isin(["price_change","best_bid_ask","last_trade_price"])].copy()
            if df.empty:
                continue
            df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
            df.sort_values("timestamp", inplace=True, kind="stable")
            for rec in df.to_dict(orient="records"):
                asset = rec.get("asset_id")
                if isinstance(asset, (bytes, bytearray, memoryview)):
                    asset_key = str(int.from_bytes(bytes(asset), "big"))
                else:
                    asset_key = str(asset)
                typ = str(rec.get("event_type"))
                ts_ns = int(pd.Timestamp(rec["timestamp"]).value)
                if typ in {"price_change","best_bid_ask"}:
                    try:
                        bb=float(rec.get("best_bid")); ba=float(rec.get("best_ask"))
                    except (TypeError,ValueError):
                        continue
                    if 0 < bb <= ba < 1:
                        last_bbo[asset_key]=(bb,ba,ts_ns)
                    continue
                if typ != "last_trade_price" or asset_key not in last_bbo:
                    continue
                try:
                    px=float(rec.get("price"))
                except (TypeError,ValueError):
                    continue
                bb,ba,bbo_ts=last_bbo[asset_key]
                side=str(rec.get("side") or "").upper()
                if side=="SELL":
                    relation = "AT_OR_BELOW_BID" if px <= bb + 1e-12 else ("AT_OR_ABOVE_ASK" if px >= ba - 1e-12 else "INSIDE")
                elif side=="BUY":
                    relation = "AT_OR_ABOVE_ASK" if px >= ba - 1e-12 else ("AT_OR_BELOW_BID" if px <= bb + 1e-12 else "INSIDE")
                else:
                    relation="UNKNOWN_SIDE"
                counts[f"{side}:{relation}"] += 1
                if len(examples_out) < 20:
                    examples_out.append({"side":side,"price":px,"best_bid":bb,"best_ask":ba,"bbo_age_ms":(ts_ns-bbo_ts)/1e6,"relation":relation})
        side_semantics.append({"path":str(path.relative_to(root)),"counts":dict(counts),"examples":examples_out})
    (OUT / "TRADE_SIDE_SEMANTICS.json").write_text(
        json.dumps(side_semantics, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

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
