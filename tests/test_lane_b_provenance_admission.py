"""LANE B independent provenance and rate-admission safety negatives."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from predictions_cup.config import AppSettings
from predictions_cup.research.paired_sig_probe import (
    _PRODUCTION_SERVICES,
    _safe_snapshot,
    inspect_admission,
)
from predictions_cup.research.pm_provenance import PmProvenance

NOW = datetime(2026, 10, 13, 0, tzinfo=UTC)


def book(token: str = "YES", *, time: datetime = NOW) -> dict[str, Any]:
    return {
        "event_type": "book", "asset_id": token, "market": "pm-market",
        "timestamp": str(int(time.timestamp() * 1000)),
        "bids": [{"price": "0.40", "size": "3"}],
        "asks": [{"price": "0.60", "size": "5"}],
    }


def delta(token: str, *, price: str = "0.41", qty: str = "2") -> dict[str, Any]:
    return {
        "event_type": "price_change", "timestamp": str(int(NOW.timestamp() * 1000)),
        "price_changes": [
            {"asset_id": token, "side": "BUY", "price": price, "size": qty},
        ],
    }


def make_provenance() -> PmProvenance:
    return PmProvenance(frozenset(("YES",)), frozenset(("NO",)))


def test_pm_source_epoch_and_no_sequence_are_explicit_no_trust() -> None:
    a = make_provenance()
    assert a.receive(book(), NOW)[0]["reason"] == "WEBSOCKET_DISCONNECTED"
    a.boundary("CONNECTED", NOW)
    assert a.receive(delta("YES"), NOW) == []
    assert a.report()["counters"]["ignored_delta_SUBSCRIBED_UNSEEDED"] == 1
    seeded = a.receive(book(), NOW)[0]
    assert seeded["complete"] is True
    assert seeded["source_sequence_verified"] is False
    assert seeded["trusted"] is False
    assert seeded["local_continuous_since_seed"] is True
    result = a.receive(delta("YES"), NOW + timedelta(milliseconds=2))[0]
    assert result["kind"] == "PRICE_CHANGE"
    assert result["bids"][0]["price"] == "0.41"
    assert result["trusted"] is False
    a.boundary("DISCONNECTED", NOW + timedelta(seconds=1))
    assert len(a.seeded) == 0
    assert a.receive(delta("YES"), NOW + timedelta(seconds=2))[0]["reason"] == "WEBSOCKET_DISCONNECTED"
    a.boundary("CONNECTED", NOW + timedelta(seconds=3))
    assert a.receive(delta("YES"), NOW + timedelta(seconds=4)) == []
    assert a.report()["counters"]["ignored_delta_SUBSCRIBED_UNSEEDED"] == 2


def test_pm_orphan_classification_by_subscribed_opposite_and_external() -> None:
    a = make_provenance()
    a.boundary("CONNECTED", NOW)
    a.receive(book(), NOW)
    mixed = delta("YES")
    mixed["price_changes"] += [
        {"asset_id": "NO", "side": "BUY", "price": "0.42", "size": "1"},
        {"asset_id": "OUTSIDE", "side": "BUY", "price": "0.42", "size": "1"},
    ]
    assert len(a.receive(mixed, NOW + timedelta(milliseconds=10))) == 1
    counts = a.report()["counters"]
    assert counts["ignored_delta_UNSUBSCRIBED_OPPOSITE"] == 1
    assert counts["ignored_delta_OUTSIDE_ACCEPTED_TOKEN_SET"] == 1
    assert counts["applied_delta_subscribed"] == 1


def test_pm_partial_seed_and_bad_delta_invalidates_book() -> None:
    a = make_provenance()
    a.boundary("CONNECTED", NOW)
    partial = book()
    del partial["asks"]
    assert a.receive(partial, NOW)[0]["reason"] == "INCOMPLETE_FULL_SNAPSHOT"
    assert a.report()["subscribed_seeded"] == 0
    a.receive(book(), NOW)
    invalid = a.receive(delta("YES", price="1.01"), NOW + timedelta(milliseconds=1))[0]
    assert invalid["reason"] == "INVALID_DELTA_INVALIDATED"
    assert a.report()["subscribed_seeded"] == 0


def test_pm_zero_side_rejected_after_authoritative_full_book() -> None:
    a = make_provenance()
    a.boundary("CONNECTED", NOW)
    empty = book()
    empty["bids"] = []
    state = a.receive(empty, NOW)[0]
    assert state["reason"] == "CROSSED_OR_ZERO_DEPTH"
    assert state["local_continuous_since_seed"] is False
    assert state["trusted"] is False


def _settings(path: Path | None) -> AppSettings:
    return AppSettings(sig_rest_account_budget_path=path)


def _services(path: str) -> dict[str, tuple[bool, dict[str, str]]]:
    return {name: (True, {"shared_path": path}) for name in _PRODUCTION_SERVICES}


def _approval(path: Path) -> dict[str, Any]:
    return {
        "measured_at": NOW.isoformat(), "expires_at": (NOW + timedelta(minutes=5)).isoformat(),
        "operator_approved": True, "shared_budget_path": str(path),
        "verified_free_reads_per_minute": 12, "approval_ticket": "issue-220-explicit-research",
    }


def test_sig_admission_default_deny_when_maker_and_capture_unshared(tmp_path: Path) -> None:
    path = tmp_path / "shared.json"
    s = _settings(None)
    result = inspect_admission(s, service_status=_services(""),
                               interval_s=30, universe_size=3,
                               authorisation=None, now=NOW)
    assert not result.permitted
    assert "SHARED_BUDGET_NOT_CONFIGURED" in result.reasons
    result = inspect_admission(_settings(path), service_status=_services(""),
                               interval_s=30, universe_size=3,
                               authorisation=_approval(path), now=NOW)
    assert not result.permitted
    assert any("HOST_SHARED_BUDGET_MISSING" in x for x in result.reasons)


def test_sig_admission_denies_unmeasured_rate_and_stale_approval(tmp_path: Path) -> None:
    path = tmp_path / "shared"
    auth = _approval(path)
    auth["verified_free_reads_per_minute"] = 1
    result = inspect_admission(_settings(path), service_status=_services(str(path)),
                               interval_s=20, universe_size=6,
                               authorisation=auth, now=NOW)
    assert "UNVERIFIED_RESIDUAL_CAPACITY" in result.reasons
    auth["verified_free_reads_per_minute"] = 30
    auth["measured_at"] = (NOW - timedelta(hours=1)).isoformat()
    result = inspect_admission(_settings(path), service_status=_services(str(path)),
                               interval_s=30, universe_size=1,
                               authorisation=auth, now=NOW)
    assert "AUTHORISATION_STALE" in result.reasons


def test_sig_admission_all_shared_and_low_rate_can_pass(tmp_path: Path) -> None:
    path = tmp_path / "budget"
    result = inspect_admission(_settings(path), service_status=_services(str(path)),
                               interval_s=30, universe_size=1,
                               authorisation=_approval(path), now=NOW)
    assert result.permitted
    assert not result.reasons
    assert result.proposed_requests_per_minute == 2.0


def test_sig_rest_book_provenance_never_fakes_exchange_event_time() -> None:
    raw = SimpleNamespace(exchange_id="947", market_id="172", depth=20,
                          bids=[SimpleNamespace(price="0.49", quantity="5")],
                          asks=[SimpleNamespace(price="0.51", quantity="6")])
    record = _safe_snapshot(raw, exchange="947", market="172",
                            started=NOW, received=NOW + timedelta(milliseconds=45),
                            run_id="isolated-run")
    assert record["complete"] is True
    assert record["exchange_at"] is None
    assert record["revision"] is None
    assert record["continuity"] is False
    assert record["trusted"] is False
    assert record["source_timestamp_status"] == "NOT_IN_SIG_REST_RESPONSE"
    assert record["elapsed_request_ms"] == 45.0


def test_sig_wrong_market_rejected_without_substituting_book() -> None:
    raw = SimpleNamespace(exchange_id="947", market_id="wrong", depth=20, bids=[], asks=[])
    r = _safe_snapshot(raw, exchange="947", market="172", started=NOW,
                       received=NOW, run_id="isolated-run")
    assert r["complete"] is False
    assert r["reason"] == "WRONG_SIG_EXCHANGE_OR_MARKET"
