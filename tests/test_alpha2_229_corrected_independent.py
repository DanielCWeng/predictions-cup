"""Independent #229 corrected-head falsification, synthetic files and venue-free.

Frozen #231 producer and #223 Lane C source are copied byte-for-byte into
tests/reviewer_229_fixtures ONLY: neither is in actual #229 production tree.
"""

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from predictions_cup.models.market import OrderBook, OrderBookLevel
from predictions_cup.research.maker_depth_audit import (
    AuditError,
    accepted_specs,
    audit,
    pm_pair_rejections,
    sha256_file,
)
from reviewer_229_fixtures.depth_mirror import MakerDepthMirror
from reviewer_229_fixtures.microstructure_c_bridge import bridge_lane_b

OBSERVED = datetime(2026, 10, 17, 0, tzinfo=UTC)


def _mapping(tmp_path: Path) -> dict[str, object]:
    mapping_path = tmp_path / "exact.json"
    mapping_path.write_text(
        json.dumps(
            {"records": [{
                "sig_exchange_id": "947",
                "sig_market_id": "77",
                "sig_tournament_id": "cup",
                "status": "VERIFIED",
                "mapping_class": "EXACT",
                "mapping_direction": "SAME",
                "sig_outcome_label": "YES",
                "direct_polymarket": {
                    "event_id": "e1", "token_ids": ["pm_yes", "pm_no"],
                    "outcomes": ["Yes", "No"], "mapped_token_id": "pm_yes",
                    "mapped_outcome": "Yes",
                },
            }]}
        ),
        encoding="utf-8",
    )
    acceptance_path = tmp_path / "accepted.json"
    acceptance_path.write_text(
        json.dumps({
            "all_records_verified": True,
            "artifact_sha256": {"crosswalk_json": sha256_file(mapping_path)},
        }),
        encoding="utf-8",
    )
    return accepted_specs(mapping_path, acceptance_path, ("947",))


def _row(quantity: str = "20") -> dict[str, object]:
    return {
        "schema": "maker-depth-mirror-v1",
        "event": "SIG_MAKER_REST_RECONCILED_BOOK",
        "source": "MAKER_ALREADY_FETCHED_SIG_REST",
        "snapshot_at_reconciliation": True,
        "run_id": "a" * 32,
        "exchange_id": "947",
        "market_id": "77",
        "tournament_id": "cup",
        "received_at": OBSERVED.isoformat(),
        "triggering_realtime_revision": 12,
        "venue_timestamp": None,
        "venue_revision": None,
        "source_clock_verified": False,
        "source_sequence_verified": False,
        "trusted_for_paired_paper": False,
        "bids": [{"price": "0.45", "quantity": quantity}],
        "asks": [{"price": "0.55", "quantity": "20"}],
    }


def _audit(tmp_path: Path, quantity: str = "20", counters: dict[str, int] | None = None):
    specs = _mapping(tmp_path)
    raw_path = tmp_path / "source.jsonl"
    raw_path.write_text(json.dumps(_row(quantity)) + "\n", encoding="utf-8")
    return audit(raw_path, sha256_file(raw_path), specs, counters=counters)


COUNTERS: dict[str, int] = {
    "accepted": 4,
    "written": 2,
    "dropped": 3,
    "producer_attempted": 6,
    "producer_rejected": 2,
    "writer_lost": 1,
    "queue_pending": 1,
    "errors": 0,
}


