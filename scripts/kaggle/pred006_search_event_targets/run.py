# ruff: noqa
from __future__ import annotations

import hashlib, json, math
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, brier_score_loss, log_loss, roc_auc_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

OUT=Path("/kaggle/working/pred006_event_targets")
SEED=606106
BOOT=500
PURGE_FALLBACK=1800
HORIZONS=(30,120,600,1800)
THRESHOLDS=(0.005,0.01,0.02)
WINDOWS=(5,30,120,600,1800)
MIN_TRAIN=1000
MIN_DEV=300
MIN_CONDITIONS=25
EXPECTED_SPLIT_SHA="28d41fbb590f7f9abe59ca24e00841307fe13991c3c695748240fe127847f349"

def q(p:Path)->str: return str(p).replace("'","''")

def locate(name:str,fragment:str)->Path:
    m=[p for p in Path("/kaggle/input").rglob(name) if fragment in str(p)]
    if len(m)!=1: raise RuntimeError(f"expected one {name} under {fragment}, got {m}")
    return m[0]

def sha256(p:Path)->str:
    h=hashlib.sha256()
    with p.open("rb") as f:
        for c in iter(lambda:f.read(1<<20),b""): h.update(c)
    return h.hexdigest()

def load_split()->dict[str,Any]:
    p=locate("split_manifest.json","pred006-phase0-audit")
    if sha256(p)!=EXPECTED_SPLIT_SHA: raise RuntimeError("split hash mismatch")
    d=json.loads(p.read_text())
    if d.get("predictive_outcomes_accessed") is not False: raise RuntimeError("contaminated split")
    if d.get("ordering")!=["block_number","log_index"]: raise RuntimeError("wrong chronology")
    return d

def load_frame(final_start:int)->pd.DataFrame:
    econ=locate("economic_fills_DATA003.parquet","005b-data003-block-gate")
    txb=locate("tx_block_DATA003.parquet","005b-data003-block-gate")
    bts=locate("block_timestamp.parquet","005b-data003-block-gate")
    con=duckdb.connect()
    con.execute("pragma threads=4")
    df=con.execute(f"""
      with x as (
        select
          b.block_timestamp as timestamp,
          t.block_number,
          cast(e.log_index as bigint) as log_index,
          cast(e.condition_id as varchar) as condition_id,
          cast(e.p_yes as double) as p_yes,
          cast(e.size_shares as double) as size_shares,
          cast(e.value_usd as double) as value_usd,
          cast(e.sig_market_id as varchar) as sig_market_id,
          upper(cast(e.mapping_class as varchar)) as mapping_class,
          cast(e.window_id as varchar) as window_id
        from read_parquet('{q(econ)}') e
        join read_parquet('{q(txb)}') t
          on lower(cast(e.tx_hash as varchar))=t.tx_hash
        join read_parquet('{q(bts)}') b using(block_number)
        where b.block_timestamp < {int(final_start)}
      )
      select
        timestamp, block_number, condition_id,
        arg_max(p_yes,log_index) as p_yes,
        sum(size_shares) as size_shares,
        sum(value_usd) as value_usd,
        arg_max(sig_market_id,log_index) as sig_market_id,
        arg_max(mapping_class,log_index) as mapping_class,
        arg_max(window_id,log_index) as window_id,
        count(*) as block_trade_count
      from x
      group by timestamp,block_number,condition_id
      order by block_number,condition_id
    """).df()
    con.close()
    if df.empty: raise RuntimeError("empty frame")
    if int(df.timestamp.max())>=final_start: raise RuntimeError("FINAL leaked")
    if df.duplicated(["condition_id","block_number"]).any(): raise RuntimeError("duplicate condition/block")
    return df.reset_index(drop=True)

