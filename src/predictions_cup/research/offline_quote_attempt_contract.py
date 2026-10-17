"""Standalone OFFLINE-only quote-attempt/fill claim validator (research fixtures).

Not an authenticated maker fill ledger. No imports from trading, execution,
network, book capture, adapters, estimator, or live journal. In particular,
SCHEMA_ONLY cannot promote PAPER/LIVE or yield realised PnL or a native fill rate.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, cast


class ContractDenied(ValueError):
    """Invalid or ambiguous future source-shaped contract; not source admission."""


@dataclass(frozen=True)
class AttemptProjection:
    tournament_id: str
    exchange_id: str
    wire_attempt_id: str
    logical_intent_id: str
    idempotency_key: str
    local_strategy: str
    native_order_id_raw: str | None
    native_order_scoped_hint: tuple[str, str, str, str] | None
    terminal_state: str
    observed_fill_event_claims: int


@dataclass(frozen=True)
class AuditResult:
    verdict: str
    total_wire_attempts: int
    local_make_attempt_denominator: int
    local_make_explicit_zero_fill: int
    local_make_unresolved: int
    local_make_rejected: int
    distinct_claimed_fill_events: int
    projections: tuple[AttemptProjection, ...]
    issues: tuple[str, ...]
    native_id_scope_status: str = "UNKNOWN"
    native_fill_count: None = None
    native_fill_rate: None = None
    realised_after_cost_net: None = None
    source_admitted: bool = False
    paper_admitted: bool = False


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise ContractDenied(reason)


def _text(value: Any, reason: str) -> str:
    _require(isinstance(value, str) and bool(value.strip()), reason)
    return cast(str, value)


def _nullable_text(value: Any, reason: str) -> str | None:
    return None if value is None else _text(value, reason)


def _stamp(value: Any, *, required: bool = False) -> datetime | None:
    if value is None and not required:
        return None
    _require(isinstance(value, str) and value.endswith(("Z", "+00:00")), "TIMESTAMP_NOT_EXPLICIT_UTC")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ContractDenied("TIMESTAMP_INVALID") from exc
    _require(result.utcoffset() == timedelta(0), "TIMESTAMP_NOT_EXPLICIT_UTC")
    return result


def _utc_required(value: Any) -> datetime:
    stamp = _stamp(value, required=True)
    assert stamp is not None
    return stamp


def _whole(value: Any, reason: str) -> Decimal:
    _require(isinstance(value, str), reason)
    try:
        x = Decimal(value)
    except InvalidOperation as exc:
        raise ContractDenied(reason) from exc
    _require(x.is_finite() and x > 0 and x == x.to_integral_value(), reason)
    return x


def _money(value: Any, reason: str, *, allow_zero: bool = True) -> Decimal:
    _require(isinstance(value, str), reason)
    try:
        x = Decimal(value)
    except InvalidOperation as exc:
        raise ContractDenied(reason) from exc
    _require(x.is_finite() and (x >= 0 if allow_zero else x > 0), reason)
    return x


def _object(value: Any) -> dict[str, Any]:
    _require(isinstance(value, dict) and all(isinstance(k, str) for k in value), "EXPECTED_OBJECT")
    return cast(dict[str, Any], value)


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        _require(key not in result, "DUPLICATE_JSON_FIELD")
        result[key] = value
    return result


def _reject_nonstandard_constant(value: str) -> None:
    raise ContractDenied("NONSTANDARD_JSON_NUMBER")


def parse_offline_json(raw: bytes) -> dict[str, Any]:
    """Bounded UTF-8 JSON parse. Caller chooses an authorised offline source."""
    _require(0 < len(raw) <= 2_000_000, "INPUT_EMPTY_OR_OVERSIZE")
    try:
        return _object(json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_nonstandard_constant,
        ))
    except ContractDenied:
        raise
    except (UnicodeError, ValueError) as exc:
        raise ContractDenied("INVALID_OFFLINE_JSON") from exc


def validate_offline_bundle(bundle: dict[str, Any]) -> AuditResult:
    """Reconcile hypothetical wire attempts without authenticating issuer claims.

    Every MAKE-labelled wire-attempt stays in the LOCAL attempt denominator,
    including ACK-loss, rejects, cancellations, no-fills and censored attempts.
    A repeated observed trade cannot create a second shadow-fill claim.
    UNKNOWN native namespace and fees always prohibit real fill rate/PnL.
    """
    _require(bundle.get("schema_version") == "offline-maker-attempts-v1", "SCHEMA_VERSION_UNSUPPORTED")
    source = _object(bundle.get("source"))
    _text(source.get("permission_ref"), "PERMISSION_REF_MISSING")
    _text(source.get("issuer_id"), "ISSUER_ID_MISSING")
    _text(source.get("capture_epoch"), "CAPTURE_EPOCH_MISSING")
    _text(source.get("publisher_clock_basis"), "PUBLISHER_CLOCK_BASIS_MISSING")
    _text(source.get("receive_clock_basis"), "RECEIVE_CLOCK_BASIS_MISSING")
    _nullable_text(source.get("source_signature"), "SOURCE_SIGNATURE_INVALID")
    _nullable_text(source.get("original_sha256"), "ORIGINAL_HASH_INVALID")
    _require(source.get("native_id_namespace_status") == "UNKNOWN", "UNVERIFIED_NATIVE_NAMESPACE_CLAIM")
    attempts = bundle.get("wire_attempts")
    _require(isinstance(attempts, list), "WIRE_ATTEMPT_ARRAY_MISSING")

    issues: set[str] = {"NATIVE_ID_NAMESPACE_UNKNOWN", "ISSUER_SOURCE_NOT_AUTHENTICATED"}
    if source.get("source_signature") is None or source.get("original_sha256") is None:
        issues.add("SOURCE_PROVENANCE_UNVERIFIED")
    claimed_events: dict[tuple[str, str, str], tuple[bytes, tuple[str, str, str]]] = {}
    allocated_fills: dict[tuple[str, str, str, str], tuple[str, str, str]] = {}
    allocated_trades: dict[tuple[str, str, str, str], tuple[str, str, str]] = {}
    attempt_ids: set[tuple[str, str, str]] = set()
    projected: list[AttemptProjection] = []
    make, zero, unresolved, rejected, fill_claims = 0, 0, 0, 0, 0
    for raw_attempt in attempts:
        a = _object(raw_attempt)
        tournament = _text(a.get("tournament_id"), "TOURNAMENT_ID_MISSING")
        exchange = _text(a.get("exchange_id"), "EXCHANGE_ID_MISSING")
        aid = _text(a.get("wire_attempt_id"), "WIRE_ATTEMPT_ID_MISSING")
        identity = (tournament, exchange, aid)
        _require(identity not in attempt_ids, "DUPLICATE_WIRE_ATTEMPT")
        attempt_ids.add(identity)
        intent = _text(a.get("logical_intent_id"), "LOGICAL_INTENT_MISSING")
        idempotency = _text(a.get("idempotency_key"), "IDEMPOTENCY_MISSING")
        _text(a.get("decision_id"), "DECISION_ID_MISSING")
        _text(a.get("market_id"), "MARKET_ID_MISSING")
        _require(a.get("local_strategy") in {"MAKE", "TAKE", "UNKNOWN"}, "LOCAL_STRATEGY_INVALID")
        _require(a.get("issuer_liquidity_role") in {"MAKER", "TAKER", "UNKNOWN"}, "ISSUER_ROLE_INVALID")
        _require(a.get("side") in {"BID", "ASK"} and a.get("outcome") in {"YES", "NO"}, "SIDE_OR_OUTCOME_INVALID")
        _require(a.get("publish_state") in {"ON_WIRE", "POSSIBLY_ON_WIRE", "NOT_ON_WIRE"}, "PUBLISH_STATE_INVALID")
        _require(a.get("queue_assumption") in {"UNKNOWN", "UNVERIFIED_RESTING", "UNVERIFIED_PRIORITY"}, "QUEUE_ASSUMPTION_INVALID")
        _require(a.get("exposure_reservation") in {"HELD", "RELEASED", "UNKNOWN"}, "EXPOSURE_STATE_INVALID")
        _require(type(a.get("quote_revision")) is int and a["quote_revision"] >= 0, "REVISION_INVALID")
        size = _whole(a.get("whole_size"), "NOT_POSITIVE_WHOLE_SIZE")
        _require(_money(a.get("limit_price"), "PRICE_INVALID") < 1, "PRICE_INVALID")
        decision = _utc_required(a.get("decision_at"))
        publish = _stamp(a.get("publish_at"))
        ack = _stamp(a.get("ack_at"))
        terminal = _stamp(a.get("terminal_at"))
        cancel_req = _stamp(a.get("cancel_request_at"))
        cancel_ack = _stamp(a.get("cancel_ack_at"))
        _require(not(publish and publish < decision), "PUBLISH_BEFORE_DECISION")
        _require(not(ack and (publish is None or ack < publish)), "ACK_WITHOUT_PRIOR_PUBLISH")
        _require(not(cancel_req and (publish is None or cancel_req < publish)), "CANCEL_REQUEST_WITHOUT_PUBLISH")
        _require(not(cancel_ack and (cancel_req is None or cancel_ack < cancel_req)), "CANCEL_ACK_BEFORE_REQUEST")
        _require(not(terminal and terminal < decision), "TERMINAL_BEFORE_DECISION")
        state = a.get("terminal_state")
        _require(state in {"OPEN", "UNCERTAIN", "CANCELLED", "EXPIRED", "REJECTED", "FILLED"}, "TERMINAL_STATE_INVALID")
        if state in {"OPEN", "UNCERTAIN"} and terminal is not None:
            issues.add("TERMINAL_STATE_CLOCK_CONFLICT")
        if state in {"OPEN", "UNCERTAIN"} and terminal is None:
            issues.add("TERMINAL_UNRESOLVED")
        if state not in {"OPEN", "UNCERTAIN"} and terminal is None:
            issues.add("TERMINAL_MISSING")
        if publish is None and a.get("publish_state") != "NOT_ON_WIRE":
            issues.add("PUBLISH_CLOCK_UNKNOWN")
        if ack is None and a.get("publish_state") != "NOT_ON_WIRE":
            issues.add("ACK_MISSING_OR_POSSIBLY_LOST")
        if cancel_req is not None and cancel_ack is None:
            issues.add("CANCEL_UNCERTAIN")
        if state == "CANCELLED" and cancel_ack is None:
            issues.add("CANCEL_ACK_MISSING")
        if a.get("exposure_reservation") == "RELEASED" and state in {"OPEN", "UNCERTAIN"}:
            issues.add("EXPOSURE_RELEASE_UNPROVEN")
        if a.get("issuer_liquidity_role") != "MAKER" and a.get("local_strategy") == "MAKE":
            issues.add("ISSUER_MAKER_ROLE_UNVERIFIED")
        native_order = _nullable_text(a.get("native_order_id"), "NATIVE_ORDER_ID_INVALID")
        scoped_order = (tournament, exchange, source["issuer_id"], native_order) if native_order else None
        if native_order is None:
            issues.add("NATIVE_ORDER_ID_UNKNOWN")
        if a.get("local_strategy") == "MAKE":
            make += 1
            if state in {"OPEN", "UNCERTAIN"} or cancel_req is not None and cancel_ack is None:
                unresolved += 1
            if state == "REJECTED":
                rejected += 1
        raw_events = a.get("events")
        _require(isinstance(raw_events, list), "EVENT_ARRAY_MISSING")
        fill_in_attempt = 0
        qty_total = Decimal(0)
        for raw_event in raw_events:
            e = _object(raw_event)
            eid = _text(e.get("journal_event_id"), "JOURNAL_EVENT_ID_MISSING")
            event_key = (tournament, exchange, eid)
            canonical = json.dumps(e, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
            earlier = claimed_events.get(event_key)
            if earlier is not None:
                if earlier[0] != canonical:
                    issues.add("JOURNAL_EVENT_CONFLICT")
                elif earlier[1] != identity:
                    issues.add("JOURNAL_EVENT_REPLAY_ACROSS_ATTEMPTS")
                # Identical replay is NOT another fill (even if attached twice).
                continue
            claimed_events[event_key] = canonical, identity
            _require(e.get("kind") in {"PUBLISH", "ACK", "FILL", "CANCEL_REQUEST", "CANCEL_ACK", "REJECT", "TERMINAL"}, "EVENT_KIND_INVALID")
            issuer_at = _stamp(e.get("issuer_at"))
            received_at = _stamp(e.get("received_at"))
            _nullable_text(e.get("publisher_signature"), "PUBLISHER_SIGNATURE_INVALID")
            if issuer_at is None or received_at is None:
                issues.add("ISSUER_OR_RECEIVE_CLOCK_UNKNOWN")
            elif received_at < issuer_at:
                issues.add("RECEIVE_PRECEDES_ISSUER_CLOCK")
            if e.get("kind") != "FILL":
                continue
            fill_in_attempt += 1
            fill_claims += 1
            qty_total += _whole(e.get("whole_qty"), "FILL_NOT_WHOLE_SIZE")
            _require(_money(e.get("fill_price"), "FILL_PRICE_INVALID") < 1, "FILL_PRICE_INVALID")
            fid = _nullable_text(e.get("native_fill_id"), "FILL_ID_INVALID")
            tid = _nullable_text(e.get("native_trade_id"), "TRADE_ID_INVALID")
            if fid is None or tid is None:
                issues.add("FILL_IDENTITY_UNKNOWN")
            if fid is not None:
                key = (tournament, exchange, source["issuer_id"], fid)
                if key in allocated_fills:
                    issues.add("NATIVE_FILL_DOUBLE_CONSUMED")
                allocated_fills[key] = identity
            if tid is not None:
                key = (tournament, exchange, source["issuer_id"], tid)
                if key in allocated_trades and allocated_trades[key] != identity:
                    issues.add("TRADE_EVENT_DOUBLE_SPENT_ACROSS_QUOTES")
                allocated_trades[key] = identity
            if a.get("issuer_liquidity_role") != "MAKER" or e.get("issuer_liquidity_role") != "MAKER":
                issues.add("FILL_NATIVE_MAKER_ROLE_UNKNOWN")
            if e.get("fee_paid") is None or e.get("fee_currency") is None or e.get("rebate_paid") is None:
                issues.add("FEE_OR_REBATE_UNKNOWN")
            else:
                _money(e["fee_paid"], "FEE_PAID_INVALID")
                _money(e["rebate_paid"], "REBATE_PAID_INVALID")
                _text(e["fee_currency"], "FEE_CURRENCY_INVALID")
            if cancel_ack is not None and issuer_at is not None and issuer_at > cancel_ack:
                issues.add("FILL_AFTER_CANCEL_ACK_UNRECONCILED")
        if qty_total > size:
            issues.add("CLAIMED_FILL_EXCEEDS_WHOLE_SIZE")
        if state == "FILLED" and fill_in_attempt == 0:
            issues.add("FILLED_WITHOUT_NATIVE_EVENT")
        if a.get("local_strategy") == "MAKE" and fill_in_attempt == 0 and state in {"CANCELLED", "EXPIRED", "REJECTED"} and terminal is not None:
            zero += 1
        projected.append(AttemptProjection(
            tournament, exchange, aid, intent, idempotency, a["local_strategy"],
            native_order, scoped_order, state, fill_in_attempt,
        ))
    return AuditResult(
        verdict="SCHEMA_ONLY" if not (issues - {"NATIVE_ID_NAMESPACE_UNKNOWN", "ISSUER_SOURCE_NOT_AUTHENTICATED", "SOURCE_PROVENANCE_UNVERIFIED"}) else "INCOMPLETE_OR_CONFLICTING_CLAIMS",
        total_wire_attempts=len(projected),
        local_make_attempt_denominator=make,
        local_make_explicit_zero_fill=zero,
        local_make_unresolved=unresolved,
        local_make_rejected=rejected,
        distinct_claimed_fill_events=fill_claims,
        projections=tuple(projected),
        issues=tuple(sorted(issues)),
    )
