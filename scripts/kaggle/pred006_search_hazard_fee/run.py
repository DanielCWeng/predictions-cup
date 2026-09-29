# ruff: noqa: E501
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
from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

OUT=Path("/kaggle/working/pred006_hazard_fee"); OUT.mkdir(parents=True,exist_ok=True)
HORIZONS=(5,30,120,600,1800)
WINDOWS=(30,120,600,1800)
SEED=606607
BOOT=500

def q(p:Path)->str:return str(p).replace("'","''")
def sha256(p:Path)->str:
    h=hashlib.sha256()
    with p.open("rb") as f:
        for c in iter(lambda:f.read(1<<20),b""):h.update(c)
    return h.hexdigest()
def locate(name:str,frag:str)->Path:
    m=[p for p in Path("/kaggle/input").rglob(name) if frag in str(p)]
    if len(m)!=1:raise RuntimeError(f"expected one {name} under {frag}, got {m}")
    return m[0]
def data_files(kind:str)->list[Path]:
    out=[]
    needle=f"/{kind}/"
    for p in Path("/kaggle/input").rglob("*.parquet"):
        s=str(p)
        if needle in s and "/rebates/" not in s and "/unattributed_fee_legs/" not in s:out.append(p)
    if not out:raise RuntimeError(f"no DATA-003 {kind} files")
    return sorted(out)

def load_frame(final_start:int)->pd.DataFrame:
    econ=locate("economic_fills_DATA003.parquet","005b-data003-block-gate")
    txb=locate("tx_block_DATA003.parquet","005b-data003-block-gate")
    bts=locate("block_timestamp.parquet","005b-data003-block-gate")
    fees=data_files("fees")
    fs=",".join("'" + q(p) + "'" for p in fees)
    con=duckdb.connect();con.execute("pragma threads=4")
    # Only contemporaneous realised fee evidence is used. Current Gamma fee
    # snapshot fields are intentionally excluded because they are not historical.
    con.execute(f"""
      create temp view active_fee as
      select cast(condition_id as varchar) condition_id,
             lower(cast(tx_hash as varchar)) tx_hash,
             max(lower(cast(participant_address as varchar))) filter(where cast(order_is_match_taker_order as boolean)) actor,
             max(upper(cast(outcome_side as varchar))) filter(where cast(order_is_match_taker_order as boolean)) active_outcome,
             max(upper(cast(participant_side as varchar))) filter(where cast(order_is_match_taker_order as boolean)) active_side,
             max(cast(fee_evidence as varchar)) filter(where cast(order_is_match_taker_order as boolean)) active_fee_evidence,
             max(cast(fee_net_usd_equiv as double)) filter(where cast(order_is_match_taker_order as boolean)) active_fee_net,
             max(cast(fee_charged_usd_equiv as double)) filter(where cast(order_is_match_taker_order as boolean)) active_fee_charged,
             max(cast(fee_refunded_usd_equiv as double)) filter(where cast(order_is_match_taker_order as boolean)) active_fee_refunded,
             max(cast(n_charge_legs as double)) filter(where cast(order_is_match_taker_order as boolean)) active_charge_legs
      from read_parquet([{fs}],union_by_name=true)
      where cast(timestamp as bigint)<{int(final_start)}
      group by 1,2
    """)
    df=con.execute(f"""
      select cast(e.timestamp as bigint) timestamp, lower(cast(e.tx_hash as varchar)) tx_hash,
             cast(e.log_index as bigint) log_index,cast(e.condition_id as varchar) condition_id,
             cast(e.p_yes as double) p_yes,cast(e.size_shares as double) size_shares,
             cast(e.value_usd as double) value_usd,cast(e.sig_market_id as varchar) sig_market_id,
             upper(cast(e.mapping_class as varchar)) mapping_class,cast(e.window_id as varchar) window_id,
             t.block_number,f.actor,f.active_outcome,f.active_side,f.active_fee_evidence,
             f.active_fee_net,f.active_fee_charged,f.active_fee_refunded,f.active_charge_legs
      from read_parquet('{q(econ)}') e
      join read_parquet('{q(txb)}') t on lower(cast(e.tx_hash as varchar))=t.tx_hash
      join read_parquet('{q(bts)}') b using(block_number)
      left join active_fee f on cast(e.condition_id as varchar)=f.condition_id and lower(cast(e.tx_hash as varchar))=f.tx_hash
      where cast(e.timestamp as bigint)<{int(final_start)}
      order by t.block_number,cast(e.log_index as bigint)
    """).df();con.close()
    if df.empty or int(df.timestamp.max())>=final_start:raise RuntimeError("FINAL contamination")
    if df.duplicated(["block_number","log_index"]).any():raise RuntimeError("bad canonical ordering")
    return df.reset_index(drop=True)

