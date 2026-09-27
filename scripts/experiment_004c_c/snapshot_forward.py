from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pyarrow as pa
import pyarrow.dataset as ds
import pyarrow.parquet as pq

START = datetime.fromisoformat("2026-09-27T21:40:22.888233+00:00")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_stream(root: Path, stream: str, cutoff: datetime, time_field: str) -> pa.Table:
    files = sorted((root / stream).rglob("*.parquet"))
    if not files:
        return pa.table({})
    dataset = ds.dataset([str(path) for path in files], format="parquet")
    filt = (ds.field(time_field) >= START) & (ds.field(time_field) < cutoff)
    return dataset.to_table(filter=filt)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sig", type=Path, required=True)
    parser.add_argument("--pm-root", type=Path, required=True)
    parser.add_argument("--mapping", type=Path, required=True)
    parser.add_argument("--preregistration", type=Path, required=True)
    parser.add_argument("--implementation-freeze", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    cutoff = datetime.now(UTC)
    args.output_dir.mkdir(parents=True, exist_ok=True)

    con = sqlite3.connect(f"file:{args.sig.resolve()}?mode=ro", uri=True)
    rows = con.execute(
        """
        SELECT id, exchange_id, market_id, latest_price, best_bid, best_ask,
               spread, rest_observed_at, reason
        FROM price_observations
        WHERE rest_observed_at >= ? AND rest_observed_at < ?
        ORDER BY rest_observed_at, id
        """,
        (START.isoformat(), cutoff.isoformat()),
    ).fetchall()
    con.close()
    sig = pa.table(
        {
            "id": [row[0] for row in rows],
            "exchange_id": [row[1] for row in rows],
            "market_id": [row[2] for row in rows],
            "latest_price": [row[3] for row in rows],
            "best_bid": [row[4] for row in rows],
            "best_ask": [row[5] for row in rows],
            "spread": [row[6] for row in rows],
            "rest_observed_at": [row[7] for row in rows],
            "reason": [row[8] for row in rows],
        }
    )
    pq.write_table(sig, args.output_dir / "sig_prices.parquet", compression="zstd")

    streams = {
        "observations": "observed_at",
        "book_changes": "observed_at",
        "depth_snapshots": "recorded_at",
        "trades": "observed_at",
    }
    counts = {"sig_prices": sig.num_rows}
    for stream, time_field in streams.items():
        table = _read_stream(args.pm_root, stream, cutoff, time_field)
        pq.write_table(table, args.output_dir / f"pm_{stream}.parquet", compression="zstd")
        counts[f"pm_{stream}"] = table.num_rows

    mapping_target = args.output_dir / "sig_polymarket_2026.json"
    mapping_target.write_bytes(args.mapping.read_bytes())
    prereg_target = args.output_dir / "preregistration.json"
    prereg_target.write_bytes(args.preregistration.read_bytes())
    implementation_target = args.output_dir / "implementation_freeze_001.json"
    implementation_target.write_bytes(args.implementation_freeze.read_bytes())

    files = sorted(args.output_dir.glob("*.parquet"))
    manifest = {
        "schema_version": 1,
        "snapshot_start_utc": START.isoformat(),
        "snapshot_cutoff_utc": cutoff.isoformat(),
        "row_counts": counts,
        "mapping_sha256": sha256(mapping_target),
        "preregistration_sha256": sha256(prereg_target),
        "implementation_freeze_sha256": sha256(implementation_target),
        "files": {
            path.name: {"bytes": path.stat().st_size, "sha256": sha256(path)}
            for path in files
        },
    }
    (args.output_dir / "snapshot_manifest.json").write_text(
        json.dumps(manifest, sort_keys=True, indent=2) + "\n"
    )
    print(json.dumps(manifest, sort_keys=True))


if __name__ == "__main__":
    main()