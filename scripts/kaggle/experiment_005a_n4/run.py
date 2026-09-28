# ruff: noqa: E501,E701,E702
from __future__ import annotations

import csv
import hashlib
import json
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.dataset as pads
import pyarrow.parquet as pq

INPUT = Path("/kaggle/input")
WORK = Path("/kaggle/working/005a_n4_participant_null")
WORK.mkdir(parents=True, exist_ok=True)

MASTER_SEED = 20260928005
DRAWS = 999
NS = 1_000_000_000
UTC_US = pa.timestamp("us", tz="UTC")
PRIMARY_EVENTS = ("colombia_first_round", "peru_runoff", "colombia_runoff")
REGIMES = ("PRE_ELECTION", "ACTIVE_RESULTS")
EVENT_FAMILY = {
    "colombia_first_round": "COL_2026",
    "peru_runoff": "PER_2026",
    "colombia_runoff": "COL_2026",
}
A2_BASE = (
    "own_price_move_t_minus_30_to_t", "midpoint_t", "spread_t", "quote_age_t",
    "abs_own_price_move_300s", "genuine_bbo_changes_30s", "fill_count_30s",
    "unsigned_fill_value_30s", "common_event_move_ex_target",
)
A2_CHALLENGER = ("signed_taker_value_30s", "signed_taker_shares_30s")
A4_BASE = A2_BASE + A2_CHALLENGER
A4_CHALLENGER = ("participant_conditioned_value_30s", "participant_conditioned_shares_30s")


def sha256(path: Path) -> str:
    d=hashlib.sha256()
    with path.open("rb") as h:
        for chunk in iter(lambda:h.read(1<<20),b""): d.update(chunk)
    return d.hexdigest()


def seed(component: str, draw: int) -> int:
    d=hashlib.sha256(f"{MASTER_SEED}|{component}|{draw}".encode()).digest()
    return int.from_bytes(d[:8],"big")


def dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z","+00:00"))


def ns(value: datetime) -> int:
    return int(value.timestamp()*NS)


def read_csv(path: Path) -> list[dict[str,str]]:
    with path.open(newline="",encoding="utf-8") as h: return list(csv.DictReader(h))


def write_csv(path: Path, rows: list[dict[str,Any]]) -> None:
    fields=[]
    for row in rows:
        for k in row:
            if k not in fields: fields.append(k)
    with path.open("w",newline="",encoding="utf-8") as h:
        w=csv.DictWriter(h,fieldnames=fields,lineterminator="\n")
        w.writeheader(); w.writerows(rows)


def manifests() -> dict[str,tuple[Path,dict[str,Any]]]:
    result={}
    for p in INPUT.rglob("run_manifest.json"):
        try: m=json.loads(p.read_text())
        except Exception: continue
        stage=m.get("stage")
        if stage in {"A2_A3_OBSERVED_AND_PANELS","A4_PARTICIPANT_OBSERVED_AND_PANELS"}:
            if stage in result: raise RuntimeError(f"duplicate manifest stage {stage}")
            result[stage]=(p.parent,m)
    return result


def load_bundle() -> tuple[Path,dict[str,Any]]:
    matches=sorted(INPUT.rglob("005a_a45_code_manifest.json"))
    if len(matches)!=1: raise RuntimeError(f"expected one code manifest, got {matches}")
    p=matches[0]; root=p.parent; m=json.loads(p.read_text())
    for rel,expected in m["files"].items():
        q=root/rel
        if not q.exists() or sha256(q)!=expected: raise RuntimeError(f"code bundle mismatch {rel}")
    sys.path.insert(0,str(root/"predictions_cup_005a.bundle"))
    return root,m


def locate_data001() -> tuple[Path,dict[str,Any]]:
    matches=[p for p in INPUT.rglob("corpus_manifest.json") if p.parent.name=="schema_version=1"]
    if len(matches)!=1: raise RuntimeError(f"expected one DATA001 manifest, got {matches}")
    p=matches[0]; return p.parent,json.loads(p.read_text())


