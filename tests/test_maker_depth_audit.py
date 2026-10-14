# ruff: noqa: E501, I001
"""Synthetic, adversarial offline tests; never evidence of actual SIG fills."""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal as D
from pathlib import Path
from typing import Any

import pytest

from predictions_cup.research.maker_depth_audit import (
    AuditError, accepted_specs, audit, liquidity, pm_pair_rejections, sha256_file,
)

STAMP = datetime(2026, 10, 14, 1, tzinfo=UTC)
RUN = "a" * 32


def mapping(tmp_path: Path, direction: str = "SAME") -> tuple[dict[str, Any], str]:
    row = {
        "sig_exchange_id": "947", "sig_market_id": "77", "sig_tournament_id": "cup",
        "status": "VERIFIED", "mapping_class": "EXACT", "mapping_direction": direction,
        "sig_outcome_label": "YES", "direct_polymarket": {
            "market_id": "m", "condition_id": "c", "event_id": "event",
            "token_ids": ["pm_yes", "pm_no"], "outcomes": ["Yes", "No"],
            "mapped_token_id": "pm_yes", "mapped_outcome": "Yes"}}
    path = tmp_path / "mapping.json"
    path.write_text(json.dumps({"records": [row]}))
    digest = sha256_file(path)
    acceptance = tmp_path / "acceptance.json"
    acceptance.write_text(json.dumps({"all_records_verified": True,
        "artifact_sha256": {"crosswalk_json": digest}}))
    return accepted_specs(path, acceptance, ("947",)), digest


def book(**override: Any) -> dict[str, Any]:
    row = {
        "schema": "maker-depth-mirror-v1", "event": "SIG_MAKER_REST_RECONCILED_BOOK",
        "run_id": RUN, "exchange_id": "947", "market_id": "77", "tournament_id": "cup",
        "received_at": STAMP.isoformat(), "venue_timestamp": None, "venue_revision": None,
        "triggering_realtime_revision": 15, "source": "MAKER_ALREADY_FETCHED_SIG_REST",
        "source_clock_verified": False, "source_sequence_verified": False,
        "trusted_for_paired_paper": False, "snapshot_at_reconciliation": True,
        "bids": [{"price": "0.45", "quantity": "8"}, {"price": "0.44", "quantity": "20"}],
        "asks": [{"price": "0.55", "quantity": "8"}, {"price": "0.56", "quantity": "20"}],
    }
    row.update(override)
    return row


def fixture(tmp_path: Path, rows: list[dict[str, Any]], last_newline: bool = True) -> Path:
    target = tmp_path / "maker.jsonl"
    content = "\n".join(json.dumps(row) for row in rows)
    if last_newline:
        content += "\n"
    target.write_text(content)
    return target


def audit_rows(tmp_path: Path, rows: list[dict[str, Any]], **kw: Any) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    specs, _ = mapping(tmp_path)
    path = fixture(tmp_path, rows)
    report, ledger, _ = audit(path, sha256_file(path), specs, **kw)
    return report, ledger


def test_accepted_exact_and_mapping_digest_are_mandatory(tmp_path: Path) -> None:
    specs, digest = mapping(tmp_path)
    assert specs["947"].mapping_sha256 == digest
    p = tmp_path / "mapping.json"
    p.write_text(p.read_text().replace('"EXACT"', '"NEAR"'))
    with pytest.raises(AuditError, match="HASH_MISMATCH"):
        accepted_specs(p, tmp_path / "acceptance.json", ("947",))
    with pytest.raises(AuditError, match="UNIVERSE"):
        accepted_specs(p, tmp_path / "acceptance.json", ())


def test_valid_books_are_liquidity_only_not_verified_clock_or_pnl(tmp_path: Path) -> None:
    r, ledger = audit_rows(tmp_path, [book()])
    assert r["valid_two_sided_snapshots"] == 1
    assert r["offline_liquidity_eligible_observations"] == 1
    assert r["independently_certified_causal_paired_paper_observations"] == 0
    assert r["source_book_age_ms"] is None
    assert r["capture_cadence_sec"] is None
    assert r["coverage_rate"] is None and r["producer_counters"]["queue_drops"] is None
    assert r["native_sig_event_time"] is None and r["native_sig_revision"] is None
    assert r["net_pnl"] is None and r["real_trades"] is None
    assert ledger[0]["paired_depth_trusted"] is False
    assert ledger[0]["state"] == "INDICATIVE_LIQUIDITY_ONLY"
    assert ledger[0]["conditional_liquidity"]["buy_yes"]["sizes"]["1"] is not None
    assert ledger[0]["conditional_liquidity"]["buy_yes"]["sizes"]["20"] is None


