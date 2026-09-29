# ruff: noqa
from __future__ import annotations

import json
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

OUT=Path("/kaggle/working/r3_fv_001_seat_calibrated_ecm")
OUT.mkdir(parents=True,exist_ok=True)

DATA004_SLUG="sig-cup-data-004-ets-p0p1-fills"
BLOCK_GATE_FRAGMENT="005b-data003-block-gate"
PREPARE_FRAGMENT="r3-fv-001-prepare-v2"
MAX_AGE_S=86400
BOOTSTRAPS=1000
RNG_SEED=20260930

HOUSE_IDS=["919489","919490","919491","919492","919493","919494","919495","919496","919497","919498"]
SENATE_IDS=["943819","943820","943821","943822","943823","943824","943825","943826","943827","943828","943829"]
HOUSE_TARGET="152"
SENATE_TARGET="154"


def q(path:Path)->str:
    return str(path).replace("'","''")


def idstr(v)->str:
    if v is None:return ""
    try:
        if pd.isna(v):return ""
    except Exception:pass
    s=str(v).strip()
    if s.endswith(".0") and s[:-2].isdigit():return s[:-2]
    return s


def locate(name:str,fragment:str)->Path:
    m=[p for p in Path("/kaggle/input").rglob(name) if fragment in str(p)]
    if len(m)!=1:raise RuntimeError(f"expected one {name} under {fragment}, got {m}")
    return m[0]


def locate_data004_root()->Path:
    m=[]
    for p in Path("/kaggle/input").rglob("MANIFEST.json"):
        if DATA004_SLUG in str(p.parent):m.append(p.parent)
    m=sorted(set(m))
    if len(m)!=1:raise RuntimeError(f"expected one DATA-004 root, got {m}")
    return m[0]


def load_split()->dict:
    x=json.loads(locate("R3_SPLIT_MANIFEST.json",PREPARE_FRAGMENT).read_text())
    if x.get("final_opened") is not False:raise RuntimeError("FINAL opened")
    return x


def split_name(ts:int,split:dict)->str|None:
    ds=int(split["dev_start_epoch"]); fs=int(split["final_start_epoch"])
    purge=int(split["selection_rule"]["boundary_purge_seconds"])
    if ts<ds-purge:return "TRAIN"
    if ds<=ts<fs-purge:return "DEV"
    if ts>=fs:raise RuntimeError("FINAL reached")
    return None


def load_direct(final_start:int)->pd.DataFrame:
    econ=locate("economic_fills_DATA003.parquet",BLOCK_GATE_FRAGMENT)
    txb=locate("tx_block_DATA003.parquet",BLOCK_GATE_FRAGMENT)
    bts=locate("block_timestamp.parquet",BLOCK_GATE_FRAGMENT)
    con=duckdb.connect();con.execute("pragma threads=4")
    df=con.execute(f"""
      with r as (
        select cast(e.timestamp as bigint) ts,cast(t.block_number as bigint) block_number,
               cast(e.log_index as bigint) log_index,cast(e.sig_market_id as varchar) sig_market_id,
               case when upper(cast(e.mapping_direction as varchar))='SAME' then cast(e.p_yes as double)
                    when upper(cast(e.mapping_direction as varchar))='COMPLEMENT' then 1.0-cast(e.p_yes as double)
                    else null end aligned_p
        from read_parquet('{q(econ)}') e
        join read_parquet('{q(txb)}') t on lower(cast(e.tx_hash as varchar))=t.tx_hash
        join read_parquet('{q(bts)}') b using(block_number)
        where upper(cast(e.mapping_class as varchar)) in ('EXACT','NEAR')
          and upper(cast(e.mapping_direction as varchar)) in ('SAME','COMPLEMENT')
          and cast(e.sig_market_id as varchar) in ('{HOUSE_TARGET}','{SENATE_TARGET}')
          and cast(e.timestamp as bigint)<{final_start}
      )
      select block_number,max(ts) ts,sig_market_id,arg_max(aligned_p,log_index) p_target
      from r where aligned_p between 0 and 1 group by 1,3 order by 1,3
    """).fetchdf();con.close()
    df["sig_market_id"]=df["sig_market_id"].map(idstr)
    return df


