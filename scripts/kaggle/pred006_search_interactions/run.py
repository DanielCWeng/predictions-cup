# ruff: noqa
from __future__ import annotations

import hashlib, json, math
from collections import defaultdict, deque
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.impute import SimpleImputer
from sklearn.linear_model import Ridge
from sklearn.metrics import mean_absolute_error, mean_squared_error
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

OUTROOT=Path("/kaggle/working/pred006_search")
HORIZONS=(5,30,120,600,1800)
WINDOWS=(5,30,120,600,1800)
SEED=606006
BOOT=500
MIN_TRAIN=1000
MIN_DEV=300
MIN_CONDITIONS=25

def q(p:Path)->str: return str(p).replace("'","''")

def sha256(p:Path)->str:
    h=hashlib.sha256()
    with p.open("rb") as f:
        for c in iter(lambda:f.read(1<<20),b""): h.update(c)
    return h.hexdigest()

def locate(name:str,fragment:str)->Path:
    m=[p for p in Path("/kaggle/input").rglob(name) if fragment in str(p)]
    if len(m)!=1: raise RuntimeError(f"expected one {name} under {fragment}, got {m}")
    return m[0]

def fill_files()->list[Path]:
    out=[]
    for p in Path("/kaggle/input").rglob("*.parquet"):
        s=str(p)
        if "/fills/" in s and "/fees/" not in s and "/rebates/" not in s and "/unattributed_fee_legs/" not in s:
            out.append(p)
    if not out: raise RuntimeError("DATA-003 fill parquet files not found")
    return sorted(out)

def load_split()->dict[str,Any]:
    p=locate("split_manifest.json","pred006-phase0-audit")
    d=json.loads(p.read_text())
    if d.get("predictive_outcomes_accessed") is not False: raise RuntimeError("Phase0 split is contaminated")
    if d.get("ordering") != ["block_number","log_index"]: raise RuntimeError("wrong ordering")
    return d

def build_frame(final_start:int)->pd.DataFrame:
    econ=locate("economic_fills_DATA003.parquet","005b-data003-block-gate")
    txb=locate("tx_block_DATA003.parquet","005b-data003-block-gate")
    bts=locate("block_timestamp.parquet","005b-data003-block-gate")
    raw=fill_files()
    rawsql=",".join("'" + q(p) + "'" for p in raw)
    con=duckdb.connect()
    con.execute("pragma threads=4")
    con.execute(f"""
      create temp view active as
      select
        cast(condition_id as varchar) AS condition_id,
        lower(cast(tx_hash as varchar)) AS tx_hash,
        max(lower(cast(participant_address as varchar))) filter(where cast(order_is_match_taker_order as boolean)) AS actor,
        max(upper(cast(outcome_side as varchar))) filter(where cast(order_is_match_taker_order as boolean)) AS active_outcome,
        max(upper(cast(side as varchar))) filter(where cast(order_is_match_taker_order as boolean)) AS active_side
      from read_parquet([{rawsql}],union_by_name=true)
      where cast(timestamp as bigint) < {int(final_start)}
      group by 1,2
    """)
    df=con.execute(f"""
      with enriched as (
        select
          b.block_timestamp AS event_ts,
          t.block_number,
          cast(e.log_index as bigint) AS log_index,
          lower(cast(e.tx_hash as varchar)) AS tx_hash,
          cast(e.condition_id as varchar) AS condition_id,
          cast(e.p_yes as double) AS p_yes,
          cast(e.size_shares as double) AS size_shares,
          cast(e.value_usd as double) AS value_usd,
          cast(e.sig_market_id as varchar) AS sig_market_id,
          upper(cast(e.mapping_class as varchar)) AS mapping_class,
          upper(cast(e.mapping_direction as varchar)) AS mapping_direction,
          cast(e.window_id as varchar) AS window_id,
          a.actor,
          case
            when a.active_outcome='YES' and a.active_side='BUY' then 1.0
            when a.active_outcome='YES' and a.active_side='SELL' then -1.0
            when a.active_outcome='NO' and a.active_side='BUY' then -1.0
            when a.active_outcome='NO' and a.active_side='SELL' then 1.0
            else 0.0
          end AS active_yes_sign
        from read_parquet('{q(econ)}') e
        join read_parquet('{q(txb)}') t
          on lower(cast(e.tx_hash as varchar))=t.tx_hash
        join read_parquet('{q(bts)}') b using(block_number)
        left join active a
          on cast(e.condition_id as varchar)=a.condition_id
         and lower(cast(e.tx_hash as varchar))=a.tx_hash
        where b.block_timestamp < {int(final_start)}
      )
      select
        event_ts,
        block_number,
        condition_id,
        arg_max(p_yes,log_index) AS p_yes,
        sum(size_shares) AS size_shares,
        sum(value_usd) AS value_usd,
        arg_max(sig_market_id,log_index) AS sig_market_id,
        arg_max(mapping_class,log_index) AS mapping_class,
        arg_max(mapping_direction,log_index) AS mapping_direction,
        arg_max(window_id,log_index) AS window_id,
        count(*) AS block_trade_count,
        count(distinct actor) filter(where actor is not null) AS block_actor_count,
        sum(active_yes_sign*value_usd) AS block_signed_flow,
        arg_max(actor,log_index) AS actor
      from enriched
      group by event_ts,block_number,condition_id
      order by block_number,condition_id
    """).df()
    con.close()
    df=df.rename(columns={"event_ts":"timestamp"})
    if df.empty: raise RuntimeError("empty pre-final block-end frame")
    if int(df["timestamp"].max()) >= int(final_start): raise RuntimeError("FINAL leaked into search frame")
    if df.duplicated(["condition_id","block_number"]).any():
        raise RuntimeError("duplicate condition/block observations")
    return df.reset_index(drop=True)

