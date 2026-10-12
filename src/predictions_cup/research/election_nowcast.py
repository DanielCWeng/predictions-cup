"""Official-count nowcasting: read-only, SHADOW-only research for issue #204.

No network access, live credential, order interface, or production integration.
Partial precinct percentage is not remaining-ballot composition.
"""
from __future__ import annotations

import hashlib
import itertools
import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit

import numpy as np

BALLOT_TYPES = frozenset({"ELECTION_DAY", "EARLY", "MAIL", "PROVISIONAL", "OVERSEAS", "UNKNOWN"})
RESULT_STATUSES = frozenset({"UNOFFICIAL_RESULTS", "CERTIFIED"})
CANDIDATE_TTL = timedelta(seconds=45)
MAX_TRANSPORT_LAG = timedelta(minutes=5)
MAX_GROUPS = 6


class Abstain(ValueError):
    """Insufficient trustworthy evidence. Never substitute a numeric prediction."""


def utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise Abstain("naive clock")
    return value.astimezone(UTC)


@dataclass(frozen=True)
class OfficialSource:
    source_id: str
    hostname: str
    approved: bool
    reference_url: str
    licensing_note: str
    publisher_id: str
    reviewed_registry_sha256: str


@dataclass(frozen=True)
class MarketRule:
    target_exchange_id: str
    contest_id: str
    event_family: str
    outcome_a: str
    source_id: str
    settlement_rule_sha256: str
    rules_reviewed: bool
    binary_contest_reviewed: bool
    a_candidate_id: str
    a_party_id: str
    a_ballot_choice_id: str
    b_candidate_id: str
    b_party_id: str
    b_ballot_choice_id: str
    rule_type: str = "TWO_CANDIDATE_PLURALITY"


@dataclass(frozen=True)
class RemainingGroup:
    stratum: str
    ballot_type: str
    min_count: int
    max_count: int


@dataclass(frozen=True)
class ReportedChoice:
    candidate_id: str
    party_id: str
    ballot_choice_id: str
    votes: int


@dataclass(frozen=True)
class CountObservation:
    contest_id: str
    source_id: str
    source_ref: str
    source_url: str
    source_time: datetime
    receive_time: datetime
    revision_id: str
    revision_kind: str
    status: str
    reported_choices: tuple[ReportedChoice, ...]
    remaining: tuple[RemainingGroup, ...]
    remaining_inventory_verified: bool
    source_document_sha256: str


@dataclass(frozen=True)
class RevisionEvidence:
    """Independent captured-revision receipt; retain original bytes for audit.

    The source-specific parser/capture must be separately vetted: hashing alone
    does not authenticate an internet publisher or verify parsing semantics.
    """

    publisher_id: str
    source_id: str
    contest_id: str
    revision_id: str
    source_url: str
    source_time: datetime
    receive_time: datetime
    raw_source_body: bytes
    extracted_choices: tuple[ReportedChoice, ...]
    capture_ref: str
    capture_manifest_sha256: str
    publisher_timestamp_field: str


@dataclass(frozen=True)
class FrozenPrior:
    model_id: str
    frozen_at: datetime
    training_event_families: tuple[str, ...]
    evidence_ref: str
    calibration_allowance: float
    common_shock_sd: float
    beta_by_stratum: dict[str, tuple[float, float]]


