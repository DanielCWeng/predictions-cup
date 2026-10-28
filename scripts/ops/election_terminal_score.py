"""Offline, read-only settlement slate scoring from a locally reviewed JSON file.

Usage:
  python scripts/ops/election_terminal_score.py input.json

No SIG authentication, election feed requests, order submissions, or file writes.
The input must contain externally verified executable prices, complete joint
scenario outcomes, and explicit independently justified probability views.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from predictions_cup.analysis.election_terminal import (
    ExecutableTrade,
    Holding,
    ProbabilityView,
    SettlementScenario,
    TradePlan,
    evaluate_trade_plans,
)


def score_document(document: dict[str, Any]) -> list[dict[str, object]]:
    scenarios = tuple(
        SettlementScenario(name=s["name"], resolves_yes=s["resolves_yes"])
        for s in document["scenarios"]
    )
    views = tuple(
        ProbabilityView(
            name=m["name"],
            evidence_reference=m["evidence_reference"],
            probabilities=m["probabilities"],
        )
        for m in document["probability_views"]
    )
    holdings = tuple(Holding(**h) for h in document.get("holdings", []))
    plans = tuple(
        TradePlan(
            name=plan["name"],
            trades=tuple(ExecutableTrade(**trade) for trade in plan["trades"]),
        )
        for plan in document["plans"]
    )
    results = evaluate_trade_plans(
        scenarios=scenarios,
        probability_views=views,
        holdings=holdings,
        cash=document["cash"],
        target_balance=document["target_balance"],
        plans=plans,
    )
    return [asdict(r) for r in results]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Offline comparison of election terminal-settlement plans"
    )
    parser.add_argument("input_json", type=Path)
    args = parser.parse_args()
    try:
        payload = json.loads(args.input_json.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("expected a JSON object")
        results = score_document(payload)
    except (ValueError, KeyError, TypeError) as exc:
        parser.error(f"invalid evidence or trade plan: {exc}")
    print(json.dumps({
        "mode": "OFFLINE_RESEARCH_ONLY",
        "execution_authority": False,
        "calibration_claim": "NONE_INPUT_PROBABILITIES_UNVERIFIED",
        "ranked_plans": results,
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
