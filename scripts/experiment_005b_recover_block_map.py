#!/usr/bin/env python3
"""Recover authoritative Polygon block numbers for the frozen 005B fill population.

The canonical trade source stores block timestamp, tx_hash and log_index but projected
block_number away. Polygon Bor block timestamps are strictly increasing, so one
authoritative transaction lookup per distinct stored block timestamp is sufficient to
recover the block_number for every 005B row. This script still validates the resulting
timestamp->block relation is one-to-one and strictly monotone, then verifies 100% row
coverage and block/log uniqueness across the frozen economic-fill population.

This is provenance/audit infrastructure only. It does not alter any feature, target,
model, threshold, split, shortlist or parent PR #45 artefact.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
import requests

FAMILIES = ("US_2024", "CAN_2025", "COL_2026", "HUN_2026", "PER_2026")
DEFAULT_RPC = "https://polygon.drpc.org"
DEFAULT_WORKERS = 16
RPC_BATCH = 10
CHECKPOINT_ROWS = 10_000


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def connect(path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(path, timeout=60)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    con.execute("PRAGMA temp_store=MEMORY")
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS block_map (
            timestamp INTEGER PRIMARY KEY,
            tx_hash TEXT NOT NULL,
            block_number INTEGER,
            block_hash TEXT
        ) WITHOUT ROWID
        """
    )
    return con


def seed_representatives(con: sqlite3.Connection, root: Path) -> None:
    existing = int(con.execute("SELECT COUNT(*) FROM block_map").fetchone()[0])
    if existing:
        print(f"representatives already seeded rows={existing}", flush=True)
        return

    for family in FAMILIES:
        path = root / f"canonical_trades_{family}.parquet"
        pf = pq.ParquetFile(path)
        prev_ts: int | None = None
        pending: list[tuple[int, str]] = []
        family_reps = 0
        for batch in pf.iter_batches(
            batch_size=131_072,
            columns=["timestamp", "tx_hash"],
        ):
            data = batch.to_pydict()
            for ts_raw, tx_raw in zip(
                data["timestamp"], data["tx_hash"], strict=True
            ):
                if ts_raw is None or tx_raw is None:
                    raise RuntimeError(f"{family}: null timestamp/tx_hash")
                ts = int(ts_raw)
                tx = str(tx_raw).lower()
                if prev_ts is not None and ts < prev_ts:
                    raise RuntimeError(
                        f"{family}: canonical input is not timestamp sorted"
                    )
                if ts != prev_ts:
                    pending.append((ts, tx))
                    family_reps += 1
                    prev_ts = ts
                    if len(pending) >= 20_000:
                        con.executemany(
                            """
                            INSERT OR IGNORE INTO block_map(timestamp, tx_hash)
                            VALUES (?, ?)
                            """,
                            pending,
                        )
                        con.commit()
                        pending.clear()
        if pending:
            con.executemany(
                """
                INSERT OR IGNORE INTO block_map(timestamp, tx_hash)
                VALUES (?, ?)
                """,
                pending,
            )
            con.commit()
        total = int(con.execute("SELECT COUNT(*) FROM block_map").fetchone()[0])
        print(
            f"seed {family} family_reps={family_reps} "
            f"cross_family_unique={total}",
            flush=True,
        )


def rpc_group(
    rpc: str,
    rows: list[tuple[int, str]],
    *,
    timeout: float = 20.0,
    attempts: int = 10,
) -> list[tuple[int, str, int, str]]:
    payload = [
        {
            "jsonrpc": "2.0",
            "id": i,
            "method": "eth_getTransactionByHash",
            "params": [tx],
        }
        for i, (_, tx) in enumerate(rows)
    ]
    last_error = "unknown"
    for attempt in range(attempts):
        try:
            response = requests.post(rpc, json=payload, timeout=timeout)
            data = response.json()
            if response.status_code != 200 or not isinstance(data, list):
                last_error = (
                    f"http={response.status_code} body={str(data)[:300]}"
                )
                raise RuntimeError(last_error)
            by_id = {
                int(item["id"]): item
                for item in data
                if isinstance(item, dict) and "id" in item
            }
            if len(by_id) != len(rows):
                raise RuntimeError(
                    f"incomplete batch {len(by_id)} != {len(rows)}"
                )

            out: list[tuple[int, str, int, str]] = []
            for i, (timestamp, tx_hash) in enumerate(rows):
                item = by_id[i]
                if item.get("error"):
                    raise RuntimeError(
                        f"rpc error for {tx_hash}: {item['error']}"
                    )
                result = item.get("result")
                if not isinstance(result, dict):
                    raise RuntimeError(f"null result for {tx_hash}")
                returned_hash = str(result.get("hash", "")).lower()
                if returned_hash != tx_hash:
                    raise RuntimeError(
                        f"hash mismatch {returned_hash} != {tx_hash}"
                    )
                block_hex = result.get("blockNumber")
                block_hash = result.get("blockHash")
                if not block_hex or not block_hash:
                    raise RuntimeError(
                        f"missing block provenance for {tx_hash}"
                    )
                out.append(
                    (
                        timestamp,
                        tx_hash,
                        int(str(block_hex), 16),
                        str(block_hash).lower(),
                    )
                )
            return out
        except Exception as exc:  # noqa: BLE001
            last_error = f"{type(exc).__name__}: {exc}"
            if attempt + 1 == attempts:
                break
            time.sleep(min(20.0, 0.4 * (2**attempt)))
    raise RuntimeError(f"RPC batch failed after retries: {last_error}")


