"""Build the immutable 005B fill-key -> block_number map from an upstream source.

The input MUST be an upstream/pre-canonical OrderFilled export that still carries
block_number.  The canonical PolyLeviathan trades lake is not sufficient because
its TRADE_SCHEMA projects block_number away.

This script does not alter the 005B fill population.  It inner-joins the exact
DATA-002 fill keys and hard-fails unless every key maps uniquely to one
(block_number, timestamp).
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import duckdb

FAMILIES = ("US_2024", "CAN_2025", "COL_2026", "HUN_2026", "PER_2026")
REQUIRED_SOURCE_COLUMNS = {
    "tx_hash",
    "log_index",
    "token_id",
    "timestamp",
    "block_number",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def q(value: str | Path) -> str:
    return str(value).replace("'", "''")


def parquet_columns(con: duckdb.DuckDBPyConnection, glob: str) -> set[str]:
    rows = con.execute(
        f"DESCRIBE SELECT * FROM read_parquet('{q(glob)}')"
    ).fetchall()
    return {str(row[0]) for row in rows}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--source-glob",
        required=True,
        help=(
            "Glob for upstream/pre-canonical OrderFilled parquet files carrying "
            "block_number, log_index, tx_hash, token_id and timestamp."
        ),
    )
    parser.add_argument("--fees-dir", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument(
        "--source-manifest-sha256",
        default=None,
        help="Optional immutable upstream-source manifest hash.",
    )
    args = parser.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)

    con = duckdb.connect()
    con.execute("PRAGMA threads=4")
    source_columns = parquet_columns(con, args.source_glob)
    missing = sorted(REQUIRED_SOURCE_COLUMNS - source_columns)
    if missing:
        raise RuntimeError(
            "upstream source is not block-aware; missing columns "
            + ",".join(missing)
        )

    reports: list[dict[str, Any]] = []
    for family in FAMILIES:
        fees = args.fees_dir / f"fees_{family}.parquet"
        if not fees.exists():
            raise FileNotFoundError(fees)
        out = args.out_dir / f"upstream_order_{family}.parquet"
        fees_q = q(fees)
        src_q = q(args.source_glob)
        out_q = q(out)

        con.execute(
            f"""
            CREATE OR REPLACE TEMP VIEW needed AS
            SELECT DISTINCT
                CAST(tx_hash AS VARCHAR) AS tx_hash,
                CAST(log_index AS BIGINT) AS log_index,
                CAST(token_id AS VARCHAR) AS token_id,
                CAST(timestamp AS BIGINT) AS expected_timestamp
            FROM read_parquet('{fees_q}')
            """
        )
        needed_rows = int(
            con.execute("SELECT COUNT(*) FROM needed").fetchone()[0]
        )
        conflicts = int(
            con.execute(
                f"""
                SELECT COUNT(*) FROM (
                    SELECT
                        s.tx_hash,
                        CAST(s.log_index AS BIGINT) AS log_index,
                        CAST(s.token_id AS VARCHAR) AS token_id
                    FROM read_parquet('{src_q}') AS s
                    INNER JOIN needed AS n
                      ON CAST(s.tx_hash AS VARCHAR)=n.tx_hash
                     AND CAST(s.log_index AS BIGINT)=n.log_index
                     AND CAST(s.token_id AS VARCHAR)=n.token_id
                    GROUP BY 1,2,3
                    HAVING COUNT(DISTINCT CAST(s.block_number AS BIGINT)) <> 1
                       OR COUNT(DISTINCT CAST(s.timestamp AS BIGINT)) <> 1
                )
                """
            ).fetchone()[0]
        )
        if conflicts:
            raise RuntimeError(
                f"{family}: {conflicts} upstream keys have conflicting "
                "block_number/timestamp"
            )

        con.execute(
            f"""
            COPY (
                SELECT
                    n.tx_hash,
                    n.log_index,
                    n.token_id,
                    MIN(CAST(s.block_number AS BIGINT)) AS block_number,
                    MIN(CAST(s.timestamp AS BIGINT)) AS timestamp
                FROM needed AS n
                LEFT JOIN read_parquet('{src_q}') AS s
                  ON CAST(s.tx_hash AS VARCHAR)=n.tx_hash
                 AND CAST(s.log_index AS BIGINT)=n.log_index
                 AND CAST(s.token_id AS VARCHAR)=n.token_id
                GROUP BY 1,2,3
                ORDER BY block_number, log_index, tx_hash, token_id
            )
            TO '{out_q}' (FORMAT PARQUET, COMPRESSION ZSTD)
            """
        )
        mapped, null_blocks, ts_mismatch = con.execute(
            f"""
            SELECT
                COUNT(*),
                COUNT(*) FILTER (WHERE o.block_number IS NULL),
                COUNT(*) FILTER (
                    WHERE o.timestamp IS DISTINCT FROM n.expected_timestamp
                )
            FROM read_parquet('{out_q}') AS o
            JOIN needed AS n USING (tx_hash, log_index, token_id)
            """
        ).fetchone()
        if int(mapped) != needed_rows:
            raise RuntimeError(
                f"{family}: mapped rows {mapped} != required {needed_rows}"
            )
        if int(null_blocks):
            raise RuntimeError(
                f"{family}: {null_blocks} fill keys lack block_number"
            )
        if int(ts_mismatch):
            raise RuntimeError(
                f"{family}: {ts_mismatch} upstream timestamps disagree with DATA-002"
            )

        duplicate_block_log = int(
            con.execute(
                f"""
                SELECT COUNT(*) FROM (
                    SELECT block_number, log_index, COUNT(*) n
                    FROM read_parquet('{out_q}')
                    GROUP BY 1,2 HAVING COUNT(*) > 1
                )
                """
            ).fetchone()[0]
        )
        if duplicate_block_log:
            raise RuntimeError(
                f"{family}: block_number/log_index is not unique for "
                f"{duplicate_block_log} source log positions"
            )

        reports.append(
            {
                "family": family,
                "required_fill_keys": needed_rows,
                "mapped_fill_keys": int(mapped),
                "null_block_numbers": int(null_blocks),
                "timestamp_mismatches": int(ts_mismatch),
                "block_log_duplicate_groups": duplicate_block_log,
                "fees_sha256": sha256(fees),
                "output_path": out.name,
                "output_bytes": out.stat().st_size,
                "output_sha256": sha256(out),
            }
        )

    report = {
        "schema_version": 1,
        "experiment_id": "EXPERIMENT-005B-ORDERING-FALSIFICATION",
        "stage": "upstream_block_map",
        "source_glob": args.source_glob,
        "source_manifest_sha256": args.source_manifest_sha256,
        "source_requirement": (
            "pre-canonical/upstream OrderFilled rows carrying block_number; "
            "no timestamp- or tx-hash-derived block inference is allowed"
        ),
        "families": reports,
        "totals": {
            "required_fill_keys": sum(
                int(row["required_fill_keys"]) for row in reports
            ),
            "mapped_fill_keys": sum(
                int(row["mapped_fill_keys"]) for row in reports
            ),
        },
    }
    report_path = args.out_dir / "upstream_block_map_report.json"
    report_path.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
