from __future__ import annotations

import json
from pathlib import Path
from typing import Any

WORK = Path("/kaggle/working")
ROOT = Path("/kaggle/input")

V3_HOSTILE_REQUIRED = {
    "005H-C01-RELATIVE-SIZE",
    "005H-C02-FAILED-REPLENISHMENT",
    "005H-C03-FILL-BEYOND-STATE",
}

HOLDOUT_RULES = {
    "005H-C01-RELATIVE-SIZE": {
        "metric": "B0 token-cluster bootstrap of R2(relative-size)-R2(absolute-size)",
        "minimum_rows": 50,
        "pass": "point > 0 and 2.5% bootstrap bound > 0",
        "economic_scope": "exact venue price+size stratum only",
    },
    "005H-C02-FAILED-REPLENISHMENT": {
        "metric": "B0 failed-minus-recovered aggressor-signed 30s move",
        "minimum_rows_each_group": 20,
        "pass": "point > 0 and token-cluster 2.5% bootstrap bound > 0",
    },
    "005H-C03-FILL-BEYOND-STATE": {
        "metric": "B0 fill-minus-one-to-one-matched-nonfill aggressor-signed 30s move",
        "minimum_pairs": 50,
        "pass": "point > 0 and token-cluster 2.5% bootstrap bound > 0",
    },
    "005H-C04-ARRIVAL-STATE": {
        "metric": "frozen logistic arrival model B0 AUC",
        "minimum_rows": 100,
        "pass": "AUC >= 0.60 and token-cluster bootstrap 2.5% AUC bound > 0.50",
    },
    "005H-C05-DIRECTION-STATE": {
        "metric": "frozen logistic BUY-vs-SELL model B0 AUC",
        "minimum_rows_each_side": 50,
        "pass": "AUC >= 0.58 and token-cluster bootstrap 2.5% AUC bound > 0.50",
    },
    "005H-C06-BOOK-MICROSTRUCTURE-CHALLENGER": {
        "metric": "frozen challenger-minus-state-baseline B0 R2",
        "minimum_rows": 50,
        "pass": "R2 improvement > 0 and token-cluster bootstrap 2.5% bound > 0",
    },
}


def locate(name: str) -> Path:
    matches = sorted(ROOT.rglob(name))
    if len(matches) != 1:
        raise RuntimeError(f"expected one {name}, found {matches}")
    return matches[0]


def load_json(name: str) -> dict[str, Any]:
    return json.loads(locate(name).read_text(encoding="utf-8"))


