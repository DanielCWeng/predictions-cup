"""Lane-B PAPER source ingestion never creates phantom microstructure profit."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest

from predictions_cup.replay.microstructure_c import CANDIDATES, evaluate
from predictions_cup.replay.microstructure_c_bridge import bridge_lane_b

T = datetime(2026, 10, 13, 0, 0, tzinfo=UTC)


def frame(*, valid: bool = True) -> dict[str, Any]:
    s = T.isoformat()
    return {
        "schema_version": "lane-b-paper-v1", "label": "PAPER_NOT_OBSERVED_FILL",
        "sequence": 1, "decision_at": (T + timedelta(milliseconds=200)).isoformat(),
        "sig_exchange_id": "947", "sig_market_id": "100",
        "pm_token_id": "PM-TEST-YES", "pm_market_id": "PM-100",
        "pm_event_id": "event1", "mapping_direction": "SAME",
        "side": "BUY_YES", "decision": "PAPER_COMPARABLE_NOT_EXECUTED",
        "reason": "UNOBSERVED_FILL_AND_UNCALIBRATED_QUEUE",
        "sig_depth": {"bids": [{"price": "0.48", "size": "4"}],
                      "asks": [{"price": "0.51", "size": "3"}]},
        "pm_depth": {"bids": [{"price": "0.52", "size": "3"}],
                     "asks": [{"price": "0.54", "size": "4"}]},
        "sig_exchange_at": s, "pm_exchange_at": s,
        "sig_received_at": s, "pm_received_at": (T + timedelta(milliseconds=100)).isoformat(),
        "sig_trust": True, "pm_trust": valid, "observed_fill_qty": None,
        "observed_pnl": None,
    }


def test_valid_synthetic_b_is_still_unattested_and_no_trades() -> None:
    rows, report = bridge_lane_b([frame()])
    assert report["source_row_count"] == 1
    assert report["source_ledger"][0]["reason"] == "SOURCE_EPOCH_SEED_AUDIT_NOT_YET_PROVEN"
    assert rows[0]["paired_depth_trusted"] is False
    assert rows[0]["exact_mapping_verified"] is False
    rows[0]["exact_mapping_verified"] = True  # canonical registry controls identity
    result = evaluate(rows, cash_free=Decimal(100), gross_free=Decimal(100))
    assert len(result["ledger"]) == len(CANDIDATES)
    assert next(x for x in result["ledger"] if x["strategy"] == "NO_TRADE")[
        "status"] == "ABSTAIN"
    assert all(x["reason"] == "paired_depth_not_trusted"
               for x in result["ledger"] if x["strategy"] != "NO_TRADE")
    assert all(s["measured_realized_net_pnl"] is None
               for s in result["scorecard"].values())
    assert result["scorecard"]["NO_TRADE"]["abstain_count"] == 1
    assert result["scorecard"]["NO_TRADE"]["baseline_incremental_new_trade_pnl"] == 0.0
    assert result["scorecard"]["DIRECT_FV"]["candidate_opportunities"] is None


@pytest.mark.parametrize(("mutate", "reason"), [
    ({"pm_trust": False, "reason": "PM_UNTRUSTED",
      "decision": "NO_TRADE"}, "LANE_B_NO_TRADE_OR_UNTRUSTED"),
    ({"sig_depth": None}, "LANE_B_MISSING_EXECUTABLE_DEPTH"),
    ({"pm_depth": {"bids": [], "asks": []}}, "LANE_B_MISSING_EXECUTABLE_DEPTH"),
    ({"pm_received_at": (T + timedelta(seconds=5)).isoformat()},
     "LANE_B_CLOCK_CAUSALITY"),
    ({"sig_received_at": (T - timedelta(seconds=5)).isoformat(),
      "sig_exchange_at": (T - timedelta(seconds=5)).isoformat()},
     "LANE_B_STALE_OR_SKEWED_RECEIPTS"),
    ({"schema_version": "unknown"}, "LANE_B_SCHEMA_UNVERIFIED"),
    ({"mapping_direction": "UNVERIFIED"}, "LANE_B_UNVERIFIED_MAPPING_IDENTITY"),
])
def test_source_problems_are_not_relabelled_as_missing_signals(
    mutate: dict[str, Any], reason: str
) -> None:
    row = frame()
    row.update(mutate)
    _, report = bridge_lane_b([row])
    assert report["source_ledger"][0]["reason"] == reason
    assert report["measured_net_pnl"] is None


def test_preserve_every_original_rejection_even_unparseable() -> None:
    a = frame()
    a["decision_at"] = "bad-timestamp"
    b = frame()
    b["decision_at"] = "bad-timestamp"
    c = frame()
    c["sequence"] = 3
    rows, report = bridge_lane_b([a, b, c])
    assert len(rows) == 1
    assert len(report["source_ledger"]) == 3
    assert report["source_ledger"][0]["reason"] == "LANE_B_CLOCK_CAUSALITY"
    assert report["source_ledger"][1]["reason"] == "DUPLICATED_PAPER_EPISODE"
    assert report["source_ledger"][2]["reason"] == "SOURCE_EPOCH_SEED_AUDIT_NOT_YET_PROVEN"
    assert report["source_row_count"] == 3


def test_already_failed_lane_b_row_is_preserved_not_deleted() -> None:
    row = frame(valid=False)
    row["decision"] = "NO_TRADE"
    row["reason"] = "PM_REVISION_OR_HEALTH_GAP"
    rows, report = bridge_lane_b([row])
    assert report["lane_b_reason_counts"]["PM_REVISION_OR_HEALTH_GAP"] == 1
    assert report["source_ledger"][0]["lane_b_decision"] == "NO_TRADE"
    assert rows[0]["paired_depth_trusted"] is False


def test_duplicate_valid_paper_rows_never_generate_two_opportunities() -> None:
    row = frame()
    rows, report = bridge_lane_b([row, row])
    assert report["preserved_source_rejection_count"] == 2
    assert report["source_ledger"][1]["reason"] == "DUPLICATED_PAPER_EPISODE"
    assert all(not r["paired_depth_trusted"] for r in rows)


def test_two_sides_of_same_lane_b_frame_do_not_double_opportunity() -> None:
    yes = frame()
    no = frame()
    no["side"] = "BUY_NO"
    rows, report = bridge_lane_b([yes, no])
    assert len(rows) == 1
    assert report["source_row_count"] == 2
    assert report["preserved_source_rejection_count"] == 2
    assert report["source_ledger"][1]["reason"] == "SECOND_SIDE_OF_SAME_PAIRED_SNAPSHOT"
