# ruff: noqa
from __future__ import annotations
import hashlib,json,math
from pathlib import Path
import duckdb,numpy as np,pandas as pd
from scipy.optimize import linprog,minimize
from scipy.stats import spearmanr

OUT=Path("/kaggle/working/r3_fv_001_math_challengers"); OUT.mkdir(parents=True,exist_ok=True)
D4="sig-cup-data-004-ets-p0p1-fills"; GATE="005b-data003-block-gate"; PREP="r3-fv-001-prepare-v2"
HL=21600.; EPS=1e-8; SEED=20260929; MATH_HEAD="e46eac3cb8aa713728407ef2f71d96f71f31e41e"

def loc(name,frag):
    m=[p for p in Path("/kaggle/input").rglob(name) if frag in str(p)]
    if len(m)!=1: raise RuntimeError((name,frag,m))
    return m[0]
def root4():
    r=sorted({p.parent for p in Path("/kaggle/input").rglob("MANIFEST.json") if D4 in str(p.parent)})
    if len(r)!=1: raise RuntimeError(r)
    return r[0]
def graph_bundle(root):
    m=sorted(root.rglob("ETS_SIG_ANCHOR_GRAPH.csv"))
    if len(m)!=1: raise RuntimeError(("ETS_SIG_ANCHOR_GRAPH.csv",m))
    return m[0].parent
