# ruff: noqa: E501
"""Fresh DATA-003 confirmation for the seven 005B realised-movement horizons.

No search, tuning, candidate selection or event-time transfer is performed here.
The seven model specifications are copied from the finalized ordering-falsification
parent and are refit only on the corrected historical 005B TRAIN+DEV sample.
DATA-003 is used once as a disjoint fresh confirmation population.
"""

from __future__ import annotations

import asyncio
import json
import math
import zipfile
from pathlib import Path
from typing import Any

import aiohttp
import duckdb
import numpy as np
import pandas as pd
import pyarrow.parquet as pq
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LinearRegression, Ridge
from sklearn.metrics import mean_absolute_error, mean_squared_error
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

OUT = Path("/kaggle/working/005b_data003_transfer")
OUT.mkdir(parents=True, exist_ok=True)
TMP = OUT / "tmp"
TMP.mkdir(parents=True, exist_ok=True)

PARENT_COMMIT = "3bdf0393e2791f3fbbfa8bceb0740c3905b92fa8"
PARENT_FREEZE_SHA256 = "44025aa2c5e5bddd97d88c81e649d86d6bf55d6cccec1d57c2aa7af12a9a7ec6"
PARENT_HOLDOUT_SHA256 = "94db3e2319d376078e9f80f5e28d04215dcade53d76c1ec749af8bcc0fa8972a"
CARRY_SPEC_SHA256 = "1973b32871ded25c40ea7dea1e8ceaa00443f7e1a5dcbabf45232bd197539c2e"
SEED = 505005
BOOT = 1000
EMBARGO = 300
EPS = 1e-6
WINDOWS = (5, 15, 30, 60, 120, 300, 900)
HORIZONS = (1, 5, 15, 30, 60, 120, 300)
FAMILIES = ("US_2024", "CAN_2025", "COL_2026", "HUN_2026", "PER_2026")
BASELINES = ("persistence", "own_recent_price", "current_absolute_movement", "recent_activity")
RPC_ENDPOINTS = (
    "https://tenderly.rpc.polygon.community/",
    "https://polygon.drpc.org",
)

MODELS: dict[str, dict[str, Any]] = {
    "target_clock_realised_movement_1": {
        "features": ["realised_vol_5", "absolute_return_5", "recent_max_move_900", "absolute_return_30", "absolute_return_15", "absolute_return_60", "absolute_return_120", "absolute_return_300"],
        "name": "hist_gradient_boosting",
        "params": {"l2_regularization": 1.0, "learning_rate": 0.05, "max_iter": 100, "max_leaf_nodes": 15},
    },
    "target_clock_realised_movement_5": {
        "features": ["realised_vol_5", "recent_max_move_900", "absolute_return_5", "absolute_return_30", "absolute_return_60", "absolute_return_15", "absolute_return_120", "absolute_return_300"],
        "name": "ridge",
        "params": {"alpha": 10.0},
    },
    "target_clock_realised_movement_15": {
        "features": ["realised_vol_5", "recent_max_move_900", "absolute_return_300", "absolute_return_60", "absolute_return_120", "absolute_return_30", "absolute_return_15", "absolute_return_900"],
        "name": "hist_gradient_boosting",
        "params": {"l2_regularization": 1.0, "learning_rate": 0.05, "max_iter": 100, "max_leaf_nodes": 15},
    },
    "target_clock_realised_movement_30": {
        "features": ["recent_max_move_900", "realised_vol_5", "absolute_return_300", "absolute_return_120", "absolute_return_60", "absolute_return_900", "absolute_return_30", "absolute_return_15"],
        "name": "hist_gradient_boosting",
        "params": {"l2_regularization": 1.0, "learning_rate": 0.05, "max_iter": 100, "max_leaf_nodes": 15},
    },
    "target_clock_realised_movement_60": {
        "features": ["recent_max_move_900", "absolute_return_300", "realised_vol_5", "absolute_return_900", "absolute_return_120", "absolute_return_60", "absolute_return_30", "absolute_return_15"],
        "name": "hist_gradient_boosting",
        "params": {"l2_regularization": 1.0, "learning_rate": 0.05, "max_iter": 100, "max_leaf_nodes": 15},
    },
    "target_clock_realised_movement_120": {
        "features": ["recent_max_move_900", "absolute_return_300", "absolute_return_900", "absolute_return_120", "realised_vol_5", "absolute_return_60", "distance_recent_low_300", "absolute_return_30"],
        "name": "hist_gradient_boosting",
        "params": {"l2_regularization": 1.0, "learning_rate": 0.05, "max_iter": 100, "max_leaf_nodes": 15},
    },
    "target_clock_realised_movement_300": {
        "features": ["recent_max_move_900", "absolute_return_900", "absolute_return_300", "absolute_return_120", "absolute_return_60", "realised_vol_5", "distance_recent_low_300", "absolute_return_30"],
        "name": "hist_gradient_boosting",
        "params": {"l2_regularization": 1.0, "learning_rate": 0.05, "max_iter": 100, "max_leaf_nodes": 15},
    },
}


