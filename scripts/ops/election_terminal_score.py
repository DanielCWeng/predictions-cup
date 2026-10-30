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
from datetime import datetime
from pathlib import Path
from typing import Any

from predictions_cup.analysis.election_opportunity import CandidateQuote, rank_opportunities
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



def rank_candidate_document(document: dict[str, Any]) -> dict[str, object]:
    """Rank observed, whole-depth candidates from a reviewed evidence file.

    The CLI cannot invent quote provenance, settlement probabilities or a
    leaderboard threshold. It also never accesses credentials or places orders.
    """
    required = (
        "portfolio_complete", "account_reconciled", "uncertain_order_exposure",
        "authoritative_position_market_ids", "as_of", "candidate_quotes",
    )
    missing = [name for name in required if name not in document]
    if missing:
        raise ValueError(f"missing required live-evidence fields: {missing}")
    scenarios = tuple(
        SettlementScenario(name=s["name"], resolves_yes=s["resolves_yes"])
        for s in document["scenarios"]
    )
    views = tuple(
        ProbabilityView(
            name=m["name"], evidence_reference=m["evidence_reference"],
            probabilities=m["probabilities"],
        )
        for m in document["probability_views"]
    )
    holdings = tuple(Holding(**h) for h in document.get("holdings", []))
    candidates = tuple(
        CandidateQuote(
            identifier=c["id"],
            trade=ExecutableTrade(**{
                key: c[key] for key in (
                    "market_id", "action", "side", "quantity",
                    "executable_price", "displayed_shares",
                )
            }, extra_cost_per_share=c.get("extra_cost_per_share", 0)),
            observed_at=datetime.fromisoformat(c["quote_observed_at"]),
            source_reference=c["source_reference"],
        )
        for c in document["candidate_quotes"]
    )
    result = rank_opportunities(
        scenarios=scenarios, probability_views=views,
        holdings=holdings, cash=document["cash"],
        target_balance=document["target_balance"],
        candidates=candidates,
        now=datetime.fromisoformat(document["as_of"]),
        authoritative_position_market_ids=frozenset(
            document["authoritative_position_market_ids"]
        ),
        portfolio_complete=document["portfolio_complete"] is True,
        account_reconciled=document["account_reconciled"] is True,
        uncertain_order_exposure=document["uncertain_order_exposure"],
        max_quote_age_seconds=document.get("max_quote_age_seconds", 15.0),
        min_robust_gain=document.get("min_robust_gain", 0.0),
        max_results=document.get("max_results", 15),
    )
    return result.as_document()

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
        if "candidate_quotes" in payload:
            response = rank_candidate_document(payload)
        else:
            results = score_document(payload)
            response = {
                "mode": "OFFLINE_RESEARCH_ONLY",
                "execution_authority": False,
                "calibration_claim": "NONE_INPUT_PROBABILITIES_UNVERIFIED",
                "ranked_plans": results,
            }
    except (ValueError, KeyError, TypeError) as exc:
        parser.error(f"invalid evidence or trade plan: {exc}")
    print(json.dumps(response, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
