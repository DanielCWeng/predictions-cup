from datetime import UTC, datetime, timedelta

from predictions_cup.learning.flow_response import NS, reconstruct_genuine_bbo
from predictions_cup.learning.liquidity_response import (
    depth_response,
    quote_response,
    reconstruct_depth_series,
)

BASE = datetime(2026, 5, 1, tzinfo=UTC)


def _row(second: int, bid: str, ask: str) -> dict[str, object]:
    return {
        "observed_at": BASE + timedelta(seconds=second),
        "best_bid": bid,
        "best_ask": ask,
    }


def test_quote_response_counts_genuine_not_repeated_archive_rows() -> None:
    series = reconstruct_genuine_bbo(
        [
            _row(0, "0.40", "0.42"),
            _row(5, "0.40", "0.42"),
            _row(10, "0.41", "0.43"),
            _row(15, "0.41", "0.43"),
            _row(20, "0.41", "0.43"),
        ]
    )
    start = int(BASE.timestamp() * NS)
    collector = series.times_ns.copy()
    response = quote_response(
        series,
        collector,
        decision_ns=start,
        horizon_seconds=15,
    )
    assert response.available
    assert response.genuine_change_count == 1
    assert response.raw_record_count == 3
    assert response.repeated_unchanged_count == 2
    assert response.genuine_renewal
    assert response.motion == "TRANSLATION_UP"


def test_quote_response_fails_closed_on_capture_gap() -> None:
    series = reconstruct_genuine_bbo(
        [_row(0, "0.40", "0.42"), _row(40, "0.41", "0.43")]
    )
    start = int(BASE.timestamp() * NS)
    collector = series.times_ns.copy()
    response = quote_response(
        series,
        collector,
        decision_ns=start,
        horizon_seconds=10,
    )
    assert not response.available
    assert response.motion == "UNAVAILABLE"


def _depth_row(second: int, bid_sizes: list[str], ask_sizes: list[str]) -> dict[str, object]:
    return {
        "recorded_at": BASE + timedelta(seconds=second),
        "bids": [{"price": "0.4", "size": size} for size in bid_sizes],
        "asks": [{"price": "0.6", "size": size} for size in ask_sizes],
    }


def test_depth_response_keeps_snapshot_age_and_cadence_visible() -> None:
    series = reconstruct_depth_series(
        [
            _depth_row(0, ["10", "5"], ["7"]),
            _depth_row(10, ["12", "5"], ["6"]),
            _depth_row(20, ["8"], ["9", "2"]),
        ]
    )
    start = int(BASE.timestamp() * NS)
    response = depth_response(series, decision_ns=start + 10 * NS, horizon_seconds=10)
    assert response["available"] is True
    assert response["total_depth_change"] == -4.0
    assert response["bid_depth_change"] == -9.0
    assert response["ask_depth_change"] == 5.0
    assert response["snapshots_in_interval"] == 1
    assert response["current_snapshot_age_seconds"] == 0.0
    assert response["future_snapshot_age_seconds"] == 0.0


def test_conflicting_same_timestamp_depth_snapshots_are_dropped() -> None:
    series = reconstruct_depth_series(
        [
            _depth_row(0, ["10"], ["10"]),
            _depth_row(0, ["11"], ["10"]),
            _depth_row(5, ["12"], ["12"]),
        ]
    )
    assert series.times_ns.tolist() == [
        int((BASE + timedelta(seconds=5)).timestamp() * NS)
    ]