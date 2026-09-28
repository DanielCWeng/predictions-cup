# ruff: noqa: E501,E701,E702
from __future__ import annotations

import csv
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

INPUT = Path("/kaggle/input")
WORK = Path("/kaggle/working/005a_n5_maker_liquidity_nulls")
WORK.mkdir(parents=True, exist_ok=True)

MASTER_SEED = 20260928005
DRAWS = 999
NS = 1_000_000_000
HORIZONS = (1, 5, 30, 60, 300)
REGIMES = ("PRE_ELECTION", "ACTIVE_RESULTS")

A2_BASE = (
    "own_price_move_t_minus_30_to_t", "midpoint_t", "spread_t", "quote_age_t",
    "abs_own_price_move_300s", "genuine_bbo_changes_30s", "fill_count_30s",
    "unsigned_fill_value_30s", "common_event_move_ex_target",
)
A2_CHALLENGER = ("signed_taker_value_30s", "signed_taker_shares_30s")
MAKER_BASE = ("midpoint_t", "spread_t", "quote_age_t", "abs_price_move_300s", "total_depth_t")
MAKER_CHALLENGER = (
    "signed_taker_value_30s", "signed_taker_shares_30s",
    "participant_conditioned_taker_value_30s", "family_activity_ex_target_30s",
)
PRIMARY_RESPONSES = (
    "future_genuine_change_count", "future_spread_change", "future_total_depth_change",
)
PLACEBO_RESPONSES = (
    "future_raw_record_count", "future_repeated_unchanged_count", "future_depth_snapshot_count",
)


def sha256(path: Path) -> str:
    d = hashlib.sha256()
    with path.open("rb") as h:
        for chunk in iter(lambda: h.read(1 << 20), b""):
            d.update(chunk)
    return d.hexdigest()


def seed(component: str, draw: int) -> int:
    d = hashlib.sha256(f"{MASTER_SEED}|{component}|{draw}".encode()).digest()
    return int.from_bytes(d[:8], "big")


def one(name: str) -> Path:
    matches = sorted(INPUT.rglob(name))
    if len(matches) != 1:
        raise RuntimeError(f"expected exactly one {name}, got {matches}")
    return matches[0]


def load_bundle() -> dict[str, Any]:
    p = one("005a_a45_code_manifest.json")
    root = p.parent
    m = json.loads(p.read_text())
    for rel, expected in m["files"].items():
        q = root / rel
        if not q.exists() or sha256(q) != expected:
            raise RuntimeError(f"code bundle mismatch: {rel}")
    sys.path.insert(0, str(root / "predictions_cup_005a.bundle"))
    return m


def locate_stage1() -> tuple[Path, dict[str, Any]]:
    p = one("run_manifest.json")
    m = json.loads(p.read_text())
    if m.get("stage") != "A5_MAKER_LIQUIDITY_OBSERVED_AND_PANELS":
        raise RuntimeError(f"wrong A5 manifest: {m.get('stage')}")
    root = p.parent
    for name, meta in m["maker_panel_outputs"].items():
        q = root / name
        if not q.exists() or q.stat().st_size != int(meta["bytes"]) or sha256(q) != meta["sha256"]:
            raise RuntimeError(f"maker panel mismatch: {name}")
    for name, meta in m["liquidity_panel_outputs"].items():
        q = root / name
        if not q.exists() or q.stat().st_size != int(meta["bytes"]) or sha256(q) != meta["sha256"]:
            raise RuntimeError(f"liquidity panel mismatch: {name}")
    return root, m


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fields: list[str] = []
    for row in rows:
        for k in row:
            if k not in fields:
                fields.append(k)
    with path.open("w", newline="", encoding="utf-8") as h:
        w = csv.DictWriter(h, fieldnames=fields, lineterminator="\\n")
        w.writeheader()
        w.writerows(rows)


