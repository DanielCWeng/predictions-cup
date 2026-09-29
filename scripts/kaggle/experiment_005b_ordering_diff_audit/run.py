"""Compare original 005B matrices with ordering-corrected matrices.

Post-hoc diagnostic only. Joins by immutable economic-fill identity and reports
exact changed feature, target and label-endpoint observations. It never selects
or scores candidates.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import duckdb

FAMILIES = ("US_2024", "CAN_2025", "COL_2026", "HUN_2026", "PER_2026")
ORIGINAL = {
    "US_2024": "005b-us-merge",
    "CAN_2025": "005b-historical-predictive-atlas-features-can-2025",
    "COL_2026": "005b-historical-predictive-atlas-features-col-2026",
    "HUN_2026": "005b-historical-predictive-atlas-features-hun-2026",
    "PER_2026": "005b-historical-predictive-atlas-features-per-2026",
}
CORRECTED = {
    "US_2024": "005b-ordering-falsification-us-merge",
    "CAN_2025": "005b-ordering-falsification-features-can-2025",
    "COL_2026": "005b-ordering-falsification-features-col-2026",
    "HUN_2026": "005b-ordering-falsification-features-hun-2026",
    "PER_2026": "005b-ordering-falsification-features-per-2026",
}
OUT = Path("/kaggle/working/005b_ordering_falsification/matrix_diff")
OUT.mkdir(parents=True, exist_ok=True)
KEYS = ("condition_id", "tx_hash", "log_index")
EXCLUDED = {
    "family",
    "event_id",
    "market_id",
    "condition_id",
    "timestamp",
    "tx_hash",
    "log_index",
    "market_order_us",
    "raw_split",
    "train_end_timestamp",
    "dev_end_timestamp",
}


def locate(slug: str, family: str) -> Path:
    name = f"feature_target_{family}.parquet"
    matches = [
        path
        for path in Path("/kaggle/input").rglob(name)
        if slug in str(path)
    ]
    if len(matches) != 1:
        raise RuntimeError(
            f"expected one {name} under {slug}, found {matches}"
        )
    return matches[0]


def quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def qpath(path: Path) -> str:
    return str(path).replace("'", "''")


def schema_columns(
    con: duckdb.DuckDBPyConnection,
    path: Path,
) -> list[str]:
    rows = con.execute(
        f"DESCRIBE SELECT * FROM read_parquet('{qpath(path)}')"
    ).fetchall()
    return [str(row[0]) for row in rows]


def changed_expression(column: str) -> str:
    col = quote(column)
    return f'o.{col} IS DISTINCT FROM n.{col}'


def aggregate_counts(
    con: duckdb.DuckDBPyConnection,
    columns: list[str],
) -> dict[str, int]:
    if not columns:
        return {}
    expressions = ",\n".join(
        f"COUNT(*) FILTER (WHERE {changed_expression(column)}) "
        f"AS {quote(column)}"
        for column in columns
    )
    row = con.execute(
        f"""
        SELECT {expressions}
        FROM original AS o
        INNER JOIN corrected AS n USING (condition_id, tx_hash, log_index)
        """
    ).fetchone()
    return {
        column: int(value)
        for column, value in zip(columns, row, strict=True)
    }


def rows_changed(
    con: duckdb.DuckDBPyConnection,
    columns: list[str],
) -> int:
    if not columns:
        return 0
    condition = " OR ".join(changed_expression(column) for column in columns)
    return int(
        con.execute(
            f"""
            SELECT COUNT(*)
            FROM original AS o
            INNER JOIN corrected AS n USING (condition_id, tx_hash, log_index)
            WHERE {condition}
            """
        ).fetchone()[0]
    )


def audit_family(family: str) -> dict[str, Any]:
    original = locate(ORIGINAL[family], family)
    corrected = locate(CORRECTED[family], family)
    con = duckdb.connect()
    con.execute("PRAGMA threads=4")
    con.execute(
        f"CREATE VIEW original AS SELECT * FROM read_parquet('{qpath(original)}')"
    )
    con.execute(
        f"CREATE VIEW corrected AS SELECT * FROM read_parquet('{qpath(corrected)}')"
    )

    original_columns = schema_columns(con, original)
    corrected_columns = schema_columns(con, corrected)
    if set(original_columns) != set(corrected_columns):
        raise RuntimeError(
            f"{family}: schema-name drift "
            f"only_original={sorted(set(original_columns)-set(corrected_columns))} "
            f"only_corrected={sorted(set(corrected_columns)-set(original_columns))}"
        )

    target_columns = [
        column for column in original_columns if column.startswith("target_")
    ]
    label_columns = [
        column
        for column in original_columns
        if column.startswith("clock_label_end_")
        or column.startswith("event_label_end_")
    ]
    feature_columns = [
        column
        for column in original_columns
        if column not in EXCLUDED
        and column not in target_columns
        and column not in label_columns
    ]
    if len(feature_columns) != 226 or len(target_columns) != 47:
        raise RuntimeError(
            f"{family}: unexpected frozen schema "
            f"features={len(feature_columns)} targets={len(target_columns)}"
        )

    original_rows = int(con.execute("SELECT COUNT(*) FROM original").fetchone()[0])
    corrected_rows = int(con.execute("SELECT COUNT(*) FROM corrected").fetchone()[0])
    if original_rows != corrected_rows:
        raise RuntimeError(
            f"{family}: row-count drift {original_rows} != {corrected_rows}"
        )
    joined_rows = int(
        con.execute(
            """
            SELECT COUNT(*)
            FROM original AS o
            INNER JOIN corrected AS n USING (condition_id, tx_hash, log_index)
            """
        ).fetchone()[0]
    )
    if joined_rows != original_rows:
        raise RuntimeError(
            f"{family}: immutable-key coverage {joined_rows} != {original_rows}"
        )
    for view in ("original", "corrected"):
        dupes = int(
            con.execute(
                f"""
                SELECT COUNT(*) FROM (
                    SELECT condition_id, tx_hash, log_index
                    FROM {view}
                    GROUP BY 1,2,3 HAVING COUNT(*) > 1
                )
                """
            ).fetchone()[0]
        )
        if dupes:
            raise RuntimeError(f"{family}: duplicate immutable keys in {view}")

    feature_counts = aggregate_counts(con, feature_columns)
    target_counts = aggregate_counts(con, target_columns)
    label_counts = aggregate_counts(con, label_columns)

    report = {
        "family": family,
        "rows": original_rows,
        "feature_count": len(feature_columns),
        "target_count": len(target_columns),
        "label_endpoint_count": len(label_columns),
        "feature_rows_changed": rows_changed(con, feature_columns),
        "target_rows_changed": rows_changed(con, target_columns),
        "label_endpoint_rows_changed": rows_changed(con, label_columns),
        "feature_observations_changed": sum(feature_counts.values()),
        "target_observations_changed": sum(target_counts.values()),
        "label_endpoint_observations_changed": sum(label_counts.values()),
        "feature_changed_counts": feature_counts,
        "target_changed_counts": target_counts,
        "label_endpoint_changed_counts": label_counts,
    }
    con.close()
    return report


def main() -> None:
    families = []
    for family in FAMILIES:
        print(f"DIFF {family}", flush=True)
        row = audit_family(family)
        families.append(row)
        print(json.dumps({
            key: row[key]
            for key in (
                "family",
                "rows",
                "feature_rows_changed",
                "target_rows_changed",
                "label_endpoint_rows_changed",
                "feature_observations_changed",
                "target_observations_changed",
                "label_endpoint_observations_changed",
            )
        }, sort_keys=True), flush=True)

    result = {
        "schema_version": 1,
        "experiment_id": "EXPERIMENT-005B-ORDERING-FALSIFICATION",
        "classification": "POST_HOC_FALSIFICATION_ONLY",
        "stage": "matrix_difference_audit",
        "parent_pr": 45,
        "comparison": (
            "original timestamp,tx_hash,log_index vs corrected "
            "timestamp,log_index,tx_hash"
        ),
        "families": families,
        "totals": {
            key: sum(int(row[key]) for row in families)
            for key in (
                "rows",
                "feature_rows_changed",
                "target_rows_changed",
                "label_endpoint_rows_changed",
                "feature_observations_changed",
                "target_observations_changed",
                "label_endpoint_observations_changed",
            )
        },
    }
    (OUT / "matrix_difference_audit.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
