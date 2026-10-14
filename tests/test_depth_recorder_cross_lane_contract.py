"""Deterministic offline #227 -> #229 -> #223 cross-lane contract regression.

No network, exchange clients, production files, credentials or order placement.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal as D
from pathlib import Path

import pytest

from predictions_cup.models.market import OrderBook, OrderBookLevel
from predictions_cup.replay.microstructure_c_bridge import bridge_lane_b
from predictions_cup.research.maker_depth_audit import (
    accepted_specs,
    audit,
    pm_pair_rejections,
    sha256_file,
)
from predictions_cup.sig.depth_mirror import MakerDepthMirror


@pytest.mark.parametrize("pm_size", ["20", "20.5", "20.00000001"])
def test_actual_recorder_to_auditor_to_lane_c_is_never_promoted(
    tmp_path: Path, pm_size: str,
) -> None:
    received = datetime(2026, 10, 14, 10, tzinfo=UTC)
    raw_file = tmp_path / "producer.jsonl"
    producer = MakerDepthMirror(raw_file, queue_size=8)
    producer.start()
    book = OrderBook(
        exchange_id="947",
        bids=(OrderBookLevel(price=D("0.45"), quantity=D("20")),),
        asks=(OrderBookLevel(price=D("0.55"), quantity=D("20")),),
        timestamp=received,
        source="sig-rest",
    )
    producer.record(book, "77", "cup", received, 12)
    producer.close(timeout=2.0)
    counters = producer.snapshot()
    assert counters["producer_attempted"] == 1
    assert counters["producer_rejected"] == 0
    assert counters["accepted"] == counters["written"] == 1
    assert counters["writer_lost"] == counters["dropped"] == 0
    assert counters["queue_pending"] == 0

    lines = raw_file.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    exported = json.loads(lines[0])
    assert exported["source_clock_verified"] is False
    assert exported["source_sequence_verified"] is False
    assert exported["venue_timestamp"] is None
    assert exported["venue_revision"] is None
    assert exported["trusted_for_paired_paper"] is False

    mapping_path = tmp_path / "exact-mapping.json"
    mapping_path.write_text(json.dumps({"records": [{
        "sig_exchange_id": "947", "sig_market_id": "77", "sig_tournament_id": "cup",
        "status": "VERIFIED", "mapping_class": "EXACT", "mapping_direction": "SAME",
        "sig_outcome_label": "YES",
        "direct_polymarket": {
            "event_id": "e", "token_ids": ["pm_yes", "pm_no"],
            "outcomes": ["Yes", "No"], "mapped_token_id": "pm_yes",
            "mapped_outcome": "Yes",
        },
    }]}), encoding="utf-8")
    acceptance_path = tmp_path / "exact-acceptance.json"
    acceptance_path.write_text(json.dumps({
        "all_records_verified": True,
        "artifact_sha256": {"crosswalk_json": sha256_file(mapping_path)},
    }), encoding="utf-8")
    specs = accepted_specs(mapping_path, acceptance_path, ("947",))
    audited, ledger, snapshots = audit(
        raw_file, sha256_file(raw_file), specs, counters=counters,
    )
    assert audited["valid_two_sided_snapshots"] == 1
    assert audited["offline_liquidity_eligible_observations"] == 1
    assert audited["independently_certified_causal_paired_paper_observations"] == 0
    assert audited["producer_counters"]["counter_arithmetic_ok"] is True
    assert audited["producer_counters"]["terminal_quiescence_proven"] is False
    assert audited["producer_counters"]["written_equals_raw_line_count_unattested"] is True
    assert ledger[0]["paired_depth_trusted"] is False
    assert len(snapshots) == 1

    pm_path = tmp_path / "pm.jsonl"
    pm_path.write_text(json.dumps({
        "venue": "PM", "instrument_id": "pm_yes",
        "received_at": (received - timedelta(milliseconds=100)).isoformat(),
        "bids": [{"price": "0.66", "size": pm_size}],
        "asks": [{"price": "0.70", "size": "20"}],
        # Deliberately forged trust cannot promote paired data.
        "trusted": True, "continuity": True, "source_clock_verified": True,
    }) + "\n", encoding="utf-8")
    paper, pm_evidence = pm_pair_rejections(
        snapshots, specs, pm_path, sha256_file(pm_path), fee_per_share=D("0.02"),
    )
    assert pm_evidence["certified_pair_count"] == 0
    assert len(paper) == 2
    assert {r["side"] for r in paper} == {"BUY_YES", "BUY_NO"}
    assert all(r["decision"] == "NO_TRADE" for r in paper)
    assert all(r["paired_depth_trusted"] is False for r in paper)
    assert all(r["sig_exchange_at"] is None for r in paper)
    episodes, lane_c = bridge_lane_b(paper)
    assert len(episodes) == 1
    assert lane_c["source_row_count"] == 2
    assert lane_c["preserved_source_rejection_count"] == 2
    assert len(lane_c["source_ledger"]) == 2
    assert all(x["source_gate"] == "NO_TRADE" for x in lane_c["source_ledger"])
    assert episodes[0]["exact_mapping_verified"] is False
    assert episodes[0]["paired_depth_trusted"] is False
    assert lane_c["measured_real_executable_opportunities"] is None
    assert lane_c["measured_net_pnl"] is None