def main() -> None:
    extended = load_json("EXTENDED_V3_SHORTLIST.json")
    falsification = load_json("V3_FALSIFICATION_SUMMARY.json")
    source = load_json("SOURCE_VERSION_AUDIT.json")

    if extended.get("b0_opened") is not False:
        raise RuntimeError("extended source does not prove B0 stayed sealed")
    if falsification.get("b0_opened") is not False:
        raise RuntimeError("falsification source does not prove B0 stayed sealed")
    if source.get("b0_opened") is not False:
        raise RuntimeError("source audit does not prove B0 stayed sealed")

    extended_candidates = {
        str(row["candidate_id"]): row
        for row in extended.get("candidates", [])
    }
    robust_v3 = {
        str(value)
        for value in falsification.get("robust_v3_survivors", [])
    }
    provisional_v3 = {
        str(value)
        for value in falsification.get("provisional_v3_only", [])
    }
    rejected_v3 = {
        str(value)
        for value in falsification.get("rejected_v3", [])
    }

    full_models = extended.get("full_models", {})
    arrival_model = extended.get("arrival_result", {})
    direction_model = extended.get("direction_result", {})

    def frozen_spec(candidate_id: str, row: dict[str, Any]) -> dict[str, Any]:
        if candidate_id == "005H-C01-RELATIVE-SIZE":
            return {
                "absolute_model": full_models.get("abs_size"),
                "relative_model": full_models.get("rel_size"),
            }
        if candidate_id == "005H-C04-ARRIVAL-STATE":
            return {"arrival_model": arrival_model}
        if candidate_id == "005H-C05-DIRECTION-STATE":
            return {"direction_model": direction_model}
        if candidate_id == "005H-C06-BOOK-MICROSTRUCTURE-CHALLENGER":
            mechanism = str(row.get("mechanism") or "")
            return {
                "challenger_name": mechanism,
                "baseline_model": full_models.get("baseline"),
                "challenger_model": full_models.get(mechanism),
            }
        return {}

    frozen: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    for candidate_id, row in sorted(extended_candidates.items()):
        if candidate_id in V3_HOSTILE_REQUIRED and candidate_id not in robust_v3:
            excluded.append(
                {
                    "candidate_id": candidate_id,
                    "reason": (
                        "failed experiment-wide admission because hostile V3 "
                        "falsification did not label ROBUST_V3_SURVIVOR"
                    ),
                    "v3_status": (
                        "PROVISIONAL_V3_ONLY"
                        if candidate_id in provisional_v3
                        else "REJECTED_V3"
                        if candidate_id in rejected_v3
                        else "NOT_EVALUATED"
                    ),
                }
            )
            continue
        if candidate_id not in HOLDOUT_RULES:
            excluded.append(
                {
                    "candidate_id": candidate_id,
                    "reason": "no preregistered B0 rule",
                }
            )
            continue
        spec = frozen_spec(candidate_id, row)
        if candidate_id in {
            "005H-C01-RELATIVE-SIZE",
            "005H-C04-ARRIVAL-STATE",
            "005H-C05-DIRECTION-STATE",
            "005H-C06-BOOK-MICROSTRUCTURE-CHALLENGER",
        }:
            missing_spec = (
                not spec
                or any(value is None for value in spec.values())
            )
            if missing_spec:
                excluded.append(
                    {
                        "candidate_id": candidate_id,
                        "reason": "frozen model specification unavailable",
                    }
                )
                continue
        frozen.append(
            {
                **row,
                "holdout_rule": HOLDOUT_RULES[candidate_id],
                "frozen_spec": spec,
            }
        )

    source_decisions = source.get("best_window_source_decisions", [])
    pre_v3_eligible = [
        row
        for row in source_decisions
        if row.get("sequential_eligibility") == "ELIGIBLE"
    ]

    freeze = {
        "schema_version": 1,
        "experiment": "EXPERIMENT-005H",
        "stage": "PRE_HOLDOUT_FREEZE",
        "status": (
            "B0_AUTHORIZED_ONCE"
            if frozen
            else "NO_B0_CANDIDATES_KEEP_SEALED"
        ),
        "training": "W17 V3",
        "development": "W18 V3",
        "final_holdout": {
            "window_id": "B0",
            "source_directory": "baseline_sep",
            "start_utc": "2026-09-01T00:00:00Z",
            "end_exclusive_utc": "2026-09-22T00:00:00Z",
            "source_version": "V3",
        },
        "candidate_ids": [row["candidate_id"] for row in frozen],
        "candidates": frozen,
        "excluded_before_holdout": excluded,
        "v3_hostile_required": sorted(V3_HOSTILE_REQUIRED),
        "robust_v3_survivors": sorted(robust_v3),
        "pre_v3_source_identity_audit": {
            "completed": True,
            "eligible_windows": [
                str(row.get("window_id"))
                for row in pre_v3_eligible
            ],
            "interpretation": (
                "identity/chronology capability only; pre-V3 outcome effects "
                "are not pooled into V3 candidate selection or B0 scoring"
            ),
        },
        "transportability_boundary": (
            "B0 confirmation is V3-only. Any survivor remains "
            "FUTURE_CONFIRMATION_REQUIRED for live data and source-version "
            "transportability."
        ),
        "b0_authorized": bool(frozen),
        "b0_opened": False,
        "no_retune_rule": (
            "After B0 is opened: no candidate addition, threshold change, "
            "feature change, model refit, preprocessing refit, horizon change, "
            "subsetting rescue, or second B0 run."
        ),
        "real_sig_orders_sent": False,
    }
    (WORK / "PRE_HOLDOUT_FREEZE.json").write_text(
        json.dumps(freeze, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )

    lines = [
        "# EXPERIMENT-005H — Pre-Holdout Freeze",
        "",
        f"- Frozen B0 candidates: **{len(frozen)}**",
        f"- B0 authorized once: **{'YES' if frozen else 'NO'}**",
        "- B0 opened: **NO**",
        "- Final window: **2026-09-01 through 2026-09-21 inclusive**",
        "- Evaluation scope: **V3 only**",
        "",
    ]
    for row in frozen:
        lines.append(
            f"- {row['candidate_id']}: {row.get('mechanism', '')}"
        )
    if excluded:
        lines.extend(["", "Excluded before B0:"])
        for row in excluded:
            lines.append(
                f"- {row['candidate_id']}: {row['reason']}"
            )
    lines.extend(
        [
            "",
            "No post-B0 rescue or retuning is permitted.",
            "",
            "REAL SIG ORDERS SENT: NO",
        ]
    )
    (WORK / "PRE_HOLDOUT_FREEZE.md").write_text(
        "\n".join(lines) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(freeze, sort_keys=True, default=str), flush=True)


if __name__ == "__main__":
    main()