def rolling_group_features(df:pd.DataFrame)->pd.DataFrame:
    n=len(df)
    p=df["p_yes"].to_numpy(float)
    ts=df["timestamp"].to_numpy(np.int64)
    innov=np.zeros(n,float)
    since_prev=np.full(n,np.nan)
    since_move=np.full(n,np.nan)
    market_age=np.zeros(n,float)
    for w in WINDOWS:
        df[f"mom_{w}"]=np.nan
        df[f"vol_{w}"]=np.nan
        df[f"count_{w}"]=np.nan
    # never cross DATA-003 capture-window boundaries
    for _,idx in df.groupby(["window_id","condition_id"],sort=False).indices.items():
        idx=np.asarray(idx,dtype=int)
        t=ts[idx]; x=p[idx]
        dp=np.zeros(len(idx),float)
        if len(idx)>1: dp[1:]=np.diff(x)
        innov[idx]=dp
        if len(idx)>1: since_prev[idx[1:]]=np.diff(t)
        last_move=None
        for k,pos in enumerate(idx):
            if k>0 and abs(dp[k])>1e-12: last_move=int(t[k-1])
            since_move[pos]=np.nan if last_move is None else float(t[k]-last_move)
        market_age[idx]=t-t[0]
        sqpref=np.concatenate([[0.0],np.cumsum(dp*dp)])
        for w in WINDOWS:
            left=np.searchsorted(t,t-w,side="left")
            loc=np.arange(len(idx))
            mom=x-x[left]
            vol=np.sqrt(np.maximum(0.0,sqpref[loc+1]-sqpref[left]))
            cnt=loc-left+1
            # Require a complete lookback interval inside this capture window.
            supported=(t-w)>=t[0]
            df.loc[idx,f"mom_{w}"]=np.where(supported,mom,np.nan)
            df.loc[idx,f"vol_{w}"]=np.where(supported,vol,np.nan)
            df.loc[idx,f"count_{w}"]=np.where(supported,cnt,np.nan)
    df["innovation"]=innov
    df["since_prev"]=since_prev
    df["since_move"]=since_move
    df["market_age"]=market_age
    df["p_yes_clip"]=np.clip(p,1e-4,1-1e-4)
    df["logit_p"]=np.log(df["p_yes_clip"]/(1-df["p_yes_clip"]))
    df["boundary_distance"]=np.minimum(p,1-p)
    df["size_log"]=np.log1p(np.maximum(0,df["size_shares"].to_numpy(float)))
    df["value_log"]=np.log1p(np.maximum(0,df["value_usd"].to_numpy(float)))
    df["block_trade_count_log"]=np.log1p(np.maximum(0,df["block_trade_count"].to_numpy(float)))
    df["block_actor_count_log"]=np.log1p(np.maximum(0,df["block_actor_count"].to_numpy(float)))
    secday=np.mod(ts,86400)
    df["tod_sin"]=np.sin(2*np.pi*secday/86400)
    df["tod_cos"]=np.cos(2*np.pi*secday/86400)
    for klass in ("EXACT","DERIVED","NEAR"):
        df[f"map_{klass.lower()}"]=(df["mapping_class"].astype(str)==klass).astype(float)
    return df

