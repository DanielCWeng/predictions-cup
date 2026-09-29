# ruff: noqa
from __future__ import annotations

import hashlib, json, math
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

OUT=Path("/kaggle/working/pred006_final"); OUT.mkdir(parents=True,exist_ok=True)
SEED=606999
BOOT=1000
EXPECTED_SPLIT_SHA="28d41fbb590f7f9abe59ca24e00841307fe13991c3c695748240fe127847f349"
WINDOWS=(30,120,600,1800)
BASE=["p_yes","boundary_distance","since_prev","count_120","vol_120","mom_120"]

def q(p:Path)->str:return str(p).replace("'","''")
def sha256(p:Path)->str:
    h=hashlib.sha256()
    with p.open("rb") as f:
        for c in iter(lambda:f.read(1<<20),b""):h.update(c)
    return h.hexdigest()
def locate(name:str,frag:str)->Path:
    m=[p for p in Path("/kaggle/input").rglob(name) if frag in str(p)]
    if len(m)!=1: raise RuntimeError(f"expected one {name} under {frag}, got {m}")
    return m[0]
def data_files(kind:str)->list[Path]:
    needle=f"/{kind}/"; out=[]
    for p in Path("/kaggle/input").rglob("*.parquet"):
        s=str(p)
        if needle in s and "/rebates/" not in s and "/unattributed_fee_legs/" not in s: out.append(p)
    if not out: raise RuntimeError(f"no DATA-003 {kind} files")
    return sorted(out)

def load_full_frame()->pd.DataFrame:
    econ=locate("economic_fills_DATA003.parquet","005b-data003-block-gate")
    txb=locate("tx_block_DATA003.parquet","005b-data003-block-gate")
    bts=locate("block_timestamp.parquet","005b-data003-block-gate")
    fees=data_files("fees"); fs=",".join("'" + q(p) + "'" for p in fees)
    con=duckdb.connect(); con.execute("pragma threads=4")
    con.execute(f"""
      create temp view active_fee as
      select cast(condition_id as varchar) AS condition_id,
             lower(cast(tx_hash as varchar)) AS tx_hash,
             max(cast(fee_evidence as varchar)) filter(where cast(order_is_match_taker_order as boolean)) AS active_fee_evidence,
             max(cast(fee_net_usd_equiv as double)) filter(where cast(order_is_match_taker_order as boolean)) AS active_fee_net,
             max(cast(fee_charged_usd_equiv as double)) filter(where cast(order_is_match_taker_order as boolean)) AS active_fee_charged,
             max(cast(fee_refunded_usd_equiv as double)) filter(where cast(order_is_match_taker_order as boolean)) AS active_fee_refunded,
             max(cast(n_charge_legs as double)) filter(where cast(order_is_match_taker_order as boolean)) AS active_charge_legs
      from read_parquet([{fs}],union_by_name=true)
      group by 1,2
    """)
    df=con.execute(f"""
      with enriched as (
        select
          b.block_timestamp AS timestamp,
          t.block_number,
          cast(e.log_index as bigint) AS log_index,
          cast(e.condition_id as varchar) AS condition_id,
          cast(e.p_yes as double) AS p_yes,
          cast(e.size_shares as double) AS size_shares,
          cast(e.value_usd as double) AS value_usd,
          cast(e.sig_market_id as varchar) AS sig_market_id,
          upper(cast(e.mapping_class as varchar)) AS mapping_class,
          cast(e.window_id as varchar) AS window_id,
          lower(coalesce(cast(f.active_fee_evidence as varchar),'')) AS fee_evidence,
          coalesce(cast(f.active_fee_net as double),0.0) AS fee_net,
          coalesce(cast(f.active_fee_charged as double),0.0) AS fee_charged_amt,
          coalesce(cast(f.active_fee_refunded as double),0.0) AS fee_refunded_amt,
          coalesce(cast(f.active_charge_legs as double),0.0) AS charge_legs
        from read_parquet('{q(econ)}') e
        join read_parquet('{q(txb)}') t on lower(cast(e.tx_hash as varchar))=t.tx_hash
        join read_parquet('{q(bts)}') b using(block_number)
        left join active_fee f
          on cast(e.condition_id as varchar)=f.condition_id
         and lower(cast(e.tx_hash as varchar))=f.tx_hash
      )
      select
        timestamp, block_number, condition_id,
        arg_max(p_yes,log_index) AS p_yes,
        sum(size_shares) AS size_shares,
        sum(value_usd) AS value_usd,
        arg_max(sig_market_id,log_index) AS sig_market_id,
        arg_max(mapping_class,log_index) AS mapping_class,
        arg_max(window_id,log_index) AS window_id,
        count(*) AS block_trade_count,
        max(case when fee_evidence='fee_charged' then 1 else 0 end) AS fee_charged,
        max(case when fee_evidence='custody_not_ingested' then 1 else 0 end) AS fee_missing,
        max(case when fee_evidence='no_fee_leg_observed' then 1 else 0 end) AS fee_no_leg,
        sum(fee_net) AS active_fee_net,
        sum(fee_charged_amt) AS active_fee_charged,
        sum(fee_refunded_amt) AS active_fee_refunded,
        sum(charge_legs) AS active_charge_legs
      from enriched
      group by timestamp,block_number,condition_id
      order by block_number,condition_id
    """).df()
    con.close()
    if df.empty: raise RuntimeError("empty DATA-003 final frame")
    if df.duplicated(["condition_id","block_number"]).any(): raise RuntimeError("duplicate condition/block observations")
    return df.reset_index(drop=True)

