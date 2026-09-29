# ruff: noqa
from __future__ import annotations

import ast
import hashlib
import json
import math
import zipfile
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import duckdb
import pandas as pd

OUT = Path("/kaggle/working/r3_fv_001_prepare")
OUT.mkdir(parents=True, exist_ok=True)

DATA004_SLUG = "sig-cup-data-004-ets-p0p1-fills"
BLOCK_GATE_FRAGMENT = "005b-data003-block-gate"
FINAL_ANCHOR_FRAC = 0.82
DEV_ANCHOR_FRAC_PREFINAL = 0.65
MAX_FUTURE_HORIZON_SECONDS = 21600


def q(path: Path) -> str:
    return str(path).replace("'", "''")


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def locate(name: str, fragment: str) -> Path:
    matches = [p for p in Path("/kaggle/input").rglob(name) if fragment in str(p)]
    if len(matches) != 1:
        raise RuntimeError(f"expected one {name} under {fragment}, got {matches}")
    return matches[0]


def locate_data004_root() -> Path:
    matches = []
    for p in Path("/kaggle/input").rglob("MANIFEST.json"):
        if DATA004_SLUG in str(p.parent):
            matches.append(p.parent)
    matches = sorted(set(matches))
    if len(matches) != 1:
        raise RuntimeError(f"expected one DATA-004 root, got {matches}")
    return matches[0]


def parse_jsonish(value):
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, float) and math.isnan(value):
        return []
    s = str(value).strip()
    if not s:
        return []
    try:
        x = json.loads(s)
        return x if isinstance(x, list) else [x]
    except Exception:
        try:
            x = ast.literal_eval(s)
            return x if isinstance(x, list) else [x]
        except Exception:
            return []


def next_midnight(ts: int) -> int:
    d = datetime.fromtimestamp(int(ts), tz=timezone.utc)
    n = d.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
    return int(n.timestamp())


def pct_direct_row(con: duckdb.DuckDBPyConnection, view: str, frac: float) -> tuple[int, int]:
    n = int(con.execute(f"select count(*) from {view}").fetchone()[0])
    if n < 100:
        raise RuntimeError(f"insufficient direct chronology in {view}: {n}")
    idx = max(1, min(n, int(math.floor(frac * n))))
    r = con.execute(
        f"""
        select ts, block_number
        from {view}
        order by block_number, ts
        limit 1 offset {idx - 1}
        """
    ).fetchone()
    return int(r[0]), int(r[1])


def extract_graph_bundle(root: Path) -> Path:
    required = [
        "ETS_MARKET_GRAPH.csv",
        "ETS_SIG_ANCHOR_GRAPH.csv",
        "ETS_FILL_ACQUISITION.csv",
        "ETS_GAMMA_SEMANTIC_AUDIT.csv",
        "ETS_RELATIONSHIP_TAXONOMY.json",
        "ETS_COMPONENTS.json",
    ]

    mounted = root / "full_frozen_universe"
    if mounted.is_dir():
        for name in required:
            if len(list(mounted.rglob(name))) != 1:
                raise RuntimeError(
                    f"mounted graph directory missing/ambiguous file {name}"
                )
        return mounted

    zips = list(root.rglob("full_frozen_universe.zip"))
    if len(zips) != 1:
        raise RuntimeError(
            "expected mounted full_frozen_universe directory or exactly one "
            f"full_frozen_universe.zip, got dirs={mounted.is_dir()} zips={zips}"
        )
    target = OUT / "full_frozen_universe"
    target.mkdir(exist_ok=True)
    with zipfile.ZipFile(zips[0]) as zf:
        zf.extractall(target)
    for name in required:
        if len(list(target.rglob(name))) != 1:
            raise RuntimeError(f"extracted graph bundle missing/ambiguous file {name}")
    return target


def one(root: Path, name: str) -> Path:
    m = list(root.rglob(name))
    if len(m) != 1:
        raise RuntimeError(f"expected one {name}, got {m}")
    return m[0]


