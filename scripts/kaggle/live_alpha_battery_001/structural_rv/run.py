LANE="structural_rv"

from pathlib import Path
import json, math, os, re, warnings
import numpy as np
import pandas as pd
warnings.filterwarnings("ignore")
OUT=Path(os.environ.get("LIVE_ALPHA_OUTPUT","/kaggle/working")); OUT.mkdir(parents=True,exist_ok=True)
override=os.environ.get("LIVE_ALPHA_INPUT")
if override:
    ROOT=Path(override)
else:
    hits=list(Path("/kaggle/input").rglob("trade_features_part01.csv"))
    if not hits: hits=list(Path("/kaggle/src").rglob("trade_features_part01.csv"))
    if not hits: raise RuntimeError("live alpha tape not found")
    ROOT=hits[0].parent

def load_parts(pat):
    fs=sorted(ROOT.glob(pat))
    if not fs: raise RuntimeError("missing "+pat)
    return pd.concat([pd.read_csv(f) for f in fs],ignore_index=True)

def load():
    tr=load_parts("trade_features_part*.csv"); st=load_parts("state_minute_part*.csv")
    tr["executed_at"]=pd.to_datetime(tr["executed_at"],utc=True); st["at"]=pd.to_datetime(st["at"],utc=True)
    for df,exclude in [(tr,{"executed_at","exchange_id","title","token_id","taker_side"}),(st,{"at","exchange_id","title","token_id"})]:
        for c in df.columns:
            if c not in exclude: df[c]=pd.to_numeric(df[c],errors="coerce")
        df["exchange_id"]=df["exchange_id"].astype(str)
    return tr,st

def split_col(df,tcol):
    x=df[tcol].dropna().sort_values(); cut=x.iloc[int(max(1,len(x)*.60))-1]
    return np.where(df[tcol]<=cut,"DEV","VAL"),cut

def ci95(x):
    x=pd.Series(x).dropna().astype(float)
    if len(x)<2:return (np.nan,np.nan)
    se=x.std(ddof=1)/math.sqrt(len(x)); return (x.mean()-1.96*se,x.mean()+1.96*se)

def save(name,rows,result,lines):
    pd.DataFrame(rows).to_csv(OUT/(name+".csv"),index=False)
    (OUT/"result.json").write_text(json.dumps(result,indent=2,sort_keys=True)+"\n")
    (OUT/"FINAL_REPORT.md").write_text("\n".join(lines)+"\n")
    print("\n".join(lines)); print("outputs",sorted(p.name for p in OUT.iterdir()))

def maker_queue():
    tr,_=load()
    v=tr[(tr.taker_side.isin(["BUY","SELL"]))&(tr.sig_bbo_age_s<=10)&(tr.pm_age_s<=65)&tr.pm_mid.notna()&tr.price.notna()&tr.sig_bid.notna()&tr.sig_ask.notna()].copy()
    v["split"],cut=split_col(v,"executed_at"); v["depth_q"]=v[["pm_bid1_size","pm_ask1_size"]].min(axis=1)
    v["excess_spread"]=v.sig_spread-v.pm_spread; v["mid_inside"]=(v.pm_mid>=v.sig_bid)&(v.pm_mid<=v.sig_ask)
    rows=[]
    for off in [.005,.0075,.01,.0125,.015,.02]:
      for pmax in [.005,.01,.02]:
       for dep in [50,100,200,500]:
        for excess in [0,.005,.01,.015]:
         q=v[(v.pm_spread<=pmax)&(v.depth_q>=dep)&(v.excess_spread>=excess)&v.mid_inside].copy()
         q["quote_px"]=np.where(q.taker_side=="SELL",q.pm_mid-off,q.pm_mid+off)
         q["competitive"]=np.where(q.taker_side=="SELL",(q.quote_px>=q.sig_bid)&(q.quote_px<q.sig_ask),(q.quote_px<=q.sig_ask)&(q.quote_px>q.sig_bid))
         q["filled"]=q.competitive & np.where(q.taker_side=="SELL",q.price<=q.quote_px,q.price>=q.quote_px)
         f=q[q.filled].copy()
         if len(f)==0: continue
         f["m60"]=f.maker_sign*(f.pm_mid_60s-f.quote_px); f["m300"]=f.maker_sign*(f.pm_mid_300s-f.quote_px)
         f["through"]=np.where(f.taker_side=="SELL",f.quote_px-f.price,f.price-f.quote_px)
         d=f[f.split=="DEV"]; z=f[f.split=="VAL"]; lo,_=ci95(z.m60)
         rows.append({"offset_c":off*100,"pm_max_c":pmax*100,"depth":dep,"excess_c":excess*100,"dev_n":len(d),"val_n":len(z),"val_markets":z.exchange_id.nunique(),"val_m60_c":100*z.m60.mean() if len(z) else np.nan,"val_m300_c":100*z.m300.mean() if z.m300.notna().any() else np.nan,"val_ci_lo_c":100*lo if np.isfinite(lo) else np.nan,"val_pre_move_c":100*z.pre_move_against_maker.mean() if len(z) else np.nan,"val_through_c":100*z.through.mean() if len(z) else np.nan,"concentration":z.exchange_id.value_counts(normalize=True).max() if len(z) else np.nan})
    tab=pd.DataFrame(rows)
    good=tab[(tab.val_n>=8)&(tab.val_markets>=3)&(tab.val_ci_lo_c>0)&(tab.concentration<=.6)].sort_values(["val_ci_lo_c","val_n"],ascending=False)
    result={"lane":"maker_queue","cut":str(cut),"valid_trades":len(v),"candidate_count":len(good),"top":good.head(20).to_dict("records")}
    lines=["# LIVE ALPHA BATTERY — Maker / Queue Economics","",f"Valid matched trades: **{len(v)}**. Chronological cut: **{cut}**.","Fill model is optimistic on queue priority; execution price is our PM-centred quote, not the observed print.","Candidate gate: validation n>=8, >=3 markets, positive 95% lower bound at 60s, concentration <=60%.","","## Top candidates",good.head(20).to_markdown(index=False) if len(good) else "No candidate passed."]
    save("maker_grid",rows,result,lines)

