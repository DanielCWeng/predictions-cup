from __future__ import annotations

from typing import cast

import pytest

from predictions_cup.maker.taker_economics import (
    Direction,
    TakerEconomics,
    evaluate_taker_economics,
    growth_optimal_quantity,
)
from predictions_cup.runtime.models import RuntimeLevel


def _eval(**overrides: object) -> TakerEconomics:
    """Pass typed arguments; heterogeneous pytest overrides are test data."""
    return evaluate_taker_economics(
        direction=cast(Direction, overrides.get("direction", "BUY")),
        levels=cast(
            tuple[RuntimeLevel, ...],
            overrides.get(
                "levels",
                (RuntimeLevel(100, 10), RuntimeLevel(101, 20), RuntimeLevel(104, 50)),
            ),
        ),
        conservative_fair=cast(float, overrides.get("conservative_fair", 0.53)),
        desired_quantity=cast(int, overrides.get("desired_quantity", 50)),
        trusted_depth=cast(bool, overrides.get("trusted_depth", True)),
        book_age_ns=cast(int, overrides.get("book_age_ns", 10)),
        reference_age_ns=cast(int, overrides.get("reference_age_ns", 10)),
        fee_per_share=cast(float, overrides.get("fee_per_share", 0.001)),
        latency_haircut=cast(float, overrides.get("latency_haircut", 0.005)),
        adverse_selection_haircut=cast(
            float, overrides.get("adverse_selection_haircut", 0.005)
        ),
        min_net_edge_per_share=cast(
            float, overrides.get("min_net_edge_per_share", 0.005)
        ),
        maker_price_ticks=cast(int | None, overrides.get("maker_price_ticks", 99)),
        available_cash=cast(float | None, overrides.get("available_cash")),
        reducible_inventory_shares=cast(
            int, overrides.get("reducible_inventory_shares", 0)
        ),
        max_book_age_ns=cast(
            int, overrides.get("max_book_age_ns", 1_500_000_000)
        ),
        max_reference_age_ns=cast(
            int, overrides.get("max_reference_age_ns", 5_000_000_000)
        ),
    )


def test_buy_walks_full_depth_and_stops_before_unprofitable_level() -> None:
    result = _eval()
    assert result.admissible
    assert result.quantity == 30
    assert result.limit_price_ticks == 101
    assert result.vwap is not None
    assert result.vwap == pytest.approx((10 * 0.5 + 20 * 0.505) / 30)
    assert result.net_expected_value == pytest.approx(30 * (0.53 - result.vwap - 0.011))
    assert result.total_slippage == pytest.approx(20 * 0.005)
    assert result.maker_conditional_edge_per_share == pytest.approx(0.53 - 0.495 - 0.001)


def test_sell_walks_bid_depth_and_uses_upper_fair_bound() -> None:
    result = _eval(
        direction="SELL",
        levels=(RuntimeLevel(110, 7), RuntimeLevel(108, 9), RuntimeLevel(101, 30)),
        conservative_fair=0.50,
        maker_price_ticks=111,
    )
    assert result.admissible
    assert result.quantity == 16
    assert result.limit_price_ticks == 108
    assert result.net_edge_per_share > 0.02


@pytest.mark.parametrize(
    "changes,why",
    [
        (dict(trusted_depth=False), "depth_untrusted"),
        (dict(book_age_ns=1_500_000_001), "stale_data"),
        (dict(reference_age_ns=5_000_000_001), "stale_data"),
        (dict(book_age_ns=-1), "stale_data"),
        (dict(levels=()), "no_quantity_or_depth"),
        (dict(conservative_fair=float("nan")), "invalid_economics"),
        (dict(levels=(RuntimeLevel(100, 1), RuntimeLevel(99, 10))), "invalid_book"),
        (dict(levels=(RuntimeLevel(100, float("nan")),)), "invalid_book"),
        (
            dict(levels=(RuntimeLevel(100, 50),), conservative_fair=0.49),
            "edge_or_depth_insufficient",
        ),
        (dict(available_cash=float("nan")), "cash_untrusted"),
    ],
)
def test_fail_closed(changes: dict[str, object], why: str) -> None:
    assert _eval(**changes).reason == why


