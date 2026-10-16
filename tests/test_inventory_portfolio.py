"""Synthetic regression tests only; they do not establish an empirical edge."""
from __future__ import annotations

from decimal import Decimal as D
from typing import Any

import pytest

from predictions_cup.research.inventory_portfolio import (
    build_ledger,
    close_slice,
    paired_review,
    party_race,
    tournament_sensitivity,
)

NOW = "2026-10-12T21:00:00Z"


def book(eid: str, *, bid: str = ".30", ask: str = ".40", size: str = "100",
         received: str = NOW) -> dict[str, Any]:
    return {
        "exchange_id": eid, "status": "open",
        "request_started_at": received, "received_at": received,
        "bids": [{"price": bid, "quantity": size}],
        "asks": [{"price": ask, "quantity": size}],
    }


def p(eid: str, side: str, party: str, race: str = "FL-22") -> dict[str, Any]:
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
    b = book("D", bid=".60", ask=".70", size="40")
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
    books = {"D": book("D", bid=".8", ask=".85"), "R": book("R", ask=".4")}
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
    kwargs: dict[str, Any] = {
        "cash": D("30000"),
        "race_inputs": {"FL-22": {"p_democratic": ".5", "state": "FL",
                                  "p_republican": ".5", "p_other": "0",
                                  "settlement_review": "VERIFIED",
                                  "independent_source": "SYNTHETIC"}},
        "competitor_inputs": [{"mean": "30000", "sd": "1000"}] * 3,
        "seed": 3, "paths": 200,
    }
    a = tournament_sensitivity(rows, **kwargs)
    assert a == tournament_sensitivity(rows, **kwargs)
    assert "SYNTHETIC" in a["model"]

def test_third_party_winner_pays_both_party_no_positions_not_yes() -> None:
    # An Other winner is an admissible election state. Both D and R YES
    # contracts lose; both NO contracts pay. A binary D/not-D model would
    # incorrectly settle the R YES as a win in every Other scenario.
    holdings = [
        p("DYES", "100", "Democratic"),
        p("RYES", "100", "Republican"),
        p("DNO", "-100", "Democratic"),
        p("RNO", "-100", "Republican"),
    ]
    rows = build_ledger(
        {"positions": holdings},
        {row["exchange_id"]: book(row["exchange_id"]) for row in holdings},
        evaluated_at=NOW,
    )
    got = tournament_sensitivity(
        rows,
        cash=D("300"),
        race_inputs={
            "FL-22": {
                "state": "FL", "p_democratic": "0", "p_republican": "0",
                "p_other": "1", "settlement_review": "VERIFIED",
                "independent_source": "SYNTHETIC_3WAY",
            }
        },
        competitor_inputs=[{"mean": "0", "sd": "0"}] * 3,
        national_logit_sd=0,
        state_logit_sd=0,
        paths=20,
    )
    assert got["expected_final_value"] == 500
    assert got["p05_final_value"] == 500


def test_third_party_probability_is_not_automatically_republican() -> None:
    rows = build_ledger(
        {"positions": [p("R", "100", "Republican")]},
        {"R": book("R")},
        evaluated_at=NOW,
    )
    race = {
        "state": "FL", "p_democratic": ".45", "p_republican": ".45",
        "p_other": ".10", "settlement_review": "VERIFIED",
        "independent_source": "SYNTHETIC_3WAY",
    }
    kwargs = {
        "cash": D("0"),
        "race_inputs": {"FL-22": race},
        "competitor_inputs": [{"mean": "0", "sd": "0"}] * 3,
        "national_logit_sd": 0,
        "state_logit_sd": 0,
        "seed": 3,
        "paths": 12000,
    }
    got = tournament_sensitivity(rows, **kwargs)
    assert 43 < got["expected_final_value"] < 47
    del race["p_other"]
    with pytest.raises(KeyError, match="p_other"):
        tournament_sensitivity(rows, **kwargs)


