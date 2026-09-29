# ruff: noqa
from __future__ import annotations
import json, hashlib
from pathlib import Path
import duckdb, numpy as np, pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

OUT=Path("/kaggle/working/pred006_hazard_ablation"); OUT.mkdir(parents=True,exist_ok=True)
SEED=606707; BOOT=500; WINDOWS=(30,120,600,1800)
BASE=["p_yes","boundary_distance","since_prev","count_120","vol_120","mom_120"]
FEE={"fee_charged","fee_missing","fee_no_leg","fee_net_log","fee_charge_log","fee_refund_log","charge_legs_log"}

def q(p): return str(p).replace("'","''")
def locate(name,frag):
    m=[p for p in Path("/kaggle/input").rglob(name) if frag in str(p)]
    if len(m)!=1: raise RuntimeError(f"expected one {name} under {frag}, got {m}")
    return m[0]
def data_files(kind):
    needle=f"/{kind}/"; out=[]
    for p in Path("/kaggle/input").rglob("*.parquet"):
        s=str(p)
        if needle in s and "/rebates/" not in s and "/unattributed_fee_legs/" not in s: out.append(p)
    if not out: raise RuntimeError(f"no {kind} files")
    return sorted(out)

def load_frame(final_start):
    econ=locate("economic_fills_DATA003.parquet","005b-data003-block-gate")
    txb=locate("tx_block_DATA003.parquet","005b-data003-block-gate")
    bts=locate("block_timestamp.parquet","005b-data003-block-gate")
    fs=",".join("'" + q(p) + "'" for p in data_files("fees"))
    con=duckdb.connect(); con.execute("pragma threads=4")
    con.execute(f"""
      create temp view active_fee as
      select cast(condition_id as varchar) AS condition_id, lower(cast(tx_hash as varchar)) AS tx_hash,
             max(cast(fee_evidence as varchar)) filter(where cast(order_is_match_taker_order as boolean)) AS active_fee_evidence,
             max(cast(fee_net_usd_equiv as double)) filter(where cast(order_is_match_taker_order as boolean)) AS active_fee_net,
             max(cast(fee_charged_usd_equiv as double)) filter(where cast(order_is_match_taker_order as boolean)) AS active_fee_charged,
             max(cast(fee_refunded_usd_equiv as double)) filter(where cast(order_is_match_taker_order as boolean)) AS active_fee_refunded,
             max(cast(n_charge_legs as double)) filter(where cast(order_is_match_taker_order as boolean)) AS active_charge_legs
      from read_parquet([{fs}],union_by_name=true)
      where cast(timestamp as bigint)<{int(final_start)}
      group by 1,2
    """)
    df=con.execute(f"""
      with x as (
        select b.block_timestamp AS timestamp,t.block_number,cast(e.log_index as bigint) AS log_index,
               cast(e.condition_id as varchar) AS condition_id,cast(e.p_yes as double) AS p_yes,
               cast(e.size_shares as double) AS size_shares,cast(e.value_usd as double) AS value_usd,
               cast(e.sig_market_id as varchar) AS sig_market_id,upper(cast(e.mapping_class as varchar)) AS mapping_class,
               cast(e.window_id as varchar) AS window_id,
               lower(coalesce(cast(f.active_fee_evidence as varchar),'')) AS fee_evidence,
               coalesce(cast(f.active_fee_net as double),0) AS fee_net,
               coalesce(cast(f.active_fee_charged as double),0) AS fee_charge,
               coalesce(cast(f.active_fee_refunded as double),0) AS fee_refund,
               coalesce(cast(f.active_charge_legs as double),0) AS charge_legs
        from read_parquet('{q(econ)}') e
        join read_parquet('{q(txb)}') t on lower(cast(e.tx_hash as varchar))=t.tx_hash
        join read_parquet('{q(bts)}') b using(block_number)
        left join active_fee f on cast(e.condition_id as varchar)=f.condition_id and lower(cast(e.tx_hash as varchar))=f.tx_hash
        where b.block_timestamp<{int(final_start)}
      )
      select timestamp,block_number,condition_id,arg_max(p_yes,log_index) AS p_yes,sum(size_shares) AS size_shares,
             sum(value_usd) AS value_usd,arg_max(sig_market_id,log_index) AS sig_market_id,arg_max(mapping_class,log_index) AS mapping_class,
             arg_max(window_id,log_index) AS window_id,count(*) AS block_trade_count,
             max(case when fee_evidence='fee_charged' then 1 else 0 end) AS fee_charged,
             max(case when fee_evidence='custody_not_ingested' then 1 else 0 end) AS fee_missing,
             max(case when fee_evidence='no_fee_leg_observed' then 1 else 0 end) AS fee_no_leg,
             sum(fee_net) AS active_fee_net,sum(fee_charge) AS active_fee_charged,sum(fee_refund) AS active_fee_refunded,sum(charge_legs) AS active_charge_legs
      from x group by timestamp,block_number,condition_id order by block_number,condition_id
    """).df(); con.close()
    if df.empty or int(df.timestamp.max())>=final_start: raise RuntimeError("FINAL contamination")
    if df.duplicated(["condition_id","block_number"]).any(): raise RuntimeError("duplicate condition/block")
    return df.reset_index(drop=True)

