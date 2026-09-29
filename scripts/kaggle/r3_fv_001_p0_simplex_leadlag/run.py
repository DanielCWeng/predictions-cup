# ruff: noqa
from __future__ import annotations

import json
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

OUT = Path("/kaggle/working/r3_fv_001_p0_simplex_leadlag")
OUT.mkdir(parents=True, exist_ok=True)

DATA004_SLUG = "sig-cup-data-004-ets-p0p1-fills"
BLOCK_GATE_FRAGMENT = "005b-data003-block-gate"
PREPARE_FRAGMENT = "r3-fv-001-prepare-v2"
MAX_AGE_S = 86400
COOLDOWN_S = 30
EPS = 1e-4
RIDGE_LAMBDA = 1.0
HORIZONS = (300, 30, 5)
BOOTSTRAPS = 1000
RNG_SEED = 20260930

PAIR_SPECS = [
    {
        "pair_id":"house_count__r_house",
        "event_id":"103166",
        "source_ids":["919489","919490","919491","919492","919493","919494","919495","919496","919497","919498"],
        "target":"152",
        "label":"House count -> Republican House",
    },
    {
        "pair_id":"senate_count__r_senate",
        "event_id":"106193",
        "source_ids":["943819","943820","943821","943822","943823","943824","943825","943826","943827","943828","943829"],
        "target":"154",
        "label":"Senate count -> Republican Senate",
    },
    {
        "pair_id":"cross_chamber__d_house",
        "event_id":"633839",
        "source_ids":["2683257","2683258","2683259","2683260","2683261","2683262","2683263","2683264","2683265","2683266","2683267","2683268","2683269"],
        "target":"151",
        "label":"Cross-chamber -> Democratic House",
    },
    {
        "pair_id":"cross_chamber__r_house",
        "event_id":"633839",
        "source_ids":["2683257","2683258","2683259","2683260","2683261","2683262","2683263","2683264","2683265","2683266","2683267","2683268","2683269"],
        "target":"152",
        "label":"Cross-chamber -> Republican House",
    },
    {
        "pair_id":"cross_chamber__d_senate",
        "event_id":"633839",
        "source_ids":["2683257","2683258","2683259","2683260","2683261","2683262","2683263","2683264","2683265","2683266","2683267","2683268","2683269"],
        "target":"153",
        "label":"Cross-chamber -> Democratic Senate",
    },
    {
        "pair_id":"cross_chamber__r_senate",
        "event_id":"633839",
        "source_ids":["2683257","2683258","2683259","2683260","2683261","2683262","2683263","2683264","2683265","2683266","2683267","2683268","2683269"],
        "target":"154",
        "label":"Cross-chamber -> Republican Senate",
    },
    {
        "pair_id":"core_four__d_senate",
        "event_id":"162226",
        "source_ids":["1178882"],
        "target":"153",
        "label":"Core-four joint -> Democratic Senate",
    },
]


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
    matches=[p for p in Path("/kaggle/input").rglob(name) if fragment in str(p)]
    if len(matches)!=1:
        raise RuntimeError(f"expected one {name} under {fragment}, got {matches}")
    return matches[0]


def locate_data004_root() -> Path:
    matches=[]
    for p in Path("/kaggle/input").rglob("MANIFEST.json"):
        if DATA004_SLUG in str(p.parent):
            matches.append(p.parent)
    matches=sorted(set(matches))
    if len(matches)!=1:
        raise RuntimeError(f"expected one DATA-004 root, got {matches}")
    return matches[0]


def load_split() -> dict:
    split=json.loads(locate("R3_SPLIT_MANIFEST.json",PREPARE_FRAGMENT).read_text())
    if split.get("final_opened") is not False:
        raise RuntimeError("R3 split says FINAL opened")
    if split.get("performance_or_future_target_metrics_accessed") is not False:
        raise RuntimeError("R3 split was not frozen outcome-blind")
    return split


def split_name(ts:int,split:dict) -> str | None:
    dev_start=int(split["dev_start_epoch"])
    final_start=int(split["final_start_epoch"])
    purge=int(split["selection_rule"]["boundary_purge_seconds"])
    if ts<dev_start-purge:
        return "TRAIN"
    if dev_start<=ts<final_start-purge:
        return "DEV"
    if ts>=final_start:
        raise RuntimeError("FINAL row reached P0 simplex lead-lag")
    return None


