# ruff: noqa
from __future__ import annotations

import ast
import json
from pathlib import Path

import duckdb
import pandas as pd

OUT = Path("/kaggle/working/r3_fv_001_p0_support")
OUT.mkdir(parents=True, exist_ok=True)

DATA004_SLUG = "sig-cup-data-004-ets-p0p1-fills"
BLOCK_GATE_FRAGMENT = "005b-data003-block-gate"
PREPARE_FRAGMENT = "r3-fv-001-prepare-v2"


def q(path: Path) -> str:
    return str(path).replace("'", "''")


def idstr(value) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except Exception:
        pass
    s = str(value).strip()
    if s.endswith(".0") and s[:-2].isdigit():
        return s[:-2]
    return s


def parse_jsonish(value) -> list[str]:
    if value is None:
        return []
    try:
        if pd.isna(value):
            return []
    except Exception:
        pass
    if isinstance(value, (list, tuple, set)):
        return [idstr(x) for x in value if idstr(x)]
    s = str(value).strip()
    if not s:
        return []
    for loader in (json.loads, ast.literal_eval):
        try:
            obj = loader(s)
            if isinstance(obj, (list, tuple, set)):
                return [idstr(x) for x in obj if idstr(x)]
            if obj is None:
                return []
            return [idstr(obj)]
        except Exception:
            continue
    return []


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


def graph_bundle(root: Path) -> Path:
    mounted = root / "full_frozen_universe"
    if mounted.is_dir():
        return mounted
    matches = list(root.rglob("ETS_FILL_ACQUISITION.csv"))
    if len(matches) == 1:
        return matches[0].parent
    raise RuntimeError("DATA-004 semantic graph is not mounted")


def load_split() -> dict:
    split = json.loads(locate("R3_SPLIT_MANIFEST.json", PREPARE_FRAGMENT).read_text())
    if split.get("final_opened") is not False:
        raise RuntimeError("R3 split says FINAL opened")
    if split.get("performance_or_future_target_metrics_accessed") is not False:
        raise RuntimeError("R3 split was not frozen outcome-blind")
    return split


def load_source_support(root: Path, split: dict) -> pd.DataFrame:
    files = sorted(root.rglob("fills/date=*/part-*.parquet"))
    if not files:
        raise RuntimeError("no DATA-004 fill parquet files")
    sql = ",".join("'" + q(p) + "'" for p in files)
    dev_start = int(split["dev_start_epoch"])
    final_start = int(split["final_start_epoch"])
    purge = int(split["selection_rule"]["boundary_purge_seconds"])
    train_cut = dev_start - purge
    dev_cut = final_start - purge

    con = duckdb.connect()
    con.execute("pragma threads=4")
    df = con.execute(
        f"""
        with x as (
            select
                cast(market_id as varchar) market_id,
                cast("timestamp" as bigint) ts,
                cast(block_number as bigint) block_number,
                cast(log_index as bigint) log_index
            from read_parquet([{sql}], union_by_name=true)
            where upper(cast(order_role as varchar))='TAKER'
              and cast("timestamp" as bigint) < {final_start}
        )
        select
            market_id,
            count(*) filter (where ts < {train_cut}) train_rows,
            count(distinct block_number) filter (where ts < {train_cut}) train_blocks,
            count(*) filter (where ts >= {dev_start} and ts < {dev_cut}) dev_rows,
            count(distinct block_number) filter (
                where ts >= {dev_start} and ts < {dev_cut}
            ) dev_blocks,
            min(ts) filter (where ts < {train_cut}) train_first_ts,
            max(ts) filter (where ts < {train_cut}) train_last_ts,
            min(ts) filter (where ts >= {dev_start} and ts < {dev_cut}) dev_first_ts,
            max(ts) filter (where ts >= {dev_start} and ts < {dev_cut}) dev_last_ts,
            min(ts) first_prefinal_ts,
            max(ts) last_prefinal_ts
        from x
        group by market_id
        """
    ).fetchdf()
    con.close()
    df["market_id"] = df["market_id"].map(idstr)
    return df


