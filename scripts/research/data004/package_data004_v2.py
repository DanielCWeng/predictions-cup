#!/usr/bin/env python3
"""Refresh the DATA-004 v2 Kaggle tree from local v2 evidence and OCI manifest."""
from __future__ import annotations

import csv
import hashlib
import json
import os
import time
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[3]
INBOX = Path("/home/ubuntu/inbox/data004_20260929")
PACKAGE_NAME = "sig-cup-data-004-ets-p0p1-fills"
PACKAGE = INBOX / PACKAGE_NAME
V2 = ROOT / "data/research/data004_ets_p0p1/v2"
BUCKET = "polymarket-bot-state"
OCI_PREFIX = "research/data004_ets_p0p1/v2/"
MIN_FREE_BYTES = 6 * (1 << 30)
HARD_MIN_FREE_BYTES = 5 * (1 << 30)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def json_write(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")


def disk_gate(path: Path, stage: str, reserve_bytes: int = 0) -> int:
    log_path = Path("/home/ubuntu/campaigns/data004_20260929/data004_a3_disk_check.jsonl")
    started = time.monotonic()
    while True:
        result = subprocess.run(["df", "-B1", "--output=avail", str(path)], check=True, text=True, capture_output=True)
        available = int([line.strip() for line in result.stdout.splitlines() if line.strip()][-1])
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"stage": stage, "available_bytes": available, "reserve_bytes": reserve_bytes,
                                     "checked_at_utc": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat()}) + "\n")
        if available < HARD_MIN_FREE_BYTES:
            raise RuntimeError(f"disk below hard 5 GiB floor at {stage}: {available}")
        if available >= MIN_FREE_BYTES + reserve_bytes:
            return available
        if time.monotonic() - started > 2 * 60 * 60:
            raise RuntimeError(f"disk gate timed out at {stage}: {available}")
        print(f"PAUSED disk gate stage={stage} free_bytes={available}; rechecking in 5 minutes", flush=True)
        time.sleep(300)


def copy_review_files() -> None:
    names = [
        "block_timestamp_evidence.json",
        "data004_baseline_pairing.json",
        "data004_quality.json",
        "date_coverage.csv",
        "event_coverage.csv",
        "fill_composition.json",
        "full_universe_manifest.json",
        "maker_taker_conservation.json",
        "maker_taker_scope_audit.json",
        "maker_taker_tx_condition_residuals.csv",
        "market_coverage.csv",
        "condition_coverage.csv",
        "sample_fills.csv",
        "source_day_inventory.csv",
        "token_coverage.csv",
        "zero_fill_markets.csv",
    ]
    for name in names:
        source = V2 / name
        if not source.is_file():
            raise FileNotFoundError(source)
        disk_gate(PACKAGE, f"before_package_copy_{name}", reserve_bytes=source.stat().st_size * 2)
        target_name = "QUALITY.json" if name == "data004_quality.json" else name
        shutil.copy2(source, PACKAGE / target_name)

    # The Kaggle bundle points to the same canonical v2 source manifest that will
    # be published as the final OCI object.
    source_manifest = V2 / "data004_manifest.json"
    disk_gate(PACKAGE, "before_package_copy_oci_manifest", reserve_bytes=source_manifest.stat().st_size * 2)
    shutil.copy2(source_manifest, PACKAGE / "OCI_SOURCE_MANIFEST.json")