def qpath(path: Path) -> str:
    return str(path).replace("'", "''")


def connect() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    con.execute("PRAGMA threads=4")
    con.execute("PRAGMA preserve_insertion_order=false")
    escaped = qpath(TMP)
    con.execute(f"PRAGMA temp_directory='{escaped}'")
    return con


def locate_data003_zip() -> Path:
    matches = sorted(Path("/kaggle/input").rglob("fills.zip"))
    if len(matches) != 1:
        raise RuntimeError(f"expected one DATA-003 fills.zip, found {matches}")
    return matches[0]


def extract_fills() -> Path:
    source = locate_data003_zip()
    root = OUT / "data003_fills"
    if root.exists() and any(root.rglob("*.parquet")):
        return root
    root.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(source) as archive:
        archive.extractall(root)
    if not any(root.rglob("*.parquet")):
        raise RuntimeError("DATA-003 fills.zip contained no parquet files")
    return root


def fill_glob(root: Path) -> str:
    return qpath(root / "**" / "*.parquet")


def schema_columns(con: duckdb.DuckDBPyConnection, glob: str) -> set[str]:
    rows = con.execute(
        f"DESCRIBE SELECT * FROM read_parquet('{glob}', union_by_name=true, hive_partitioning=true)"
    ).fetchall()
    return {str(row[0]) for row in rows}


def choose_column(columns: set[str], names: tuple[str, ...], label: str) -> str:
    for name in names:
        if name in columns:
            return name
    raise RuntimeError(f"DATA-003 missing {label}; available={sorted(columns)}")


