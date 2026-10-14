"""Entirely synthetic provenance fixtures; do not infer market PnL."""
from __future__ import annotations

import csv
import io
from pathlib import Path
from runpy import run_path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts/research/lane_c_historical_maker_audit.py"
audit_fills = run_path(str(_SCRIPT))["audit_fills"]

HEADER = [
    "venue_fill_id", "venue_order_id", "exchange_id", "market_id", "filled_at",
    "side", "signed_quantity", "price", "status", "confidence", "journal_event_id",
    "journal_event_type", "journal_fill_id", "journal_order_id",
    "journal_exchange_id", "journal_quantity", "journal_price",
    "journal_timestamp", "journal_strategy_family",
]


def row(*, fill: str = "42", status: str = "EXACT_AUTHORITATIVE_FILL_ID",
        confidence: str = "FILL_CONFIRMED", family: str = "MAKE") -> dict[str, str]:
    return dict(zip(HEADER, [
        fill, "o1", "101", "m1", "2026-10-01T12:00:00Z", "no", "-10", ".30",
        status, confidence, "17", "AUTHORITATIVE_FILL", fill, "o1", "101",
        "-10", ".3", "2026-10-01T12:00:00Z", family,
    ], strict=True))


def payload(rows: list[dict[str, str]]) -> bytes:
    stream = io.StringIO()
    writer = csv.DictWriter(stream, fieldnames=HEADER)
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue().encode()


def aggregate(rows: list[dict[str, str]]) -> dict[str, object]:
    from collections import Counter
    return {
        "venue_fill_count": len(rows),
        "confirmed_fill_count": sum(x["confidence"].startswith("FILL_CONFIRMED") for x in rows),
        "order_only_count": sum(x["confidence"] == "ORDER_ONLY" for x in rows),
        "unique_venue_order_count": len({x["venue_order_id"] for x in rows}),
        "confidence_counts": dict(Counter(x["confidence"] for x in rows)),
        "status_counts": dict(Counter(x["status"] for x in rows)),
        "snapshot_sha256": "venue-source-synthetic-hash",
    }


def test_make_label_is_not_passive_executed_profit() -> None:
    rows = [row()]
    report, ledger = audit_fills(payload(rows), lane_a_aggregate=aggregate(rows))
    assert report["confirmed_fill_row_strategy_family"] == {"MAKE": 1, "TAKE": 0, "UNKNOWN": 0}
    assert report["independently_verified_passive_maker_fills"] is None
    assert report["maker_eligible_order_ack_count"] is None
    assert report["measured_after_cost_maker_pnl"] is None
    assert report["shadow_hypotheses_proposed"] == []
    assert ledger[0]["executable_exit_markouts"] == {
        "5s": None, "30s": None, "5m": None, "1h": None,
    }


def test_order_only_taker_or_maker_does_not_become_fill_verified() -> None:
    weak = row(status="ORDER_HAS_AGGREGATE_SUMMARY", confidence="ORDER_ONLY")
    weak["journal_event_type"] = "FILL_SUMMARY"
    weak["journal_timestamp"] = ""
    weak["journal_price"] = ""
    result, ledger = audit_fills(payload([weak]))
    assert result["confirmed_fill_level"] == 0
    assert result["order_level_only"] == 1
    assert ledger[0]["maker_taker_attribution"] == "NOT_FILL_ATTRIBUTABLE"
    assert ledger[0]["fill_level_confidence"] is False


def test_realtime_match_within_two_seconds_and_mixed_sides() -> None:
    first = row(status="UNIQUE_REALTIME_ECONOMIC_TUPLE",
                confidence="FILL_CONFIRMED_NO_SHARED_ID", family="TAKE")
    first["journal_event_type"] = "REALTIME_FILL"
    first["journal_fill_id"] = ""
    first["journal_timestamp"] = "2026-10-01T12:00:01.5Z"
    second = row(fill="43", family="")
    second["venue_order_id"] = "o2"
    second["journal_order_id"] = "o2"
    second["side"] = "yes"
    second["signed_quantity"] = "10"
    second["journal_quantity"] = "10"
    report, _ = audit_fills(payload([first, second]))
    assert report["confirmed_fill_row_strategy_family"] == {"MAKE": 0, "TAKE": 1, "UNKNOWN": 1}
    assert report["confirmed_fill_side_counts"] == {"NO": 1, "YES": 1}


@pytest.mark.parametrize("change", [
    {"journal_quantity": "11"},
    {"journal_order_id": "different"},
    {"journal_exchange_id": "other"},
    {"journal_price": "0.31"},
    {"journal_fill_id": "other"},
    {"journal_timestamp": ""},
    {"price": "NaN"},
    {"filled_at": "2026-10-01T12:00:00"},
    {"confidence": "ORDER_ONLY"},
])
def test_no_forged_proof_or_bad_values(change: dict[str, str]) -> None:
    f = row()
    f.update(change)
    with pytest.raises(ValueError):
        audit_fills(payload([f]))


def test_lagging_realtime_and_duplicate_venue_fill_blocked() -> None:
    f = row(status="UNIQUE_REALTIME_ECONOMIC_TUPLE", confidence="FILL_CONFIRMED_NO_SHARED_ID")
    f["journal_event_type"] = "REALTIME_FILL"
    f["journal_timestamp"] = "2026-10-01T11:00:00Z"
    with pytest.raises(ValueError, match="outside"):
        audit_fills(payload([f]))
    with pytest.raises(ValueError, match="duplicate"):
        audit_fills(payload([row(), row()]))


def test_hash_and_lane_a_aggregate_fail_closed() -> None:
    f = row()
    src = payload([f])
    with pytest.raises(ValueError, match="SHA256"):
        audit_fills(src, expected_sha256="0" * 64)
    agg = aggregate([f])
    agg["confirmed_fill_count"] = 999
    with pytest.raises(ValueError, match="disagrees"):
        audit_fills(src, lane_a_aggregate=agg)


def test_empty_and_missing_headers() -> None:
    with pytest.raises(ValueError, match="not a Lane A"):
        audit_fills(b"fake,a\n1,2\n")
    report, ledger = audit_fills(payload([]))
    assert report["venue_fills"] == 0
    assert report["measured_after_cost_maker_pnl"] is None
    assert ledger == []