def build_direct_chronology() -> tuple[duckdb.DuckDBPyConnection, dict]:
    econ = locate("economic_fills_DATA003.parquet", BLOCK_GATE_FRAGMENT)
    txb = locate("tx_block_DATA003.parquet", BLOCK_GATE_FRAGMENT)
    bts = locate("block_timestamp.parquet", BLOCK_GATE_FRAGMENT)
    gate = locate("data003_block_gate_report.json", BLOCK_GATE_FRAGMENT)
    gatej = json.loads(gate.read_text())
    if gatej.get("all_hard_gates_pass") is not True:
        raise RuntimeError("accepted DATA-003 block gate did not pass")
    if gatej.get("predictive_outcomes_accessed") is not False:
        raise RuntimeError("upstream block gate accessed predictive outcomes")

    con = duckdb.connect()
    con.execute("pragma threads=4")
    con.execute(
        f"""
        create temp view direct_rows as
        select
            cast(e.timestamp as bigint) ts,
            lower(cast(e.tx_hash as varchar)) tx_hash,
            cast(e.log_index as bigint) log_index,
            cast(e.condition_id as varchar) condition_id,
            cast(e.sig_market_id as varchar) sig_market_id,
            upper(cast(e.mapping_class as varchar)) mapping_class,
            upper(cast(e.mapping_direction as varchar)) mapping_direction,
            cast(e.p_yes as double) p_yes,
            cast(t.block_number as bigint) block_number,
            cast(b.block_timestamp as bigint) block_ts
        from read_parquet('{q(econ)}') e
        join read_parquet('{q(txb)}') t
          on lower(cast(e.tx_hash as varchar))=t.tx_hash
        join read_parquet('{q(bts)}') b using(block_number)
        """
    )
    audit = con.execute(
        """
        select
            count(*) n_rows,
            count(distinct condition_id) conditions,
            count(distinct sig_market_id) sig_markets,
            count(distinct block_number) blocks,
            min(ts) min_ts,
            max(ts) max_ts,
            count(*) filter(where block_number is null) missing_blocks,
            count(*) filter(where block_ts is null) missing_block_ts,
            count(*) filter(where ts<>block_ts) timestamp_mismatch,
            count(*) filter(where mapping_class is null) missing_mapping_class,
            count(*) filter(where mapping_direction is null) missing_mapping_direction
        from direct_rows
        """
    ).fetchone()
    dup = int(
        con.execute(
            """
            select count(*) from (
                select block_number,log_index,count(*) n
                from direct_rows group by 1,2 having n>1
            )
            """
        ).fetchone()[0]
    )
    if any(int(x) for x in (*audit[6:], dup)):
        raise RuntimeError(f"DATA-003 chronology hard gate failed: {audit}, dup={dup}")

    # Complete a block before exposing its target state.  For split selection we only
    # need the timestamp/block identity, never future price performance.
    con.execute(
        """
        create temp view direct_block_updates as
        select
            block_number,
            max(ts) ts,
            sig_market_id,
            any_value(mapping_class) mapping_class,
            count(distinct condition_id) conditions_updated
        from direct_rows
        group by 1,3
        """
    )
    direct_audit = {
        "rows": int(audit[0]),
        "conditions": int(audit[1]),
        "sig_markets": int(audit[2]),
        "blocks": int(audit[3]),
        "min_timestamp": int(audit[4]),
        "max_timestamp": int(audit[5]),
        "missing_block_numbers": int(audit[6]),
        "missing_block_timestamps": int(audit[7]),
        "timestamp_mismatches": int(audit[8]),
        "missing_mapping_class": int(audit[9]),
        "missing_mapping_direction": int(audit[10]),
        "duplicate_block_log_groups": dup,
        "upstream_gate_report_sha256": sha256(gate),
        "economic_fills_sha256": gatej["reconstruction"]["economic_fills_sha256"],
        "tx_block_sha256": gatej["tx_block_sha256"],
        "block_timestamp_sha256": gatej["block_timestamp_sha256"],
    }
    return con, direct_audit