def reconstruct_economic_fills(root: Path) -> tuple[Path, dict[str, Any]]:
    con = connect()
    glob = fill_glob(root)
    columns = schema_columns(con, glob)
    outcome = choose_column(columns, ("outcome", "outcome_side"), "YES/NO outcome column")
    active = choose_column(columns, ("order_is_match_taker_order",), "active-order evidence")
    sig_market = choose_column(columns, ("sig_market_id",), "SIG market id")
    mapping_class = choose_column(columns, ("mapping_class",), "mapping class")
    window_id = choose_column(columns, ("window_id",), "window id")

    con.execute(
        f"""
        CREATE TEMP VIEW src AS
        SELECT
          CAST(timestamp AS BIGINT) AS timestamp,
          CAST(tx_hash AS VARCHAR) AS tx_hash,
          CAST(log_index AS BIGINT) AS log_index,
          CAST(condition_id AS VARCHAR) AS condition_id,
          CAST({sig_market} AS VARCHAR) AS sig_market_id,
          CAST({mapping_class} AS VARCHAR) AS mapping_class,
          CAST({window_id} AS VARCHAR) AS window_id,
          UPPER(CAST({outcome} AS VARCHAR)) AS outcome_side,
          CAST({active} AS BOOLEAN) AS is_active,
          CAST(price AS DOUBLE) AS price,
          CAST(size_shares AS DOUBLE) AS size_shares,
          CAST(value_usd AS DOUBLE) AS value_usd
        FROM read_parquet('{glob}', union_by_name=true, hive_partitioning=true)
        """
    )
    input_rows = int(con.execute("SELECT COUNT(*) FROM src").fetchone()[0])
    con.execute(
        """
        CREATE TEMP TABLE group_audit AS
        SELECT
          condition_id, tx_hash,
          COUNT(*) AS row_count,
          SUM(CASE WHEN is_active THEN 1 ELSE 0 END) AS active_count,
          SUM(CASE WHEN NOT is_active THEN 1 ELSE 0 END) AS passive_count,
          BOOL_AND(outcome_side IN ('YES','NO')) AS all_binary,
          MAX(CASE WHEN is_active THEN size_shares END) AS active_size,
          SUM(CASE WHEN NOT is_active THEN size_shares ELSE 0 END) AS passive_size,
          MAX(CASE WHEN is_active THEN (CASE WHEN outcome_side='YES' THEN price ELSE 1-price END)*size_shares END) AS active_yes_notional,
          SUM(CASE WHEN NOT is_active THEN (CASE WHEN outcome_side='YES' THEN price ELSE 1-price END)*size_shares ELSE 0 END) AS passive_yes_notional
        FROM src
        GROUP BY condition_id, tx_hash
        """
    )
    con.execute(
        """
        CREATE TEMP VIEW accepted AS
        SELECT condition_id, tx_hash
        FROM group_audit
        WHERE active_count=1
          AND passive_count>=1
          AND all_binary
          AND ABS(active_size-passive_size) <= 1e-8*GREATEST(1.0,ABS(active_size),ABS(passive_size))
          AND ABS(active_yes_notional-passive_yes_notional) <= 1e-3*GREATEST(1.0,ABS(active_yes_notional),ABS(passive_yes_notional))
        """
    )
    out = OUT / "data003_economic_preblock.parquet"
    con.execute(
        f"""
        COPY (
          SELECT
            s.timestamp, s.tx_hash, s.log_index, s.condition_id,
            s.sig_market_id, s.mapping_class, s.window_id,
            CASE WHEN s.outcome_side='YES' THEN s.price ELSE 1-s.price END AS p_yes,
            s.size_shares, s.value_usd
          FROM src AS s
          INNER JOIN accepted AS a USING(condition_id,tx_hash)
          WHERE NOT s.is_active
          ORDER BY timestamp, log_index, condition_id
        ) TO '{qpath(out)}' (FORMAT PARQUET, COMPRESSION ZSTD)
        """
    )
    groups = int(con.execute("SELECT COUNT(*) FROM group_audit").fetchone()[0])
    accepted = int(con.execute("SELECT COUNT(*) FROM accepted").fetchone()[0])
    rows = int(con.execute(f"SELECT COUNT(*) FROM read_parquet('{qpath(out)}')").fetchone()[0])
    con.close()
    return out, {
        "participant_rows": input_rows,
        "transaction_condition_groups": groups,
        "accepted_groups": accepted,
        "rejected_groups": groups-accepted,
        "economic_rows": rows,
    }


async def rpc_batch(session: aiohttp.ClientSession, endpoint: str, calls: list[tuple[int, str, list[Any]]]) -> dict[int, Any]:
    payload = [{"jsonrpc": "2.0", "id": ident, "method": method, "params": params} for ident, method, params in calls]
    for attempt in range(7):
        try:
            async with session.post(endpoint, json=payload, timeout=aiohttp.ClientTimeout(total=45)) as response:
                data = await response.json(content_type=None)
                if response.status == 200 and isinstance(data, list):
                    return {int(row["id"]): row.get("result") for row in data if isinstance(row, dict) and "id" in row}
        except (aiohttp.ClientError, TimeoutError, json.JSONDecodeError):
            pass
        await asyncio.sleep(min(8.0, 0.35*(2**attempt)))
    return {}


