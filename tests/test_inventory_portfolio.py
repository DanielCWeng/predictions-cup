"""Synthetic regression tests only; they do not establish an empirical edge."""
from __future__ import annotations

from decimal import Decimal as D

import pytest

from predictions_cup.research.inventory_portfolio import (
    build_ledger,
    close_slice,
    paired_review,
    party_race,
    tournament_sensitivity,
)

NOW = "2026-10-12T21:00:00Z"


def book(eid: str, *, bid: str = ".60", ask: str = ".40", size: str = "100",
         received: str = NOW) -> dict:
    return {
        "exchange_id": eid, "status": "open",
        "request_started_at": received, "received_at": received,
        "bids": [{"price": bid, "quantity": size}],
        "asks": [{"price": ask, "quantity": size}],
    }


def p(eid: str, side: str, party: str, race: str = "FL-22") -> dict:
    return {
        "exchange_id": eid, "market_id": eid,
        "market_title": f"Will the {party} Party win the {race} House race?",
        "option": "YES", "quantity": side, "avg_cost": ".60",
        "cost_basis": "60", "market_value": "50", "unrealized_pnl": "-10",
        "current_price": ".50",
    }


def test_title_extraction_is_screening_only() -> None:
    assert party_race("Will the Republican Party win the FL-22 House race?") == (
        "Republican", "FL-22"
    )
    assert party_race("Unsourced election") is None


def test_long_no_closes_at_one_minus_yes_ask_not_short_profit() -> None:
    b = book("R", ask=".45")
    v = close_slice(D("-100"), D("100"), b, exchange_id="R",
                    evaluated_at=NOW, extra_slippage_ticks=0, fee_bps=D("0"))
    assert D(v["net_cash_if_filled"]) == D("55")
    assert v["gross_freed_if_filled"] == "100"


def test_long_yes_consumes_yes_bids() -> None:
    b = book("D", bid=".35")
    v = close_slice(D("100"), D("100"), b, exchange_id="D",
                    evaluated_at=NOW, extra_slippage_ticks=0, fee_bps=D("0"))
    assert D(v["net_cash_if_filled"]) == D("35")


def test_partial_book_does_not_certify_complete_exit() -> None:
    v = close_slice(D("-100"), D("100"), book("R", size="40"),
                    exchange_id="R", evaluated_at=NOW)
    assert v["status"] == "NOT_READY"
    assert v["fillable"] == "40"
    assert v["net_cash_if_filled"] is None


def test_future_and_stale_books_fail_closed() -> None:
    for t in ("2026-10-12T20:59:45Z", "2026-10-12T21:00:01Z"):
        v = close_slice(D("100"), D("100"), book("D", received=t),
                        exchange_id="D", evaluated_at=NOW)
        assert v["status"] == "NOT_READY"


def test_wrong_exchange_or_closed_market_abstains() -> None:
    b = book("R")
    assert close_slice(D("100"), D("1"), b, exchange_id="D",
                       evaluated_at=NOW)["reason"] == "WRONG_EXCHANGE"
    b["status"] = "closed"
    assert close_slice(D("100"), D("1"), b, exchange_id="R",
                       evaluated_at=NOW)["status"] == "NOT_READY"


def test_depth_walk_requires_second_price_level() -> None:
    b = book("D", bid=".60", size="40")
    b["bids"].append({"price": ".50", "quantity": "60"})
    v = close_slice(D("100"), D("100"), b, exchange_id="D",
                    evaluated_at=NOW, extra_slippage_ticks=0, fee_bps=D("0"))
    assert D(v["net_cash_if_filled"]) == D("54")


def test_no_no_pair_disadvantage_and_no_promotion() -> None:
    snapshot = {"positions": [p("D", "-100", "Democratic"), p("R", "-100", "Republican")]}
    books = {"D": book("D", ask=".570"), "R": book("R", ask=".465")}
    ledger = build_ledger(snapshot, books, evaluated_at=NOW)
    assert all(row["held_side"] == "NO" for row in ledger)
    assert all(row["cost_basis"] == "60" for row in ledger)
    pair = paired_review(ledger, books, evaluated_at=NOW)[0]
    assert pair["type"] == "NO_NO"
    assert D(pair["unwind_cost_vs_strict_two_party_hold"]) > 0
    assert pair["incremental_expected_portfolio_benefit"] is None
    assert pair["executable"] is False


def test_opposite_party_yes_no_is_not_a_hedge() -> None:
    snapshot = {"positions": [p("D", "100", "Democratic"), p("R", "-100", "Republican")]}
    books = {"D": book("D", bid=".8"), "R": book("R", ask=".4")}
    pair = paired_review(build_ledger(snapshot, books, evaluated_at=NOW),
                         books, evaluated_at=NOW)[0]
    assert pair["type"] == "DOUBLED_DIRECTIONAL"
    assert D(pair["directional_backed_party_break_even_probability"]) > D(".65")
    assert pair["status"] == "NOT_RANKABLE_OR_PROMOTABLE"


def test_duplicate_exchange_rejected() -> None:
    with pytest.raises(ValueError, match="duplicate"):
        build_ledger({"positions": [p("D", "100", "Democratic")] * 2},
                     {"D": book("D")}, evaluated_at=NOW)


def test_podium_requires_independent_assumptions_and_reviewed_semantics() -> None:
    rows = build_ledger({"positions": [p("D", "100", "Democratic")]},
                        {"D": book("D")}, evaluated_at=NOW)
    with pytest.raises(ValueError, match="unverified"):
        tournament_sensitivity(
            rows, cash=D("30000"),
            race_inputs={"FL-22": {"p_democratic": ".5", "state": "FL",
                                   "independent_source": "synthetic"}},
            competitor_inputs=[{"mean": "30000", "sd": "1000"}] * 3,
        )


def test_hypothetical_factor_sensitivity_is_deterministic() -> None:
    rows = build_ledger({"positions": [p("D", "100", "Democratic")]},
                        {"D": book("D")}, evaluated_at=NOW)
    kwargs = {
        "cash": D("30000"),
        "race_inputs": {"FL-22": {"p_democratic": ".5", "state": "FL",
                                  "settlement_review": "VERIFIED",
                                  "independent_source": "SYNTHETIC"}},
        "competitor_inputs": [{"mean": "30000", "sd": "1000"}] * 3,
        "seed": 3, "paths": 200,
    }
    a = tournament_sensitivity(rows, **kwargs)
    assert a == tournament_sensitivity(rows, **kwargs)
    assert "SYNTHETIC" in a["model"]
