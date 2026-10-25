#!/usr/bin/env python3
"""Read-only, low-overhead SIG production scorecard.

No network calls, no database modifications, no inferred strategy profits.
Run manually or at low cadence. All monetary values are SUSQies.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import statistics
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import quote


def connect_ro(path: Path) -> sqlite3.Connection:
    if not path.is_file():
        raise FileNotFoundError(str(path))
    uri = "file:" + quote(str(path.resolve()), safe="/") + "?mode=ro"
    db = sqlite3.connect(uri, uri=True, timeout=0.4)
    db.execute("PRAGMA query_only=ON")
    db.execute("PRAGMA busy_timeout=400")
    return db


def num(value: object) -> Decimal | None:
    if value is None:
        return None
    try:
        d = Decimal(str(value))
        return d if d.is_finite() else None
    except (InvalidOperation, ValueError):
        return None


def pretty(value: Decimal | None) -> str | None:
    return str(value) if value is not None else None


def latency_percentiles(values: list[float]) -> dict[str, object]:
    clean = sorted(x for x in values if 0 <= x <= 60000)
    if not clean:
        return {"sample_count": 0, "p50_ms": None, "p95_ms": None}
    def p(q: float) -> float:
        idx = (len(clean) - 1) * q
        low = int(idx)
        upper = min(low + 1, len(clean) - 1)
        return round(clean[low] + (clean[upper] - clean[low]) * (idx - low), 3)
    return {"sample_count": len(clean), "p50_ms": p(.5), "p95_ms": p(.95)}


def classify(op: str | None, family: str | None, strategy: str | None) -> str:
    words = " ".join((op or "", family or "", strategy or "")).lower()
    if "take" in words or "taker" in words:
        return "TAKE"
    if "make" in words or "maker" in words:
        return "MAKE"
    return "UNATTRIBUTED"


def score(risk_path: Path, journal_path: Path, max_age: float, event_limit: int) -> dict:
    now = datetime.now(timezone.utc).isoformat()
    with connect_ro(risk_path) as risk:
        row = risk.execute("SELECT payload_json FROM risk_state WHERE singleton=1").fetchone()
    if row is None:
        raise ValueError("RISK-002 singleton state missing")
    state = json.loads(row[0])
    age = (time.monotonic_ns() - int(state["account_observed_monotonic_ns"])) / 1e9
    trust = {
        "account_trusted": bool(state.get("account_trusted")),
        "marks_trusted": bool(state.get("marks_trusted")),
        "reconciliation_complete": bool(state.get("reconciliation_complete")),
        "account_age_s": round(age, 2),
        "fresh": 0 <= age <= max_age,
        "strategy_attribution_complete": bool(state.get("exposure", {}).get("strategy_attribution_complete")),
    }
    exposure = state.get("exposure", {})
    result = {
        "asof_utc": now,
        "scope": "RISK-002 state and BUILD-009 local journal (no new API calls)",
        "risk_source": str(risk_path),
        "journal_source": str(journal_path),
        "trust": trust,
        "equity_susq": state.get("current_equity"),
        "session_start_equity_susq": state.get("session_start_equity"),
        "net_external_cash_flow_susq": state.get("net_external_cash_flow"),
        "realised_pnl_susq": state.get("realised_pnl"),
        "unrealised_pnl_susq": state.get("unrealised_pnl"),
        "drawdown_susq": state.get("drawdown"),
        "peak_session_equity_susq": state.get("peak_session_equity"),
        "cash_susq": None,
        "trading_fees_susq": None,
        "gross_exposure_shares": exposure.get("gross_exposure"),
        "open_order_exposure_shares": exposure.get("open_order_exposure"),
        "uncertain_order_exposure_shares": exposure.get("uncertain_order_exposure"),
        "strategy_realised_pnl_susq": {"MAKE": None, "TAKE": None, "UNATTRIBUTED": None},
        "strategy_unrealised_pnl_susq": {"MAKE": None, "TAKE": None, "UNATTRIBUTED": None},
        "limitations": [
            "Cash and fees require a fresh authoritative account/transaction snapshot; not derived from equity or fill price.",
            "Maker/taker economic P&L requires fill-level basis, fees, position inventory and authoritative marks.",
            "Execution fill rows are deduplicated by the journal's identity table; unmatched external fills are unknown.",
            "Gross exposure is RISK-002 exposure accounting, not executable liquidation value.",
        ],
    }
    db = connect_ro(journal_path)
    try:
        fills = db.execute("""
            SELECT e.logical_operation_id, e.strategy_family, e.strategy_id,
                   e.exchange_id, e.quantity, e.price
            FROM execution_events AS e
            WHERE e.event_id IN (SELECT DISTINCT event_id FROM execution_fill_dedupe)
              AND e.event_type IN ('REALTIME_FILL','AUTHORITATIVE_FILL')
        """).fetchall()
        agg = defaultdict(lambda: {"unique_fill_events": 0, "quantity": Decimal(0),
                                   "price_times_quantity": Decimal(0), "unknown_price_events": 0})
        for op, family, strategy, exchange, quantity, price in fills:
            bucket = agg[classify(op, family, strategy)]
            q = num(quantity)
            if q is None:
                continue
            q = abs(q)
            bucket["unique_fill_events"] += 1
            bucket["quantity"] += q
            p = num(price)
            if p is not None:
                bucket["price_times_quantity"] += p*q
            else:
                bucket["unknown_price_events"] += 1
        result["fill_activity_by_strategy"] = {
            key: {"unique_fill_events": value["unique_fill_events"],
                  "volume_shares": str(value["quantity"]),
                  "gross_price_times_volume_susq": str(value["price_times_quantity"]),
                  "missing_price_events": value["unknown_price_events"]}
            for key, value in sorted(agg.items())
        }
        result["inventory_turnover"] = None
        # Recent event IDs avoid scanning an unbounded production journal.
        rows = db.execute("""
            SELECT logical_operation_id, logical_intent_id, event_type,
                   observed_monotonic_ns
            FROM execution_events ORDER BY event_id DESC LIMIT ?
        """, (event_limit,)).fetchall()
        counts = Counter()
        dispatched = {}
        cancel_dispatched = {}
        ack_latency = []
        cancel_latency = []
        for op, intent, event_type, observed in reversed(rows):
            counts[event_type] += 1
            key = (str(op), str(intent or ""))
            observed = int(observed)
            if event_type == "NETWORK_DISPATCH":
                dispatched[key] = observed
            elif event_type == "ACK":
                v = dispatched.get(key, dispatched.get((str(op), "")))
                if v is not None:
                    ack_latency.append((observed-v)/1e6)
            elif event_type == "CANCEL_SUBMITTED":
                cancel_dispatched[key] = observed
            elif event_type == "CANCEL_ACK":
                v = cancel_dispatched.get(key, cancel_dispatched.get((str(op), "")))
                if v is not None:
                    cancel_latency.append((observed-v)/1e6)
        result["execution_recent"] = {
            "window_events": len(rows),
            "counts": dict(sorted(counts.items())),
            "dispatch_to_ack": latency_percentiles(ack_latency),
            "cancel_submit_to_ack": latency_percentiles(cancel_latency),
        }
        result["lifecycle_states"] = dict(db.execute(
            "SELECT lifecycle_state, COUNT(*) FROM execution_envelopes GROUP BY lifecycle_state"
        ).fetchall())
        if not trust["fresh"] or not trust["account_trusted"] or not trust["reconciliation_complete"]:
            result["status"] = "STALE_OR_UNTRUSTED"
        else:
            result["status"] = "TRUSTED_AGGREGATES_ATTRIBUTION_PARTIAL"
        return result
    finally:
        db.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--risk", type=Path, default=Path("data/risk_002.sqlite3"))
    parser.add_argument("--journal", type=Path, default=Path("data/execution_journal.sqlite3"))
    parser.add_argument("--max-account-age-s", type=float, default=30)
    parser.add_argument("--recent-events", type=int, default=4000)
    args = parser.parse_args()
    if not 0 < args.recent_events <= 20000 or not 0 < args.max_account_age_s <= 3600:
        parser.error("invalid bounded scan or freshness window")
    try:
        print(json.dumps(score(args.risk, args.journal, args.max_account_age_s,
                               args.recent_events), indent=2, sort_keys=True))
    except (OSError, sqlite3.Error, KeyError, ValueError, TypeError) as exc:
        print(json.dumps({"status": "UNAVAILABLE", "error": str(exc)}), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