def residual_taker():
    _,st=load(); s=st.dropna(subset=["sig_bid","sig_ask","pm_mid","pm_spread"]).sort_values("at").copy()
    s["split"],cut=split_col(s,"at"); s["depth_q"]=s[["pm_bid1_size","pm_ask1_size"]].min(axis=1)
    rows=[]
    for thr in [.005,.01,.015,.02,.03,.05]:
      for pmax in [.005,.01,.02]:
       for dep in [50,100,200,500]:
        for cooldown in [60,300,600]:
         cand=[]; last={}
         for r in s.itertuples(index=False):
          direction=None
          if r.sig_ask<=r.pm_mid-thr: direction="BUY"
          elif r.sig_bid>=r.pm_mid+thr: direction="SELL"
          if direction is None or r.pm_spread>pmax or r.depth_q<dep: continue
          k=(r.exchange_id,direction); t=r.at.timestamp()
          if t-last.get(k,-1e18)<cooldown: continue
          last[k]=t
          e60=(r.pm_mid_60s-r.sig_ask) if direction=="BUY" else (r.sig_bid-r.pm_mid_60s)
          e300=(r.pm_mid_300s-r.sig_ask) if direction=="BUY" else (r.sig_bid-r.pm_mid_300s)
          edge=(r.pm_mid-r.sig_ask) if direction=="BUY" else (r.sig_bid-r.pm_mid)
          cand.append((r.split,r.exchange_id,edge,e60,e300))
         if not cand:continue
         d=pd.DataFrame(cand,columns=["split","exchange_id","edge","m60","m300"]); z=d[d.split=="VAL"]; dv=d[d.split=="DEV"]; lo,_=ci95(z.m60)
         rows.append({"threshold_c":thr*100,"pm_max_c":pmax*100,"depth":dep,"cooldown_s":cooldown,"dev_n":len(dv),"val_n":len(z),"val_markets":z.exchange_id.nunique(),"val_entry_c":100*z.edge.mean() if len(z) else np.nan,"val_m60_c":100*z.m60.mean() if len(z) else np.nan,"val_m300_c":100*z.m300.mean() if z.m300.notna().any() else np.nan,"val_ci_lo_c":100*lo if np.isfinite(lo) else np.nan,"concentration":z.exchange_id.value_counts(normalize=True).max() if len(z) else np.nan})
    tab=pd.DataFrame(rows); good=tab[(tab.val_n>=5)&(tab.val_markets>=3)&(tab.val_ci_lo_c>0)&(tab.concentration<=.6)].sort_values(["val_ci_lo_c","val_n"],ascending=False)
    result={"lane":"residual_taker","cut":str(cut),"candidate_count":len(good),"top":good.head(20).to_dict("records")}
    lines=["# LIVE ALPHA BATTERY — Residual Taker","","Signals are de-duplicated by market/direction cooldown, so standing dislocations are not counted every sample.",f"Chronological cut: **{cut}**.","","## Top candidates",good.head(20).to_markdown(index=False) if len(good) else "No candidate passed."]
    save("residual_grid",rows,result,lines)