def write_docs(manifest: dict[str, Any], quality: dict[str, Any]) -> None:
    (PACKAGE / "README.md").write_text(
        """# DATA-004 — ETS P0/P1 Polymarket fills (v2)

**Status: BLOCKED_QUALITY_GATE.** This is a frozen data package for review; it contains no predictive result.

The private Kaggle dataset ID is `polyleviathan/sig-cup-data-004-ets-p0p1-fills`. Delivery is **Kaggle (pending team upload)**; the immutable OCI source copy is `polymarket-bot-state/research/data004_ets_p0p1/v2/`, with final manifest `research/data004_ets_p0p1/v2/MANIFEST.json`.

## Scope

- 298 markets: 210 P0 and 88 P1; 298 conditions and 596 Gamma-aligned outcome tokens.
- 231,964 rows from 2025-10-14T00:40:19Z through 2026-09-21T23:58:24Z.
- Full history is partitioned by UTC date under `fills/date=YYYY-MM-DD/`.
- The package retains metadata for the full 1,279-market / 2,558-token candidate universe. The 797 `book_needed_later` markets have no acquired fills.
- `data004_baseline_pairing.json` preserves the plan for 231 accepted DATA-003 anchors (140 EXACT, 87 DERIVED, 4 NEAR); no DATA-003 source fills were reacquired.

## Version and provenance

v2 reuses every v1 fill row and does not rescan the trade lake. The 4,989 rows on 2026-09-20/21 without a custody object receive a block number only when `public.block_timestamps` contains exactly one block for the fill's Unix-second timestamp. The method agreed with custody on all 9,803 fills from 2026-09-15 through 2026-09-19. Provenance remains explicit as `block_timestamps_unique_ts`; no block was imputed. The original v1 Parquet objects remain unchanged in OCI and are marked blocked/superseded by v2.

## Review outcome

All 231,964 rows now have a block number and pass strict per-token `(block_number, log_index)` ordering. Deduplication, universe/token alignment, source-date coverage, price bounds, core field completeness, lifecycle bounds and per-market coverage remain passing. Maker/taker conservation is the one remaining failed gate: 96,587 of 96,630 `(tx_hash, condition_id)` groups match exactly under exact-decimal summation; 43 have an unexplained difference of exactly 0.0001 shares. A targeted source lookup found no out-of-scope fills for those hashes. Each is listed in `maker_taker_tx_condition_residuals.csv`; see `QUALITY.json` and `COVERAGE.md` before use.

This package contains no order-book history, R3 predictive/fair-value test or outcome-driven graph change.
""",
        encoding="utf-8",
    )
    (PACKAGE / "COVERAGE.md").write_text(
        f"""# DATA-004 v2 coverage and quality

## Source and fill history

The original scan read all 348 available trade-object dates from 2025-10-09 through 2026-09-21. There were no missing trade-object dates; selected fills occur on 343 dates, from 2025-10-14 through 2026-09-21. The latest available trade-object day remains 2026-09-21. `source_day_inventory.csv` documents each trade and custody object; its block-missing counts describe the original custody join, before v2's unique-timestamp derivation.

There are 231,964 raw and retained rows, zero duplicate key groups, and zero removed rows. All 298 markets have fills; `zero_fill_markets.csv` is empty. Detailed per-market, condition, token, event and date tables are included.

## Block provenance and ordering

The custody join supplied 226,975 block numbers. Custody objects are absent for 2026-09-20 and 2026-09-21, affecting 4,989 fill rows (1,625 and 3,364). Before applying timestamp derivation, the method was tested on custody-covered dates 2026-09-15–19: 9,803 of 9,803 rows mapped to exactly one timestamp block and matched custody (100%). For all 4,989 gap rows, the timestamp map is unique; zero rows are ambiguous or unmapped. v2 provenance is `CUSTODY_TX_HASH_JOIN` for 226,975 rows and `block_timestamps_unique_ts` for 4,989; missing and imputed block counts are both zero. The per-token ordering gate has zero failures.

## Maker/taker conservation

The gate groups all taker-order rows (`order_is_match_taker_order=true`, equivalent to an exchange counterparty) and maker rows by `(tx_hash, condition_id)`. It sums the serialized Parquet share values using `Decimal(str(value))`, pairing the two binary outcome-token sides, without an epsilon tolerance. 96,587 groups match exactly; 43 of 96,630 have a residual of exactly ±0.0001 shares; no groups are one-sided. A targeted read-only lookup of the 43 transaction hashes in their source trade objects found zero out-of-scope fill rows, so none of these residuals is classified as an explained scope residual. All 43 are listed individually in `maker_taker_tx_condition_residuals.csv` with transaction, condition, date, row counts, maker/taker totals and source-scope checks.

Price-indexed buckets are retained only as descriptive diagnostics: the taker row can aggregate multiple maker price levels, so those buckets are not the transaction-condition conservation gate. No size tolerance or silent rounding was applied.

## Gate status and research boundary

`QUALITY.json` contains the full gate summary. The block-provenance and ordering defects from v1 are cleared. The 43 unexplained exact-decimal conservation residuals remain a failed gate, so DATA-004 remains `BLOCKED_QUALITY_GATE`; do not expand beyond P0/P1 or start R3 predictive/fair-value work until they are resolved.
""",
        encoding="utf-8",
    )


