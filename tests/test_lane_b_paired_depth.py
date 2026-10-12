# ruff: noqa: E501
"""Adversarial, no-network tests for Lane B's PAPER data-quality boundary."""
from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from predictions_cup.research.paired_depth import InvalidInput, PairedPaper, Spec, load_specs

T = datetime(2026, 10, 12, 20, 0, tzinfo=UTC)
D = Decimal


def spec(direction: str = "SAME") -> Spec:
    return Spec("947", "100", "PM-YES", "PM-MARKET", "CID", "EID", direction, "SHA")


def book(venue: str, *, t: datetime = T, exchange: datetime | None = None,
         instrument: str | None = None, trusted: bool = True, connected: bool = True,
         continuity: bool = True, complete: bool = True, epoch: int = 1,
         revision: int | None = None, prev: int | None = None) -> dict[str, Any]:
    return {
        "venue": venue, "instrument_id": instrument or ("947" if venue == "SIG" else "PM-YES"),
        "received_at": t.isoformat(), "exchange_at": (exchange or t).isoformat(),
        "bids": [{"price": "0.48", "size": "4"}, {"price": "0.47", "size": "8"}],
        "asks": [{"price": "0.51", "size": "3"}, {"price": "0.52", "size": "9"}],
        "complete": complete, "trusted": trusted, "connected": connected,
        "continuity": continuity, "source": "OBSERVED_VERIFIED_FULL_SNAPSHOT",
        "connection_epoch": epoch, "revision": revision, "previous_revision": prev,
    }


def gate(direction: str = "SAME", costs: bool = True) -> PairedPaper:
    return PairedPaper({"947": spec(direction)}, max_age_ms=5000, skew_ms=2000,
                       lot_cap=8,
                       sig_fee_per_share=D("0.001") if costs else None,
                       pm_fee_per_share=D("0.002") if costs else None,
                       stress_per_share=D("0.01") if costs else None)


def match(g: PairedPaper, sig: dict[str, Any] | None = None, pm: dict[str, Any] | None = None) -> dict[str, Any]:
    g.observe(sig or book("SIG"))
    return g.observe(pm or book("PM", t=T + timedelta(milliseconds=100)))[0]


def test_complete_depth_marginal_not_vwap_and_no_fabricated_fills() -> None:
    row = match(gate())
    assert row["decision"] == "PAPER_COMPARABLE_NOT_EXECUTED"
    assert row["executable_whole_lots"] == 8
    assert row["sig_max_marginal_limit"] == "0.52"
    assert D(row["sig_buy_vwap"]) == (D("0.51") * 3 + D("0.52") * 5) / 8
    assert row["sig_cash_requirement_max"] == "4.16"
    assert row["reference_discrepancy_after_assumptions_per_share"] is not None
    assert row["observed_fill_qty"] is None
    assert row["observed_pnl"] is None
    assert row["fill_probability"] is None


def test_no_buy_is_same_sig_exchange_complement_of_yes_bids() -> None:
    row = match(gate())  # BUY YES and BUY NO emitted on every observation
    g = gate()
    g.observe(book("SIG"))
    no = g.observe(book("PM", t=T + timedelta(milliseconds=100)))[1]
    assert no["side"] == "BUY_NO"
    assert no["sig_max_marginal_limit"] == "0.53"
    assert D(no["sig_buy_vwap"]) == (D("0.52") * 4 + D("0.53") * 4) / 8
    assert no["pm_reference_sell_vwap"] is not None
    assert row["sig_exchange_id"] == no["sig_exchange_id"]


def test_mapping_complement_changes_external_orientation_only() -> None:
    same = match(gate("SAME"))
    complement = match(gate("COMPLEMENT"))
    assert D(complement["pm_reference_sell_vwap"]) != D(same["pm_reference_sell_vwap"])
    assert complement["sig_buy_vwap"] == same["sig_buy_vwap"]


@pytest.mark.parametrize(
    ("frame_change", "reason"),
    [
        ({"trusted": False}, "PM_UNTRUSTED"),
        ({"connected": False}, "PM_DISCONNECTED"),
        ({"continuity": False}, "PM_REVISION_OR_HEALTH_GAP"),
        ({"complete": False}, "PM_INCOMPLETE_SNAPSHOT"),
        ({"bids": [], "asks": []}, "PM_ZERO_DEPTH"),
        ({"bids": [{"price": "0.52", "size": "1"}]}, "PM_CROSSED_OR_LOCKED"),
        ({"exchange_at": (T + timedelta(seconds=10)).isoformat()}, "PM_SOURCE_CLOCK_AHEAD"),
    ],
)
def test_bad_pm_book_never_becomes_opportunity(frame_change: dict[str, Any], reason: str) -> None:
    pm = book("PM", t=T + timedelta(milliseconds=100))
    pm.update(frame_change)
    assert match(gate(), pm=pm)["reason"] == reason


