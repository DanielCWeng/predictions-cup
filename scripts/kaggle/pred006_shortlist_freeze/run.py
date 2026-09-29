from __future__ import annotations

import hashlib, json
from pathlib import Path

OUT=Path("/kaggle/working/pred006_freeze"); OUT.mkdir(parents=True,exist_ok=True)
GENERAL=("temporal","cross-market","participant-flow","interactions")
SEARCH_FRAGS={
 "temporal":"pred006-search-temporal",
 "cross_market":"pred006-search-cross-market",
 "participant_flow":"pred006-search-participant-flow",
 "interactions":"pred006-search-interactions",
 "hazard_fee":"pred006-search-hazard-fee",
}

def locate(name:str,frag:str)->Path:
    m=[p for p in Path("/kaggle/input").rglob(name) if frag in str(p)]
    if len(m)!=1: raise RuntimeError(f"expected one {name} under {frag}, got {m}")
    return m[0]

def sha256(p:Path)->str:
    h=hashlib.sha256()
    with p.open("rb") as f:
        for c in iter(lambda:f.read(1<<20),b""):h.update(c)
    return h.hexdigest()

def main():
    split=locate("split_manifest.json","pred006-phase0-audit")
    split_hash=sha256(split)
    pool=[]
    search_ledger={}
    for family,frag in SEARCH_FRAGS.items():
        s=json.loads(locate("results_summary.json",frag).read_text())
        if s.get("final_rows_accessed_for_predictive_search") != 0:
            raise RuntimeError(f"{family} touched FINAL")
        if s.get("split_manifest_sha256") != split_hash:
            raise RuntimeError(f"{family} split hash mismatch")
        search_ledger[family]={
          "disposition":s.get("family_disposition"),
          "search_breadth":s.get("search_breadth"),
          "shortlist_count":len(s.get("family_shortlist",[])),
        }
        if family!="hazard_fee":
            hyp=json.loads(locate("hypothesis_ledger.json",frag).read_text())
            features=list(hyp["features"])
            for row in s.get("family_shortlist",[]):
                pool.append({
                  "family":family,"target_type":row["target"],"horizon_seconds":int(row["horizon"]),
                  "model_kind":row["kind"],"model_param":row["param"],"features":features,
                  "dev_improvement_vs_best_baseline":row["improvement_vs_best_baseline"],
                  "dev_diagnostics":row["diagnostics"],
                  "baseline_spec":["trivial","own_history_ridge"],
                  "metric":"mae",
                })
        else:
            features=list(s["features"])
            for row in s.get("family_shortlist",[]):
                pool.append({
                  "family":family,"target_type":"next_price_change_hazard","horizon_seconds":int(row["horizon"]),
                  "model_kind":row["kind"],"model_param":row["param"],"features":features,
                  "dev_improvement_vs_best_baseline":row["improvement_vs_best_baseline"],
                  "dev_diagnostics":row["diagnostics"],
                  "baseline_spec":["base_rate","own_history_logit"],
                  "metric":"brier",
                })
    # Winner's-curse control: only candidates that already passed each family's
    # hostile DEV screen enter this pool. Collapse target/horizon duplicates,
    # preferring stronger baseline improvement then fewer features.
    pool=sorted(pool,key=lambda x:(-float(x["dev_improvement_vs_best_baseline"]),len(x["features"]),x["family"]))
    dedup=[];keys=set();family_count={}
    for x in pool:
        key=(x["target_type"],x["horizon_seconds"])
        if key in keys: continue
        if family_count.get(x["family"],0)>=2: continue
        keys.add(key);family_count[x["family"]]=family_count.get(x["family"],0)+1
        dedup.append(x)
        if len(dedup)>=5: break
    for i,x in enumerate(dedup,1):
        x["candidate_id"]=f"PRED006-C{i:02d}"
        x["missingness"]="median imputation fit on pre-FINAL training data only"
        x["market_scope"]="all DATA-003 economic-fill observations supported by the frozen feature/target construction; no post-hoc mapping-class or market filtering"
        x["participant_handling"]="past-observable only; no future leaderboard/state"
        x["final_pass_rule"]={
          "min_relative_loss_improvement_vs_best_frozen_baseline":0.01,
          "condition_cluster_bootstrap_lower_2_5_must_exceed":0.0,
          "first_half_loss_improvement_must_exceed":0.0,
          "second_half_loss_improvement_must_exceed":0.0,
          "min_fraction_conditions_with_positive_loss_improvement":0.55,
          "max_top_decile_share_of_positive_condition_gain":0.75,
          "no_reselection_or_retuning":True,
        }
    freeze={
      "schema_version":1,"experiment_id":"PRED-006",
      "classification":"SHORTLIST_FREEZE_BEFORE_FINAL",
      "split_manifest_sha256":split_hash,
      "selection_rule":"DEV-screen pass only; sort by relative improvement vs best frozen baseline, tie-break fewer features; deduplicate target/horizon; max two per family; max five total",
      "search_ledger":search_ledger,
      "pool_size_before_dedup":len(pool),
      "candidate_count":len(dedup),
      "candidates":dedup,
      "final_open_rule":"FINAL may be read only by pred006-final after this freeze exists. If candidate_count=0, FINAL remains unopened and disposition is TERMINAL_NULL.",
      "post_final_rescue_forbidden":True,
    }
    fp=OUT/"shortlist_freeze.json";fp.write_text(json.dumps(freeze,indent=2,sort_keys=True)+"\n")
    registry={"schema_version":1,"experiment_id":"PRED-006","candidates":dedup}
    (OUT/"candidate_registry.json").write_text(json.dumps(registry,indent=2,sort_keys=True)+"\n")
    print("PRED006_FREEZE_RESULT="+json.dumps({"candidate_count":len(dedup),"candidate_ids":[x["candidate_id"] for x in dedup],
      "split_manifest_sha256":split_hash,"shortlist_freeze_sha256":sha256(fp)},sort_keys=True),flush=True)
if __name__=="__main__":main()
