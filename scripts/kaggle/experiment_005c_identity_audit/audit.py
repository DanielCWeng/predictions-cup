from __future__ import annotations

import csv
import json
import zipfile
from pathlib import Path

import duckdb

INPUT = Path("/kaggle/input")
WORK = Path("/kaggle/working/005c_source_identity_audit")
WORK.mkdir(parents=True, exist_ok=True)

FAMILIES = ("US_2024", "CAN_2025", "COL_2026", "HUN_2026", "PER_2026")
FALLBACK = {"US_2024", "CAN_2025"}
KEY = ("tx_hash", "log_index", "token_id")


def optional_one(pattern: str) -> Path | None:
    matches = sorted(INPUT.rglob(pattern))
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise RuntimeError(f"multiple loose {pattern}: {matches}")

    archived: list[tuple[Path, str]] = []
    for archive_path in sorted(INPUT.rglob("*.zip")):
        try:
            with zipfile.ZipFile(archive_path) as archive:
                for name in archive.namelist():
                    if Path(name).name == pattern:
                        archived.append((archive_path, name))
        except zipfile.BadZipFile:
            continue
    if not archived:
        return None
    if len(archived) > 1:
        raise RuntimeError(f"multiple archived {pattern}: {archived}")

    archive_path, member = archived[0]
    target = WORK / "_cache" / pattern
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        with (
            zipfile.ZipFile(archive_path) as archive,
            archive.open(member) as source,
            target.open("wb") as output,
        ):
            while chunk := source.read(1 << 20):
                output.write(chunk)
    return target


def one(pattern: str) -> Path:
    path = optional_one(pattern)
    if path is None:
        raise RuntimeError(f"missing {pattern}")
    return path


def audit(
    con: duckdb.DuckDBPyConnection,
    path: Path,
    family: str,
    source_kind: str,
) -> dict[str, object]:
    source = str(path).replace("'", "''")
    total, null_key_rows = con.execute(
        f"""
        SELECT
          COUNT(*)::BIGINT,
          SUM(
            CASE
              WHEN tx_hash IS NULL OR log_index IS NULL OR token_id IS NULL
              THEN 1 ELSE 0
            END
          )::BIGINT
        FROM read_parquet('{source}')
        """
    ).fetchone()

    duplicate_keys, duplicate_rows, duplicate_excess_rows, max_multiplicity = (
        con.execute(
            f"""
            WITH grouped AS (
              SELECT
                tx_hash,
                log_index,
                token_id,
                COUNT(*)::BIGINT AS n
              FROM read_parquet('{source}')
              WHERE tx_hash IS NOT NULL
                AND log_index IS NOT NULL
                AND token_id IS NOT NULL
              GROUP BY tx_hash, log_index, token_id
              HAVING COUNT(*) > 1
            )
            SELECT
              COUNT(*)::BIGINT,
              COALESCE(SUM(n), 0)::BIGINT,
              COALESCE(SUM(n - 1), 0)::BIGINT,
              COALESCE(MAX(n), 1)::BIGINT
            FROM grouped
            """
        ).fetchone()
    )
    valid_key_rows = int(total) - int(null_key_rows or 0)
    unique_keys = valid_key_rows - int(duplicate_excess_rows or 0)

    result = {
        "family": family,
        "source_kind": source_kind,
        "source_file": path.name,
        "total_rows": int(total),
        "valid_key_rows": valid_key_rows,
        "null_key_rows": int(null_key_rows or 0),
        "unique_keys": unique_keys,
        "duplicate_keys": int(duplicate_keys or 0),
        "duplicate_rows": int(duplicate_rows or 0),
        "duplicate_excess_rows": int(duplicate_excess_rows or 0),
        "max_multiplicity": int(max_multiplicity or 1),
        "passes_frozen_identity_rule": (
            int(null_key_rows or 0) == 0
            and int(duplicate_keys or 0) == 0
        ),
    }
    print(json.dumps(result, sort_keys=True))
    return result


def main() -> None:
    con = duckdb.connect(str(WORK / "audit.duckdb"))
    con.execute(f"SET temp_directory='{WORK / 'duckdb_tmp'}'")
    con.execute("SET memory_limit='6GB'")
    con.execute("SET threads=2")

    rows: list[dict[str, object]] = []
    for family in FAMILIES:
        if family in FALLBACK:
            path = one(f"fees_{family}.parquet")
            source_kind = "DATA002_MATCHED_FILL_FALLBACK"
        else:
            path = one(f"fills_{family}.parquet")
            source_kind = "DATA001_RAW_FILL"
        rows.append(audit(con, path, family, source_kind))
    con.close()

    csv_path = WORK / "source_identity_audit.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    summary = {
        "experiment_id": "EXPERIMENT-005C",
        "audit": "POST_RUN_FROZEN_SOURCE_IDENTITY_RULE_VERIFICATION",
        "key": list(KEY),
        "families": rows,
        "all_pass": all(bool(row["passes_frozen_identity_rule"]) for row in rows),
        "scientific_design_changed": False,
        "predictive_outcomes_used_to_define_audit": False,
        "note": (
            "This post-run audit enforces the exact source-identity rule already frozen "
            "in preregistration; it does not introduce or select any predictive hypothesis."
        ),
    }
    (WORK / "source_identity_audit.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    if not summary["all_pass"]:
        raise SystemExit("frozen source identity rule failed")


if __name__ == "__main__":
    main()