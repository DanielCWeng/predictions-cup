"""Research-only consumer falsification of frozen X1/X2 composite.

Invented timestamps, prices, quantities and issuer identities only.
These are intentionally strict FAIL-CLOSED regression wishes: failures mean
source/consumer safety has NOT been demonstrated, not a request to relax tests.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from predictions_cup.analysis import live_diag

START = datetime(2026, 11, 3, 1, tzinfo=UTC)


def _external() -> tuple[live_diag.Quote, ...]:
    return (
        live_diag.Quote(START, 0.48, 0.52),
        live_diag.Quote(START + timedelta(seconds=1), 0.68, 0.72),
    )


def test_untrusted_same_price_zero_depth_must_invalidate_previous_liquidity() -> None:
    """Do not reuse 50 shares after a received untrusted cancellation."""
    impulse = START + timedelta(seconds=1)
    quotes = (
        live_diag.Quote(
            START, 0.40, 0.50, bid_depth=50, ask_depth=50,
            depth_observed_at=START,
        ),
        live_diag.Quote(
            impulse + timedelta(milliseconds=200), 0.40, 0.50,
            bid_depth=0, ask_depth=0,
            depth_observed_at=impulse + timedelta(milliseconds=200),
            trusted=False,
        ),
        live_diag.Quote(
            impulse + timedelta(seconds=2), 0.45, 0.55,
            bid_depth=0, ask_depth=0,
            depth_observed_at=impulse + timedelta(seconds=2),
        ),
    )
    rows = live_diag.analyze_lead_lag(
        sig_quotes=quotes,
        external_quotes=_external(),
        latency_ms=500,
        max_depth_age_seconds=60,
    )
    assert len(rows) == 1
    # Current _economic skips the invalidating quote but keeps the older book.
    assert rows[0].status is not live_diag.ResearchStatus.MONETIZABLE_CANDIDATE
    assert rows[0].available_depth in (None, 0)


def test_depth_only_zero_snapshot_must_invalidate_asof_without_new_bbo(
    tmp_path: Path,
) -> None:
    """A DEPTH_SNAPSHOT with no BBO_SNAPSHOT must revoke old 50-share depth."""
    impulse = START + timedelta(seconds=1)
    zero_at = impulse + timedelta(milliseconds=200)

    def row(
        at: datetime,
        kind: str,
        bid: float,
        ask: float,
        quantity: int | None = None,
    ) -> dict[str, object]:
        payload = (
            None
            if quantity is None
            else json.dumps({
                "bids": [{"quantity": quantity}],
                "asks": [{"quantity": quantity}],
            })
        )
        return {
            "exchange_id": "INVENTED_SIG",
            "observed_at": at,
            "best_bid": bid,
            "best_ask": ask,
            "event_type": kind,
            "payload_json": payload,
            "trust_state": "TRUSTED",
            "book_valid": True,
        }

    rows = [
        row(START, "DEPTH_SNAPSHOT", 0.40, 0.50, 50),
        row(START, "BBO_SNAPSHOT", 0.40, 0.50),
        row(zero_at, "DEPTH_SNAPSHOT", 0.40, 0.50, 0),
        row(impulse + timedelta(seconds=2), "BBO_SNAPSHOT", 0.45, 0.55),
    ]
    path = tmp_path / "normalized_events" / "invented.parquet"
    path.parent.mkdir(parents=True)
    pq.write_table(pa.Table.from_pylist(rows), path)

    loaded = live_diag._load_quotes(
        tmp_path,
        stream="normalized_events",
        identity_column="exchange_id",
        identities={"INVENTED_SIG"},
        sig=True,
    )
    assert "INVENTED_SIG" in loaded
    replay = live_diag.analyze_lead_lag(
        sig_quotes=loaded["INVENTED_SIG"],
        external_quotes=_external(),
        latency_ms=500,
        max_depth_age_seconds=60,
    )
    assert len(replay) == 1
    # Current _load_quotes only emits BBO rows; zero-depth-only is lost.
    assert replay[0].status is not live_diag.ResearchStatus.MONETIZABLE_CANDIDATE
    assert replay[0].available_depth in (None, 0)