@pytest.mark.parametrize(("replacement", "reason"), [
    ({"source_clock_verified": True}, "SPOOFED_SOURCE_PROVENANCE"),
    ({"trusted_for_paired_paper": True}, "SPOOFED_SOURCE_PROVENANCE"),
    ({"venue_timestamp": STAMP.isoformat()}, "SPOOFED_SOURCE_PROVENANCE"),
    ({"run_id": "fake"}, "INVALID_RUN_ID"),
    ({"exchange_id": "999"}, "UNACCEPTED_EXCHANGE_ID"),
    ({"market_id": "not-77"}, "MARKET_OR_TOURNAMENT_MISMATCH"),
    ({"bids": []}, "ONE_SIDED_OR_ZERO_DEPTH"),
    ({"asks": [{"price": "0.45", "quantity": "5"}]}, "CROSSED_OR_LOCKED_BOOK"),
    ({"bids": [{"price": "0.50", "quantity": "NaN"}]}, "NONFINITE_DECIMAL"),
    ({"received_at": "2026-10-14T01:00:00"}, "INVALID_RECEIPT_TIMESTAMP"),
    ({"triggering_realtime_revision": True}, "INVALID_TRIGGER_REVISION"),
    ({"schema": "different"}, "WRONG_SCHEMA"),
    ({"asks": [{"price": "0.56", "quantity": "4"}, {"price": "0.55", "quantity": "2"}]}, "UNSORTED_LADDER"),
])
def test_bad_input_is_explicitly_rejected(tmp_path: Path, replacement: dict[str, Any], reason: str) -> None:
    r, ledger = audit_rows(tmp_path, [book(**replacement)])
    assert r["valid_two_sided_snapshots"] == 0
    assert ledger[0]["reason"] == reason
    assert ledger[0]["paired_depth_trusted"] is False


def test_duplicate_out_of_order_and_cross_session_fail_closed(tmp_path: Path) -> None:
    first = book()
    later = book(received_at=(STAMP + timedelta(seconds=1)).isoformat())
    earlier = book(received_at=(STAMP - timedelta(seconds=1)).isoformat())
    other = book(run_id="b" * 32, received_at=(STAMP + timedelta(seconds=2)).isoformat())
    r, ledger = audit_rows(tmp_path, [first, first, later, earlier, other])
    assert r["valid_two_sided_snapshots"] == 2
    assert [x["reason"] for x in ledger[1:]] == [
        "DUPLICATE_RECORD", "SIG_NATIVE_CLOCK_SEQUENCE_UNKNOWN",
        "OUT_OF_ORDER_GLOBAL_RECEIPT", "MULTIPLE_RUNS_IN_FILE"]


def test_gap_and_quote_change_do_not_imply_missing_count(tmp_path: Path) -> None:
    second = book(received_at=(STAMP + timedelta(seconds=51)).isoformat(),
                  bids=[{"price": "0.46", "quantity": "8"}],
                  asks=[{"price": "0.57", "quantity": "8"}])
    r, _ = audit_rows(tmp_path, [book(), second])
    m = r["markets"]["947"]
    assert m["gap_count"] == 1
    assert m["max_gap_seconds"] == 51
    assert m["best_bid_changes"]["p50"] == pytest.approx(.01)
    assert m["bid_quantity_changes"]["p50"] == 20.0  # second bid has 8; first 8+20
    assert m["ask_quantity_changes"]["p50"] == 20.0
    assert m["conditional_buy_yes_slippage"]["5"]["count"] == 1  # second snapshot fails haircut
    assert r["missing_segments"] is None
    assert r["source_book_age_ms"] is None


def test_truncated_and_corrupted_json_are_not_silently_skipped(tmp_path: Path) -> None:
    specs, _ = mapping(tmp_path)
    p = fixture(tmp_path, [book()], last_newline=False)
    r, ledger, _ = audit(p, sha256_file(p), specs)
    assert not r["input"]["complete_final_newline"]
    assert ledger[0]["reason"] == "TRUNCATED_FINAL_LINE"
    p.write_bytes(b'{"no":\n')
    r, ledger, _ = audit(p, sha256_file(p), specs)
    assert ledger[0]["reason"] == "MALFORMED_JSONL_RECORD"
    assert r["rejected_snapshots"] == 1