@pytest.mark.parametrize(
    "overrides",
    [
        {"written": 5},
        {"writer_lost": 5, "dropped": 7},
        {"queue_pending": 3},
        {"producer_attempted": 5},
        {"producer_rejected": 3},
        {"dropped": 4},
        {"accepted": 1, "producer_attempted": 3},
        {"producer_attempted": 0},
        {"queue_pending": 10**9},
    ],
)
def test_every_impossible_or_incoherent_v2_counter_nulls_loss_statistics(
    tmp_path: Path, overrides: dict[str, int],
) -> None:
    counters = {**COUNTERS, **overrides}
    report, rows, books = _audit(tmp_path, counters=counters)
    c = report["producer_counters"]
    assert len(books) == 1 and len(rows) == 1
    assert c["schema"] == "CORRECTED_PRODUCER_V2"
    assert c["counter_arithmetic_ok"] is False
    assert c["drop_rate"] is None
    assert c["queue_drops"] is None
    assert c["producer_rejected"] is None
    assert c["writer_lost"] is None
    assert c["unobserved_producer_attempts"] is None
    assert c["terminal_quiescence_proven"] is False
    assert c["independently_attested"] is False
    assert report["coverage_rate"] is None
    assert report["independently_certified_causal_paired_paper_observations"] == 0


@pytest.mark.parametrize(
    "malformed",
    [
        {"writer_lost": -1},
        {"producer_attempted": True},
        {"producer_rejected": "2"},
        {"producer_attempted": None},
    ],
)
def test_partial_or_type_invalid_v2_never_reports_finite_rate(
    tmp_path: Path, malformed: dict[str, object],
) -> None:
    report, _, _ = _audit(tmp_path, counters={**COUNTERS, **malformed})
    c = report["producer_counters"]
    assert c["schema"] == "INVALID_PARTIAL_V2"
    assert c["drop_rate"] is None
    assert c["queue_drops"] is None
    assert c["terminal_quiescence_proven"] is False


def test_bounded_inflight_underbalance_does_not_claim_terminal_completion(
    tmp_path: Path,
) -> None:
    counters = {
        "accepted": 5, "written": 2, "writer_lost": 1, "queue_pending": 1,
        "producer_attempted": 7, "producer_rejected": 2,
        "dropped": 3, "errors": 0,
    }
    report, _, _ = _audit(tmp_path, counters=counters)
    c = report["producer_counters"]
    assert c["schema"] == "CORRECTED_PRODUCER_V2"
    assert c["counter_feasibility_ok"] is True
    assert c["counter_arithmetic_ok"] is True
    assert c["accepted_balance_without_inflight"] is False
    assert c["drop_rate"] == pytest.approx(3 / 7)
    assert c["independently_attested"] is False
    assert c["terminal_quiescence_proven"] is False
    assert report["coverage_rate"] is None


@pytest.mark.parametrize(
    ("quantity", "reason"),
    [
        ("1000000000.00000001", "FRACTIONAL_QUANTITY_UNVERIFIED"),
        ("1000000001", "QUANTITY_EXCEEDS_RESEARCH_CAP"),
        ("1E+1000000", "QUANTITY_EXCEEDS_RESEARCH_CAP"),
        ("1E-1000000", "FRACTIONAL_QUANTITY_UNVERIFIED"),
        ("20.00000001", "FRACTIONAL_QUANTITY_UNVERIFIED"),
    ],
)
def test_sig_fractional_or_over_cap_levels_never_gain_liquidity(
    tmp_path: Path, quantity: str, reason: str,
) -> None:
    report, ledger, snapshots = _audit(tmp_path, quantity)
    assert not snapshots
    assert report["offline_liquidity_eligible_observations"] == 0
    assert report["independently_certified_causal_paired_paper_observations"] == 0
    assert len(ledger) == 1
    assert ledger[0]["state"] == "REJECTED"
    assert ledger[0]["reason"] == reason


def test_exact_research_cap_is_admitted_for_indicative_liquidity_only(
    tmp_path: Path,
) -> None:
    report, ledger, snapshots = _audit(tmp_path, "1000000000")
    assert len(snapshots) == 1
    assert report["offline_liquidity_eligible_observations"] == 1
    assert report["independently_certified_causal_paired_paper_observations"] == 0
    assert ledger[0]["paired_depth_trusted"] is False
    assert ledger[0]["certified_paired_paper"] is False


