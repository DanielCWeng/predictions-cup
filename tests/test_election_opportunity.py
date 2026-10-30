"""Synthetic, disjoint settlement scenarios: never live prices or forecasts."""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from predictions_cup.analysis.election_opportunity import CandidateQuote, rank_opportunities
from predictions_cup.analysis.election_terminal import (
    ExecutableTrade,
    Holding,
    ProbabilityView,
    SettlementScenario,
)

NOW = datetime(2026, 10, 30, 15, 0, tzinfo=UTC)
SCENARIOS = (
    SettlementScenario("swing", {"a": False, "b": True}),
    SettlementScenario("hold", {"a": True, "b": False}),
)
VIEWS = (
    ProbabilityView("first", "fixture://one", {"swing": .7, "hold": .3}),
    ProbabilityView("second", "fixture://two", {"swing": .6, "hold": .4}),
)
HOLDINGS = (Holding("a", yes_shares=50),)


def _candidate(
    identifier: str, market: str, action: str, side: str,
    quantity: int, price: float,
) -> CandidateQuote:
    return CandidateQuote(
        identifier=identifier,
        trade=ExecutableTrade(
            market_id=market, action=action, side=side,  # type: ignore[arg-type]
            quantity=quantity, executable_price=price,
            displayed_shares=quantity,
            extra_cost_per_share=.01,
        ),
        observed_at=NOW,
        source_reference=f"fixture://book/{identifier}",
    )


def _rank(
    candidates: tuple[CandidateQuote, ...],
    **overrides: object,
):
    parameters = dict(
        scenarios=SCENARIOS, probability_views=VIEWS,
        holdings=HOLDINGS, cash=0.0, target_balance=45.0,
        candidates=candidates, now=NOW,
        authoritative_position_market_ids=frozenset({"a"}),
        portfolio_complete=True, account_reconciled=True,
        uncertain_order_exposure=0.0,
    )
    parameters.update(overrides)
    return rank_opportunities(**parameters)  # type: ignore[arg-type]


def test_sale_first_rotation_funds_winning_joint_outcome_and_beats_ev_only() -> None:
    sale = _candidate("sell-a", "a", "SELL", "YES", 50, .6)
    buy = _candidate("buy-b", "b", "BUY", "YES", 50, .4)
    ranking = _rank((sale, buy))
    assert ranking.plans_evaluated == 3
    assert ranking.rejected["insufficient_cash"] == 1
    assert ranking.ranked[0].name == "rotate:sell-a:buy-b"
    winner = ranking.ranked[0]
    assert winner.capital_released_by_exits == pytest.approx(29.5)
    assert winner.score.remaining_cash == pytest.approx(9)
    assert winner.score.min_probability_improvement == pytest.approx(.2)
    assert winner.score.min_expected_gain == pytest.approx(19)
    assert winner.worst_outcome_delta == pytest.approx(-41)
    assert winner.best_outcome_delta == pytest.approx(59)
    assert winner.max_initial_cash_required == 0
    assert ranking.execution_authority is False


def test_untrusted_account_can_rank_shadow_but_cannot_claim_execution() -> None:
    sale = _candidate("sell-a", "a", "SELL", "YES", 50, .6)
    report = _rank((sale,), account_reconciled=False, uncertain_order_exposure=15000)
    assert report.ranked
    assert not report.inputs_trusted
    assert not report.as_document()["execution_authority"]


def test_completeness_and_probability_support_are_mandatory() -> None:
    sale = (_candidate("s", "a", "SELL", "YES", 50, .6),)
    with pytest.raises(ValueError, match="complete authoritative inventory"):
        _rank(sale, portfolio_complete=False)
    with pytest.raises(ValueError, match="differs from authoritative"):
        _rank(sale, authoritative_position_market_ids=frozenset({"a", "b"}))
    with pytest.raises(ValueError, match="model probabilities"):
        _rank(sale, probability_views=(
            ProbabilityView("bad", "fixture://bad", {"swing": .7, "hold": .7}),
        ))
    with pytest.raises(ValueError, match="every market explicitly"):
        _rank(sale, scenarios=(
            SCENARIOS[0], SettlementScenario("hold", {"a": True}),
        ))


