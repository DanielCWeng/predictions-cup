"""Role-separated post-fill SIG executable markout audit (read-only).

Only REALTIME_FILL events are used: FILL_SUMMARY is a duplicate and an
acknowledged NO-buy may be represented as a YES sell at the complementary price.
A markout is NOT realised P&L; the position may still be open at the horizon.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from statistics import median


def _time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def run(journal: Path, tape: Path, *, max_fills: int = 400) -> dict:
    event_db = sqlite3.connect(f"file:{journal}?mode=ro", uri=True, timeout=2)
    tape_db = sqlite3.connect(f"file:{tape}?mode=ro", uri=True, timeout=2)
    submission = {}
    for op, family, detail in event_db.execute(
        """SELECT logical_operation_id, strategy_family, detail_json
           FROM execution_events
           WHERE event_type='SUBMISSION' AND strategy_family IN ('MAKE','FV-TAKE')"""
    ):
        if not detail:
            continue
        data = json.loads(detail)
        side, action = data.get("outcome_side"), data.get("action")
        if action == "buy" and side == "yes":
            direction = "BUY"
        elif (action == "sell" and side == "yes") or (action == "buy" and side == "no"):
            direction = "SELL"
        else:
            continue
        submission[op] = (family, direction)
    fills = event_db.execute(
        """SELECT logical_operation_id,strategy_family,exchange_id,quantity,price,
                  source_timestamp,detail_json
           FROM execution_events WHERE event_type='REALTIME_FILL'
           ORDER BY event_id DESC LIMIT ?""",
        (max_fills,),
    ).fetchall()
    rows = defaultdict(list)
    excluded = defaultdict(int)
    for op, role, exch, qty, px, stamp, _detail in fills:
        provenance = submission.get(op)
        if provenance is None:
            excluded["missing_submission_or_side"] += 1
            continue
        family, direction = provenance
        if (family == "FV-TAKE" and role not in ("TAKE", "FV-TAKE")) or (
            family == "MAKE" and role != "MAKE"
        ):
            excluded["role_conflict"] += 1
            continue
        try:
            timestamp, quantity, fill_price = _time(stamp), float(qty), float(px)
        except (ValueError, TypeError):
            excluded["missing_time_quantity_price"] += 1
            continue
        if quantity <= 0 or not 0 <= fill_price <= 1:
            excluded["invalid_fill"] += 1
            continue
        marks = {}
        for seconds in (30, 300):
            target = timestamp + timedelta(seconds=seconds)
            record = tape_db.execute(
                """SELECT best_bid,best_ask,rest_observed_at
                   FROM price_observations
                   WHERE exchange_id=? AND rest_observed_at>=?
                   ORDER BY rest_observed_at LIMIT 1""",
                (exch, target.isoformat()),
            ).fetchone()
            if record is None or record[0] is None or record[1] is None:
                marks[seconds] = None
                continue
            mark_time = _time(record[2])
            if not timedelta(0) <= mark_time - target <= timedelta(seconds=15):
                marks[seconds] = None
                continue
            exit_price = float(record[0] if direction == "BUY" else record[1])
            marks[seconds] = (
                exit_price - fill_price if direction == "BUY" else fill_price - exit_price
            )
        rows[family].append(
            {"quantity": quantity, "price": fill_price, "direction": direction, "markouts": marks}
        )
    out = {"source_fills": len(fills), "excluded": dict(excluded), "roles": {}}
    for family, entries in sorted(rows.items()):
        metrics = {
            "fill_events": len(entries),
            "filled_shares": sum(x["quantity"] for x in entries),
            "yes_buy_shares": sum(x["quantity"] for x in entries if x["direction"] == "BUY"),
            "yes_sell_shares": sum(x["quantity"] for x in entries if x["direction"] == "SELL"),
        }
        for sec in (30, 300):
            valid = [
                (x["quantity"], x["markouts"][sec])
                for x in entries
                if x["markouts"][sec] is not None
            ]
            vol = sum(q for q, _ in valid)
            metrics[f"{sec}s_covered_events"] = len(valid)
            metrics[f"{sec}s_covered_shares"] = vol
            metrics[f"{sec}s_qty_weighted_markout_per_share"] = (
                sum(q * m for q, m in valid) / vol if vol else None
            )
            metrics[f"{sec}s_markout_total"] = sum(q * m for q, m in valid)
            metrics[f"{sec}s_median_event_markout_per_share"] = (
                median(m for _, m in valid) if valid else None
            )
        out["roles"][family] = metrics
    return out


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--journal", type=Path, required=True)
    parser.add_argument("--tape", type=Path, required=True)
    parser.add_argument("--max-fills", type=int, default=400)
    args = parser.parse_args()
    print(json.dumps(run(args.journal, args.tape, max_fills=args.max_fills), indent=2))