async def map_values(items: list[str], method: str, parse: Any) -> dict[str, Any]:
    remaining = list(dict.fromkeys(items))
    resolved: dict[str, Any] = {}
    for endpoint in RPC_ENDPOINTS:
        if not remaining:
            break
        connector = aiohttp.TCPConnector(limit=40)
        async with aiohttp.ClientSession(connector=connector) as session:
            semaphore = asyncio.Semaphore(32)

            async def work(batch_no: int, batch: list[str]) -> dict[str, Any]:
                calls = [(batch_no*100+i, method, [value]) for i, value in enumerate(batch)]
                async with semaphore:
                    reply = await rpc_batch(session, endpoint, calls)
                out: dict[str, Any] = {}
                for i, value in enumerate(batch):
                    result = reply.get(batch_no*100+i)
                    parsed = parse(result)
                    if parsed is not None:
                        out[value] = parsed
                return out

            batches = [remaining[i:i+10] for i in range(0, len(remaining), 10)]
            tasks = [asyncio.create_task(work(i, batch)) for i, batch in enumerate(batches)]
            for i, task in enumerate(asyncio.as_completed(tasks), start=1):
                resolved.update(await task)
                if i % 500 == 0:
                    print(f"RPC {method} {endpoint} batches={i}/{len(batches)} resolved={len(resolved)}", flush=True)
        remaining = [value for value in remaining if value not in resolved]
    if remaining:
        raise RuntimeError(f"{method}: unresolved values={len(remaining)} sample={remaining[:5]}")
    return resolved


def parse_tx(result: Any) -> int | None:
    if not isinstance(result, dict) or not result.get("blockNumber"):
        return None
    return int(str(result["blockNumber"]), 16)


def parse_block(result: Any) -> int | None:
    if not isinstance(result, dict) or not result.get("timestamp"):
        return None
    return int(str(result["timestamp"]), 16)


def enrich_blocks(preblock: Path) -> tuple[Path, dict[str, Any]]:
    con = connect()
    q = qpath(preblock)
    txs = [str(row[0]) for row in con.execute(f"SELECT DISTINCT tx_hash FROM read_parquet('{q}') ORDER BY tx_hash").fetchall()]
    con.close()
    tx_to_block = asyncio.run(map_values(txs, "eth_getTransactionByHash", parse_tx))
    blocks = sorted(set(tx_to_block.values()))
    block_keys = [hex(block) for block in blocks]
    block_to_ts_hex = asyncio.run(map_values(block_keys, "eth_getBlockByNumber", parse_block))
    block_to_ts = {int(key, 16): value for key, value in block_to_ts_hex.items()}

    tx_frame = pd.DataFrame({"tx_hash": list(tx_to_block), "block_number": [tx_to_block[x] for x in tx_to_block]})
    block_frame = pd.DataFrame({"block_number": list(block_to_ts), "block_timestamp": [block_to_ts[x] for x in block_to_ts]})
    con = connect()
    con.register("tx_map", tx_frame)
    con.register("block_map", block_frame)
    out = OUT / "data003_economic_blocked.parquet"
    con.execute(
        f"""
        COPY (
          SELECT p.*, t.block_number, b.block_timestamp
          FROM read_parquet('{q}') AS p
          LEFT JOIN tx_map AS t USING(tx_hash)
          LEFT JOIN block_map AS b USING(block_number)
          ORDER BY block_number, log_index, condition_id
        ) TO '{qpath(out)}' (FORMAT PARQUET, COMPRESSION ZSTD)
        """
    )
    audit = con.execute(
        f"""
        SELECT
          COUNT(*) AS rows,
          COUNT(*) FILTER (WHERE block_number IS NULL) AS missing_blocks,
          COUNT(*) FILTER (WHERE block_timestamp IS NULL) AS missing_timestamps,
          COUNT(*) FILTER (WHERE timestamp<>block_timestamp) AS timestamp_mismatches
        FROM read_parquet('{qpath(out)}')
        """
    ).fetchone()
    dupes = int(
        con.execute(
            f"""
            SELECT COUNT(*) FROM (
              SELECT block_number,log_index
              FROM read_parquet('{qpath(out)}')
              GROUP BY 1,2 HAVING COUNT(*)>1
            )
            """
        ).fetchone()[0]
    )
    con.close()
    if any(int(value) != 0 for value in audit[1:]) or dupes:
        raise RuntimeError(f"DATA-003 block gate failed audit={audit} duplicate_block_log_groups={dupes}")
    return out, {
        "economic_rows": int(audit[0]),
        "tx_count": len(txs),
        "block_count": len(blocks),
        "missing_block_numbers": int(audit[1]),
        "missing_block_timestamps": int(audit[2]),
        "timestamp_mismatches": int(audit[3]),
        "duplicate_block_log_groups": dupes,
        "passed": True,
    }