def load_source(root:Path,final_start:int)->pd.DataFrame:
    files=sorted(root.rglob("fills/date=*/part-*.parquet"))
    sql=",".join("'" + q(p) + "'" for p in files)
    wanted=HOUSE_IDS+SENATE_IDS
    wsql=",".join("'" + x + "'" for x in wanted)
    con=duckdb.connect();con.execute("pragma threads=4")
    df=con.execute(f"""
      select cast(block_number as bigint) block_number,cast(log_index as bigint) log_index,
             cast("timestamp" as bigint) ts,cast(market_id as varchar) market_id,
             case when upper(cast(outcome_label as varchar))='YES' then cast(price as double)
                  when upper(cast(outcome_label as varchar))='NO' then 1.0-cast(price as double)
                  else null end p_yes
      from read_parquet([{sql}],union_by_name=true)
      where upper(cast(order_role as varchar))='TAKER'
        and cast(price as double) between 0 and 1
        and cast("timestamp" as bigint)<{final_start}
        and cast(market_id as varchar) in ({wsql})
      order by block_number,log_index
    """).fetchdf();con.close()
    df["market_id"]=df["market_id"].map(idstr)
    return df[df["p_yes"].notna()].copy()


class Lookup:
    def __init__(self,df:pd.DataFrame):
        self.d={}
        for mid,g in df.groupby("market_id",sort=False):
            g=g.sort_values(["block_number","log_index"])
            self.d[str(mid)]=(g["block_number"].to_numpy(np.int64),g["ts"].to_numpy(np.int64),g["p_yes"].to_numpy(float))
    def latest_before(self,mid:str,block:int):
        x=self.d.get(mid)
        if x is None:return None
        blocks,ts,p=x
        i=int(np.searchsorted(blocks,block,side="left")-1)
        if i<0:return None
        return float(p[i]),int(ts[i]),int(blocks[i])
    def complete(self,ids:list[str],block:int,target_ts:int):
        vals=[];ages=[]
        for mid in ids:
            st=self.latest_before(mid,block)
            if st is None:return None
            p,sts,_=st;age=target_ts-sts
            if age<0 or age>MAX_AGE_S:return None
            vals.append(p);ages.append(age)
        return np.asarray(vals,float),int(max(ages))


def project_simplex(v):
    v=np.asarray(v,float);u=np.sort(v)[::-1];cssv=np.cumsum(u);idx=np.arange(1,len(v)+1)
    cond=u-(cssv-1.0)/idx>0
    if not np.any(cond):return np.full_like(v,1/len(v))
    rho=int(np.where(cond)[0][-1]);theta=(cssv[rho]-1.0)/(rho+1)
    return np.maximum(v-theta,0.0)


def project_kl(v):
    x=np.maximum(np.asarray(v,float),1e-9);return x/x.sum()


def scalar(raw,family,method):
    qv=project_simplex(raw) if method=="QP" else project_kl(raw)
    if family=="HOUSE":return float(qv[7]+qv[8]+qv[9]+0.4*qv[6])
    return float(qv[3:].sum())


def build_target_base(direct,target,split):
    f=direct[direct["sig_market_id"]==target].sort_values(["block_number"]).copy()
    f["split"]=f["ts"].map(lambda x:split_name(int(x),split))
    f=f[f["split"].notna()].copy()
    f["prev"]=f["p_target"].shift(1)
    f["next_y"]=f["p_target"].shift(-1)
    f["next_split"]=f["split"].shift(-1)
    f.loc[f["next_split"]!=f["split"],"next_y"]=np.nan
    return f.rename(columns={"p_target":"y"})


