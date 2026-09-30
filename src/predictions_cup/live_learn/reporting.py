"""Rolling machine-readable and human-readable LIVE-LEARN reports."""

from __future__ import annotations

import asyncio
import json
import os
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Protocol

from predictions_cup.live_learn.contracts import DecisionOutcome, OutcomeStatus
from predictions_cup.shadow.contracts import CandidateDecision, DecisionStatus


@dataclass(frozen=True, slots=True)
class RollingReport:
    report_id: str
    cadence_seconds: int
    window_seconds: int
    window_start: datetime
    window_end: datetime
    payload: Mapping[str, object]
    human_text: str


class ReportSink(Protocol):
    async def emit(self, report: RollingReport) -> None: ...


class FileReportSink:
    def __init__(self, root: Path) -> None:
        self._root = root

    async def emit(self, report: RollingReport) -> None:
        await asyncio.to_thread(self._write, report)

    def _write(self, report: RollingReport) -> None:
        cadence = _cadence_name(report.cadence_seconds)
        directory = self._root / cadence
        directory.mkdir(parents=True, exist_ok=True)
        stamp = report.window_end.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
        json_path = directory / f"{stamp}.json"
        md_path = directory / f"{stamp}.md"
        payload = {
            "report_id": report.report_id,
            "cadence_seconds": report.cadence_seconds,
            "window_seconds": report.window_seconds,
            "window_start": report.window_start.astimezone(UTC).isoformat(),
            "window_end": report.window_end.astimezone(UTC).isoformat(),
            **dict(report.payload),
        }
        _atomic_write(
            json_path,
            json.dumps(payload, sort_keys=True, indent=2, allow_nan=False) + "\n",
        )
        _atomic_write(md_path, report.human_text.rstrip() + "\n")