def microstructure():
    tr,st=load(); s=st.sort_values(["exchange_id","at"]).copy(); g=s.groupby("exchange_id",group_keys=False)
    s["pm_ret1"]=s.pm_mid-g.pm_mid.shift(1); s["pm_ret5"]=s.pm_mid-g.pm_mid.shift(5); s["sig_ret1"]=s.sig_mid-g.sig_mid.shift(1); s["sig_ret5"]=s.sig_mid-g.sig_mid.shift(5)
    s["depth_total"]=s.pm_bid_depth5+s.pm_ask_depth5; s["depth_delta1"]=s.depth_total-g.depth_total.shift(1); s["rel_spread"]=s.pm_spread/s.pm_mid.clip(lower=.01)
    s["future_pm_move300"]=s.pm_mid_300s-s.pm_mid; s["split"],cut=split_col(s,"at")
    rows=[]
    for thr in [.0025,.005,.01]:
      q=s[s.pm_ret5.abs()>=thr].copy(); q["score"]=-np.sign(q.pm_ret5)*q.future_pm_move300
      for sp,z in q.groupby("split"):
        lo,_=ci95(z.score); rows.append({"mechanism":"005I_5M_REVERSION_LIVE","spec":f"abs_pm_ret5>={thr}","split":sp,"n":len(z),"markets":z.exchange_id.nunique(),"economic_c":100*z.score.mean(),"ci_lo_c":100*lo if np.isfinite(lo) else np.nan})
    for age in [15,30,60,120,210]:
      q=s[s.sig_age_s>=age].copy(); q["score"]=-((q.pm_mid_300s-q.sig_mid_300s).abs()-(q.pm_mid-q.sig_mid).abs())
      for sp,z in q.groupby("split"):
        lo,_=ci95(z.score); rows.append({"mechanism":"005F_SIG_BBO_AGE_PROXY","spec":f"sig_age_s>={age}","split":sp,"n":len(z),"markets":z.exchange_id.nunique(),"economic_c":100*z.score.mean(),"ci_lo_c":100*lo if np.isfinite(lo) else np.nan})
    q=s[(s.rel_spread>=.9)&(s.depth_total<=605)].copy(); q["score"]=(q.pm_mid_300s-q.pm_mid).abs()
    for sp,z in q.groupby("split"): rows.append({"mechanism":"005I_LIQUIDITY_STRESS_PROXY","spec":"rel_spread>=0.9 depth_total<=605","split":sp,"n":len(z),"markets":z.exchange_id.nunique(),"economic_c":100*z.score.mean(),"ci_lo_c":np.nan})
    q=s[s.depth_delta1>=200].copy(); q["score"]=-(q.pm_mid_300s-q.pm_mid).abs()
    for sp,z in q.groupby("split"):
      lo,_=ci95(z.score); rows.append({"mechanism":"005I_REPLENISHMENT_PROXY","spec":"depth_delta1>=200","split":sp,"n":len(z),"markets":z.exchange_id.nunique(),"economic_c":100*z.score.mean(),"ci_lo_c":100*lo if np.isfinite(lo) else np.nan})
    t=tr[(tr.taker_side.isin(["BUY","SELL"]))&tr.maker_m60.notna()].copy(); t["split"],_=split_col(t,"executed_at"); t["flow_abs"]=t.prior60_signed_qty.abs()
    for qtile in [.5,.75,.9]:
      cutq=t[t.split=="DEV"].flow_abs.quantile(qtile); z=t[t.flow_abs>=cutq]
      for sp,a in z.groupby("split"):
        lo,_=ci95(a.maker_m60); rows.append({"mechanism":"FLOW_CONTEXT_ONLY","spec":f"prior60_abs>DEV_q{qtile}","split":sp,"n":len(a),"markets":a.exchange_id.nunique(),"economic_c":100*a.maker_m60.mean(),"ci_lo_c":100*lo if np.isfinite(lo) else np.nan})
    tab=pd.DataFrame(rows); piv=tab[tab.split=="VAL"].sort_values(["ci_lo_c","n"],ascending=False)
    result={"lane":"microstructure","cut":str(cut),"rows":len(s),"notes":["005F genuine_age parity unavailable: SIG BBO age is proxy only.","005I quote-event/OFI parity unavailable in compact tape: stress/replenishment are proxy diagnostics.","Lead-lag is not promoted."],"top_validation":piv.head(30).to_dict("records")}
    lines=["# LIVE ALPHA BATTERY — 005F / 005I Microstructure","","This lane distinguishes exact observable concepts from live proxies; it does not relabel proxies as frozen-model replications.","Lead-lag is diagnostic only and cannot be promoted.","","## Validation slices",piv.head(30).to_markdown(index=False)]
    save("microstructure_slices",rows,result,lines)

