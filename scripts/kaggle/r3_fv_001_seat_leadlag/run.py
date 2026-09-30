# ruff: noqa
from __future__ import annotations

import json
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

OUT = Path("/kaggle/working/r3_fv_001_seat_leadlag")
OUT.mkdir(parents=True, exist_ok=True)

DATA004_SLUG = "sig-cup-data-004-ets-p0p1-fills"
BLOCK_GATE_FRAGMENT = "005b-data003-block-gate"
PREPARE_FRAGMENT = "r3-fv-001-prepare-v2"
MAX_AGE_S = 86400
COOLDOWN_S = 30
HORIZONS = (300, 30, 5)
BOOTSTRAPS = 1000
RNG_SEED = 20260930

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
        raise RuntimeError("FINAL row reached seat lead-lag")
    return None


def load_direct_events(final_start: int) -> pd.DataFrame:
    econ = locate("economic_fills_DATA003.parquet", BLOCK_GATE_FRAGMENT)
    txb = locate("tx_block_DATA003.parquet", BLOCK_GATE_FRAGMENT)
    bts = locate("block_timestamp.parquet", BLOCK_GATE_FRAGMENT)
    gate = json.loads(locate("data003_block_gate_report.json", BLOCK_GATE_FRAGMENT).read_text())
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
            arg_max(aligned_p,log_index) p_target
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
    df = df[df["p_yes"].notna()].copy()
    if df.duplicated(["block_number","log_index"]).any():
        raise RuntimeError("duplicate source block/log chronology")
    return df


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


def house_scalar(qcat: np.ndarray) -> float:
    return float(qcat[7] + qcat[8] + qcat[9] + 0.4 * qcat[6])


def senate_t50(qcat: np.ndarray) -> float:
    return float(qcat[3:].sum())


def build_source_series(
    rows: pd.DataFrame,
    ids: list[str],
    family: str,
    split: dict,
) -> pd.DataFrame:
    wanted = set(ids)
    f = rows[rows["market_id"].isin(wanted)].sort_values(
        ["block_number","log_index"]
    )
    state: dict[str, tuple[float,int,int]] = {}
    out = []
    previous = {"QP": None, "KL": None}

    for block, g in f.groupby("block_number", sort=True):
        for ev in g.itertuples(index=False):
            state[str(ev.market_id)] = (
                float(ev.p_yes), int(ev.ts), int(ev.block_number)
            )
        if len(state) != len(ids):
            continue
        block_ts = int(g["ts"].max())
        vals = []
        ages = []
        for market_id in ids:
            p, sts, _ = state[market_id]
            age = block_ts - sts
            if age < 0:
                raise RuntimeError("source state age is negative")
            vals.append(p)
            ages.append(age)
        if max(ages) > MAX_AGE_S:
            continue
        raw = np.asarray(vals, dtype=float)
        q_qp = project_simplex(raw)
        q_kl = project_kl(raw)
        if family == "HOUSE":
            scalars = {"QP": house_scalar(q_qp), "KL": house_scalar(q_kl)}
        elif family == "SENATE":
            scalars = {"QP": senate_t50(q_qp), "KL": senate_t50(q_kl)}
        else:
            raise ValueError(family)

        sp = split_name(block_ts, split)
        if sp is None:
            continue
        row = {
            "family": family,
            "split": sp,
            "block_number": int(block),
            "ts": block_ts,
            "max_component_age_s": int(max(ages)),
            "raw_sum": float(raw.sum()),
        }
        for method in ("QP","KL"):
            scalar = float(scalars[method])
            prev = previous[method]
            row[f"scalar_{method.lower()}"] = scalar
            row[f"delta_{method.lower()}"] = (
                np.nan if prev is None else scalar - float(prev)
            )
            previous[method] = scalar
        out.append(row)
    return pd.DataFrame(out).sort_values(["block_number"])


def train_threshold(series: pd.DataFrame, method: str) -> float | None:
    col = f"delta_{method.lower()}"
    vals = series.loc[
        (series["split"]=="TRAIN") & series[col].notna() & (series[col].abs()>0),
        col,
    ].abs().to_numpy(dtype=float)
    if len(vals) < 20:
        return None
    return float(np.quantile(vals, 0.75))