def h(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()
def qq(p): return str(p).replace("'","''")
def sid(x):
    s=str(x).strip()
    return s[:-2] if s.endswith(".0") and s[:-2].isdigit() else s
def split_load():
    p=loc("R3_SPLIT_MANIFEST.json",PREP); s=json.loads(p.read_text())
    assert s["split_status"]=="FROZEN_BEFORE_PERFORMANCE" and s["final_opened"] is False
    return s,p

def direct_rows(final):
    e=loc("economic_fills_DATA003.parquet",GATE); t=loc("tx_block_DATA003.parquet",GATE); b=loc("block_timestamp.parquet",GATE)
    gate=json.loads(loc("data003_block_gate_report.json",GATE).read_text()); assert gate["all_hard_gates_pass"] is True
    con=duckdb.connect(); con.execute("pragma threads=4")
    d=con.execute(f"""
      with x as (
       select cast(e.timestamp as bigint) ts,cast(t.block_number as bigint) block_number,
       cast(e.log_index as bigint) log_index,cast(e.sig_market_id as varchar) sig_market_id,
       upper(cast(e.mapping_class as varchar)) mapping_class,
       case when upper(cast(e.mapping_direction as varchar))='SAME' then cast(e.p_yes as double)
            when upper(cast(e.mapping_direction as varchar))='COMPLEMENT' then 1-cast(e.p_yes as double) end p
       from read_parquet('{qq(e)}') e join read_parquet('{qq(t)}') t on lower(cast(e.tx_hash as varchar))=t.tx_hash
       join read_parquet('{qq(b)}') b using(block_number)
       where upper(cast(e.mapping_class as varchar)) in ('EXACT','NEAR')
       and upper(cast(e.mapping_direction as varchar)) in ('SAME','COMPLEMENT'))
      select block_number,max(ts) ts,sig_market_id,arg_max(p,log_index) y,arg_max(mapping_class,log_index) mapping_class
      from x where p between 0 and 1 group by 1,3 order by 1,3
    """).fetchdf(); con.close()
    d["sig_market_id"]=d.sig_market_id.map(sid); d=d[d.ts<final].copy()
    assert not (d.ts>=final).any()
    return d

def source_rows(r,final,ids):
    fs=sorted(r.rglob("fills/date=*/part-*.parquet")); sql=",".join("'"+qq(p)+"'" for p in fs); il=",".join("'"+x+"'" for x in sorted(ids))
    con=duckdb.connect(); con.execute("pragma threads=4")
    d=con.execute(f"""
      select cast(block_number as bigint) block_number,cast(log_index as bigint) log_index,cast("timestamp" as bigint) ts,
      cast(market_id as varchar) market_id,upper(cast(acquisition_tier as varchar)) tier,
      case when upper(cast(outcome_label as varchar))='YES' then cast(price as double)
           when upper(cast(outcome_label as varchar))='NO' then 1-cast(price as double) end p
      from read_parquet([{sql}],union_by_name=true)
      where upper(cast(order_role as varchar))='TAKER' and cast(price as double) between 0 and 1
      and cast(market_id as varchar) in ({il}) and upper(cast(acquisition_tier as varchar)) in ('P0','P1')
      order by block_number,log_index
    """).fetchdf(); con.close()
    d["market_id"]=d.market_id.map(sid); d=d[d.ts<final].copy()
    assert set(d.tier.unique()).issubset({"P0","P1"}) and not d.duplicated(["block_number","log_index"]).any()
    return d

def components():
    A=np.zeros((4,9)); A[np.arange(4),[0,1,3,4]]=1
    ga={"ids":["3729337","3729339","3729340","3729338"],"A":A,"states":9,
        "targets":{"166":np.array([1,1,1,0,0,0,0,0,0.]),"167":np.array([0,0,0,1,1,1,0,0,0.]),"258":np.array([1,0,0,1,0,0,1,0,0.]),"259":np.array([0,1,0,0,1,0,0,1,0.])},
        "external":"9 states; rank 4, rank+norm 5; four unidentified fine-state dimensions"}
    sp=[("<=46",0),("47-49",0),("50",0),("51-52",1),(">=53",1)]
    hp=[("<=192",0),("193-207",0),("208-217",0),("218-222",1),(">=223",1)]
    st=[(s,h,sc,hc) for s,sc in sp for h,hc in hp]
    def sb(s): return "<=46" if s=="<=46" else "47-49" if s=="47-49" else "50-52" if s in ("50","51-52") else ">=53"
    def hb(x): return "<=192" if x=="<=192" else "193-207" if x=="193-207" else "208-222" if x in ("208-217","218-222") else ">=223"
    def mem(i,s,x):
        a,b=sb(s),hb(x); z=[
          a=="<=46" and b in ("208-222",">=223"),a=="<=46" and b=="193-207",a=="<=46" and b=="<=192",
          a=="47-49" and b in ("208-222",">=223"),a=="47-49" and b=="193-207",a=="47-49" and b=="<=192",
          a=="50-52" and b==">=223",a=="50-52" and b=="208-222",a=="50-52" and b=="193-207",a=="50-52" and b=="<=192",
          a==">=53" and b==">=223",a==">=53" and b=="208-222",a==">=53" and b in ("<=192","193-207")]
        return z[i]
    B=np.array([[float(mem(i,s,x)) for s,x,_,_ in st] for i in range(13)])
    assert np.allclose(B.sum(0),1)
    sh={"ids":["2683267","2683268","2683269","2683264","2683265","2683266","2683260","2683261","2683262","2683263","2683257","2683258","2683259"],
        "A":B,"states":25,"targets":{"152":np.array([hc for _,_,_,hc in st],float),"154":np.array([sc for _,_,sc,_ in st],float)},
        "external":"audited 16-state/13-category surface rank 13; evaluation splits majority-straddling bins"}
    C=np.zeros((7,51)); groups=[range(22),range(22,24),range(24,26),range(26,28),range(28,30),range(30,32),range(32,51)]
    for i,g in enumerate(groups): C[i,list(g)]=1
    gov={"ids":[str(i) for i in range(907686,907693)],"A":C,"states":51,"targets":{},
         "external":"51 count states; rank 7; 44 unidentified fine-count dimensions"}
    return {"GEORGIA_GOV_SENATE":ga,"SENATE_HOUSE_COUNTS":sh,"REPUBLICAN_GOV_COUNT":gov}

def wgt(age,equal=False): return np.ones(len(age)) if equal else np.exp2(-np.maximum(age,0)/HL)
def bkl(x,p):
    x=np.clip(x,EPS,1-EPS); p=np.clip(p,EPS,1-EPS)
    return x*np.log(x/p)+(1-x)*np.log((1-x)/(1-p))
def proj(A,p,w,kind,c=None,prev=None,pw=0):
    n=A.shape[1]; x0=np.ones(n)/n
    def f(x):
        z=A@x
        v=np.sum(w*(z-p)**2) if kind=="qp" else np.sum(w*bkl(z,p))
        if c is not None and prev is not None:
            u=float(c@x); v+=pw*((u-prev)**2 if kind=="qp" else bkl(np.array([u]),np.array([prev]))[0])
        return float(v)
    r=minimize(f,x0,method="SLSQP",bounds=[(0,1)]*n,constraints=[{"type":"eq","fun":lambda x:x.sum()-1}],options={"maxiter":600,"ftol":1e-11})
    if not r.success: raise RuntimeError((kind,r.message))
    x=np.clip(r.x,0,1); return x/x.sum()
def lp_detail(A,q,c):
    n=A.shape[1]; one=np.ones((1,n)); AE=np.vstack([A,one]); be=np.r_[q,1.]
    l=linprog(c,A_eq=AE,b_eq=be,bounds=[(0,1)]*n,method="highs")
    u=linprog(-c,A_eq=AE,b_eq=be,bounds=[(0,1)]*n,method="highs")
    if not l.success or not u.success: raise RuntimeError("LP infeasible")
    ld=np.asarray(l.eqlin.marginals,dtype=float)
    ud=-np.asarray(u.eqlin.marginals,dtype=float)
    return float(l.fun),float(-u.fun),ld,ud
def lp(A,q,c):
    lo,hi,_,_=lp_detail(A,q,c); return lo,hi
def bounds_at_delta(A,p,c,delta):
    n=A.shape[1]
    Aub=np.vstack([A,-A]); bub=np.r_[p+delta,-p+delta]
    one=np.ones((1,n))
    l=linprog(c,A_ub=Aub,b_ub=bub,A_eq=one,b_eq=[1.0],
              bounds=[(0,1)]*n,method="highs")
    u=linprog(-c,A_ub=Aub,b_ub=bub,A_eq=one,b_eq=[1.0],
              bounds=[(0,1)]*n,method="highs")
    if not l.success or not u.success: raise RuntimeError("robust LP infeasible")
    return float(l.fun),float(-u.fun)
def robust_bounds(A,p,c):
    m,n=A.shape
    obj=np.zeros(n+1); obj[-1]=1.0
    Aub=np.vstack([np.c_[A,-np.ones(m)],np.c_[-A,-np.ones(m)]])
    bub=np.r_[p,-p]
    Aeq=np.zeros((1,n+1)); Aeq[0,:n]=1.0
    r=linprog(obj,A_ub=Aub,b_ub=bub,A_eq=Aeq,b_eq=[1.0],
              bounds=[(0,1)]*n+[(0,1)],method="highs")
    if not r.success: raise RuntimeError(("coherence_tube",r.message))
    delta=max(0.0,float(r.x[-1]))+1e-10
    lo,hi=bounds_at_delta(A,p,c,delta)
    return lo,hi,delta
def category_prior(A):
    n=A.shape[1]; groups=[]; used=set()
    for row in A:
        idx=np.flatnonzero(row>0.5)
        if len(idx):
            groups.append(idx); used.update(int(i) for i in idx)
    rest=np.array([i for i in range(n) if i not in used],dtype=int)
    if len(rest): groups.append(rest)
    r=np.zeros(n,dtype=float)
    for g in groups:
        r[g]=1.0/len(groups)/len(g)
    return r/r.sum()
def iproject(A,q,prior=None):
    n=A.shape[1]; one=np.ones((1,n))
    if np.linalg.matrix_rank(np.vstack([A,one]))==np.linalg.matrix_rank(A): AE,be=A,q
    else: AE,be=np.vstack([A,one]),np.r_[q,1.]
    ref=np.ones(n)/n if prior is None else np.asarray(prior,dtype=float)
    ref=np.clip(ref,EPS,None); ref=ref/ref.sum()
    feasible=linprog(np.zeros(n),A_eq=AE,b_eq=be,bounds=[(0,1)]*n,method="highs")
    if not feasible.success: raise RuntimeError(("iproject_feasible_start",feasible.message))
    x0=np.clip(feasible.x,0,1)
    def f(x):
        z=np.clip(x,EPS,1)
        return float(np.sum(z*np.log(z/ref)))
    r=minimize(f,x0,method="SLSQP",bounds=[(0,1)]*n,constraints=[{"type":"eq","fun":lambda x:AE@x-be}],options={"maxiter":1200,"ftol":1e-10})
    if not r.success:
        return x0/x0.sum()
    x=np.clip(r.x,0,1); return x/x.sum()

def evaluate(d,s,sp,cs,mode="strict",equal=False,shuffle=False,drop=-1):
    ds=int(sp["dev_start_epoch"]); fs=int(sp["final_start_epoch"]); purge=int(sp["selection_rule"]["boundary_purge_seconds"])
    t2c={t:n for n,c in cs.items() for t in c["targets"]}; sr=list(s[["block_number","ts","market_id","p"]].itertuples(index=False,name=None))
    state={}; k=0; prev={}; out=[]
    for e in d.itertuples(index=False):
        b=int(e.block_number); cut=b if mode=="leaky" else b-2 if mode=="delay" else b-1
        while k<len(sr) and int(sr[k][0])<=cut:
            bb,tt,m,p=sr[k]; state[sid(m)]=(float(p),int(tt)); k+=1
        t=sid(e.sig_market_id); ts=int(e.ts); spl="TRAIN" if ts<ds-purge else "DEV" if ds<=ts<fs-purge else None
        if t in t2c and t in prev and spl:
            n=t2c[t]; c0=cs[n]; ids=list(c0["ids"]); A=c0["A"].copy()
            if shuffle and n=="GEORGIA_GOV_SENATE": ids=[ids[i] for i in [1,3,0,2]]
            if drop>=0 and n=="GEORGIA_GOV_SENATE": keep=[i for i in range(4) if i!=drop]; ids=[ids[i] for i in keep]; A=A[keep]
            if all(x in state for x in ids):
                p=np.array([state[x][0] for x in ids]); age=np.array([max(0,ts-state[x][1]) for x in ids],float); W=wgt(age,equal); c=c0["targets"][t]
                xs=proj(A,p,W,"qp"); q=A@xs
                lo,hi,tube=robust_bounds(A,p,c)
                xu=iproject(A,q); xc=iproject(A,q,category_prior(A))
                pv,pt=prev[t]; pw=float(wgt(np.array([max(0,ts-pt)]),equal)[0])
                xq=proj(A,p,W,"qp",c,pv,pw); xk=proj(A,p,W,"kl",c,pv,pw)
                out.append({"component":n,"sig_market_id":t,"split":spl,"block_number":b,"ts":ts,"prev":pv,"y":float(e.y),
                  "lp_lower":lo,"lp_upper":hi,"coherence_tube":tube,"qp":float(c@xq),"kl":float(c@xk),
                  "maxent":float(c@xu),"maxent_category":float(c@xc),
                  "n_sources":len(ids),"mean_age_h":float(age.mean()/3600),"max_age_h":float(age.max()/3600)})
        prev[t]=(float(e.y),ts)
    return pd.DataFrame(out)

def cluster_bootstrap(g,col,draws=1000):
    if g.empty:return (np.nan,np.nan,np.nan)
    x=g.copy(); x["imp"]=np.abs(x.y-x.prev)-np.abs(x.y-x[col])
    groups=[(float(z.imp.sum()),len(z)) for _,z in x.groupby("sig_market_id")]
    if not groups:return (np.nan,np.nan,np.nan)
    sums=np.array([a for a,_ in groups],float); counts=np.array([b for _,b in groups],float)
    rng=np.random.default_rng(SEED+len(g)+len(groups)); vals=np.empty(draws)
    for i in range(draws):
        j=rng.integers(0,len(groups),size=len(groups))
        vals[i]=sums[j].sum()/counts[j].sum()
    return tuple(float(np.quantile(vals,q)) for q in (0.025,0.5,0.975))
def point_summary(df,col):
    rows=[]
    if df.empty:return rows
    for (comp,t,spl),g in df.groupby(["component","sig_market_id","split"]):
        y=g.y.to_numpy(); p=g.prev.to_numpy(); z=g[col].to_numpy(); be=np.abs(y-p); me=np.abs(y-z); imp=be-me
        rho=float(spearmanr(z-p,y-p).statistic) if len(g)>=3 else np.nan
        rows.append({"component":comp,"sig_market_id":t,"split":spl,"method":col,"events":len(g),"sig_markets":1,
          "baseline_mae":be.mean(),"method_mae":me.mean(),"abs_improvement":imp.mean(),
          "rel_improvement":imp.mean()/be.mean() if be.mean() else np.nan,"rmse":np.sqrt(np.mean((y-z)**2)),
          "residual_spearman":rho,"mean_source_age_h":g.mean_age_h.mean(),
          "bootstrap_lower_95":np.nan,"bootstrap_median":np.nan,"bootstrap_upper_95":np.nan,
          "first_half_abs_improvement":np.nan,"second_half_abs_improvement":np.nan})
    for (comp,spl),g in df.groupby(["component","split"]):
        y=g.y.to_numpy(); p=g.prev.to_numpy(); z=g[col].to_numpy(); be=np.abs(y-p); me=np.abs(y-z); imp=be-me
        rho=float(spearmanr(z-p,y-p).statistic) if len(g)>=3 else np.nan
        lo,med,hi=cluster_bootstrap(g,col)
        ordered=g.sort_values(["block_number","sig_market_id"]); cut=max(1,len(ordered)//2)
        a=ordered.iloc[:cut]; b=ordered.iloc[cut:]
        fh=float((np.abs(a.y-a.prev)-np.abs(a.y-a[col])).mean()) if len(a) else np.nan
        sh=float((np.abs(b.y-b.prev)-np.abs(b.y-b[col])).mean()) if len(b) else np.nan
        rows.append({"component":comp,"sig_market_id":"ALL","split":spl,"method":col,"events":len(g),
          "sig_markets":int(g.sig_market_id.nunique()),"baseline_mae":be.mean(),"method_mae":me.mean(),
          "abs_improvement":imp.mean(),"rel_improvement":imp.mean()/be.mean() if be.mean() else np.nan,
          "rmse":np.sqrt(np.mean((y-z)**2)),"residual_spearman":rho,"mean_source_age_h":g.mean_age_h.mean(),
          "bootstrap_lower_95":lo,"bootstrap_median":med,"bootstrap_upper_95":hi,
          "first_half_abs_improvement":fh,"second_half_abs_improvement":sh})
    return rows
def bound_summary(df):
    rows=[]
    if df.empty:return rows
    for (comp,t,spl),g in df.groupby(["component","sig_market_id","split"]):
        wd=g.lp_upper-g.lp_lower; inside=(g.y>=g.lp_lower-1e-9)&(g.y<=g.lp_upper+1e-9)
        rows.append({"component":comp,"sig_market_id":t,"split":spl,"events":len(g),"mean_bound_width":wd.mean(),
          "median_bound_width":wd.median(),"coverage_of_direct_repricing":inside.mean(),"width_reduction_vs_unconstrained":1-wd.mean(),
          "mean_minimal_uniform_coherence_tube":g.coherence_tube.mean(),
          "binding_constraints":"raw asynchronous source prices within the minimal uniform coherence tube plus simplex"})
    return rows
def method_mae(df,col):
    if df.empty:return None
    g=df[df.split=="DEV"]
    if g.empty:return None
    return {"events":len(g),"baseline_mae":float(np.mean(np.abs(g.y-g.prev))),"method_mae":float(np.mean(np.abs(g.y-g[col])))}

def latest_lp_certificates(s,cs):
    latest={}
    for x in s.itertuples(index=False):
        latest[sid(x.market_id)]=(float(x.p),int(x.ts))
    out=[]
    for name,c0 in cs.items():
        ids=list(c0["ids"]); A=c0["A"]
        if not all(x in latest for x in ids): continue
        ts=max(latest[x][1] for x in ids)
        p=np.array([latest[x][0] for x in ids])
        age=np.array([max(0,ts-latest[x][1]) for x in ids],float)
        xs=proj(A,p,wgt(age),"qp"); q=A@xs
        targets=dict(c0["targets"])
        if name=="REPUBLICAN_GOV_COUNT":
            targets["P_K_GE_26"]=np.array([float(k>=26) for k in range(51)])
        for target,c in targets.items():
            rlo,rhi,tube=robust_bounds(A,p,c); width=rhi-rlo; iv=[]
            elo,ehi,ld,ud=lp_detail(A,q,c)
            for j,mid in enumerate(ids):
                keep=[i for i in range(len(ids)) if i!=j]
                l2,u2=bounds_at_delta(A[keep,:],p[keep],c,tube)
                iv.append({"source_id":mid,"leave_one_width":u2-l2,
                           "information_value":max(0.0,(u2-l2)-width),
                           "ambiguity_radius_held_fixed":tube})
            lower_value=float(q@ld[:-1]+ld[-1])
            upper_value=float(q@ud[:-1]+ud[-1])
            out.append({"component":name,"target":target,"asof_epoch":ts,
              "lower":rlo,"upper":rhi,"width":width,
              "minimal_uniform_coherence_tube":tube,
              "family_information_value":max(0.0,1.0-width),
              "information_value_definition":"leave-one-source width increase at fixed full-surface ambiguity radius",
              "reconciled_surface_lower":elo,"reconciled_surface_upper":ehi,
              "lower_dual_value":lower_value,"upper_dual_value":upper_value,
              "lower_dual_gap":elo-lower_value,"upper_dual_gap":upper_value-ehi,
              "dual_certificate_scope":"conditional on freshness-weighted QP reconciled source surface",
              "lower_active_sources":[{"source_id":ids[i],"weight":float(ld[i])}
                 for i in range(len(ids)) if abs(ld[i])>1e-8],
              "upper_active_sources":[{"source_id":ids[i],"weight":float(ud[i])}
                 for i in range(len(ids)) if abs(ud[i])>1e-8],
              "normalization_dual_lower":float(ld[-1]),
              "normalization_dual_upper":float(ud[-1]),
              "contract_information_value":iv})
    return out


def logit01(p):
    p=float(np.clip(p,1e-6,1-1e-6)); return math.log(p/(1-p))
def sigmoid01(z):
    z=float(np.clip(z,-40,40)); return 1.0/(1.0+math.exp(-z))
def pb_pmf(ps):
    out=np.array([1.0])
    for p in ps: out=np.convolve(out,np.array([1-p,p]))
    return out
def gov_bucket_pmf(pmf):
    return np.array([pmf[:22].sum(),pmf[22:24].sum(),pmf[24:26].sum(),pmf[26:28].sum(),
                     pmf[28:30].sum(),pmf[30:32].sum(),pmf[32:].sum()],float)
def cat_kl(obs,model):
    o=np.clip(np.asarray(obs,float),EPS,1); m=np.clip(np.asarray(model,float),EPS,1)
    return float(np.sum(o*np.log(o/m)))
def fit_gov_delta(priors,u0,q7):
    vals=[float(x) for x in priors]
    U=max(0,50-len(vals))
    def obj(d):
        ps=[sigmoid01(logit01(p)+d) for p in vals]+[sigmoid01(logit01(u0)+d)]*U
        return cat_kl(q7,gov_bucket_pmf(pb_pmf(ps)))
    # One-dimensional bounded golden-section search. Same likelihood/objective as
    # V7, but deterministic and much cheaper than unconstrained Nelder-Mead.
    lo,hi=-8.0,8.0
    phi=(1.0+math.sqrt(5.0))/2.0
    c=hi-(hi-lo)/phi; d=lo+(hi-lo)/phi
    fc=obj(c); fd=obj(d)
    for _ in range(32):
        if fc<fd:
            hi=d; d=c; fd=fc
            c=hi-(hi-lo)/phi; fc=obj(c)
        else:
            lo=c; c=d; fc=fd
            d=lo+(hi-lo)/phi; fd=obj(d)
    delta=0.5*(lo+hi)
    return float(delta),float(obj(delta))
def governor_targets(root):
    b=graph_bundle(root); a=pd.read_csv(b/"ETS_SIG_ANCHOR_GRAPH.csv",low_memory=False)
    out=set()
    for r in a.itertuples(index=False):
        sid0=sid(getattr(r,"sig_market_id",""))
        qn=str(getattr(r,"sig_question",getattr(r,"question",""))).lower()
        if "republican" in qn and "governor" in qn and ("win" in qn or "race" in qn):
            out.add(sid0)
    return out
def build_gov_factor_events(d,s,sp,root,cs):
    targets=governor_targets(root); gc=cs["REPUBLICAN_GOV_COUNT"]; ids=gc["ids"]; A=gc["A"]
    ds=int(sp["dev_start_epoch"]); fs=int(sp["final_start_epoch"]); purge=int(sp["selection_rule"]["boundary_purge_seconds"])
    sr=list(s[s.market_id.isin(ids)][["block_number","ts","market_id","p"]].itertuples(index=False,name=None))
    state={}; k=0; prev={}; rows=[]; last_source_by_target={}
    for e in d.itertuples(index=False):
        b=int(e.block_number)
        while k<len(sr) and int(sr[k][0])<b:
            bb,tt,m,p=sr[k]; state[sid(m)]=(float(p),int(tt),int(bb)); k+=1
        t=sid(e.sig_market_id); ts=int(e.ts)
        spl="TRAIN" if ts<ds-purge else "DEV" if ds<=ts<fs-purge else None
        if spl and t in targets and t in prev and all(x in state for x in ids):
            source_latest_block=max(state[x][2] for x in ids)
            # One observation per genuinely new national count surface for each
            # target. Repeated target ticks under the same source state are not
            # independent structural evidence.
            if last_source_by_target.get(t)==source_latest_block:
                prev[t]=(float(e.y),ts)
                continue
            raw=np.array([state[x][0] for x in ids],float)
            age=np.array([max(0,ts-state[x][1]) for x in ids],float)
            xs=proj(A,raw,wgt(age),"qp"); q7=A@xs
            known_all={kk:float(vv[0]) for kk,vv in prev.items() if kk in targets}
            known={kk:v for kk,v in known_all.items() if kk!=t}
            ccount=np.arange(51,dtype=float)
            mean_lo,mean_hi,_=robust_bounds(A,raw,ccount)
            others=sum(known.values()); U=max(0,49-len(known))
            hard_lo=max(0.0,mean_lo-others-U); hard_hi=min(1.0,mean_hi-others)
            rows.append({"split":spl,"sig_market_id":t,"block_number":b,"ts":ts,
                         "source_latest_block":source_latest_block,
                         "prev":float(prev[t][0]),"y":float(e.y),
                         "q7_json":json.dumps([float(x) for x in q7]),"priors_json":json.dumps(known,sort_keys=True),
                         "known_governor_races":len(known),"unknown_other_governor_races":U,
                         "hard_lower":hard_lo,"hard_upper":hard_hi,"mean_count_lower":mean_lo,"mean_count_upper":mean_hi,
                         "mean_source_age_h":float(age.mean()/3600),"max_source_age_h":float(age.max()/3600)})
            last_source_by_target[t]=source_latest_block
        prev[t]=(float(e.y),ts)
    return pd.DataFrame(rows),targets
def choose_gov_u0(train):
    if train.empty:return 0.5,{}
    x=train.drop_duplicates("block_number").sort_values("block_number")
    if len(x)>140:x=x.iloc[np.linspace(0,len(x)-1,140).astype(int)]
    scores={}
    for u0 in (0.2,0.3,0.4,0.5,0.6,0.7,0.8):
        vals=[]
        for r in x.itertuples(index=False):
            pri=list(json.loads(r.priors_json).values()); q7=np.array(json.loads(r.q7_json),float)
            if pri:
                _,loss=fit_gov_delta(pri,u0,q7); vals.append(loss)
        scores[str(u0)]=float(np.mean(vals)) if vals else None
    good=[(float(k),v) for k,v in scores.items() if v is not None]
    return (min(good,key=lambda z:z[1])[0] if good else 0.5),scores
def apply_gov_factor(df,u0):
    x=df.copy(); loo=[]; blend=[]; deltas=[]; blend_deltas=[]; losses=[]; blend_losses=[]
    for r in x.itertuples(index=False):
        pri=json.loads(r.priors_json); vals=[float(pri[k]) for k in sorted(pri)]
        q7=np.array(json.loads(r.q7_json),float)
        dlt,loss=fit_gov_delta(vals,u0,q7)
        loo.append(sigmoid01(logit01(u0)+dlt)); deltas.append(dlt); losses.append(loss)
        d2,l2=fit_gov_delta(vals+[float(r.prev)],u0,q7)
        blend.append(sigmoid01(logit01(float(r.prev))+d2)); blend_deltas.append(d2); blend_losses.append(l2)
    x["factor_loo_pred"]=loo; x["factor_direct_prior_pred"]=blend
    x["factor_delta"]=deltas; x["factor_direct_prior_delta"]=blend_deltas
    x["factor_bucket_kl"]=losses; x["factor_direct_prior_bucket_kl"]=blend_losses
    return x
def gov_factor_summary(df,u0,scores,target_count):
    if df.empty:return {"status":"NO_SUPPORT","method":"GOV_ONE_FACTOR_EXACT_POISSON_BINOMIAL","train_u0":u0,"u0_scores":scores}
    g=df[df.split=="DEV"]
    if g.empty:return {"status":"NO_DEV","method":"GOV_ONE_FACTOR_EXACT_POISSON_BINOMIAL","train_u0":u0,"u0_scores":scores}
    be=np.abs(g.y-g.prev); me=np.abs(g.y-g.factor_loo_pred); imp=be-me
    blend=np.abs(g.y-g.factor_direct_prior_pred)
    lo,med,hi=cluster_bootstrap(g,"factor_loo_pred")
    ordered=g.sort_values(["block_number","sig_market_id"]); cut=max(1,len(ordered)//2)
    a=ordered.iloc[:cut]; b=ordered.iloc[cut:]
    fh=float((np.abs(a.y-a.prev)-np.abs(a.y-a.factor_loo_pred)).mean()) if len(a) else None
    sh=float((np.abs(b.y-b.prev)-np.abs(b.y-b.factor_loo_pred)).mean()) if len(b) else None
    return {"status":"EVALUATED","method":"GOV_ONE_FACTOR_EXACT_POISSON_BINOMIAL_LOO","events":int(len(g)),
            "sig_markets":int(g.sig_market_id.nunique()),"governor_target_count":int(target_count),
            "baseline_mae":float(be.mean()),"method_mae":float(me.mean()),"abs_improvement":float(imp.mean()),
            "rel_improvement":float(imp.mean()/be.mean()) if be.mean() else None,
            "bootstrap_lower_95":lo,"bootstrap_median":med,"bootstrap_upper_95":hi,
            "first_half_abs_improvement":fh,"second_half_abs_improvement":sh,
            "direct_prior_blend_mae":float(blend.mean()),
            "inside_hard_interval_fraction":float(((g.factor_loo_pred>=g.hard_lower-1e-9)&(g.factor_loo_pred<=g.hard_upper+1e-9)).mean()),
            "mean_hard_interval_width":float((g.hard_upper-g.hard_lower).mean()),
            "train_selected_unobserved_base_probability":float(u0),"train_u0_grid_scores":scores,
            "assumptions":"primary LOO prediction removes the target direct prior; single common Republican-governor logit factor; conditional Bernoulli independence; target and unobserved races exchangeable at TRAIN-selected base probability; exact Poisson-binomial count likelihood"}

def main():
    sp,spp=split_load()
    final=int(sp["final_start_epoch"])
    cs=components()
    ids=set(cs["REPUBLICAN_GOV_COUNT"]["ids"])

    r=root4()
    man=json.loads((r/"MANIFEST.json").read_text())
    qual=json.loads((r/"QUALITY.json").read_text())
    assert man["version"]=="v2" and man["status"]=="ACCEPTED_V2"
    assert qual["all_gates_pass"] is True

    # DATA-003 remains the target/direct chronology. DATA-004 P0/P1 supplies
    # the structural national governor-count surface. DATA-001 is not touched.
    d=direct_rows(final)
    s=source_rows(r,final,ids)
    assert not (d.ts>=final).any() and not (s.ts>=final).any()

    # Cheap identification object for the count surface itself.
    gc=cs["REPUBLICAN_GOV_COUNT"]
    ident={
      "component":"REPUBLICAN_GOV_COUNT",
      "source_ids":gc["ids"],
      "latent_states":gc["states"],
      "observed_categories":int(gc["A"].shape[0]),
      "rank":int(np.linalg.matrix_rank(gc["A"])),
      "rank_with_norm":int(np.linalg.matrix_rank(np.vstack([gc["A"],np.ones((1,gc["A"].shape[1]))]))),
      "external_identification":gc["external"],
    }
    (OUT/"COMPONENT_IDENTIFICATION.csv").write_text(
        pd.DataFrame([ident]).to_csv(index=False)
    )

    # Latest aggregate LP diagnostic only. Do not rerun the already-settled
    # Georgia / Senate-House point-FV families.
    latest={}
    for x in s[s.market_id.isin(gc["ids"])].itertuples(index=False):
        latest[sid(x.market_id)]=(float(x.p),int(x.ts))
    aggregate={}
    if all(x in latest for x in gc["ids"]):
        ts=max(v[1] for v in latest.values())
        p=np.array([latest[x][0] for x in gc["ids"]])
        age=np.array([ts-latest[x][1] for x in gc["ids"]])
        x=proj(gc["A"],p,wgt(age),"qp")
        q7=gc["A"]@x
        mean_lo,mean_hi,tube=robust_bounds(
            gc["A"],p,np.arange(51,dtype=float)
        )
        aggregate={
          "asof_epoch":int(ts),
          "reconciled_bucket_probabilities":[float(v) for v in q7],
          "p_k_ge_26_reconciled":float(q7[3:].sum()),
          "expected_k_lower":mean_lo,
          "expected_k_upper":mean_hi,
          "minimal_uniform_coherence_tube":tube,
        }

    gov_events_all,gov_target_ids=build_gov_factor_events(d,s,sp,r,cs)
    gov_events=(
        gov_events_all[gov_events_all.max_source_age_h<=24].copy()
        if len(gov_events_all) else gov_events_all
    )
    train_gov=gov_events[gov_events.split=="TRAIN"] if len(gov_events) else gov_events

    gov_u0,gov_u0_scores=choose_gov_u0(train_gov)
    gov_factor=apply_gov_factor(gov_events,gov_u0) if len(gov_events) else gov_events
    gov_result=gov_factor_summary(
        gov_factor,gov_u0,gov_u0_scores,len(gov_target_ids)
    )
    if len(gov_factor):
        gov_factor.to_csv(OUT/"GOV_FACTOR_EVENT_RESULTS.csv",index=False)

    # This is a challenger, not a rescue search. A promotion requires positive
    # cluster-bootstrap lower bound, stable chronological halves, >= 200 DEV
    # episodes, and at least 10 SIG governor targets.
    promote=bool(
        gov_result.get("status")=="EVALUATED"
        and gov_result.get("events",0)>=200
        and gov_result.get("sig_markets",0)>=10
        and gov_result.get("abs_improvement",0)>0
        and gov_result.get("bootstrap_lower_95",-1)>0
        and (gov_result.get("first_half_abs_improvement") or -1)>=0
        and (gov_result.get("second_half_abs_improvement") or -1)>=0
    )
    decision="PROMOTE_TO_R3_DISCOVERY" if promote else "DO_NOT_PROMOTE"

    comparison={
      "schema_version":1,
      "experiment_id":"R3-FV-001M-V9",
      "stage":"LEAN_GOVERNOR_INVERSE",
      "binding":{
        "target":"DATA-003 accepted current SIG-universe direct/reference chronology",
        "source":"DATA-004 P0/P1 ETS governor-count surface",
        "data001_used":False,
      },
      "final_opened":False,
      "final_rows_accessed":0,
      "governor_identification":ident,
      "governor_aggregate":aggregate,
      "governor_scalable_challenger":gov_result,
      "decision":decision,
      "duplicate_point_fv_families_rerun":False,
      "duplicate_point_fv_reason":"Parent R3 already closed QP/KL/MaxEnt/calibrated-ECM historical point-FV paths; V9 isolates only the distinct aggregate-to-constituent inverse.",
    }
    (OUT/"METHOD_COMPARISON.json").write_text(
        json.dumps(comparison,indent=2,sort_keys=True,default=str)+"\n"
    )
    pd.DataFrame([gov_result]).to_csv(
        OUT/"SCALABLE_CHALLENGER_RESULTS.csv",index=False
    )

    hand=f"""# R3-FV-001M V9 governor inverse handoff

**R3 FINAL accessed:** NO

**DATA-003 used:** YES (target/direct chronology)
**DATA-004 used:** YES (P0/P1 structural source)
**DATA-001 used:** NO

V9 deliberately does not rerun QP/KL/MaxEnt/ECM families already closed by parent R3.

Governor inverse result:
{json.dumps(gov_result,indent=2,default=str)}

Aggregate governor-count diagnostic:
{json.dumps(aggregate,indent=2,default=str)}

Decision: **{decision}**

The primary LOO model excludes the target's direct prior, uses a single common
Republican-governor logit factor, conditional Bernoulli independence, exact
Poisson-binomial count evaluation, and a TRAIN-selected exchangeable base
probability for the target/unobserved races. Observations are episode-native:
one row per genuinely new national count surface per target.

Any negative or unsupported result closes this aggregate-to-constituent
challenger for historical launch use. A positive result still requires the
strict gate above and does not reopen dead point-FV families.
"""
    (OUT/"R3_METHOD_HANDOFF.md").write_text(hand)

    print("R3_FV_001M_RESULT="+json.dumps({
      "status":"COMPLETE",
      "version":"V9_LEAN_GOVERNOR_INVERSE",
      "data003_used":True,
      "data004_p0p1_used":True,
      "data001_used":False,
      "final_opened":False,
      "train_episodes":int((gov_events.split=="TRAIN").sum()) if len(gov_events) else 0,
      "dev_episodes":int((gov_events.split=="DEV").sum()) if len(gov_events) else 0,
      "governor_targets":int(len(gov_target_ids)),
      "decision":decision,
      "result":gov_result,
    },sort_keys=True),flush=True)

if __name__=="__main__": main()