def test_expired_future_unknown_and_oversize_quotes_do_not_become_trades() -> None:
    good = _candidate("good", "a", "SELL", "YES", 50, .6)
    old = replace(good, identifier="old", observed_at=NOW-timedelta(seconds=16))
    future = replace(good, identifier="future", observed_at=NOW+timedelta(seconds=1))
    missing = replace(good, identifier="unknown", trade=replace(
        good.trade, market_id="not-modelled",
    ))
    oversized = replace(good, identifier="oversize", trade=replace(
        good.trade, displayed_shares=10,
    ))
    report = _rank((good, old, future, missing, oversized))
    assert report.plans_evaluated == 1
    assert report.rejected["stale_or_future_quote"] == 2
    assert report.rejected["missing_identity_or_settlement"] == 1
    assert report.rejected["invalid_executable_quote"] == 1
    assert report.ranked[0].constituent_ids == ("good",)


def test_duplicate_id_and_unbounded_candidate_search_block() -> None:
    candidate = _candidate("one", "a", "SELL", "YES", 50, .6)
    with pytest.raises(ValueError, match="duplicate"):
        _rank((candidate, candidate))
    with pytest.raises(ValueError, match="search bound"):
        _rank((candidate,), max_candidates=0)


def test_uncorroborated_buy_cannot_spend_held_inventory_as_cash() -> None:
    entry = _candidate("buy-b", "b", "BUY", "YES", 50, .4)
    result = _rank((entry,))
    assert result.ranked == ()
    assert result.rejected["insufficient_cash"] == 1


def test_no_position_does_not_create_naked_sale_opportunity() -> None:
    exit_quote = _candidate("sell-b", "b", "SELL", "YES", 30, .8)
    result = _rank((exit_quote,))
    assert not result.ranked
    assert result.rejected["unowned_inventory"] == 1


def test_nonpositive_forecast_consensus_is_not_recommended() -> None:
    sale = _candidate("sell-a", "a", "SELL", "YES", 50, .01)
    result = _rank((sale,))
    assert result.ranked == ()
    assert result.rejected["nonpositive_robust_expected_gain"] == 1


def test_cli_auto_shortlist_is_offline_and_never_authorizes_orders(tmp_path: Path) -> None:
    payload = {
        "cash": 0, "target_balance": 45,
        "as_of": NOW.isoformat(), "portfolio_complete": True,
        "account_reconciled": False, "uncertain_order_exposure": 15000,
        "authoritative_position_market_ids": ["a"],
        "holdings": [{"market_id": "a", "yes_shares": 50, "no_shares": 0}],
        "scenarios": [
            {"name": "swing", "resolves_yes": {"a": False, "b": True}},
            {"name": "hold", "resolves_yes": {"a": True, "b": False}},
        ],
        "probability_views": [
            {"name": "first", "evidence_reference": "fixture://one",
             "probabilities": {"swing": .7, "hold": .3}},
            {"name": "second", "evidence_reference": "fixture://two",
             "probabilities": {"swing": .6, "hold": .4}},
        ],
        "candidate_quotes": [
            {"id": "sell-a", "market_id": "a", "action": "SELL",
             "side": "YES", "quantity": 50, "executable_price": .6,
             "displayed_shares": 50, "extra_cost_per_share": .01,
             "source_reference": "fixture://ask",
             "quote_observed_at": NOW.isoformat()},
            {"id": "buy-b", "market_id": "b", "action": "BUY",
             "side": "YES", "quantity": 50, "executable_price": .4,
             "displayed_shares": 50, "extra_cost_per_share": .01,
             "source_reference": "fixture://bid",
             "quote_observed_at": NOW.isoformat()},
        ],
    }
    file = tmp_path / "input.json"
    file.write_text(json.dumps(payload), encoding="utf-8")
    cli = Path(__file__).resolve().parents[1] / "scripts/ops/election_terminal_score.py"
    run = subprocess.run(
        [sys.executable, str(cli), str(file)], capture_output=True,
        check=True, text=True,
    )
    output = json.loads(run.stdout)
    assert not output["execution_authority"]
    assert not output["inputs_trusted"]
    assert output["ranked_opportunities"][0]["name"] == "rotate:sell-a:buy-b"