def build_report(
    *,
    decisions: Sequence[CandidateDecision],
    outcomes: Sequence[DecisionOutcome],
    cadence_seconds: int,
    window_seconds: int,
    window_end: datetime,
    horizons: Sequence[int],
) -> RollingReport:
    if window_end.tzinfo is None or window_end.utcoffset() is None:
        raise ValueError("report window_end must be timezone-aware")
    end = window_end.astimezone(UTC)
    start = end - timedelta(seconds=window_seconds)
    cohort = tuple(
        decision
        for decision in decisions
        if start <= decision.observed_at.astimezone(UTC) < end
    )
    cohort_ids = {decision.decision_id for decision in cohort}
    matured = tuple(
        outcome
        for outcome in outcomes
        if outcome.decision_id in cohort_ids and outcome.maturity_at <= end
    )
    by_candidate_decisions: dict[tuple[str, str, str], list[CandidateDecision]] = (
        defaultdict(list)
    )
    for decision in cohort:
        key = (
            decision.candidate_id,
            decision.candidate_version,
            decision.strategy_family,
        )
        by_candidate_decisions[key].append(decision)

    by_candidate_outcomes: dict[tuple[str, str, str], list[DecisionOutcome]] = (
        defaultdict(list)
    )
    for outcome in matured:
        key = (
            outcome.candidate_id,
            outcome.candidate_version,
            outcome.strategy_family,
        )
        by_candidate_outcomes[key].append(outcome)

    candidate_rows: list[dict[str, object]] = []
    for key in sorted(by_candidate_decisions):
        candidate_id, candidate_version, strategy_family = key
        candidate_decisions = by_candidate_decisions[key]
        candidate_outcomes = by_candidate_outcomes.get(key, [])
        firing = sum(
            decision.decision_status is DecisionStatus.OK
            for decision in candidate_decisions
        )
        abstaining = sum(
            decision.decision_status is DecisionStatus.ABSTAIN
            for decision in candidate_decisions
        )
        eligible = sum(
            1
            for decision in candidate_decisions
            for horizon in horizons
            if decision.observed_at + timedelta(seconds=horizon) <= end
        )
        scored = sum(
            outcome.outcome_status is OutcomeStatus.MATURED_SCORED
            for outcome in candidate_outcomes
        )
        stale = sum(
            outcome.outcome_status is OutcomeStatus.STALE_UNTRUSTED_EVIDENCE
            for outcome in candidate_outcomes
        )
        metrics = _mean_metrics(candidate_outcomes)
        market_counts: dict[str, int] = defaultdict(int)
        for outcome in candidate_outcomes:
            market_counts[outcome.dimensions.get("market", "UNKNOWN")] += 1
        concentration = (
            0.0
            if not candidate_outcomes
            else max(market_counts.values(), default=0) / len(candidate_outcomes)
        )
        candidate_rows.append(
            {
                "candidate_id": candidate_id,
                "candidate_version": candidate_version,
                "strategy_family": strategy_family,
                "decisions": len(candidate_decisions),
                "firing": firing,
                "abstaining": abstaining,
                "abstention_rate": (
                    0.0
                    if not candidate_decisions
                    else abstaining / len(candidate_decisions)
                ),
                "candidate_coverage": (
                    0.0
                    if not candidate_decisions
                    else firing / len(candidate_decisions)
                ),
                "matured_outcomes": len(candidate_outcomes),
                "outcome_coverage": (
                    0.0 if eligible == 0 else len(candidate_outcomes) / eligible
                ),
                "scored_outcomes": scored,
                "stale_untrusted_evidence_rate": (
                    0.0 if not candidate_outcomes else stale / len(candidate_outcomes)
                ),
                "top_market_evidence_share": concentration,
                "metrics": metrics,
                "thin_evidence": scored < 10,
            }
        )

    matched = _matched_support(candidate_rows, matured)
    payload: dict[str, object] = {
        "decision_count": len(cohort),
        "matured_outcome_count": len(matured),
        "candidates": candidate_rows,
        "dimension_breakdowns": _dimension_breakdowns(matured),
        "matched_support_comparisons": matched,
        "baseline_notes": {
            "direct_pm_reference": "matched only on identical snapshot+horizon support",
            "make_baseline": "available when a MAKE-family candidate has matched forecast_error",
            "family_baseline": "aggregation dimension retained; no automatic promotion",
            "abstention_no_action": "reported as coverage/abstention, not assigned synthetic PnL",
        },
    }
    report_id = (
        f"live-learn-{cadence_seconds}-"
        + end.strftime("%Y%m%dT%H%M%S%fZ")
    )
    human = _render_human(
        cadence_seconds=cadence_seconds,
        start=start,
        end=end,
        rows=candidate_rows,
        matched=matched,
    )
    return RollingReport(
        report_id=report_id,
        cadence_seconds=cadence_seconds,
        window_seconds=window_seconds,
        window_start=start,
        window_end=end,
        payload=payload,
        human_text=human,
    )


def _mean_metrics(outcomes: Sequence[DecisionOutcome]) -> dict[str, float]:
    values: dict[str, list[float]] = defaultdict(list)
    for outcome in outcomes:
        for name, value in outcome.metric_values.items():
            values[name].append(value)
    return {
        name: sum(items) / len(items)
        for name, items in sorted(values.items())
        if items
    }


def _dimension_breakdowns(
    outcomes: Sequence[DecisionOutcome],
) -> dict[str, list[dict[str, object]]]:
    result: dict[str, list[dict[str, object]]] = {}
    for dimension in (
        "market",
        "mapping_class",
        "liquidity_bucket",
        "regime",
        "strategy_family",
    ):
        groups: dict[str, list[DecisionOutcome]] = defaultdict(list)
        for outcome in outcomes:
            value = outcome.dimensions.get(dimension)
            if value is not None:
                groups[value].append(outcome)
        rows: list[dict[str, object]] = []
        for value, items in sorted(groups.items()):
            rows.append(
                {
                    "value": value,
                    "outcomes": len(items),
                    "scored": sum(
                        item.outcome_status is OutcomeStatus.MATURED_SCORED
                        for item in items
                    ),
                    "metrics": _mean_metrics(items),
                }
            )
        result[dimension] = rows
    return result