def features(df:pd.DataFrame)->pd.DataFrame:
    n=len(df);ts=df.timestamp.to_numpy(np.int64);p=df.p_yes.to_numpy(float)
    df["boundary_distance"]=np.minimum(p,1-p)
    df["logit_p"]=np.log(np.clip(p,1e-4,1-1e-4)/(1-np.clip(p,1e-4,1-1e-4)))
    df["size_log"]=np.log1p(np.maximum(0,df.size_shares.to_numpy(float)))
    df["value_log"]=np.log1p(np.maximum(0,df.value_usd.to_numpy(float)))
    df["since_prev"]=np.nan
    for w in WINDOWS:
        df[f"count_{w}"]=np.nan;df[f"vol_{w}"]=np.nan;df[f"mom_{w}"]=np.nan
    for _,raw in df.groupby(["window_id","condition_id"],sort=False).indices.items():
        idx=np.asarray(raw,int);t=ts[idx];x=p[idx]
        dp=np.zeros(len(idx));dp[1:]=np.diff(x)
        if len(idx)>1:df.loc[idx[1:],"since_prev"]=np.diff(t)
        pref=np.concatenate([[0.0],np.cumsum(dp*dp)]);loc=np.arange(len(idx))
        for w in WINDOWS:
            left=np.searchsorted(t,t-w,side="left");supported=(t-w)>=t[0]
            df.loc[idx,f"count_{w}"]=np.where(supported,loc-left+1,np.nan)
            df.loc[idx,f"vol_{w}"]=np.where(supported,np.sqrt(np.maximum(0,pref[loc+1]-pref[left])),np.nan)
            df.loc[idx,f"mom_{w}"]=np.where(supported,x-x[left],np.nan)
    ev=df.active_fee_evidence.fillna("").astype(str).str.lower()
    df["fee_charged"]=(ev=="fee_charged").astype(float)
    df["fee_missing"]=(ev=="custody_not_ingested").astype(float)
    df["fee_no_leg"]=(ev=="no_fee_leg_observed").astype(float)
    df["fee_net_log"]=np.sign(df.active_fee_net.fillna(0))*np.log1p(np.abs(df.active_fee_net.fillna(0)))
    df["fee_charge_log"]=np.log1p(np.maximum(0,df.active_fee_charged.fillna(0)))
    df["fee_refund_log"]=np.log1p(np.maximum(0,df.active_fee_refunded.fillna(0)))
    df["charge_legs_log"]=np.log1p(np.maximum(0,df.active_charge_legs.fillna(0)))
    return df

def hazard_target(df:pd.DataFrame,h:int)->np.ndarray:
    n=len(df);y=np.full(n,np.nan);ts=df.timestamp.to_numpy(np.int64);p=df.p_yes.to_numpy(float)
    for _,raw in df.groupby(["window_id","condition_id"],sort=False).indices.items():
        idx=np.asarray(raw,int);t=ts[idx];x=p[idx]
        # next different-price row for each constant-price run
        start=0
        while start<len(idx):
            end=start
            while end+1<len(idx) and abs(x[end+1]-x[start])<=1e-12:end+=1
            nxt=end+1
            for k in range(start,end+1):
                if t[k]+h>t[-1]:continue
                if nxt<len(idx):y[idx[k]]=1.0 if int(t[nxt]-t[k])<=h else 0.0
                else:y[idx[k]]=0.0
            start=end+1
    return y

FEATURES=[
 "p_yes","logit_p","boundary_distance","size_log","value_log","since_prev",
 "count_30","count_120","count_600","count_1800",
 "vol_30","vol_120","vol_600","vol_1800",
 "mom_30","mom_120","mom_600","mom_1800",
 "fee_charged","fee_missing","fee_no_leg","fee_net_log","fee_charge_log","fee_refund_log","charge_legs_log"
]
BASE=["p_yes","boundary_distance","since_prev","count_120","vol_120","mom_120"]
def X(df,cols):return df[cols].apply(pd.to_numeric,errors="coerce").to_numpy(float)
def model(kind,param):
    if kind=="logit":return make_pipeline(SimpleImputer(strategy="median"),StandardScaler(),LogisticRegression(C=float(param),max_iter=500,random_state=SEED))
    return make_pipeline(SimpleImputer(strategy="median"),HistGradientBoostingClassifier(learning_rate=.05,max_iter=120,max_leaf_nodes=int(param),l2_regularization=2,random_state=SEED))
def specs():return [("logit",.1),("logit",1.0),("logit",10.0),("hgb",7),("hgb",15),("hgb",31)]
def metrics(y,p):
    p=np.clip(p,1e-6,1-1e-6)
    return {"brier":float(brier_score_loss(y,p)),"logloss":float(log_loss(y,p)),"auc":float(roc_auc_score(y,p)) if len(np.unique(y))>1 else None}
def diag(dev,y,p,b):
    diff=(y-b)**2-(y-p)**2
    tmp=pd.DataFrame({"condition":dev.condition_id.astype(str).to_numpy(),"diff":diff})
    per=tmp.groupby("condition").diff.mean().to_numpy(float)
    rng=np.random.default_rng(SEED);reps=np.empty(BOOT)
    for i in range(BOOT):reps[i]=float(np.mean(per[rng.integers(0,len(per),len(per))]))
    med=int(np.median(dev.timestamp.to_numpy(np.int64)));first=dev.timestamp.to_numpy(np.int64)<=med
    def imp(m):
        bb=float(np.mean((y[m]-b[m])**2));mm=float(np.mean((y[m]-p[m])**2));return (bb-mm)/bb if bb else 0
    return {"conditions":int(len(per)),"condition_positive_fraction":float(np.mean(per>0)),
            "bootstrap_lower_2_5":float(np.quantile(reps,.025)),"bootstrap_upper_97_5":float(np.quantile(reps,.975)),
            "first_half_improvement":float(imp(first)),"second_half_improvement":float(imp(~first))}
