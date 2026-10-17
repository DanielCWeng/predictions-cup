"""Generated synthetic adversarial fixtures only. No original records or venue I/O."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from predictions_cup.research import offline_quote_attempt_contract as contract

_MODULE = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "predictions_cup"
    / "research"
    / "offline_quote_attempt_contract.py"
)


def bundle(*attempts: dict[str, object]) -> dict[str, object]:
    return {
        "schema_version": "offline-maker-attempts-v1",
        "source": {
            "permission_ref": "FICTIONAL_FIXTURE_NOT_AN_OWNER_GRANT",
            "issuer_id": "FICTIONAL_ISSUER",
            "capture_epoch": "invented-epoch",
            "publisher_clock_basis": "UNKNOWN",
            "receive_clock_basis": "UNKNOWN",
            "source_signature": None,
            "original_sha256": None,
            "native_id_namespace_status": "UNKNOWN",
        },
        "wire_attempts": list(attempts),
    }


def attempt(
    aid: str,
    *,
    side: str = "BID",
    start: int = 0,
    tournament: str = "FAKE-A",
    state: str = "CANCELLED",
    events: list[dict[str, object]] | None = None,
    cancel_request: bool = True,
    ack: bool = True,
    issuer_role: str = "UNKNOWN",
) -> dict[str, object]:
    timestamp = lambda sec: f"2026-11-05T00:{sec // 60:02d}:{sec % 60:02d}Z"
    # All times intentionally fabricated, including (nonexistent) issuer clocks.
    return {
        "tournament_id": tournament,
        "exchange_id": "FAKE-EXCHANGE",
        "market_id": "INVENTED-MARKET",
        "wire_attempt_id": aid,
        "logical_intent_id": "intent-" + aid,
        "idempotency_key": "retry-group-1",
        "decision_id": "decision-" + aid,
        "native_order_id": "raw-order-reused" if tournament != "MISSING" else None,
        "local_strategy": "MAKE",
        "issuer_liquidity_role": issuer_role,
        "side": side,
        "outcome": "YES",
        "limit_price": "0.46",
        "whole_size": "2",
        "quote_revision": 0,
        "queue_assumption": "UNKNOWN",
        "exposure_reservation": "HELD",
        "publish_state": "ON_WIRE",
        "decision_at": timestamp(start),
        "publish_at": timestamp(start + 1),
        "ack_at": timestamp(start + 2) if ack else None,
        "cancel_request_at": timestamp(start + 40) if cancel_request else None,
        "cancel_ack_at": timestamp(start + 41) if cancel_request else None,
        "terminal_at": timestamp(start + 42) if state not in {"OPEN", "UNCERTAIN"} else None,
        "terminal_state": state,
        "events": [] if events is None else events,
    }


def fill(eid: str, *, fid: str | None, tid: str | None, sec: int = 35,
         issuer_role: str = "MAKER", fee: str | None = "0.001") -> dict[str, object]:
    timestamp = f"2026-11-05T00:{sec // 60:02d}:{sec % 60:02d}Z"
    return {
        "journal_event_id": eid,
        "kind": "FILL",
        "issuer_at": timestamp,
        "received_at": timestamp,
        "publisher_signature": None,
        "native_fill_id": fid,
        "native_trade_id": tid,
        "whole_qty": "1",
        "fill_price": "0.46",
        "issuer_liquidity_role": issuer_role,
        "fee_paid": fee,
        "rebate_paid": "0",
        "fee_currency": "SUSQ",
    }


def validate(*attempts: dict[str, object]) -> object:
    return contract.validate_offline_bundle(bundle(*attempts))


def assert_not_admitted(out: object) -> None:
    assert out.source_admitted is False
    assert out.paper_admitted is False
    assert out.native_fill_count is None
    assert out.native_fill_rate is None
    assert out.realised_after_cost_net is None
    assert out.native_id_scope_status == "UNKNOWN"


def test_no_fill_quote_and_rejected_quote_both_stay_in_make_denominator() -> None:
    a = attempt("zero")
    b = attempt("reject", state="REJECTED", cancel_request=False)
    out = validate(a, b)
    assert out.total_wire_attempts == out.local_make_attempt_denominator == 2
    assert out.local_make_explicit_zero_fill == 2
    assert out.local_make_rejected == 1
    assert_not_admitted(out)


def test_cancel_request_without_ack_or_terminal_is_censored_not_a_zero_fill() -> None:
    a = attempt("lost-cancel", state="UNCERTAIN", ack=False, cancel_request=True)
    a["cancel_ack_at"] = None
    a["exposure_reservation"] = "RELEASED"
    out = validate(a)
    assert out.local_make_attempt_denominator == 1
    assert out.local_make_unresolved == 1
    assert out.local_make_explicit_zero_fill == 0
    for reason in ("ACK_MISSING_OR_POSSIBLY_LOST", "CANCEL_UNCERTAIN", "TERMINAL_UNRESOLVED", "EXPOSURE_RELEASE_UNPROVEN"):
        assert reason in out.issues
    assert_not_admitted(out)


def test_two_30s_grid_shadow_quotes_one_trade_within_both_60s_windows() -> None:
    # First quote decision t=0 and second t=30; one market trade t=45
    # could touch both while the old 60s lifetime overlaps. NEVER allocate twice.
    a = attempt("q0", start=0, events=[fill("e1", fid="f1", tid="one-trade", sec=45)])
    b = attempt("q30", start=30, events=[fill("e2", fid="f2", tid="one-trade", sec=45)])
    out = validate(a, b)
    assert out.local_make_attempt_denominator == 2
    assert "TRADE_EVENT_DOUBLE_SPENT_ACROSS_QUOTES" in out.issues
    assert out.verdict == "INCOMPLETE_OR_CONFLICTING_CLAIMS"
    assert_not_admitted(out)


def test_bid_ask_two_sided_same_native_fill_blocked() -> None:
    b = attempt("bid", side="BID", events=[fill("e-bid", fid="same-fill", tid="trade-1")])
    a = attempt("ask", side="ASK", events=[fill("e-ask", fid="same-fill", tid="trade-2")])
    out = validate(b, a)
    assert "NATIVE_FILL_DOUBLE_CONSUMED" in out.issues
    assert_not_admitted(out)


def test_same_trade_and_fill_id_cannot_satisfy_two_distinct_wire_attempts() -> None:
    a = attempt("q1", events=[fill("e1", fid="f1", tid="t1")])
    b = attempt("q2", events=[fill("e2", fid="f1", tid="t1")])
    out = validate(a, b)
    assert "NATIVE_FILL_DOUBLE_CONSUMED" in out.issues
    assert "TRADE_EVENT_DOUBLE_SPENT_ACROSS_QUOTES" in out.issues
    assert_not_admitted(out)


def test_duplicate_identical_journal_event_is_one_claim_not_two() -> None:
    e = fill("e1", fid="f1", tid="t1")
    a = attempt("one", events=[e, dict(e)])
    out = validate(a)
    assert out.distinct_claimed_fill_events == 1
    assert out.projections[0].observed_fill_event_claims == 1
    assert "NATIVE_FILL_DOUBLE_CONSUMED" not in out.issues
    assert_not_admitted(out)


def test_mutated_reuse_of_same_journal_event_id_is_rejected() -> None:
    e = fill("e1", fid="f1", tid="t1")
    altered = {**e, "fee_paid": "0"}
    out = validate(attempt("conflict", events=[e, altered]))
    assert "JOURNAL_EVENT_CONFLICT" in out.issues
    assert out.distinct_claimed_fill_events == 1
    assert_not_admitted(out)


def test_late_fill_after_cancel_ack_is_preserved_and_quarantined() -> None:
    out = validate(attempt("late", events=[fill("e1", fid="f1", tid="t1", sec=55)]))
    assert "FILL_AFTER_CANCEL_ACK_UNRECONCILED" in out.issues
    assert out.local_make_attempt_denominator == 1
    assert out.distinct_claimed_fill_events == 1
    assert_not_admitted(out)


def test_cross_tournament_same_raw_native_order_and_fill_are_scoped_hints_not_proven_namespace() -> None:
    a = attempt("one", tournament="FAKE-A", events=[fill("e1", fid="same-fill", tid="same-trade")])
    b = attempt("two", tournament="FAKE-B", events=[fill("e1", fid="same-fill", tid="same-trade")])
    out = validate(a, b)
    assert out.local_make_attempt_denominator == 2
    assert out.projections[0].native_order_id_raw == out.projections[1].native_order_id_raw
    assert out.projections[0].native_order_scoped_hint != out.projections[1].native_order_scoped_hint
    assert "NATIVE_FILL_DOUBLE_CONSUMED" not in out.issues
    assert "NATIVE_ID_NAMESPACE_UNKNOWN" in out.issues
    assert_not_admitted(out)


def test_estimator_namespace_claim_cannot_be_self_approved() -> None:
    b = bundle(attempt("a"))
    b["source"]["native_id_namespace_status"] = "VERIFIED_GLOBAL"
    with pytest.raises(contract.ContractDenied, match="UNVERIFIED_NATIVE_NAMESPACE_CLAIM"):
        contract.validate_offline_bundle(b)


def test_missing_native_trade_identity_and_fee_or_issuer_maker_role_fail_closed() -> None:
    a = attempt("missing", issuer_role="UNKNOWN", events=[fill("e1", fid="claimed-fill", tid=None, fee=None, issuer_role="UNKNOWN")])
    out = validate(a)
    assert {"FILL_IDENTITY_UNKNOWN", "FEE_OR_REBATE_UNKNOWN", "FILL_NATIVE_MAKER_ROLE_UNKNOWN", "ISSUER_MAKER_ROLE_UNVERIFIED"}.issubset(set(out.issues))
    assert_not_admitted(out)


def test_omitted_terminal_cannot_certify_zero_fill() -> None:
    out = validate(attempt("pending", state="OPEN", cancel_request=False))
    assert out.local_make_unresolved == 1
    assert out.local_make_explicit_zero_fill == 0
    assert "TERMINAL_UNRESOLVED" in out.issues
    assert_not_admitted(out)


def test_fractional_weighted_proxy_fill_quantity_cannot_be_passed_as_native_whole_order() -> None:
    a = attempt("size")
    a["whole_size"] = "67.75"  # 005G fractional weighted equivalents, never native fills
    with pytest.raises(contract.ContractDenied, match="NOT_POSITIVE_WHOLE_SIZE"):
        validate(a)


def test_overfill_claim_and_invalid_issuer_clocks_rejected_without_native_pnl() -> None:
    e1 = fill("e1", fid="f1", tid="t1")
    e2 = fill("e2", fid="f2", tid="t2")
    e3 = fill("e3", fid="f3", tid="t3")
    out = validate(attempt("over", events=[e1, e2, e3]))
    assert "CLAIMED_FILL_EXCEEDS_WHOLE_SIZE" in out.issues
    assert_not_admitted(out)


def test_missing_issuer_receive_timestamps_prevent_trusting_fill() -> None:
    e = fill("e1", fid="f1", tid="t1")
    e["issuer_at"] = None
    out = validate(attempt("clock", events=[e]))
    assert "ISSUER_OR_RECEIVE_CLOCK_UNKNOWN" in out.issues
    assert_not_admitted(out)


def test_local_take_is_not_added_to_make_attempt_denominator() -> None:
    a = attempt("make")
    b = attempt("take")
    b["local_strategy"] = "TAKE"
    out = validate(a, b)
    assert out.total_wire_attempts == 2
    assert out.local_make_attempt_denominator == 1
    assert_not_admitted(out)


def test_parse_duplicate_json_keys_and_nan_rejected() -> None:
    with pytest.raises(contract.ContractDenied, match="DUPLICATE_JSON_FIELD"):
        contract.parse_offline_json(b'{"x":1,"x":2}')
    with pytest.raises(contract.ContractDenied, match="NONSTANDARD_JSON_NUMBER"):
        contract.parse_offline_json(b'{"x":NaN}')
    assert contract.parse_offline_json(json.dumps(bundle(attempt("ok"))).encode())["schema_version"] == "offline-maker-attempts-v1"


def test_standalone_source_does_not_import_execution_or_runtime() -> None:
    code = _MODULE.read_text(encoding="utf-8")
    assert "from predictions_cup" not in code
    assert "import requests" not in code
    assert "import aiohttp" not in code
