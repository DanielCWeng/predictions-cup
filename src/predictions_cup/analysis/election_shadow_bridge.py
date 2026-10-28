"""Pure official-source / independently calibrated model / SIG-quote bridge.

No network, no funds, no side effects. A public result lead is NOT an implied
probability, and only a source-bound independently calibrated model may enter
the existing shadow evaluator. Output never authorises an exchange order.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from predictions_cup.analysis.election_official_texas import TexasSenateObservation
from predictions_cup.observe.election_lag import (
    Abstain,
    ElectionEvidenceKind,
    ElectionLagPolicy,
    ElectionProbabilityUpdate,
    ShadowCapacity,
    ShadowLagDecision,
    SigExecutableTop,
    evaluate_election_lag,
)


@dataclass(frozen=True, slots=True)
class IndependentEventModel:
    source_digest_sha256: str
    evidence_reference: str
    event_id: str
    published_at: datetime
    received_at: datetime
    received_monotonic_ns: int
    clock_domain: str
    exchange_id: str
    market_id: str
    probability_lower: float
    probability_upper: float
    calibration_independently_validated: bool
    mapping_independently_verified: bool
    sig_payoff_independently_verified: bool


def evaluate_official_result_shadow(
    *,
    official: TexasSenateObservation,
    model: IndependentEventModel | None,
    quote: SigExecutableTop,
    capacity: ShadowCapacity,
    now_monotonic_ns: int,
    now_utc: datetime,
    policy: ElectionLagPolicy | None = None,
) -> ShadowLagDecision:
    if official.status != "UNVERIFIED_COUNTS":
        return Abstain("no_current_official_nonzero_count")
    if model is None:
        return Abstain("no_independently_calibrated_probability")
    if (
        not model.evidence_reference.strip()
        or model.source_digest_sha256 != official.body_sha256
        or len(model.source_digest_sha256) != 64
    ):
        return Abstain("model_not_bound_to_exact_official_source_body")
    if not model.calibration_independently_validated:
        return Abstain("calibration_not_independently_validated")
    event = ElectionProbabilityUpdate(
        event_id=model.event_id,
        source_id=f"TX_SOS:{model.evidence_reference}",
        exchange_id=model.exchange_id,
        market_id=model.market_id,
        published_at=model.published_at,
        received_at=model.received_at,
        received_monotonic_ns=model.received_monotonic_ns,
        clock_domain=model.clock_domain,
        probability_lower=model.probability_lower,
        probability_upper=model.probability_upper,
        kind=ElectionEvidenceKind.OFFICIAL_RESULT,
        source_verified=True,
        exact_mapping_verified=model.mapping_independently_verified,
        sig_yes_payoff_verified=model.sig_payoff_independently_verified,
        probability_interval_calibrated=model.calibration_independently_validated,
    )
    return evaluate_election_lag(
        event, quote, capacity,
        now_monotonic_ns=now_monotonic_ns, now_utc=now_utc,
        policy=policy,
    )