def add_group_rolling_sum(df:pd.DataFrame,keys:list[str],value_col:str,prefix:str,windows:tuple[int,...]):
    vals=df[value_col].to_numpy(float)
    ts=df["timestamp"].to_numpy(np.int64)
    for w in windows: df[f"{prefix}_{w}"]=np.nan
    groups=[np.arange(len(df))] if not keys else list(df.groupby(keys,sort=False).indices.values())
    for raw_idx in groups:
        idx=np.asarray(raw_idx,dtype=int)
        # Group index order follows global chronology because df is canonical-sorted.
        t=ts[idx]; v=vals[idx]
        pref=np.concatenate([[0.0],np.cumsum(v)])
        loc=np.arange(len(idx))
        for w in windows:
            left=np.searchsorted(t,t-w,side="left")
            sums=pref[loc+1]-pref[left]
            df.loc[idx,f"{prefix}_{w}"]=sums

def add_cross(df:pd.DataFrame)->pd.DataFrame:
    keys=["window_id","block_number","timestamp"]
    global_block=(
        df.groupby(keys,as_index=False)
        .agg(
            global_block_innov=("innovation","sum"),
            global_block_activity=("condition_id","nunique"),
            block_p_mean=("p_yes","mean"),
            block_p_std=("p_yes","std"),
        )
        .sort_values(["window_id","timestamp","block_number"])
        .reset_index(drop=True)
    )
    sig_keys=keys+["sig_market_id"]
    sig_block=(
        df.groupby(sig_keys,as_index=False)
        .agg(
            sig_block_innov=("innovation","sum"),
            sig_block_activity=("condition_id","nunique"),
            sig_p_mean=("p_yes","mean"),
        )
        .sort_values(["window_id","sig_market_id","timestamp","block_number"])
        .reset_index(drop=True)
    )
    def rolling_table(table:pd.DataFrame,group_keys:list[str],value:str,prefix:str,windows:tuple[int,...])->pd.DataFrame:
        out=table.copy()
        ts=out["timestamp"].to_numpy(np.int64)
        vals=out[value].to_numpy(float)
        for w in windows: out[f"{prefix}_{w}"]=np.nan
        for _,raw in out.groupby(group_keys,sort=False).indices.items():
            idx=np.sort(np.asarray(raw,dtype=int))
            t=ts[idx]; v=vals[idx]
            pref=np.concatenate([[0.0],np.cumsum(v)])
            loc=np.arange(len(idx))
            for w in windows:
                left=np.searchsorted(t,t-w,side="left")
                out.loc[idx,f"{prefix}_{w}"]=pref[loc+1]-pref[left]
        return out
    windows=(30,120,600,1800)
    global_block=rolling_table(global_block,["window_id"],"global_block_innov","global_innov",windows)
    global_block=rolling_table(global_block,["window_id"],"global_block_activity","global_activity",windows)
    sig_block=rolling_table(sig_block,["window_id","sig_market_id"],"sig_block_innov","sig_innov",windows)
    sig_block=rolling_table(sig_block,["window_id","sig_market_id"],"sig_block_activity","sig_activity",windows)
    gcols=keys+["block_p_mean","block_p_std"]+[f"global_innov_{w}" for w in windows]+[f"global_activity_{w}" for w in windows]
    scols=sig_keys+["sig_p_mean"]+[f"sig_innov_{w}" for w in windows]+[f"sig_activity_{w}" for w in windows]
    df=df.merge(global_block[gcols],on=keys,how="left",validate="many_to_one")
    df=df.merge(sig_block[scols],on=sig_keys,how="left",validate="many_to_one")
    df["block_p_rank"]=df.groupby(keys,sort=False)["p_yes"].rank(pct=True,method="average")
    df["block_p_dispersion"]=df["p_yes"]-df["block_p_mean"]
    df["sig_p_dispersion"]=df["p_yes"]-df["sig_p_mean"]
    df["block_active_conditions_log"]=np.log1p(df["global_activity_30"].fillna(0))
    for w in windows:
        own=df[f"mom_{w}"].fillna(0)
        df[f"sig_other_innov_{w}"]=df[f"sig_innov_{w}"].fillna(0)-own
        df[f"global_other_innov_{w}"]=df[f"global_innov_{w}"].fillna(0)-own
        df[f"resid_sig_{w}"]=df[f"mom_{w}"]-df[f"sig_other_innov_{w}"]
    return df