def build_rows(base,lookup,ids,family):
    rows=[]
    for ev in base.itertuples(index=False):
        if not np.isfinite(ev.prev):continue
        st=lookup.complete(ids,int(ev.block_number),int(ev.ts))
        if st is None:continue
        raw,age=st
        d=ev._asdict()
        d["source_age_max_s"]=age
        for method in ("QP","KL"):
            d[f"struct_{method.lower()}"]=scalar(raw,family,method)
        rows.append(d)
    return pd.DataFrame(rows)


def fit_affine(train,col):
    f=train[[col,"y"]].dropna()
    if len(f)<30:return None
    X=np.column_stack([np.ones(len(f)),f[col].to_numpy(float)])
    y=f["y"].to_numpy(float)
    coef=np.linalg.lstsq(X,y,rcond=None)[0]
    return float(coef[0]),float(coef[1])


def bootstrap(frame,pred_col,y_col,base_col,seed):
    f=frame[["ts",pred_col,y_col,base_col]].dropna().copy()
    f["day"]=pd.to_datetime(f["ts"],unit="s",utc=True).dt.strftime("%Y-%m-%d")
    f["imp"]=np.abs(f[y_col]-f[base_col])-np.abs(f[y_col]-f[pred_col])
    agg=f.groupby("day")["imp"].agg(["sum","count"])
    if len(agg)<2:return {"status":"INSUFFICIENT_DAY_CLUSTERS","clusters":int(len(agg))}
    sums=agg["sum"].to_numpy(float);counts=agg["count"].to_numpy(float)
    rng=np.random.default_rng(seed);draws=np.empty(BOOTSTRAPS)
    for i in range(BOOTSTRAPS):
        idx=rng.integers(0,len(agg),size=len(agg));draws[i]=sums[idx].sum()/counts[idx].sum()
    return {"status":"OK","clusters":int(len(agg)),"lower_95":float(np.quantile(draws,.025)),"median":float(np.quantile(draws,.5)),"upper_95":float(np.quantile(draws,.975))}


