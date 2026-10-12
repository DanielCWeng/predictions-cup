"""Synthetic identity/retention fixtures; not real production-fill certification."""

from __future__ import annotations

from typing import Any

import pytest
from scripts.research.reconcile_inventory_fills import match_fills


def fill(ident: int = 42, order: int = 77) -> dict[str, Any]:
    return {
        "id": ident,
        "order_id": order,
        "exchange_id": "101",
        "market_id": "10",
        "filled_at": "2026-10-12T12:00:00.000Z",
        "side": "no",
        "quantity": "-50",
        "price": "0.30000000000000004",
    }


def event(kind: str, event_id: int = 2, ident: str | None = None) -> dict[str, Any]:
    return {
        "event_id": event_id,
        "event_type": kind,
        "tournament_id": "cup",
        "exchange_id": "101",
        "exchange_order_id": "77",
        "fill_id": ident,
        "quantity": "-50",
        "price": "0.3",
        "source_timestamp": "2026-10-12T12:00:00.100+00:00",
        "strategy_family": "SYNTHETIC",
        "strategy_id": "test",
    }


def test_exact_fill_id_and_realtime_deduplication() -> None:
    fills = [fill(42), fill(43, 78)]
    exact = event("AUTHORITATIVE_FILL", ident="42")
    realtime = event("REALTIME_FILL", event_id=3)
    realtime["exchange_order_id"] = "78"
    rows, report = match_fills(fills, [exact, realtime])
    assert [r["status"] for r in rows] == [
        "EXACT_AUTHORITATIVE_FILL_ID",
        "UNIQUE_REALTIME_ECONOMIC_TUPLE",
    ]
    assert report["confirmed_fill_count"] == 2


def test_identical_duplicate_authoritative_rows_are_not_lost_fills() -> None:
    a = event("AUTHORITATIVE_FILL", event_id=1, ident="42")
    b = event("AUTHORITATIVE_FILL", event_id=2, ident="42")
    rows, report = match_fills([fill()], [a, b])
    assert rows[0]["status"] == "DUPLICATE_AUTHORITATIVE_ID_SAME_ECONOMICS"
    assert report["confirmed_fill_count"] == 1


def test_conflicting_authoritative_id_stays_unresolved() -> None:
    a = event("AUTHORITATIVE_FILL", event_id=1, ident="42")
    b = event("AUTHORITATIVE_FILL", event_id=2, ident="42")
    b["price"] = ".31"
    rows, _ = match_fills([fill()], [a, b])
    assert rows[0]["status"] == "FILL_ID_CONFLICT"


def test_order_summary_is_not_fill_confirmation() -> None:
    rows, report = match_fills([fill()], [event("FILL_SUMMARY")])
    assert rows[0]["confidence"] == "ORDER_ONLY"
    assert report["confirmed_fill_count"] == 0


def test_ack_only_does_not_prove_fill() -> None:
    rows, _ = match_fills([fill()], [event("ACK")])
    assert rows[0]["status"] == "ORDER_EVENT_WITHOUT_FILL_MATCH"


def test_no_order_reference_remains_unresolved() -> None:
    rows, report = match_fills([fill()], [])
    assert rows[0]["status"] == "NO_JOURNAL_ORDER_REFERENCE"
    assert report["unreferenced_order_count"] == 1


def test_stale_realtime_or_wrong_exchange_does_not_match() -> None:
    stale = event("REALTIME_FILL")
    stale["source_timestamp"] = "2026-10-12T11:00:00Z"
    rows, _ = match_fills([fill()], [stale])
    assert rows[0]["status"] == "ORDER_EVENT_WITHOUT_FILL_MATCH"
    wrong = event("REALTIME_FILL")
    wrong["exchange_id"] = "102"
    rows, _ = match_fills([fill()], [wrong])
    assert rows[0]["status"] == "NO_JOURNAL_ORDER_REFERENCE"


def test_null_exchange_ack_is_order_only_not_proven_fill() -> None:
    ack = event("ACK")
    ack["exchange_id"] = None
    rows, report = match_fills([fill()], [ack])
    assert rows[0]["status"] == "ORDER_ID_ONLY_NULL_EXCHANGE"
    assert report["confirmed_fill_count"] == 0


def test_same_realtime_event_cannot_pay_two_fills() -> None:
    rows, report = match_fills([fill(42), fill(43)], [event("REALTIME_FILL")])
    assert report["confirmed_fill_count"] == 1
    assert sum(r["status"] == "ORDER_EVENT_WITHOUT_FILL_MATCH" for r in rows) == 1


def test_ambiguous_tuple_is_not_assigned_arbitrarily() -> None:
    rows, report = match_fills(
        [fill()],
        [
            event("REALTIME_FILL", event_id=2),
            event("REALTIME_FILL", event_id=3),
        ],
    )
    assert rows[0]["status"] == "AMBIGUOUS_REALTIME_TUPLE"
    assert report["confirmed_fill_count"] == 0


def test_duplicate_venue_id_or_zero_tolerance_rejected() -> None:
    with pytest.raises(ValueError):
        match_fills([fill(), fill()], [])
    with pytest.raises(ValueError):
        match_fills([fill()], [], tolerance_ms=0)