def add_features(df:pd.DataFrame)->pd.DataFrame:
    ts=df.timestamp.to_numpy(np.int64); p=df.p_yes.to_numpy(float)
    df["boundary_distance"]=np.minimum(p,1-p)
    cp=np.clip(p,1e-4,1-1e-4); df["logit_p"]=np.log(cp/(1-cp))
    df["size_log"]=np.log1p(np.maximum(0,df.size_shares.to_numpy(float)))
    df["value_log"]=np.log1p(np.maximum(0,df.value_usd.to_numpy(float)))
    df["since_prev"]=np.nan
    for w in WINDOWS:
        df[f"count_{w}"]=np.nan; df[f"vol_{w}"]=np.nan; df[f"mom_{w}"]=np.nan
    for _,raw in df.groupby(["window_id","condition_id"],sort=False).indices.items():
        idx=np.asarray(raw,int); t=ts[idx]; x=p[idx]
        dp=np.zeros(len(idx)); dp[1:]=np.diff(x)
        if len(idx)>1: df.loc[idx[1:],"since_prev"]=np.diff(t)
        pref=np.concatenate([[0.0],np.cumsum(dp*dp)]); loc=np.arange(len(idx))
        for w in WINDOWS:
            left=np.searchsorted(t,t-w,side="left"); supported=(t-w)>=t[0]
            df.loc[idx,f"count_{w}"]=np.where(supported,loc-left+1,np.nan)
            df.loc[idx,f"vol_{w}"]=np.where(supported,np.sqrt(np.maximum(0,pref[loc+1]-pref[left])),np.nan)
            df.loc[idx,f"mom_{w}"]=np.where(supported,x-x[left],np.nan)
    for c in ("fee_charged","fee_missing","fee_no_leg"):
        df[c]=pd.to_numeric(df[c],errors="coerce").fillna(0).astype(float)
    df["fee_net_log"]=np.sign(df.active_fee_net.fillna(0))*np.log1p(np.abs(df.active_fee_net.fillna(0)))
    df["fee_charge_log"]=np.log1p(np.maximum(0,df.active_fee_charged.fillna(0)))
    df["fee_refund_log"]=np.log1p(np.maximum(0,df.active_fee_refunded.fillna(0)))
    df["charge_legs_log"]=np.log1p(np.maximum(0,df.active_charge_legs.fillna(0)))
    return df

def hazard_target(df:pd.DataFrame,h:int)->np.ndarray:
    n=len(df); y=np.full(n,np.nan); ts=df.timestamp.to_numpy(np.int64); p=df.p_yes.to_numpy(float)
    for _,raw in df.groupby(["window_id","condition_id"],sort=False).indices.items():
        idx=np.asarray(raw,int); t=ts[idx]; x=p[idx]; start=0
        while start<len(idx):
            end=start
            while end+1<len(idx) and abs(x[end+1]-x[start])<=1e-12: end+=1
            nxt=end+1
            for k in range(start,end+1):
                if t[k]+h>t[-1]: continue
                if nxt<len(idx): y[idx[k]]=1.0 if int(t[nxt]-t[k])<=h else 0.0
                else: y[idx[k]]=0.0
            start=end+1
    return y