def add_features(df):
    ts=df.timestamp.to_numpy(np.int64); p=df.p_yes.to_numpy(float)
    cp=np.clip(p,1e-4,1-1e-4); df["logit_p"]=np.log(cp/(1-cp)); df["boundary_distance"]=np.minimum(p,1-p)
    df["size_log"]=np.log1p(np.maximum(0,df.size_shares)); df["value_log"]=np.log1p(np.maximum(0,df.value_usd)); df["since_prev"]=np.nan
    for w in WINDOWS: df[f"count_{w}"]=np.nan; df[f"vol_{w}"]=np.nan; df[f"mom_{w}"]=np.nan
    for _,raw in df.groupby(["window_id","condition_id"],sort=False).indices.items():
        idx=np.asarray(raw,int); t=ts[idx]; x=p[idx]; dp=np.zeros(len(idx)); dp[1:]=np.diff(x)
        if len(idx)>1: df.loc[idx[1:],"since_prev"]=np.diff(t)
        pref=np.concatenate([[0.0],np.cumsum(dp*dp)]); loc=np.arange(len(idx))
        for w in WINDOWS:
            left=np.searchsorted(t,t-w,side="left"); supported=(t-w)>=t[0]
            df.loc[idx,f"count_{w}"]=np.where(supported,loc-left+1,np.nan)
            df.loc[idx,f"vol_{w}"]=np.where(supported,np.sqrt(np.maximum(0,pref[loc+1]-pref[left])),np.nan)
            df.loc[idx,f"mom_{w}"]=np.where(supported,x-x[left],np.nan)
    for c in ("fee_charged","fee_missing","fee_no_leg"): df[c]=pd.to_numeric(df[c],errors="coerce").fillna(0).astype(float)
    df["fee_net_log"]=np.sign(df.active_fee_net.fillna(0))*np.log1p(np.abs(df.active_fee_net.fillna(0)))
    df["fee_charge_log"]=np.log1p(np.maximum(0,df.active_fee_charged.fillna(0)))
    df["fee_refund_log"]=np.log1p(np.maximum(0,df.active_fee_refunded.fillna(0)))
    df["charge_legs_log"]=np.log1p(np.maximum(0,df.active_charge_legs.fillna(0)))
    return df

