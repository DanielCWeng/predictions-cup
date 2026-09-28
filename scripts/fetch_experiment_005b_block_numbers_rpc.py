"""Recover Polygon block_number for the exact frozen 005B economic transactions.

Source of truth: Polygon JSON-RPC eth_getTransactionByHash.  The original
PolyLeviathan OrderFilled decoder obtains the same immutable blockNumber before
the canonical trades schema drops it.

The script is resumable.  It materializes sorted distinct tx hashes from the
parent canonical economic-fill parquet, fetches in bounded JSON-RPC batches,
writes immutable chunk parquets, and hard-fails on any missing/conflicting tx.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import time
from pathlib import Path
from typing import Any

import aiohttp
import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

DEFAULT_RPC = "https://polygon.drpc.org"
RPC_BATCH = 10
DEFAULT_CONCURRENCY = 20
CHUNK_TX = 100_000
MAX_RETRIES = 12


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


async def fetch_rpc_batch(
    session: aiohttp.ClientSession,
    semaphore: asyncio.Semaphore,
    rpc_url: str,
    txs: list[str],
    base_id: int,
) -> list[tuple[str, int]]:
    payload = [
        {
            "jsonrpc": "2.0",
            "id": base_id + i,
            "method": "eth_getTransactionByHash",
            "params": [tx],
        }
        for i, tx in enumerate(txs)
    ]
    delay = 0.25
    for attempt in range(MAX_RETRIES):
        try:
            async with semaphore:
                async with session.post(
                    rpc_url,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as response:
                    status = response.status
                    data: Any = await response.json(content_type=None)
            if status == 200 and isinstance(data, list):
                by_id = {
                    int(item["id"]): item
                    for item in data
                    if isinstance(item, dict) and "id" in item
                }
                output: list[tuple[str, int]] = []
                for i, tx in enumerate(txs):
                    item = by_id.get(base_id + i, {})
                    result = item.get("result") or {}
                    value = result.get("blockNumber")
                    returned_hash = str(
                        result.get("hash") or result.get("transactionHash") or ""
                    ).lower()
                    if value is None:
                        raise RuntimeError(f"missing blockNumber for {tx}")
                    if returned_hash and returned_hash != tx.lower():
                        raise RuntimeError(
                            f"RPC hash mismatch {returned_hash} != {tx}"
                        )
                    output.append((tx, int(value, 16)))
                return output
            if status not in (429, 500, 502, 503, 504):
                raise RuntimeError(
                    f"RPC status={status} body={str(data)[:300]}"
                )
        except (
            aiohttp.ClientError,
            asyncio.TimeoutError,
            RuntimeError,
            ValueError,
        ):
            if attempt == MAX_RETRIES - 1:
                raise
        await asyncio.sleep(delay)
        delay = min(delay * 1.8, 8.0)
    raise RuntimeError("unreachable retry loop")


async def fetch_chunk(
    txs: list[str],
    rpc_url: str,
    concurrency: int,
) -> list[tuple[str, int]]:
    semaphore = asyncio.Semaphore(concurrency)
    connector = aiohttp.TCPConnector(limit=concurrency + 5)
    async with aiohttp.ClientSession(
        connector=connector,
        headers={"User-Agent": "predictions-cup-005b-ordering-falsification"},
    ) as session:
        tasks = [
            fetch_rpc_batch(
                session,
                semaphore,
                rpc_url,
                txs[start : start + RPC_BATCH],
                start,
            )
            for start in range(0, len(txs), RPC_BATCH)
        ]
        groups = await asyncio.gather(*tasks)
    return [item for group in groups for item in group]


def materialize_tx_universe(source: Path, target: Path) -> int:
    con = duckdb.connect()
    sq = str(source).replace("'", "''")
    tq = str(target).replace("'", "''")
    con.execute(
        f"""
        COPY (
            SELECT DISTINCT CAST(tx_hash AS VARCHAR) AS tx_hash
            FROM read_parquet('{sq}')
            WHERE tx_hash IS NOT NULL
            ORDER BY tx_hash
        )
        TO '{tq}' (FORMAT PARQUET, COMPRESSION ZSTD)
        """
    )
    count = int(
        con.execute(
            f"SELECT COUNT(*) FROM read_parquet('{tq}')"
        ).fetchone()[0]
    )
    con.close()
    return count


def chunk_path(root: Path, index: int) -> Path:
    return root / f"chunk_{index:06d}.parquet"


def write_chunk(path: Path, rows: list[tuple[str, int]]) -> None:
    table = pa.table(
        {
            "tx_hash": pa.array([row[0] for row in rows], pa.string()),
            "block_number": pa.array([row[1] for row in rows], pa.int64()),
        }
    )
    pq.write_table(table, path, compression="zstd")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--family", required=True)
    parser.add_argument("--canonical", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--rpc-url", default=DEFAULT_RPC)
    parser.add_argument("--concurrency", type=int, default=DEFAULT_CONCURRENCY)
    args = parser.parse_args()

    family_root = args.out_dir / args.family
    chunks_root = family_root / "chunks"
    family_root.mkdir(parents=True, exist_ok=True)
    chunks_root.mkdir(parents=True, exist_ok=True)

    tx_universe = family_root / "tx_universe.parquet"
    if not tx_universe.exists():
        tx_count = materialize_tx_universe(args.canonical, tx_universe)
    else:
        tx_count = pq.ParquetFile(tx_universe).metadata.num_rows

    pf = pq.ParquetFile(tx_universe)
    index = 0
    processed = 0
    started = time.time()
    buffer: list[str] = []

    def process_buffer(items: list[str], idx: int) -> None:
        nonlocal processed
        out = chunk_path(chunks_root, idx)
        if out.exists():
            existing = pq.read_table(out, columns=["tx_hash"])
            expected = items
            got = existing.column("tx_hash").to_pylist()
            if got != expected:
                raise RuntimeError(f"checkpoint content mismatch {out}")
            processed += len(items)
            return
        rows = asyncio.run(
            fetch_chunk(items, args.rpc_url, args.concurrency)
        )
        if len(rows) != len(items):
            raise RuntimeError(
                f"RPC row count {len(rows)} != requested {len(items)}"
            )
        if [row[0] for row in rows] != items:
            raise RuntimeError("RPC output order/hash mismatch")
        write_chunk(out, rows)
        processed += len(items)
        print(
            json.dumps(
                {
                    "family": args.family,
                    "chunk": idx,
                    "chunk_rows": len(items),
                    "processed": processed,
                    "total": tx_count,
                    "elapsed_seconds": round(time.time() - started, 1),
                },
                sort_keys=True,
            ),
            flush=True,
        )

    for batch in pf.iter_batches(
        batch_size=50_000,
        columns=["tx_hash"],
    ):
        buffer.extend(batch.column(0).to_pylist())
        while len(buffer) >= CHUNK_TX:
            current = buffer[:CHUNK_TX]
            del buffer[:CHUNK_TX]
            process_buffer(current, index)
            index += 1
    if buffer:
        process_buffer(buffer, index)

    combined = family_root / f"tx_block_{args.family}.parquet"
    glob = str(chunks_root / "chunk_*.parquet").replace("'", "''")
    cq = str(combined).replace("'", "''")
    con = duckdb.connect()
    con.execute(
        f"""
        COPY (
            SELECT tx_hash, block_number
            FROM read_parquet('{glob}')
            ORDER BY tx_hash
        )
        TO '{cq}' (FORMAT PARQUET, COMPRESSION ZSTD)
        """
    )
    rows, unique_tx, nulls, conflicts = con.execute(
        f"""
        SELECT
            COUNT(*),
            COUNT(DISTINCT tx_hash),
            COUNT(*) FILTER (WHERE block_number IS NULL),
            COUNT(*) - COUNT(DISTINCT tx_hash)
        FROM read_parquet('{cq}')
        """
    ).fetchone()
    con.close()
    if (
        int(rows) != tx_count
        or int(unique_tx) != tx_count
        or int(nulls)
        or int(conflicts)
    ):
        raise RuntimeError(
            "final block map failed coverage/uniqueness gate: "
            f"rows={rows} unique={unique_tx} expected={tx_count} "
            f"nulls={nulls} conflicts={conflicts}"
        )

    report = {
        "schema_version": 1,
        "experiment_id": "EXPERIMENT-005B-ORDERING-FALSIFICATION",
        "stage": "rpc_tx_block_enrichment",
        "classification": "POST_HOC_FALSIFICATION_ONLY",
        "family": args.family,
        "rpc_method": "eth_getTransactionByHash",
        "rpc_url": args.rpc_url,
        "parent_canonical_path": str(args.canonical),
        "parent_canonical_sha256": sha256(args.canonical),
        "tx_count": tx_count,
        "mapped_tx_count": int(rows),
        "missing_tx_count": 0,
        "output_path": combined.name,
        "output_bytes": combined.stat().st_size,
        "output_sha256": sha256(combined),
    }
    (family_root / f"tx_block_report_{args.family}.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