@dataclass(frozen=True)
class ShadowCandidate:
    source_time: datetime
    receive_time: datetime
    target_exchange_id: str
    p_interval: tuple[float, float]
    confidence: str
    expiry: datetime
    reason: str
    source_refs: tuple[str, ...]
    decision_time: datetime
    probability_estimate: float
    publication_to_receipt_ms: int
    contest_id: str
    event_family: str
    revision_id: str
    source_document_sha256: str
    source_id: str
    revision_kind: str
    reported_margin: int
    mode: str = "SHADOW"
    execution_authority: bool = False

    def as_dict(self) -> dict[str, object]:
        return {
            "source_time": self.source_time.isoformat(),
            "receive_time": self.receive_time.isoformat(),
            "target_exchange_id": self.target_exchange_id,
            "p_interval": list(self.p_interval),
            "confidence": self.confidence,
            "expiry": self.expiry.isoformat(),
            "reason": self.reason,
            "source_refs": list(self.source_refs),
            "decision_time": self.decision_time.isoformat(),
            "probability_estimate": self.probability_estimate,
            "publication_to_receipt_ms": self.publication_to_receipt_ms,
            "contest_id": self.contest_id,
            "event_family": self.event_family,
            "revision_id": self.revision_id,
            "source_document_sha256": self.source_document_sha256,
            "source_id": self.source_id,
            "revision_kind": self.revision_kind,
            "reported_margin": self.reported_margin,
            "mode": self.mode,
            "execution_authority": self.execution_authority,
        }


def _host_valid(source: OfficialSource, url: str) -> bool:
    parsed = urlsplit(url)
    return (
        parsed.scheme == "https"
        and parsed.username is None
        and parsed.password is None
        and parsed.port in (None, 443)
        and parsed.hostname == source.hostname.lower()
    )


def _validated(
    obs: CountObservation,
    source: OfficialSource,
    rule: MarketRule,
    prior: FrozenPrior,
    evidence: RevisionEvidence,
    decision_at: datetime,
) -> tuple[datetime, datetime, datetime, int, int]:
    published, received, decision = utc(obs.source_time), utc(obs.receive_time), utc(decision_at)
    if not published <= received <= decision:
        raise Abstain("future publication/receipt or inverted clock")
    if decision - received >= CANDIDATE_TTL:
        raise Abstain("candidate TTL expired")
    if received - published > MAX_TRANSPORT_LAG:
        raise Abstain("late transport / unverifiable latency")
    if not utc(prior.frozen_at) < published:
        raise Abstain("prior not frozen before event")
    if rule.event_family in prior.training_event_families:
        raise Abstain("training-event leakage")
    if not (source.approved and rule.rules_reviewed and rule.binary_contest_reviewed):
        raise Abstain("source or contract rules not approved")
    if (
        obs.source_id != source.source_id
        or rule.source_id != source.source_id
        or rule.contest_id != obs.contest_id
        or rule.rule_type != "TWO_CANDIDATE_PLURALITY"
        or not _host_valid(source, obs.source_url)
    ):
        raise Abstain("source/host/market identity mismatch")
    if not all((
        rule.target_exchange_id, rule.event_family, rule.outcome_a, obs.source_ref,
        obs.revision_id, prior.model_id, prior.evidence_ref
    )):
        raise Abstain("missing source/contract/provenance identifier")
    for digest in (
        rule.settlement_rule_sha256, obs.source_document_sha256,
        source.reviewed_registry_sha256, evidence.capture_manifest_sha256,
    ):
        if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise Abstain("missing verified SHA256 identifier")
    if obs.revision_kind not in {"INITIAL", "UPDATE", "CORRECTION"}:
        raise Abstain("unknown revision type")
    if obs.status not in RESULT_STATUSES:
        raise Abstain("media projection is not official count status")
    if not obs.remaining_inventory_verified:
        raise Abstain("unverified remaining-ballot inventory")
    reviewed_a = (rule.a_candidate_id, rule.a_party_id, rule.a_ballot_choice_id)
    reviewed_b = (rule.b_candidate_id, rule.b_party_id, rule.b_ballot_choice_id)
    if (
        not all((*reviewed_a, *reviewed_b))
        or reviewed_a[0] == reviewed_b[0]
        or reviewed_a[2] == reviewed_b[2]
        or rule.outcome_a != rule.a_candidate_id
    ):
        raise Abstain("unreviewed candidate/outcome orientation")
    if len(obs.reported_choices) != 2:
        raise Abstain("third-party/multi-candidate results unsupported")
    counts: dict[tuple[str, str, str], int] = {}
    for choice in obs.reported_choices:
        identity = (choice.candidate_id, choice.party_id, choice.ballot_choice_id)
        if (
            not all(identity) or identity in counts
            or type(choice.votes) is not int or choice.votes < 0
        ):
            raise Abstain("invalid or duplicated candidate/ballot identity")
        counts[identity] = choice.votes
    if set(counts) != {reviewed_a, reviewed_b}:
        raise Abstain("candidate, party or ballot choice mismatch")
    if (
        evidence.publisher_id != source.publisher_id
        or not source.publisher_id
        or evidence.source_id != obs.source_id
        or evidence.contest_id != obs.contest_id
        or evidence.revision_id != obs.revision_id
        or evidence.source_url != obs.source_url
        or utc(evidence.source_time) != published
        or utc(evidence.receive_time) != received
        or evidence.extracted_choices != obs.reported_choices
        or evidence.publisher_timestamp_field != "PUBLISHER_REPORTED"
        or not evidence.capture_ref
        or not isinstance(evidence.raw_source_body, bytes)
        or not evidence.raw_source_body
        or hashlib.sha256(evidence.raw_source_body).hexdigest() != obs.source_document_sha256
    ):
        raise Abstain("missing or mismatched authoritative revision receipt")
    if not 0 < len(obs.remaining) <= MAX_GROUPS:
        raise Abstain("missing or unbounded number of ballot strata")
    if not all(
        math.isfinite(v) and 0 <= v <= 1
        for v in (prior.common_shock_sd, prior.calibration_allowance)
    ):
        raise Abstain("nonfinite/invalid model uncertainty")
    if not prior.beta_by_stratum:
        raise Abstain("missing frozen stratified priors")
    seen: set[str] = set()
    for group in obs.remaining:
        if group.stratum in seen or group.ballot_type not in BALLOT_TYPES:
            raise Abstain("duplicate or unknown ballot stratum")
        seen.add(group.stratum)
        if (
            type(group.min_count) is not int or type(group.max_count) is not int
            or group.min_count < 0 or group.max_count < group.min_count
        ):
            raise Abstain("invalid remaining-ballot counts")
        if group.stratum not in prior.beta_by_stratum:
            raise Abstain("missing frozen prior for ballot cohort")
        a, b = prior.beta_by_stratum[group.stratum]
        if not all(math.isfinite(v) and v > 0 for v in (a, b)):
            raise Abstain("nonfinite beta prior")
    return published, received, decision, counts[reviewed_a], counts[reviewed_b]


