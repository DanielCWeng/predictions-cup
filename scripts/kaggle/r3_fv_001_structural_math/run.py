# ruff: noqa
from __future__ import annotations

import ast
import json
import math
from collections import defaultdict
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

OUT = Path("/kaggle/working/r3_fv_001_structural_math")
OUT.mkdir(parents=True, exist_ok=True)

DATA004_SLUG = "sig-cup-data-004-ets-p0p1-fills"
BLOCK_GATE_FRAGMENT = "005b-data003-block-gate"
PREPARE_FRAGMENT = "r3-fv-001-prepare-v2"
PRIMARY_MAX_AGE_S = 24 * 3600
SENSITIVITY_AGES_S = (6 * 3600, 24 * 3600, 72 * 3600)
BOOTSTRAPS = 1000
RNG_SEED = 20260929

GEORGIA_IDS = ["3729337", "3729338", "3729339", "3729340"]
SH_IDS = [
    "2683267", "2683268", "2683269",
    "2683264", "2683265", "2683266",
    "2683260", "2683261", "2683262", "2683263",
    "2683257", "2683258", "2683259",
]
GOV_COUNT_IDS = ["907686", "907687", "907688", "907689", "907690", "907691", "907692"]


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


def parse_relationship_map(value) -> dict[str, list[str]]:
    if value is None:
        return {}
    try:
        if pd.isna(value):
            return {}
    except Exception:
        pass
    try:
        obj = json.loads(str(value))
    except Exception:
        return {}
    if not isinstance(obj, dict):
        return {}
    out = {}
    for key, values in obj.items():
        if isinstance(values, (list, tuple, set)):
            out[str(key)] = [idstr(x) for x in values if idstr(x)]
    return out


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
    matches = list(root.rglob("ETS_MARKET_GRAPH.csv"))
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


def load_direct_events(final_start: int) -> pd.DataFrame:
    econ = locate("economic_fills_DATA003.parquet", BLOCK_GATE_FRAGMENT)
    txb = locate("tx_block_DATA003.parquet", BLOCK_GATE_FRAGMENT)
    bts = locate("block_timestamp.parquet", BLOCK_GATE_FRAGMENT)
    gate = json.loads(
        locate("data003_block_gate_report.json", BLOCK_GATE_FRAGMENT).read_text()
    )
    if gate.get("all_hard_gates_pass") is not True:
        raise RuntimeError("DATA-003 block gate failed")

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
                upper(cast(e.mapping_direction as varchar)) mapping_direction,
                case
                    when upper(cast(e.mapping_direction as varchar))='SAME'
                        then cast(e.p_yes as double)
                    when upper(cast(e.mapping_direction as varchar))='COMPLEMENT'
                        then 1.0-cast(e.p_yes as double)
                    else null
                end aligned_p
            from read_parquet('{q(econ)}') e
            join read_parquet('{q(txb)}') t
              on lower(cast(e.tx_hash as varchar))=t.tx_hash
            join read_parquet('{q(bts)}') b using(block_number)
            where upper(cast(e.mapping_class as varchar)) in ('EXACT','NEAR')
              and upper(cast(e.mapping_direction as varchar)) in ('SAME','COMPLEMENT')
        )
        select
            block_number,
            max(ts) ts,
            sig_market_id,
            arg_max(aligned_p,log_index) p_target,
            arg_max(mapping_class,log_index) mapping_class
        from rows
        where aligned_p between 0 and 1
        group by 1,3
        order by 1,3
        """
    ).fetchdf()
    con.close()
    if df.empty:
        raise RuntimeError("no eligible DATA-003 direct events")
    df["sig_market_id"] = df["sig_market_id"].map(idstr)
    df = df[df["ts"] < int(final_start)].copy()
    if (df["ts"] >= int(final_start)).any():
        raise RuntimeError("FINAL target rows entered structural-math discovery")
    return df


def load_source_rows(root: Path, final_start: int) -> pd.DataFrame:
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
            cast(market_id as varchar) market_id,
            case
                when upper(cast(outcome_label as varchar))='YES'
                    then cast(price as double)
                when upper(cast(outcome_label as varchar))='NO'
                    then 1.0-cast(price as double)
                else null
            end p_yes
        from read_parquet([{sql}], union_by_name=true)
        where upper(cast(order_role as varchar))='TAKER'
          and cast(price as double) between 0 and 1
        order by block_number,log_index
        """
    ).fetchdf()
    con.close()
    df["market_id"] = df["market_id"].map(idstr)
    df = df[(df["ts"] < int(final_start)) & df["p_yes"].notna()].copy()
    if df.duplicated(["block_number", "log_index"]).any():
        raise RuntimeError("DATA-004 TAKER chronology has duplicate block/log keys")
    return df