def verify_data001(corpus: Path, manifest: dict[str,Any]) -> dict[str,int]:
    records={row["path"]:row for row in manifest["output_files"]}
    checked=rows=0
    for event in PRIMARY_EVENTS:
        for p in sorted((corpus/event).rglob("*.parquet")):
            rel=str(p.relative_to(corpus)); rec=records.get(rel)
            if rec is None or sha256(p)!=rec["sha256"]: raise RuntimeError(f"DATA001 mismatch {rel}")
            checked+=1; rows+=int(rec["rows"])
    return {"files_checked":checked,"rows_checked":rows}


def parquet_files(root: Path) -> list[str]:
    return [str(p) for p in sorted(root.rglob("*.parquet"))]


def table_for(root: Path,columns: list[str],*,tokens: list[str]|None,time_col: str,
              start: datetime,end: datetime) -> pa.Table:
    files=parquet_files(root)
    dataset=pads.dataset(files,format="parquet")
    expr=(pads.field(time_col)>=pa.scalar(start,UTC_US))&(pads.field(time_col)<pa.scalar(end,UTC_US))
    if tokens is not None: expr=expr&pads.field("token_id").isin(tokens)
    return dataset.to_table(columns=columns,filter=expr)


def load_bbo_assets(corpus: Path,event: str,tokens: list[str],start: datetime,end: datetime):
    from predictions_cup.learning.flow_response import reconstruct_genuine_bbo
    root=corpus/event/"books"/"book_changes"
    table=table_for(root,["token_id","observed_at","best_bid","best_ask"],
                    tokens=tokens,time_col="observed_at",start=start,end=end)
    grouped=defaultdict(list)
    for row in table.to_pylist(): grouped[str(row["token_id"])].append(row)
    recon={token:reconstruct_genuine_bbo(grouped.get(token,[])) for token in tokens}
    c=table_for(root,["observed_at"],tokens=None,time_col="observed_at",start=start,end=end)
    if c.num_rows:
        values=pc.cast(c["observed_at"],pa.int64()).to_numpy(zero_copy_only=False)
        collector=np.unique(np.asarray(values,np.int64)*1000)
    else: collector=np.zeros(0,np.int64)
    return recon,collector


def load_windows(code_root: Path):
    payload=json.loads((code_root/"regime_definitions.json").read_text()); out={}
    for event in payload["events"]:
        eid=str(event["regime_id"])
        if eid not in PRIMARY_EVENTS: continue
        out[eid]={}
        for r in event["regimes"]:
            if r["name"] in REGIMES: out[eid][r["name"]]=(dt(r["start_utc"]),dt(r["end_utc"]))
    return out


def fit_a4(frame: pd.DataFrame) -> dict[str,Any]:
    from predictions_cup.learning.flow_models import (
        chronological_split_by_group,
        fit_weighted_ridge,
        hierarchical_equal_weights,
        predict_ridge,
        weighted_mse,
    )
    features=A4_BASE+A4_CHALLENGER; yname="y_1s"
    mask=np.isfinite(frame[yname].to_numpy(float))
    for f in features: mask&=np.isfinite(frame[f].to_numpy(float))
    data=frame.loc[mask].copy()
    train,val=chronological_split_by_group(
        data["time_ns"].to_numpy(np.int64),data["event"].astype(str).to_numpy(object),
        train_fraction=2/3,embargo_ns=300*NS)
    tr=data.loc[train].copy(); va=data.loc[val].copy()
    tw=hierarchical_equal_weights([
        tr["event_family"].astype(str).to_numpy(object),tr["event"].astype(str).to_numpy(object),
        tr["block"].astype(str).to_numpy(object),tr["condition_id"].astype(str).to_numpy(object)])
    vw=hierarchical_equal_weights([
        va["event_family"].astype(str).to_numpy(object),va["event"].astype(str).to_numpy(object),
        va["block"].astype(str).to_numpy(object),va["condition_id"].astype(str).to_numpy(object)])
    x0=tr.loc[:,list(A4_BASE)].to_numpy(float); x1=tr.loc[:,list(features)].to_numpy(float)
    y=tr[yname].to_numpy(float)
    base=fit_weighted_ridge(x0,y,tw,feature_names=A4_BASE,alpha=1.0)
    chall=fit_weighted_ridge(x1,y,tw,feature_names=features,alpha=1.0)
    vx0=va.loc[:,list(A4_BASE)].to_numpy(float); vx1=va.loc[:,list(features)].to_numpy(float)
    vy=va[yname].to_numpy(float); p0=predict_ridge(base,vx0); p1=predict_ridge(chall,vx1)
    l0=weighted_mse(vy,p0,vw); l1=weighted_mse(vy,p1,vw)
    return {"data":data,"train":train,"validation":val,"validation_frame":va,
            "challenger_model":chall,"val_w":vw,"vy":vy,"baseline_mse":l0,
            "observed_gain":l0-l1,"baseline_model":base}