def add_flow(df:pd.DataFrame)->pd.DataFrame:
    n=len(df); ts=df["timestamp"].to_numpy(np.int64)
    flow=df["block_signed_flow"].fillna(0).to_numpy(float)
    df["signed_flow"]=flow
    df["signed_flow_sqrt"]=np.sign(flow)*np.sqrt(np.abs(flow))
    df["block_abs_flow_log"]=np.log1p(np.abs(flow))
    add_group_rolling_sum(df,["window_id","condition_id"],"signed_flow","own_flow",(30,120,600,1800))
    past_count=np.zeros(n,float); cum=np.zeros(n,float); recent=np.zeros(n,float); breadth=np.zeros(n,float)
    counts=defaultdict(int); sums=defaultdict(float); seen=defaultdict(set); qs=defaultdict(deque); qsum=defaultdict(float)
    actors=df["actor"].fillna("").astype(str).to_numpy()
    cond=df["condition_id"].astype(str).to_numpy()
    for block,raw in df.groupby("block_number",sort=False).indices.items():
        idx=np.sort(np.asarray(raw,dtype=int))
        block_ts=int(ts[idx[0]])
        touched=set()
        for i in idx:
            a=actors[i]
            if not a or a.lower() in ("nan","none"): continue
            dq=qs[a]
            cutoff=block_ts-600
            while dq and dq[0][0] < cutoff:
                _,fv=dq.popleft(); qsum[a]-=fv
            past_count[i]=counts[a]
            cum[i]=sums[a]
            recent[i]=qsum[a]
            breadth[i]=len(seen[a])
            touched.add(a)
        for i in idx:
            a=actors[i]
            if not a or a.lower() in ("nan","none"): continue
            counts[a]+=1
            sums[a]+=float(flow[i])
            seen[a].add(cond[i])
            qs[a].append((block_ts,float(flow[i])))
            qsum[a]+=float(flow[i])
    df["actor_past_count"]=past_count
    df["actor_cum_flow"]=cum
    df["actor_recent_flow_600"]=recent
    df["actor_breadth"]=breadth
    df["actor_is_novel"]=(past_count==0).astype(float)
    return df

