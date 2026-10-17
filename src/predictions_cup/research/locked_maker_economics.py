"""Frozen, local-only Oct-2026 maker economics estimator.

Structural research calculations, NOT source admission, issuer verification,
PAPER admission or LIVE authority. No network, disk, venue or host access.
Only an independent custodian can attest the claimed issuer/use references.
"""
from __future__ import annotations

import hashlib
import random
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Literal

START = datetime(2026, 10, 1, tzinfo=UTC)
END = datetime(2026, 10, 7, tzinfo=UTC)
ZERO = Decimal("0")
Status = Literal["INTENT", "SUBMISSION", "ACK", "PARTIAL_FILL", "CANCEL_REQUEST",
                 "CANCEL_ACK", "REJECTED", "EXPIRED", "TERMINAL", "UNCERTAIN"]
FlowKind = Literal["ENTRY", "FEE", "REBATE", "EXIT", "SETTLEMENT", "CARRY_COST"]


@dataclass(frozen=True)
class SourceProof:
    table: str
    original_bytes: bytes
    original_sha256: str
    issuer_reference: str | None
    use_rights_reference: str | None
    independent_custodian_reference: str | None
    complete: bool


@dataclass(frozen=True)
class MakerAttempt:
    attempt_id: str
    order_id: str | None
    market_id: str
    event_id: str
    issuer_at: datetime
    received_at: datetime
    outcome: Literal["YES", "NO"]
    action: Literal["BUY", "SELL"]
    requested_qty: Decimal
    maker_eligible: bool
    native_eligibility_reference: str | None


@dataclass(frozen=True)
class OrderEvent:
    event_id: str
    attempt_id: str
    status: Status
    issuer_at: datetime
    received_at: datetime
    native_terminal_confirmed: bool = False


@dataclass(frozen=True)
class VenueFill:
    fill_id: str
    attempt_id: str
    order_id: str
    market_id: str
    issuer_at: datetime
    received_at: datetime
    quantity: Decimal
    price: Decimal
    native_role: Literal["MAKER", "TAKER", "UNKNOWN"]
    native_role_reference: str | None


@dataclass(frozen=True)
class InventoryCashflow:
    flow_id: str
    attempt_id: str
    fill_id: str | None
    kind: FlowKind
    amount_susq: Decimal | None
    position_delta: Decimal
    issuer_at: datetime
    received_at: datetime
    source_reference: str | None
    source_class: Literal["PAID_ACTUAL", "MIDPOINT_MARKOUT", "SIMULATED"] = "SIMULATED"
    resolution_rules_reference: str | None = None


@dataclass(frozen=True)
class QuoteMarkout:
    fill_id: str
    horizon_seconds: int
    issuer_at: datetime
    received_at: datetime
    decision_at: datetime
    executable_bid: Decimal
    executable_ask: Decimal
    valid_native_reference: str | None
    executable_bid_size: Decimal | None = None
    executable_ask_size: Decimal | None = None


@dataclass(frozen=True)
class TakerControl:
    attempt_id: str
    market_id: str
    issuer_day: str
    signed_net_susq: Decimal
    executed_quantity: Decimal
    source_reference: str | None


@dataclass(frozen=True)
class LockedBundle:
    proofs: tuple[SourceProof, ...]
    attempts: tuple[MakerAttempt, ...]
    events: tuple[OrderEvent, ...]
    fills: tuple[VenueFill, ...]
    cashflows: tuple[InventoryCashflow, ...]
    markouts: tuple[QuoteMarkout, ...] = ()
    taker_controls: tuple[TakerControl, ...] = ()
    holdout_start: datetime = datetime(2026, 10, 4, tzinfo=UTC)