def load_semantics(root: Path):
    bundle = graph_bundle(root)
    anchors = pd.read_csv(bundle / "ETS_SIG_ANCHOR_GRAPH.csv", low_memory=False)
    components = json.loads((bundle / "ETS_COMPONENTS.json").read_text())
    family = pd.read_csv(
        locate("DIRECT_INFORMATION_FAMILY_SEED.csv", PREPARE_FRAGMENT),
        low_memory=False,
    )

    anchor_by_target = {}
    exact_equiv_by_target = {}
    for r in anchors.itertuples(index=False):
        target = idstr(r.sig_market_id)
        direct_ids = set(parse_jsonish(r.existing_direct_pm_market_ids_json))
        relationships = parse_relationship_map(r.v2_relationship_market_ids_json)
        exact_equiv = set(relationships.get("EXACT_EQUIVALENT", [])) - direct_ids
        anchor_by_target[target] = {
            "exchange_id": idstr(r.sig_exchange_id),
            "question": str(r.sig_question),
            "mapping_class": str(r.existing_direct_mapping_class),
            "adds": str(r.adds_beyond_direct),
            "relevant_ids": set(parse_jsonish(r.v2_relevant_pm_market_ids_json)),
            "direct_ids": direct_ids,
            "exact_equiv_ids": exact_equiv,
        }
        exact_equiv_by_target[target] = exact_equiv

    # Load the frozen family seed as a provenance gate even though corrected
    # LOO_PRICE uses only settlement-aligned EXACT_EQUIVALENT relationships.
    if family.empty:
        raise RuntimeError("DirectInformationFamily seed is empty")

    worked = {
        x["component"]: x for x in components.get("worked_rank_computations", [])
    }
    sh_key = next(k for k in worked if "Senate x House seat count" in k)
    sh_matrix = np.asarray(worked[sh_key]["matrix"], dtype=float)
    if sh_matrix.shape != (13, 16):
        raise RuntimeError(f"unexpected Senate-House matrix shape {sh_matrix.shape}")

    return anchor_by_target, exact_equiv_by_target, sh_matrix


class SourceLookup:
    def __init__(self, source: pd.DataFrame):
        self.by_market = {}
        for market_id, g in source.groupby("market_id", sort=False):
            g = g.sort_values(["block_number", "log_index"])
            self.by_market[str(market_id)] = (
                g["block_number"].to_numpy(dtype=np.int64),
                g["ts"].to_numpy(dtype=np.int64),
                g["p_yes"].to_numpy(dtype=float),
            )

    def latest_before(self, market_id: str, block: int):
        item = self.by_market.get(str(market_id))
        if item is None:
            return None
        blocks, ts, p = item
        idx = int(np.searchsorted(blocks, int(block), side="left") - 1)
        if idx < 0:
            return None
        return float(p[idx]), int(ts[idx]), int(blocks[idx])

    def component(
        self,
        market_ids: list[str] | set[str],
        block: int,
        target_ts: int,
        max_age_s: int,
    ):
        states = {}
        for market_id in market_ids:
            st = self.latest_before(str(market_id), int(block))
            if st is None:
                continue
            p, sts, sblock = st
            age = int(target_ts) - int(sts)
            if age < 0:
                raise RuntimeError("source timestamp is after target timestamp")
            if age <= int(max_age_s):
                states[str(market_id)] = {
                    "p": float(p),
                    "ts": int(sts),
                    "block": int(sblock),
                    "age_s": int(age),
                }
        return states


def project_simplex(v: np.ndarray) -> np.ndarray:
    v = np.asarray(v, dtype=float)
    if len(v) == 0:
        return v
    u = np.sort(v)[::-1]
    cssv = np.cumsum(u)
    idx = np.arange(1, len(v) + 1)
    cond = u - (cssv - 1.0) / idx > 0
    if not np.any(cond):
        return np.full_like(v, 1.0 / len(v))
    rho = int(np.where(cond)[0][-1])
    theta = (cssv[rho] - 1.0) / float(rho + 1)
    return np.maximum(v - theta, 0.0)


def project_kl_simplex(v: np.ndarray, eps: float = 1e-9) -> np.ndarray:
    x = np.maximum(np.asarray(v, dtype=float), eps)
    return x / float(x.sum())


def split_name(ts: int, split: dict) -> str | None:
    dev_start = int(split["dev_start_epoch"])
    final_start = int(split["final_start_epoch"])
    purge = int(split["selection_rule"]["boundary_purge_seconds"])
    if ts < dev_start - purge:
        return "TRAIN"
    if dev_start <= ts < final_start - purge:
        return "DEV"
    if ts >= final_start:
        raise RuntimeError("FINAL target row reached split assignment")
    return None


