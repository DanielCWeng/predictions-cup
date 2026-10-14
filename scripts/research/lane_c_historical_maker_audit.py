"""Offline, fail-closed provenance triage of Lane A's immutable venue-fill CSV.

This does NOT measure market-making profitability: the input has venue fills but
not a complete maker order/no-fill denominator, source-timed quotes, matched
execution fees or settled inventory lifecycles. Never connect to SIG here.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
from collections import Counter
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

FILL_CONFIDENCE = ("FILL_CONFIRMED", "FILL_CONFIRMED_NO_SHARED_ID")
STATUS_CONFIDENCE = {
    "EXACT_AUTHORITATIVE_FILL_ID": "FILL_CONFIRMED",
    "DUPLICATE_AUTHORITATIVE_ID_SAME_ECONOMICS": "FILL_CONFIRMED",
    "UNIQUE_REALTIME_ECONOMIC_TUPLE": "FILL_CONFIRMED_NO_SHARED_ID",
    "ORDER_HAS_AGGREGATE_SUMMARY": "ORDER_ONLY",
    "ORDER_EVENT_WITHOUT_FILL_MATCH": "ORDER_ONLY",
    "ORDER_ID_ONLY_NULL_EXCHANGE": "ORDER_ONLY",
    "AMBIGUOUS_REALTIME_TUPLE": "UNRESOLVED",
    "FILL_ID_CONFLICT": "UNRESOLVED",
    "NO_JOURNAL_ORDER_REFERENCE": "UNREFERENCED",
}
MARKOUT_HORIZONS = ("5s", "30s", "5m", "1h")


def _datetime(value: str) -> datetime:
    at = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if at.tzinfo is None or at.utcoffset() is None:
        raise ValueError("source timestamp must include UTC offset")
    return at.astimezone(UTC)


def _decimal(value: str) -> Decimal:
    try:
        number = Decimal(value)
    except (ValueError, InvalidOperation) as exc:
        raise ValueError("malformed decimal") from exc
    if not number.is_finite():
        raise ValueError("nonfinite venue input")
    return number


def audit_fills(
    csv_payload: bytes,
    *,
    lane_a_aggregate: dict[str, Any] | None = None,
    expected_sha256: str | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Check source-consistency and preserve every fill with evidence-specific NULLs.

    A CSV and SHA on their own do not prove they came from the venue. Lane A's
    separately authenticated snapshot/SQL matching is a prerequisite.
    """
    digest = hashlib.sha256(csv_payload).hexdigest()
    if expected_sha256 is not None and digest != expected_sha256:
        raise ValueError("source CSV SHA256 mismatch")
    reader = csv.DictReader(csv_payload.decode("utf-8-sig").splitlines())
    required = {
        "venue_fill_id", "venue_order_id", "exchange_id", "market_id",
        "filled_at", "side", "signed_quantity", "price", "status",
        "confidence", "journal_event_id", "journal_event_type",
        "journal_fill_id", "journal_order_id", "journal_exchange_id",
        "journal_quantity", "journal_price", "journal_timestamp",
        "journal_strategy_family",
    }
    if reader.fieldnames is None or not required <= set(reader.fieldnames):
        raise ValueError("not a Lane A fill_provenance.csv")
    rows = list(reader)
    ids: set[str] = set()
    statuses: Counter[str] = Counter()
    confidence: Counter[str] = Counter()
    roles: Counter[str] = Counter()
    confirmed_sides: Counter[str] = Counter()
    days: Counter[str] = Counter()
    orders: set[str] = set()
    audit_ledger: list[dict[str, Any]] = []
    timestamps: list[datetime] = []
    with_source_timestamp = 0
    for row in rows:
        fill_id = row["venue_fill_id"].strip()
        order_id = row["venue_order_id"].strip()
        if not fill_id or fill_id in ids or not order_id or not row["exchange_id"].strip():
            raise ValueError("duplicate/blank venue fill ID, order ID or exchange ID")
        ids.add(fill_id)
        orders.add(order_id)
        status = row["status"]
        tier = row["confidence"]
        if STATUS_CONFIDENCE.get(status) != tier:
            raise ValueError(f"invalid provenance status/confidence pair: {status}/{tier}")
        occurred = _datetime(row["filled_at"])
        timestamps.append(occurred)
        qty = _decimal(row["signed_quantity"])
        price = _decimal(row["price"])
        side = row["side"].lower()
        if qty == 0 or side not in ("yes", "no") or not Decimal(0) <= price <= Decimal(1):
            raise ValueError("invalid venue side/quantity/price")
        statuses[status] += 1
        confidence[tier] += 1
        days[occurred.date().isoformat()] += 1
        role = "UNKNOWN"
        source_ok = False
        if tier in FILL_CONFIDENCE:
            expected_type = (
                "AUTHORITATIVE_FILL" if tier == "FILL_CONFIRMED"
                else "REALTIME_FILL"
            )
            if row["journal_event_type"] != expected_type:
                raise ValueError("fill-level match uses wrong journal event type")
            if not row["journal_event_id"] or not row["journal_timestamp"]:
                raise ValueError("confirmed fill missing journal event/source clock")
            if (row["journal_order_id"] != order_id
                    or row["journal_exchange_id"] != row["exchange_id"]
                    or _decimal(row["journal_quantity"]) != qty
                    or abs(_decimal(row["journal_price"]) - price) > Decimal("0.000001")):
                raise ValueError("confirmed fill's matching economic tuple changed")
            if tier == "FILL_CONFIRMED":
                if row["journal_fill_id"] != fill_id:
                    raise ValueError("authoritative ID mismatch")
            elif abs((_datetime(row["journal_timestamp"]) - occurred).total_seconds()) > 2:
                raise ValueError("realtime evidence outside Lane A match window")
            source_ok = True
            with_source_timestamp += 1
            confirmed_sides[side.upper()] += 1
            role_tag = row["journal_strategy_family"].strip().upper()
            role = role_tag if role_tag in ("MAKE", "TAKE") else "UNKNOWN"
            roles[role] += 1
        else:
            # An order-only association cannot inherit a fill-specific strategy
            # family, even if an order elsewhere had a known MAKE designation.
            role = "NOT_FILL_ATTRIBUTABLE"
        audit_ledger.append({
            "venue_fill_id": fill_id,
            "venue_order_id": order_id,
            "exchange_id": row["exchange_id"],
            "market_id": row["market_id"],
            "venue_fill_at": occurred.isoformat(),
            "venue_side": side.upper(),
            "venue_signed_quantity": str(qty),
            "venue_raw_price": str(price),
            "provenance_status": status,
            "fill_level_confidence": source_ok,
            "maker_taker_attribution": role,
            "source_timestamp_matched": source_ok,
            "confirmed_passive_quote": None,
            "captured_bid_ask_spread": None,
            "fees_susqies": None,
            "inventory_capital_susqies": None,
            "signed_post_fill_price_change": None,
            "midpoint_markouts": {h: None for h in MARKOUT_HORIZONS},
            "executable_exit_markouts": {h: None for h in MARKOUT_HORIZONS},
            "realised_net_pnl_susqies": None,
            "quote_distance_from_fv": None,
            "inventory_before_fill": None,
            "settlement_verified": None,
            "unavailable_reason": "NO_CAUSAL_QUOTE_FEE_EXIT_AND_ORDER_LIFECYCLE_EVIDENCE",
        })

    if lane_a_aggregate is not None:
        check = {
            "venue_fill_count": len(rows),
            "confirmed_fill_count": sum(confidence[c] for c in FILL_CONFIDENCE),
            "order_only_count": confidence["ORDER_ONLY"],
            "unique_venue_order_count": len(orders),
        }
        for field, value in check.items():
            if lane_a_aggregate.get(field) != value:
                raise ValueError(f"Lane A aggregate disagrees: {field}")
        if lane_a_aggregate.get("confidence_counts") != dict(confidence):
            raise ValueError("Lane A aggregate confidence census disagrees")
        if lane_a_aggregate.get("status_counts") != dict(statuses):
            raise ValueError("Lane A aggregate provenance breakdown disagrees")
        if lane_a_aggregate.get("snapshot_sha256") is None:
            raise ValueError("Lane A venue snapshot digest missing")

    confirmed = sum(confidence[c] for c in FILL_CONFIDENCE)
    report: dict[str, Any] = {
        "schema": "LANE_C_HISTORICAL_MAKER_TRIAGE_V1",
        "source_csv_sha256": digest,
        "lane_a_aggregate_crosschecked": lane_a_aggregate is not None,
        "upstream_venue_snapshot_sha256": (
            lane_a_aggregate.get("snapshot_sha256") if lane_a_aggregate else None
        ),
        "source_authentication": (
            "REQUIRES_INDEPENDENT_LANE_A_SNAPSHOT_JOURNAL_ATTESTATION"
        ),
        "venue_fills": len(rows),
        "unique_orders_with_venue_fills": len(orders),
        "window_utc": {
            "first_fill": min(timestamps).isoformat() if timestamps else None,
            "last_fill": max(timestamps).isoformat() if timestamps else None,
            "days": dict(sorted(days.items())),
        },
        "provenance_status_counts": dict(sorted(statuses.items())),
        "fill_confidence_counts": dict(sorted(confidence.items())),
        "confirmed_fill_level": confirmed,
        "order_level_only": confidence["ORDER_ONLY"],
        "matched_source_clock_count": with_source_timestamp,
        "confirmed_fill_side_counts": dict(sorted(confirmed_sides.items())),
        "confirmed_fill_row_strategy_family": {
            "MAKE": roles["MAKE"], "TAKE": roles["TAKE"], "UNKNOWN": roles["UNKNOWN"]
        },
        "independently_verified_passive_maker_fills": None,
        "maker_eligible_order_ack_count": None,
        "maker_zero_fill_acks": None,
        "maker_cancel_request_and_confirmed_cancel": None,
        "maker_rejected_or_unresolved_orders": None,
        "empirical_maker_fill_probability": None,
        "maker_fill_calibration_vs_487_4_149": "NOT_COMPARABLE_UNRECONCILED_SAMPLES",
        "fees_verified_fill_count": 0,
        "receipt_time_valid_post_fill_markouts": {h: None for h in MARKOUT_HORIZONS},
        "measured_after_cost_maker_pnl": None,
        "measured_capital_share_days": None,
        "market_day_clustered_95pct_interval": None,
        "no_quote_incremental_trading_pnl": 0,
        "no_quote_control_caveat": (
            "incremental order PnL only, not counterfactual inventory "
            "or opportunity cost"
        ),
        "adverse_selection_and_fv_regime_subgroups": None,
        "shadow_hypotheses_proposed": [],
        "decision": "INSUFFICIENT_VERIFIABLE_FILLS",
        "reason": (
            "Venue fill matches are not independently attributed complete passive maker "
            "orders. Eligible non-fill/cancel denominators, reliable quote-time BBO, "
            "execution fees, exits and inventory histories are absent from this CSV."
        ),
    }
    return report, audit_ledger


