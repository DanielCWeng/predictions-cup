"""Read-only deterministic SIG venue/journal fill identity and scope audit."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sqlite3
from collections import Counter, defaultdict
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from predictions_cup.research.inventory_portfolio import dec, timestamp


def journal_events(path: Path, *, tournament_id: str) -> list[dict[str, Any]]:
    """Read SQLite and currently attached WAL without modifying either."""
    with sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        rows = db.execute(
            "SELECT event_id,event_type,tournament_id,exchange_id,exchange_order_id,"
            "fill_id,quantity,price,source_timestamp,strategy_family,strategy_id "
            "FROM execution_events WHERE tournament_id=? ORDER BY event_id",
            (tournament_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def economics_match(fill: dict[str, Any], event: dict[str, Any]) -> bool:
    return (
        str(fill["order_id"]) == str(event["exchange_order_id"])
        and str(fill["exchange_id"]) == str(event["exchange_id"])
        and event["quantity"] is not None
        and event["price"] is not None
        and dec(fill["quantity"]) == dec(event["quantity"])
        and abs(dec(fill["price"]) - dec(event["price"])) <= Decimal(".000001")
    )


def source_delta_ms(fill: dict[str, Any], event: dict[str, Any]) -> float | None:
    if not event["source_timestamp"]:
        return None
    return (
        abs((timestamp(fill["filled_at"]) - timestamp(event["source_timestamp"])).total_seconds())
        * 1000
    )


def match_fills(
    venue: list[dict[str, Any]],
    journal: list[dict[str, Any]],
    *,
    tolerance_ms: float = 2000,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if tolerance_ms <= 0 or len({str(x["id"]) for x in venue}) != len(venue):
        raise ValueError("invalid tolerance or duplicate venue fill ID")
    by_id: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_order: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in journal:
        if row["fill_id"] is not None:
            by_id[str(row["fill_id"])].append(row)
        if row["exchange_order_id"] is not None:
            by_order[str(row["exchange_order_id"])].append(row)
    seen_realtime: set[int] = set()
    records = []
    for fill in sorted(venue, key=lambda x: (x["filled_at"], int(x["id"]))):
        order_events = [
            e
            for e in by_order.get(str(fill["order_id"]), [])
            if str(e["exchange_id"]) == str(fill["exchange_id"])
        ]
        direct = by_id.get(str(fill["id"]), [])
        chosen = None
        status = "UNRESOLVED"
        confidence = "UNRESOLVED"
        candidates = 0
        if direct:
            matches = [e for e in direct if economics_match(fill, e)]
            unique_identity = {
                (
                    e["exchange_order_id"],
                    e["exchange_id"],
                    e["fill_id"],
                    e["quantity"],
                    e["price"],
                    e["source_timestamp"],
                )
                for e in direct
            }
            if (
                len(matches) == len(direct)
                and len(unique_identity) == 1
                and all(e["event_type"] == "AUTHORITATIVE_FILL" for e in direct)
            ):
                chosen = matches[0]
                status = (
                    "DUPLICATE_AUTHORITATIVE_ID_SAME_ECONOMICS"
                    if len(matches) > 1
                    else "EXACT_AUTHORITATIVE_FILL_ID"
                )
                confidence = "FILL_CONFIRMED"
            else:
                status = "FILL_ID_CONFLICT"
        else:
            matches = [
                e
                for e in order_events
                if e["event_type"] == "REALTIME_FILL"
                and e["event_id"] not in seen_realtime
                and economics_match(fill, e)
                and source_delta_ms(fill, e) is not None
                and source_delta_ms(fill, e) <= tolerance_ms
            ]
            candidates = len(matches)
            if len(matches) == 1:
                chosen = matches[0]
                seen_realtime.add(int(chosen["event_id"]))
                status = "UNIQUE_REALTIME_ECONOMIC_TUPLE"
                confidence = "FILL_CONFIRMED_NO_SHARED_ID"
            elif len(matches) > 1:
                status = "AMBIGUOUS_REALTIME_TUPLE"
            else:
                summary = [e for e in order_events if e["event_type"] == "FILL_SUMMARY"]
                if summary:
                    chosen = summary[0]
                    status = "ORDER_HAS_AGGREGATE_SUMMARY"
                    confidence = "ORDER_ONLY"
                elif order_events:
                    chosen = order_events[0]
                    status = "ORDER_EVENT_WITHOUT_FILL_MATCH"
                    confidence = "ORDER_ONLY"
                elif by_order.get(str(fill["order_id"])) and all(
                    e["exchange_id"] is None for e in by_order[str(fill["order_id"])]
                ):
                    chosen = by_order[str(fill["order_id"])][0]
                    status = "ORDER_ID_ONLY_NULL_EXCHANGE"
                    confidence = "ORDER_ONLY"
                else:
                    status = "NO_JOURNAL_ORDER_REFERENCE"
                    confidence = "UNREFERENCED"
        records.append(
            {
                "venue_fill_id": str(fill["id"]),
                "venue_order_id": str(fill["order_id"]),
                "exchange_id": str(fill["exchange_id"]),
                "market_id": str(fill["market_id"]),
                "filled_at": str(fill["filled_at"]),
                "side": str(fill["side"]),
                "signed_quantity": str(fill["quantity"]),
                "price": str(fill["price"]),
                "status": status,
                "confidence": confidence,
                "journal_event_id": chosen["event_id"] if chosen else None,
                "journal_event_type": chosen["event_type"] if chosen else None,
                "journal_fill_id": chosen["fill_id"] if chosen else None,
                "journal_order_id": chosen["exchange_order_id"] if chosen else None,
                "journal_exchange_id": chosen["exchange_id"] if chosen else None,
                "journal_quantity": chosen["quantity"] if chosen else None,
                "journal_price": chosen["price"] if chosen else None,
                "journal_timestamp": chosen["source_timestamp"] if chosen else None,
                "journal_tournament_id": chosen["tournament_id"] if chosen else None,
                "journal_strategy_family": chosen["strategy_family"] if chosen else None,
                "journal_strategy_id": chosen["strategy_id"] if chosen else None,
                "time_delta_ms": source_delta_ms(fill, chosen) if chosen else None,
                "ambiguous_realtime_candidates": candidates if candidates > 1 else 0,
                "other_journal_event_types": "|".join(
                    sorted({e["event_type"] for e in order_events})
                ),
            }
        )
    statuses = Counter(x["status"] for x in records)
    by_day = {
        day: dict(Counter(x["status"] for x in records if x["filled_at"].startswith(day)))
        for day in sorted({x["filled_at"][:10] for x in records})
    }
    summary = {
        "venue_fill_count": len(venue),
        "unique_venue_order_count": len({str(x["order_id"]) for x in venue}),
        "journal_event_count": len(journal),
        "journal_events_by_type": dict(Counter(x["event_type"] for x in journal)),
        "status_counts": dict(statuses),
        "confidence_counts": dict(Counter(x["confidence"] for x in records)),
        "counts_by_day": by_day,
        "confirmed_fill_count": sum(x["confidence"].startswith("FILL_CONFIRMED") for x in records),
        "order_only_count": sum(x["confidence"] == "ORDER_ONLY" for x in records),
        "unreferenced_order_count": statuses["NO_JOURNAL_ORDER_REFERENCE"],
        "not_confirmed_individual_fill_count": sum(
            not x["confidence"].startswith("FILL_CONFIRMED") for x in records
        ),
        "interpretation": (
            "Exact authoritative IDs and unique timestamp-matched economic tuples "
            "are fill-level evidence. Summary or order link does not prove every fill. "
            "Absence of an order reference may be older/manual scope or journal gap, "
            "not necessarily a lost trade. No SQLite/WAL writes performed."
        ),
    }
    return records, summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--journal", type=Path, required=True)
    parser.add_argument("--tournament-id", type=str, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--max-delta-ms", type=float, default=2000)
    args = parser.parse_args()
    venue = json.loads(args.snapshot.read_text())["fills"]
    rows, result = match_fills(
        venue,
        journal_events(args.journal, tournament_id=args.tournament_id),
        tolerance_ms=args.max_delta_ms,
    )
    args.out.mkdir(mode=0o700, parents=True, exist_ok=True)
    args.out.chmod(0o700)
    with (args.out / "fill_provenance.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    result["snapshot_sha256"] = hashlib.sha256(args.snapshot.read_bytes()).hexdigest()
    result["completed_at"] = datetime.now(UTC).isoformat()
    (args.out / "fill_aggregate.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n"
    )
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
