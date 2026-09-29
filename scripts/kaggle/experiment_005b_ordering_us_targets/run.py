# ruff: noqa: E501
"""EXPERIMENT-005B US continuation from validated Stage-2 intermediates."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import duckdb

TASK = "targets"
FAMILY = "US_2024"
SHORT_WINDOWS = (5, 15, 30, 60)
LONG_WINDOWS = (120, 300, 900)
CLOCK_HORIZONS = (1, 5, 15, 30, 60, 120, 300)
EVENT_HORIZONS = (1, 2, 5, 10)
FEATURE_TARGET_SPEC_SHA256 = "47dc9c9a678f58558aba7e4bc282103cacbc0836f08018a79d4197487d689cf7"
OUT = Path("/kaggle/working/005b_ordering_falsification/us_continuation")
OUT.mkdir(parents=True, exist_ok=True)
TMP = Path("/kaggle/working/005b_ordering_falsification/us_tmp") / TASK
TMP.mkdir(parents=True, exist_ok=True)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


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


def locate_unique(pattern: str) -> Path:
    matches = sorted(Path("/kaggle/input").rglob(pattern))
    if len(matches) != 1:
        raise RuntimeError(f"expected one {pattern}, found {matches}")
    return matches[0]


def base_path() -> Path:
    return locate_unique("_base_US_2024.parquet")


def price_path() -> Path:
    return locate_unique("_price_US_2024.parquet")


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


def file_report(path: Path) -> dict[str, object]:
    con = connect()
    source = qpath(path)
    row = con.execute(
        f"SELECT COUNT(*), MIN(row_id), MAX(row_id) FROM read_parquet('{source}')"
    ).fetchone()
    schema = con.execute(
        f"DESCRIBE SELECT * FROM read_parquet('{source}')"
    ).fetchall()
    con.close()
    return {
        "path": path.name,
        "bytes": path.stat().st_size,
        "sha256": sha256(path),
        "rows": int(row[0]),
        "min_row_id": int(row[1]),
        "max_row_id": int(row[2]),
        "columns": [item[0] for item in schema],
    }


def create_size_refs(con: duckdb.DuckDBPyConnection, source: str) -> int:
    train_end = int(
        con.execute(
            f"SELECT MAX(train_end_timestamp) FROM read_parquet('{source}')"
        ).fetchone()[0]
    )
    con.execute(
        f"""
        CREATE TEMP TABLE size_refs AS
        WITH market AS (
          SELECT condition_id, COUNT(*) AS n_train,
                 APPROX_QUANTILE(size_shares,0.90) AS market_q90
          FROM read_parquet('{source}')
          WHERE timestamp<{train_end}
          GROUP BY condition_id
        ),
        fam AS (
          SELECT APPROX_QUANTILE(size_shares,0.90) AS family_q90
          FROM read_parquet('{source}')
          WHERE timestamp<{train_end}
        )
        SELECT condition_id,
          CASE WHEN n_train>=100 THEN market_q90 ELSE family_q90 END AS size_q90_threshold
        FROM market CROSS JOIN fam
        """
    )
    return train_end


def build_activity_pass(windows: tuple[int, ...], output: Path, *, short: bool) -> None:
    base = base_path()
    con = connect()
    src = qpath(base)
    train_end = create_size_refs(con, src)
    raw_path = OUT / ("_activity_short_raw.parquet" if short else "_activity_long_raw.parquet")
    raw_dst = qpath(raw_path)
    expressions: list[str] = []
    for seconds in windows:
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
    dst = qpath(output)
    if short:
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
        con.execute(
            f"""
            COPY (
              SELECT
                a.* EXCLUDE (condition_id, timestamp, interarrival_seconds),
                a.interarrival_seconds AS last_interarrival_seconds,
                a.trade_count_60/(r.activity_ref_60+1e-9) AS activity_vs_train_history_60
              FROM read_parquet('{raw_dst}') AS a
              LEFT JOIN activity_refs AS r USING (condition_id)
              ORDER BY row_id
            )
            TO '{dst}' (FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE 100000)
            """
        )
    else:
        con.execute(
            f"""
            COPY (
              SELECT * EXCLUDE (condition_id, timestamp, interarrival_seconds)
              FROM read_parquet('{raw_dst}')
              ORDER BY row_id
            )
            TO '{dst}' (FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE 100000)
            """
        )
    con.close()
    raw_path.unlink(missing_ok=True)


def build_activity() -> list[Path]:
    short = OUT / "activity_short_US_2024.parquet"
    long = OUT / "activity_long_US_2024.parquet"
    print("ACTIVITY_SHORT", flush=True)
    build_activity_pass(SHORT_WINDOWS, short, short=True)
    print("ACTIVITY_LONG", flush=True)
    build_activity_pass(LONG_WINDOWS, long, short=False)
    return [short, long]


def build_context_pass(
    scope: str,
    windows: tuple[int, ...],
    output: Path,
    *,
    include_common: bool,
) -> None:
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
    src = qpath(base_path())
    raw_expr: list[str] = []
    final_expr: list[str] = []
    for seconds in windows:
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
    if include_common:
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


def build_context(scope: str) -> list[Path]:
    short = OUT / f"{scope}_context_short_US_2024.parquet"
    long = OUT / f"{scope}_context_long_US_2024.parquet"
    print(f"{scope.upper()}_SHORT", flush=True)
    build_context_pass(
        scope,
        SHORT_WINDOWS,
        short,
        include_common=(scope == "event"),
    )
    print(f"{scope.upper()}_LONG", flush=True)
    build_context_pass(
        scope,
        LONG_WINDOWS,
        long,
        include_common=False,
    )
    return [short, long]


def build_targets() -> list[Path]:
    con = connect()
    src = qpath(base_path())
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
    output = OUT / "targets_US_2024.parquet"
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
    return [output]


def validate_alignment(paths: list[Path]) -> int:
    expected: tuple[int, int, int] | None = None
    for path in paths:
        report = file_report(path)
        triple = (
            int(report["rows"]),
            int(report["min_row_id"]),
            int(report["max_row_id"]),
        )
        if expected is None:
            expected = triple
        elif triple != expected:
            raise RuntimeError(
                f"row alignment mismatch {path.name}: {triple} != {expected}"
            )
    if expected is None:
        raise RuntimeError("no alignment paths")
    return expected[0]


def build_merge() -> list[Path]:
    base = base_path()
    price = price_path()
    activity_short = locate_unique("activity_short_US_2024.parquet")
    activity_long = locate_unique("activity_long_US_2024.parquet")
    family_short = locate_unique("family_context_short_US_2024.parquet")
    family_long = locate_unique("family_context_long_US_2024.parquet")
    event_short = locate_unique("event_context_short_US_2024.parquet")
    event_long = locate_unique("event_context_long_US_2024.parquet")
    targets = locate_unique("targets_US_2024.parquet")
    paths = [
        base,
        price,
        activity_short,
        activity_long,
        family_short,
        family_long,
        event_short,
        event_long,
        targets,
    ]
    aligned_rows = validate_alignment(paths)
    con = connect()
    qb, qp = qpath(base), qpath(price)
    qas, qal = qpath(activity_short), qpath(activity_long)
    qfs, qfl = qpath(family_short), qpath(family_long)
    qes, qel = qpath(event_short), qpath(event_long)
    qt = qpath(targets)
    output = OUT / "feature_target_US_2024.parquet"
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
            ash.* EXCLUDE (row_id),
            alo.* EXCLUDE (row_id),
            ash.trade_count_30/30.0-alo.trade_count_300/300.0
              AS activity_acceleration_30_300,
            fsh.* EXCLUDE (row_id),
            flo.* EXCLUDE (row_id),
            esh.* EXCLUDE (row_id),
            elo.* EXCLUDE (row_id),
            p.price_change_30-esh.loo_event_price_movement_30
              AS market_minus_event_momentum_30,
            t.* EXCLUDE (row_id)
          FROM read_parquet('{qb}') AS b
          POSITIONAL JOIN read_parquet('{qp}') AS p
          POSITIONAL JOIN read_parquet('{qas}') AS ash
          POSITIONAL JOIN read_parquet('{qal}') AS alo
          POSITIONAL JOIN read_parquet('{qfs}') AS fsh
          POSITIONAL JOIN read_parquet('{qfl}') AS flo
          POSITIONAL JOIN read_parquet('{qes}') AS esh
          POSITIONAL JOIN read_parquet('{qel}') AS elo
          POSITIONAL JOIN read_parquet('{qt}') AS t
        )
        TO '{dst}' (FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE 100000)
        """
    )
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
        "execution_plan": "validated_failed_run_base_price_plus_bounded_continuations",
        "rows": row_count,
        "split_counts": split_counts,
        "train_end_timestamp": int(
            duckdb.connect().execute(
                f"SELECT MAX(train_end_timestamp) FROM read_parquet('{q}')"
            ).fetchone()[0]
        ),
        "dev_end_timestamp": int(
            duckdb.connect().execute(
                f"SELECT MAX(dev_end_timestamp) FROM read_parquet('{q}')"
            ).fetchone()[0]
        ),
        "feature_count": len(feature_columns),
        "target_count": len(target_columns),
        "feature_columns": feature_columns,
        "target_columns": target_columns,
        "output_path": output.name,
        "output_bytes": output.stat().st_size,
        "output_sha256": sha256(output),
        "validated_base_sha256": sha256(base),
        "validated_price_sha256": sha256(price),
    }
    report_path = OUT / "feature_target_build_report_US_2024.json"
    report_path.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n"
    )
    return [output, report_path]


def write_task_report(outputs: list[Path]) -> None:
    if TASK == "merge":
        return
    payload = {
        "schema_version": 1,
        "experiment_id": "EXPERIMENT-005B",
        "family": FAMILY,
        "task": TASK,
        "feature_target_spec_sha256": FEATURE_TARGET_SPEC_SHA256,
        "outputs": [file_report(path) for path in outputs],
    }
    path = OUT / f"us_continuation_{TASK}_report.json"
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def main() -> None:
    if TASK == "activity":
        outputs = build_activity()
    elif TASK == "family_context":
        outputs = build_context("family")
    elif TASK == "event_context":
        outputs = build_context("event")
    elif TASK == "targets":
        outputs = build_targets()
    elif TASK == "merge":
        outputs = build_merge()
    else:
        raise RuntimeError(f"unknown TASK {TASK}")
    write_task_report(outputs)
    print(
        json.dumps(
            {
                "task": TASK,
                "outputs": [file_report(path) for path in outputs],
            },
            sort_keys=True,
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
