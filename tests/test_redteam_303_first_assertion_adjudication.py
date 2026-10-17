"""Independent frozen #303 first-assertion check on unchanged #209/#214.

50% per price LEVEL with whole-share FLOOR is a conservative *research*
qualification convention, NOT a verified SIG matching rule or actual fill.
The existing #303 strict test is inherited byte-identically and left RED.
"""
from __future__ import annotations

import hashlib
from decimal import ROUND_DOWN, Decimal
from pathlib import Path
from typing import Any

from predictions_cup.replay.economic_005i import evaluate
from predictions_cup.replay.model import BookLevel
from test_economic_005i import _multilevel_tape, cfg, feature, freeze, pair

D = Decimal
ORIGINAL_303_TEST_GIT_BLOB = "5313bfe7ab80b645d7b7c7acc0a843e356154831"


def test_original_alpha1_303_strict_test_byte_identical() -> None:
    raw = Path(__file__).with_name("test_005i_whole_share_depth_falsifier.py").read_bytes()
    digest = hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\x00" + raw).hexdigest()
    assert digest == ORIGINAL_303_TEST_GIT_BLOB


def _two_level_result(displayed_per_level: str) -> dict[str, Any]:
    levels = (
        BookLevel(D("0.40"), D(displayed_per_level)),
        BookLevel(D("0.50"), D(displayed_per_level)),
    )
    tape = _multilevel_tape(levels, levels)
    return evaluate(
        tape, [feature()], [pair()],
        cfg(quantity=D("1"), depth_haircut=D("0.5")), freeze(),
    )


def test_frozen_first_failure_per_level_floor_vs_aggregate_fractional_replay() -> None:
    levels = (BookLevel(D("0.40"), D("1")), BookLevel(D("0.50"), D("1")))
    assert sum(
        ((x.quantity * D("0.5")).to_integral_value(rounding=ROUND_DOWN) for x in levels),
        D(0),
    ) == D(0)
    r = _two_level_result("1")
    assert r["results"]["REVERSAL"]["signals"] == 1
    # The FIRST frozen #303 failing economic assertion ONLY. Do not
    # claim any of that test's later assertions execute on this RED case.
    assert r["results"]["REVERSAL"]["qualified_orders"] == 0


def test_separate_2_share_level_positive_shape_control() -> None:
    # Unlike the frozen RED case, floor(2 * 0.5) = one WHOLE share per level.
    levels = (BookLevel(D("0.40"), D("2")), BookLevel(D("0.50"), D("2")))
    assert sum(
        ((x.quantity * D("0.5")).to_integral_value(rounding=ROUND_DOWN) for x in levels),
        D(0),
    ) == D(2)
    r = _two_level_result("2")
    s = r["results"]["REVERSAL"]
    assert s["qualified_orders"] == 1
    assert s["touch_fill_upper_bound"] == 1
    assert r["promotion"].startswith("BLOCKED")