def load_direct_events(final_start:int) -> pd.DataFrame:
    econ=locate("economic_fills_DATA003.parquet",BLOCK_GATE_FRAGMENT)
    txb=locate("tx_block_DATA003.parquet",BLOCK_GATE_FRAGMENT)
    bts=locate("block_timestamp.parquet",BLOCK_GATE_FRAGMENT)
    gate=json.loads(locate("data003_block_gate_report.json",BLOCK_GATE_FRAGMENT).read_text())
    if gate.get("all_hard_gates_pass") is not True:
        raise RuntimeError("DATA-003 block gate failed")
    targets=sorted({x["target"] for x in PAIR_SPECS})
    targets_sql=",".join("'" + x + "'" for x in targets)
    con=duckdb.connect()
    con.execute("pragma threads=4")
    df=con.execute(f"""
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
              and cast(e.sig_market_id as varchar) in ({targets_sql})
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
    """).fetchdf()
    con.close()
    df["sig_market_id"]=df["sig_market_id"].map(idstr)
    return df


def load_source_rows(root:Path,final_start:int) -> pd.DataFrame:
    files=sorted(root.rglob("fills/date=*/part-*.parquet"))
    if not files:
        raise RuntimeError("no DATA-004 fill parquet files")
    sql=",".join("'" + q(p) + "'" for p in files)
    wanted=sorted({mid for spec in PAIR_SPECS for mid in spec["source_ids"]})
    wanted_sql=",".join("'" + x + "'" for x in wanted)
    con=duckdb.connect()
    con.execute("pragma threads=4")
    df=con.execute(f"""
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
        from read_parquet([{sql}],union_by_name=true)
        where upper(cast(order_role as varchar))='TAKER'
          and cast(price as double) between 0 and 1
          and cast("timestamp" as bigint) < {int(final_start)}
          and cast(market_id as varchar) in ({wanted_sql})
        order by block_number,log_index
    """).fetchdf()
    con.close()
    df["market_id"]=df["market_id"].map(idstr)
    df=df[df["p_yes"].notna()].copy()
    if df.duplicated(["block_number","log_index"]).any():
        raise RuntimeError("duplicate DATA-004 block/log chronology")
    return df


def project_simplex(v:np.ndarray) -> np.ndarray:
    v=np.asarray(v,dtype=float)
    u=np.sort(v)[::-1]
    cssv=np.cumsum(u)
    idx=np.arange(1,len(v)+1)
    cond=u-(cssv-1.0)/idx>0
    if not np.any(cond):
        return np.full_like(v,1.0/len(v))
    rho=int(np.where(cond)[0][-1])
    theta=(cssv[rho]-1.0)/float(rho+1)
    return np.maximum(v-theta,0.0)


def project_kl(v:np.ndarray) -> np.ndarray:
    x=np.maximum(np.asarray(v,dtype=float),1e-9)
    return x/float(x.sum())


def source_distribution(raw:np.ndarray,method:str) -> np.ndarray:
    if len(raw)==1:
        p=float(np.clip(raw[0],0.0,1.0))
        return np.asarray([p,1.0-p],dtype=float)
    if method=="QP":
        return project_simplex(raw)
    if method=="KL":
        return project_kl(raw)
    raise ValueError(method)


def clr(qv:np.ndarray) -> np.ndarray:
    z=np.log(np.maximum(np.asarray(qv,dtype=float),EPS))
    return z-float(z.mean())


