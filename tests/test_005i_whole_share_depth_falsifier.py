"""Strict, invented-only whole-share executable-depth falsifier for #209→#214.

Preregistered before this test was written: #204/6237249558.
No authentic source/fill/fee/admission or LIVE operation is involved.
Expected: a one-share order cannot be qualified when EACH native ask level
has one displayed share and a 50% conservative per-level haircut. Each
level contributes floor(1 * 0.5) = zero executable WHOLE shares.

This test calls the unchanged real research evaluator, not the #291 oracle.
Do not xfail, skip, relax the fixed 50% haircut, or reinterpret a quote
scenario/touch upper bound as a venue execution or positive net alpha.
"""

from __future__ import annotations

from decimal import ROUND_DOWN, Decimal

from predictions_cup.replay.economic_005i import evaluate
from predictions_cup.replay.model import BookLevel
from test_economic_005i import _multilevel_tape, cfg, feature, freeze, pair

D = Decimal


def test_005i_fractional_per_level_depth_cannot_qualify_one_whole_share() -> None:
    # Frozen negative witness: two physically separate price levels, each
    # carrying ONE share. Do not combine two theoretical half-shares into an
    # executable one-share IOC across levels.
    one_share_levels = (
        BookLevel(D("0.40"), D("1")),
        BookLevel(D("0.50"), D("1")),
    )
    haircut = D("0.5")
    assert sum(
        (
            (level.quantity * haircut).to_integral_value(rounding=ROUND_DOWN)
            for level in one_share_levels
        ),
        D(0),
    ) == D(0)

    result = evaluate(
        _multilevel_tape(one_share_levels, one_share_levels),
        [feature()], [pair()],
        cfg(quantity=D("1"), depth_haircut=haircut), freeze(),
    )
    reversal = result["results"]["REVERSAL"]

    # The synthetic PM 5-minute ret is negative and references a separate,
    # fresh SIG exchange with a plausible PM fair-value buffer. Only the
    # executable size must reject this invented candidate.
    assert reversal["signals"] == 1
    assert reversal["qualified_orders"] == 0
    assert reversal["touch_fill_upper_bound"] == 0
    assert reversal["trades"] == []
    assert result["promotion"].startswith("BLOCKED")

    # Positive shape-control: at least one true whole share survives
    # the 50% haircut when the first level has two displayed shares.
    two_share_levels = (
        BookLevel(D("0.40"), D("2")),
        BookLevel(D("0.50"), D("2")),
    )
    valid = evaluate(
        _multilevel_tape(two_share_levels, two_share_levels),
        [feature()], [pair()],
        cfg(quantity=D("1"), depth_haircut=haircut), freeze(),
    )["results"]["REVERSAL"]
    assert valid["qualified_orders"] == 1
    assert valid["touch_fill_upper_bound"] == 1