def _wilson(successes: int, total: int) -> tuple[float, float]:
    z = 1.96
    p = successes / total
    den = 1 + z * z / total
    mid = (p + z * z / (2 * total)) / den
    half = z * math.sqrt((p * (1 - p) + z * z / (4 * total)) / total) / den
    return max(0.0, mid - half), min(1.0, mid + half)


def make_shadow_candidate(
    obs: CountObservation,
    source: OfficialSource,
    rule: MarketRule,
    prior: FrozenPrior,
    evidence: RevisionEvidence,
    decision_at: datetime,
    *,
    simulations: int = 4096,
) -> ShadowCandidate:
    """Frozen beta-binomial sensitivity over all remaining-ballot min/max corners.

    This is a *model-dependent* probability interval with Monte Carlo uncertainty
    and stated calibration allowance, NOT verified forecast accuracy or trade EV.
    """
    published, received, decision, a_votes, b_votes = _validated(
        obs, source, rule, prior, evidence, decision_at
    )
    if type(simulations) is not int or not 512 <= simulations <= 65536:
        raise Abstain("invalid simulation support")
    token = "|".join((prior.model_id, obs.source_document_sha256, obs.revision_id))
    seed = int.from_bytes(hashlib.sha256(token.encode()).digest()[:8], "big")
    rng = np.random.default_rng(seed)
    common = rng.normal(0, prior.common_shock_sd, size=simulations)
    shares = []
    for group in obs.remaining:
        a, b = prior.beta_by_stratum[group.stratum]
        shares.append(np.clip(rng.beta(a, b, size=simulations) + common, 0, 1))
    lows: list[float] = []
    highs: list[float] = []
    estimates: list[float] = []
    for corner in itertools.product((0, 1), repeat=len(obs.remaining)):
        for_count = np.full(simulations, a_votes, dtype=np.int64)
        against_count = np.full(simulations, b_votes, dtype=np.int64)
        for group, upper, share in zip(obs.remaining, corner, shares, strict=True):
            n = group.max_count if upper else group.min_count
            added = rng.binomial(n, share)
            for_count += added
            against_count += n - added
        # A tie is not an A win; actual settlement tie rules still need review.
        won = int(np.count_nonzero(for_count > against_count))
        lo, hi = _wilson(won, simulations)
        lows.append(lo)
        highs.append(hi)
        estimates.append(won / simulations)
    interval = (
        max(0.0, min(lows) - prior.calibration_allowance),
        min(1.0, max(highs) + prior.calibration_allowance),
    )
    return ShadowCandidate(
        source_time=published,
        receive_time=received,
        target_exchange_id=rule.target_exchange_id,
        p_interval=interval,
        confidence="MODEL_DEPENDENT_UNVALIDATED",
        expiry=received + CANDIDATE_TTL,
        reason=(
            f"{obs.status}; revision={obs.revision_id}; "
            f"reported_margin={a_votes - b_votes}; "
            f"strata={len(obs.remaining)}; prior={prior.model_id}; "
            "not a settlement call or executable edge"
        ),
        source_refs=(
            obs.source_ref, source.reference_url, evidence.capture_ref, prior.evidence_ref
        ),
        decision_time=decision,
        probability_estimate=float((min(estimates) + max(estimates)) / 2),
        publication_to_receipt_ms=int((received - published).total_seconds() * 1000),
        contest_id=obs.contest_id,
        event_family=rule.event_family,
        revision_id=obs.revision_id,
        source_document_sha256=obs.source_document_sha256,
        source_id=obs.source_id,
        revision_kind=obs.revision_kind,
        reported_margin=a_votes - b_votes,
    )