def build_source_series(
    source:pd.DataFrame,
    source_ids:list[str],
    event_id:str,
    split:dict,
) -> pd.DataFrame:
    f=source[source["market_id"].isin(set(source_ids))].sort_values(
        ["block_number","log_index"]
    )
    state={}
    previous={"QP":None,"KL":None}
    out=[]
    for block,g in f.groupby("block_number",sort=True):
        for ev in g.itertuples(index=False):
            state[str(ev.market_id)]=(float(ev.p_yes),int(ev.ts),int(ev.block_number))
        if len(state)!=len(source_ids):
            continue
        block_ts=int(g["ts"].max())
        raw=[]
        ages=[]
        for mid in source_ids:
            p,sts,_=state[mid]
            age=block_ts-sts
            if age<0:
                raise RuntimeError("negative source age")
            raw.append(p)
            ages.append(age)
        if max(ages)>MAX_AGE_S:
            continue
        sp=split_name(block_ts,split)
        if sp is None:
            continue
        row={
            "event_id":event_id,
            "split":sp,
            "block_number":int(block),
            "ts":block_ts,
            "max_component_age_s":int(max(ages)),
            "raw_sum":float(np.sum(raw)),
        }
        rawv=np.asarray(raw,dtype=float)
        for method in ("QP","KL"):
            qv=source_distribution(rawv,method)
            zv=clr(qv)
            prev=previous[method]
            dv=None if prev is None else zv-prev
            row[f"q_{method.lower()}"]=qv
            row[f"x_{method.lower()}"]=dv
            row[f"norm_{method.lower()}"]=(
                np.nan if dv is None else float(np.linalg.norm(dv))
            )
            previous[method]=zv
        out.append(row)
    return pd.DataFrame(out).sort_values(["block_number"])


def train_threshold(series:pd.DataFrame,method:str) -> float | None:
    col=f"norm_{method.lower()}"
    vals=series.loc[
        (series["split"]=="TRAIN") & series[col].notna() & (series[col]>0),
        col,
    ].to_numpy(dtype=float)
    if len(vals)<20:
        return None
    return float(np.quantile(vals,0.75))


def select_shocks(series:pd.DataFrame,method:str,threshold:float) -> pd.DataFrame:
    ncol=f"norm_{method.lower()}"
    xcol=f"x_{method.lower()}"
    f=series[series[ncol].notna() & (series[ncol]>=threshold)].copy()
    rows=[]
    last_by_split={}
    for ev in f.sort_values(["ts","block_number"]).itertuples(index=False):
        sp=str(ev.split)
        last=last_by_split.get(sp)
        if last is not None and int(ev.ts)-last<COOLDOWN_S:
            continue
        d=ev._asdict()
        d["x"]=np.asarray(d[xcol],dtype=float)
        rows.append(d)
        last_by_split[sp]=int(ev.ts)
    return pd.DataFrame(rows)


def target_arrays(direct:pd.DataFrame,target:str):
    f=direct[direct["sig_market_id"]==target].sort_values(["block_number"]).copy()
    return (
        f["block_number"].to_numpy(dtype=np.int64),
        f["ts"].to_numpy(dtype=np.int64),
        f["p_target"].to_numpy(dtype=float),
    )


def match_shocks(
    shocks:pd.DataFrame,
    direct:pd.DataFrame,
    target:str,
    split:dict,
) -> pd.DataFrame:
    if shocks.empty:
        return pd.DataFrame()
    blocks,tss,prices=target_arrays(direct,target)
    rows=[]
    for _,ev in shocks.iterrows():
        b=int(ev["block_number"])
        prev_idx=int(np.searchsorted(blocks,b,side="left")-1)
        fut_idx=int(np.searchsorted(blocks,b,side="right"))
        if prev_idx<0 or fut_idx>=len(blocks):
            continue
        future_ts=int(tss[fut_idx])
        if split_name(future_ts,split)!=str(ev["split"]):
            continue
        latency=future_ts-int(ev["ts"])
        if latency<0:
            raise RuntimeError("target response precedes source shock")
        d=ev.to_dict()
        d.update({
            "target_before_block":int(blocks[prev_idx]),
            "target_before_ts":int(tss[prev_idx]),
            "target_before":float(prices[prev_idx]),
            "target_future_block":int(blocks[fut_idx]),
            "target_future_ts":future_ts,
            "target_future":float(prices[fut_idx]),
            "latency_s":int(latency),
            "target_change":float(prices[fut_idx]-prices[prev_idx]),
        })
        rows.append(d)
    out=pd.DataFrame(rows)
    if out.empty:
        return out
    out=out.sort_values(["target_future_block","block_number"]).drop_duplicates(
        ["target_future_block"],keep="first"
    )
    return out.sort_values(["block_number"])


def stack_x(frame:pd.DataFrame) -> np.ndarray:
    return np.vstack([np.asarray(x,dtype=float) for x in frame["x"]])


