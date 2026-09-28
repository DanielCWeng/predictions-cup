#!/usr/bin/env python3
"""Quantify EXPERIMENT-005B exposure to same-second pseudo-ordering.

This is diagnostic only. It does not modify the frozen 005B feature/target/model
specification or any original PR #45 artefact.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import duckdb

FAMILIES = ("US_2024", "CAN_2025", "COL_2026", "HUN_2026", "PER_2026")


def quantify(root: Path) -> dict[str, object]:
    con = duckdb.connect()
    con.execute("PRAGMA threads=4")
    families: list[dict[str, object]] = []
    for family in FAMILIES:
        path = root / f"canonical_trades_{family}.parquet"
        con.read_parquet(str(path)).create_view("fills", replace=True)
        row: dict[str, object] = {
            "family": family,
            "total_rows": int(con.execute("SELECT COUNT(*) FROM fills").fetchone()[0]),
        }
        for label, keys in (
            ("family", ("timestamp",)),
            ("event", ("event_id", "timestamp")),
            ("market", ("condition_id", "timestamp")),
        ):
            key_sql = ", ".join(keys)
            con.execute(
                f"""
                CREATE OR REPLACE TEMP TABLE groups AS
                SELECT {key_sql},
                       COUNT(*) AS n_rows,
                       COUNT(DISTINCT tx_hash) AS n_tx
                FROM fills
                GROUP BY {key_sql}
                """
            )
            grouped = con.execute(
                """
                SELECT
                    COALESCE(SUM(n_rows) FILTER (WHERE n_rows > 1), 0),
                    COALESCE(SUM(n_rows) FILTER (WHERE n_tx > 1), 0),
                    COUNT(*) FILTER (WHERE n_tx > 1)
                FROM groups
                """
            ).fetchone()
            row[f"rows_same_{label}_second"] = int(grouped[0])
            row[f"rows_multi_tx_{label}_second"] = int(grouped[1])
            row[f"collision_groups_{label}"] = int(grouped[2])
            using = ", ".join(keys)
            tx_count = con.execute(
                f"""
                SELECT COUNT(DISTINCT fills.tx_hash)
                FROM fills
                JOIN groups USING ({using})
                WHERE groups.n_tx > 1
                """
            ).fetchone()[0]
            row[f"unique_tx_multi_{label}_second"] = int(tx_count)
        families.append(row)

    totals: dict[str, int] = {"total_rows": sum(int(x["total_rows"]) for x in families)}
    for key in families[0]:
        if key not in {"family", "total_rows"}:
            totals[key] = sum(int(x[key]) for x in families)
    return {
        "schema_version": 1,
        "experiment_id": "EXPERIMENT-005B-ORDERING-FALSIFICATION",
        "diagnostic": "same_second_collision_exposure",
        "families": families,
        "totals": totals,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    result = quantify(args.root)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps(result["totals"], sort_keys=True))


if __name__ == "__main__":
    main()
