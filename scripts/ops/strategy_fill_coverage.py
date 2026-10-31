"""Read-only fill strategy coverage, NOT a realised P&L estimator.

Issuer cash/position basis, fees and netting are not derivable from fill
prices alone. Never describe execution notional or estimated edge as profit.
No network calls, journal writes, or historical data collection.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path


@dataclass
class _Group:
    fills: int = 0
    shares: Decimal = Decimal("0")
    notional: Decimal = Decimal("0")


def fill_coverage(db_path: Path, tournament_id: str) -> dict[str, object]:
    if not db_path.is_file() or not tournament_id.strip():
        raise ValueError("existing journal and tournament identity are required")
    db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        db.execute("PRAGMA busy_timeout=5000")
        # ACK->SUBMISSION attribution is independent of the fill event's
        # optional strategy fields. Reject ambiguous/contradictory mappings.
        order_families: dict[str, str | None] = {}
        for oid, family in db.execute(
            """
            SELECT ack.exchange_order_id, submission.strategy_family
            FROM execution_events AS ack
            JOIN execution_events AS submission
              ON submission.logical_operation_id=ack.logical_operation_id
             AND submission.logical_intent_id=ack.logical_intent_id
             AND submission.event_type='SUBMISSION'
            WHERE ack.event_type='ACK'
              AND ack.exchange_order_id IS NOT NULL
              AND ack.tournament_id=?
            """,
            (tournament_id,),
        ):
            order_key = str(oid)
            value = None if family is None else str(family)
            if order_key in order_families and order_families[order_key] != value:
                order_families[order_key] = None
            else:
                order_families[order_key] = value

        groups: dict[str, _Group] = defaultdict(_Group)
        seen: set[tuple[object, ...]] = set()
        duplicates = 0
        fallback_identities = 0
        missing_economics = 0
        conflicted = 0
        for (
            oid, exchange, fill_id, stamp, qty_text, price_text, family
        ) in db.execute(
            """
            SELECT exchange_order_id, exchange_id, fill_id, source_timestamp,
                   quantity, price, strategy_family
            FROM execution_events
            WHERE tournament_id=?
              AND event_type IN ('REALTIME_FILL','AUTHORITATIVE_FILL')
            ORDER BY event_id
            """,
            (tournament_id,),
        ):
            fill_key: tuple[object, ...]
            if fill_id is not None:
                fill_key = ("fill", str(exchange), str(fill_id))
            else:
                fallback_identities += 1
                fill_key = ("fallback", str(oid), str(exchange), str(stamp),
                            str(qty_text), str(price_text))
            if fill_key in seen:
                duplicates += 1
                continue
            seen.add(fill_key)
            journal_family = None if family is None else str(family)
            ack_family = order_families.get(str(oid))
            if (
                journal_family is not None
                and ack_family is not None
                and journal_family != ack_family
            ):
                conflicted += 1
                group = "UNATTRIBUTED"
            else:
                candidate = journal_family or ack_family
                group = candidate if candidate in {"MAKE", "TAKE"} else "UNATTRIBUTED"
            row = groups[group]
            row.fills += 1
            try:
                qty, price = abs(Decimal(str(qty_text))), Decimal(str(price_text))
                if not qty.is_finite() or not price.is_finite() or qty <= 0 or not 0 <= price <= 1:
                    raise InvalidOperation
                row.shares += qty
                row.notional += qty * price
            except (InvalidOperation, ValueError, TypeError):
                missing_economics += 1
        result = {}
        for family in ("MAKE", "TAKE", "UNATTRIBUTED"):
            row = groups[family]
            result[family] = {
                "unique_fill_events": row.fills,
                "quantity_shares": str(row.shares),
                "gross_fill_notional": str(row.notional),
                "realised_pnl": None,
            }
        total = len(seen)
        known = groups["MAKE"].fills + groups["TAKE"].fills
        return {
            "source": str(db_path),
            "tournament_id": tournament_id,
            "status": "EXECUTION_COVERAGE_ONLY_NOT_REALISED_PNL",
            "fill_events": total,
            "attributable_fill_events": known,
            "attribution_coverage_fraction": (
                round(known / total, 6) if total else None
            ),
            "duplicate_rows_dropped": duplicates,
            "fallback_identity_rows": fallback_identities,
            "conflicting_order_attributions": conflicted,
            "missing_price_or_quantity": missing_economics,
            "by_strategy_family": result,
            "limitations": [
                "Price multiplied by volume is execution notional, not profit.",
                "Exact fill IDs and strategy metadata are incomplete for some events.",
                "Maker/taker realised P&L requires authoritative cash flows, fees, "
                "lot basis, YES/NO netting and reconciled inventory marks.",
            ],
        }
    finally:
        db.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--journal", type=Path, default=Path("data/execution_journal.sqlite3"))
    parser.add_argument("--tournament", required=True)
    options = parser.parse_args()
    print(json.dumps(fill_coverage(options.journal, options.tournament), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