def aggregate_features(rows: list[dict[str,Any]], scores: np.ndarray, panel: pd.DataFrame,
                       excluded: frozenset[str]=frozenset()) -> tuple[np.ndarray,np.ndarray]:
    from predictions_cup.learning.role_markouts import aggregate_participant_conditioned_flow
    value=np.zeros(len(panel),float); shares=np.zeros(len(panel),float)
    for event in PRIMARY_EVENTS:
        for regime in REGIMES:
            local=np.asarray([i for i,r in enumerate(rows) if r["event"]==event and r["regime"]==regime],np.int64)
            if not len(local): continue
            local_rows=[rows[int(i)] for i in local]
            local_scores=scores[local].copy()
            if excluded:
                for j,r in enumerate(local_rows):
                    if str(r["participant"]).lower() in excluded: local_scores[j]=0.0
            submask=(panel["event"]==event)&(panel["regime"]==regime)
            sub=panel.loc[submask]
            grid=np.sort(sub["time_ns"].unique().astype(np.int64))
            cond=aggregate_participant_conditioned_flow(local_rows,local_scores,grid,lookback_seconds=30,role="TAKER")
            for condition_id,group in sub.groupby("condition_id",sort=False):
                idx=group.index.to_numpy(np.int64); series=cond.get(str(condition_id))
                if series is None: continue
                # Panel condition rows are a complete sorted decision grid.
                order=np.argsort(group["time_ns"].to_numpy(np.int64),kind="stable")
                if len(order)!=len(series["participant_conditioned_value"]):
                    raise RuntimeError("participant feature grid mismatch")
                value[idx[order]]=series["participant_conditioned_value"]
                shares[idx[order]]=series["participant_conditioned_shares"]
    return value,shares


def score_query_identities(history_rows: list[dict[str,Any]], query_rows: list[dict[str,Any]],
                           query_identities: np.ndarray) -> np.ndarray:
    from predictions_cup.learning.participant_role import shrink_mean
    order=np.argsort(np.asarray([int(r["label_available_ns"]) for r in history_rows],np.int64),kind="stable")
    available=np.asarray([int(history_rows[int(i)]["label_available_ns"]) for i in order],np.int64)
    query_order=np.argsort(np.asarray([int(r["decision_ns"]) for r in query_rows],np.int64),kind="stable")
    totals=defaultdict(float); counts=defaultdict(int); cursor=0; result=np.zeros(len(query_rows),float)
    for qi in query_order:
        t=int(query_rows[int(qi)]["decision_ns"]); cutoff=t-300*NS
        while cursor<len(order) and int(available[cursor])<cutoff:
            r=history_rows[int(order[cursor])]; outcome=float(r["owner_markout"])
            if np.isfinite(outcome):
                key=(str(r["participant"]).lower(),"TAKER"); totals[key]+=outcome; counts[key]+=1
            cursor+=1
        key=(str(query_identities[int(qi)]).lower(),"TAKER"); count=counts.get(key,0)
        if count>=20: result[int(qi)]=shrink_mean(totals.get(key,0.0),count,prior_mean=0.0,prior_count=20)
    return result