def frame(seconds: int, future: bool = False) -> str:
    span = seconds*1_000_000
    bounds = f"RANGE BETWEEN 1 FOLLOWING AND {span} FOLLOWING" if future else f"RANGE BETWEEN {span} PRECEDING AND CURRENT ROW"
    return f"PARTITION BY condition_id ORDER BY market_order_us {bounds}"


def build_confirmation_matrix(blocked: Path) -> Path:
    con = connect()
    q = qpath(blocked)
    base = OUT / "data003_base.parquet"
    con.execute(
        f"""
        COPY (
          WITH raw AS (
            SELECT *,
              ROW_NUMBER() OVER (ORDER BY block_number,log_index,condition_id) AS row_id,
              CAST(timestamp AS BIGINT)*1000000
                + ROW_NUMBER() OVER (
                    PARTITION BY condition_id,timestamp
                    ORDER BY block_number,log_index
                  ) - 1 AS market_order_us
            FROM read_parquet('{q}')
          ),
          lagged AS (
            SELECT *,
              LAG(p_yes) OVER (PARTITION BY condition_id ORDER BY market_order_us) AS previous_price
            FROM raw
          )
          SELECT *,
            p_yes-previous_price AS one_trade_change
          FROM lagged
          ORDER BY row_id
        ) TO '{qpath(base)}' (FORMAT PARQUET, COMPRESSION ZSTD)
        """
    )

    expressions: list[str] = []
    for seconds in WINDOWS:
        own = frame(seconds)
        expressions.extend([
            f"p_yes-FIRST_VALUE(p_yes) OVER ({own}) AS price_change_{seconds}",
            f"SQRT(SUM(POWER(one_trade_change,2)) OVER ({own})) AS realised_vol_{seconds}",
            f"ABS(p_yes-FIRST_VALUE(p_yes) OVER ({own})) AS absolute_return_{seconds}",
            f"MAX(ABS(one_trade_change)) OVER ({own}) AS recent_max_move_{seconds}",
        ])
    expressions.extend([
        "p_yes-MIN(p_yes) OVER (PARTITION BY condition_id ORDER BY market_order_us RANGE BETWEEN 300000000 PRECEDING AND CURRENT ROW) AS distance_recent_low_300",
        f"COUNT(*) OVER ({frame(30)}) AS trade_count_30",
    ])
    for seconds in HORIZONS:
        future = frame(seconds, future=True)
        expressions.extend([
            f"COUNT(*) OVER ({future}) AS future_count_{seconds}",
            f"SQRT(SUM(POWER(one_trade_change,2)) OVER ({future})) AS future_realised_{seconds}",
            f"MAX(timestamp) OVER ({future}) AS clock_label_end_{seconds}",
        ])
    expr = ",\n              ".join(expressions)
    matrix = OUT / "data003_confirmation_matrix.parquet"
    target_expr = ",\n            ".join(
        f"CASE WHEN future_count_{h}>0 THEN future_realised_{h} END AS target_clock_realised_movement_{h}"
        for h in HORIZONS
    )
    con.execute(
        f"""
        COPY (
          WITH w AS (
            SELECT
              row_id,condition_id,sig_market_id,mapping_class,window_id,
              timestamp,tx_hash,log_index,block_number,p_yes,
              {expr}
            FROM read_parquet('{qpath(base)}')
          )
          SELECT
            *,
            {target_expr}
          FROM w
          ORDER BY row_id
        ) TO '{qpath(matrix)}' (FORMAT PARQUET, COMPRESSION ZSTD)
        """
    )
    con.close()
    return matrix