def select_shocks(
    series: pd.DataFrame,
    method: str,
    threshold: float,
) -> pd.DataFrame:
    col = f"delta_{method.lower()}"
    f = series[series[col].notna() & (series[col].abs() >= threshold)].copy()
    rows = []
    last_ts_by_split: dict[str,int] = {}
    for ev in f.sort_values(["ts","block_number"]).itertuples(index=False):
        sp = str(ev.split)
        last_ts = last_ts_by_split.get(sp)
        if last_ts is not None and int(ev.ts) - last_ts < COOLDOWN_S:
            continue
        rows.append(ev._asdict())
        last_ts_by_split[sp] = int(ev.ts)
    return pd.DataFrame(rows)


def target_arrays(direct: pd.DataFrame, target: str):
    f = direct[direct["sig_market_id"]==target].sort_values(
        ["block_number"]
    ).copy()
    return (
        f["block_number"].to_numpy(dtype=np.int64),
        f["ts"].to_numpy(dtype=np.int64),
        f["p_target"].to_numpy(dtype=float),
        f,
    )


def match_shocks(
    shocks: pd.DataFrame,
    direct: pd.DataFrame,
    target: str,
    split: dict,
) -> pd.DataFrame:
    if shocks.empty:
        return pd.DataFrame()
    blocks, tss, prices, _ = target_arrays(direct, target)
    rows = []
    for ev in shocks.itertuples(index=False):
        b = int(ev.block_number)
        prev_idx = int(np.searchsorted(blocks, b, side="left") - 1)
        fut_idx = int(np.searchsorted(blocks, b, side="right"))
        if prev_idx < 0 or fut_idx >= len(blocks):
            continue
        future_ts = int(tss[fut_idx])
        future_split = split_name(future_ts, split)
        if future_split != str(ev.split):
            continue
        latency = future_ts - int(ev.ts)
        if latency < 0:
            raise RuntimeError("future target timestamp before source shock")
        row = ev._asdict()
        row.update({
            "target_before_block": int(blocks[prev_idx]),
            "target_before_ts": int(tss[prev_idx]),
            "target_before": float(prices[prev_idx]),
            "target_future_block": int(blocks[fut_idx]),
            "target_future_ts": future_ts,
            "target_future": float(prices[fut_idx]),
            "latency_s": int(latency),
            "target_change": float(prices[fut_idx]-prices[prev_idx]),
        })
        rows.append(row)
    out = pd.DataFrame(rows)
    if out.empty:
        return out
    # Earliest deployable shock per future target update; avoids outcome pseudo-replication.
    out = out.sort_values(["target_future_block","block_number"]).drop_duplicates(
        ["target_future_block"], keep="first"
    )
    return out.sort_values(["block_number"])


def fit_beta(train: pd.DataFrame, delta_col: str) -> float | None:
    f = train[[delta_col,"target_change"]].dropna()
    if len(f) < 20:
        return None
    x = f[delta_col].to_numpy(dtype=float)
    y = f["target_change"].to_numpy(dtype=float)
    denom = float(np.dot(x,x))
    if denom <= 1e-14:
        return None
    return float(np.dot(x,y)/denom)


def bootstrap_days(f: pd.DataFrame, pred_col: str, seed: int) -> dict:
    g = f[["ts","target_before","target_future",pred_col]].dropna().copy()
    if g.empty:
        return {"status":"NO_ROWS"}
    g["day"] = pd.to_datetime(g["ts"],unit="s",utc=True).dt.strftime("%Y-%m-%d")
    g["imp"] = (
        np.abs(g["target_future"]-g["target_before"])
        - np.abs(g["target_future"]-g[pred_col])
    )
    agg = g.groupby("day")["imp"].agg(["sum","count"])
    if len(agg)<2:
        return {"status":"INSUFFICIENT_DAY_CLUSTERS","clusters":int(len(agg))}
    sums=agg["sum"].to_numpy(dtype=float)
    counts=agg["count"].to_numpy(dtype=float)
    rng=np.random.default_rng(seed)
    draws=np.empty(BOOTSTRAPS)
    for i in range(BOOTSTRAPS):
        idx=rng.integers(0,len(agg),size=len(agg))
        draws[i]=sums[idx].sum()/counts[idx].sum()
    return {
        "status":"OK","clusters":int(len(agg)),"resamples":BOOTSTRAPS,
        "lower_95":float(np.quantile(draws,0.025)),
        "median":float(np.quantile(draws,0.5)),
        "upper_95":float(np.quantile(draws,0.975)),
    }


