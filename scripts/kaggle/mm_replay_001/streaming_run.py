# ruff: noqa
from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any
import sys

import pyarrow as pa
import pyarrow.parquet as pq

SOURCE_ROOT = Path(__file__).resolve().parent
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from predictions_cup.mm_replay_001 import EXPERIMENT_ID, FillAssumption

from streaming_common import (
    PRIMARY_SCOPE,
    WORK,
    compact_baseline,
    dataset_root,
    load_frozen_pre_model,
    load_json,
    mean,
    write_csv,
    write_json,
)
from streaming_replay import (
    aggregate_latency,
    aggregate_toxicity,
    replay_condition,
    transfer_summary,
)

MANIFEST_PATH = Path("input_manifest.json")
UNIVERSE_PATH = Path("repo_context/execution_universe_exact.json")
FIT_MANIFEST_PATH = Path("repo_context/fit_freeze_manifest.json")


def final_report(
    audit: dict[str, Any],
    policy_rows: list[dict[str, Any]],
    summary: dict[str, Any],
) -> str:
    reference = [
        row
        for row in policy_rows
        if row["scenario_family"] == "LATENCY_SWEEP"
        and int(row["reaction_delay_ms"]) == 100
        and row["fill_assumption"] == FillAssumption.OBSERVED_TRADE.value
    ]
    by_policy: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in reference:
        by_policy[str(row["policy_id"])].append(row)

    lines = [
        "# MM-REPLAY-001 - Final Report",
        "",
        "SCIENTIFIC_RESULT=RUN_COMPLETE",
        "",
        "## Scope",
        "",
        (
            f"- Primary window: {PRIMARY_SCOPE} "
            "2026-09-01 through 2026-09-29 UTC."
        ),
        (
            "- Passive-fill execution universe: "
            f"{audit['primary_exact_markets']} VERIFIED EXACT direct "
            "SIG-to-Polymarket mappings."
        ),
        (
            "- Local execution laboratory: mapped Polymarket token book "
            "standing in for the SIG-equivalent contract."
        ),
        (
            "- External FV: strictly-as-of opposite-outcome token midpoint "
            "transformed as 1 - midpoint(complement)."
        ),
        (
            "- This is a same-venue proxy for SIG maker mechanics; it is not "
            "historical evidence of Polymarket-to-SIG latency."
        ),
        (
            "- Fills require observed last_trade_price prints; "
            "price_change is never treated as a fill."
        ),
        (
            "- Net P&L is not reported because maker fees and terminal "
            "unwind costs were not bound."
        ),
        "",
        "## 100 ms observed-trade reference",
        "",
    ]

    for policy in (
        "B0-local-mid",
        "B1-external-fv",
        "B2-external-fv-inventory",
        "B3-external-fv-toxicity",
    ):
        rows = by_policy.get(policy, [])
        lines.append(
            f"- **{policy}**: "
            f"fills={sum(int(row['fills']) for row in rows)}, "
            f"mean active fraction={mean([row['active_fraction'] for row in rows])}, "
            f"mean 5m external markout={mean([row['mean_external_markout_5m'] for row in rows])}, "
            f"mean 5m local markout={mean([row['mean_local_markout_5m'] for row in rows])}."
        )

    lines.extend(
        [
            "",
            "## Frozen 005F transfer",
            "",
            f"- Status: {summary.get('status')}",
            f"- Scored quote-relevant 15s buckets: {summary.get('scored_rows')}",
            f"- Brier score: {summary.get('brier')}",
            (
                "- Low-score quartile update rate: "
                f"{summary.get('update_rate_low_score_quartile')}"
            ),
            (
                "- High-score quartile update rate: "
                f"{summary.get('update_rate_high_score_quartile')}"
            ),
            "- Frozen artifacts were hash-matched; no model was refit.",
            "",
            "## Interpretation boundary",
            "",
            (
                "- Gross cash/inventory P&L and markouts are diagnostics, "
                "not fee-complete executable profit."
            ),
            (
                "- FINAL was not used for threshold rescue tuning; "
                "policies and latency grid were predeclared."
            ),
            (
                "- No SIG orders were sent and no live configuration is "
                "emitted automatically."
            ),
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> None:
    manifest = load_json(MANIFEST_PATH)
    universe = load_json(UNIVERSE_PATH)
    root = dataset_root()
    targets = load_json(root / "_manifests" / "targets.json")

    target_tokens = {str(token) for token in targets["token_ids"]}
    execution_tokens = {
        str(token)
        for record in universe["records"]
        for token in (
            record["local_token_id"],
            record["complement_token_id"],
        )
    }
    baseline_files = sorted((root / PRIMARY_SCOPE).rglob("events.parquet"))

    errors: list[str] = []
    if manifest.get("status") != "BOUND":
        errors.append("input manifest is not BOUND")
    if len(baseline_files) != 672:
        errors.append(
            f"expected 672 baseline_sep files, found {len(baseline_files)}"
        )
    if len(universe["records"]) != 140:
        errors.append(
            f"expected 140 EXACT execution records, found {len(universe['records'])}"
        )
    missing = sorted(execution_tokens - target_tokens)
    if missing:
        errors.append(
            f"{len(missing)} execution tokens absent from target manifest"
        )

    audit = {
        "experiment": EXPERIMENT_ID,
        "passed": not errors,
        "dataset_slug": manifest.get("kaggle_dataset_slug"),
        "dataset_version": manifest.get("dataset_version"),
        "dataset_status": "ready",
        "primary_scope": PRIMARY_SCOPE,
        "primary_files": len(baseline_files),
        "primary_exact_markets": len(universe["records"]),
        "primary_execution_tokens": len(execution_tokens),
        "full_target_tokens": len(target_tokens),
        "external_fv_provider": "BINARY_COMPLEMENT_BOOK_MID_ASOF",
        "fill_evidence": "last_trade_price only",
        "observable_ordering": "timestamp_received",
        "005f_regime": "PRE_ELECTION",
        "errors": errors,
        "warnings": [
            (
                "Historical Polymarket books are a proxy laboratory for "
                "SIG maker mechanics."
            ),
            (
                "Maker fee and unwind cost are intentionally unbound; "
                "net P&L stays null."
            ),
            (
                "Queue-aware fill is unavailable because queue-ahead is "
                "not observed."
            ),
        ],
    }
    write_json(WORK / "INPUT_AUDIT.json", audit)
    (WORK / "INPUT_AUDIT.md").write_text(
        (
            "# MM-REPLAY-001 - Input Audit\n\n"
            f"- Status: **{'PASS' if audit['passed'] else 'FAIL'}**\n"
            f"- Primary files: {audit['primary_files']}\n"
            f"- EXACT markets: {audit['primary_exact_markets']}\n"
            f"- Execution/complement tokens: {audit['primary_execution_tokens']}\n"
            f"- External FV: {audit['external_fv_provider']}\n"
            f"- 005F regime: {audit['005f_regime']}\n\n"
            "## Errors\n\n"
            + (
                "\n".join(f"- {error}" for error in errors)
                if errors
                else "- none"
            )
            + "\n"
        ),
        encoding="utf-8",
    )
    if errors:
        raise RuntimeError("input audit failed: " + "; ".join(errors))

    model, scaler, artifact_row, artifact_hashes = load_frozen_pre_model(
        FIT_MANIFEST_PATH
    )

    compact_audit = compact_baseline(root, universe)
    write_json(WORK / "COMPACT_AUDIT.json", compact_audit)
    if compact_audit["market_files_written"] != 140:
        raise RuntimeError(
            "compaction produced "
            f"{compact_audit['market_files_written']} markets, expected 140"
        )
    if compact_audit.get("trade_rows", 0) <= 0:
        raise RuntimeError(
            "no observed last_trade_price rows found for primary execution tokens"
        )

    all_policy: list[dict[str, Any]] = []
    all_fills: list[dict[str, Any]] = []
    all_markouts: list[dict[str, Any]] = []
    all_toxicity: list[dict[str, Any]] = []
    all_fv: list[dict[str, Any]] = []
    all_transfer: list[dict[str, Any]] = []
    hazard_pairs: list[tuple[float, float]] = []
    market_audit: list[dict[str, Any]] = []

    for index, record in enumerate(universe["records"], start=1):
        sig_market_id = str(record["sig_market_id"])
        compact_path = WORK / "compact" / f"{sig_market_id}.parquet"
        result = replay_condition(
            compact_path,
            record,
            model,
            scaler,
            artifact_row,
        )
        all_policy.extend(result["policy_rows"])
        all_fills.extend(result["fills"])
        all_markouts.extend(result["markouts"])
        all_toxicity.extend(result["toxicity"])
        all_fv.extend(result["fv"])
        all_transfer.append(result["transfer"])
        hazard_pairs.extend(result["hazard_pairs"])
        market_audit.append(
            {
                "sig_market_id": sig_market_id,
                "local_bbo_groups": result["local_bbo_groups"],
                "complement_bbo_groups": result["complement_bbo_groups"],
                "local_trades": result["local_trades"],
                "005f_scored_rows": result["transfer"].get(
                    "scored_rows",
                    0,
                ),
            }
        )
        if index % 10 == 0:
            print(
                f"replay progress {index}/140 fills={len(all_fills)}",
                flush=True,
            )

    summary = transfer_summary(
        all_transfer,
        hazard_pairs,
        artifact_hashes,
    )

    write_csv(WORK / "005F_TRANSFER_RESULTS.csv", all_transfer)
    write_json(WORK / "005F_TRANSFER_SUMMARY.json", summary)
    write_csv(WORK / "MM_POLICY_RESULTS.csv", all_policy)
    write_csv(WORK / "MM_MARKOUTS.csv", all_markouts)
    write_csv(
        WORK / "MM_TOXICITY_BUCKETS.csv",
        aggregate_toxicity(all_toxicity),
    )
    write_csv(WORK / "FV_CONVERGENCE.csv", all_fv)
    write_csv(
        WORK / "LATENCY_SENSITIVITY.csv",
        aggregate_latency(all_policy),
    )
    write_csv(WORK / "MARKET_BREAKDOWN.csv", all_policy)
    write_json(WORK / "MARKET_INPUT_AUDIT.json", market_audit)

    fill_table = (
        pa.Table.from_pylist(all_fills)
        if all_fills
        else pa.table({"status": pa.array([], type=pa.string())})
    )
    pq.write_table(
        fill_table,
        WORK / "MM_FILL_RESULTS.parquet",
        compression="zstd",
    )

    report = final_report(audit, all_policy, summary)
    (WORK / "FINAL_REPORT.md").write_text(report, encoding="utf-8")
    (WORK / "MASTER_HANDOFF_MM_REPLAY_001.md").write_text(
        (
            "# MM-REPLAY-001 - Empirical Handoff\n\n"
            "DATA_STATUS=BOUND\n"
            "SCIENTIFIC_RESULT=RUN_COMPLETE\n"
            "PRIMARY_SCOPE=baseline_sep\n"
            "PRIMARY_EXACT_MARKETS=140\n"
            f"FILLS={len(all_fills)}\n"
            f"005F_STATUS={summary.get('status')}\n"
            "REAL SIG ORDERS SENT: NO\n"
        ),
        encoding="utf-8",
    )
    print(report, flush=True)


if __name__ == "__main__":
    main()