def _matched_support(
    candidate_rows: Sequence[Mapping[str, object]],
    outcomes: Sequence[DecisionOutcome],
) -> list[dict[str, object]]:
    candidate_ids = [str(row["candidate_id"]) for row in candidate_rows]
    baselines: list[str] = []
    if "direct-pm-reference" in candidate_ids:
        baselines.append("direct-pm-reference")
    make_candidates = [
        str(row["candidate_id"])
        for row in candidate_rows
        if str(row["strategy_family"]) == "MAKE"
    ]
    baselines.extend(
        candidate_id
        for candidate_id in make_candidates
        if candidate_id not in baselines
    )

    errors: dict[str, dict[tuple[str, int], float]] = defaultdict(dict)
    for outcome in outcomes:
        error = outcome.metric_values.get("forecast_error")
        if error is None:
            continue
        errors[outcome.candidate_id][
            (outcome.input_snapshot_id, outcome.horizon_seconds)
        ] = error

    rows: list[dict[str, object]] = []
    for candidate_id in candidate_ids:
        for baseline_id in baselines:
            if candidate_id == baseline_id:
                continue
            common = sorted(
                set(errors.get(candidate_id, {}))
                & set(errors.get(baseline_id, {}))
            )
            if not common:
                continue
            candidate_mean = sum(
                errors[candidate_id][key] for key in common
            ) / len(common)
            baseline_mean = sum(
                errors[baseline_id][key] for key in common
            ) / len(common)
            rows.append(
                {
                    "candidate_id": candidate_id,
                    "baseline_id": baseline_id,
                    "matched_support": len(common),
                    "candidate_mean_forecast_error": candidate_mean,
                    "baseline_mean_forecast_error": baseline_mean,
                    "candidate_minus_baseline_forecast_error": (
                        candidate_mean - baseline_mean
                    ),
                }
            )
    return rows


def _render_human(
    *,
    cadence_seconds: int,
    start: datetime,
    end: datetime,
    rows: Sequence[Mapping[str, object]],
    matched: Sequence[Mapping[str, object]],
) -> str:
    lines = [
        f"# LIVE-LEARN {_cadence_name(cadence_seconds)} report",
        "",
        f"Window: {start.isoformat()} -> {end.isoformat()}",
        "",
        "| Candidate | Decisions | Firing | Abstain | Matured | Scored | Outcome cov. | Thin? |",
        "|---|---:|---:|---:|---:|---:|---:|---|",
    ]
    for row in rows:
        lines.append(
            "| {candidate_id}@{candidate_version} | {decisions} | {firing} | "
            "{abstaining} | {matured_outcomes} | {scored_outcomes} | "
            "{outcome_coverage:.3f} | {thin_evidence} |".format(**row)
        )
        metrics = row.get("metrics")
        if isinstance(metrics, dict) and metrics:
            rendered = ", ".join(
                f"{name}={float(value):.6g}"
                for name, value in sorted(metrics.items())
            )
            lines.append(f"| ↳ metrics |  |  |  |  |  | {rendered} |  |")
    if not rows:
        lines.append("| _No decisions in window_ | 0 | 0 | 0 | 0 | 0 | 0 | yes |")
    lines.extend(
        [
            "",
            "Matched-support comparisons use identical snapshot+horizon support only.",
        ]
    )
    for item in matched:
        lines.append(
            "- {candidate_id} vs {baseline_id}: n={matched_support}, "
            "forecast-error delta={candidate_minus_baseline_forecast_error:.6g}".format(
                **item
            )
        )
    lines.append(
        "No candidate is promoted automatically; thin or unmatched evidence remains descriptive."
    )
    return "\n".join(lines)


def _cadence_name(seconds: int) -> str:
    if seconds % 3600 == 0:
        return f"{seconds // 3600}h"
    if seconds % 60 == 0:
        return f"{seconds // 60}m"
    return f"{seconds}s"


def _atomic_write(path: Path, content: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)
