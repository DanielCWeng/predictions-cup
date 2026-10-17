"""Offline-only causal 005I label CONTRACT, never an execution or source-admission engine.

Delegates immutable archive, SIG/PM mapping, seed/epoch/sequence, rights *shape* and
source-hash validation to #277 validate_offline_tape. An untrusted candidate
projection is NOT cryptographically bound to that archive by GateResult; only
independent operator provenance can bind original records. Consequently this
module cannot issue REAL_AFTER_COST labels or PAPER eligibility.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import ROUND_DOWN, Decimal
from pathlib import Path
from typing import Literal

from predictions_cup.research.offline_paired_tape_gate import (
    GateResult,
    validate_offline_tape,
)

_HEX64 = re.compile(r"[0-9a-f]{64}\Z")
_SIZES = frozenset({1, 5, 10, 20})
_D = Decimal


@dataclass(frozen=True)
class FrozenDecision:
    model_sha256: str
    policy_sha256: str
    split: Literal["TRAIN", "DEV", "HOLDOUT"]
    horizon_seconds: int
    signal_threshold: Decimal


@dataclass(frozen=True)
class Level:
    price: Decimal
    shares: Decimal


@dataclass(frozen=True)
class Book:
    exchange_id: str
    market_id: str
    native_book_id: str
    sequence: int
    epoch: int
    publisher_at: datetime
    received_at: datetime
    uncertainty_ms: int
    bids: tuple[Level, ...]  # Canonical SIG YES bids, descending.
    asks: tuple[Level, ...]  # Canonical SIG YES asks, ascending.


@dataclass(frozen=True)
class CompletedMinute:
    ended_at: datetime
    received_at: datetime
    complete: bool


@dataclass(frozen=True)
class FeeRecord:
    source_class: Literal["HYPOTHETICAL", "OPERATOR_RECORDED"]
    native_fee_id: str
    paid_susqies: Decimal
    maker_taker_role: str


@dataclass(frozen=True)
class SettlementRecord:
    source_class: Literal["HYPOTHETICAL", "OPERATOR_RECORDED"]
    published_at: datetime
    received_at: datetime
    status: str  # SETTLED; VOID/RECOUNT never inferred as paid cash.
    cash_per_share: Decimal


@dataclass(frozen=True)
class DecisionCandidate:
    candidate_id: str
    paired_interval_id: str
    event_id: str
    episode_id: str
    frozen: FrozenDecision
    sig_exchange_id: str
    sig_market_id: str
    pm_condition_id: str
    pm_token_id: str
    sig_outcome: str  # Canonical YES book only, not a sibling-party YES.
    pm_outcome: str
    mapping_direction: str
    buy_side: Literal["YES", "NO"]
    quantity: int
    depth_haircut: Decimal
    decision_at: datetime
    latency_ms: int
    max_age_ms: int
    max_uncertainty_ms: int
    pm_minutes: tuple[CompletedMinute, ...]
    decision_book: Book
    arrival_book: Book
    exit_at: datetime
    exit_book: Book | None
    settlement: SettlementRecord | None
    fee_record: FeeRecord | None


@dataclass(frozen=True)
class CandidateLabel:
    candidate_id: str
    status: str
    rejection: str | None
    economic_rejections: tuple[str, ...]
    cluster_key: str
    share_count: int
    hypothetical_entry_limit: Decimal | None = None
    hypothetical_exit_proceeds: Decimal | None = None
    real_after_cost_susqies: None = None
    source_admitted: bool = False
    paper_admitted: bool = False


@dataclass(frozen=True)
class LabelBatch:
    tape_gate: GateResult
    candidates: tuple[CandidateLabel, ...]
    rejection_counts: tuple[tuple[str, int], ...]
    no_trade_control_count: int
    admitted_real_net_susqies: None = None
    source_admitted: bool = False
    paper_admitted: bool = False
    network_requests: int = 0


class Reject(ValueError):
    """Fail-closed independent decision/whole-lot/causal label reason."""


def _check(ok: bool, code: str) -> None:
    if not ok:
        raise Reject(code)


def _utc(t: datetime, code: str) -> None:
    _check(t.tzinfo is not None and t.utcoffset() == timedelta(0), code)


def _book(book: Book, candidate: DecisionCandidate, at: datetime, stage: str) -> None:
    _utc(book.publisher_at, "NAIVE_OR_NONUTC_PUBLISHER")
    _utc(book.received_at, "NAIVE_OR_NONUTC_RECEIPT")
    _check(
        book.exchange_id == candidate.sig_exchange_id
        and book.market_id == candidate.sig_market_id
        and bool(book.native_book_id) and book.sequence > 0 and book.epoch > 0,
        "NATIVE_BOOK_IDENTITY_MISMATCH",
    )
    _check(0 <= book.uncertainty_ms <= candidate.max_uncertainty_ms,
           "CLOCK_UNCERTAINTY_EXCEEDED")
    _check(book.received_at <= at, f"FUTURE_{stage}_BOOK")
    _check(
        book.publisher_at <= book.received_at
        + timedelta(milliseconds=book.uncertainty_ms),
        "NONCAUSAL_PUBLISHER_RECEIPT",
    )
    _check(
        at - book.publisher_at + timedelta(milliseconds=book.uncertainty_ms)
        <= timedelta(milliseconds=candidate.max_age_ms),
        f"STALE_{stage}_BOOK",
    )
    _check(bool(book.bids) and bool(book.asks), "MISSING_FULL_L2")
    for levels, desc in ((book.bids, True), (book.asks, False)):
        previous: Decimal | None = None
        for level in levels:
            _check(level.price.is_finite() and _D(0) < level.price < _D(1)
                   and level.shares.is_finite() and level.shares > 0,
                   "INVALID_L2_LEVEL")
            if previous is not None:
                _check(level.price < previous if desc else level.price > previous,
                       "UNSORTED_OR_DUPLICATE_L2")
            previous = level.price
    _check(book.bids[0].price < book.asks[0].price, "CROSSED_BOOK")


def _walk(
    levels: tuple[Level, ...], quantity: int, haircut: Decimal,
    *, complement: bool,
) -> tuple[Decimal, Decimal]:
    """Conservative synthetic whole-level floor; NO price=1−YES level."""
    remaining = _D(quantity)
    proceeds_or_cost = _D(0)
    marginal = _D(0)
    for level in levels:
        eligible = (level.shares * haircut).to_integral_value(rounding=ROUND_DOWN)
        take = min(remaining, eligible)
        price = _D(1) - level.price if complement else level.price
        proceeds_or_cost += take * price
        if take > 0:
            marginal = price
        remaining -= take
        if remaining == 0:
            return marginal, proceeds_or_cost
    raise Reject("WHOLE_LOT_DEPTH_UNAVAILABLE")


def _label(c: DecisionCandidate, seen: dict[tuple[str, str], datetime]) -> CandidateLabel:
    key = f"{c.event_id}:{c.sig_market_id}:{c.decision_at.date().isoformat()}"
    try:
        _check(bool(c.candidate_id) and bool(c.paired_interval_id)
               and bool(c.event_id) and bool(c.episode_id), "MISSING_DECISION_IDENTITY")
        _check(bool(_HEX64.fullmatch(c.frozen.model_sha256))
               and bool(_HEX64.fullmatch(c.frozen.policy_sha256)),
               "UNPINNED_FROZEN_MODEL_OR_POLICY")
        _check(c.frozen.split in ("TRAIN", "DEV", "HOLDOUT")
               and type(c.frozen.horizon_seconds) is int and c.frozen.horizon_seconds > 0
               and c.frozen.signal_threshold.is_finite()
               and c.frozen.signal_threshold > 0, "UNFROZEN_SIGNAL")
        _check(type(c.quantity) is int and c.quantity in _SIZES, "INVALID_WHOLE_SHARE_LOT")
        _check(c.depth_haircut.is_finite() and _D(0) < c.depth_haircut <= 1,
               "INVALID_CONSERVATIVE_HAIRCUT")
        _check(type(c.latency_ms) is int and c.latency_ms > 0
               and c.max_age_ms > 0 and c.max_uncertainty_ms >= 0,
               "INVALID_CLOCK_OR_LATENCY_BUDGET")
        _utc(c.decision_at, "NAIVE_DECISION_CLOCK")
        _utc(c.exit_at, "NAIVE_EXIT_CLOCK")
        _check(c.exit_at >= c.decision_at
               + timedelta(seconds=c.frozen.horizon_seconds),
               "EXIT_BEFORE_FROZEN_HORIZON")
        _check(c.sig_outcome == "YES" and c.pm_outcome in ("YES", "NO")
               and c.mapping_direction in ("SAME", "COMPLEMENT")
               and ((c.pm_outcome == "YES") == (c.mapping_direction == "SAME"))
               and all((c.sig_exchange_id, c.sig_market_id,
                        c.pm_condition_id, c.pm_token_id)), "MAPPING_ORIENTATION_INVALID")
        _check(c.buy_side in ("YES", "NO"), "WRONG_NO_ORIENTATION")
        _check(len(c.pm_minutes) == 6, "PM_SIX_COMPLETED_MINUTES_MISSING")
        for i, m in enumerate(c.pm_minutes):
            _utc(m.ended_at, "NAIVE_PM_MINUTE_CLOCK")
            _utc(m.received_at, "NAIVE_PM_MINUTE_RECEIPT")
            _check(m.complete and m.ended_at <= m.received_at <= c.decision_at,
                   "PM_MINUTE_FUTURE_LEAK")
            if i > 0:
                _check(m.ended_at - c.pm_minutes[i - 1].ended_at
                       == timedelta(minutes=1), "PM_MINUTE_SEQUENCE_GAP")
        _check(c.pm_minutes[-1].ended_at >= c.decision_at - timedelta(minutes=1),
               "PM_MINUTE_STALE")
        overlap_key = (c.event_id, c.sig_market_id)
        _check(overlap_key not in seen or c.decision_at >= seen[overlap_key],
               "OVERLAPPING_EVENT_EPISODE")
        arrival_at = c.decision_at + timedelta(milliseconds=c.latency_ms)
        _book(c.decision_book, c, c.decision_at, "DECISION")
        _book(c.arrival_book, c, arrival_at, "ARRIVAL")
        _check(c.arrival_book.received_at > c.decision_at
               and c.arrival_book.epoch == c.decision_book.epoch
               and c.arrival_book.sequence > c.decision_book.sequence
               and c.arrival_book.native_book_id != c.decision_book.native_book_id,
               "ARRIVAL_NOT_NEW_CAUSAL_BOOK")
        entry_levels = (
            c.decision_book.asks if c.buy_side == "YES" else c.decision_book.bids
        )
        limit, _ = _walk(entry_levels, c.quantity, c.depth_haircut,
                         complement=c.buy_side == "NO")
        arrival_levels = (
            c.arrival_book.asks if c.buy_side == "YES" else c.arrival_book.bids
        )
        arrival_marginal, _ = _walk(arrival_levels, c.quantity, c.depth_haircut,
                                    complement=c.buy_side == "NO")
        _check(arrival_marginal <= limit, "LATENCY_IOC_LIMIT_BREACHED")
        if c.exit_book is None and c.settlement is None:
            raise Reject("EXIT_NOT_AVAILABLE")
        if c.exit_book is not None and c.settlement is not None:
            raise Reject("AMBIGUOUS_EXIT_VS_SETTLEMENT")
        exit_value: Decimal
        if c.exit_book is not None:
            _book(c.exit_book, c, c.exit_at, "EXIT")
            _check(c.exit_book.received_at > arrival_at
                   and c.exit_book.epoch == c.arrival_book.epoch
                   and c.exit_book.sequence > c.arrival_book.sequence,
                   "EXIT_NOT_NEW_CAUSAL_BOOK")
            exit_levels = c.exit_book.bids if c.buy_side == "YES" else c.exit_book.asks
            _, exit_value = _walk(exit_levels, c.quantity, c.depth_haircut,
                                  complement=c.buy_side == "NO")
        else:
            assert c.settlement is not None
            _utc(c.settlement.published_at, "NAIVE_SETTLEMENT_PUBLISHER")
            _utc(c.settlement.received_at, "NAIVE_SETTLEMENT_RECEIPT")
            _check(c.settlement.status == "SETTLED"
                   and c.settlement.published_at <= c.settlement.received_at <= c.exit_at
                   and c.settlement.cash_per_share.is_finite()
                   and _D(0) <= c.settlement.cash_per_share <= 1,
                   "SETTLEMENT_NOT_PROVEN")
            exit_value = c.settlement.cash_per_share * c.quantity
        issues = ["SOURCE_NOT_EXTERNALLY_AUTHENTICATED", "CLOSED_CASH_NOT_ADMITTED"]
        if c.fee_record is None:
            issues.append("NATIVE_FEE_MISSING")
        else:
            _check(bool(c.fee_record.native_fee_id)
                   and c.fee_record.paid_susqies.is_finite()
                   and c.fee_record.paid_susqies >= 0
                   and c.fee_record.maker_taker_role in ("MAKER", "TAKER"),
                   "INVALID_FEE_RECORD")
            issues.append("FEE_PROOF_NOT_EXTERNALLY_ADMITTED")
        # Only source-shape scenarios can be positive; real profit ALWAYS NULL.
        seen[overlap_key] = (
            c.decision_at + timedelta(seconds=c.frozen.horizon_seconds)
        )
        return CandidateLabel(
            c.candidate_id, "SCHEMA_ONLY/PAPER_NOT_ADMITTED", None, tuple(issues),
            key, c.quantity, limit, exit_value,
        )
    except (Reject, ArithmeticError, TypeError, ValueError) as exc:
        reason = str(exc) if isinstance(exc, Reject) else "INVALID_CANDIDATE_INPUT"
        return CandidateLabel(c.candidate_id, "REJECTED/PAPER_NOT_ADMITTED",
                              reason, ("REAL_NET_NOT_ESTIMABLE",), key, c.quantity)


def label_offline_candidates(
    manifest_path: Path, mapping_path: Path, sig_path: Path, pm_path: Path,
    *, expected_manifest_sha256: str, candidates: tuple[DecisionCandidate, ...],
) -> LabelBatch:
    """0-network consumer; invokes #277 gate and always refuses LIVE/PAPER/REAL net.

    GateResult does NOT expose bound row bytes or issuer authenticity. Caller-built
    candidate projections are thus SCHEMA_ONLY, not admissions or real cash labels.
    """
    gate = validate_offline_tape(
        manifest_path, mapping_path, sig_path, pm_path,
        expected_manifest_sha256=expected_manifest_sha256,
    )
    seen: dict[tuple[str, str], datetime] = {}
    labels: list[CandidateLabel] = []
    for c in candidates:
        if gate.verdict != "SCHEMA_ONLY/PAPER_NOT_ADMITTED":
            reason = gate.rejections[0][1] if gate.rejections else "PAIRED_TAPE_DENIED"
            labels.append(CandidateLabel(
                c.candidate_id, "REJECTED/PAPER_NOT_ADMITTED",
                f"UPSTREAM_PAIRED_TAPE:{reason}", ("REAL_NET_NOT_ESTIMABLE",),
                f"{c.event_id}:{c.sig_market_id}", c.quantity,
            ))
        else:
            labels.append(_label(c, seen))
    counts = Counter(label.rejection for label in labels if label.rejection)
    return LabelBatch(gate, tuple(labels), tuple(sorted(counts.items())),
                      len(candidates))
