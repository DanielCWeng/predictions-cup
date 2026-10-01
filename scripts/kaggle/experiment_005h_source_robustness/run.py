from __future__ import annotations

import json
from pathlib import Path
from typing import Any

WORK = Path("/kaggle/working")
ROOT = Path("/kaggle/input")


def locate(name: str) -> Path:
    matches = sorted(ROOT.rglob(name))
    if len(matches) != 1:
        raise RuntimeError(f"expected one {name}, found {matches}")
    return matches[0]


def load_json(name: str) -> dict[str, Any]:
    return json.loads(locate(name).read_text(encoding="utf-8"))


def main() -> None:
    source = load_json("SOURCE_VERSION_AUDIT.json")
    shortlist = load_json("EXTENDED_V3_SHORTLIST.json")
    if source.get("b0_opened") is not False:
        raise RuntimeError("source audit does not prove B0 stayed sealed")
    if shortlist.get("b0_opened") is not False:
        raise RuntimeError("shortlist does not prove B0 stayed sealed")

    candidate_ids = {
        str(row["candidate_id"])
        for row in shortlist.get("candidates", [])
    }
    eligible = [
        row
        for row in source.get("best_window_source_decisions", [])
        if row.get("sequential_eligibility") == "ELIGIBLE"
    ]
    if not eligible:
        raise RuntimeError("no eligible pre-V3 source strata")
    sequence_windows = [
        str(row["window_id"])
        for row in eligible
        if bool(row.get("sequence_available"))
    ]
    no_sequence_windows = [
        str(row["window_id"])
        for row in eligible
        if not bool(row.get("sequence_available"))
    ]

    records: list[dict[str, Any]] = []
    for candidate_id in (
        "005H-C01-RELATIVE-SIZE",
        "005H-C02-FAILED-REPLENISHMENT",
        "005H-C03-FILL-BEYOND-STATE",
    ):
        if candidate_id in candidate_ids:
            records.append(
                {
                    "candidate_id": candidate_id,
                    "status": "NOT_APPLICABLE",
                    "interpretation": (
                        "candidate did not achieve ROBUST_V3_SURVIVOR "
                        "status in the preregistered hostile V3 gate; "
                        "pre-V3 evidence cannot restore B0 eligibility"
                    ),
                }
            )

    if "005H-C04-ARRIVAL-STATE" in candidate_ids:
        records.append(
            {
                "candidate_id": "005H-C04-ARRIVAL-STATE",
                "status": "UNTESTABLE_SOURCE",
                "tested_strata": 0,
                "eligible_pre_v3_windows": [
                    str(row["window_id"]) for row in eligible
                ],
                "sequence_available_windows": sequence_windows,
                "sequence_missing_windows": no_sequence_windows,
                "interpretation": (
                    "The frozen C04 feature vector includes mid_vol_60 "
                    "computed from the ordered BBO path. Every eligible "
                    "pre-V3 V2/AG6 source lacks recoverable sequence "
                    "ordering, so equal-receive-time book events cannot "
                    "be ordered exactly. Strictly-pre-fill state can be "
                    "defined, but exact frozen rolling-feature semantics "
                    "cannot be proven. The frozen source-robustness "
                    "protocol therefore requires UNTESTABLE_SOURCE rather "
                    "than an approximate replay."
                ),
            }
        )

    if "005H-C05-DIRECTION-STATE" in candidate_ids:
        records.append(
            {
                "candidate_id": "005H-C05-DIRECTION-STATE",
                "status": "UNTESTABLE_SOURCE",
                "tested_strata": 0,
                "eligible_pre_v3_windows": [
                    str(row["window_id"]) for row in eligible
                ],
                "sequence_available_windows": sequence_windows,
                "sequence_missing_windows": no_sequence_windows,
                "interpretation": (
                    "The frozen C05 feature vector includes event-wise "
                    "OFI_60. OFI depends on the ordered sequence of BBO "
                    "changes. Every eligible pre-V3 V2/AG6 source lacks "
                    "recoverable sequence ordering, so exact feature "
                    "parity is not identifiable. Under the frozen "
                    "protocol this is UNTESTABLE_SOURCE, not evidence "
                    "for or against transportability."
                ),
            }
        )

    if "005H-C06-BOOK-MICROSTRUCTURE-CHALLENGER" in candidate_ids:
        records.append(
            {
                "candidate_id": "005H-C06-BOOK-MICROSTRUCTURE-CHALLENGER",
                "status": "UNTESTABLE_SOURCE",
                "interpretation": (
                    "No C06 challenger cleared the frozen V3 discovery "
                    "gate; exact event-wise OFI parity is also unavailable "
                    "in pre-V3 sources without sequence ordering."
                ),
            }
        )

    summary = {
        "schema_version": 1,
        "experiment": "EXPERIMENT-005H",
        "stage": "SOURCE_VERSION_ROBUSTNESS",
        "method": "SEMANTIC_PARITY_ADJUDICATION",
        "selection_boundary": (
            "V2/AG6 evidence cannot add candidates, retune V3 "
            "candidates, or alter B0 thresholds."
        ),
        "source_audit_stage": source.get("stage"),
        "eligible_pre_v3_windows": [
            str(row["window_id"]) for row in eligible
        ],
        "all_eligible_sources_missing_sequence": not sequence_windows,
        "candidate_records": records,
        "superseded_approximate_replay": {
            "status": "SUPERSEDED_PRE_OUTCOME",
            "reason": (
                "An approximate pre-V3 feature replay was rejected before "
                "scientific outcome inspection because the frozen protocol "
                "requires exact feature-semantic parity. Missing sequence "
                "makes that parity unprovable for C04/C05."
            ),
        },
        "b0_opened": False,
        "real_sig_orders_sent": False,
    }
    (WORK / "SOURCE_ROBUSTNESS_SUMMARY.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    lines = [
        "# EXPERIMENT-005H — Source Robustness",
        "",
        "Method: **semantic-parity adjudication**.",
        "",
    ]
    for row in records:
        lines.append(
            f"- **{row['candidate_id']}**: {row['status']}"
        )
    lines.extend(
        [
            "",
            (
                "No approximate pre-V3 outcome replay is admitted where "
                "the frozen feature semantics cannot be reconstructed."
            ),
            "",
            "B0 opened: **NO**",
            "",
            "REAL SIG ORDERS SENT: NO",
        ]
    )
    (WORK / "SOURCE_ROBUSTNESS_REPORT.md").write_text(
        "\n".join(lines) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