def query_null_features(query_rows: list[dict[str,Any]], scores: np.ndarray,
                        validation: pd.DataFrame) -> tuple[np.ndarray,np.ndarray]:
    value=np.zeros(len(validation),float); shares=np.zeros(len(validation),float)
    for (event,condition),group in validation.groupby(["event","condition_id"],sort=False):
        positions=np.asarray([i for i,r in enumerate(query_rows)
                              if r["event"]==event and str(r["condition_id"])==str(condition)],np.int64)
        if not len(positions): continue
        vals=group["time_ns"].to_numpy(np.int64); outpos=group.index.to_numpy(np.int64)
        order=np.argsort(vals,kind="stable"); vals=vals[order]; outpos=outpos[order]
        fill=np.asarray([int(query_rows[int(i)]["timestamp_seconds"])*NS for i in positions],np.int64)
        j=np.searchsorted(vals,fill,side="right")
        valid=(j<len(vals))
        cand=np.where(valid,j,0)
        valid&=fill>vals[cand]-30*NS
        for local,ok in enumerate(valid):
            if not ok: continue
            qi=int(positions[local]); target=int(outpos[int(j[local])])
            r=query_rows[qi]; sign=float(r["owner_yes_sign"])
            value[target]+=sign*float(r["value_usd"])*float(scores[qi])
            shares[target]+=sign*float(r["size_shares"])*float(scores[qi])
    return value,shares


def null_gain(fit: dict[str,Any], zvalue: np.ndarray,zshares: np.ndarray) -> float:
    from predictions_cup.learning.flow_models import predict_ridge, weighted_mse
    va=fit["validation_frame"]; model=fit["challenger_model"]
    x=va.loc[:,list(model.feature_names)].to_numpy(float)
    x[:,model.feature_names.index(A4_CHALLENGER[0])]=zvalue
    x[:,model.feature_names.index(A4_CHALLENGER[1])]=zshares
    p=predict_ridge(model,x)
    return float(fit["baseline_mse"]-weighted_mse(fit["vy"],p,fit["val_w"]))