def structural_rv():
    _,st=load(); s=st.copy(); pat=re.compile(r"^Will the (Democratic|Republican) Party win the (.+)\?$")
    parsed=s.title.astype(str).str.extract(pat); s["party"]=parsed[0]; s["race"]=parsed[1]; s=s[s.party.notna()].copy()
    d=s[s.party=="Democratic"]; r=s[s.party=="Republican"]; m=d.merge(r,on=["at","race"],suffixes=("_d","_r"))
    if len(m)==0: raise RuntimeError("no D/R pairs")
    m["pm_pair"]=m.pm_mid_d+m.pm_mid_r; m["sig_ask_pair"]=m.sig_ask_d+m.sig_ask_r; m["sig_bid_pair"]=m.sig_bid_d+m.sig_bid_r
    m["pm_pair60"]=m.pm_mid_60s_d+m.pm_mid_60s_r; m["pm_pair300"]=m.pm_mid_300s_d+m.pm_mid_300s_r
    m["depth_q"]=m[["pm_bid1_size_d","pm_ask1_size_d","pm_bid1_size_r","pm_ask1_size_r"]].min(axis=1); m["pm_spread_max"]=m[["pm_spread_d","pm_spread_r"]].max(axis=1)
    m["split"],cut=split_col(m,"at"); rows=[]
    for thr in [.005,.01,.015,.02,.03]:
     for pmax in [.005,.01,.02]:
      for dep in [50,100,200]:
       for cooldown in [60,300]:
        q=m[(m.pm_pair.sub(1).abs()<=.03)&(m.pm_spread_max<=pmax)&(m.depth_q>=dep)].sort_values("at"); picks=[]; last={}
        for x in q.itertuples(index=False):
          buy=x.pm_pair-x.sig_ask_pair; sell=x.sig_bid_pair-x.pm_pair
          if max(buy,sell)<thr: continue
          direction="BUY_PAIR" if buy>=sell else "SELL_PAIR"; key=(x.race,direction); ts=x.at.timestamp()
          if ts-last.get(key,-1e18)<cooldown: continue
          last[key]=ts
          e60=(x.pm_pair60-x.sig_ask_pair) if direction=="BUY_PAIR" else (x.sig_bid_pair-x.pm_pair60)
          e300=(x.pm_pair300-x.sig_ask_pair) if direction=="BUY_PAIR" else (x.sig_bid_pair-x.pm_pair300)
          picks.append((x.split,x.race,direction,max(buy,sell),e60,e300))
        if not picks: continue
        z=pd.DataFrame(picks,columns=["split","race","direction","edge","m60","m300"]); vv=z[z.split=="VAL"]; dd=z[z.split=="DEV"]; lo,_=ci95(vv.m60)
        rows.append({"threshold_c":thr*100,"pm_max_c":pmax*100,"depth":dep,"cooldown_s":cooldown,"dev_n":len(dd),"val_n":len(vv),"val_races":vv.race.nunique(),"val_edge_c":100*vv.edge.mean() if len(vv) else np.nan,"val_m60_c":100*vv.m60.mean() if len(vv) else np.nan,"val_m300_c":100*vv.m300.mean() if vv.m300.notna().any() else np.nan,"val_ci_lo_c":100*lo if np.isfinite(lo) else np.nan})
    tab=pd.DataFrame(rows); good=tab[(tab.val_n>=4)&(tab.val_races>=2)&(tab.val_ci_lo_c>0)].sort_values(["val_ci_lo_c","val_n"],ascending=False)
    result={"lane":"structural_rv","pair_rows":len(m),"cut":str(cut),"candidate_count":len(good),"top":good.head(20).to_dict("records")}
    lines=["# LIVE ALPHA BATTERY — Structural Relative Value","","Only D/R same-race pairs with PM pair value within 3c of 1 are used. PM pair value is the fair-value anchor; this is not a settlement-exhaustiveness assumption.",f"Paired minute rows: **{len(m)}**.","","## Top candidates",good.head(20).to_markdown(index=False) if len(good) else "No candidate passed."]
    save("structural_pair_grid",rows,result,lines)