@pytest.mark.parametrize("pm_size", ["20", "20.5", "20.00000001"])
def test_frozen_test_only_producer_auditor_lane_c_no_trade_bridge(
    tmp_path: Path, pm_size: str,
) -> None:
    """Actual writer is frozen #231 test fixture, bridge frozen #223 fixture."""
    specs = _mapping(tmp_path)
    source = tmp_path / "actual-synthetic-writer.jsonl"
    writer = MakerDepthMirror(source)
    writer.start()
    book = OrderBook(
        exchange_id="947",
        bids=(OrderBookLevel(price=Decimal("0.45"), quantity=Decimal(20)),),
        asks=(OrderBookLevel(price=Decimal("0.55"), quantity=Decimal(20)),),
        timestamp=OBSERVED,
        source="fake-rest",
    )
    writer.record(book, "77", "cup", OBSERVED, 17)
    writer.close(timeout=2)
    counters = writer.snapshot()
    assert counters["accepted"] == counters["written"] == 1
    assert counters["writer_lost"] == counters["producer_rejected"] == 0
    source_sha = sha256_file(source)
    report, ledger, parsed = audit(source, source_sha, specs, counters=counters)
    assert report["input"]["sha256"] == source_sha
    assert report["producer_counters"]["counter_arithmetic_ok"] is True
    assert report["producer_counters"]["written_equals_raw_line_count_unattested"] is True
    assert report["producer_counters"]["independently_attested"] is False
    assert report["independently_certified_causal_paired_paper_observations"] == 0
    assert len(parsed) == 1
    assert ledger[0]["paired_depth_trusted"] is False

    pm = tmp_path / "pm-fake.jsonl"
    pm.write_text(
        json.dumps({
            "venue": "PM",
            "instrument_id": "pm_yes",
            "received_at": (OBSERVED - timedelta(milliseconds=100)).isoformat(),
            "bids": [{"price": "0.66", "size": pm_size}],
            "asks": [{"price": "0.70", "size": "20"}],
            "trusted": True,
            "continuity": True,
            "source_clock_verified": True,
        }) + "\n",
        encoding="utf-8",
    )
    pm_sha = sha256_file(pm)
    paper, pm_report = pm_pair_rejections(
        parsed, specs, pm, pm_sha, fee_per_share=Decimal("0.02"),
    )
    assert len(paper) == 2
    assert pm_report["pm_readable_depth_rows"] == 1
    assert pm_report["certified_pair_count"] == 0
    assert all(p["decision"] == "NO_TRADE" for p in paper)
    assert all(p["paired_depth_trusted"] is False for p in paper)
    assert all(p["sig_exchange_at"] is None for p in paper)
    assert all(p["observed_pnl"] is None for p in paper)
    assert all(p["observed_fill_qty"] is None for p in paper)
    assert all(p["fill_probability"] is None for p in paper)
    assert all(p["pm_depth"]["bids"][0]["size"] == pm_size for p in paper)

    episodes, lane_c = bridge_lane_b(paper)
    assert len(episodes) == 1
    assert len(lane_c["source_ledger"]) == 2
    assert lane_c["preserved_source_rejection_count"] == 2
    assert lane_c["source_row_count"] == 2
    assert all(row["source_gate"] == "NO_TRADE" for row in lane_c["source_ledger"])
    assert episodes[0]["paired_depth_trusted"] is False
    assert episodes[0]["exact_mapping_verified"] is False
    assert lane_c["measured_real_executable_opportunities"] is None
    assert lane_c["measured_net_pnl"] is None

    # Even with internally consistent writer counts, a replaced byte image is
    # not accepted unless the exact original external SHA is supplied.
    source.write_bytes(source.read_bytes() + b" ")
    with pytest.raises(AuditError, match="INPUT_SHA256_MISMATCH"):
        audit(source, source_sha, specs, counters=counters)