def locate_historical(family: str) -> Path:
    matches = sorted(Path("/kaggle/input").rglob(f"feature_target_{family}.parquet"))
    if len(matches) != 1:
        raise RuntimeError(f"expected one corrected historical matrix for {family}, found {matches}")
    return matches[0]


def load_historical(target: str, features: list[str]) -> pd.DataFrame:
    parts = []
    label = f"clock_label_end_{target.rsplit('_',1)[1]}"
    needed = sorted(set(features) | {target, label, "price_change_30", "absolute_return_30", "trade_count_30"})
    cols = ",".join(needed)
    for family in FAMILIES:
        path = locate_historical(family)
        con = duckdb.connect()
        q = qpath(path)
        parts.append(
            con.execute(
                f"""
                SELECT {cols}
                FROM read_parquet('{q}')
                WHERE timestamp < dev_end_timestamp-{EMBARGO}
                  AND {target} IS NOT NULL
                  AND {label} IS NOT NULL
                  AND {label} < dev_end_timestamp
                ORDER BY hash(condition_id,tx_hash,log_index)
                LIMIT 100000
                """
            ).df()
        )
        con.close()
    return pd.concat(parts, ignore_index=True)


def matrix(df: pd.DataFrame, features: list[str]) -> np.ndarray:
    return df[features].apply(pd.to_numeric, errors="coerce").to_numpy(dtype=np.float64, copy=False)


def regression_model(name: str, params: dict[str, Any]) -> Any:
    if name == "ridge":
        return make_pipeline(SimpleImputer(strategy="median"), StandardScaler(), Ridge(alpha=float(params["alpha"])))
    if name == "hist_gradient_boosting":
        return make_pipeline(
            SimpleImputer(strategy="median"),
            HistGradientBoostingRegressor(
                learning_rate=float(params["learning_rate"]),
                max_iter=int(params["max_iter"]),
                max_leaf_nodes=int(params["max_leaf_nodes"]),
                l2_regularization=float(params["l2_regularization"]),
                random_state=SEED,
            ),
        )
    raise RuntimeError(f"unexpected frozen model {name}")


def baseline_predictions(train: pd.DataFrame, test: pd.DataFrame, target: str) -> dict[str, np.ndarray]:
    y = pd.to_numeric(train[target], errors="coerce").to_numpy(dtype=float)
    valid = np.isfinite(y)
    out = {"persistence": np.full(len(test), float(np.nanmean(y)))}
    for name, feature in (
        ("own_recent_price", "price_change_30"),
        ("current_absolute_movement", "absolute_return_30"),
        ("recent_activity", "trade_count_30"),
    ):
        model = make_pipeline(SimpleImputer(strategy="median"), StandardScaler(), LinearRegression())
        model.fit(matrix(train.loc[valid].reset_index(drop=True), [feature]), y[valid])
        out[name] = model.predict(matrix(test, [feature]))
    return out


def pearson(x: np.ndarray, y: np.ndarray) -> float:
    valid = np.isfinite(x) & np.isfinite(y)
    if valid.sum() < 3 or np.std(x[valid]) <= 0 or np.std(y[valid]) <= 0:
        return math.nan
    return float(np.corrcoef(x[valid], y[valid])[0,1])


def market_bootstrap(frame: pd.DataFrame) -> dict[str, float]:
    market = frame.groupby("sig_market_id", as_index=False)["diff"].mean()
    values = market["diff"].to_numpy(dtype=float)
    rng = np.random.default_rng(SEED)
    reps = [float(np.mean(values[rng.integers(0, len(values), size=len(values))])) for _ in range(BOOT)]
    return {
        "mean": float(np.mean(reps)),
        "lower_2_5": float(np.quantile(reps,0.025)),
        "upper_97_5": float(np.quantile(reps,0.975)),
    }