def hazard():
    _,st=load()
    from sklearn.ensemble import HistGradientBoostingClassifier
    from sklearn.impute import SimpleImputer
    from sklearn.metrics import brier_score_loss, roc_auc_score
    from sklearn.pipeline import make_pipeline
    s=st.sort_values(["exchange_id","at"]).copy(); g=s.groupby("exchange_id",group_keys=False)
    s["sig_ret1"]=s.sig_mid-g.sig_mid.shift(1); s["sig_ret5"]=s.sig_mid-g.sig_mid.shift(5); s["pm_ret1"]=s.pm_mid-g.pm_mid.shift(1); s["pm_ret5"]=s.pm_mid-g.pm_mid.shift(5)
    s["depth_total"]=s.pm_bid_depth5+s.pm_ask_depth5; s["imbalance"]=(s.pm_bid_depth5-s.pm_ask_depth5)/s.depth_total.replace(0,np.nan); s["signed_residual"]=s.sig_mid-s.pm_mid; s["boundary_distance"]=np.minimum(s.sig_mid,1-s.sig_mid)
    s["split"],cut=split_col(s,"at")
    base=["sig_mid","sig_spread","sig_age_s","sig_ret1","sig_ret5"]
    full=base+["pm_mid","pm_spread","pm_age_s","pm_ret1","pm_ret5","depth_total","imbalance","signed_residual","abs_residual","boundary_distance"]
    rows=[]
    for horizon in [60,300]:
      fut=s[f"sig_mid_{horizon}s"]; y=((fut-s.sig_mid).abs()>=.005).astype(float); ok=fut.notna()&s.sig_mid.notna()
      d=s[ok].copy(); y=y[ok].astype(int); train=d.split=="DEV"; val=d.split=="VAL"
      if y[train].nunique()<2 or y[val].nunique()<2: continue
      pred={}
      for name,feats,leaf in [("BASE",base,7),("FULL7",full,7),("FULL15",full,15)]:
        model=make_pipeline(SimpleImputer(strategy="median"),HistGradientBoostingClassifier(max_leaf_nodes=leaf,max_iter=120,learning_rate=.05,l2_regularization=1.0,random_state=7))
        model.fit(d.loc[train,feats],y[train]); pred[name]=model.predict_proba(d.loc[val,feats])[:,1]
      b0=brier_score_loss(y[val],pred["BASE"])
      for name in ["FULL7","FULL15"]:
        b=brier_score_loss(y[val],pred[name]); imp=(b0-b)/b0; auc=roc_auc_score(y[val],pred[name])
        temp=pd.DataFrame({"exchange_id":d.loc[val,"exchange_id"].values,"y":y[val].values,"pb":pred["BASE"],"p":pred[name]}); gains=[]
        for _,z in temp.groupby("exchange_id"):
          if len(z)>=5: gains.append(np.mean((z.y-z.pb)**2-(z.y-z.p)**2))
        rows.append({"horizon_s":horizon,"model":name,"train_n":int(train.sum()),"val_n":int(val.sum()),"base_brier":b0,"model_brier":b,"relative_improvement":imp,"auc":auc,"market_positive_fraction":float(np.mean(np.array(gains)>0)) if gains else np.nan,"markets_evaluated":len(gains)})
    tab=pd.DataFrame(rows); good=tab[(tab.relative_improvement>.01)&(tab.market_positive_fraction>=.55)].sort_values("relative_improvement",ascending=False)
    result={"lane":"pred006_live_hazard","cut":str(cut),"parity":"PRED-006-inspired only: fee features and 600/1800s horizons unavailable on this snapshot","candidates":good.to_dict("records"),"all":rows}
    lines=["# LIVE ALPHA BATTERY — PRED-006-Inspired Repricing Hazard","","This is not a frozen PRED-006 replication: live fee features are absent and the snapshot is too short for its 600s/1800s targets.","It tests the same economic question at 60s/300s: can observable state improve the hazard of a material SIG repricing versus own-history state?","","## Results",tab.to_markdown(index=False) if len(tab) else "Insufficient target variation.","","## Candidates",good.to_markdown(index=False) if len(good) else "No candidate passed the >1% Brier improvement and >=55% positive-market gate."]
    save("hazard_models",rows,result,lines)

def run(lane):
    if lane=="maker_queue": maker_queue()
    elif lane=="residual_taker": residual_taker()
    elif lane=="microstructure": microstructure()
    elif lane=="structural_rv": structural_rv()
    elif lane=="hazard": hazard()
    else: raise RuntimeError("unknown lane "+lane)

run(LANE)