def features(df:pd.DataFrame)->pd.DataFrame:
    n=len(df); ts=df.timestamp.to_numpy(np.int64); p=df.p_yes.to_numpy(float)
    df["since_prev"]=np.nan; df["market_age"]=np.nan
    for w in WINDOWS:
        df[f"mom_{w}"]=np.nan; df[f"vol_{w}"]=np.nan; df[f"count_{w}"]=np.nan
    for _,raw in df.groupby(["window_id","condition_id"],sort=False).indices.items():
        idx=np.asarray(raw,dtype=int); t=ts[idx]; x=p[idx]
        dp=np.zeros(len(idx)); 
        if len(idx)>1: dp[1:]=np.diff(x)
        if len(idx)>1: df.loc[idx[1:],"since_prev"]=np.diff(t)
        df.loc[idx,"market_age"]=t-t[0]
        sqpref=np.concatenate([[0.0],np.cumsum(dp*dp)])
        loc=np.arange(len(idx))
        for w in WINDOWS:
            left=np.searchsorted(t,t-w,side="left")
            supported=(t-w)>=t[0]
            df.loc[idx,f"mom_{w}"]=np.where(supported,x-x[left],np.nan)
            df.loc[idx,f"vol_{w}"]=np.where(supported,np.sqrt(np.maximum(0,sqpref[loc+1]-sqpref[left])),np.nan)
            df.loc[idx,f"count_{w}"]=np.where(supported,loc-left+1,np.nan)
    pc=np.clip(p,1e-4,1-1e-4)
    df["logit_p"]=np.log(pc/(1-pc))
    df["boundary_distance"]=np.minimum(p,1-p)
    df["size_log"]=np.log1p(np.maximum(0,df.size_shares.to_numpy(float)))
    df["value_log"]=np.log1p(np.maximum(0,df.value_usd.to_numpy(float)))
    df["block_trade_count_log"]=np.log1p(np.maximum(0,df.block_trade_count.to_numpy(float)))
    keys=["window_id","block_number","timestamp"]
    block=df.groupby(keys,as_index=False).agg(block_p_mean=("p_yes","mean"),block_p_std=("p_yes","std"),block_conditions=("condition_id","nunique"))
    df=df.merge(block,on=keys,how="left",validate="many_to_one")
    df["block_p_rank"]=df.groupby(keys,sort=False)["p_yes"].rank(pct=True,method="average")
    df["block_p_dispersion"]=df.p_yes-df.block_p_mean
    df["block_conditions_log"]=np.log1p(df.block_conditions)
    sec=np.mod(ts,86400)
    df["tod_sin"]=np.sin(2*np.pi*sec/86400); df["tod_cos"]=np.cos(2*np.pi*sec/86400)
    for k in ("EXACT","DERIVED","NEAR"): df[f"map_{k.lower()}"]=(df.mapping_class.astype(str)==k).astype(float)
    return df

FEATURES=[
 "p_yes","logit_p","boundary_distance","since_prev","market_age","size_log","value_log","block_trade_count_log",
 "tod_sin","tod_cos","map_exact","map_derived","map_near",
 "mom_5","mom_30","mom_120","mom_600","mom_1800",
 "vol_30","vol_120","vol_600","vol_1800","count_30","count_120","count_600","count_1800",
 "block_p_rank","block_p_dispersion","block_p_std","block_conditions_log"
]
BASE=["p_yes","boundary_distance","mom_30","mom_120","vol_120","count_120","since_prev"]

def fixed_delta(df:pd.DataFrame,h:int)->np.ndarray:
    n=len(df); out=np.full(n,np.nan); ts=df.timestamp.to_numpy(np.int64); p=df.p_yes.to_numpy(float)
    for _,raw in df.groupby(["window_id","condition_id"],sort=False).indices.items():
        idx=np.asarray(raw,dtype=int); t=ts[idx]; x=p[idx]
        j=np.searchsorted(t,t+h,side="right")-1
        j=np.maximum(j,np.arange(len(idx)))
        d=x[j]-x
        out[idx]=np.where((t+h)<=t[-1],d,np.nan)
    return out