def recover(
    con: sqlite3.Connection,
    rpc: str,
    workers: int,
) -> None:
    started = time.time()
    initial_done = int(
        con.execute(
            "SELECT COUNT(*) FROM block_map WHERE block_number IS NOT NULL"
        ).fetchone()[0]
    )
    total = int(con.execute("SELECT COUNT(*) FROM block_map").fetchone()[0])
    print(
        f"recovery start resolved={initial_done} total={total} "
        f"workers={workers} rpc_batch={RPC_BATCH}",
        flush=True,
    )

    while True:
        rows = con.execute(
            """
            SELECT timestamp, tx_hash
            FROM block_map
            WHERE block_number IS NULL
            ORDER BY timestamp
            LIMIT ?
            """,
            (CHECKPOINT_ROWS,),
        ).fetchall()
        if not rows:
            break

        groups = [
            [(int(ts), str(tx).lower()) for ts, tx in rows[i : i + RPC_BATCH]]
            for i in range(0, len(rows), RPC_BATCH)
        ]
        recovered: list[tuple[int, str, int, str]] = []
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {
                executor.submit(rpc_group, rpc, group): index
                for index, group in enumerate(groups)
            }
            ordered: dict[int, list[tuple[int, str, int, str]]] = {}
            for future in as_completed(futures):
                ordered[futures[future]] = future.result()
            for index in range(len(groups)):
                recovered.extend(ordered[index])

        con.executemany(
            """
            UPDATE block_map
            SET block_number = ?, block_hash = ?
            WHERE timestamp = ? AND tx_hash = ?
            """,
            [
                (block_number, block_hash, timestamp, tx_hash)
                for timestamp, tx_hash, block_number, block_hash in recovered
            ],
        )
        con.commit()
        done = int(
            con.execute(
                "SELECT COUNT(*) FROM block_map WHERE block_number IS NOT NULL"
            ).fetchone()[0]
        )
        elapsed = max(time.time() - started, 1e-9)
        session_done = done - initial_done
        print(
            f"checkpoint resolved={done}/{total} "
            f"session_rate={session_done/elapsed:.1f}_timestamps_s",
            flush=True,
        )


