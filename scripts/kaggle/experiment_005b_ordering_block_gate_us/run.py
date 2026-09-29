"""Recover true Polygon block ordering for the exact frozen 005B economic fills.

Source of truth is archival Polygon JSON-RPC. For every distinct parent economic
transaction we fetch eth_getTransactionByHash -> blockNumber, then for every
mapped block fetch eth_getBlockByNumber -> timestamp. The script hard-fails
unless every parent row maps, every canonical timestamp equals the actual block
header timestamp, and (block_number, log_index) is unique within the retained
economic-fill population.

This is engineering provenance for a POST_HOC_FALSIFICATION_ONLY lane. It does
not change the 005B economic population, feature/target definitions, model
specification, thresholds, splits, shortlist, or parent PR #45 artefacts.
"""

# ruff: noqa: UP047

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeVar

import aiohttp
import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

DEFAULT_RPC = "https://tenderly.rpc.polygon.community/"
RPC_BATCH = 1_000
DEFAULT_CONCURRENCY = 4
CHUNK_TX = 200_000
CHUNK_BLOCKS = 200_000
MAX_RETRIES = 12

T = TypeVar("T")


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def qpath(path: Path) -> str:
    return str(path).replace("'", "''")


async def rpc_batch(
    session: aiohttp.ClientSession,
    semaphore: asyncio.Semaphore,
    rpc_url: str,
    method: str,
    params: list[list[Any]],
    base_id: int,
    parse: Callable[[dict[str, Any], list[Any]], T],
) -> list[T]:
    payload = [
        {
            "jsonrpc": "2.0",
            "id": base_id + i,
            "method": method,
            "params": item,
        }
        for i, item in enumerate(params)
    ]
    delay = 0.25
    last_error: Exception | None = None
    for attempt in range(MAX_RETRIES):
        try:
            async with semaphore, session.post(
                rpc_url,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=90),
            ) as response:
                status = response.status
                data: Any = await response.json(content_type=None)
            if status == 200 and isinstance(data, list):
                by_id = {
                    int(item["id"]): item
                    for item in data
                    if isinstance(item, dict) and "id" in item
                }
                output: list[T] = []
                for i, expected_params in enumerate(params):
                    item = by_id.get(base_id + i)
                    if not isinstance(item, dict):
                        raise RuntimeError(
                            f"{method}: missing JSON-RPC id {base_id+i}"
                        )
                    if item.get("error") is not None:
                        raise RuntimeError(
                            f"{method}: JSON-RPC error "
                            f"{str(item['error'])[:300]}"
                        )
                    result = item.get("result")
                    if not isinstance(result, dict):
                        raise RuntimeError(
                            f"{method}: missing result for {expected_params}"
                        )
                    output.append(parse(result, expected_params))
                return output
            if status not in (408, 429, 500, 502, 503, 504):
                raise RuntimeError(
                    f"{method}: RPC status={status} body={str(data)[:300]}"
                )
            last_error = RuntimeError(
                f"{method}: retryable RPC status={status}"
            )
        except (
            aiohttp.ClientError,
            TimeoutError,
            RuntimeError,
            ValueError,
        ) as exc:
            last_error = exc
            if attempt == MAX_RETRIES - 1:
                raise
        await asyncio.sleep(delay)
        delay = min(delay * 1.8, 12.0)
    raise RuntimeError(f"{method}: retries exhausted: {last_error}")


def parse_transaction(
    result: dict[str, Any],
    expected_params: list[Any],
) -> tuple[str, int]:
    tx = str(expected_params[0]).lower()
    returned_hash = str(
        result.get("hash") or result.get("transactionHash") or ""
    ).lower()
    if returned_hash != tx:
        raise RuntimeError(
            f"eth_getTransactionByHash hash mismatch {returned_hash} != {tx}"
        )
    value = result.get("blockNumber")
    if value is None:
        raise RuntimeError(f"missing blockNumber for {tx}")
    return tx, int(str(value), 16)


def parse_block(
    result: dict[str, Any],
    expected_params: list[Any],
) -> tuple[int, int]:
    requested = int(str(expected_params[0]), 16)
    number = result.get("number")
    timestamp = result.get("timestamp")
    if number is None or timestamp is None:
        raise RuntimeError(f"missing block header fields for {requested}")
    returned = int(str(number), 16)
    if returned != requested:
        raise RuntimeError(
            f"eth_getBlockByNumber mismatch {returned} != {requested}"
        )
    return requested, int(str(timestamp), 16)


