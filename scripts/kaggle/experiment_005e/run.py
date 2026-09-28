"""Kaggle empirical runner for EXPERIMENT-005E Participant Ecology / Behavioural Atlas."""

# ruff: noqa: E501

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import pandas as pd
import pyarrow.parquet as pq
from sklearn.cluster import MiniBatchKMeans
from sklearn.decomposition import PCA
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler

INPUT = Path("/kaggle/input")
WORK = Path("/kaggle/working/005e_participant_ecology_atlas")
WORK.mkdir(parents=True, exist_ok=True)
FAMILIES = ("US_2024", "CAN_2025", "COL_2026", "HUN_2026", "PER_2026")
BASE_SHA = "ba938bedcf63f562be8b26c9502e828391123867"
DATA002_MANIFEST_SHA = "2d496514c9307a31d8864ac9deeb2ef2ad781fb6fabe894714cbdf00c7244f7a"
SIZE_TOL = 1e-8
YES_NOTIONAL_TOL = 1e-3
SAMPLE_TARGET = 740_000
SAMPLE_CAP = 750_000
US_MIN = 600_000
EMBARGO_SECONDS = 300
HIST_PRIOR = 20.0
PERMUTATIONS = 50
SEED = 505_005
BASELINE = (
    "current_logit",
    "ret_15",
    "ret_60",
    "ret_300",
    "econ_count_15",
    "econ_count_60",
    "econ_count_300",
    "econ_value_15",
    "econ_value_60",
    "econ_value_300",
    "log_value",
    "event_count_60_ex_market",
)
FEATURE_FAMILIES = {
    "recurrence": (
        "log_prior_fill_count",
        "log_prior_active_days",
        "log_recency_seconds",
        "log_lifetime_seconds",
        "log_recent_fill_count_300s",
        "log_recent_fill_count_1800s",
    ),
    "specialisation": (
        "log_prior_same_market_count",
        "log_unique_markets_prior",
        "market_history_share",
        "log_unique_events_prior",
        "event_history_share",
        "current_market_novelty",
        "current_event_novelty",
    ),
    "size_behaviour": (
        "prior_mean_log_value",
        "prior_std_log_value",
        "current_size_z",
        "market_size_percentile_proxy",
    ),
    "directional_behaviour": (
        "prior_pressure_balance",
        "prior_pressure_abs",
        "current_prior_direction_alignment",
    ),
    "timing_behaviour": (
        "prior_same_hour_share",
        "activity_acceleration",
    ),
    "historical_markout": (
        "hist_markout_score",
        "log_hist_markout_count",
        "hist_markout_uncertainty",
    ),
    "network_cross_market": (
        "log_market_participant_degree",
        "log_unique_markets_prior",
        "log_unique_events_prior",
        "current_market_novelty",
    ),
}
IDENTITY_HISTORY = (
    "log_prior_fill_count",
    "log_prior_active_days",
    "log_recency_seconds",
    "hist_markout_score",
    "log_hist_markout_count",
)
FINGERPRINT = tuple(dict.fromkeys(sum((list(v) for v in FEATURE_FAMILIES.values()), [])))
CLOCK_TARGETS = {
    "y_15": "y15_end",
    "y_60": "y60_end",
    "y_300": "y300_end",
    "signed_60": "y60_end",
    "signed_300": "y300_end",
    "abs_60": "y60_end",
    "activity_60": "activity60_end",
    "event_2": "event2_end",
    "event_10": "event10_end",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_json_sha(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def locate_unique(name: str) -> Path:
    matches = sorted(INPUT.rglob(name))
    if len(matches) != 1:
        raise RuntimeError(f"expected exactly one {name}, got {matches}")
    return matches[0]


def load_bundle() -> tuple[Path, dict[str, Any], dict[str, Any]]:
    manifest_path = locate_unique("005e_code_manifest.json")
    root = manifest_path.parent
    manifest = json.loads(manifest_path.read_text())
    if manifest["runner_sha256"] != sha256(Path(__file__)):
        raise RuntimeError("005E runner hash mismatch")
    for relative, expected in manifest["files"].items():
        path = root / relative
        if not path.exists() or sha256(path) != expected:
            raise RuntimeError(f"005E bundle hash mismatch: {relative}")
    sys.path.insert(0, str(root / "predictions_cup_005e.bundle"))
    config = json.loads((root / "run_config.json").read_text())
    return root, manifest, config


def locate_data002(code_root: Path) -> tuple[Path, dict[str, Any]]:
    candidates = [p for p in INPUT.rglob("data_002_manifest.json") if code_root not in p.parents]
    if len(candidates) != 1:
        raise RuntimeError(f"expected one DATA-002 manifest, got {candidates}")
    actual = candidates[0]
    if sha256(actual) != DATA002_MANIFEST_SHA:
        raise RuntimeError("DATA-002 manifest SHA mismatch")
    payload = json.loads(actual.read_text())
    expected = json.loads((code_root / "data_002_manifest_expected.json").read_text())
    if payload != expected:
        raise RuntimeError("DATA-002 manifest semantic mismatch")
    return actual.parent, payload


def family_source(
    data_root: Path, manifest: dict[str, Any], family: str
) -> tuple[Path, dict[str, Any]]:
    record = next(
        row
        for row in manifest["files"]
        if row.get("kind") == "matched_fills" and f"fees_{family}.parquet" in row["path"]
    )
    rel = str(record["path"])
    candidates = [data_root / rel]
    prefix = "schema_version=1/"
    if rel.startswith(prefix):
        candidates.append(data_root / rel[len(prefix) :])
    path = next((candidate for candidate in candidates if candidate.exists()), None)
    if path is None:
        raise RuntimeError(f"missing DATA-002 family parquet {family}")
    if path.stat().st_size != int(record["bytes"]) or sha256(path) != record["sha256"]:
        raise RuntimeError(f"DATA-002 family hash mismatch {family}")
    return path, record


def q(value: Path | str) -> str:
    return str(value).replace("'", "''")


def connection_for(family: str) -> duckdb.DuckDBPyConnection:
    temp = WORK / "duckdb_tmp" / family
    temp.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    con.execute("PRAGMA threads=4")
    con.execute("PRAGMA preserve_insertion_order=false")
    con.execute(f"SET temp_directory='{q(temp)}'")
    return con


def scalar(con: duckdb.DuckDBPyConnection, query: str) -> int | float:
    row = con.execute(query).fetchone()
    if row is None:
        raise RuntimeError("scalar query returned no row")
    return row[0]


def create_source_and_reconstruction(
    con: duckdb.DuckDBPyConnection,
    source: Path,
    infra: list[str],
) -> dict[str, Any]:
    infra_sql = ",".join(f"'{address.lower()}'" for address in infra) or "''"
    con.execute(
        f"""
        CREATE TEMP VIEW src AS
        SELECT
            CAST(family AS VARCHAR) AS family,
            COALESCE(CAST(event_id AS VARCHAR), CAST(condition_id AS VARCHAR)) AS event_id,
            CAST(market_id AS VARCHAR) AS market_id,
            CAST(condition_id AS VARCHAR) AS condition_id,
            CAST(timestamp AS BIGINT) AS timestamp,
            CAST(tx_hash AS VARCHAR) AS tx_hash,
            CAST(log_index AS BIGINT) AS log_index,
            LOWER(CAST(participant_address AS VARCHAR)) AS participant_address,
            UPPER(CAST(participant_side AS VARCHAR)) AS participant_side,
            CAST(order_is_match_taker_order AS BOOLEAN) AS is_active,
            UPPER(CAST(outcome_side AS VARCHAR)) AS outcome_side,
            CAST(price AS DOUBLE) AS price,
            CAST(size_shares AS DOUBLE) AS size_shares,
            CAST(value_usd AS DOUBLE) AS value_usd
        FROM read_parquet('{q(source)}')
        """
    )
    con.execute(
        """
        CREATE TEMP TABLE group_audit AS
        SELECT
            family, condition_id, tx_hash,
            COUNT(*) row_count,
            SUM(CASE WHEN is_active THEN 1 ELSE 0 END) active_count,
            SUM(CASE WHEN NOT is_active THEN 1 ELSE 0 END) passive_count,
            BOOL_AND(outcome_side IN ('YES','NO')) all_binary,
            MAX(CASE WHEN is_active THEN size_shares END) active_size,
            SUM(CASE WHEN NOT is_active THEN size_shares ELSE 0 END) passive_size,
            MAX(CASE WHEN is_active THEN
                (CASE WHEN outcome_side='YES' THEN price ELSE 1-price END)*size_shares
                END) active_yes_notional,
            SUM(CASE WHEN NOT is_active THEN
                (CASE WHEN outcome_side='YES' THEN price ELSE 1-price END)*size_shares
                ELSE 0 END) passive_yes_notional
        FROM src
        GROUP BY family, condition_id, tx_hash
        """
    )
    con.execute(
        f"""
        CREATE TEMP TABLE accepted AS
        SELECT family, condition_id, tx_hash
        FROM group_audit
        WHERE active_count=1
          AND passive_count>=1
          AND all_binary
          AND ABS(active_size-passive_size)
              <= {SIZE_TOL}*GREATEST(1.0,ABS(active_size),ABS(passive_size))
          AND ABS(active_yes_notional-passive_yes_notional)
              <= {YES_NOTIONAL_TOL}*GREATEST(
                    1.0,ABS(active_yes_notional),ABS(passive_yes_notional)
                 )
        """
    )
    con.execute(
        """
        CREATE TEMP TABLE econ_raw AS
        SELECT
            s.family, s.event_id, s.market_id, s.condition_id,
            s.timestamp, s.tx_hash, s.log_index,
            CASE WHEN s.outcome_side='YES' THEN s.price ELSE 1-s.price END p_yes,
            s.size_shares, s.value_usd
        FROM src s
        INNER JOIN accepted a USING (family,condition_id,tx_hash)
        WHERE NOT s.is_active
          AND s.outcome_side IN ('YES','NO')
          AND s.price>0 AND s.price<1
        """
    )
    con.execute(
        """
        CREATE TEMP TABLE econ AS
        SELECT *,
            ROW_NUMBER() OVER (
                PARTITION BY condition_id
                ORDER BY timestamp,tx_hash,log_index
            ) econ_seq
        FROM econ_raw
        """
    )
    con.execute(
        f"""
        CREATE TEMP TABLE participant_eligible AS
        SELECT
            s.family, s.event_id, s.market_id, s.condition_id,
            s.timestamp, s.tx_hash, s.log_index, s.participant_address,
            s.participant_side, s.outcome_side,
            CASE WHEN s.outcome_side='YES' THEN s.price ELSE 1-s.price END p_yes,
            s.size_shares, s.value_usd,
            CASE
              WHEN s.outcome_side='YES' AND s.participant_side='BUY' THEN 1.0
              WHEN s.outcome_side='YES' AND s.participant_side='SELL' THEN -1.0
              WHEN s.outcome_side='NO' AND s.participant_side='BUY' THEN -1.0
              WHEN s.outcome_side='NO' AND s.participant_side='SELL' THEN 1.0
              ELSE NULL
            END pressure,
            LN(1+GREATEST(COALESCE(s.value_usd,0),0)) log_value
        FROM src s
        INNER JOIN accepted a USING (family,condition_id,tx_hash)
        WHERE s.outcome_side IN ('YES','NO')
          AND s.price>0 AND s.price<1
          AND s.participant_address IS NOT NULL
          AND LENGTH(s.participant_address)=42
          AND s.participant_address NOT IN ({infra_sql})
        """
    )
    total_rows = int(scalar(con, "SELECT COUNT(*) FROM src"))
    total_groups = int(scalar(con, "SELECT COUNT(*) FROM group_audit"))
    accepted_groups = int(scalar(con, "SELECT COUNT(*) FROM accepted"))
    participant_rows = int(scalar(con, "SELECT COUNT(*) FROM participant_eligible"))
    economic_rows = int(scalar(con, "SELECT COUNT(*) FROM econ"))
    infra_excluded = int(
        scalar(
            con,
            f"""
            SELECT COUNT(*)
            FROM src s INNER JOIN accepted a USING (family,condition_id,tx_hash)
            WHERE LOWER(CAST(s.participant_address AS VARCHAR)) IN ({infra_sql})
            """,
        )
    )
    distinct_participants = int(
        scalar(con, "SELECT COUNT(DISTINCT participant_address) FROM participant_eligible")
    )
    failure_counts = {
        "active_count_not_one": int(
            scalar(con, "SELECT COUNT(*) FROM group_audit WHERE active_count<>1")
        ),
        "no_passive": int(
            scalar(con, "SELECT COUNT(*) FROM group_audit WHERE passive_count<1")
        ),
        "non_binary": int(scalar(con, "SELECT COUNT(*) FROM group_audit WHERE NOT all_binary")),
    }
    # These wide audit/reconstruction intermediates are no longer needed after
    # econ and participant_eligible have been materialized. Drop them before the
    # large participant-window stage so Kaggle's bounded DuckDB temp disk is
    # available for the actual frozen feature computation.
    con.execute("DROP TABLE group_audit")
    con.execute("DROP TABLE accepted")
    con.execute("DROP TABLE econ_raw")
    return {
        "source_rows": total_rows,
        "transaction_condition_groups": total_groups,
        "accepted_groups": accepted_groups,
        "rejected_groups": total_groups - accepted_groups,
        "economic_trade_rows": economic_rows,
        "eligible_participant_rows": participant_rows,
        "distinct_eligible_participants": distinct_participants,
        "infrastructure_rows_excluded": infra_excluded,
        "failure_counts": failure_counts,
    }


def create_participant_features(con: duckdb.DuckDBPyConnection) -> tuple[int, int]:
    """Build strictly timestamp-prior participant state.

    DATA-002 timestamps are one-second block-time proxies. History therefore
    excludes the entire current timestamp batch; lexical tx/log order is never
    treated as observable sequencing within a second.
    """
    cuts = con.execute(
        """
        SELECT
          CAST(QUANTILE_DISC(timestamp,0.70) AS BIGINT),
          CAST(QUANTILE_DISC(timestamp,0.85) AS BIGINT)
        FROM participant_eligible
        """
    ).fetchone()
    if cuts is None:
        raise RuntimeError("could not derive split cuts")
    train_end, dev_end = int(cuts[0]), int(cuts[1])
    con.execute(
        """
        CREATE TEMP TABLE p0 AS
        SELECT
          p.*,
          CAST(FLOOR(timestamp/86400) AS BIGINT) day_bucket,
          CAST(FLOOR((timestamp%86400)/3600) AS BIGINT) hour_bucket,
          COUNT(*) OVER w_p_prior prior_fill_count,
          COUNT(*) OVER w_pm_prior prior_same_market_count,
          COUNT(*) OVER w_pe_prior prior_same_event_count,
          COUNT(*) OVER w_ph_prior prior_same_hour_count,
          COUNT(*) OVER w_p_300 recent_fill_count_300s,
          COUNT(*) OVER w_p_1800 recent_fill_count_1800s,
          MAX(timestamp) OVER w_p_prior participant_prev_ts,
          MIN(timestamp) OVER w_p_seen participant_first_ts,
          COUNT(DISTINCT CAST(FLOOR(timestamp/86400) AS BIGINT))
            OVER w_p_prior prior_active_days,
          COUNT(DISTINCT condition_id) OVER w_p_prior unique_markets_prior,
          COUNT(DISTINCT event_id) OVER w_p_prior unique_events_prior,
          AVG(log_value) OVER w_p_prior prior_mean_log_value,
          STDDEV_SAMP(log_value) OVER w_p_prior prior_std_log_value,
          AVG(pressure) OVER w_p_prior prior_pressure_balance,
          AVG(log_value) OVER w_m_prior market_prior_mean_log_value,
          STDDEV_SAMP(log_value) OVER w_m_prior market_prior_std_log_value,
          COUNT(DISTINCT participant_address)
            OVER w_m_prior market_participant_degree_prior
        FROM participant_eligible p
        WINDOW
          w_p_prior AS (
            PARTITION BY participant_address ORDER BY timestamp
            RANGE BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
          ),
          w_pm_prior AS (
            PARTITION BY participant_address,condition_id ORDER BY timestamp
            RANGE BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
          ),
          w_pe_prior AS (
            PARTITION BY participant_address,event_id ORDER BY timestamp
            RANGE BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
          ),
          w_ph_prior AS (
            PARTITION BY participant_address,
              CAST(FLOOR((timestamp%86400)/3600) AS BIGINT)
            ORDER BY timestamp
            RANGE BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
          ),
          w_p_300 AS (
            PARTITION BY participant_address ORDER BY timestamp
            RANGE BETWEEN 300 PRECEDING AND 1 PRECEDING
          ),
          w_p_1800 AS (
            PARTITION BY participant_address ORDER BY timestamp
            RANGE BETWEEN 1800 PRECEDING AND 1 PRECEDING
          ),
          w_p_seen AS (
            PARTITION BY participant_address ORDER BY timestamp
            RANGE BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
          ),
          w_m_prior AS (
            PARTITION BY condition_id ORDER BY timestamp
            RANGE BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
          )
        """
    )
    con.execute(
        """
        CREATE TEMP TABLE p4 AS
        SELECT *,
          LN(1+COALESCE(prior_fill_count,0)) log_prior_fill_count,
          LN(1+COALESCE(prior_active_days,0)) log_prior_active_days,
          LN(1+COALESCE(timestamp-participant_prev_ts,0)) log_recency_seconds,
          LN(1+GREATEST(timestamp-participant_first_ts,0)) log_lifetime_seconds,
          LN(1+COALESCE(recent_fill_count_300s,0)) log_recent_fill_count_300s,
          LN(1+COALESCE(recent_fill_count_1800s,0)) log_recent_fill_count_1800s,
          LN(1+COALESCE(prior_same_market_count,0)) log_prior_same_market_count,
          LN(1+COALESCE(unique_markets_prior,0)) log_unique_markets_prior,
          LN(1+COALESCE(unique_events_prior,0)) log_unique_events_prior,
          CASE WHEN COALESCE(prior_fill_count,0)>0
               THEN prior_same_market_count::DOUBLE/prior_fill_count
               ELSE 0 END market_history_share,
          CASE WHEN COALESCE(prior_fill_count,0)>0
               THEN prior_same_event_count::DOUBLE/prior_fill_count
               ELSE 0 END event_history_share,
          CASE WHEN COALESCE(prior_same_market_count,0)=0
               THEN 1.0 ELSE 0.0 END current_market_novelty,
          CASE WHEN COALESCE(prior_same_event_count,0)=0
               THEN 1.0 ELSE 0.0 END current_event_novelty,
          COALESCE(prior_mean_log_value,0) prior_mean_log_value_f,
          COALESCE(prior_std_log_value,0) prior_std_log_value_f,
          CASE WHEN COALESCE(prior_std_log_value,0)>1e-12
               THEN (log_value-prior_mean_log_value)/prior_std_log_value
               ELSE 0 END current_size_z,
          CASE WHEN COALESCE(market_prior_std_log_value,0)>1e-12
               THEN 1/(1+EXP(
                    -(log_value-market_prior_mean_log_value)
                    /market_prior_std_log_value
               ))
               ELSE 0.5 END market_size_percentile_proxy,
          COALESCE(prior_pressure_balance,0) prior_pressure_balance_f,
          ABS(COALESCE(prior_pressure_balance,0)) prior_pressure_abs,
          COALESCE(pressure,0)*COALESCE(prior_pressure_balance,0)
            current_prior_direction_alignment,
          CASE WHEN COALESCE(prior_fill_count,0)>0
               THEN prior_same_hour_count::DOUBLE/prior_fill_count
               ELSE 0 END prior_same_hour_share,
          CASE
            WHEN COALESCE(prior_fill_count,0)>0
             AND timestamp-participant_first_ts>0
            THEN
              (COALESCE(recent_fill_count_1800s,0)/30.0)
              / GREATEST(
                  prior_fill_count
                  / GREATEST(
                      (timestamp-participant_first_ts)/60.0,
                      1.0
                    ),
                  1e-9
                )
            ELSE 1.0
          END activity_acceleration,
          LN(1+COALESCE(market_participant_degree_prior,0))
            log_market_participant_degree
        FROM p0
        """
    )
    con.execute("DROP TABLE p0")
    con.execute("DROP TABLE participant_eligible")
    return train_end, dev_end


def create_sample_and_markout_history(
    con: duckdb.DuckDBPyConnection,
    *,
    train_end: int,
    dev_end: int,
    stage: str,
) -> None:
    p4_rows = int(scalar(con, "SELECT COUNT(*) FROM p4"))
    if p4_rows <= SAMPLE_CAP:
        sample_threshold_ppm = 1_000_000
    else:
        sample_threshold_ppm = max(
            1,
            int(SAMPLE_TARGET * 1_000_000 / p4_rows),
        )
    con.execute(
        f"""
        CREATE TEMP TABLE sample_base AS
        SELECT
          p4.*,
          MD5(
            tx_hash || ':' || CAST(log_index AS VARCHAR) || ':' ||
            participant_address || ':' || condition_id
          ) sample_id
        FROM p4
        WHERE MOD(
          HASH(tx_hash,log_index,participant_address,condition_id),
          1000000
        ) < {sample_threshold_ppm}
        """
    )
    sampled_rows = int(scalar(con, "SELECT COUNT(*) FROM sample_base"))
    if sampled_rows > SAMPLE_CAP:
        raise RuntimeError(
            f"deterministic hash sample exceeded hard cap: {sampled_rows}>{SAMPLE_CAP}"
        )
    con.execute(
        """
        CREATE TEMP TABLE train_last_raw AS
        SELECT p4.*
        FROM p4
        WHERE timestamp<? - ?
        QUALIFY timestamp=MAX(timestamp) OVER (
          PARTITION BY participant_address
        )
        """,
        [train_end, EMBARGO_SECONDS],
    )
    history_limit = dev_end if stage == "discovery" else 9_223_372_036_854_775_000
    con.execute(
        f"""
        CREATE TEMP TABLE markout_labeled AS
        SELECT
          r.participant_address,
          r.timestamp+300 available_ts,
          r.pressure * (
            LN(LEAST(GREATEST(f.p_yes,1e-6),1-1e-6)/
               (1-LEAST(GREATEST(f.p_yes,1e-6),1-1e-6)))
            -
            LN(LEAST(GREATEST(r.p_yes,1e-6),1-1e-6)/
               (1-LEAST(GREATEST(r.p_yes,1e-6),1-1e-6)))
          ) markout
        FROM p4 r
        ASOF LEFT JOIN econ f
          ON r.condition_id=f.condition_id
         AND r.timestamp+300>=f.timestamp
        WHERE r.pressure IS NOT NULL
          AND f.timestamp>r.timestamp
          AND r.timestamp+300<{history_limit}
        """
    )
    con.execute(
        """
        CREATE TEMP TABLE markout_by_time AS
        SELECT participant_address,available_ts,
               SUM(markout) markout_sum_at_time,
               COUNT(*) markout_count_at_time
        FROM markout_labeled
        WHERE ISFINITE(markout)
        GROUP BY participant_address,available_ts
        """
    )
    con.execute(
        """
        CREATE TEMP TABLE markout_cum AS
        SELECT
          participant_address,available_ts,
          SUM(markout_sum_at_time) OVER (
            PARTITION BY participant_address
            ORDER BY available_ts
            ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
          ) hist_sum,
          SUM(markout_count_at_time) OVER (
            PARTITION BY participant_address
            ORDER BY available_ts
            ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
          ) hist_count
        FROM markout_by_time
        """
    )
    con.execute(
        f"""
        CREATE TEMP TABLE sample_hist AS
        SELECT
          s.*,
          COALESCE(m.hist_sum,0)/(COALESCE(m.hist_count,0)+{HIST_PRIOR})
            hist_markout_score,
          LN(1+COALESCE(m.hist_count,0)) log_hist_markout_count,
          1/SQRT(COALESCE(m.hist_count,0)+{HIST_PRIOR})
            hist_markout_uncertainty,
          COALESCE(md.hist_sum,0)/(COALESCE(md.hist_count,0)+{HIST_PRIOR})
            hist_markout_score_delayed_1h
        FROM sample_base s
        ASOF LEFT JOIN markout_cum m
          ON s.participant_address=m.participant_address
         AND s.timestamp>m.available_ts
        ASOF LEFT JOIN markout_cum md
          ON s.participant_address=md.participant_address
         AND s.timestamp-3600>md.available_ts
        """
    )
    con.execute(
        f"""
        CREATE TEMP TABLE train_fingerprint AS
        SELECT
          t.*,
          COALESCE(m.hist_sum,0)/(COALESCE(m.hist_count,0)+{HIST_PRIOR})
            hist_markout_score,
          LN(1+COALESCE(m.hist_count,0)) log_hist_markout_count,
          1/SQRT(COALESCE(m.hist_count,0)+{HIST_PRIOR})
            hist_markout_uncertainty
        FROM train_last_raw t
        ASOF LEFT JOIN markout_cum m
          ON t.participant_address=m.participant_address
         AND t.timestamp>m.available_ts
        WHERE t.prior_fill_count>=20
          AND t.prior_active_days>=2
        """
    )


def create_cumulative_market_state(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(
        """
        CREATE TEMP TABLE econ_sec AS
        SELECT condition_id,event_id,timestamp,
               COUNT(*) sec_count,
               SUM(COALESCE(value_usd,0)) sec_value
        FROM econ
        GROUP BY condition_id,event_id,timestamp
        """
    )
    con.execute(
        """
        CREATE TEMP TABLE econ_cum AS
        SELECT *,
          SUM(sec_count) OVER (
            PARTITION BY condition_id ORDER BY timestamp
            ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
          ) cum_count,
          SUM(sec_value) OVER (
            PARTITION BY condition_id ORDER BY timestamp
            ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
          ) cum_value
        FROM econ_sec
        """
    )
    con.execute(
        """
        CREATE TEMP TABLE event_sec AS
        SELECT event_id,timestamp,COUNT(*) sec_count
        FROM econ
        GROUP BY event_id,timestamp
        """
    )
    con.execute(
        """
        CREATE TEMP TABLE event_cum AS
        SELECT *,
          SUM(sec_count) OVER (
            PARTITION BY event_id ORDER BY timestamp
            ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
          ) cum_count
        FROM event_sec
        """
    )


def export_train_fingerprint(
    con: duckdb.DuckDBPyConnection,
    output: Path,
) -> None:
    feature_cols = ",\n".join(f"AVG({name}) AS {name}" for name in FINGERPRINT)
    query = f"""
      SELECT
        family,
        participant_address,
        timestamp,
        MAX(prior_fill_count) AS prior_fill_count,
        MAX(prior_active_days) AS prior_active_days,
        {feature_cols}
      FROM train_fingerprint
      GROUP BY family,participant_address,timestamp
    """
    con.execute(f"COPY ({query}) TO '{q(output)}' (FORMAT PARQUET,COMPRESSION ZSTD)")


def export_panel(
    con: duckdb.DuckDBPyConnection,
    output: Path,
    *,
    dev_end: int,
    stage: str,
) -> None:
    scope = f"WHERE s.timestamp<{dev_end}" if stage == "discovery" else ""
    query = f"""
    WITH context AS (
      SELECT
        s.*,
        LN(LEAST(GREATEST(s.p_yes,1e-6),1-1e-6)/
           (1-LEAST(GREATEST(s.p_yes,1e-6),1-1e-6))) current_logit,
        CASE WHEN p15.p_yes IS NOT NULL THEN
          LN(LEAST(GREATEST(s.p_yes,1e-6),1-1e-6)/
             (1-LEAST(GREATEST(s.p_yes,1e-6),1-1e-6)))
          - LN(LEAST(GREATEST(p15.p_yes,1e-6),1-1e-6)/
               (1-LEAST(GREATEST(p15.p_yes,1e-6),1-1e-6))) END ret_15,
        CASE WHEN p60.p_yes IS NOT NULL THEN
          LN(LEAST(GREATEST(s.p_yes,1e-6),1-1e-6)/
             (1-LEAST(GREATEST(s.p_yes,1e-6),1-1e-6)))
          - LN(LEAST(GREATEST(p60.p_yes,1e-6),1-1e-6)/
               (1-LEAST(GREATEST(p60.p_yes,1e-6),1-1e-6))) END ret_60,
        CASE WHEN p300.p_yes IS NOT NULL THEN
          LN(LEAST(GREATEST(s.p_yes,1e-6),1-1e-6)/
             (1-LEAST(GREATEST(s.p_yes,1e-6),1-1e-6)))
          - LN(LEAST(GREATEST(p300.p_yes,1e-6),1-1e-6)/
               (1-LEAST(GREATEST(p300.p_yes,1e-6),1-1e-6))) END ret_300,
        COALESCE(cend.cum_count,0)-COALESCE(c15.cum_count,0) econ_count_15,
        COALESCE(cend.cum_count,0)-COALESCE(c60.cum_count,0) econ_count_60,
        COALESCE(cend.cum_count,0)-COALESCE(c300.cum_count,0) econ_count_300,
        COALESCE(cend.cum_value,0)-COALESCE(c15.cum_value,0) econ_value_15,
        COALESCE(cend.cum_value,0)-COALESCE(c60.cum_value,0) econ_value_60,
        COALESCE(cend.cum_value,0)-COALESCE(c300.cum_value,0) econ_value_300,
        GREATEST(
          COALESCE(evend.cum_count,0)-COALESCE(ev60.cum_count,0)
          - (COALESCE(cend.cum_count,0)-COALESCE(c60.cum_count,0)),0
        ) event_count_60_ex_market,
        COALESCE(cf60.cum_count,0)-COALESCE(cnow.cum_count,0) future_econ_count_60,
        f15.timestamp f15_ts, f15.p_yes f15_p,
        f60.timestamp f60_ts, f60.p_yes f60_p,
        f300.timestamp f300_ts, f300.p_yes f300_p,
        ff.timestamp first_future_ts, ff.p_yes first_future_p, ff.econ_seq first_future_seq
      FROM sample_hist s
      ASOF LEFT JOIN econ p15
        ON s.condition_id=p15.condition_id AND s.timestamp-15>=p15.timestamp
      ASOF LEFT JOIN econ p60
        ON s.condition_id=p60.condition_id AND s.timestamp-60>=p60.timestamp
      ASOF LEFT JOIN econ p300
        ON s.condition_id=p300.condition_id AND s.timestamp-300>=p300.timestamp
      ASOF LEFT JOIN econ_cum cend
        ON s.condition_id=cend.condition_id AND s.timestamp-1>=cend.timestamp
      ASOF LEFT JOIN econ_cum c15
        ON s.condition_id=c15.condition_id AND s.timestamp-16>=c15.timestamp
      ASOF LEFT JOIN econ_cum c60
        ON s.condition_id=c60.condition_id AND s.timestamp-61>=c60.timestamp
      ASOF LEFT JOIN econ_cum c300
        ON s.condition_id=c300.condition_id AND s.timestamp-301>=c300.timestamp
      ASOF LEFT JOIN event_cum evend
        ON s.event_id=evend.event_id AND s.timestamp-1>=evend.timestamp
      ASOF LEFT JOIN event_cum ev60
        ON s.event_id=ev60.event_id AND s.timestamp-61>=ev60.timestamp
      ASOF LEFT JOIN econ_cum cnow
        ON s.condition_id=cnow.condition_id AND s.timestamp>=cnow.timestamp
      ASOF LEFT JOIN econ_cum cf60
        ON s.condition_id=cf60.condition_id AND s.timestamp+60>=cf60.timestamp
      ASOF LEFT JOIN econ f15
        ON s.condition_id=f15.condition_id AND s.timestamp+15>=f15.timestamp
      ASOF LEFT JOIN econ f60
        ON s.condition_id=f60.condition_id AND s.timestamp+60>=f60.timestamp
      ASOF LEFT JOIN econ f300
        ON s.condition_id=f300.condition_id AND s.timestamp+300>=f300.timestamp
      ASOF LEFT JOIN econ ff
        ON s.condition_id=ff.condition_id AND s.timestamp<ff.timestamp
      {scope}
    ),
    labelled AS (
      SELECT
        c.*,
        e2.timestamp event2_end_raw,e2.p_yes event2_p,
        e10.timestamp event10_end_raw,e10.p_yes event10_p,
        CASE WHEN c.f15_ts>c.timestamp THEN
          LN(LEAST(GREATEST(f15_p,1e-6),1-1e-6)/
             (1-LEAST(GREATEST(f15_p,1e-6),1-1e-6))) - current_logit END y_15,
        CASE WHEN c.f60_ts>c.timestamp THEN
          LN(LEAST(GREATEST(f60_p,1e-6),1-1e-6)/
             (1-LEAST(GREATEST(f60_p,1e-6),1-1e-6))) - current_logit END y_60,
        CASE WHEN c.f300_ts>c.timestamp THEN
          LN(LEAST(GREATEST(f300_p,1e-6),1-1e-6)/
             (1-LEAST(GREATEST(f300_p,1e-6),1-1e-6))) - current_logit END y_300,
        CASE WHEN c.f60_ts>c.timestamp THEN pressure * (
          LN(LEAST(GREATEST(f60_p,1e-6),1-1e-6)/
             (1-LEAST(GREATEST(f60_p,1e-6),1-1e-6))) - current_logit
        ) END signed_60,
        CASE WHEN c.f300_ts>c.timestamp THEN pressure * (
          LN(LEAST(GREATEST(f300_p,1e-6),1-1e-6)/
             (1-LEAST(GREATEST(f300_p,1e-6),1-1e-6))) - current_logit
        ) END signed_300,
        CASE WHEN c.f60_ts>c.timestamp THEN ABS(
          LN(LEAST(GREATEST(f60_p,1e-6),1-1e-6)/
             (1-LEAST(GREATEST(f60_p,1e-6),1-1e-6))) - current_logit
        ) END abs_60,
        CAST(future_econ_count_60 AS DOUBLE) activity_60,
        CASE WHEN c.first_future_ts>c.timestamp
             THEN SIGN(c.first_future_p-c.p_yes) END next_price_change_sign
      FROM context c
      LEFT JOIN econ e2
        ON e2.condition_id=c.condition_id
       AND e2.econ_seq=c.first_future_seq+1
      LEFT JOIN econ e10
        ON e10.condition_id=c.condition_id
       AND e10.econ_seq=c.first_future_seq+9
    )
    SELECT
      *,
      CASE WHEN event2_end_raw IS NOT NULL THEN
        LN(LEAST(GREATEST(event2_p,1e-6),1-1e-6)/
           (1-LEAST(GREATEST(event2_p,1e-6),1-1e-6))) - current_logit END event_2,
      CASE WHEN event10_end_raw IS NOT NULL THEN
        LN(LEAST(GREATEST(event10_p,1e-6),1-1e-6)/
           (1-LEAST(GREATEST(event10_p,1e-6),1-1e-6))) - current_logit END event_10,
      CASE WHEN c.f15_ts>c.timestamp THEN f15_ts END y15_end,
      CASE WHEN c.f60_ts>c.timestamp THEN f60_ts END y60_end,
      CASE WHEN c.f300_ts>c.timestamp THEN f300_ts END y300_end,
      timestamp+60 activity60_end,
      event2_end_raw event2_end,
      event10_end_raw event10_end,
      COALESCE(prior_mean_log_value,0) prior_mean_log_value_clean,
      COALESCE(prior_std_log_value,0) prior_std_log_value_clean,
      COALESCE(prior_pressure_balance,0) prior_pressure_balance_clean
    FROM labelled c
    """
    con.execute(
        f"COPY ({query}) TO '{q(output)}' (FORMAT PARQUET,COMPRESSION ZSTD,ROW_GROUP_SIZE 200000)"
    )


def build_family(
    *,
    family: str,
    source: Path,
    infra: list[str],
    stage: str,
) -> dict[str, Any]:
    print(json.dumps({"stage": stage, "family": family, "status": "START"}), flush=True)
    family_dir = WORK / "panels"
    family_dir.mkdir(parents=True, exist_ok=True)
    con = connection_for(family)
    audit = create_source_and_reconstruction(con, source, infra)
    train_end, dev_end = create_participant_features(con)
    create_sample_and_markout_history(con, train_end=train_end, dev_end=dev_end, stage=stage)
    create_cumulative_market_state(con)
    panel = family_dir / f"panel_{family}.parquet"
    fingerprint = family_dir / f"fingerprint_{family}.parquet"
    export_panel(con, panel, dev_end=dev_end, stage=stage)
    export_train_fingerprint(con, fingerprint)
    sample_rows = int(pq.ParquetFile(panel).metadata.num_rows)
    fingerprint_rows = int(pq.ParquetFile(fingerprint).metadata.num_rows)
    con.close()
    result = {
        **audit,
        "family": family,
        "train_end": train_end,
        "dev_end": dev_end,
        "sample_rows": sample_rows,
        "fingerprint_rows": fingerprint_rows,
        "panel": panel.name,
        "panel_sha256": sha256(panel),
        "fingerprint": fingerprint.name,
        "fingerprint_sha256": sha256(fingerprint),
    }
    print(
        json.dumps(
            {"stage": stage, "family": family, "status": "DONE", "sample_rows": sample_rows}
        ),
        flush=True,
    )
    return result


def load_panels(family_reports: list[dict[str, Any]]) -> pd.DataFrame:
    frames = [pd.read_parquet(WORK / "panels" / str(report["panel"])) for report in family_reports]
    panel = pd.concat(frames, ignore_index=True)
    for original, clean in (
        ("prior_mean_log_value", "prior_mean_log_value_clean"),
        ("prior_std_log_value", "prior_std_log_value_clean"),
        ("prior_pressure_balance", "prior_pressure_balance_clean"),
    ):
        if clean in panel:
            panel[original] = panel[clean]
    return panel


def split_masks(
    frame: pd.DataFrame,
    cuts: dict[str, tuple[int, int]],
    label_end: str,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    n = len(frame)
    train = np.zeros(n, dtype=bool)
    dev = np.zeros(n, dtype=bool)
    holdout = np.zeros(n, dtype=bool)
    end_values = pd.to_numeric(frame[label_end], errors="coerce").to_numpy(float)
    ts_values = pd.to_numeric(frame["timestamp"], errors="coerce").to_numpy(np.int64)
    families = frame["family"].astype(str).to_numpy()
    valid = np.isfinite(end_values)
    for family, (train_end, dev_end) in cuts.items():
        rows = families == family
        train |= rows & valid & (ts_values < train_end - EMBARGO_SECONDS) & (end_values < train_end)
        dev |= (
            rows
            & valid
            & (ts_values >= train_end)
            & (ts_values < dev_end - EMBARGO_SECONDS)
            & (end_values < dev_end)
        )
        holdout |= rows & valid & (ts_values >= dev_end + EMBARGO_SECONDS)
    return train, dev, holdout


def impute_standardize_fit(
    frame: pd.DataFrame, features: tuple[str, ...] | list[str]
) -> tuple[np.ndarray, dict[str, list[float]]]:
    x = frame.loc[:, list(features)].apply(pd.to_numeric, errors="coerce").to_numpy(float)
    med = np.nanmedian(x, axis=0)
    med = np.where(np.isfinite(med), med, 0.0)
    x = np.where(np.isfinite(x), x, med)
    mean = np.mean(x, axis=0)
    scale = np.std(x, axis=0)
    scale = np.where(scale > 1e-12, scale, 1.0)
    return (x - mean) / scale, {
        "median": med.tolist(),
        "mean": mean.tolist(),
        "scale": scale.tolist(),
    }


def impute_standardize_apply(
    frame: pd.DataFrame,
    features: tuple[str, ...] | list[str],
    params: dict[str, list[float]],
) -> np.ndarray:
    x = frame.loc[:, list(features)].apply(pd.to_numeric, errors="coerce").to_numpy(float)
    med = np.asarray(params["median"], dtype=float)
    mean = np.asarray(params["mean"], dtype=float)
    scale = np.asarray(params["scale"], dtype=float)
    x = np.where(np.isfinite(x), x, med)
    return (x - mean) / scale


def hierarchical_weights(frame: pd.DataFrame) -> np.ndarray:
    """Equalise family -> market -> observable timestamp batch -> row."""
    keys = pd.DataFrame(
        {
            "family": frame["family"].astype(str),
            "condition_id": frame["condition_id"].astype(str),
            "timestamp": pd.to_numeric(frame["timestamp"], errors="raise").astype(np.int64),
        },
        index=frame.index,
    )
    rows_per_batch = keys.groupby(["family", "condition_id", "timestamp"], sort=False)[
        "timestamp"
    ].transform("size")
    batches_per_market = keys.groupby(["family", "condition_id"], sort=False)[
        "timestamp"
    ].transform("nunique")
    markets_per_family = keys.groupby("family", sort=False)["condition_id"].transform("nunique")
    n_families = max(1, keys["family"].nunique())
    w = 1.0 / (
        n_families
        * markets_per_family.to_numpy(float)
        * batches_per_market.to_numpy(float)
        * rows_per_batch.to_numpy(float)
    )
    return w / np.mean(w)


def mse(y: np.ndarray, p: np.ndarray, w: np.ndarray | None = None) -> float:
    error = (np.asarray(y, float) - np.asarray(p, float)) ** 2
    return float(np.average(error, weights=w)) if w is not None else float(np.mean(error))


def fit_ridge_on_dev(
    train: pd.DataFrame,
    dev: pd.DataFrame,
    features: tuple[str, ...] | list[str],
    target: str,
) -> dict[str, Any]:
    from predictions_cup.learning.participant_ecology import ridge_fit, ridge_predict

    x_train, prep = impute_standardize_fit(train, features)
    x_dev = impute_standardize_apply(dev, features, prep)
    y_train = pd.to_numeric(train[target], errors="coerce").to_numpy(float)
    y_dev = pd.to_numeric(dev[target], errors="coerce").to_numpy(float)
    w_train = hierarchical_weights(train)
    w_dev = hierarchical_weights(dev)
    best: dict[str, Any] | None = None
    for alpha in (0.1, 1.0, 10.0, 100.0):
        beta = ridge_fit(x_train, y_train, alpha=alpha, sample_weight=w_train)
        pred = ridge_predict(beta, x_dev)
        loss = mse(y_dev, pred, w_dev)
        row = {
            "alpha": alpha,
            "dev_mse": loss,
            "beta": beta.tolist(),
            "preprocess": prep,
            "prediction": pred,
        }
        if best is None or loss < best["dev_mse"]:
            best = row
    if best is None:
        raise RuntimeError("ridge search produced no candidate")
    return best


def fingerprint_matrix(frame: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    x = frame.loc[:, list(FINGERPRINT)].apply(pd.to_numeric, errors="coerce").to_numpy(float)
    med = np.nanmedian(x, axis=0)
    med = np.where(np.isfinite(med), med, 0.0)
    return np.where(np.isfinite(x), x, med), med


def fit_archetypes(fingerprint_paths: list[Path]) -> tuple[dict[str, Any], dict[str, Any]]:
    from sklearn.metrics import adjusted_rand_score

    frame = pd.concat([pd.read_parquet(path) for path in fingerprint_paths], ignore_index=True)
    if len(frame) < 100:
        raise RuntimeError("insufficient TRAIN participant fingerprints")
    x, med = fingerprint_matrix(frame)
    scaler = StandardScaler().fit(x)
    z = scaler.transform(x)
    n_components = min(6, z.shape[1])
    pca = PCA(n_components=n_components, random_state=SEED).fit(z)
    pc = pca.transform(z)
    km = MiniBatchKMeans(n_clusters=6, random_state=SEED, batch_size=8192, n_init=10).fit(pc)
    labels = km.labels_
    rng = np.random.default_rng(SEED)
    stability: list[float] = []
    bootstrap_n = min(len(frame), 200_000)
    eval_n = min(len(frame), 100_000)
    eval_idx = np.arange(eval_n)
    for rep in range(5):
        idx = rng.choice(len(frame), size=bootstrap_n, replace=True)
        alt = MiniBatchKMeans(
            n_clusters=6, random_state=SEED + rep + 1, batch_size=8192, n_init=5
        ).fit(pc[idx])
        stability.append(float(adjusted_rand_score(labels[eval_idx], alt.predict(pc[eval_idx]))))
    report_clusters = []
    standardized = z
    for cluster in range(6):
        mask = labels == cluster
        means = np.mean(standardized[mask], axis=0)
        top = np.argsort(np.abs(means))[::-1][:5]
        report_clusters.append(
            {
                "cluster": cluster,
                "rows": int(mask.sum()),
                "share": float(mask.mean()),
                "top_standardized_features": [
                    {"feature": FINGERPRINT[i], "mean_z": float(means[i])} for i in top
                ],
            }
        )
    params = {
        "features": list(FINGERPRINT),
        "median": med.tolist(),
        "scaler_mean": scaler.mean_.tolist(),
        "scaler_scale": scaler.scale_.tolist(),
        "pca_mean": pca.mean_.tolist(),
        "pca_components": pca.components_.tolist(),
        "centers": km.cluster_centers_.tolist(),
    }
    report = {
        "fit_rows": int(len(frame)),
        "unique_participant_states": int(len(frame)),
        "n_clusters": 6,
        "pca_components": n_components,
        "pca_explained_variance_ratio": pca.explained_variance_ratio_.tolist(),
        "bootstrap_ari": stability,
        "bootstrap_ari_median": float(np.median(stability)),
        "clusters": report_clusters,
        "interpretation": "descriptive behavioural archetypes; not assumed alpha classes",
    }
    return params, report


def assign_archetypes(frame: pd.DataFrame, params: dict[str, Any]) -> pd.DataFrame:
    features = list(params["features"])
    x = frame.loc[:, features].apply(pd.to_numeric, errors="coerce").to_numpy(float)
    med = np.asarray(params["median"], float)
    x = np.where(np.isfinite(x), x, med)
    z = (x - np.asarray(params["scaler_mean"], float)) / np.asarray(params["scaler_scale"], float)
    pc = (z - np.asarray(params["pca_mean"], float)) @ np.asarray(params["pca_components"], float).T
    centers = np.asarray(params["centers"], float)
    dist = np.sum((pc[:, None, :] - centers[None, :, :]) ** 2, axis=2)
    cluster = np.argmin(dist, axis=1)
    sparse = (
        pd.to_numeric(frame["prior_fill_count"], errors="coerce").fillna(0).to_numpy() < 20
    ) | (pd.to_numeric(frame["prior_active_days"], errors="coerce").fillna(0).to_numpy() < 2)
    cluster = np.where(sparse, -1, cluster)
    for k in range(6):
        frame[f"archetype_{k}"] = (cluster == k).astype(np.float32)
    frame["archetype_sparse"] = (cluster < 0).astype(np.float32)
    return frame


def family_gains(
    dev: pd.DataFrame,
    y: np.ndarray,
    baseline_pred: np.ndarray,
    challenger_pred: np.ndarray,
) -> dict[str, float]:
    out: dict[str, float] = {}
    families = dev["family"].astype(str).to_numpy()
    for family in sorted(set(families)):
        mask = families == family
        if mask.sum() < 2:
            continue
        scoped = dev.loc[mask]
        weight = hierarchical_weights(scoped)
        out[family] = mse(y[mask], baseline_pred[mask], weight) - mse(
            y[mask], challenger_pred[mask], weight
        )
    return out


def clean_model_result(result: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in result.items()
        if key not in {"prediction", "beta", "preprocess"}
    }


def train_atlas(
    panel: pd.DataFrame,
    cuts: dict[str, tuple[int, int]],
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    all_features = list(dict.fromkeys([*BASELINE, *FINGERPRINT]))
    for family, (train_end, _) in cuts.items():
        scope = panel[
            (panel["family"].astype(str) == family)
            & (pd.to_numeric(panel["timestamp"], errors="coerce") < train_end - EMBARGO_SECONDS)
        ]
        for feature in all_features:
            values = pd.to_numeric(scope[feature], errors="coerce").to_numpy(float)
            finite = values[np.isfinite(values)]
            if len(finite) == 0:
                continue
            rows.append(
                {
                    "family": family,
                    "feature": feature,
                    "n": int(len(finite)),
                    "mean": float(np.mean(finite)),
                    "std": float(np.std(finite)),
                    "q10": float(np.quantile(finite, 0.10)),
                    "median": float(np.quantile(finite, 0.50)),
                    "q90": float(np.quantile(finite, 0.90)),
                }
            )
    return pd.DataFrame(rows)


def target_frames(
    panel: pd.DataFrame,
    cuts: dict[str, tuple[int, int]],
    target: str,
    label_end: str,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    train_mask, dev_mask, holdout_mask = split_masks(panel, cuts, label_end)
    y = pd.to_numeric(panel[target], errors="coerce").to_numpy(float)
    finite = np.isfinite(y)
    return (
        panel.loc[train_mask & finite].copy(),
        panel.loc[dev_mask & finite].copy(),
        panel.loc[holdout_mask & finite].copy(),
    )


def fit_hgb_dev(
    train: pd.DataFrame,
    dev: pd.DataFrame,
    features: list[str],
    target: str,
) -> dict[str, Any]:
    x_train = train[features].apply(pd.to_numeric, errors="coerce").to_numpy(float)
    med = np.nanmedian(x_train, axis=0)
    med = np.where(np.isfinite(med), med, 0.0)
    x_train = np.where(np.isfinite(x_train), x_train, med)
    y_train = pd.to_numeric(train[target], errors="coerce").to_numpy(float)
    order = np.argsort(train["sample_id"].astype(str).to_numpy(), kind="stable")
    if len(order) > 400_000:
        order = order[:400_000]
    x_fit, y_fit = x_train[order], y_train[order]
    x_dev = dev[features].apply(pd.to_numeric, errors="coerce").to_numpy(float)
    x_dev = np.where(np.isfinite(x_dev), x_dev, med)
    y_dev = pd.to_numeric(dev[target], errors="coerce").to_numpy(float)
    w_dev = hierarchical_weights(dev)
    best: dict[str, Any] | None = None
    for leaves in (15, 31):
        model = HistGradientBoostingRegressor(
            learning_rate=0.05,
            max_iter=100,
            max_leaf_nodes=leaves,
            l2_regularization=1.0,
            random_state=SEED,
        ).fit(x_fit, y_fit)
        pred = model.predict(x_dev)
        loss = mse(y_dev, pred, w_dev)
        row = {"max_leaf_nodes": leaves, "dev_mse": loss, "prediction": pred}
        if best is None or loss < best["dev_mse"]:
            best = row
    if best is None:
        raise RuntimeError("HGB search failed")
    return best


def fit_logistic_dev(
    train: pd.DataFrame,
    dev: pd.DataFrame,
    features: list[str],
) -> dict[str, Any]:
    train = train[pd.to_numeric(train["next_price_change_sign"], errors="coerce") != 0].copy()
    dev = dev[pd.to_numeric(dev["next_price_change_sign"], errors="coerce") != 0].copy()
    x_train, prep = impute_standardize_fit(train, features)
    x_dev = impute_standardize_apply(dev, features, prep)
    y_train = (pd.to_numeric(train["next_price_change_sign"]).to_numpy(float) > 0).astype(int)
    y_dev = (pd.to_numeric(dev["next_price_change_sign"]).to_numpy(float) > 0).astype(int)
    order = np.argsort(train["sample_id"].astype(str).to_numpy(), kind="stable")
    if len(order) > 500_000:
        order = order[:500_000]
    best: dict[str, Any] | None = None
    for c_value in (0.1, 1.0, 10.0):
        model = LogisticRegression(C=c_value, max_iter=200, random_state=SEED)
        model.fit(x_train[order], y_train[order])
        prob = np.clip(model.predict_proba(x_dev)[:, 1], 1e-9, 1 - 1e-9)
        loss = float(-np.mean(y_dev * np.log(prob) + (1 - y_dev) * np.log(1 - prob)))
        row = {
            "C": c_value,
            "dev_log_loss": loss,
            "dev_accuracy": float(np.mean((prob >= 0.5) == y_dev)),
            "rows_train": int(len(order)),
            "rows_dev": int(len(dev)),
        }
        if best is None or loss < best["dev_log_loss"]:
            best = row
    if best is None:
        raise RuntimeError("logistic search failed")
    return best


def discovery_analysis(
    panel: pd.DataFrame,
    cuts: dict[str, tuple[int, int]],
    archetype_params: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    panel = assign_archetypes(panel, archetype_params)
    train, dev, _ = target_frames(panel, cuts, "y_60", "y60_end")
    if len(train) < 1000 or len(dev) < 500:
        raise RuntimeError("insufficient primary TRAIN/DEV coverage")
    y_dev = pd.to_numeric(dev["y_60"], errors="coerce").to_numpy(float)
    baseline = fit_ridge_on_dev(train, dev, list(BASELINE), "y_60")
    baseline_pred = np.asarray(baseline["prediction"], float)
    models: dict[str, dict[str, Any]] = {
        "baseline": {
            **clean_model_result(baseline),
            "features": list(BASELINE),
        }
    }
    family_tests: dict[str, Any] = {}
    for name, extra in FEATURE_FAMILIES.items():
        features = list(dict.fromkeys([*BASELINE, *extra]))
        result = fit_ridge_on_dev(train, dev, features, "y_60")
        gains = family_gains(dev, y_dev, baseline_pred, np.asarray(result["prediction"], float))
        family_tests[name] = {
            "features": list(extra),
            "dev_mse": float(result["dev_mse"]),
            "overall_gain": float(baseline["dev_mse"] - result["dev_mse"]),
            "family_gains": gains,
            "positive_families": int(sum(v > 0 for v in gains.values())),
            "alpha": float(result["alpha"]),
        }
    selected_families = [
        name
        for name, row in family_tests.items()
        if row["overall_gain"] > 0 and row["positive_families"] >= 3
    ]
    selected_features = list(
        dict.fromkeys(
            feature for family in selected_families for feature in FEATURE_FAMILIES[family]
        )
    )
    variant_specs = {
        "identity_history": list(dict.fromkeys([*BASELINE, *IDENTITY_HISTORY])),
        "full_behavior": list(dict.fromkeys([*BASELINE, *FINGERPRINT])),
        "archetype": [*BASELINE, *[f"archetype_{k}" for k in range(6)], "archetype_sparse"],
    }
    if selected_features:
        variant_specs["selected_behavior"] = list(dict.fromkeys([*BASELINE, *selected_features]))
    for name, features in variant_specs.items():
        result = fit_ridge_on_dev(train, dev, features, "y_60")
        pred = np.asarray(result["prediction"], float)
        models[name] = {
            **clean_model_result(result),
            "features": features,
            "overall_gain_vs_baseline": float(baseline["dev_mse"] - result["dev_mse"]),
            "family_gains": family_gains(dev, y_dev, baseline_pred, pred),
        }
    hgb = fit_hgb_dev(train, dev, variant_specs["full_behavior"], "y_60")
    models["hgb_full_behavior"] = {
        "features": variant_specs["full_behavior"],
        "max_leaf_nodes": int(hgb["max_leaf_nodes"]),
        "dev_mse": float(hgb["dev_mse"]),
        "overall_gain_vs_baseline": float(baseline["dev_mse"] - hgb["dev_mse"]),
    }
    secondary: dict[str, Any] = {}
    full_features = variant_specs["full_behavior"]
    for target, label_end in CLOCK_TARGETS.items():
        if target == "y_60":
            continue
        t_train, t_dev, _ = target_frames(panel, cuts, target, label_end)
        if len(t_train) < 1000 or len(t_dev) < 500:
            secondary[target] = {
                "status": "COVERAGE_LIMITED",
                "train": len(t_train),
                "dev": len(t_dev),
            }
            continue
        base = fit_ridge_on_dev(t_train, t_dev, list(BASELINE), target)
        full = fit_ridge_on_dev(t_train, t_dev, full_features, target)
        secondary[target] = {
            "status": "OK",
            "rows_train": int(len(t_train)),
            "rows_dev": int(len(t_dev)),
            "baseline_alpha": float(base["alpha"]),
            "baseline_mse": float(base["dev_mse"]),
            "full_alpha": float(full["alpha"]),
            "full_mse": float(full["dev_mse"]),
            "incremental_gain": float(base["dev_mse"] - full["dev_mse"]),
            "family_gains": family_gains(
                t_dev,
                pd.to_numeric(t_dev[target], errors="coerce").to_numpy(float),
                np.asarray(base["prediction"], float),
                np.asarray(full["prediction"], float),
            ),
        }
    sign_train, sign_dev, _ = target_frames(
        panel, cuts, "next_price_change_sign", "first_future_ts"
    )
    classification: dict[str, Any]
    if len(sign_train) >= 1000 and len(sign_dev) >= 500:
        logit_base = fit_logistic_dev(sign_train, sign_dev, list(BASELINE))
        logit_full = fit_logistic_dev(sign_train, sign_dev, full_features)
        classification = {
            "status": "OK",
            "baseline": logit_base,
            "full_behavior": logit_full,
            "log_loss_gain": float(logit_base["dev_log_loss"] - logit_full["dev_log_loss"]),
        }
    else:
        classification = {"status": "COVERAGE_LIMITED"}
    report = {
        "primary_target": "60s own-market logit change",
        "rows_train": int(len(train)),
        "rows_dev": int(len(dev)),
        "models": models,
        "feature_family_tests": family_tests,
        "selected_feature_families": selected_families,
        "selected_behavior_features": selected_features,
        "secondary_targets": secondary,
        "next_change_classification": classification,
    }
    freeze = {
        "schema_version": 1,
        "experiment_id": "EXPERIMENT-005E",
        "freeze_kind": "IMMUTABLE_PRE_HOLDOUT_DEV_FREEZE",
        "base_sha": BASE_SHA,
        "cuts": {k: {"train_end": v[0], "dev_end": v[1]} for k, v in cuts.items()},
        "archetype_transform": archetype_params,
        "selected_feature_families": selected_families,
        "selected_behavior_features": selected_features,
        "model_specs": {
            name: {
                "features": row["features"],
                **({"alpha": row["alpha"]} if "alpha" in row else {}),
                **({"max_leaf_nodes": row["max_leaf_nodes"]} if "max_leaf_nodes" in row else {}),
            }
            for name, row in models.items()
        },
        "secondary_model_specs": {
            target: {
                "baseline_alpha": row.get("baseline_alpha"),
                "full_alpha": row.get("full_alpha"),
            }
            for target, row in secondary.items()
            if row.get("status") == "OK"
        },
        "classification_specs": classification if classification.get("status") == "OK" else {},
        "frozen_omissions": [
            "dynamic participant directional-agreement feature omitted before outcomes: exact all-row economic-state join would duplicate baseline construction at disproportionate cost",
            "common-event price movement omitted where no unambiguous event-wide probability aggregate exists; event activity remains in baseline",
            "raw participant one-hot identity model omitted by design; identity-history benchmark is strictly past-only recurrence/outcome state",
        ],
    }
    freeze["freeze_sha256"] = canonical_json_sha(freeze)
    return report, freeze


def fit_fixed_ridge(
    train: pd.DataFrame,
    test: pd.DataFrame,
    features: list[str],
    target: str,
    alpha: float,
) -> tuple[np.ndarray, np.ndarray, dict[str, Any]]:
    from predictions_cup.learning.participant_ecology import ridge_fit, ridge_predict

    x_train, prep = impute_standardize_fit(train, features)
    x_test = impute_standardize_apply(test, features, prep)
    y_train = pd.to_numeric(train[target], errors="coerce").to_numpy(float)
    beta = ridge_fit(x_train, y_train, alpha=alpha, sample_weight=hierarchical_weights(train))
    return ridge_predict(beta, x_test), beta, prep


def block_bootstrap_p(
    frame: pd.DataFrame,
    loss_gain: np.ndarray,
    *,
    reps: int = 500,
) -> tuple[float, list[float]]:
    from predictions_cup.learning.participant_ecology import hierarchical_block_bootstrap

    timestamp = pd.to_numeric(frame["timestamp"], errors="raise").to_numpy(np.int64)
    weights = hierarchical_weights(frame)
    p_value, draws = hierarchical_block_bootstrap(
        np.asarray(loss_gain, dtype=float),
        weights,
        frame["family"].astype(str).to_numpy(),
        timestamp // 86400,
        reps=reps,
        seed=SEED,
    )
    return p_value, draws.tolist()


def concentration_report(
    frame: pd.DataFrame,
    loss_gain: np.ndarray,
    weight: np.ndarray,
) -> dict[str, Any]:
    from predictions_cup.learning.participant_ecology import effective_number

    row_gain = np.asarray(loss_gain, dtype=float)
    row_weight = np.asarray(weight, dtype=float)
    work = pd.DataFrame(
        {
            "participant": frame["participant_address"].astype(str).to_numpy(),
            "gain": row_gain * row_weight,
        }
    )
    grouped = work.groupby("participant", sort=False)["gain"].sum()
    absolute = grouped.abs().sort_values(ascending=False)
    total_abs = float(absolute.sum())

    def effect_share(n: int) -> float:
        if total_abs <= 0:
            return 0.0
        return float(absolute.iloc[: min(n, len(absolute))].sum() / total_abs)

    n_one_pct = max(1, int(np.ceil(len(absolute) * 0.01))) if len(absolute) else 0
    contribution_shares = (
        absolute.to_numpy(float) / total_abs if total_abs > 0 else np.zeros(len(absolute))
    )
    activity = work.groupby("participant", sort=False).size().sort_values(ascending=False)
    top_activity = activity.index.tolist()

    def gain_without(addresses: list[str]) -> float | None:
        keep = ~work["participant"].isin(addresses).to_numpy()
        if not np.any(keep):
            return None
        return float(np.average(row_gain[keep], weights=row_weight[keep]))

    activity_one_pct = max(1, int(np.ceil(len(activity) * 0.01))) if len(activity) else 0
    return {
        "participants": int(len(grouped)),
        "top_participant_effect_share": effect_share(1),
        "top5_effect_share": effect_share(5),
        "top10_effect_share": effect_share(10),
        "top1pct_effect_share": effect_share(n_one_pct) if n_one_pct else 0.0,
        "effective_contributing_participants": float(effective_number(contribution_shares)),
        "gain_without_top1_activity": gain_without(top_activity[:1]),
        "gain_without_top5_activity": gain_without(top_activity[:5]),
        "gain_without_top10_activity": gain_without(top_activity[:10]),
        "gain_without_top1pct_activity": gain_without(top_activity[:activity_one_pct]),
    }


def slice_gain(
    frame: pd.DataFrame,
    loss_gain: np.ndarray,
    mask: np.ndarray,
    weight: np.ndarray,
) -> dict[str, float | int | None]:
    del frame
    selected = np.asarray(mask, dtype=bool)
    if int(selected.sum()) == 0:
        return {"rows": 0, "mean_loss_gain": None}
    return {
        "rows": int(selected.sum()),
        "mean_loss_gain": float(
            np.average(
                np.asarray(loss_gain, float)[selected],
                weights=np.asarray(weight, float)[selected],
            )
        ),
    }


def participant_state_permutation(
    frame: pd.DataFrame,
    features: list[str],
    *,
    mode: str,
) -> tuple[pd.DataFrame, float]:
    out = frame.copy()
    participant_features = [feature for feature in features if feature not in BASELINE]
    if not participant_features:
        return out, 0.0
    ts = pd.to_numeric(out["timestamp"], errors="coerce").fillna(0).astype(np.int64)
    prior = pd.to_numeric(out["prior_fill_count"], errors="coerce").fillna(0)
    freq_bin = np.minimum(np.floor(np.log1p(prior) / np.log(2.0)), 12).astype(int)
    if mode == "market_time":
        strata = pd.DataFrame(
            {
                "family": out["family"].astype(str),
                "market": out["condition_id"].astype(str),
                "block": ts // 300,
            },
            index=out.index,
        )
    elif mode == "frequency_matched":
        strata = pd.DataFrame(
            {
                "family": out["family"].astype(str),
                "block": ts // 900,
                "freq": freq_bin,
            },
            index=out.index,
        )
    else:
        raise ValueError(mode)
    rng = np.random.default_rng(SEED + (11 if mode == "market_time" else 17))
    mapped = np.arange(len(out))
    permuted = 0
    for _, positions in strata.groupby(list(strata.columns), sort=False).indices.items():
        pos = np.asarray(positions, dtype=np.int64)
        if len(pos) < 2:
            continue
        shuffled = rng.permutation(pos)
        mapped[pos] = shuffled
        permuted += len(pos)
    out.loc[:, participant_features] = out.iloc[mapped][participant_features].to_numpy()
    return out, float(permuted / max(1, len(out)))


def fixed_ridge_prediction(
    train: pd.DataFrame,
    test: pd.DataFrame,
    *,
    features: list[str],
    target: str,
    alpha: float,
) -> tuple[np.ndarray, np.ndarray, dict[str, Any]]:
    from predictions_cup.learning.participant_ecology import ridge_fit, ridge_predict

    x_train, prep = impute_standardize_fit(train, features)
    x_test = impute_standardize_apply(test, features, prep)
    y_train = pd.to_numeric(train[target], errors="coerce").to_numpy(float)
    beta = ridge_fit(
        x_train,
        y_train,
        alpha=alpha,
        sample_weight=hierarchical_weights(train),
    )
    return ridge_predict(beta, x_test), beta, prep


def fixed_hgb_prediction(
    train: pd.DataFrame,
    test: pd.DataFrame,
    *,
    features: list[str],
    target: str,
    max_leaf_nodes: int,
) -> np.ndarray:
    x_train = train[features].apply(pd.to_numeric, errors="coerce").to_numpy(float)
    med = np.nanmedian(x_train, axis=0)
    med = np.where(np.isfinite(med), med, 0.0)
    x_train = np.where(np.isfinite(x_train), x_train, med)
    x_test = test[features].apply(pd.to_numeric, errors="coerce").to_numpy(float)
    x_test = np.where(np.isfinite(x_test), x_test, med)
    y_train = pd.to_numeric(train[target], errors="coerce").to_numpy(float)
    order = np.argsort(train["sample_id"].astype(str).to_numpy(), kind="stable")
    if len(order) > 500_000:
        order = order[:500_000]
    model = HistGradientBoostingRegressor(
        learning_rate=0.05,
        max_iter=100,
        max_leaf_nodes=max_leaf_nodes,
        l2_regularization=1.0,
        random_state=SEED,
    ).fit(x_train[order], y_train[order])
    return np.asarray(model.predict(x_test), float)


def fixed_logistic_result(
    train: pd.DataFrame,
    test: pd.DataFrame,
    *,
    features: list[str],
    c_value: float,
) -> dict[str, Any]:
    train = train[pd.to_numeric(train["next_price_change_sign"], errors="coerce") != 0].copy()
    test = test[pd.to_numeric(test["next_price_change_sign"], errors="coerce") != 0].copy()
    if len(train) < 100 or len(test) < 50:
        return {"status": "COVERAGE_LIMITED", "rows_train": len(train), "rows_holdout": len(test)}
    x_train, prep = impute_standardize_fit(train, features)
    x_test = impute_standardize_apply(test, features, prep)
    y_train = (pd.to_numeric(train["next_price_change_sign"]).to_numpy(float) > 0).astype(int)
    y_test = (pd.to_numeric(test["next_price_change_sign"]).to_numpy(float) > 0).astype(int)
    model = LogisticRegression(C=c_value, max_iter=200, random_state=SEED).fit(x_train, y_train)
    prob = np.clip(model.predict_proba(x_test)[:, 1], 1e-9, 1 - 1e-9)
    return {
        "status": "OK",
        "rows_train": int(len(train)),
        "rows_holdout": int(len(test)),
        "log_loss": float(-np.mean(y_test * np.log(prob) + (1 - y_test) * np.log(1 - prob))),
        "accuracy": float(np.mean((prob >= 0.5) == y_test)),
    }


def holdout_analysis(
    panel: pd.DataFrame,
    cuts: dict[str, tuple[int, int]],
    freeze: dict[str, Any],
) -> dict[str, Any]:
    from predictions_cup.learning.participant_ecology import bh_adjust

    panel = assign_archetypes(panel, freeze["archetype_transform"])
    train, dev, holdout = target_frames(panel, cuts, "y_60", "y60_end")
    preholdout = pd.concat([train, dev], ignore_index=True)
    if len(preholdout) < 1000 or len(holdout) < 500:
        raise RuntimeError("insufficient frozen primary holdout coverage")
    y = pd.to_numeric(holdout["y_60"], errors="coerce").to_numpy(float)
    baseline_spec = freeze["model_specs"]["baseline"]
    baseline_pred, _, _ = fixed_ridge_prediction(
        preholdout,
        holdout,
        features=list(baseline_spec["features"]),
        target="y_60",
        alpha=float(baseline_spec["alpha"]),
    )
    w_holdout = hierarchical_weights(holdout)
    baseline_mse = mse(y, baseline_pred, w_holdout)
    results: dict[str, Any] = {}
    raw_p: dict[str, float] = {}
    prediction_cache: dict[str, np.ndarray] = {}
    for name, spec in freeze["model_specs"].items():
        if name == "baseline":
            continue
        features = list(spec["features"])
        if "alpha" in spec:
            pred, _, _ = fixed_ridge_prediction(
                preholdout,
                holdout,
                features=features,
                target="y_60",
                alpha=float(spec["alpha"]),
            )
        elif "max_leaf_nodes" in spec:
            pred = fixed_hgb_prediction(
                preholdout,
                holdout,
                features=features,
                target="y_60",
                max_leaf_nodes=int(spec["max_leaf_nodes"]),
            )
        else:
            continue
        prediction_cache[name] = pred
        challenger_mse = mse(y, pred, w_holdout)
        loss_gain = (y - baseline_pred) ** 2 - (y - pred) ** 2
        p_value, draws = block_bootstrap_p(holdout, loss_gain)
        weighted_gain = float(baseline_mse - challenger_mse)
        raw_p[name] = p_value if weighted_gain > 0 else 1.0
        family_gain = family_gains(holdout, y, baseline_pred, pred)
        unseen = (
            pd.to_numeric(holdout["prior_fill_count"], errors="coerce").fillna(0).to_numpy() == 0
        )
        sparse = (
            pd.to_numeric(holdout["prior_fill_count"], errors="coerce").fillna(0).to_numpy() < 20
        ) | (pd.to_numeric(holdout["prior_active_days"], errors="coerce").fillna(0).to_numpy() < 2)
        results[name] = {
            "features": features,
            "rows_holdout": int(len(holdout)),
            "baseline_mse": baseline_mse,
            "challenger_mse": challenger_mse,
            "weighted_mse_gain": weighted_gain,
            "mean_row_loss_gain": float(np.mean(loss_gain)),
            "bootstrap_p": raw_p[name],
            "bootstrap_ci95": [float(np.quantile(draws, 0.025)), float(np.quantile(draws, 0.975))],
            "family_gains": family_gain,
            "positive_families": int(sum(value > 0 for value in family_gain.values())),
            "concentration": concentration_report(holdout, loss_gain, w_holdout),
            "slices": {
                "unseen": slice_gain(holdout, loss_gain, unseen, w_holdout),
                "sparse": slice_gain(holdout, loss_gain, sparse, w_holdout),
                "repeat": slice_gain(holdout, loss_gain, ~sparse, w_holdout),
            },
        }
    fdr = bh_adjust(raw_p, alpha=0.05)
    for name, row in fdr.items():
        if name in results:
            results[name]["fdr"] = row

    falsification: dict[str, Any] = {}
    full = freeze["model_specs"].get("full_behavior")
    if full is not None and "alpha" in full and "full_behavior" in prediction_cache:
        full_features = list(full["features"])
        participant_only_features = [
            feature for feature in full_features if feature not in BASELINE
        ]
        full_pred = prediction_cache["full_behavior"]
        observed_loss_gain = (y - baseline_pred) ** 2 - (y - full_pred) ** 2
        _, beta, prep = fixed_ridge_prediction(
            preholdout,
            holdout,
            features=full_features,
            target="y_60",
            alpha=float(full["alpha"]),
        )
        from predictions_cup.learning.participant_ecology import ridge_predict

        for mode in ("market_time", "frequency_matched"):
            placebo_frame, coverage = participant_state_permutation(
                holdout, participant_only_features, mode=mode
            )
            placebo_x = impute_standardize_apply(placebo_frame, full_features, prep)
            placebo_pred = ridge_predict(beta, placebo_x)
            falsification[f"participant_state_{mode}_permutation"] = {
                "permuted_row_share": coverage,
                "permuted_features": participant_only_features,
                "mse": mse(y, placebo_pred, w_holdout),
                "gain_vs_baseline": float(baseline_mse - mse(y, placebo_pred, w_holdout)),
                "observed_full_behavior_gain": float(results["full_behavior"]["weighted_mse_gain"]),
            }
        shuffled = holdout.copy()
        rng = np.random.default_rng(SEED + 23)
        for feature in ("hist_markout_score", "log_hist_markout_count", "hist_markout_uncertainty"):
            shuffled[feature] = rng.permutation(shuffled[feature].to_numpy())
        x_shuffled = impute_standardize_apply(shuffled, full_features, prep)
        pred_shuffled = ridge_predict(beta, x_shuffled)
        falsification["shuffled_historical_score"] = {
            "gain_vs_baseline": float(baseline_mse - mse(y, pred_shuffled, w_holdout))
        }
        delayed = holdout.copy()
        delayed["hist_markout_score"] = delayed["hist_markout_score_delayed_1h"]
        x_delayed = impute_standardize_apply(delayed, full_features, prep)
        pred_delayed = ridge_predict(beta, x_delayed)
        falsification["delayed_history_1h"] = {
            "gain_vs_baseline": float(baseline_mse - mse(y, pred_delayed, w_holdout))
        }
        leave_family: dict[str, Any] = {}
        for family in sorted(holdout["family"].astype(str).unique()):
            tr = preholdout[preholdout["family"].astype(str) != family]
            te = holdout[holdout["family"].astype(str) == family]
            if len(tr) < 1000 or len(te) < 50:
                leave_family[family] = {"status": "COVERAGE_LIMITED"}
                continue
            base_family, _, _ = fixed_ridge_prediction(
                tr,
                te,
                features=list(baseline_spec["features"]),
                target="y_60",
                alpha=float(baseline_spec["alpha"]),
            )
            full_family, _, _ = fixed_ridge_prediction(
                tr,
                te,
                features=full_features,
                target="y_60",
                alpha=float(full["alpha"]),
            )
            yf = pd.to_numeric(te["y_60"], errors="coerce").to_numpy(float)
            wf = hierarchical_weights(te)
            leave_family[family] = {
                "status": "OK",
                "rows": int(len(te)),
                "gain": float(mse(yf, base_family, wf) - mse(yf, full_family, wf)),
            }
        falsification["leave_family_out"] = leave_family
        transfer = pd.DataFrame(
            {
                "condition_id": holdout["condition_id"].astype(str).to_numpy(),
                "event_id": holdout["event_id"].astype(str).to_numpy(),
                "weighted_numerator": observed_loss_gain * w_holdout,
                "weight": w_holdout,
            }
        )

        def grouped_transfer(key: str) -> pd.DataFrame:
            grouped = (
                transfer.groupby(key, sort=False)
                .agg(
                    weighted_numerator=("weighted_numerator", "sum"),
                    weight=("weight", "sum"),
                    rows=("weight", "size"),
                )
                .reset_index()
            )
            grouped["gain"] = grouped["weighted_numerator"] / grouped["weight"]
            return grouped

        market_gain = grouped_transfer("condition_id")
        event_gain = grouped_transfer("event_id")
        falsification["per_market_transfer"] = {
            "markets": int(len(market_gain)),
            "positive_market_share": float(np.mean(market_gain["gain"].to_numpy(float) > 0)),
            "median_market_gain": float(np.median(market_gain["gain"].to_numpy(float))),
            "note": "hierarchically weighted per-market transfer diagnostic; exhaustive leave-one-market retraining omitted as computationally pathological",
        }
        falsification["per_event_transfer"] = {
            "events": int(len(event_gain)),
            "positive_event_share": float(np.mean(event_gain["gain"].to_numpy(float) > 0)),
            "median_event_gain": float(np.median(event_gain["gain"].to_numpy(float))),
            "note": "hierarchically weighted event-level transfer diagnostic; true leave-family-out retraining remains the stronger frozen transfer test",
        }

    secondary: dict[str, Any] = {}
    if full is not None and "alpha" in full:
        full_features = list(full["features"])
        for target, label_end in CLOCK_TARGETS.items():
            if target == "y_60":
                continue
            t_train, t_dev, t_hold = target_frames(panel, cuts, target, label_end)
            if len(t_hold) < 100 or len(t_train) + len(t_dev) < 500:
                secondary[target] = {"status": "COVERAGE_LIMITED", "rows_holdout": len(t_hold)}
                continue
            spec = freeze.get("secondary_model_specs", {}).get(target, {})
            base_alpha = spec.get("baseline_alpha")
            full_alpha = spec.get("full_alpha")
            if base_alpha is None or full_alpha is None:
                secondary[target] = {"status": "NOT_FROZEN"}
                continue
            t_pre = pd.concat([t_train, t_dev], ignore_index=True)
            bp, _, _ = fixed_ridge_prediction(
                t_pre,
                t_hold,
                features=list(BASELINE),
                target=target,
                alpha=float(base_alpha),
            )
            fp, _, _ = fixed_ridge_prediction(
                t_pre,
                t_hold,
                features=full_features,
                target=target,
                alpha=float(full_alpha),
            )
            ty = pd.to_numeric(t_hold[target], errors="coerce").to_numpy(float)
            tw = hierarchical_weights(t_hold)
            secondary[target] = {
                "status": "OK",
                "rows_holdout": int(len(t_hold)),
                "baseline_mse": mse(ty, bp, tw),
                "full_behavior_mse": mse(ty, fp, tw),
                "gain": float(mse(ty, bp, tw) - mse(ty, fp, tw)),
                "family_gains": family_gains(t_hold, ty, bp, fp),
            }

    classification: dict[str, Any] = {"status": "NOT_FROZEN"}
    class_freeze = freeze.get("classification_specs", {})
    if class_freeze.get("status") == "OK":
        s_train, s_dev, s_hold = target_frames(
            panel, cuts, "next_price_change_sign", "first_future_ts"
        )
        s_pre = pd.concat([s_train, s_dev], ignore_index=True)
        baseline_c = float(class_freeze["baseline"]["C"])
        full_c = float(class_freeze["full_behavior"]["C"])
        baseline_class = fixed_logistic_result(
            s_pre, s_hold, features=list(BASELINE), c_value=baseline_c
        )
        full_class = fixed_logistic_result(
            s_pre,
            s_hold,
            features=list(full["features"]) if full is not None else list(BASELINE),
            c_value=full_c,
        )
        classification = {
            "status": "OK"
            if baseline_class.get("status") == "OK" and full_class.get("status") == "OK"
            else "COVERAGE_LIMITED",
            "baseline": baseline_class,
            "full_behavior": full_class,
        }
        if classification["status"] == "OK":
            classification["log_loss_gain"] = float(
                baseline_class["log_loss"] - full_class["log_loss"]
            )

    full_result = results.get("full_behavior")
    disposition = "INCONCLUSIVE"
    if full_result is not None:
        q = float(full_result.get("fdr", {}).get("q", 1.0))
        gain = float(full_result["weighted_mse_gain"])
        concentration = full_result["concentration"]
        permutation_bad = any(
            row.get("gain_vs_baseline", -np.inf) >= gain
            for key, row in falsification.items()
            if key.startswith("participant_state_")
        )
        broad = (
            q <= 0.05
            and gain > 0
            and int(full_result["positive_families"]) >= 3
            and float(concentration["top_participant_effect_share"]) < 0.50
            and float(concentration["effective_contributing_participants"]) >= 10
            and not permutation_bad
        )
        regime = q <= 0.05 and gain > 0 and int(full_result["positive_families"]) >= 1
        if broad:
            disposition = "CROSS_FAMILY_CANDIDATE"
        elif regime:
            disposition = "REGIME_SPECIFIC_CANDIDATE"
        elif q > 0.05 or gain <= 0:
            disposition = "NO_INCREMENTAL_EVIDENCE"

    return {
        "schema_version": 1,
        "experiment_id": "EXPERIMENT-005E",
        "phase": "HOLDOUT",
        "primary_target": "y_60",
        "rows_train": int(len(train)),
        "rows_dev": int(len(dev)),
        "rows_holdout": int(len(holdout)),
        "baseline_mse": baseline_mse,
        "primary_variants": results,
        "fdr_family_size": int(len(raw_p)),
        "falsification": falsification,
        "secondary_targets": secondary,
        "classification": classification,
        "disposition": disposition,
        "holdout_inspected_once": True,
    }


def write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main() -> None:
    code_root, bundle_manifest, config = load_bundle()
    phase = str(config.get("phase", "TRAIN_DEV")).upper()
    if phase not in {"TRAIN_DEV", "HOLDOUT"}:
        raise RuntimeError(f"unsupported phase {phase}")
    data_root, data_manifest = locate_data002(code_root)
    infra_payload = json.loads((code_root / "infra_registry.json").read_text())
    infra = [str(value).lower() for value in infra_payload["infra_addresses"]]
    family_reports: list[dict[str, Any]] = []
    for family in FAMILIES:
        source, _ = family_source(data_root, data_manifest, family)
        family_reports.append(
            build_family(
                family=family,
                source=source,
                infra=infra,
                stage="discovery" if phase == "TRAIN_DEV" else "holdout",
            )
        )
    us_report = next(row for row in family_reports if row["family"] == "US_2024")
    if int(us_report["sample_rows"]) < US_MIN:
        raise RuntimeError("US_2024 sampled participant panel below preregistered minimum")
    write_json(
        WORK / "participant_data_audit.json",
        {
            "schema_version": 1,
            "experiment_id": "EXPERIMENT-005E",
            "phase": phase,
            "data_002_manifest_sha256": DATA002_MANIFEST_SHA,
            "families": family_reports,
            "totals": {
                key: int(sum(int(row[key]) for row in family_reports))
                for key in (
                    "source_rows",
                    "transaction_condition_groups",
                    "accepted_groups",
                    "rejected_groups",
                    "economic_trade_rows",
                    "eligible_participant_rows",
                    "infrastructure_rows_excluded",
                    "sample_rows",
                    "fingerprint_rows",
                )
            },
        },
    )
    write_json(
        WORK / "infrastructure_exclusion_audit.json",
        {
            "registry_sha256": sha256(code_root / "infra_registry.json"),
            "registry_count": len(infra),
            "policy": "excluded from participant ecology/skill claims; unknown identity is not assumed human",
            "excluded_rows_by_family": {
                row["family"]: row["infrastructure_rows_excluded"] for row in family_reports
            },
        },
    )
    panel = load_panels(family_reports)
    cuts = {
        str(row["family"]): (int(row["train_end"]), int(row["dev_end"])) for row in family_reports
    }
    fingerprint_paths = [WORK / "panels" / str(row["fingerprint"]) for row in family_reports]
    if phase == "TRAIN_DEV":
        archetype_params, archetype_report = fit_archetypes(fingerprint_paths)
        atlas = train_atlas(panel, cuts)
        atlas.to_csv(WORK / "train_behavioural_atlas.csv", index=False)
        write_json(WORK / "cluster_archetype_report.json", archetype_report)
        discovery, freeze = discovery_analysis(panel, cuts, archetype_params)
        write_json(WORK / "dev_selection_report.json", discovery)
        write_json(WORK / "proposed_pre_holdout_freeze.json", freeze)
        summary = {
            "phase": "TRAIN_DEV",
            "holdout_status": "SEALED_NOT_INSPECTED",
            "family_reports": family_reports,
            "pre_holdout_freeze_sha256": freeze["freeze_sha256"],
            "bundle": bundle_manifest,
        }
        write_json(WORK / "train_dev_summary.json", summary)
        print(
            json.dumps({"phase": phase, "freeze_sha256": freeze["freeze_sha256"]}, sort_keys=True)
        )
        return

    freeze_path = code_root / "pre_holdout_freeze.json"
    if not freeze_path.exists():
        raise RuntimeError("HOLDOUT requires embedded pre_holdout_freeze.json")
    freeze = json.loads(freeze_path.read_text())
    expected = str(freeze.get("freeze_sha256", ""))
    check = dict(freeze)
    check.pop("freeze_sha256", None)
    if canonical_json_sha(check) != expected:
        raise RuntimeError("pre-HOLDOUT freeze hash mismatch")
    expected_cut = {
        family: {"train_end": values[0], "dev_end": values[1]} for family, values in cuts.items()
    }
    if freeze.get("cuts") != expected_cut:
        raise RuntimeError("timestamp split cuts changed between TRAIN_DEV and HOLDOUT")
    evaluation = holdout_analysis(panel, cuts, freeze)
    write_json(WORK / "holdout_evaluation.json", evaluation)
    write_json(
        WORK / "falsification_concentration_report.json",
        {
            "schema_version": 1,
            "experiment_id": "EXPERIMENT-005E",
            "phase": "HOLDOUT",
            "primary_target": evaluation["primary_target"],
            "disposition": evaluation["disposition"],
            "primary_variants": {
                name: {
                    key: row.get(key)
                    for key in (
                        "weighted_mse_gain",
                        "bootstrap_p",
                        "bootstrap_ci95",
                        "fdr",
                        "family_gains",
                        "positive_families",
                        "concentration",
                        "slices",
                    )
                }
                for name, row in evaluation["primary_variants"].items()
            },
            "falsification": evaluation["falsification"],
            "family_local_history_boundary": (
                "participant histories are constructed independently inside each family file; "
                "cross-family evidence is model transfer/stability, not a globally continuous wallet ledger"
            ),
        },
    )
    write_json(
        WORK / "exact_kaggle_provenance.json",
        {
            "experiment_id": "EXPERIMENT-005E",
            "phase": phase,
            "base_sha": BASE_SHA,
            "data_002_manifest_sha256": DATA002_MANIFEST_SHA,
            "bundle_manifest": bundle_manifest,
            "pre_holdout_freeze_sha256": expected,
            "runner_sha256": sha256(Path(__file__)),
            "output_files": sorted(path.name for path in WORK.iterdir() if path.is_file()),
        },
    )
    print(json.dumps({"phase": phase, "disposition": evaluation["disposition"]}, sort_keys=True))


if __name__ == "__main__":
    main()