def error_metrics(frame: pd.DataFrame, pred_col: str) -> dict:
    if frame.empty:
        return {"status": "NO_ROWS", "rows": 0}
    y = frame["y"].to_numpy(dtype=float)
    prev = frame["prev"].to_numpy(dtype=float)
    pred = frame[pred_col].to_numpy(dtype=float)
    mask = np.isfinite(y) & np.isfinite(prev) & np.isfinite(pred)
    y, prev, pred = y[mask], prev[mask], pred[mask]
    if len(y) == 0:
        return {"status": "NO_FINITE_ROWS", "rows": 0}
    base_abs = np.abs(y - prev)
    model_abs = np.abs(y - pred)
    base_mae = float(base_abs.mean())
    model_mae = float(model_abs.mean())
    imp = base_mae - model_mae
    return {
        "status": "OK",
        "rows": int(len(y)),
        "targets": int(frame.loc[mask, "sig_market_id"].nunique()),
        "baseline_mae": base_mae,
        "model_mae": model_mae,
        "absolute_mae_improvement": imp,
        "relative_mae_improvement": float(imp / base_mae) if base_mae > 0 else None,
        "baseline_rmse": float(np.sqrt(np.mean((y - prev) ** 2))),
        "model_rmse": float(np.sqrt(np.mean((y - pred) ** 2))),
        "direction_accuracy": float(
            np.mean(np.sign(pred - prev) == np.sign(y - prev))
        ),
    }


def target_cluster_bootstrap(frame: pd.DataFrame, pred_col: str, seed: int) -> dict:
    if frame.empty:
        return {"status": "NO_ROWS"}
    f = frame[np.isfinite(frame[pred_col])].copy()
    if f.empty:
        return {"status": "NO_FINITE_ROWS"}
    f["improvement"] = np.abs(f["y"] - f["prev"]) - np.abs(
        f["y"] - f[pred_col]
    )
    grouped = f.groupby("sig_market_id")["improvement"].agg(["sum", "count"])
    if grouped.empty:
        return {"status": "NO_CLUSTERS"}
    sums = grouped["sum"].to_numpy(dtype=float)
    counts = grouped["count"].to_numpy(dtype=float)
    rng = np.random.default_rng(seed)
    draws = np.empty(BOOTSTRAPS)
    for i in range(BOOTSTRAPS):
        idx = rng.integers(0, len(grouped), size=len(grouped))
        draws[i] = sums[idx].sum() / counts[idx].sum()
    return {
        "status": "OK",
        "clusters": int(len(grouped)),
        "resamples": BOOTSTRAPS,
        "lower_95": float(np.quantile(draws, 0.025)),
        "median": float(np.quantile(draws, 0.5)),
        "upper_95": float(np.quantile(draws, 0.975)),
    }


def orientation_from_question(question: str) -> int | None:
    qn = question.lower()
    if "republican party" in qn or "republicans" in qn:
        return 1
    if "democratic party" in qn or "democrats" in qn:
        return -1
    return None


def georgia_target_kind(question: str) -> str | None:
    qn = question.lower()
    if "georgia" not in qn:
        return None
    if "governor" in qn:
        if "democratic" in qn:
            return "GOV_D"
        if "republican" in qn:
            return "GOV_R"
    if "senate" in qn:
        if "democratic" in qn:
            return "SEN_D"
        if "republican" in qn:
            return "SEN_R"
    return None


def georgia_bounds(states: dict[str, dict], kind: str):
    cells = {
        "DD": states.get("3729337"),
        "DR": states.get("3729339"),
        "RD": states.get("3729340"),
        "RR": states.get("3729338"),
    }
    in_cells = {
        "GOV_D": ("DD", "DR"),
        "GOV_R": ("RD", "RR"),
        "SEN_D": ("DD", "RD"),
        "SEN_R": ("DR", "RR"),
    }[kind]
    opp_cells = {
        "GOV_D": ("RD", "RR"),
        "GOV_R": ("DD", "DR"),
        "SEN_D": ("DR", "RR"),
        "SEN_R": ("DD", "RD"),
    }[kind]
    lower = sum(cells[x]["p"] for x in in_cells if cells[x] is not None)
    upper = 1.0 - sum(
        cells[x]["p"] for x in opp_cells if cells[x] is not None
    )
    lower = max(0.0, min(1.0, float(lower)))
    upper = max(0.0, min(1.0, float(upper)))
    return lower, upper, cells


def georgia_point_from_fine(q9: np.ndarray, kind: str) -> float:
    masks = {
        "GOV_D": [0, 1, 2],
        "GOV_R": [3, 4, 5],
        "SEN_D": [0, 3, 6],
        "SEN_R": [1, 4, 7],
    }
    return float(q9[masks[kind]].sum())


