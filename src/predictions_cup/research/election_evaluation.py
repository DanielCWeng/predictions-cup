"""Chronological election-nowcast evaluation; never execution or PnL approval."""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta

import numpy as np

from predictions_cup.research.election_nowcast import Abstain, ShadowCandidate, utc

MAX_QUOTE_AGE = timedelta(seconds=30)


@dataclass(frozen=True)
class EvaluationRow:
    event_family: str
    contest_id: str
    candidate: ShadowCandidate
    quote_asof: datetime
    quote_received: datetime
    sig_bid_yes: float
    sig_ask_yes: float
    outcome_a_won: bool
    outcome_known_at: datetime
    split: str


@dataclass(frozen=True)
class EventDiagnostic:
    event_family: str
    contest_id: str
    decision_rows: int
    revisions: int
    brier_improvement_vs_asof_mid: float
    mean_forecast: float
    resolved_outcome: int
    calibration_residual: float
    unchanged_sig_mid: bool
    count_lead_reversed: bool
    hypothetical_fills: int = 0


@dataclass(frozen=True)
class ForecastReport:
    status: str
    rows: int
    independent_event_families: int
    brier_improvement_vs_asof_mid: float | None
    improvement_cluster_ci95: tuple[float, float] | None
    shuffled_probability_control_improvement: float | None
    source_latency_p50_ms: float | None
    source_latency_p95_ms: float | None
    observed_quote_age_p95_ms: float | None
    failed_rows: int
    failures: tuple[str, ...]
    economic_advantage: str = "NOT_MEASURED_NO_EXECUTABLE_REPLAY"
    per_event: tuple[EventDiagnostic, ...] = ()
    calibration_gap: float | None = None
    calibration_gap_cluster_ci95: tuple[float, float] | None = None
    historical_negative_comparison: str = "R3_PRED007_NOT_COMPARABLE_TARGETS"


@dataclass(frozen=True)
class _Score:
    family: str
    contest: str
    revision_id: str
    margin: int
    revision_kind: str
    probability: float
    outcome: int
    mid: float
    advantage: float
    quote_age_ms: float
    source_latency_ms: int


def _brier(p: float, y: float) -> float:
    return (p - y) ** 2