@dataclass(frozen=True)
class Estimate:
    verdict: str
    reason: str
    attempts: int
    fully_filled: int
    partially_filled: int
    zero_filled: int
    censored: int
    rejected: int
    fill_count: int
    fill_quantity: Decimal
    realised_gross: Decimal | None
    realised_net: Decimal | None
    paid_fees: Decimal | None
    rebates: Decimal | None
    outstanding_quantity: Decimal | None
    training_net: Decimal | None
    holdout_net: Decimal | None
    holdout_cluster_ci95: tuple[Decimal, Decimal] | None
    taker_reference_difference: Decimal | None
    observed_position_share_days: Decimal | None
    descriptive_markout_count: int
    observed_cash_turnover_susq: Decimal | None = None
    capital_reservation_susq_days: Decimal | None = None
    descriptive_executable_markouts: tuple[tuple[int, Decimal], ...] = ()
    verified_eligible_maker_denominator: int | None = None
    verified_conservative_alpha_lower_bound: Decimal | None = None
    source_admitted: bool = False
    paper_admitted: bool = False
    network_requests: int = 0


def _fail(reason: str, count: int = 0) -> Estimate:
    return Estimate("NOT_ESTIMABLE", reason, count, 0, 0, 0, 0, 0, 0, ZERO,
                    None, None, None, None, None, None, None, None, None, None, 0)


def _clock(issuer: datetime, received: datetime) -> bool:
    return (issuer.tzinfo is not None and received.tzinfo is not None
            and issuer.utcoffset() == received.utcoffset() == timedelta(0)
            and issuer <= received)


def _amount(value: Decimal | None) -> bool:
    return type(value) is Decimal and value.is_finite()


def _unique(values: list[str]) -> bool:
    return len(values) == len(set(values)) and all(bool(x) for x in values)


def _ci95(cluster_totals: list[Decimal]) -> tuple[Decimal, Decimal] | None:
    # Deterministic nonparametric resampling of whole (market, issuer-day) clusters.
    # Descriptive only: source provenance/holdout independence remain external gates.
    if len(cluster_totals) < 5:
        return None
    rng = random.Random(20261017)
    draws = sorted(
        sum((cluster_totals[rng.randrange(len(cluster_totals))]
             for _ in cluster_totals), ZERO)
        for _ in range(2000)
    )
    return draws[49], draws[1949]


