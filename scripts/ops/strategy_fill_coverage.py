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


def _canonical_family(value: str | None) -> str | None:
    # Execution envelope / SUBMISSION uses FV-TAKE while realtime fills use
    # TAKE. They are the same strategy family, not a provenance conflict.
    if value in {"TAKE", "FV-TAKE"}:
        return "TAKE"
    if value == "MAKE":
        return "MAKE"
    return None


def fill_coverage(db_path: Path, tournament_id: str) -> dict[str, object]:
    if not db_path.is_file() or not tournament_id.strip():
        raise ValueError("existing journal and tournament identity are required")
    db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        db.execute("PRAGMA busy_timeout=5000")
        # Read only the small fill cohort first. Joining all 190k+ maker
        # submissions to every ACK was O(large journal) and took 36s on
        # West, competing with the live trader. Both lookups below are
        # restricted to the filled order/operation identities and chunked.
        fill_rows = db.execute(
            """
            SELECT exchange_order_id, exchange_id, fill_id, source_timestamp,
                   quantity, price, strategy_family
            FROM execution_events
            WHERE tournament_id=?
              AND event_type IN ('REALTIME_FILL','AUTHORITATIVE_FILL')
            ORDER BY event_id
            """,
            (tournament_id,),
        ).fetchall()

        def batches(values: set[str], size: int = 200) -> list[tuple[str, ...]]:
            sorted_values = sorted(values)
            return [
                tuple(sorted_values[i:i + size])
                for i in range(0, len(sorted_values), size)
            ]

        ack_rows: list[tuple[str, str, str | None]] = []
        filled_order_ids = {str(row[0]) for row in fill_rows if row[0] is not None}
        for chunk in batches(filled_order_ids):
            marks = ",".join("?" for _ in chunk)
            ack_rows.extend(
                (str(oid), str(operation), None if intent is None else str(intent))
                for oid, operation, intent in db.execute(
                    "SELECT exchange_order_id, logical_operation_id, "
                    "logical_intent_id FROM execution_events "
                    f"WHERE event_type='ACK' AND tournament_id=? "
                    f"AND exchange_order_id IN ({marks})",
                    (tournament_id, *chunk),
                )
            )

        submissions: dict[tuple[str, str | None], str | None] = {}
        ambiguous_submissions: set[tuple[str, str | None]] = set()
        operations = {op for _oid, op, _intent in ack_rows}
        for chunk in batches(operations):
            marks = ",".join("?" for _ in chunk)
            for op, intent, family in db.execute(
                "SELECT logical_operation_id,logical_intent_id,strategy_family "
                "FROM execution_events "
                f"WHERE event_type='SUBMISSION' AND tournament_id=? "
                f"AND logical_operation_id IN ({marks})",
                (tournament_id, *chunk),
            ):
                key = (str(op), None if intent is None else str(intent))
                value = _canonical_family(None if family is None else str(family))
                if key in submissions and submissions[key] != value:
                    ambiguous_submissions.add(key)
                    submissions[key] = None
                elif key not in ambiguous_submissions:
                    submissions[key] = value

        order_families: dict[str, str | None] = {}
        ambiguous_orders: set[str] = set()
        for oid, op, intent in ack_rows:
            value = submissions.get((op, intent))
            if oid in order_families and order_families[oid] != value:
                ambiguous_orders.add(oid)
                order_families[oid] = None
            elif oid not in ambiguous_orders:
                order_families[oid] = value

        groups: dict[str, _Group] = defaultdict(_Group)
        seen: set[tuple[object, ...]] = set()
        duplicates = 0
        fallback_identities = 0
        missing_economics = 0
        conflicted = 0
        for oid, exchange, fill_id, stamp, qty_text, price_text, family in fill_rows:
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
            journal_family = _canonical_family(None if family is None else str(family))
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
