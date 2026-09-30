# ruff: noqa
from __future__ import annotations

import json
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

OUT = Path("/kaggle/working/r3_fv_001_seat_counts")
OUT.mkdir(parents=True, exist_ok=True)

DATA004_SLUG = "sig-cup-data-004-ets-p0p1-fills"
BLOCK_GATE_FRAGMENT = "005b-data003-block-gate"
PREPARE_FRAGMENT = "r3-fv-001-prepare-v2"
MAX_AGE_S = 86400
BOOTSTRAPS = 1000
RNG_SEED = 20260929

HOUSE_IDS = [
    "919489","919490","919491","919492","919493",
    "919494","919495","919496","919497","919498",
]
SENATE_IDS = [
    "943819","943820","943821","943822","943823","943824",
    "943825","943826","943827","943828","943829",
]
HOUSE_TARGET = "152"
SENATE_TARGET = "154"


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


def load_split() -> dict:
    split = json.loads(locate("R3_SPLIT_MANIFEST.json", PREPARE_FRAGMENT).read_text())
    if split.get("final_opened") is not False:
        raise RuntimeError("R3 split says FINAL opened")
    if split.get("performance_or_future_target_metrics_accessed") is not False:
        raise RuntimeError("R3 split was not frozen outcome-blind")
    return split


def split_name(ts: int, split: dict) -> str | None:
    dev_start = int(split["dev_start_epoch"])
    final_start = int(split["final_start_epoch"])
    purge = int(split["selection_rule"]["boundary_purge_seconds"])
    if ts < dev_start - purge:
        return "TRAIN"
    if dev_start <= ts < final_start - purge:
        return "DEV"
    if ts >= final_start:
        raise RuntimeError("FINAL target row reached seat-count discovery")
    return None


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
              and cast(e.sig_market_id as varchar) in ('{HOUSE_TARGET}','{SENATE_TARGET}')
              and cast(e.timestamp as bigint) < {int(final_start)}
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
    df["sig_market_id"] = df["sig_market_id"].map(idstr)
    return df