def estimate(bundle: LockedBundle) -> Estimate:
    """Calculate only when original population and ALL actual cashflows reconcile.

    All records/provenance are caller-provided. A structurally calculable output
    is NOT an independent issuer signature, investment result or admission.
    """
    attempts = bundle.attempts
    if not attempts or not START < bundle.holdout_start < END:
        return _fail("NO_COHORT_OR_INVALID_FROZEN_HOLDOUT")
    n = len(attempts)
    required = {"venue_fills", "order_lifecycle", "inventory_cashflows"}
    proofs = {p.table: p for p in bundle.proofs}
    if len(proofs) != len(bundle.proofs) or not required <= set(proofs):
        return _fail("INCOMPLETE_OR_DUPLICATE_SOURCE_MANIFEST", n)
    if bundle.markouts and "quote_markouts" not in proofs:
        return _fail("UNATTESTED_MARKOUT_SOURCE", n)
    for proof in bundle.proofs:
        if (not proof.complete or not proof.issuer_reference or not proof.use_rights_reference
                or not proof.independent_custodian_reference
                or hashlib.sha256(proof.original_bytes).hexdigest() != proof.original_sha256):
            return _fail("SOURCE_HASH_COMPLETENESS_RIGHTS_OR_ISSUER_NOT_PROVEN", n)
    amap = {a.attempt_id: a for a in attempts}
    if not _unique([a.attempt_id for a in attempts]) or not _unique(
        [a.event_id for a in attempts]
    ):
        return _fail("DUPLICATE_OR_MISSING_ATTEMPT", n)
    assigned = [a.order_id for a in attempts if a.order_id is not None]
    if not _unique(assigned):
        return _fail("NONUNIQUE_NATIVE_ORDER_ID", n)
    for a in attempts:
        if (not a.maker_eligible or not a.native_eligibility_reference
                or not a.market_id or not _clock(a.issuer_at, a.received_at)
                or not START <= a.issuer_at < END or not _amount(a.requested_qty)
                or a.requested_qty <= ZERO):
            return _fail("INCOMPLETE_MAKER_DENOMINATOR_OR_CLOCK", n)
    if not _unique([e.event_id for e in bundle.events]):
        return _fail("DUPLICATE_EVENT_IDS", n)
    events: dict[str, list[OrderEvent]] = defaultdict(list)
    for e in bundle.events:
        if (e.attempt_id not in amap or not _clock(e.issuer_at, e.received_at)
                or e.issuer_at < amap[e.attempt_id].issuer_at
                or (e.native_terminal_confirmed
                    and e.status not in {"TERMINAL", "EXPIRED", "CANCEL_ACK", "REJECTED"})):
            return _fail("INVALID_LIFECYCLE_OR_CLOCK", n)
        events[e.attempt_id].append(e)
    if not _unique([f.fill_id for f in bundle.fills]):
        return _fail("DUPLICATE_NATIVE_FILL", n)
    fills: dict[str, list[VenueFill]] = defaultdict(list)
    fill_ids = {f.fill_id for f in bundle.fills}
    for f in bundle.fills:
        attempt = amap.get(f.attempt_id)
        if (attempt is None or f.order_id != attempt.order_id
                or f.market_id != attempt.market_id
                or not _clock(f.issuer_at, f.received_at)
                or f.issuer_at < attempt.issuer_at or not _amount(f.quantity)
                or not _amount(f.price) or f.quantity <= ZERO
                or not ZERO <= f.price <= Decimal("1")
                or f.native_role != "MAKER" or not f.native_role_reference):
            return _fail("NATIVE_MAKER_ROLE_OR_FILL_IDENTITY_NOT_PROVEN", n)
        fills[f.attempt_id].append(f)
    if not _unique([c.flow_id for c in bundle.cashflows]):
        return _fail("DUPLICATE_CASHFLOW", n)
    flows: dict[str, list[InventoryCashflow]] = defaultdict(list)
    for c in bundle.cashflows:
        attempt = amap.get(c.attempt_id)
        if (attempt is None or not _clock(c.issuer_at, c.received_at)
                or c.issuer_at < attempt.issuer_at or not _amount(c.position_delta)
                or not c.source_reference
                or c.source_class != "PAID_ACTUAL"
                or (c.kind == "SETTLEMENT" and not c.resolution_rules_reference)
                or (c.fill_id is not None and c.fill_id not in fill_ids)):
            return _fail("INVALID_CASHFLOW_OR_ORPHAN_FILL", n)
        if c.fill_id is not None and all(
            f.fill_id != c.fill_id for f in fills[c.attempt_id]
        ):
            return _fail("CASHFLOW_CROSSES_ATTEMPTS", n)
        flows[c.attempt_id].append(c)

    full = partial = zero = censored = rejected = 0
    gross = fees = rebates = net = total_qty = outstanding = ZERO
    position_share_days = ZERO
    training = holdout = ZERO
    clusters: dict[tuple[str, str], Decimal] = defaultdict(lambda: ZERO)
    per_attempt_net: dict[str, Decimal] = {}
    for a in attempts:
        seq = sorted(events[a.attempt_id], key=lambda x: (x.issuer_at, x.event_id))
        if not seq or seq[0].status not in {"INTENT", "SUBMISSION", "ACK", "REJECTED"}:
            return _fail("MISSING_INITIAL_INTENT_OR_ACK", n)
        seen_ack = any(e.status == "ACK" for e in seq)
        end = seq[-1]
        terminal = end.status in {"TERMINAL", "EXPIRED", "CANCEL_ACK", "REJECTED"}
        if any(e.status == "UNCERTAIN" for e in seq) and not (
            terminal and end.native_terminal_confirmed
        ):
            censored += 1
            continue
        if not terminal or not end.native_terminal_confirmed:
            censored += 1
            continue
        if end.status == "REJECTED":
            if fills[a.attempt_id]:
                return _fail("REJECTED_ORDER_HAS_FILLS", n)
            rejected += 1
            continue
        if not seen_ack or a.order_id is None:
            return _fail("FILLED_OR_TERMINAL_WITHOUT_NATIVE_ACK", n)
        fs = fills[a.attempt_id]
        qty = sum((f.quantity for f in fs), ZERO)
        if fs and max(f.issuer_at for f in fs) > end.issuer_at:
            return _fail("FILL_OCCURS_AFTER_NATIVE_TERMINAL", n)
        if qty > a.requested_qty:
            return _fail("PARTIAL_FILL_EXCEEDS_ORDER", n)
        if qty == ZERO:
            if flows[a.attempt_id]:
                return _fail("NO_FILL_WITH_ECONOMIC_FLOWS", n)
            zero += 1
            continue
        if qty == a.requested_qty:
            full += 1
        else:
            partial += 1
        total_qty += qty
        cf = flows[a.attempt_id]
        if not cf or any(c.amount_susq is None or not _amount(c.amount_susq) for c in cf):
            return _fail("MISSING_ACTUAL_FEE_OR_EXIT_CASHFLOW", n)
        entry = [c for c in cf if c.kind == "ENTRY"]
        charged = [c for c in cf if c.kind == "FEE"]
        endflows = [c for c in cf if c.kind in {"EXIT", "SETTLEMENT"}]
        if not charged or not entry or not endflows:
            return _fail("MISSING_REAL_FEE_ENTRY_OR_DISPOSAL", n)
        direction = Decimal("1") if a.action == "BUY" else Decimal("-1")
        if any(sum((c.position_delta for c in entry if c.fill_id == f.fill_id),
                   ZERO) != direction * f.quantity for f in fs):
            return _fail("PER_FILL_ENTRY_CASH_NOT_RECONCILED", n)
        if (sum((c.position_delta for c in entry), ZERO) != direction * qty
                or sum((c.position_delta for c in endflows), ZERO) != -direction * qty
                or any(c.position_delta != ZERO for c in cf if c.kind not in
                       {"ENTRY", "EXIT", "SETTLEMENT"})
                or any(c.fill_id is None for c in entry)
                or any(c.amount_susq != -f.quantity * f.price * direction
                       for f in fs for c in entry if c.fill_id == f.fill_id)
                or any(c.position_delta * direction <= ZERO for c in entry)
                or any(c.position_delta * direction >= ZERO for c in endflows)):
            return _fail("POSITION_OR_PARTIAL_FILL_CASH_CONSERVATION_FAIL", n)
        if any(c.kind in {"ENTRY", "EXIT", "SETTLEMENT"} and
               c.amount_susq is not None and
               (c.amount_susq >= ZERO if c.position_delta * direction > ZERO
                else c.amount_susq <= ZERO) for c in cf):
            return _fail("INCONSISTENT_SIGNED_CASH_OR_HELD_NO_BASIS", n)
        # Filled entry/exit position quantities are signed; native NO is held
        # directly, never converted to YES by a guessed complement price.
        g = sum((c.amount_susq for c in cf if c.kind in
                 {"ENTRY", "EXIT", "SETTLEMENT"} and c.amount_susq is not None), ZERO)
        fcost = sum((c.amount_susq for c in cf if c.kind in
                     {"FEE", "CARRY_COST"} and c.amount_susq is not None), ZERO)
        reb = sum((c.amount_susq for c in cf if c.kind == "REBATE"
                   and c.amount_susq is not None), ZERO)
        if fcost > ZERO or reb < ZERO:
            return _fail("FEE_REBATE_SIGN_CONFLICT", n)
        per = g + fcost + reb
        gross += g
        fees += fcost
        rebates += reb
        net += per
        per_attempt_net[a.attempt_id] = per
        last_exit = max(c.issuer_at for c in endflows)
        position_share_days += qty * Decimal(
            str((last_exit - a.issuer_at).total_seconds())
        ) / Decimal("86400")
        if a.issuer_at < bundle.holdout_start:
            if last_exit >= bundle.holdout_start:
                return _fail("CHRONOLOGICAL_HOLDOUT_OUTCOME_LEAKAGE", n)
            training += per
        else:
            holdout += per
            clusters[(a.market_id, a.issuer_at.date().isoformat())] += per
    if censored:
        # The sample includes unresolved eligible maker attempts; this must
        # not silently become a zero-fill or closed-position profitable cohort.
        return Estimate("NOT_ESTIMABLE", "CENSORED_MAKER_ATTEMPTS_NOT_RESOLVED",
                        n, full, partial, zero, censored, rejected, len(bundle.fills),
                        total_qty, None, None, None, None, None, None, None, None,
                        None, None, 0)
    if len(per_attempt_net) != full + partial:
        return _fail("INCOMPLETE_FILL_TO_SETTLED_CASH_LEDGER", n)
    by_fill_id = {f.fill_id: f for f in bundle.fills}
    descriptive_marks: dict[int, Decimal] = defaultdict(lambda: ZERO)
    for m in bundle.markouts:
        mark_fill = by_fill_id.get(m.fill_id)
        if (mark_fill is None or not m.valid_native_reference
                or not _clock(m.issuer_at, m.received_at)
                or m.decision_at.tzinfo is None
                or m.decision_at.utcoffset() != timedelta(0)
                or m.decision_at > mark_fill.issuer_at
                or m.horizon_seconds not in {5, 30, 300, 3600}
                or not _amount(m.executable_bid)
                or not _amount(m.executable_ask)
                or not _amount(m.executable_bid_size)
                or not _amount(m.executable_ask_size)
                or m.executable_bid_size < mark_fill.quantity
                or m.executable_ask_size < mark_fill.quantity
                or not ZERO <= m.executable_bid <= m.executable_ask <= Decimal("1")
                or not ZERO <= (m.issuer_at - mark_fill.issuer_at).total_seconds()
                - m.horizon_seconds <= 2
                or m.received_at > mark_fill.issuer_at
                + timedelta(seconds=m.horizon_seconds + 2)):
            return _fail("UNVERIFIED_CAUSAL_EXECUTABLE_MARKOUT", n)
        a = amap[mark_fill.attempt_id]
        exit_px = m.executable_bid if a.action == "BUY" else m.executable_ask
        per_share = exit_px - mark_fill.price if a.action == "BUY" else mark_fill.price - exit_px
        descriptive_marks[m.horizon_seconds] += per_share * mark_fill.quantity
    taker_diff: Decimal | None = None
    if bundle.taker_controls:
        control_by_attempt = {t.attempt_id: t for t in bundle.taker_controls}
        if len(control_by_attempt) != len(bundle.taker_controls):
            return _fail("DUPLICATE_TAKER_REFERENCE", n)
        if any(not t.source_reference or not _amount(t.signed_net_susq)
               or not _amount(t.executed_quantity)
               or t.attempt_id not in per_attempt_net
               or t.market_id != amap[t.attempt_id].market_id
               or t.issuer_day != amap[t.attempt_id].issuer_at.date().isoformat()
               or t.executed_quantity != sum(
                   (f.quantity for f in fills[t.attempt_id]), ZERO
               ) for t in bundle.taker_controls):
            return _fail("UNMATCHED_TAKER_CONTROL", n)
        taker_diff = sum(
            (per_attempt_net[t.attempt_id] - t.signed_net_susq
             for t in bundle.taker_controls), ZERO
        )
    # No-quote incremental NEW-order cashflow baseline = 0 by definition.
    # It is NOT a measured no-trade inventory/payoff opportunity counterfactual.
    return Estimate(
        "SYNTHETIC_OR_STRUCTURAL_ONLY_NOT_SOURCE_ADMISSION", "REAL_WORLD_PROVENANCE_EXTERNAL",
        n, full, partial, zero, 0, rejected, len(bundle.fills),
        total_qty, gross, net, fees, rebates, outstanding,
        training, holdout, _ci95(list(clusters.values())), taker_diff,
        position_share_days, len(bundle.markouts),
        sum((abs(c.amount_susq) for c in bundle.cashflows
             if c.kind in {"ENTRY", "EXIT", "SETTLEMENT"}
             and c.amount_susq is not None), ZERO),
        None, tuple(sorted(descriptive_marks.items())),
    )