def target(df,h):
    n=len(df); y=np.full(n,np.nan); ts=df.timestamp.to_numpy(np.int64); p=df.p_yes.to_numpy(float)
    for _,raw in df.groupby(["window_id","condition_id"],sort=False).indices.items():
        idx=np.asarray(raw,int); t=ts[idx]; x=p[idx]; start=0
        while start<len(idx):
            end=start
            while end+1<len(idx) and abs(x[end+1]-x[start])<=1e-12: end+=1
            nxt=end+1
            for k in range(start,end+1):
                if t[k]+h>t[-1]: continue
                y[idx[k]]=1.0 if (nxt<len(idx) and int(t[nxt]-t[k])<=h) else 0.0
            start=end+1
    return y

def X(df,cols): return df[cols].apply(pd.to_numeric,errors="coerce").to_numpy(float)
def clf(kind,param):
    if kind=="logit": return make_pipeline(SimpleImputer(strategy="median"),StandardScaler(),LogisticRegression(C=float(param),max_iter=700,random_state=SEED))
    return make_pipeline(SimpleImputer(strategy="median"),HistGradientBoostingClassifier(learning_rate=.05,max_iter=120,max_leaf_nodes=int(param),l2_regularization=2,random_state=SEED))
def metric(y,p):
    p=np.clip(p,1e-6,1-1e-6); return {"brier":float(brier_score_loss(y,p)),"logloss":float(log_loss(y,p,labels=[0,1])),"auc":float(roc_auc_score(y,p)) if len(np.unique(y))>1 else None}