def main():
    split=json.loads(locate("split_manifest.json","pred006-phase0-audit").read_text())
    if split.get("predictive_outcomes_accessed") is not False:raise RuntimeError("contaminated split")
    dev_start=int(split["dev_start_epoch"]);final_start=int(split["final_start_epoch"])
    df=features(load_frame(final_start));matrix=[];cache={}
    for h in HORIZONS:
        ya=hazard_target(df,h);ts=df.timestamp.to_numpy(np.int64)
        trm=(ts<dev_start-h)&np.isfinite(ya);dvm=(ts>=dev_start)&(ts<final_start-h)&np.isfinite(ya)
        tr=df.loc[trm].reset_index(drop=True);dv=df.loc[dvm].reset_index(drop=True);yt=ya[trm];yd=ya[dvm]
        if len(tr)<1000 or len(dv)<300 or dv.condition_id.nunique()<25 or len(np.unique(yt))<2:
            matrix.append({"horizon":h,"status":"INSUFFICIENT_SUPPORT","train_rows":len(tr),"dev_rows":len(dv)});continue
        base_const=np.full(len(dv),float(np.mean(yt)))
        bm=model("logit",1.0);bm.fit(X(tr,BASE),yt);base_hist=bm.predict_proba(X(dv,BASE))[:,1]
        bset={"base_rate":base_const,"own_history_logit":base_hist};bmet={k:metrics(yd,v) for k,v in bset.items()}
        bname=min(bmet,key=lambda k:bmet[k]["brier"]);best=bset[bname];bb=bmet[bname]["brier"]
        for kind,param in specs():
            m=model(kind,param);m.fit(X(tr,FEATURES),yt);pr=m.predict_proba(X(dv,FEATURES))[:,1];mm=metrics(yd,pr)
            row={"horizon":h,"status":"EVALUATED","kind":kind,"param":param,"train_rows":len(tr),"dev_rows":len(dv),
                 "dev_conditions":int(dv.condition_id.nunique()),**mm,"best_baseline":bname,"best_baseline_brier":bb,
                 "improvement_vs_best_baseline":float((bb-mm["brier"])/bb) if bb else 0.0}
            matrix.append(row);cache[(h,kind,param)]=(dv,yd,pr,best,row)
    ev=[r for r in matrix if r.get("status")=="EVALUATED"];rank=sorted(ev,key=lambda r:r["improvement_vs_best_baseline"],reverse=True)
    tops=[]
    for r in rank[:8]:
        dv,yd,p,b,_=cache[(r["horizon"],r["kind"],r["param"])];d=diag(dv,yd,p,b);x=dict(r);x["diagnostics"]=d
        x["dev_screen_pass"]=bool(r["improvement_vs_best_baseline"]>=.01 and d["bootstrap_lower_2_5"]>0 and d["first_half_improvement"]>0 and d["second_half_improvement"]>0 and d["condition_positive_fraction"]>=.55)
        tops.append(x)
    short=[];seen=set()
    for r in tops:
        if r["dev_screen_pass"] and r["horizon"] not in seen:seen.add(r["horizon"]);short.append(r)
        if len(short)>=3:break
    pd.DataFrame(matrix).to_csv(OUT/"experiment_matrix.csv",index=False)
    dead=[r for r in matrix if r.get("status")!="EVALUATED" or r.get("improvement_vs_best_baseline",0)<=0]
    (OUT/"dead_idea_ledger.json").write_text(json.dumps(dead,indent=2,sort_keys=True)+"\n")
    result={"schema_version":1,"experiment_id":"PRED-006","family":"hazard_fee","target":"time_to_next_price_change_hazard",
            "features":FEATURES,"search_breadth":{"evaluations":len(ev),"horizons":list(HORIZONS),"models":len(specs()),"features":len(FEATURES)},
            "top_diagnostics":tops,"family_shortlist":short,"family_disposition":"DEV_CANDIDATES" if short else "NO_DEV_CANDIDATE",
            "final_rows_accessed_for_predictive_search":0,
            "fee_guard":"Only realised contemporaneous fee evidence used; current Gamma snapshot configuration and rebates excluded."}
    (OUT/"results_summary.json").write_text(json.dumps(result,indent=2,sort_keys=True)+"\n")
    print("PRED006_HAZARD_RESULT="+json.dumps({"evaluations":len(ev),"shortlist_count":len(short),"disposition":result["family_disposition"],
          "top":[{"horizon":r["horizon"],"kind":r["kind"],"improvement":r["improvement_vs_best_baseline"],"pass":r["dev_screen_pass"]} for r in tops[:5]],
          "final_rows_accessed":0},sort_keys=True),flush=True)
if __name__=="__main__":main()