@pytest.mark.parametrize("bid,ask", [(".60", ".40"), (".40", ".40")])
@pytest.mark.parametrize("signed", ["100", "-100"])
def test_crossed_locked_fail_closed_for_yes_and_no(bid: str, ask: str, signed: str) -> None:
    result = close_slice(D(signed), D("10"), book("D", bid=bid, ask=ask),
                         exchange_id="D", evaluated_at=NOW)
    assert result["reason"] == "CROSSED_OR_LOCKED_BOOK"
    assert result["net_cash_if_filled"] is None
    assert result["gross_freed_if_filled"] == "0"


@pytest.mark.parametrize("signed", ["100", "-100"])
@pytest.mark.parametrize("missing", ["bids", "asks"])
def test_one_sided_books_never_produce_proceeds(signed: str, missing: str) -> None:
    b = book("D")
    del b[missing]
    result = close_slice(D(signed), D("10"), b, exchange_id="D", evaluated_at=NOW)
    assert result["reason"] == "ONE_SIDED_OR_EMPTY_BOOK"
    assert result["net_cash_if_filled"] is None


@pytest.mark.parametrize("price,capacity", [
    (".401", "100"), ("1.005", "100"), ("0", "100"),
    (".40", "0"), (".40", "-1"), (".40", "1.5"),
    (".40", "NaN"), ("NaN", "100"), ("bad", "100"),
])
def test_malformed_tick_price_lot_rejected_on_opposite_side(
    price: str, capacity: str
) -> None:
    b = book("D")
    b["asks"][0] = {"price": price, "quantity": capacity}
    result = close_slice(D("100"), D("10"), b, exchange_id="D", evaluated_at=NOW)
    assert result["reason"] == "INVALID_DEPTH"
    assert result["net_cash_if_filled"] is None


@pytest.mark.parametrize("side,extra_price", [
    ("bids", ".35"), ("asks", ".35"), ("bids", ".40"), ("asks", ".40")
])
def test_unsorted_duplicate_depth_rejected(side: str, extra_price: str) -> None:
    b = book("D")
    b[side].append({"price": extra_price, "quantity": "50"})
    result = close_slice(D("-100"), D("5"), b, exchange_id="D", evaluated_at=NOW)
    assert result["reason"] == "UNSORTED_OR_DUPLICATE_DEPTH"
    assert result["net_cash_if_filled"] is None


def test_validate_unconsumed_levels_after_requested_size() -> None:
    b = book("D")
    b["asks"].append({"price": ".405", "quantity": "0"})
    result = close_slice(D("100"), D("1"), b, exchange_id="D", evaluated_at=NOW)
    assert result["reason"] == "INVALID_DEPTH"
    assert result["net_cash_if_filled"] is None


@pytest.mark.parametrize("signed,quantity", [
    ("1.5", "1"), ("100", ".5"), ("-100", "1.5"),
])
def test_fractional_position_or_close_lot_rejected(signed: str, quantity: str) -> None:
    with pytest.raises(ValueError, match="invalid close quantity"):
        close_slice(D(signed), D(quantity), book("D"),
                    exchange_id="D", evaluated_at=NOW)


def test_partial_no_close_uses_complement_and_frees_only_slice() -> None:
    result = close_slice(D("-200"), D("40"), book("D", ask=".45"),
                         exchange_id="D", evaluated_at=NOW,
                         extra_slippage_ticks=0, fee_bps=D("0"))
    assert result["status"] == "DEPTH_ONLY_NOT_RESERVED"
    assert result["gross_freed_if_filled"] == "40"
    assert D(result["net_cash_if_filled"]) == D("22")


def test_stale_book_never_certifies_partial_close() -> None:
    result = close_slice(D("-200"), D("40"),
                         book("D", received="2026-10-12T20:00:00Z"),
                         exchange_id="D", evaluated_at=NOW)
    assert result["reason"] == "STALE_NONCAUSAL_BOOK"
    assert result["net_cash_if_filled"] is None