def finite_frame(frame: pd.DataFrame, features: tuple[str, ...], y_name: str) -> pd.DataFrame:
    mask = np.isfinite(frame[y_name].to_numpy(float))
    for f in features:
        mask &= np.isfinite(frame[f].to_numpy(float))
    return frame.loc[mask].copy()


def fit_cell(frame: pd.DataFrame, *, base_features: tuple[str, ...], challenger_features: tuple[str, ...],
             y_name: str, entity_column: str) -> dict[str, Any]:
    from predictions_cup.learning.flow_models import (
        chronological_split_by_group,
        fit_weighted_ridge,
        hierarchical_equal_weights,
        predict_ridge,
        weighted_mse,
    )
    features = base_features + challenger_features
    data = finite_frame(frame, features, y_name)
    groups = (data["event"].astype(str) + "|" + data["regime"].astype(str)).to_numpy(object)
    train, validation = chronological_split_by_group(
        data["time_ns"].to_numpy(np.int64), groups, train_fraction=2/3, embargo_ns=300*NS
    )
    if train.sum() < 5 or validation.sum() < 5:
        return {"status": "COVERAGE_LIMITED", "rows": len(data)}
    tr, va = data.loc[train].copy(), data.loc[validation].copy()
    tw = hierarchical_equal_weights([
        tr["event_family"].astype(str).to_numpy(object), tr["event"].astype(str).to_numpy(object),
        tr["block"].astype(str).to_numpy(object), tr[entity_column].astype(str).to_numpy(object),
    ])
    vw = hierarchical_equal_weights([
        va["event_family"].astype(str).to_numpy(object), va["event"].astype(str).to_numpy(object),
        va["block"].astype(str).to_numpy(object), va[entity_column].astype(str).to_numpy(object),
    ])
    x0 = tr.loc[:, list(base_features)].to_numpy(float)
    x1 = tr.loc[:, list(features)].to_numpy(float)
    y = tr[y_name].to_numpy(float)
    base = fit_weighted_ridge(x0, y, tw, feature_names=base_features, alpha=1.0)
    challenger = fit_weighted_ridge(x1, y, tw, feature_names=features, alpha=1.0)
    vx0 = va.loc[:, list(base_features)].to_numpy(float)
    vx1 = va.loc[:, list(features)].to_numpy(float)
    vy = va[y_name].to_numpy(float)
    p0, p1 = predict_ridge(base, vx0), predict_ridge(challenger, vx1)
    l0, l1 = weighted_mse(vy, p0, vw), weighted_mse(vy, p1, vw)
    return {
        "status":"OK", "data":data, "validation":validation, "validation_frame":va,
        "challenger_model":challenger, "val_w":vw, "vy":vy, "baseline_mse":l0,
        "challenger_mse":l1, "observed_gain":l0-l1,
    }


def source_index(validation: pd.DataFrame, features: tuple[str, ...]) -> dict[str, Any]:
    keys = ["event", "condition_id", "time_ns"]
    grouped = validation.groupby(keys, sort=False, dropna=False)
    for f in features:
        spread = grouped[f].agg(lambda x: float(np.nanmax(x)-np.nanmin(x))).to_numpy(float)
        if np.any(np.abs(spread) > 1e-12):
            raise RuntimeError(f"inconsistent condition-time values: {f}")
    unique = validation.drop_duplicates(keys, keep="first").loc[:, keys+["block"]+list(features)].copy()
    unique = unique.sort_values(keys, kind="stable").reset_index(drop=True)
    mapping = {(str(r.event),str(r.condition_id),int(r.time_ns)):i
               for i,r in enumerate(unique.itertuples(index=False))}
    uid = np.asarray([mapping[(str(e),str(c),int(t))] for e,c,t in zip(
        validation["event"],validation["condition_id"],validation["time_ns"],strict=True)],np.int64)
    groups = np.asarray([f"{e}|{c}" for e,c in zip(unique["event"],unique["condition_id"],strict=True)],object)
    return {"unique":unique,"uid":uid,"groups":groups,
            "values":unique.loc[:,list(features)].to_numpy(float)}