def next_change_direction(df:pd.DataFrame)->tuple[np.ndarray,np.ndarray]:
    n=len(df); y=np.full(n,np.nan); endts=np.full(n,np.nan)
    ts=df.timestamp.to_numpy(np.int64); p=df.p_yes.to_numpy(float)
    for _,raw in df.groupby(["window_id","condition_id"],sort=False).indices.items():
        idx=np.asarray(raw,dtype=int); x=p[idx]; t=ts[idx]; m=len(idx)
        nxt=np.full(m,-1,dtype=int)
        for i in range(m-2,-1,-1):
            if abs(x[i+1]-x[i])>1e-12: nxt[i]=i+1
            else: nxt[i]=nxt[i+1]
        ok=nxt>=0
        loc=np.arange(m)[ok]; jj=nxt[ok]
        y[idx[loc]]=(x[jj]>x[loc]).astype(float)
        endts[idx[loc]]=t[jj]
    return y,endts

def X(df:pd.DataFrame,cols:list[str])->np.ndarray:
    return df[cols].apply(pd.to_numeric,errors="coerce").to_numpy(float)

def model(kind:str,param:float):
    if kind=="logit":
        return make_pipeline(SimpleImputer(strategy="median"),StandardScaler(),LogisticRegression(C=float(param),max_iter=700,random_state=SEED))
    if kind=="hgb":
        return make_pipeline(SimpleImputer(strategy="median"),HistGradientBoostingClassifier(learning_rate=.05,max_iter=150,max_leaf_nodes=int(param),l2_regularization=2.0,random_state=SEED))
    raise ValueError(kind)

def specs(): return [("logit",.1),("logit",1.0),("logit",10.0),("hgb",7),("hgb",15)]

def metrics(y,p):
    p=np.clip(p,1e-6,1-1e-6)
    return {
      "brier":float(brier_score_loss(y,p)),
      "logloss":float(log_loss(y,p,labels=[0,1])),
      "accuracy":float(accuracy_score(y,p>=.5)),
      "auc":float(roc_auc_score(y,p)) if len(np.unique(y))>1 else None
    }

def baselines(tr:pd.DataFrame,dv:pd.DataFrame,yt:np.ndarray,yd:np.ndarray):
    base_rate=np.full(len(dv),float(np.mean(yt)))
    hist=model("logit",1.0); hist.fit(X(tr,BASE),yt); hp=hist.predict_proba(X(dv,BASE))[:,1]
    preds={"base_rate":base_rate,"own_history_logit":hp}
    mets={k:metrics(yd,v) for k,v in preds.items()}
    name=min(mets,key=lambda k:mets[k]["brier"])
    return name,preds[name],mets

