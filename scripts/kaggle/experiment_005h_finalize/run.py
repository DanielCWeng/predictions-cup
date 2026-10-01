# ruff: noqa
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path("/kaggle/input")
WORK = Path("/kaggle/working")


def locate(name: str, *, required: bool = True) -> Path | None:
    matches = sorted(ROOT.rglob(name))
    if len(matches) == 1:
        return matches[0]
    if not matches and not required:
        return None
    raise RuntimeError(f"expected one {name}, found {matches}")


def load_json(name: str, *, required: bool = True) -> dict[str, Any] | None:
    path = locate(name, required=required)
    if path is None:
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


def candidate_routes(row: dict[str, Any]) -> list[str]:
    raw = row.get("classification") or []
    if not isinstance(raw, list):
        return []
    allowed = {
        "SEND_TO_MM_REPLAY",
        "SEND_TO_005I",
        "SEND_TO_LIVE_DIAG",
        "SEND_TO_SHADOW",
        "FUTURE_CONFIRMATION_REQUIRED",
        "DESCRIPTIVE_ONLY",
        "REJECTED",
    }
    return [str(value) for value in raw if str(value) in allowed]


def main() -> None:
    core = load_json("V3_DISCOVERY_SUMMARY.json")
    extended = load_json("EXTENDED_DISCOVERY_SUMMARY.json")
    shortlist = load_json("EXTENDED_V3_SHORTLIST.json")
    hostile = load_json("V3_FALSIFICATION_SUMMARY.json")
    source_audit = load_json("SOURCE_VERSION_AUDIT.json")
    source_robustness = load_json("SOURCE_ROBUSTNESS_SUMMARY.json")
    freeze = load_json("PRE_HOLDOUT_FREEZE.json")
    holdout = load_json(
        "HOLDOUT_EVALUATION_SUMMARY.json",
        required=False,
    )

    if any(
        payload is None
        for payload in (
            core,
            extended,
            shortlist,
            hostile,
            source_audit,
            source_robustness,
            freeze,
        )
    ):
        raise RuntimeError("missing mandatory pre-holdout scientific artifacts")
    assert core is not None
    assert extended is not None
    assert shortlist is not None
    assert hostile is not None
    assert source_audit is not None
    assert source_robustness is not None
    assert freeze is not None

    if freeze.get("stage") != "PRE_HOLDOUT_FREEZE":
        raise RuntimeError("wrong pre-holdout stage")
    b0_authorized = bool(freeze.get("b0_authorized"))
    if b0_authorized and holdout is None:
        raise RuntimeError(
            "B0 was authorized but holdout evaluation artifact is missing"
        )
    if not b0_authorized and holdout is not None:
        raise RuntimeError(
            "holdout artifact exists despite no B0 authorization"
        )

    extended_candidates = {
        str(row["candidate_id"]): row
        for row in shortlist.get("candidates", [])
    }
    source_records = {
        str(row["candidate_id"]): row
        for row in source_robustness.get("candidate_records", [])
        if row.get("candidate_id")
    }
    frozen_candidates = {
        str(row["candidate_id"]): row
        for row in freeze.get("candidates", [])
    }
    excluded = {
        str(row["candidate_id"]): row
        for row in freeze.get("excluded_before_holdout", [])
        if row.get("candidate_id")
    }
    holdout_results = {
        str(row["candidate_id"]): row
        for row in (holdout or {}).get("results", [])
    }

    candidate_ids = sorted(
        set(extended_candidates)
        | set(frozen_candidates)
        | set(excluded)
        | set(holdout_results)
    )
    classifications: list[dict[str, Any]] = []
    for candidate_id in candidate_ids:
        discovery = extended_candidates.get(candidate_id, {})
        source = source_records.get(candidate_id)
        final = holdout_results.get(candidate_id)
        exclusion = excluded.get(candidate_id)
        routes = candidate_routes(discovery)
        if final is not None:
            if final.get("status") == "PASS":
                routes = unique(
                    routes + ["FUTURE_CONFIRMATION_REQUIRED"]
                )
                disposition = "HISTORICAL_B0_PASS_FUTURE_CONFIRMATION_REQUIRED"
            else:
                routes = ["REJECTED"]
                disposition = "REJECTED_ON_ONE_SHOT_B0"
        elif exclusion is not None:
            routes = ["REJECTED"]
            disposition = "REJECTED_BEFORE_HOLDOUT"
        elif not b0_authorized:
            routes = ["REJECTED"]
            disposition = "NO_HOLDOUT_CANDIDATE"
        else:
            routes = unique(
                routes + ["FUTURE_CONFIRMATION_REQUIRED"]
            )
            disposition = "FROZEN_BUT_NO_FINAL_ROW"
        classifications.append(
            {
                "candidate_id": candidate_id,
                "mechanism": discovery.get("mechanism"),
                "disposition": disposition,
                "classification": routes,
                "source_robustness": source,
                "pre_holdout_exclusion": exclusion,
                "holdout_result": final,
            }
        )

    passed = [
        row
        for row in classifications
        if row["disposition"]
        == "HISTORICAL_B0_PASS_FUTURE_CONFIRMATION_REQUIRED"
    ]
    rejected = [
        row
        for row in classifications
        if "REJECTED" in row["classification"]
    ]

    hostile_robust = set(
        hostile.get("robust_v3_survivors", [])
    )
    hostile_provisional = set(
        hostile.get("provisional_v3_only", [])
    )
    def evidence_rank(row: dict[str, Any]) -> tuple[int, str]:
        candidate_id = str(row["candidate_id"])
        if row["disposition"] == (
            "HISTORICAL_B0_PASS_FUTURE_CONFIRMATION_REQUIRED"
        ):
            return (6, candidate_id)
        if row.get("holdout_result") is not None:
            return (5, candidate_id)
        if candidate_id in frozen_candidates:
            return (4, candidate_id)
        if candidate_id in hostile_robust:
            return (3, candidate_id)
        if candidate_id in hostile_provisional:
            return (2, candidate_id)
        return (1, candidate_id)

    ranked = sorted(
        classifications,
        key=lambda row: (
            -evidence_rank(row)[0],
            evidence_rank(row)[1],
        ),
    )
    top_five: list[dict[str, Any]] = []
    for row in ranked[:5]:
        source = row.get("source_robustness") or {}
        hold = row.get("holdout_result")
        exclusion = row.get("pre_holdout_exclusion")
        candidate_id = str(row["candidate_id"])
        if hold is not None and hold.get("status") != "PASS":
            falsification = "one-shot B0 gate failed"
        elif exclusion is not None:
            falsification = str(exclusion.get("reason") or exclusion)
        elif source.get("status") == "SOURCE_SPECIFIC":
            falsification = "pre-V3 source-version replication was source-specific"
        elif source.get("status") == "UNTESTABLE_SOURCE":
            falsification = "pre-V3 source-version challenge was chronologically untestable"
        elif candidate_id in hostile_robust:
            falsification = "survived preregistered hostile V3 falsification"
        elif candidate_id in hostile_provisional:
            falsification = "did not clear the full hostile V3 robustness bar"
        else:
            falsification = "did not advance beyond discovery"
        top_five.append(
            {
                "candidate_id": candidate_id,
                "mechanism": row.get("mechanism"),
                "evidence_stage_rank": evidence_rank(row)[0],
                "disposition": row["disposition"],
                "classification": row["classification"],
                "strongest_falsification_or_constraint": falsification,
            }
        )

    required_outputs = [
        "FILL_EVENTS.parquet",
        "FILL_EPISODES.parquet",
        "FILL_ARRIVAL_RESULTS.parquet",
        "FILL_DIRECTION_RESULTS.parquet",
        "IMPACT_CURVES.parquet",
        "REVERSAL_CONTINUATION.parquet",
        "RESILIENCE_RESULTS.parquet",
        "FLOW_PERSISTENCE.parquet",
        "TOXICITY_RESULTS.parquet",
        "SPREAD_DECOMPOSITION.parquet",
        "TRADFI_CHALLENGERS.parquet",
        "DISCOVERY_RESULTS.parquet",
        "FALSIFICATION_RESULTS.parquet",
        "PRE_HOLDOUT_FREEZE.json",
    ]
    output_presence = {
        name: bool(list(ROOT.rglob(name)))
        for name in required_outputs
    }
    output_presence["HOLDOUT_RESULTS.parquet"] = bool(
        list(ROOT.rglob("HOLDOUT_RESULTS.parquet"))
    )

    summary = {
        "schema_version": 1,
        "experiment": "EXPERIMENT-005H",
        "scientific_status": (
            "HISTORICAL_B0_SURVIVORS_REQUIRE_FUTURE_CONFIRMATION"
            if passed
            else "NO_HISTORICAL_B0_SURVIVOR"
            if b0_authorized
            else "NO_CANDIDATE_AUTHORIZED_FOR_B0"
        ),
        "canonical_identity": (
            "DATA-003 accepted active transaction episode linked by "
            "transaction_hash + active_token_id"
        ),
        "canonical_causal_source": "V3",
        "train_window": "W17",
        "dev_window": "W18",
        "final_window": "B0" if b0_authorized else None,
        "b0_authorized": b0_authorized,
        "b0_opened": bool((holdout or {}).get("b0_opened", False)),
        "candidate_classifications": classifications,
        "top_five_mechanisms": top_five,
        "b0_passed": [row["candidate_id"] for row in passed],
        "rejected": [row["candidate_id"] for row in rejected],
        "source_robustness": source_robustness,
        "join_summary": core.get("join_audits"),
        "core_counts": {
            "fill_events": core.get("fill_events"),
            "episodes": core.get("episodes"),
            "matched_pairs": core.get("matched_pairs"),
        },
        "extended_candidates": shortlist.get("candidates", []),
        "hostile_falsification": {
            "robust_v3_survivors": hostile.get(
                "robust_v3_survivors",
                [],
            ),
            "provisional_v3_only": hostile.get(
                "provisional_v3_only",
                [],
            ),
            "rejected_v3": hostile.get("rejected_v3", []),
        },
        "output_presence": output_presence,
        "real_sig_orders_sent": False,
    }
    (WORK / "FINAL_SUMMARY.json").write_text(
        json.dumps(
            summary,
            indent=2,
            sort_keys=True,
            default=str,
        )
        + "\n",
        encoding="utf-8",
    )

    lines = [
        "# MASTER HANDOFF — EXPERIMENT-005H",
        "",
        "## Scope and causal boundary",
        "",
        (
            "Primary evidence uses DATA-003 accepted active transaction "
            "episodes linked to the DATA-003 current-universe order-book "
            "corpus by exact transaction hash plus active token identity."
        ),
        (
            "Sequential causal claims use the V3 recorder receive clock and "
            "sequence ordering. DATA-001 was not substituted."
        ),
        "",
        "## Data construction",
        "",
        f"- Canonical V3 fill events: **{core.get('fill_events', 'NA')}**",
        f"- Canonical 10-second episodes: **{core.get('episodes', 'NA')}**",
        f"- Matched non-fill pairs: **{core.get('matched_pairs', 'NA')}**",
        "- TRAIN: **W17**",
        "- DEV: **W18**",
        (
            "- FINAL: **B0 one-shot**"
            if b0_authorized
            else "- FINAL: **not opened; no candidate authorized**"
        ),
        "",
        "## Candidate disposition",
        "",
    ]
    if classifications:
        for row in classifications:
            routes = ", ".join(row["classification"])
            lines.append(
                f"- **{row['candidate_id']}** — "
                f"{row.get('mechanism') or 'mechanism'} — "
                f"{row['disposition']} — {routes}"
            )
    else:
        lines.append("- No mechanism reached candidate status.")

    lines.extend(
        [
            "",
            "## Top mechanisms",
            "",
        ]
    )
    if top_five:
        for number, row in enumerate(top_five, start=1):
            lines.append(
                f"{number}. **{row['candidate_id']}** — "
                f"{row.get('mechanism') or 'mechanism'} — "
                f"{row['disposition']}. "
                f"Constraint: {row['strongest_falsification_or_constraint']}."
            )
    else:
        lines.append("- No mechanism reached candidate status.")

    lines.extend(
        [
            "",
            "## Hostile falsification",
            "",
            (
                "- Robust V3 survivors: "
                + (
                    ", ".join(
                        hostile.get("robust_v3_survivors", [])
                    )
                    or "NONE"
                )
            ),
            (
                "- Provisional V3 only: "
                + (
                    ", ".join(
                        hostile.get("provisional_v3_only", [])
                    )
                    or "NONE"
                )
            ),
            (
                "- Rejected on V3 falsification: "
                + (
                    ", ".join(hostile.get("rejected_v3", []))
                    or "NONE"
                )
            ),
            "",
            "## Source-version robustness",
            "",
        ]
    )
    records = source_robustness.get("candidate_records", [])
    if records:
        for row in records:
            lines.append(
                f"- **{row.get('candidate_id')}**: "
                f"{row.get('status')} — "
                f"{row.get('interpretation', '')}"
            )
    else:
        lines.append("- No candidate-specific source robustness records.")

    lines.extend(
        [
            "",
            "## Holdout",
            "",
        ]
    )
    if holdout is None:
        lines.append("- B0 was not authorized and remained sealed.")
    else:
        for row in holdout.get("results", []):
            lines.append(
                f"- **{row.get('candidate_id')}**: "
                f"{row.get('status')}"
            )
        lines.append(
            "- No B0 refit, candidate search, threshold change, or rescue was permitted."
        )

    lines.extend(
        [
            "",
            "## Production routing",
            "",
        ]
    )
    routed = [
        row for row in classifications
        if "REJECTED" not in row["classification"]
    ]
    if routed:
        for row in routed:
            lines.append(
                f"- **{row['candidate_id']}**: "
                + ", ".join(row["classification"])
            )
    else:
        lines.append(
            "- No mechanism is eligible for production routing from 005H."
        )

    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            (
                "Any B0 pass is historical same-source confirmation, not "
                "deployment proof. It remains future-confirmation-required "
                "and cannot directly authorize MAKE or real execution."
            ),
            "",
            "REAL SIG ORDERS SENT: NO",
        ]
    )
    (WORK / "MASTER_HANDOFF_005H.md").write_text(
        "\n".join(lines) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "scientific_status": summary["scientific_status"],
                "b0_passed": summary["b0_passed"],
                "rejected": summary["rejected"],
            },
            sort_keys=True,
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
