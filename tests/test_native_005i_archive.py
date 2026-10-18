"""Offline archive schema and price/size/clock regressions; fixtures are synthetic."""
from __future__ import annotations

import json
from collections import Counter
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from predictions_cup.replay.native_005i_archive import (
    PINNED,
    completed_reversal,
    native_rows,
    quote,
    replay,
)

T = datetime(2026, 10, 18, 16, 56, tzinfo=UTC)


def depth(*levels: tuple[str, str]) -> list[dict[str, str]]:
    return [{"price": p, "quantity": q} for p, q in levels]


def pm(token: str, *, rising: bool) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for i in range(6):
        mid = (Decimal("0.40") + Decimal(i) * Decimal("0.02") if rising
               else Decimal("0.60") - Decimal(i) * Decimal("0.02"))
        rows.append({
            "recorded_at": T - timedelta(minutes=5-i, seconds=1),
            "token_id": token,
            "bids": depth((str(mid - Decimal("0.01")), "40")),
            "asks": depth((str(mid + Decimal("0.01")), "40")),
        })
    return rows


def sig(exchange: str, at: datetime, seq: int,
        bids: list[dict[str, str]],
        asks: list[dict[str, str]]) -> dict[str, object]:
    return {
        "local_receive_at": at, "session_id": "fixture", "connection_epoch": 1,
        "raw_json": json.dumps({"books": [{
            "exchangeId": int(exchange),
            "asOf": {"at": (at - timedelta(milliseconds=50)).isoformat(),
                     "sequence": seq},
            "bids": bids, "asks": asks,
        }]}),
    }


def test_only_completed_six_minutes_signed_reversal_and_no_lookahead() -> None:
    rows = pm(PINNED["912"], rising=False) + pm(PINNED["913"], rising=True)
    counts: Counter[str] = Counter()
    signals = completed_reversal(rows, counts)
    assert [(s.exchange, s.side, s.end) for s in signals] == [
        ("912", "YES", T), ("913", "NO", T)]
    assert counts["n_signed_signal"] == 2
    for row in rows:
        stamp = row["recorded_at"]
        assert isinstance(stamp, datetime)
        row["recorded_at"] = stamp - timedelta(seconds=9)
    assert not completed_reversal(rows, Counter())


def test_original_native_books_reject_regression_crossed_and_future_timestamp() -> None:
    bid, ask = depth(("0.30", "2")), depth(("0.40", "2"))
    raw = [
        sig("912", T + timedelta(milliseconds=100), 5, bid, ask),
        sig("912", T + timedelta(milliseconds=200), 4, bid, ask),
        sig("912", T + timedelta(milliseconds=300), 6, depth(("0.50", "2")), ask),
    ]
    counts: Counter[str] = Counter()
    books = native_rows(raw, counts)
    assert len(books["912"]) == 1
    assert counts["sig_native_regression"] == 1
    assert counts["sig_invalid_native"] == 1


def test_whole_share_floor_no_complement_correctness_and_quote_not_fill() -> None:
    events = [
        sig("912", T + timedelta(milliseconds=100), 1,
            depth(("0.30", "30")),
            depth(("0.40", "1.8"), ("0.50", "4.8"))),
        sig("912", T + timedelta(minutes=5, milliseconds=200), 2,
            depth(("0.45", "1.8"), ("0.35", "4.8")),
            depth(("0.60", "30"))),
        sig("913", T + timedelta(milliseconds=100), 1,
            depth(("0.70", "1.8"), ("0.60", "4.8")),
            depth(("0.80", "30"))),
        sig("913", T + timedelta(minutes=5, milliseconds=200), 2,
            depth(("0.40", "30")),
            depth(("0.60", "1.8"), ("0.70", "4.8"))),
    ]
    books = native_rows(events, Counter())
    signals = completed_reversal(
        pm(PINNED["912"], rising=False) + pm(PINNED["913"], rising=True),
        Counter(),
    )
    assert quote(books["913"][0], "NO", 1, buy=True) == Decimal("0.30")
    assert quote(books["913"][1], "NO", 1, buy=False) == Decimal("0.40")
    assert quote(books["912"][0], "YES", 5, buy=True) == Decimal("0.48")
    assert quote(books["912"][0], "YES", 10, buy=True) is None
    result = replay(signals, books)
    assert result["counts"]["n_signal"] == 2
    assert result["counts"]["n_sig_entry"] == 2
    assert result["counts"]["n_sig_exit"] == 2
    assert result["counts"]["n_1_feasible"] == 2
    assert result["counts"]["n_5_feasible"] == 2
    assert result["counts"].get("n_10_feasible", 0) == 0
    assert result["sizes"]["5"]["ask_to_bid_proxy"]["worst"] == "-0.11"
    assert result["real_fill_count"] is None
    assert result["real_net_pnl"] is None
    assert result["source_admitted_pairs"] == 0
    assert result["time_shift_10m_placebo"]["1"]["n"] == 0