def test_file_digest_and_cap_fail_before_ingestion(tmp_path: Path) -> None:
    specs, _ = mapping(tmp_path)
    p = fixture(tmp_path, [book()])
    with pytest.raises(AuditError, match="INPUT_SHA256_MISMATCH"):
        audit(p, "0" * 64, specs)
    with pytest.raises(AuditError, match="FILE_OVER_APPROVED_CAP"):
        audit(p, sha256_file(p), specs, max_bytes=10)


def test_fee_unknown_and_tick_assumption_are_explicit(tmp_path: Path) -> None:
    r, ledger = audit_rows(tmp_path, [book()], tick=D("0.01"))
    quote = ledger[0]["conditional_liquidity"]["buy_no"]["sizes"]["1"]
    assert quote["fee_adjusted_cost_per_share"] is None
    assert quote["vwap"] == "0.55"  # 1 - SIG best bid 0.45
    assert r["tick_assumption"] == "0.01"
    r, ledger = audit_rows(tmp_path, [book(asks=[{"price": "0.555", "quantity": "8"}])],
                           tick=D("0.01"))
    assert ledger[0]["reason"] == "PRICE_OFF_ASSUMED_TICK"


def test_counters_cannot_prove_drop_rate_or_complete_session(tmp_path: Path) -> None:
    r, _ = audit_rows(tmp_path, [book()], counters={"accepted": 2, "written": 1,
        "dropped": 3, "errors": 0, "queue_pending": 0})
    assert r["producer_counters"]["schema"] == "LEGACY_UNCLASSIFIED_DROPS"
    assert r["producer_counters"]["reported_dropped"] == 3
    assert r["producer_counters"]["queue_drops"] is None
    assert r["producer_counters"]["drop_rate"] is None
    assert r["producer_counters"]["independently_attested"] is False
    assert r["coverage_rate"] is None


def test_pm_adapter_respects_asof_uses_size_and_never_certifies(tmp_path: Path) -> None:
    specs, _ = mapping(tmp_path)
    path = fixture(tmp_path, [book()])
    _, _, books = audit(path, sha256_file(path), specs)
    pm = tmp_path / "pm.jsonl"
    pm.write_text(json.dumps({"venue": "PM", "instrument_id": "pm_yes",
        "received_at": (STAMP - timedelta(milliseconds=100)).isoformat(),
        "bids": [{"price": "0.66", "size": "20"}],
        "asks": [{"price": "0.70", "size": "20"}], "trusted": True,
        "continuity": True, "source_clock_verified": True}) + "\n")
    rows, report = pm_pair_rejections(books, specs, pm, sha256_file(pm), fee_per_share=D("0.02"))
    assert len(rows) == 2
    assert report["certified_pair_count"] == 0
    assert report["conditional_comparisons"] == 2
    assert all(x["decision"] == "NO_TRADE" for x in rows)
    assert all(x["sig_exchange_at"] is None for x in rows)
    assert all(x["paired_depth_trusted"] is False for x in rows)
    assert rows[0]["conditional_only"]["currency_comparable"] is False
    assert rows[0]["conditional_only"]["indicative_after_assumed_cost"] is not None
    assert rows[0]["observed_pnl"] is None


def test_pm_future_and_stale_frames_never_used_at_decision(tmp_path: Path) -> None:
    specs, _ = mapping(tmp_path)
    path = fixture(tmp_path, [book()])
    _, _, books = audit(path, sha256_file(path), specs)
    pm = tmp_path / "pm.jsonl"
    row = {"venue": "PM", "instrument_id": "pm_yes",
           "received_at": (STAMP + timedelta(milliseconds=100)).isoformat(),
           "bids": [{"price": "0.66", "size": "20"}],
           "asks": [{"price": "0.70", "size": "20"}]}
    pm.write_text(json.dumps(row) + "\n")
    rows, _ = pm_pair_rejections(books, specs, pm, sha256_file(pm))
    assert all(x["reason"] == "PM_MISSING_ASOF_DEPTH" for x in rows)
    row["received_at"] = (STAMP - timedelta(seconds=8)).isoformat()
    pm.write_text(json.dumps(row) + "\n")
    rows, _ = pm_pair_rejections(books, specs, pm, sha256_file(pm))
    assert all(x["reason"] == "PM_STALE_RECEIPT" for x in rows)