def load_target_events(split: dict) -> pd.DataFrame:
    econ = locate("economic_fills_DATA003.parquet", BLOCK_GATE_FRAGMENT)
    txb = locate("tx_block_DATA003.parquet", BLOCK_GATE_FRAGMENT)
    bts = locate("block_timestamp.parquet", BLOCK_GATE_FRAGMENT)
    gate = json.loads(
        locate("data003_block_gate_report.json", BLOCK_GATE_FRAGMENT).read_text()
    )
    if gate.get("all_hard_gates_pass") is not True:
        raise RuntimeError("DATA-003 block gate failed")

    dev_start = int(split["dev_start_epoch"])
    final_start = int(split["final_start_epoch"])
    purge = int(split["selection_rule"]["boundary_purge_seconds"])
    train_cut = dev_start - purge
    dev_cut = final_start - purge

    con = duckdb.connect()
    con.execute("pragma threads=4")
    df = con.execute(
        f"""
        with rows as (
            select
                cast(e.timestamp as bigint) ts,
                cast(t.block_number as bigint) block_number,
                cast(e.log_index as bigint) log_index,
                cast(e.sig_market_id as varchar) sig_market_id,
                upper(cast(e.mapping_class as varchar)) mapping_class,
                upper(cast(e.mapping_direction as varchar)) mapping_direction
            from read_parquet('{q(econ)}') e
            join read_parquet('{q(txb)}') t
              on lower(cast(e.tx_hash as varchar))=t.tx_hash
            join read_parquet('{q(bts)}') b using(block_number)
            where upper(cast(e.mapping_class as varchar)) in ('EXACT','NEAR')
              and upper(cast(e.mapping_direction as varchar)) in ('SAME','COMPLEMENT')
              and cast(e.timestamp as bigint) < {final_start}
        ),
        events as (
            select
                max(ts) ts,
                block_number,
                sig_market_id
            from rows
            group by block_number, sig_market_id
        )
        select
            sig_market_id,
            count(*) filter (where ts < {train_cut}) train_target_events,
            count(*) filter (where ts >= {dev_start} and ts < {dev_cut}) dev_target_events,
            count(distinct block_number) filter (where ts < {train_cut}) train_target_blocks,
            count(distinct block_number) filter (
                where ts >= {dev_start} and ts < {dev_cut}
            ) dev_target_blocks
        from events
        group by sig_market_id
        """
    ).fetchdf()
    con.close()
    df["sig_market_id"] = df["sig_market_id"].map(idstr)
    return df


