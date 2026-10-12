"""Chronological election-nowcast evaluation, never execution or PnL approval."""
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
    """Strict as-observed HOLDOUT with whole-event bootstrap and shuffled control.

    A positive Brier advantage is a research score only: no depth, fills, fees,
    queue/cancel state or net execution economics are evaluated here.
    """
    if sealed_event_families & training_event_families:
        raise Abstain("holdout overlaps training event families")
    if not 200 <= resamples <= 10000:
        raise Abstain("invalid bootstrap repetitions")
    accepted: list[tuple[str, float, float, float, float, int]] = []
    errors: list[str] = []
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
            if not (0 <= c.p_interval[0] <= c.probability_estimate <= c.p_interval[1] <= 1):
                raise Abstain("invalid candidate probability envelope")
            market = (row.sig_bid_yes + row.sig_ask_yes) / 2
            y = float(row.outcome_a_won)
            advantage = _brier(market, y) - _brier(c.probability_estimate, y)
            accepted.append((
                row.event_family, advantage, c.probability_estimate, y,
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
    families = sorted({r[0] for r in accepted})
    grouped = {f: np.array([r[1] for r in accepted if r[0] == f]) for f in families}
    measured = float(np.mean([r[1] for r in accepted]))
    rng = np.random.default_rng(seed)
    bootstrap = [
        float(np.mean(np.concatenate([
            grouped[f] for f in rng.choice(families, size=len(families), replace=True)
        ])))
        for _ in range(resamples)
    ]
    lower, upper = (float(x) for x in np.quantile(bootstrap, [0.025, 0.975]))
    # Broken event/decision association. Diagnostic; never optimize model on it.
    shuffled = rng.permutation([r[2] for r in accepted])
    baseline = [_brier(r[2], r[3]) + r[1] for r in accepted]
    shuffled_gain = float(np.mean([
        loss - _brier(float(p), r[3])
        for loss, p, r in zip(baseline, shuffled, accepted, strict=True)
    ]))
    enough = len(families) >= 5 and len(accepted) >= 50
    status = (
        "INSUFFICIENT_INDEPENDENT_EVIDENCE" if not enough
        else "RESEARCH_FORECAST_ADVANTAGE_NOT_ECONOMIC"
        if lower > 0 and measured > shuffled_gain
        else "NO_VERIFIED_FORECAST_ADVANTAGE"
    )
    lags = [r[5] for r in accepted]
    ages = [r[4] for r in accepted]
    return ForecastReport(
        status, len(accepted), len(families), measured, (lower, upper),
        shuffled_gain, float(np.percentile(lags, 50)),
        float(np.percentile(lags, 95)), float(np.percentile(ages, 95)),
        len(errors), tuple(errors[:20]),
    )