async def fetch_many(
    requests: list[list[Any]],
    rpc_url: str,
    concurrency: int,
    method: str,
    parse: Callable[[dict[str, Any], list[Any]], T],
) -> list[T]:
    semaphore = asyncio.Semaphore(concurrency)
    connector = aiohttp.TCPConnector(limit=concurrency + 4)
    async with aiohttp.ClientSession(
        connector=connector,
        headers={
            "User-Agent": "predictions-cup-005b-ordering-falsification"
        },
    ) as session:
        tasks = [
            rpc_batch(
                session,
                semaphore,
                rpc_url,
                method,
                requests[start : start + RPC_BATCH],
                start,
                parse,
            )
            for start in range(0, len(requests), RPC_BATCH)
        ]
        groups = await asyncio.gather(*tasks)
    return [item for group in groups for item in group]


def materialize_tx_universe(source: Path, target: Path) -> int:
    con = duckdb.connect()
    sq = qpath(source)
    tq = qpath(target)
    con.execute(
        f"""
        COPY (
            SELECT DISTINCT LOWER(CAST(tx_hash AS VARCHAR)) AS tx_hash
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


def chunk_path(root: Path, prefix: str, index: int) -> Path:
    return root / f"{prefix}_{index:06d}.parquet"


def write_tx_chunk(
    path: Path,
    rows: list[tuple[str, int]],
) -> None:
    pq.write_table(
        pa.table(
            {
                "tx_hash": pa.array(
                    [row[0] for row in rows],
                    pa.string(),
                ),
                "block_number": pa.array(
                    [row[1] for row in rows],
                    pa.int64(),
                ),
            }
        ),
        path,
        compression="zstd",
    )


def write_block_chunk(
    path: Path,
    rows: list[tuple[int, int]],
) -> None:
    pq.write_table(
        pa.table(
            {
                "block_number": pa.array(
                    [row[0] for row in rows],
                    pa.int64(),
                ),
                "block_timestamp": pa.array(
                    [row[1] for row in rows],
                    pa.int64(),
                ),
            }
        ),
        path,
        compression="zstd",
    )


def process_tx_universe(
    tx_universe: Path,
    chunks_root: Path,
    rpc_url: str,
    concurrency: int,
) -> tuple[Path, int]:
    pf = pq.ParquetFile(tx_universe)
    tx_count = int(pf.metadata.num_rows)
    index = 0
    processed = 0
    started = time.time()
    buffer: list[str] = []

    def process(items: list[str], idx: int) -> None:
        nonlocal processed
        out = chunk_path(chunks_root, "tx", idx)
        if out.exists():
            existing = pq.read_table(out, columns=["tx_hash"])
            got = existing.column("tx_hash").to_pylist()
            if got != items:
                raise RuntimeError(f"checkpoint content mismatch {out}")
            processed += len(items)
            return
        requests = [[tx] for tx in items]
        rows = asyncio.run(
            fetch_many(
                requests,
                rpc_url,
                concurrency,
                "eth_getTransactionByHash",
                parse_transaction,
            )
        )
        if len(rows) != len(items):
            raise RuntimeError(
                f"tx RPC rows {len(rows)} != requested {len(items)}"
            )
        if [row[0] for row in rows] != items:
            raise RuntimeError("tx RPC output order/hash mismatch")
        write_tx_chunk(out, rows)
        processed += len(items)
        print(
            json.dumps(
                {
                    "stage": "tx_to_block",
                    "chunk": idx,
                    "chunk_rows": len(items),
                    "processed": processed,
                    "total": tx_count,
                    "elapsed_seconds": round(
                        time.time() - started,
                        1,
                    ),
                },
                sort_keys=True,
            ),
            flush=True,
        )

    for batch in pf.iter_batches(
        batch_size=100_000,
        columns=["tx_hash"],
    ):
        buffer.extend(batch.column(0).to_pylist())
        while len(buffer) >= CHUNK_TX:
            current = buffer[:CHUNK_TX]
            del buffer[:CHUNK_TX]
            process(current, index)
            index += 1
    if buffer:
        process(buffer, index)

    combined = chunks_root.parent / "tx_block.parquet"
    glob = qpath(chunks_root / "tx_*.parquet")
    cq = qpath(combined)
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
    rows, unique_tx, nulls = con.execute(
        f"""
        SELECT
            COUNT(*),
            COUNT(DISTINCT tx_hash),
            COUNT(*) FILTER (WHERE block_number IS NULL)
        FROM read_parquet('{cq}')
        """
    ).fetchone()
    con.close()
    if int(rows) != tx_count or int(unique_tx) != tx_count or int(nulls):
        raise RuntimeError(
            "tx block map coverage gate failed: "
            f"rows={rows} unique={unique_tx} expected={tx_count} "
            f"nulls={nulls}"
        )
    return combined, tx_count


def materialize_block_universe(
    tx_block: Path,
    target: Path,
) -> int:
    con = duckdb.connect()
    sq = qpath(tx_block)
    tq = qpath(target)
    con.execute(
        f"""
        COPY (
            SELECT DISTINCT block_number
            FROM read_parquet('{sq}')
            ORDER BY block_number
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


def process_block_universe(
    block_universe: Path,
    chunks_root: Path,
    rpc_url: str,
    concurrency: int,
) -> tuple[Path, int]:
    pf = pq.ParquetFile(block_universe)
    block_count = int(pf.metadata.num_rows)
    index = 0
    processed = 0
    started = time.time()
    buffer: list[int] = []

    def process(items: list[int], idx: int) -> None:
        nonlocal processed
        out = chunk_path(chunks_root, "block", idx)
        if out.exists():
            existing = pq.read_table(
                out,
                columns=["block_number"],
            )
            got = existing.column("block_number").to_pylist()
            if got != items:
                raise RuntimeError(f"checkpoint content mismatch {out}")
            processed += len(items)
            return
        requests = [[hex(number), False] for number in items]
        rows = asyncio.run(
            fetch_many(
                requests,
                rpc_url,
                concurrency,
                "eth_getBlockByNumber",
                parse_block,
            )
        )
        if len(rows) != len(items):
            raise RuntimeError(
                f"block RPC rows {len(rows)} != requested {len(items)}"
            )
        if [row[0] for row in rows] != items:
            raise RuntimeError("block RPC output order mismatch")
        write_block_chunk(out, rows)
        processed += len(items)
        print(
            json.dumps(
                {
                    "stage": "block_timestamp",
                    "chunk": idx,
                    "chunk_rows": len(items),
                    "processed": processed,
                    "total": block_count,
                    "elapsed_seconds": round(
                        time.time() - started,
                        1,
                    ),
                },
                sort_keys=True,
            ),
            flush=True,
        )

    for batch in pf.iter_batches(
        batch_size=100_000,
        columns=["block_number"],
    ):
        buffer.extend(int(value) for value in batch.column(0).to_pylist())
        while len(buffer) >= CHUNK_BLOCKS:
            current = buffer[:CHUNK_BLOCKS]
            del buffer[:CHUNK_BLOCKS]
            process(current, index)
            index += 1
    if buffer:
        process(buffer, index)

    combined = chunks_root.parent / "block_timestamp.parquet"
    glob = qpath(chunks_root / "block_*.parquet")
    cq = qpath(combined)
    con = duckdb.connect()
    con.execute(
        f"""
        COPY (
            SELECT block_number, block_timestamp
            FROM read_parquet('{glob}')
            ORDER BY block_number
        )
        TO '{cq}' (FORMAT PARQUET, COMPRESSION ZSTD)
        """
    )
    rows, unique_blocks, nulls = con.execute(
        f"""
        SELECT
            COUNT(*),
            COUNT(DISTINCT block_number),
            COUNT(*) FILTER (WHERE block_timestamp IS NULL)
        FROM read_parquet('{cq}')
        """
    ).fetchone()
    con.close()
    if (
        int(rows) != block_count
        or int(unique_blocks) != block_count
        or int(nulls)
    ):
        raise RuntimeError(
            "block timestamp map coverage gate failed: "
            f"rows={rows} unique={unique_blocks} "
            f"expected={block_count} nulls={nulls}"
        )
    return combined, block_count


def validate_parent(
    canonical: Path,
    tx_block: Path,
    block_timestamp: Path,
) -> dict[str, int]:
    con = duckdb.connect()
    cq = qpath(canonical)
    tq = qpath(tx_block)
    bq = qpath(block_timestamp)
    result = con.execute(
        f"""
        WITH joined AS (
            SELECT
                c.tx_hash,
                CAST(c.log_index AS BIGINT) AS log_index,
                CAST(c.timestamp AS BIGINT) AS canonical_timestamp,
                t.block_number,
                b.block_timestamp
            FROM read_parquet('{cq}') AS c
            LEFT JOIN read_parquet('{tq}') AS t
              ON LOWER(CAST(c.tx_hash AS VARCHAR))=t.tx_hash
            LEFT JOIN read_parquet('{bq}') AS b
              USING (block_number)
        )
        SELECT
            COUNT(*) AS rows,
            COUNT(*) FILTER (WHERE block_number IS NULL) AS missing_blocks,
            COUNT(*) FILTER (
                WHERE block_timestamp IS NULL
            ) AS missing_block_timestamps,
            COUNT(*) FILTER (
                WHERE canonical_timestamp IS DISTINCT FROM block_timestamp
            ) AS timestamp_mismatches
        FROM joined
        """
    ).fetchone()
    duplicates = int(
        con.execute(
            f"""
            WITH joined AS (
                SELECT
                    t.block_number,
                    CAST(c.log_index AS BIGINT) AS log_index
                FROM read_parquet('{cq}') AS c
                JOIN read_parquet('{tq}') AS t
                  ON LOWER(CAST(c.tx_hash AS VARCHAR))=t.tx_hash
            )
            SELECT COUNT(*) FROM (
                SELECT block_number, log_index
                FROM joined
                GROUP BY 1,2
                HAVING COUNT(*) > 1
            )
            """
        ).fetchone()[0]
    )
    block_timestamp_conflicts = int(
        con.execute(
            f"""
            SELECT COUNT(*) FROM (
                SELECT block_timestamp
                FROM read_parquet('{bq}')
                GROUP BY block_timestamp
                HAVING COUNT(DISTINCT block_number) > 1
            )
            """
        ).fetchone()[0]
    )
    con.close()
    audit = {
        "economic_rows": int(result[0]),
        "missing_block_numbers": int(result[1]),
        "missing_block_timestamps": int(result[2]),
        "timestamp_mismatches": int(result[3]),
        "duplicate_block_log_groups": duplicates,
        "distinct_blocks_sharing_timestamp": block_timestamp_conflicts,
    }
    if any(
        audit[key]
        for key in (
            "missing_block_numbers",
            "missing_block_timestamps",
            "timestamp_mismatches",
            "duplicate_block_log_groups",
            "distinct_blocks_sharing_timestamp",
        )
    ):
        raise RuntimeError(f"parent block-order hard gate failed: {audit}")
    return audit


def main() -> None:
    matches = sorted(
        Path("/kaggle/input").rglob("canonical_trades_US_2024.parquet")
    )
    if len(matches) != 1:
        raise RuntimeError(
            "expected one canonical_trades_US_2024.parquet, "
            f"found {matches}"
        )
    canonical = matches[0]
    family = "US_2024"
    out_dir = Path(
        "/kaggle/working/005b_ordering_falsification/block_gate"
    )
    concurrency = DEFAULT_CONCURRENCY

    family_root = out_dir / family
    chunks_root = family_root / "chunks"
    family_root.mkdir(parents=True, exist_ok=True)
    chunks_root.mkdir(parents=True, exist_ok=True)

    tx_universe = family_root / "tx_universe.parquet"
    if not tx_universe.exists():
        tx_count = materialize_tx_universe(canonical, tx_universe)
    else:
        tx_count = int(pq.ParquetFile(tx_universe).metadata.num_rows)

    tx_block, mapped_tx = process_tx_universe(
        tx_universe,
        chunks_root,
        DEFAULT_RPC,
        concurrency,
    )
    if mapped_tx != tx_count:
        raise RuntimeError(f"mapped tx {mapped_tx} != universe {tx_count}")

    block_universe = family_root / "block_universe.parquet"
    if not block_universe.exists():
        block_count = materialize_block_universe(
            tx_block,
            block_universe,
        )
    else:
        block_count = int(
            pq.ParquetFile(block_universe).metadata.num_rows
        )

    block_timestamp, mapped_blocks = process_block_universe(
        block_universe,
        chunks_root,
        DEFAULT_RPC,
        concurrency,
    )
    if mapped_blocks != block_count:
        raise RuntimeError(
            f"mapped blocks {mapped_blocks} != universe {block_count}"
        )

    audit = validate_parent(canonical, tx_block, block_timestamp)

    family_map = family_root / f"tx_block_{family}.parquet"
    if family_map != tx_block:
        family_map.write_bytes(tx_block.read_bytes())

    report = {
        "schema_version": 2,
        "experiment_id": "EXPERIMENT-005B-ORDERING-FALSIFICATION",
        "stage": "archival_chain_tx_block_enrichment",
        "classification": "POST_HOC_FALSIFICATION_ONLY",
        "family": family,
        "source": "Polygon archival JSON-RPC",
        "rpc_methods": [
            "eth_getTransactionByHash",
            "eth_getBlockByNumber",
        ],
        "rpc_url": DEFAULT_RPC,
        "parent_canonical_path": str(canonical),
        "parent_canonical_sha256": sha256(canonical),
        "tx_count": tx_count,
        "mapped_tx_count": mapped_tx,
        "block_count": block_count,
        "mapped_block_count": mapped_blocks,
        **audit,
        "tx_block_path": family_map.name,
        "tx_block_bytes": family_map.stat().st_size,
        "tx_block_sha256": sha256(family_map),
        "block_timestamp_path": block_timestamp.name,
        "block_timestamp_bytes": block_timestamp.stat().st_size,
        "block_timestamp_sha256": sha256(block_timestamp),
    }
    report_path = family_root / f"tx_block_report_{family}.json"
    report_path.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
