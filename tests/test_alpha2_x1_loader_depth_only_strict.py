"""Invented X1 consumer regression: depth-only rows must invalidate old liquidity.

Review-only test against frozen Red Team composite; intentionally RED until the
offline loader and the depth-sensitive lead/lag consumer agree on event updates.
No original venue data, trading, connectivity, or production changes.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from predictions_cup.analysis import live_diag

START = datetime(2026, 11, 4, 1, tzinfo=UTC)
MARKET = "invented-sig-depth-only"


def _row(
    kind: str,
    at: datetime,
    bid: float,
    ask: float,
    *,
    bid_size: int | None = None,
    ask_size: int | None = None,
) -> dict[str, object]:
    payload = None
    if kind == "DEPTH_SNAPSHOT":
        payload = json.dumps(
            {
                "bids": [] if not bid_size else [{"quantity": bid_size}],
                "asks": [] if not ask_size else [{"quantity": ask_size}],
            }
        )
    return {
        "exchange_id": MARKET,
        "observed_at": at,
        "best_bid": bid,
        "best_ask": ask,
        "book_valid": True,
        "trust_state": "TRUSTED",
        "event_type": kind,
        "payload_json": payload,
    }


@pytest.mark.parametrize("direction", ["BUY_YES", "BUY_NO"])
@pytest.mark.parametrize("companion_bbo", [False, True])
def test_depth_only_zero_update_must_not_leave_a_fictitious_arrival_fill(
    tmp_path: Path,
    direction: str,
    companion_bbo: bool,
) -> None:
    """Both signed sides: a 50->0 depth frame must take effect without BBO echo."""
    depleted = START + timedelta(seconds=1, milliseconds=200)
    response = START + timedelta(seconds=3)
    rows = [
        _row("DEPTH_SNAPSHOT", START, 0.40, 0.50, bid_size=50, ask_size=50),
        _row("BBO_SNAPSHOT", START, 0.40, 0.50),
        _row("DEPTH_SNAPSHOT", depleted, 0.40, 0.50, bid_size=0, ask_size=0),
    ]
    if companion_bbo:
        rows.append(_row("BBO_SNAPSHOT", depleted, 0.40, 0.50))
    rows.append(
        _row(
            "BBO_SNAPSHOT",
            response,
            0.45 if direction == "BUY_YES" else 0.35,
            0.55 if direction == "BUY_YES" else 0.45,
        )
    )
    location = tmp_path / "sig"
    location.mkdir()
    pq.write_table(pa.Table.from_pylist(rows), location / "invented.parquet")
    tape = live_diag._load_quotes(
        tmp_path,
        stream="sig",
        identity_column="exchange_id",
        identities={MARKET},
        sig=True,
    )[MARKET]
    external_at_impulse = (
        live_diag.Quote(
            START + timedelta(seconds=1),
            0.68 if direction == "BUY_YES" else 0.18,
            0.72 if direction == "BUY_YES" else 0.22,
        )
    )
    lead_lag = live_diag.analyze_lead_lag(
        sig_quotes=tape,
        external_quotes=(
            live_diag.Quote(START, 0.48, 0.52),
            external_at_impulse,
        ),
        latency_ms=500,
    )
    assert len(lead_lag) == 1
    # X1's direct-Quote tests pass, but the parquet loader drops independent
    # DEPTH_SNAPSHOT changes unless another BBO happens to follow them.
    # Thus the (companion_bbo=False) cases must FAIL against the frozen source.
    assert lead_lag[0].status is live_diag.ResearchStatus.INSUFFICIENT_DEPTH
    assert lead_lag[0].available_depth in (None, 0)