def fit_ridge(train:pd.DataFrame):
    if len(train)<20:
        return None
    X=stack_x(train)
    y=train["target_change"].to_numpy(dtype=float)
    scale=X.std(axis=0,ddof=0)
    scale=np.where(scale<1e-9,1.0,scale)
    Xs=X/scale
    A=Xs.T@Xs + RIDGE_LAMBDA*np.eye(Xs.shape[1])
    b=Xs.T@y
    w=np.linalg.solve(A,b)
    return scale,w


def bootstrap_days(frame:pd.DataFrame,pred:np.ndarray,seed:int) -> dict:
    f=frame[["ts","target_before","target_future"]].copy()
    f["pred"]=pred
    f["day"]=pd.to_datetime(f["ts"],unit="s",utc=True).dt.strftime("%Y-%m-%d")
    f["imp"]=np.abs(f["target_future"]-f["target_before"])-np.abs(
        f["target_future"]-f["pred"]
    )
    agg=f.groupby("day")["imp"].agg(["sum","count"])
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


def evaluate(matched:pd.DataFrame,horizon:int,seed:int):
    f=matched[matched["latency_s"]<=horizon].copy()
    train=f[f["split"]=="TRAIN"].copy()
    dev=f[f["split"]=="DEV"].copy()
    fit=fit_ridge(train)
    if fit is None or dev.empty:
        return {
            "status":"INSUFFICIENT_SUPPORT",
            "horizon_seconds":horizon,
            "train_rows":int(len(train)),
            "dev_rows":int(len(dev)),
        },pd.DataFrame()
    scale,w=fit
    Xd=stack_x(dev)/scale
    delta_pred=Xd@w
    pred=np.clip(dev["target_before"].to_numpy(dtype=float)+delta_pred,0.0,1.0)
    before=dev["target_before"].to_numpy(dtype=float)
    future=dev["target_future"].to_numpy(dtype=float)
    base_abs=np.abs(future-before)
    mod_abs=np.abs(future-pred)
    base_mae=float(base_abs.mean())
    model_mae=float(mod_abs.mean())
    imp=base_mae-model_mae
    order=np.argsort(dev["ts"].to_numpy(dtype=np.int64))
    cut=max(1,len(order)//2)
    halves=[]
    for idx in (order[:cut],order[cut:]):
        if len(idx)==0:
            halves.append(None)
        else:
            halves.append(float(base_abs[idx].mean()-mod_abs[idx].mean()))
    bs=bootstrap_days(dev,pred,seed)
    metrics={
        "status":"OK",
        "horizon_seconds":horizon,
        "ridge_lambda":RIDGE_LAMBDA,
        "feature_dimension":int(len(w)),
        "train_rows":int(len(train)),
        "dev_rows":int(len(dev)),
        "dev_day_clusters":int(bs.get("clusters",0)),
        "baseline_mae":base_mae,
        "model_mae":model_mae,
        "absolute_mae_improvement":imp,
        "relative_mae_improvement":float(imp/base_mae) if base_mae>0 else None,
        "baseline_rmse":float(np.sqrt(np.mean((future-before)**2))),
        "model_rmse":float(np.sqrt(np.mean((future-pred)**2))),
        "direction_accuracy":float(np.mean(
            np.sign(pred-before)==np.sign(future-before)
        )),
        "first_half_mae_improvement":halves[0],
        "second_half_mae_improvement":halves[1],
        "median_latency_s":float(dev["latency_s"].median()),
        "p90_latency_s":float(dev["latency_s"].quantile(0.9)),
        "bootstrap_day":bs,
    }
    export=dev[[
        "event_id","split","block_number","ts","target_before_block",
        "target_before_ts","target_before","target_future_block",
        "target_future_ts","target_future","latency_s","target_change",
    ]].copy()
    export["pred_target"]=pred
    export["pred_change"]=pred-before
    export["shock_norm"]=[float(np.linalg.norm(np.asarray(x,dtype=float))) for x in dev["x"]]
    return metrics,export


def gate(m:dict) -> bool:
    if m.get("status")!="OK":
        return False
    bs=m.get("bootstrap_day",{})
    return bool(
        m.get("train_rows",0)>=40
        and m.get("dev_rows",0)>=20
        and m.get("dev_day_clusters",0)>=4
        and m.get("absolute_mae_improvement",0)>0
        and bs.get("status")=="OK"
        and bs.get("lower_95",-1)>0
        and (m.get("first_half_mae_improvement") or 0)>=0
        and (m.get("second_half_mae_improvement") or 0)>=0
        and m.get("direction_accuracy",0)>0.5
    )


def main():
    split=load_split()
    final_start=int(split["final_start_epoch"])
    root=locate_data004_root()
    direct=load_direct_events(final_start)
    source=load_source_rows(root,final_start)

    source_cache={}
    for spec in PAIR_SPECS:
        event_id=spec["event_id"]
        if event_id not in source_cache:
            source_cache[event_id]=build_source_series(
                source,spec["source_ids"],event_id,split
            )

    results={}
    exports=[]
    source_summary={}
    for event_id,series in source_cache.items():
        source_summary[event_id]={
            "rows":int(len(series)),
            "train_rows":int((series["split"]=="TRAIN").sum()),
            "dev_rows":int((series["split"]=="DEV").sum()),
        }

    for pi,spec in enumerate(PAIR_SPECS):
        pair_id=spec["pair_id"]
        series=source_cache[spec["event_id"]]
        pair={
            "event_id":spec["event_id"],
            "target_sig_market_id":spec["target"],
            "label":spec["label"],
            "methods":{},
        }
        for mi,method in enumerate(("QP","KL")):
            threshold=train_threshold(series,method)
            if threshold is None:
                pair["methods"][method]={"status":"INSUFFICIENT_TRAIN_SHOCK_SUPPORT"}
                continue
            shocks=select_shocks(series,method,threshold)
            matched=match_shocks(shocks,direct,spec["target"],split)
            mres={
                "train_q75_clr_norm_threshold":threshold,
                "shock_rows":int(len(shocks)),
                "matched_rows":int(len(matched)),
                "horizons":{},
            }
            for hi,h in enumerate(HORIZONS):
                met,exp=evaluate(matched,h,RNG_SEED+1000*pi+100*mi+hi)
                met["primary_gate_pass"]=bool(h==300 and gate(met))
                mres["horizons"][str(h)]=met
                if len(exp):
                    exp["pair_id"]=pair_id
                    exp["method"]=method
                    exp["horizon_seconds"]=h
                    exports.append(exp)
            pair["methods"][method]=mres
        pair["candidate"]=bool(
            pair["methods"].get("QP",{}).get("horizons",{}).get("300",{}).get("primary_gate_pass",False)
            and pair["methods"].get("KL",{}).get("horizons",{}).get("300",{}).get("primary_gate_pass",False)
        )
        results[pair_id]=pair

    candidates=[pid for pid,p in results.items() if p["candidate"]]
    result={
        "schema_version":1,
        "experiment_id":"R3-FV-001",
        "subexperiment":"R3-P0-SIMPLEX-LEADLAG-001",
        "stage":"TRAIN_DEV_ONLY",
        "empirical_universe":{
            "source":"DATA-004","target":"DATA-003",
            "data001_used":False,"historical_analogue_families_used":False,
        },
        "final_opened":False,
        "final_rows_accessed":0,
        "representation":"CLR delta of QP/KL coherent source distribution",
        "ridge_lambda":RIDGE_LAMBDA,
        "max_component_age_seconds":MAX_AGE_S,
        "cooldown_seconds":COOLDOWN_S,
        "primary_horizon_seconds":300,
        "source_summary":source_summary,
        "pairs":results,
        "candidate_pairs":candidates,
        "programme_candidate":bool(candidates),
        "decision_policy":"Pairwise 300s primary only; 30s/5s cannot rescue. Candidate must pass under both QP and KL.",
    }
    (OUT/"P0_SIMPLEX_LEADLAG_RESULTS.json").write_text(
        json.dumps(result,indent=2,sort_keys=True)+"\n"
    )
    for event_id,series in source_cache.items():
        clean=series.drop(columns=[
            c for c in series.columns if c.startswith("q_") or c.startswith("x_")
        ])
        clean.to_csv(OUT/f"SOURCE_SERIES_{event_id}.csv",index=False)
    if exports:
        pd.concat(exports,ignore_index=True).to_csv(
            OUT/"DEV_MATCHED_EVENTS.csv",index=False
        )

    print("R3_P0_SIMPLEX_LEADLAG_RESULT="+json.dumps({
        "candidate_pairs":candidates,
        "programme_candidate":bool(candidates),
        "final_opened":False,
    },sort_keys=True),flush=True)


if __name__=="__main__":
    main()