def write_package_manifest(source_manifest: dict[str, Any]) -> dict[str, Any]:
    old_path = PACKAGE / "MANIFEST.json"
    disk_gate(PACKAGE, "before_package_manifest_refresh", reserve_bytes=2 * (1 << 20))
    old = json.loads(old_path.read_text(encoding="utf-8")) if old_path.exists() else {}
    old_files = {row["path"]: row for row in old.get("files", [])}
    object_by_date = {row["date"]: row for row in source_manifest["files"] if row.get("kind") == "fills"}
    package_files: list[dict[str, Any]] = []
    for path in sorted(p for p in PACKAGE.rglob("*") if p.is_file() and p.name != "MANIFEST.json"):
        relative = path.relative_to(PACKAGE).as_posix()
        record = dict(old_files.get(relative, {}))
        record.update({"path": relative, "bytes": path.stat().st_size, "sha256": sha256_file(path)})
        if relative.startswith("fills/date=") and path.suffix == ".parquet":
            day = relative.split("date=", 1)[1].split("/", 1)[0]
            source = object_by_date[day]
            record.update({"rows": int(source["rows"]), "source_oci_object": source["path"]})
        elif path.suffix == ".parquet":
            record["rows"] = pq.ParquetFile(path).metadata.num_rows
        package_files.append(record)
    manifest = {
        "dataset_id": "DATA-004",
        "version": "v2",
        "status": source_manifest["status"],
        "scope": {"markets": 298, "conditions": 298, "tokens": 596, "p0_markets": 210, "p1_markets": 88},
        "source_oci": {
            "bucket": BUCKET,
            "object_prefix": OCI_PREFIX,
            "manifest_object": OCI_PREFIX + "MANIFEST.json",
            "manifest_sha256": sha256_file(V2 / "data004_manifest.json"),
        },
        "full_universe_manifest": "full_universe_manifest.json",
        "files": package_files,
    }
    json_write(old_path, manifest)
    return manifest


def main() -> None:
    if os.geteuid() == 0:
        raise RuntimeError("run as ubuntu")
    if not PACKAGE.is_dir():
        raise FileNotFoundError(PACKAGE)
    source_manifest = json.loads((V2 / "data004_manifest.json").read_text(encoding="utf-8"))
    quality = json.loads((V2 / "data004_quality.json").read_text(encoding="utf-8"))
    if source_manifest.get("version") != "v2" or source_manifest.get("status") != quality.get("status"):
        raise RuntimeError("v2 manifest and quality summary disagree")
    disk_gate(PACKAGE, "before_package_refresh", reserve_bytes=10 * (1 << 20))
    copy_review_files()
    write_docs(source_manifest, quality)
    manifest = write_package_manifest(source_manifest)
    print(json.dumps({"package": str(PACKAGE), "version": manifest["version"], "status": manifest["status"],
                      "file_count": len(manifest["files"]), "uncompressed_bytes": sum(row["bytes"] for row in manifest["files"]),
                      "source_manifest_sha256": manifest["source_oci"]["manifest_sha256"]}, sort_keys=True))


if __name__ == "__main__":
    main()
