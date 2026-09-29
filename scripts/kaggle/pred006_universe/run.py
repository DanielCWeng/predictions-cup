# ruff: noqa
from __future__ import annotations

import json
from pathlib import Path

import duckdb

OUT = Path("/kaggle/working/pred006_universe")
OUT.mkdir(parents=True, exist_ok=True)


def q(path: Path) -> str:
    return str(path).replace("'", "''")


def source_files(kind: str) -> list[Path]:
    files = []
    for path in Path("/kaggle/input").rglob("*.parquet"):
        text = str(path)
        if f"/{kind}/" not in text:
            continue
        if kind == "fills" and any(x in text for x in ("/fees/", "/rebates/", "/unattributed_fee_legs/")):
            continue
        files.append(path)
    if not files:
        raise RuntimeError(f"no DATA-003 {kind} parquet files found")
    return sorted(files)


def relation(files: list[Path]) -> str:
    paths = ",".join("'" + q(path) + "'" for path in files)
    return f"read_parquet([{paths}], union_by_name=true)"


def records(frame):
    return json.loads(frame.to_json(orient="records", date_format="iso"))


def main() -> None:
    fills = source_files("fills")
    fees = source_files("fees")
    con = duckdb.connect()
    con.execute("PRAGMA threads=4")
    fill_rel = relation(fills)
    fee_rel = relation(fees)

    overview = con.execute(f"""
        SELECT
          COUNT(*) AS row_count,
          COUNT(DISTINCT condition_id) AS conditions,
          COUNT(DISTINCT token_id) AS tokens,
          COUNT(DISTINCT sig_market_id) AS sig_markets,
          COUNT(DISTINCT participant_address) AS participants,
          COUNT(DISTINCT counterparty_address) AS counterparties,
          MIN(CAST(timestamp AS BIGINT)) AS min_timestamp,
          MAX(CAST(timestamp AS BIGINT)) AS max_timestamp,
          COUNT(*) FILTER (WHERE participant_address IS NULL) AS missing_participant,
          COUNT(*) FILTER (WHERE counterparty_address IS NULL) AS missing_counterparty,
          COUNT(*) FILTER (WHERE price IS NULL) AS missing_price,
          COUNT(*) FILTER (WHERE size_shares IS NULL) AS missing_size,
          COUNT(*) FILTER (WHERE value_usd IS NULL) AS missing_value
        FROM {fill_rel}
    """).fetchdf()

    mapping = con.execute(f"""
        SELECT UPPER(CAST(mapping_class AS VARCHAR)) AS mapping_class,
               COUNT(*) AS row_count,
               COUNT(DISTINCT condition_id) AS conditions,
               COUNT(DISTINCT sig_market_id) AS sig_markets
        FROM {fill_rel}
        GROUP BY 1 ORDER BY 1
    """).fetchdf()

    daily = con.execute(f"""
        SELECT CAST(to_timestamp(CAST(timestamp AS BIGINT)) AS DATE) AS utc_date,
               COUNT(*) AS row_count,
               COUNT(DISTINCT condition_id) AS active_conditions,
               COUNT(DISTINCT sig_market_id) AS active_sig_markets,
               COUNT(DISTINCT participant_address) AS participants
        FROM {fill_rel}
        GROUP BY 1 ORDER BY 1
    """).fetchdf()

    hour = con.execute(f"""
        SELECT CAST(strftime(to_timestamp(CAST(timestamp AS BIGINT)), '%H') AS INTEGER) AS utc_hour,
               COUNT(*) AS row_count,
               COUNT(DISTINCT condition_id) AS conditions,
               COUNT(DISTINCT participant_address) AS participants
        FROM {fill_rel}
        GROUP BY 1 ORDER BY 1
    """).fetchdf()

    participants = con.execute(f"""
        WITH p AS (
          SELECT CAST(participant_address AS VARCHAR) AS participant,
                 COUNT(*) AS fills,
                 COUNT(DISTINCT condition_id) AS conditions,
                 COUNT(DISTINCT sig_market_id) AS sig_markets,
                 MIN(CAST(timestamp AS BIGINT)) AS first_ts,
                 MAX(CAST(timestamp AS BIGINT)) AS last_ts
          FROM {fill_rel}
          WHERE participant_address IS NOT NULL
          GROUP BY 1
        )
        SELECT
          COUNT(*) AS participants,
          SUM(CASE WHEN fills >= 2 THEN 1 ELSE 0 END) AS recurring_2plus,
          SUM(CASE WHEN fills >= 5 THEN 1 ELSE 0 END) AS recurring_5plus,
          SUM(CASE WHEN fills >= 20 THEN 1 ELSE 0 END) AS recurring_20plus,
          quantile_cont(fills, 0.5) AS median_fills,
          quantile_cont(fills, 0.9) AS p90_fills,
          quantile_cont(fills, 0.99) AS p99_fills,
          MAX(fills) AS max_fills,
          quantile_cont(conditions, 0.5) AS median_conditions,
          quantile_cont(conditions, 0.9) AS p90_conditions,
          MAX(conditions) AS max_conditions
        FROM p
    """).fetchdf()

    participant_overlap = con.execute(f"""
        WITH pc AS (
          SELECT DISTINCT CAST(participant_address AS VARCHAR) AS participant,
                          CAST(condition_id AS VARCHAR) AS condition_id
          FROM {fill_rel}
          WHERE participant_address IS NOT NULL
        ),
        ps AS (
          SELECT participant,
                 COUNT(DISTINCT condition_id) AS conditions
          FROM pc GROUP BY 1
        )
        SELECT
          SUM(CASE WHEN conditions = 1 THEN 1 ELSE 0 END) AS one_condition,
          SUM(CASE WHEN conditions >= 2 THEN 1 ELSE 0 END) AS multi_condition,
          SUM(CASE WHEN conditions >= 5 THEN 1 ELSE 0 END) AS five_plus_conditions,
          SUM(CASE WHEN conditions >= 20 THEN 1 ELSE 0 END) AS twenty_plus_conditions
        FROM ps
    """).fetchdf()

    market_lifetimes = con.execute(f"""
        WITH x AS (
          SELECT CAST(condition_id AS VARCHAR) AS condition_id,
                 COUNT(*) AS fills,
                 MIN(CAST(timestamp AS BIGINT)) AS first_ts,
                 MAX(CAST(timestamp AS BIGINT)) AS last_ts,
                 COUNT(DISTINCT CAST(to_timestamp(CAST(timestamp AS BIGINT)) AS DATE)) AS active_days
          FROM {fill_rel}
          GROUP BY 1
        )
        SELECT
          quantile_cont(fills, 0.5) AS median_fills,
          quantile_cont(fills, 0.9) AS p90_fills,
          quantile_cont(fills, 0.99) AS p99_fills,
          quantile_cont(last_ts-first_ts, 0.5) AS median_lifetime_s,
          quantile_cont(last_ts-first_ts, 0.9) AS p90_lifetime_s,
          quantile_cont(active_days, 0.5) AS median_active_days,
          quantile_cont(active_days, 0.9) AS p90_active_days
        FROM x
    """).fetchdf()

    simultaneous = con.execute(f"""
        WITH x AS (
          SELECT CAST(timestamp AS BIGINT) AS timestamp,
                 COUNT(*) AS row_count,
                 COUNT(DISTINCT condition_id) AS conditions,
                 COUNT(DISTINCT sig_market_id) AS sig_markets
          FROM {fill_rel}
          GROUP BY 1
        )
        SELECT
          COUNT(*) AS distinct_timestamps,
          SUM(CASE WHEN row_count > 1 THEN row_count ELSE 0 END) AS row_count_at_shared_timestamps,
          SUM(CASE WHEN conditions > 1 THEN 1 ELSE 0 END) AS multi_condition_timestamps,
          SUM(CASE WHEN sig_markets > 1 THEN 1 ELSE 0 END) AS multi_sig_market_timestamps,
          MAX(row_count) AS max_rows_same_timestamp,
          MAX(conditions) AS max_conditions_same_timestamp
        FROM x
    """).fetchdf()

    prices = con.execute(f"""
        SELECT
          quantile_cont(CAST(price AS DOUBLE), 0.01) AS p01,
          quantile_cont(CAST(price AS DOUBLE), 0.05) AS p05,
          quantile_cont(CAST(price AS DOUBLE), 0.25) AS p25,
          quantile_cont(CAST(price AS DOUBLE), 0.50) AS p50,
          quantile_cont(CAST(price AS DOUBLE), 0.75) AS p75,
          quantile_cont(CAST(price AS DOUBLE), 0.95) AS p95,
          quantile_cont(CAST(price AS DOUBLE), 0.99) AS p99,
          AVG(CASE WHEN CAST(price AS DOUBLE) <= 0.05 OR CAST(price AS DOUBLE) >= 0.95 THEN 1.0 ELSE 0.0 END) AS tail_fraction
        FROM {fill_rel}
    """).fetchdf()

    fee = con.execute(f"""
        SELECT CAST(fee_evidence AS VARCHAR) AS fee_evidence,
               COUNT(*) AS row_count,
               COUNT(DISTINCT condition_id) AS conditions
        FROM {fee_rel}
        GROUP BY 1 ORDER BY rows DESC
    """).fetchdf()

    windows = con.execute(f"""
        SELECT CAST(window_id AS VARCHAR) AS window_id,
               COUNT(*) AS row_count,
               COUNT(DISTINCT condition_id) AS conditions,
               MIN(CAST(timestamp AS BIGINT)) AS min_ts,
               MAX(CAST(timestamp AS BIGINT)) AS max_ts
        FROM {fill_rel}
        GROUP BY 1 ORDER BY min_ts
    """).fetchdf()

    concentration = con.execute(f"""
        WITH c AS (
          SELECT CAST(condition_id AS VARCHAR) AS condition_id,
                 COUNT(*) AS fills
          FROM {fill_rel}
          GROUP BY 1
        ),
        ranked AS (
          SELECT fills,
                 ROW_NUMBER() OVER (ORDER BY fills DESC) AS rn,
                 SUM(fills) OVER () AS total
          FROM c
        )
        SELECT
          SUM(CASE WHEN rn <= 1 THEN fills ELSE 0 END) * 1.0 / MAX(total) AS top1_share,
          SUM(CASE WHEN rn <= 5 THEN fills ELSE 0 END) * 1.0 / MAX(total) AS top5_share,
          SUM(CASE WHEN rn <= 20 THEN fills ELSE 0 END) * 1.0 / MAX(total) AS top20_share
        FROM ranked
    """).fetchdf()

    payload = {
      "schema_version": 1,
      "experiment_id": "PRED-006",
      "classification": "NON_PREDICTIVE_UNIVERSE_CHARACTERIZATION",
      "hypothesis_families_frozen_before_this_output": True,
      "predictive_targets_computed": False,
      "predictive_performance_computed": False,
      "overview": records(overview),
      "mapping_classes": records(mapping),
      "daily_activity": records(daily),
      "utc_hour_activity": records(hour),
      "participant_recurrence": records(participants),
      "participant_cross_market_overlap": records(participant_overlap),
      "market_lifetimes": records(market_lifetimes),
      "same_timestamp_structure": records(simultaneous),
      "price_distribution": records(prices),
      "fee_evidence_availability": records(fee),
      "capture_windows": records(windows),
      "activity_concentration": records(concentration),
      "source_fill_files": len(fills),
      "source_fee_files": len(fees)
    }
    path = OUT / "universe_audit.json"
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    print("PRED006_UNIVERSE_RESULT=" + json.dumps({
      "rows": payload["overview"][0]["row_count"],
      "conditions": payload["overview"][0]["conditions"],
      "sig_markets": payload["overview"][0]["sig_markets"],
      "participants": payload["overview"][0]["participants"],
      "predictive_targets_computed": False
    }, sort_keys=True), flush=True)
    con.close()


if __name__ == "__main__":
    main()