def test_cash_limits_quantity_at_worst_limit_price() -> None:
    result = _eval(available_cash=3, desired_quantity=50)
    assert result.quantity == 6
    assert result.limit_price_ticks == 100


def test_partials_fully_costed_and_dont_claim_guaranteed_arb() -> None:
    result = _eval(levels=(RuntimeLevel(100, 2),), desired_quantity=100)
    assert result.quantity == 2
    assert result.admissible
    assert result.net_expected_value < 1
    assert result.maker_conditional_edge_per_share is not None


def test_no_additional_levels_after_min_edge() -> None:
    result = _eval(
        levels=(RuntimeLevel(100, 3), RuntimeLevel(103, 40)),
        conservative_fair=0.52,
    )
    assert result.quantity == 3
    assert result.limit_price_ticks == 100


def test_sell_no_buy_uses_complementary_collateral() -> None:
    result = _eval(
        direction="SELL",
        levels=(RuntimeLevel(110, 50),),
        conservative_fair=0.50,
        available_cash=9.5,
    )
    assert result.admissible
    assert result.quantity == 21
    assert result.worst_case_settlement_loss == pytest.approx(21 * 0.45 + 21 * 0.001)


def test_provably_reducing_inventory_needs_no_new_collateral() -> None:
    sale = _eval(
        direction="SELL",
        levels=(RuntimeLevel(110, 50),),
        conservative_fair=0.50,
        available_cash=0,
        reducible_inventory_shares=15,
    )
    assert sale.quantity == 15
    buy = _eval(available_cash=0, reducible_inventory_shares=7)
    assert buy.quantity == 7


def test_negative_reducible_inventory_fails_closed() -> None:
    assert _eval(reducible_inventory_shares=-1).reason == "invalid_inventory"


def test_wi01_small_edge_cannot_justify_uncapped_22998_shares() -> None:
    """Expected growth is not raw EV multiplied by unlimited contracts."""
    quantity = growth_optimal_quantity(
        desired_quantity=22_998,
        outcome_cost=0.515,
        fair_probability=0.54,
        execution_cost_per_share=0.01,
        equity=100_760.12,
        already_held_shares=3_100,
        reference_depth_shares=50_000,
    )
    # Conservatively valued p(NO)=.53: approximately 3.1% Kelly bankroll,
    # less the inventory already held in this same binary outcome.
    assert 0 < quantity < 4_000
    assert quantity * 0.515 < 2_000
    assert quantity < 22_998


def test_growth_sizing_refuses_to_extrapolate_thin_reference_quote() -> None:
    quantity = growth_optimal_quantity(
        desired_quantity=20_000,
        outcome_cost=0.40,
        fair_probability=0.55,
        execution_cost_per_share=0.01,
        equity=100_000,
        already_held_shares=0,
        reference_depth_shares=80,
    )
    assert quantity == 80


def test_growth_sizing_has_no_bet_without_conservative_edge() -> None:
    assert growth_optimal_quantity(
        desired_quantity=1_000,
        outcome_cost=0.515,
        fair_probability=0.52,
        execution_cost_per_share=0.01,
        equity=100_000,
        already_held_shares=0,
        reference_depth_shares=1_000,
    ) == 0


def test_growth_sizing_counts_existing_same_side_holdings() -> None:
    def size(held: float) -> int:
        return growth_optimal_quantity(
            desired_quantity=100_000,
            outcome_cost=0.40,
            fair_probability=0.55,
            execution_cost_per_share=0.01,
            equity=100_000.0,
            reference_depth_shares=100_000.0,
            already_held_shares=held,
        )
    assert size(1_000) == max(0, size(0) - 1_000)