def add_interactions(df:pd.DataFrame)->pd.DataFrame:
    df["stale_x_sig120"]=np.log1p(df["since_prev"].fillna(0))*df["sig_other_innov_120"].fillna(0)
    df["flow_x_activity120"]=df["signed_flow_sqrt"].fillna(0)*np.log1p(df["count_120"].fillna(0))
    df["tail_x_vol120"]=(0.5-df["boundary_distance"].fillna(0.5))*df["vol_120"].fillna(0)
    df["actor_x_size"]=np.log1p(df["actor_past_count"].fillna(0))*df["size_log"].fillna(0)
    df["flow_x_tail"]=df["signed_flow_sqrt"].fillna(0)*(0.5-df["boundary_distance"].fillna(0.5))
    df["cross_x_vol"]=df["sig_other_innov_120"].fillna(0)*df["vol_120"].fillna(0)
    return df

def target_arrays(df:pd.DataFrame,h:int)->tuple[np.ndarray,np.ndarray]:
    n=len(df); delta=np.full(n,np.nan); ab=np.full(n,np.nan)
    ts=df["timestamp"].to_numpy(np.int64); p=df["p_yes"].to_numpy(float)
    for _,idx in df.groupby(["window_id","condition_id"],sort=False).indices.items():
        idx=np.asarray(idx,dtype=int); t=ts[idx]; x=p[idx]
        end=int(t[-1])
        j=np.searchsorted(t,t+h,side="right")-1
        j=np.maximum(j,np.arange(len(idx)))
        d=x[j]-x
        supported=(t+h)<=end
        delta[idx]=np.where(supported,d,np.nan)
        ab[idx]=np.where(supported,np.abs(d),np.nan)
    return delta,ab

def family_features(family:str)->list[str]:
    temporal=[
      "p_yes","logit_p","boundary_distance","since_prev","since_move","market_age","size_log","value_log",
      "block_trade_count_log","block_actor_count_log",
      "tod_sin","tod_cos","map_exact","map_derived","map_near",
      "mom_5","mom_30","mom_120","mom_600","mom_1800",
      "vol_30","vol_120","vol_600","vol_1800",
      "count_30","count_120","count_600","count_1800"
    ]
    cross=[
      "sig_other_innov_30","sig_other_innov_120","sig_other_innov_600","sig_other_innov_1800",
      "global_other_innov_30","global_other_innov_120","global_other_innov_600","global_other_innov_1800",
      "sig_activity_30","sig_activity_120","sig_activity_600",
      "resid_sig_30","resid_sig_120","resid_sig_600","resid_sig_1800",
      "block_p_rank","block_p_dispersion","sig_p_dispersion","block_active_conditions_log"
    ]
    flow=[
      "signed_flow_sqrt","own_flow_30","own_flow_120","own_flow_600","own_flow_1800",
      "actor_past_count","actor_cum_flow","actor_recent_flow_600","actor_breadth",
      "actor_is_novel","block_abs_flow_log"
    ]
    interactions=["stale_x_sig120","flow_x_activity120","tail_x_vol120","actor_x_size","flow_x_tail","cross_x_vol"]
    if family=="temporal": return temporal
    if family=="cross_market": return temporal+cross
    if family=="participant_flow": return temporal+flow
    if family=="interactions": return temporal+cross+flow+interactions
    raise ValueError(family)

BASE_FEATURES=["p_yes","mom_30","mom_120","mom_600","vol_120","count_120","since_prev","boundary_distance"]

def X(df:pd.DataFrame,features:list[str])->np.ndarray:
    return df[features].apply(pd.to_numeric,errors="coerce").to_numpy(float)

def make_model(kind:str,param:float):
    if kind=="ridge":
        return make_pipeline(SimpleImputer(strategy="median"),StandardScaler(),Ridge(alpha=param))
    if kind=="hgb":
        leaves=int(param)
        return make_pipeline(SimpleImputer(strategy="median"),HistGradientBoostingRegressor(
            learning_rate=0.05,max_iter=120,max_leaf_nodes=leaves,l2_regularization=2.0,random_state=SEED
        ))
    raise ValueError(kind)

