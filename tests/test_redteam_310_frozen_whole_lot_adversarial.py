"""Independent #310 grader: unchanged #303 strict oracle and new whole-lot adversaries.

All books and events invented. Conservative 50%-per-level integer flooring is
a frozen offline-screening convention; not a native SIG fill/lot-size assertion.
"""
from __future__ import annotations

import hashlib
from decimal import Decimal
from pathlib import Path

from predictions_cup.replay.economic_005i import (
    _marginal_buy_limit,
    _walk,
    _walk_partial_close,
    evaluate,
)
from predictions_cup.replay.model import BookLevel
from test_economic_005i import _multilevel_tape, cfg, feature, freeze, pair

D = Decimal
ORIGINAL_303_BLOB = "5313bfe7ab80b645d7b7c7acc0a843e356154831"
HALF = D("0.5")


def _levels(*quantities: str) -> tuple[BookLevel, ...]:
    return tuple(
        BookLevel(D("0.40") + D("0.10") * i, D(qty))
        for i, qty in enumerate(quantities)
    )


def test_frozen_original_303_assertions_remain_byte_identical() -> None:
    raw = Path(__file__).with_name("test_005i_whole_share_depth_falsifier.py").read_bytes()
    canonical = b"blob " + str(len(raw)).encode() + b"\x00" + raw
    assert hashlib.sha1(canonical).hexdigest() == ORIGINAL_303_BLOB


def test_per_level_1_999_is_not_whole_after_half_haircut() -> None:
    # No per-level or across-level rounding up of two almost-whole fragments.
    thin = _levels("1.999", "1.999")
    assert _walk(thin, D("1"), buy=True, haircut=HALF) is None
    assert _marginal_buy_limit(thin, D("1"), haircut=HALF) is None
    assert _walk_partial_close(thin, D("1"), haircut=HALF) == (D(0), D(0))


def test_marginal_limit_uses_second_level_only_when_whole_share_survives() -> None:
    # Best ask 0.40 is screened to 0 because its book level has 1 share;
    # second level 0.50 has 2 shares so one whole unit survives.
    levels = _levels("1", "2")
    assert _walk(levels, D("1"), buy=True, haircut=HALF) == D("0.50")
    assert _marginal_buy_limit(levels, D("1"), haircut=HALF) == D("0.50")
    assert _walk(levels, D("1"), buy=True, haircut=HALF,
                 max_buy_price=D("0.40")) is None
    assert _walk_partial_close(levels, D("1"), haircut=HALF) == (
        D("1"), D("0.50"),
    )


def test_separate_decision_and_post_latency_arrival_never_reuse_thin_depth() -> None:
    deep, thin = _levels("2", "2"), _levels("1", "1")
    trial = evaluate(
        _multilevel_tape(deep, thin),
        [feature()], [pair()], cfg(quantity=D("1"), depth_haircut=HALF), freeze(),
    )["results"]["REVERSAL"]
    assert trial["signals"] == 1
    assert trial["qualified_orders"] == 1
    assert trial["touch_fill_upper_bound"] == 0
    assert trial["trades"] == []
    assert trial["rejections"]["ioc_limit_or_depth_no_fill"] == 1