def georgia_complete_projection(cells: dict[str, dict], method: str):
    order = ["DD", "DR", "RD", "RR"]
    raw4 = np.asarray([cells[x]["p"] for x in order], dtype=float)
    residual = max(0.0, 1.0 - float(raw4.sum()))
    raw9 = np.asarray(
        [
            raw4[0],
            raw4[1],
            residual / 5.0,
            raw4[2],
            raw4[3],
            residual / 5.0,
            residual / 5.0,
            residual / 5.0,
            residual / 5.0,
        ],
        dtype=float,
    )
    if method == "QP":
        q9 = project_simplex(raw9)
    elif method == "KL":
        q9 = project_kl_simplex(raw9)
    else:
        raise ValueError(method)
    return q9, float(np.abs(q9 - raw9).sum()), float(raw4.sum())


def load_sh_matrix(sh_matrix: np.ndarray):
    if sh_matrix.shape != (13, 16):
        raise RuntimeError("frozen Senate-House matrix shape drift")
    row_sums = sh_matrix.sum(axis=1)
    if not np.all((row_sums == 1) | (row_sums == 2)):
        raise RuntimeError("unexpected Senate-House coarsening")
    if not np.all(sh_matrix.sum(axis=0) == 1):
        raise RuntimeError("Senate-House categories do not partition 16 fine cells")
    return sh_matrix


def maxent_fine_from_partition(qcat: np.ndarray, matrix: np.ndarray) -> np.ndarray:
    fine = np.zeros(matrix.shape[1], dtype=float)
    for j in range(matrix.shape[0]):
        members = np.flatnonzero(matrix[j] > 0.5)
        fine[members] += float(qcat[j]) / float(len(members))
    if not np.isclose(fine.sum(), 1.0, atol=1e-8):
        raise RuntimeError("MaxEnt fine state does not sum to one")
    return fine


def threshold_bounds_from_partition(
    qcat: np.ndarray,
    matrix: np.ndarray,
    fine_lower: np.ndarray,
    fine_upper: np.ndarray,
):
    lower = 0.0
    upper = 0.0
    for j in range(matrix.shape[0]):
        members = np.flatnonzero(matrix[j] > 0.5)
        lower += float(qcat[j]) * float(np.min(fine_lower[members]))
        upper += float(qcat[j]) * float(np.max(fine_upper[members]))
    return float(lower), float(upper)


def sh_thresholds(qcat: np.ndarray, matrix: np.ndarray):
    fine = maxent_fine_from_partition(qcat, matrix)
    house_point_weight = np.tile(np.asarray([0.0, 0.0, 5.0 / 15.0, 1.0]), 4)
    senate_point_weight = np.repeat(
        np.asarray([0.0, 0.0, 2.0 / 3.0, 1.0]), 4
    )
    house_low = np.tile(np.asarray([0.0, 0.0, 0.0, 1.0]), 4)
    house_high = np.tile(np.asarray([0.0, 0.0, 1.0, 1.0]), 4)
    senate_low = np.repeat(np.asarray([0.0, 0.0, 0.0, 1.0]), 4)
    senate_high = np.repeat(np.asarray([0.0, 0.0, 1.0, 1.0]), 4)

    h_l, h_u = threshold_bounds_from_partition(
        qcat, matrix, house_low, house_high
    )
    s_l, s_u = threshold_bounds_from_partition(
        qcat, matrix, senate_low, senate_high
    )
    return {
        "house_point": float(np.dot(fine, house_point_weight)),
        "house_lower": h_l,
        "house_upper": h_u,
        "senate_point": float(np.dot(fine, senate_point_weight)),
        "senate_lower": s_l,
        "senate_upper": s_u,
    }


GOV_BINS = [
    list(range(0, 22)),
    [22, 23],
    [24, 25],
    [26, 27],
    [28, 29],
    [30, 31],
    list(range(32, 51)),
]


def gov_expected_count(qcat: np.ndarray) -> float:
    return float(
        sum(
            float(qcat[j]) * float(np.mean(GOV_BINS[j]))
            for j in range(len(GOV_BINS))
        )
    )


def component_complete(
    lookup: SourceLookup,
    ids: list[str],
    block: int,
    ts: int,
    max_age_s: int,
):
    states = lookup.component(ids, block, ts, max_age_s)
    if len(states) != len(ids):
        return None, states
    return np.asarray([states[mid]["p"] for mid in ids], dtype=float), states


def source_age_summary(states: dict[str, dict]) -> dict:
    ages = [x["age_s"] for x in states.values()]
    blocks = [x["block"] for x in states.values()]
    tss = [x["ts"] for x in states.values()]
    return {
        "source_count": len(states),
        "source_age_max_s": int(max(ages)) if ages else None,
        "source_age_median_s": float(np.median(ages)) if ages else None,
        "source_latest_block": int(max(blocks)) if blocks else None,
        "source_latest_ts": int(max(tss)) if tss else None,
    }