def regular_transform(index: dict[str, Any], *, mode: str, component: str, draw: int) -> tuple[np.ndarray,int]:
    u=index["unique"]; values=np.asarray(index["values"],float); groups=np.asarray(index["groups"],object)
    out=values.copy(); rng=np.random.default_rng(seed(component,draw)); valid=0
    for g in sorted(set(map(str,groups))):
        ids=np.flatnonzero(groups==g)
        order=ids[np.argsort(u.loc[ids,"time_ns"].to_numpy(np.int64),kind="stable")]
        n=len(order)
        if mode=="circular":
            if n<=20: continue
            shift=int(rng.integers(10,n-9))
        elif mode=="block":
            times=u.loc[order,"time_ns"].to_numpy(np.int64)
            if n<20 or np.any(np.diff(times)!=30*NS): continue
            blocks=u.loc[order,"block"].to_numpy(np.int64)
            _,counts=np.unique(blocks,return_counts=True)
            if len(counts)<2 or np.any(counts!=10): continue
            shift=int(rng.integers(1,len(counts)))*10
        else: raise ValueError(mode)
        out[order]=np.roll(values[order],shift,axis=0); valid+=1
    if valid==0: raise RuntimeError(f"no valid {mode} groups")
    return out[index["uid"]],valid


def maker_valid_shifts(times: np.ndarray) -> np.ndarray:
    n=len(times)
    if n<2: return np.zeros(0,np.int64)
    # Outcome-independent, evenly spaced candidate offsets. Every accepted donor pairing
    # must be at least 300 seconds apart in observed time.
    candidates=np.unique(np.linspace(1,n-1,min(128,n-1),dtype=np.int64))
    valid=[]
    for k in candidates:
        if np.min(np.abs(times-np.roll(times,int(k)))) >= 300*NS:
            valid.append(int(k))
    return np.asarray(valid,np.int64)


def maker_circular_transform(index: dict[str, Any], *, component: str, draw: int) -> tuple[np.ndarray,int,int]:
    u=index["unique"]; values=np.asarray(index["values"],float); groups=np.asarray(index["groups"],object)
    out=values.copy(); rng=np.random.default_rng(seed(component,draw)); valid=0; unsupported=0
    for g in sorted(set(map(str,groups))):
        ids=np.flatnonzero(groups==g)
        order=ids[np.argsort(u.loc[ids,"time_ns"].to_numpy(np.int64),kind="stable")]
        times=u.loc[order,"time_ns"].to_numpy(np.int64)
        shifts=maker_valid_shifts(times)
        if len(shifts)==0:
            unsupported+=1; continue
        k=int(shifts[int(rng.integers(0,len(shifts)))])
        out[order]=np.roll(values[order],k,axis=0); valid+=1
    return out[index["uid"]],valid,unsupported


def maker_block_transform(index: dict[str, Any], *, component: str, draw: int) -> tuple[np.ndarray,int,int]:
    u=index["unique"]; values=np.asarray(index["values"],float); groups=np.asarray(index["groups"],object)
    out=values.copy(); rng=np.random.default_rng(seed(component,draw)); valid=0; unsupported=0
    for g in sorted(set(map(str,groups))):
        ids=np.flatnonzero(groups==g)
        order=ids[np.argsort(u.loc[ids,"time_ns"].to_numpy(np.int64),kind="stable")]
        blocks=u.loc[order,"block"].to_numpy(np.int64)
        labels,counts=np.unique(blocks,return_counts=True)
        if len(labels)<2 or np.any(counts!=counts[0]):
            unsupported+=1; continue
        shift_blocks=int(rng.integers(1,len(labels)))
        shift=shift_blocks*int(counts[0])
        out[order]=np.roll(values[order],shift,axis=0); valid+=1
    return out[index["uid"]],valid,unsupported


