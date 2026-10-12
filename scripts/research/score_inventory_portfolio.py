"""Re-score a PRIVATE GET-only SIG snapshot. No network, credentials or order I/O.

python scripts/research/score_inventory_portfolio.py \
  --snapshot /private/snapshot.json --books /private/orderbooks.json \
  --out /private/inventory_report --gross-cap 100000
Gross cap is explicit and requires an independent up-to-date source.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from predictions_cup.research.inventory_portfolio import (
    build_ledger,
    dec,
    paired_review,
    timestamp,
    tournament_sensitivity,
)


def put(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, indent=2, sort_keys=True, default=str) + "\n")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--snapshot", required=True, type=Path)
    p.add_argument("--books", required=True, type=Path)
    p.add_argument("--out", required=True, type=Path)
    p.add_argument("--as-of")
    p.add_argument("--gross-cap", type=Decimal)
    p.add_argument("--gross-cap-source")
    p.add_argument("--scenarios", type=Path)
    p.add_argument("--max-age-seconds", default=8, type=int)
    args = p.parse_args()
    if args.gross_cap is not None and not args.gross_cap_source:
        p.error("--gross-cap-source required for any declared cap")
    if args.max_age_seconds <= 0:
        p.error("invalid age")
    snapshot = json.loads(args.snapshot.read_text())
    books = json.loads(args.books.read_text())
    as_of = args.as_of or snapshot["ended"]
    ledger = build_ledger(snapshot, books, evaluated_at=as_of,
                          max_age_seconds=args.max_age_seconds)
    pairs = paired_review(ledger, books, evaluated_at=as_of)
    marked = sum((dec(x["market_value"]) for x in ledger), Decimal("0"))
    basis = sum((dec(x["cost_basis"]) for x in ledger), Decimal("0"))
    gross = sum((abs(dec(x["signed_yes_quantity"])) for x in ledger), Decimal("0"))
    net = sum((dec(x["signed_yes_quantity"]) for x in ledger), Decimal("0"))
    orders = snapshot.get("open_orders", [])
    reserved = sum((abs(dec(x["quantity"])) for x in orders), Decimal("0"))
    cap = args.gross_cap
    free_gross = max(Decimal("0"), cap - gross - reserved) if cap is not None else None
    account = snapshot["pnl"]
    if abs(dec(account["total_holdings_value"]) - marked) > Decimal(".02"):
        raise ValueError("position marks fail account reconciliation")
    if abs(dec(account["total_cost_basis"]) - basis) > Decimal(".02"):
        raise ValueError("position cost bases fail account reconciliation")
    fresh = sum(x["exit"]["status"] == "DEPTH_ONLY_NOT_RESERVED" for x in ledger)
    # Intentionally DO NOT sum depth and call it executable account cash.
    seconds = max(0, (timestamp("2026-11-04T17:00:00Z") -
                       timestamp(as_of)).total_seconds())
    for action in pairs:
        action["conditional_capital_seconds_freed"] = str(
            dec(action["gross_exposure_release_if_both_filled"]) * Decimal(str(seconds))
        )
    queue = pairs + [
        {
            "name": x["title"], "type": "SINGLE_EXIT",
            "exchanges": [x["exchange_id"]],
            "potential_gross_release": str(abs(dec(x["signed_yes_quantity"]))),
            "incremental_expected_portfolio_benefit": None,
            "reason": "INDEPENDENT_FAIR_AND_OPPORTUNITY_UNVALIDATED",
        }
        for x in ledger
    ]
    # Never present a gross-sorted screening list as a ranked expected-profit list.
    queue = sorted(queue, key=lambda x: dec(
        x.get("gross_exposure_release_if_both_filled",
              x.get("potential_gross_release", "0"))
    ), reverse=True)
    scenario: dict[str, Any] = {
        "status": "NOT_ESTIMABLE_WITHOUT_INDEPENDENT_PROBABILITIES_AND_COMPETITORS"
    }
    if args.scenarios:
        specs = json.loads(args.scenarios.read_text())
        scenario = tournament_sensitivity(
            ledger, cash=dec(specs["cash"]),
            race_inputs=specs["race_inputs"],
            competitor_inputs=specs["competitor_inputs"],
            seed=specs.get("seed", 20261012), paths=specs.get("paths", 5000),
        )
    report = {
        "as_of": as_of, "snapshot_started": snapshot.get("started"),
        "snapshot_ended": snapshot.get("ended"),
        "source": "PRECAPTURED_GET_ONLY_SIG_DATA",
        "portfolio_stable": snapshot.get("stable"),
        "positions": len(ledger), "gross_shares": str(gross),
        "net_signed_yes": str(net), "cost_basis": str(basis),
        "position_mark_value": str(marked),
        "account_value": str(account["total_account_value"]),
        "gross_cap": str(cap) if cap is not None else None,
        "gross_cap_source": args.gross_cap_source,
        "remaining_risk_gross": str(free_gross) if free_gross is not None else None,
        "outstanding_orders": len(orders),
        "full_depth_and_fresh_book_count": fresh,
        "total_simultaneously_executable_cash": None,
        "ranking_by_incremental_expected_benefit": "BLOCKED_INDEPENDENT_FV_AND_REDEPLOYMENT",
        "screening_top_10_NOT_EV_RANKED": queue[:10],
        "scenario": scenario,
        "no_order_I_O": True,
        "no_journal_write": True,
    }
    args.out.mkdir(parents=True, exist_ok=True, mode=0o700)
    args.out.chmod(0o700)
    put(args.out / "ledger.json", ledger)
    put(args.out / "paired_proposals.json", pairs)
    put(args.out / "review_queue.json", queue)
    put(args.out / "summary.json", report)
    with (args.out / "ledger.csv").open("w", newline="") as f:
        fields = [k for k in ledger[0] if k != "exit"] + [
            "exit_status", "conditional_exit_net_cash"
        ]
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in ledger:
            writer.writerow({
                **{k: row[k] for k in fields if k in row},
                "exit_status": row["exit"]["status"],
                "conditional_exit_net_cash": row["exit"]["net_cash_if_filled"],
            })
    put(args.out / "manifest.json", {
        "source_sha256": {
            str(x): hashlib.sha256(x.read_bytes()).hexdigest()
            for x in (args.snapshot, args.books)
        },
        "created_at": datetime.now(UTC).isoformat(),
        "orders_submitted": 0,
    })
    print(json.dumps({
        "positions": len(ledger),
        "fresh_depth_books": fresh,
        "remaining_gross_capacity": str(free_gross),
        "net_EV_ranking": report["ranking_by_incremental_expected_benefit"],
        "report": str(args.out / "summary.json"),
    }, indent=2))


if __name__ == "__main__":
    main()