def build_event_base(direct: pd.DataFrame, split: dict):
    out = []
    previous = {}
    for ev in direct.sort_values(["block_number", "sig_market_id"]).itertuples(index=False):
        target = idstr(ev.sig_market_id)
        prev = previous.get(target)
        sp = split_name(int(ev.ts), split)
        if prev is not None and sp is not None:
            prev_p, prev_block, prev_ts = prev
            out.append(
                {
                    "split": sp,
                    "sig_market_id": target,
                    "block_number": int(ev.block_number),
                    "ts": int(ev.ts),
                    "prev_block_number": int(prev_block),
                    "prev_ts": int(prev_ts),
                    "mapping_class": str(ev.mapping_class),
                    "prev": float(prev_p),
                    "y": float(ev.p_target),
                }
            )
        previous[target] = (
            float(ev.p_target),
            int(ev.block_number),
            int(ev.ts),
        )
    return pd.DataFrame(out)


def build_loo_price(
    base: pd.DataFrame,
    anchor_by_target: dict,
    exact_equiv_by_target: dict,
    lookup: SourceLookup,
):
    rows = []
    for ev in base.itertuples(index=False):
        ids = sorted(exact_equiv_by_target.get(str(ev.sig_market_id), set()))
        if not ids:
            continue
        states = lookup.component(
            ids, int(ev.block_number), int(ev.ts), PRIMARY_MAX_AGE_S
        )
        if not states:
            continue
        values = np.asarray([x["p"] for x in states.values()], dtype=float)
        pred = float(np.clip(values.mean(), 0.0, 1.0))
        row = ev._asdict()
        row.update(
            {
                "pred_qp": pred,
                "candidate_exact_equiv_sources": len(ids),
                **source_age_summary(states),
            }
        )
        rows.append(row)
    return pd.DataFrame(rows)


def build_georgia(
    base: pd.DataFrame,
    anchor_by_target: dict,
    lookup: SourceLookup,
):
    rows = []
    georgia_targets = {
        t
        for t, meta in anchor_by_target.items()
        if set(GEORGIA_IDS).issubset(meta["relevant_ids"])
        and georgia_target_kind(meta["question"]) is not None
    }
    for ev in base[base["sig_market_id"].isin(georgia_targets)].itertuples(index=False):
        meta = anchor_by_target[str(ev.sig_market_id)]
        kind = georgia_target_kind(meta["question"])
        states = lookup.component(
            GEORGIA_IDS,
            int(ev.block_number),
            int(ev.ts),
            PRIMARY_MAX_AGE_S,
        )
        lower, upper, cells = georgia_bounds(states, kind)
        raw_sum = float(sum(x["p"] for x in states.values()))
        raw_feasible = bool(raw_sum <= 1.0 + 1e-9 and lower <= upper + 1e-9)
        row = ev._asdict()
        row.update(
            {
                "target_kind": kind,
                "question": meta["question"],
                "lp_lower": lower,
                "lp_upper": upper,
                "lp_width": max(0.0, upper - lower),
                "lp_contains_direct": bool(lower - 1e-12 <= ev.y <= upper + 1e-12),
                "raw_observed_sum": raw_sum,
                "raw_feasible": raw_feasible,
                "pred_qp": np.nan,
                "pred_kl": np.nan,
                "qp_adjust_l1": np.nan,
                "kl_adjust_l1": np.nan,
                **source_age_summary(states),
            }
        )
        if len(states) == 4:
            for method, col in (("QP", "pred_qp"), ("KL", "pred_kl")):
                q9, adj, _ = georgia_complete_projection(cells, method)
                row[col] = georgia_point_from_fine(q9, kind)
                row[f"{method.lower()}_adjust_l1"] = adj
        rows.append(row)
    return pd.DataFrame(rows)