def null_gain(fit: dict[str, Any], transformed: np.ndarray, challenger: tuple[str, ...]) -> float:
    from predictions_cup.learning.flow_models import predict_ridge, weighted_mse
    va=fit["validation_frame"]; model=fit["challenger_model"]
    full=va.loc[:,list(model.feature_names)].to_numpy(float)
    for j,name in enumerate(challenger):
        full[:,model.feature_names.index(name)]=transformed[:,j]
    p=predict_ridge(model,full)
    return float(fit["baseline_mse"]-weighted_mse(fit["vy"],p,fit["val_w"]))


def upper_p(obs: float, vals: list[float]) -> float:
    if obs<=0 or not vals: return 1.0
    a=np.asarray(vals,float)
    return float((1+np.sum(a>=obs))/(len(a)+1))


def run_cell(frame: pd.DataFrame, *, mechanism: str, regime: str, horizon: int,
             base: tuple[str,...], challenger: tuple[str,...], y: str,
             mode_kind: str, entity: str) -> tuple[dict[str,Any],list[dict[str,Any]]]:
    fit=fit_cell(frame,base_features=base,challenger_features=challenger,y_name=y,entity_column=entity)
    if fit["status"]!="OK":
        return {"mechanism":mechanism,"regime":regime,"horizon_seconds":horizon,
                "response":y,"status":fit["status"],"intersection_p":1.0},[]
    obs=float(fit["observed_gain"])
    row={"mechanism":mechanism,"regime":regime,"horizon_seconds":horizon,"response":y,
         "status":"OK","rows":len(fit["data"]),"validation_rows":int(np.sum(fit["validation"])),
         "observed_gain":obs,"relative_gain":obs/float(fit["baseline_mse"])}
    if obs<=0:
        row.update({"circular_p":1.0,"block_p":1.0,"intersection_p":1.0,
                    "circular_valid_groups_min":0,"block_valid_groups_min":0,
                    "block_support":"NOT_NEEDED_NEGATIVE_OBSERVED"})
        return row,[]
    idx=source_index(fit["validation_frame"],challenger)
    draws=[]
    circular=[]; block=[]; circ_valid=[]; block_valid=[]; block_unsupported=[]
    for d in range(DRAWS):
        comp=f"{mechanism}|{regime}|{horizon}|{y}"
        if mode_kind=="regular":
            z,v=regular_transform(idx,mode="circular",component=comp+"|circular",draw=d)
            b,bv=regular_transform(idx,mode="block",component=comp+"|block",draw=d)
            bu=0
        else:
            z,v,_=maker_circular_transform(idx,component=comp+"|circular",draw=d)
            b,bv,bu=maker_block_transform(idx,component=comp+"|block",draw=d)
        cg=null_gain(fit,z,challenger); circular.append(cg); circ_valid.append(v)
        draws.append({"mechanism":mechanism,"regime":regime,"horizon_seconds":horizon,
                      "response":y,"null":"circular","draw":d,"loss_gain":cg})
        if bv>0:
            bg=null_gain(fit,b,challenger); block.append(bg); block_valid.append(bv); block_unsupported.append(bu)
            draws.append({"mechanism":mechanism,"regime":regime,"horizon_seconds":horizon,
                          "response":y,"null":"block","draw":d,"loss_gain":bg})
    cp=upper_p(obs,circular); bp=upper_p(obs,block)
    # Missing block support fails closed; it cannot be ignored to promote maker evidence.
    intersection=max(cp,bp) if len(block)==DRAWS else 1.0
    row.update({
        "circular_p":cp,"block_p":bp if block else 1.0,"intersection_p":intersection,
        "circular_valid_groups_min":min(circ_valid) if circ_valid else 0,
        "block_valid_groups_min":min(block_valid) if block_valid else 0,
        "block_unsupported_groups_max":max(block_unsupported) if block_unsupported else 0,
        "block_support":"FULL" if len(block)==DRAWS else "STRUCTURALLY_UNAVAILABLE_FAIL_CLOSED",
        "circular_null_mean":float(np.mean(circular)) if circular else np.nan,
        "block_null_mean":float(np.mean(block)) if block else np.nan,
    })
    return row,draws


