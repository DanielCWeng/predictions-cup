"""Finalize EXPERIMENT-005B evidence already copied into the repository.

This script performs reporting only. It never recomputes features, selects candidates,
fits models, or queries HOLDOUT.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path
from typing import Any


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def fmt(value: Any, digits: int = 4) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:.{digits}g}"
    return str(value)


def holdout_model_summary(model: dict[str, Any] | None) -> tuple[str, str, str]:
    if not model:
        return "—", "—", "—"
    selected = model.get("dev_selected", {})
    name = str(selected.get("name", "—"))
    metrics = model.get("holdout_metrics", {})
    if "mae_improvement_vs_persistence" in metrics:
        primary = f"MAE Δ {fmt(metrics.get('mae_improvement_vs_persistence'))}"
    elif "accuracy" in metrics:
        primary = f"accuracy {fmt(metrics.get('accuracy'))}"
    else:
        primary = "—"
    bootstrap = model.get("hierarchical_market_bootstrap", {})
    interval = (
        f"[{fmt(bootstrap.get('lower_2_5'))}, "
        f"{fmt(bootstrap.get('upper_97_5'))}]"
        if bootstrap
        else "—"
    )
    return name, primary, interval


def build_registry(
    freeze: dict[str, Any],
    holdout: dict[str, Any],
) -> list[dict[str, Any]]:
    hold_map = {row["target"]: row for row in holdout.get("results", [])}
    rows: list[dict[str, Any]] = []
    for target in sorted(freeze.get("shortlist", {})):
        shortlisted = freeze["shortlist"].get(target, [])
        stable = [row for row in shortlisted if row.get("stable_train_dev")]
        chosen = stable[0] if stable else None
        hrow = hold_map.get(target, {})
        scalar_hold = (hrow.get("scalar_candidate") or {}).get("holdout", {})
        model_name, model_primary, model_interval = holdout_model_summary(
            hrow.get("model")
        )
        rows.append(
            {
                "target": target,
                "scalar_feature": chosen.get("feature") if chosen else None,
                "candidate_label": (
                    chosen.get("selection_label") if chosen else "DISCOVERY_ONLY"
                ),
                "train_spearman": chosen.get("spearman") if chosen else None,
                "dev_pearson": chosen.get("dev_pearson") if chosen else None,
                "holdout_spearman": scalar_hold.get("spearman"),
                "holdout_pearson": scalar_hold.get("pearson"),
                "holdout_support": scalar_hold.get("support"),
                "dev_selected_model": model_name,
                "holdout_model_primary_metric": model_primary,
                "holdout_bootstrap_interval": model_interval,
                "holdout_rows": hrow.get("holdout_rows"),
                "holdout_market_count": hrow.get("holdout_market_count"),
            }
        )
    return rows


def write_registry(path: Path, rows: list[dict[str, Any]]) -> None:
    fieldnames = [
        "target",
        "scalar_feature",
        "candidate_label",
        "train_spearman",
        "dev_pearson",
        "holdout_spearman",
        "holdout_pearson",
        "holdout_support",
        "dev_selected_model",
        "holdout_model_primary_metric",
        "holdout_bootstrap_interval",
        "holdout_rows",
        "holdout_market_count",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def build_report(
    reconstruction: dict[str, Any],
    feature_build: dict[str, Any],
    freeze: dict[str, Any],
    holdout: dict[str, Any],
    registry: list[dict[str, Any]],
) -> str:
    totals = reconstruction["totals"]
    stable = [
        row
        for row in registry
        if row["scalar_feature"] is not None
    ]
    evaluated = {
        row["target"]: row
        for row in holdout.get("results", [])
    }
    lines = [
        "# EXPERIMENT-005B — Final Historical Price / Fill Predictive Atlas",
        "",
        "## Disposition",
        "",
        (
            "This report is a discovery atlas, not an executable strategy or alpha claim. "
            "Feature/target definitions, splits, screening policy and model grids were frozen "
            "before intentional inspection of prior ranked result cells. HOLDOUT was opened "
            "only after the TRAIN/DEV shortlist was hash-bound."
        ),
        "",
        "## Corpus and reconstruction",
        "",
        f"- DATA-002 participant-side rows: **{totals['participant_rows']:,}**.",
        f"- Transaction-condition groups: **{totals['transaction_condition_groups']:,}**.",
        f"- Accepted canonical groups: **{totals['accepted_groups']:,}**.",
        f"- Rejected groups: **{totals['rejected_groups']:,}**.",
        f"- Canonical economic fills: **{totals['economic_trade_rows']:,}**.",
        "",
        "The active aggregate OrderFilled row is audit-only; passive legs form the economic "
        "trade series. Binary prices are canonicalised to the YES axis. Role, fee and address "
        "fields do not enter the 005B predictors.",
        "",
        "## Atlas dimensions",
        "",
        f"- Feature count: **{freeze.get('feature_count', '—')}**.",
        f"- Target count: **{freeze.get('target_count', '—')}**.",
        f"- Redundancy clusters: **{freeze.get('redundancy_cluster_count', '—')}**.",
        (
            "- Representative features after TRAIN-only collapse: "
            f"**{freeze.get('representative_feature_count', '—')}**."
        ),
        (
            "- Feature-build rows: "
            f"**{feature_build.get('totals', {}).get('rows', '—')}**."
        ),
        "",
        "## TRAIN → DEV screen",
        "",
        (
            f"Targets with at least one TRAIN→DEV stable scalar candidate: "
            f"**{len(stable)} / {freeze.get('target_count', len(registry))}**."
        ),
        (
            f"Targets opened in sealed HOLDOUT: "
            f"**{holdout.get('targets_evaluated', len(evaluated))}**."
        ),
        "",
        "Candidate labels are descriptive evidence labels only. A stable sign across TRAIN and "
        "DEV does not by itself establish economic usefulness, portability, or executability.",
        "",
        "## Candidate registry",
        "",
        (
            "| Target | Scalar feature | Label | TRAIN ρ | DEV r | HOLDOUT ρ | "
            "Model | HOLDOUT model metric | Bootstrap interval |"
        ),
        "|---|---|---|---:|---:|---:|---|---|---|",
    ]
    for row in registry:
        if row["scalar_feature"] is None and row["target"] not in evaluated:
            continue
        lines.append(
            "| "
            + " | ".join(
                [
                    str(row["target"]),
                    str(row["scalar_feature"] or "—"),
                    str(row["candidate_label"]),
                    fmt(row["train_spearman"]),
                    fmt(row["dev_pearson"]),
                    fmt(row["holdout_spearman"]),
                    str(row["dev_selected_model"]),
                    str(row["holdout_model_primary_metric"]),
                    str(row["holdout_bootstrap_interval"]),
                ]
            )
            + " |"
        )

    lines += [
        "",
        "## Negative controls and baselines",
        "",
        "For model-eligible targets, sealed HOLDOUT compares the DEV-selected model against "
        "persistence, own recent price movement, current absolute movement and recent anonymous "
        "activity. Regression uncertainty is reported with hierarchical family→market and "
        "calendar moving-block resampling; classification uses the same family/market support "
        "breakdown and calibrated probability metrics.",
        "",
        "## Evidence limitations",
        "",
        "- Historical election families are not globally pristine because earlier experiments "
        "used portions of the same corpus. 005B therefore treats HOLDOUT as an internal "
        "development holdout, not a claim of untouched external replication.",
        "- Economic fills share markets, participants, events and time bursts; raw row count is "
        "not interpreted as IID confirmation.",
        "- Anonymous fill activity is intentionally separated from maker/taker and participant "
        "identity. Those mechanisms belong to other 005-series experiments.",
        "- 005B is a predictive research atlas. It does not include trading costs, execution "
        "policy, position sizing or live SIG order placement.",
        "",
        "## Reproducibility",
        "",
        "The branch stores the preregistration, feature and target dictionaries, operational "
        "amendments, reconstruction evidence, TRAIN/DEV freeze, sealed HOLDOUT result, candidate "
        "registry and a run manifest with SHA-256 hashes for the evidence files.",
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--root",
        type=Path,
        default=Path("data/experiments/experiment_005b/results"),
    )
    args = parser.parse_args()
    root = args.root
    reconstruction = load_json(root / "trade_reconstruction_report.json")
    feature_build = load_json(root / "feature_target_build_report.json")
    freeze = load_json(root / "train_dev_shortlist_freeze.json")
    holdout = load_json(root / "holdout_results.json")

    registry = build_registry(freeze, holdout)
    registry_path = root / "candidate_registry.csv"
    write_registry(registry_path, registry)

    report_path = root / "FINAL_REPORT.md"
    report_path.write_text(
        build_report(
            reconstruction,
            feature_build,
            freeze,
            holdout,
            registry,
        ),
        encoding="utf-8",
    )

    evidence_files = sorted(
        path
        for path in root.iterdir()
        if path.is_file() and path.name != "run_manifest.json"
    )
    manifest = {
        "schema_version": 1,
        "experiment_id": "EXPERIMENT-005B",
        "files": [
            {
                "path": path.name,
                "bytes": path.stat().st_size,
                "sha256": sha256(path),
            }
            for path in evidence_files
        ],
    }
    (root / "run_manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