def build_chamber(
    base: pd.DataFrame,
    anchor_by_target: dict,
    lookup: SourceLookup,
    sh_matrix: np.ndarray,
):
    rows = []
    sh_set = set(SH_IDS)
    chamber_targets = {
        t
        for t, meta in anchor_by_target.items()
        if sh_set.issubset(meta["relevant_ids"])
        and ("u.s. house" in meta["question"].lower()
             or "u.s. senate" in meta["question"].lower())
    }
    for ev in base[base["sig_market_id"].isin(chamber_targets)].itertuples(index=False):
        meta = anchor_by_target[str(ev.sig_market_id)]
        raw, states = component_complete(
            lookup,
            SH_IDS,
            int(ev.block_number),
            int(ev.ts),
            PRIMARY_MAX_AGE_S,
        )
        if raw is None:
            continue
        q_qp = project_simplex(raw)
        q_kl = project_kl_simplex(raw)
        th_qp = sh_thresholds(q_qp, sh_matrix)
        th_kl = sh_thresholds(q_kl, sh_matrix)
        qn = meta["question"].lower()
        chamber = "HOUSE" if "house" in qn else "SENATE"
        orient = orientation_from_question(meta["question"])
        if orient is None:
            continue

        key = "house" if chamber == "HOUSE" else "senate"
        p_qp = th_qp[f"{key}_point"]
        p_kl = th_kl[f"{key}_point"]
        l_qp, u_qp = th_qp[f"{key}_lower"], th_qp[f"{key}_upper"]
        l_kl, u_kl = th_kl[f"{key}_lower"], th_kl[f"{key}_upper"]
        if orient < 0:
            p_qp, p_kl = 1.0 - p_qp, 1.0 - p_kl
            l_qp, u_qp = 1.0 - u_qp, 1.0 - l_qp
            l_kl, u_kl = 1.0 - u_kl, 1.0 - l_kl

        row = ev._asdict()
        row.update(
            {
                "question": meta["question"],
                "chamber": chamber,
                "party_orientation": orient,
                "pred_qp": float(p_qp),
                "pred_kl": float(p_kl),
                "lp_lower_qp": float(l_qp),
                "lp_upper_qp": float(u_qp),
                "lp_lower_kl": float(l_kl),
                "lp_upper_kl": float(u_kl),
                "raw_partition_sum": float(raw.sum()),
                "raw_coherence_gap": float(raw.sum() - 1.0),
                "qp_adjust_l1": float(np.abs(q_qp - raw).sum()),
                "kl_adjust_l1": float(np.abs(q_kl - raw).sum()),
                **source_age_summary(states),
            }
        )
        rows.append(row)
    return pd.DataFrame(rows)


def build_governor_inverse_rows(
    base: pd.DataFrame,
    anchor_by_target: dict,
    lookup: SourceLookup,
):
    eligible = {
        t
        for t, meta in anchor_by_target.items()
        if "governor" in meta["question"].lower()
        and orientation_from_question(meta["question"]) is not None
    }

    rows = []
    for ev in base[base["sig_market_id"].isin(eligible)].sort_values(
        ["block_number", "sig_market_id"]
    ).itertuples(index=False):
        meta = anchor_by_target[str(ev.sig_market_id)]
        raw_now, states_now = component_complete(
            lookup,
            GOV_COUNT_IDS,
            int(ev.block_number),
            int(ev.ts),
            PRIMARY_MAX_AGE_S,
        )
        raw_prev, states_prev = component_complete(
            lookup,
            GOV_COUNT_IDS,
            int(ev.prev_block_number),
            int(ev.prev_ts),
            PRIMARY_MAX_AGE_S,
        )
        if raw_now is None or raw_prev is None:
            continue
        orient = orientation_from_question(meta["question"])
        indices_now = {
            "QP": gov_expected_count(project_simplex(raw_now)),
            "KL": gov_expected_count(project_kl_simplex(raw_now)),
        }
        indices_prev = {
            "QP": gov_expected_count(project_simplex(raw_prev)),
            "KL": gov_expected_count(project_kl_simplex(raw_prev)),
        }
        age_now = source_age_summary(states_now)
        row = ev._asdict()
        row.update(
            {
                "question": meta["question"],
                "party_orientation": orient,
                "raw_partition_sum": float(raw_now.sum()),
                "raw_coherence_gap": float(raw_now.sum() - 1.0),
                "previous_raw_partition_sum": float(raw_prev.sum()),
                "source_age_max_s": age_now["source_age_max_s"],
                "source_age_median_s": age_now["source_age_median_s"],
                "source_latest_block": age_now["source_latest_block"],
                "source_latest_ts": age_now["source_latest_ts"],
            }
        )
        for method in ("QP", "KL"):
            row[f"k_{method.lower()}"] = indices_now[method]
            row[f"k_prev_{method.lower()}"] = indices_prev[method]
            row[f"x_{method.lower()}"] = float(
                orient * (indices_now[method] - indices_prev[method])
            )
        rows.append(row)
    return pd.DataFrame(rows)


def fit_inverse(train: pd.DataFrame, x_col: str):
    f = train[np.isfinite(train[x_col])].copy()
    if len(f) < 50:
        return None
    x = f[x_col].to_numpy(dtype=float)
    dy = (f["y"] - f["prev"]).to_numpy(dtype=float)
    denom = float(np.dot(x, x))
    if denom <= 1e-14:
        return None
    return float(np.dot(x, dy) / denom)