def corr_rank(a:np.ndarray,b:np.ndarray)->float:
    ok=np.isfinite(a)&np.isfinite(b)
    if ok.sum()<3:return math.nan
    ra=pd.Series(a[ok]).rank(method="average").to_numpy()
    rb=pd.Series(b[ok]).rank(method="average").to_numpy()
    if np.std(ra)==0 or np.std(rb)==0:return math.nan
    return float(np.corrcoef(ra,rb)[0,1])

def baseline_predictions(train:pd.DataFrame,dev:pd.DataFrame,ytr:np.ndarray,target_type:str)->dict[str,np.ndarray]:
    if target_type=="delta":
        trivial=np.zeros(len(dev),float)
    else:
        trivial=np.full(len(dev),float(np.nanmedian(ytr)))
    m=make_pipeline(SimpleImputer(strategy="median"),StandardScaler(),Ridge(alpha=10.0))
    m.fit(X(train,BASE_FEATURES),ytr)
    hist=m.predict(X(dev,BASE_FEATURES))
    return {"trivial":trivial,"own_history_ridge":hist}

def spec_models()->list[tuple[str,float]]:
    return [("ridge",0.1),("ridge",1.0),("ridge",10.0),("ridge",100.0),
            ("hgb",7.0),("hgb",15.0),("hgb",31.0)]

def eval_spec(train:pd.DataFrame,dev:pd.DataFrame,ytr:np.ndarray,ydev:np.ndarray,features:list[str],kind:str,param:float,target_type:str)->dict[str,Any]:
    model=make_model(kind,param); model.fit(X(train,features),ytr); pred=model.predict(X(dev,features))
    bases=baseline_predictions(train,dev,ytr,target_type)
    bmetrics={k:float(mean_absolute_error(ydev,v)) for k,v in bases.items()}
    best_name=min(bmetrics,key=bmetrics.get); best=bases[best_name]; best_mae=bmetrics[best_name]
    mae=float(mean_absolute_error(ydev,pred))
    return {
      "kind":kind,"param":param,"mae":mae,"rmse":float(mean_squared_error(ydev,pred)**0.5),
      "rank_ic":corr_rank(pred,ydev),"best_baseline":best_name,"best_baseline_mae":best_mae,
      "improvement_vs_best_baseline":float((best_mae-mae)/best_mae) if best_mae>0 else 0.0,
      "_pred":pred,"_base":best
    }

