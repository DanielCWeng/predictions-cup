"""Correct 005B reconstruction with true block/log ordering and audit rank changes.

This is a post-hoc falsification lane.  It reproduces the original accepted economic
fill population exactly, adds block_number from a separately provenance-checked
upstream block map, and changes only observable ordering.

Original PR #45 artefacts are inputs for comparison only and are never overwritten.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import duckdb

FAMILIES = ("US_2024", "CAN_2025", "COL_2026", "HUN_2026", "PER_2026")
SIZE_TOLERANCE = 1e-8
YES_NOTIONAL_TOLERANCE = 1e-3
OUT = Path(
    "/kaggle/working/005b_ordering_falsification/corrected_reconstruction"
)
OUT.mkdir(parents=True, exist_ok=True)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def qpath(path: Path) -> str:
    return str(path).replace("'", "''")


def locate_unique(name: str) -> Path:
    matches = sorted(Path("/kaggle/input").rglob(name))
    if len(matches) != 1:
        raise RuntimeError(f"expected one {name}, found {matches}")
    return matches[0]


def scalar(con: duckdb.DuckDBPyConnection, sql: str) -> int:
    return int(con.execute(sql).fetchone()[0])


def collision_stats(
    con: duckdb.DuckDBPyConnection,
    source_sql: str,
    scope: str,
) -> dict[str, int]:
    if scope == "family":
        prefix = ""
    elif scope == "event":
        prefix = "event_id,"
    elif scope == "market":
        prefix = "condition_id,"
    else:
        raise ValueError(scope)

    def grouped(key: str) -> tuple[int, int, int]:
        row = con.execute(
            f"""
            WITH g AS (
                SELECT {prefix}{key},
                       COUNT(*) AS n,
                       COUNT(DISTINCT tx_hash) AS ntx
                FROM {source_sql}
                GROUP BY {prefix}{key}
            )
            SELECT
                COALESCE(SUM(n) FILTER (WHERE n>1),0),
                COALESCE(SUM(n) FILTER (WHERE ntx>1),0),
                COUNT(*) FILTER (WHERE ntx>1)
            FROM g
            """
        ).fetchone()
        return tuple(int(value) for value in row)

    ts_rows, ts_multi_tx_rows, ts_groups = grouped("timestamp")
    block_rows, block_multi_tx_rows, block_groups = grouped("block_number")
    return {
        f"{scope}_rows_in_shared_timestamp": ts_rows,
        f"{scope}_rows_in_multi_tx_timestamp": ts_multi_tx_rows,
        f"{scope}_multi_tx_timestamp_groups": ts_groups,
        f"{scope}_rows_in_shared_block": block_rows,
        f"{scope}_rows_in_multi_tx_block": block_multi_tx_rows,
        f"{scope}_multi_tx_block_groups": block_groups,
    }


def reconstruct_family(family: str) -> dict[str, Any]:
    fees = locate_unique(f"fees_{family}.parquet")
    block_map = locate_unique(f"upstream_order_{family}.parquet")
    old = locate_unique(f"canonical_trades_{family}.parquet")
    output = OUT / f"canonical_trades_{family}.parquet"

    con = duckdb.connect()
    con.execute("PRAGMA threads=4")
    fees_q = qpath(fees)
    map_q = qpath(block_map)
    old_q = qpath(old)
    output_q = qpath(output)

    con.execute(
        f"""
        CREATE TEMP VIEW src AS
        SELECT
            f.family,
            f.event_id,
            f.market_id,
            f.condition_id,
            f.token_id,
            CAST(f.timestamp AS BIGINT) AS timestamp,
            f.tx_hash,
            CAST(f.log_index AS BIGINT) AS log_index,
            CAST(m.block_number AS BIGINT) AS block_number,
            CAST(f.order_is_match_taker_order AS BOOLEAN) AS is_active,
            UPPER(CAST(f.outcome_side AS VARCHAR)) AS outcome_side,
            CAST(f.price AS DOUBLE) AS price,
            CAST(f.size_shares AS DOUBLE) AS size_shares,
            CAST(f.value_usd AS DOUBLE) AS value_usd
        FROM read_parquet('{fees_q}') AS f
        LEFT JOIN read_parquet('{map_q}') AS m
          ON CAST(f.tx_hash AS VARCHAR)=CAST(m.tx_hash AS VARCHAR)
         AND CAST(f.log_index AS BIGINT)=CAST(m.log_index AS BIGINT)
         AND CAST(f.token_id AS VARCHAR)=CAST(m.token_id AS VARCHAR)
        """
    )
    null_blocks = scalar(
        con,
        "SELECT COUNT(*) FROM src WHERE block_number IS NULL",
    )
    if null_blocks:
        raise RuntimeError(
            f"{family}: {null_blocks} DATA-002 rows lack upstream block_number"
        )

    con.execute(
        """
        CREATE TEMP TABLE group_audit AS
        SELECT
            family,
            condition_id,
            tx_hash,
            COUNT(*) AS row_count,
            SUM(CASE WHEN is_active THEN 1 ELSE 0 END) AS active_count,
            SUM(CASE WHEN NOT is_active THEN 1 ELSE 0 END) AS passive_count,
            BOOL_AND(outcome_side IN ('YES','NO')) AS all_binary,
            MAX(CASE WHEN is_active THEN size_shares END) AS active_size,
            SUM(
                CASE WHEN NOT is_active THEN size_shares ELSE 0 END
            ) AS passive_size,
            MAX(
                CASE WHEN is_active THEN
                    (
                        CASE
                            WHEN outcome_side='YES' THEN price
                            ELSE 1-price
                        END
                    ) * size_shares
                END
            ) AS active_yes_notional,
            SUM(
                CASE WHEN NOT is_active THEN
                    (
                        CASE
                            WHEN outcome_side='YES' THEN price
                            ELSE 1-price
                        END
                    ) * size_shares
                ELSE 0 END
            ) AS passive_yes_notional
        FROM src
        GROUP BY family, condition_id, tx_hash
        """
    )
    con.execute(
        f"""
        CREATE TEMP VIEW accepted AS
        SELECT family, condition_id, tx_hash
        FROM group_audit
        WHERE active_count = 1
          AND passive_count >= 1
          AND all_binary
          AND ABS(active_size-passive_size)
              <= {SIZE_TOLERANCE}
                 * GREATEST(1.0, ABS(active_size), ABS(passive_size))
          AND ABS(active_yes_notional-passive_yes_notional)
              <= {YES_NOTIONAL_TOLERANCE}
                 * GREATEST(
                     1.0,
                     ABS(active_yes_notional),
                     ABS(passive_yes_notional)
                 )
        """
    )
    con.execute(
        f"""
        COPY (
            SELECT
                s.family,
                s.event_id,
                s.market_id,
                s.condition_id,
                s.timestamp,
                s.block_number,
                s.tx_hash,
                s.log_index,
                CASE
                    WHEN s.outcome_side='YES' THEN s.price
                    ELSE 1-s.price
                END AS p_yes,
                s.size_shares,
                s.value_usd
            FROM src AS s
            INNER JOIN accepted AS a
              USING (family, condition_id, tx_hash)
            WHERE NOT s.is_active
            ORDER BY
                s.block_number,
                s.log_index,
                s.tx_hash,
                s.condition_id
        )
        TO '{output_q}'
        (FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE 250000)
        """
    )

    new_rows = scalar(
        con,
        f"SELECT COUNT(*) FROM read_parquet('{output_q}')",
    )
    old_rows = scalar(
        con,
        f"SELECT COUNT(*) FROM read_parquet('{old_q}')",
    )
    if new_rows != old_rows:
        raise RuntimeError(
            f"{family}: corrected rows {new_rows} != parent rows {old_rows}"
        )

    # Population/content identity: block_number is the only new data field.
    diff = con.execute(
        f"""
        WITH old AS (
            SELECT
                condition_id, tx_hash, log_index, timestamp,
                p_yes, size_shares, value_usd
            FROM read_parquet('{old_q}')
        ),
        new AS (
            SELECT
                condition_id, tx_hash, log_index, timestamp,
                p_yes, size_shares, value_usd
            FROM read_parquet('{output_q}')
        )
        SELECT
            COUNT(*) FILTER (
                WHERE old.tx_hash IS NULL OR new.tx_hash IS NULL
            ) AS key_mismatch,
            COUNT(*) FILTER (
                WHERE old.timestamp IS DISTINCT FROM new.timestamp
                   OR old.p_yes IS DISTINCT FROM new.p_yes
                   OR old.size_shares IS DISTINCT FROM new.size_shares
                   OR old.value_usd IS DISTINCT FROM new.value_usd
            ) AS content_mismatch
        FROM old
        FULL OUTER JOIN new
          USING (condition_id, tx_hash, log_index)
        """
    ).fetchone()
    if int(diff[0]) or int(diff[1]):
        raise RuntimeError(
            f"{family}: parent population changed "
            f"key_mismatch={diff[0]} content_mismatch={diff[1]}"
        )

    # Compare the exact parent pseudo-order to true block/log order.
    con.execute(
        f"""
        CREATE TEMP TABLE rank_audit AS
        SELECT
            condition_id,
            event_id,
            tx_hash,
            log_index,
            timestamp,
            block_number,
            ROW_NUMBER() OVER (
                PARTITION BY condition_id
                ORDER BY timestamp, tx_hash, log_index
            ) AS old_market_rank,
            ROW_NUMBER() OVER (
                PARTITION BY condition_id
                ORDER BY block_number, log_index, tx_hash
            ) AS new_market_rank,
            ROW_NUMBER() OVER (
                PARTITION BY event_id
                ORDER BY timestamp, tx_hash, log_index, condition_id
            ) AS old_event_rank,
            ROW_NUMBER() OVER (
                PARTITION BY event_id
                ORDER BY block_number, log_index, tx_hash, condition_id
            ) AS new_event_rank,
            ROW_NUMBER() OVER (
                ORDER BY timestamp, tx_hash, log_index, condition_id
            ) AS old_family_rank,
            ROW_NUMBER() OVER (
                ORDER BY block_number, log_index, tx_hash, condition_id
            ) AS new_family_rank
        FROM read_parquet('{output_q}')
        """
    )
    rank_row = con.execute(
        """
        SELECT
            COUNT(*) FILTER (
                WHERE old_market_rank <> new_market_rank
            ),
            COUNT(*) FILTER (
                WHERE old_event_rank <> new_event_rank
            ),
            COUNT(*) FILTER (
                WHERE old_family_rank <> new_family_rank
            )
        FROM rank_audit
        """
    ).fetchone()

    stats: dict[str, Any] = {
        "family": family,
        "economic_rows": new_rows,
        "market_order_rank_changed_rows": int(rank_row[0]),
        "event_order_rank_changed_rows": int(rank_row[1]),
        "family_order_rank_changed_rows": int(rank_row[2]),
    }
    source_sql = f"read_parquet('{output_q}')"
    for scope in ("family", "event", "market"):
        stats.update(collision_stats(con, source_sql, scope))

    block_timestamp_conflicts = scalar(
        con,
        f"""
        SELECT COUNT(*) FROM (
            SELECT block_number
            FROM read_parquet('{output_q}')
            GROUP BY block_number
            HAVING COUNT(DISTINCT timestamp) <> 1
        )
        """,
    )
    if block_timestamp_conflicts:
        raise RuntimeError(
            f"{family}: {block_timestamp_conflicts} blocks map to "
            "multiple timestamps"
        )

    log_position_conflicts = scalar(
        con,
        f"""
        SELECT COUNT(*) FROM (
            SELECT block_number, log_index
            FROM read_parquet('{output_q}')
            GROUP BY 1,2 HAVING COUNT(*) > 1
        )
        """,
    )
    if log_position_conflicts:
        raise RuntimeError(
            f"{family}: {log_position_conflicts} duplicate block/log positions"
        )

    stats.update(
        {
            "block_timestamp_conflicts": block_timestamp_conflicts,
            "block_log_position_conflicts": log_position_conflicts,
            "fees_sha256": sha256(fees),
            "block_map_sha256": sha256(block_map),
            "parent_canonical_sha256": sha256(old),
            "corrected_canonical_path": output.name,
            "corrected_canonical_bytes": output.stat().st_size,
            "corrected_canonical_sha256": sha256(output),
        }
    )
    con.close()
    return stats


def main() -> None:
    reports = [reconstruct_family(family) for family in FAMILIES]
    numeric_keys = [
        key
        for key, value in reports[0].items()
        if isinstance(value, int)
    ]
    result = {
        "schema_version": 1,
        "experiment_id": "EXPERIMENT-005B-ORDERING-FALSIFICATION",
        "stage": "corrected_reconstruction_and_pre_feature_order_audit",
        "classification": "POST_HOC_FALSIFICATION_ONLY",
        "parent_pr": 45,
        "parent_final_head": (
            "232c7c396edd18c93df72b309235110a89b5143a"
        ),
        "old_order": "timestamp, tx_hash, log_index",
        "correct_order": "block_number, log_index",
        "families": reports,
        "totals": {
            key: sum(int(row[key]) for row in reports)
            for key in numeric_keys
        },
    }
    path = OUT / "ordering_audit_pre_features.json"
    path.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result["totals"], sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
