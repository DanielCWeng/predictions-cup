# ruff: noqa: E501
"""EXPERIMENT-005B Stage 2: causal feature and target matrix construction on Kaggle only."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import duckdb

FAMILIES = ("US_2024", "CAN_2025", "COL_2026", "HUN_2026", "PER_2026")
WINDOWS = (5, 15, 30, 60, 120, 300, 900)
CLOCK_HORIZONS = (1, 5, 15, 30, 60, 120, 300)
EVENT_HORIZONS = (1, 2, 5, 10)
EPS = 1e-6
JUMP = 0.02
OUT = Path("/kaggle/working/005b_historical_predictive_atlas/features_targets")
OUT.mkdir(parents=True, exist_ok=True)
SPEC_PATH = Path(__file__).with_name("feature_target_spec.json")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def locate_trade_file(family: str) -> Path:
    matches = sorted(Path("/kaggle/input").rglob(f"canonical_trades_{family}.parquet"))
    if len(matches) != 1:
        raise RuntimeError(f"expected one reconstruction parquet for {family}, found {matches}")
    return matches[0]


def window(partition: str, order: str, seconds: int, *, following: bool = False) -> str:
    span = seconds * 1_000_000
    if following:
        frame = f"RANGE BETWEEN 1 FOLLOWING AND {span} FOLLOWING"
    else:
        frame = f"RANGE BETWEEN {span} PRECEDING AND CURRENT ROW"
    return f"PARTITION BY {partition} ORDER BY {order} {frame}"


def safe_ratio(num: str, den: str) -> str:
    return f"CASE WHEN ({den})>0 THEN ({num})/({den}) ELSE NULL END"


def loo_dispersion(sum_: str, sumsq: str, count: str) -> str:
    mean = safe_ratio(sum_, count)
    variance = f"CASE WHEN ({count})>0 THEN GREATEST(0.0, ({sumsq})/({count})-POWER(({mean}),2)) ELSE NULL END"
    return f"SQRT({variance})"


def build_family(family: str) -> dict[str, object]:
    source = locate_trade_file(family)
    con = duckdb.connect()
    con.execute("PRAGMA threads=4")
    con.execute("PRAGMA preserve_insertion_order=false")
    con.execute("PRAGMA temp_directory='/kaggle/working/duckdb_tmp'")
    quoted = str(source).replace("'", "''")
    minimum, maximum = con.execute(
        f"SELECT MIN(timestamp), MAX(timestamp) FROM read_parquet('{quoted}')"
    ).fetchone()
    if minimum is None or maximum is None or maximum <= minimum:
        raise RuntimeError(f"invalid time span for {family}")
    span = int(maximum) - int(minimum)
    train_end = int(minimum) + int(span * 0.60)
    dev_end = int(minimum) + int(span * 0.80)

    con.execute(
        f"""
        CREATE TEMP TABLE ordered AS
        WITH raw AS (
          SELECT *,
            CAST(timestamp AS BIGINT) * 1000000
              + ROW_NUMBER() OVER (
                  PARTITION BY condition_id, timestamp
                  ORDER BY tx_hash, log_index
                ) - 1 AS market_order_us,
            CAST(timestamp AS BIGINT) * 1000000
              + ROW_NUMBER() OVER (
                  PARTITION BY family, timestamp
                  ORDER BY tx_hash, log_index, condition_id
                ) - 1 AS family_order_us,
            CAST(timestamp AS BIGINT) * 1000000
              + ROW_NUMBER() OVER (
                  PARTITION BY event_id, timestamp
                  ORDER BY tx_hash, log_index, condition_id
                ) - 1 AS event_order_us
          FROM read_parquet('{quoted}')
        ),
        lagged AS (
          SELECT *,
            LN(GREATEST({EPS}, LEAST(1.0-{EPS}, p_yes))
               / (1.0-GREATEST({EPS}, LEAST(1.0-{EPS}, p_yes)))) AS logit_price,
            LAG(p_yes) OVER (
              PARTITION BY condition_id ORDER BY market_order_us
            ) AS previous_price,
            LAG(timestamp) OVER (
              PARTITION BY condition_id ORDER BY market_order_us
            ) AS previous_timestamp
          FROM raw
        ),
        deltaed AS (
          SELECT *,
            p_yes-previous_price AS one_trade_change,
            CAST(timestamp-previous_timestamp AS DOUBLE) AS interarrival_seconds
          FROM lagged
        ),
        signed AS (
          SELECT *,
            SIGN(one_trade_change) AS return_sign,
            LAG(SIGN(one_trade_change)) OVER (
              PARTITION BY condition_id ORDER BY market_order_us
            ) AS previous_return_sign,
            LAG(one_trade_change) OVER (
              PARTITION BY condition_id ORDER BY market_order_us
            ) AS previous_one_trade_change
          FROM deltaed
        ),
        streak_groups AS (
          SELECT *,
            SUM(
              CASE
                WHEN return_sign IS NULL OR return_sign=0
                  OR previous_return_sign IS NULL
                  OR previous_return_sign=0
                  OR return_sign<>previous_return_sign
                THEN 1 ELSE 0
              END
            ) OVER (
              PARTITION BY condition_id ORDER BY market_order_us
              ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
            ) AS streak_group
          FROM signed
        )
        SELECT *,
          CASE WHEN return_sign IS NULL OR return_sign=0 THEN 0
               ELSE ROW_NUMBER() OVER (
                 PARTITION BY condition_id, streak_group ORDER BY market_order_us
               )
          END AS return_streak
        FROM streak_groups
        """
    )

    con.execute(
        f"""
        CREATE TEMP TABLE size_refs AS
        WITH market AS (
          SELECT condition_id, COUNT(*) AS n_train,
                 APPROX_QUANTILE(size_shares, 0.90) AS market_q90
          FROM ordered
          WHERE timestamp < {train_end}
          GROUP BY condition_id
        ),
        family AS (
          SELECT APPROX_QUANTILE(size_shares, 0.90) AS family_q90
          FROM ordered
          WHERE timestamp < {train_end}
        )
        SELECT condition_id, n_train,
          CASE WHEN n_train>=100 THEN market_q90 ELSE family_q90 END AS size_q90_threshold
        FROM market CROSS JOIN family
        """
    )
    con.execute(
        """
        CREATE TEMP VIEW enriched AS
        SELECT o.*, r.size_q90_threshold
        FROM ordered AS o
        LEFT JOIN size_refs AS r USING (condition_id)
        """
    )

    rolling: list[str] = []
    context_raw: list[str] = []
    own_aux: list[str] = []
    for seconds in WINDOWS:
        own = window("condition_id", "market_order_us", seconds)
        fam = window("family", "family_order_us", seconds)
        evt = window("event_id", "event_order_us", seconds)
        family_own = window(
            "family, condition_id",
            "family_order_us",
            seconds,
        )
        event_own = window(
            "event_id, condition_id",
            "event_order_us",
            seconds,
        )
        suffix = str(seconds)

        rolling.extend(
            [
                f"p_yes-FIRST_VALUE(p_yes) OVER ({own}) AS price_change_{suffix}",
                f"logit_price-FIRST_VALUE(logit_price) OVER ({own}) AS logit_return_{suffix}",
                f"p_yes-FIRST_VALUE(p_yes) OVER ({own}) AS momentum_{suffix}",
                f"SQRT(SUM(POWER(one_trade_change,2)) OVER ({own})) AS realised_vol_{suffix}",
                f"ABS(p_yes-FIRST_VALUE(p_yes) OVER ({own})) AS absolute_return_{suffix}",
                f"MAX(ABS(one_trade_change)) OVER ({own}) AS recent_max_move_{suffix}",
                f"AVG(CASE WHEN one_trade_change IS NULL THEN NULL WHEN ABS(one_trade_change)>={JUMP} THEN 1.0 ELSE 0.0 END) OVER ({own}) AS jump_frequency_{suffix}",
                f"AVG(CASE WHEN one_trade_change IS NULL THEN NULL WHEN ABS(one_trade_change)<1e-12 THEN 1.0 ELSE 0.0 END) OVER ({own}) AS stasis_fraction_{suffix}",
                f"AVG(CASE WHEN return_sign IS NULL OR return_sign=0 OR previous_return_sign IS NULL OR previous_return_sign=0 THEN NULL WHEN return_sign<>previous_return_sign THEN 1.0 ELSE 0.0 END) OVER ({own}) AS direction_change_frequency_{suffix}",
                f"COUNT(*) OVER ({own}) AS trade_count_{suffix}",
                f"SUM(value_usd) OVER ({own}) AS notional_value_{suffix}",
                f"SUM(size_shares) OVER ({own}) AS share_volume_{suffix}",
                f"AVG(size_shares) OVER ({own}) AS mean_trade_size_{suffix}",
                f"APPROX_QUANTILE(size_shares,0.50) OVER ({own}) AS median_trade_size_{suffix}",
                f"VAR_POP(size_shares) OVER ({own}) AS trade_size_variance_{suffix}",
                f"APPROX_QUANTILE(size_shares,0.90) OVER ({own}) AS trade_size_q90_{suffix}",
                f"MAX(size_shares) OVER ({own}) AS max_trade_size_{suffix}",
                f"AVG(CASE WHEN size_shares>=size_q90_threshold THEN 1.0 ELSE 0.0 END) OVER ({own}) AS large_trade_share_{suffix}",
                f"AVG(interarrival_seconds) OVER ({own}) AS mean_interarrival_{suffix}",
                f"STDDEV_POP(interarrival_seconds) OVER ({own})/(AVG(interarrival_seconds) OVER ({own})+1e-9) AS burstiness_{suffix}",
            ]
        )
        for own_prefix, own_frame in (
            ("family", family_own),
            ("event", event_own),
        ):
            own_aux.extend(
                [
                    f"SUM(one_trade_change) OVER ({own_frame}) AS {own_prefix}_own_delta_sum_{suffix}",
                    f"SUM(POWER(one_trade_change,2)) OVER ({own_frame}) AS {own_prefix}_own_delta_sq_sum_{suffix}",
                    f"COUNT(one_trade_change) OVER ({own_frame}) AS {own_prefix}_own_delta_count_{suffix}",
                    f"COUNT(*) OVER ({own_frame}) AS {own_prefix}_own_activity_count_{suffix}",
                    f"SUM(CASE WHEN one_trade_change>0 THEN 1 ELSE 0 END) OVER ({own_frame}) AS {own_prefix}_own_up_{suffix}",
                    f"SUM(CASE WHEN one_trade_change<0 THEN 1 ELSE 0 END) OVER ({own_frame}) AS {own_prefix}_own_down_{suffix}",
                ]
            )
        for prefix, frame in (("family", fam), ("event", evt)):
            context_raw.extend(
                [
                    f"SUM(one_trade_change) OVER ({frame}) AS {prefix}_delta_sum_{suffix}",
                    f"SUM(POWER(one_trade_change,2)) OVER ({frame}) AS {prefix}_delta_sq_sum_{suffix}",
                    f"COUNT(one_trade_change) OVER ({frame}) AS {prefix}_delta_count_{suffix}",
                    f"COUNT(*) OVER ({frame}) AS {prefix}_activity_count_{suffix}",
                    f"SUM(CASE WHEN one_trade_change>0 THEN 1 ELSE 0 END) OVER ({frame}) AS {prefix}_up_{suffix}",
                    f"SUM(CASE WHEN one_trade_change<0 THEN 1 ELSE 0 END) OVER ({frame}) AS {prefix}_down_{suffix}",
                ]
            )

    target_raw: list[str] = []
    for seconds in CLOCK_HORIZONS:
        future = window("condition_id", "market_order_us", seconds, following=True)
        suffix = str(seconds)
        target_raw.extend(
            [
                f"COUNT(*) OVER ({future}) AS future_count_{suffix}",
                f"LAST_VALUE(p_yes) OVER ({future}) AS future_price_{suffix}",
                f"LAST_VALUE(logit_price) OVER ({future}) AS future_logit_{suffix}",
                f"MAX(timestamp) OVER ({future}) AS clock_label_end_{suffix}",
                f"SQRT(SUM(POWER(one_trade_change,2)) OVER ({future})) AS target_future_realised_movement_{suffix}",
            ]
        )
    for count in EVENT_HORIZONS:
        target_raw.extend(
            [
                f"LEAD(p_yes,{count}) OVER (PARTITION BY condition_id ORDER BY market_order_us) AS event_future_price_{count}",
                f"LEAD(timestamp,{count}) OVER (PARTITION BY condition_id ORDER BY market_order_us) AS event_label_end_{count}",
            ]
        )

    selected = ",\n          ".join(rolling + own_aux + context_raw + target_raw)
    con.execute(
        f"""
        CREATE TEMP TABLE wide_raw AS
        SELECT
          family,event_id,market_id,condition_id,timestamp,tx_hash,log_index,market_order_us,
          p_yes,logit_price,one_trade_change,interarrival_seconds,return_streak,
          ABS(previous_one_trade_change) AS previous_jump_size,
          {selected}
        FROM enriched
        """
    )

    con.execute(
        f"""
        CREATE TEMP TABLE activity_refs AS
        WITH market AS (
          SELECT condition_id, COUNT(*) AS n_train,
                 APPROX_QUANTILE(trade_count_60,0.50) AS market_median
          FROM wide_raw
          WHERE timestamp < {train_end}
          GROUP BY condition_id
        ),
        family AS (
          SELECT APPROX_QUANTILE(trade_count_60,0.50) AS family_median
          FROM wide_raw
          WHERE timestamp < {train_end}
        )
        SELECT condition_id,
          CASE WHEN n_train>=100 THEN market_median ELSE family_median END AS activity_ref_60
        FROM market CROSS JOIN family
        """
    )

    final_features: list[str] = [
        "p_yes AS price_yes",
        "logit_price",
        "return_streak",
        "previous_jump_size",
        "p_yes-MAX(p_yes) OVER (PARTITION BY condition_id ORDER BY market_order_us RANGE BETWEEN 300000000 PRECEDING AND CURRENT ROW) AS distance_recent_high_300",
        "p_yes-MIN(p_yes) OVER (PARTITION BY condition_id ORDER BY market_order_us RANGE BETWEEN 300000000 PRECEDING AND CURRENT ROW) AS distance_recent_low_300",
        "REGR_SLOPE(p_yes,market_order_us/1000000.0) OVER (PARTITION BY condition_id ORDER BY market_order_us RANGE BETWEEN 60000000 PRECEDING AND CURRENT ROW) AS trend_slope_60",
        "REGR_SLOPE(p_yes,market_order_us/1000000.0) OVER (PARTITION BY condition_id ORDER BY market_order_us RANGE BETWEEN 300000000 PRECEDING AND CURRENT ROW) AS trend_slope_300",
        "price_change_30-price_change_300 AS momentum_short_minus_long_30_300",
        "-price_change_30*SIGN(price_change_300) AS reversal_30_300",
        "price_change_15/15.0-price_change_60/60.0 AS acceleration_15_60",
        "interarrival_seconds AS last_interarrival_seconds",
        "trade_count_30/30.0-trade_count_300/300.0 AS activity_acceleration_30_300",
        "trade_count_60/(activity_ref_60+1e-9) AS activity_vs_train_history_60",
    ]
    for seconds in WINDOWS:
        suffix=str(seconds)
        final_features.extend(
            [
                f"price_change_{suffix}",
                f"logit_return_{suffix}",
                f"momentum_{suffix}",
                f"realised_vol_{suffix}",
                f"absolute_return_{suffix}",
                f"recent_max_move_{suffix}",
                f"jump_frequency_{suffix}",
                f"stasis_fraction_{suffix}",
                f"direction_change_frequency_{suffix}",
                f"trade_count_{suffix}",
                f"notional_value_{suffix}",
                f"share_volume_{suffix}",
                f"mean_trade_size_{suffix}",
                f"median_trade_size_{suffix}",
                f"trade_size_variance_{suffix}",
                f"trade_size_q90_{suffix}",
                f"max_trade_size_{suffix}",
                f"large_trade_share_{suffix}",
                f"mean_interarrival_{suffix}",
                f"burstiness_{suffix}",
            ]
        )
        for prefix in ("family","event"):
            count_expr = (
                f"{prefix}_delta_count_{suffix}"
                f"-{prefix}_own_delta_count_{suffix}"
            )
            sum_expr = (
                f"{prefix}_delta_sum_{suffix}"
                f"-{prefix}_own_delta_sum_{suffix}"
            )
            sq_expr = (
                f"{prefix}_delta_sq_sum_{suffix}"
                f"-{prefix}_own_delta_sq_sum_{suffix}"
            )
            activity_expr = (
                f"{prefix}_activity_count_{suffix}"
                f"-{prefix}_own_activity_count_{suffix}"
            )
            up_expr = (
                f"{prefix}_up_{suffix}"
                f"-{prefix}_own_up_{suffix}"
            )
            down_expr = (
                f"{prefix}_down_{suffix}"
                f"-{prefix}_own_down_{suffix}"
            )
            final_features.extend(
                [
                    f"{safe_ratio(sum_expr,count_expr)} AS loo_{prefix}_price_movement_{suffix}",
                    f"{activity_expr} AS loo_{prefix}_activity_{suffix}",
                    f"{loo_dispersion(sum_expr,sq_expr,count_expr)} AS loo_{prefix}_dispersion_{suffix}",
                    f"{safe_ratio(up_expr,count_expr)} AS loo_{prefix}_fraction_up_{suffix}",
                    f"{safe_ratio(down_expr,count_expr)} AS loo_{prefix}_fraction_down_{suffix}",
                ]
            )
    final_features.extend(
        [
            "loo_event_price_movement_30 AS common_event_momentum_30",
            "price_change_30-loo_event_price_movement_30 AS market_minus_event_momentum_30",
        ]
    )

    targets: list[str] = []
    label_times: list[str] = []
    for seconds in CLOCK_HORIZONS:
        suffix=str(seconds)
        targets.extend(
            [
                f"CASE WHEN future_count_{suffix}>0 THEN future_price_{suffix}-p_yes END AS target_clock_price_change_{suffix}",
                f"CASE WHEN future_count_{suffix}>0 THEN future_logit_{suffix}-logit_price END AS target_clock_logit_change_{suffix}",
                f"CASE WHEN future_count_{suffix}>0 THEN SIGN(future_price_{suffix}-p_yes) END AS target_clock_sign_{suffix}",
                f"CASE WHEN future_count_{suffix}>0 THEN ABS(future_price_{suffix}-p_yes) END AS target_clock_absolute_change_{suffix}",
                f"CASE WHEN future_count_{suffix}>0 THEN target_future_realised_movement_{suffix} END AS target_clock_realised_movement_{suffix}",
            ]
        )
        label_times.append(f"clock_label_end_{suffix}")
    for count in EVENT_HORIZONS:
        targets.extend(
            [
                f"event_future_price_{count}-p_yes AS target_event_price_change_{count}",
                f"SIGN(event_future_price_{count}-p_yes) AS target_event_sign_{count}",
                f"ABS(event_future_price_{count}-p_yes) AS target_event_absolute_change_{count}",
            ]
        )
        label_times.append(f"event_label_end_{count}")

    output=OUT/f"feature_target_{family}.parquet"
    qout=str(output).replace("'","''")
    select_final=",\n          ".join(final_features+targets+label_times)
    con.execute(
        f"""
        COPY (
          SELECT
            family,event_id,market_id,condition_id,timestamp,tx_hash,log_index,market_order_us,
            CASE WHEN timestamp<{train_end} THEN 'TRAIN'
                 WHEN timestamp<{dev_end} THEN 'DEV' ELSE 'HOLDOUT' END AS raw_split,
            {train_end} AS train_end_timestamp,
            {dev_end} AS dev_end_timestamp,
            {select_final}
          FROM wide_raw
          LEFT JOIN activity_refs USING (condition_id)
          ORDER BY timestamp,tx_hash,log_index,condition_id
        )
        TO '{qout}' (FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE 100000)
        """
    )
    rows=int(con.execute(f"SELECT COUNT(*) FROM read_parquet('{qout}')").fetchone()[0])
    split_counts=dict(
        con.execute(
            f"SELECT raw_split,COUNT(*) FROM read_parquet('{qout}') GROUP BY raw_split"
        ).fetchall()
    )
    schema=con.execute(f"DESCRIBE SELECT * FROM read_parquet('{qout}')").fetchall()
    feature_cols=[row[0] for row in schema if not row[0].startswith("target_") and row[0] not in {
        "family","event_id","market_id","condition_id","timestamp","tx_hash","log_index",
        "market_order_us","raw_split","train_end_timestamp","dev_end_timestamp"
    } and not row[0].startswith("clock_label_end_") and not row[0].startswith("event_label_end_")]
    target_cols=[row[0] for row in schema if row[0].startswith("target_")]
    con.close()
    return {
        "family":family,
        "source_path":str(source),
        "source_sha256":sha256(source),
        "rows":rows,
        "split_counts":split_counts,
        "train_end_timestamp":train_end,
        "dev_end_timestamp":dev_end,
        "feature_count":len(feature_cols),
        "target_count":len(target_cols),
        "feature_columns":feature_cols,
        "target_columns":target_cols,
        "output_path":output.name,
        "output_bytes":output.stat().st_size,
        "output_sha256":sha256(output),
    }


def main() -> None:
    spec=json.loads(SPEC_PATH.read_text())
    reports=[]
    for family in FAMILIES:
        print(f"BUILD {family}", flush=True)
        reports.append(build_family(family))
    payload={
        "schema_version":1,
        "experiment_id":"EXPERIMENT-005B",
        "stage":"features_targets",
        "feature_target_spec_sha256":sha256(SPEC_PATH),
        "spec":spec,
        "families":reports,
        "totals":{
            "rows":sum(int(item["rows"]) for item in reports),
            "output_bytes":sum(int(item["output_bytes"]) for item in reports),
        },
    }
    path=OUT/"feature_target_build_report.json"
    path.write_text(json.dumps(payload,indent=2,sort_keys=True)+"\n")
    print(json.dumps(payload["totals"],sort_keys=True))


if __name__=="__main__":
    main()