def test_no_side_complement_ladders_use_sig_bids_only(tmp_path: Path) -> None:
    specs, _ = mapping(tmp_path)
    path = fixture(tmp_path, [book()])
    _, _, books = audit(path, sha256_file(path), specs)
    liq_result = liquidity(books[0], (1, 4, 5), D("0.5"), None)
    assert liq_result["buy_yes"]["sizes"]["4"]["vwap"] == "0.55"
    assert liq_result["buy_no"]["sizes"]["4"]["vwap"] == "0.55"
    assert liq_result["buy_no"]["sizes"]["5"]["marginal"] == "0.56"


def test_empty_run_is_not_claimed_as_complete(tmp_path: Path) -> None:
    r, _ = audit_rows(tmp_path, [])
    assert r["sessions_detected"] == 0
    assert r["offline_liquidity_eligible_observations"] == 0
    assert r["capture_cadence_sec"] is None
    assert r["independently_certified_causal_paired_paper_observations"] == 0


@pytest.mark.parametrize(("quantity", "reason"), [
    ("2.5", "FRACTIONAL_QUANTITY_UNVERIFIED"),
    ("1000000001", "QUANTITY_EXCEEDS_RESEARCH_CAP"),
    ("1E+1000000", "QUANTITY_EXCEEDS_RESEARCH_CAP"),
])
def test_invalid_fractional_or_unbounded_depth_is_not_liquidity(
    tmp_path: Path, quantity: str, reason: str,
) -> None:
    row = book()
    row["bids"][0]["quantity"] = quantity
    report, ledger = audit_rows(tmp_path, [row])
    assert report["valid_two_sided_snapshots"] == 0
    assert report["offline_liquidity_eligible_observations"] == 0
    assert ledger[0]["reason"] == reason


def test_corrected_producer_counters_report_rejected_and_lost_separately(
    tmp_path: Path,
) -> None:
    # A producer snapshot may correspond to a wider capture than our one-row
    # illustrative file; arithmetic is only diagnostic and NEVER provenance.
    counters = {
        "accepted": 3, "written": 2, "dropped": 3,
        "producer_attempted": 5, "producer_rejected": 2, "writer_lost": 1,
        "errors": 1, "queue_pending": 0, "disabled": True,
    }
    r, _ = audit_rows(tmp_path, [book()], counters=counters)
    c = r["producer_counters"]
    assert c["schema"] == "CORRECTED_PRODUCER_V2"
    assert c["producer_attempted"] == 5
    assert c["producer_rejected"] == c["queue_drops"] == 2
    assert c["writer_lost"] == 1
    assert c["reported_dropped"] == 3
    assert c["drop_rate"] == pytest.approx(3 / 5)
    assert c["counter_arithmetic_ok"] is True
    assert c["accepted_balance_without_inflight"] is True
    assert c["written_equals_raw_line_count_unattested"] is False
    assert c["terminal_quiescence_proven"] is False
    assert c["independently_attested"] is False
    assert r["coverage_rate"] is None


@pytest.mark.parametrize("broken", [
    {"producer_attempted": 6},
    {"writer_lost": -1},
    {"producer_rejected": "2"},
])
def test_malformed_v2_counters_cannot_assert_drop_rate(
    tmp_path: Path, broken: dict[str, object],
) -> None:
    counters: dict[str, object] = {
        "accepted": 3, "written": 2, "dropped": 3,
        "producer_attempted": 5, "producer_rejected": 2, "writer_lost": 1,
        "errors": 1, "queue_pending": 0,
    }
    counters.update(broken)
    r, _ = audit_rows(tmp_path, [book()], counters=counters)
    c = r["producer_counters"]
    if set(broken) == {"producer_attempted"} and broken["producer_attempted"] == 6:
        # All fields present but arithmetic contradictory: may never be called
        # verified, even though typed values can still be displayed.
        assert c["counter_arithmetic_ok"] is False
        assert c["drop_rate"] is None
    else:
        assert c["schema"] == "INVALID_PARTIAL_V2"
        assert c["drop_rate"] is None
    assert c["terminal_quiescence_proven"] is False
    assert c["independently_attested"] is False