def calendar_bootstrap(frame: pd.DataFrame) -> dict[str, float]:
    daily = frame.assign(day=(frame["timestamp"]//86400).astype(int)).groupby("day",as_index=False)["diff"].mean().sort_values("day")
    values = daily["diff"].to_numpy(dtype=float)
    if len(values) < 3:
        return {"mean": math.nan, "lower_2_5": math.nan, "upper_97_5": math.nan}
    rng = np.random.default_rng(SEED+1)
    reps = []
    starts = np.arange(len(values))
    for _ in range(BOOT):
        sample: list[float] = []
        while len(sample) < len(values):
            start = int(rng.choice(starts))
            sample.extend(values[(start+np.arange(3))%len(values)].tolist())
        reps.append(float(np.mean(sample[:len(values)])))
    return {
        "mean": float(np.mean(reps)),
        "lower_2_5": float(np.quantile(reps,0.025)),
        "upper_97_5": float(np.quantile(reps,0.975)),
    }


def group_diagnostics(test: pd.DataFrame, y: np.ndarray, pred: np.ndarray, persistence: np.ndarray, column: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for value in sorted(test[column].dropna().astype(str).unique()):
        mask = test[column].astype(str).to_numpy() == value
        if not mask.any():
            continue
        base = mean_absolute_error(y[mask], persistence[mask])
        cand = mean_absolute_error(y[mask], pred[mask])
        out[value] = {
            "rows": int(mask.sum()),
            "mae": float(cand),
            "persistence_mae": float(base),
            "mae_improvement_vs_persistence": float((base-cand)/base) if base else 0.0,
        }
    return out


def evaluate(matrix_path: Path, block_audit: dict[str, Any], reconstruction: dict[str, Any]) -> dict[str, Any]:
    con = duckdb.connect()
    q = qpath(matrix_path)
    test_all = con.execute(f"SELECT * FROM read_parquet('{q}')").df()
    con.close()
    results = []
    for horizon in HORIZONS:
        target = f"target_clock_realised_movement_{horizon}"
        spec = MODELS[target]
        features = list(spec["features"])
        train = load_historical(target, features)
        test = test_all.loc[pd.to_numeric(test_all[target],errors="coerce").notna()].reset_index(drop=True)
        y_train = pd.to_numeric(train[target],errors="coerce").to_numpy(dtype=float)
        valid_train = np.isfinite(y_train)
        train = train.loc[valid_train].reset_index(drop=True)
        y_train = y_train[valid_train]
        y = pd.to_numeric(test[target],errors="coerce").to_numpy(dtype=float)

        model = regression_model(str(spec["name"]), dict(spec["params"]))
        model.fit(matrix(train,features), y_train)
        pred = model.predict(matrix(test,features))
        bases = baseline_predictions(train,test,target)

        mae = float(mean_absolute_error(y,pred))
        mse = float(mean_squared_error(y,pred))
        baseline_metrics = {
            name:{
                "mae":float(mean_absolute_error(y,values)),
                "mse":float(mean_squared_error(y,values)),
                "predictive_ic":pearson(values,y),
            }
            for name,values in bases.items()
        }
        persistence = bases["persistence"]
        diff = np.abs(y-persistence)-np.abs(y-pred)
        uncertainty = test[["sig_market_id","timestamp"]].copy()
        uncertainty["diff"] = diff
        boot = market_bootstrap(uncertainty)
        cal = calendar_bootstrap(uncertainty)
        beats_all = all(mae < baseline_metrics[name]["mae"] for name in BASELINES)
        primary_pass = beats_all and boot["lower_2_5"] > 0
        results.append({
            "target":target,
            "horizon_seconds":horizon,
            "historical_fit_rows":int(len(train)),
            "data003_rows":int(len(test)),
            "data003_market_count":int(test["sig_market_id"].nunique()),
            "frozen_model":{"name":spec["name"],"params":spec["params"],"features":features},
            "metrics":{
                "mae":mae,
                "mse":mse,
                "predictive_ic":pearson(pred,y),
                "mae_improvement_vs_persistence":float((baseline_metrics["persistence"]["mae"]-mae)/baseline_metrics["persistence"]["mae"]),
            },
            "baseline_metrics":baseline_metrics,
            "beats_every_original_named_baseline":beats_all,
            "market_bootstrap":boot,
            "calendar_3day_block_bootstrap":cal,
            "mapping_class_diagnostics":group_diagnostics(test,y,pred,persistence,"mapping_class"),
            "window_diagnostics":group_diagnostics(test,y,pred,persistence,"window_id"),
            "primary_confirmation_pass":primary_pass,
        })
        print(json.dumps({"target":target,"mae":mae,"beats_all":beats_all,"bootstrap_lower":boot["lower_2_5"],"pass":primary_pass},sort_keys=True),flush=True)

    confirmed = sum(bool(row["primary_confirmation_pass"]) for row in results)
    return {
        "schema_version":1,
        "experiment_id":"EXPERIMENT-005B-DATA003-TRANSFER",
        "classification":"FRESH_CONFIRMATION",
        "parent_ordering_falsification_commit":PARENT_COMMIT,
        "parent_train_dev_freeze_sha256":PARENT_FREEZE_SHA256,
        "parent_holdout_sha256":PARENT_HOLDOUT_SHA256,
        "carry_forward_spec_sha256":CARRY_SPEC_SHA256,
        "data003_dataset_ref":"polyleviathan/sig-cup-data-003-sig-actual-fills",
        "data003_expected_rows":132928,
        "reconstruction":reconstruction,
        "block_number_gate":block_audit,
        "confirmation_rule":{
            "per_horizon":"candidate MAE below all four original named baselines AND market-bootstrap lower 2.5% > 0",
            "overall":"all seven horizons pass for full seven-horizon confirmation",
        },
        "results":results,
        "horizons_confirmed":confirmed,
        "horizon_count":len(HORIZONS),
        "full_seven_horizon_confirmation":confirmed==len(HORIZONS),
        "new_candidates_introduced":0,
    }


def render_report(payload: dict[str, Any]) -> str:
    lines = [
        "# 005B → DATA-003 Fresh Confirmation",
        "",
        "**Classification:** FRESH_CONFIRMATION",
        "",
        f"Confirmed horizons: **{payload['horizons_confirmed']} / {payload['horizon_count']}**",
        f"Full seven-horizon confirmation: **{'YES' if payload['full_seven_horizon_confirmation'] else 'NO'}**",
        "",
        "| Horizon | MAE improvement vs persistence | Predictive IC | Market bootstrap lower 2.5% | Beats all named baselines | Pass |",
        "|---:|---:|---:|---:|:---:|:---:|",
    ]
    for row in payload["results"]:
        lines.append(
            f"| {row['horizon_seconds']}s | {row['metrics']['mae_improvement_vs_persistence']:.2%} | "
            f"{row['metrics']['predictive_ic']:.3f} | {row['market_bootstrap']['lower_2_5']:.6f} | "
            f"{'Yes' if row['beats_every_original_named_baseline'] else 'No'} | "
            f"{'PASS' if row['primary_confirmation_pass'] else 'FAIL'} |"
        )
    lines.extend([
        "",
        "No feature, target, model, hyperparameter or candidate was selected from DATA-003.",
        "Mapping-class and window results are diagnostics only.",
    ])
    return "\n".join(lines)+"\n"


def main() -> None:
    root = extract_fills()
    preblock, reconstruction = reconstruct_economic_fills(root)
    blocked, block_audit = enrich_blocks(preblock)
    matrix_path = build_confirmation_matrix(blocked)
    payload = evaluate(matrix_path, block_audit, reconstruction)
    result_path = OUT/"data003_confirmation_results.json"
    result_path.write_text(json.dumps(payload,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    (OUT/"FINAL_REPORT.md").write_text(render_report(payload),encoding="utf-8")
    print(json.dumps({"horizons_confirmed":payload["horizons_confirmed"],"full_seven_horizon_confirmation":payload["full_seven_horizon_confirmation"]},sort_keys=True),flush=True)


if __name__ == "__main__":
    main()