def make_split(con: duckdb.DuckDBPyConnection) -> dict:
    p82 = pct_direct_row(con, "direct_block_updates", FINAL_ANCHOR_FRAC)
    final_start = next_midnight(p82[0])
    con.execute(
        f"""
        create temp view direct_prefinal as
        select * from direct_block_updates where ts < {final_start}
        """
    )
    p65 = pct_direct_row(con, "direct_prefinal", DEV_ANCHOR_FRAC_PREFINAL)
    dev_start = next_midnight(p65[0])
    mm = con.execute(
        "select min(ts),max(ts) from direct_block_updates"
    ).fetchone()
    if not (int(mm[0]) < dev_start < final_start <= int(mm[1])):
        raise RuntimeError(
            f"invalid chronological split min={mm[0]} dev={dev_start} "
            f"final={final_start} max={mm[1]}"
        )

    p = MAX_FUTURE_HORIZON_SECONDS
    counts = con.execute(
        f"""
        select
            count(*) filter(where ts < {dev_start}-{p}) train_rows,
            count(*) filter(
                where ts >= {dev_start}
                  and ts < {final_start}-{p}
            ) dev_rows,
            count(*) filter(where ts >= {final_start}) final_rows,
            count(distinct sig_market_id) filter(
                where ts < {dev_start}-{p}
            ) train_markets,
            count(distinct sig_market_id) filter(
                where ts >= {dev_start}
                  and ts < {final_start}-{p}
            ) dev_markets,
            count(distinct sig_market_id) filter(
                where ts >= {final_start}
            ) final_markets
        from direct_block_updates
        """
    ).fetchone()
    if min(int(counts[i]) for i in range(3)) <= 0:
        raise RuntimeError(f"empty split: {counts}")

    by_class = con.execute(
        f"""
        select
            mapping_class,
            count(*) filter(where ts < {dev_start}-{p}) train_rows,
            count(*) filter(
                where ts >= {dev_start}
                  and ts < {final_start}-{p}
            ) dev_rows,
            count(*) filter(where ts >= {final_start}) final_rows
        from direct_block_updates
        group by 1 order by 1
        """
    ).fetchdf().to_dict("records")

    return {
        "schema_version": 1,
        "experiment_id": "R3-FV-001",
        "split_status": "FROZEN_BEFORE_PERFORMANCE",
        "selection_inputs": [
            "DATA-003 block-corrected direct update timestamps",
            "direct update row support",
            "direct SIG-market coverage",
        ],
        "performance_or_future_target_metrics_accessed": False,
        "ordering": ["block_number", "log_index"],
        "same_block_policy": (
            "An inference at target block B may use source state only from blocks < B. "
            "Block B is exposed only after completion."
        ),
        "selection_rule": {
            "final_anchor_fraction": FINAL_ANCHOR_FRAC,
            "final_start_rule": (
                "first UTC midnight strictly after floor(82% * N) block-complete "
                "direct-update row timestamp"
            ),
            "dev_anchor_fraction_of_prefinal": DEV_ANCHOR_FRAC_PREFINAL,
            "dev_start_rule": (
                "first UTC midnight strictly after floor(65% * N_prefinal) "
                "block-complete direct-update row timestamp"
            ),
            "boundary_purge_seconds": MAX_FUTURE_HORIZON_SECONDS,
            "maximum_permitted_future_evaluation_horizon_seconds": (
                MAX_FUTURE_HORIZON_SECONDS
            ),
        },
        "dev_start_epoch": dev_start,
        "dev_start_utc": datetime.fromtimestamp(
            dev_start, tz=timezone.utc
        ).isoformat(),
        "final_start_epoch": final_start,
        "final_start_utc": datetime.fromtimestamp(
            final_start, tz=timezone.utc
        ).isoformat(),
        "train_rows": int(counts[0]),
        "dev_rows": int(counts[1]),
        "final_rows": int(counts[2]),
        "train_sig_markets": int(counts[3]),
        "dev_sig_markets": int(counts[4]),
        "final_sig_markets": int(counts[5]),
        "mapping_class_support": by_class,
        "final_opened": False,
    }


def data004_economic_audit(root: Path) -> tuple[pd.DataFrame, dict]:
    files = sorted(root.rglob("fills/date=*/part-*.parquet"))
    if not files:
        raise RuntimeError("no DATA-004 fill parquet files")
    sql = ",".join("'" + q(p) + "'" for p in files)
    con = duckdb.connect()
    con.execute("pragma threads=4")
    df = con.execute(
        f"""
        select
            cast(block_number as bigint) block_number,
            cast(log_index as bigint) log_index,
            cast("timestamp" as bigint) ts,
            cast(condition_id as varchar) condition_id,
            cast(market_id as varchar) market_id,
            cast(event_id as varchar) event_id,
            cast(token_id as varchar) token_id,
            cast(outcome_label as varchar) outcome_label,
            cast(price as double) price,
            upper(cast(order_role as varchar)) order_role,
            upper(cast(acquisition_tier as varchar)) acquisition_tier,
            cast(contract_archetype as varchar) contract_archetype,
            cast(mathematical_class as varchar) mathematical_class,
            cast(relationship_class_set_json as varchar) relationship_class_set_json,
            cast(graph_relationship_classes_json as varchar) graph_relationship_classes_json,
            cast(sig_market_ids_json as varchar) sig_market_ids_json,
            cast(sig_exchange_ids_json as varchar) sig_exchange_ids_json
        from read_parquet([{sql}], union_by_name=true)
        where upper(cast(order_role as varchar))='TAKER'
        order by block_number,log_index
        """
    ).fetchdf()
    con.close()
    if df.empty:
        raise RuntimeError("no DATA-004 TAKER economic fills")
    df["p_yes"] = df["price"].where(
        df["outcome_label"].str.upper().eq("YES"), 1.0 - df["price"]
    )
    missing = int(df[["block_number", "log_index"]].isna().any(axis=1).sum())
    dup = int(df.duplicated(["block_number", "log_index"]).sum())
    if missing or dup:
        raise RuntimeError(
            f"DATA-004 economic chronology invalid missing={missing} duplicate={dup}"
        )
    audit = {
        "participant_fill_rows_upstream": 231964,
        "economic_taker_rows": int(len(df)),
        "conditions": int(df["condition_id"].nunique()),
        "markets": int(df["market_id"].nunique()),
        "blocks": int(df["block_number"].nunique()),
        "min_timestamp": int(df["ts"].min()),
        "max_timestamp": int(df["ts"].max()),
        "missing_order_keys": missing,
        "duplicate_block_log_keys": dup,
        "p0_rows": int(df["acquisition_tier"].eq("P0").sum()),
        "p1_rows": int(df["acquisition_tier"].eq("P1").sum()),
        "note": (
            "Participant rows are collapsed to the unique active/taker side of each "
            "(transaction, condition) economic match group for state reconstruction."
        ),
    }
    return df, audit