def diagnostics(dv,y,p,b):
    gain=(y-b)**2-(y-p)**2; tmp=pd.DataFrame({"c":dv.condition_id.astype(str),"d":dv.timestamp.to_numpy(np.int64)//86400,"g":gain})
    pc=tmp.groupby("c")["g"].mean().to_numpy(float); pdays=tmp.groupby("d")["g"].mean().to_numpy(float)
    rng=np.random.default_rng(SEED)
    def boot(v):
        reps=np.empty(BOOT)
        for i in range(BOOT): reps[i]=float(np.mean(v[rng.integers(0,len(v),len(v))]))
        return float(np.quantile(reps,.025)),float(np.quantile(reps,.975))
    clo,chi=boot(pc); dlo,dhi=boot(pdays); med=int(np.median(dv.timestamp)); first=dv.timestamp.to_numpy(np.int64)<=med
    def imp(m):
        bb=float(np.mean((y[m]-b[m])**2)); mm=float(np.mean((y[m]-p[m])**2)); return (bb-mm)/bb if bb else 0
    return {"condition_bootstrap_lower_2_5":clo,"condition_bootstrap_upper_97_5":chi,"day_bootstrap_lower_2_5":dlo,"day_bootstrap_upper_97_5":dhi,
            "condition_positive_fraction":float(np.mean(pc>0)),"first_half_improvement":imp(first),"second_half_improvement":imp(~first)}

def main():
    split=json.loads(locate("split_manifest.json","pred006-phase0-audit").read_text())
    src=json.loads(locate("results_summary.json","pred006-search-hazard-fee").read_text())
    if src.get("observation_unit")!="condition_block_end": raise RuntimeError("hazard source not block-safe")
    if src.get("final_rows_accessed_for_predictive_search")!=0: raise RuntimeError("source touched FINAL")
    top=src.get("family_shortlist",[])
    if not top: raise RuntimeError("no V4 candidates to ablate")
    final_start=int(split["final_start_epoch"]); dev_start=int(split["dev_start_epoch"]); purge=int(split["selection_rule"]["target_purge_seconds"])
    df=add_features(load_frame(final_start)); full=list(src["features"]); no_fee=[x for x in full if x not in FEE]
    variants={"full":full,"no_fee":no_fee,"base_hgb":BASE,"fee_plus_state":[x for x in full if x in FEE or x in {"p_yes","logit_p","boundary_distance","size_log","value_log","since_prev"}]}
    out=[]
    for cand in top:
        h=int(cand["horizon"]); kind=str(cand["kind"]); param=cand["param"]; y=target(df,h); ts=df.timestamp.to_numpy(np.int64)
        trm=(ts<dev_start-purge)&np.isfinite(y); dvm=(ts>=dev_start)&(ts<final_start-purge)&np.isfinite(y)
        tr=df.loc[trm].reset_index(drop=True); dv=df.loc[dvm].reset_index(drop=True); yt=y[trm].astype(int); yd=y[dvm].astype(int)
        br=np.full(len(dv),float(np.mean(yt))); hm=clf("logit",1.0); hm.fit(X(tr,BASE),yt); hp=hm.predict_proba(X(dv,BASE))[:,1]
        bases={"base_rate":br,"own_history_logit":hp}; bm={k:metric(yd,v) for k,v in bases.items()}; bname=min(bm,key=lambda k:bm[k]["brier"]); bp=bases[bname]; bb=bm[bname]["brier"]
        for vn,cols in variants.items():
            m=clf(kind,param); m.fit(X(tr,cols),yt); p=m.predict_proba(X(dv,cols))[:,1]; mm=metric(yd,p); d=diagnostics(dv,yd,p,bp)
            out.append({"horizon":h,"model_kind":kind,"model_param":param,"variant":vn,"features":cols,"feature_count":len(cols),
                        **mm,"best_baseline":bname,"best_baseline_brier":bb,"improvement_vs_best_baseline":float((bb-mm["brier"])/bb),
                        "diagnostics":d})
        # permutation placebo on the exact full candidate
        rng=np.random.default_rng(SEED+h); yp=rng.permutation(yt); m=clf(kind,param); m.fit(X(tr,full),yp); p=m.predict_proba(X(dv,full))[:,1]; mm=metric(yd,p)
        out.append({"horizon":h,"model_kind":kind,"model_param":param,"variant":"permuted_train_labels","features":full,"feature_count":len(full),
                    **mm,"best_baseline":bname,"best_baseline_brier":bb,"improvement_vs_best_baseline":float((bb-mm["brier"])/bb)})
    rec=[]
    for cand in top:
        h=int(cand["horizon"]); rows=[x for x in out if x["horizon"]==h and x["variant"]!="permuted_train_labels"]
        fullrow=next(x for x in rows if x["variant"]=="full")
        eligible=[]
        for x in rows:
            d=x["diagnostics"]
            ok=x["improvement_vs_best_baseline"]>=.01 and d["condition_bootstrap_lower_2_5"]>0 and d["day_bootstrap_lower_2_5"]>0 and d["first_half_improvement"]>0 and d["second_half_improvement"]>0 and d["condition_positive_fraction"]>=.55
            if ok and x["improvement_vs_best_baseline"]>=fullrow["improvement_vs_best_baseline"]-.01: eligible.append(x)
        chosen=min(eligible,key=lambda x:x["feature_count"]) if eligible else fullrow
        rec.append({"horizon":h,"model_kind":cand["kind"],"model_param":cand["param"],"recommended_variant":chosen["variant"],"recommended_features":chosen["features"],
                    "recommended_improvement":chosen["improvement_vs_best_baseline"],"full_improvement":fullrow["improvement_vs_best_baseline"],
                    "ablation_validated":bool(eligible)})
    payload={"schema_version":1,"experiment_id":"PRED-006","classification":"DEV_ABLATION_ONLY","source_family":"hazard_fee",
             "final_rows_accessed":0,"results":out,"recommendations":rec}
    (OUT/"hazard_ablation.json").write_text(json.dumps(payload,indent=2,sort_keys=True)+"\n")
    print("PRED006_HAZARD_ABLATION_RESULT="+json.dumps({"recommendations":[{"horizon":x["horizon"],"variant":x["recommended_variant"],"improvement":x["recommended_improvement"],"validated":x["ablation_validated"]} for x in rec],"final_rows_accessed":0},sort_keys=True),flush=True)
if __name__=="__main__": main()