def test_stale_timestamp_and_skew_fail_closed() -> None:
    pm = book("PM", t=T + timedelta(seconds=6))
    assert match(gate(), pm=pm)["reason"] == "SIG_STALE_RECEIPT"
    pm = book("PM", t=T + timedelta(seconds=3))
    assert match(gate(), pm=pm)["reason"] == "PAIR_SKEW"


def test_depth_only_change_changes_output_and_gross_locks() -> None:
    g = gate()
    before = match(g)
    update = book("PM", t=T + timedelta(milliseconds=200))
    update["bids"][0]["size"] = "1"
    after = g.observe(update)[0]
    assert before["pm_reference_sell_vwap"] != after["pm_reference_sell_vwap"]
    assert after["sig_gross_share_requirement"] == 8


def test_duplicate_out_of_order_and_disconnect_record_no_trade() -> None:
    g = gate()
    g.observe(book("SIG"))
    pm = book("PM", t=T + timedelta(milliseconds=100))
    g.observe(pm)
    assert g.observe(pm)[0]["reason"] == "DUPLICATE"
    assert g.observe(book("PM", t=T - timedelta(seconds=1)))[0]["reason"] == "OUT_OF_ORDER_RECEIPT"
    later = book("SIG", t=T + timedelta(seconds=1), connected=False)
    assert g.observe(later)[0]["reason"] == "SIG_DISCONNECTED"


def test_costs_unknown_are_not_positive_economics() -> None:
    row = match(gate(costs=False))
    assert row["decision"] == "NO_TRADE"
    assert row["reason"] == "COSTS_UNKNOWN"


def test_not_a_synthetic_snapshot_generator() -> None:
    g = gate()
    g.observe(book("SIG"))
    row = g.observe(book("PM", t=T + timedelta(milliseconds=100), complete=False))[0]
    assert row["decision"] == "NO_TRADE"
    assert row["reason"] == "PM_INCOMPLETE_SNAPSHOT"
    assert row["observed_fill_qty"] is None


@pytest.mark.parametrize("field", ["trusted", "connected", "complete", "continuity"])
def test_missing_explicit_integrity_attestation_rejected(field: str) -> None:
    g = gate()
    payload = book("SIG")
    payload.pop(field)
    assert g.observe(payload)[0]["reason"].startswith("INVALID_INPUT:")


def test_acceptance_hash_and_verified_exact_identity_required(tmp_path: Path) -> None:
    mapping = {
        "records": [
            {"sig_exchange_id": "947", "sig_market_id": "100",
             "sig_outcome_label": "YES", "status": "VERIFIED",
             "mapping_class": "EXACT", "mapping_direction": "SAME",
             "direct_polymarket": {
                 "token_ids": ["A", "B"], "outcomes": ["Yes", "No"],
                 "mapped_token_id": "A", "mapped_outcome": "Yes",
                 "market_id": "M", "condition_id": "C", "event_id": "E",
             }}
        ]
    }
    raw = json.dumps(mapping).encode()
    m = tmp_path / "map.json"
    a = tmp_path / "acceptance.json"
    m.write_bytes(raw)
    a.write_text(json.dumps({"all_records_verified": True,
                             "artifact_sha256": {"crosswalk_json": hashlib.sha256(raw).hexdigest()}}))
    assert load_specs(m, a, ["947"])["947"].token == "A"
    with pytest.raises(InvalidInput, match="unaccepted"):
        load_specs(m, a, ["NOT-IN-MAPPING"])
    mapping["records"][0]["mapping_class"] = "NEAR"
    m.write_text(json.dumps(mapping))
    with pytest.raises(InvalidInput, match="hash mismatch"):
        load_specs(m, a, ["947"])
    a.write_text(json.dumps({"all_records_verified": True,
                             "artifact_sha256": {"crosswalk_json": hashlib.sha256(m.read_bytes()).hexdigest()}}))
    with pytest.raises(InvalidInput, match="unverified"):
        load_specs(m, a, ["947"])


def test_invalid_price_levels_fail_closed() -> None:
    g = gate()
    raw = book("SIG")
    raw["bids"] = [{"price": "0.48", "size": "4"}, {"price": "0.49", "size": "8"}]
    assert g.observe(raw)[0]["reason"].startswith("INVALID_INPUT:")


def test_source_receipt_clock_and_unsupported_instrument() -> None:
    g = gate()
    sig = book("SIG", exchange=T + timedelta(milliseconds=1))
    assert g.observe(sig)[0]["reason"] == "SIG_SOURCE_CLOCK_AHEAD"
    assert g.observe(book("PM", t=T + timedelta(milliseconds=100),
                          instrument="UNMAPPED")) == []