def _exclusive_write_json(path: Path, data: Any) -> None:
    """Private local artifact only; never overwrite any evidence/output."""
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(data, handle, sort_keys=True, indent=2)
        handle.write("\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provenance-csv", type=Path, required=True)
    parser.add_argument("--lane-a-aggregate", type=Path, required=True)
    parser.add_argument("--expected-sha256", required=True,
                        help="Independently recorded SHA256 of fill_provenance.csv")
    parser.add_argument("--output", type=Path, required=True,
                        help="PRIVATE local JSON scorecard path, must not exist")
    parser.add_argument("--private-ledger-output", type=Path,
                        help="Optional PRIVATE row-level audit path, must not exist")
    args = parser.parse_args()
    paths = [args.provenance_csv.resolve(), args.lane_a_aggregate.resolve(),
             args.output.resolve()]
    if args.private_ledger_output:
        paths.append(args.private_ledger_output.resolve())
    if len(set(paths)) != len(paths):
        parser.error("input/output paths must all differ")
    aggregate = json.loads(args.lane_a_aggregate.read_text(encoding="utf-8"))
    report, rows = audit_fills(
        args.provenance_csv.read_bytes(), lane_a_aggregate=aggregate,
        expected_sha256=args.expected_sha256,
    )
    if args.output.exists() or (args.private_ledger_output and args.private_ledger_output.exists()):
        parser.error("refusing to overwrite prior research artifact")
    # The caller must supply an existing PRIVATE destination directory; this
    # code never creates a public report containing fill/order identities.
    _exclusive_write_json(args.output, report)
    if args.private_ledger_output:
        _exclusive_write_json(args.private_ledger_output, rows)


if __name__ == "__main__":
    main()