def graph_semantic_summary(bundle: Path, data004: pd.DataFrame) -> tuple[dict, pd.DataFrame]:
    market_graph = pd.read_csv(one(bundle, "ETS_MARKET_GRAPH.csv"), low_memory=False)
    anchors = pd.read_csv(one(bundle, "ETS_SIG_ANCHOR_GRAPH.csv"), low_memory=False)
    acquisition = pd.read_csv(one(bundle, "ETS_FILL_ACQUISITION.csv"), low_memory=False)

    summary = {
        "market_graph_columns": list(market_graph.columns),
        "market_graph_rows": int(len(market_graph)),
        "anchor_graph_columns": list(anchors.columns),
        "anchor_rows": int(len(anchors)),
        "fill_acquisition_columns": list(acquisition.columns),
        "fill_acquisition_rows": int(len(acquisition)),
    }
    for col in [
        "relationship_type",
        "relationship_class",
        "relationship",
        "information_dimension",
        "independent_information_dimension",
        "mathematical_class",
        "hard_or_soft",
        "structural_status",
    ]:
        if col in market_graph.columns:
            summary[f"market_graph_{col}_counts"] = (
                market_graph[col]
                .fillna("<NULL>")
                .astype(str)
                .value_counts()
                .head(100)
                .to_dict()
            )
    for col in [
        "independent_information_dimension",
        "fv_coverage",
        "identifiability_class",
        "existing_direct_mapping_class",
        "adds_beyond_direct",
    ]:
        if col in anchors.columns:
            summary[f"anchor_{col}_counts"] = (
                anchors[col]
                .fillna("<NULL>")
                .astype(str)
                .value_counts()
                .head(100)
                .to_dict()
            )

    # Conservative machine-generated seed.  This is intentionally not the final
    # LOO-FAMILY set: unresolved exact-structural ids remain flagged for semantic
    # classification before model research.
    p1_by_target: dict[str, set[str]] = defaultdict(set)
    for row in data004.itertuples(index=False):
        if str(row.acquisition_tier).upper() != "P1":
            continue
        if str(row.contract_archetype).upper() != "RACE_WINNER":
            continue
        for sid in parse_jsonish(row.sig_market_ids_json):
            p1_by_target[str(sid)].add(str(row.market_id))

    rows = []
    for a in anchors.itertuples(index=False):
        sid = str(getattr(a, "sig_market_id"))
        direct_ids = sorted(
            {str(x) for x in parse_jsonish(getattr(a, "existing_direct_pm_market_ids_json", ""))}
        )
        exact_ids = sorted(
            {str(x) for x in parse_jsonish(getattr(a, "v2_exact_structural_pm_ids_json", ""))}
        )
        p1_ids = sorted(p1_by_target.get(sid, set()))
        excluded = []
        for mid in direct_ids:
            excluded.append(
                {
                    "market_id": mid,
                    "reason": "ACCEPTED_DIRECT_MAPPING",
                    "source": "DATA-003 accepted crosswalk / R2.5 anchor graph",
                }
            )
        for mid in p1_ids:
            excluded.append(
                {
                    "market_id": mid,
                    "reason": "P1_DIRECT_WINNER_REFRESH_OF_TARGET",
                    "source": "DATA-004 P1 RACE_WINNER linked to same SIG target",
                }
            )
        rows.append(
            {
                "sig_market_id": sid,
                "sig_exchange_id": str(getattr(a, "sig_exchange_id")),
                "mapping_class": str(getattr(a, "existing_direct_mapping_class")),
                "direct_information_family_seed_json": json.dumps(excluded, sort_keys=True),
                "direct_market_ids_json": json.dumps(direct_ids),
                "p1_direct_winner_ids_json": json.dumps(p1_ids),
                "exact_structural_ids_pending_semantic_classification_json": json.dumps(
                    [x for x in exact_ids if x not in set(p1_ids)]
                ),
                "independent_information_dimension": str(
                    getattr(a, "independent_information_dimension", "")
                ),
                "adds_beyond_direct": str(getattr(a, "adds_beyond_direct", "")),
                "status": "SEED_ONLY_PENDING_EXACT_FAMILY_SEMANTIC_CLASSIFICATION",
            }
        )
    family_seed = pd.DataFrame(rows)
    summary["family_seed_targets"] = int(len(family_seed))
    summary["family_seed_p1_exclusions"] = int(
        sum(len(parse_jsonish(x)) for x in family_seed["p1_direct_winner_ids_json"])
    )
    summary["family_seed_pending_exact_ids"] = int(
        sum(
            len(parse_jsonish(x))
            for x in family_seed[
                "exact_structural_ids_pending_semantic_classification_json"
            ]
        )
    )
    return summary, family_seed


