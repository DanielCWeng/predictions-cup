# ruff: noqa: E501
"""EXPERIMENT-005B Stage 2: memory-bounded causal feature/target build for one family."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import duckdb

FAMILY = "COL_2026"
WINDOWS = (5, 15, 30, 60, 120, 300, 900)
CLOCK_HORIZONS = (1, 5, 15, 30, 60, 120, 300)
EVENT_HORIZONS = (1, 2, 5, 10)
EPS = 1e-6
JUMP = 0.02
FEATURE_TARGET_SPEC_SHA256 = "47dc9c9a678f58558aba7e4bc282103cacbc0836f08018a79d4197487d689cf7"
FEATURE_TARGET_SPEC_REPO_PATH = "data/experiments/experiment_005b/feature_target_spec.json"

OUT = Path("/kaggle/working/005b_historical_predictive_atlas/features_targets")
OUT.mkdir(parents=True, exist_ok=True)
TMP = Path("/kaggle/working/005b_historical_predictive_atlas/duckdb_tmp") / FAMILY
TMP.mkdir(parents=True, exist_ok=True)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def locate_source() -> Path:
    matches = sorted(Path("/kaggle/input").rglob(f"canonical_trades_{FAMILY}.parquet"))
    if len(matches) != 1:
        raise RuntimeError(f"expected one reconstruction parquet for {FAMILY}, found {matches}")
    return matches[0]


def connect() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    con.execute("PRAGMA threads=2")
    con.execute("PRAGMA preserve_insertion_order=false")
    con.execute("PRAGMA memory_limit='6GB'")
    escaped = str(TMP).replace("'", "''")
    con.execute(f"PRAGMA temp_directory='{escaped}'")
    return con


def qpath(path: Path) -> str:
    return str(path).replace("'", "''")


def frame(partition: str, order: str, seconds: int, *, future: bool = False) -> str:
    span = seconds * 1_000_000
    bounds = (
        f"RANGE BETWEEN 1 FOLLOWING AND {span} FOLLOWING"
        if future
        else f"RANGE BETWEEN {span} PRECEDING AND CURRENT ROW"
    )
    return f"PARTITION BY {partition} ORDER BY {order} {bounds}"


def safe_ratio(num: str, den: str) -> str:
    return f"CASE WHEN ({den})>0 THEN ({num})/({den}) ELSE NULL END"


def loo_dispersion(sum_: str, sumsq: str, count: str) -> str:
    mean = safe_ratio(sum_, count)
    variance = (
        f"CASE WHEN ({count})>0 THEN GREATEST("
        f"0.0, ({sumsq})/({count})-POWER(({mean}),2)"
        f") ELSE NULL END"
    )
    return f"SQRT({variance})"


def build_base(source: Path, base: Path) -> tuple[int, int]:
    con = connect()
    src = qpath(source)
    minimum, maximum = con.execute(
        f"SELECT MIN(timestamp), MAX(timestamp) FROM read_parquet('{src}')"
    ).fetchone()
    if minimum is None or maximum is None or maximum <= minimum:
        raise RuntimeError(f"invalid time span for {FAMILY}")
    minimum = int(minimum)
    maximum = int(maximum)
    span = maximum - minimum
    train_end = minimum + int(span * 0.60)
    dev_end = minimum + int(span * 0.80)
    dst = qpath(base)
    con.execute(
        f"""
        COPY (
          WITH raw AS (
            SELECT *,
              ROW_NUMBER() OVER (
                ORDER BY timestamp, tx_hash, log_index, condition_id
              ) AS row_id,
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
            FROM read_parquet('{src}')
          ),
          lagged AS (
            SELECT *,
              LN(
                GREATEST({EPS}, LEAST(1.0-{EPS}, p_yes))
                / (1.0-GREATEST({EPS}, LEAST(1.0-{EPS}, p_yes)))
              ) AS logit_price,
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
          SELECT
            row_id, family, event_id, market_id, condition_id, timestamp,
            tx_hash, log_index, market_order_us, family_order_us, event_order_us,
            p_yes, size_shares, value_usd, logit_price, one_trade_change,
            interarrival_seconds, return_sign, previous_return_sign,
            previous_one_trade_change,
            CASE
              WHEN return_sign IS NULL OR return_sign=0 THEN 0
              ELSE ROW_NUMBER() OVER (
                PARTITION BY condition_id, streak_group ORDER BY market_order_us
              )
            END AS return_streak,
            CASE
              WHEN timestamp<{train_end} THEN 'TRAIN'
              WHEN timestamp<{dev_end} THEN 'DEV'
              ELSE 'HOLDOUT'
            END AS raw_split,
            {train_end} AS train_end_timestamp,
            {dev_end} AS dev_end_timestamp
          FROM streak_groups
          ORDER BY row_id
        )
        TO '{dst}' (FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE 100000)
        """
    )
    con.close()
    return train_end, dev_end


def build_price(base: Path, output: Path) -> None:
    con = connect()
    src = qpath(base)
    dst = qpath(output)
    expressions: list[str] = []
    for seconds in WINDOWS:
        own = frame("condition_id", "market_order_us", seconds)
        suffix = str(seconds)
        expressions.extend(
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
            ]
        )
    expressions.extend(
        [
            "p_yes-MAX(p_yes) OVER (PARTITION BY condition_id ORDER BY market_order_us RANGE BETWEEN 300000000 PRECEDING AND CURRENT ROW) AS distance_recent_high_300",
            "p_yes-MIN(p_yes) OVER (PARTITION BY condition_id ORDER BY market_order_us RANGE BETWEEN 300000000 PRECEDING AND CURRENT ROW) AS distance_recent_low_300",
            "REGR_SLOPE(p_yes,market_order_us/1000000.0) OVER (PARTITION BY condition_id ORDER BY market_order_us RANGE BETWEEN 60000000 PRECEDING AND CURRENT ROW) AS trend_slope_60",
            "REGR_SLOPE(p_yes,market_order_us/1000000.0) OVER (PARTITION BY condition_id ORDER BY market_order_us RANGE BETWEEN 300000000 PRECEDING AND CURRENT ROW) AS trend_slope_300",
        ]
    )
    raw = ",\n              ".join(expressions)
    con.execute(
        f"""
        COPY (
          WITH w AS (
            SELECT row_id, {raw}
            FROM read_parquet('{src}')
          )
          SELECT
            *,
            price_change_30-price_change_300 AS momentum_short_minus_long_30_300,
            -price_change_30*SIGN(price_change_300) AS reversal_30_300,
            price_change_15/15.0-price_change_60/60.0 AS acceleration_15_60
          FROM w
          ORDER BY row_id
        )
        TO '{dst}' (FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE 100000)
        """
    )
    con.close()


def build_activity(base: Path, output: Path, train_end: int) -> None:
    raw_path = OUT / f"_activity_raw_{FAMILY}.parquet"
    con = connect()
    src = qpath(base)
    raw_dst = qpath(raw_path)
    con.execute(
        f"""
        CREATE TEMP TABLE size_refs AS
        WITH market AS (
          SELECT condition_id, COUNT(*) AS n_train,
                 APPROX_QUANTILE(size_shares,0.90) AS market_q90
          FROM read_parquet('{src}')
          WHERE timestamp<{train_end}
          GROUP BY condition_id
        ),
        fam AS (
          SELECT APPROX_QUANTILE(size_shares,0.90) AS family_q90
          FROM read_parquet('{src}')
          WHERE timestamp<{train_end}
        )
        SELECT condition_id,
          CASE WHEN n_train>=100 THEN market_q90 ELSE family_q90 END AS size_q90_threshold
        FROM market CROSS JOIN fam
        """
    )
    expressions: list[str] = []
    for seconds in WINDOWS:
        own = frame("condition_id", "market_order_us", seconds)
        suffix = str(seconds)
        expressions.extend(
            [
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
    expr = ",\n              ".join(expressions)
    con.execute(
        f"""
        COPY (
          SELECT
            b.row_id, b.condition_id, b.timestamp, b.interarrival_seconds,
            {expr}
          FROM (
            SELECT b.*, s.size_q90_threshold
            FROM read_parquet('{src}') AS b
            LEFT JOIN size_refs AS s USING (condition_id)
          ) AS b
          ORDER BY row_id
        )
        TO '{raw_dst}' (FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE 100000)
        """
    )
    con.execute(
        f"""
        CREATE TEMP TABLE activity_refs AS
        WITH market AS (
          SELECT condition_id, COUNT(*) AS n_train,
                 APPROX_QUANTILE(trade_count_60,0.50) AS market_median
          FROM read_parquet('{raw_dst}')
          WHERE timestamp<{train_end}
          GROUP BY condition_id
        ),
        fam AS (
          SELECT APPROX_QUANTILE(trade_count_60,0.50) AS family_median
          FROM read_parquet('{raw_dst}')
          WHERE timestamp<{train_end}
        )
        SELECT condition_id,
          CASE WHEN n_train>=100 THEN market_median ELSE family_median END AS activity_ref_60
        FROM market CROSS JOIN fam
        """
    )
    window_cols = []
    for seconds in WINDOWS:
        suffix = str(seconds)
        window_cols.extend(
            [
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
    cols = ",\n            ".join(window_cols)
    dst = qpath(output)
    con.execute(
        f"""
        COPY (
          SELECT
            a.row_id,
            a.interarrival_seconds AS last_interarrival_seconds,
            {cols},
            trade_count_30/30.0-trade_count_300/300.0 AS activity_acceleration_30_300,
            trade_count_60/(r.activity_ref_60+1e-9) AS activity_vs_train_history_60
          FROM read_parquet('{raw_dst}') AS a
          LEFT JOIN activity_refs AS r USING (condition_id)
          ORDER BY row_id
        )
        TO '{dst}' (FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE 100000)
        """
    )
    con.close()
    raw_path.unlink(missing_ok=True)


def build_context(base: Path, output: Path, scope: str) -> None:
    if scope == "family":
        global_partition = "family"
        own_partition = "family, condition_id"
        order = "family_order_us"
    elif scope == "event":
        global_partition = "event_id"
        own_partition = "event_id, condition_id"
        order = "event_order_us"
    else:
        raise ValueError(scope)

    con = connect()
    src = qpath(base)
    raw_expr: list[str] = []
    final_expr: list[str] = []
    for seconds in WINDOWS:
        suffix = str(seconds)
        global_frame = frame(global_partition, order, seconds)
        own_frame = frame(own_partition, order, seconds)
        for prefix, win in (("all", global_frame), ("own", own_frame)):
            raw_expr.extend(
                [
                    f"SUM(one_trade_change) OVER ({win}) AS {prefix}_sum_{suffix}",
                    f"SUM(POWER(one_trade_change,2)) OVER ({win}) AS {prefix}_sq_{suffix}",
                    f"COUNT(one_trade_change) OVER ({win}) AS {prefix}_count_{suffix}",
                    f"COUNT(*) OVER ({win}) AS {prefix}_activity_{suffix}",
                    f"SUM(CASE WHEN one_trade_change>0 THEN 1 ELSE 0 END) OVER ({win}) AS {prefix}_up_{suffix}",
                    f"SUM(CASE WHEN one_trade_change<0 THEN 1 ELSE 0 END) OVER ({win}) AS {prefix}_down_{suffix}",
                ]
            )
        count_expr = f"all_count_{suffix}-own_count_{suffix}"
        sum_expr = f"all_sum_{suffix}-own_sum_{suffix}"
        sq_expr = f"all_sq_{suffix}-own_sq_{suffix}"
        activity_expr = f"all_activity_{suffix}-own_activity_{suffix}"
        up_expr = f"all_up_{suffix}-own_up_{suffix}"
        down_expr = f"all_down_{suffix}-own_down_{suffix}"
        final_expr.extend(
            [
                f"{safe_ratio(sum_expr,count_expr)} AS loo_{scope}_price_movement_{suffix}",
                f"{activity_expr} AS loo_{scope}_activity_{suffix}",
                f"{loo_dispersion(sum_expr,sq_expr,count_expr)} AS loo_{scope}_dispersion_{suffix}",
                f"{safe_ratio(up_expr,count_expr)} AS loo_{scope}_fraction_up_{suffix}",
                f"{safe_ratio(down_expr,count_expr)} AS loo_{scope}_fraction_down_{suffix}",
            ]
        )
    if scope == "event":
        final_expr.append(
            "CASE WHEN (all_count_30-own_count_30)>0 "
            "THEN (all_sum_30-own_sum_30)/(all_count_30-own_count_30) "
            "ELSE NULL END AS common_event_momentum_30"
        )
    raw = ",\n              ".join(raw_expr)
    final = ",\n            ".join(final_expr)
    dst = qpath(output)
    con.execute(
        f"""
        COPY (
          WITH w AS (
            SELECT row_id, {raw}
            FROM read_parquet('{src}')
          )
          SELECT row_id, {final}
          FROM w
          ORDER BY row_id
        )
        TO '{dst}' (FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE 100000)
        """
    )
    con.close()


def build_targets(base: Path, output: Path) -> None:
    con = connect()
    src = qpath(base)
    raw_expr: list[str] = []
    final_expr: list[str] = []
    for seconds in CLOCK_HORIZONS:
        suffix = str(seconds)
        future = frame("condition_id", "market_order_us", seconds, future=True)
        raw_expr.extend(
            [
                f"COUNT(*) OVER ({future}) AS future_count_{suffix}",
                f"LAST_VALUE(p_yes) OVER ({future}) AS future_price_{suffix}",
                f"LAST_VALUE(logit_price) OVER ({future}) AS future_logit_{suffix}",
                f"MAX(timestamp) OVER ({future}) AS clock_label_end_{suffix}",
                f"SQRT(SUM(POWER(one_trade_change,2)) OVER ({future})) AS future_realised_{suffix}",
            ]
        )
        final_expr.extend(
            [
                f"CASE WHEN future_count_{suffix}>0 THEN future_price_{suffix}-p_yes END AS target_clock_price_change_{suffix}",
                f"CASE WHEN future_count_{suffix}>0 THEN future_logit_{suffix}-logit_price END AS target_clock_logit_change_{suffix}",
                f"CASE WHEN future_count_{suffix}>0 THEN SIGN(future_price_{suffix}-p_yes) END AS target_clock_sign_{suffix}",
                f"CASE WHEN future_count_{suffix}>0 THEN ABS(future_price_{suffix}-p_yes) END AS target_clock_absolute_change_{suffix}",
                f"CASE WHEN future_count_{suffix}>0 THEN future_realised_{suffix} END AS target_clock_realised_movement_{suffix}",
                f"clock_label_end_{suffix}",
            ]
        )
    for count in EVENT_HORIZONS:
        raw_expr.extend(
            [
                f"LEAD(p_yes,{count}) OVER (PARTITION BY condition_id ORDER BY market_order_us) AS event_future_price_{count}",
                f"LEAD(timestamp,{count}) OVER (PARTITION BY condition_id ORDER BY market_order_us) AS event_label_end_{count}",
            ]
        )
        final_expr.extend(
            [
                f"event_future_price_{count}-p_yes AS target_event_price_change_{count}",
                f"SIGN(event_future_price_{count}-p_yes) AS target_event_sign_{count}",
                f"ABS(event_future_price_{count}-p_yes) AS target_event_absolute_change_{count}",
                f"event_label_end_{count}",
            ]
        )
    raw = ",\n              ".join(raw_expr)
    final = ",\n            ".join(final_expr)
    dst = qpath(output)
    con.execute(
        f"""
        COPY (
          WITH w AS (
            SELECT row_id, p_yes, logit_price, {raw}
            FROM read_parquet('{src}')
          )
          SELECT row_id, {final}
          FROM w
          ORDER BY row_id
        )
        TO '{dst}' (FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE 100000)
        """
    )
    con.close()


def validate_alignment(paths: list[Path]) -> int:
    con = connect()
    expected: tuple[int, int, int] | None = None
    for path in paths:
        source = qpath(path)
        row = con.execute(
            f"SELECT COUNT(*), MIN(row_id), MAX(row_id) FROM read_parquet('{source}')"
        ).fetchone()
        triple = (int(row[0]), int(row[1]), int(row[2]))
        if expected is None:
            expected = triple
        elif triple != expected:
            raise RuntimeError(
                f"row alignment mismatch {path.name}: {triple} != {expected}"
            )
    con.close()
    if expected is None:
        raise RuntimeError("no alignment paths")
    return expected[0]


def build_final(
    base: Path,
    price: Path,
    activity: Path,
    family_context: Path,
    event_context: Path,
    targets: Path,
    output: Path,
) -> None:
    con = connect()
    qb = qpath(base)
    qp = qpath(price)
    qa = qpath(activity)
    qf = qpath(family_context)
    qe = qpath(event_context)
    qt = qpath(targets)
    dst = qpath(output)
    con.execute(
        f"""
        COPY (
          SELECT
            b.family, b.event_id, b.market_id, b.condition_id, b.timestamp,
            b.tx_hash, b.log_index, b.market_order_us,
            b.raw_split, b.train_end_timestamp, b.dev_end_timestamp,
            b.p_yes AS price_yes,
            b.logit_price,
            b.return_streak,
            ABS(b.previous_one_trade_change) AS previous_jump_size,
            p.* EXCLUDE (row_id),
            a.* EXCLUDE (row_id),
            fc.* EXCLUDE (row_id),
            ec.* EXCLUDE (row_id),
            p.price_change_30-ec.loo_event_price_movement_30
              AS market_minus_event_momentum_30,
            t.* EXCLUDE (row_id)
          FROM read_parquet('{qb}') AS b
          POSITIONAL JOIN read_parquet('{qp}') AS p
          POSITIONAL JOIN read_parquet('{qa}') AS a
          POSITIONAL JOIN read_parquet('{qf}') AS fc
          POSITIONAL JOIN read_parquet('{qe}') AS ec
          POSITIONAL JOIN read_parquet('{qt}') AS t
        )
        TO '{dst}' (FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE 100000)
        """
    )
    con.close()


def main() -> None:
    source = locate_source()
    base = OUT / f"_base_{FAMILY}.parquet"
    price = OUT / f"_price_{FAMILY}.parquet"
    activity = OUT / f"_activity_{FAMILY}.parquet"
    family_context = OUT / f"_family_context_{FAMILY}.parquet"
    event_context = OUT / f"_event_context_{FAMILY}.parquet"
    targets = OUT / f"_targets_{FAMILY}.parquet"
    output = OUT / f"feature_target_{FAMILY}.parquet"

    print(f"BASE {FAMILY}", flush=True)
    train_end, dev_end = build_base(source, base)
    print(f"PRICE {FAMILY}", flush=True)
    build_price(base, price)
    print(f"ACTIVITY {FAMILY}", flush=True)
    build_activity(base, activity, train_end)
    print(f"FAMILY_CONTEXT {FAMILY}", flush=True)
    build_context(base, family_context, "family")
    print(f"EVENT_CONTEXT {FAMILY}", flush=True)
    build_context(base, event_context, "event")
    print(f"TARGETS {FAMILY}", flush=True)
    build_targets(base, targets)

    aligned_rows = validate_alignment(
        [base, price, activity, family_context, event_context, targets]
    )
    print(f"MERGE {FAMILY}", flush=True)
    build_final(
        base,
        price,
        activity,
        family_context,
        event_context,
        targets,
        output,
    )

    con = connect()
    q = qpath(output)
    row_count = int(
        con.execute(f"SELECT COUNT(*) FROM read_parquet('{q}')").fetchone()[0]
    )
    split_counts = dict(
        con.execute(
            f"SELECT raw_split, COUNT(*) FROM read_parquet('{q}') GROUP BY raw_split"
        ).fetchall()
    )
    schema = con.execute(
        f"DESCRIBE SELECT * FROM read_parquet('{q}')"
    ).fetchall()
    con.close()

    target_columns = [
        row[0] for row in schema if row[0].startswith("target_")
    ]
    excluded = {
        "family", "event_id", "market_id", "condition_id", "timestamp",
        "tx_hash", "log_index", "market_order_us", "raw_split",
        "train_end_timestamp", "dev_end_timestamp",
    }
    feature_columns = [
        row[0]
        for row in schema
        if row[0] not in excluded
        and not row[0].startswith("target_")
        and not row[0].startswith("clock_label_end_")
        and not row[0].startswith("event_label_end_")
    ]
    if aligned_rows != row_count:
        raise RuntimeError(
            f"final row count mismatch {row_count} != {aligned_rows}"
        )
    if len(feature_columns) != 226:
        raise RuntimeError(
            f"feature count mismatch {len(feature_columns)} != 226"
        )
    if len(target_columns) != 47:
        raise RuntimeError(
            f"target count mismatch {len(target_columns)} != 47"
        )

    report = {
        "schema_version": 1,
        "experiment_id": "EXPERIMENT-005B",
        "stage": "features_targets",
        "family": FAMILY,
        "feature_target_spec_sha256": FEATURE_TARGET_SPEC_SHA256,
        "feature_target_spec_repo_path": FEATURE_TARGET_SPEC_REPO_PATH,
        "execution_plan": (
            "memory_bounded_category_passes_then_positional_merge"
        ),
        "memory_limit": "6GB",
        "rows": row_count,
        "split_counts": split_counts,
        "train_end_timestamp": train_end,
        "dev_end_timestamp": dev_end,
        "feature_count": len(feature_columns),
        "target_count": len(target_columns),
        "feature_columns": feature_columns,
        "target_columns": target_columns,
        "source_path": str(source),
        "source_sha256": sha256(source),
        "output_path": output.name,
        "output_bytes": output.stat().st_size,
        "output_sha256": sha256(output),
    }
    report_path = OUT / f"feature_target_build_report_{FAMILY}.json"
    report_path.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n"
    )

    for intermediate in (
        base,
        price,
        activity,
        family_context,
        event_context,
        targets,
    ):
        intermediate.unlink(missing_ok=True)

    print(
        json.dumps(
            {
                "family": FAMILY,
                "rows": row_count,
                "features": len(feature_columns),
                "targets": len(target_columns),
                "bytes": output.stat().st_size,
            },
            sort_keys=True,
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