def metrics(frame,pred_col,y_col,base_col,seed):
    f=frame[[pred_col,y_col,base_col,"ts"]].dropna().copy()
    if f.empty:return {"status":"NO_ROWS","rows":0}
    pred=f[pred_col].to_numpy(float);y=f[y_col].to_numpy(float);base=f[base_col].to_numpy(float)
    ba=np.abs(y-base);ma=np.abs(y-pred);imp=float(ba.mean()-ma.mean())
    order=np.argsort(f["ts"].to_numpy(np.int64));cut=max(1,len(order)//2)
    halves=[]
    for idx in (order[:cut],order[cut:]):
        halves.append(None if len(idx)==0 else float(ba[idx].mean()-ma[idx].mean()))
    bs=bootstrap(f,pred_col,y_col,base_col,seed)
    return {"status":"OK","rows":int(len(f)),"dev_day_clusters":int(bs.get("clusters",0)),
            "baseline_mae":float(ba.mean()),"model_mae":float(ma.mean()),"absolute_mae_improvement":imp,
            "relative_mae_improvement":float(imp/ba.mean()) if ba.mean()>0 else None,
            "direction_accuracy":float(np.mean(np.sign(pred-base)==np.sign(y-base))),
            "first_half_mae_improvement":halves[0],"second_half_mae_improvement":halves[1],"bootstrap_day":bs}


def fit_ecm(train,fv_col):
    f=train[[fv_col,"y","next_y"]].dropna()
    if len(f)<50:return None
    x=(f[fv_col]-f["y"]).to_numpy(float);dy=(f["next_y"]-f["y"]).to_numpy(float)
    den=float(x@x)
    if den<=1e-14:return None
    return float((x@dy)/den)


def gate(m,train_rows):
    if m.get("status")!="OK":return False
    bs=m.get("bootstrap_day",{})
    return bool(train_rows>=100 and m.get("rows",0)>=30 and m.get("dev_day_clusters",0)>=4
                and m.get("absolute_mae_improvement",0)>0 and bs.get("status")=="OK"
                and bs.get("lower_95",-1)>0
                and (m.get("first_half_mae_improvement") or 0)>=0
                and (m.get("second_half_mae_improvement") or 0)>=0
                and m.get("direction_accuracy",0)>0.5)


def family_result(rows,family,seed):
    train=rows[rows["split"]=="TRAIN"].copy();dev=rows[rows["split"]=="DEV"].copy()
    result={"train_rows":int(len(train)),"dev_rows":int(len(dev)),"methods":{}}
    for i,method in enumerate(("QP","KL")):
        scol=f"struct_{method.lower()}"
        ab=fit_affine(train,scol)
        if ab is None:
            result["methods"][method]={"status":"INSUFFICIENT_TRAIN"}
            continue
        a,b=ab
        for f in (train,dev):
            f[f"fv_{method.lower()}"]=np.clip(a+b*f[scol],0,1)
        level=metrics(dev,f"fv_{method.lower()}","y","prev",seed+10*i)
        beta=fit_ecm(train,f"fv_{method.lower()}")
        if beta is None:
            ecm={"status":"INSUFFICIENT_TRAIN"}
        else:
            predcol=f"pred_next_{method.lower()}"
            dev[predcol]=np.clip(dev["y"]+beta*(dev[f"fv_{method.lower()}"]-dev["y"]),0,1)
            ecm=metrics(dev,predcol,"next_y","y",seed+100+10*i)
            ecm["beta_train"]=beta
        result["methods"][method]={"affine_intercept":a,"affine_slope":b,"level":level,"ecm_next_update":ecm,
                                   "candidate_gate_pass":gate(ecm,int(len(train)))}
    result["candidate"]=bool(all(result["methods"].get(m,{}).get("candidate_gate_pass",False) for m in ("QP","KL")))
    return result,train,dev


def main():
    split=load_split();fs=int(split["final_start_epoch"])
    direct=load_direct(fs);source=load_source(locate_data004_root(),fs);lookup=Lookup(source)
    hb=build_target_base(direct,HOUSE_TARGET,split);sb=build_target_base(direct,SENATE_TARGET,split)
    hr=build_rows(hb,lookup,HOUSE_IDS,"HOUSE");sr=build_rows(sb,lookup,SENATE_IDS,"SENATE")
    house,htr,hdev=family_result(hr,"HOUSE",RNG_SEED+1000)
    senate,strn,sdev=family_result(sr,"SENATE",RNG_SEED+2000)
    result={"schema_version":1,"experiment_id":"R3-FV-001","subexperiment":"R3-SEAT-CALIBRATED-ECM-001",
            "stage":"ADAPTIVE_TRAIN_DEV","empirical_universe":{"source":"DATA-004","target":"DATA-003","data001_used":False},
            "final_opened":False,"final_rows_accessed":0,"house":house,"senate_t50":senate,
            "programme_candidate":bool(house["candidate"] or senate["candidate"]),
            "decision_policy":"Adaptive DEV method; any survivor must be frozen before one-shot FINAL."}
    (OUT/"SEAT_CALIBRATED_ECM_RESULTS.json").write_text(json.dumps(result,indent=2,sort_keys=True)+"\n")
    if len(hdev):hdev.to_csv(OUT/"HOUSE_DEV_EVENTS.csv",index=False)
    if len(sdev):sdev.to_csv(OUT/"SENATE_DEV_EVENTS.csv",index=False)
    print("R3_SEAT_CALIBRATED_ECM_RESULT="+json.dumps({"house_candidate":house["candidate"],"senate_candidate":senate["candidate"],"programme_candidate":result["programme_candidate"],"final_opened":False},sort_keys=True),flush=True)


if __name__=="__main__":
    main()