def X(df,cols): return df[cols].apply(pd.to_numeric,errors="coerce").to_numpy(float)
def model(kind,param):
    if kind=="logit":
        return make_pipeline(SimpleImputer(strategy="median"),StandardScaler(),LogisticRegression(C=float(param),max_iter=700,random_state=SEED))
    if kind=="hgb":
        return make_pipeline(SimpleImputer(strategy="median"),HistGradientBoostingClassifier(learning_rate=.05,max_iter=120,max_leaf_nodes=int(param),l2_regularization=2,random_state=SEED))
    raise ValueError(kind)
def metrics(y,p):
    p=np.clip(p,1e-6,1-1e-6)
    return {"brier":float(brier_score_loss(y,p)),"logloss":float(log_loss(y,p,labels=[0,1])),
            "auc":float(roc_auc_score(y,p)) if len(np.unique(y))>1 else None}
def calibration(y,p,bins=10):
    p=np.asarray(p,float); y=np.asarray(y,float)
    qs=np.unique(np.quantile(p,np.linspace(0,1,bins+1)))
    out=[]
    if len(qs)<2:return out
    ix=np.digitize(p,qs[1:-1],right=True)
    for b in range(len(qs)-1):
        m=ix==b
        if m.any(): out.append({"bin":int(b),"n":int(m.sum()),"mean_pred":float(p[m].mean()),"mean_y":float(y[m].mean())})
    return out

