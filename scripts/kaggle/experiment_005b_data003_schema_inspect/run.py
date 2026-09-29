"""Schema-only inspection of DATA-003. No predictive targets or outcomes are evaluated."""
from __future__ import annotations

import json
import zipfile
from pathlib import Path

import duckdb
import pyarrow.parquet as pq

OUT = Path("/kaggle/working/data003_schema_inspect")
OUT.mkdir(parents=True, exist_ok=True)


def main() -> None:
    zips = sorted(Path("/kaggle/input").rglob("fills.zip"))
    if len(zips) != 1:
        raise RuntimeError(f"expected one fills.zip, found {zips}")
    extract = OUT / "fills"
    extract.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zips[0]) as archive:
        archive.extractall(extract)
    files = sorted(extract.rglob("*.parquet"))
    if not files:
        raise RuntimeError("no fill parquet files after extraction")

    schema = pq.read_schema(files[0])
    columns = schema.names
    outcome_candidates = [
        name for name in ("outcome_side", "outcome")
        if name in columns
    ]
    if len(outcome_candidates) != 1:
        raise RuntimeError(
            f"expected one outcome column, found {outcome_candidates}"
        )
    outcome = outcome_candidates[0]

    con = duckdb.connect()
    file_sql = ",".join(
        "'" + str(path).replace("'", "''") + "'"
        for path in files
    )
    source = f"read_parquet([{file_sql}], union_by_name=true)"
    total = int(con.execute(f"SELECT COUNT(*) FROM {source}").fetchone()[0])
    active = {}
    if "order_is_match_taker_order" in columns:
        active = {
            str(key): int(value)
            for key, value in con.execute(
                f"""
                SELECT CAST(order_is_match_taker_order AS VARCHAR), COUNT(*)
                FROM {source}
                GROUP BY 1 ORDER BY 1
                """
            ).fetchall()
        }
    outcomes = {
        str(key): int(value)
        for key, value in con.execute(
            f"SELECT UPPER(CAST({outcome} AS VARCHAR)), COUNT(*) "
            f"FROM {source} GROUP BY 1 ORDER BY 1"
        ).fetchall()
    }
    groups = None
    if "order_is_match_taker_order" in columns:
        groups = {
            key: int(value)
            for key, value in con.execute(
                f"""
                WITH g AS (
                  SELECT condition_id, tx_hash,
                         COUNT(*) AS n,
                         SUM(
                           CASE
                             WHEN CAST(order_is_match_taker_order AS BOOLEAN)
                             THEN 1 ELSE 0
                           END
                         ) AS active_n
                  FROM {source}
                  GROUP BY 1,2
                )
                SELECT
                  COUNT(*) AS groups,
                  COUNT(*) FILTER (WHERE active_n=1) AS one_active,
                  COUNT(*) FILTER (WHERE active_n=1 AND n-active_n>=1)
                    AS one_active_with_passive
                FROM g
                """
            ).fetchone()
            for key in ()
        }
    # Build group summary without a fragile dict-comprehension trick.
    if "order_is_match_taker_order" in columns:
        vals = con.execute(
            f"""
            WITH g AS (
              SELECT condition_id, tx_hash,
                     COUNT(*) AS n,
                     SUM(
                       CASE
                         WHEN CAST(order_is_match_taker_order AS BOOLEAN)
                         THEN 1 ELSE 0
                       END
                     ) AS active_n
              FROM {source}
              GROUP BY 1,2
            )
            SELECT COUNT(*),
                   COUNT(*) FILTER (WHERE active_n=1),
                   COUNT(*) FILTER (WHERE active_n=1 AND n-active_n>=1)
            FROM g
            """
        ).fetchone()
        groups = {
            "transaction_condition_groups": int(vals[0]),
            "groups_with_exactly_one_active": int(vals[1]),
            "groups_with_one_active_and_passive": int(vals[2]),
        }
    report = {
        "schema_version": 1,
        "stage": "DATA003_SCHEMA_ONLY",
        "predictive_outcomes_accessed": False,
        "fills_zip": zips[0].name,
        "parquet_files": len(files),
        "rows": total,
        "schema": [
            {"name": field.name, "type": str(field.type)}
            for field in schema
        ],
        "outcome_column": outcome,
        "outcome_counts": outcomes,
        "active_flag_counts": active,
        "group_structure": groups,
    }
    (OUT / "schema_report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({
        "rows": total,
        "outcome_column": outcome,
        "active_flag_counts": active,
        "group_structure": groups,
    }, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