def load_source_rows(root: Path, final_start: int) -> pd.DataFrame:
    files = sorted(root.rglob("fills/date=*/part-*.parquet"))
    if not files:
        raise RuntimeError("no DATA-004 fill parquet files")
    sql = ",".join("'" + q(p) + "'" for p in files)
    wanted = HOUSE_IDS + SENATE_IDS
    wanted_sql = ",".join("'" + x + "'" for x in wanted)
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
          and cast("timestamp" as bigint) < {int(final_start)}
          and cast(market_id as varchar) in ({wanted_sql})
        order by block_number,log_index
        """
    ).fetchdf()
    con.close()
    df["market_id"] = df["market_id"].map(idstr)
    return df[df["p_yes"].notna()].copy()


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

    def complete(self, market_ids: list[str], block: int, target_ts: int):
        vals = []
        ages = []
        src_blocks = []
        for market_id in market_ids:
            st = self.latest_before(market_id, block)
            if st is None:
                return None
            p, sts, sblock = st
            age = int(target_ts) - int(sts)
            if age < 0:
                raise RuntimeError("future source timestamp entered seat-count state")
            if age > MAX_AGE_S:
                return None
            vals.append(float(p))
            ages.append(age)
            src_blocks.append(int(sblock))
        return {
            "raw": np.asarray(vals, dtype=float),
            "age_max_s": int(max(ages)),
            "age_median_s": float(np.median(ages)),
            "latest_source_block": int(max(src_blocks)),
        }


def project_simplex(v: np.ndarray) -> np.ndarray:
    v = np.asarray(v, dtype=float)
    u = np.sort(v)[::-1]
    cssv = np.cumsum(u)
    idx = np.arange(1, len(v) + 1)
    cond = u - (cssv - 1.0) / idx > 0
    if not np.any(cond):
        return np.full_like(v, 1.0 / len(v))
    rho = int(np.where(cond)[0][-1])
    theta = (cssv[rho] - 1.0) / float(rho + 1)
    return np.maximum(v - theta, 0.0)


def project_kl(v: np.ndarray) -> np.ndarray:
    x = np.maximum(np.asarray(v, dtype=float), 1e-9)
    return x / float(x.sum())


def house_values(qcat: np.ndarray) -> dict:
    lower = float(qcat[7] + qcat[8] + qcat[9])
    straddle = float(qcat[6])
    upper = float(lower + straddle)
    return {
        "lower": lower,
        "upper": upper,
        "maxent": float(lower + 0.4 * straddle),
        "midpoint": float(lower + 0.5 * straddle),
    }


def senate_values(qcat: np.ndarray) -> dict:
    return {
        "t50": float(qcat[3:].sum()),
        "t51": float(qcat[4:].sum()),
    }


def build_base(direct: pd.DataFrame, split: dict) -> pd.DataFrame:
    rows = []
    prev = {}
    for ev in direct.sort_values(["block_number", "sig_market_id"]).itertuples(index=False):
        target = idstr(ev.sig_market_id)
        old = prev.get(target)
        sp = split_name(int(ev.ts), split)
        if old is not None and sp is not None:
            rows.append(
                {
                    "split": sp,
                    "sig_market_id": target,
                    "block_number": int(ev.block_number),
                    "ts": int(ev.ts),
                    "mapping_class": str(ev.mapping_class),
                    "prev": float(old[0]),
                    "y": float(ev.p_target),
                }
            )
        prev[target] = (float(ev.p_target), int(ev.block_number), int(ev.ts))
    return pd.DataFrame(rows)


def build_family(
    base: pd.DataFrame,
    target: str,
    ids: list[str],
    lookup: SourceLookup,
    family: str,
) -> pd.DataFrame:
    rows = []
    target_base = base[base["sig_market_id"] == target].sort_values(
        ["ts", "block_number"]
    ).copy()
    target_base["next_y"] = target_base["y"].shift(-1)
    target_base["next_split"] = target_base["split"].shift(-1)
    target_base["next_ts"] = target_base["ts"].shift(-1)
    target_base.loc[
        target_base["next_split"] != target_base["split"],
        ["next_y", "next_ts"],
    ] = np.nan

    for ev in target_base.itertuples(index=False):
        state = lookup.complete(ids, int(ev.block_number), int(ev.ts))
        if state is None:
            continue
        raw = state["raw"]
        q_qp = project_simplex(raw)
        q_kl = project_kl(raw)
        row = ev._asdict()
        row.update(
            {
                "raw_sum": float(raw.sum()),
                "raw_coherence_gap": float(raw.sum() - 1.0),
                "source_age_max_s": state["age_max_s"],
                "source_age_median_s": state["age_median_s"],
                "source_latest_block": state["latest_source_block"],
            }
        )
        if family == "HOUSE":
            for method, qcat in (("qp", q_qp), ("kl", q_kl)):
                vals = house_values(qcat)
                row[f"{method}_lower"] = vals["lower"]
                row[f"{method}_upper"] = vals["upper"]
                row[f"{method}_maxent"] = vals["maxent"]
                row[f"{method}_midpoint"] = vals["midpoint"]
        elif family == "SENATE":
            for method, qcat in (("qp", q_qp), ("kl", q_kl)):
                vals = senate_values(qcat)
                row[f"{method}_t50"] = vals["t50"]
                row[f"{method}_t51"] = vals["t51"]
        else:
            raise ValueError(family)
        rows.append(row)
    return pd.DataFrame(rows).sort_values(["ts", "block_number"])


def bootstrap_days(frame: pd.DataFrame, base_col: str, pred_col: str, y_col: str, seed: int):
    f = frame[[base_col, pred_col, y_col, "ts"]].dropna().copy()
    if f.empty:
        return {"status": "NO_ROWS"}
    f["day"] = pd.to_datetime(f["ts"], unit="s", utc=True).dt.strftime("%Y-%m-%d")
    f["imp"] = np.abs(f[y_col] - f[base_col]) - np.abs(f[y_col] - f[pred_col])
    grouped = f.groupby("day")["imp"].agg(["sum", "count"])
    if len(grouped) < 2:
        return {"status": "INSUFFICIENT_DAY_CLUSTERS", "clusters": int(len(grouped))}
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


def metrics(frame: pd.DataFrame, base_col: str, pred_col: str, y_col: str, seed: int):
    f = frame[[base_col, pred_col, y_col, "ts"]].dropna().copy()
    if f.empty:
        return {"status": "NO_ROWS", "rows": 0}
    base = f[base_col].to_numpy(dtype=float)
    pred = f[pred_col].to_numpy(dtype=float)
    y = f[y_col].to_numpy(dtype=float)
    base_abs = np.abs(y - base)
    pred_abs = np.abs(y - pred)
    base_mae = float(base_abs.mean())
    pred_mae = float(pred_abs.mean())
    imp = base_mae - pred_mae
    ordered = f.sort_values("ts")
    cut = max(1, len(ordered) // 2)
    halves = []
    for part in (ordered.iloc[:cut], ordered.iloc[cut:]):
        if len(part) == 0:
            halves.append(None)
        else:
            halves.append(
                float(
                    np.abs(part[y_col] - part[base_col]).mean()
                    - np.abs(part[y_col] - part[pred_col]).mean()
                )
            )
    return {
        "status": "OK",
        "rows": int(len(f)),
        "baseline_mae": base_mae,
        "model_mae": pred_mae,
        "absolute_mae_improvement": imp,
        "relative_mae_improvement": float(imp / base_mae) if base_mae > 0 else None,
        "baseline_rmse": float(np.sqrt(np.mean((y - base) ** 2))),
        "model_rmse": float(np.sqrt(np.mean((y - pred) ** 2))),
        "direction_accuracy": float(np.mean(np.sign(pred - base) == np.sign(y - base))),
        "first_half_mae_improvement": halves[0],
        "second_half_mae_improvement": halves[1],
        "bootstrap_day": bootstrap_days(f, base_col, pred_col, y_col, seed),
    }


def fit_residual_beta(train: pd.DataFrame, fv_col: str):
    f = train[[fv_col, "y", "next_y"]].dropna()
    if len(f) < 30:
        return None
    x = (f[fv_col] - f["y"]).to_numpy(dtype=float)
    dy = (f["next_y"] - f["y"]).to_numpy(dtype=float)
    denom = float(np.dot(x, x))
    if denom <= 1e-14:
        return None
    return float(np.dot(x, dy) / denom)


def residual_eval(frame: pd.DataFrame, fv_col: str, seed: int):
    train = frame[frame["split"] == "TRAIN"].copy()
    dev = frame[frame["split"] == "DEV"].copy()
    beta = fit_residual_beta(train, fv_col)
    if beta is None:
        return {
            "status": "INSUFFICIENT_TRAIN_SUPPORT",
            "train_rows": int(train[fv_col].notna().sum()),
            "dev_rows": int(dev[fv_col].notna().sum()),
        }
    col = "pred_next"
    dev[col] = np.clip(dev["y"] + beta * (dev[fv_col] - dev["y"]), 0.0, 1.0)
    m = metrics(dev, "y", col, "next_y", seed)
    m.update(
        {
            "beta_train": beta,
            "train_rows_with_next": int(
                train[[fv_col, "y", "next_y"]].dropna().shape[0]
            ),
            "dev_rows_with_next": int(
                dev[[fv_col, "y", "next_y"]].dropna().shape[0]
            ),
        }
    )
    return m


def statistical_gate(m: dict) -> bool:
    if m.get("status") != "OK":
        return False
    b = m.get("bootstrap_day", {})
    return bool(
        m.get("absolute_mae_improvement", 0.0) > 0.0
        and b.get("status") == "OK"
        and b.get("lower_95", -1.0) > 0.0
        and (m.get("first_half_mae_improvement") or 0.0) >= 0.0
        and (m.get("second_half_mae_improvement") or 0.0) >= 0.0
    )


def main() -> None:
    split = load_split()
    final_start = int(split["final_start_epoch"])
    root = locate_data004_root()
    direct = load_direct_events(final_start)
    source = load_source_rows(root, final_start)
    lookup = SourceLookup(source)
    base = build_base(direct, split)

    house = build_family(base, HOUSE_TARGET, HOUSE_IDS, lookup, "HOUSE")
    senate = build_family(base, SENATE_TARGET, SENATE_IDS, lookup, "SENATE")

    house_dev = house[house["split"] == "DEV"]
    senate_dev = senate[senate["split"] == "DEV"]

    house_result = {}
    for i, method in enumerate(("qp", "kl")):
        col = f"{method}_maxent"
        level = metrics(house_dev, "prev", col, "y", RNG_SEED + 10 + i)
        resid = residual_eval(house, col, RNG_SEED + 20 + i)
        house_result[method] = {
            "level": level,
            "residual_next_update": resid,
            "level_gate_pass": statistical_gate(level),
            "residual_gate_pass": statistical_gate(resid),
        }
    if len(house_dev):
        house_result["lp"] = {
            "rows": int(len(house_dev)),
            "qp_direct_coverage": float(
                ((house_dev["y"] >= house_dev["qp_lower"])
                 & (house_dev["y"] <= house_dev["qp_upper"])).mean()
            ),
            "qp_median_width": float(
                (house_dev["qp_upper"] - house_dev["qp_lower"]).median()
            ),
            "kl_direct_coverage": float(
                ((house_dev["y"] >= house_dev["kl_lower"])
                 & (house_dev["y"] <= house_dev["kl_upper"])).mean()
            ),
            "kl_median_width": float(
                (house_dev["kl_upper"] - house_dev["kl_lower"]).median()
            ),
        }
    house_result["candidate_level"] = bool(
        house_result["qp"]["level_gate_pass"]
        and house_result["kl"]["level_gate_pass"]
    )
    house_result["candidate_residual"] = bool(
        house_result["qp"]["residual_gate_pass"]
        and house_result["kl"]["residual_gate_pass"]
    )

    senate_result = {}
    for threshold in ("t50", "t51"):
        senate_result[threshold] = {}
        for i, method in enumerate(("qp", "kl")):
            col = f"{method}_{threshold}"
            level = metrics(
                senate_dev, "prev", col, "y",
                RNG_SEED + 100 + (0 if threshold == "t50" else 10) + i,
            )
            resid = residual_eval(
                senate, col,
                RNG_SEED + 200 + (0 if threshold == "t50" else 10) + i,
            )
            senate_result[threshold][method] = {
                "level": level,
                "residual_next_update": resid,
                "statistical_level_gate": statistical_gate(level),
                "statistical_residual_gate": statistical_gate(resid),
            }
        senate_result[threshold]["promotion_allowed"] = False
        senate_result[threshold]["reason"] = (
            "SIG win/control/tie-breaking semantics remain unresolved; "
            "DEV performance cannot select T50 versus T51."
        )

    result = {
        "schema_version": 1,
        "experiment_id": "R3-FV-001",
        "subexperiment": "R3-SEAT-COUNT-001",
        "stage": "TRAIN_DEV_ONLY",
        "empirical_universe": {
            "source": "DATA-004",
            "target": "DATA-003",
            "data001_used": False,
            "historical_analogue_families_used": False,
        },
        "final_opened": False,
        "final_rows_accessed": 0,
        "max_source_age_seconds": MAX_AGE_S,
        "house": {
            "rows": int(len(house)),
            "train_rows": int((house["split"] == "TRAIN").sum()) if len(house) else 0,
            "dev_rows": int((house["split"] == "DEV").sum()) if len(house) else 0,
            "result": house_result,
        },
        "senate": {
            "rows": int(len(senate)),
            "train_rows": int((senate["split"] == "TRAIN").sum()) if len(senate) else 0,
            "dev_rows": int((senate["split"] == "DEV").sum()) if len(senate) else 0,
            "result": senate_result,
        },
        "decision_policy": (
            "House may create a structural candidate only under the frozen dual-QP/KL gate. "
            "Senate cannot promote until settlement semantics independently choose T50 or T51. "
            "FINAL remains locked."
        ),
    }

    (OUT / "SEAT_COUNT_RESULTS.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n"
    )
    if len(house):
        house.to_csv(OUT / "HOUSE_COUNT_EVENTS.csv", index=False)
    if len(senate):
        senate.to_csv(OUT / "SENATE_COUNT_EVENTS.csv", index=False)

    print(
        "R3_SEAT_COUNT_RESULT="
        + json.dumps(
            {
                "house_train_rows": result["house"]["train_rows"],
                "house_dev_rows": result["house"]["dev_rows"],
                "house_level_candidate": house_result["candidate_level"],
                "house_residual_candidate": house_result["candidate_residual"],
                "senate_train_rows": result["senate"]["train_rows"],
                "senate_dev_rows": result["senate"]["dev_rows"],
                "senate_promotion_allowed": False,
                "final_opened": False,
            },
            sort_keys=True,
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