def main() -> None:
    code_root,code_manifest=load_bundle(); stages=manifests()
    main_root,main_manifest=stages["A2_A3_OBSERVED_AND_PANELS"]
    a4_root,a4_manifest=stages["A4_PARTICIPANT_OBSERVED_AND_PANELS"]
    corpus,data001=locate_data001(); checks=verify_data001(corpus,data001)
    panel_path=a4_root/"panel_1s.parquet"
    meta=a4_manifest["panel_outputs"]["panel_1s.parquet"]
    if sha256(panel_path)!=meta["sha256"]: raise RuntimeError("A4 panel hash mismatch")
    panel=pq.read_table(panel_path).to_pandas()
    conditions_all=read_csv(code_root/"conditions.csv"); windows=load_windows(code_root)
    infra=json.loads((code_root/"infra_registry.json").read_text())
    excluded=frozenset(str(x).lower() for x in infra["infra_addresses"])
    from predictions_cup.learning.participant_role import (
        expanding_available_participant_role_scores,
        permute_participant_identity_within_strata,
    )
    from predictions_cup.learning.role_markouts import ConditionTarget, build_role_markouts

    rows=[]
    for event in PRIMARY_EVENTS:
        event_conditions=[r for r in conditions_all if r["event"]==event]
        tokens=sorted({r["token_id"] for r in event_conditions})
        start=min(windows[event][r][0] for r in REGIMES)-timedelta(seconds=301)
        end=max(windows[event][r][1] for r in REGIMES)+timedelta(seconds=601)
        recon,collector=load_bbo_assets(corpus,event,tokens,start,end)
        for regime in REGIMES:
            role_path=main_root/"role_enriched"/f"{event}__{regime}.parquet"
            rel=str(role_path.relative_to(main_root)); ometa=main_manifest["outputs"][rel]
            if sha256(role_path)!=ometa["sha256"]: raise RuntimeError(f"role output mismatch {rel}")
            role_table=pq.read_table(role_path)
            conds=[r for r in event_conditions if r["regime"]==regime]
            targets={r["condition_id"]:ConditionTarget(token_id=r["token_id"],market_family=r["market_family"])
                     for r in conds}
            local=build_role_markouts(role_table,targets,recon,collector,horizon_seconds=1,
                                      allowed_role_classes=frozenset({"TAKER_HIGH_CONFIDENCE"}),
                                      excluded_participants=excluded)
            for r in local:
                r["event"]=event; r["regime"]=regime; r["event_family"]=EVENT_FAMILY[event]
            rows.extend(local)

    times=np.asarray([int(r["decision_ns"]) for r in rows],np.int64)
    available=np.asarray([int(r["label_available_ns"]) for r in rows],np.int64)
    participants=np.asarray([str(r["participant"]) for r in rows],object)
    roles=np.asarray(["TAKER"]*len(rows),object)
    outcomes=np.asarray([float(r["owner_markout"]) for r in rows],float)
    actual_scores,_=expanding_available_participant_role_scores(
        times,available,participants,roles,outcomes,embargo_ns=300*NS,
        minimum_history=20,prior_count=20,prior_mean=0.0,neutral_fallback=0.0,
        excluded_participants=excluded)
    rebuilt_value,rebuilt_shares=aggregate_features(rows,actual_scores,panel)
    max_value=float(np.max(np.abs(rebuilt_value-panel[A4_CHALLENGER[0]].to_numpy(float))))
    max_shares=float(np.max(np.abs(rebuilt_shares-panel[A4_CHALLENGER[1]].to_numpy(float))))
    if max_value>1e-9 or max_shares>1e-9:
        raise RuntimeError(f"A4 feature reconstruction mismatch {max_value} {max_shares}")

    pre=panel[panel["regime"]=="PRE_ELECTION"].copy()
    fit=fit_a4(pre); observed=float(fit["observed_gain"])
    va=fit["validation_frame"].copy().reset_index(drop=True)
    fit["validation_frame"]=va
    # Query rows are fills capable of entering at least one finite validation 30s window.
    query=[]
    for r in rows:
        if r["regime"]!="PRE_ELECTION": continue
        ev=str(r["event"]); cid=str(r["condition_id"]); fill=int(r["timestamp_seconds"])*NS
        group=va[(va["event"]==ev)&(va["condition_id"].astype(str)==cid)]
        if group.empty: continue
        lo=int(group["time_ns"].min())-30*NS; hi=int(group["time_ns"].max())
        if fill>lo and fill<hi: query.append(r)

    start_ns={(event,regime):ns(windows[event][regime][0]) for event in PRIMARY_EVENTS for regime in REGIMES}
    strata=np.asarray([
        f"{r['event']}|{r['regime']}|{r['market_family']}|"
        f"{(int(r['decision_ns'])-start_ns[(r['event'],r['regime'])])//(300*NS)}|TAKER"
        for r in query],object)
    qparticipants=np.asarray([str(r["participant"]) for r in query],object)
    nulls=[]; draw_rows=[]
    for d in range(DRAWS):
        perm=permute_participant_identity_within_strata(qparticipants,strata,
                seed=seed("A4|PRE_ELECTION|1s|identity",d))
        qscores=score_query_identities(rows,query,perm)
        zval,zshares=query_null_features(query,qscores,va)
        gain=null_gain(fit,zval,zshares); nulls.append(gain)
        draw_rows.append({"draw":d,"loss_gain":gain})

    pval=float((1+np.sum(np.asarray(nulls)>=observed))/(DRAWS+1)) if observed>0 else 1.0
    from predictions_cup.learning.flow_models import benjamini_hochberg
    p_family=np.ones(10,float); p_family[0]=pval
    q_family=benjamini_hochberg(p_family); q=float(q_family[0])

    volume=defaultdict(float)
    for r in rows: volume[str(r["participant"]).lower()]+=float(r["value_usd"])
    ranked=sorted(volume,key=lambda k:(-volume[k],k))
    ablations=[]
    sets=[("TOP_1",frozenset(ranked[:1])),("TOP_5",frozenset(ranked[:5])),
          ("TOP_10",frozenset(ranked[:10]))]
    sets += [(f"ONE_{i+1}",frozenset({wallet})) for i,wallet in enumerate(ranked[:10])]
    for label,excluded_set in sets:
        v,s=aggregate_features(rows,actual_scores,panel,excluded=excluded_set)
        modified=panel.copy(); modified[A4_CHALLENGER[0]]=v; modified[A4_CHALLENGER[1]]=s
        f=fit_a4(modified[modified["regime"]=="PRE_ELECTION"])
        ablations.append({"ablation":label,"wallets_removed":len(excluded_set),
                          "loss_gain":float(f["observed_gain"]),
                          "fraction_of_observed":float(f["observed_gain"]/observed) if observed!=0 else np.nan})

    summary=[{"mechanism":"A4_PARTICIPANT","regime":"PRE_ELECTION","horizon_seconds":1,
              "observed_gain":observed,"identity_p":pval,"bh_q":q,
              "bh_reject_5pct":bool(q<=0.05 and observed>0),
              "null_mean":float(np.mean(nulls)),"null_median":float(np.median(nulls)),
              "query_fill_rows":len(query),"history_fill_rows":len(rows),
              "feature_reconstruction_max_abs_value":max_value,
              "feature_reconstruction_max_abs_shares":max_shares}]
    for regime in REGIMES:
        for horizon in (1,5,30,60,300):
            if regime=="PRE_ELECTION" and horizon==1: continue
            summary.append({"mechanism":"A4_PARTICIPANT","regime":regime,"horizon_seconds":horizon,
                            "observed_gain":"NONPOSITIVE_STAGE1","identity_p":1.0,"bh_q":1.0,
                            "bh_reject_5pct":False})

    write_csv(WORK/"participant_null_summary.csv",summary)
    write_csv(WORK/"identity_null_draws.csv",draw_rows)
    write_csv(WORK/"concentration_ablations.csv",ablations)
    manifest={"experiment_id":"EXPERIMENT-005A","stage":"N4_PARTICIPANT_IDENTITY_NULL",
              "master_seed":MASTER_SEED,"draws":DRAWS,"data001":checks,
              "main_stage1_manifest_sha256":sha256(main_root/"run_manifest.json"),
              "a4_stage1_manifest_sha256":sha256(a4_root/"run_manifest.json"),
              "a4_stage1_implementation_commit":a4_manifest["code_manifest"]["implementation_commit"],
              "code_bundle_implementation_commit":code_manifest["implementation_commit"],
              "outputs":{
                  "participant_null_summary.csv":sha256(WORK/"participant_null_summary.csv"),
                  "identity_null_draws.csv":sha256(WORK/"identity_null_draws.csv"),
                  "concentration_ablations.csv":sha256(WORK/"concentration_ablations.csv")}}
    (WORK/"run_manifest.json").write_text(json.dumps(manifest,indent=2,sort_keys=True)+"\n")
    print(json.dumps({"status":"COMPLETE","observed_gain":observed,"identity_p":pval,"bh_q":q,
                      "query_rows":len(query),"feature_rebuild_max":max(max_value,max_shares)},sort_keys=True))


if __name__=="__main__":
    main()
