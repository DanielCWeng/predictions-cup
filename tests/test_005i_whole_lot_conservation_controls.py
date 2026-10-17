"""Synthetic-only regression controls for #303 whole-share-level convention.

No original SIG book, native matching-lot attestation, order or actual fill is
present. The unchanged strict negative is tests/test_005i_whole_share_depth_falsifier.py.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

from predictions_cup.replay.economic_005i import (
    _marginal_buy_limit,
    _walk,
    _walk_partial_close,
    evaluate,
)
from predictions_cup.replay.model import BookLevel, QuotePayload, ReplayEvent, ReplaySource
from test_economic_005i import (
    T,
    _multilevel_tape,
    cfg,
    feature,
    freeze,
    pair,
    tape,
)

D = Decimal
HAIRCUT = D("0.5")
THIN = (BookLevel(D("0.40"), D("1")), BookLevel(D("0.50"), D("1")))
DEEP = (BookLevel(D("0.40"), D("2")), BookLevel(D("0.50"), D("2")))


def test_per_level_floor_aligns_entry_limit_and_partial_exit() -> None:
    # Two separately priced half-shares must not be pooled to one whole lot.
    assert _walk(THIN, D("1"), buy=True, haircut=HAIRCUT) is None
    assert _marginal_buy_limit(THIN, D("1"), haircut=HAIRCUT) is None
    assert _walk_partial_close(THIN, D("1"), haircut=HAIRCUT) == (
        D(0), D(0),
    )
    assert _walk(DEEP, D("1"), buy=True, haircut=HAIRCUT) == D("0.40")
    assert _marginal_buy_limit(DEEP, D("1"), haircut=HAIRCUT) == D("0.40")
    assert _walk_partial_close(DEEP, D("2"), haircut=HAIRCUT) == (
        D("2"), D("0.90"),
    )


def test_yes_decision_and_arrival_books_each_gate_whole_lot() -> None:
    # Insufficient at decision: never reserve or claim a placeable IOC,
    # regardless of abundant liquidity after the decision.
    early_thin = evaluate(
        _multilevel_tape(THIN, DEEP),
        [feature()], [pair()], cfg(quantity=D("1")), freeze(),
    )["results"]["REVERSAL"]
    assert early_thin["signals"] == 1
    assert early_thin["qualified_orders"] == 0
    assert early_thin["touch_fill_upper_bound"] == 0

    # Whole share exists at decision but vanishes at arrival: do not
    # invent a fill from past book depth.
    late_thin = evaluate(
        _multilevel_tape(DEEP, THIN),
        [feature()], [pair()], cfg(quantity=D("1")), freeze(),
    )["results"]["REVERSAL"]
    assert late_thin["qualified_orders"] == 1
    assert late_thin["touch_fill_upper_bound"] == 0
    assert late_thin["rejections"]["ioc_limit_or_depth_no_fill"] == 1
    assert late_thin["trades"] == []

    both_deep = evaluate(
        _multilevel_tape(DEEP, DEEP),
        [feature()], [pair()], cfg(quantity=D("1")), freeze(),
    )
    yes = both_deep["results"]["REVERSAL"]
    assert yes["qualified_orders"] == 1
    assert yes["touch_fill_upper_bound"] == 1
    assert yes["trades"][0]["placeable_marginal_buy_limit"] == 0.40
    assert yes["trades"][0]["entry_cost"] == 0.40
    assert yes["trades"][0]["side"] == "YES"
    assert both_deep["promotion"].startswith("BLOCKED")


def _no_books(
    decision_qty: str, arrival_qty: str,
) -> tuple[ReplayEvent, ...]:
    """Same exchange YES bids become held-NO buy asks via 1 - YES bid."""
    events = list(tape(pm_no_favoured=True))
    targets = {
        T - timedelta(milliseconds=100): D(decision_qty),
        T + timedelta(milliseconds=200): D(arrival_qty),
    }
    for i, event in enumerate(events):
        if event.source is not ReplaySource.SIG or event.observed_at not in targets:
            continue
        if not isinstance(event.payload, QuotePayload):
            raise AssertionError("invented SIG quote fixture changed shape")
        quantity = targets[event.observed_at]
        events[i] = replace(event, payload=replace(
            event.payload, best_bid=D("0.54"), best_ask=D("0.65"),
            bids=(
                BookLevel(D("0.54"), quantity),
                BookLevel(D("0.50"), quantity),
            ),
            asks=(BookLevel(D("0.65"), D("100")),),
        ))
    return tuple(events)


def test_no_complement_remains_same_exchange_with_whole_lot_floor() -> None:
    # Frozen PM 5m positive return induces NO, while SIG stores only YES
    # book depth; no sibling outcome book is ever fabricated.
    no_feature = feature(ret_5m=0.03)
    thin = evaluate(
        _no_books("1", "2"), [no_feature], [pair()],
        cfg(quantity=D("1")), freeze(),
    )["results"]["REVERSAL"]
    assert thin["signals"] == 1
    assert thin["qualified_orders"] == 0
    assert thin["touch_fill_upper_bound"] == 0

    late_thin = evaluate(
        _no_books("2", "1"), [no_feature], [pair()],
        cfg(quantity=D("1")), freeze(),
    )["results"]["REVERSAL"]
    assert late_thin["qualified_orders"] == 1
    assert late_thin["touch_fill_upper_bound"] == 0
    assert late_thin["trades"] == []

    valid = evaluate(
        _no_books("2", "2"), [no_feature], [pair()],
        cfg(quantity=D("1")), freeze(),
    )
    no = valid["results"]["REVERSAL"]
    assert no["qualified_orders"] == 1
    assert no["touch_fill_upper_bound"] == 1
    trade = no["trades"][0]
    assert trade["side"] == "NO"
    assert trade["exchange_id"] == "sig-yes"
    assert trade["placeable_marginal_buy_limit"] == 0.46
    assert trade["entry_cost"] == 0.46
    assert trade["signed_yes_entry_delta"] == -1
    assert valid["promotion"].startswith("BLOCKED")