def evaluate_inverse(frame: pd.DataFrame, method: str, seed: int):
    x_col = f"x_{method.lower()}"
    if frame.empty or "split" not in frame.columns or x_col not in frame.columns:
        return {
            "status": "INSUFFICIENT_SUPPORT",
            "method": method,
            "train_rows": 0,
            "dev_rows": 0,
            "reason": "No complete current-universe governorship count-to-constituent rows under the frozen primary source-age rule.",
        }, pd.DataFrame()
    train = frame[frame["split"] == "TRAIN"].copy()
    dev = frame[frame["split"] == "DEV"].copy()
    beta = fit_inverse(train, x_col)
    if beta is None or dev.empty:
        return {
            "status": "INSUFFICIENT_SUPPORT",
            "method": method,
            "train_rows": int(len(train)),
            "dev_rows": int(len(dev)),
        }, pd.DataFrame()
    dev[f"pred_{method.lower()}"] = np.clip(
        dev["prev"] + beta * dev[x_col], 0.0, 1.0
    )
    pred_col = f"pred_{method.lower()}"
    metrics = error_metrics(dev, pred_col)
    metrics.update(
        {
            "method": method,
            "beta_train": beta,
            "train_rows": int(len(train)),
            "train_targets": int(train["sig_market_id"].nunique()),
            "dev_rows": int(len(dev)),
            "dev_targets": int(dev["sig_market_id"].nunique()),
            "bootstrap": target_cluster_bootstrap(dev, pred_col, seed),
        }
    )
    ordered = dev.sort_values(["ts", "block_number"])
    cut = max(1, len(ordered) // 2)
    metrics["dev_first_half"] = error_metrics(ordered.iloc[:cut], pred_col)
    metrics["dev_second_half"] = error_metrics(ordered.iloc[cut:], pred_col)
    return metrics, dev


def coverage_sensitivity(
    base: pd.DataFrame,
    anchor_by_target: dict,
    lookup: SourceLookup,
):
    specs = {
        "GEORGIA": (
            GEORGIA_IDS,
            {
                t
                for t, m in anchor_by_target.items()
                if set(GEORGIA_IDS).issubset(m["relevant_ids"])
                and georgia_target_kind(m["question"]) is not None
            },
        ),
        "SENATE_HOUSE": (
            SH_IDS,
            {
                t
                for t, m in anchor_by_target.items()
                if set(SH_IDS).issubset(m["relevant_ids"])
                and ("u.s. house" in m["question"].lower()
                     or "u.s. senate" in m["question"].lower())
            },
        ),
        "GOV_COUNT": (
            GOV_COUNT_IDS,
            {
                t
                for t, m in anchor_by_target.items()
                if "governor" in m["question"].lower()
                and orientation_from_question(m["question"]) is not None
            },
        ),
    }
    result = {}
    for label, (ids, targets) in specs.items():
        result[label] = {}
        f = base[base["sig_market_id"].isin(targets)]
        for cutoff in SENSITIVITY_AGES_S:
            complete = 0
            for ev in f.itertuples(index=False):
                states = lookup.component(
                    ids, int(ev.block_number), int(ev.ts), int(cutoff)
                )
                complete += int(len(states) == len(ids))
            result[label][str(cutoff)] = {
                "eligible_target_events": int(len(f)),
                "complete_source_states": int(complete),
            }
    return result


def summarize_frame(frame: pd.DataFrame, prediction_cols: list[str]) -> dict:
    out = {
        "rows": int(len(frame)),
        "targets": int(frame["sig_market_id"].nunique()) if len(frame) else 0,
        "train_rows": int((frame["split"] == "TRAIN").sum()) if len(frame) else 0,
        "dev_rows": int((frame["split"] == "DEV").sum()) if len(frame) else 0,
    }
    if len(frame):
        for col in prediction_cols:
            if col in frame:
                out[col] = error_metrics(frame[frame["split"] == "DEV"], col)
    return out


def main() -> None:
    split = load_split()
    final_start = int(split["final_start_epoch"])
    root = locate_data004_root()
    direct = load_direct_events(final_start)
    source = load_source_rows(root, final_start)
    anchor_by_target, exact_equiv_by_target, sh_matrix = load_semantics(root)
    sh_matrix = load_sh_matrix(sh_matrix)
    lookup = SourceLookup(source)
    base = build_event_base(direct, split)

    if (base["ts"] >= final_start).any():
        raise RuntimeError("FINAL leaked into corrected structural math base")

    loo_price = build_loo_price(base, anchor_by_target, exact_equiv_by_target, lookup)
    georgia = build_georgia(base, anchor_by_target, lookup)
    chamber = build_chamber(base, anchor_by_target, lookup, sh_matrix)
    gov_inverse = build_governor_inverse_rows(base, anchor_by_target, lookup)

    inv_qp, inv_qp_rows = evaluate_inverse(gov_inverse, "QP", RNG_SEED + 1)
    inv_kl, inv_kl_rows = evaluate_inverse(gov_inverse, "KL", RNG_SEED + 2)

    loo_summary = summarize_frame(loo_price, ["pred_qp"])
    georgia_summary = summarize_frame(georgia, ["pred_qp", "pred_kl"])
    if len(georgia):
        gdev = georgia[georgia["split"] == "DEV"]
        georgia_summary["lp_direct_coverage"] = (
            float(gdev["lp_contains_direct"].mean()) if len(gdev) else None
        )
        georgia_summary["median_lp_width"] = (
            float(gdev["lp_width"].median()) if len(gdev) else None
        )
        georgia_summary["raw_feasible_fraction"] = (
            float(gdev["raw_feasible"].mean()) if len(gdev) else None
        )
    chamber_summary = summarize_frame(chamber, ["pred_qp", "pred_kl"])
    if len(chamber):
        cdev = chamber[chamber["split"] == "DEV"]
        chamber_summary["median_abs_raw_coherence_gap"] = (
            float(cdev["raw_coherence_gap"].abs().median()) if len(cdev) else None
        )

    coverage = coverage_sensitivity(base, anchor_by_target, lookup)

    method_status = {
        "R3-S01-LP-BOUNDS": {
            "status": "EXERCISED",
            "evidence": [
                "Georgia partial-identification marginal intervals",
                "Senate-House threshold intervals after coherent category projection",
            ],
        },
        "R3-S02-LOO-PRICE-QP": {
            "status": "EXERCISED",
            "evidence": "Settlement-aligned EXACT_EQUIVALENT redundancy comparator with literal DATA-003/direct IDs excluded",
        },
        "R3-S03-LOO-FAMILY-QP": {
            "status": "EXERCISED_PRIORITY_COMPONENTS",
            "evidence": [
                "Georgia joint-state Euclidean coherent projection",
                "Senate-House category simplex projection",
                "Governorship category simplex projection",
            ],
        },
        "R3-S04-LOO-FAMILY-KL": {
            "status": "EXERCISED_PRIORITY_COMPONENTS",
            "evidence": "Positive-category I-projection / normalization on the same frozen components",
        },
        "R3-S05-MAXENT": {
            "status": "EXERCISED_PRIORITY_COMPONENTS",
            "evidence": [
                "Georgia residual completion across five unobserved fine states",
                "Senate-House coarsened-cell completion",
                "Governorship within-bin count completion",
            ],
        },
        "R3-S06-LATENT": {
            "status": "EXERCISED_SCALABLE_INVERSE",
            "evidence": "TRAIN-fitted pooled governorship count-index shock -> linked Governor constituent update",
        },
    }

    result = {
        "schema_version": 1,
        "experiment_id": "R3-FV-001",
        "stage": "CORRECTED_STRUCTURAL_MATH_TRAIN_DEV",
        "empirical_universe": {
            "source": "DATA-004",
            "target": "DATA-003",
            "data001_used": False,
            "historical_analogue_families_used": False,
        },
        "final_opened": False,
        "final_rows_accessed": 0,
        "source_rows_prefinal": int(len(source)),
        "direct_rows_prefinal": int(len(direct)),
        "base_target_events": int(len(base)),
        "primary_max_source_age_seconds": PRIMARY_MAX_AGE_S,
        "coverage_sensitivity": coverage,
        "loo_price_qp": loo_summary,
        "georgia_joint": georgia_summary,
        "senate_house_count": chamber_summary,
        "governorship_inverse": {
            "raw_rows": int(len(gov_inverse)),
            "qp": inv_qp,
            "kl": inv_kl,
        },
        "method_status": method_status,
        "decision_policy": (
            "No programme-level stop is permitted from this run alone. "
            "Interpret each structural layer separately; FINAL remains locked."
        ),
    }

    (OUT / "STRUCTURAL_MATH_RESULTS.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n"
    )
    if len(loo_price):
        loo_price.to_csv(OUT / "LOO_PRICE_QP_EVENTS.csv", index=False)
    if len(georgia):
        georgia.to_csv(OUT / "GEORGIA_STRUCTURAL_EVENTS.csv", index=False)
    if len(chamber):
        chamber.to_csv(OUT / "SENATE_HOUSE_STRUCTURAL_EVENTS.csv", index=False)
    if len(gov_inverse):
        gov_inverse.to_csv(OUT / "GOV_COUNT_INVERSE_BASE.csv", index=False)
    if len(inv_qp_rows):
        inv_qp_rows.to_csv(OUT / "GOV_COUNT_INVERSE_QP_DEV.csv", index=False)
    if len(inv_kl_rows):
        inv_kl_rows.to_csv(OUT / "GOV_COUNT_INVERSE_KL_DEV.csv", index=False)

    compact = {
        "stage": result["stage"],
        "data001_used": False,
        "source_rows_prefinal": result["source_rows_prefinal"],
        "direct_rows_prefinal": result["direct_rows_prefinal"],
        "loo_price_dev_rows": loo_summary.get("dev_rows", 0),
        "georgia_dev_rows": georgia_summary.get("dev_rows", 0),
        "chamber_dev_rows": chamber_summary.get("dev_rows", 0),
        "gov_inverse_qp": inv_qp,
        "gov_inverse_kl": inv_kl,
        "final_opened": False,
        "final_rows_accessed": 0,
    }
    print("R3_STRUCTURAL_MATH_RESULT=" + json.dumps(compact, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