def evaluate(
    matched: pd.DataFrame,
    method: str,
    horizon: int,
    seed: int,
) -> tuple[dict,pd.DataFrame]:
    delta_col=f"delta_{method.lower()}"
    f=matched[matched["latency_s"]<=int(horizon)].copy()
    train=f[f["split"]=="TRAIN"].copy()
    dev=f[f["split"]=="DEV"].copy()
    beta=fit_beta(train,delta_col)
    if beta is None or dev.empty:
        return ({
            "status":"INSUFFICIENT_SUPPORT",
            "horizon_seconds":horizon,
            "train_rows":int(len(train)),
            "dev_rows":int(len(dev)),
        },dev)
    pred_col="pred_target"
    dev[pred_col]=np.clip(
        dev["target_before"]+beta*dev[delta_col],0.0,1.0
    )
    base_abs=np.abs(dev["target_future"]-dev["target_before"])
    model_abs=np.abs(dev["target_future"]-dev[pred_col])
    base_mae=float(base_abs.mean())
    model_mae=float(model_abs.mean())
    imp=base_mae-model_mae
    ordered=dev.sort_values(["ts","block_number"])
    cut=max(1,len(ordered)//2)
    halves=[]
    for part in (ordered.iloc[:cut],ordered.iloc[cut:]):
        if len(part)==0:
            halves.append(None)
        else:
            halves.append(float(
                np.abs(part["target_future"]-part["target_before"]).mean()
                - np.abs(part["target_future"]-part[pred_col]).mean()
            ))
    bs=bootstrap_days(dev,pred_col,seed)
    m={
        "status":"OK",
        "horizon_seconds":horizon,
        "beta_train":beta,
        "train_rows":int(len(train)),
        "dev_rows":int(len(dev)),
        "dev_day_clusters":int(bs.get("clusters",0)),
        "baseline_mae":base_mae,
        "model_mae":model_mae,
        "absolute_mae_improvement":imp,
        "relative_mae_improvement":float(imp/base_mae) if base_mae>0 else None,
        "baseline_rmse":float(np.sqrt(np.mean(
            (dev["target_future"]-dev["target_before"])**2
        ))),
        "model_rmse":float(np.sqrt(np.mean(
            (dev["target_future"]-dev[pred_col])**2
        ))),
        "direction_accuracy":float(np.mean(
            np.sign(dev[pred_col]-dev["target_before"])
            == np.sign(dev["target_future"]-dev["target_before"])
        )),
        "first_half_mae_improvement":halves[0],
        "second_half_mae_improvement":halves[1],
        "median_latency_s":float(dev["latency_s"].median()),
        "p90_latency_s":float(dev["latency_s"].quantile(0.9)),
        "bootstrap_day":bs,
    }
    return m,dev


def gate(m: dict) -> bool:
    if m.get("status")!="OK":
        return False
    bs=m.get("bootstrap_day",{})
    return bool(
        m.get("train_rows",0)>=50
        and m.get("dev_rows",0)>=30
        and m.get("dev_day_clusters",0)>=4
        and m.get("absolute_mae_improvement",0)>0
        and bs.get("status")=="OK"
        and bs.get("lower_95",-1)>0
        and (m.get("first_half_mae_improvement") or 0)>=0
        and (m.get("second_half_mae_improvement") or 0)>=0
        and m.get("direction_accuracy",0)>0.5
    )


def family_run(
    family: str,
    target: str,
    ids: list[str],
    source: pd.DataFrame,
    direct: pd.DataFrame,
    split: dict,
    seed_base: int,
):
    series=build_source_series(source,ids,family,split)
    result={
        "source_series_rows":int(len(series)),
        "train_source_series_rows":int((series["split"]=="TRAIN").sum()),
        "dev_source_series_rows":int((series["split"]=="DEV").sum()),
        "methods":{},
    }
    export=[]
    for mi,method in enumerate(("QP","KL")):
        threshold=train_threshold(series,method)
        if threshold is None:
            result["methods"][method]={
                "status":"INSUFFICIENT_TRAIN_SHOCK_SUPPORT"
            }
            continue
        shocks=select_shocks(series,method,threshold)
        matched=match_shocks(shocks,direct,target,split)
        mres={
            "train_q75_abs_delta_threshold":threshold,
            "shock_rows":int(len(shocks)),
            "matched_rows":int(len(matched)),
            "train_matched_rows":int((matched["split"]=="TRAIN").sum()) if len(matched) else 0,
            "dev_matched_rows":int((matched["split"]=="DEV").sum()) if len(matched) else 0,
            "horizons":{},
        }
        for hi,h in enumerate(HORIZONS):
            met,dev=evaluate(matched,method,h,seed_base+100*mi+hi)
            met["primary_gate_pass"]=bool(h==300 and gate(met))
            mres["horizons"][str(h)]=met
            if len(dev):
                x=dev.copy()
                x["method"]=method
                x["horizon_seconds"]=h
                export.append(x)
        result["methods"][method]=mres
    result["candidate"]=bool(
        result["methods"].get("QP",{}).get("horizons",{}).get("300",{}).get("primary_gate_pass",False)
        and result["methods"].get("KL",{}).get("horizons",{}).get("300",{}).get("primary_gate_pass",False)
    )
    export_df=pd.concat(export,ignore_index=True) if export else pd.DataFrame()
    return result,series,export_df


def main():
    split=load_split()
    final_start=int(split["final_start_epoch"])
    root=locate_data004_root()
    direct=load_direct_events(final_start)
    source=load_source_rows(root,final_start)

    house,house_series,house_dev=family_run(
        "HOUSE",HOUSE_TARGET,HOUSE_IDS,source,direct,split,RNG_SEED+1000
    )
    senate,senate_series,senate_dev=family_run(
        "SENATE",SENATE_TARGET,SENATE_IDS,source,direct,split,RNG_SEED+2000
    )

    result={
        "schema_version":1,
        "experiment_id":"R3-FV-001",
        "subexperiment":"R3-SEAT-LEADLAG-001",
        "stage":"TRAIN_DEV_ONLY",
        "empirical_universe":{
            "source":"DATA-004","target":"DATA-003",
            "data001_used":False,"historical_analogue_families_used":False,
        },
        "final_opened":False,
        "final_rows_accessed":0,
        "max_component_age_seconds":MAX_AGE_S,
        "cooldown_seconds":COOLDOWN_S,
        "primary_horizon_seconds":300,
        "house":house,
        "senate_t50":senate,
        "programme_candidate":bool(house["candidate"] or senate["candidate"]),
        "decision_policy":"300s primary only. 30s/5s diagnostics cannot rescue. No FINAL opening automatically.",
    }
    (OUT/"SEAT_LEADLAG_RESULTS.json").write_text(
        json.dumps(result,indent=2,sort_keys=True)+"\n"
    )
    house_series.to_csv(OUT/"HOUSE_SOURCE_SERIES.csv",index=False)
    senate_series.to_csv(OUT/"SENATE_SOURCE_SERIES.csv",index=False)
    if len(house_dev):
        house_dev.to_csv(OUT/"HOUSE_DEV_MATCHED.csv",index=False)
    if len(senate_dev):
        senate_dev.to_csv(OUT/"SENATE_DEV_MATCHED.csv",index=False)

    print("R3_SEAT_LEADLAG_RESULT="+json.dumps({
        "house_candidate":house["candidate"],
        "senate_candidate":senate["candidate"],
        "programme_candidate":result["programme_candidate"],
        "final_opened":False,
    },sort_keys=True),flush=True)


if __name__=="__main__":
    main()
