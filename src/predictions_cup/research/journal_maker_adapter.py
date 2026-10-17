"""Inert journal-shaped evidence projection for the frozen maker estimator.

SCHEMA/TEST_FIXTURE ONLY. This deliberately cannot attest issuer roles, fees,
terminality, journal coverage, rights, fills or source admission. No I/O.
See #225, West source mapping #199 and immutable estimator DRAFT #285.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Literal

from predictions_cup.research.locked_maker_economics import (
    InventoryCashflow,
    LockedBundle,
    MakerAttempt,
    OrderEvent,
    SourceProof,
    VenueFill,
)

PLACEMENTS = frozenset({"single_placement", "best_effort_batch", "atomic_multi_leg"})
MISSING = (
    "IMMUTABLE_R2_JOURNAL_AND_LAWFUL_CUSTODIAN_RELEASE",
    "COMPLETE_MAKER_ELIGIBLE_SUBMISSION_UNIVERSE",
    "ORIGINAL_ISSUER_AND_RECEIPT_UTC_WITH_SOURCE_COVERAGE",
    "VENUE_NATIVE_MAKER_ROLE_AND_ORDER_FILL_COMPLETENESS",
    "VENUE_CONFIRMED_TERMINAL_OR_CANCEL_AND_ZERO_FILL",
    "PAID_FEE_REBATE_AND_LOT_SPECIFIC_EXIT_SETTLEMENT",
    "ORIGINAL_CASH_RULES_AND_INDEPENDENT_SOURCE_ADMISSION",
)


@dataclass(frozen=True)
class EnvelopeRow:
    tournament_id: str
    logical_operation_id: str
    operation_kind: str
    sink_mode: str
    payload_json: str
    payload_sha256: str
    intent_ids_json: str
    created_at_utc: str | None = None  # application clock, NOT venue issuer clock


@dataclass(frozen=True)
class EventRow:
    event_id: int
    tournament_id: str
    logical_operation_id: str
    logical_intent_id: str | None
    event_type: str
    source_timestamp: str | None = None  # NOT independently issuer authenticated
    observed_monotonic_ns: int | None = None  # never UTC across process boots
    exchange_order_id: str | None = None
    exchange_id: str | None = None
    fill_id: str | None = None
    quantity: str | None = None
    price: str | None = None
    terminal_status: str | None = None
    detail_json: str | None = None
    strategy_family: str | None = None


@dataclass(frozen=True)
class FillDedupeRow:
    dedupe_key: str
    event_id: int  # multiple aliases can refer to SAME event, never extra fills


@dataclass(frozen=True)
class NativeProofHoles:
    independent_maker_eligibility: None = None
    native_liquidity_role: None = None
    original_fill_universe_complete: None = None
    terminality_verified: None = None
    paid_fee_susq: None = None
    later_lot_exit_cash_susq: None = None
    issuer_receipt_clock_verified: None = None


@dataclass(frozen=True)
class JournalCandidate:
    attempt_key: str  # stable deterministic pseudonym; not de-identification guarantee
    tournament_id: str
    logical_operation_id: str
    logical_intent_id: str
    market_id: str | None
    action: Literal["BUY", "SELL"] | None
    outcome: Literal["YES", "NO"] | None
    requested_qty: Decimal | None
    possible_native_order_id: str | None
    native_fill_ids_observed: tuple[str, ...]
    journal_event_types: tuple[str, ...]
    local_strategy_label: str | None
    purported_publisher_at: datetime | None
    receipt_at: None  # historical event row has only process-local monotonic clock
    verified_zero_fill: None
    economic_net_susq: None
    evidence_holes: NativeProofHoles
    censored_reason: str


@dataclass(frozen=True)
class AdapterResult:
    verdict: Literal["MISSING_PROOF"]
    candidates: tuple[JournalCandidate, ...]
    rejected_rows: tuple[str, ...]
    missing_proofs: tuple[str, ...]
    eligible_maker_denominator: None
    realised_net: None
    conservative_ci: None
    locked_bundle: None  # no authentic LockedBundle from local journal rows
    source_admitted: Literal[False] = False
    paper_admitted: Literal[False] = False


def _parse_json(raw: str) -> object:
    def reject_constant(value: str) -> None:
        raise ValueError("NONSTANDARD_JSON_CONSTANT:" + value)

    return json.loads(raw, parse_float=Decimal, parse_int=Decimal,
                      parse_constant=reject_constant)


def _utc(raw: str | None) -> datetime | None:
    if raw is None:
        return None
    try:
        result = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if result.tzinfo is None or result.utcoffset() != timedelta(0):
        return None
    return result.astimezone(UTC)


def _positive_decimal(raw: object) -> Decimal | None:
    if type(raw) is not str:
        return None
    try:
        value = Decimal(raw)
    except InvalidOperation:
        return None
    return value if value.is_finite() and value > 0 else None


def _id(tournament: str, operation: str, intent: str) -> str:
    identity = json.dumps([tournament, operation, intent], separators=(",", ":"))
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def _fail(*reasons: str) -> AdapterResult:
    return AdapterResult("MISSING_PROOF", (), tuple(reasons), MISSING,
                         None, None, None, None)


def normalise_journal(
    envelopes: tuple[EnvelopeRow, ...],
    events: tuple[EventRow, ...],
    fill_aliases: tuple[FillDedupeRow, ...],
) -> AdapterResult:
    """Project local evidence, NEVER certify its missing native components.

    No SQLite, venue client, file operations, automatic permissions or
    issuer-admission booleans. Strict structural rejects block entire projection;
    incomplete issuer/fee evidence always remains NULL even on well-formed rows.
    """
    if (len({e.logical_operation_id for e in envelopes}) != len(envelopes)
            or not all(e.logical_operation_id and e.tournament_id for e in envelopes)):
        return _fail("DUPLICATE_OR_EMPTY_OPERATION")
    env_by_id = {e.logical_operation_id: e for e in envelopes}
    if len({e.event_id for e in events}) != len(events):
        return _fail("DUPLICATE_EVENT_ID")
    event_by_id = {e.event_id: e for e in events}
    if (len({a.dedupe_key for a in fill_aliases}) != len(fill_aliases)
            or any(not a.dedupe_key or a.event_id not in event_by_id
                   for a in fill_aliases)):
        return _fail("INVALID_ALIAS_REFERENCE")
    if any(ev.logical_operation_id not in env_by_id
           or env_by_id[ev.logical_operation_id].tournament_id != ev.tournament_id
           for ev in events):
        return _fail("ORPHAN_OR_CROSS_TOURNAMENT_EVENT")

    candidates: list[JournalCandidate] = []
    rejected: list[str] = []
    seen_native_orders: dict[tuple[str, str], str] = {}
    seen_fill_ids: dict[tuple[str, str], tuple[str, str, str | None, str | None]] = {}
    for env in envelopes:
        if env.sink_mode != "LIVE" or env.operation_kind not in PLACEMENTS:
            continue  # SHADOW/cancellations are never maker submission denominator
        try:
            if hashlib.sha256(env.payload_json.encode()).hexdigest() != env.payload_sha256:
                raise ValueError("LOCAL_PAYLOAD_HASH_MISMATCH")
            intent_ids = _parse_json(env.intent_ids_json)
            payload = _parse_json(env.payload_json)
            if not isinstance(intent_ids, list) or not isinstance(payload, dict):
                raise ValueError("INVALID_ENVELOPE_JSON")
            if (not intent_ids or any(not isinstance(i, str) or not i for i in intent_ids)
                    or len(set(intent_ids)) != len(intent_ids)):
                raise ValueError("DUPLICATE_OR_MISSING_INTENT")
            if env.operation_kind == "single_placement":
                orders = [payload]
            else:
                field = "orders" if env.operation_kind == "best_effort_batch" else "legs"
                orders = payload.get(field)
            if not isinstance(orders, list) or len(orders) != len(intent_ids) or any(
                not isinstance(o, dict) for o in orders
            ):
                raise ValueError("BATCH_INTENT_CARDINALITY_UNKNOWN")
            own_events = [
                ev for ev in events if ev.logical_operation_id == env.logical_operation_id
            ]
            if any(ev.logical_intent_id is not None
                   and ev.logical_intent_id not in intent_ids for ev in own_events):
                raise ValueError("EVENT_INTENT_CROSS_JOIN")
            for intent_id, order in zip(intent_ids, orders, strict=True):
                own = [ev for ev in own_events if ev.logical_intent_id == intent_id]
                submissions = [ev for ev in own if ev.event_type == "SUBMISSION"]
                if len(submissions) != 1:
                    raise ValueError("MISSING_OR_DUPLICATE_ORIGINAL_SUBMISSION")
                submission = submissions[0]
                info = _parse_json(submission.detail_json or "{}")
                if not isinstance(info, dict):
                    raise ValueError("INVALID_SUBMISSION_DETAIL")
                # Detail is local, not a native issuer assertion. Guard batch mapping.
                market = info.get("market_id")
                action = info.get("action")
                outcome = info.get("outcome_side")
                qty = _positive_decimal(submission.quantity)
                if (not isinstance(market, str) or not market
                        or action not in ("BUY", "SELL")
                        or outcome not in ("YES", "NO") or qty is None
                        or (order.get("market_id") not in (None, market))
                        or (order.get("action") not in (None, action))
                        or (order.get("outcome_side") not in (None, outcome))
                        or (order.get("quantity") not in (None, submission.quantity))):
                    raise ValueError("UNPROVEN_PER_INTENT_ORDER_MAPPING")
                acks = [ev.exchange_order_id for ev in own
                        if ev.event_type == "ACK" and ev.exchange_order_id]
                native_order = acks[0] if len(set(acks)) == 1 and acks else None
                if len(set(acks)) > 1:
                    raise ValueError("CONFLICTING_NATIVE_ACK_IDS")
                if native_order is not None:
                    native_key = (env.tournament_id, native_order)
                    prior = seen_native_orders.setdefault(native_key, intent_id)
                    if prior != intent_id:
                        raise ValueError("NATIVE_ORDER_SHARED_BY_DISTINCT_INTENTS")
                ids: set[str] = set()
                for ev in own:
                    if ev.event_type not in ("REALTIME_FILL", "AUTHORITATIVE_FILL"):
                        continue  # FILL_SUMMARY not a native fill
                    if not ev.fill_id:
                        rejected.append("MISSING_ORIGINAL_FILL_ID")
                        continue
                    identity = (env.tournament_id, ev.fill_id)
                    signature = (native_order or "", market, ev.quantity, ev.price)
                    previous = seen_fill_ids.setdefault(identity, signature)
                    if previous != signature:
                        raise ValueError("CONFLICTING_NATIVE_FILL_ALIAS")
                    ids.add(ev.fill_id)  # aliases/events do not multiply a fill
                # Neither a local CANCEL_ACK nor journal silence certifies zero fills.
                candidates.append(JournalCandidate(
                    _id(env.tournament_id, env.logical_operation_id, intent_id),
                    env.tournament_id, env.logical_operation_id, intent_id,
                    market, action, outcome, qty, native_order, tuple(sorted(ids)),
                    tuple(ev.event_type for ev in own), submission.strategy_family,
                    _utc(submission.source_timestamp), None, None, None,
                    NativeProofHoles(),
                    "UNCERTAIN" if any(ev.event_type == "UNCERTAIN" for ev in own)
                    else "VENUE_TERMINAL_AND_COMPLETE_FILL_SET_UNVERIFIED",
                ))
        except (ValueError, TypeError, KeyError) as exc:
            # Conservative atomic rejection: no partial economic cohort survives.
            return _fail(f"{env.logical_operation_id}:{exc}")
    if not candidates:
        rejected.append("NO_PROVEN_COMPLETE_ELIGIBLE_MAKER_COHORT")
    return AdapterResult("MISSING_PROOF", tuple(candidates), tuple(rejected),
                         MISSING, None, None, None, None)


def invented_fixture_locked_bundle() -> LockedBundle:
    """Hard-coded invented TEST_FIXTURE, never adapted original journal data.

    Only illustrates type conversion and Decimal cash in #285. The estimator
    independently returns SYNTHETIC_OR_STRUCTURAL_ONLY_NOT_SOURCE_ADMISSION.
    """
    at = datetime(2026, 10, 5, 10, tzinfo=UTC)
    def after(seconds: int) -> datetime:
        return at + timedelta(seconds=seconds)
    def proof(name: str) -> SourceProof:
        raw = ("TEST_FIXTURE_ONLY:" + name).encode()
        return SourceProof(name, raw, hashlib.sha256(raw).hexdigest(),
                           "TEST_FIXTURE", "TEST_FIXTURE", "TEST_FIXTURE", True)
    return LockedBundle(
        proofs=tuple(proof(t) for t in ("venue_fills", "order_lifecycle", "inventory_cashflows")),
        attempts=(MakerAttempt("fixture-a", "fixture-order", "fixture-market",
                               "fixture-intent", at, after(1), "NO", "BUY",
                               Decimal("2"), True, "TEST_FIXTURE"),),
        events=(OrderEvent("ack-a", "fixture-a", "ACK", at, after(1)),
                OrderEvent("terminal-a", "fixture-a", "TERMINAL",
                           after(120), after(121), True)),
        fills=(VenueFill("fixture-fill", "fixture-a", "fixture-order", "fixture-market",
                         after(2), after(3), Decimal("2"), Decimal("0.4"),
                         "MAKER", "TEST_FIXTURE"),),
        cashflows=(
            InventoryCashflow("entry", "fixture-a", "fixture-fill", "ENTRY",
                              Decimal("-0.8"), Decimal("2"), after(2), after(3),
                              "TEST_FIXTURE", "PAID_ACTUAL"),
            InventoryCashflow("fee", "fixture-a", None, "FEE",
                              Decimal("-0.02"), Decimal("0"), after(2), after(3),
                              "TEST_FIXTURE", "PAID_ACTUAL"),
            InventoryCashflow("exit", "fixture-a", None, "EXIT",
                              Decimal("1.0"), Decimal("-2"), after(60), after(61),
                              "TEST_FIXTURE", "PAID_ACTUAL"),
        ),
    )