def diagnostics(dev,y,p,b):
    gain=(y-b)**2-(y-p)**2
    tmp=pd.DataFrame({"condition":dev.condition_id.astype(str).to_numpy(),"day":dev.timestamp.to_numpy(np.int64)//86400,"gain":gain})
    pc=tmp.groupby("condition")["gain"].mean().to_numpy(float)
    rng=np.random.default_rng(SEED); reps=np.empty(BOOT)
    for i in range(BOOT): reps[i]=float(np.mean(pc[rng.integers(0,len(pc),len(pc))]))
    med=int(np.median(dev.timestamp.to_numpy(np.int64))); first=dev.timestamp.to_numpy(np.int64)<=med
    def imp(mask):
        bb=float(np.mean((y[mask]-b[mask])**2)); mm=float(np.mean((y[mask]-p[mask])**2))
        return (bb-mm)/bb if bb else 0.0
    pos=np.sort(pc[pc>0])[::-1]
    share=1.0
    if len(pos):
        k=max(1,int(np.ceil(.10*len(pos)))); share=float(pos[:k].sum()/pos.sum())
    return {
      "condition_cluster_bootstrap_lower_2_5":float(np.quantile(reps,.025)),
      "condition_cluster_bootstrap_upper_97_5":float(np.quantile(reps,.975)),
      "condition_positive_fraction":float(np.mean(pc>0)),
      "condition_count":int(len(pc)),
      "first_half_improvement":float(imp(first)),
      "second_half_improvement":float(imp(~first)),
      "top_decile_share_positive_condition_gain":share
    }

def group_stability(frame,y,p,b,col,min_rows=20):
    labels=frame[col].fillna("__MISSING__").astype(str).to_numpy()
    rows=[]
    for label in sorted(set(labels)):
        m=labels==label
        if int(m.sum())<min_rows: continue
        bb=float(np.mean((y[m]-b[m])**2)); mm=float(np.mean((y[m]-p[m])**2))
        rows.append({"group":label,"rows":int(m.sum()),"conditions":int(frame.loc[m,"condition_id"].nunique()),
                     "baseline_brier":bb,"candidate_brier":mm,
                     "relative_brier_improvement":float((bb-mm)/bb) if bb else 0.0})
    return rows

def market_stability(frame,y,p,b,min_rows=10):
    rows=group_stability(frame,y,p,b,"sig_market_id",min_rows)
    if not rows:
        return {"markets_with_support":0,"positive_fraction":None,"median_relative_improvement":None,"p10":None,"p90":None,"best":[],"worst":[]}
    vals=np.asarray([r["relative_brier_improvement"] for r in rows],float)
    ordered=sorted(rows,key=lambda r:r["relative_brier_improvement"])
    return {"markets_with_support":len(rows),"positive_fraction":float(np.mean(vals>0)),
            "median_relative_improvement":float(np.median(vals)),
            "p10":float(np.quantile(vals,.10)),"p90":float(np.quantile(vals,.90)),
            "best":ordered[-5:][::-1],"worst":ordered[:5]}

def evaluate_candidate(df,split,cand):
    if cand["family"]!="hazard_fee" or cand["target_type"]!="next_price_change_hazard":
        raise RuntimeError(f"unsupported frozen candidate family/target {cand['family']} {cand['target_type']}")
    h=int(cand["horizon_seconds"]); features=list(cand["features"])
    final_start=int(split["final_start_epoch"]); purge=int(split["selection_rule"]["target_purge_seconds"])
    yall=hazard_target(df,h); ts=df.timestamp.to_numpy(np.int64)
    trm=(ts<final_start-purge)&np.isfinite(yall)
    fm=(ts>=final_start)&np.isfinite(yall)
    tr=df.loc[trm].reset_index(drop=True); fin=df.loc[fm].reset_index(drop=True)
    yt=yall[trm].astype(int); yf=yall[fm].astype(int)
    if len(tr)<1000 or len(fin)<300 or fin.condition_id.nunique()<25: raise RuntimeError("insufficient frozen FINAL support")
    if any(c not in df.columns for c in features): raise RuntimeError("frozen feature missing")
    base_rate=np.full(len(fin),float(np.mean(yt)))
    hist=model("logit",1.0); hist.fit(X(tr,BASE),yt); hp=hist.predict_proba(X(fin,BASE))[:,1]
    bases={"base_rate":base_rate,"own_history_logit":hp}
    bmets={k:metrics(yf,v) for k,v in bases.items()}
    bname=min(bmets,key=lambda k:bmets[k]["brier"]); bp=bases[bname]; bb=bmets[bname]["brier"]
    m=model(str(cand["model_kind"]),cand["model_param"]); m.fit(X(tr,features),yt); pp=m.predict_proba(X(fin,features))[:,1]
    met=metrics(yf,pp); rel=(bb-met["brier"])/bb if bb else 0.0; d=diagnostics(fin,yf,pp,bp)
    rule=cand["final_pass_rule"]
    passed=bool(
      rel>=float(rule["min_relative_loss_improvement_vs_best_frozen_baseline"]) and
      d["condition_cluster_bootstrap_lower_2_5"]>float(rule["condition_cluster_bootstrap_lower_2_5_must_exceed"]) and
      d["first_half_improvement"]>float(rule["first_half_loss_improvement_must_exceed"]) and
      d["second_half_improvement"]>float(rule["second_half_loss_improvement_must_exceed"]) and
      d["condition_positive_fraction"]>=float(rule["min_fraction_conditions_with_positive_loss_improvement"]) and
      d["top_decile_share_positive_condition_gain"]<=float(rule["max_top_decile_share_of_positive_condition_gain"])
    )
    return {
      "candidate_id":cand["candidate_id"],"family":cand["family"],"target_type":cand["target_type"],"horizon_seconds":h,
      "model_kind":cand["model_kind"],"model_param":cand["model_param"],"features":features,
      "train_rows":int(len(tr)),"final_rows":int(len(fin)),"final_conditions":int(fin.condition_id.nunique()),
      "final_sig_markets":int(fin.sig_market_id.nunique()),
      "best_frozen_baseline_on_final":bname,"baseline_metrics":bmets,
      "candidate_metrics":met,"relative_brier_improvement_vs_best_baseline":float(rel),
      "diagnostics":d,"calibration_bins":calibration(yf,pp),
      "mapping_class_stability":group_stability(fin,yf,pp,bp,"mapping_class",20),
      "sig_market_stability":market_stability(fin,yf,pp,bp,10),
      "pass":passed
    }

def main():
    split_path=locate("split_manifest.json","pred006-phase0-audit"); split=json.loads(split_path.read_text())
    if sha256(split_path)!=EXPECTED_SPLIT_SHA: raise RuntimeError("split hash mismatch")
    freeze_path=locate("shortlist_freeze.json","pred006-shortlist-freeze"); freeze=json.loads(freeze_path.read_text())
    if freeze.get("classification")!="SHORTLIST_FREEZE_BEFORE_FINAL": raise RuntimeError("invalid shortlist freeze")
    if freeze.get("split_manifest_sha256")!=EXPECTED_SPLIT_SHA: raise RuntimeError("freeze split mismatch")
    cands=freeze.get("candidates",[])
    if not cands:
        payload={"schema_version":1,"experiment_id":"PRED-006","final_opened":False,"candidate_count":0,"disposition":"TERMINAL_NULL","reason":"Frozen shortlist empty; FINAL remained unopened."}
        (OUT/"final_results.json").write_text(json.dumps(payload,indent=2,sort_keys=True)+"\n")
        (OUT/"FINAL_REPORT.md").write_text("# PRED-006 FINAL\n\nFrozen shortlist empty. FINAL predictive outcomes were not opened.\n\nDisposition: **TERMINAL_NULL**.\n")
        print("PRED006_FINAL_RESULT="+json.dumps(payload,sort_keys=True),flush=True); return
    # This is the one and only code path that reads FINAL predictive outcomes.
    df=add_features(load_full_frame())
    results=[evaluate_candidate(df,split,c) for c in cands]
    survivors=[r for r in results if r["pass"]]
    disposition="CURRENT_UNIVERSE_CANDIDATE_REQUIRES_FUTURE_CONFIRMATION" if survivors else "TERMINAL_NULL"
    payload={"schema_version":1,"experiment_id":"PRED-006","final_opened":True,"final_start_epoch":int(split["final_start_epoch"]),
             "shortlist_freeze_sha256":sha256(freeze_path),"candidate_count":len(cands),"results":results,
             "survivor_ids":[r["candidate_id"] for r in survivors],"disposition":disposition,
             "post_final_rescue_forbidden":True}
    (OUT/"final_results.json").write_text(json.dumps(payload,indent=2,sort_keys=True)+"\n")
    lines=["# PRED-006 — One-Shot FINAL Results","",f"Disposition: **{disposition}**.","",
           f"Frozen candidates evaluated: {len(cands)}. Survivors: {len(survivors)}.","",
           "| Candidate | Horizon | FINAL rows | Brier improvement | Bootstrap 2.5% | Conditions positive | First half | Second half | Pass |",
           "|---|---:|---:|---:|---:|---:|---:|---:|---|"]
    for r in results:
        d=r["diagnostics"]
        lines.append(f"| {r['candidate_id']} | {r['horizon_seconds']}s | {r['final_rows']} | {r['relative_brier_improvement_vs_best_baseline']:.4%} | {d['condition_cluster_bootstrap_lower_2_5']:.6f} | {d['condition_positive_fraction']:.2%} | {d['first_half_improvement']:.4%} | {d['second_half_improvement']:.4%} | {'PASS' if r['pass'] else 'FAIL'} |")
    lines += ["","No post-FINAL rescue, retuning, subsetting, horizon changes, or feature changes are permitted."]
    (OUT/"FINAL_REPORT.md").write_text("\n".join(lines)+"\n")
    specs=[]
    for r in survivors:
        specs.append({"candidate_id":r["candidate_id"],"interface":"observable snapshot -> bounded hazard probability + confidence + freshness",
                      "target":"probability canonical YES price changes within frozen horizon","horizon_seconds":r["horizon_seconds"],
                      "features":r["features"],"model_kind":r["model_kind"],"model_param":r["model_param"],
                      "integration_status":"DO_NOT_INTEGRATE_YET_REQUIRES_FUTURE_CONFIRMATION"})
    (OUT/"make_plugin_spec.json").write_text(json.dumps({"schema_version":1,"experiment_id":"PRED-006","survivors":specs},indent=2,sort_keys=True)+"\n")
    print("PRED006_FINAL_RESULT="+json.dumps({"candidate_count":len(cands),"survivor_count":len(survivors),
          "survivor_ids":[r["candidate_id"] for r in survivors],"disposition":disposition,"final_opened":True},sort_keys=True),flush=True)

if __name__=="__main__": main()