def main() -> None:
    code=load_bundle(); root,stage1=locate_stage1()
    maker_rows=[]; liq_primary=[]; liq_placebo=[]; draws=[]
    for horizon in HORIZONS:
        maker=pq.read_table(root/f"maker_panel_{horizon}s.parquet").to_pandas()
        liq=pq.read_table(root/f"liquidity_panel_{horizon}s.parquet").to_pandas()
        for regime in REGIMES:
            m=maker[maker["regime"]==regime]
            r,d=run_cell(m,mechanism="A5_MAKER",regime=regime,horizon=horizon,
                         base=MAKER_BASE,challenger=MAKER_CHALLENGER,y="adverse_markout",
                         mode_kind="maker",entity="condition_id")
            maker_rows.append(r); draws.extend(d)
            q=liq[liq["regime"]==regime]
            for response in PRIMARY_RESPONSES:
                r,d=run_cell(q,mechanism="A5_LIQUIDITY",regime=regime,horizon=horizon,
                             base=A2_BASE,challenger=A2_CHALLENGER,y=response,
                             mode_kind="regular",entity="condition_id")
                liq_primary.append(r); draws.extend(d)
            for response in PLACEBO_RESPONSES:
                r,d=run_cell(q,mechanism="A5_CAPTURE_PLACEBO",regime=regime,horizon=horizon,
                             base=A2_BASE,challenger=A2_CHALLENGER,y=response,
                             mode_kind="regular",entity="condition_id")
                liq_placebo.append(r); draws.extend(d)

    from predictions_cup.learning.flow_models import benjamini_hochberg
    for family in (maker_rows,liq_primary,liq_placebo):
        p=np.asarray([float(r.get("intersection_p",1.0)) for r in family],float)
        q=benjamini_hochberg(p)
        for r,v in zip(family,q,strict=True):
            r["bh_q"]=float(v)
            r["bh_reject_5pct"]=bool(v<=0.05 and float(r.get("observed_gain",-np.inf))>0)

    write_csv(WORK/"maker_null_summary.csv",maker_rows)
    write_csv(WORK/"liquidity_primary_null_summary.csv",liq_primary)
    write_csv(WORK/"capture_placebo_null_summary.csv",liq_placebo)
    write_csv(WORK/"null_draws.csv",draws)
    manifest={
        "experiment_id":"EXPERIMENT-005A","stage":"N5_MAKER_LIQUIDITY_NULLS",
        "master_seed":MASTER_SEED,"draws":DRAWS,
        "stage1_manifest_sha256":sha256(root/"run_manifest.json"),
        "stage1_implementation_commit":stage1["code_manifest"]["implementation_commit"],
        "code_bundle_implementation_commit":code["implementation_commit"],
        "outputs":{
            "maker_null_summary.csv":sha256(WORK/"maker_null_summary.csv"),
            "liquidity_primary_null_summary.csv":sha256(WORK/"liquidity_primary_null_summary.csv"),
            "capture_placebo_null_summary.csv":sha256(WORK/"capture_placebo_null_summary.csv"),
            "null_draws.csv":sha256(WORK/"null_draws.csv")
        }
    }
    (WORK/"run_manifest.json").write_text(json.dumps(manifest,indent=2,sort_keys=True)+"\\n")
    print(json.dumps({
        "status":"COMPLETE","maker_cells":len(maker_rows),"liquidity_cells":len(liq_primary),
        "placebo_cells":len(liq_placebo),"draw_rows":len(draws),
        "maker_rejections":sum(bool(r["bh_reject_5pct"]) for r in maker_rows),
        "liquidity_rejections":sum(bool(r["bh_reject_5pct"]) for r in liq_primary)
    },sort_keys=True))


if __name__=="__main__":
    main()