def choose_observation(
    observations: list[CountObservation], decision_at: datetime
) -> CountObservation:
    """Point-in-time version choice: corrections visible only on receipt."""
    decision = utc(decision_at)
    eligible = sorted(
        (o for o in observations if utc(o.receive_time) <= decision),
        key=lambda o: (utc(o.receive_time), utc(o.source_time), o.revision_id),
    )
    if not eligible:
        raise Abstain("no observed count by decision")
    seen_revisions: set[tuple[str, str, str]] = set()
    last: dict[tuple[str, str], CountObservation] = {}
    for obs in eligible:
        key = (obs.contest_id, obs.source_id)
        revision_key = (*key, obs.revision_id)
        if revision_key in seen_revisions:
            raise Abstain("duplicate/replaced historical revision")
        seen_revisions.add(revision_key)
        prev = last.get(key)
        if prev is not None:
            if utc(obs.source_time) < utc(prev.source_time):
                raise Abstain("out-of-order publication")
            previous = {
                (c.candidate_id, c.party_id, c.ballot_choice_id): c.votes
                for c in prev.reported_choices
            }
            current = {
                (c.candidate_id, c.party_id, c.ballot_choice_id): c.votes
                for c in obs.reported_choices
            }
            if set(previous) != set(current) or len(current) != len(obs.reported_choices):
                raise Abstain("unreviewed candidate identity changed in revision")
            if (
                any(current[k] < v for k, v in previous.items())
                and obs.revision_kind != "CORRECTION"
            ):
                raise Abstain("unlabeled downward correction")
        last[key] = obs
    if len(last) != 1:
        raise Abstain("mixed contest or source; isolate episodes")
    return eligible[-1]