def main() -> None:
    split = load_split()
    root = locate_data004_root()
    bundle = graph_bundle(root)

    acquisition = pd.read_csv(bundle / "ETS_FILL_ACQUISITION.csv", low_memory=False)
    p0 = acquisition[acquisition["acquisition_class"].astype(str) == "FILLS_P0"].copy()
    if len(p0) != 210:
        raise RuntimeError(f"expected 210 P0 rows, got {len(p0)}")

    p0["market_id"] = p0["market_id"].map(idstr)
    p0["event_id"] = p0["event_id"].map(idstr)
    p0["linked_sig_ids"] = p0["sig_market_ids_json"].map(parse_jsonish)

    source = load_source_support(root, split)
    targets = load_target_events(split)
    target_by_id = targets.set_index("sig_market_id").to_dict("index")

    market = p0.merge(source, on="market_id", how="left")
    fill_cols = [
        "train_rows", "train_blocks", "dev_rows", "dev_blocks",
        "train_first_ts", "train_last_ts", "dev_first_ts", "dev_last_ts",
        "first_prefinal_ts", "last_prefinal_ts",
    ]
    for col in fill_cols:
        if col not in market:
            market[col] = 0
    for col in ["train_rows", "train_blocks", "dev_rows", "dev_blocks"]:
        market[col] = market[col].fillna(0).astype(int)

    market["has_train"] = market["train_rows"] > 0
    market["has_dev"] = market["dev_rows"] > 0

    group_rows = []
    for (event_id, event_slug), g in market.groupby(["event_id", "event_slug"], sort=False):
        sig_ids = sorted({sid for xs in g["linked_sig_ids"] for sid in xs})
        train_targets = 0
        dev_targets = 0
        train_target_ids = 0
        dev_target_ids = 0
        for sid in sig_ids:
            rec = target_by_id.get(sid, {})
            tr = int(rec.get("train_target_events", 0) or 0)
            dv = int(rec.get("dev_target_events", 0) or 0)
            train_targets += tr
            dev_targets += dv
            train_target_ids += int(tr > 0)
            dev_target_ids += int(dv > 0)

        group_rows.append(
            {
                "event_id": event_id,
                "event_slug": event_slug,
                "event_title": str(g["event_title"].iloc[0]),
                "contract_archetype": str(g["contract_archetype"].iloc[0]),
                "mathematical_class": str(g["mathematical_class"].iloc[0]),
                "market_count": int(len(g)),
                "markets_with_train_fill": int(g["has_train"].sum()),
                "markets_with_dev_fill": int(g["has_dev"].sum()),
                "train_rows": int(g["train_rows"].sum()),
                "train_blocks": int(g["train_blocks"].sum()),
                "dev_rows": int(g["dev_rows"].sum()),
                "dev_blocks": int(g["dev_blocks"].sum()),
                "linked_sig_ids": len(sig_ids),
                "linked_sig_with_train_target": train_target_ids,
                "linked_sig_with_dev_target": dev_target_ids,
                "train_target_events": train_targets,
                "dev_target_events": dev_targets,
                "full_component_train_ready": bool(g["has_train"].all()),
                "full_component_dev_ready": bool(g["has_dev"].all()),
                "train_dev_source_ready": bool(g["has_train"].all() and g["has_dev"].all()),
                "train_dev_end_to_end_ready": bool(
                    g["has_train"].all()
                    and g["has_dev"].all()
                    and train_targets > 0
                    and dev_targets > 0
                ),
                "market_ids_json": json.dumps(sorted(g["market_id"].tolist())),
                "linked_sig_ids_json": json.dumps(sig_ids),
            }
        )

    groups = pd.DataFrame(group_rows).sort_values(
        ["train_dev_end_to_end_ready", "train_rows", "dev_rows"],
        ascending=[False, False, False],
    )

    summary = {
        "schema_version": 1,
        "experiment_id": "R3-FV-001",
        "stage": "P0_TRAIN_DEV_SUPPORT_AUDIT",
        "source_dataset": "DATA-004",
        "target_dataset": "DATA-003",
        "data001_used": False,
        "historical_analogue_families_used": False,
        "final_opened": False,
        "final_rows_accessed": 0,
        "p0_market_rows": int(len(market)),
        "p0_event_groups": int(len(groups)),
        "groups_full_train_source": int(groups["full_component_train_ready"].sum()),
        "groups_full_dev_source": int(groups["full_component_dev_ready"].sum()),
        "groups_train_dev_source_ready": int(groups["train_dev_source_ready"].sum()),
        "groups_train_dev_end_to_end_ready": int(
            groups["train_dev_end_to_end_ready"].sum()
        ),
        "end_to_end_ready_event_ids": groups.loc[
            groups["train_dev_end_to_end_ready"], "event_id"
        ].astype(str).tolist(),
        "source_train_cut_epoch": int(split["dev_start_epoch"])
        - int(split["selection_rule"]["boundary_purge_seconds"]),
        "source_dev_start_epoch": int(split["dev_start_epoch"]),
        "source_dev_cut_epoch": int(split["final_start_epoch"])
        - int(split["selection_rule"]["boundary_purge_seconds"]),
    }

    groups.to_csv(OUT / "P0_EVENT_GROUP_SUPPORT.csv", index=False)
    market.to_csv(OUT / "P0_MARKET_SUPPORT.csv", index=False)
    (OUT / "P0_SUPPORT_SUMMARY.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n"
    )

    ready = groups[groups["train_dev_end_to_end_ready"]][
        [
            "event_id", "event_title", "contract_archetype", "market_count",
            "train_rows", "dev_rows", "train_target_events", "dev_target_events",
        ]
    ].to_dict("records")
    print(
        "R3_P0_SUPPORT_RESULT="
        + json.dumps(
            {
                "groups": int(len(groups)),
                "end_to_end_ready": int(
                    groups["train_dev_end_to_end_ready"].sum()
                ),
                "ready": ready,
                "final_opened": False,
            },
            sort_keys=True,
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
