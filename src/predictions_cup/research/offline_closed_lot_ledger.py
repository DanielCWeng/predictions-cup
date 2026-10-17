"""Offline *unadmitted* original-issuer lot/capital evidence contract.

For #225. No file/network/client/venue access and NO source admission.
The caller can invent every supplied string, byte and SHA; even an internally
consistent alleged original is NOT authenticated issuer evidence. All outputs
are research-only. No bridge to #301 or #304 and no trading/ledger writes.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import timedelta, timezone, datetime
from decimal import Decimal
from typing import Literal

ZERO = Decimal("0")
DAY_SECONDS = Decimal("86400")
Outcome = Literal["YES", "NO"]
EndState = Literal["TERMINAL", "UNCERTAIN", "ACK", "CANCEL_REQUEST"]
DisposalKind = Literal["EXIT", "SETTLEMENT", "VOID", "RECOUNT"]
DatasetKind = Literal["SYNTHETIC", "AGGREGATE", "UNVERIFIED_ORIGINAL"]


@dataclass(frozen=True)
class OriginalDocument:
    key: str
    issuer_reference: str
    raw_bytes: bytes
    original_sha256: str
    issuer_rule_version: str
    source_namespace_reference: str | None
    permitted_use_reference: str | None
    independent_custodian_reference: str | None
    issuer_at: datetime
    received_at: datetime
    revision: str


@dataclass(frozen=True)
class MakerAttempt:
    key: str
    native_order_id: str
    tournament: str
    exchange: str
    market: str
    outcome: Outcome
    requested_whole_shares: Decimal
    native_role: Literal["MAKER", "TAKER", "UNKNOWN"]
    native_role_reference: str | None
    terminal_state: EndState
    native_terminal_reference: str | None
    reserved_gross_shares: Decimal
    source_key: str


@dataclass(frozen=True)
class NativeEntryFill:
    key: str
    attempt_key: str
    native_order_id: str
    lot_key: str
    whole_shares: Decimal
    original_side_price: Decimal
    original_cash_paid: Decimal | None
    paid_fee_susq: Decimal | None
    rebate_susq: Decimal | None
    fee_currency: str | None
    source_key: str


@dataclass(frozen=True)
class NativeLotClosure:
    key: str
    lot_key: str
    whole_shares: Decimal
    kind: DisposalKind
    original_cash_received: Decimal | None
    paid_fee_susq: Decimal | None
    rebate_susq: Decimal | None
    fee_currency: str | None
    settlement_rule_version: str | None
    issuer_terminal_reference: str | None
    final: bool
    source_key: str


@dataclass(frozen=True)
class AccountFrame:
    source_key: str
    asof_utc: datetime
    start_equity_susq: Decimal | None
    external_flows_susq: Decimal | None
    account_realised_susq: Decimal | None
    unrealised_change_susq: Decimal | None
    end_equity_susq: Decimal | None
    gross_shares: Decimal | None
    reserved_shares: Decimal | None
    gross_limit: Decimal | None


@dataclass(frozen=True)
class CapitalSlice:
    source_key: str
    start_utc: datetime
    end_utc: datetime
    gross_shares: Decimal
    reserved_shares: Decimal
    gross_limit: Decimal
    unresolved_reservations: bool


@dataclass(frozen=True)
class OfflineBundle:
    kind: DatasetKind
    sources: tuple[OriginalDocument, ...]
    attempts: tuple[MakerAttempt, ...]
    fills: tuple[NativeEntryFill, ...]
    closures: tuple[NativeLotClosure, ...]
    account: AccountFrame | None
    capital_slices: tuple[CapitalSlice, ...]
    capital_window: tuple[datetime, datetime] | None
    native_namespace_policy_version: str | None = None
    source_version: str = "offline-lot-ledger-001-v1"


@dataclass(frozen=True)
class AuditResult:
    verdict: Literal["SCHEMA_ONLY", "REJECTED", "INCOMPLETE"]
    reasons: tuple[str, ...]
    descriptive_closed_lot_net_susq: Decimal | None
    gross_share_days: Decimal | None
    account_identity_pass: bool | None
    eligible_attempts_descriptive: int | None
    maker_closed_lots_descriptive: int
    realised_maker_net_susq: None = None
    independently_verified_maker_denominator: None = None
    source_admitted: Literal[False] = False
    paper_admitted: Literal[False] = False
    real_alpha: None = None


def _number(n: object, *, nonnegative: bool = True, whole: bool = False) -> bool:
    if type(n) is not Decimal or not n.is_finite():
        return False
    if nonnegative and n < ZERO:
        return False
    return not whole or n == n.to_integral_value()


def _utc(t: datetime) -> bool:
    return (t.tzinfo is not None and t.utcoffset() == timedelta(0)
            and t.tzinfo is not None and t.tzinfo != timezone(timedelta(hours=1)))


def audit_offline(bundle: OfflineBundle) -> AuditResult:
    """Check arithmetic and identity only; impossible to self-certify REAL SIG alpha."""
    errors: set[str] = set()
    holes: set[str] = set()
    docs = {s.key: s for s in bundle.sources}

    if len(docs) != len(bundle.sources) or not docs:
        errors.add("DUPLICATE_OR_ABSENT_SOURCE")
    if bundle.kind != "SYNTHETIC":
        holes.add("EXTERNAL_ISSUER_CUSTODIAN_RIGHTS_AUTHENTICATION_REQUIRED")
    if bundle.kind == "AGGREGATE":
        holes.add("AGGREGATE_IS_NOT_ORIGINAL_LOT_EVIDENCE")
    if bundle.native_namespace_policy_version is None:
        holes.add("ORIGINAL_NATIVE_ORDER_FILL_NAMESPACE_UNVERIFIED")
    if bundle.source_version != "offline-lot-ledger-001-v1":
        errors.add("SOURCE_CONTRACT_VERSION_MISMATCH")
    for d in bundle.sources:
        if not d.key or not d.issuer_reference or not d.issuer_rule_version or not d.revision:
            holes.add("ORIGINAL_ISSUER_RULE_AND_REVISION_REQUIRED")
        if (not isinstance(d.raw_bytes, bytes)
                or hashlib.sha256(d.raw_bytes).hexdigest() != d.original_sha256):
            errors.add("SOURCE_BYTES_SHA256_MISMATCH")
        if not _utc(d.issuer_at) or not _utc(d.received_at) or d.issuer_at > d.received_at:
            errors.add("ISSUER_RECEIPT_CLOCK_INVALID")
        if (not d.permitted_use_reference or not d.independent_custodian_reference
                or not d.source_namespace_reference):
            holes.add("ORIGINAL_RIGHTS_CUSTODIAN_NAMESPACE_EVIDENCE_REQUIRED")

    def issuer(source_key: str) -> datetime | None:
        d = docs.get(source_key)
        if d is None:
            errors.add("UNKNOWN_ORIGINAL_SOURCE_KEY")
            return None
        return d.issuer_at

    attempts = {a.key: a for a in bundle.attempts}
    if len(attempts) != len(bundle.attempts):
        errors.add("DUPLICATE_ATTEMPT")
    native_orders: dict[str, MakerAttempt] = {}
    for a in bundle.attempts:
        issuer(a.source_key)
        if not a.key or not a.native_order_id or not all(
            (a.tournament, a.exchange, a.market)
        ):
            errors.add("MISSING_ORDER_OR_MARKET_IDENTITY")
        prior = native_orders.get(a.native_order_id)
        if prior is not None and prior.key != a.key:
            errors.add("NATIVE_ORDER_COLLISION_SAME_TOURNAMENT" if prior.tournament == a.tournament
                       else "NATIVE_ORDER_COLLISION_CROSS_TOURNAMENT_SCOPE_UNKNOWN")
        native_orders[a.native_order_id] = a
        if not _number(a.requested_whole_shares, whole=True) or a.requested_whole_shares <= ZERO:
            errors.add("INVALID_WHOLE_ORDER_SIZE")
        if not _number(a.reserved_gross_shares, whole=True):
            errors.add("INVALID_RESERVED_GROSS")
        if a.native_role != "MAKER" or not a.native_role_reference:
            holes.add("NATIVE_MAKER_ROLE_UNPROVEN")
        if a.terminal_state != "TERMINAL" or not a.native_terminal_reference:
            holes.add("UNCERTAIN_OR_UNATTESTED_TERMINAL_STATE")

    fills = {f.key: f for f in bundle.fills}
    lots = {f.lot_key: f for f in bundle.fills}
    if len(fills) != len(bundle.fills) or len(lots) != len(bundle.fills):
        errors.add("DUPLICATE_NATIVE_FILL_OR_LOT")
    qty_by_attempt: dict[str, Decimal] = {}
    for f in bundle.fills:
        issuer(f.source_key)
        a = attempts.get(f.attempt_key)
        if a is None or not f.key or not f.lot_key or f.native_order_id != a.native_order_id:
            errors.add("FILL_NOT_JOINED_TO_ORIGINAL_ORDER")
        for x in (f.whole_shares, f.original_side_price):
            if not _number(x):
                errors.add("INVALID_NATIVE_FILL_DECIMAL")
        if not _number(f.whole_shares, whole=True) or f.whole_shares <= ZERO:
            errors.add("INVALID_WHOLE_FILL_SIZE")
        if _number(f.original_side_price) and f.original_side_price > Decimal("1"):
            errors.add("INVALID_NATIVE_PRICE")
        if not _number(f.original_cash_paid):
            holes.add("MISSING_ORIGINAL_ENTRY_CASH")
        elif (_number(f.whole_shares) and _number(f.original_side_price)
              and f.original_cash_paid != f.whole_shares * f.original_side_price):
            errors.add("ORIGINAL_PAID_CASH_PRICE_QTY_MISMATCH")
        if not (_number(f.paid_fee_susq) and _number(f.rebate_susq)
                and f.fee_currency == "SUSQ"):
            holes.add("MISSING_NATIVE_PAID_FEE_REBATE_OR_CURRENCY")
        if _number(f.whole_shares):
            qty_by_attempt[f.attempt_key] = qty_by_attempt.get(f.attempt_key, ZERO) + f.whole_shares
    for key, q in qty_by_attempt.items():
        a = attempts.get(key)
        if a is not None and _number(a.requested_whole_shares) and q > a.requested_whole_shares:
            errors.add("FILL_EXCEEDS_REQUESTED_WHOLE_SHARES")

    exits_by_lot: dict[str, Decimal] = {}
    disposal_keys: set[str] = set()
    net = ZERO
    all_cash_known = bool(bundle.fills) and bool(bundle.closures)
    for f in bundle.fills:
        if (_number(f.original_cash_paid) and _number(f.paid_fee_susq)
                and _number(f.rebate_susq)):
            net += -f.original_cash_paid - f.paid_fee_susq + f.rebate_susq
        else:
            all_cash_known = False
    for x in bundle.closures:
        exit_at = issuer(x.source_key)
        entry = lots.get(x.lot_key)
        if x.key in disposal_keys or not x.key or entry is None:
            errors.add("DUPLICATE_OR_ORPHAN_CLOSURE")
        disposal_keys.add(x.key)
        entry_at = docs.get(entry.source_key).issuer_at if entry and entry.source_key in docs else None
        if exit_at is not None and entry_at is not None and exit_at <= entry_at:
            errors.add("EXIT_BEFORE_OR_AT_FILL")
        if (not _number(x.whole_shares, whole=True) or x.whole_shares <= ZERO):
            errors.add("INVALID_WHOLE_EXIT_SIZE")
        if x.kind in ("SETTLEMENT", "VOID", "RECOUNT") and (
            not x.settlement_rule_version or not x.issuer_terminal_reference
        ):
            holes.add("MISSING_VOID_RECOUNT_SETTLEMENT_ORIGINAL_RULE")
        if not x.final or not x.issuer_terminal_reference:
            holes.add("UNCLOSED_VOID_RECOUNT_OR_UNCERTAIN_EXIT")
        if not (_number(x.original_cash_received) and _number(x.paid_fee_susq)
                and _number(x.rebate_susq) and x.fee_currency == "SUSQ"):
            holes.add("MISSING_ORIGINAL_EXIT_CASH_FEE_REBATE_OR_CURRENCY")
            all_cash_known = False
        else:
            net += x.original_cash_received - x.paid_fee_susq + x.rebate_susq
        if _number(x.whole_shares):
            exits_by_lot[x.lot_key] = exits_by_lot.get(x.lot_key, ZERO) + x.whole_shares
    for f in bundle.fills:
        if exits_by_lot.get(f.lot_key, ZERO) != f.whole_shares:
            holes.add("OPEN_OR_PARTIALLY_CLOSED_OR_OVERFILLED_LOT")
        if exits_by_lot.get(f.lot_key, ZERO) > f.whole_shares:
            errors.add("EXIT_EXCEEDS_OPEN_LOT")

    account_pass: bool | None = None
    a = bundle.account
    if a is None:
        holes.add("MISSING_SAME_CLOCK_ACCOUNT_EQUITY")
    else:
        stamp = issuer(a.source_key)
        if stamp is not None and (a.asof_utc != stamp or not _utc(a.asof_utc)):
            errors.add("ACCOUNT_ASOF_NOT_ISSUER_SNAPSHOT_CLOCK")
        vals = (a.start_equity_susq, a.external_flows_susq, a.account_realised_susq,
                a.unrealised_change_susq, a.end_equity_susq)
        if all(_number(v, nonnegative=False) for v in vals):
            account_pass = vals[0] + vals[1] + vals[2] + vals[3] == vals[4]  # type: ignore[operator]
            if not account_pass:
                errors.add("ACCOUNT_DOUBLE_ENTRY_MISMATCH")
        else:
            holes.add("MISSING_ACCOUNT_PNL_OR_EXTERNAL_FLOW")
        if not all(_number(v, whole=True) for v in (a.gross_shares, a.reserved_shares)):
            holes.add("MISSING_SAME_CLOCK_GROSS_RESERVED_CAPITAL")
        elif not _number(a.gross_limit, whole=True) or (
            a.gross_shares + a.reserved_shares > a.gross_limit  # type: ignore[operator]
        ):
            errors.add("GROSS_CAP_OR_RESERVATION_BREACH")

    share_days: Decimal | None = None
    periods = sorted(bundle.capital_slices, key=lambda s: s.start_utc)
    if bundle.capital_window is None or not periods:
        holes.add("MISSING_FULL_COVERAGE_GROSS_SHARE_DAY_WINDOW")
    else:
        begin, end = bundle.capital_window
        if (not _utc(begin) or not _utc(end) or begin >= end
                or periods[0].start_utc != begin or periods[-1].end_utc != end):
            holes.add("INCOMPLETE_GROSS_SHARE_DAY_WINDOW")
        days = ZERO
        last = begin
        for s in periods:
            issuer(s.source_key)
            if (not _utc(s.start_utc) or not _utc(s.end_utc)
                    or s.start_utc != last or s.end_utc <= s.start_utc):
                holes.add("GAP_OVERLAP_OR_INVALID_GROSS_SHARE_DAY_CLOCK")
            if not all(_number(n, whole=True)
                       for n in (s.gross_shares, s.reserved_shares, s.gross_limit)):
                errors.add("INVALID_CAPITAL_EXPOSURE_UNITS")
            elif s.gross_shares + s.reserved_shares > s.gross_limit:
                errors.add("GROSS_CAP_OR_RESERVATION_BREACH")
            if s.unresolved_reservations:
                holes.add("UNCERTAIN_ORPHAN_RESERVED_EXPOSURE")
            if _number(s.gross_shares):
                days += (s.gross_shares + s.reserved_shares) * (
                    Decimal(str((s.end_utc - s.start_utc).total_seconds())) / DAY_SECONDS
                )
            last = s.end_utc
        if "INCOMPLETE_GROSS_SHARE_DAY_WINDOW" not in holes and not any(
            "GROSS_SHARE_DAY" in h for h in holes
        ) and not errors:
            share_days = days

    if errors:
        verdict: Literal["SCHEMA_ONLY", "REJECTED", "INCOMPLETE"] = "REJECTED"
    elif holes:
        verdict = "INCOMPLETE"
    else:
        verdict = "SCHEMA_ONLY"
    # No caller-provided boolean, SHA, issuer string, synthetic fixture or local
    # double-entry identity can independently admit genuine original venue data.
    # Even internally complete positive cash arithmetic is research-only.
    return AuditResult(
        verdict=verdict, reasons=tuple(sorted(errors | holes)),
        descriptive_closed_lot_net_susq=net if all_cash_known and not errors and not holes else None,
        gross_share_days=share_days, account_identity_pass=account_pass,
        eligible_attempts_descriptive=len(bundle.attempts) if bundle.kind == "SYNTHETIC" else None,
        maker_closed_lots_descriptive=len(bundle.fills) if verdict == "SCHEMA_ONLY" else 0,
    )