def diagnostics(dev:pd.DataFrame,y:np.ndarray,pred:np.ndarray,base:np.ndarray)->dict[str,Any]:
    diff=np.abs(y-base)-np.abs(y-pred)
    frame=pd.DataFrame({
        "condition_id":dev["condition_id"].astype(str).to_numpy(),
        "utc_day":(dev["timestamp"].to_numpy(np.int64)//86400),
        "diff":diff,
    })
    per_condition=frame.groupby("condition_id",as_index=False)["diff"].mean()
    per_day=frame.groupby("utc_day",as_index=False)["diff"].mean()
    rng=np.random.default_rng(SEED)
    def boot(values:np.ndarray)->tuple[float,float]:
        reps=np.empty(BOOT,float)
        for i in range(BOOT):
            reps[i]=float(np.mean(values[rng.integers(0,len(values),size=len(values))]))
        return float(np.quantile(reps,0.025)),float(np.quantile(reps,0.975))
    cvals=per_condition["diff"].to_numpy(float)
    dvals=per_day["diff"].to_numpy(float)
    clo,chi=boot(cvals); dlo,dhi=boot(dvals)
    med=int(np.median(dev["timestamp"].to_numpy(np.int64)))
    first=dev["timestamp"].to_numpy(np.int64)<=med
    def imp(mask):
        bm=float(np.mean(np.abs(y[mask]-base[mask]))); mm=float(np.mean(np.abs(y[mask]-pred[mask])))
        return (bm-mm)/bm if bm>0 else 0.0
    positive=np.sort(cvals[cvals>0])[::-1]
    if positive.size:
        k=max(1,int(np.ceil(0.10*positive.size)))
        top_share=float(positive[:k].sum()/positive.sum())
    else:
        top_share=1.0
    yp=rng.permutation(y)
    placebo=corr_rank(pred,yp)
    return {
      "condition_count":int(len(cvals)),
      "day_count":int(len(dvals)),
      "condition_positive_fraction":float(np.mean(cvals>0)),
      "condition_median_diff":float(np.median(cvals)),
      "condition_bootstrap_lower_2_5":clo,
      "condition_bootstrap_upper_97_5":chi,
      "day_bootstrap_lower_2_5":dlo,
      "day_bootstrap_upper_97_5":dhi,
      "top_decile_share_positive_condition_gain":top_share,
      "first_half_improvement":float(imp(first)),
      "second_half_improvement":float(imp(~first)),
      "placebo_rank_ic":float(placebo) if np.isfinite(placebo) else None,
      "bootstrap_repetitions":BOOT
    }

def main(family:str):
    out=OUTROOT/family; out.mkdir(parents=True,exist_ok=True)
    split=load_split(); dev_start=int(split["dev_start_epoch"]); final_start=int(split["final_start_epoch"])
    df=build_frame(final_start)
    df=rolling_group_features(df)
    if family in ("cross_market","interactions"): df=add_cross(df)
    if family in ("participant_flow","interactions"): df=add_flow(df)
    if family=="interactions": df=add_interactions(df)
    if int(df["timestamp"].max())>=final_start: raise RuntimeError("FINAL entered feature frame")
    features=family_features(family)
    missing=[c for c in features if c not in df.columns]
    if missing: raise RuntimeError(f"missing features {missing}")
    hypothesis={
      "schema_version":1,"family":family,"independent_from_005_hypothesis_lists":True,
      "features":features,"targets":["future_yes_delta","future_absolute_movement"],
      "horizons_seconds":list(HORIZONS),"models":[{"kind":k,"param":p} for k,p in spec_models()],
      "observation_unit":"condition_block_end",
      "target_future_rule":"future targets use block-end states and therefore begin strictly after the current Polygon block",
      "final_start_epoch":final_start,"final_rows_accessed_for_predictive_search":0
    }
    hp=out/"hypothesis_ledger.json"; hp.write_text(json.dumps(hypothesis,indent=2,sort_keys=True)+"\n")
    matrix=[]; cache={}
    for h in HORIZONS:
        delta,ab=target_arrays(df,h)
        for target_type,yall in (("delta",delta),("abs_move",ab)):
            purge=int(split["selection_rule"]["target_purge_seconds"])
            trainmask=(df["timestamp"].to_numpy(np.int64)<dev_start-purge)&np.isfinite(yall)
            devmask=(df["timestamp"].to_numpy(np.int64)>=dev_start)&(df["timestamp"].to_numpy(np.int64)<final_start-purge)&np.isfinite(yall)
            tr=df.loc[trainmask].reset_index(drop=True); dv=df.loc[devmask].reset_index(drop=True)
            ytr=yall[trainmask]; ydv=yall[devmask]
            if len(tr)<MIN_TRAIN or len(dv)<MIN_DEV or dv["condition_id"].nunique()<MIN_CONDITIONS:
                matrix.append({"family":family,"horizon":h,"target":target_type,"status":"INSUFFICIENT_SUPPORT",
                               "train_rows":int(len(tr)),"dev_rows":int(len(dv)),"dev_conditions":int(dv["condition_id"].nunique())})
                continue
            for kind,param in spec_models():
                r=eval_spec(tr,dv,ytr,ydv,features,kind,param,target_type)
                row={k:v for k,v in r.items() if not k.startswith("_")}
                row.update({"family":family,"horizon":h,"target":target_type,"status":"EVALUATED",
                            "train_rows":int(len(tr)),"dev_rows":int(len(dv)),"dev_conditions":int(dv["condition_id"].nunique())})
                matrix.append(row)
                key=(target_type,h,kind,param)
                cache[key]=(tr,dv,ytr,ydv,r["_pred"],r["_base"],row)
    evaluated=[r for r in matrix if r.get("status")=="EVALUATED"]
    ranked=sorted(evaluated,key=lambda r:r["improvement_vs_best_baseline"],reverse=True)
    diagnostics_rows=[]
    for row in ranked[:8]:
        key=(row["target"],row["horizon"],row["kind"],row["param"])
        tr,dv,ytr,ydv,pred,base,_=cache[key]
        d=diagnostics(dv,ydv,pred,base)
        enriched=dict(row); enriched["diagnostics"]=d
        pass_gate=(
          row["improvement_vs_best_baseline"]>=0.01 and
          d["condition_bootstrap_lower_2_5"]>0 and
          d["day_bootstrap_lower_2_5"]>0 and
          d["first_half_improvement"]>0 and d["second_half_improvement"]>0 and
          d["condition_positive_fraction"]>=0.55 and
          d["top_decile_share_positive_condition_gain"]<=0.75 and
          d["condition_count"]>=MIN_CONDITIONS and d["day_count"]>=8 and
          (d["placebo_rank_ic"] is None or abs(d["placebo_rank_ic"])<0.05)
        )
        enriched["dev_screen_pass"]=bool(pass_gate)
        diagnostics_rows.append(enriched)
    passes=[r for r in diagnostics_rows if r["dev_screen_pass"]]
    # Do not over-promote several near-duplicates from the same family/target/horizon.
    shortlist=[]
    seen=set()
    for r in passes:
        k=(r["target"],r["horizon"])
        if k in seen: continue
        seen.add(k); shortlist.append(r)
        if len(shortlist)>=3: break
    pd.DataFrame(matrix).to_csv(out/"experiment_matrix.csv",index=False)
    dead=[r for r in matrix if r.get("status")!="EVALUATED" or r.get("improvement_vs_best_baseline",0)<=0]
    (out/"dead_idea_ledger.json").write_text(json.dumps(dead,indent=2,sort_keys=True)+"\n")
    payload={
      "schema_version":1,"experiment_id":"PRED-006","family":family,
      "split_manifest_sha256":sha256(locate("split_manifest.json","pred006-phase0-audit")),
      "search_breadth":{"model_target_horizon_evaluations":len(evaluated),"failed_or_nonpositive":len(dead),
                        "horizons":list(HORIZONS),"models_per_supported_target_horizon":len(spec_models()),"feature_count":len(features)},
      "top_diagnostics":diagnostics_rows,"family_shortlist":shortlist,
      "family_disposition":"DEV_CANDIDATES" if shortlist else "NO_DEV_CANDIDATE",
      "final_rows_accessed_for_predictive_search":0
    }
    rp=out/"results_summary.json"; rp.write_text(json.dumps(payload,indent=2,sort_keys=True)+"\n")
    print("PRED006_SEARCH_RESULT="+json.dumps({
      "family":family,"evaluations":len(evaluated),"shortlist_count":len(shortlist),
      "disposition":payload["family_disposition"],
      "top":[{"target":r["target"],"horizon":r["horizon"],"kind":r["kind"],"param":r["param"],
              "improvement":r["improvement_vs_best_baseline"],"pass":r["dev_screen_pass"]} for r in diagnostics_rows[:5]],
      "observation_unit":"condition_block_end","final_rows_accessed":0
    },sort_keys=True),flush=True)


if __name__ == "__main__":
    main("interactions")