def validate_and_export(
    con: sqlite3.Connection,
    root: Path,
    out_dir: Path,
) -> dict[str, Any]:
    total = int(con.execute("SELECT COUNT(*) FROM block_map").fetchone()[0])
    unresolved = int(
        con.execute(
            "SELECT COUNT(*) FROM block_map WHERE block_number IS NULL"
        ).fetchone()[0]
    )
    distinct_blocks = int(
        con.execute(
            "SELECT COUNT(DISTINCT block_number) FROM block_map"
        ).fetchone()[0]
    )
    monotonic_violations = int(
        con.execute(
            """
            SELECT COUNT(*) FROM (
                SELECT timestamp, block_number,
                       LAG(block_number) OVER (ORDER BY timestamp) AS prev_block
                FROM block_map
            )
            WHERE prev_block IS NOT NULL
              AND block_number <= prev_block
            """
        ).fetchone()[0]
    )
    if unresolved:
        raise RuntimeError(f"unresolved block timestamps: {unresolved}")
    if distinct_blocks != total:
        raise RuntimeError(
            f"timestamp->block not one-to-one: {distinct_blocks} != {total}"
        )
    if monotonic_violations:
        raise RuntimeError(
            f"timestamp->block monotonic violations: {monotonic_violations}"
        )

    out_dir.mkdir(parents=True, exist_ok=True)
    map_path = out_dir / "timestamp_block_map.parquet"
    writer = pq.ParquetWriter(
        map_path,
        pa.schema(
            [
                ("timestamp", pa.int64()),
                ("representative_tx_hash", pa.string()),
                ("block_number", pa.int64()),
                ("block_hash", pa.string()),
            ]
        ),
        compression="zstd",
    )
    cursor = con.execute(
        """
        SELECT timestamp, tx_hash, block_number, block_hash
        FROM block_map
        ORDER BY timestamp
        """
    )
    while True:
        rows = cursor.fetchmany(100_000)
        if not rows:
            break
        writer.write_table(
            pa.Table.from_pylist(
                [
                    {
                        "timestamp": int(ts),
                        "representative_tx_hash": str(tx),
                        "block_number": int(block),
                        "block_hash": str(block_hash),
                    }
                    for ts, tx, block, block_hash in rows
                ]
            )
        )
    writer.close()

    # Materialize the compact map in memory for deterministic streaming row audit.
    timestamp_to_block = {
        int(ts): int(block)
        for ts, block in con.execute(
            "SELECT timestamp, block_number FROM block_map"
        )
    }

    per_family = []
    aggregate_rows = 0
    aggregate_mapped = 0
    aggregate_dup_block_log_rows = 0
    for family in FAMILIES:
        path = root / f"canonical_trades_{family}.parquet"
        pf = pq.ParquetFile(path)
        rows = 0
        mapped = 0
        block_log_seen: set[tuple[int, int]] = set()
        duplicate_block_log_rows = 0
        for batch in pf.iter_batches(
            batch_size=131_072,
            columns=["timestamp", "log_index"],
        ):
            data = batch.to_pydict()
            for ts_raw, log_raw in zip(
                data["timestamp"], data["log_index"], strict=True
            ):
                rows += 1
                if ts_raw is None or log_raw is None:
                    raise RuntimeError(
                        f"{family}: null timestamp/log_index"
                    )
                block_number = timestamp_to_block.get(int(ts_raw))
                if block_number is None:
                    continue
                mapped += 1
                key = (block_number, int(log_raw))
                if key in block_log_seen:
                    duplicate_block_log_rows += 1
                else:
                    block_log_seen.add(key)
        if mapped != rows:
            raise RuntimeError(
                f"{family}: block mapping coverage {mapped}/{rows}"
            )
        if duplicate_block_log_rows:
            raise RuntimeError(
                f"{family}: duplicate block/log rows "
                f"{duplicate_block_log_rows}"
            )
        per_family.append(
            {
                "family": family,
                "economic_rows": rows,
                "mapped_rows": mapped,
                "mapping_coverage": 1.0,
                "duplicate_block_log_rows": duplicate_block_log_rows,
            }
        )
        aggregate_rows += rows
        aggregate_mapped += mapped
        aggregate_dup_block_log_rows += duplicate_block_log_rows

    report = {
        "schema_version": 1,
        "experiment_id": "EXPERIMENT-005B-ORDERING-FALSIFICATION",
        "classification": "POST_HOC_FALSIFICATION_ONLY",
        "stage": "authoritative_block_number_recovery",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "rpc_method": "eth_getTransactionByHash",
        "rpc_endpoint": rpc_host(DEFAULT_RPC),
        "mapping_logic": (
            "one immutable fill tx_hash per distinct stored block timestamp; "
            "Polygon Bor valid-block timestamps are strictly increasing, then "
            "100% of frozen fills inherit the authoritative block_number for "
            "their exact stored block timestamp"
        ),
        "distinct_timestamps": total,
        "distinct_blocks": distinct_blocks,
        "unresolved_timestamps": unresolved,
        "timestamp_block_monotonic_violations": monotonic_violations,
        "timestamp_block_map": {
            "path": map_path.name,
            "rows": pq.ParquetFile(map_path).metadata.num_rows,
            "bytes": map_path.stat().st_size,
            "sha256": sha256(map_path),
        },
        "families": per_family,
        "totals": {
            "economic_rows": aggregate_rows,
            "mapped_rows": aggregate_mapped,
            "mapping_coverage": (
                aggregate_mapped / aggregate_rows if aggregate_rows else 0.0
            ),
            "duplicate_block_log_rows": aggregate_dup_block_log_rows,
        },
    }
    report_path = out_dir / "authoritative_block_map_report.json"
    report_path.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return report


def rpc_host(url: str) -> str:
    # Keep provenance useful without ever persisting credentials if a keyed RPC
    # is supplied later.
    from urllib.parse import urlsplit

    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}{parts.path}"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--state-db", type=Path, required=True)
    parser.add_argument("--rpc", default=DEFAULT_RPC)
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    args = parser.parse_args()

    args.state_db.parent.mkdir(parents=True, exist_ok=True)
    con = connect(args.state_db)
    seed_representatives(con, args.root)
    recover(con, args.rpc, args.workers)
    report = validate_and_export(con, args.root, args.out_dir)
    con.close()
    print(json.dumps(report["totals"], sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