def diag(dv:pd.DataFrame,y:np.ndarray,p:np.ndarray,b:np.ndarray):
    gain=(y-b)**2-(y-p)**2
    tmp=pd.DataFrame({"condition":dv.condition_id.astype(str).to_numpy(),"day":dv.timestamp.to_numpy(np.int64)//86400,"gain":gain})
    pc=tmp.groupby("condition")["gain"].mean().to_numpy(float)
    pdays=tmp.groupby("day")["gain"].mean().to_numpy(float)
    rng=np.random.default_rng(SEED)
    def boot(v):
        reps=np.empty(BOOT)
        for i in range(BOOT): reps[i]=float(np.mean(v[rng.integers(0,len(v),len(v))]))
        return float(np.quantile(reps,.025)),float(np.quantile(reps,.975))
    clo,chi=boot(pc); dlo,dhi=boot(pdays)
    med=int(np.median(dv.timestamp.to_numpy(np.int64))); first=dv.timestamp.to_numpy(np.int64)<=med
    def imp(mask):
        bb=float(np.mean((y[mask]-b[mask])**2)); mm=float(np.mean((y[mask]-p[mask])**2))
        return (bb-mm)/bb if bb else 0.0
    pos=np.sort(pc[pc>0])[::-1]
    share=1.0
    if len(pos):
        k=max(1,int(np.ceil(.1*len(pos)))); share=float(pos[:k].sum()/pos.sum())
    yp=rng.permutation(y)
    placebo_auc=float(roc_auc_score(yp,p)) if len(np.unique(yp))>1 else None
    return {
      "condition_count":int(len(pc)),"day_count":int(len(pdays)),
      "condition_positive_fraction":float(np.mean(pc>0)),
      "condition_bootstrap_lower_2_5":clo,"condition_bootstrap_upper_97_5":chi,
      "day_bootstrap_lower_2_5":dlo,"day_bootstrap_upper_97_5":dhi,
      "first_half_improvement":imp(first),"second_half_improvement":imp(~first),
      "top_decile_share_positive_condition_gain":share,"placebo_auc":placebo_auc,
      "bootstrap_repetitions":BOOT
    }

def evaluate_target(df,split,target_name,horizon,threshold,yall,endts=None):
    dev_start=int(split["dev_start_epoch"]); final_start=int(split["final_start_epoch"])
    purge=int(split["selection_rule"].get("target_purge_seconds",PURGE_FALLBACK))
    ts=df.timestamp.to_numpy(np.int64)
    finite=np.isfinite(yall)
    if endts is None:
        trm=(ts<dev_start-purge)&finite
        dvm=(ts>=dev_start)&(ts<final_start-purge)&finite
    else:
        trm=(ts<dev_start-purge)&finite&np.isfinite(endts)&(endts<dev_start)
        dvm=(ts>=dev_start)&finite&np.isfinite(endts)&(endts<final_start-purge)
    tr=df.loc[trm].reset_index(drop=True); dv=df.loc[dvm].reset_index(drop=True)
    yt=yall[trm].astype(int); yd=yall[dvm].astype(int)
    base={"target":target_name,"horizon":horizon,"threshold":threshold,"train_rows":int(len(tr)),"dev_rows":int(len(dv)),"dev_conditions":int(dv.condition_id.nunique())}
    if len(tr)<MIN_TRAIN or len(dv)<MIN_DEV or dv.condition_id.nunique()<MIN_CONDITIONS or min(int(yt.sum()),int(len(yt)-yt.sum()),int(yd.sum()),int(len(yd)-yd.sum()))<40:
        return [{**base,"status":"INSUFFICIENT_SUPPORT"}],[]
    bname,bpred,bmets=baselines(tr,dv,yt,yd)
    bb=bmets[bname]["brier"]; rows=[]; cache=[]
    for kind,param in specs():
        m=model(kind,param); m.fit(X(tr,FEATURES),yt); pp=m.predict_proba(X(dv,FEATURES))[:,1]
        met=metrics(yd,pp); imp=(bb-met["brier"])/bb if bb else 0.0
        row={**base,"status":"EVALUATED","kind":kind,"param":float(param),**met,"best_baseline":bname,"best_baseline_brier":bb,"improvement_vs_best_baseline":float(imp)}
        rows.append(row); cache.append((row,dv,yd,pp,bpred))
    return rows,cache

def main():
    OUT.mkdir(parents=True,exist_ok=True)
    split=load_split(); final_start=int(split["final_start_epoch"])
    df=features(load_frame(final_start))
    if int(df.timestamp.max())>=final_start: raise RuntimeError("FINAL entered predictive frame")
    matrix=[]; cache=[]
    for h in HORIZONS:
        d=fixed_delta(df,h)
        for th in THRESHOLDS:
            y=np.where(np.isfinite(d)&(np.abs(d)>=th),(d>0).astype(float),np.nan)
            rows,c=evaluate_target(df,split,"material_direction",h,th,y)
            matrix.extend(rows); cache.extend(c)
    yn,ets=next_change_direction(df)
    rows,c=evaluate_target(df,split,"next_price_change_direction",None,None,yn,ets)
    matrix.extend(rows); cache.extend(c)
    evaluated=[r for r in matrix if r.get("status")=="EVALUATED"]
    ranked=sorted(cache,key=lambda z:z[0]["improvement_vs_best_baseline"],reverse=True)
    diagnostics=[]
    for row,dv,y,p,b in ranked[:12]:
        d=diag(dv,y,p,b); e=dict(row); e["diagnostics"]=d
        e["dev_screen_pass"]=bool(
          row["improvement_vs_best_baseline"]>=.01 and
          d["condition_bootstrap_lower_2_5"]>0 and d["day_bootstrap_lower_2_5"]>0 and
          d["first_half_improvement"]>0 and d["second_half_improvement"]>0 and
          d["condition_positive_fraction"]>=.55 and d["top_decile_share_positive_condition_gain"]<=.75 and
          d["condition_count"]>=MIN_CONDITIONS and d["day_count"]>=8 and
          (d["placebo_auc"] is None or abs(d["placebo_auc"]-.5)<.08)
        )
        diagnostics.append(e)
    shortlist=[]; seen=set()
    for r in diagnostics:
        if not r["dev_screen_pass"]: continue
        key=(r["target"],r["horizon"],r["threshold"])
        if key in seen: continue
        seen.add(key); shortlist.append(r)
        if len(shortlist)>=3: break
    pd.DataFrame(matrix).to_csv(OUT/"experiment_matrix.csv",index=False)
    dead=[r for r in matrix if r.get("status")!="EVALUATED" or r.get("improvement_vs_best_baseline",0)<=0]
    (OUT/"dead_idea_ledger.json").write_text(json.dumps(dead,indent=2,sort_keys=True)+"\n")
    hypothesis={
      "schema_version":1,"experiment_id":"PRED-006","family":"event_targets",
      "independent_from_005_hypothesis_lists":True,"observation_unit":"condition_block_end",
      "features":FEATURES,
      "targets":{"material_direction":{"horizons_seconds":list(HORIZONS),"absolute_move_thresholds":list(THRESHOLDS)},
                 "next_price_change_direction":{"future_endpoint":"first strictly later condition-block with changed canonical YES price"}},
      "models":[{"kind":k,"param":p} for k,p in specs()],
      "split_manifest_sha256":EXPECTED_SPLIT_SHA,"final_rows_accessed_for_predictive_search":0
    }
    (OUT/"hypothesis_ledger.json").write_text(json.dumps(hypothesis,indent=2,sort_keys=True)+"\n")
    payload={
      "schema_version":1,"experiment_id":"PRED-006","family":"event_targets",
      "split_manifest_sha256":EXPECTED_SPLIT_SHA,
      "search_breadth":{"evaluations":len(evaluated),"target_definitions":13,"feature_count":len(FEATURES),"models_per_supported_target":len(specs())},
      "top_diagnostics":diagnostics,"family_shortlist":shortlist,
      "family_disposition":"DEV_CANDIDATES" if shortlist else "NO_DEV_CANDIDATE",
      "final_rows_accessed_for_predictive_search":0
    }
    (OUT/"results_summary.json").write_text(json.dumps(payload,indent=2,sort_keys=True)+"\n")
    print("PRED006_EVENT_TARGETS_RESULT="+json.dumps({
      "evaluations":len(evaluated),"shortlist_count":len(shortlist),"disposition":payload["family_disposition"],
      "top":[{"target":r["target"],"horizon":r["horizon"],"threshold":r["threshold"],"kind":r["kind"],"param":r["param"],
              "improvement":r["improvement_vs_best_baseline"],"pass":r["dev_screen_pass"]} for r in diagnostics[:6]],
      "final_rows_accessed":0
    },sort_keys=True),flush=True)

if __name__=="__main__": main()
