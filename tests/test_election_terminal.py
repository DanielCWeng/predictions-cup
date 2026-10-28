"""Offline, invented election slates: no live prices, accounts, or alpha claim."""

from __future__ import annotations

from dataclasses import replace

import pytest

from predictions_cup.analysis.election_terminal import (
    ExecutableTrade,
    Holding,
    ProbabilityView,
    SettlementScenario,
    TradePlan,
    evaluate_trade_plans,
)


def _scenarios() -> tuple[SettlementScenario, ...]:
    # The two races move together under an election-wide swing.
    return (
        SettlementScenario("swing", {"race-a": True, "race-b": True}),
        SettlementScenario("no-swing", {"race-a": False, "race-b": False}),
    )


def _views() -> tuple[ProbabilityView, ...]:
    return (
        ProbabilityView("forecast-one", "synthetic://forecast-one", {
            "swing": .3, "no-swing": .7,
        }),
        ProbabilityView("forecast-two", "synthetic://forecast-two", {
            "swing": .4, "no-swing": .6,
        }),
    )


def _trade(
    market: str = "race-a", action: str = "BUY", side: str = "YES",
    quantity: int = 50, price: float = .2, visible: int = 50,
) -> ExecutableTrade:
    return ExecutableTrade(
        market, action, side, quantity, price, visible  # type: ignore[arg-type]
    )


def _score(
    plans: tuple[TradePlan, ...],
    *,
    scenarios: tuple[SettlementScenario, ...] | None = None,
    models: tuple[ProbabilityView, ...] | None = None,
    holdings: tuple[Holding, ...] = (),
    cash: float = 100.0,
    target: float = 139.0,
) -> tuple:
    return evaluate_trade_plans(
        scenarios=scenarios if scenarios is not None else _scenarios(),
        probability_views=models if models is not None else _views(),
        holdings=holdings,
        cash=cash,
        target_balance=target,
        plans=plans,
    )


def test_joint_correlated_swing_is_counted_once_not_twice() -> None:
    only_a = TradePlan("a", (_trade(),))
    only_b = TradePlan("b", (_trade("race-b", price=.25),))
    combined = TradePlan("joint", (_trade(), _trade("race-b", price=.25)))
    results = _score((only_a, only_b, combined))
    assert [r.name for r in results] == ["joint", "a", "b"]
    joint = results[0]
    assert joint.worst_settlement_balance == pytest.approx(77.5)
    assert joint.best_settlement_balance == pytest.approx(177.5)
    assert joint.min_expected_gain == pytest.approx(7.5)
    assert joint.min_probability_improvement == pytest.approx(.3)
    assert [m.probability_beat_target for m in joint.model_scores] == pytest.approx(
        [.3, .4]
    )
    # Both legs win in the SAME scenario: probability is 30%, not 60%.
    assert joint.model_scores[0].probability_beat_target == pytest.approx(.3)


def test_target_objective_is_prioritized_over_raw_ev() -> None:
    # Cheap 10-share bet generates expected return but cannot reach the target.
    high_ev_small = TradePlan("ev-only", (_trade(quantity=10, price=.1),))
    wins_target = TradePlan("terminal-upside", (_trade(),))
    result = _score((high_ev_small, wins_target))
    assert [r.name for r in result] == ["terminal-upside", "ev-only"]
    assert result[1].min_expected_gain > 0
    assert result[1].min_probability_improvement == 0
    assert result[0].min_probability_improvement > 0


def test_opposite_no_position_and_inventory_reduction_settle_correctly() -> None:
    held = (Holding("race-a", yes_shares=10, no_shares=8),)
    plan = TradePlan("sell-held", (_trade(
        action="SELL", side="YES", quantity=5, price=.7,
    ),))
    result = _score((plan,), holdings=held)[0]
    # Selling YES: receive 3.5 now, forgo 5 if the YES outcome settles.
    assert result.remaining_cash == pytest.approx(103.5)
    assert result.worst_settlement_balance == pytest.approx(108.5)
    assert result.best_settlement_balance == pytest.approx(111.5)
    # The NO shares pay precisely when YES loses, not in both scenarios.
    no_buy = TradePlan("no-buy", (_trade(
        action="BUY", side="NO", quantity=5, price=.4,
    ),))
    no_result = _score((no_buy,), holdings=held)[0]
    assert no_result.best_settlement_balance == pytest.approx(111)


def test_missing_settlement_or_untrusted_probability_view_rejected() -> None:
    plan = (TradePlan("a", (_trade(),)),)
    with pytest.raises(ValueError, match="every market"):
        _score(plan, scenarios=(
            SettlementScenario("swing", {"race-a": True, "race-b": True}),
            SettlementScenario("no-swing", {"race-a": False}),
        ))
    with pytest.raises(ValueError, match="sum to one"):
        _score(plan, models=(
            ProbabilityView("bad", "synthetic://bad", {
                "swing": .9, "no-swing": .5,
            }),
        ))
    with pytest.raises(ValueError, match="support mismatch"):
        _score(plan, models=(
            ProbabilityView("bad", "synthetic://bad", {"swing": 1.0}),
        ))
    with pytest.raises(ValueError, match="model identity"):
        _score(plan, models=(_views()[0], _views()[0]))


def test_executable_depth_cash_and_actual_inventory_bound_all_trades() -> None:
    with pytest.raises(ValueError, match="invalid executable"):
        _score((TradePlan("thin", (_trade(visible=2),)),))
    with pytest.raises(ValueError, match="insufficient cash"):
        _score((TradePlan("unfunded", (_trade(
            quantity=50, price=.8,
        ),)),), cash=30)
    with pytest.raises(ValueError, match="unowned YES"):
        _score((TradePlan("naked", (_trade(
            action="SELL", side="YES", quantity=50, price=.7,
        ),)),))
    with pytest.raises(ValueError, match="unowned NO"):
        _score((TradePlan("naked-no", (_trade(
            action="SELL", side="NO", quantity=50, price=.7,
        ),)),))
    with pytest.raises(ValueError, match="invalid executable"):
        _score((TradePlan("fake", (replace(_trade(), executable_price=.0),)),))
    with pytest.raises(ValueError, match="invalid executable"):
        _score((TradePlan("fake-size", (replace(_trade(), quantity=True),)),))
    with pytest.raises(ValueError, match="invalid executable"):
        _score((TradePlan("nan", (replace(_trade(), executable_price=float("nan")),)),))


def test_multi_leg_rotation_requires_sold_proceeds_before_purchase() -> None:
    holdings = (Holding("race-a", yes_shares=40),)
    rotate = TradePlan("rotate", (
        _trade(action="SELL", side="YES", quantity=40, price=.7),
        _trade("race-b", quantity=50, price=.5),
    ))
    assessed = _score((rotate,), holdings=holdings, cash=1)[0]
    assert assessed.remaining_cash == pytest.approx(4)
    # The same plan with an unfunded purchase before the sale is not executable
    # as an atomic operation: partial-fill and ordering assumptions matter.
    reversed_plan = TradePlan("backwards", tuple(reversed(rotate.trades)))
    with pytest.raises(ValueError, match="insufficient cash"):
        _score((reversed_plan,), holdings=holdings, cash=1)


def test_equal_to_target_is_not_a_win() -> None:
    result = _score(
        (TradePlan("a", (_trade(),)),), target=140,
    )[0]
    assert result.min_probability_improvement == 0
    assert all(m.probability_beat_target == 0 for m in result.model_scores)
