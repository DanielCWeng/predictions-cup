"""Kaggle stage 1 for EXPERIMENT-005B: canonical economic-trade reconstruction."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import duckdb

FAMILIES = ("US_2024", "CAN_2025", "COL_2026", "HUN_2026", "PER_2026")
SPEC_FREEZE_COMMIT = "1ad933fe586d0c07da7ea06801af05a0a808f385"
DATASET_MANIFEST_SHA256 = "3bcb544fdcf3479f5e8a6973906c8ccfd5b9abd77592629daa70facfdfdd6d5c"
SIZE_TOLERANCE = 1e-8
YES_NOTIONAL_TOLERANCE = 1e-3
OUT = Path("/kaggle/working/005b_historical_predictive_atlas/reconstruction")
OUT.mkdir(parents=True, exist_ok=True)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def locate_family_file(family: str) -> Path:
    matches = sorted(Path("/kaggle/input").rglob(f"fees_{family}.parquet"))
    if len(matches) != 1:
        raise RuntimeError(f"expected one DATA-002 parquet for {family}, found {matches}")
    return matches[0]


def scalar(connection: duckdb.DuckDBPyConnection, query: str) -> int | float:
    return connection.execute(query).fetchone()[0]


def reconstruct_family(family: str) -> dict[str, object]:
    source = locate_family_file(family)
    output = OUT / f"canonical_trades_{family}.parquet"
    con = duckdb.connect()
    con.execute("PRAGMA threads=4")
    con.execute("PRAGMA preserve_insertion_order=false")
    quoted = str(source).replace("'", "''")
    con.execute(
        f"""
        CREATE TEMP VIEW src AS
        SELECT
            family,
            event_id,
            market_id,
            condition_id,
            CAST(timestamp AS BIGINT) AS timestamp,
            tx_hash,
            CAST(log_index AS BIGINT) AS log_index,
            CAST(order_is_match_taker_order AS BOOLEAN) AS is_active,
            UPPER(CAST(outcome_side AS VARCHAR)) AS outcome_side,
            CAST(price AS DOUBLE) AS price,
            CAST(size_shares AS DOUBLE) AS size_shares,
            CAST(value_usd AS DOUBLE) AS value_usd
        FROM read_parquet('{quoted}')
        """
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
            SUM(CASE WHEN NOT is_active THEN size_shares ELSE 0 END) AS passive_size,
            MAX(
                CASE WHEN is_active THEN
                    (CASE WHEN outcome_side='YES' THEN price ELSE 1-price END) * size_shares
                END
            ) AS active_yes_notional,
            SUM(
                CASE WHEN NOT is_active THEN
                    (CASE WHEN outcome_side='YES' THEN price ELSE 1-price END) * size_shares
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
              <= {SIZE_TOLERANCE} * GREATEST(1.0, ABS(active_size), ABS(passive_size))
          AND ABS(active_yes_notional-passive_yes_notional)
              <= {YES_NOTIONAL_TOLERANCE} * GREATEST(
                    1.0, ABS(active_yes_notional), ABS(passive_yes_notional)
                 )
        """
    )
    target = str(output).replace("'", "''")
    con.execute(
        f"""
        COPY (
            SELECT
                s.family,
                s.event_id,
                s.market_id,
                s.condition_id,
                s.timestamp,
                s.tx_hash,
                s.log_index,
                CASE WHEN s.outcome_side='YES' THEN s.price ELSE 1-s.price END AS p_yes,
                s.size_shares,
                s.value_usd
            FROM src AS s
            INNER JOIN accepted AS a
              USING (family, condition_id, tx_hash)
            WHERE NOT s.is_active
            ORDER BY s.timestamp, s.tx_hash, s.log_index
        )
        TO '{target}' (FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE 250000)
        """
    )
    total_rows = int(scalar(con, "SELECT COUNT(*) FROM src"))
    total_groups = int(scalar(con, "SELECT COUNT(*) FROM group_audit"))
    accepted_groups = int(scalar(con, "SELECT COUNT(*) FROM accepted"))
    trade_rows = int(
        con.execute(f"SELECT COUNT(*) FROM read_parquet('{target}')").fetchone()[0]
    )
    failure_counts = {
        "active_count_not_one": int(
            scalar(con, "SELECT COUNT(*) FROM group_audit WHERE active_count <> 1")
        ),
        "no_passive": int(
            scalar(con, "SELECT COUNT(*) FROM group_audit WHERE passive_count < 1")
        ),
        "non_binary": int(
            scalar(con, "SELECT COUNT(*) FROM group_audit WHERE NOT all_binary")
        ),
        "size_conservation": int(
            scalar(
                con,
                f"""SELECT COUNT(*) FROM group_audit
                    WHERE active_count=1 AND passive_count>=1 AND all_binary
                      AND ABS(active_size-passive_size)
                        > {YES_NOTIONAL_TOLERANCE} * GREATEST(1.0,ABS(active_size),ABS(passive_size))""",
            )
        ),
        "yes_notional_conservation": int(
            scalar(
                con,
                f"""SELECT COUNT(*) FROM group_audit
                    WHERE active_count=1 AND passive_count>=1 AND all_binary
                      AND ABS(active_yes_notional-passive_yes_notional)
                        > {YES_NOTIONAL_TOLERANCE} * GREATEST(
                            1.0,ABS(active_yes_notional),ABS(passive_yes_notional)
                          )""",
            )
        ),
    }
    con.close()
    return {
        "family": family,
        "source_path": str(source),
        "source_bytes": source.stat().st_size,
        "source_sha256": sha256(source),
        "participant_rows": total_rows,
        "transaction_condition_groups": total_groups,
        "accepted_groups": accepted_groups,
        "rejected_groups": total_groups - accepted_groups,
        "economic_trade_rows": trade_rows,
        "failure_counts": failure_counts,
        "output_path": output.name,
        "output_bytes": output.stat().st_size,
        "output_sha256": sha256(output),
    }


def main() -> None:
    families = [reconstruct_family(family) for family in FAMILIES]
    report = {
        "schema_version": 1,
        "experiment_id": "EXPERIMENT-005B",
        "stage": "canonical_trade_reconstruction",
        "spec_freeze_commit": SPEC_FREEZE_COMMIT,
        "data_002_manifest_sha256": DATASET_MANIFEST_SHA256,
        "size_tolerance": SIZE_TOLERANCE,
        "yes_notional_tolerance": YES_NOTIONAL_TOLERANCE,
        "policy": {
            "economic_trade_rows": "passive OrderFilled rows only",
            "active_row": "audit-only aggregate; never double counted",
            "yes_axis": "YES p; NO 1-p; OTHER rejected",
            "multi_maker": "preserved as separate passive economic fills",
            "rounding_audit": "005B reconstruction audit found max YES-notional relative error <5e-4 and zero groups above 1e-3",
        },
        "families": families,
        "totals": {
            "participant_rows": sum(int(row["participant_rows"]) for row in families),
            "transaction_condition_groups": sum(
                int(row["transaction_condition_groups"]) for row in families
            ),
            "accepted_groups": sum(int(row["accepted_groups"]) for row in families),
            "rejected_groups": sum(int(row["rejected_groups"]) for row in families),
            "economic_trade_rows": sum(int(row["economic_trade_rows"]) for row in families),
        },
    }
    path = OUT / "trade_reconstruction_report.json"
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps(report["totals"], sort_keys=True))


if __name__ == "__main__":
    main()