def main() -> None:
    root = locate_data004_root()
    package = json.loads((root / "MANIFEST.json").read_text())
    quality = json.loads((root / "QUALITY.json").read_text())
    if package.get("version") != "v2" or package.get("status") != "ACCEPTED_V2":
        raise RuntimeError("wrong DATA-004 package")
    if quality.get("all_gates_pass") is not True:
        raise RuntimeError("DATA-004 quality gate failed")

    con, direct_audit = build_direct_chronology()
    split = make_split(con)
    con.close()

    data004, data004_audit = data004_economic_audit(root)
    bundle = extract_graph_bundle(root)
    semantic_summary, family_seed = graph_semantic_summary(bundle, data004)

    split_path = OUT / "R3_SPLIT_MANIFEST.json"
    split_path.write_text(json.dumps(split, indent=2, sort_keys=True) + "\n")
    direct_path = OUT / "DIRECT_CHRONOLOGY_AUDIT.json"
    direct_path.write_text(json.dumps(direct_audit, indent=2, sort_keys=True) + "\n")
    d4_path = OUT / "DATA004_ECONOMIC_AUDIT.json"
    d4_path.write_text(json.dumps(data004_audit, indent=2, sort_keys=True) + "\n")
    semantic_path = OUT / "SEMANTIC_GRAPH_SUMMARY.json"
    semantic_path.write_text(
        json.dumps(semantic_summary, indent=2, sort_keys=True, default=str) + "\n"
    )
    family_path = OUT / "DIRECT_INFORMATION_FAMILY_SEED.csv"
    family_seed.to_csv(family_path, index=False)

    result = {
        "schema_version": 1,
        "stage": "R3-FV-001_PREPARE_NONPREDICTIVE",
        "performance_or_future_target_metrics_accessed": False,
        "final_opened": False,
        "split_manifest_sha256": sha256(split_path),
        "dev_start_utc": split["dev_start_utc"],
        "final_start_utc": split["final_start_utc"],
        "train_rows": split["train_rows"],
        "dev_rows": split["dev_rows"],
        "final_rows": split["final_rows"],
        "train_sig_markets": split["train_sig_markets"],
        "dev_sig_markets": split["dev_sig_markets"],
        "final_sig_markets": split["final_sig_markets"],
        "direct_rows": direct_audit["rows"],
        "data004_economic_taker_rows": data004_audit["economic_taker_rows"],
        "graph_rows": semantic_summary["market_graph_rows"],
        "anchor_rows": semantic_summary["anchor_rows"],
        "family_seed_targets": semantic_summary["family_seed_targets"],
        "family_seed_pending_exact_ids": semantic_summary[
            "family_seed_pending_exact_ids"
        ],
        "semantic_graph_summary_sha256": sha256(semantic_path),
        "family_seed_sha256": sha256(family_path),
    }
    print("R3_SPLIT_MANIFEST_JSON=" + json.dumps(split, sort_keys=True), flush=True)
    print("R3_SEMANTIC_GRAPH_SUMMARY_JSON=" + json.dumps(semantic_summary, sort_keys=True, default=str), flush=True)
    print("R3_PREPARE_RESULT=" + json.dumps(result, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
