# ruff: noqa
from __future__ import annotations

import hashlib, json
from pathlib import Path

OUT=Path("/kaggle/working/pred006_freeze"); OUT.mkdir(parents=True,exist_ok=True)
SEARCH_FRAGS={
 "temporal":"pred006-search-temporal",
 "cross_market":"pred006-search-cross-market",
 "participant_flow":"pred006-search-participant-flow",
 "interactions":"pred006-search-interactions",
 "event_targets":"pred006-search-event-targets",
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
    ab=json.loads(locate("hazard_ablation.json","pred006-hazard-ablation").read_text())
    if ab.get("final_rows_accessed") != 0:
        raise RuntimeError("hazard ablation touched FINAL")
    ab_rec={int(x["horizon"]):x for x in ab.get("recommendations",[])}
    ab_rows={(int(x["horizon"]),str(x["variant"])):x for x in ab.get("results",[]) if x.get("horizon") is not None}
    pool=[]
    search_ledger={"hazard_ablation":{"recommendations":ab.get("recommendations",[])}}
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
        if family in ("temporal","cross_market","participant_flow","interactions"):
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
        elif family=="event_targets":
            # This lane produced no hostile-screen DEV candidates. Keep it in
            # the search ledger, but do not create a candidate from a failed screen.
            if s.get("family_shortlist"):
                raise RuntimeError("event-target candidate support was not implemented in the frozen final evaluator")
        elif family=="hazard_fee":
            for row in s.get("family_shortlist",[]):
                h=int(row["horizon"])
                rec=ab_rec.get(h)
                if not rec or not rec.get("ablation_validated"):
                    continue
                variant=str(rec["recommended_variant"])
                chosen=ab_rows.get((h,variant))
                placebo=ab_rows.get((h,"permuted_train_labels"))
                if chosen is None or placebo is None:
                    raise RuntimeError(f"missing hazard ablation evidence for {h}")
                if float(placebo.get("improvement_vs_best_baseline",1.0)) >= 0.01:
                    continue
                pool.append({
                  "family":family,"target_type":"next_price_change_hazard","horizon_seconds":h,
                  "model_kind":row["kind"],"model_param":row["param"],
                  "features":list(rec["recommended_features"]),
                  "dev_improvement_vs_best_baseline":float(rec["recommended_improvement"]),
                  "dev_diagnostics":chosen["diagnostics"],
                  "baseline_spec":["base_rate","own_history_logit"],
                  "metric":"brier",
                  "ablation_variant":variant,
                  "full_feature_dev_improvement":float(rec["full_improvement"]),
                  "permuted_label_dev_improvement":float(placebo["improvement_vs_best_baseline"]),
                })
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
        x["market_scope"]="all DATA-003 condition-block-end observations supported by the frozen feature/target construction; no post-hoc mapping-class or market filtering"
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
      "selection_rule":"Hostile DEV-screen pass only; corrected block-end hazard candidates additionally require ablation validation and a non-promoting permuted-label placebo. Sort by DEV relative improvement, tie-break fewer features; deduplicate target/horizon; max two per family; max five total.",
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