def evaluate_holdout(
    rows: list[EvaluationRow],
    *,
    sealed_event_families: frozenset[str],
    training_event_families: frozenset[str],
    resamples: int = 2000,
    seed: int = 204,
) -> ForecastReport:
    """Score each episode once, then each independent election family once.

    Revisions and quote ticks count as *observations*, not independent positive
    election episodes; event-family bootstrap and calibration stay family-level.
    This is NOT executable economics or validation of a nominal probability CI.
    """
    if sealed_event_families & training_event_families:
        raise Abstain("holdout overlaps training event families")
    if not 200 <= resamples <= 10000:
        raise Abstain("invalid bootstrap repetitions")
    accepted: list[_Score] = []
    errors: list[str] = []
    seen_decisions: set[tuple[str, str, str, datetime]] = set()
    final_truth: dict[tuple[str, str], bool] = {}
    for i, row in enumerate(rows):
        try:
            c = row.candidate
            decision = utc(c.decision_time)
            quote_asof = utc(row.quote_asof)
            quote_received = utc(row.quote_received)
            known = utc(row.outcome_known_at)
            if row.split != "HOLDOUT" or row.event_family not in sealed_event_families:
                raise Abstain("not a sealed holdout family")
            if row.event_family in training_event_families:
                raise Abstain("training overlap")
            if c.mode != "SHADOW" or c.execution_authority is not False:
                raise Abstain("candidate is executable")
            if c.contest_id != row.contest_id or c.event_family != row.event_family:
                raise Abstain("candidate event/contest does not match score target")
            if not c.revision_id or not c.source_document_sha256:
                raise Abstain("missing official source-revision provenance")
            if type(row.outcome_a_won) is not bool:
                raise Abstain("outcome is not binary")
            if not (utc(c.source_time) <= utc(c.receive_time) <= decision < known):
                raise Abstain("source or outcome future leakage")
            if not (quote_asof <= quote_received <= decision < utc(c.expiry)):
                raise Abstain("quote not yet received or candidate expired")
            if decision - quote_asof > MAX_QUOTE_AGE:
                raise Abstain("stale SIG quote")
            if not (
                math.isfinite(row.sig_bid_yes) and math.isfinite(row.sig_ask_yes)
                and 0 <= row.sig_bid_yes <= row.sig_ask_yes <= 1
            ):
                raise Abstain("invalid/crossed market quote")
            if not (
                0 <= c.p_interval[0] <= c.probability_estimate <= c.p_interval[1] <= 1
            ):
                raise Abstain("invalid candidate probability envelope")
            key = (row.event_family, row.contest_id)
            unique = (*key, c.revision_id, decision)
            if unique in seen_decisions:
                raise Abstain("duplicated event/revision/decision observation")
            if key in final_truth and final_truth[key] != row.outcome_a_won:
                raise Abstain("inconsistent resolved outcome within contest")
            seen_decisions.add(unique)
            final_truth[key] = row.outcome_a_won
            mid = (row.sig_bid_yes + row.sig_ask_yes) / 2
            y = float(row.outcome_a_won)
            accepted.append(_Score(
                row.event_family, row.contest_id, c.revision_id,
                c.reported_margin, c.revision_kind,
                c.probability_estimate, int(y), mid,
                _brier(mid, y) - _brier(c.probability_estimate, y),
                (decision - quote_asof).total_seconds() * 1000,
                c.publication_to_receipt_ms,
            ))
        except (Abstain, TypeError, ValueError) as exc:
            errors.append(f"row={i}: {exc}")
    if not accepted:
        return ForecastReport(
            "NO_VALID_DECISION_TIME_OBSERVATIONS", 0, 0, None, None, None,
            None, None, None, len(errors), tuple(errors[:20]),
        )

    episodes = sorted({(r.family, r.contest) for r in accepted})
    per_event: list[EventDiagnostic] = []
    for family, contest in episodes:
        group = [r for r in accepted if (r.family, r.contest) == (family, contest)]
        per_event.append(EventDiagnostic(
            event_family=family,
            contest_id=contest,
            decision_rows=len(group),
            revisions=len({r.revision_id for r in group}),
            brier_improvement_vs_asof_mid=float(np.mean([r.advantage for r in group])),
            mean_forecast=float(np.mean([r.probability for r in group])),
            resolved_outcome=group[0].outcome,
            calibration_residual=group[0].outcome - float(
                np.mean([r.probability for r in group])
            ),
            unchanged_sig_mid=len({r.mid for r in group}) == 1,
            count_lead_reversed=(
                any(r.margin > 0 for r in group) and any(r.margin < 0 for r in group)
                and any(r.revision_kind == "CORRECTION" for r in group)
            ),
        ))

    families = sorted({e.event_family for e in per_event})
    family_improvement = {
        f: float(np.mean([
            e.brier_improvement_vs_asof_mid for e in per_event if e.event_family == f
        ]))
        for f in families
    }
    family_gap = {
        f: float(np.mean([
            e.calibration_residual for e in per_event if e.event_family == f
        ]))
        for f in families
    }
    measured = float(np.mean(list(family_improvement.values())))
    gap = float(np.mean(list(family_gap.values())))
    rng = np.random.default_rng(seed)
    bootstrap = np.array([
        [
            float(np.mean([family_improvement[f] for f in selected])),
            float(np.mean([family_gap[f] for f in selected])),
        ]
        for selected in (
            rng.choice(families, size=len(families), replace=True)
            for _ in range(resamples)
        )
    ])
    low, high = (float(v) for v in np.quantile(bootstrap[:, 0], [0.025, 0.975]))
    calibration_ci = tuple(float(v) for v in np.quantile(bootstrap[:, 1], [0.025, 0.975]))

    # Shuffled *episode* probabilities, not highly correlated quote ticks.
    shuffled = rng.permutation([e.mean_forecast for e in per_event])
    shuffled_gains = [
        float(np.mean([
            _brier(r.mid, r.outcome)
            for r in accepted
            if r.family == e.event_family and r.contest == e.contest_id
        ])) - _brier(float(p), e.resolved_outcome)
        for p, e in zip(shuffled, per_event, strict=True)
    ]
    shuffle_by_family = {
        f: float(np.mean([
            g for g, e in zip(shuffled_gains, per_event, strict=True)
            if e.event_family == f
        ]))
        for f in families
    }
    shuffled_gain = float(np.mean(list(shuffle_by_family.values())))
    enough = len(families) >= 5 and len(episodes) >= 5 and len(accepted) >= 50
    status = (
        "INSUFFICIENT_INDEPENDENT_EVIDENCE" if not enough
        else "RESEARCH_FORECAST_ADVANTAGE_NOT_ECONOMIC"
        if low > 0 and measured > shuffled_gain
        else "NO_VERIFIED_FORECAST_ADVANTAGE"
    )
    lags = [r.source_latency_ms for r in accepted]
    ages = [r.quote_age_ms for r in accepted]
    return ForecastReport(
        status, len(accepted), len(families), measured, (low, high),
        shuffled_gain, float(np.percentile(lags, 50)),
        float(np.percentile(lags, 95)), float(np.percentile(ages, 95)),
        len(errors), tuple(errors[:20]),
        per_event=tuple(per_event), calibration_gap=gap,
        calibration_gap_cluster_ci95=calibration_ci if enough else None,
    )
