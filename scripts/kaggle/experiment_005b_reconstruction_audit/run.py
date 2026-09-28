"""Bounded schema audit for 005B reconstruction conservation failures."""

# ruff: noqa: E501

from __future__ import annotations

import json
from pathlib import Path

import duckdb

FAMILIES = ("US_2024", "CAN_2025", "COL_2026", "HUN_2026", "PER_2026")
OUT = Path("/kaggle/working/005b_historical_predictive_atlas/reconstruction_audit")
OUT.mkdir(parents=True, exist_ok=True)


def locate(family: str) -> Path:
    matches = sorted(Path("/kaggle/input").rglob(f"fees_{family}.parquet"))
    if len(matches) != 1:
        raise RuntimeError(f"expected one file for {family}: {matches}")
    return matches[0]


def audit_family(family: str) -> dict[str, object]:
    con = duckdb.connect()
    con.execute("PRAGMA threads=4")
    path = str(locate(family)).replace("'", "''")
    con.execute(
        f"""
        CREATE TEMP TABLE g AS
        SELECT
          condition_id,
          tx_hash,
          SUM(CASE WHEN order_is_match_taker_order THEN 1 ELSE 0 END) active_count,
          SUM(CASE WHEN NOT order_is_match_taker_order THEN 1 ELSE 0 END) passive_count,
          MAX(CASE WHEN order_is_match_taker_order THEN UPPER(outcome_side) END) active_outcome,
          STRING_AGG(DISTINCT CASE WHEN NOT order_is_match_taker_order THEN UPPER(outcome_side) END, ',' ORDER BY CASE WHEN NOT order_is_match_taker_order THEN UPPER(outcome_side) END) passive_outcomes,
          MAX(CASE WHEN order_is_match_taker_order THEN CAST(size_shares AS DOUBLE) END) active_size,
          SUM(CASE WHEN NOT order_is_match_taker_order THEN CAST(size_shares AS DOUBLE) ELSE 0 END) passive_size,
          MAX(CASE WHEN order_is_match_taker_order THEN CAST(value_usd AS DOUBLE) END) active_value,
          SUM(CASE WHEN NOT order_is_match_taker_order THEN CAST(value_usd AS DOUBLE) ELSE 0 END) passive_value,
          MAX(CASE WHEN order_is_match_taker_order THEN
              (CASE WHEN UPPER(outcome_side)='YES' THEN CAST(price AS DOUBLE) ELSE 1-CAST(price AS DOUBLE) END)
              * CAST(size_shares AS DOUBLE) END) active_yes_notional,
          SUM(CASE WHEN NOT order_is_match_taker_order THEN
              (CASE WHEN UPPER(outcome_side)='YES' THEN CAST(price AS DOUBLE) ELSE 1-CAST(price AS DOUBLE) END)
              * CAST(size_shares AS DOUBLE) ELSE 0 END) passive_yes_notional
        FROM read_parquet('{path}')
        GROUP BY condition_id, tx_hash
        """
    )
    base = """
      active_count=1 AND passive_count>=1
      AND active_outcome IN ('YES','NO')
      AND passive_outcomes IS NOT NULL
      AND ABS(active_size-passive_size) <= 1e-8*GREATEST(1.0,ABS(active_size),ABS(passive_size))
    """
    con.execute(
        f"""
        CREATE TEMP VIEW x AS
        SELECT *,
          ABS(active_yes_notional-passive_yes_notional)
            / GREATEST(1.0,ABS(active_yes_notional),ABS(passive_yes_notional)) AS yes_rel_error,
          ABS(active_value-passive_value)
            / GREATEST(1.0,ABS(active_value),ABS(passive_value)) AS value_rel_error
        FROM g
        WHERE {base}
        """
    )
    quantiles = con.execute(
        """
        SELECT
          COUNT(*) n,
          QUANTILE_CONT(yes_rel_error, 0.5),
          QUANTILE_CONT(yes_rel_error, 0.9),
          QUANTILE_CONT(yes_rel_error, 0.95),
          QUANTILE_CONT(yes_rel_error, 0.99),
          QUANTILE_CONT(yes_rel_error, 0.999),
          MAX(yes_rel_error),
          QUANTILE_CONT(value_rel_error, 0.99),
          MAX(value_rel_error)
        FROM x
        """
    ).fetchone()
    tolerances = {}
    for tol in (1e-8,1e-7,1e-6,1e-5,1e-4,1e-3,1e-2):
        tolerances[str(tol)] = int(
            con.execute("SELECT COUNT(*) FROM x WHERE yes_rel_error > ?", [tol]).fetchone()[0]
        )
    patterns = [
        {
            "active_outcome": row[0],
            "passive_outcomes": row[1],
            "passive_count": int(row[2]),
            "group_count": int(row[3]),
            "median_yes_rel_error": float(row[4]),
        }
        for row in con.execute(
            """
            SELECT active_outcome, passive_outcomes, passive_count, COUNT(*) group_count,
                   MEDIAN(yes_rel_error)
            FROM x
            WHERE yes_rel_error > 1e-8
            GROUP BY 1,2,3
            ORDER BY group_count DESC
            LIMIT 25
            """
        ).fetchall()
    ]
    con.close()
    return {
        "family": family,
        "eligible_groups": int(quantiles[0]),
        "yes_rel_error_quantiles": {
            "p50": float(quantiles[1]),
            "p90": float(quantiles[2]),
            "p95": float(quantiles[3]),
            "p99": float(quantiles[4]),
            "p999": float(quantiles[5]),
            "max": float(quantiles[6]),
        },
        "value_rel_error_p99": float(quantiles[7]),
        "value_rel_error_max": float(quantiles[8]),
        "groups_above_yes_tolerance": tolerances,
        "top_failure_patterns": patterns,
    }


def main() -> None:
    report = {
        "experiment_id": "EXPERIMENT-005B",
        "purpose": "schema-only reconstruction audit; no predictor/target outcomes inspected",
        "families": [audit_family(family) for family in FAMILIES],
    }
    (OUT / "reconstruction_conservation_audit.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n"
    )
    print(json.dumps(report, sort_keys=True))


if __name__ == "__main__":
    main()
