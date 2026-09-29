"""Diagnosis-only impact audit for EXPERIMENT-005B ordering falsification.

Measures how often the observable-order correction changes rows actually used by
the original promoted findings. It does not select, fit, score, tune, or promote
any candidate.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import duckdb

FAMILIES = ("US_2024", "CAN_2025", "COL_2026", "HUN_2026", "PER_2026")
ORIGINAL = {
    "US_2024": "005b-us-merge",
    "CAN_2025": "005b-historical-predictive-atlas-features-can-2025",
    "COL_2026": "005b-historical-predictive-atlas-features-col-2026",
    "HUN_2026": "005b-historical-predictive-atlas-features-hun-2026",
    "PER_2026": "005b-historical-predictive-atlas-features-per-2026",
}
CORRECTED = {
    "US_2024": "005b-ordering-falsification-us-merge",
    "CAN_2025": "005b-ordering-falsification-features-can-2025",
    "COL_2026": "005b-ordering-falsification-features-col-2026",
    "HUN_2026": "005b-ordering-falsification-features-hun-2026",
    "PER_2026": "005b-ordering-falsification-features-per-2026",
}
MOVEMENT = tuple(
    f"target_clock_realised_movement_{h}" for h in (1, 5, 15, 30, 60, 120, 300)
)
EVENT = (
    "target_event_sign_1",
    "target_event_sign_2",
    "target_event_sign_5",
    "target_event_sign_10",
    "target_event_price_change_5",
    "target_event_price_change_10",
)
TARGETS = MOVEMENT + EVENT
FREEZE_SHA256 = "44025aa2c5e5bddd97d88c81e649d86d6bf55d6cccec1d57c2aa7af12a9a7ec6"
EMBARGO = 300
OUT = Path("/kaggle/working/005b_ordering_falsification/impact_audit")
OUT.mkdir(parents=True, exist_ok=True)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def locate_unique(name: str, slug: str | None = None) -> Path:
    matches = [
        path
        for path in Path("/kaggle/input").rglob(name)
        if slug is None or slug in str(path)
    ]
    if len(matches) != 1:
        raise RuntimeError(f"expected one {name} ({slug}), found {matches}")
    return matches[0]


def locate_matrix(slug: str, family: str) -> Path:
    return locate_unique(f"feature_target_{family}.parquet", slug)


def qpath(path: Path) -> str:
    return str(path).replace("'", "''")


def quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def label_for_target(target: str) -> str:
    horizon = target.split("_")[-1]
    if target.startswith("target_clock_"):
        return f"clock_label_end_{horizon}"
    if target.startswith("target_event_"):
        return f"event_label_end_{horizon}"
    raise ValueError(target)


def regression_feature_subset(target: str, selection: dict[str, Any]) -> list[str]:
    best = selection["best"]
    name = best["name"]
    features = list(selection["features"])
    if name == "univariate_linear":
        return [str(best["params"]["feature"])]
    if name == "own_history_linear":
        return ["price_change_30" if "price_change" in target else "realised_vol_30"]
    return features


def selected_features(target: str, freeze: dict[str, Any]) -> list[str]:
    selection = freeze["model_selection"].get(target, {})
    if selection.get("promotion_eligible") is not True:
        raise RuntimeError(f"{target}: original model was not promotion eligible")
    if "_sign_" in target:
        return list(selection["features"])
    return regression_feature_subset(target, selection)


def changed_expr(column: str) -> str:
    c = quote(column)
    return f"o.{c} IS DISTINCT FROM n.{c}"


def eval_expr(alias: str, target: str, label: str) -> str:
    return (
        f"{alias}.raw_split='HOLDOUT' "
        f"AND {alias}.timestamp >= {alias}.dev_end_timestamp+{EMBARGO} "
        f"AND {alias}.{quote(target)} IS NOT NULL "
        f"AND {alias}.{quote(label)} IS NOT NULL"
    )


def audit_target(
    con: duckdb.DuckDBPyConnection,
    family: str,
    target: str,
    features: list[str],
) -> dict[str, Any]:
    label = label_for_target(target)
    o_eval = eval_expr("o", target, label)
    n_eval = eval_expr("n", target, label)
    feature_changed = " OR ".join(changed_expr(c) for c in features) or "FALSE"
    target_changed = changed_expr(target)
    label_changed = changed_expr(label)
    any_changed = f"({feature_changed}) OR ({target_changed}) OR ({label_changed})"
    row = con.execute(
        f"""
        SELECT
          COUNT(*) FILTER (WHERE {o_eval}) AS original_eval_rows,
          COUNT(*) FILTER (WHERE {n_eval}) AS corrected_eval_rows,
          COUNT(*) FILTER (WHERE ({o_eval}) AND ({n_eval})) AS common_eval_rows,
          COUNT(*) FILTER (WHERE ({o_eval}) != ({n_eval})) AS membership_changed_rows,
          COUNT(*) FILTER (
            WHERE ({o_eval}) AND ({n_eval}) AND ({feature_changed})
          ) AS selected_feature_changed_rows,
          COUNT(*) FILTER (
            WHERE ({o_eval}) AND ({n_eval}) AND ({target_changed})
          ) AS target_changed_rows,
          COUNT(*) FILTER (
            WHERE ({o_eval}) AND ({n_eval}) AND ({label_changed})
          ) AS label_endpoint_changed_rows,
          COUNT(*) FILTER (
            WHERE ({o_eval}) AND ({n_eval}) AND ({any_changed})
          ) AS common_eval_affected_rows,
          COUNT(*) FILTER (WHERE ({o_eval}) OR ({n_eval})) AS eval_union_rows
        FROM original o
        INNER JOIN corrected n USING (condition_id, tx_hash, log_index)
        """
    ).fetchone()
    keys = (
        "original_eval_rows",
        "corrected_eval_rows",
        "common_eval_rows",
        "membership_changed_rows",
        "selected_feature_changed_rows",
        "target_changed_rows",
        "label_endpoint_changed_rows",
        "common_eval_affected_rows",
        "eval_union_rows",
    )
    result = {key: int(value) for key, value in zip(keys, row, strict=True)}
    result["affected_eval_rows"] = (
        result["membership_changed_rows"] + result["common_eval_affected_rows"]
    )
    denom = result["eval_union_rows"]
    result["affected_eval_rate"] = (
        result["affected_eval_rows"] / denom if denom else None
    )
    result.update(
        {
            "family": family,
            "target": target,
            "selected_features": features,
            "selected_feature_count": len(features),
        }
    )
    return result


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    count_keys = (
        "original_eval_rows",
        "corrected_eval_rows",
        "common_eval_rows",
        "membership_changed_rows",
        "selected_feature_changed_rows",
        "target_changed_rows",
        "label_endpoint_changed_rows",
        "common_eval_affected_rows",
        "eval_union_rows",
        "affected_eval_rows",
    )
    result = {key: sum(int(row[key]) for row in rows) for key in count_keys}
    result["affected_eval_rate"] = (
        result["affected_eval_rows"] / result["eval_union_rows"]
        if result["eval_union_rows"]
        else None
    )
    return result


def main() -> None:
    freeze_path = locate_unique(
        "train_dev_shortlist_freeze.json",
        "005b-historical-predictive-atlas-train-dev",
    )
    if sha256(freeze_path) != FREEZE_SHA256:
        raise RuntimeError("TRAIN/DEV freeze hash mismatch")
    freeze = json.loads(freeze_path.read_text())
    feature_map = {target: selected_features(target, freeze) for target in TARGETS}

    rows: list[dict[str, Any]] = []
    for family in FAMILIES:
        original = locate_matrix(ORIGINAL[family], family)
        corrected = locate_matrix(CORRECTED[family], family)
        con = duckdb.connect()
        con.execute("PRAGMA threads=4")
        con.execute(
            f"CREATE VIEW original AS SELECT * FROM read_parquet('{qpath(original)}')"
        )
        con.execute(
            f"CREATE VIEW corrected AS SELECT * FROM read_parquet('{qpath(corrected)}')"
        )
        joined = int(
            con.execute(
                """SELECT COUNT(*) FROM original o
                   INNER JOIN corrected n USING (condition_id,tx_hash,log_index)"""
            ).fetchone()[0]
        )
        original_n = int(con.execute("SELECT COUNT(*) FROM original").fetchone()[0])
        corrected_n = int(con.execute("SELECT COUNT(*) FROM corrected").fetchone()[0])
        if joined != original_n or joined != corrected_n:
            raise RuntimeError(
                f"{family}: immutable-key coverage drift "
                f"{joined=} {original_n=} {corrected_n=}"
            )
        for target in TARGETS:
            report = audit_target(con, family, target, feature_map[target])
            rows.append(report)
            print(json.dumps(report, sort_keys=True), flush=True)
        con.close()

    movement_rows = [row for row in rows if row["target"] in MOVEMENT]
    event_rows = [row for row in rows if row["target"] in EVENT]
    by_target = {
        target: summarize([row for row in rows if row["target"] == target])
        for target in TARGETS
    }
    by_family = {
        family: summarize([row for row in rows if row["family"] == family])
        for family in FAMILIES
    }
    result = {
        "schema_version": 1,
        "experiment_id": "EXPERIMENT-005B-ORDERING-FALSIFICATION",
        "classification": "DIAGNOSIS_ONLY_POST_HOC_FALSIFICATION",
        "purpose": (
            "Quantify ordering contamination on the exact evaluation populations "
            "of the previously reported seven movement and six event-time findings."
        ),
        "interpretation": (
            "Counts are diagnostic only and cannot select, tune, promote, reconfirm, "
            "or alter any finding."
        ),
        "parent_train_dev_freeze_sha256": FREEZE_SHA256,
        "embargo_seconds": EMBARGO,
        "targets": list(TARGETS),
        "per_family_target": rows,
        "by_target": by_target,
        "by_family": by_family,
        "movement_findings_total": summarize(movement_rows),
        "event_findings_total": summarize(event_rows),
        "all_findings_total": summarize(rows),
    }
    path = OUT / "finding_evaluation_impact_audit.json"
    path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps({
        "movement_affected_eval_rate": result["movement_findings_total"]["affected_eval_rate"],
        "event_affected_eval_rate": result["event_findings_total"]["affected_eval_rate"],
        "all_affected_eval_rate": result["all_findings_total"]["affected_eval_rate"],
    }, sort_keys=True))


if __name__ == "__main__":
    main()
