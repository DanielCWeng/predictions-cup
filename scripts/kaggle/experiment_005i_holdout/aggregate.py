# ruff: noqa: E501,I001
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq


def read_one(root: Path) -> dict[str, Any]:
    evidence_files = list(root.rglob("HOLDOUT_EVIDENCE.json"))
    if len(evidence_files) != 1:
        raise RuntimeError(f"expected one HOLDOUT_EVIDENCE.json in {root}, found {len(evidence_files)}")
    results = evidence_files[0].parent
    evidence = json.loads(evidence_files[0].read_text(encoding="utf-8"))
    return {
        "evidence": evidence,
        "reversal": pq.read_table(results / "HOLDOUT_REVERSAL.parquet").to_pandas(),
        "regimes": pq.read_table(results / "HOLDOUT_REGIMES.parquet").to_pandas(),
        "transitions": pq.read_table(results / "HOLDOUT_TRANSITIONS.parquet").to_pandas(),
        "resilience": pq.read_table(results / "HOLDOUT_RESILIENCE.parquet").to_pandas(),
    }


def weighted_mean(values: list[tuple[float, int]]) -> float | None:
    n = sum(weight for _, weight in values)
    if n <= 0:
        return None
    return float(sum(value * weight for value, weight in values) / n)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifacts-root", required=True)
    parser.add_argument("--spec", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()

    spec = json.loads(Path(args.spec).read_text(encoding="utf-8"))
    roots = [p for p in Path(args.artifacts_root).iterdir() if p.is_dir()]
    workers = [read_one(root) for root in sorted(roots)]
    if len(workers) != 5:
        raise RuntimeError(f"expected five frozen HOLDOUT workers, found {len(workers)}")
    if any(not w["evidence"].get("holdout_read") for w in workers):
        raise RuntimeError("all artifacts must be HOLDOUT evidence")

    worker_rows: list[dict[str, Any]] = []
    sample_base_loss = sample_ofi_loss = 0.0
    sample_n = 0
    full_base_loss = full_ofi_loss = 0.0
    full_n = 0
    sample_positive_workers = 0
    full_positive_workers = 0

    for w in workers:
        e = w["evidence"]
        p = e["predictive_confirmation"]
        sn = int(p["sample_n"])
        fn = int(p["full_n"])
        sample_n += sn
        full_n += fn
        sample_base_loss += float(p["sample_baseline_logloss"]) * sn
        sample_ofi_loss += float(p["sample_ofi_logloss"]) * sn
        full_base_loss += float(p["full_baseline_logloss"]) * fn
        full_ofi_loss += float(p["full_ofi_logloss"]) * fn
        simpr = float(p["sample_logloss_improvement"])
        fimpr = float(p["full_logloss_improvement"])
        sample_positive_workers += int(simpr > 0)
        full_positive_workers += int(fimpr > 0)
        worker_rows.append(
            {
                "worker_id": e["worker_id"],
                "files_completed": int(e["files_completed"]),
                "sample_n": sn,
                "sample_ofi_logloss_improvement": simpr,
                "sample_baseline_auc": float(p["sample_baseline_auc"]),
                "sample_ofi_auc": float(p["sample_ofi_auc"]),
                "full_n": fn,
                "full_ofi_logloss_improvement": fimpr,
                "transition_count": int(e["transition_count"]),
            }
        )

    agg_sample_base = sample_base_loss / sample_n
    agg_sample_ofi = sample_ofi_loss / sample_n
    agg_full_base = full_base_loss / full_n
    agg_full_ofi = full_ofi_loss / full_n

    # Reversal confirmation and fixed falsification slices.
    rev_frames = []
    worker_reversal: list[float] = []
    for w in workers:
        frame = w["reversal"].copy()
        frame["reverse_count"] = frame["reversal_rate"] * frame["n"]
        all_row = frame[(frame["surface"] == "SAMPLE") & (frame["slice_type"] == "ALL")]
        if len(all_row) != 1:
            raise RuntimeError("missing unique SAMPLE/ALL reversal row")
        worker_reversal.append(float(all_row.iloc[0]["reversal_rate"]))
        rev_frames.append(frame)
    rev = pd.concat(rev_frames, ignore_index=True)
    rev_agg = (
        rev.groupby(["surface", "slice_type", "slice"], dropna=False, as_index=False)
        .agg(n=("n", "sum"), reverse_count=("reverse_count", "sum"))
    )
    rev_agg["reversal_rate"] = rev_agg["reverse_count"] / rev_agg["n"]
    rev_agg.drop(columns=["reverse_count"], inplace=True)
    sample_all = rev_agg[(rev_agg["surface"] == "SAMPLE") & (rev_agg["slice_type"] == "ALL")]
    if len(sample_all) != 1:
        raise RuntimeError("missing aggregate SAMPLE/ALL reversal row")
    aggregate_reversal = float(sample_all.iloc[0]["reversal_rate"])
    aggregate_reversal_n = int(sample_all.iloc[0]["n"])

    # Regime state rows.
    regime_frames = []
    regime_worker: dict[str, list[dict[str, Any]]] = {}
    for w in workers:
        frame = w["regimes"].copy()
        wid = w["evidence"]["worker_id"]
        frame["worker_id"] = wid
        regime_frames.append(frame)
        for row in frame.to_dict("records"):
            regime_worker.setdefault(str(row["state"]), []).append(row)
    regimes = pd.concat(regime_frames, ignore_index=True)

    # Transitions.
    trans_frames = []
    for w in workers:
        frame = w["transitions"].copy()
        frame["worker_id"] = w["evidence"]["worker_id"]
        trans_frames.append(frame)
    transitions = pd.concat(trans_frames, ignore_index=True)
    trans_agg = (
        transitions.groupby(["from_state", "to_state"], as_index=False)
        .agg(count=("count", "sum"))
    )

    def state_totals(state: str) -> dict[str, Any]:
        sub = regimes[regimes["state"] == state]
        occupancy = int(sub["occupancy_minutes"].sum())
        exits = int(sub["exits"].sum())
        future_abs = weighted_mean(
            [(float(row.future_abs_5m_mean), int(row.occupancy_minutes)) for row in sub.itertuples() if pd.notna(row.future_abs_5m_mean)]
        )
        return {
            "occupancy_minutes": occupancy,
            "exits": exits,
            "exit_hazard_per_minute": float(exits / occupancy) if occupancy else None,
            "worker_dwell_p90_min": [float(x) for x in sub["dwell_p90_min"].tolist()],
            "future_abs_5m_mean_occupancy_weighted": future_abs,
        }

    discovery = state_totals("PRICE_DISCOVERY")
    stress = state_totals("LIQUIDITY_STRESS")

    discovery_exit_count = discovery["exits"]
    dpost = trans_agg[(trans_agg["from_state"] == "PRICE_DISCOVERY") & (trans_agg["to_state"] == "POST_SHOCK")]
    discovery_post_count = int(dpost["count"].sum()) if not dpost.empty else 0
    discovery_post_share = discovery_post_count / discovery_exit_count if discovery_exit_count else None

    stress_exit_count = stress["exits"]
    srepl = trans_agg[(trans_agg["from_state"] == "LIQUIDITY_STRESS") & (trans_agg["to_state"] == "REPLENISHMENT")]
    stress_repl_count = int(srepl["count"].sum()) if not srepl.empty else 0
    stress_repl_share = stress_repl_count / stress_exit_count if stress_exit_count else None

    # Resilience confirmation.
    resilience_worker_rows: list[dict[str, Any]] = []
    recovered_pairs: list[tuple[float, int]] = []
    not_recovered_pairs: list[tuple[float, int]] = []
    resilience_positive_workers = 0
    for w in workers:
        frame = w["resilience"]
        recovered = frame[frame["recovery_class"] == "RECOVERED_NEXT_MINUTE"]
        not_rec = frame[frame["recovery_class"] == "NOT_RECOVERED_NEXT_MINUTE"]
        if len(recovered) != 1 or len(not_rec) != 1:
            raise RuntimeError("resilience artifact missing one of the frozen classes")
        rr = recovered.iloc[0]
        nr = not_rec.iloc[0]
        rm = float(rr["subsequent_abs_5m_mean"])
        nm = float(nr["subsequent_abs_5m_mean"])
        rn = int(rr["n"])
        nn = int(nr["n"])
        direction = rm < nm
        resilience_positive_workers += int(direction)
        recovered_pairs.append((rm, rn))
        not_recovered_pairs.append((nm, nn))
        resilience_worker_rows.append(
            {
                "worker_id": w["evidence"]["worker_id"],
                "recovered_n": rn,
                "recovered_abs_5m_mean": rm,
                "not_recovered_n": nn,
                "not_recovered_abs_5m_mean": nm,
                "recovered_lower": direction,
            }
        )
    recovered_mean = weighted_mean(recovered_pairs)
    not_recovered_mean = weighted_mean(not_recovered_pairs)
    relative_reduction = (
        (not_recovered_mean - recovered_mean) / not_recovered_mean
        if recovered_mean is not None and not_recovered_mean
        else None
    )

    reversal_slices: dict[str, dict[str, dict[str, float | int]]] = {}
    sample_slices = rev_agg[rev_agg["surface"] == "SAMPLE"]
    for slice_type, group in sample_slices.groupby("slice_type", dropna=False):
        key = str(slice_type)
        reversal_slices[key] = {}
        for row in group.itertuples(index=False):
            reversal_slices[key][str(row.slice)] = {
                "n": int(row.n),
                "reversal_rate": float(row.reversal_rate),
            }

    regime_confirmation_rows: dict[str, list[dict[str, Any]]] = {}
    for state in ("PRICE_DISCOVERY", "LIQUIDITY_STRESS"):
        rows = regimes[regimes["state"] == state][
            [
                "worker_id",
                "occupancy_minutes",
                "exits",
                "exit_hazard_per_minute",
                "dwell_p90_min",
                "future_abs_5m_mean",
                "ofi_sign_agreement",
                "ofi_sign_n",
            ]
        ].to_dict("records")
        regime_confirmation_rows[state] = rows

    gates = {
        "OFI_INCREMENTAL": {
            "supported": (agg_sample_base - agg_sample_ofi) > 0 and sample_positive_workers >= 3,
            "aggregate_sample_n": sample_n,
            "aggregate_sample_baseline_logloss": agg_sample_base,
            "aggregate_sample_ofi_logloss": agg_sample_ofi,
            "aggregate_sample_logloss_improvement": agg_sample_base - agg_sample_ofi,
            "sample_positive_workers": sample_positive_workers,
            "aggregate_full_n": full_n,
            "aggregate_full_baseline_logloss": agg_full_base,
            "aggregate_full_ofi_logloss": agg_full_ofi,
            "aggregate_full_logloss_improvement": agg_full_base - agg_full_ofi,
            "full_positive_workers": full_positive_workers,
        },
        "FIVE_MINUTE_REVERSAL": {
            "supported": aggregate_reversal > 0.60 and min(worker_reversal) >= 0.55,
            "aggregate_sample_n": aggregate_reversal_n,
            "aggregate_sample_reversal_rate": aggregate_reversal,
            "worker_reversal_rates": worker_reversal,
            "min_worker_reversal_rate": min(worker_reversal),
        },
        "PRICE_DISCOVERY_TRANSITION": {
            "supported": discovery_post_share is not None and discovery_post_share >= 0.40 and sum(x <= 5 for x in discovery["worker_dwell_p90_min"]) >= 3,
            **discovery,
            "exit_to_post_shock_count": discovery_post_count,
            "exit_to_post_shock_share": discovery_post_share,
            "workers_dwell_p90_le_5": sum(x <= 5 for x in discovery["worker_dwell_p90_min"]),
        },
        "LIQUIDITY_STRESS_METASTABILITY": {
            "supported": stress["exit_hazard_per_minute"] is not None and stress["exit_hazard_per_minute"] < 0.10 and stress_repl_share is not None and stress_repl_share >= 0.20,
            **stress,
            "exit_to_replenishment_count": stress_repl_count,
            "exit_to_replenishment_share": stress_repl_share,
        },
        "RESILIENCE_AFTER_WITHDRAWAL": {
            "supported": recovered_mean is not None and not_recovered_mean is not None and recovered_mean < not_recovered_mean and resilience_positive_workers >= 3,
            "recovered_n": sum(n for _, n in recovered_pairs),
            "recovered_abs_5m_mean": recovered_mean,
            "not_recovered_n": sum(n for _, n in not_recovered_pairs),
            "not_recovered_abs_5m_mean": not_recovered_mean,
            "relative_reduction": relative_reduction,
            "positive_workers": resilience_positive_workers,
        },
    }

    output = {
        "schema_version": 1,
        "experiment": "EXPERIMENT-005I",
        "phase": "HOLDOUT_CONFIRMATION",
        "aggregation_only": True,
        "artifact_ids": [int(x) for x in spec["artifacts"]],
        "workers": worker_rows,
        "confirmatory_gates": gates,
        "reversal_falsification_slices": reversal_slices,
        "regime_confirmation_by_worker": regime_confirmation_rows,
        "resilience_confirmation_by_worker": resilience_worker_rows,
        "all_primary_gates_supported": all(v["supported"] for v in gates.values()),
        "holdout_read": True,
        "post_holdout_refit": False,
        "post_holdout_threshold_change": False,
        "make_modified": False,
        "real_sig_orders_sent": False,
    }

    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "HOLDOUT_RESULTS.json").write_text(json.dumps(output, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    pq.write_table(pa.Table.from_pandas(rev_agg, preserve_index=False), out / "HOLDOUT_REVERSAL_AGG.parquet")
    pq.write_table(pa.Table.from_pandas(regimes, preserve_index=False), out / "HOLDOUT_REGIMES_BY_WORKER.parquet")
    pq.write_table(pa.Table.from_pandas(trans_agg, preserve_index=False), out / "HOLDOUT_TRANSITIONS_AGG.parquet")
    pq.write_table(pa.Table.from_pandas(pd.DataFrame(resilience_worker_rows), preserve_index=False), out / "HOLDOUT_RESILIENCE_BY_WORKER.parquet")


if __name__ == "__main__":
    main()
